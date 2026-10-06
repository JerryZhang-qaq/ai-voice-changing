import hashlib
import json
from pathlib import Path
import tempfile
import zipfile


def assign_split(manifest):
    accepted = [c for c in manifest["clips"] if c["status"] == "accepted"]
    # Exact and suspected duplicate relationships include excluded/review clips:
    # rejecting a copy does not make its source independent of the other source.
    parents = {c["source_group"]: c["source_group"] for c in manifest["clips"]}
    def root(group):
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group
    def merge(a, b):
        a, b = root(a), root(b)
        if a != b:
            low, high = sorted((a, b))
            parents[high] = low
    by_id = {c["artifact_id"]: c for c in manifest["clips"]}
    fingerprints = {}
    for clip in manifest["clips"]:
        if clip.get("duplicate_group"):
            earlier = fingerprints.setdefault(clip["duplicate_group"], clip["source_group"])
            merge(earlier, clip["source_group"])
        for other_id in clip.get("near_duplicate_of", []):
            if other_id in by_id:
                merge(clip["source_group"], by_id[other_id]["source_group"])
    for clip in manifest["clips"]:
        clip["split_group"] = root(clip["source_group"])
    groups = sorted({c["split_group"] for c in accepted}, key=lambda g: hashlib.sha256(("split-v1:" + g).encode()).hexdigest())
    # Keep entire sources together. One source cannot prove independent validation.
    validation_groups = set(groups[:max(1, round(len(groups) * .2))]) if len(groups) >= 2 else set()
    manifest["split"] = {"version": "duplicate-aware-source-group-2", "train": [], "validation": [],
                         "warning": None if validation_groups else "SINGLE_SOURCE_NO_INDEPENDENT_VALIDATION",
                         "linked_source_count": len(parents) - len({root(g) for g in parents})}
    for clip in accepted:
        fold = "validation" if clip["split_group"] in validation_groups else "train"
        manifest["split"][fold].append(clip["artifact_id"])
    manifest["summary"]["train_count"] = len(manifest["split"]["train"])
    manifest["summary"]["validation_count"] = len(manifest["split"]["validation"])
    manifest["validation"] = {"status": "held_out" if validation_groups else "unavailable_single_source", "groups": len(groups)}
    return manifest


def export_dataset(store, job_id, dataset_id, progress=None):
    manifest = json.loads(store.path(dataset_id).read_text(encoding="utf-8"))
    assign_split(manifest)
    accepted = [c for c in manifest["clips"] if c["status"] == "accepted"]
    if not accepted:
        raise ValueError("没有已接受的片段，不能导出训练数据")
    if len({c["duplicate_group"] for c in accepted}) != len(accepted):
        raise ValueError("已接受片段包含完全重复音频，请先排除重复项")
    validation = set(manifest["split"]["validation"])
    with tempfile.TemporaryDirectory(dir=store.root, prefix=f"processing-{job_id}-") as temp:
        archive_path = Path(temp) / "dataset.zip"
        # PCM/audio is already large; no expensive compression during export.
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, clip in enumerate(accepted):
                if progress:
                    progress(index, len(accepted))
                folder = "validation" if clip["artifact_id"] in validation else "train"
                clip["export_path"] = f'{folder}/{clip["artifact_id"]}.wav'
                archive.write(store.path(clip["artifact_id"]), clip["export_path"])
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return store.import_file(archive_path, name="训练数据集.zip", role="export", job_id=job_id,
                                 max_bytes=20 * 1024**3, metadata={"kind": "dataset_export", "dataset_id": dataset_id, "summary": manifest["summary"]})["id"]
