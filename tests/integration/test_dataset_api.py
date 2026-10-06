from io import BytesIO

from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf

from voice_workbench_api.app import create_app
from voice_workbench_worker.runner import run_one


def wav():
    sr = 16000
    t = np.arange(sr * 4) / sr
    x = (.2 * np.sin(2 * np.pi * (440 * t + 2 * np.sin(2 * np.pi * 5 * t)))).astype(np.float32)
    stream = BytesIO()
    sf.write(stream, np.r_[np.zeros(sr // 2), x, np.zeros(sr // 2)], sr, format="WAV", subtype="FLOAT")
    return stream.getvalue()


def test_upload_process_review_and_cache_cleanup(tmp_path):
    app = create_app(tmp_path)
    client, store = TestClient(app), app.state.store
    original = wav()
    response = client.post("/api/sources", files={"file": ("vocal.wav", original)}, data={"kind": "dry_vocal"})
    assert response.status_code == 201
    source = response.json()
    assert store.path(source["id"]).read_bytes() == original
    job = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
    assert run_one(store)
    complete = client.get(f'/api/jobs/{job["id"]}').json()
    assert complete["status"] == "completed", complete
    result_id = complete["metadata"]["result_id"]
    manifest = client.get(f'/api/datasets/{result_id}').json()
    assert manifest["summary"]["clip_count"] == 1
    clip = manifest["clips"][0]
    assert clip["status"] == "review"
    assert "IDENTITY_UNVERIFIED" not in clip["reasons"]
    assert "SOLO_DECLARATION_REQUIRED" in clip["reasons"]
    assert client.get(f'/api/artifacts/{clip["artifact_id"]}/file').status_code == 200
    reviewed = client.post(f'/api/datasets/{result_id}/review', json={"decisions": {clip["artifact_id"]: "accepted"}})
    assert reviewed.status_code == 201, reviewed.text
    new = client.get(f'/api/datasets/{reviewed.json()["id"]}').json()
    assert new["summary"]["accepted_count"] == 1
    assert client.get(f'/api/datasets/{result_id}').json()["summary"]["accepted_count"] == 0
    cleanup = client.post("/api/cache/cleanup", json={}).json()
    assert clip["artifact_id"] not in cleanup["deleted_ids"]
    assert store.path(clip["artifact_id"]).exists()


def test_invalid_audio_rejected_and_song_does_not_bypass_separation(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/sources", files={"file": ("bad.wav", b"invalid")}).status_code == 400
    assert app.state.store.inventory()["items"] == []
    source = client.post("/api/sources", files={"file": ("song.wav", wav())}, data={"kind": "song"}).json()
    assert client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).status_code == 409


def test_queued_cancel_releases_input_hold(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    source = client.post("/api/sources", files={"file": ("vocal.wav", wav())}).json()
    job = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
    assert client.post(f'/api/jobs/{job["id"]}/cancel').json()["status"] == "cancelled"
    assert not run_one(app.state.store)


def test_running_cancel_does_not_stop_next_job(tmp_path):
    app = create_app(tmp_path)
    store, client = app.state.store, TestClient(app)
    source = client.post("/api/sources", files={"file": ("vocal.wav", wav())}).json()
    first = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
    second = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
    store.update_job(first["id"], "queued", metadata={"cancel_requested": True})
    assert run_one(store)
    assert store.job(first["id"])["status"] == "cancelled"
    assert run_one(store)
    assert store.job(second["id"])["status"] == "completed"


def test_repeat_prepare_reuses_then_rebuilds_cleaned_clips(tmp_path):
    app = create_app(tmp_path)
    store, client = app.state.store, TestClient(app)
    source = client.post("/api/sources", files={"file": ("vocal.wav", wav())}).json()
    def prepare():
        j = client.post("/api/datasets/prepare", json={"source_ids": [source["id"]]}).json()
        assert run_one(store)
        return store.job(j["id"])["metadata"]["result_id"]
    first = prepare()
    assert prepare() == first
    store.cleanup()
    rebuilt = prepare()
    assert rebuilt != first
    assert all(c["available"] for c in client.get(f'/api/datasets/{rebuilt}').json()["clips"])
