"""Immutable boundary edits. A changed boundary requires a new review decision."""
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile

import soundfile as sf

from voice_workbench_audio import inspect_audio
from .admission import plateau_ratio
from .analysis import analyze_file
from .curation import assign_split
from .duplicates import NearDuplicateIndex


def edit_clip(store, job_id, dataset_id, clip_id, start_seconds, end_seconds, progress=None):
    manifest = json.loads(store.path(dataset_id).read_text(encoding="utf-8"))
    original = next((c for c in manifest["clips"] if c["artifact_id"] == clip_id), None)
    if original is None:
        raise ValueError("片段不属于此数据集")
    source = next(s for s in manifest["sources"] if s["id"] == original["source_id"])
    master_path = store.path(source["master_id"])
    info = sf.info(master_path)
    sr = info.samplerate
    a, b = round(start_seconds * sr), round(end_seconds * sr)
    if not 0 <= a < b <= info.frames or not .5 <= (b - a) / sr <= 30:
        raise ValueError("边界必须位于工作母版内，核心长度需为 0.5—30 秒")
    for other in manifest["clips"]:
        if other["artifact_id"] != clip_id and other["source_id"] == original["source_id"]:
            if a < other["valid_end_sample"] and b > other["valid_start_sample"]:
                raise ValueError("编辑后核心范围与同来源片段重叠；请缩小边界")
    padding = round(manifest["config"]["padding_seconds"] * sr)
    start, end = max(0, a - padding), min(info.frames, b + padding)
    if (end - start) / sr > 30:
        raise ValueError("含边缘余量的片段不能超过 30 秒")
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as temp:
        directory = Path(temp)
        with sf.SoundFile(master_path) as stream:
            stream.seek(start)
            audio = stream.read(end - start, dtype="float32")
        core = audio[a - start:b - start]
        path = directory / "edited.wav"
        sf.write(path, audio, sr, subtype="FLOAT")
        voice, _ = analyze_file(path, directory, (lambda: progress("检查编辑片段", 0, 1)) if progress else None)
        interval = {"start_sample": start, "end_sample": end, "valid_start_sample": a, "valid_end_sample": b,
                    "duration": (end - start) / sr}
        artifact = store.import_file(path, name=f"{manifest.get('singer','未分类歌手')}-clip-edited.wav", job_id=job_id,
                                    metadata={"source_id": source["id"], "master_id": source["master_id"], "interval": interval, "parent_clip_id": clip_id})
        reasons = [r for r in original["reasons"] if r not in {"SHORT_CLIP", "UNSAFE_BOUNDARY", "EXACT_DUPLICATE", "NEAR_DUPLICATE", "CLIPPING_DETECTED", "SEVERE_CLIPPING", "LOW_SIGNAL", "CLIP_UNDER_ONE_SECOND",
                                                             "COMPLEX_HARMONY", "HARMONY_UNCERTAIN", "HARMONY_INPUT_UNCERTAIN", "HARMONY_STEMS_MISALIGNED", "HARMONY_NOT_CHECKED", "HARMONY_CHECK_REQUIRED"}]
        new = {**copy.deepcopy(original), **interval, "artifact_id": artifact["id"], "name": artifact["name"], "status": "review",
               "metrics": {**inspect_audio(audio, sr), "plateau_ratio": plateau_ratio(audio)},
               "singing": voice.summary(), "harmony": {"status": "not_checked", "scope": "clip"},
               "reasons": [*reasons, "MANUAL_BOUNDARY_REVIEW", "HARMONY_NOT_CHECKED"],
               "duplicate_group": hashlib.sha256(str(sr).encode() + b":" + core.tobytes()).hexdigest(),
               "decision": {"origin": "boundary_edit", "parent_clip_id": clip_id}}
        if len(audio) < sr:
            new["status"] = "excluded"
            new["reasons"].append("CLIP_UNDER_ONE_SECOND")
        manifest["clips"][manifest["clips"].index(original)] = new
        index, seen = NearDuplicateIndex(directory), set()
        for i, clip in enumerate(manifest["clips"]):
            if progress:
                progress("重新检查重复片段", i, len(manifest["clips"]))
            x, rate = sf.read(store.path(clip["artifact_id"]), dtype="float32")
            core = x[clip["valid_start_sample"] - clip["start_sample"]:clip["valid_end_sample"] - clip["start_sample"]]
            fingerprint = clip["duplicate_group"]
            clip["reasons"] = [r for r in clip["reasons"] if r not in {"EXACT_DUPLICATE", "NEAR_DUPLICATE"}]
            if fingerprint in seen:
                clip["reasons"].append("EXACT_DUPLICATE")
                clip["status"] = "excluded"
                clip["near_duplicate_of"] = []
            else:
                check = index.add(clip["artifact_id"], core, rate)
                clip["near_duplicate_check"] = check
                clip["near_duplicate_of"] = [m["artifact_id"] for m in check["matches"]]
                if check["matches"]:
                    clip["reasons"].append("NEAR_DUPLICATE")
                    if clip["decision"].get("origin") == "automatic":
                        clip["status"] = "review"
            seen.add(fingerprint)
        for status in ("accepted", "review", "excluded"):
            manifest["summary"][f"{status}_count"] = sum(c["status"] == status for c in manifest["clips"])
        manifest["summary"]["near_duplicate_count"] = sum(bool(c["near_duplicate_of"]) for c in manifest["clips"])
        manifest["summary"]["harmony_review_count"] = sum("COMPLEX_HARMONY" in c["reasons"] for c in manifest["clips"])
        manifest["parent_manifest_id"] = dataset_id
        assign_split(manifest)
        return store.import_stream(BytesIO(json.dumps(manifest, ensure_ascii=False, indent=2).encode()), name=f"{manifest.get('singer','未分类歌手')}-after-edited.json",
                                   role="dataset", job_id=job_id, metadata={"kind": "dataset_manifest", "parent_id": dataset_id, "singer": manifest.get("singer", "未分类歌手"), "purpose": "training", "summary": manifest["summary"]})["id"]
