"""An OS-owned single-worker lock, released when the owning process exits."""
import os


class WorkerLock:
    def __init__(self, path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.stream = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.stream.seek(0, 2) == 0:
                    self.stream.write(b"\0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise RuntimeError("同一个运行目录只能启动一个 worker") from error
        return self

    def __exit__(self, *args):
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()
