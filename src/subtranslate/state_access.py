"""Exclusive process lease for service/maintenance access to a state root.

The lock file is permanent: unlinking it would allow two different inodes to
be locked at once. An OS lock is released automatically after a process exits.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class StateAccessBusy(RuntimeError):
    """Another service or maintenance process owns this state directory."""


class StateAccessLease:
    """Nonblocking, exclusive Linux/Windows lease; never follows a lock symlink."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self._file: BinaryIO | None = None

    def acquire(self) -> "StateAccessLease":
        if self._file is not None:
            return self
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / ".state-access.lock"
        if path.is_symlink():
            raise StateAccessBusy("lock de estado não pode ser link simbólico")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        handle = os.fdopen(fd, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise StateAccessBusy("estado ocupado: pare o serviço antes da manutenção") from error
        self._file = handle
        return self

    def release(self) -> None:
        if self._file is None:
            return
        handle, self._file = self._file, None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "StateAccessLease":
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()
