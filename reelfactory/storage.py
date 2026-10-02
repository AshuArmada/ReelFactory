"""Atomic file replacement and short, cross-process metadata transactions."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import time

_locks = [threading.RLock() for _ in range(64)]


def atomic_text(path, text):
    path = Path(path)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + "-", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows can briefly deny replacement while a reader or another
        # replacement holds the destination. Keep the complete temp file and
        # retry; never truncate the old destination to work around the lock.
        for attempt in range(10):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 9:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


@contextmanager
def file_lock(path):
    path = Path(path).resolve()
    with _locks[hash(str(path)) % len(_locks)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name("." + path.name + ".lock"), "a+b") as stream:
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                deadline = time.monotonic() + 60
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Another request is updating this product. Please retry.")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)
