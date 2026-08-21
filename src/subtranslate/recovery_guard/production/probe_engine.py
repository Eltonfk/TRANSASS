"""Canonical, read-only probe engine used by future protected probes.

The historical probe remains frozen.  This module keeps its serialization,
TOCTOU reads, blocker classification, snapshot fingerprint and toolchain
fingerprint in one parameterized implementation.  Profiles are constructed by
trusted callers/tests; no profile is accepted from a CLI, socket or model.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.3.0"
PROBE_VERSION = "0.3.0"
MAX_CANONICAL_BYTES = 8 * 1024 * 1024
MAX_RUNTIME_BYTES = 2 * 1024 * 1024
GIT_TIMEOUT = 5
REQUIRED_LEDGER_IDENTITY_FIELDS = (
    "episode_id", "episode_family_id", "family_contract",
    "family_contract_sha256", "logical_calls", "updated_at",
)
EXECUTION_TOOLCHAIN_ACTION_ID = "RECOVERY_LEDGER_REPREPARATION"
LEGACY_TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_recovery_ledger_reprepare.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-audit.md",
    ".opencode/commands/subtranslate-next.md",
)


@dataclass(frozen=True)
class ProbeProfile:
    candidate_root: str
    authority_root: str
    runtime_parent: str
    git_root: str | None
    execution_toolchain_components: tuple[str, ...]
    execution_toolchain_executor_id: str
    source_commit: str | None = None
    source_tree: str | None = None


def legacy_profile() -> ProbeProfile:
    """Return the historical profile, for equivalence tests only."""
    candidate = "/home/palhacinho/codex-projects/subtranslate-v238-candidate"
    authority = "/home/palhacinho/codex-projects/anime-subtitle-translator-review"
    return ProbeProfile(candidate, authority, os.path.join(authority, "runtime-evidence"),
                        candidate, LEGACY_TOOLCHAIN_COMPONENTS,
                        "RECOVERY_LEDGER_REPREPARATION_V1")


def production_profile(*, release_root: Path, manifest: dict[str, Any]) -> ProbeProfile:
    """Build a protected profile from already-validated manifest metadata."""
    source = manifest.get("source_git")
    tree = manifest.get("source_tree")
    if not isinstance(source, str) or not isinstance(tree, str):
        raise ValueError("PROBE_SOURCE_AUTHORITY_INVALID")
    authority = manifest.get("authority_root", "/home/palhacinho/codex-projects/anime-subtitle-translator-review")
    runtime_parent = manifest.get("runtime_parent", os.path.join(authority, "runtime-evidence"))
    if not isinstance(authority, str) or not isinstance(runtime_parent, str):
        raise ValueError("PROBE_RUNTIME_POLICY_INVALID")
    components = tuple(manifest.get("probe_toolchain_components", ()))
    executor_id = manifest.get("probe_executor_id", "RECOVERY_LEDGER_REPREPARATION_V2")
    if not components or not all(isinstance(item, str) and not Path(item).is_absolute() for item in components):
        raise ValueError("PROBE_TOOLCHAIN_POLICY_INVALID")
    root = Path(release_root).resolve(strict=True)
    return ProbeProfile(str(root), authority, runtime_parent, None, components, executor_id, source, tree)


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
        if any(getattr(before, field) != getattr(current, field)
               for current in (descriptor_after, after) for field in identity):
            _issue(unknowns, "FILE_CHANGED_DURING_READ", "UNKNOWN",
                   {"path": path, "before": before.st_mtime_ns, "after": after.st_mtime_ns})
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


def _descendant(path: str, runtime_parent: str) -> bool:
    try:
        root = Path(runtime_parent).resolve(strict=True)
        candidate = Path(path).resolve(strict=False)
        candidate.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _git(profile: ProbeProfile, argv: list[str], unknowns: list) -> str | None:
    if profile.git_root is None:
        return None
    try:
        result = subprocess.run(argv, shell=False, cwd=profile.git_root, timeout=GIT_TIMEOUT,
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


def inspect_git(profile: ProbeProfile, unknowns: list, blockers: list) -> dict[str, Any]:
    if profile.git_root is None:
        return {"root": profile.candidate_root, "branch": None, "head": profile.source_commit,
                "tree": profile.source_tree, "tracked_dirty": False, "tracked_changes": [],
                "untracked_known_tooling": [], "untracked_unknown": []}
    branch = _git(profile, ["git", "branch", "--show-current"], unknowns)
    head = _git(profile, ["git", "rev-parse", "HEAD"], unknowns)
    tree = _git(profile, ["git", "rev-parse", "HEAD^{tree}"], unknowns)
    status = _git(profile, ["git", "status", "--porcelain"], unknowns)
    entries = [] if status is None else [line for line in status.splitlines() if line]
    tracked = [line for line in entries if not line.startswith("??")]
    untracked = [line[3:] for line in entries if line.startswith("?? ")]
    known = []
    unknown = []
    for item in untracked:
        if item == ".opencode/" or item.startswith(".opencode/") or item in {"AGENTS.md", "opencode.json"} or item.startswith("opencode.json.before-") or (item.startswith("tests/offline/") and "subtranslate" in item):
            known.append(item)
        else:
            unknown.append(item)
    if tracked:
        _issue(blockers, "CANDIDATE_TRACKED_DIRTY", "BLOCK", tracked)
    if unknown:
        _issue(blockers, "CANDIDATE_UNTRACKED_UNKNOWN", "BLOCK", unknown)
    return {"root": profile.candidate_root, "branch": branch.strip() if branch else None,
            "head": head.strip() if head else None, "tree": tree.strip() if tree else None,
            "tracked_dirty": bool(tracked), "tracked_changes": tracked,
            "untracked_known_tooling": known, "untracked_unknown": unknown}


def inspect_execution_toolchain(profile: ProbeProfile, unknowns: list, blockers: list) -> dict[str, Any]:
    components = []
    for relative in profile.execution_toolchain_components:
        absolute = os.path.join(profile.candidate_root, relative)
        data, meta = read_consistent(absolute, MAX_CANONICAL_BYTES, unknowns, blockers)
        entry = {"path": relative, "sha256": meta.get("sha256") if data is not None else None}
        if data is None:
            _issue(unknowns, "EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE", "UNKNOWN", relative)
        components.append(entry)
    manifest = [{"path": entry["path"], "sha256": entry["sha256"]} for entry in components]
    fingerprint = _digest(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) if all(entry["sha256"] for entry in manifest) else None
    return {"action_id": EXECUTION_TOOLCHAIN_ACTION_ID,
            "executor_id": profile.execution_toolchain_executor_id,
            "components": components,
            "execution_toolchain_fingerprint": fingerprint}


def run_probe(profile: ProbeProfile) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    state_path = os.path.join(profile.authority_root, "PROJECT_STATE.json")
    handoff_path = os.path.join(profile.authority_root, "HANDOFF_CHATGPT.md")
    state, state_meta = read_json(state_path, MAX_CANONICAL_BYTES, unknowns, blockers)
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
    if runtime_root is not None and (not isinstance(runtime_root, str) or not _descendant(runtime_root, profile.runtime_parent)):
        _issue(blockers, "RUNTIME_ROOT_OUTSIDE_AUTHORITY", "BLOCK", runtime_root)
        runtime_root = None
    if not occurrences:
        _issue(unknowns, "E1_RECONCILIATION_ABSENT", "UNKNOWN", "r6c_batch4_recovery_e1_reconciliation")
    if handoff_text is None:
        handoff_info = {**handoff_meta, "readable": False, "e1_addendum_present": None, "c2_facts_present": None,
                        "phase_d_blocker_present": None, "indeterminate": [x["code"] for x in handoff_issues]}
    else:
        handoff_info = {**handoff_meta, "readable": True,
            "e1_addendum_present": "PHASE E1 RECONCILIATION PREFLIGHT" in handoff_text,
            "c2_facts_present": ("C2 criou fisicamente" in handoff_text and "operation_id" in handoff_text),
            "phase_d_blocker_present": ("BLOCKED_BEFORE_RESERVATION_AND_TRANSPORT" in handoff_text or "bloqueou antes de reservation e transporte" in handoff_text),
            "indeterminate": [x["code"] for x in handoff_issues]}

    canonical = {"project_state": {**state_meta,
        "current_operation": state.get("current_operation") if isinstance(state, dict) else None,
        "state": state.get("state") if isinstance(state, dict) else None,
        "status": state.get("status") if isinstance(state, dict) else None,
        "latest_decision": state.get("latest_decision") if isinstance(state, dict) else None,
        "next_action": state.get("next_action") if isinstance(state, dict) else None},
        "r6c_batch4_recovery_e1_reconciliation": {"present_top_level": top, "nested_inside_recovery_protocol": bool(nested),
            "occurrence_count": len(occurrences), "type": type(e1_value).__name__ if occurrences else None},
        "handoff": handoff_info}
    runtime: dict[str, Any] = {"root": runtime_root}
    observed = {"initial_consumed": None, "retry_consumed": None, "reservation_count": None, "attempt_count": None}
    if runtime_root:
        op, op_meta = read_json(os.path.join(runtime_root, "operation.json"), MAX_RUNTIME_BYTES, unknowns, blockers)
        budget, budget_meta = read_json(os.path.join(runtime_root, "episode-budget.json"), MAX_RUNTIME_BYTES, unknowns, blockers)
        _, lock_meta = read_consistent(os.path.join(runtime_root, "episode-budget.json.lock"), MAX_RUNTIME_BYTES, unknowns, blockers)
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
        missing_identity = [field for field in REQUIRED_LEDGER_IDENTITY_FIELDS if not isinstance(budget, dict) or field not in budget]
        if isinstance(budget, dict) and missing_identity:
            _issue(blockers, "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE", "BLOCK", {"path": os.path.join(runtime_root, "episode-budget.json"), "missing_fields": missing_identity})
        elif isinstance(budget, dict) and isinstance(state, dict) and state.get("next_action") == "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION":
            _issue(blockers, "POST_REPREPARE_CANONICAL_RECONCILIATION_REQUIRED", "BLOCK", {"path": os.path.join(runtime_root, "episode-budget.json"), "reason": "runtime ledger identity is complete while canonical action still describes the pre-reprepare blocker"})
        runtime.update({"operation": {**op_meta, "exists": os.path.exists(os.path.join(runtime_root, "operation.json")), "operation_id": op.get("operation_id") if isinstance(op, dict) else None},
                        "episode_budget": {**budget_meta, "exists": os.path.exists(os.path.join(runtime_root, "episode-budget.json")),
                            **({k: budget.get(k) for k in ("planned_initial_calls", "retry_reserve", "physical_ceiling", "initial_consumed", "retry_consumed", "reservations")} if isinstance(budget, dict) else {}),
                            "reservation_count": len(budget.get("reservations", [])) if isinstance(budget, dict) and isinstance(budget.get("reservations"), list) else None,
                            "fields_present": {k: isinstance(budget, dict) and k in budget for k in REQUIRED_LEDGER_IDENTITY_FIELDS},
                            "missing_identity_fields": missing_identity,
                            "identity_state": ("INCOMPLETE" if missing_identity else "COMPLETE") if isinstance(budget, dict) else "UNKNOWN"},
                        "lock": {"exists": lock_meta.get("readable", False), **{k: lock_meta[k] for k in ("size", "sha256") if k in lock_meta}},
                        "calls_attempts": {"calls_dir_exists": os.path.isdir(calls_path), "attempt_count": len(attempt_names), "ids_names_observable": attempt_names},
                        "B5_B6_B7_evidence": {"present": False, "observable": [], "b5_evidence_exists": False, "b6_evidence_exists": False, "b7_evidence_exists": False}})
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

    candidate_git = inspect_git(profile, unknowns, blockers)
    execution_toolchain = inspect_execution_toolchain(profile, unknowns, blockers)
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
    result = {"schema_version": SCHEMA_VERSION, "probe_version": PROBE_VERSION, "canonical": canonical,
              "candidate_git": candidate_git, "runtime": runtime, "accounting": accounting,
              "execution_toolchain": execution_toolchain, "blockers": blockers, "unknowns": unknowns,
              "integrity": {"snapshot_consistent": not unknowns, "side_effects_performed": False}}
    result["snapshot_fingerprint"] = _digest(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
    return result


def canonical_json(result: dict[str, Any]) -> bytes:
    return json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


__all__ = ["ProbeProfile", "legacy_profile", "production_profile", "run_probe", "canonical_json"]
