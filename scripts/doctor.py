"""Save diagnostics without collecting credentials or environment values."""
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from voice_workbench_engines.registry import settings
from voice_workbench_engines.separation import status as separation_status
from voice_workbench_engines.rvc import status as rvc_status
from voice_workbench_engines.resources import inventory


def main():
    data = {"platform": platform.platform(), "python": sys.version, "tools": {name: bool(shutil.which(name)) for name in ("git", "ffmpeg", "ffprobe", "nvidia-smi")},
            "separation": separation_status(), "rvc": rvc_status(), "resources": inventory()}
    directory = Path(os.environ.get("WORKBENCH_RUNTIME", "runtime")) / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "doctor.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"诊断已保存：{target}")


if __name__ == "__main__":
    main()
