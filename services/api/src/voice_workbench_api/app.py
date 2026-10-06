from __future__ import annotations

import os
import json
from pathlib import Path
import tempfile

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from voice_workbench_storage import ArtifactStore, Conflict, NotFound
from voice_workbench_audio.io import probe, AudioError
from voice_workbench_dataset import SliceConfig
from voice_workbench_dataset.curation import assign_split
from voice_workbench_dataset.admission import PreparationPolicy
from voice_workbench_engines.separation import status as separation_status
from voice_workbench_engines.registry import SEPARATION_MODELS
from voice_workbench_engines.registry import settings
from voice_workbench_engines.rvc import status as rvc_status
from voice_workbench_engines.process import EngineError, run_process


class CleanupRequest(BaseModel):
    artifact_ids: list[str] | None = Field(default=None, max_length=10000)
    job_id: str | None = None


class RetainRequest(BaseModel):
    retained: bool


class PrepareRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=20)
    min_seconds: float = 2
    target_seconds: float = 8
    max_seconds: float = 15
    silence_seconds: float = .3
    padding_seconds: float = .15
    threshold_db: float | None = None
    force_vocal_separation: bool = False
    separate_backing: bool = False
    dereverb: bool = False
    denoise: bool = False
    check_harmony: bool = False
    mode: str = "review"
    solo_confirmed: bool = False


class ReviewRequest(BaseModel):
    decisions: dict[str, str] = Field(max_length=10000)
    note: str = Field(default="", max_length=2000)


class ResourcesRequest(BaseModel):
    resource_ids: list[str] = Field(min_length=1, max_length=8)
    verify_only: bool = False


class EditRequest(BaseModel):
    clip_id: str
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)


class SeparationRequest(BaseModel):
    source_id: str
    model_id: str
    segment_size: int = Field(default=256, ge=64, le=512)
    overlap: int = Field(default=8, ge=2, le=50)


class TrainRequest(BaseModel):
    dataset_id: str
    rate: str = "40k"
    epochs: int = Field(default=200, ge=1, le=10000)
    batch_size: int = Field(default=4, ge=1, le=64)
    save_every: int = Field(default=10, ge=1, le=1000)
    resume_id: str | None = None


class ConvertRequest(BaseModel):
    audio_id: str
    model_id: str
    index_id: str | None = None
    pitch: int = Field(default=0, ge=-24, le=24)
    index_rate: float = Field(default=.5, ge=0, le=1)
    protect: float = Field(default=.33, ge=0, le=.5)
    rms_mix: float = Field(default=1, ge=0, le=1)
    speaker_id: int = Field(default=0, ge=0, le=109)


class MixRequest(BaseModel):
    vocal_id: str
    instrumental_id: str
    vocal_db: float = Field(default=0, ge=-40, le=12)
    instrumental_db: float = Field(default=0, ge=-40, le=12)
    format: str = "wav"


class CoverRequest(BaseModel):
    source_id: str
    model_id: str
    index_id: str | None = None
    separation_model: str = "vocals_melband_unwa"
    pitch: int = Field(default=0, ge=-24, le=24)
    index_rate: float = Field(default=.5, ge=0, le=1)
    protect: float = Field(default=.33, ge=0, le=.5)
    rms_mix: float = Field(default=1, ge=0, le=1)
    speaker_id: int = Field(default=0, ge=0, le=109)
    segment_size: int = Field(default=256, ge=64, le=512)
    overlap: int = Field(default=8, ge=2, le=50)
    vocal_db: float = Field(default=0, ge=-40, le=12)
    instrumental_db: float = Field(default=0, ge=-40, le=12)
    format: str = "wav"


def create_app(root=None, web_dir=None):
    app = FastAPI(title="AI 翻唱工作台", version="0.0.1")
    store = ArtifactStore(root or os.environ.get("WORKBENCH_RUNTIME", "runtime"))
    app.state.store = store

    @app.exception_handler(NotFound)
    async def not_found(request: Request, error: NotFound):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request: Request, error: Conflict):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(EngineError)
    async def engine_error(request: Request, error: EngineError):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.0.1"}

    @app.get("/api/resources")
    def resources():
        from voice_workbench_engines.resources import inventory
        return {"items": inventory()}

    @app.post("/api/resources", status_code=202)
    def submit_resources(body: ResourcesRequest):
        from voice_workbench_engines.resources import selected
        selected(body.resource_ids)
        return store.create_job("resources", metadata=body.model_dump())

    @app.get("/api/artifacts")
    def inventory():
        return store.inventory()

    @app.post("/api/cache/preview")
    def preview(body: CleanupRequest):
        return store.preview(**body.model_dump())

    @app.post("/api/cache/cleanup")
    def cleanup(body: CleanupRequest):
        return store.cleanup(**body.model_dump())

    @app.patch("/api/artifacts/{artifact_id}/retention")
    def retain(artifact_id: str, body: RetainRequest):
        return store.retain(artifact_id, body.retained)

    @app.get("/api/artifacts/{artifact_id}/file")
    def file(artifact_id: str):
        artifact = store.get(artifact_id)
        path = store.path(artifact_id)
        if not path.is_file():
            raise Conflict("此产物是工作目录，不能作为单个文件下载")
        return FileResponse(path, filename=artifact["name"], content_disposition_type="inline")

    @app.get("/api/jobs")
    def jobs():
        return store.jobs()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return store.job(job_id)

    @app.get("/api/jobs/{job_id}/logs")
    def job_logs(job_id: str):
        job = store.job(job_id)
        paths = []
        if job["metadata"].get("workspace_id"):
            try:
                paths.append(store.path(job["metadata"]["workspace_id"]) / "engine.log")
            except NotFound:
                pass
        for artifact in store.inventory()["items"]:
            if artifact["job_id"] == job_id and artifact["metadata"].get("kind") == "engine_log":
                paths.append(store.path(artifact["id"]))
        content = []
        for path in paths:
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 65536))
                    content.append(stream.read(65536).decode("utf-8", errors="replace"))
        return {"text": "\n".join(content)[-65536:]}

    @app.get("/api/engines")
    def engines():
        return current_engines()

    def current_engines():
        reported = store.runtime("engines")
        if reported:
            return reported
        # API containers intentionally have no GPU. The worker's observation is
        # the authority; do not disable a healthy GPU by probing the API host.
        sep, rvc = separation_status(), rvc_status()
        sep["environment"] = {"ready": False, "reason": "工作进程尚未上报状态或心跳已过期"}
        for model in sep["models"]:
            model["ready"] = False
        rvc["ready"] = False
        rvc["environment"] = {"ready": False, "reason": "工作进程尚未上报状态或心跳已过期"}
        return {"separation": sep, "rvc": rvc}

    @app.post("/api/separation", status_code=202)
    def submit_separation(body: SeparationRequest):
        if body.model_id not in SEPARATION_MODELS:
            raise ValueError("不支持的分离模型")
        source = store.get(body.source_id)
        spec = SEPARATION_MODELS[body.model_id]
        if spec["task"] in {"lead_backing", "dereverb", "denoise"} and not (
            source["metadata"].get("kind") == "dry_vocal" or source["metadata"].get("stem") in {"vocals", "lead", "dry", "clean"}
        ):
            raise ValueError("和声分离与去混响需要先输入人声音轨")
        model = next(m for m in current_engines()["separation"]["models"] if m["id"] == body.model_id)
        if not model["ready"]:
            raise Conflict("分离引擎未就绪：需要 GPU、独立引擎环境及已下载权重")
        return store.create_job("separation", [body.source_id], body.model_dump())

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        with store.connect(write=True) as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise NotFound("任务不存在")
            if row["status"] == "queued":
                db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
                db.execute("DELETE FROM holds WHERE job_id=?", (job_id,))
            elif row["status"] == "running":
                metadata = json.loads(db.execute("SELECT metadata FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
                metadata["cancel_requested"] = True
                db.execute("UPDATE jobs SET metadata=? WHERE id=?", (json.dumps(metadata), job_id))
        return store.job(job_id)

    @app.post("/api/sources", status_code=201)
    def upload(file: UploadFile = File(...), kind: str = Form("dry_vocal")):
        if kind not in {"dry_vocal", "song"}:
            raise ValueError("素材类型只能是干声或歌曲")
        filename = Path(file.filename or "audio").name
        with tempfile.TemporaryDirectory(dir=store.root, prefix="upload-") as temp:
            path = Path(temp) / "input"
            size = 0
            with path.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 512 * 1024**2:
                        raise HTTPException(413, "单个文件暂限 512 MB")
                    out.write(chunk)
            info = probe(path)
            return store.import_file(path, name=filename, role="source", metadata={"kind": kind, "audio": info})

    @app.post("/api/datasets/prepare", status_code=202)
    def prepare(body: PrepareRequest):
        preprocessing_fields = {"force_vocal_separation", "separate_backing", "dereverb", "denoise", "check_harmony"}
        policy = PreparationPolicy(body.mode, body.solo_confirmed)
        policy.validate()
        config = SliceConfig(**body.model_dump(exclude={"source_ids", "mode", "solo_confirmed", *preprocessing_fields}))
        config.validate()
        required = set()
        for aid in body.source_ids:
            source = store.get(aid)
            if source["metadata"].get("kind") not in {"song", "dry_vocal"} and source["metadata"].get("stem") not in {"vocals", "lead", "dry", "clean"}:
                raise ValueError("请选择歌曲或人声音频")
            if source["metadata"].get("kind") == "song" or body.force_vocal_separation:
                required.add("vocals_melband_unwa")
        if body.separate_backing or body.check_harmony or body.mode == "automatic":
            required.add("lead_melband_aufr33")
        if body.dereverb:
            required.add("dereverb_melband_anvuew")
        if body.denoise:
            required.add("denoise_melband_aufr33")
        if required:
            models = {m["id"]: m for m in current_engines()["separation"]["models"]}
            if not all(models[m]["ready"] for m in required):
                raise Conflict("自动清洗需要 GPU 分离引擎和相应权重；当前只可处理不带伴奏的干声")
        return store.create_job("dataset_prepare", body.source_ids, {"source_ids": body.source_ids, "config": config.dict(),
                                "policy": policy.dict(), "preprocessing": body.model_dump(include=preprocessing_fields)})

    def read_manifest(artifact_id):
        artifact = store.get(artifact_id)
        if artifact["metadata"].get("kind") != "dataset_manifest":
            raise ValueError("此文件不是数据集清单")
        return json.loads(store.path(artifact_id).read_text(encoding="utf-8"))

    def validate_model(model_id, index_id=None):
        model = store.get(model_id)
        if model["metadata"].get("kind") != "rvc_model":
            raise ValueError("请选择 RVC 音色模型")
        if index_id:
            index = store.get(index_id)
            if index["metadata"].get("kind") != "rvc_index" or index["metadata"].get("model_id") != model_id:
                raise ValueError("索引必须与所选音色模型关联")

    @app.post("/api/models/import", status_code=201)
    def import_model(model: UploadFile = File(...), index: UploadFile | None = File(default=None)):
        config = settings()
        if not config["rvc_python"] or not Path(config["rvc_python"]).is_file():
            raise Conflict("请先配置独立 RVC 环境，用于安全检查模型结构")
        if Path(model.filename or "").suffix.lower() != ".pth" or (index and Path(index.filename or "").suffix.lower() != ".index"):
            raise ValueError("需要 .pth 推理权重和可选 .index 索引")
        with tempfile.TemporaryDirectory(dir=store.root, prefix="model-import-") as temp:
            work = Path(temp)
            for upload, filename in ((model, "model.pth"), (index, "model.index")):
                if upload:
                    total = 0
                    with (work / filename).open("wb") as out:
                        while chunk := upload.file.read(1024 * 1024):
                            total += len(chunk)
                            if total > 1024**3:
                                raise HTTPException(413, "单个模型文件不超过 1 GB")
                            out.write(chunk)
            import voice_workbench_engines.model_probe as validator
            args = [config["rvc_python"], str(Path(validator.__file__).resolve()), str(work / "model.pth"), str(work / "metadata.json")]
            if index:
                args.append(str(work / "model.index"))
            try:
                run_process(args, cwd=work, log=work / "probe.log", timeout=60)
            except EngineError:
                raise ValueError("模型解析失败：文件不兼容、索引不匹配或不是安全的 RVC 推理权重")
            info = json.loads((work / "metadata.json").read_text())
            result = store.import_file(work / "model.pth", name=Path(model.filename).name, role="model", metadata=info)
            index_result = None
            if index:
                index_result = store.import_file(work / "model.index", name=Path(index.filename).name, role="model",
                                                 metadata={"kind": "rvc_index", "model_id": result["id"], "dimension": info["index_dimension"]})
            return {"model": result, "index": index_result}

    @app.post("/api/training", status_code=202)
    def submit_training(body: TrainRequest):
        if body.rate not in {"32k", "40k", "48k"}:
            raise ValueError("采样率应为 32k、40k 或 48k")
        manifest = read_manifest(body.dataset_id)
        assign_split(manifest)
        accepted = manifest["split"]["train"]
        if len(accepted) < 2:
            raise ValueError("按来源划分验证数据后，训练集仍需至少两个有效片段")
        state = current_engines()["rvc"]
        if not state["ready"] or not state["training_rates"][body.rate]:
            raise Conflict("RVC 训练引擎未就绪，需要 GPU、基础权重及所选采样率的预训练权重")
        return store.create_job("rvc_train", [body.dataset_id, *accepted, *([body.resume_id] if body.resume_id else [])], body.model_dump())

    @app.post("/api/conversion", status_code=202)
    def submit_conversion(body: ConvertRequest):
        validate_model(body.model_id, body.index_id)
        store.get(body.audio_id)
        if not current_engines()["rvc"]["ready"]:
            raise Conflict("RVC 转换引擎未就绪")
        return store.create_job("rvc_convert", [body.audio_id, body.model_id, *([body.index_id] if body.index_id else [])], body.model_dump())

    @app.post("/api/mixing", status_code=202)
    def submit_mix(body: MixRequest):
        if body.format not in {"wav", "flac", "mp3"}:
            raise ValueError("不支持的导出格式")
        return store.create_job("mix", [body.vocal_id, body.instrumental_id], body.model_dump())

    @app.post("/api/covers", status_code=202)
    def submit_cover(body: CoverRequest):
        validate_model(body.model_id, body.index_id)
        store.get(body.source_id)
        if body.format not in {"wav", "flac", "mp3"}:
            raise ValueError("不支持的导出格式")
        observed = current_engines()
        if not observed["rvc"]["ready"] or not any(m["id"] == body.separation_model and m["task"] == "vocals" and m["ready"] for m in observed["separation"]["models"]):
            raise Conflict("制作翻唱需要 RVC 和人声/伴奏分离引擎均已就绪")
        return store.create_job("cover", [body.source_id, body.model_id, *([body.index_id] if body.index_id else [])], body.model_dump())

    @app.get("/api/datasets/{artifact_id}")
    def dataset(artifact_id: str):
        manifest = read_manifest(artifact_id)
        def available(aid):
            try:
                store.path(aid)
                return True
            except (NotFound, Conflict):
                return False
        for clip in manifest["clips"]:
            clip["available"] = available(clip["artifact_id"])
        for source in manifest.get("sources", []):
            for stage in source.get("processing", {}).get("stages", []):
                for key in ("input", "candidate", "selected", "report"):
                    stage[f"{key}_available"] = available(stage[f"{key}_id"])
            harmony = source.get("processing", {}).get("harmony")
            if harmony:
                for key in ("lead", "backing", "report"):
                    harmony[f"{key}_available"] = available(harmony[f"{key}_id"])
        return manifest

    @app.post("/api/datasets/{artifact_id}/export", status_code=202)
    def export(artifact_id: str):
        manifest = read_manifest(artifact_id)
        ids = [c["artifact_id"] for c in manifest["clips"] if c["status"] == "accepted"]
        if not ids:
            raise ValueError("请先审查并接受片段")
        return store.create_job("dataset_export", [artifact_id, *ids], {"dataset_id": artifact_id})

    @app.post("/api/datasets/{artifact_id}/edit", status_code=202)
    def edit(artifact_id: str, body: EditRequest):
        manifest = read_manifest(artifact_id)
        clip = next((c for c in manifest["clips"] if c["artifact_id"] == body.clip_id), None)
        if clip is None:
            raise ValueError("片段不属于此数据集")
        source = next(s for s in manifest["sources"] if s["id"] == clip["source_id"])
        if not source.get("master_id"):
            raise Conflict("来源没有可编辑的工作母版")
        return store.create_job("dataset_edit", [artifact_id, source["master_id"], *(c["artifact_id"] for c in manifest["clips"])],
                                {"dataset_id": artifact_id, **body.model_dump()})

    @app.get("/api/datasets/{artifact_id}/waveform/{clip_id}")
    def waveform(artifact_id: str, clip_id: str):
        manifest = read_manifest(artifact_id)
        if not any(c["artifact_id"] == clip_id for c in manifest["clips"]):
            raise ValueError("片段不属于此数据集")
        import numpy as np
        import soundfile as sf
        # Read only while protected from cache cleanup.
        with store.connect(write=True):
            x, sr = sf.read(store.path(clip_id), dtype="float32")
            peaks = [float(np.max(np.abs(block))) for block in np.array_split(x, min(500, len(x))) if len(block)]
            return {"peaks": peaks, "duration": len(x) / sr}

    @app.post("/api/datasets/{artifact_id}/review", status_code=201)
    def review(artifact_id: str, body: ReviewRequest):
        manifest = read_manifest(artifact_id)
        known = {c["artifact_id"] for c in manifest["clips"]}
        if not set(body.decisions) <= known or not set(body.decisions.values()) <= {"accepted", "review", "excluded"}:
            raise ValueError("无效的片段或审查决定")
        for clip in manifest["clips"]:
            if clip["artifact_id"] in body.decisions:
                clip["status"] = body.decisions[clip["artifact_id"]]
                clip["decision"] = {"origin": "manual", "note": body.note}
        accepted = [c["artifact_id"] for c in manifest["clips"] if c["status"] == "accepted"]
        store.promote_dataset(accepted)
        manifest["parent_manifest_id"] = artifact_id
        for status in ("accepted", "review", "excluded"):
            manifest["summary"][f"{status}_count"] = sum(c["status"] == status for c in manifest["clips"])
        assign_split(manifest)
        from io import BytesIO
        return store.import_stream(BytesIO(json.dumps(manifest, ensure_ascii=False, indent=2).encode()),
                                   name="数据集审查清单.json", role="dataset", metadata={"kind": "dataset_manifest", "summary": manifest["summary"], "parent_id": artifact_id})

    dist = Path(web_dir or "apps/web/dist")
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def app_factory():
    return create_app()
