from io import BytesIO

from fastapi.testclient import TestClient

from voice_workbench_api.app import create_app


def test_preview_pin_cleanup_and_download(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    a = store.import_stream(BytesIO(b"fixture"), name="cached.wav")
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/cache/preview", json={}).json()["reclaimable_bytes"] == 7
    assert client.patch(f'/api/artifacts/{a["id"]}/retention', json={"retained": True}).status_code == 200
    assert client.post("/api/cache/cleanup", json={}).json()["deleted_ids"] == []
    assert client.get(f'/api/artifacts/{a["id"]}/file').content == b"fixture"
    client.patch(f'/api/artifacts/{a["id"]}/retention', json={"retained": False})
    assert client.post("/api/cache/cleanup", json={}).json()["deleted_ids"] == [a["id"]]
    assert client.get(f'/api/artifacts/{a["id"]}/file').status_code == 404
