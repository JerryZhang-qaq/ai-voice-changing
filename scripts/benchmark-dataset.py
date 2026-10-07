"""Two independent real-voice datasets; no invented purity or listening scores."""
import argparse
from io import BytesIO
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

import numpy as np
import soundfile as sf

from voice_workbench_storage import ArtifactStore
from voice_workbench_dataset import prepare_dataset, SliceConfig
from voice_workbench_dataset.admission import PreparationPolicy
from voice_workbench_dataset.quality import compare_features, features_array

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runtime/benchmarks")
    parser.add_argument("--import-workbench", action="store_true", help="在工作台中登记两份独立待复核数据集")
    args = parser.parse_args()
    catalog = json.loads((ROOT / "docs/benchmarks/sources.json").read_text(encoding="utf-8"))
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = {"quality_precision": None, "quality_recall": None,
               "limitations": ["没有独立人工污染标签，不计算自动清洗准确率", "未训练 RVC，不推断翻唱音质"], "datasets": []}
    for language in ("cn", "jp"):
        store = ArtifactStore(Path(__import__('os').environ.get('WORKBENCH_RUNTIME', ROOT / 'runtime')) if args.import_workbench else output / language)
        ids, originals = [], []
        singer = "Opencpop" if language == "cn" else "NIT-SONG070-F001"
        existing = {item['sha256']:item for item in store.inventory()['items'] if item['exists'] and item['role']=='source' and item['metadata'].get('singer')==singer and item['metadata'].get('benchmark')}

        for entry in catalog["files"]:
            if entry["language"] != language or entry.get("kind") == "song":
                continue
            name = Path(entry["path"]).name
            local = args.input_root / ("cn" if language == "cn" else "jp-f001") / name if args.input_root else None
            data = local.read_bytes() if local else urlopen(Request(entry["url"], headers={"User-Agent": "VoiceWorkbench benchmark"}), timeout=30).read()
            if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise RuntimeError(f"素材校验失败：{name}")
            artifact = existing.get(entry["sha256"]) or store.import_stream(BytesIO(data), name=name, role="source", metadata={"kind": "dry_vocal", "singer": singer, "purpose": "training", "benchmark": True, "source": entry["url"], "license": catalog["groups"][language]["license"]})
            ids.append(artifact["id"])
            x, sr = sf.read(BytesIO(data), dtype="float32")
            originals.append({"name": name, "duration": len(x) / sr, "sample_rate": sr, "sha256": entry["sha256"]})
        if not ids:
            raise RuntimeError(f"没有 {language} 素材")
        job = store.create_job("dataset_prepare", ids, metadata={"singer":singer,"purpose":"training"}, status="running")
        start = time.monotonic()
        aid = prepare_dataset(store, job["id"], ids, SliceConfig(), policy=PreparationPolicy("review", True))
        with store.connect(write=True) as db:
            db.execute("UPDATE artifacts SET name=? WHERE id=?", (singer + "-after.json", aid))
        store.update_job(job["id"], "completed", metadata={"result_id": aid})
        manifest = json.loads(store.path(aid).read_text(encoding="utf-8"))
        covered = sum((c["valid_end_sample"] - c["valid_start_sample"]) / c["sample_rate"] for c in manifest["clips"])
        dataset = {"language": language, "group": catalog["groups"][language], "sources": originals, "input_seconds": sum(s["duration"] for s in originals),
                   "processing_seconds": time.monotonic() - start, "core_seconds": covered, "summary": manifest["summary"],
                   "lengths_seconds": [c["duration"] for c in manifest["clips"]], "unsafe_boundaries": sum("UNSAFE_BOUNDARY" in c["reasons"] for c in manifest["clips"]),
                   "reason_counts": {}, "manifest_id": aid}
        for clip in manifest["clips"]:
            for reason in clip["reasons"]:
                dataset["reason_counts"][reason] = dataset["reason_counts"].get(reason, 0) + 1
        # Controlled damage is assessed on actual singing, separately from
        # claims about separator/model quality.
        original = originals[0]
        x, sr = sf.read(store.path(ids[0]), dtype="float32")
        x = x[:min(len(x), 20 * sr)]
        missing = x.copy()
        missing[len(x)//3:len(x)//2] = 0
        dataset["controlled_damage"] = {
            "gain_polarity": compare_features(features_array(x, sr), features_array(-.5*x, sr), "dereverb"),
            "signal_collapse": compare_features(features_array(x, sr), features_array(np.zeros_like(x), sr), "dereverb"),
            "partial_erasure": compare_features(features_array(x, sr), features_array(missing, sr), "denoise")}
        (output / f"{language}-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        results["datasets"].append(dataset)
        print(language, dataset["summary"], flush=True)
    report = output / "report.json"
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{report}")


if __name__ == "__main__":
    main()
