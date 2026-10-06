"""Watch the worker even after SIGKILL, and stop the entire owned engine group."""
import os
import signal
import subprocess
import sys
import time


def main():
    expected_parent = int(sys.argv[1])
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if os.getppid() != expected_parent or stopping:
        return 143
    windows_job = None
    if os.name == "nt":
        from windows_process import WindowsJob
        windows_job = WindowsJob(expected_parent)
    engine = subprocess.Popen(sys.argv[2:], start_new_session=os.name != "nt")
    result = 143
    try:
        while engine.poll() is None:
            if stopping or (not windows_job.parent_alive() if windows_job else os.getppid() != expected_parent):
                return 143
            time.sleep(.2)
        result = engine.returncode
        return result
    finally:
        if windows_job:
            windows_job.finish(result)
            return result
        try:
            os.killpg(engine.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            engine.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(engine.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        engine.wait()


if __name__ == "__main__":
    raise SystemExit(main())
