#!/usr/bin/env python3
"""Fixed, atomic canonical transitions for the AUTO-03D B4 workflow.

No paths or document contents are accepted from the caller.  Each transition
derives facts from one fresh read-only probe and accepts exactly one canonical
prestate.  Both authority documents are backed up and published atomically;
partial publication is rolled back from the just-created backups.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT = AUTHORITY_ROOT / "PROJECT_STATE.json"
HANDOFF = AUTHORITY_ROOT / "HANDOFF_CHATGPT.md"
PROBE_PATH = CANDIDATE_ROOT / ".opencode/tools/subtranslate_readonly_probe.py"
EXECUTOR_PATH = CANDIDATE_ROOT / ".opencode/tools/subtranslate_b4_recovery_call.py"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")

PREFLIGHT_STATE = "SUBTRANSLATE_V238_E07_R6C_BATCH4_LEDGER_REPREPARED_R4_CLOSED_B4_RECOVERY_CALL_PREFLIGHT_READ_ONLY_REQUIRED"
PREFLIGHT_NEXT = "B4_RECOVERY_CALL_PREFLIGHT_READ_ONLY_REQUIRED"
AUTH_REQUIRED_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_PREFLIGHT_READY_EXECUTION_AUTHORIZATION_REQUIRED"
AUTH_REQUIRED_NEXT = "B4_RECOVERY_CALL_EXECUTION_AUTHORIZATION_REQUIRED"
AUTHORIZED_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_EXECUTION_AUTHORIZED_SINGLE_CALL_ZERO_RETRY"
AUTHORIZED_NEXT = "B4_RECOVERY_CALL_EXECUTION_AUTHORIZED"
POST_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_EXECUTED_CANONICAL_RECONCILIATION_REQUIRED"
POST_NEXT = "B4_RECOVERY_CALL_POST_EXECUTION_AUDIT_REQUIRED"
FAMILY = "V238_E07_R6C_B4_RECOVERY"
OPERATION = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
PAYLOAD_SHA = "236f7f81243f025bd757b6f116da7d0607529fa63559199309ad78513b92c7a8"
EXECUTOR_ID = "B4_RECOVERY_CALL_EXECUTOR_V1"
PREFLIGHT_KEY = "auto03d_b4_recovery_call_preflight_r2"
AUTHORIZATION_KEY = "auto03d_b4_recovery_call_execution_authorization_r2"
OBSERVATION_KEY = "auto03d_b4_recovery_call_execution_observed_r2"
FAILURE_KEY = "auto03d_b4_recovery_call_pretransport_failure_r2"
FAILURE_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_PRETRANSPORT_FAIL_STOP_TOOLCHAIN_CORRECTION_REQUIRED"
FAILURE_NEXT = "B4_RECOVERY_CALL_TOOLCHAIN_CONTRACT_CORRECTION_REQUIRED"


class TransitionBlocked(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TransitionBlocked(f"UNSAFE_FILE:{path}")
    return info


def load_project() -> dict[str, Any]:
    regular(PROJECT)
    value = json.loads(PROJECT.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TransitionBlocked("PROJECT_STATE_ROOT_INVALID")
    return value


def fresh_probe() -> dict[str, Any]:
    regular(PROBE_PATH)
    spec = importlib.util.spec_from_file_location("auto03d_fresh_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise TransitionBlocked("PROBE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result = module.probe()
    if result.get("blockers") or result.get("unknowns") or not result.get("integrity", {}).get("snapshot_consistent"):
        raise TransitionBlocked("FRESH_PROBE_NOT_CLEAN")
    return result


def b4_toolchain(probe: dict[str, Any]) -> dict[str, Any]:
    toolchain = probe.get("execution_toolchains", {}).get("B4_RECOVERY_CALL_EXECUTION")
    if not isinstance(toolchain, dict) or toolchain.get("executor_id") != EXECUTOR_ID:
        raise TransitionBlocked("B4_TOOLCHAIN_UNAVAILABLE")
    fingerprint = toolchain.get("execution_toolchain_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or not toolchain.get("materialized"):
        raise TransitionBlocked("B4_TOOLCHAIN_NOT_MATERIALIZED")
    return toolchain


def call_plan() -> dict[str, Any]:
    regular(EXECUTOR_PATH)
    spec = importlib.util.spec_from_file_location("auto03d_b4_executor_plan", EXECUTOR_PATH)
    if spec is None or spec.loader is None:
        raise TransitionBlocked("B4_EXECUTOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result = module.plan(require_authorization=False)
    if result.get("status") != "READY" or result.get("side_effects_performed") is not False:
        raise TransitionBlocked("B4_EXECUTOR_PLAN_NOT_READY")
    return result


def transition(mode: str, before: dict[str, Any], probe: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    toolchain = b4_toolchain(probe)
    fingerprint = str(probe["snapshot_fingerprint"])
    now = datetime.now(UTC).isoformat()
    after = json.loads(json.dumps(before))
    if mode == "record-preflight":
        if before.get("state") != PREFLIGHT_STATE or before.get("next_action") != PREFLIGHT_NEXT:
            raise TransitionBlocked("PREFLIGHT_CANONICAL_PRESTATE_MISMATCH")
        plan = call_plan()
        key = PREFLIGHT_KEY
        if key in before:
            raise TransitionBlocked("PREFLIGHT_RECORD_ALREADY_EXISTS")
        after[key] = {
            "mode": "PREFLIGHT_READ_ONLY", "status": "READY", "recorded_at": now,
            "snapshot_fingerprint": fingerprint, "execution_toolchain": toolchain,
            "executor_plan": plan, "execution_authorized": False,
            "pipeline_model_call": False, "external_transport": False,
            "runtime_write": False, "side_effects_performed": False,
        }
        after["state"] = AUTH_REQUIRED_STATE
        after["latest_decision"] = "B4_RECOVERY_CALL_PREFLIGHT_READY_EXECUTION_NOT_AUTHORIZED"
        after["next_action"] = AUTH_REQUIRED_NEXT
        title = "AUTO-03D-B4-RECOVERY-CALL-PREFLIGHT-R2"
        summary = "Preflight read-only READY; toolchain B4 materializada e vinculada; execucao nao autorizada."
    elif mode == "record-authorization":
        if before.get("state") != AUTH_REQUIRED_STATE or before.get("next_action") != AUTH_REQUIRED_NEXT:
            raise TransitionBlocked("B4_AUTHORIZATION_PRESTATE_MISMATCH")
        key = AUTHORIZATION_KEY
        if key in before:
            raise TransitionBlocked("B4_AUTHORIZATION_ALREADY_EXISTS")
        after[key] = {
            "mode": "AUTHORIZED_PRECHECK", "authorized_at": now,
            "action_id": "B4_RECOVERY_CALL_EXECUTION", "executor_id": EXECUTOR_ID,
            "snapshot_fingerprint": fingerprint,
            "execution_toolchain_fingerprint": toolchain["execution_toolchain_fingerprint"],
            "apply_permission_active": True, "pipeline_model_call_authorized": True,
            "external_transport_authorized": True, "runtime_write_authorized": True,
            "persistent_backup_write_authorized": True, "production_write_authorized": False,
            "data_delete_authorized": False, "automatic_retry_authorized": False,
            "max_retries": 0, "max_client_calls": 1, "max_http_posts": 1,
            "family_id": FAMILY, "operation_id": OPERATION,
            "expected_request_payload_sha256": PAYLOAD_SHA,
            "future_batches_authorized": False, "canonical_reconciliation_required": True,
        }
        after["state"] = AUTHORIZED_STATE
        after["latest_decision"] = "B4_RECOVERY_CALL_SINGLE_EXECUTION_AUTHORIZED_ZERO_RETRY"
        after["next_action"] = AUTHORIZED_NEXT
        title = "AUTO-03D-B4-RECOVERY-CALL-EXECUTION-AUTHORIZATION-R2"
        summary = "Autorizacao vinculada a uma chamada B4, um POST e zero retry; B5-B7 nao autorizados."
    elif mode == "record-post-execution":
        if before.get("state") != AUTHORIZED_STATE or before.get("next_action") != AUTHORIZED_NEXT:
            raise TransitionBlocked("B4_POST_EXECUTION_PRESTATE_MISMATCH")
        runtime = probe.get("runtime", {})
        budget = runtime.get("episode_budget", {})
        attempts = runtime.get("calls_attempts", {})
        if budget.get("initial_consumed") != 1 or budget.get("retry_consumed") != 0 or attempts.get("attempt_count") != 1:
            raise TransitionBlocked("B4_POST_EXECUTION_FACTS_MISMATCH")
        key = OBSERVATION_KEY
        if key in before:
            raise TransitionBlocked("B4_POST_EXECUTION_RECORD_ALREADY_EXISTS")
        after[key] = {"mode": "POST_EXECUTION_OBSERVATION", "recorded_at": now,
                      "snapshot_fingerprint": fingerprint, "initial_consumed": 1,
                      "retry_consumed": 0, "attempt_count": 1,
                      "canonical_reconciliation_required": True,
                      "b5_authorized": False, "b6_authorized": False, "b7_authorized": False}
        after["state"] = POST_STATE
        after["latest_decision"] = "B4_RECOVERY_CALL_EXECUTED_POST_EXECUTION_AUDIT_REQUIRED"
        after["next_action"] = POST_NEXT
        title = "AUTO-03D-B4-RECOVERY-CALL-POST-EXECUTION-OBSERVATION-R2"
        summary = "Uma tentativa B4 observada; zero retry; auditoria e reconciliacao canonica obrigatorias."
    elif mode == "record-failure":
        if before.get("state") != AUTHORIZED_STATE or before.get("next_action") != AUTHORIZED_NEXT:
            raise TransitionBlocked("B4_FAILURE_PRESTATE_MISMATCH")
        runtime = probe.get("runtime", {})
        budget = runtime.get("episode_budget", {})
        attempts = runtime.get("calls_attempts", {})
        if budget.get("initial_consumed") != 0 or budget.get("retry_consumed") != 0 or budget.get("reservations") != []:
            raise TransitionBlocked("B4_FAILURE_RUNTIME_NOT_ZERO")
        if attempts.get("attempt_count") != 0 or attempts.get("calls_dir_exists") is not False:
            raise TransitionBlocked("B4_FAILURE_NOT_PRETRANSPORT")
        key = FAILURE_KEY
        if key in before:
            raise TransitionBlocked("B4_FAILURE_ALREADY_EXISTS")
        after[key] = {"mode": "PRETRANSPORT_FAIL_STOP_OBSERVATION", "recorded_at": now,
                      "snapshot_fingerprint": fingerprint, "authorization_consumed": True,
                      "authorization_valid_for_reexecution": False, "initial_consumed": 0,
                      "retry_consumed": 0, "attempt_count": 0, "reservations": [],
                      "model_call_executed": False, "external_transport_executed": False,
                      "toolchain_correction_required": True, "future_side_effects_authorized": False}
        after["state"] = FAILURE_STATE
        after["latest_decision"] = "B4_RECOVERY_CALL_AUTHORIZATION_CONSUMED_PRETRANSPORT_FAIL_STOP"
        after["next_action"] = FAILURE_NEXT
        title = "AUTO-03D-B4-RECOVERY-CALL-PRETRANSPORT-FAILURE-R2"
        summary = "FAIL_STOP pre-transporte registrado; autorizacao consumida; toolchain correction obrigatoria."
    else:
        raise TransitionBlocked("UNKNOWN_TRANSITION")
    return after, title, summary


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def publish(path: Path, data: bytes, mode: int) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.auto03d-", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path); fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def apply(mode: str) -> dict[str, Any]:
    project_info = regular(PROJECT); handoff_info = regular(HANDOFF)
    before_project_bytes = PROJECT.read_bytes(); before_handoff = HANDOFF.read_bytes()
    before = json.loads(before_project_bytes)
    probe = fresh_probe()
    after, title, summary = transition(mode, before, probe)
    after_project = canonical(after)
    addendum = (f"\n\n---\n\n## Addendum {datetime.now(UTC).date().isoformat()} — {title}\n\n"
                f"{summary}\n\nSNAPSHOT_FINGERPRINT={probe['snapshot_fingerprint']}\n"
                "FUTURE_SIDE_EFFECTS_AUTHORIZED=false\n"
                f"NEXT_ACTION={after['next_action']}\n").encode()
    after_handoff = before_handoff + addendum
    backup = BACKUP_PARENT / f"subtranslate-{title.lower()}"
    if backup.exists() or backup.is_symlink():
        raise TransitionBlocked("TRANSITION_BACKUP_ALREADY_EXISTS")
    backup.mkdir(mode=0o700)
    write_new(backup / "PROJECT_STATE.json.before", before_project_bytes)
    write_new(backup / "HANDOFF_CHATGPT.md.before", before_handoff)
    fsync_dir(backup); fsync_dir(BACKUP_PARENT)
    published: list[tuple[Path, bytes, int]] = []
    try:
        publish(PROJECT, after_project, stat.S_IMODE(project_info.st_mode)); published.append((PROJECT, before_project_bytes, stat.S_IMODE(project_info.st_mode)))
        publish(HANDOFF, after_handoff, stat.S_IMODE(handoff_info.st_mode)); published.append((HANDOFF, before_handoff, stat.S_IMODE(handoff_info.st_mode)))
        if json.loads(PROJECT.read_text()) != after or not HANDOFF.read_bytes().startswith(before_handoff):
            raise TransitionBlocked("POST_PUBLISH_VERIFICATION_FAILED")
    except Exception:
        for path, data, mode_bits in reversed(published): publish(path, data, mode_bits)
        raise
    return {"status": "PASS", "transition": mode, "project_state_sha256": digest(after_project),
            "handoff_sha256": digest(after_handoff), "backup_root": str(backup),
            "snapshot_fingerprint": probe["snapshot_fingerprint"], "next_action": after["next_action"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True,
                        choices=("record-preflight", "record-authorization", "record-post-execution", "record-failure"))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(apply(args.mode), sort_keys=True, ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "transition": args.mode,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
