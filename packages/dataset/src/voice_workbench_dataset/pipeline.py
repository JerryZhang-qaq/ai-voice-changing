from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf

from voice_workbench_audio import decode, inspect_audio, mono
from voice_workbench_storage import ArtifactStore
from voice_workbench_storage import NotFound
from .segmentation import SliceConfig, segment
from .duplicates import NearDuplicateIndex, DUPLICATE_VERSION
from .quality import GUARD_VERSION
from .analysis import analyze_file, ANALYSIS_VERSION
from .admission import PreparationPolicy, decide_clip, plateau_ratio, ADMISSION_VERSION
from .curation import assign_split


PROCESSOR_VERSION = "solo-pipeline-3"


def prepare_dataset(store: ArtifactStore, job_id: str, source_ids: list[str], config: SliceConfig, progress=None, *, processing_records=None, policy=None):
    config.validate()
    policy = policy or PreparationPolicy()
    policy.validate()
    records = processing_records or [{"original_id": aid, "stages": []} for aid in source_ids]
    if len(records) != len(source_ids):
        raise ValueError("来源处理记录与素材不匹配")
    cache_key = hashlib.sha256(json.dumps({"sources": [(a, store.get(a)["sha256"]) for a in source_ids], "config": config.dict(), "processor": PROCESSOR_VERSION, "processing": records, "policy": policy.dict()}, sort_keys=True).encode()).hexdigest()
    cached = store.cached(cache_key, job_id)
    if cached:
        previous = json.loads(store.path(cached["id"]).read_text(encoding="utf-8"))
        try:
            store.hold_inputs(job_id, [c["artifact_id"] for c in previous["clips"]])
            return cached["id"]
        except NotFound:
            pass  # A cleaned clip means this data preparation must be rebuilt.
    dataset = {"schema_version": 3, "processor_version": PROCESSOR_VERSION, "config": config.dict(), "policy": policy.dict(),
               "quality_rules": {"preservation_version": GUARD_VERSION, "duplicate_version": DUPLICATE_VERSION, "admission_version": ADMISSION_VERSION, "analysis_version": ANALYSIS_VERSION, "calibrated": False},
               "quality_mode": policy.mode, "sources": [], "clips": [], "validation": {"status": "not_split"}}
    seen = {}
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as tmp:
        work = Path(tmp)
        duplicates = NearDuplicateIndex(work)
        for index, source_id in enumerate(source_ids):
            if progress:
                progress("解码与诊断", index, len(source_ids))
            source = store.get(source_id)
            processing = records[index]
            harmony = processing.get("harmony", {})
            if harmony.get("status") == "rejected":
                dataset["sources"].append({"id": source_id, "original_id": processing["original_id"], "sha256": source["sha256"],
                                           "processing": processing, "status": "excluded", "reasons": ["COMPLEX_HARMONY"],
                                           "analysis": {"activity_seconds": 0.}})
                continue
            master_path = work / "master.wav"
            provenance = decode(store.path(source_id), master_path)
            x, sr = sf.read(master_path, dtype="float32", always_2d=True)
            mono_x, channel = mono(x)
            del x
            diagnosis = inspect_audio(mono_x, sr)
            source_reasons = list(provenance.get("requires_review", [])) + list(channel.get("requires_review", []))
            source_reasons.extend(reason for stage in processing["stages"] for reason in stage["reasons"])
            dc = diagnosis["dc_offset"]
            clean = mono_x - np.float32(dc) if abs(dc) > 1e-4 else mono_x
            cleaned_diagnosis = inspect_audio(clean, sr)
            used_tasks = {s["task"] for s in processing["stages"] if s["decision"] == "candidate"}
            cleanup = {"dc_removed": float(dc) if abs(dc) > 1e-4 else 0.0, "denoise": "denoise" in used_tasks,
                       "dereverb": "dereverb" in used_tasks, "separation": "vocals" in used_tasks,
                       "backing_separation": "lead_backing" in used_tasks, "after_diagnosis": cleaned_diagnosis}
            sf.write(master_path, clean, sr, subtype="FLOAT")
            master = store.import_file(master_path, name="工作母版.wav", job_id=job_id,
                                       metadata={"source_id": source_id, "processor_version": PROCESSOR_VERSION, "cleanup": cleanup, "channel": channel})
            voice, _ = analyze_file(master_path, work, (lambda: progress("分析音高与发音边界", index, len(source_ids))) if progress else None)
            slices, analysis = segment(clean, sr, config, voice=voice)
            analysis["singing"] = voice.summary()
            dataset["sources"].append({"id": source_id, "original_id": processing["original_id"], "sha256": source["sha256"], "master_id": master["id"], "processing": processing,
                                       "sample_rate": sr, "provenance": provenance, "diagnosis": diagnosis, "channel": channel, "cleanup": cleanup, "analysis": analysis, "reasons": source_reasons})
            for clip_index, interval in enumerate(slices):
                if progress:
                    progress("切片与质量记录", index, len(source_ids))
                audio = clean[interval["start_sample"]:interval["end_sample"]]
                core = clean[interval["valid_start_sample"]:interval["valid_end_sample"]]
                metrics = {**inspect_audio(audio, sr), "plateau_ratio": plateau_ratio(audio)}
                singing = voice.region(interval["valid_start_sample"] / sr, interval["valid_end_sample"] / sr).summary()
                fingerprint = hashlib.sha256(str(sr).encode() + b":" + core.tobytes()).hexdigest()
                reasons = list(dict.fromkeys(source_reasons + interval["reasons"]))
                if fingerprint in seen:
                    reasons.append("EXACT_DUPLICATE")
                file = work / "clip.wav"
                sf.write(file, audio, sr, subtype="FLOAT")
                artifact = store.import_file(file, name=f'{Path(source["name"]).stem}_{clip_index + 1:04d}.wav', job_id=job_id,
                                             metadata={"source_id": source_id, "master_id": master["id"], "interval": interval, "processor_version": PROCESSOR_VERSION})
                duplicate_check = {"version": DUPLICATE_VERSION, "status": "exact_duplicate", "matches": []} if fingerprint in seen else duplicates.add(artifact["id"], core, sr)
                if duplicate_check["matches"]:
                    reasons.append("NEAR_DUPLICATE")
                status, reasons, decision = decide_clip(policy, metrics, reasons, singing, harmony)
                seen.setdefault(fingerprint, artifact["id"])
                dataset["clips"].append({"artifact_id": artifact["id"], "source_id": source_id, "source_group": source["metadata"].get("source_group", source["sha256"]),
                                         "sample_rate": sr, **interval, "metrics": metrics, "status": status, "reasons": reasons,
                                         "duplicate_group": fingerprint, "near_duplicate_check": duplicate_check,
                                         "near_duplicate_of": [m["artifact_id"] for m in duplicate_check["matches"]], "singing": singing, "decision": decision})
            del clean, mono_x
        dataset["summary"] = {"clip_count": len(dataset["clips"]), "review_count": sum(c["status"] == "review" for c in dataset["clips"]),
                              "excluded_count": sum(c["status"] == "excluded" for c in dataset["clips"]),
                              "accepted_count": sum(c["status"] == "accepted" for c in dataset["clips"]),
                              "excluded_source_count": sum(s.get("status") == "excluded" for s in dataset["sources"]),
                              "near_duplicate_count": sum(bool(c["near_duplicate_of"]) for c in dataset["clips"]),
                              "cleanup_fallback_count": sum(stage["decision"] == "fallback_input" for record in records for stage in record["stages"]),
                              "activity_seconds": sum(s["analysis"]["activity_seconds"] for s in dataset["sources"])}
        assign_split(dataset)
        store.promote_dataset([c["artifact_id"] for c in dataset["clips"] if c["status"] == "accepted"])
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
        # Manifest is permanent; provisional clips remain cleanable unless explicitly retained.
        manifest = store.import_file(manifest_path, name="数据集审查清单.json", role="dataset", job_id=job_id,
                                     metadata={"kind": "dataset_manifest", "summary": dataset["summary"], "processor_version": PROCESSOR_VERSION, "cache_key": cache_key})
    return manifest["id"]
