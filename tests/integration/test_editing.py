from io import BytesIO
import json

from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_worker.runner import run_one


def prepared(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    t = np.arange(16000 * 6) / 16000
    stream = BytesIO()
    sf.write(stream, .2 * np.sin(2 * np.pi * 220 * t), 16000, format="WAV", subtype="FLOAT")
    source = client.post("/api/sources", files={"file": ("solo.wav", stream.getvalue())}).json()
    job = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
    run_one(app.state.store)
    aid = app.state.store.job(job["id"])["metadata"]["result_id"]
    manifest = client.get(f"/api/datasets/{aid}").json()
    return app, client, aid, manifest


def test_boundary_edit_is_immutable_reauditable_and_holds_master(tmp_path):
    app, client, aid, manifest = prepared(tmp_path)
    clip = manifest["clips"][0]
    wave = client.get(f"/api/datasets/{aid}/waveform/{clip['artifact_id']}").json()
    assert len(wave["peaks"]) == 500 and max(wave["peaks"]) > .19
    job = client.post(f"/api/datasets/{aid}/edit", json={"clip_id": clip["artifact_id"], "start_seconds": 1, "end_seconds": 4}).json()
    assert manifest["sources"][0]["master_id"] not in app.state.store.cleanup()["deleted_ids"]
    run_one(app.state.store)
    result = app.state.store.job(job["id"])
    assert result["status"] == "completed", result
    edited = client.get(f"/api/datasets/{result['metadata']['result_id']}").json()
    assert edited["parent_manifest_id"] == aid
    assert edited["clips"][0]["valid_start_sample"] == 16000
    assert edited["clips"][0]["status"] == "review"
    assert client.get(f"/api/datasets/{aid}").json()["clips"][0]["artifact_id"] == clip["artifact_id"]


def test_out_of_bounds_edit_fails_without_changing_dataset(tmp_path):
    app, client, aid, manifest = prepared(tmp_path)
    job = client.post(f"/api/datasets/{aid}/edit", json={"clip_id": manifest["clips"][0]["artifact_id"], "start_seconds": 1, "end_seconds": 100}).json()
    run_one(app.state.store)
    assert app.state.store.job(job["id"])["status"] == "failed"
    assert len(json.loads(app.state.store.path(aid).read_text())["clips"]) == 1
