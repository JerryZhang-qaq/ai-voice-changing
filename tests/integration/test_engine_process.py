import sys
import os

import pytest

from voice_workbench_engines.process import run_process, EngineError


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_exit_status_and_log_preserved(tmp_path):
    log = tmp_path / "engine.log"
    with pytest.raises(EngineError, match="退出码 7"):
        run_process([sys.executable, "-c", "print('real failure', flush=True); raise SystemExit(7)"], cwd=tmp_path, log=log)
    content = log.read_text(encoding="utf-8")
    assert "real failure" in content
    assert "engine_start_requested" in content and '"exit_code": 7' in content


def test_silent_engine_failure_has_diagnostics(tmp_path):
    log = tmp_path / "engine.log"
    with pytest.raises(EngineError, match="退出码 9"):
        run_process([sys.executable, "-c", "raise SystemExit(9)"], cwd=tmp_path, log=log)
    content = log.read_text(encoding="utf-8")
    assert "engine_start_requested" in content
    assert '"exit_code": 9' in content


def test_timeout_reaps_running_engine(tmp_path):
    import os
    pidfile = tmp_path / "pid"
    code = "import os,time,pathlib;pathlib.Path('pid').write_text(str(os.getpid()));time.sleep(30)"
    with pytest.raises(EngineError, match="时限"):
        run_process([sys.executable, "-c", code], cwd=tmp_path, log=tmp_path / "log", timeout=1)
    pid = int(pidfile.read_text())
    assert not alive(pid)


def test_parent_death_stops_owned_engine(tmp_path):
    import subprocess
    import time
    root = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "packages/engines/src")
    env = os.environ.copy()
    env["PYTHONPATH"] = root
    code = "import os,time,pathlib;pathlib.Path('orphan.pid').write_text(str(os.getpid()));time.sleep(60)"
    worker = subprocess.Popen([sys.executable, "-c", "import sys;from pathlib import Path;from voice_workbench_engines.process import run_process;run_process([sys.executable,'-c',sys.argv[1]],cwd=Path.cwd(),log=Path('orphan.log'))", code], cwd=tmp_path, env=env)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "orphan.pid").exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (tmp_path / "orphan.pid").exists()
        pid = int((tmp_path / "orphan.pid").read_text())
        worker.kill()
        worker.wait(timeout=5)
        deadline = time.monotonic() + 6
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not alive(pid)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
