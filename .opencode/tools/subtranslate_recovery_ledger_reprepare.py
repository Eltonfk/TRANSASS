#!/usr/bin/env python3
"""AUTO-03B1: action-specific recovery-ledger re-preparation executor.

The production CLI is intentionally read-only in AUTO-03B1.  ``--plan``
evaluates the fixed real action; ``--apply`` is rejected by the CLI.  The
write path is exposed only through ``apply_for_fixture`` so offline tests can
exercise backup, atomic publish, validation and rollback without touching the
real authority or runtime roots.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
RUNTIME_PARENT = AUTHORITY_ROOT / "runtime-evidence"
BACKUP_ROOT = Path("/home/palhacinho/opencode-backups")
PROJECT_STATE_NAME = "PROJECT_STATE.json"
HANDOFF_NAME = "HANDOFF_CHATGPT.md"
ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
ACTION_CLASS = "RUNTIME_CONTROL"
EXECUTOR_ID = "RECOVERY_LEDGER_REPREPARATION_V1"
EXECUTOR_VERSION = "0.2.0"
PLAN_SCHEMA_VERSION = "0.2.0"
FAMILY_ID = "V238_E07_R6C_B4_RECOVERY"
EPISODE_ID = "79"
OPERATION_ID = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
EXPECTED_PROJECT_STATE_SHA256 = "5c61765aa877a44e007897b85bac50fc139115df4e5ede740435480ca9df6303"
EXPECTED_LEDGER_MISSING_FIELDS = (
    "episode_id", "episode_family_id", "family_contract",
    "family_contract_sha256", "logical_calls", "updated_at",
)
ALLOWED_FIELDS = list(EXPECTED_LEDGER_MISSING_FIELDS)
EXPECTED_ENVELOPE = {"planned_initial_calls": 1, "physical_ceiling": 1, "retry_reserve": 0}
MAX_FILE_BYTES = 8 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 0.0
TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_recovery_ledger_reprepare.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-audit.md",
)

try:
    source_root = CANDIDATE_ROOT / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from subtranslate.v238_per_call_durability import (  # type: ignore
        EpisodeBudgetLedger,
        _family_contract,
        canonical_bytes,
    )
except Exception:  # pragma: no cover - exercised through structured failure paths
    EpisodeBudgetLedger = None  # type: ignore[assignment]
    _family_contract = None  # type: ignore[assignment]
    canonical_bytes = lambda value: (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")  # type: ignore[assignment]


class ExecutorIssue(RuntimeError):
    def __init__(self, code: str, evidence: Any = None, severity: str = "BLOCK") -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence
        self.severity = severity


@dataclass(frozen=True)
class ExecutorRoots:
    candidate_root: Path
    authority_root: Path
    project_state: Path
    handoff: Path
    backup_root: Path
    expected_project_state_sha256: str | None = EXPECTED_PROJECT_STATE_SHA256
    expected_runtime_root: Path | None = None
    expected_target_sha256: str | None = "f434a4718e0d32cd8f4b3bd7548fbed6a1ce428b0a77264b396236b8928539cc"
    fixture_temporary_root: Path | None = None

    @classmethod
    def real(cls) -> "ExecutorRoots":
        return cls(
            candidate_root=CANDIDATE_ROOT,
            authority_root=AUTHORITY_ROOT,
            project_state=AUTHORITY_ROOT / PROJECT_STATE_NAME,
            handoff=AUTHORITY_ROOT / HANDOFF_NAME,
            backup_root=BACKUP_ROOT,
            expected_runtime_root=RUNTIME_PARENT / "V238_E07_R6C_B4_RECOVERY",
            expected_target_sha256="f434a4718e0d32cd8f4b3bd7548fbed6a1ce428b0a77264b396236b8928539cc",
        )


@dataclass(frozen=True)
class FixtureExecutionContext:
    """Explicit, test-only execution context with no route to real roots.

    This object is deliberately not accepted by the public CLI.  Offline tests
    must construct it from their own TemporaryDirectory and call
    ``apply_fixture_context`` directly.
    """

    temporary_root: Path
    roots: ExecutorRoots
    runtime_root: Path
    target_ledger: Path
    operation_file: Path
    lock_file: Path
    backup_root: Path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _fixture_context_issue(context: FixtureExecutionContext) -> ExecutorIssue | None:
    """Reject real roots, traversal and symlink escapes before any fixture write."""
    try:
        temporary_root = context.temporary_root.resolve(strict=True)
    except OSError:
        return ExecutorIssue("TEST_FIXTURE_CONTEXT_INCOMPLETE", "temporary_root", "UNKNOWN")
    real_roots = (AUTHORITY_ROOT.resolve(strict=False), BACKUP_ROOT.resolve(strict=False), CANDIDATE_ROOT.resolve(strict=False))
    expected = {
        "authority_root": context.roots.authority_root,
        "candidate_root": context.roots.candidate_root,
        "runtime_root": context.runtime_root,
        "target_ledger": context.target_ledger,
        "operation_file": context.operation_file,
        "lock_file": context.lock_file,
        "backup_root": context.backup_root,
    }
    if context.roots.expected_runtime_root is None:
        return ExecutorIssue("TEST_FIXTURE_CONTEXT_INCOMPLETE", "expected_runtime_root", "UNKNOWN")
    if not (
        _same_path(context.runtime_root, context.roots.expected_runtime_root)
        and _same_path(context.target_ledger, context.runtime_root / "episode-budget.json")
        and _same_path(context.operation_file, context.runtime_root / "operation.json")
        and _same_path(context.lock_file, context.runtime_root / "episode-budget.json.lock")
        and _same_path(context.backup_root, context.roots.backup_root)
    ):
        return ExecutorIssue("TEST_FIXTURE_CONTEXT_INCOMPLETE", "derived paths", "UNKNOWN")
    for name, path in expected.items():
        if not _inside(path, temporary_root):
            return ExecutorIssue("TEST_FIXTURE_ROOT_ESCAPE", {"field": name, "path": str(path)})
        if any(_same_path(path, real) or _inside(path, real) for real in real_roots):
            return ExecutorIssue("TEST_FIXTURE_ROOT_ESCAPE", {"field": name, "path": str(path), "real_root": True})
    for path in (context.runtime_root, context.target_ledger, context.operation_file, context.lock_file):
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                return ExecutorIssue("TEST_FIXTURE_ROOT_ESCAPE", {"path": str(path), "symlink": True})
        except OSError:
            return ExecutorIssue("TEST_FIXTURE_CONTEXT_INCOMPLETE", str(path), "UNKNOWN")
    return None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return canonical_bytes(value)


def _toolchain_fingerprint(manifest: list[dict[str, Any]]) -> str:
    """Match the probe's newline-free, canonical toolchain serialization."""
    return _digest(json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8"))


def _read_bytes(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            raise ExecutorIssue("UNEXPECTED_SYMLINK", str(path))
        if not stat.S_ISREG(before.st_mode):
            raise ExecutorIssue("FILE_NOT_REGULAR", str(path))
        if before.st_size > MAX_FILE_BYTES:
            raise ExecutorIssue("FILE_TOO_LARGE", {"path": str(path), "size": before.st_size})
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ExecutorIssue("FILE_TOO_LARGE", {"path": str(path), "size": len(data)})
        after = os.lstat(path)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode"):
            if getattr(before, field) != getattr(after, field):
                raise ExecutorIssue("EXECUTOR_PRECONDITION_CHANGED", {"path": str(path), "field": field})
        return data, before
    except FileNotFoundError as exc:
        raise ExecutorIssue("FILE_DISAPPEARED", str(path), "UNKNOWN") from exc
    except PermissionError as exc:
        raise ExecutorIssue("FILE_PERMISSION_DENIED", str(path), "UNKNOWN") from exc
    except ExecutorIssue:
        raise
    except OSError as exc:
        raise ExecutorIssue("FILE_READ_ERROR", {"path": str(path), "error": type(exc).__name__}, "UNKNOWN") from exc


def _read_json(path: Path) -> tuple[Any, bytes, os.stat_result]:
    data, metadata = _read_bytes(path)
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ExecutorIssue("INVALID_JSON", str(path)) from exc
    if not isinstance(value, dict):
        raise ExecutorIssue("JSON_ROOT_NOT_OBJECT", str(path))
    return value, data, metadata


def _descendant(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return False


def _stable_toolchain(candidate_root: Path = CANDIDATE_ROOT) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    for relative in TOOLCHAIN_COMPONENTS:
        path = candidate_root / relative
        try:
            data, _ = _read_bytes(path)
            components.append({"path": relative, "sha256": _digest(data)})
        except ExecutorIssue as issue:
            components.append({"path": relative, "sha256": None, "error": issue.code})
    manifest = [{"path": item["path"], "sha256": item["sha256"]} for item in components]
    fingerprint = _toolchain_fingerprint(manifest) if all(item["sha256"] for item in manifest) else None
    return {
        "executor_id": EXECUTOR_ID,
        "components": components,
        "execution_toolchain_fingerprint": fingerprint,
    }


def _official_initial(state: Mapping[str, Any], budget: Mapping[str, Any]) -> dict[str, Any]:
    if EpisodeBudgetLedger is None or _family_contract is None:
        raise ExecutorIssue("OFFICIAL_SCHEMA_UNAVAILABLE", "subtranslate.v238_per_call_durability", "UNKNOWN")
    authority = state.get("current_authority")
    reconciliation = state.get("r6c_reconciliation")
    if not isinstance(authority, dict) or not isinstance(reconciliation, dict):
        raise ExecutorIssue("OFFICIAL_FAMILY_CONTEXT_UNAVAILABLE", "PROJECT_STATE.json", "UNKNOWN")
    required = {
        "candidate_commit": authority.get("candidate_commit"),
        "anime_series_id": reconciliation.get("anime_series_id"),
        "episode_id": reconciliation.get("episode_id"),
        "source_sha256": reconciliation.get("source_sha256"),
        "pipeline_id": reconciliation.get("pipeline"),
        "stage_id": reconciliation.get("stage"),
        "model": reconciliation.get("model"),
        "model_digest": reconciliation.get("model_digest"),
        "prompt_schema_hash": reconciliation.get("prompt_schema_hash"),
        "glossary_hash": reconciliation.get("glossary_hash"),
        "configuration_hash": reconciliation.get("configuration_hash"),
    }
    if any(value in (None, "") for value in required.values()):
        raise ExecutorIssue("OFFICIAL_FAMILY_CONTEXT_INCOMPLETE", sorted(key for key, value in required.items() if value in (None, "")), "UNKNOWN")
    context = {
        "episode_family_id": FAMILY_ID,
        "anime_series_id": required["anime_series_id"],
        "episode_id": required["episode_id"],
        "source_sha256": required["source_sha256"],
        "pipeline_id": required["pipeline_id"],
        "stage_id": required["stage_id"],
        "model": required["model"],
        "model_tag": required["model"],
        "model_digest": required["model_digest"],
        "prompt_schema_hash": required["prompt_schema_hash"],
        "glossary_hash": required["glossary_hash"],
        "configuration_hash": required["configuration_hash"],
        "candidate_commit": required["candidate_commit"],
    }
    family_contract = dict(_family_contract(context))
    limits = {
        "planned_initial_calls": budget.get("planned_initial_calls", 0),
        "retry_reserve": budget.get("retry_reserve", 0),
        "physical_ceiling": budget.get("physical_ceiling", 0),
        "operation_retry_transport_cap": budget.get("operation_retry_transport_cap"),
        "per_event_retry_transport_cap": budget.get("per_event_retry_transport_cap"),
    }
    # EpisodeBudgetLedger.__init__ creates/chmods directories.  Calling it
    # would violate --plan read-only, so initialize only its pure data
    # attributes and invoke the official _initial() implementation itself.
    ledger = EpisodeBudgetLedger.__new__(EpisodeBudgetLedger)
    ledger.path = Path("<offline-plan>")
    ledger.family_contract = family_contract
    ledger.episode_id = str(family_contract["episode_id"])
    ledger.family_contract_sha256 = str(family_contract["family_contract_sha256"])
    ledger.limits = {
        "planned_initial_calls": int(limits.get("planned_initial_calls", 0) or 0),
        "retry_reserve": int(limits.get("retry_reserve", 0) or 0),
        "physical_ceiling": int(limits.get("physical_ceiling", 0) or 0),
        "operation_retry_transport_cap": None if limits.get("operation_retry_transport_cap") is None else int(limits["operation_retry_transport_cap"]),
        "per_event_retry_transport_cap": None if limits.get("per_event_retry_transport_cap") is None else int(limits["per_event_retry_transport_cap"]),
    }
    return ledger._initial()


class RecoveryLedgerExecutor:
    """Fixed identity executor; arbitrary actions and paths are impossible."""

    def __init__(self, roots: ExecutorRoots | None = None) -> None:
        self.roots = roots or ExecutorRoots.real()

    def _runtime_from_state(self, state: Mapping[str, Any]) -> Path:
        e1 = state.get("r6c_batch4_recovery_e1_reconciliation")
        if not isinstance(e1, dict):
            raise ExecutorIssue("CANONICAL_E1_NOT_TOP_LEVEL", "r6c_batch4_recovery_e1_reconciliation")
        protocol = state.get("r6c_batch4_recovery_protocol")
        if isinstance(protocol, dict) and "r6c_batch4_recovery_e1_reconciliation" in protocol:
            raise ExecutorIssue("CANONICAL_E1_NESTED", "r6c_batch4_recovery_protocol")
        runtime_value = e1.get("runtime_root")
        if not isinstance(runtime_value, str) or not runtime_value:
            raise ExecutorIssue("RUNTIME_ROOT_UNAVAILABLE", "PROJECT_STATE.json", "UNKNOWN")
        runtime = Path(runtime_value).resolve(strict=False)
        runtime_parent = (self.roots.authority_root / "runtime-evidence").resolve(strict=True)
        if not _descendant(runtime, runtime_parent):
            raise ExecutorIssue("RUNTIME_ROOT_OUTSIDE_AUTHORITY", runtime_value)
        expected = self.roots.expected_runtime_root
        if expected is not None and not _same_path(runtime, expected):
            raise ExecutorIssue("RUNTIME_ROOT_UNEXPECTED", {"actual": str(runtime), "expected": str(expected)})
        return runtime

    def _collect(self) -> dict[str, Any]:
        state, state_bytes, state_stat = _read_json(self.roots.project_state)
        state_sha = _digest(state_bytes)
        if self.roots.expected_project_state_sha256 and state_sha != self.roots.expected_project_state_sha256:
            raise ExecutorIssue("CANONICAL_PRESTATE_CHANGED", {"expected": self.roots.expected_project_state_sha256, "actual": state_sha})
        if state.get("next_action") != "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION":
            raise ExecutorIssue("CANONICAL_ACTION_PRECONDITION_MISMATCH", state.get("next_action"))
        runtime = self._runtime_from_state(state)
        target = runtime / "episode-budget.json"
        operation = runtime / "operation.json"
        lock = runtime / "episode-budget.json.lock"
        budget, budget_bytes, budget_stat = _read_json(target)
        operation_value, operation_bytes, operation_stat = _read_json(operation)
        lock_bytes, lock_stat = _read_bytes(lock)
        if operation_value.get("operation_id") != OPERATION_ID:
            raise ExecutorIssue("OPERATION_ID_MISMATCH", operation_value.get("operation_id"))
        if isinstance(state.get("r6c_batch4_recovery_e1_reconciliation"), dict) and state["r6c_batch4_recovery_e1_reconciliation"].get("operation_id") != OPERATION_ID:
            raise ExecutorIssue("CANONICAL_OPERATION_ID_MISMATCH", state["r6c_batch4_recovery_e1_reconciliation"].get("operation_id"))
        for key, expected in EXPECTED_ENVELOPE.items():
            try:
                actual = int(budget.get(key))
            except (TypeError, ValueError):
                raise ExecutorIssue("LEDGER_ENVELOPE_INVALID", {"field": key, "actual": budget.get(key)})
            if actual != expected:
                raise ExecutorIssue("LEDGER_ENVELOPE_MISMATCH", {"field": key, "expected": expected, "actual": actual})
        if int(budget.get("initial_consumed", -1)) != 0:
            raise ExecutorIssue("LEDGER_INITIAL_CONSUMPTION_NONZERO", budget.get("initial_consumed"))
        if int(budget.get("retry_consumed", -1)) != 0:
            raise ExecutorIssue("LEDGER_RETRY_CONSUMPTION_NONZERO", budget.get("retry_consumed"))
        if budget.get("reservations") != []:
            raise ExecutorIssue("LEDGER_RESERVATIONS_PRESENT", budget.get("reservations"))
        calls = runtime / "calls"
        try:
            calls_stat = os.lstat(calls)
            if stat.S_ISLNK(calls_stat.st_mode):
                raise ExecutorIssue("CALLS_DIR_SYMLINK", str(calls))
            if not stat.S_ISDIR(calls_stat.st_mode):
                raise ExecutorIssue("CALLS_PATH_NOT_DIRECTORY", str(calls))
            names = sorted(item.name for item in calls.iterdir() if item.name)
            raise ExecutorIssue("CALLS_OR_ATTEMPTS_PRESENT", names)
        except FileNotFoundError:
            calls_stat = None
        e1 = state["r6c_batch4_recovery_e1_reconciliation"]
        r6 = state.get("r6c_reconciliation")
        if e1.get("runtime_family_id") != FAMILY_ID:
            raise ExecutorIssue("CANONICAL_FAMILY_MISMATCH", e1.get("runtime_family_id"))
        if not isinstance(r6, dict) or r6.get("episode_id") != 79:
            raise ExecutorIssue("CANONICAL_EPISODE_MISMATCH", r6.get("episode_id") if isinstance(r6, dict) else None)
        target_sha = _digest(budget_bytes)
        if self.roots.expected_target_sha256 and target_sha != self.roots.expected_target_sha256:
            raise ExecutorIssue("LEDGER_PRESTATE_HASH_MISMATCH", {"expected": self.roots.expected_target_sha256, "actual": target_sha})
        official = _official_initial(state, budget)
        missing = [field for field in EXPECTED_LEDGER_MISSING_FIELDS if field not in budget]
        if set(missing) != set(EXPECTED_LEDGER_MISSING_FIELDS):
            if not missing:
                for field, expected in official.items():
                    if field == "updated_at":
                        if not isinstance(budget.get(field), str) or not budget.get(field):
                            raise ExecutorIssue("ALREADY_REPREPARED_IDENTITY_INVALID", field)
                    elif budget.get(field) != expected:
                        raise ExecutorIssue("ALREADY_REPREPARED_IDENTITY_MISMATCH", field)
                state_kind = "ALREADY_REPREPARED"
            else:
                raise ExecutorIssue("LEDGER_IDENTITY_SCHEMA_UNEXPECTED", {"missing": missing})
        else:
            state_kind = "ELIGIBLE"
        handoff_bytes, handoff_stat = _read_bytes(self.roots.handoff)
        target_stat = budget_stat
        return {
            "state": state,
            "state_bytes": state_bytes,
            "state_stat": state_stat,
            "state_sha256": state_sha,
            "runtime": runtime,
            "target": target,
            "target_bytes": budget_bytes,
            "target_stat": target_stat,
            "target_sha256": target_sha,
            "operation": operation,
            "operation_bytes": operation_bytes,
            "operation_stat": operation_stat,
            "lock": lock,
            "lock_bytes": lock_bytes,
            "lock_stat": lock_stat,
            "handoff_bytes": handoff_bytes,
            "handoff_stat": handoff_stat,
            "budget": budget,
            "official_initial": official,
            "state_kind": state_kind,
            "calls_stat": calls_stat,
        }

    def _base_plan(self, *, current_target_sha256: str | None, blockers: list[dict[str, Any]], unknowns: list[dict[str, Any]], expected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        toolchain = _stable_toolchain(self.roots.candidate_root)
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "action_id": ACTION_ID,
            "executor_id": EXECUTOR_ID,
            "eligible": not blockers and not unknowns,
            "preconditions": [
                "canonical_project_state_sha256_and_action",
                "canonical_e1_top_level_only",
                "fixed_runtime_root_and_operation_id",
                "ledger_envelope_1_1_0_and_zero_consumption",
                "reservations_empty_and_calls_attempts_absent",
                "official_episode_budget_initializer_available",
                "target_regular_file_and_ownership_observable",
                "persistent_backup_proof_before_publish",
            ],
            "current_target_sha256": current_target_sha256,
            "expected_changes": expected or [],
            "allowed_fields": ALLOWED_FIELDS,
            "mode": "plan",
            "blocked_reasons": blockers,
            "unknowns": unknowns,
            "backup_requirements": {
                "persistent": True,
                "root": str(self.roots.backup_root),
                "outside_runtime_target": True,
                "must_not_overwrite_existing_bundle": True,
                "contents": ["episode-budget.json", "operation.json", "PROJECT_STATE.json", "HANDOFF_CHATGPT.md", "episode-budget.json.lock", "manifest.json"],
            },
            "rollback_policy": {
                "automatic_retry": False,
                "max_retries": 0,
                "automatic_rollback_on_postcheck_failure": True,
                "max_rollback_attempts": 1,
                "rollback_is_retry": False,
            },
            "lock_policy": {
                "lock_path": "episode-budget.json.lock",
                "exclusive": True,
                "timeout_seconds": LOCK_TIMEOUT_SECONDS,
                "held_through_postvalidation_and_rollback": True,
                "must_exist": True,
            },
            "post_execution": {
                "probe_required": True,
                "probe_max_attempts": 1,
                "audit_required": True,
                "audit_max_calls": 1,
                "canonical_reconciliation_required_before_next_operational_phase": True,
            },
            "apply_permission_active": False,
            "toolchain": toolchain,
            "side_effects_performed": False,
        }

    def plan(self) -> dict[str, Any]:
        try:
            info = self._collect()
            official = info["official_initial"]
            expected = []
            for field in EXPECTED_LEDGER_MISSING_FIELDS:
                item: dict[str, Any] = {"field": field, "from": "ABSENT", "source": "EpisodeBudgetLedger._initial"}
                if field == "updated_at":
                    item["expected_type"] = "str"
                    item["value_policy"] = "fresh_official_initializer_timestamp"
                elif field == "family_contract":
                    item["expected_type"] = "dict"
                    item["expected_value"] = official[field]
                elif field == "logical_calls":
                    item["expected_type"] = "dict"
                    item["expected_value"] = {}
                else:
                    item["expected_type"] = type(official[field]).__name__
                    item["expected_value"] = official[field]
                expected.append(item)
            if info["state_kind"] == "ALREADY_REPREPARED":
                blockers = [{"code": "ALREADY_REPREPARED", "severity": "BLOCK", "evidence": "no rewrite permitted"}]
                return self._base_plan(current_target_sha256=info["target_sha256"], blockers=blockers, unknowns=[], expected=[])
            return self._base_plan(current_target_sha256=info["target_sha256"], blockers=[], unknowns=[], expected=expected)
        except ExecutorIssue as issue:
            blockers = []
            unknowns = []
            target = None
            if issue.severity == "UNKNOWN":
                unknowns.append({"code": issue.code, "severity": issue.severity, "evidence": issue.evidence})
            else:
                blockers.append({"code": issue.code, "severity": issue.severity, "evidence": issue.evidence})
            return self._base_plan(current_target_sha256=target, blockers=blockers, unknowns=unknowns)
        except Exception as exc:  # fail closed and structured
            return self._base_plan(current_target_sha256=None, blockers=[{"code": "EXECUTOR_INTERNAL_ERROR", "severity": "BLOCK", "evidence": type(exc).__name__}], unknowns=[])

    @contextlib.contextmanager
    def _exclusive_lock(self, lock_path: Path):
        """Acquire the pre-existing official lock without creating or touching it."""
        _, expected = _read_bytes(lock_path)
        try:
            descriptor = os.open(lock_path, os.O_RDONLY)
        except OSError as exc:
            raise ExecutorIssue("EXECUTION_LOCK_UNAVAILABLE", {"path": str(lock_path), "error": type(exc).__name__}) from exc
        try:
            actual = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ExecutorIssue("EXECUTOR_PRECONDITION_CHANGED", {"path": str(lock_path), "field": "identity"})
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ExecutorIssue("EXECUTION_LOCK_UNAVAILABLE", {"path": str(lock_path), "timeout_seconds": LOCK_TIMEOUT_SECONDS}) from exc
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _atomic_write(path: Path, data: bytes, original_stat: os.stat_result) -> None:
        """Validate a same-directory temporary file, then publish with one replace."""
        fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temporary = Path(raw)
        try:
            os.chmod(temporary, stat.S_IMODE(original_stat.st_mode))
            try:
                os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
            except OSError as exc:
                raise ExecutorIssue("OWNERSHIP_UNPROVEN", {"path": str(path), "error": type(exc).__name__}) from exc
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            saved, temporary_stat = _read_bytes(temporary)
            if saved != data or stat.S_IMODE(temporary_stat.st_mode) != stat.S_IMODE(original_stat.st_mode):
                raise ExecutorIssue("ATOMIC_TEMP_VALIDATION_FAILED", str(temporary))
            os.replace(temporary, path)
            descriptor = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _bundle_path(self, backup_root: Path) -> Path:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return backup_root / f"{EXECUTOR_ID}-{OPERATION_ID}-{stamp}-{secrets.token_hex(8)}"

    @staticmethod
    def _write_backup_file(path: Path, data: bytes) -> dict[str, Any]:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        saved, metadata = _read_bytes(path)
        if saved != data:
            raise ExecutorIssue("BACKUP_BYTE_MISMATCH", path.name)
        return {"name": path.name, "sha256": _digest(saved), "size": len(saved), "mode": stat.S_IMODE(metadata.st_mode)}

    def _create_backup(self, info: Mapping[str, Any]) -> Path:
        runtime = Path(info["runtime"])
        backup_root = self.roots.backup_root.resolve(strict=False)
        fixture_backup = self.roots.fixture_temporary_root is not None and _inside(backup_root, self.roots.fixture_temporary_root)
        if (str(backup_root).startswith("/tmp") and not fixture_backup) or _descendant(backup_root, runtime) or _same_path(backup_root, runtime):
            raise ExecutorIssue("BACKUP_ROOT_INSIDE_RUNTIME", str(backup_root))
        if backup_root.exists():
            root_stat = os.lstat(backup_root)
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                raise ExecutorIssue("BACKUP_ROOT_UNSAFE", str(backup_root))
        else:
            try:
                backup_root.mkdir(parents=True, mode=0o700)
            except OSError as exc:
                raise ExecutorIssue("BACKUP_CREATION_FAILED", {"path": str(backup_root), "error": type(exc).__name__}) from exc
        bundle = self._bundle_path(backup_root)
        try:
            bundle.mkdir(mode=0o700)
        except FileExistsError as exc:  # UUID collision is never overwritten.
            raise ExecutorIssue("BACKUP_ALREADY_EXISTS", str(bundle)) from exc
        except OSError as exc:
            raise ExecutorIssue("BACKUP_CREATION_FAILED", {"path": str(bundle), "error": type(exc).__name__}) from exc
        files = {
            "episode-budget.json.before": bytes(info["target_bytes"]),
            "operation.json": bytes(info["operation_bytes"]),
            "PROJECT_STATE.json": bytes(info["state_bytes"]),
            "HANDOFF_CHATGPT.md": bytes(info["handoff_bytes"]),
            "episode-budget.json.lock": bytes(info["lock_bytes"]),
        }
        manifest_files = [self._write_backup_file(bundle / name, data) for name, data in files.items()]
        toolchain = _stable_toolchain(self.roots.candidate_root)
        manifest = {
            "schema_version": "0.2.0",
            "action_id": ACTION_ID,
            "executor_id": EXECUTOR_ID,
            "operation_id": OPERATION_ID,
            "family_id": FAMILY_ID,
            "episode_id": EPISODE_ID,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "target_prewrite_sha256": info["target_sha256"],
            "files": manifest_files,
            "execution_toolchain_fingerprint": toolchain["execution_toolchain_fingerprint"],
            "rollback_policy": {"automatic_retry": False, "max_retries": 0, "automatic_rollback_on_postcheck_failure": True, "max_rollback_attempts": 1},
            "persistent": True,
            "outside_runtime_target": True,
        }
        manifest["manifest_sha256"] = _digest(_json_bytes(manifest))
        self._write_backup_file(bundle / "manifest.json", _json_bytes(manifest))
        descriptor = os.open(str(bundle), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return bundle

    def _verify_persistent_backup(self, info: Mapping[str, Any], bundle: Path) -> dict[str, Any]:
        runtime = Path(info["runtime"])
        fixture_backup = self.roots.fixture_temporary_root is not None and _inside(bundle, self.roots.fixture_temporary_root)
        if _descendant(bundle, runtime) or (str(bundle).startswith("/tmp") and not fixture_backup) or not _same_path(bundle.parent, self.roots.backup_root):
            raise ExecutorIssue("PERSISTENT_ROLLBACK_PROOF_FAILED", "backup location")
        manifest, _, _ = _read_json(bundle / "manifest.json")
        if manifest.get("target_prewrite_sha256") != info["target_sha256"] or manifest.get("operation_id") != OPERATION_ID:
            raise ExecutorIssue("PERSISTENT_ROLLBACK_PROOF_FAILED", "manifest identity")
        expected = {
            "episode-budget.json.before": bytes(info["target_bytes"]),
            "operation.json": bytes(info["operation_bytes"]),
            "PROJECT_STATE.json": bytes(info["state_bytes"]),
            "HANDOFF_CHATGPT.md": bytes(info["handoff_bytes"]),
            "episode-budget.json.lock": bytes(info["lock_bytes"]),
        }
        by_name = {item.get("name"): item for item in manifest.get("files", []) if isinstance(item, dict)}
        for name, data in expected.items():
            saved, _ = _read_bytes(bundle / name)
            if saved != data or by_name.get(name, {}).get("sha256") != _digest(data):
                raise ExecutorIssue("PERSISTENT_ROLLBACK_PROOF_FAILED", name)
        return {"status": "PASS", "bundle": str(bundle), "target_backup_sha256": _digest(expected["episode-budget.json.before"])}

    @staticmethod
    def _validate_postwrite(info: Mapping[str, Any], actual_bytes: bytes, expected: Mapping[str, Any]) -> None:
        try:
            actual = json.loads(actual_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutorIssue("POSTWRITE_JSON_INVALID", str(info["target"])) from exc
        if not isinstance(actual, dict):
            raise ExecutorIssue("POSTWRITE_SCHEMA_INVALID", "root")
        for key, value in info["budget"].items():
            if key not in EXPECTED_LEDGER_MISSING_FIELDS and actual.get(key) != value:
                raise ExecutorIssue("POSTWRITE_UNEXPECTED_CHANGE", key)
        for key in EXPECTED_LEDGER_MISSING_FIELDS:
            if key not in actual:
                raise ExecutorIssue("POSTWRITE_IDENTITY_INCOMPLETE", key)
        if actual.get("episode_id") != expected["episode_id"] or actual.get("episode_family_id") != expected["episode_family_id"]:
            raise ExecutorIssue("POSTWRITE_IDENTITY_MISMATCH", "episode")
        if actual.get("family_contract") != expected["family_contract"] or actual.get("family_contract_sha256") != expected["family_contract_sha256"]:
            raise ExecutorIssue("POSTWRITE_FAMILY_CONTRACT_MISMATCH", "family_contract")
        if actual.get("logical_calls") != expected["logical_calls"] or actual.get("updated_at") != expected["updated_at"]:
            raise ExecutorIssue("POSTWRITE_INITIALIZER_FIELDS_INVALID", "logical_calls/updated_at")
        if actual.get("reservations") != [] or actual.get("initial_consumed") != 0 or actual.get("retry_consumed") != 0:
            raise ExecutorIssue("POSTWRITE_OPERATIONAL_FACT_CHANGED", "counters/reservations")

    @staticmethod
    def _prepared_bytes(info: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Use one official initializer result while the exclusive lock is held."""
        expected = dict(info["official_initial"])
        prepared = dict(info["budget"])
        for field in EXPECTED_LEDGER_MISSING_FIELDS:
            prepared[field] = expected[field]
        unexpected = sorted(key for key in set(prepared) | set(info["budget"])
                            if prepared.get(key) != info["budget"].get(key) and key not in EXPECTED_LEDGER_MISSING_FIELDS)
        if unexpected:
            raise ExecutorIssue("UNEXPECTED_SCHEMA_DIFF", unexpected)
        payload = _json_bytes(prepared)
        RecoveryLedgerExecutor._validate_postwrite(info, payload, expected)
        return expected, payload

    def _prepublish_recheck(self, original: Mapping[str, Any]) -> dict[str, Any]:
        """Re-read all final prestate material after backup, still under lock."""
        current = self._collect()
        for key in ("state_bytes", "target_bytes", "operation_bytes", "lock_bytes", "handoff_bytes"):
            if current[key] != original[key]:
                raise ExecutorIssue("EXECUTOR_PRECONDITION_CHANGED", {"field": key})
        if current["state_kind"] != "ELIGIBLE":
            raise ExecutorIssue("EXECUTOR_PRECONDITION_CHANGED", {"state_kind": current["state_kind"]})
        return current

    def _lock_path(self) -> Path:
        runtime = self.roots.expected_runtime_root
        if runtime is None:
            raise ExecutorIssue("RUNTIME_ROOT_UNAVAILABLE", "fixed executor roots", "UNKNOWN")
        return runtime / "episode-budget.json.lock"

    def apply(self, *, force_post_validation_failure: bool = False) -> dict[str, Any]:
        """Apply the single fixed action. Production invocation is separately gated."""
        backup: Path | None = None
        proof: dict[str, Any] | None = None
        publish_attempts = 0
        rollback_attempts = 0
        try:
            with self._exclusive_lock(self._lock_path()):
                info = self._collect()
                if info["state_kind"] == "ALREADY_REPREPARED":
                    return {"action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "mode": "apply", "eligible": False,
                            "terminal_state": "ALREADY_REPREPARED", "publish_attempted": False,
                            "publish_succeeded": False, "side_effects_performed": False, "blockers": [{"code": "ALREADY_REPREPARED", "severity": "BLOCK"}], "unknowns": []}
                backup = self._create_backup(info)
                proof = self._verify_persistent_backup(info, backup)
                final = self._prepublish_recheck(info)
                expected, payload = self._prepared_bytes(final)
                publish_attempts = 1
                try:
                    self._atomic_write(Path(final["target"]), payload, final["target_stat"])
                    if force_post_validation_failure:
                        raise ExecutorIssue("POSTWRITE_VALIDATION_FAILED", "test fault")
                    actual_bytes, _ = _read_bytes(Path(final["target"]))
                    self._validate_postwrite(final, actual_bytes, expected)
                    return {"action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "mode": "apply", "eligible": True,
                            "prestate": "PASS", "backup": {"directory": str(backup)}, "persistent_rollback_proof": proof,
                            "publish_attempted": True, "publish_succeeded": True, "postvalidation": "PASS",
                            "rollback": {"attempts": 0, "performed": False}, "side_effects_performed": True,
                            "terminal_state": "EXECUTION_SUCCESS_PRE_AUDIT", "blockers": [], "unknowns": [],
                            "publish_attempts": publish_attempts, "rollback_attempts": rollback_attempts}
                except Exception as failure:
                    rollback_attempts = 1
                    try:
                        original, _ = _read_bytes(backup / "episode-budget.json.before")
                        if _digest(original) != final["target_sha256"]:
                            raise ExecutorIssue("ROLLBACK_SOURCE_UNTRUSTED", str(backup))
                        self._atomic_write(Path(final["target"]), original, final["target_stat"])
                        restored, _ = _read_bytes(Path(final["target"]))
                        if _digest(restored) != final["target_sha256"]:
                            raise ExecutorIssue("ROLLBACK_BYTES_MISMATCH", str(final["target"]))
                        return {"action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "mode": "apply", "eligible": True,
                                "prestate": "PASS", "backup": {"directory": str(backup)}, "persistent_rollback_proof": proof,
                                "publish_attempted": True, "publish_succeeded": False, "postvalidation": getattr(failure, "code", type(failure).__name__),
                                "rollback": {"attempts": 1, "performed": True, "restored_prewrite_sha256": final["target_sha256"]},
                                "side_effects_performed": True, "terminal_state": "EXECUTION_FAILED_ROLLED_BACK",
                                "blockers": [{"code": getattr(failure, "code", type(failure).__name__), "severity": "BLOCK"}], "unknowns": [],
                                "publish_attempts": publish_attempts, "rollback_attempts": rollback_attempts}
                    except Exception as rollback_failure:
                        return {"action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "mode": "apply", "eligible": True,
                                "backup": {"directory": str(backup)}, "persistent_rollback_proof": proof,
                                "publish_attempted": True, "publish_succeeded": False,
                                "rollback": {"attempts": 1, "performed": True}, "side_effects_performed": True,
                                "terminal_state": "CRITICAL_FAIL_STOP", "blockers": [{"code": "ROLLBACK_FAILED", "severity": "BLOCK", "evidence": getattr(rollback_failure, "code", type(rollback_failure).__name__)}], "unknowns": [],
                                "publish_attempts": publish_attempts, "rollback_attempts": rollback_attempts}
        except ExecutorIssue as issue:
            side_effects = backup is not None
            terminal = "EXECUTION_ABORTED_AFTER_BACKUP" if side_effects else "FAIL_STOP"
            return {"action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "mode": "apply", "eligible": False,
                    "prestate": issue.code, "backup": ({"directory": str(backup)} if backup else None),
                    "persistent_rollback_proof": proof, "publish_attempted": False, "publish_succeeded": False,
                    "postvalidation": None, "rollback": {"attempts": 0, "performed": False},
                    "side_effects_performed": side_effects, "terminal_state": terminal,
                    "blockers": ([] if issue.severity == "UNKNOWN" else [{"code": issue.code, "severity": issue.severity, "evidence": issue.evidence}]),
                    "unknowns": ([{"code": issue.code, "severity": issue.severity, "evidence": issue.evidence}] if issue.severity == "UNKNOWN" else []),
                    "publish_attempts": publish_attempts, "rollback_attempts": rollback_attempts}

    def apply_for_fixture(self, *, force_post_validation_failure: bool = False) -> dict[str, Any]:
        raise ExecutorIssue("TEST_FIXTURE_CONTEXT_REQUIRED", "use apply_fixture_context")


def apply_fixture_context(context: FixtureExecutionContext, *, force_post_validation_failure: bool = False) -> dict[str, Any]:
    """The only supported apply API for offline tests; never selects real roots."""
    issue = _fixture_context_issue(context)
    if issue is not None:
        return {
            "action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "mode": "fixture_apply",
            "eligible": False, "terminal_state": "FAIL_STOP", "publish_attempted": False,
            "publish_succeeded": False, "side_effects_performed": False,
            "blockers": ([] if issue.severity == "UNKNOWN" else [{"code": issue.code, "severity": issue.severity, "evidence": issue.evidence}]),
            "unknowns": ([{"code": issue.code, "severity": issue.severity, "evidence": issue.evidence}] if issue.severity == "UNKNOWN" else []),
        }
    return RecoveryLedgerExecutor(context.roots).apply(force_post_validation_failure=force_post_validation_failure)


def _error_result(code: str, evidence: Any = None) -> dict[str, Any]:
    return {"schema_version": PLAN_SCHEMA_VERSION, "action_id": ACTION_ID, "executor_id": EXECUTOR_ID, "error": {"code": code, "evidence": evidence}, "side_effects_performed": False}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args == ["--plan"]:
        result = RecoveryLedgerExecutor(ExecutorRoots.real()).plan()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if result.get("unknowns"):
            return 3
        return 0 if result.get("eligible") else 2
    if args == ["--apply"]:
        result = RecoveryLedgerExecutor(ExecutorRoots.real()).apply()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        if result.get("unknowns"):
            return 3
        return 0 if result.get("terminal_state") == "EXECUTION_SUCCESS_PRE_AUDIT" else 2
    print(json.dumps(_error_result("UNEXPECTED_ARGUMENT", {"arguments": args}), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
