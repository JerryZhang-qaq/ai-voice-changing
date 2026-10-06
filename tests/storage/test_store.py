from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier

import pytest

from voice_workbench_storage import ArtifactStore, NotFound


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "runtime")


def add(store, role="cache", **kwargs):
    return store.import_stream(BytesIO(b"audio bytes"), name="voice.wav", role=role, **kwargs)


def test_clear_only_unretained_inactive_cache(store):
    originals = [add(store, role) for role in ("source", "model", "dataset", "export")]
    held = add(store)
    pinned = add(store)
    store.retain(pinned["id"], True)
    job = store.create_job("train", [held["id"]])
    output = add(store, job_id=job["id"])
    disposable = add(store)
    assert store.preview()["cleanable_count"] == 1
    result = store.cleanup()
    assert result["deleted_ids"] == [disposable["id"]]
    assert result["reclaimed_bytes"] == len(b"audio bytes")
    for artifact in originals + [held, pinned, output]:
        assert store.path(artifact["id"]).read_bytes() == b"audio bytes"
    store.update_job(job["id"], "completed")
    assert set(store.cleanup()["deleted_ids"]) == {held["id"], output["id"]}
    assert store.cleanup()["reclaimed_bytes"] == 0


def test_cleanup_rechecks_after_preview(store):
    artifact = add(store)
    assert store.preview()["cleanable_count"] == 1
    store.create_job("analysis", [artifact["id"]])
    assert store.cleanup()["deleted_ids"] == []


def test_job_scoped_cleanup(store):
    jobs = [store.create_job("analysis") for _ in range(2)]
    artifacts = [add(store, job_id=j["id"]) for j in jobs]
    for j in jobs:
        store.update_job(j["id"], "completed")
    assert store.cleanup(job_id=jobs[0]["id"])["deleted_ids"] == [artifacts[0]["id"]]
    assert store.path(artifacts[1]["id"]).exists()


def test_symlink_escape_cannot_be_deleted(store, tmp_path):
    artifact = add(store)
    path = store.path(artifact["id"])
    path.unlink()
    victim = tmp_path / "external.wav"
    victim.write_bytes(b"keep")
    path.symlink_to(victim)
    assert store.cleanup()["deleted_ids"] == []
    assert victim.read_bytes() == b"keep"


def test_oversize_upload_cleans_staging(store):
    with pytest.raises(ValueError):
        store.import_stream(BytesIO(b"oversize"), name="bad.wav", max_bytes=2)
    assert list(store.root.glob("staging-*")) == []
    assert store.inventory()["items"] == []


def test_interrupted_deletion_recovered(store):
    artifact = add(store)
    with store.connect(write=True) as db:
        db.execute("UPDATE artifacts SET state='deleting' WHERE id=?", (artifact["id"],))
    reopened = ArtifactStore(store.root)
    assert reopened.inventory()["items"] == []
    assert list(store.files.iterdir()) == []


def test_cleanup_job_acquisition_race(store):
    artifact = add(store)
    barrier = Barrier(2)
    def acquire():
        barrier.wait()
        try:
            return store.create_job("train", [artifact["id"]])
        except NotFound:
            return None
    def clear():
        barrier.wait()
        return store.cleanup()
    with ThreadPoolExecutor(2) as executor:
        a, b = executor.submit(acquire), executor.submit(clear)
        job, cleanup = a.result(), b.result()
    if job:
        assert cleanup["deleted_ids"] == []
        assert store.path(artifact["id"]).exists()
    else:
        assert cleanup["deleted_ids"] == [artifact["id"]]
