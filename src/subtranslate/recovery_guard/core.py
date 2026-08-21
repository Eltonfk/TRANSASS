"""Deterministic, fixture-only one-shot capability guard.

This module deliberately has no OpenCode registration, no CLI, no real state
root default, and no subprocess runner.  A future adapter must be separately
authorized and must supply the fixed runner.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "0.1.0-fixture"
ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
OPERATION_ID = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
FAMILY_ID = "V238_E07_R6C_B4_RECOVERY"
EPISODE_ID = "79"
EXECUTOR_ID = "RECOVERY_LEDGER_REPREPARATION_V1"
FIXED_ARGV = (
    "/usr/bin/python3",
    "/home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/tools/subtranslate_recovery_ledger_reprepare.py",
    "--apply",
)
MINIMAL_ENV = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}
TERMINAL = {"SUCCEEDED", "FAILED", "CLAIMED_EXECUTION_STATE_UNKNOWN", "STALE_INVALIDATED", "REVOKED"}


class CapabilityError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fixed_argv_identity() -> str:
    return hashlib.sha256(_canonical(list(FIXED_ARGV))).hexdigest()


def fixture_python_identity(path: Path) -> tuple[str, str]:
    """Physical interpreter identity for fixtures; no system interpreter is read."""
    if path.is_symlink() or not path.is_file():
        raise CapabilityError("UNSAFE_FIXTURE_INTERPRETER")
    return str(path.resolve()), hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_child(root: Path, child: Path) -> Path:
    if child.is_symlink() or child.parent.resolve() != root.resolve():
        raise CapabilityError("PATH_ESCAPE_OR_SYMLINK")
    return child


@dataclass(frozen=True)
class ExpectedBindings:
    target_path: str
    target_prewrite_sha256: str
    snapshot_fingerprint: str
    execution_toolchain_fingerprint: str
    executor_sha256: str
    fixed_argv_identity: str = fixed_argv_identity()
    interpreter_resolved_path: str = "/fixture/python3"
    interpreter_sha256: str = "fixture-interpreter-sha256"
    action_id: str = ACTION_ID
    operation_id: str = OPERATION_ID
    family_id: str = FAMILY_ID
    episode_id: str = EPISODE_ID
    executor_id: str = EXECUTOR_ID


class FixtureAuthenticator:
    """HMAC interface for fixtures only; never creates or persists a real key."""
    def __init__(self, key: bytes):
        if len(key) < 32:
            raise ValueError("fixture key must be at least 256 bits")
        self._key = key

    def sign(self, document: dict[str, Any]) -> str:
        unsigned = dict(document)
        unsigned.pop("authenticity", None)
        return hmac.new(self._key, _canonical(unsigned), hashlib.sha256).hexdigest()

    def verify(self, document: dict[str, Any]) -> bool:
        got = document.get("authenticity", {}).get("mac")
        return isinstance(got, str) and hmac.compare_digest(got, self.sign(document))


class StateStore:
    def __init__(self, root: Path):
        if root.is_symlink() or not root.exists():
            raise CapabilityError("FIXTURE_ROOT_MUST_EXIST_AND_BE_REGULAR")
        self.root = root.resolve()
        for name in ("armed", "claimed", "terminal", "journal", "locks"):
            path = self.root / name
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        os.chmod(self.root, 0o700)

    def folder(self, name: str) -> Path:
        return self.root / name

    def path(self, folder: str, capability_id: str) -> Path:
        if not capability_id or "/" in capability_id or capability_id in {".", ".."}:
            raise CapabilityError("INVALID_CAPABILITY_ID")
        return _safe_child(self.folder(folder), self.folder(folder) / f"{capability_id}.json")

    def write_atomic(self, path: Path, content: bytes) -> None:
        _safe_child(path.parent, path)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(content); handle.flush(); os.fsync(handle.fileno())
            os.close(fd)
            os.replace(tmp_name, path)
            os.chmod(path, 0o600)
            _fsync_dir(path.parent)
        except Exception:
            try: os.close(fd)
            except OSError: pass
            try: os.unlink(tmp_name)
            except FileNotFoundError: pass
            raise

    def read(self, path: Path) -> dict[str, Any]:
        _safe_child(path.parent, path)
        if not path.is_file() or path.is_symlink():
            raise CapabilityError("CAPABILITY_MISSING_OR_UNSAFE")
        return json.loads(path.read_bytes())

    def append_journal(self, event: str, document: dict[str, Any]) -> None:
        record = {"timestamp": time.time_ns(), "capability_id": document["capability_id"], "action_id": document["action_id"], "state": document["state"], "event": event}
        path = self.path("journal", document["capability_id"]).with_suffix(".jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, _canonical(record) + b"\n"); os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(path, 0o600); _fsync_dir(path.parent)

    def locked(self, capability_id: str):
        path = self.folder("locks") / f"{capability_id}.lock"
        class Lock:
            def __enter__(inner):
                inner.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                fcntl.flock(inner.fd, fcntl.LOCK_EX); return inner
            def __exit__(inner, *_):
                fcntl.flock(inner.fd, fcntl.LOCK_UN); os.close(inner.fd)
        return Lock()


class CapabilityIssuer:
    """External-arming library API. Broker does not import this class."""
    def __init__(self, store: StateStore, authenticator: FixtureAuthenticator, authority: str = "fixture-external-maintainer"):
        self.store, self.auth, self.authority = store, authenticator, authority

    def issue(self, bindings: ExpectedBindings, *, expires_at: int | None = None) -> dict[str, Any]:
        with self.store.locked("_issuer"):
            if list(self.store.folder("armed").glob("*.json")):
                raise CapabilityError("DUPLICATE_LIVE_CAPABILITY")
            capability_id, nonce = secrets.token_hex(32), secrets.token_urlsafe(32)
            doc = {"schema_version": SCHEMA_VERSION, "capability_id": capability_id, "nonce": nonce,
                   **asdict(bindings), "max_uses": 1, "uses": 0, "issued_at": time.time_ns(),
                   "expires_at": expires_at, "state": "ARMED", "arming_authority": self.authority}
            doc["authenticity"] = {"algorithm": "HMAC-SHA256-FIXTURE-ONLY", "mac": self.auth.sign(doc)}
            self.store.write_atomic(self.store.path("armed", capability_id), _canonical(doc))
            self.store.append_journal("ISSUED", doc)
            return doc


class Broker:
    """Execution-only guard. It intentionally has no issue/arm/rearm/reset API."""
    def __init__(self, store: StateStore, authenticator: FixtureAuthenticator, bindings: ExpectedBindings, runner: Callable[[tuple[str, ...], dict[str, str]], bool]):
        self.store, self.auth, self.bindings, self.runner = store, authenticator, bindings, runner

    def _validate(self, doc: dict[str, Any]) -> None:
        required = {"schema_version": str, "capability_id": str, "nonce": str, "arming_authority": str,
                    "issued_at": int, "action_id": str, "operation_id": str, "family_id": str,
                    "episode_id": str, "target_path": str, "target_prewrite_sha256": str,
                    "snapshot_fingerprint": str, "execution_toolchain_fingerprint": str,
                    "executor_id": str, "executor_sha256": str, "fixed_argv_identity": str,
                    "interpreter_resolved_path": str, "interpreter_sha256": str}
        if doc.get("schema_version") != SCHEMA_VERSION or any(not isinstance(doc.get(k), t) or not doc.get(k) for k, t in required.items()):
            raise CapabilityError("CAPABILITY_SCHEMA_INVALID")
        if not self.auth.verify(doc): raise CapabilityError("AUTHENTICITY_INVALID")
        if doc.get("state") != "ARMED" or doc.get("max_uses") != 1 or doc.get("uses") != 0:
            raise CapabilityError("CAPABILITY_NOT_ARMED")
        if doc.get("expires_at") is not None and time.time_ns() >= int(doc["expires_at"]):
            raise CapabilityError("CAPABILITY_EXPIRED")
        for key, value in asdict(self.bindings).items():
            if doc.get(key) != value: raise CapabilityError(f"CAPABILITY_STALE_{key.upper()}")

    def _transition(self, source: Path, state: str, event: str) -> dict[str, Any]:
        doc = self.store.read(source); doc["state"] = state; doc["uses"] = 1
        doc["authenticity"]["mac"] = self.auth.sign(doc)
        destination = self.store.path("terminal", doc["capability_id"])
        self.store.append_journal(event, doc)
        self.store.write_atomic(destination, _canonical(doc)); source.unlink(); _fsync_dir(source.parent)
        return doc

    def _reconcile_abandoned_claims(self) -> None:
        """A surviving claim is never retried; conservatively terminalize it."""
        for claimed in self.store.folder("claimed").glob("*.json"):
            cap_id = claimed.stem
            with self.store.locked(cap_id):
                if claimed.exists():
                    self._transition(claimed, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN")

    def execute_zero_args(self, args: dict[str, Any] | None = None, *, fault: str | None = None) -> str:
        if args not in (None, {}): raise CapabilityError("ZERO_ARGUMENT_CONTRACT_VIOLATION")
        self._reconcile_abandoned_claims()
        armed = list(self.store.folder("armed").glob("*.json"))
        if not armed: raise CapabilityError("CAPABILITY_NOT_ARMED")
        if len(armed) != 1: raise CapabilityError("CAPABILITY_STATE_AMBIGUOUS")
        path = armed[0]; cap_id = path.stem
        with self.store.locked(cap_id):
            try:
                doc = self.store.read(path); self._validate(doc)
            except CapabilityError as exc:
                if path.exists() and exc.args[0].startswith("CAPABILITY_STALE_") or exc.args[0] == "CAPABILITY_EXPIRED":
                    self._transition(path, "STALE_INVALIDATED", "STALE_INVALIDATED")
                raise
            claimed = self.store.path("claimed", cap_id)
            os.replace(path, claimed); _fsync_dir(path.parent); _fsync_dir(claimed.parent)
            try:
                self.store.append_journal("CLAIMED", {**doc, "state": "CLAIMED"})
            except Exception:
                self._transition(claimed, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN")
                raise CapabilityError("JOURNAL_FAILURE_AFTER_CLAIM")
            if fault == "after_claim":
                self._transition(claimed, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN")
                raise CapabilityError("CRASH_AFTER_CLAIM")
            try:
                self.store.append_journal("EXECUTOR_STARTED", {**doc, "state": "EXECUTOR_STARTED"})
                if fault == "after_start": raise RuntimeError("fixture crash")
                result = self.runner(FIXED_ARGV, dict(MINIMAL_ENV))
                if fault == "after_exit": raise RuntimeError("fixture result loss")
            except Exception:
                self._transition(claimed, "CLAIMED_EXECUTION_STATE_UNKNOWN", "UNKNOWN")
                raise CapabilityError("EXECUTION_STATE_UNKNOWN")
            self.store.append_journal("EXECUTOR_EXITED", {**doc, "state": "EXECUTOR_EXITED"})
            self._transition(claimed, "SUCCEEDED" if result else "FAILED", "SUCCEEDED" if result else "FAILED")
            return "SUCCEEDED" if result else "FAILED"
