import json

from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_dataset import SliceConfig
from voice_workbench_worker.runner import run_one


SR = 16000


def vocal():
    t = np.arange(SR * 4) / SR
    return (.2 * (1 + .6 * np.sin(2 * np.pi * 1.7 * t)) *
            np.sin(2 * np.pi * (230 * t + 3 * np.sin(2 * np.pi * 1.2 * t)))).astype(np.float32)


def source(store, path, audio):
    sf.write(path, audio, SR, subtype="FLOAT")
    return store.import_file(path, name=path.name, role="source", metadata={"kind": "dry_vocal"})["id"]


def test_worker_guard_fallback_and_embedded_evidence_survive_cleanup(tmp_path, monkeypatch):
    app = create_app(tmp_path / "runtime")
    store, client = app.state.store, TestClient(app)
    aid = source(store, tmp_path / "vocal.wav", vocal())
    # A deliberately damaged engine output tests orchestration only; this is
    # not presented as a real GPU model or a separator quality test.
    def damaged_output(store, jid, source_id, model_id, **kwargs):
        path = tmp_path / "damaged.wav"
        sf.write(path, np.zeros(SR * 4, dtype=np.float32), SR, subtype="FLOAT")
        result = store.import_file(path, name="damaged.wav", job_id=jid,
                                   metadata={"stem": "dry", "task": "dereverb", "weight_sha256": "test-only"})
        return {"dry": result["id"]}
    monkeypatch.setattr("voice_workbench_worker.runner.separate", damaged_output)
    job = store.create_job("dataset_prepare", [aid], {"source_ids": [aid], "config": SliceConfig().dict(), "preprocessing": {"dereverb": True}})
    assert run_one(store)
    complete = store.job(job["id"])
    assert complete["status"] == "completed", complete
    mid = complete["metadata"]["result_id"]
    manifest = client.get(f"/api/datasets/{mid}").json()
    stage = manifest["sources"][0]["processing"]["stages"][0]
    assert stage["decision"] == "fallback_input"
    assert stage["selected_id"] == aid
    assert stage["input_available"] and stage["candidate_available"]
    assert stage["engine"]["weight_sha256"] == "test-only"
    assert manifest["summary"]["cleanup_fallback_count"] == 1
    clip = manifest["clips"][0]
    assert clip["status"] == "review"
    assert "CLEANUP_SIGNAL_COLLAPSE" in clip["reasons"]
    client.post(f"/api/datasets/{mid}/review", json={"decisions": {clip["artifact_id"]: "accepted"}})
    client.post("/api/cache/cleanup", json={})
    after = client.get(f"/api/datasets/{mid}").json()
    assert after["sources"][0]["processing"]["stages"][0]["candidate_available"] is False
    assert after["sources"][0]["processing"]["stages"][0]["input_available"] is True
    assert after["sources"][0]["processing"]["stages"][0]["engine"]["weight_sha256"] == "test-only"
    assert after["clips"][0]["available"] is True
    assert store.path(aid).exists()


def test_gain_copies_review_without_leaking_into_validation(tmp_path):
    app = create_app(tmp_path / "runtime")
    store, client = app.state.store, TestClient(app)
    ids = [source(store, tmp_path / name, gain * vocal()) for name, gain in (("a.wav", 1), ("b.wav", -.25))]
    response = client.post("/api/datasets/prepare", json={"source_ids": ids})
    assert response.status_code == 202
    assert run_one(store)
    complete = store.job(response.json()["id"])
    assert complete["status"] == "completed", complete
    mid = complete["metadata"]["result_id"]
    manifest = client.get(f"/api/datasets/{mid}").json()
    assert manifest["summary"]["near_duplicate_count"] == 1
    assert all(c["status"] == "review" for c in manifest["clips"])
    assert manifest["clips"][1]["near_duplicate_of"] == [manifest["clips"][0]["artifact_id"]]
    reviewed = client.post(f"/api/datasets/{mid}/review", json={"decisions": {c["artifact_id"]: "accepted" for c in manifest["clips"]}}).json()
    frozen = json.loads(store.path(reviewed["id"]).read_text())
    assert frozen["split"]["validation"] == []
    assert frozen["split"]["linked_source_count"] == 1
    assert frozen["validation"]["status"] == "unavailable_single_source"
