"""Cross-process repository Integration Slot for cooperative ATC writers."""
from __future__ import annotations

import errno
import os
import subprocess
import threading
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def common_dir(repo: Path) -> Path:
    value = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return value.resolve()


class IntegrationSlot:
    """Persistent OS lock shared by linked worktrees and separate processes.

    The lock file is deliberately never unlinked: replacing it while another
    process holds the old inode would split the lock identity.
    """

    def __init__(self, repo: Path):
        self.path = common_dir(repo) / "atc" / "integration.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd: int | None = None
        self._owns_local = False
        with _LOCAL_LOCKS_GUARD:
            self._local_lock = _LOCAL_LOCKS.setdefault(str(self.path), threading.Lock())

    def acquire(self, blocking: bool = True) -> bool:
        if not self._local_lock.acquire(blocking=blocking):
            return False
        self._owns_local = True
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            self.fd = os.open(self.path, flags, 0o600)
            if os.name == "nt":
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"\0")
                while True:
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                        return True
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EDEADLK, errno.EAGAIN):
                            raise
                        if not blocking:
                            self._close_fd()
                            self._release_local()
                            return False
                        time.sleep(0.05)
            else:
                lock_flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                fcntl.flock(self.fd, lock_flags)
                return True
        except BlockingIOError:
            self._close_fd()
            self._release_local()
            return False
        except BaseException:
            self._close_fd()
            self._release_local()
            raise

    def _close_fd(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

    def _release_local(self) -> None:
        if self._owns_local:
            self._owns_local = False
            self._local_lock.release()

    def release(self) -> None:
        """Unlock and release local ownership even if OS unlock/close raises."""
        error: BaseException | None = None
        fd, self.fd = self.fd, None
        try:
            if fd is not None:
                try:
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except BaseException as exc:
                    error = exc
                finally:
                    try:
                        os.close(fd)
                    except BaseException as exc:
                        if error is None:
                            error = exc
        finally:
            self._release_local()
        if error is not None:
            raise error

    def __enter__(self) -> "IntegrationSlot":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
