"""Start and own a local API/worker pair; shutdown releases GPU children."""
import argparse
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def main():
    def stop(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("端口范围应为 1024—65535")
    os.chdir(ROOT)
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", args.port))
        except OSError:
            raise SystemExit("端口已被使用；请关闭已有工作台，或用 --port 更换端口")
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "voice_workbench_worker"]))
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "voice_workbench_api.app:app_factory", "--factory", "--host", "127.0.0.1", "--port", str(args.port)]))
        ready = False
        for _ in range(120):
            if any(p.poll() is not None for p in children):
                raise SystemExit("启动进程失败，请检查上方日志")
            try:
                with urlopen(f"http://127.0.0.1:{args.port}/api/health", timeout=1) as response:
                    ready = response.status == 200
                if ready:
                    break
            except OSError:
                pass
            time.sleep(.25)
        if not ready:
            raise SystemExit("API 启动超时")
        print("工作台已启动。保持此窗口运行；按 Ctrl+C 停止。", flush=True)
        if os.name == "nt" and not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        while all(p.poll() is None for p in children):
            time.sleep(.5)
        raise SystemExit("API 或工作进程已退出，请检查日志")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
