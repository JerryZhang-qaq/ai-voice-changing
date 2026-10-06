import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from voice_workbench_api.app import create_app
from voice_workbench_worker.runner import run_one


def test_mix_api_and_worker_produce_downloadable_export(tmp_path):
    app = create_app(tmp_path / "runtime")
    store, client = app.state.store, TestClient(app)
    path = tmp_path / "track.wav"
    sr = 16000
    sf.write(path, .1 * np.sin(2 * np.pi * 440 * np.arange(sr * 2) / sr), sr)
    ids = [store.import_file(path, name=name, role="source")["id"] for name in ("vocal.wav", "instrumental.wav")]
    response = client.post("/api/mixing", json={"vocal_id": ids[0], "instrumental_id": ids[1], "format": "flac"})
    assert response.status_code == 202
    assert run_one(store)
    job = store.job(response.json()["id"])
    assert job["status"] == "completed", job
    output = client.get(f'/api/artifacts/{job["metadata"]["result_id"]}/file')
    assert output.status_code == 200 and output.content.startswith(b"fLaC")


def test_missing_rvc_does_not_queue_fake_training_or_conversion(tmp_path, monkeypatch):
    monkeypatch.delenv("RVC_PYTHON", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    rvc = client.get("/api/engines").json()["rvc"]
    assert rvc["ready"] is False and rvc["validated"] is False
    response = client.post("/api/models/import", files={"model": ("bad.pth", b"bad")})
    assert response.status_code == 409
    assert app.state.store.jobs() == []
