"""One supervised interpreter per batch, with serial file-based requests.

Only the current model is resident. The existing supervisor owns descendants on
both Windows and Linux; cancellation and worker death cannot leave GPU orphans.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from .process import EngineError


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class SeparationSession:
    def __init__(self, store, job_id):
        self.store, self.job_id = store, job_id
        self.temporary = None
        self.process = None
        self.serial = 0
        self.ready = None
        self.hashes = {}
        self.model_states = {}

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(dir=self.store.root, prefix=f"processing-{self.job_id}-session-")
        self.root = Path(self.temporary.name)
        self.log = self.root / "engine.log"
        return self

    def digest(self, path, hash_file):
        path = Path(path)
        info = path.stat()
        identity = (str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity not in self.hashes:
            self.hashes[identity] = hash_file(path)
        return self.hashes[identity]

    def run(self, request_path, python, progress=None, timeout=7200):
        if self.process is None:
            env = os.environ.copy()
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            self.log_stream = self.log.open("ab", buffering=0)
            command = [sys.executable, str(Path(__file__).with_name("engine_launcher.py")), str(os.getpid()),
                       python, str(Path(__file__).with_name("separation_bridge.py")), "--serve", str(self.root)]
            self.process = subprocess.Popen(command, cwd=self.root, stdout=self.log_stream, stderr=subprocess.STDOUT, env=env,
                                            start_new_session=os.name != "nt",
                                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        self.serial += 1
        response = self.root / f"finished-{self.serial}.json"
        atomic_json(self.root / f"request-{self.serial:08d}.json", {"request": str(request_path), "response": str(response)})
        started = time.monotonic()
        while not response.is_file():
            if self.process.poll() is not None:
                raise EngineError(f"分离会话退出，退出码 {self.process.returncode}，请查看任务日志")
            if time.monotonic() - started > timeout:
                raise EngineError("分离步骤超过运行时限，已停止")
            if progress:
                progress()
            time.sleep(.2)
        if progress:
            progress()
        result = json.loads(response.read_text(encoding="utf-8"))
        response.unlink()
        if result.get("error"):
            raise EngineError(result["error"])

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.process:
                (self.root / "stop").touch()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        self.process.terminate()
                    else:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name == "nt":
                            self.process.kill()
                        else:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait(timeout=5)
            if hasattr(self, "log_stream"):
                self.log_stream.close()
            if self.log.exists():
                self.store.import_file(self.log, name="批量分离会话日志.txt", job_id=self.job_id, metadata={"kind": "engine_log"})
        finally:
            self.store.update_job(self.job_id, "running", metadata={"live_engine_log": None})
            self.temporary.cleanup()
