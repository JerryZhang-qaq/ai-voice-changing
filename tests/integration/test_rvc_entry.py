"""Real Python import resolution and encoded engine output; no GPU claim."""
import os
from pathlib import Path
import subprocess
import sys

from voice_workbench_engines.process import run_process
from voice_workbench_engines.rvc import rvc_command


def test_training_entry_does_not_shadow_its_package(tmp_path):
    root = tmp_path / "RVC 中文 空格"
    train = root / "train"
    train.mkdir(parents=True)
    # Match the upstream namespace package: no __init__.py in train/.
    (train / "utils.py").write_text("TOKEN = 'REAL_PACKAGE_UTILS'\n", encoding="utf-8")
    script = train / "train.py"
    script.write_text(
        "from train import utils\nimport sys\n"
        "assert sys.argv[1:] == ['-e', 'example', '-te', '50']\n"
        "print(utils.TOKEN)\nprint('训练入口导入成功')\n", encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}
    before = subprocess.run(
        [sys.executable, str(script), "-e", "example", "-te", "50"],
        cwd=root, env=env, capture_output=True, text=True, timeout=20,
    )
    assert before.returncode != 0 and "partially initialized module" in before.stderr
    log = tmp_path / "engine.log"
    run_process(rvc_command(sys.executable, root, "train/train.py", ["-e", "example", "-te", 50]),
                cwd=root, log=log, env=env)
    output = log.read_text(encoding="utf-8")
    assert "REAL_PACKAGE_UTILS" in output and "训练入口导入成功" in output
    # Caller environment remains unchanged; only owned engine children use UTF-8.
    assert env["PYTHONIOENCODING"] == "ascii" and env["PYTHONUTF8"] == "0"


def test_feature_entry_arguments_and_pythonpath_are_preserved(tmp_path):
    root = tmp_path / "rvc"
    dataset = root / "train/dataset"
    dataset.mkdir(parents=True)
    (root / "shared.py").write_text("TOKEN = 'ROOT_IMPORT_AVAILABLE'\n", encoding="utf-8")
    (dataset / "extract_f0.py").write_text(
        "import shared,sys\nassert sys.argv[1:] == ['cuda', '1', '0']\nprint(shared.TOKEN)\n",
        encoding="utf-8",
    )
    log = tmp_path / "engine.log"
    run_process(rvc_command(sys.executable, root, "train/dataset/extract_f0.py", ["cuda", 1, 0]),
                cwd=root, log=log, env={**os.environ, "PYTHONPATH": str(root)})
    assert "ROOT_IMPORT_AVAILABLE" in log.read_text(encoding="utf-8")
