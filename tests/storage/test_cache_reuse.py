from io import BytesIO

from voice_workbench_storage import ArtifactStore


def test_cache_hit_protects_the_original_until_consumer_finishes(tmp_path):
    store = ArtifactStore(tmp_path)
    cached = store.import_stream(BytesIO(b"cached"), name="audio.wav", metadata={"cache_key": "key"})
    job = store.create_job("convert")
    assert store.cached("key", job["id"])["id"] == cached["id"]
    assert store.cleanup()["deleted_ids"] == []
    store.update_job(job["id"], "completed")
    assert store.cleanup()["deleted_ids"] == [cached["id"]]
    next_job = store.create_job("convert")
    assert store.cached("key", next_job["id"]) is None


def test_tampered_cache_not_reused(tmp_path):
    store = ArtifactStore(tmp_path)
    cached = store.import_stream(BytesIO(b"cached"), name="audio.wav", metadata={"cache_key": "key"})
    store.path(cached["id"]).write_bytes(b"tampered")
    job = store.create_job("convert")
    assert store.cached("key", job["id"]) is None
    assert store.get(cached["id"])["metadata"]["integrity_error"] is True
