"""Runs with the separate audio-separator interpreter; never imports the API."""
import json
import gc
import os
from pathlib import Path
import sys
import time
import traceback


class NonFiniteOutput(RuntimeError):
    pass


def configure_attention(model, fused=True):
    """Allow native PyTorch SDPA on Ada/Blackwell, retaining math fallback."""
    count = 0
    for module in model.modules():
        if module.__class__.__module__.endswith(".roformer.attend") and getattr(module, "flash", False):
            config = getattr(module, "cuda_config", None)
            if config is not None:
                module.cuda_config = config._replace(enable_flash=fused, enable_math=True, enable_mem_efficient=True)
                count += 1
    return count


def event(**values):
    print(json.dumps({"event": "workbench_separation", **values}, ensure_ascii=False), flush=True)


def check_finite(outputs):
    import numpy as np
    arrays = outputs.values() if isinstance(outputs, dict) else [outputs]
    for array in arrays:
        flat = np.asarray(array).reshape(-1)
        for start in range(0, len(flat), 1024 * 1024):
            if not np.isfinite(flat[start:start + 1024 * 1024]).all():
                raise NonFiniteOutput("分离模型输出包含 NaN/Inf")


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


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
    wanted = set(request.get("keep_stems", request["stems"].values()))
    if not wanted or not wanted <= set(request["stems"].values()):
        raise RuntimeError("请选择此模型支持的输出声部")
    # Training needs only the vocal candidate for most cleaning steps. The
    # upstream option skips writing the unused stem without altering inference.
    single_stem = next(key for key, value in request["stems"].items() if value in wanted) if len(wanted) == 1 else None
    separator = LocalSeparator(model_file_dir=request["model_dir"], output_dir=request["output_dir"],
                          output_format="WAV", sample_rate=44100, use_soundfile=True,
                          use_autocast=request.get("precision", "fp32") == "amp_fp16",
                          output_single_stem=single_stem,
                          normalization_threshold=1.0, amplification_threshold=0.0,
                          mdxc_params={"segment_size": request["segment_size"], "override_model_segment_size": True, "batch_size": 1, "overlap": request["overlap"], "pitch_shift": 0})
    separator.load_model(request["filename"])
    actual = {separator.model_instance.primary_stem_name.lower(), separator.model_instance.secondary_stem_name.lower()}
    if actual != {key.lower() for key in request["stems"]}:
        raise RuntimeError(f"模型声部与固定配置不符：{actual}")
    separator.workbench_attention_modules = configure_attention(separator.model_instance.model_run, request.get("attention") == "auto_sdpa")
    original_demix = separator.model_instance.demix
    def checked_demix(*args, **kwargs):
        started = time.monotonic()
        outputs = original_demix(*args, **kwargs)
        check_finite(outputs)  # Validate before PCM export can conceal NaN/Inf.
        separator.workbench_inference_seconds = time.monotonic() - started
        return outputs
    separator.model_instance.demix = checked_demix
    return separator


class SeparatorRunner:
    def __init__(self):
        self.separator, self.identity = None, None

    def run(self, request):
        import torch
        from audio_separator.separator.execution_policy import AUTOCAST, FP32
        started = time.monotonic()
        identity = tuple(request.get(key) for key in ("model_dir", "filename", "config", "weight_sha256", "config_sha256"))
        reused = identity == self.identity and self.separator is not None
        event(state="loading" if not reused else "ready", model_reused=reused, done=0, total=None)
        load_started = time.monotonic()
        if not reused:
            self.separator = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            self.separator = build_separator(request)
            self.identity = identity
        separator = self.separator
        load_seconds = time.monotonic() - load_started
        wanted = set(request.get("keep_stems", request["stems"].values()))
        single = next(key for key, value in request["stems"].items() if value in wanted) if len(wanted) == 1 else None
        separator.output_dir = separator.model_instance.output_dir = request["output_dir"]
        separator.output_single_stem = separator.model_instance.output_single_stem = single
        actual = {key: request.get(key) for key in ("profile", "precision", "attention", "segment_size", "overlap", "policy_version")}
        actual["precision"] = actual["precision"] or "fp32"
        fallback = None
        for attempt in range(2):
            instance = separator.model_instance
            instance.segment_size, instance.overlap = actual["segment_size"], actual["overlap"]
            instance.effective_precision = AUTOCAST if actual["precision"] == "amp_fp16" else FP32
            attention_count = configure_attention(instance.model_run, actual["attention"] == "auto_sdpa")
            event(state="running", parameters=actual, model_reused=reused, load_seconds=round(load_seconds, 3),
                  attention_modules=attention_count, fallback_reason=fallback, done=0, total=None)
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            try:
                outputs = separator.separate(request["input"], custom_output_names=request["stems"])
                break
            except (RuntimeError, NonFiniteOutput) as error:
                message = str(error).lower()
                oom = "out of memory" in message
                recoverable = isinstance(error, NonFiniteOutput) or oom or any(t in message for t in ("no available kernel", "not implemented for 'half'", "no viable backend"))
                if attempt or not recoverable:
                    raise
                fallback = str(error)
                if oom:
                    if actual["segment_size"] <= 64:
                        raise
                    actual["segment_size"] = max(64, actual["segment_size"] // 2)
                else:
                    actual.update(precision="fp32", attention="legacy")
                # Failed inference frames can retain activation tensors. Drop
                # that traceback before freeing CUDA memory and retrying.
                error.__traceback__ = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                event(state="fallback", reason=fallback, parameters=actual, done=0, total=None)
        elapsed = time.monotonic() - started
        performance = {"elapsed_seconds": round(elapsed, 3), "load_seconds": round(load_seconds, 3),
                       "inference_seconds": round(getattr(separator, "workbench_inference_seconds", 0), 3),
                       "model_reused": reused, "fallback_reason": fallback,
                       "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024**2, 1) if torch.cuda.is_available() else None,
                       "attention_policy": actual["attention"]}
        event(state="completed", parameters=actual, **performance)
        atomic_json(request["response"], {"outputs": outputs, "parameters": actual, "performance": performance})


def serve(directory, runner):
    directory = Path(directory)
    while not (directory / "stop").exists():
        paths = sorted(directory.glob("request-*.json"))
        if not paths:
            time.sleep(.05)
            continue
        command = json.loads(paths[0].read_text(encoding="utf-8"))
        paths[0].unlink()
        try:
            runner.run(json.loads(Path(command["request"]).read_text(encoding="utf-8")))
            atomic_json(command["response"], {"ok": True})
        except Exception as error:
            traceback.print_exc()
            atomic_json(command["response"], {"error": f"分离失败：{error}；请查看任务日志"})


def main():
    import torch
    if not torch.cuda.is_available() or torch.cuda.get_device_capability(0) < (8, 9):
        raise RuntimeError("需要计算能力至少 8.9 的 NVIDIA GPU")
    runner = SeparatorRunner()
    if sys.argv[1] == "--serve":
        serve(sys.argv[2], runner)
    else:
        runner.run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
