"""Training adapter artifact lifecycle with CPU CLI stand-ins; no GPU claim."""
from io import BytesIO
import json
import sys

import pytest

from voice_workbench_engines import rvc
from voice_workbench_storage import ArtifactStore


@pytest.fixture
def training(tmp_path, monkeypatch):
    root = tmp_path / "RVC 中文"
    preset = root / "configs/v1"
    preset.mkdir(parents=True)
    (preset / "40k.json").write_text("{}", encoding="utf-8")
    store = ArtifactStore(tmp_path / "runtime")
    clips = []
    for index in range(2):
        audio = store.import_stream(BytesIO(f"audio-{index}".encode()), name="clip.wav", role="dataset")
        clips.append({"artifact_id": audio["id"], "status": "accepted", "source_group": "same-source",
                      "duplicate_group": str(index)})
    dataset = store.import_stream(BytesIO(json.dumps({"clips": clips, "summary": {}}).encode()),
                                  name="dataset.json", role="dataset")
    job = store.create_job("rvc_train", [dataset["id"], *(c["artifact_id"] for c in clips)], status="running")
    monkeypatch.setattr(rvc, "require_ready", lambda: {"rvc_root": str(root), "rvc_python": sys.executable})
    monkeypatch.setattr(rvc, "status", lambda: {"training_rates": {"40k": True}})
    monkeypatch.setattr(rvc, "training_chunks", lambda *args: [c["artifact_id"] for c in clips])
    monkeypatch.setattr(rvc, "validate_features", lambda *args: None)
    calls = []

    def cli(command, **kwargs):
        # This assertion fails on the old adapter before its first GPU stage.
        weights = root / "assets/weights"
        assert weights.is_dir()
        workspace = store.path(store.job(job["id"])["metadata"]["workspace_id"])
        if command[1:3] == ["-m", "train.train"]:
            calls.append("train")
            experiment = command[command.index("-e") + 1]
            (weights / f"{experiment}.pth").write_bytes(b"inference-model-fixture")
            (workspace / "G_2333333.pth").write_bytes(b"generator-checkpoint")
            (workspace / "D_2333333.pth").write_bytes(b"discriminator-checkpoint")
        elif command[1].endswith("train_index.py"):
            calls.append("index")
            (workspace / "added_fixture.index").write_bytes(b"index-fixture")
        else:
            calls.append("features")

    monkeypatch.setattr(rvc, "run_process", cli)
    return root, store, dataset, job, calls


def test_first_training_prepares_exports_before_features(training):
    root, store, dataset, job, calls = training
    assert not (root / "assets/weights").exists()
    result = rvc.train(store, job["id"], dataset["id"])
    assert calls == ["features", "features", "train", "index"]
    assert store.path(result["model"]).read_bytes() == b"inference-model-fixture"
    assert store.path(result["index"]).read_bytes() == b"index-fixture"
    assert store.get(result["model"])["role"] == "model"
    assert not list((root / "assets/weights").glob("*.pth"))
    assert not (root / "logs" / ("workbench_" + job["id"])).exists()


def test_blocked_export_destination_fails_before_features(training):
    root, store, dataset, job, calls = training
    (root / "assets").mkdir()
    blocked = root / "assets/weights"
    blocked.write_bytes(b"keep-this-file")
    with pytest.raises(rvc.EngineError, match="模型输出目录"):
        rvc.train(store, job["id"], dataset["id"])
    assert not calls
    assert blocked.read_bytes() == b"keep-this-file"


def test_resume_preserves_previous_checkpoints_and_exports_model(training):
    root, store, dataset, job, calls = training
    old_job = store.create_job("rvc_train", status="running")
    old = store.allocate_workspace(old_job["id"], name="saved training", metadata={
        "dataset_id": dataset["id"], "rate": "40k", "version": "v2",
    })
    previous = store.path(old["id"])
    for filename in ("G_2333333.pth", "D_2333333.pth"):
        (previous / filename).write_bytes(b"previous-checkpoint")
    store.update_job(old_job["id"], "completed")
    result = rvc.train(store, job["id"], dataset["id"], epochs=201, resume_id=old["id"])
    assert calls == ["train", "index"]
    assert store.path(result["model"]).is_file()
    for filename in ("G_2333333.pth", "D_2333333.pth"):
        assert (previous / filename).read_bytes() == b"previous-checkpoint"
