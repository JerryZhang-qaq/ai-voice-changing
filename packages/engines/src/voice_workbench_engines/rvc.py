from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess

import numpy as np
import soundfile as sf

from voice_workbench_storage import ArtifactStore
from .process import EngineError, probe_python, run_process
from .registry import RVC_REVISION, settings
from .directories import link_directory, unlink_directory


def status():
    config = settings()
    root = Path(config["rvc_root"])
    environment = probe_python(config["rvc_python"], cwd=root if root.is_dir() else None)
    revision = None
    if (root / ".git").exists():
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            revision = r.stdout.strip()
    base_files = ["assets/hubert_base/config.json", "assets/hubert_base/preprocessor_config.json", "assets/hubert_base/pytorch_model.bin", "assets/rmvpe/rmvpe.pt"]
    missing = [f for f in base_files if not (root / f).is_file()]
    from .resources import inventory
    verified_resources = {r["id"]: r["ready"] for r in inventory()}
    return {"environment": environment, "revision": revision, "expected_revision": RVC_REVISION,
            "missing_base_files": missing, "base_verified": verified_resources["rvc_base"], "ready": environment["ready"] and revision == RVC_REVISION and not missing and verified_resources["rvc_base"],
            "validated": False,
            "training_rates": {rate: verified_resources[f"rvc_{rate}"] for rate in ("32k", "40k", "48k")}}


def require_ready():
    state = status()
    if not state["ready"]:
        raise EngineError("RVC 引擎未就绪：需要 GPU、匹配的上游版本和完整基础权重")
    return settings()


def resample_for_training(source: Path, destination: Path, rate: int):
    destination.parent.mkdir(exist_ok=True, parents=True)
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-ac", "1", "-ar", str(rate), "-c:a", "pcm_f32le", "-y", str(destination)], capture_output=True, timeout=60)
    if r.returncode:
        raise EngineError("训练音频重采样失败")
    info = sf.info(destination)
    if info.frames <= 0 or info.samplerate != rate:
        raise EngineError("训练音频格式不正确")


def validate_features(workspace: Path, clip_ids):
    entries = []
    for aid in clip_ids:
        paths = [workspace / "0_gt_wavs" / f"{aid}.wav", workspace / "3_feature768" / f"{aid}.npy",
                 workspace / "2a_f0" / f"{aid}.wav.npy", workspace / "2b-f0nsf" / f"{aid}.wav.npy"]
        if not all(p.is_file() for p in paths):
            raise EngineError("特征提取输出不完整，不能开始训练")
        feature = np.load(paths[1], allow_pickle=False)
        pitch = np.load(paths[2], allow_pickle=False)
        continuous = np.load(paths[3], allow_pickle=False)
        if feature.ndim != 2 or feature.shape[1] != 768 or not len(feature) or not np.isfinite(feature).all():
            raise EngineError("内容特征为空、维度不符或包含无效值")
        if pitch.ndim != 1 or continuous.ndim != 1 or len(pitch) != len(continuous) or not len(pitch) or not np.isfinite(continuous).all() or not np.isfinite(pitch).all():
            raise EngineError("音高特征无效")
        entries.append("|".join([*(str(p) for p in paths), "0"]))
    if not entries:
        raise EngineError("没有执行有效特征提取")
    (workspace / "filelist.txt").write_text("\n".join(entries), encoding="utf-8")


def training_chunks(store, accepted, workspace, rate):
    """Adapt long accepted phrases to the pinned RVC loader without losing their tail.

    The upstream sampler estimates frames from bytes/(3*hop) and drops >900.
    Balanced <=5.4 s core windows plus context fit its FLOAT-WAV buckets.
    """
    derived = []
    for clip in accepted:
        audio, sr = sf.read(store.path(clip["artifact_id"]), dtype="float32")
        if audio.ndim != 1 or len(audio) / sr < 1.1:
            raise EngineError("已接受片段过短或不是单声道，请先复核数据集")
        count = max(1, math.ceil(len(audio) / (5.4 * sr)))
        edges = [round(i * len(audio) / count) for i in range(count + 1)]
        for i, (a, b) in enumerate(zip(edges, edges[1:])):
            start, end = max(0, a - round(.15 * sr)), min(len(audio), b + round(.15 * sr))
            aid = hashlib.sha256(f'{clip["artifact_id"]}:{start}:{end}'.encode()).hexdigest()[:32]
            temporary = workspace / "chunk-input.wav"
            sf.write(temporary, audio[start:end], sr, subtype="FLOAT")
            resample_for_training(temporary, workspace / "0_gt_wavs" / f"{aid}.wav", rate)
            resample_for_training(temporary, workspace / "1_16k_wavs" / f"{aid}.wav", 16000)
            temporary.unlink()
            bucket_length = (workspace / "0_gt_wavs" / f"{aid}.wav").stat().st_size // (3 * (rate // 100))
            if not 100 < bucket_length <= 900:
                raise EngineError("训练格式未满足当前 RVC 分桶限制，禁止静默丢弃片段")
            derived.append({"id": aid, "parent_id": clip["artifact_id"], "sample_rate": sr,
                            "start_sample": start, "end_sample": end, "valid_start_sample": a, "valid_end_sample": b})
    (workspace / "training_audio_manifest.json").write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
    return [d["id"] for d in derived]


def train(store: ArtifactStore, job_id: str, dataset_id: str, *, rate="40k", epochs=200, batch_size=4, save_every=10, resume_id=None, progress=None):
    config = require_ready()
    if not status()["training_rates"].get(rate):
        raise EngineError("此采样率的预训练 G/D 权重尚未下载")
    root, python = Path(config["rvc_root"]), config["rvc_python"]
    manifest = json.loads(store.path(dataset_id).read_text(encoding="utf-8"))
    from voice_workbench_dataset.curation import assign_split
    assign_split(manifest)
    train_ids = set(manifest["split"]["train"])
    accepted = [c for c in manifest["clips"] if c["artifact_id"] in train_ids]
    if len(accepted) < 2:
        raise EngineError("至少需要两个已接受片段；实际音质还需要足够有效时长和覆盖")
    clips = sorted({c["artifact_id"] for c in accepted})
    if len(clips) != len(accepted):
        raise EngineError("数据集包含重复片段引用")
    if len({c["duplicate_group"] for c in accepted}) != len(accepted):
        raise EngineError("数据集包含完全重复音频，请先排除")
    workspace_record = store.allocate_workspace(job_id, name="RVC 特征与训练检查点", metadata={"dataset_id": dataset_id, "rate": rate, "version": "v2", "epochs": epochs})
    workspace = store.path(workspace_record["id"])
    (workspace / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    store.update_job(job_id, "running", metadata={"workspace_id": workspace_record["id"]})
    experiment = "workbench_" + job_id
    logs_link = root / "logs" / experiment
    logs_link.parent.mkdir(exist_ok=True)
    if logs_link.exists() or logs_link.is_symlink():
        raise EngineError("训练实验路径冲突")
    log = workspace / "engine.log"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root)
    env["OMP_NUM_THREADS"] = "4"
    def execute(script, args, stage, timeout=7200):
        if progress:
            progress(stage)
        run_process([python, str(root / script), *map(str, args)], cwd=root, log=log, timeout=timeout, env=env,
                    progress=(lambda: progress(stage)) if progress else None)
    try:
        if resume_id:
            previous = store.get(resume_id)
            if previous["metadata"].get("dataset_id") != dataset_id or previous["metadata"].get("rate") != rate or previous["metadata"].get("version") != "v2":
                raise EngineError("继续训练必须使用相同数据集版本、模型版本和采样率")
            shutil.copytree(store.path(resume_id), workspace, dirs_exist_ok=True, symlinks=False, ignore=shutil.ignore_patterns("indices", "*.index"))
            if not list(workspace.glob("G_*.pth")) or not list(workspace.glob("D_*.pth")):
                raise EngineError("没有可继续训练的 G/D 检查点")
        link_directory(logs_link, workspace)
        numeric_rate = {"32k": 32000, "40k": 40000, "48k": 48000}[rate]
        if progress:
            progress("生成训练音频")
        clips = training_chunks(store, accepted, workspace, numeric_rate)
        if not resume_id:
            execute("train/dataset/extract_f0.py", ["cuda", 1, 0, "0", workspace, "True"], "RMVPE 音高提取")
            execute("train/dataset/extract_hubert_feature.py", ["cuda:0", 1, 0, "0", workspace, "v2", "True"], "HuBERT 内容特征提取")
        validate_features(workspace, clips)
        preset = root / "configs" / ("v1" if rate == "40k" else "v2") / f"{rate}.json"
        (workspace / "config.json").write_text(preset.read_text(encoding="utf-8"), encoding="utf-8")
        execute("train/train.py", ["-e", experiment, "-sr", rate, "-f0", 1, "-bs", batch_size, "-g", "0", "-te", epochs,
                                  "-se", save_every, "-pg", root / f"assets/pretrained_v2/f0G{rate}.pth", "-pd", root / f"assets/pretrained_v2/f0D{rate}.pth",
                                  "-l", 1, "-c", 0, "-sw", 1, "-v", "v2"], "RVC 训练", timeout=7 * 86400)
        final = root / "assets" / "weights" / f"{experiment}.pth"
        if not final.is_file() or final.stat().st_size == 0:
            raise EngineError("训练没有生成最终推理模型；检查点已保留")
        execute("train/train_index.py", [experiment, "v2", workspace / "indices", 4, "auto"], "FAISS 索引构建")
        indices = list(workspace.glob("added_*.index"))
        if len(indices) != 1:
            raise EngineError("索引未生成或不唯一，检查点已保留")
        model = store.import_file(final, name=f"音色_{job_id[:8]}.pth", role="model", job_id=job_id,
                                  metadata={"kind": "rvc_model", "version": "v2", "sample_rate": numeric_rate, "f0": True, "dataset_id": dataset_id,
                                            "rvc_revision": RVC_REVISION, "speaker_id": 0, "epochs": epochs})
        index = store.import_file(indices[0], name=f"音色_{job_id[:8]}.index", role="model", job_id=job_id,
                                  metadata={"kind": "rvc_index", "model_id": model["id"], "dimension": 768})
        final.unlink()
        # Epoch inference exports are redundant once the final model is catalogued.
        for extra in final.parent.glob(f"{experiment}_e*_s*.pth"):
            extra.unlink()
        return {"model": model["id"], "index": index["id"], "workspace": workspace_record["id"]}
    finally:
        # Keep all exports owned by this experiment inside its managed workspace,
        # including on cancellation/failure, so the cache page accounts for them.
        exports = root / "assets" / "weights"
        checkpoint_dir = workspace / "inference_checkpoints"
        for candidate in [exports / f"{experiment}.pth", *exports.glob(f"{experiment}_e*_s*.pth")]:
            if candidate.is_file() and not candidate.is_symlink():
                checkpoint_dir.mkdir(exist_ok=True)
                shutil.move(str(candidate), checkpoint_dir / candidate.name)
        unlink_directory(logs_link, workspace)


def convert(store: ArtifactStore, job_id, audio_id, model_id, *, index_id=None, pitch=0, index_rate=.5, protect=.33, rms_mix=1.0, speaker_id=0, progress=None):
    config = require_ready()
    model = store.get(model_id)
    if model["metadata"].get("kind") != "rvc_model":
        raise EngineError("请选择经过验证的 RVC 推理模型")
    if index_id and store.get(index_id)["metadata"].get("kind") != "rvc_index":
        raise EngineError("索引类型不正确")
    parameters = {"pitch": pitch, "index_rate": index_rate if index_id else 0, "protect": protect, "rms_mix": rms_mix, "speaker_id": speaker_id}
    cache_key = hashlib.sha256(json.dumps({"source": store.get(audio_id)["sha256"], "model": model["sha256"],
                                          "index": store.get(index_id)["sha256"] if index_id else None, "parameters": parameters, "rvc_revision": RVC_REVISION}, sort_keys=True).encode()).hexdigest()
    cached = store.cached(cache_key, job_id)
    if cached:
        return cached["id"]
    workspace_record = store.allocate_workspace(job_id, name="RVC 转换工作目录", metadata={"task": "convert"})
    workspace = store.path(workspace_record["id"])
    store.update_job(job_id, "running", metadata={"workspace_id": workspace_record["id"]})
    output, log = workspace / "converted.wav", workspace / "engine.log"
    args = [config["rvc_python"], "infer/cli.py", "--model", str(store.path(model_id)), "--input", str(store.path(audio_id)), "--output", str(output),
            "--pitch", str(pitch), "--f0-method", "rmvpe", "--index-rate", str(index_rate if index_id else 0),
            "--rms-mix-rate", str(rms_mix), "--protect", str(protect), "--speaker-id", str(speaker_id), "--format", "wav"]
    if index_id:
        args += ["--index", str(store.path(index_id))]
    env = os.environ.copy()
    env["PYTHONPATH"] = config["rvc_root"]
    run_process(args, cwd=Path(config["rvc_root"]), log=log, progress=progress, env=env)
    if not output.is_file() or sf.info(output).frames == 0:
        raise EngineError("RVC 没有生成有效音频")
    from voice_workbench_audio.io import probe
    if abs(sf.info(output).duration - probe(store.path(audio_id))["duration"]) > .1:
        raise EngineError("转换音频时长异常，禁止直接混音")
    return store.import_file(output, name="转换人声.wav", job_id=job_id, metadata={"kind": "converted_audio", "source_id": audio_id,
                             "model_id": model_id, "index_id": index_id, "parameters": parameters, "rvc_revision": RVC_REVISION, "cache_key": cache_key})["id"]
