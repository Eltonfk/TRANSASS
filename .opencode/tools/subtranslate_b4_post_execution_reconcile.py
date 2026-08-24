#!/usr/bin/env python3
"""AUTO-03D B4 post-execution canonical reconciliation.

This helper is deliberately action-specific.  It accepts no paths or content.
``--plan`` is strictly read-only.  ``--apply`` is a separately gated,
documentary-only transition with fixed prestates, persistent backup, atomic
publication, verification, and rollback of only the two authority documents.
It never invokes the B4 executor, a model, transport, retry, B5, B6, or B7.
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

CANDIDATE = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT = AUTHORITY / "PROJECT_STATE.json"
HANDOFF = AUTHORITY / "HANDOFF_CHATGPT.md"
PROBE = CANDIDATE / ".opencode/tools/subtranslate_readonly_probe.py"
EXECUTION_BACKUP = Path("/home/palhacinho/opencode-backups/subtranslate-auto03d-b4-recovery-call-execution-r2")
DOCUMENT_BACKUP = Path("/home/palhacinho/opencode-backups/subtranslate-auto-03d-b4-post-execution-canonical-reconciliation-r1")

PROJECT_SHA = "1e62825d6e67da250926e960e4551afbd628fab4d4b680f5607b1912d057985d"
HANDOFF_SHA = "e1808dbb1b66f9c102edbdb8cbc7b11cf027a4e82de55607422417c17b7d30f0"
LEDGER_SHA = "78fe815aa1ec8239053b6364d76303c3a5cc3f94a8ed33f13b7a30fa3ad4e903"
PRE_LEDGER_SHA = "32e641be94c59343f71259534049a250cf75ef89fee6bdf10beabf0842ad0d8e"
RESPONSE_SHA = "cd5968a6eb0ac66ff694f610a82da78ffef16005dc07c9d99d6f87bbc1a54e49"
ATTEMPT_ID = "v226-attempt-e04a2acf07ad148f1a50d03b0f5e8a7b"
OPERATION = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
FAMILY = "V238_E07_R6C_B4_RECOVERY"
PAYLOAD_SHA = "236f7f81243f025bd757b6f116da7d0607529fa63559199309ad78513b92c7a8"

PRE_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_EXECUTED_CANONICAL_RECONCILIATION_REQUIRED"
PRE_DECISION = "B4_RECOVERY_CALL_EXECUTED_POST_EXECUTION_AUDIT_REQUIRED"
PRE_NEXT = "B4_RECOVERY_CALL_POST_EXECUTION_AUDIT_REQUIRED"
POST_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_SUCCEEDED_POST_EXECUTION_AUDITED_B5_PREFLIGHT_READ_ONLY_REQUIRED"
POST_DECISION = "B4_RECOVERY_CALL_POST_EXECUTION_AUDIT_PASS_CANONICAL_RECONCILED_B5_PREFLIGHT_READ_ONLY_REQUIRED"
POST_NEXT = "B5_PREFLIGHT_READ_ONLY_REQUIRED"
AUTH_KEY = "auto03d_b4_recovery_call_execution_authorization_r2"
OBS_KEY = "auto03d_b4_recovery_call_execution_observed_r2"
NEW_KEY = "auto03d_b4_recovery_call_post_execution_canonical_reconciliation_r1"


class Blocked(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Blocked(f"UNSAFE_REGULAR_FILE:{path}")
    return info


def load_json(path: Path) -> dict[str, Any]:
    regular(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Blocked(f"JSON_ROOT_NOT_OBJECT:{path}")
    return value


def fresh_probe() -> dict[str, Any]:
    regular(PROBE)
    spec = importlib.util.spec_from_file_location("auto03d_post_b4_probe", PROBE)
    if spec is None or spec.loader is None:
        raise Blocked("PROBE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.probe()
    integrity = result.get("integrity", {})
    if (result.get("blockers") != [] or result.get("unknowns") != [] or
            integrity.get("snapshot_consistent") is not True or
            integrity.get("side_effects_performed") is not False):
        raise Blocked("FRESH_PROBE_NOT_CLEAN")
    return result


def verify_execution_backup() -> dict[str, Any]:
    info = EXECUTION_BACKUP.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Blocked("EXECUTION_BACKUP_ROOT_UNSAFE")
    manifest = load_json(EXECUTION_BACKUP / "manifest.json")
    if manifest.get("action_id") != "B4_RECOVERY_CALL_EXECUTION" or manifest.get("immutable_pre_transport") is not True:
        raise Blocked("EXECUTION_BACKUP_MANIFEST_CONTRACT_MISMATCH")
    expected = {
        "episode-budget.json": PRE_LEDGER_SHA,
        "episode-budget.json.lock": hashlib.sha256(b"").hexdigest(),
        "operation.json": "3d253f7aca034cbe89c9c7b9b980c60393e9b76d29a3fb8f08026d21bba5753c",
    }
    if manifest.get("files") != expected:
        raise Blocked("EXECUTION_BACKUP_MANIFEST_FILES_MISMATCH")
    for name, expected_sha in expected.items():
        path = EXECUTION_BACKUP / name
        regular(path)
        if sha(path.read_bytes()) != expected_sha:
            raise Blocked(f"EXECUTION_BACKUP_HASH_MISMATCH:{name}")
    return {"root": str(EXECUTION_BACKUP), "manifest_sha256": sha((EXECUTION_BACKUP / "manifest.json").read_bytes()), "files": expected}


def validate(project: dict[str, Any], project_bytes: bytes, handoff_bytes: bytes,
             probe: dict[str, Any]) -> dict[str, Any]:
    if sha(project_bytes) != PROJECT_SHA or sha(handoff_bytes) != HANDOFF_SHA:
        raise Blocked("AUTHORITY_DOCUMENT_PRESTATE_HASH_MISMATCH")
    if (project.get("state"), project.get("latest_decision"), project.get("next_action")) != (PRE_STATE, PRE_DECISION, PRE_NEXT):
        raise Blocked("CANONICAL_POINTER_PRESTATE_MISMATCH")
    if not isinstance(project.get(AUTH_KEY), dict) or not isinstance(project.get(OBS_KEY), dict) or NEW_KEY in project:
        raise Blocked("CANONICAL_LINEAGE_MISMATCH")
    runtime = probe.get("runtime", {})
    budget = runtime.get("episode_budget", {})
    attempts = runtime.get("calls_attempts", {})
    future = runtime.get("B5_B6_B7_evidence", {})
    if (budget.get("sha256") != LEDGER_SHA or budget.get("identity_state") != "COMPLETE" or
            budget.get("initial_consumed") != 1 or budget.get("retry_consumed") != 0 or
            budget.get("reservation_count") != 1 or budget.get("physical_ceiling") != 1):
        raise Blocked("B4_LEDGER_TERMINAL_FACTS_MISMATCH")
    reservations = budget.get("reservations")
    if not isinstance(reservations, list) or len(reservations) != 1:
        raise Blocked("B4_RESERVATION_CARDINALITY_MISMATCH")
    reservation = reservations[0]
    expected = {
        "family_id": FAMILY, "operation_id": OPERATION, "physical_attempt_id": ATTEMPT_ID,
        "state": "PARSED_VALID", "http_status": 200, "response_sha256": RESPONSE_SHA,
        "request_payload_sha256": PAYLOAD_SHA, "attempt_type": "INITIAL", "attempt_ordinal": 1,
    }
    for key, value in expected.items():
        if reservation.get(key) != value:
            raise Blocked(f"B4_RESERVATION_FIELD_MISMATCH:{key}")
    if (attempts.get("attempt_count") != 1 or attempts.get("calls_dir_exists") is not True or
            attempts.get("ids_names_observable") != [ATTEMPT_ID]):
        raise Blocked("B4_ATTEMPT_EVIDENCE_MISMATCH")
    if future != {"present": False, "observable": [], "b5_evidence_exists": False,
                  "b6_evidence_exists": False, "b7_evidence_exists": False}:
        raise Blocked("B5_B6_B7_NOT_ABSENT")
    execution_backup = verify_execution_backup()
    return {
        "snapshot_fingerprint": probe.get("snapshot_fingerprint"),
        "ledger_sha256": LEDGER_SHA, "attempt_id": ATTEMPT_ID,
        "response_sha256": RESPONSE_SHA, "http_posts": 1, "retry_count": 0,
        "execution_backup": execution_backup,
    }


def build_after(before: dict[str, Any], facts: dict[str, Any], recorded_at: str) -> dict[str, Any]:
    after = json.loads(json.dumps(before))
    if NEW_KEY in after:
        raise Blocked("RECONCILIATION_ALREADY_RECORDED")
    after[NEW_KEY] = {
        "mode": "DOCUMENTAL_APPLY",
        "recorded_at": recorded_at,
        "audit_status": "PASS",
        "audit_scope_result": "PASS_READ_ONLY_POST_EXECUTION_CLOSURE",
        "snapshot_fingerprint": facts["snapshot_fingerprint"],
        "family_id": FAMILY,
        "operation_id": OPERATION,
        "ledger_sha256": facts["ledger_sha256"],
        "attempt_id": facts["attempt_id"],
        "attempt_count": 1,
        "http_posts": 1,
        "http_status": 200,
        "response_sha256": facts["response_sha256"],
        "terminal_state": "PARSED_VALID",
        "initial_consumed": 1,
        "retry_consumed": 0,
        "automatic_retry_executed": False,
        "execution_backup": facts["execution_backup"],
        "b5_authorized": False,
        "b6_authorized": False,
        "b7_authorized": False,
        "future_side_effects_authorized": False,
    }
    after["state"] = POST_STATE
    after["latest_decision"] = POST_DECISION
    after["next_action"] = POST_NEXT
    return after


def prepare() -> tuple[dict[str, Any], bytes, bytes, os.stat_result, os.stat_result, dict[str, Any]]:
    project_info = regular(PROJECT)
    handoff_info = regular(HANDOFF)
    project_bytes = PROJECT.read_bytes()
    handoff_bytes = HANDOFF.read_bytes()
    project = json.loads(project_bytes)
    if not isinstance(project, dict):
        raise Blocked("PROJECT_STATE_ROOT_NOT_OBJECT")
    probe = fresh_probe()
    facts = validate(project, project_bytes, handoff_bytes, probe)
    return project, project_bytes, handoff_bytes, project_info, handoff_info, facts


def plan() -> dict[str, Any]:
    project, project_bytes, handoff_bytes, _, _, facts = prepare()
    preview = build_after(project, facts, "<APPLY_TIME_UTC>")
    return {
        "status": "READY", "mode": "PLAN_READ_ONLY", "side_effects_performed": False,
        "project_state_pre_sha256": sha(project_bytes), "handoff_pre_sha256": sha(handoff_bytes),
        "backup_root": str(DOCUMENT_BACKUP), "backup_root_prestate": "ABSENT" if not DOCUMENT_BACKUP.exists() else "PRESENT_BLOCKING",
        "new_object": NEW_KEY, "post_state": preview["state"], "post_latest_decision": preview["latest_decision"],
        "post_next_action": preview["next_action"], "facts": facts,
        "b4_reexecution": False, "model_call": False, "transport": False,
        "runtime_write": False, "b5_b6_b7_execution": False,
        "apply_authorized_by_this_plan": False,
        "next_gate": "AUTO-03D-B4-POST-EXECUTION-CANONICAL-RECONCILIATION-WRITE-R1",
    }


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish(path: Path, data: bytes, mode: int) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.auto03d-", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def apply() -> dict[str, Any]:
    project, project_bytes, handoff_bytes, project_info, handoff_info, facts = prepare()
    if DOCUMENT_BACKUP.exists() or DOCUMENT_BACKUP.is_symlink():
        raise Blocked("DOCUMENT_BACKUP_ALREADY_EXISTS")
    now = datetime.now(UTC).isoformat()
    after = build_after(project, facts, now)
    after_project = canonical(after)
    addendum = (
        f"\n\n---\n\n## Addendum {datetime.now(UTC).date().isoformat()} — "
        "AUTO-03D-B4-POST-EXECUTION-CANONICAL-RECONCILIATION-R1\n\n"
        "Auditoria pos-execucao B4 reconciliada canonicamente: uma tentativa, um POST HTTP 200, "
        "zero retry, resultado PARSED_VALID e backup persistente verificado. B4 nao sera reexecutado.\n\n"
        f"SNAPSHOT_FINGERPRINT={facts['snapshot_fingerprint']}\n"
        f"ATTEMPT_ID={ATTEMPT_ID}\nRESPONSE_SHA256={RESPONSE_SHA}\n"
        "B5_AUTHORIZED=false\nB6_AUTHORIZED=false\nB7_AUTHORIZED=false\n"
        "FUTURE_SIDE_EFFECTS_AUTHORIZED=false\n"
        f"NEXT_ACTION={POST_NEXT}\n"
    ).encode()
    after_handoff = handoff_bytes + addendum
    DOCUMENT_BACKUP.mkdir(mode=0o700)
    write_new(DOCUMENT_BACKUP / "PROJECT_STATE.json.before", project_bytes)
    write_new(DOCUMENT_BACKUP / "HANDOFF_CHATGPT.md.before", handoff_bytes)
    backup_manifest = canonical({
        "action_id": "B4_POST_EXECUTION_CANONICAL_RECONCILIATION",
        "files": {"PROJECT_STATE.json.before": sha(project_bytes), "HANDOFF_CHATGPT.md.before": sha(handoff_bytes)},
        "immutable_pre_publish": True,
    })
    write_new(DOCUMENT_BACKUP / "manifest.json", backup_manifest)
    fsync_dir(DOCUMENT_BACKUP)
    fsync_dir(DOCUMENT_BACKUP.parent)
    published: list[tuple[Path, bytes, int]] = []
    try:
        publish(PROJECT, after_project, stat.S_IMODE(project_info.st_mode))
        published.append((PROJECT, project_bytes, stat.S_IMODE(project_info.st_mode)))
        publish(HANDOFF, after_handoff, stat.S_IMODE(handoff_info.st_mode))
        published.append((HANDOFF, handoff_bytes, stat.S_IMODE(handoff_info.st_mode)))
        if json.loads(PROJECT.read_text(encoding="utf-8")) != after:
            raise Blocked("PROJECT_POST_PUBLISH_MISMATCH")
        if not HANDOFF.read_bytes().startswith(handoff_bytes):
            raise Blocked("HANDOFF_NOT_APPEND_ONLY")
    except Exception:
        for path, data, mode in reversed(published):
            publish(path, data, mode)
        raise
    return {
        "status": "PASS", "mode": "DOCUMENTAL_APPLY", "new_object": NEW_KEY,
        "project_state_sha256": sha(after_project), "handoff_sha256": sha(after_handoff),
        "backup_root": str(DOCUMENT_BACKUP), "rollback_result": "NOT_REQUIRED",
        "b4_reexecution": False, "model_call": False, "transport": False,
        "runtime_write": False, "b5_b6_b7_execution": False,
        "next_action": POST_NEXT,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    mode = "apply" if args.apply else "plan"
    try:
        result = apply() if args.apply else plan()
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "mode": mode,
                          "side_effects_performed": False if mode == "plan" else "FAIL_CLOSED",
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
