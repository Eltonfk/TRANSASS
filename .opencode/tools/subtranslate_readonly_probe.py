#!/usr/bin/env python3
"""AUTO-02R: deterministic, read-only Subtranslate state probe.

This module deliberately has no operational input surface.  Every filesystem
operation is read-only; every important file is read with a before/after
identity check.  Exit policy: 0 complete/no blocker, 2 proven blocker, 3
unknown or an untrustworthy snapshot (UNKNOWN has precedence), 4 invalid
invocation or internal error.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = "/home/palhacinho/codex-projects/subtranslate-v238-candidate"
AUTHORITY_ROOT = "/home/palhacinho/codex-projects/anime-subtitle-translator-review"
RUNTIME_PARENT = os.path.join(AUTHORITY_ROOT, "runtime-evidence")
SCHEMA_VERSION = "0.4.0"
PROBE_VERSION = "0.4.0"
MAX_CANONICAL_BYTES = 8 * 1024 * 1024
MAX_RUNTIME_BYTES = 2 * 1024 * 1024
GIT_TIMEOUT = 5
REQUIRED_LEDGER_IDENTITY_FIELDS = (
    "episode_id", "episode_family_id", "family_contract",
    "family_contract_sha256", "logical_calls", "updated_at",
)
EXECUTION_TOOLCHAIN_ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
EXECUTION_TOOLCHAIN_EXECUTOR_ID = "RECOVERY_LEDGER_REPREPARATION_V1"
EXECUTION_TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_recovery_ledger_reprepare.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-audit.md",
    ".opencode/commands/subtranslate-next.md",
)
B4_EXECUTION_TOOLCHAIN_ACTION_ID = "B4_RECOVERY_CALL_EXECUTION"
B4_EXECUTION_TOOLCHAIN_EXECUTOR_ID = "B4_RECOVERY_CALL_EXECUTOR_V1"
B4_EXECUTION_TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_b4_recovery_call.py",
    ".opencode/tools/subtranslate_canonical_transition.py",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/agents/subtranslate-audit.md",
    ".opencode/agents/subtranslate-doc-sync.md",
    ".opencode/commands/subtranslate-next.md",
    ".opencode/skills/subtranslate-canary/SKILL.md",
)
B4_MODEL_TAG = "qwen3.5:9b"
B4_MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
B5_EXECUTION_TOOLCHAIN_ACTION_ID = "B5_BATCH_EXECUTION"
B5_EXECUTION_TOOLCHAIN_EXECUTOR_ID = "B5_BATCH_EXECUTOR_V1"
B5_EXECUTION_TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_b5_executor.py",
    ".opencode/tools/subtranslate_b5_preflight.py",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/agents/subtranslate-audit.md",
    ".opencode/agents/subtranslate-doc-sync.md",
    ".opencode/commands/subtranslate-next.md",
    ".opencode/skills/subtranslate-canary/SKILL.md",
)
B5_MODEL_TAG = "qwen3.5:9b"
B5_MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
CONTEXT_HYGIENE_POLICY = {
    "policy_id": "AUTO-03D-OPENCODE-ACTIVE-CONTEXT-HYGIENE-POLICY-R1",
    "baseline_after_detach": {
        "authority_files": 3397,
        "authority_bytes": 59052891,
        "core_lines": 2850,
    },
    "hard_limits": {
        "authority_files": 3500,
        "authority_bytes": 70 * 1024 * 1024,
        "project_state_lines": 800,
        "handoff_lines": 700,
        "orchestrator_lines": 1400,
        "audit_lines": 350,
        "core_lines": 3200,
    },
    "compaction_targets": {
        "project_state_lines": 250,
        "handoff_lines": 200,
        "orchestrator_lines": 450,
        "audit_lines": 200,
        "core_lines": 1100,
    },
    "automatic_delete": False,
    "future_detach_requires_separate_gate": True,
}


def _issue(target: list[dict[str, Any]], code: str, severity: str, evidence: Any) -> None:
    target.append({"code": code, "severity": severity, "evidence": evidence})


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_consistent(path: str, limit: int, unknowns: list, blockers: list) -> tuple[bytes | None, dict[str, Any]]:
    """Read exact bytes, rejecting symlinks, size violations and TOCTOU."""
    before = descriptor_before = descriptor_after = after = None
    descriptor = None
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode):
            _issue(unknowns, "UNEXPECTED_SYMLINK", "UNKNOWN", path)
            return None, {"path": path, "readable": False}
        if not stat.S_ISREG(before.st_mode):
            _issue(blockers, "IMPORTANT_FILE_NOT_REGULAR", "BLOCK", path)
            return None, {"path": path, "readable": False}
        if before.st_size > limit:
            _issue(unknowns, "IMPORTANT_FILE_TOO_LARGE", "UNKNOWN", {"path": path, "size": before.st_size})
            return None, {"path": path, "readable": False, "size": before.st_size}
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        descriptor_before = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_mode")
        if any(getattr(before, field) != getattr(descriptor_before, field) for field in identity):
            _issue(unknowns, "FILE_CHANGED_DURING_READ", "UNKNOWN", {"path": path, "stage": "open"})
            return None, {"path": path, "readable": False}
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            _issue(unknowns, "IMPORTANT_FILE_TOO_LARGE", "UNKNOWN", {"path": path, "size": len(data)})
            return None, {"path": path, "readable": False, "size": len(data)}
        descriptor_after = os.fstat(descriptor)
        after = os.lstat(path)
        if any(
            getattr(before, field) != getattr(current, field)
            for current in (descriptor_after, after)
            for field in identity
        ):
            _issue(unknowns, "FILE_CHANGED_DURING_READ", "UNKNOWN", {"path": path, "before": before.st_mtime_ns, "after": after.st_mtime_ns})
            return None, {"path": path, "readable": False}
        return data, {"path": path, "readable": True, "size": len(data), "sha256": _digest(data)}
    except FileNotFoundError:
        _issue(unknowns, "FILE_DISAPPEARED", "UNKNOWN", path)
    except PermissionError:
        _issue(unknowns, "FILE_PERMISSION_DENIED", "UNKNOWN", path)
    except OSError as exc:
        _issue(unknowns, "FILE_READ_ERROR", "UNKNOWN", {"path": path, "error": type(exc).__name__})
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return None, {"path": path, "readable": False}


def read_json(path: str, limit: int, unknowns: list, blockers: list) -> tuple[Any, dict[str, Any]]:
    data, meta = read_consistent(path, limit, unknowns, blockers)
    if data is None:
        meta.setdefault("json_valid", False)
        return None, meta
    try:
        value = json.loads(data)
        if not isinstance(value, (dict, list)):
            raise ValueError("JSON root must be object or array")
        meta["json_valid"] = True
        return value, meta
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        _issue(blockers, "INVALID_JSON", "BLOCK", path)
        meta["json_valid"] = False
        return None, meta


def _walk_key(value: Any, wanted: str, path: str = "$") -> list[tuple[str, Any]]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == wanted:
                found.append((child_path, child))
            found.extend(_walk_key(child, wanted, child_path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(_walk_key(child, wanted, f"{path}[{i}]"))
    return found


def _descendant(path: str) -> bool:
    try:
        root = Path(RUNTIME_PARENT).resolve(strict=True)
        candidate = Path(path).resolve(strict=False)
        candidate.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _git(argv: list[str], unknowns: list) -> str | None:
    try:
        result = subprocess.run(argv, shell=False, cwd=CANDIDATE_ROOT, timeout=GIT_TIMEOUT,
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            _issue(unknowns, "GIT_FAILURE", "UNKNOWN", {"argv": argv, "returncode": result.returncode})
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        _issue(unknowns, "GIT_TIMEOUT", "UNKNOWN", {"argv": argv})
    except OSError as exc:
        _issue(unknowns, "GIT_FAILURE", "UNKNOWN", {"argv": argv, "error": type(exc).__name__})
    return None


def inspect_git(unknowns: list, blockers: list) -> dict[str, Any]:
    branch = _git(["git", "branch", "--show-current"], unknowns)
    head = _git(["git", "rev-parse", "HEAD"], unknowns)
    tree = _git(["git", "rev-parse", "HEAD^{tree}"], unknowns)
    status = _git(["git", "status", "--porcelain"], unknowns)
    entries = [] if status is None else [line for line in status.splitlines() if line]
    tracked = [line for line in entries if not line.startswith("??")]
    untracked = [line[3:] for line in entries if line.startswith("?? ")]
    known = []
    unknown = []
    for item in untracked:
        if item == ".opencode/" or item.startswith(".opencode/") or item in {
            "AGENTS.md", "opencode.json"
        } or item.startswith("opencode.json.before-") or (item.startswith("tests/offline/") and "subtranslate" in item):
            known.append(item)
        else:
            unknown.append(item)
    if tracked:
        _issue(blockers, "CANDIDATE_TRACKED_DIRTY", "BLOCK", tracked)
    if unknown:
        _issue(blockers, "CANDIDATE_UNTRACKED_UNKNOWN", "BLOCK", unknown)
    return {"root": CANDIDATE_ROOT, "branch": branch.strip() if branch else None,
            "head": head.strip() if head else None, "tree": tree.strip() if tree else None,
            "tracked_dirty": bool(tracked), "tracked_changes": tracked,
            "untracked_known_tooling": known, "untracked_unknown": unknown}


def _inspect_toolchain(action_id: str, executor_id: str, paths: tuple[str, ...],
                       unknowns: list, blockers: list) -> dict[str, Any]:
    """Hash a fixed action toolchain; never hash probe output or runtime data."""
    components = []
    for relative in paths:
        absolute = os.path.join(CANDIDATE_ROOT, relative)
        data, meta = read_consistent(absolute, MAX_CANONICAL_BYTES, unknowns, blockers)
        entry = {"path": relative, "sha256": meta.get("sha256") if data is not None else None}
        if data is None:
            _issue(unknowns, "EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE", "UNKNOWN", relative)
        components.append(entry)
    manifest = [{"path": entry["path"], "sha256": entry["sha256"]} for entry in components]
    fingerprint = _digest(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) if all(entry["sha256"] for entry in manifest) else None
    return {"action_id": action_id,
            "executor_id": executor_id,
            "components": components,
            "execution_toolchain_fingerprint": fingerprint,
            "materialized": fingerprint is not None}


def inspect_execution_toolchains(unknowns: list, blockers: list) -> dict[str, Any]:
    ledger = _inspect_toolchain(EXECUTION_TOOLCHAIN_ACTION_ID, EXECUTION_TOOLCHAIN_EXECUTOR_ID,
                                EXECUTION_TOOLCHAIN_COMPONENTS, unknowns, blockers)
    b4 = _inspect_toolchain(B4_EXECUTION_TOOLCHAIN_ACTION_ID, B4_EXECUTION_TOOLCHAIN_EXECUTOR_ID,
                            B4_EXECUTION_TOOLCHAIN_COMPONENTS, unknowns, blockers)
    b4.update({
        "model_binding": {"model_tag": B4_MODEL_TAG, "model_digest": B4_MODEL_DIGEST},
        "transport_guard": {
            "kind": "DURABLE_EXCLUSIVE_TRANSPORT_CLAIM",
            "max_client_calls": 1,
            "max_http_posts": 1,
            "max_retries": 0,
            "executor_component": ".opencode/tools/subtranslate_b4_recovery_call.py",
            "durability_component": "src/subtranslate/v238_per_call_durability.py",
        },
    })
    b5 = _inspect_toolchain(B5_EXECUTION_TOOLCHAIN_ACTION_ID, B5_EXECUTION_TOOLCHAIN_EXECUTOR_ID,
                            B5_EXECUTION_TOOLCHAIN_COMPONENTS, unknowns, blockers)
    b5.update({
        "model_binding": {"model_tag": B5_MODEL_TAG, "model_digest": B5_MODEL_DIGEST},
        "transport_guard": {
            "kind": "DURABLE_EXCLUSIVE_TRANSPORT_CLAIM",
            "max_client_calls": 1,
            "max_http_posts": 1,
            "max_retries": 0,
            "executor_component": ".opencode/tools/subtranslate_b5_executor.py",
            "durability_component": "src/subtranslate/v238_per_call_durability.py",
        },
    })
    return {EXECUTION_TOOLCHAIN_ACTION_ID: ledger, B4_EXECUTION_TOOLCHAIN_ACTION_ID: b4,
            B5_EXECUTION_TOOLCHAIN_ACTION_ID: b5}


def inspect_context_hygiene(state_bytes: bytes | None, handoff_bytes: bytes | None,
                            unknowns: list, blockers: list) -> dict[str, Any]:
    """Measure active-context growth without writing or following symlinks."""
    core_sources: dict[str, bytes | None] = {
        "project_state_lines": state_bytes,
        "handoff_lines": handoff_bytes,
    }
    for key, relative in (
        ("orchestrator_lines", ".opencode/agents/subtranslate-orchestrator.md"),
        ("audit_lines", ".opencode/agents/subtranslate-audit.md"),
    ):
        data, _ = read_consistent(os.path.join(CANDIDATE_ROOT, relative), MAX_CANONICAL_BYTES,
                                  unknowns, blockers)
        core_sources[key] = data

    core = {key: (len(data.splitlines()) if data is not None else None)
            for key, data in core_sources.items()}
    core["core_lines"] = (sum(value for value in core.values() if value is not None)
                          if all(value is not None for value in core.values()) else None)

    authority_files = 0
    authority_bytes = 0
    walk_errors: list[dict[str, str]] = []

    def walk_error(exc: OSError) -> None:
        walk_errors.append({"type": type(exc).__name__, "path": exc.filename or AUTHORITY_ROOT})

    for root, dirs, files in os.walk(AUTHORITY_ROOT, followlinks=False, onerror=walk_error):
        dirs[:] = [name for name in dirs if not Path(root, name).is_symlink()]
        for name in files:
            path = Path(root, name)
            try:
                info = path.lstat()
            except OSError as exc:
                walk_errors.append({"type": type(exc).__name__, "path": str(path)})
                continue
            if stat.S_ISREG(info.st_mode):
                authority_files += 1
                authority_bytes += info.st_size
    observed = {"authority_files": authority_files, "authority_bytes": authority_bytes, **core}
    exceeded = {
        key: {"observed": observed.get(key), "limit": limit}
        for key, limit in CONTEXT_HYGIENE_POLICY["hard_limits"].items()
        if observed.get(key) is not None and observed[key] > limit
    }
    if exceeded:
        _issue(blockers, "ACTIVE_CONTEXT_HYGIENE_LIMIT_EXCEEDED", "BLOCK", exceeded)
    baseline = CONTEXT_HYGIENE_POLICY["baseline_after_detach"]
    growth = {
        key: observed[key] - value
        for key, value in baseline.items()
        if observed.get(key) is not None and observed[key] > value
    }
    review_reasons = []
    for key in ("project_state_lines", "handoff_lines", "orchestrator_lines", "audit_lines", "core_lines"):
        observed_value = observed.get(key)
        hard_limit = CONTEXT_HYGIENE_POLICY["hard_limits"].get(key)
        if observed_value is not None and hard_limit and observed_value >= int(hard_limit * 0.80):
            review_reasons.append({"metric": key, "observed": observed_value, "hard_limit": hard_limit})
    status = "BLOCK" if exceeded else ("REVIEW" if review_reasons else "PASS")
    return {
        "policy": CONTEXT_HYGIENE_POLICY,
        "observed": observed,
        "growth_since_baseline": growth,
        "hard_limit_exceeded": exceeded,
        "review_reasons": review_reasons,
        "recommended_next_gate": (
            "AUTO-03D-OPENCODE-ACTIVE-CONTEXT-HYGIENE-PREFLIGHT-R1"
            if status == "REVIEW" else None
        ),
        "protected_subtrees_skipped": walk_errors,
        "status": status,
        "side_effects_performed": False,
    }


def _marker(text: str, patterns: tuple[str, ...]) -> bool:
    return all(pattern in text for pattern in patterns)


def probe() -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    state_path = os.path.join(AUTHORITY_ROOT, "PROJECT_STATE.json")
    handoff_path = os.path.join(AUTHORITY_ROOT, "HANDOFF_CHATGPT.md")
    state_bytes, state_read_meta = read_consistent(state_path, MAX_CANONICAL_BYTES, unknowns, blockers)
    if state_bytes is None:
        state, state_meta = None, {**state_read_meta, "json_valid": False}
    else:
        try:
            state = json.loads(state_bytes)
            if not isinstance(state, (dict, list)):
                raise ValueError("JSON root must be object or array")
            state_meta = {**state_read_meta, "json_valid": True}
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            _issue(blockers, "INVALID_JSON", "BLOCK", state_path)
            state, state_meta = None, {**state_read_meta, "json_valid": False}
    handoff_issues: list[dict[str, Any]] = []
    handoff_bytes, handoff_meta = read_consistent(handoff_path, MAX_CANONICAL_BYTES, handoff_issues, [])
    handoff_text = None
    if handoff_bytes is not None:
        try:
            handoff_text = handoff_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _issue(blockers, "HANDOFF_NOT_UTF8", "BLOCK", handoff_path)

    occurrences = _walk_key(state, "r6c_batch4_recovery_e1_reconciliation") if state is not None else []
    top = isinstance(state, dict) and "r6c_batch4_recovery_e1_reconciliation" in state
    nested = [p for p, _ in occurrences if p != "$.r6c_batch4_recovery_e1_reconciliation"]
    e1_value = occurrences[0][1] if occurrences else None
    runtime_root = None
    if isinstance(e1_value, dict):
        runtime_root = e1_value.get("runtime_root")
    if not isinstance(runtime_root, str) and isinstance(state, dict):
        proto = state.get("r6c_batch4_recovery_protocol")
        if isinstance(proto, dict):
            nested_e1 = proto.get("r6c_batch4_recovery_e1_reconciliation")
            if isinstance(nested_e1, dict):
                runtime_root = nested_e1.get("runtime_root")
    if runtime_root is not None and (not isinstance(runtime_root, str) or not _descendant(runtime_root)):
        _issue(blockers, "RUNTIME_ROOT_OUTSIDE_AUTHORITY", "BLOCK", runtime_root)
        runtime_root = None
    if not occurrences:
        _issue(unknowns, "E1_RECONCILIATION_ABSENT", "UNKNOWN", "r6c_batch4_recovery_e1_reconciliation")
    if handoff_text is None:
        handoff_info = {**handoff_meta, "readable": False, "e1_addendum_present": None, "c2_facts_present": None, "phase_d_blocker_present": None,
                        "indeterminate": [x["code"] for x in handoff_issues]}
    else:
        handoff_info = {**handoff_meta, "readable": True,
            "e1_addendum_present": "PHASE E1 RECONCILIATION PREFLIGHT" in handoff_text,
            "c2_facts_present": ("C2 criou fisicamente" in handoff_text and "operation_id" in handoff_text),
            "phase_d_blocker_present": ("BLOCKED_BEFORE_RESERVATION_AND_TRANSPORT" in handoff_text or
                                         "bloqueou antes de reservation e transporte" in handoff_text),
            "indeterminate": [x["code"] for x in handoff_issues]}

    canonical = {"project_state": {**state_meta,
        "current_operation": state.get("current_operation") if isinstance(state, dict) else None,
        "state": state.get("state") if isinstance(state, dict) else None,
        "status": state.get("status") if isinstance(state, dict) else None,
        "latest_decision": state.get("latest_decision") if isinstance(state, dict) else None,
        "next_action": state.get("next_action") if isinstance(state, dict) else None},
        "r6c_batch4_recovery_e1_reconciliation": {
            "present_top_level": top, "nested_inside_recovery_protocol": bool(nested),
            "occurrence_count": len(occurrences), "type": type(e1_value).__name__ if occurrences else None},
        "handoff": handoff_info}

    runtime: dict[str, Any] = {"root": runtime_root}
    observed = {"initial_consumed": None, "retry_consumed": None, "reservation_count": None, "attempt_count": None}
    if runtime_root:
        op, op_meta = read_json(os.path.join(runtime_root, "operation.json"), MAX_RUNTIME_BYTES, unknowns, blockers)
        budget, budget_meta = read_json(os.path.join(runtime_root, "episode-budget.json"), MAX_RUNTIME_BYTES, unknowns, blockers)
        lock_path = os.path.join(runtime_root, "episode-budget.json.lock")
        # A ledger lock file is transient by design: it only exists while a
        # writer holds the budget.  Absence is the normal quiescent state and
        # must not degrade snapshot trust; real anomalies (symlink, TOCTOU,
        # permission) still emit unknowns through read_consistent below.
        lock_info: dict[str, Any] = {"exists": False}
        try:
            os.lstat(lock_path)
            lock_present = True
        except FileNotFoundError:
            lock_present = False
        except OSError as exc:
            _issue(unknowns, "FILE_READ_ERROR", "UNKNOWN", {"path": lock_path, "error": type(exc).__name__})
            lock_present = None
        if lock_present is True:
            _, lock_meta = read_consistent(lock_path, MAX_RUNTIME_BYTES, unknowns, blockers)
            lock_info = {"exists": True, **{k: lock_meta[k] for k in ("size", "sha256") if k in lock_meta}}
        calls_path = Path(runtime_root) / "calls"
        attempt_names = []
        try:
            calls_stat = os.lstat(calls_path)
            if stat.S_ISLNK(calls_stat.st_mode):
                _issue(unknowns, "UNEXPECTED_SYMLINK", "UNKNOWN", str(calls_path))
            elif stat.S_ISDIR(calls_stat.st_mode):
                attempt_names = sorted(x.name for x in calls_path.iterdir() if x.name)
            else:
                _issue(blockers, "CALLS_PATH_NOT_DIRECTORY", "BLOCK", str(calls_path))
        except FileNotFoundError:
            pass
        except OSError as exc:
            _issue(unknowns, "CALLS_DIRECTORY_UNREADABLE", "UNKNOWN", {"path": calls_path, "error": type(exc).__name__})
        missing_identity = [field for field in REQUIRED_LEDGER_IDENTITY_FIELDS
                            if not isinstance(budget, dict) or field not in budget]
        if isinstance(budget, dict) and missing_identity:
            _issue(blockers, "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE", "BLOCK", {
                "path": os.path.join(runtime_root, "episode-budget.json"),
                "missing_fields": missing_identity,
            })
        elif isinstance(budget, dict) and isinstance(state, dict) and state.get("next_action") == "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION":
            _issue(blockers, "POST_REPREPARE_CANONICAL_RECONCILIATION_REQUIRED", "BLOCK", {
                "path": os.path.join(runtime_root, "episode-budget.json"),
                "reason": "runtime ledger identity is complete while canonical action still describes the pre-reprepare blocker",
            })
        runtime.update({"operation": {**op_meta, "exists": os.path.exists(os.path.join(runtime_root, "operation.json")), "operation_id": op.get("operation_id") if isinstance(op, dict) else None},
                        "episode_budget": {**budget_meta, "exists": os.path.exists(os.path.join(runtime_root, "episode-budget.json")),
                            **({k: budget.get(k) for k in ("planned_initial_calls", "retry_reserve", "physical_ceiling", "initial_consumed", "retry_consumed", "reservations")} if isinstance(budget, dict) else {}),
                            "reservation_count": len(budget.get("reservations", [])) if isinstance(budget, dict) and isinstance(budget.get("reservations"), list) else None,
                            "fields_present": {k: isinstance(budget, dict) and k in budget for k in REQUIRED_LEDGER_IDENTITY_FIELDS},
                            "missing_identity_fields": missing_identity,
                            "identity_state": ("INCOMPLETE" if missing_identity else "COMPLETE") if isinstance(budget, dict) else "UNKNOWN"},
                        "lock": lock_info,
                        "calls_attempts": {"calls_dir_exists": os.path.isdir(calls_path), "attempt_count": len(attempt_names), "ids_names_observable": attempt_names},
                        "B5_B6_B7_evidence": {"present": False, "observable": [],
                            "b5_evidence_exists": False, "b6_evidence_exists": False, "b7_evidence_exists": False}})
        if isinstance(budget, dict):
            observed.update({k: budget.get(k) for k in ("initial_consumed", "retry_consumed")})
            observed["reservation_count"] = len(budget.get("reservations", [])) if isinstance(budget.get("reservations"), list) else None
        observed["attempt_count"] = len(attempt_names)
        for name in ("B5", "B6", "B7"):
            if Path(runtime_root).parent.joinpath(name).exists():
                runtime["B5_B6_B7_evidence"]["present"] = True
                runtime["B5_B6_B7_evidence"]["observable"].append(name)
                runtime["B5_B6_B7_evidence"][f"{name.lower()}_evidence_exists"] = True
    else:
        _issue(unknowns, "RUNTIME_ROOT_UNAVAILABLE", "UNKNOWN", "canonical state")

    candidate_git = inspect_git(unknowns, blockers)
    context_hygiene = inspect_context_hygiene(state_bytes, handoff_bytes, unknowns, blockers)
    execution_toolchains = inspect_execution_toolchains(unknowns, blockers)
    execution_toolchain = execution_toolchains[EXECUTION_TOOLCHAIN_ACTION_ID]
    current_next_action = state.get("next_action") if isinstance(state, dict) else None
    current_execution_toolchain = (
        execution_toolchains[B4_EXECUTION_TOOLCHAIN_ACTION_ID]
        if isinstance(current_next_action, str) and current_next_action.startswith("B4_RECOVERY_CALL_")
        else execution_toolchains[B5_EXECUTION_TOOLCHAIN_ACTION_ID]
        if isinstance(current_next_action, str) and current_next_action.startswith("B5_")
        else execution_toolchain
    )
    accounting = {"canonical_accounting": {}, "runtime_observed_accounting": observed, "comparisons": {}}
    if isinstance(state, dict):
        e1 = e1_value if isinstance(e1_value, dict) else {}
        ca = e1.get("accounting", {}) if isinstance(e1, dict) else {}
        canonical_current = ca.get("canonical_before", {}) if isinstance(ca, dict) else {}
        accounting["canonical_accounting"] = canonical_current
        mapping = {"initial_consumed": "recovery_family_consumed", "retry_consumed": "r6c_retries"}
        for observed_key, canonical_key in mapping.items():
            value = observed[observed_key]
            expected = canonical_current.get(canonical_key)
            accounting["comparisons"][observed_key] = "UNKNOWN" if value is None or expected is None else ("MATCH" if value == expected else "MISMATCH")
    if any(x["code"] == "MISMATCH" for x in []):
        pass
    result = {"schema_version": SCHEMA_VERSION, "probe_version": PROBE_VERSION, "canonical": canonical,
              "candidate_git": candidate_git, "runtime": runtime, "accounting": accounting,
              "context_hygiene": context_hygiene,
              "execution_toolchain": execution_toolchain,
              "execution_toolchains": execution_toolchains,
              "current_execution_toolchain": current_execution_toolchain,
              "blockers": blockers, "unknowns": unknowns,
              "integrity": {"snapshot_consistent": not unknowns, "side_effects_performed": False}}
    fingerprint_basis = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    result["snapshot_fingerprint"] = _digest(fingerprint_basis)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "probe_version": PROBE_VERSION,
                          "error": {"code": "UNEXPECTED_ARGUMENT", "arguments_rejected": True},
                          "integrity": {"snapshot_consistent": False, "side_effects_performed": False}}, sort_keys=True, separators=(",", ":")))
        return 4
    try:
        result = probe()
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return 3 if result["unknowns"] else (2 if result["blockers"] else 0)
    except Exception as exc:  # fail closed: internal errors are invocation errors
        print(json.dumps({"schema_version": SCHEMA_VERSION, "probe_version": PROBE_VERSION,
                          "error": {"code": "INTERNAL_ERROR", "type": type(exc).__name__},
                          "integrity": {"snapshot_consistent": False, "side_effects_performed": False}}, sort_keys=True, separators=(",", ":")))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
