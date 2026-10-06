from fastapi.testclient import TestClient

from voice_workbench_api.app import create_app


def test_missing_engine_reports_real_unready_state(tmp_path, monkeypatch):
    monkeypatch.delenv("SEPARATION_PYTHON", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    status = client.get("/api/engines").json()["separation"]
    assert status["environment"]["ready"] is False
    assert all(not m["ready"] and not m["validated"] for m in status["models"])


def test_api_uses_worker_observation_not_its_own_gpu(tmp_path):
    app = create_app(tmp_path)
    reported = {"separation": {"environment": {"ready": True}, "models": []}, "rvc": {"ready": True, "environment": {"gpu_name": "worker GPU"}}}
    app.state.store.set_runtime("engines", reported)
    client = TestClient(app)
    assert client.get("/api/engines").json() == reported
    with app.state.store.connect(write=True) as db:
        db.execute("UPDATE runtime_state SET updated_at=0")
    assert client.get("/api/engines").json()["rvc"]["ready"] is False
