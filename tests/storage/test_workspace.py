from pathlib import Path

from voice_workbench_storage import ArtifactStore


def test_workspace_protected_while_running_then_cleanable(tmp_path):
    store = ArtifactStore(tmp_path / "runtime")
    job = store.create_job("train")
    workspace = store.allocate_workspace(job["id"], name="训练检查点与特征")
    root = store.path(workspace["id"])
    (root / "checkpoint.bin").write_bytes(b"checkpoint")
    assert store.inventory()["total_bytes"] == 10
    assert store.cleanup()["deleted_ids"] == []
    store.update_job(job["id"], "interrupted")
    resumed = store.create_job("train", [workspace["id"]])
    assert store.cleanup()["deleted_ids"] == []
    store.update_job(resumed["id"], "completed")
    assert store.cleanup()["reclaimed_bytes"] == 10
    assert not root.exists()


def test_workspace_internal_symlink_never_deletes_external_files(tmp_path):
    store = ArtifactStore(tmp_path / "runtime")
    job = store.create_job("train")
    workspace = store.allocate_workspace(job["id"], name="workspace")
    external = tmp_path / "precious"
    external.mkdir()
    (external / "file").write_text("keep")
    (store.path(workspace["id"]) / "link").symlink_to(external, target_is_directory=True)
    store.update_job(job["id"], "failed")
    result = store.cleanup()
    assert result["deleted_ids"] == [workspace["id"]]
    assert (external / "file").read_text() == "keep"
