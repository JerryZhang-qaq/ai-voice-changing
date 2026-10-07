from __future__ import annotations

import json
import traceback
import time

from voice_workbench_dataset import SliceConfig, prepare_dataset
from voice_workbench_storage import ArtifactStore
from voice_workbench_engines.separation import separate
from voice_workbench_engines.rvc import train, convert
from voice_workbench_audio.mix import mix
from voice_workbench_dataset.curation import export_dataset
from voice_workbench_dataset.quality import guard_cleaning
from voice_workbench_dataset.admission import PreparationPolicy
from voice_workbench_dataset.harmony import check_harmony
from voice_workbench_engines.registry import SEPARATION_MODELS


class Cancelled(Exception):
    pass


class WorkerStopping(Cancelled):
    pass


def run_one(store: ArtifactStore):
    with store.connect(write=True) as db:
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return False
        job = dict(row)
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    config = json.loads(job["metadata"])
    store.update_job(job["id"], "running", metadata={"started_at": time.time()})
    last_write = [0., None]
    def progress(stage, done=None, total=None):
        current = store.job(job["id"])
        if current["metadata"].get("cancel_requested"):
            raise Cancelled()
        changed = current["metadata"].get("stage") != stage
        if changed or time.monotonic() - last_write[0] > .5 or done == total and total is not None:
            values = {"stage": stage}
            if changed or total is not None:
                values.update(done=done, total=total, eta_seconds=None, unit=None)
            store.update_job(job["id"], "running", metadata=values)
            last_write[:] = [time.monotonic(), stage]
    try:
        if job["kind"] == "resources":
            from voice_workbench_engines.resources import install_resources
            result = install_resources(store, job["id"], progress=progress, **config)
        elif job["kind"] in {"storage_cleanup", "storage_delete"}:
            progress("准备清理")
            tick = lambda done, total: progress("删除所选文件", done, total)
            outcome = store.cleanup(**config, progress=tick) if job["kind"] == "storage_cleanup" else store.delete_artifacts(config["artifact_ids"], progress=tick)
            store.update_job(job["id"], "running", metadata={"cleanup_result": outcome})
            result = None
        elif job["kind"] == "dataset_prepare":
            progress("准备数据", 0, len(config["source_ids"]))
            options = config.get("preprocessing", {})
            policy = PreparationPolicy(**config.get("policy", {}))
            policy.validate()
            processed = []
            processing_records = []
            for aid in config["source_ids"]:
                source = store.get(aid)
                record = {"original_id": aid, "stages": []}
                steps = []
                if source["metadata"].get("kind") == "song" or options.get("force_vocal_separation"):
                    steps.append(("vocals_melband_unwa", "vocals", "提取训练人声"))
                if options.get("separate_backing") or options.get("check_harmony") or policy.mode == "automatic":
                    steps.append(("lead_melband_aufr33", "harmony_check", "筛除复杂和声"))
                if options.get("dereverb"):
                    steps.append(("dereverb_melband_anvuew", "dry", "温和去混响"))
                if options.get("denoise"):
                    steps.append(("denoise_melband_aufr33", "clean", "按需降噪"))
                for model_id, stem, stage in steps:
                    outputs = separate(store, job["id"], aid, model_id, progress=lambda: progress(stage, len(processed), len(config["source_ids"])))
                    if stem == "harmony_check":
                        record["harmony"] = check_harmony(store, job["id"], aid, outputs,
                                                         lambda: progress("检查重叠人声", len(processed), len(config["source_ids"])))
                        if record["harmony"]["status"] == "rejected":
                            break
                        # Passing the screening does not require replacing a
                        # good solo recording with a potentially altered stem.
                        continue
                    comparison = guard_cleaning(store, job["id"], aid, outputs[stem], SEPARATION_MODELS[model_id]["task"],
                                                lambda: progress("检查清洗前后变化", len(processed), len(config["source_ids"])))
                    # Weight hashes and engine parameters live on the output
                    # artifact. Freeze them in the manifest before cache cleanup.
                    comparison["engine"] = store.get(outputs[stem])["metadata"]
                    record["stages"].append(comparison)
                    aid = comparison["selected_id"]
                processed.append(aid)
                processing_records.append(record)
            result = prepare_dataset(store, job["id"], processed, SliceConfig(**config["config"]), progress, processing_records=processing_records, policy=policy)
        elif job["kind"] == "dataset_edit":
            from voice_workbench_dataset.editing import edit_clip
            result = edit_clip(store, job["id"], progress=progress, **config)
        elif job["kind"] == "song_separation":
            source = store.get(config["source_id"])
            if source["metadata"].get("kind") == "song":
                progress("分离人声与伴奏")
                outputs = separate(store, job["id"], config["source_id"], "vocals_melband_unwa", segment_size=config["segment_size"], overlap=config["overlap"], progress=lambda: progress("分离人声与伴奏"))
            else:
                outputs = {"vocals": config["source_id"]}
            store.update_job(job["id"], "running", metadata={"outputs": outputs})
            progress("分离主唱与和声")
            outputs.update(separate(store, job["id"], outputs["vocals"], "lead_melband_aufr33", segment_size=config["segment_size"], overlap=config["overlap"], progress=lambda: progress("分离主唱与和声")))
            store.update_job(job["id"], "running", metadata={"outputs": outputs})
            result = outputs["lead"]
        elif job["kind"] == "separation":
            progress("加载分离引擎")
            outputs = separate(store, job["id"], config["source_id"], config["model_id"],
                               segment_size=config["segment_size"], overlap=config["overlap"], progress=lambda: progress("分离处理中"))
            result = next(iter(outputs.values()))
            store.update_job(job["id"], "running", metadata={"outputs": outputs})
        elif job["kind"] == "rvc_train":
            outputs = train(store, job["id"], progress=lambda stage: progress(stage), **config)
            result = outputs["model"]
            store.update_job(job["id"], "running", metadata={"outputs": outputs})
        elif job["kind"] == "rvc_convert":
            result = convert(store, job["id"], progress=lambda: progress("RVC 音色转换"), **config)
        elif job["kind"] == "mix":
            result = mix(store, job["id"], progress=lambda: progress("混音导出"), **config)
        elif job["kind"] == "dataset_export":
            result = export_dataset(store, job["id"], config["dataset_id"], lambda done,total: progress("导出音频合集", done, total), prepared=config.get("prepared", False))
        elif job["kind"] == "cover":
            progress("分离人声与伴奏")
            stems = separate(store, job["id"], config["source_id"], config["separation_model"], segment_size=config["segment_size"], overlap=config["overlap"], progress=lambda: progress("分离人声与伴奏"))
            store.update_job(job["id"], "running", metadata={"outputs": stems})
            if config.get("separate_backing", True) or config.get("include_backing"):
                progress("分离主唱与和声")
                stems.update(separate(store, job["id"], stems["vocals"], "lead_melband_aufr33", segment_size=config["segment_size"], overlap=config["overlap"], progress=lambda: progress("分离主唱与和声")))
                store.update_job(job["id"], "running", metadata={"outputs": stems})
            progress("RVC 音色转换")
            vocal = convert(store, job["id"], stems.get("lead", stems["vocals"]), config["model_id"], index_id=config["index_id"], pitch=config["pitch"],
                            index_rate=config["index_rate"], protect=config["protect"], rms_mix=config["rms_mix"], speaker_id=config["speaker_id"], progress=lambda: progress("RVC 音色转换"))
            stems["converted"] = vocal
            store.update_job(job["id"], "running", metadata={"outputs": stems})
            progress("混音导出")
            result = mix(store, job["id"], vocal, stems["instrumental"], backing_id=stems.get("backing") if config.get("include_backing") else None, backing_db=config.get("backing_db", 0), vocal_db=config["vocal_db"], instrumental_db=config["instrumental_db"], format=config["format"], progress=lambda: progress("混音导出"))
        else:
            raise ValueError("不支持的任务类型")
        last_total = store.job(job["id"])["metadata"].get("total")
        progress("完成", last_total, last_total)
        store.update_job(job["id"], "completed", metadata={"result_id": result})
    except Cancelled as error:
        store.update_job(job["id"], "cancelled")
        if isinstance(error, WorkerStopping):
            raise
    except Exception as error:
        traceback.print_exc()
        store.update_job(job["id"], "failed", error=str(error))
    return True
