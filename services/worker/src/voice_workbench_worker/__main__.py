import os
import signal
import re
import shutil
from threading import Event, Thread
from pathlib import Path
import time

from voice_workbench_storage import ArtifactStore
from voice_workbench_storage.locking import WorkerLock
from .runner import run_one, WorkerStopping
from voice_workbench_engines.separation import status as separation_status
from voice_workbench_engines.rvc import status as rvc_status


def main():
    def stop(signum, frame):
        raise WorkerStopping()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    store = ArtifactStore(os.environ.get("WORKBENCH_RUNTIME", "runtime"))
    with WorkerLock(store.root / "worker.lock"):
        with store.connect() as db:
            interrupted = [r[0] for r in db.execute("SELECT id FROM jobs WHERE status='running'")]
        if interrupted:
            # Engine watchdogs poll every .2s and allow 2s before force-stopping.
            # Keep artifact holds until those owned processes have stopped.
            time.sleep(3)
        for job_id in interrupted:
            store.update_job(job_id, "interrupted", error="worker 重启中断了任务，请重新提交；已保存的产物可检查或清理")
        from voice_workbench_engines.resources import recover_install_partials
        recover_install_partials(store)
        for directory in store.root.glob("processing-*"):
            match = re.fullmatch(r"processing-([0-9a-f]{32})-.+", directory.name)
            if not match or directory.is_symlink() or not directory.is_dir():
                continue
            with store.connect() as db:
                row = db.execute("SELECT status FROM jobs WHERE id=?", (match[1],)).fetchone()
            if row and row["status"] not in {"queued", "running"}:
                shutil.rmtree(directory)
        stopped = Event()
        def heartbeat():
            while not stopped.wait(5):
                store.touch_runtime("engines")
        Thread(target=heartbeat, daemon=True, name="worker-heartbeat").start()
        last_probe = 0.
        try:
            while True:
                if time.monotonic() - last_probe > 15:
                    store.set_runtime("engines", {"separation": separation_status(), "rvc": rvc_status()})
                    last_probe = time.monotonic()
                if not run_one(store):
                    time.sleep(.5)
        except WorkerStopping:
            return
        finally:
            stopped.set()
            with store.connect(write=True) as db:
                db.execute("DELETE FROM runtime_state WHERE key='engines'")


if __name__ == "__main__":
    main()
