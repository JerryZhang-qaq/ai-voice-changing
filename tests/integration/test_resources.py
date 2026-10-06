import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from fastapi.testclient import TestClient
import pytest

from voice_workbench_api.app import create_app
from voice_workbench_engines import resources
from voice_workbench_worker.runner import run_one, Cancelled


@pytest.fixture
def resource(tmp_path, monkeypatch):
    data = b"verified weight" * 100
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    entry = {"path": "small.ckpt", "url": f"http://127.0.0.1:{server.server_port}/weight", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    spec = {"id": "small", "root": "separation_models", "name": "test", "files": [entry]}
    monkeypatch.setattr(resources, "catalog", lambda: [spec])
    monkeypatch.setattr(resources, "settings", lambda: {"separation_models": str(tmp_path / "models")})
    app = create_app(tmp_path / "runtime")
    yield app, entry, data
    server.shutdown()
    server.server_close()


def test_download_verify_and_detect_changed_local_file(resource):
    app, entry, data = resource
    client = TestClient(app)
    assert client.post("/api/resources", json={"resource_ids": ["unknown"]}).status_code == 400
    job = client.post("/api/resources", json={"resource_ids": ["small"]}).json()
    assert run_one(app.state.store)
    assert app.state.store.job(job["id"])["status"] == "completed"
    path = resources.destination(resources.catalog()[0], entry)
    assert path.read_bytes() == data
    assert client.get("/api/resources").json()["items"][0]["ready"]
    path.write_bytes(b"broken")
    assert not client.get("/api/resources").json()["items"][0]["ready"]
    job = client.post("/api/resources", json={"resource_ids": ["small"]}).json()
    run_one(app.state.store)
    assert app.state.store.job(job["id"])["status"] == "failed"
    assert path.read_bytes() == b"broken"


def test_wrong_hash_never_installs_weight_and_partial_is_cleanable(resource):
    app, entry, _ = resource
    entry["sha256"] = "0" * 64
    job = app.state.store.create_job("resources", metadata={"resource_ids": ["small"]})
    run_one(app.state.store)
    assert app.state.store.job(job["id"])["status"] == "failed"
    assert not resources.destination(resources.catalog()[0], entry).exists()
    preview = app.state.store.preview()
    assert preview["reclaimable_bytes"] > 0
    app.state.store.cleanup()
    assert not any(app.state.store.root.rglob("*.part"))


def test_cancelled_download_never_installs_weight(resource):
    app, entry, _ = resource
    job = app.state.store.create_job("resources")
    calls = 0
    def progress(*args):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise Cancelled()
    with pytest.raises(Cancelled):
        resources.install_resources(app.state.store, job["id"], ["small"], progress=progress)
    assert not resources.destination(resources.catalog()[0], entry).exists()
    app.state.store.update_job(job["id"], "cancelled")
    assert app.state.store.preview()["reclaimable_bytes"] > 0


def test_install_partial_recovery_only_removes_owned_finished_jobs(resource):
    app, entry, _ = resource
    store = app.state.store
    done = store.create_job("resources")
    store.update_job(done["id"], "interrupted")
    active = store.create_job("resources")
    parent = resources.destination(resources.catalog()[0], entry).parent
    parent.mkdir(parents=True)
    orphan = parent / f'.workbench-install-{done["id"]}-temp.part'
    running = parent / f'.workbench-install-{active["id"]}-temp.part'
    unrelated = parent / '.workbench-install-unknown.part'
    for path in (orphan, running, unrelated):
        path.write_bytes(b"partial")
    resources.recover_install_partials(store)
    assert not orphan.exists()
    assert running.exists() and unrelated.exists()
