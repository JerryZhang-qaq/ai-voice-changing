import subprocess
import sys
import os

from voice_workbench_storage.locking import WorkerLock


def test_process_lock_rejects_second_owner_then_releases(tmp_path):
    path = tmp_path / "worker.lock"
    command = [sys.executable, "-c", "from pathlib import Path; from voice_workbench_storage.locking import WorkerLock; import sys; lock=WorkerLock(Path(sys.argv[1])); lock.__enter__(); lock.__exit__(None,None,None)", str(path)]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    with WorkerLock(path):
        assert subprocess.run(command, capture_output=True, env=env).returncode != 0
    assert subprocess.run(command, capture_output=True, env=env).returncode == 0
