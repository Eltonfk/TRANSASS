#!/usr/bin/env python3
"""Action-specific, fail-closed executor for the single B4 recovery call.

Planning is read-only.  Apply is impossible until a canonical, snapshot-bound
authorization object exists.  The executor has no user-controlled paths,
payload, model, endpoint, family, operation, retry or batch surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT_STATE = AUTHORITY_ROOT / "PROJECT_STATE.json"
RUNTIME_ROOT = AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6C_B4_RECOVERY"
LEDGER = RUNTIME_ROOT / "episode-budget.json"
OPERATION = RUNTIME_ROOT / "operation.json"
HISTORICAL_PAYLOAD = AUTHORITY_ROOT / (
    "runtime-evidence/V238_E07_R6C_BATCHES_1_7_ATTEMPT3/calls/"
    "v226-attempt-15dae08868a745003c32b88f5287ba62/request_payload.json"
)
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")
BACKUP_ROOT = BACKUP_PARENT / "subtranslate-auto03d-b4-recovery-call-execution-r2"

ACTION_ID = "B4_RECOVERY_CALL_EXECUTION"
EXECUTOR_ID = "B4_RECOVERY_CALL_EXECUTOR_V1"
FAMILY_ID = "V238_E07_R6C_B4_RECOVERY"
OPERATION_ID = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
EPISODE_ID = "79"
UNIT_IDS = [42, 43, 44, 45, 46, 47, 48, 49]
MEMBERSHIP_SHA256 = "025b18adf784186c3ee4d0d41faafa85442265615620ffff4f93454b329e107c"
PAYLOAD_SHA256 = "236f7f81243f025bd757b6f116da7d0607529fa63559199309ad78513b92c7a8"
LOGICAL_BATCH_ID = "v226-recovery-000004"
EXPECTED_LOGICAL_CALL_ID = "v226-logical-a121a78d941f027dd09f370c7beeeb71"
EXPECTED_PHYSICAL_ATTEMPT_ID = "v226-attempt-e04a2acf07ad148f1a50d03b0f5e8a7b"
MODEL = "qwen3.5:9b"
MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
SOURCE_SHA256 = "0283291ca1ad212c27a3519a56a0a4dd89c706fa6d055a2b987bd9470a826bc0"
POLICY = "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V3_OPAQUE_CONTEXT_METADATA"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TIMEOUT_SECONDS = 240
AUTHORIZATION_KEY = "auto03d_b4_recovery_call_execution_authorization_r2"
AUTHORIZED_NEXT_ACTION = "B4_RECOVERY_CALL_EXECUTION_AUTHORIZED"
B4_TOOLCHAIN_COMPONENTS = (
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


class ExecutionBlocked(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_regular(path: Path, *, mode: int | None = None) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExecutionBlocked(f"UNSAFE_FILE:{path}")
    if mode is not None and stat.S_IMODE(info.st_mode) != mode:
        raise ExecutionBlocked(f"FILE_MODE_MISMATCH:{path}")
    return info


def load_json(path: Path) -> dict[str, Any]:
    require_regular(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionBlocked(f"JSON_ROOT_INVALID:{path}")
    return value


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=CANDIDATE_ROOT, shell=False,
                            capture_output=True, text=True, check=False, timeout=10)
    if result.returncode != 0:
        raise ExecutionBlocked("GIT_AUTHORITY_UNAVAILABLE")
    return result.stdout.strip()


def current_toolchain_fingerprint() -> str:
    manifest = []
    for relative in B4_TOOLCHAIN_COMPONENTS:
        path = CANDIDATE_ROOT / relative
        require_regular(path)
        manifest.append({"path": relative, "sha256": digest(path.read_bytes())})
    return digest(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def validate_payload() -> dict[str, Any]:
    require_regular(HISTORICAL_PAYLOAD)
    raw = HISTORICAL_PAYLOAD.read_bytes()
    if digest(raw) != PAYLOAD_SHA256:
        raise ExecutionBlocked("B4_PAYLOAD_HASH_MISMATCH")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ExecutionBlocked("B4_PAYLOAD_INVALID")
    if payload.get("model") != MODEL or payload.get("stream") is not False or payload.get("think") is not False:
        raise ExecutionBlocked("B4_PAYLOAD_MODEL_OR_STREAM_MISMATCH")
    options = payload.get("options")
    if options != {"num_ctx": 4096, "num_predict": 1024, "temperature": 0.0}:
        raise ExecutionBlocked("B4_PAYLOAD_OPTIONS_MISMATCH")
    schema = payload.get("format")
    try:
        translations = schema["properties"]["translations"]
        item = translations["items"]
    except (KeyError, TypeError):
        raise ExecutionBlocked("B4_PAYLOAD_SCHEMA_MISSING")
    if translations.get("minItems") != 8 or translations.get("maxItems") != 8:
        raise ExecutionBlocked("B4_PAYLOAD_CARDINALITY_MISMATCH")
    if item.get("additionalProperties") is not False or item.get("required") != ["id", "text"]:
        raise ExecutionBlocked("B4_PAYLOAD_ITEM_SCHEMA_MISMATCH")
    return payload


def validate_runtime() -> tuple[dict[str, Any], dict[str, Any]]:
    operation = load_json(OPERATION)
    ledger = load_json(LEDGER)
    if operation.get("operation_id") != OPERATION_ID:
        raise ExecutionBlocked("B4_OPERATION_ID_MISMATCH")
    expected = {
        "episode_id": EPISODE_ID,
        "episode_family_id": FAMILY_ID,
        "planned_initial_calls": 1,
        "retry_reserve": 0,
        "physical_ceiling": 1,
        "initial_consumed": 0,
        "retry_consumed": 0,
        "reservations": [],
    }
    for key, value in expected.items():
        if ledger.get(key) != value:
            raise ExecutionBlocked(f"B4_LEDGER_PRECONDITION_MISMATCH:{key}")
    family_contract = ledger.get("family_contract")
    required_contract = {
        "anime_series_id": "3",
        "episode_id": EPISODE_ID,
        "episode_family_id": FAMILY_ID,
        "candidate_execution_contract": "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84",
        "model_tag": MODEL,
        "model_digest": MODEL_DIGEST,
    }
    if not isinstance(family_contract, dict):
        raise ExecutionBlocked("B4_FAMILY_CONTRACT_MISSING")
    for key, value in required_contract.items():
        if family_contract.get(key) != value:
            raise ExecutionBlocked(f"B4_FAMILY_CONTRACT_MISMATCH:{key}")
    if ledger.get("family_contract_sha256") != family_contract.get("family_contract_sha256"):
        raise ExecutionBlocked("B4_FAMILY_CONTRACT_SHA_MISMATCH")
    calls = RUNTIME_ROOT / "calls"
    if calls.exists() or calls.is_symlink():
        raise ExecutionBlocked("B4_CALLS_ALREADY_PRESENT")
    return operation, ledger


def build_context() -> dict[str, Any]:
    return {
        "operation_id": OPERATION_ID,
        "anime_series_id": "3",
        "episode_id": EPISODE_ID,
        "episode_family_id": FAMILY_ID,
        "source_sha256": SOURCE_SHA256,
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "durable_call_root": str(RUNTIME_ROOT),
        "episode_family_root": str(RUNTIME_ROOT),
        "episode_budget_ledger_path": str(LEDGER),
        "episode_budget_limits": {"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1,
                                  "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1},
        "pipeline_id": "v2_3_8",
        "stage_id": "FULL_TRANSLATION_V238",
        "candidate_execution_contract": "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84",
        "configuration_hash": "0248eaff2384681e6bbf24e6e43eb4ca6cac123579fb68b7de42f3d5f5cba444",
        "glossary_hash": "64b0f676fed3bc495903f290b69a3290eebe2d52f8e726886a1ae7ea813b360e",
        "prompt_schema_hash": "05911c99936b46be9cd4d8878407a8e8986351e086f3414bf297d880b4b46f63",
        "response_normalization_policy": POLICY,
        "candidate_commit": git_value("rev-parse", "HEAD"),
        "transport_claim_timeout_seconds": 0.0,
    }


def validate_projected_identity(context: dict[str, Any], payload: dict[str, Any], ledger: dict[str, Any]) -> dict[str, str]:
    sys.path.insert(0, str(CANDIDATE_ROOT / "src/subtranslate"))
    from v238_per_call_durability import _family_contract
    try:
        family_contract = _family_contract(context)
    except Exception as exc:
        raise ExecutionBlocked(f"B4_PROJECTED_FAMILY_CONTRACT_INVALID:{exc}") from exc
    if family_contract != ledger.get("family_contract") or family_contract.get("family_contract_sha256") != ledger.get("family_contract_sha256"):
        raise ExecutionBlocked("B4_PROJECTED_FAMILY_CONTRACT_MISMATCH")
    logical_identity = {
        "family_contract_sha256": family_contract["family_contract_sha256"],
        "logical_batch_id": LOGICAL_BATCH_ID,
        "unit_membership_sha256": MEMBERSHIP_SHA256,
        "model_tag": MODEL,
        "model_digest": MODEL_DIGEST,
    }
    logical = "v226-logical-" + digest(canonical_bytes(logical_identity))[:32]
    physical_identity = {"logical_call_id": logical, "attempt_type": "INITIAL", "attempt_ordinal": 1,
                         "parent_attempt_id": None, "request_payload_sha256": digest(canonical_bytes(payload))}
    physical = "v226-attempt-" + digest(canonical_bytes(physical_identity))[:32]
    if logical != EXPECTED_LOGICAL_CALL_ID or physical != EXPECTED_PHYSICAL_ATTEMPT_ID:
        raise ExecutionBlocked("B4_PROJECTED_IDENTITY_MISMATCH")
    return {"logical_call_id": logical, "physical_attempt_id": physical,
            "family_contract_sha256": family_contract["family_contract_sha256"]}


def authorization() -> dict[str, Any]:
    state = load_json(PROJECT_STATE)
    record = state.get(AUTHORIZATION_KEY)
    if not isinstance(record, dict):
        raise ExecutionBlocked("B4_EXECUTION_AUTHORIZATION_ABSENT")
    required = {
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "apply_permission_active": True,
        "pipeline_model_call_authorized": True,
        "external_transport_authorized": True,
        "runtime_write_authorized": True,
        "production_write_authorized": False,
        "automatic_retry_authorized": False,
        "max_retries": 0,
        "max_client_calls": 1,
        "max_http_posts": 1,
        "family_id": FAMILY_ID,
        "operation_id": OPERATION_ID,
        "expected_request_payload_sha256": PAYLOAD_SHA256,
        "future_batches_authorized": False,
    }
    for key, value in required.items():
        if record.get(key) != value:
            raise ExecutionBlocked(f"B4_AUTHORIZATION_CONTRACT_MISMATCH:{key}")
    if state.get("next_action") != AUTHORIZED_NEXT_ACTION:
        raise ExecutionBlocked("B4_AUTHORIZATION_POINTER_MISMATCH")
    fingerprint = record.get("execution_toolchain_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ExecutionBlocked("B4_AUTHORIZATION_TOOLCHAIN_UNBOUND")
    if fingerprint != current_toolchain_fingerprint():
        raise ExecutionBlocked("B4_AUTHORIZATION_TOOLCHAIN_CHANGED")
    snapshot = record.get("snapshot_fingerprint")
    if not isinstance(snapshot, str) or len(snapshot) != 64:
        raise ExecutionBlocked("B4_AUTHORIZATION_SNAPSHOT_UNBOUND")
    return record


def plan(*, require_authorization: bool) -> dict[str, Any]:
    payload = validate_payload()
    operation, ledger = validate_runtime()
    projected = validate_projected_identity(build_context(), payload, ledger)
    auth = authorization() if require_authorization else None
    return {
        "status": "READY" if (not require_authorization or auth) else "BLOCKED",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "family_id": FAMILY_ID,
        "operation_id": OPERATION_ID,
        "episode_id": EPISODE_ID,
        "unit_ids": UNIT_IDS,
        "unit_membership_sha256": MEMBERSHIP_SHA256,
        "request_payload_sha256": digest(canonical_bytes(payload)),
        "projected_identity": projected,
        "ledger_sha256": digest(LEDGER.read_bytes()),
        "operation_sha256": digest(OPERATION.read_bytes()),
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "max_client_calls": 1,
        "max_http_posts": 1,
        "max_retries": 0,
        "authorization_present": auth is not None,
        "side_effects_performed": False,
    }


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_backup() -> dict[str, str]:
    if BACKUP_ROOT.exists() or BACKUP_ROOT.is_symlink():
        raise ExecutionBlocked("B4_BACKUP_ROOT_ALREADY_EXISTS")
    BACKUP_ROOT.mkdir(mode=0o700)
    result: dict[str, str] = {}
    for source in (OPERATION, LEDGER, RUNTIME_ROOT / "episode-budget.json.lock"):
        require_regular(source)
        destination = BACKUP_ROOT / source.name
        with source.open("rb") as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(destination, 0o600)
        if digest(destination.read_bytes()) != digest(source.read_bytes()):
            raise ExecutionBlocked("B4_BACKUP_HASH_MISMATCH")
        result[source.name] = digest(destination.read_bytes())
    manifest = {"action_id": ACTION_ID, "files": result, "immutable_pre_transport": True}
    raw = canonical_bytes(manifest)
    manifest_path = BACKUP_ROOT / "manifest.json"
    with manifest_path.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.chmod(manifest_path, 0o600)
    fsync_dir(BACKUP_ROOT); fsync_dir(BACKUP_PARENT)
    return {"root": str(BACKUP_ROOT), "manifest_sha256": digest(raw)}


def validate_translation(value: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict) or set(value) != {"translations"}:
        raise ExecutionBlocked("B4_RESPONSE_ROOT_SCHEMA_INVALID")
    rows = value.get("translations")
    if not isinstance(rows, list) or len(rows) != len(UNIT_IDS):
        raise ExecutionBlocked("B4_RESPONSE_CARDINALITY_INVALID")
    projected = []
    normalized = False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ExecutionBlocked("B4_RESPONSE_ITEM_INVALID")
        extra = set(row) - {"id", "text"}
        normalized = normalized or bool(extra)
        projected.append({"id": row["id"], "text": row["text"]})
    if [row["id"] for row in projected] != UNIT_IDS:
        raise ExecutionBlocked("B4_RESPONSE_MEMBERSHIP_INVALID")
    return {"translations": projected}, normalized


def execute() -> dict[str, Any]:
    pre = plan(require_authorization=True)
    payload = validate_payload()
    backup = create_backup()
    sys.path.insert(0, str(CANDIDATE_ROOT / "src/subtranslate"))
    from v238_per_call_durability import DurableV226Call
    context = build_context()
    metadata = {"phase": "recovery", "attempt_type": "INITIAL", "logical_batch_id": LOGICAL_BATCH_ID,
                "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 4, "unit_ids": UNIT_IDS,
                "event_count": 8, "unit_membership_sha256": MEMBERSHIP_SHA256, "model": MODEL,
                "model_digest": MODEL_DIGEST, "timeout_seconds": TIMEOUT_SECONDS}
    call = DurableV226Call(context, payload, metadata)
    if call.logical_call_id != EXPECTED_LOGICAL_CALL_ID or call.physical_attempt_id != EXPECTED_PHYSICAL_ATTEMPT_ID:
        raise ExecutionBlocked("B4_PROJECTED_IDENTITY_MISMATCH")
    state = call.prepare_request()
    if state.get("state") != "REQUEST_DURABLE":
        raise ExecutionBlocked("B4_REQUEST_NOT_DURABLE")
    with call.exclusive_transport_claim() as owner:
        if not owner:
            raise ExecutionBlocked("B4_TRANSPORT_ALREADY_CLAIMED")
        call.begin_transport()
        import requests
        response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_SECONDS)
        raw = bytes(response.content)
        call.record_response(raw, status_code=int(response.status_code))
    if int(response.status_code) != 200:
        raise ExecutionBlocked(f"B4_HTTP_STATUS:{response.status_code}")
    try:
        envelope = json.loads(raw.decode("utf-8"))
        content = (envelope.get("message") or {}).get("content") if isinstance(envelope, dict) else None
        if not isinstance(content, str):
            raise ExecutionBlocked("B4_RESPONSE_CONTENT_MISSING")
        value = json.loads(content)
        projected, normalized = validate_translation(value)
    except Exception as exc:
        call.mark_parsed(valid=False, error=f"{type(exc).__name__}:{exc}")
        raise
    if normalized:
        call.mark_parsed(valid=False, error="MODEL_RESPONSE_EXTRA_PROPERTY_VIOLATING_STRICT_SCHEMA")
        audit = {"policy": POLICY, "raw_schema_status": "INVALID_EXTRA_PROPERTY",
                 "derived_schema_status": "VALID_AFTER_DETERMINISTIC_PROJECTION",
                 "offending_item_count": sum(bool(set(row) - {"id", "text"}) for row in value["translations"]),
                 "extra_property_count": sum(len(set(row) - {"id", "text"}) for row in value["translations"])}
        call.record_derived_normalization(projected, audit)
        call.mark_derived_parsed_valid()
    else:
        call.mark_parsed(valid=True)
    return {**pre, "status": "PASS", "backup": backup, "physical_transport_count": 1,
            "model_call_count": 1, "retry_count": 0, "final_state": call.state(),
            "logical_call_id": call.logical_call_id, "physical_attempt_id": call.physical_attempt_id,
            "canonical_reconciliation_required": True, "next_batch_started": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = plan(require_authorization=False) if args.plan else execute()
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}", "retry_executed": False},
                         sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
