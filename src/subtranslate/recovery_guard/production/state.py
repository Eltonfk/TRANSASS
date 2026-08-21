"""Protected-state adapter. Root is always explicit; production root is never implicit."""
from __future__ import annotations

import fcntl
import grp
import json
import os
import pwd
import tempfile
import time
import re
from pathlib import Path
from typing import Any

from .schema import SchemaError

FUTURE_STATE_ROOT = Path("/var/lib/subtranslate-guard")
PRODUCTION_STATE_ROOT = FUTURE_STATE_ROOT
PRODUCTION_STATE_USER = "subtranslate-guard"
PRODUCTION_STATE_GROUP = "subtranslate-guard"
PRODUCTION_STATE_MODE = 0o700
FOLDERS = ("armed", "claimed", "terminal", "journal", "locks", "backups")
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CLAIMED_EXECUTION_STATE_UNKNOWN", "STALE_INVALIDATED"})


class StateError(RuntimeError):
    pass


# The production token is deliberately module-private.  There is no public
# bypass switch and no caller-controlled equivalent.
_PRODUCTION_TOKEN = object()


def _mode(stat_result: os.stat_result) -> int:
    return stat_result.st_mode & 0o777


def _validate_production_state_boundary(
    path: Path,
    *,
    expected_uid: int | None = None,
    expected_gid: int | None = None,
    stat_provider=os.lstat,
) -> None:
    """Validate an already-installed state tree without changing it.

    This helper is intentionally pure with respect to the filesystem: it
    performs only lstat/resolve checks and never creates or repairs a path.
    ``expected_uid``/``expected_gid`` are injectable solely for deterministic
    offline tests; the production factory resolves the fixed system account.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        raise StateError("PRODUCTION_STATE_ROOT_FIXED")
    try:
        root_info = stat_provider(candidate)
    except (FileNotFoundError, OSError) as exc:
        raise StateError("PRODUCTION_STATE_BOUNDARY_NOT_INSTALLED") from exc
    if not os.path.isdir(candidate) or os.path.islink(candidate):
        raise StateError("PRODUCTION_STATE_ROOT_UNSAFE")
    try:
        if candidate.resolve(strict=True) != candidate.absolute():
            raise StateError("PRODUCTION_STATE_ROOT_ESCAPE")
    except OSError as exc:
        raise StateError("PRODUCTION_STATE_ROOT_UNSAFE") from exc
    if expected_uid is None or expected_gid is None:
        raise StateError("PRODUCTION_STATE_OWNER_POLICY_UNRESOLVED")
    if root_info.st_uid != expected_uid or root_info.st_gid != expected_gid:
        raise StateError("PRODUCTION_STATE_OWNER_MISMATCH")
    if _mode(root_info) != PRODUCTION_STATE_MODE:
        raise StateError("PRODUCTION_STATE_MODE_MISMATCH")

    # A writable parent would permit replacement of the state directory even
    # if the directory itself had the right mode.
    for parent in candidate.parents:
        try:
            parent_info = stat_provider(parent)
        except (FileNotFoundError, OSError) as exc:
            raise StateError("PRODUCTION_STATE_PARENT_UNSAFE") from exc
        if os.path.islink(parent) or parent_info.st_uid != 0 or (_mode(parent_info) & 0o022):
            raise StateError("PRODUCTION_STATE_PARENT_UNSAFE")

    for name in FOLDERS:
        folder = candidate / name
        try:
            info = stat_provider(folder)
        except (FileNotFoundError, OSError) as exc:
            raise StateError("PRODUCTION_STATE_LAYOUT_INCOMPLETE") from exc
        if os.path.islink(folder) or not os.path.isdir(folder):
            raise StateError("PRODUCTION_STATE_LAYOUT_UNSAFE")
        if info.st_uid != expected_uid or info.st_gid != expected_gid or _mode(info) != PRODUCTION_STATE_MODE:
            raise StateError("PRODUCTION_STATE_LAYOUT_UNSAFE")


def open_installed_production_state() -> "ProductionStateStore":
    """Open the fixed, pre-created production state root read/write.

    The future privileged foundation creates the root and folders.  This
    factory only resolves the fixed account and validates the installed
    boundary; it never creates, chmods, chowns, or otherwise repairs state.
    """
    if PRODUCTION_STATE_ROOT != Path("/var/lib/subtranslate-guard"):
        raise StateError("PRODUCTION_STATE_ROOT_POLICY_INVALID")
    try:
        expected_uid = pwd.getpwnam(PRODUCTION_STATE_USER).pw_uid
        expected_gid = grp.getgrnam(PRODUCTION_STATE_GROUP).gr_gid
    except KeyError as exc:
        raise StateError("PRODUCTION_STATE_BOUNDARY_NOT_INSTALLED") from exc
    _validate_production_state_boundary(
        PRODUCTION_STATE_ROOT, expected_uid=expected_uid, expected_gid=expected_gid
    )
    return ProductionStateStore(PRODUCTION_STATE_ROOT, _production_token=_PRODUCTION_TOKEN)

CAPABILITY_ID_RE = re.compile(r"^[0-9a-f]{64}$")

def validate_capability_id(capability_id: str) -> str:
    if not isinstance(capability_id, str) or CAPABILITY_ID_RE.fullmatch(capability_id) is None:
        raise StateError("CAPABILITY_ID_INVALID")
    return capability_id


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


class ProductionStateStore:
    def __init__(self, root: Path, *, _production_token=None):
        root = Path(root)
        is_production_namespace = False
        if root.is_absolute():
            try:
                is_production_namespace = (
                    root == PRODUCTION_STATE_ROOT
                    or PRODUCTION_STATE_ROOT in root.parents
                    or root.resolve(strict=False) == PRODUCTION_STATE_ROOT
                )
            except OSError:
                is_production_namespace = True
        if is_production_namespace:
            if root != PRODUCTION_STATE_ROOT or _production_token is not _PRODUCTION_TOKEN:
                raise StateError("REAL_STATE_ROOT_REQUIRES_INSTALLATION")
            # Production state is created by the privileged foundation; the
            # runtime adapter must never auto-create or repair it.
            try:
                uid = pwd.getpwnam(PRODUCTION_STATE_USER).pw_uid
                gid = grp.getgrnam(PRODUCTION_STATE_GROUP).gr_gid
            except KeyError as exc:
                raise StateError("PRODUCTION_STATE_BOUNDARY_NOT_INSTALLED") from exc
            _validate_production_state_boundary(root, expected_uid=uid, expected_gid=gid)
            self.root = root
            return
        if root.is_symlink() or not root.exists() or not root.is_dir():
            raise StateError("STATE_ROOT_UNSAFE")
        # Keep the caller's lexical root.  Resolving before checking would
        # silently accept a symlink and move all state outside the fixture.
        self.root = root.absolute()
        for name in FOLDERS:
            folder = self.root / name
            folder.mkdir(mode=0o700, exist_ok=True)
            if folder.is_symlink(): raise StateError("STATE_SYMLINK")
            os.chmod(folder, 0o700)
        os.chmod(self.root, 0o700)

    def path(self, folder: str, capability_id: str) -> Path:
        validate_capability_id(capability_id)
        if folder not in FOLDERS:
            raise StateError("STATE_PATH_INVALID")
        path = self.root / folder / (capability_id + (".jsonl" if folder == "journal" else ".json"))
        if path.parent != self.root / folder or path.is_symlink(): raise StateError("STATE_PATH_ESCAPE")
        return path

    def write_atomic(self, path: Path, data: bytes) -> None:
        self._validate_state_path(path)
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600); os.write(fd, data); os.fsync(fd); os.close(fd)
            os.replace(temporary, path); os.chmod(path, 0o600); _fsync_dir(path.parent)
        except Exception:
            try: os.close(fd)
            except OSError: pass
            try: os.unlink(temporary)
            except FileNotFoundError: pass
            raise

    def read_document(self, path: Path) -> dict[str, Any]:
        self._validate_state_path(path)
        if path.is_symlink() or not path.is_file(): raise StateError("CAPABILITY_MISSING_OR_UNSAFE")
        try: return json.loads(path.read_bytes())
        except Exception as exc: raise StateError("CAPABILITY_JSON_INVALID") from exc

    def append_event(self, event: str, payload: dict[str, Any], state: str) -> None:
        path = self.path("journal", payload["capability_id"])
        record = {"timestamp_ns": time.time_ns(), "event": event, "state": state,
                  "capability_id": payload["capability_id"], "action_id": payload["action_id"]}
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"); os.fsync(fd)
        finally: os.close(fd)
        os.chmod(path, 0o600); _fsync_dir(path.parent)

    def has_pending_issuance(self) -> bool:
        for path in (self.root / "journal").glob("*.jsonl"):
            if path.is_symlink(): raise StateError("STATE_SYMLINK")
            validate_capability_id(path.stem)
            pending = False
            for line in path.read_bytes().splitlines():
                try:
                    event = json.loads(line).get("event")
                    if event == "ISSUED_PENDING": pending = True
                    elif event == "ISSUED": pending = False
                except Exception: raise StateError("JOURNAL_INVALID")
            if pending: return True
        return False

    def lock(self, capability_id: str):
        validate_capability_id(capability_id)
        path = self.root / "locks" / (capability_id + ".lock")
        return self._lock_path(path)

    def lock_issuer(self):
        return self._lock_path(self.root / "locks" / "issuer.lock")

    def _lock_path(self, path: Path):
        class _Lock:
            def __enter__(inner):
                inner.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                fcntl.flock(inner.fd, fcntl.LOCK_EX); return inner
            def __exit__(inner, *_):
                fcntl.flock(inner.fd, fcntl.LOCK_UN); os.close(inner.fd)
        return _Lock()

    def armed_paths(self) -> list[Path]:
        paths = list((self.root / "armed").glob("*.json"))
        if any(path.is_symlink() or CAPABILITY_ID_RE.fullmatch(path.stem) is None for path in paths): raise StateError("STATE_PATH_INVALID")
        return paths

    def move_claim(self, armed: Path, capability_id: str) -> Path:
        validate_capability_id(capability_id)
        self._validate_state_path(armed, expected_folder="armed")
        claimed = self.path("claimed", capability_id)
        os.replace(armed, claimed); _fsync_dir(armed.parent); _fsync_dir(claimed.parent)
        return claimed

    def terminalize(self, claimed: Path, payload: dict[str, Any], state: str, event: str) -> None:
        if state not in TERMINAL: raise StateError("TERMINAL_STATE_INVALID")
        validate_capability_id(payload.get("capability_id"))
        # Stale validation may terminalize directly from ARMED; execution
        # reconciliation terminalizes from CLAIMED.  Both are confined.
        self._validate_state_path(claimed)
        self.append_event(event, payload, state)
        terminal = self.path("terminal", payload["capability_id"])
        os.replace(claimed, terminal); _fsync_dir(claimed.parent); _fsync_dir(terminal.parent)

    def _validate_state_path(self, path: Path, expected_folder: str | None = None) -> None:
        """Validate a state path before any open/lock/rename operation."""
        path = Path(path)
        try:
            relative = path.absolute().relative_to(self.root)
        except ValueError as exc:
            raise StateError("STATE_PATH_ESCAPE") from exc
        parts = relative.parts
        if len(parts) != 2 or parts[0] not in FOLDERS or (expected_folder and parts[0] != expected_folder):
            raise StateError("STATE_PATH_INVALID")
        suffix = ".jsonl" if parts[0] == "journal" else ".json"
        if not parts[1].endswith(suffix):
            raise StateError("STATE_PATH_INVALID")
        validate_capability_id(parts[1][:-len(suffix)])
        folder = self.root / parts[0]
        if folder.is_symlink() or path.is_symlink():
            raise StateError("STATE_SYMLINK")
