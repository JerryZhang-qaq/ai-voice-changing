"""Runs with the separate audio-separator interpreter; never imports the API."""
import json
from pathlib import Path
import sys


def build_separator(request):
    from audio_separator.separator import Separator

    class LocalSeparator(Separator):
        def list_supported_model_files(self):
            # Avoid a mutable remote catalog lookup even when already offline.
            return {"MDXC": {"Workbench pinned model": {"filename": request["filename"],
                    "download_files": [request["filename"], request["config"]]}}}

    for name in (request["filename"], request["config"]):
        if not (Path(request["model_dir"]) / name).is_file():
            raise RuntimeError("请先在资源页面下载并校验模型及配置")
    separator = LocalSeparator(model_file_dir=request["model_dir"], output_dir=request["output_dir"],
                          output_format="WAV", sample_rate=44100, use_soundfile=True,
                          normalization_threshold=1.0, amplification_threshold=0.0,
                          mdxc_params={"segment_size": request["segment_size"], "override_model_segment_size": True, "batch_size": 1, "overlap": request["overlap"], "pitch_shift": 0})
    separator.load_model(request["filename"])
    actual = {separator.model_instance.primary_stem_name.lower(), separator.model_instance.secondary_stem_name.lower()}
    if actual != {key.lower() for key in request["stems"]}:
        raise RuntimeError(f"模型声部与固定配置不符：{actual}")
    return separator


def main():
    import torch
    request = json.loads(Path(sys.argv[1]).read_text())
    if not torch.cuda.is_available() or torch.cuda.get_device_capability(0) < (8, 9):
        raise RuntimeError("需要计算能力至少 8.9 的 NVIDIA GPU")
    separator = build_separator(request)
    outputs = separator.separate(request["input"], custom_output_names=request["stems"])
    Path(request["response"]).write_text(json.dumps({"outputs": outputs}), encoding="utf-8")


if __name__ == "__main__":
    main()
