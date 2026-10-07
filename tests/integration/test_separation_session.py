"""Exercise actual supervised processes and the resident request protocol."""
import json
import os
from pathlib import Path
import sys

import pytest

from voice_workbench_engines import separation_session as sessions
from voice_workbench_engines.process import EngineError
from voice_workbench_storage import ArtifactStore


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes, api.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
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


@pytest.fixture
def session(tmp_path, monkeypatch):
    service = tmp_path / "explicit CPU service.py"
    service.write_text('''
import json,os,time,sys
from pathlib import Path
directory=Path(sys.argv[2])
(directory/'engine.pid').write_text(str(os.getpid()))
while not (directory/'stop').exists():
    paths=sorted(directory.glob('request-*.json'))
    if not paths:
        time.sleep(.02); continue
    command=json.loads(paths[0].read_text(encoding='utf-8'))
    paths[0].unlink()
    request=json.loads(Path(command['request']).read_text(encoding='utf-8'))
    print('真实进程收到请求',flush=True)
    time.sleep(request.get('delay',0))
    Path(request['response']).write_text(json.dumps({'pid':os.getpid()}))
    target=Path(command['response'])
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'ok':True}))
    os.replace(temporary,target)
''', encoding="utf-8")
    original = sessions.subprocess.Popen

    def controlled_process(command, **kwargs):
        assert command[4].endswith("separation_bridge.py")
        command = [*command]
        command[4] = str(service)
        return original(command, **kwargs)

    monkeypatch.setattr(sessions.subprocess, "Popen", controlled_process)
    store = ArtifactStore(tmp_path / "runtime")
    job = store.create_job("dataset_prepare", status="running")
    return store, job


def test_two_requests_share_one_process_and_keep_utf8_log(session, tmp_path):
    store, job = session
    pids = []
    with sessions.SeparationSession(store, job["id"]) as engine:
        root = engine.root
        for i in range(2):
            result = tmp_path / f"result{i}.json"
            request = tmp_path / f"request{i}.json"
            request.write_text(json.dumps({"response": str(result)}))
            engine.run(request, sys.executable)
            pids.append(json.loads(result.read_text())["pid"])
    assert pids[0] == pids[1]
    assert not alive(pids[0])
    assert not root.exists()
    logs = [a for a in store.inventory()["items"] if a["metadata"].get("kind") == "engine_log"]
    assert len(logs) == 1
    assert store.path(logs[0]["id"]).read_text(encoding="utf-8").count("真实进程收到请求") == 2


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_cancellation_and_timeout_reap_resident_engine(session, tmp_path, stop):
    store, job = session
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"response": str(tmp_path / "result.json"), "delay": 30}))
    pids = []

    def progress():
        pid = engine.root / "engine.pid"
        if pid.exists():
            pids[:] = [int(pid.read_text())]
            if stop == "cancel":
                raise RuntimeError("用户取消测试")

    with pytest.raises(EngineError if stop == "timeout" else RuntimeError, match="时限|取消"):
        with sessions.SeparationSession(store, job["id"]) as engine:
            root = engine.root
            engine.run(request, sys.executable, progress, timeout=1)
    assert pids and not alive(pids[0])
    assert not root.exists()
