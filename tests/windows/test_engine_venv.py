"""Use real Windows venv redirectors, not just the CI base interpreter."""
import json
import os
from pathlib import Path
import signal
import subprocess
import textwrap
import time
import venv

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows venv redirectors")


@pytest.fixture(scope="module")
def venv_python(tmp_path_factory):
    directory = tmp_path_factory.mktemp("supervisor") / "虚拟 environment"
    venv.EnvBuilder(with_pip=False).create(directory)
    return directory / "Scripts/python.exe"


def worker_script(tmp_path, code):
    script = tmp_path / "worker.py"
    script.write_text(textwrap.dedent(code), encoding="utf-8")
    return script


def worker_env():
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(ROOT / "packages/engines/src")
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_worker(venv_python, tmp_path, code):
    result = subprocess.run(
        [str(venv_python), str(worker_script(tmp_path, code))], cwd=tmp_path,
        env=worker_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout
    return result.stdout


def process_alive(pid):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        api.CloseHandle(handle)


def test_venv_reproduces_legacy_143_and_starts_fixed_engine(venv_python, tmp_path):
    # The exact startup condition from 0.0.1, isolated from the GPU engine.
    (tmp_path / "legacy.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "expected = int(sys.argv[1])\n"
        "Path('legacy-parent.json').write_text(json.dumps({'expected': expected, 'observed': os.getppid()}))\n"
        "if os.getppid() != expected: raise SystemExit(143)\n"
        "print('LEGACY_ENGINE_STARTED')\n", encoding="utf-8",
    )
    output = run_worker(venv_python, tmp_path, """
        import json,os,subprocess,sys
        from pathlib import Path
        from voice_workbench_engines.process import run_process
        assert Path(sys.executable).parent.name == 'Scripts'
        old = subprocess.run([sys.executable, 'legacy.py', str(os.getpid())], capture_output=True, text=True)
        parents = json.loads(Path('legacy-parent.json').read_text())
        print('Legacy Windows venv result:', old.returncode, parents, flush=True)
        assert parents['observed'] != parents['expected'], parents
        assert old.returncode == 143 and 'LEGACY_ENGINE_STARTED' not in old.stdout
        run_process([sys.executable, '-c', "print('REAL_VENV_ENGINE_STARTED', flush=True)"],
                    cwd=Path.cwd(), log=Path('engine.log'))
        print('FIXED_WORKER_COMPLETED')
    """)
    print(output)
    assert "FIXED_WORKER_COMPLETED" in output
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert "REAL_VENV_ENGINE_STARTED" in log
    assert "engine_start_requested" in log and '"exit_code": 0' in log


def test_venv_engine_exit_code_and_log(venv_python, tmp_path):
    run_worker(venv_python, tmp_path, """
        import sys
        from pathlib import Path
        from voice_workbench_engines.process import run_process, EngineError
        try:
            run_process([sys.executable, '-c', "print('REAL_FAILURE', flush=True); raise SystemExit(7)"],
                        cwd=Path.cwd(), log=Path('engine.log'))
        except EngineError as error:
            assert '7' in str(error), error
        else:
            raise AssertionError('Engine failure was hidden')
    """)
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert "REAL_FAILURE" in log and '"exit_code": 7' in log


def test_venv_timeout_reaps_engine(venv_python, tmp_path):
    run_worker(venv_python, tmp_path, """
        import sys
        from pathlib import Path
        from voice_workbench_engines.process import run_process, EngineError
        code = "import os,time; from pathlib import Path; Path('engine.pid').write_text(str(os.getpid())); time.sleep(60)"
        try:
            run_process([sys.executable, '-c', code], cwd=Path.cwd(), log=Path('engine.log'), timeout=3)
        except EngineError as error:
            assert '时限' in str(error), error
        else:
            raise AssertionError('Timeout was ignored')
    """)
    assert not process_alive(int((tmp_path / "engine.pid").read_text()))
    assert "engine_stop_requested" in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_venv_worker_death_reaps_detached_descendants(venv_python, tmp_path):
    (tmp_path / "engine.py").write_text(textwrap.dedent("""
        import os,subprocess,sys,time
        from pathlib import Path
        code = "import os,time; from pathlib import Path; Path('grandchild.pid').write_text(str(os.getpid())); time.sleep(60)"
        subprocess.Popen([sys.executable, '-c', code], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        Path('engine.pid').write_text(str(os.getpid()))
        time.sleep(60)
    """), encoding="utf-8")
    script = worker_script(tmp_path, """
        import os,sys
        from pathlib import Path
        from voice_workbench_engines.process import run_process
        Path('worker.pid').write_text(str(os.getpid()))
        run_process([sys.executable, 'engine.py'], cwd=Path.cwd(), log=Path('engine.log'))
    """)
    worker = subprocess.Popen([str(venv_python), str(script)], cwd=tmp_path, env=worker_env(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pidfiles = [tmp_path / (name + ".pid") for name in ("worker", "engine", "grandchild")]
    try:
        deadline = time.monotonic() + 15
        while not all(p.exists() and p.stat().st_size for p in pidfiles) and time.monotonic() < deadline:
            time.sleep(.05)
        assert all(p.exists() and p.stat().st_size for p in pidfiles), (tmp_path / "engine.log").read_text(encoding="utf-8")
        pids = [int(p.read_text()) for p in pidfiles]
        # Popen.pid belongs to the venv redirector; kill the actual worker.
        os.kill(pids[0], signal.SIGTERM)
        worker.wait(timeout=10)
        deadline = time.monotonic() + 6
        while any(process_alive(pid) for pid in pids[1:]) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not any(process_alive(pid) for pid in pids[1:])
    finally:
        for path in pidfiles:
            if path.exists() and path.stat().st_size:
                pid = int(path.read_text())
                if process_alive(pid):
                    os.kill(pid, signal.SIGTERM)
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
