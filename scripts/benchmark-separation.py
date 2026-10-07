"""Run actual quality/speed comparisons in the separation interpreter.

CPU is an explicit test exception. Production still requires a supported GPU.
No benchmark bypass is exposed to the web API or worker.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/engines/src"))


def main():
    import numpy as np
    import soundfile as sf
    import torch
    from voice_workbench_engines.registry import SEPARATION_MODELS, settings
    from voice_workbench_engines.separation_policy import parameters
    from voice_workbench_engines.separation_bridge import SeparatorRunner

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/performance-check")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--profiles", nargs="+", choices=["quality", "balanced", "fast"], default=["quality", "balanced", "fast"])
    parser.add_argument("--cpu", action="store_true", help="Explicit CPU overlap-only test; not a GPU speed measurement")
    args = parser.parse_args()
    if not args.cpu and (not torch.cuda.is_available() or torch.cuda.get_device_capability(0) < (8, 9)):
        raise SystemExit("需要 RTX 40 系及更新显卡；CPU 检查须明确指定 --cpu")
    if not math.isfinite(args.start) or not 1 <= args.seconds <= 60 or args.start < 0:
        raise SystemExit("测试片段长度为 1—60 秒，起点不得为负")
    if args.cpu:
        torch.set_num_threads(4)
    output = args.output.resolve() / time.strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    excerpt = output / "input.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(args.start), "-i", str(args.input.resolve()),
                    "-t", str(args.seconds), "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le", str(excerpt)], check=True)
    spec = SEPARATION_MODELS["vocals_melband_unwa"]
    models = str((args.model_dir or Path(settings()["separation_models"])).resolve())
    version = importlib.metadata.version("audio-separator")
    if version != "0.47.0":
        raise RuntimeError("对照需要固定 audio-separator 0.47.0")
    catalog = json.loads((ROOT / "packages/engines/src/voice_workbench_engines/resources.json").read_text())
    resource = next(r for r in catalog["resources"] if r["id"] == "vocals_melband_unwa")
    hashes = {}
    for entry in resource["files"]:
        path = Path(models) / entry["path"]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != entry["size"] or digest != entry["sha256"]:
            raise RuntimeError(f"固定权重或配置校验失败：{path.name}")
        hashes[path.name] = digest
    runner = SeparatorRunner()
    report = {"device": "cpu" if args.cpu else torch.cuda.get_device_name(0), "torch": torch.__version__,
              "input": str(args.input.resolve()), "seconds": sf.info(excerpt).duration, "model_hashes": hashes, "audio_separator": version,
              "cpu_exception": args.cpu, "runs": [], "limitations": ["短片段数值与速度检查，音质需 A/B 试听", "CPU 模式只比较重叠，不测 CUDA 混合精度或融合注意力"]}
    reference = None
    for profile in args.profiles:
        folder = output / profile
        folder.mkdir()
        params = parameters("vocals_melband_unwa", profile)
        if args.cpu:
            params.update(precision="fp32", attention="legacy")
        request = {"input": str(excerpt), "model_dir": models, "filename": spec["filename"], "config": spec["config"],
                   "stems": spec["stems"], "keep_stems": ["vocals"], "output_dir": str(folder),
                   "response": str(folder / "response.json"), "weight_sha256": hashes[spec["filename"]], "config_sha256": hashes[spec["config"]], **params}
        runner.run(request)
        result = json.loads((folder / "response.json").read_text(encoding="utf-8"))
        signal, sr = sf.read(folder / "vocals.wav", dtype="float32", always_2d=True)
        if not np.isfinite(signal).all() or abs(len(signal) / sr - report["seconds"]) > .1:
            raise RuntimeError("输出采样或时长无效")
        stats = {"profile": profile, **result, "frames": len(signal), "sample_rate": sr}
        if profile == "quality":
            reference = signal
        elif reference is not None:
            delta = signal - reference
            stats["difference_vs_quality"] = {"max_absolute": float(np.max(np.abs(delta))),
                "relative_rms": float(np.sqrt(np.mean(delta**2)) / max(1e-10, np.sqrt(np.mean(reference**2))))}
        report["runs"].append(stats)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"对照报告与试听音频：{output}", flush=True)


if __name__ == "__main__":
    main()
