"""Cross-platform process lock for the Desktop application."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO


class InstanceAlreadyRunning(RuntimeError):
    """Raised when another Transass Desktop process owns the lock."""


class SingleInstanceLock:
    """Acquire an exclusive, process-scoped lock until :meth:`release`."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("o bloqueio desta instância já foi adquirido")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                if self.path.stat().st_size == 0:
                    handle.write(" ")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            handle.close()
            if getattr(error, "errno", None) in {11, 13, 35, 36, 37} or isinstance(error, BlockingIOError):
                raise InstanceAlreadyRunning(f"Transass já está em execução ({self.path})") from error
            raise
        self._handle = handle
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()

