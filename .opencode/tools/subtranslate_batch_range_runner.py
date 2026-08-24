#!/usr/bin/env python3
"""AUTO-03D batch range runner — orchestrates the proven per-batch cycle.

Operates batches N..M within a RANGE AUTHORIZATION OBJECT:

    auto03d_batch_range_{from}_{to}_execution_authorization_r1

created by a separate documentary gate BEFORE this runner is operated.
Per batch, the runner replays the exact cycle proven in B8/B9:

  1. generalized planner (--batch N) with mandatory engine oracles;
  2. persistent backup of PROJECT_STATE/HANDOFF;
  3. payload materialization + per-batch authorization object +
     pointer flip to B{N}_BATCH_EXECUTION_AUTHORIZED;
  4. generalized executor (--apply --batch N) — exactly one model call,
     max 1 POST, 0 retries;
  5. coverage formalization + reconciliation object + pointer advance.

Fail-closed: ANY error, mismatch or missing evidence persists the progress
report and stops the whole range immediately.  Batches whose reconciliation
object already exists are skipped idempotently (resume support).  A batch
with an authorization object but NO reconciliation object means a previous
run died mid-batch and REQUIRES manual assessment — the runner refuses to
auto-resume it.

Modes:
  --plan   validates the range authorization and dry-runs the planner for
           every batch in the range (read-only, no writes);
  --apply  executes the full cycle for every batch in the range.

The runner itself performs no network I/O — all transport happens inside the
proven generalized executor subprocess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat as _stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT_STATE = AUTHORITY_ROOT / "PROJECT_STATE.json"
HANDOFF = AUTHORITY_ROOT / "HANDOFF_CHATGPT.md"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")
PLANNER = CANDIDATE_ROOT / ".opencode/tools/subtranslate_batch_planner.py"
EXECUTOR = CANDIDATE_ROOT / ".opencode/tools/subtranslate_batch_executor.py"

ACTION_ID = "BATCH_RANGE_EXECUTION"
RUNNER_ID = "BATCH_RANGE_RUNNER_V1"
POLICY = "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V3_OPAQUE_CONTEXT_METADATA"
MIN_BATCH_INDEX = 8
MAX_BATCH_INDEX = 232
SOURCE_SHA256 = "0283291ca1ad212c27a3519a56a0a4dd89c706fa6d055a2b987bd9470a826bc0"
CANDIDATE_EXECUTION_CONTRACT = "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84"
CONFIGURATION_HASH = "0248eaff2384681e6bbf24e6e43eb4ca6cac123579fb68b7de42f3d5f5cba444"
GLOSSARY_HASH = "64b0f676fed3bc495903f290b69a3290eebe2d52f8e726886a1ae7ea813b360e"
PROMPT_SCHEMA_HASH = "05911c99936b46be9cd4d8878407a8e8986351e086f3414bf297d880b4b46f63"
RANGE_TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_batch_range_runner.py",
    ".opencode/tools/subtranslate_batch_planner.py",
    ".opencode/tools/subtranslate_batch_executor.py",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/agents/subtranslate-audit.md",
    ".opencode/agents/subtranslate-doc-sync.md",
    ".opencode/commands/subtranslate-next.md",
    ".opencode/skills/subtranslate-canary/SKILL.md",
)


class RangeBlocked(RuntimeError):
    """Fail-closed range abort; never a transport or write condition."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_range(from_index: int, to_index: int) -> None:
    if not isinstance(from_index, int) or not isinstance(to_index, int):
        raise RangeBlocked("BATCH_RANGE_INDICES_INVALID")
    if from_index < MIN_BATCH_INDEX or to_index > MAX_BATCH_INDEX or from_index > to_index:
        raise RangeBlocked(
            f"BATCH_RANGE_INVALID:{from_index}-{to_index}:allowed=[{MIN_BATCH_INDEX},{MAX_BATCH_INDEX}]"
        )


def range_authorization_key(from_index: int, to_index: int) -> str:
    return f"auto03d_batch_range_{from_index}_{to_index}_execution_authorization_r1"


def logical_batch_id(batch_index: int) -> str:
    return f"v226-initial-{batch_index:06d}"


def authorized_next_action(batch_index: int) -> str:
    return f"B{batch_index}_BATCH_EXECUTION_AUTHORIZED"


def decision_required_next_action(batch_index: int) -> str:
    return f"B{batch_index}_EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND"


def current_toolchain_fingerprint() -> str:
    manifest = []
    for relative in RANGE_TOOLCHAIN_COMPONENTS:
        path = CANDIDATE_ROOT / relative
        info = path.lstat()
        if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
            raise RangeBlocked(f"UNSAFE_FILE:{path}")
        manifest.append({"path": relative, "sha256": sha256_bytes(path.read_bytes())})
    return sha256_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def load_json(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if _stat.S_ISLNK(info.st_mode) or not _stat.S_ISREG(info.st_mode):
        raise RangeBlocked(f"UNSAFE_FILE:{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RangeBlocked(f"JSON_ROOT_INVALID:{path}")
    return value


def range_authorization(from_index: int, to_index: int) -> dict[str, Any]:
    state = load_json(PROJECT_STATE)
    record = state.get(range_authorization_key(from_index, to_index))
    if not isinstance(record, dict):
        raise RangeBlocked(f"BATCH_RANGE_AUTHORIZATION_ABSENT:{from_index}-{to_index}")
    required = {
        "action_id": ACTION_ID,
        "runner_id": RUNNER_ID,
        "from_index": from_index,
        "to_index": to_index,
        "apply_permission_active": True,
        "pipeline_model_call_authorized": True,
        "external_transport_authorized": True,
        "runtime_write_authorized": True,
        "production_write_authorized": False,
        "automatic_retry_authorized": False,
        "max_retries_per_batch": 0,
        "model": "qwen3.5:9b",
        "model_digest": "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7",
        "future_batches_outside_range_authorized": False,
    }
    for key, value in required.items():
        if record.get(key) != value:
            raise RangeBlocked(f"BATCH_RANGE_CONTRACT_MISMATCH:{key}")
    fingerprint = record.get("execution_toolchain_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise RangeBlocked("BATCH_RANGE_TOOLCHAIN_UNBOUND")
    if fingerprint != current_toolchain_fingerprint():
        raise RangeBlocked("BATCH_RANGE_TOOLCHAIN_CHANGED")
    snapshot = record.get("snapshot_fingerprint")
    if not isinstance(snapshot, str) or len(snapshot) != 64:
        raise RangeBlocked("BATCH_RANGE_SNAPSHOT_UNBOUND")
    return record


def run_planner(batch_index: int) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(PLANNER), "--plan", "--batch", str(batch_index)],
        capture_output=True, text=True, check=False, timeout=300,
    )
    plan = json.loads(result.stdout)
    if plan.get("status") != "READY" or result.returncode != 0:
        raise RangeBlocked(f"BATCH_PLAN_NOT_READY:b{batch_index}:{plan.get('blocker', '')}")
    validation = plan.get("validation", {})
    membership_ok = (
        len(validation.get("plan_membership_reconstructed", {})) == 8
        and all(v == "MATCH" for v in validation["plan_membership_reconstructed"].values())
    )
    payload_oracle = validation.get("payload_oracle_reconstructed", {})
    payload_ok = sorted(payload_oracle) == ["1", "2", "3", "4"] and all(v == "MATCH" for v in payload_oracle.values())
    if not (membership_ok and payload_ok):
        raise RangeBlocked(f"BATCH_PLAN_ORACLES_NOT_CLEAN:b{batch_index}")
    return plan


def run_executor(batch_index: int) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(EXECUTOR), "--apply", "--batch", str(batch_index)],
        capture_output=True, text=True, check=False, timeout=420,
    )
    output = json.loads(result.stdout)
    if output.get("status") != "PASS" or result.returncode != 0:
        raise RangeBlocked(f"BATCH_EXECUTE_FAILED:b{batch_index}:{output.get('blocker', '')}")
    if output.get("final_state") != "PARSED_VALID":
        raise RangeBlocked(f"BATCH_NOT_PARSED_VALID:b{batch_index}")
    return output


def backup_canonical(backup_dir: Path) -> dict[str, str]:
    if backup_dir.exists():
        raise RangeBlocked("BATCH_BACKUP_DIR_ALREADY_EXISTS")
    backup_dir.mkdir(mode=0o700)
    manifest_files: dict[str, str] = {}
    for source in (PROJECT_STATE, HANDOFF):
        destination = backup_dir / (source.name + ".before")
        shutil.copyfile(source, destination)
        fsync_file(destination)
        manifest_files[source.name] = sha256_bytes(source.read_bytes())
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"files": manifest_files}, sort_keys=True), encoding="utf-8")
    fsync_file(manifest_path)
    fsync_dir(backup_dir)
    return manifest_files


def executor_toolchain_fingerprint() -> str:
    """The per-batch authorization object must carry the GENERALIZED
    EXECUTOR's toolchain fingerprint — it is the component that revalidates
    the binding at apply time (namespace fix, AUTO-03D-BATCH-RANGE-RUNNER-
    FINGERPRINT-NAMESPACE-FIX-R1)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "subtranslate_batch_executor_for_range",
        CANDIDATE_ROOT / ".opencode/tools/subtranslate_batch_executor.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.current_toolchain_fingerprint()


def materialize_payload_and_object(batch_index: int, plan: dict[str, Any], operation_id: str, backup_root: str, range_snapshot: str) -> str:
    import base64

    target = plan["target"]
    payload_dir = AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6C_BATCHES_1_7/planning" / logical_batch_id(batch_index)
    payload_path = payload_dir / "request_payload.json"
    if payload_path.exists():
        raise RangeBlocked(f"BATCH_PAYLOAD_FILE_ALREADY_EXISTS:b{batch_index}")
    payload_bytes = base64.b64decode(target["request_payload_canonical_b64"], validate=True)
    expected_sha = target["request_payload_sha256"]
    if len(payload_bytes) != target["request_payload_bytes"] or sha256_bytes(payload_bytes) != expected_sha:
        raise RangeBlocked(f"BATCH_PAYLOAD_HASH_OR_SIZE_MISMATCH:b{batch_index}")
    payload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(payload_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload_bytes)
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(payload_dir)

    state = load_json(PROJECT_STATE)
    object_key = f"auto03d_b{batch_index}_batch_execution_authorization_r1"
    if object_key in state:
        raise RangeBlocked(f"BATCH_AUTHORIZATION_OBJECT_ALREADY_EXISTS:b{batch_index}")
    record = {
        "action_id": "BATCH_EXECUTION",
        "executor_id": "BATCH_EXECUTOR_V1",
        "apply_permission_active": True,
        "pipeline_model_call_authorized": True,
        "external_transport_authorized": True,
        "runtime_write_authorized": True,
        "production_write_authorized": False,
        "automatic_retry_authorized": False,
        "max_retries": 0,
        "max_client_calls": 1,
        "max_http_posts": 1,
        "model": "qwen3.5:9b",
        "model_digest": "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7",
        "future_batches_authorized": False,
        "operation_id": operation_id,
        "family_id": f"V238_E07_R6C_B{batch_index}_BATCH",
        "episode_id": target["episode_id"],
        "logical_batch_id": logical_batch_id(batch_index),
        "unit_ids": target["unit_ids"],
        "unit_membership_sha256": target["unit_membership_sha256"],
        "request_payload_sha256": expected_sha,
        "request_payload_path": str(payload_path),
        "execution_toolchain_fingerprint": executor_toolchain_fingerprint(),
        # REAL range snapshot propagated (placeholder fix, AUTO-03D-BATCH-RANGE-
        # RUNNER-SNAPSHOT-PLACEHOLDER-FIX-R1) — never a literal placeholder.
        "snapshot_fingerprint": range_snapshot,
        "range_binding": {
            "range_authorization_key": None,
            "note": "operated under an authorized batch range; see range object",
        },
        "backup_root": backup_root,
        "provenance": "BATCH_RANGE_RUNNER_UNDER_RANGE_AUTHORIZATION",
    }
    state[object_key] = record
    state["next_action"] = authorized_next_action(batch_index)
    atomic_write_json(PROJECT_STATE, state)
    return str(payload_path)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", suffix=".json", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(serialized, encoding="utf-8")
        fsync_file(tmp_path)
        os.replace(tmp_path, path)
        fsync_dir(path.parent)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def reconcile_batch(batch_index: int, executor_result: dict[str, Any], plan: dict[str, Any], operation_id: str) -> str:
    family_id = f"V238_E07_R6C_B{batch_index}_BATCH"
    family_dir = AUTHORITY_ROOT / "runtime-evidence" / family_id
    attempt_id = executor_result["physical_attempt_id"]
    attempt_dir = family_dir / "calls" / attempt_id

    state_json = json.loads((attempt_dir / "state.json").read_text(encoding="utf-8"))
    if state_json.get("state") != "PARSED_VALID":
        raise RangeBlocked(f"BATCH_ATTEMPT_NOT_PARSED_VALID:b{batch_index}")
    response_meta = json.loads((attempt_dir / "response_metadata.json").read_text(encoding="utf-8"))
    response_sha = str(response_meta.get("response_sha256") or "")

    planning_payload = (
        AUTHORITY_ROOT / "runtime-evidence/V238_E07_R6C_BATCHES_1_7/planning"
        / logical_batch_id(batch_index) / "request_payload.json"
    )
    payload = json.loads(planning_payload.read_bytes())
    content = payload["messages"][0]["content"]
    start = content.index("TARGET: ") + len("TARGET: ")
    end = content.index("\nGLOSSARY:", start)
    targets = json.loads(content[start:end])
    outer = json.loads((attempt_dir / "response.body").read_bytes())
    translations = {t["id"]: t["text"] for t in json.loads(outer["message"]["content"])["translations"]}

    expected_ids = list(plan["target"]["unit_ids"])
    found_ids = sorted(t_id for t_id in translations if t_id in expected_ids)
    missing_ids = sorted(t_id for t_id in expected_ids if t_id not in translations)
    if missing_ids or len(found_ids) != len(expected_ids):
        raise RangeBlocked(f"BATCH_COVERAGE_INCOMPLETE:b{batch_index}")
    mapping = [
        {"id": t_id, "source": next(i["text"] for i in targets if i["id"] == t_id), "translation": translations[t_id]}
        for t_id in found_ids
    ]
    coverage = {
        "schema": f"subtranslate.v238.b{batch_index}_derived_coverage.v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "physical_attempt_id": attempt_id,
        "logical_batch_id": logical_batch_id(batch_index),
        "found_ids": found_ids,
        "missing_ids": missing_ids,
        "expected_count": len(expected_ids),
        "found_count": len(found_ids),
        "distinct_sources": len({m["source"] for m in mapping}),
        "distinct_translations": len({m["translation"] for m in mapping}),
        "mapping": mapping,
        "provenance": {"request_payload": str(planning_payload), "response_body": str(attempt_dir / "response.body")},
    }
    coverage_path = attempt_dir / "derived_coverage.json"
    if coverage_path.exists():
        raise RangeBlocked(f"BATCH_COVERAGE_ARTIFACT_ALREADY_EXISTS:b{batch_index}")
    fd = os.open(str(coverage_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(attempt_dir)

    state = load_json(PROJECT_STATE)
    reconciliation_key = f"auto03d_b{batch_index}_post_execution_reconciliation_r1"
    if reconciliation_key in state:
        raise RangeBlocked(f"BATCH_RECONCILIATION_OBJECT_ALREADY_EXISTS:b{batch_index}")
    record = {
        "execution": {
            "action_id": "BATCH_EXECUTION",
            "executor_id": "BATCH_EXECUTOR_V1",
            "batch_index": batch_index,
            "operation_id": operation_id,
            "family_id": family_id,
            "episode_id": "79",
            "logical_batch_id": logical_batch_id(batch_index),
            "unit_ids": expected_ids,
            "unit_membership_sha256": plan["target"]["unit_membership_sha256"],
            "request_payload_sha256": plan["target"]["request_payload_sha256"],
            "response_sha256": response_sha,
            "physical_attempt_id": attempt_id,
            "logical_call_id": executor_result["logical_call_id"],
            "terminal_state": "PARSED_VALID",
            "model_calls": 1,
            "http_posts": 1,
            "retries": 0,
            "model": "qwen3.5:9b",
            "policy": POLICY,
        },
        "coverage_formalized": {
            "artifact": str(coverage_path),
            "found_ids": found_ids,
            "missing_ids": missing_ids,
        },
        "authorization_lineage": {
            "range_runner": "AUTO-03D-BATCH-RANGE-RUNNER under range authorization",
        },
        "future_side_effects_authorized": False,
        "next_batch_started": False,
        "canonical_reconciliation_required": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    state[reconciliation_key] = record
    state["state"] = f"SUBTRANSLATE_V238_E07_R6C_COMPLETE_BATCHES_1_{batch_index}_ALL_PARSED_VALID_ZERO_RETRY"
    status = state.get("status", "")
    marker = f"_BATCH{batch_index}_PARSED_VALID_COVERAGE_FORMALIZED"
    if marker not in status:
        status = status.replace("_ZERO_RETRY", "") + marker + "_ZERO_RETRY"
        state["status"] = status
    atomic_write_json(PROJECT_STATE, state)
    return str(coverage_path)


def progress_report(from_index: int, to_index: int, results: list[dict[str, Any]], stopped: bool, blocker: str | None) -> dict[str, Any]:
    completed = [r for r in results if r["status"] == "COMPLETED"]
    skipped = [r for r in results if r["status"] == "SKIPPED_ALREADY_RECONCILED"]
    return {
        "range": f"{from_index}-{to_index}",
        "batches_completed": [r["batch_index"] for r in completed],
        "batches_skipped_resumed": [r["batch_index"] for r in skipped],
        "completed_count": len(completed),
        "stopped_early": stopped,
        "blocker": blocker,
        "results": results,
    }


def persist_progress(report: dict[str, Any]) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(f"/tmp/opencode/batch_range_report_{report['range'].replace('-', '_')}_{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"progress report persisted: {path}", file=sys.stderr)


def classify_batch(state: dict[str, Any], batch_index: int) -> str:
    """Decide the start action for a batch inside an authorized range.

    Returns "SKIP" when the batch is already reconciled (resume), "RUN" when
    the canonical pointer expects this batch's external decision, and raises
    RangeBlocked for any other state (mid-cycle interruption, unexpected
    pointer) — never auto-resumes a half-done batch.
    """
    if f"auto03d_b{batch_index}_post_execution_reconciliation_r1" in state:
        return "SKIP"
    auth_object_key = f"auto03d_b{batch_index}_batch_execution_authorization_r1"
    if auth_object_key in state:
        raise RangeBlocked(
            f"BATCH_MID_CYCLE_INTERRUPTED:b{batch_index}:manual assessment required"
        )
    expected_pointer = decision_required_next_action(batch_index)
    if state.get("next_action") != expected_pointer:
        raise RangeBlocked(f"BATCH_POINTER_UNEXPECTED:b{batch_index}:{state.get('next_action')}")
    return "RUN"


def backup_dir_for(batch_index: int, stamp: str) -> Path:
    """Deterministic per-batch canonical backup directory."""
    return BACKUP_PARENT / f"subtranslate-b{batch_index}-documentary-write-{stamp}"


def apply_range(from_index: int, to_index: int) -> dict[str, Any]:
    validate_range(from_index, to_index)
    range_record = range_authorization(from_index, to_index)
    results: list[dict[str, Any]] = []
    stopped = False
    blocker: str | None = None
    try:
        for batch_index in range(from_index, to_index + 1):
            state = load_json(PROJECT_STATE)
            classification = classify_batch(state, batch_index)
            if classification == "SKIP":
                results.append({"batch_index": batch_index, "status": "SKIPPED_ALREADY_RECONCILED"})
                continue

            plan = run_planner(batch_index)
            operation_id = f"SUBTRANSLATE_V238_E07_R6C_B{batch_index}_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = backup_dir_for(batch_index, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            manifest_files = backup_canonical(backup_dir)
            payload_path = materialize_payload_and_object(batch_index, plan, operation_id, str(backup_dir), range_record["snapshot_fingerprint"])
            executor_result = run_executor(batch_index)
            coverage_path = reconcile_batch(batch_index, executor_result, plan, operation_id)

            # Advance the pointer to the next batch's decision state so the
            # next iteration's classify_batch sees the expected value.
            if batch_index < to_index:
                state = load_json(PROJECT_STATE)
                state["next_action"] = decision_required_next_action(batch_index + 1)
                atomic_write_json(PROJECT_STATE, state)

            results.append({
                "batch_index": batch_index,
                "status": "COMPLETED",
                "operation_id": operation_id,
                "physical_attempt_id": executor_result["physical_attempt_id"],
                "logical_call_id": executor_result["logical_call_id"],
                "unit_ids": plan["target"]["unit_ids"],
                "coverage_artifact": coverage_path,
                "backup_root": str(backup_dir),
                "canonical_backup_hashes": manifest_files,
            })
    except Exception as exc:
        stopped = True
        blocker = f"{type(exc).__name__}:{exc}"
    report = progress_report(from_index, to_index, results, stopped, blocker)
    persist_progress(report)
    if stopped:
        print(json.dumps({"status": "FAIL_STOP", **report}, sort_keys=True, ensure_ascii=False))
        return {"status": "FAIL_STOP", "report": report}
    # Final pointer: range completed, next range decision required.
    state = load_json(PROJECT_STATE)
    state["next_action"] = f"BATCH_RANGE_{from_index}_{to_index}_COMPLETED_NEXT_RANGE_DECISION_REQUIRED"
    state["latest_decision"] = (
        f"BATCH_RANGE_{from_index}_{to_index}_ALL_PARSED_VALID_ZERO_RETRY_NEXT_RANGE_DECISION_REQUIRED"
    )
    atomic_write_json(PROJECT_STATE, state)
    summary = {**report, "status": "PASS"}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    return summary


def plan_range(from_index: int, to_index: int) -> dict[str, Any]:
    validate_range(from_index, to_index)
    range_authorization(from_index, to_index)
    batches: list[dict[str, Any]] = []
    for batch_index in range(from_index, to_index + 1):
        state = load_json(PROJECT_STATE)
        already = f"auto03d_b{batch_index}_post_execution_reconciliation_r1" in state
        plan = None if already else run_planner(batch_index)
        batches.append({
            "batch_index": batch_index,
            "already_reconciled": already,
            "status": plan["status"] if plan else "SKIPPED_ALREADY_RECONCILED",
            "unit_ids": plan["target"]["unit_ids"] if plan else None,
            "unit_membership_sha256": plan["target"]["unit_membership_sha256"] if plan else None,
            "request_payload_sha256": plan["target"]["request_payload_sha256"] if plan else None,
        })
    return {
        "status": "READY",
        "mode": "RANGE_PLAN_READ_ONLY",
        "action_id": ACTION_ID,
        "runner_id": RUNNER_ID,
        "side_effects_performed": False,
        "range": f"{from_index}-{to_index}",
        "batches": batches,
        "note": "--apply requires the range documentary gate to have created the range authorization object",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--from", dest="from_index", type=int, required=True)
    parser.add_argument("--to", dest="to_index", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.plan:
            result = plan_range(args.from_index, args.to_index)
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
            return 0
        result = apply_range(args.from_index, args.to_index)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0 if result.get("status") == "PASS" else 1
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
