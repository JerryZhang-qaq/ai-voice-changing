import json

from voice_workbench_engines.telemetry import LogTelemetry
from voice_workbench_storage import ArtifactStore
from voice_workbench_worker.batch_progress import BatchProgress


def test_chunk_counter_and_retry_cannot_overwrite_or_rewind_batch(tmp_path):
    store = ArtifactStore(tmp_path)
    job = store.create_job("dataset_prepare", status="running")
    sources = [{"name": f"曲目{i}.wav", "metadata": {"audio": {"duration": 10}}} for i in range(2)]
    batch = BatchProgress(store, job["id"], [("vocals", "人声提取", [0, 1]), ("slicing", "切片", [0, 1])], sources)
    batch.begin("vocals", 0)
    log = tmp_path / "engine.log"
    observer = LogTelemetry(store, job["id"], log, namespace="engine_progress")
    log.write_text("进度: 80/100\n", encoding="utf-8")
    observer.tick()
    state = store.job(job["id"])["metadata"]
    assert state["engine_progress"]["done"] == 80
    assert state["batch_progress"]["total"] == 4
    before = state["batch_progress"]["done"]
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "workbench_separation", "state": "fallback", "reason": "numeric retry"}) + "\n进度: 2/100\n")
    observer.tick()
    assert store.job(job["id"])["metadata"]["batch_progress"]["done"] >= before
    batch.finish()
    batch.begin("vocals", 1)
    state = store.job(job["id"])["metadata"]
    assert state["batch_progress"]["done"] == state["batch_progress"]["completed_steps"] == 1
    assert state["batch_progress"]["source_name"] == "曲目1.wav"
    assert state["batch_progress"]["source_index"] == 2
    assert state["engine_progress"] is None


def test_each_clip_fraction_stays_inside_its_source_step(tmp_path):
    store = ArtifactStore(tmp_path)
    job = store.create_job("dataset_prepare", status="running")
    batch = BatchProgress(store, job["id"], [("slicing", "切片", [0])], [{"name": "歌.wav"}])
    batch.begin("slicing", 0)
    log = tmp_path / "log"
    observer = LogTelemetry(store, job["id"], log, namespace="engine_progress")
    batch.clip(0, 10)
    log.write_text("进度: 100/100\n", encoding="utf-8")
    observer.tick()
    first = store.job(job["id"])["metadata"]["batch_progress"]["done"]
    assert 0 < first < .1
    batch.clip(1, 10)
    with log.open("a", encoding="utf-8") as stream:
        stream.write("进度: 1/100\n")
    observer.tick()
    assert store.job(job["id"])["metadata"]["batch_progress"]["done"] >= first
    batch.finish()
    assert store.job(job["id"])["metadata"]["batch_progress"]["done"] == 1
