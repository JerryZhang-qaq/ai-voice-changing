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
    if stopping:
        print("[supervisor] Shutdown requested before engine startup", file=sys.stderr, flush=True)
        return 143
    windows_job = None
    if os.name == "nt":
        # Windows venv python.exe is a redirector. Its child interpreter's
        # immediate parent can be the redirector, rather than the worker.
        # Check the actual worker handle and own descendants through its job.
        from windows_process import WindowsJob
        windows_job = WindowsJob(expected_parent)
    elif os.getppid() != expected_parent:
        print("[supervisor] Worker exited before engine startup", file=sys.stderr, flush=True)
        return 143
    print(f"[supervisor] Ready: pid={os.getpid()} worker={expected_parent} immediate_parent={os.getppid()}",
          file=sys.stderr, flush=True)
    engine = subprocess.Popen(sys.argv[2:], start_new_session=os.name != "nt")
    print(f"[supervisor] Engine started: pid={engine.pid}", file=sys.stderr, flush=True)
    result = 143
    try:
        while engine.poll() is None:
            if stopping or (not windows_job.parent_alive() if windows_job else os.getppid() != expected_parent):
                reason = "shutdown requested" if stopping else "worker exited"
                print(f"[supervisor] Stopping engine: {reason}", file=sys.stderr, flush=True)
                return 143
            time.sleep(.2)
        result = engine.returncode
        print(f"[supervisor] Engine exited: code={result}", file=sys.stderr, flush=True)
        return result
    finally:
        if windows_job:
            windows_job.finish(result)
        else:
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
