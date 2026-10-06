from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class EngineError(RuntimeError):
    pass


class EngineCancelled(EngineError):
    pass


def run_process(command, *, cwd: Path, log: Path, progress=None, timeout=7200, env=None):
    started = time.monotonic()
    with log.open("ab") as out:
        wrapped = [sys.executable, str(Path(__file__).with_name("engine_launcher.py")), str(os.getpid()), *map(str, command)]
        process = subprocess.Popen(wrapped, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, env=env,
                                   start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout:
                    raise EngineError("引擎超过运行时限，已停止")
                if progress:
                    progress()
                time.sleep(.25)
            if process.returncode:
                raise EngineError(f"引擎执行失败，退出码 {process.returncode}，请查看任务日志")
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.terminate()  # Closing its Win32 job kills descendants.
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def probe_python(python, *, module=None, cwd=None):
    if not python or not Path(python).is_file():
        return {"ready": False, "reason": "尚未配置独立引擎环境"}
    script = "import json,torch,importlib.metadata; d={'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.is_available()}; "
    if module:
        script += f"d['package_version']=importlib.metadata.version({module!r}); "
    script += "d['gpu_name']=torch.cuda.get_device_name(0) if d['gpu'] else None; d['capability']=list(torch.cuda.get_device_capability(0)) if d['gpu'] else None; d['vram_bytes']=torch.cuda.get_device_properties(0).total_memory if d['gpu'] else 0; "
    script += "\nif d['gpu']:\n x=torch.randn(32,32,device='cuda'); (x@x).sum().item(); torch.cuda.synchronize()\nprint(json.dumps(d))"
    try:
        result = subprocess.run([python, "-c", script], cwd=cwd, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return {"ready": False, "reason": "引擎依赖不完整或 CUDA 实际运算失败"}
        data = json.loads(result.stdout.strip().splitlines()[-1])
        data["ready"] = data["gpu"] and tuple(data["capability"]) >= (8, 9)
        if module == "audio-separator" and data.get("package_version") != "0.47.0":
            return {**data, "ready": False, "reason": "需要固定版本 audio-separator 0.47.0"}
        if not data["ready"]:
            data["reason"] = "需要 RTX 40 系或更新 GPU（计算能力至少 8.9）"
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"ready": False, "reason": "引擎探测失败或超时"}
