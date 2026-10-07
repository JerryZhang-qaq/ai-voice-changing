"""Native Windows/Linux installer. Run with Python 3.12 x64."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
REVISION = "81eed5e8f68b6bed1789f682fe78cdd324495afc"


def run(args, **kwargs):
    print("执行：", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, cwd=ROOT, **kwargs)


def interpreter(directory):
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def environment(directory):
    if not interpreter(directory).is_file():
        venv.EnvBuilder(with_pip=True).create(directory)
    return interpreter(directory)


def ensure_packages(python, arguments, expected, env=None):
    """Skip network resolution when the existing environment already satisfies pins."""
    import json
    script = "import importlib.metadata as m,json; names=" + repr(list(expected)) + "; print(json.dumps({n:m.version(n) for n in names}))"
    result = subprocess.run([str(python), "-c", script], capture_output=True, text=True)
    try:
        satisfied = result.returncode == 0 and json.loads(result.stdout) == expected
    except ValueError:
        satisfied = False
    if satisfied:
        print("复用已安装依赖：", ", ".join(expected), flush=True)
    else:
        run([python, "-m", "pip", "install", *arguments], **({"env": env} if env is not None else {}))


def pinned_packages(path):
    return dict(line.strip().split("==", 1) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#") and "==" in line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", action="store_true", help="安装 GPU 引擎")
    parser.add_argument("--cuda", choices=["auto", "cu118", "cu128"], default="auto")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise SystemExit("需要 Python 3.12 x64")
    for name in ("ffmpeg", "ffprobe", "git"):
        if not shutil.which(name):
            raise SystemExit(f"缺少 {name}，请先运行 Install-Windows.cmd 或按 docs/windows.md 安装依赖")
    python = environment(ROOT / ".venv")
    ensure_packages(python, ["setuptools==80.9.0", "wheel==0.45.1"], {"setuptools": "80.9.0", "wheel": "0.45.1"})
    ensure_packages(python, ["-r", ROOT / "requirements.lock"], pinned_packages(ROOT / "requirements.lock"))
    run([python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", ROOT])
    if not (ROOT / "apps/web/dist/index.html").is_file():
        npm = shutil.which("npm")
        if not npm:
            raise SystemExit("源码安装需要 Node.js 22.12+ / 24 构建页面；首测 ZIP 已内置页面")
        subprocess.run([npm, "ci"], cwd=ROOT / "apps/web", check=True)
        subprocess.run([npm, "run", "build"], cwd=ROOT / "apps/web", check=True)
    if not args.engines:
        print("工作台安装完成。GPU 功能需再运行安装程序并加 --engines。")
        return
    flavor = args.cuda
    if flavor == "auto":
        result = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, check=True)
        flavor = "cu118" if "RTX 40" in result.stdout else "cu128"
    runtime = Path(os.environ.get("WORKBENCH_RUNTIME", ROOT / "runtime")).resolve()
    checkout = runtime / "engines/rvc"
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git", checkout])
        run(["git", "-C", checkout, "checkout", "--detach", REVISION])
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise SystemExit("已有 RVC 目录版本不匹配，请使用新的运行目录；原目录未修改")
    for engine in ("rvc", "separation"):
        ep = environment(runtime / "venvs" / engine)
        ensure_packages(ep, ["setuptools==80.9.0", "wheel==0.45.1"], {"setuptools": "80.9.0", "wheel": "0.45.1"})
        ensure_packages(ep, [f"torch==2.7.1+{flavor}", f"torchaudio==2.7.1+{flavor}", f"torchvision==0.22.1+{flavor}", "--index-url", f"https://download.pytorch.org/whl/{flavor}"], {"torch":f"2.7.1+{flavor}","torchaudio":f"2.7.1+{flavor}","torchvision":f"0.22.1+{flavor}"})
        if engine == "rvc":
            ensure_packages(ep, ["-r", ROOT / "scripts/rvc-requirements.txt", "--index-url", "https://pypi.org/simple"], pinned_packages(ROOT / "scripts/rvc-requirements.txt"))
        else:
            env = os.environ.copy()
            if os.name != "nt" and not shutil.which("clang") and shutil.which("gcc"):
                env["CC"] = "gcc"
            expected = {k:v for k,v in pinned_packages(ROOT / "scripts/separation-constraints.txt").items() if k not in {"torch", "torchaudio", "torchvision"}}
            expected["audio-separator"] = "0.47.0"
            ensure_packages(ep, ["audio-separator[gpu]==0.47.0", "-c", ROOT / "scripts/separation-constraints.txt", "--index-url", "https://pypi.org/simple"], expected, env=env)
        lock = subprocess.check_output([str(ep), "-m", "pip", "freeze"], text=True)
        (runtime / f"{engine}-installed.lock").write_text(lock, encoding="utf-8")
        run([ep, "-c", "import torch; assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0)>=(8,9); x=torch.randn(32,32,device='cuda'); print(torch.cuda.get_device_name(0), (x@x).sum().item()); torch.cuda.synchronize()"])
    print("安装完成。启动后在“引擎与基础模型”页面下载资源。")


if __name__ == "__main__":
    main()
