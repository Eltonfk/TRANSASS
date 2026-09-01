#!/usr/bin/env python3
"""AUTO-03D multi-episode executor — parameterized by episode config JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat as _stat
import subprocess
import sys
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT_STATE = AUTHORITY_ROOT / "PROJECT_STATE.json"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")

ACTION_ID = "EPISODE_BATCH_EXECUTION"
EXECUTOR_ID = "EPISODE_EXECUTOR_V1"
MODEL = "qwen3.5:9b"
MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
POLICY = "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V3_OPAQUE_CONTEXT_METADATA"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TIMEOUT_SECONDS = 240
SOURCE_SHA256 = "0283291ca1ad212c27a3519a56a0a4dd89c706fa6d055a2b987bd9470a826bc0"
CANDIDATE_EXECUTION_CONTRACT = "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84"
CONFIGURATION_HASH = "0248eaff2384681e6bbf24e6e43eb4ca6cac123579fb68b7de42f3d5f5cba444"
GLOSSARY_HASH = "64b0f676fed3bc495903f290b69a3290eebe2d52f8e726886a1ae7ea813b360e"
PROMPT_SCHEMA_HASH = "05911c99936b46be9cd4d8878407a8e8986351e086f3414bf297d880b4b46f63"

EPISODE_TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_episode_executor.py",
    ".opencode/tools/subtranslate_episode_planner.py",
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise ExecutionBlocked(f"UNSAFE_FILE:{path}")
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
    for rel in EPISODE_TOOLCHAIN_COMPONENTS:
        p = CANDIDATE_ROOT / rel
        require_regular(p)
        manifest.append({"path": rel, "sha256": sha256_bytes(p.read_bytes())})
    return sha256_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def logical_batch_id(batch_index: int) -> str:
    return f"v226-initial-{batch_index:06d}"


def authorization_key(batch_index: int) -> str:
    return f"auto03d_b{batch_index}_batch_execution_authorization_r1"


def authorized_next_action(batch_index: int) -> str:
    return f"B{batch_index}_BATCH_EXECUTION_AUTHORIZED"


def validate_batch_index(batch_index: int) -> None:
    if not isinstance(batch_index, int) or batch_index < 1 or batch_index > 232:
        raise ExecutionBlocked(f"BATCH_INDEX_OUT_OF_RANGE:{batch_index}")


def authorization(batch_index: int) -> dict[str, Any]:
    state = load_json(PROJECT_STATE)
    record = state.get(authorization_key(batch_index))
    if not isinstance(record, dict):
        raise ExecutionBlocked(f"BATCH_EXECUTION_AUTHORIZATION_ABSENT:b{batch_index}")
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
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "future_batches_authorized": False,
    }
    for key, value in required.items():
        if record.get(key) != value:
            raise ExecutionBlocked(f"BATCH_AUTHORIZATION_CONTRACT_MISMATCH:{key}")
    for key in ("operation_id", "family_id", "episode_id", "unit_ids",
                "unit_membership_sha256", "request_payload_sha256", "request_payload_path", "logical_batch_id"):
        if key not in record:
            raise ExecutionBlocked(f"BATCH_AUTHORIZATION_MISSING_FACT:{key}")
    if record.get("logical_batch_id") != logical_batch_id(batch_index):
        raise ExecutionBlocked("BATCH_AUTHORIZATION_LOGICAL_BATCH_MISMATCH")
    fp = record.get("execution_toolchain_fingerprint")
    if not isinstance(fp, str) or len(fp) != 64:
        raise ExecutionBlocked("BATCH_AUTHORIZATION_TOOLCHAIN_UNBOUND")
    if fp != current_toolchain_fingerprint():
        raise ExecutionBlocked("BATCH_AUTHORIZATION_TOOLCHAIN_CHANGED")
    snap = record.get("snapshot_fingerprint")
    if not isinstance(snap, str) or len(snap) != 64:
        raise ExecutionBlocked("BATCH_AUTHORIZATION_SNAPSHOT_UNBOUND")
    return record


def plan(*, require_authorization: bool, batch_index: int, auth=None):
    if require_authorization and auth is None:
        auth = authorization(batch_index)
    return {
        "status": "READY" if (not require_authorization or auth) else "BLOCKED",
        "action_id": ACTION_ID,
        "executor_id": EXECUTOR_ID,
        "batch_index": batch_index,
        "model": MODEL,
        "policy": POLICY,
        "max_client_calls": 1,
        "max_http_posts": 1,
        "max_retries": 0,
        "authorization_present": auth is not None,
        "side_effects_performed": False,
    }


def create_backup(batch_index: int, auth: dict) -> dict:
    backup_root = BACKUP_PARENT / f"subtranslate-auto03d-b{batch_index}-batch-execution-r1"
    if backup_root.exists():
        raise ExecutionBlocked("BATCH_BACKUP_ROOT_ALREADY_EXISTS")
    backup_root.mkdir(mode=0o700)
    payload_path = Path(auth["request_payload_path"])
    require_regular(payload_path)
    dst = backup_root / "request_payload.json"
    with payload_path.open("rb") as src, dst.open("xb") as out:
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(dst, 0o600)
    if sha256_bytes(dst.read_bytes()) != sha256_bytes(payload_path.read_bytes()):
        raise ExecutionBlocked("BATCH_BACKUP_HASH_MISMATCH")
    manifest = {"action_id": ACTION_ID, "batch_index": batch_index, "immutable_pre_transport": True}
    raw = json.dumps(manifest, sort_keys=True).encode()
    mp = backup_root / "manifest.json"
    with mp.open("xb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(mp, 0o600)
    fd = os.open(str(backup_root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"root": str(backup_root)}


def execute(batch_index: int) -> dict[str, Any]:
    auth = authorization(batch_index)
    pre = plan(require_authorization=True, batch_index=batch_index, auth=auth)

    payload_path = Path(auth["request_payload_path"])
    require_regular(payload_path)
    raw_payload = payload_path.read_bytes()
    if sha256_bytes(raw_payload) != auth["request_payload_sha256"]:
        raise ExecutionBlocked("BATCH_PAYLOAD_HASH_MISMATCH")
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ExecutionBlocked("BATCH_PAYLOAD_INVALID")
    if payload.get("model") != MODEL or payload.get("stream") is not False or payload.get("think") is not False:
        raise ExecutionBlocked("BATCH_PAYLOAD_MODEL_OR_STREAM_MISMATCH")
    if payload.get("options") != {"num_ctx": 2560, "num_predict": 1024, "temperature": 0.0}:
        raise ExecutionBlocked("BATCH_PAYLOAD_OPTIONS_MISMATCH")

    backup = create_backup(batch_index, auth)

    sys.path.insert(0, str(CANDIDATE_ROOT / "src/subtranslate"))
    from v238_per_call_durability import DurableV226Call

    unit_ids = list(auth["unit_ids"])
    context = {
        "operation_id": auth["operation_id"],
        "anime_series_id": "3",
        "episode_id": auth["episode_id"],
        "episode_family_id": auth["family_id"],
        "source_sha256": SOURCE_SHA256,
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "durable_call_root": str(AUTHORITY_ROOT / "runtime-evidence" / auth["family_id"]),
        "episode_family_root": str(AUTHORITY_ROOT / "runtime-evidence" / auth["family_id"]),
        "episode_budget_ledger_path": str(AUTHORITY_ROOT / "runtime-evidence" / auth["family_id"] / "episode-budget.json"),
        "episode_budget_limits": {"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1,
                                  "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1},
        "pipeline_id": "v2_3_8",
        "stage_id": "FULL_TRANSLATION_V238",
        "candidate_execution_contract": CANDIDATE_EXECUTION_CONTRACT,
        "configuration_hash": CONFIGURATION_HASH,
        "glossary_hash": GLOSSARY_HASH,
        "prompt_schema_hash": PROMPT_SCHEMA_HASH,
        "response_normalization_policy": POLICY,
        "candidate_commit": git_value("rev-parse", "HEAD"),
        "transport_claim_timeout_seconds": 0.0,
    }
    metadata = {"phase": "batch", "attempt_type": "INITIAL", "logical_batch_id": auth["logical_batch_id"],
                "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": batch_index,
                "unit_ids": unit_ids, "event_count": len(unit_ids),
                "unit_membership_sha256": auth["unit_membership_sha256"],
                "model": MODEL, "model_digest": MODEL_DIGEST, "timeout_seconds": TIMEOUT_SECONDS}
    call = DurableV226Call(context, payload, metadata)
    state = call.prepare_request()
    if state.get("state") != "REQUEST_DURABLE":
        raise ExecutionBlocked("BATCH_REQUEST_NOT_DURABLE")
    with call.exclusive_transport_claim() as owner:
        if not owner:
            raise ExecutionBlocked("BATCH_TRANSPORT_ALREADY_CLAIMED")
        call.begin_transport()
        import requests
        response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_SECONDS)
        raw = bytes(response.content)
        call.record_response(raw, status_code=int(response.status_code))
    if int(response.status_code) != 200:
        raise ExecutionBlocked(f"BATCH_HTTP_STATUS:{response.status_code}")
    try:
        envelope = json.loads(raw.decode("utf-8"))
        content = (envelope.get("message") or {}).get("content") if isinstance(envelope, dict) else None
        if not isinstance(content, str):
            raise ExecutionBlocked("BATCH_RESPONSE_CONTENT_MISSING")
        value = json.loads(content)
        projected, normalized = validate_translation(value, unit_ids)
    except Exception as exc:
        call.mark_parsed(valid=False, error=f"{type(exc).__name__}:{exc}")
        raise
    if normalized:
        call.mark_parsed(valid=False, error="MODEL_RESPONSE_EXTRA_PROPERTY_VIOLATING_STRICT_SCHEMA")
        audit = {"policy": POLICY, "raw_schema_status": "INVALID_EXTRA_PROPERTY",
                 "derived_schema_status": "VALID_AFTER_DETERMINISTIC_PROJECTION"}
        call.record_derived_normalization(projected, audit)
        call.mark_derived_parsed_valid()
    else:
        call.mark_parsed(valid=True)
    return {**pre, "status": "PASS", "backup": backup, "physical_transport_count": 1,
            "model_call_count": 1, "retry_count": 0, "final_state": call.state(),
            "logical_call_id": call.logical_call_id, "physical_attempt_id": call.physical_attempt_id,
            "canonical_reconciliation_required": True, "next_batch_started": False}


def validate_translation(value, unit_ids):
    if not isinstance(value, dict) or set(value) != {"translations"}:
        raise ExecutionBlocked("BATCH_RESPONSE_ROOT_SCHEMA_INVALID")
    rows = value.get("translations")
    if not isinstance(rows, list) or len(rows) != len(unit_ids):
        raise ExecutionBlocked("BATCH_RESPONSE_CARDINALITY_INVALID")
    projected = []
    normalized = False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ExecutionBlocked("BATCH_RESPONSE_ITEM_INVALID")
        extra = set(row) - {"id", "text"}
        normalized = normalized or bool(extra)
        projected.append({"id": row["id"], "text": row["text"]})
    if [r["id"] for r in projected] != unit_ids:
        raise ExecutionBlocked("BATCH_RESPONSE_MEMBERSHIP_INVALID")
    return {"translations": projected}, normalized


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--batch", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        validate_batch_index(args.batch)
        result = plan(require_authorization=False, batch_index=args.batch) if args.plan else execute(args.batch)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}", "retry_executed": False},
                         sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
