from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import soundfile as sf

from voice_workbench_storage import ArtifactStore
from .process import EngineError, probe_python, run_process
from .registry import SEPARATION_MODELS, settings
from .separation_policy import parameters


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def status():
    config = settings()
    environment = probe_python(config["separation_python"], module="audio-separator")
    models = []
    from .resources import inventory
    verified_models = {r["id"]: r["ready"] for r in inventory()}
    for mid, spec in SEPARATION_MODELS.items():
        local = all((Path(config["separation_models"]) / spec[k]).is_file() for k in ("filename", "config"))
        models.append({"id": mid, **spec, "downloaded": local and verified_models[mid], "ready": local and verified_models[mid] and environment["ready"], "validated": False})
    return {"environment": environment, "models": models, "recommended_package": "audio-separator==0.47.0"}


def separate(store: ArtifactStore, job_id, source_id, model_id, *, segment_size=256, overlap=None, profile="balanced", keep_stems=None, progress=None, session=None, reuse_quality_cache=False):
    if model_id not in SEPARATION_MODELS:
        raise ValueError("不支持的分离模型")
    spec, config = SEPARATION_MODELS[model_id], settings()
    params = parameters(model_id, profile, segment_size, overlap)
    segment_size, overlap = params["segment_size"], params["overlap"]
    wanted = set(spec["stems"].values()) if keep_stems is None else set(keep_stems)
    if not wanted or not wanted <= set(spec["stems"].values()):
        raise ValueError("请选择此模型支持的输出声部")
    identity = tuple((str(Path(config["separation_models"]) / spec[key]),
                      (Path(config["separation_models"]) / spec[key]).stat().st_mtime_ns,
                      (Path(config["separation_models"]) / spec[key]).stat().st_ctime_ns,
                      (Path(config["separation_models"]) / spec[key]).stat().st_size)
                     for key in ("filename", "config") if (Path(config["separation_models"]) / spec[key]).is_file())
    ready = session.ready if session and session.ready and session.model_states.get(model_id) == identity else status()
    if session:
        session.ready = ready
        session.model_states[model_id] = identity
    if not ready["environment"]["ready"]:
        raise EngineError(ready["environment"].get("reason", "GPU 引擎未就绪"))
    if not next(m for m in ready["models"] if m["id"] == model_id)["downloaded"]:
        raise EngineError("权重及模型配置尚未下载")
    digest = (lambda path: session.digest(path, sha256_file)) if session else sha256_file
    weight_hash = digest(Path(config["separation_models"]) / spec["filename"])
    config_hash = digest(Path(config["separation_models"]) / spec["config"])
    source = store.get(source_id)
    from voice_workbench_audio.io import probe
    source_duration = probe(store.path(source_id))["duration"]
    legacy_key = {"source": source["sha256"], "singer": source["metadata"].get("singer"), "purpose": source["metadata"].get("purpose"), "weight": weight_hash, "config": config_hash, "engine_version": ready["environment"].get("package_version"),
                "segment_size": segment_size, "overlap": overlap, "task": spec["task"]}
    base_key = {**legacy_key, "policy": params}
    keys = {stem: hashlib.sha256(json.dumps({**base_key, "stem": stem}, sort_keys=True).encode()).hexdigest()
            for stem in spec["stems"].values() if stem in wanted}
    ids = {}
    for stem, key in keys.items():
        cached = store.cached(key, job_id)
        if cached:
            ids[stem] = cached["id"]
    if len(ids) != len(keys) and (reuse_quality_cache or (profile == "quality" and overlap == 8)):
        # Explicitly prefer an existing high-overlap FP32 result, rather than
        # relabeling it as accelerated output or recomputing an entire song.
        previous = {}
        quality_params = parameters(model_id, "quality", segment_size)
        for stem in keys:
            for candidate in ({**legacy_key, "overlap": 8, "policy": quality_params},
                              {**legacy_key, "overlap": 8}):
                key = hashlib.sha256(json.dumps({**candidate, "stem": stem}, sort_keys=True).encode()).hexdigest()
                cached = store.cached(key, job_id)
                if cached:
                    previous[stem] = cached["id"]
                    break
        if len(previous) == len(keys):
            ids = previous
    if len(ids) == len(keys):
        store.update_job(job_id, "running", metadata={"engine_progress": {"state": "cached", "done": 1, "total": 1,
                         "cache_hit": True, "parameters": store.get(next(iter(ids.values())))["metadata"].get("parameters"), "eta_seconds": 0}})
        return ids
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as tmp:
        work = Path(tmp)
        log = session.log if session else work / "engine.log"
        request = {"input": str(store.path(source_id)), "model_dir": config["separation_models"], "filename": spec["filename"], "config": spec["config"],
                   "output_dir": str(work), "response": str(work / "response.json"), "segment_size": segment_size, "overlap": overlap,
                   "stems": spec["stems"], "keep_stems": list(keys), **params,
                   "weight_sha256": weight_hash, "config_sha256": config_hash}
        request_path = work / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        from .telemetry import LogTelemetry
        namespace = "engine_progress" if store.job(job_id)["kind"] == "dataset_prepare" else None
        observation = LogTelemetry(store, job_id, log, namespace=namespace)
        store.update_job(job_id, "running", metadata={"engine_progress": {"state": "loading", "done": 0, "total": None},
                         "live_engine_log": str(log.relative_to(store.root))})
        def tick():
            if progress:
                progress()
            observation.tick()
        try:
            if session:
                session.run(request_path, config["separation_python"], tick)
            else:
                run_process([config["separation_python"], str(Path(__file__).with_name("separation_bridge.py")), str(request_path)], cwd=work, log=log, progress=tick)
            observation.tick()
            result = json.loads(Path(request["response"]).read_text())
            produced = []
            for name in result["outputs"]:
                output = (work / name).resolve()
                if output.parent != work or not output.is_file():
                    raise EngineError("分离引擎返回无效输出路径")
                produced.append(output)
            for stem in keys:
                matches = [p for p in produced if p.stem == stem]
                if len(matches) != 1:
                    raise EngineError(f"分离输出缺少预期声部 {stem}，请检查权重配置")
                info = sf.info(matches[0])
                if info.frames <= 0 or (source_duration and abs(info.duration - source_duration) > .1):
                    raise EngineError("分离输出时长异常")
                import numpy as np
                if any(not np.isfinite(block).all() for block in sf.blocks(matches[0], blocksize=65536, dtype="float32")):
                    raise EngineError("分离输出包含无效采样")
                if stem in ids:
                    continue
                artifact = store.import_file(matches[0], name=f'{Path(source["name"]).stem}_{stem}.wav', job_id=job_id,
                                             metadata={"kind": "separated_audio", "stem": stem, "task": spec["task"], "source_id": source_id,
                                                       "model_id": model_id, "weight_sha256": weight_hash, "config_sha256": config_hash,
                                                       "source_group": source["metadata"].get("source_group", source["sha256"]),
                                                       "cache_key": keys[stem],
                                                       "engine_version": ready["environment"].get("package_version"),
                                                       "requested_parameters": params, "parameters": result.get("parameters", params),
                                                       "performance": result.get("performance", {})})
                ids[stem] = artifact["id"]
        finally:
            if not session and log.exists():
                try:
                    store.import_file(log, name="分离引擎日志.txt", job_id=job_id, metadata={"kind": "engine_log"})
                except Exception:
                    pass
            if not session:
                store.update_job(job_id, "running", metadata={"live_engine_log": None})
    return ids
