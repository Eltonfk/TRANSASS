#!/usr/bin/env python3
"""AUTO-03E episode-integral range runner (generic, config-parameterized).

Executes ONE episode end-to-end under a single documentary authorization:

  --mode authorize  materializes the episode-integral authorization object in
                    PROJECT_STATE.json (fresh probe, exact prestate, atomic
                    publish of both authority documents, HANDOFF addendum).
  --mode status     read-only progress report (no derivation side effects).
  --mode execute    runs every pending batch of the episode sequentially:
                    per batch -> generalized planner (read-only) -> canonical
                    backup -> payload materialization + per-batch authorization
                    object -> EXACTLY one durable model call / one POST /
                    zero retry -> derived_coverage.json -> per-batch canonical
                    reconciliation object -> pointer advance.  Any divergence
                    is FAIL_STOP; already-reconciled batches are SKIP-safe;
                    a half-done batch is MID_CYCLE_INTERRUPTED (manual review).

No paths or document contents are accepted beyond --config; every binding fact
derives from the episode config and canonical sources.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROJECT = AUTHORITY_ROOT / "PROJECT_STATE.json"
HANDOFF = AUTHORITY_ROOT / "HANDOFF_CHATGPT.md"
PROBE_PATH = CANDIDATE_ROOT / ".opencode/tools/subtranslate_readonly_probe.py"
PLANNER_PATH = CANDIDATE_ROOT / ".opencode/tools/subtranslate_episode_planner.py"
DEFAULT_CONFIG = CANDIDATE_ROOT / ".opencode/tools/episode_configs/e08_config.json"
BACKUP_PARENT = Path("/home/palhacinho/opencode-backups")
SRC_ROOT = CANDIDATE_ROOT / "src/subtranslate"

ACTION_ID = "EPISODE_RANGE_EXECUTION"
RUNNER_ID = "EPISODE_RANGE_RUNNER_V1"
MODEL = "qwen3.5:9b"
MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
POLICY = "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V3_OPAQUE_CONTEXT_METADATA"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TIMEOUT_SECONDS = 240
PLANNER_TIMEOUT = 300
EXECUTOR_TIMEOUT = 600

AUTHORIZATION_KEY = "auto03e_e08_episode_execution_authorization_r1"
PRESTATE_STATE = "SUBTRANSLATE_V238_E07_R6C_COMPLETE_BATCHES_1_232_ALL_PARSED_VALID_ZERO_RETRY"
PRESTATE_NEXT = "E08_E12_V238_FLOW_PLANNING_REQUIRED"
TARGET_LATEST_DECISION = "E08_EPISODE_INTEGRAL_EXECUTION_AUTHORIZED_SINGLE_CALL_PER_BATCH_ZERO_RETRY"

TOOLCHAIN_COMPONENTS = (
    ".opencode/tools/subtranslate_episode_range_runner.py",
    ".opencode/tools/subtranslate_episode_planner.py",
    ".opencode/tools/subtranslate_readonly_probe.py",
    "src/subtranslate/v238_per_call_durability.py",
    ".opencode/agents/subtranslate-orchestrator.md",
    ".opencode/agents/subtranslate-audit.md",
    ".opencode/agents/subtranslate-doc-sync.md",
    ".opencode/commands/subtranslate-next.md",
    ".opencode/skills/subtranslate-canary/SKILL.md",
)


class RunnerBlocked(RuntimeError):
    """Fail-closed abort; never a transport or write condition."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RunnerBlocked(f"UNSAFE_FILE:{path}")
    return info


def load_json(path: Path) -> dict[str, Any]:
    regular(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerBlocked(f"JSON_ROOT_INVALID:{path}")
    return value


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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


def fresh_probe() -> dict[str, Any]:
    regular(PROBE_PATH)
    spec = importlib.util.spec_from_file_location("auto03e_fresh_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise RunnerBlocked("PROBE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.probe()
    if result.get("blockers") or result.get("unknowns") or not result.get("integrity", {}).get("snapshot_consistent"):
        raise RunnerBlocked("FRESH_PROBE_NOT_CLEAN")
    return result


def load_config(path: Path) -> dict[str, Any]:
    regular(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("source_sha256", "source_candidates", "episode_id", "series_id",
                "engine_revision", "family_id_template", "operation_id_template"):
        if key not in config:
            raise RunnerBlocked(f"CONFIG_MISSING_KEY:{key}")
    return config


def toolchain_fingerprint(config_path: Path) -> str:
    manifest = []
    for relative in TOOLCHAIN_COMPONENTS:
        path = CANDIDATE_ROOT / relative
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RunnerBlocked(f"UNSAFE_FILE:{path}")
        manifest.append({"path": relative, "sha256": sha256_bytes(path.read_bytes())})
    manifest.append({"path": str(config_path), "sha256": sha256_bytes(config_path.read_bytes())})
    return sha256_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def logical_batch_id(batch_index: int) -> str:
    return f"v226-initial-{batch_index:06d}"


def family_id_for(config: dict[str, Any], batch_index: int) -> str:
    return config["family_id_template"].format(batch_index=batch_index)


def operation_id_for(config: dict[str, Any], batch_index: int, stamp: str) -> str:
    return config["operation_id_template"].format(batch_index=batch_index, timestamp=stamp)


def batch_auth_key(batch_index: int) -> str:
    return f"auto03e_e08_b{batch_index}_batch_execution_authorization_r1"


def batch_recon_key(batch_index: int) -> str:
    return f"auto03e_e08_b{batch_index}_post_execution_reconciliation_r1"


def decision_pointer(batch_index: int) -> str:
    return f"E08_B{batch_index}_EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND"


def authorized_pointer(batch_index: int) -> str:
    return f"E08_B{batch_index}_BATCH_EXECUTION_AUTHORIZED"


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=CANDIDATE_ROOT, shell=False,
                            capture_output=True, text=True, check=False, timeout=10)
    if result.returncode != 0:
        raise RunnerBlocked("GIT_AUTHORITY_UNAVAILABLE")
    return result.stdout.strip()


def run_planner(config_path: Path, batch_index: int, expected_total: int | None) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(PLANNER_PATH), "--config", str(config_path),
         "--plan", "--batch", str(batch_index)],
        capture_output=True, text=True, check=False, timeout=PLANNER_TIMEOUT,
    )
    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerBlocked(f"BATCH_PLAN_JSON_INVALID:b{batch_index}:{exc}") from exc
    if plan.get("status") != "READY" or result.returncode != 0:
        raise RunnerBlocked(f"BATCH_PLAN_NOT_READY:b{batch_index}:{plan.get('blocker', '')}")
    target = plan.get("target") or {}
    for field in ("unit_ids", "unit_membership_sha256", "request_payload_sha256",
                  "request_payload_canonical_b64", "request_payload_bytes"):
        if field not in target:
            raise RunnerBlocked(f"BATCH_PLAN_MISSING_FACT:b{batch_index}:{field}")
    total = int(plan.get("validation", {}).get("packed_total", -1))
    if total < 1:
        raise RunnerBlocked(f"BATCH_PLAN_PACKED_TOTAL_INVALID:b{batch_index}:{total}")
    if expected_total is not None and total != expected_total:
        raise RunnerBlocked(f"BATCH_PLAN_TOTAL_CHANGED:b{batch_index}:{total}!={expected_total}")
    return plan


def backup_canonical(backup_dir: Path) -> dict[str, str]:
    if backup_dir.exists():
        raise RunnerBlocked("BATCH_BACKUP_DIR_ALREADY_EXISTS")
    backup_dir.mkdir(mode=0o700)
    manifest_files: dict[str, str] = {}
    for source in (PROJECT, HANDOFF):
        destination = backup_dir / (source.name + ".before")
        shutil.copyfile(source, destination)
        fsync_file(destination)
        manifest_files[source.name] = sha256_bytes(source.read_bytes())
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"files": manifest_files}, sort_keys=True), encoding="utf-8")
    fsync_file(manifest_path)
    fsync_dir(backup_dir)
    return manifest_files


def write_handoff_addendum(before_handoff: bytes, title: str, summary: str, fingerprint: str, next_action: str) -> bytes:
    addendum = (f"\n\n---\n\n## Addendum {datetime.now(UTC).date().isoformat()} — {title}\n\n"
                f"{summary}\n\nSNAPSHOT_FINGERPRINT={fingerprint}\n"
                "FUTURE_SIDE_EFFECTS_OUTSIDE_SCOPE=false\n"
                f"NEXT_ACTION={next_action}\n").encode()
    return before_handoff + addendum


def publish_both(before_project: bytes, after_project: bytes, before_handoff: bytes, after_handoff: bytes,
                 backup_dir: Path) -> None:
    project_info = regular(PROJECT)
    handoff_info = regular(HANDOFF)
    if backup_dir.exists():
        raise RunnerBlocked("DOCUMENTARY_BACKUP_ALREADY_EXISTS")
    backup_dir.mkdir(mode=0o700)

    def _write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    _write_new(backup_dir / "PROJECT_STATE.json.before", before_project)
    _write_new(backup_dir / "HANDOFF_CHATGPT.md.before", before_handoff)
    fsync_dir(backup_dir)

    published: list[tuple[Path, bytes, int]] = []

    def publish(path: Path, data: bytes, mode: int) -> None:
        fd, raw = tempfile.mkstemp(prefix=f".{path.name}.auto03e-", dir=path.parent)
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

    try:
        publish(PROJECT, after_project, stat.S_IMODE(project_info.st_mode))
        published.append((PROJECT, before_project, stat.S_IMODE(project_info.st_mode)))
        publish(HANDOFF, after_handoff, stat.S_IMODE(handoff_info.st_mode))
        published.append((HANDOFF, before_handoff, stat.S_IMODE(handoff_info.st_mode)))
        if json.loads(PROJECT.read_text(encoding="utf-8")) != json.loads(after_project) or not HANDOFF.read_bytes().startswith(before_handoff):
            raise RunnerBlocked("POST_PUBLISH_VERIFICATION_FAILED")
    except Exception:
        for path, data, mode_bits in reversed(published):
            publish(path, data, mode_bits)
        raise


def authorize(config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    before_project_bytes = PROJECT.read_bytes()
    before_handoff = HANDOFF.read_bytes()
    before = json.loads(before_project_bytes)
    canonical_state = before.get("state")
    first_authorization = canonical_state == PRESTATE_STATE and AUTHORIZATION_KEY not in before
    if first_authorization:
        if before.get("next_action") != PRESTATE_NEXT:
            raise RunnerBlocked("RECONCILIATION_PRESTATE_MISMATCH")
    else:
        # Episode already in progress under a previous authorization: the
        # canonical state must belong to this episode's progressive namespace.
        if not isinstance(canonical_state, str) or not canonical_state.startswith("SUBTRANSLATE_V238_ZLS_S01E08_"):
            raise RunnerBlocked(
                f"RECONCILIATION_PRESTATE_MISMATCH:{canonical_state}")
    superseded = None
    if AUTHORIZATION_KEY in before:
        # Re-authorization path: the previous episode authorization moved the
        # pointer forward; replacement is safe when every started batch is
        # already RECONCILED (no mid-cycle orphan: auth object without its
        # reconciliation counterpart).  Reconciled batches remain SKIP-safe
        # and keep their own per-batch authorization lineage.
        previous = before[AUTHORIZATION_KEY]
        if not isinstance(previous, dict):
            raise RunnerBlocked("EPISODE_AUTHORIZATION_PREVIOUS_INVALID")
        prior_total = int(previous.get("expected_total_batches", -1))
        mid_cycle = [n for n in range(max(prior_total, 0))
                     if f"auto03e_e08_b{n}_batch_execution_authorization_r1" in before
                     and f"auto03e_e08_b{n}_post_execution_reconciliation_r1" not in before]
        if mid_cycle:
            raise RunnerBlocked(
                f"EPISODE_AUTHORIZATION_REPLACE_BLOCKED_MID_CYCLE:{mid_cycle[:5]}")
        superseded = {
            "authorized_at": previous.get("authorized_at"),
            "reason": "runner corrected/re-authorized; all started batches reconciled",
            "reconciled_batches_carried_over": sorted(
                n for n in range(max(prior_total, 0))
                if f"auto03e_e08_b{n}_post_execution_reconciliation_r1" in before),
        }
    probe = fresh_probe()
    plan0 = run_planner(config_path, 0, expected_total=None)
    total = int(plan0["validation"]["packed_total"])
    now = datetime.now(UTC).isoformat()
    fingerprint = toolchain_fingerprint(config_path)
    record = {
        "action_id": ACTION_ID,
        "runner_id": RUNNER_ID,
        "mode": "EPISODE_INTEGRAL_AUTHORIZATION",
        "authorized_at": now,
        "episode_label": config.get("episode_label", "E08"),
        "episode_id": config["episode_id"],
        "series_id": config["series_id"],
        "source_sha256": config["source_sha256"],
        "engine_revision": config["engine_revision"],
        "config_sha256": sha256_bytes(config_path.read_bytes()),
        "expected_total_batches": total,
        "batch_index_range": [0, total - 1],
        "apply_permission_active": True,
        "pipeline_model_call_authorized": True,
        "external_transport_authorized": True,
        "runtime_write_authorized": True,
        "production_write_authorized": False,
        "automatic_retry_authorized": False,
        "max_retries_per_batch": 0,
        "max_client_calls_per_batch": 1,
        "max_http_posts_per_batch": 1,
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "future_episodes_authorized": False,
        "human_review_deferred_until_all_episodes_finalized": True,
        "execution_toolchain_fingerprint": fingerprint,
        "snapshot_fingerprint": probe["snapshot_fingerprint"],
        "supersedes_previous_authorization": superseded,
    }
    after = json.loads(json.dumps(before))
    after[AUTHORIZATION_KEY] = record
    # Point at the FIRST batch still missing its reconciliation object, so a
    # re-authorization mid-episode resumes instead of rewinding.
    next_expected = None
    for n in range(total):
        if f"auto03e_e08_b{n}_post_execution_reconciliation_r1" not in before:
            next_expected = n
            break
    if next_expected is None:
        after["latest_decision"] = "E08_ALL_BATCHES_RECONCILED_ASSEMBLY_REQUIRED"
        after["next_action"] = "E08_ASSEMBLY_REQUIRED"
    else:
        after["latest_decision"] = TARGET_LATEST_DECISION
        after["next_action"] = decision_pointer(next_expected)
    after_project = (json.dumps(after, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    title = "AUTO-03E-E08-EPISODE-INTEGRAL-AUTHORIZATION-R1"
    summary = ("Autorizacao de episodio integral E08 vinculada: 360 lotes esperados, um model call por lote, "
               "zero retry, producao/Library/main intocados, revisao humana adiada ate E07-E12 finalizados.")
    after_handoff = write_handoff_addendum(before_handoff, title, summary,
                                           str(probe["snapshot_fingerprint"]), after["next_action"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_PARENT / f"subtranslate-auto03e-e08-episode-integral-authorization-r1-{stamp}"
    publish_both(before_project_bytes, after_project, before_handoff, after_handoff, backup_dir)
    return {"status": "PASS", "transition": "authorize", "expected_total_batches": total,
            "project_state_sha256": digest(after_project), "handoff_sha256": digest(after_handoff),
            "backup_root": str(backup_dir), "snapshot_fingerprint": probe["snapshot_fingerprint"],
            "next_action": after["next_action"], "side_effects_pending": "run --mode execute"}


def classify(state: dict[str, Any], batch_index: int) -> str:
    if batch_recon_key(batch_index) in state:
        return "SKIP"
    if batch_auth_key(batch_index) in state:
        raise RunnerBlocked(
            f"BATCH_MID_CYCLE_INTERRUPTED:b{batch_index}:manual assessment required")
    if state.get("next_action") != decision_pointer(batch_index):
        raise RunnerBlocked(
            f"BATCH_POINTER_UNEXPECTED:b{batch_index}:{state.get('next_action')}")
    return "RUN"


def execute_batch(config: dict[str, Any], config_path: Path, batch_index: int,
                  auth_record: dict[str, Any], total: int) -> dict[str, Any]:
    plan = run_planner(config_path, batch_index, expected_total=total)
    target = plan["target"]
    family = family_id_for(config, batch_index)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    operation_id = operation_id_for(config, batch_index, stamp)
    backup_dir = BACKUP_PARENT / f"subtranslate-e08-b{batch_index}-documentary-write-{stamp}"
    manifest_files = backup_canonical(backup_dir)

    payload_dir = AUTHORITY_ROOT / "runtime-evidence" / family / "planning" / logical_batch_id(batch_index)
    payload_path = payload_dir / "request_payload.json"
    if payload_path.exists():
        raise RunnerBlocked(f"BATCH_PAYLOAD_FILE_ALREADY_EXISTS:b{batch_index}")
    payload_bytes = base64.b64decode(target["request_payload_canonical_b64"], validate=True)
    if len(payload_bytes) != int(target["request_payload_bytes"]) or sha256_bytes(payload_bytes) != target["request_payload_sha256"]:
        raise RunnerBlocked(f"BATCH_PAYLOAD_HASH_OR_SIZE_MISMATCH:b{batch_index}")
    payload_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(payload_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload_bytes)
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(payload_dir)

    state = load_json(PROJECT)
    if batch_auth_key(batch_index) in state:
        raise RunnerBlocked(f"BATCH_AUTHORIZATION_OBJECT_ALREADY_EXISTS:b{batch_index}")
    record = {
        "action_id": ACTION_ID,
        "executor_id": RUNNER_ID,
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
        "future_batches_outside_episode_authorized": False,
        "operation_id": operation_id,
        "family_id": family,
        "episode_id": str(config["episode_id"]),
        "logical_batch_id": logical_batch_id(batch_index),
        "unit_ids": target["unit_ids"],
        "unit_membership_sha256": target["unit_membership_sha256"],
        "request_payload_sha256": target["request_payload_sha256"],
        "request_payload_path": str(payload_path),
        "execution_toolchain_fingerprint": toolchain_fingerprint(config_path),
        "snapshot_fingerprint": auth_record["snapshot_fingerprint"],
        "range_binding": {"episode_authorization_key": AUTHORIZATION_KEY},
        "backup_root": str(backup_dir),
        "canonical_backup_hashes": manifest_files,
        "provenance": "EPISODE_RANGE_RUNNER_UNDER_EPISODE_AUTHORIZATION",
    }
    state[batch_auth_key(batch_index)] = record
    state["next_action"] = authorized_pointer(batch_index)
    atomic_write_json(PROJECT, state)

    sys.path.insert(0, str(SRC_ROOT))
    from v238_per_call_durability import DurableV226Call

    regular(payload_path)
    raw_payload = payload_path.read_bytes()
    if sha256_bytes(raw_payload) != record["request_payload_sha256"]:
        raise RunnerBlocked(f"BATCH_PAYLOAD_HASH_MISMATCH:b{batch_index}")
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise RunnerBlocked(f"BATCH_PAYLOAD_INVALID:b{batch_index}")
    if payload.get("model") != MODEL or payload.get("stream") is not False or payload.get("think") is not False:
        raise RunnerBlocked(f"BATCH_PAYLOAD_MODEL_OR_STREAM_MISMATCH:b{batch_index}")
    if payload.get("options") != {"num_ctx": 4096, "num_predict": 1024, "temperature": 0.0}:
        raise RunnerBlocked(f"BATCH_PAYLOAD_OPTIONS_MISMATCH:b{batch_index}")

    executor_backup = BACKUP_PARENT / f"subtranslate-auto03e-e08-b{batch_index}-batch-execution-r1"
    if executor_backup.exists():
        raise RunnerBlocked("BATCH_BACKUP_ROOT_ALREADY_EXISTS")
    executor_backup.mkdir(mode=0o700)
    dst = executor_backup / "request_payload.json"
    with payload_path.open("rb") as src, dst.open("xb") as out:
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(dst, 0o600)
    manifest = {"action_id": ACTION_ID, "batch_index": batch_index, "immutable_pre_transport": True}
    mp = executor_backup / "manifest.json"
    with mp.open("xb") as f:
        f.write(json.dumps(manifest, sort_keys=True).encode())
        f.flush()
        os.fsync(f.fileno())
    os.chmod(mp, 0o600)
    fsync_dir(executor_backup)

    context = {
        "operation_id": operation_id,
        "anime_series_id": str(config["series_id"]),
        "episode_id": str(config["episode_id"]),
        "episode_family_id": family,
        "source_sha256": config["source_sha256"],
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "durable_call_root": str(AUTHORITY_ROOT / "runtime-evidence" / family),
        "episode_family_root": str(AUTHORITY_ROOT / "runtime-evidence" / family),
        "episode_budget_ledger_path": str(AUTHORITY_ROOT / "runtime-evidence" / family / "episode-budget.json"),
        "episode_budget_limits": {"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1,
                                  "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1},
        "pipeline_id": "v2_3_8",
        "stage_id": "FULL_TRANSLATION_V238",
        "candidate_execution_contract": config["engine_revision"],
        "configuration_hash": "0248eaff2384681e6bbf24e6e43eb4ca6cac123579fb68b7de42f3d5f5cba444",
        "glossary_hash": "64b0f676fed3bc495903f290b69a3290eebe2d52f8e726886a1ae7ea813b360e",
        "prompt_schema_hash": "05911c99936b46be9cd4d8878407a8e8986351e086f3414bf297d880b4b46f63",
        "response_normalization_policy": POLICY,
        "candidate_commit": git_value("rev-parse", "HEAD"),
        "transport_claim_timeout_seconds": 0.0,
    }
    metadata = {"phase": "batch", "attempt_type": "INITIAL", "logical_batch_id": logical_batch_id(batch_index),
                "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": batch_index,
                "unit_ids": list(target["unit_ids"]), "event_count": len(target["unit_ids"]),
                "unit_membership_sha256": target["unit_membership_sha256"],
                "model": MODEL, "model_digest": MODEL_DIGEST, "timeout_seconds": TIMEOUT_SECONDS}
    call = DurableV226Call(context, payload, metadata)
    durable_state = call.prepare_request()
    if durable_state.get("state") != "REQUEST_DURABLE":
        raise RunnerBlocked(f"BATCH_REQUEST_NOT_DURABLE:b{batch_index}")
    with call.exclusive_transport_claim() as owner:
        if not owner:
            raise RunnerBlocked(f"BATCH_TRANSPORT_ALREADY_CLAIMED:b{batch_index}")
        call.begin_transport()
        import requests
        response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_SECONDS)
        raw = bytes(response.content)
        call.record_response(raw, status_code=int(response.status_code))
    if int(response.status_code) != 200:
        raise RunnerBlocked(f"BATCH_HTTP_STATUS:b{batch_index}:{response.status_code}")
    try:
        envelope = json.loads(raw.decode("utf-8"))
        content = (envelope.get("message") or {}).get("content") if isinstance(envelope, dict) else None
        if not isinstance(content, str):
            raise RunnerBlocked(f"BATCH_RESPONSE_CONTENT_MISSING:b{batch_index}")
        value = json.loads(content)
        projected, normalized = validate_translation(value, list(target["unit_ids"]), batch_index)
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

    attempt_id = call.physical_attempt_id
    attempt_dir = AUTHORITY_ROOT / "runtime-evidence" / family / "calls" / attempt_id
    state_json = json.loads((attempt_dir / "state.json").read_text(encoding="utf-8"))
    if state_json.get("state") not in ("PARSED_VALID", "DERIVED_PARSED_VALID"):
        raise RunnerBlocked(f"BATCH_ATTEMPT_NOT_PARSED_VALID:b{batch_index}:{state_json.get('state')}")
    response_meta = json.loads((attempt_dir / "response_metadata.json").read_text(encoding="utf-8"))
    response_sha = str(response_meta.get("response_sha256") or "")

    content_str = payload["messages"][0]["content"]
    start = content_str.index("TARGET: ") + len("TARGET: ")
    end = content_str.index("\nGLOSSARY:", start)
    targets = json.loads(content_str[start:end])
    translations = {t["id"]: t["text"] for t in projected["translations"]}
    expected_ids = list(target["unit_ids"])
    missing_ids = sorted(t_id for t_id in expected_ids if t_id not in translations)
    if missing_ids:
        raise RunnerBlocked(f"BATCH_COVERAGE_INCOMPLETE:b{batch_index}:{missing_ids}")
    mapping = [
        {"id": t_id, "source": next(i["text"] for i in targets if i["id"] == t_id),
         "translation": translations[t_id]}
        for t_id in expected_ids
    ]
    coverage = {
        "schema": f"subtranslate.v238.e08_b{batch_index}_derived_coverage.v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "physical_attempt_id": attempt_id,
        "logical_batch_id": logical_batch_id(batch_index),
        "found_ids": expected_ids,
        "missing_ids": [],
        "expected_count": len(expected_ids),
        "found_count": len(expected_ids),
        "distinct_sources": len({m["source"] for m in mapping}),
        "distinct_translations": len({m["translation"] for m in mapping}),
        "mapping": mapping,
        "provenance": {"request_payload": str(payload_path), "response_body": str(attempt_dir / "response.body")},
    }
    coverage_path = attempt_dir / "derived_coverage.json"
    if coverage_path.exists():
        raise RunnerBlocked(f"BATCH_COVERAGE_ARTIFACT_ALREADY_EXISTS:b{batch_index}")
    fd = os.open(str(coverage_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(attempt_dir)

    state = load_json(PROJECT)
    if batch_recon_key(batch_index) in state:
        raise RunnerBlocked(f"BATCH_RECONCILIATION_OBJECT_ALREADY_EXISTS:b{batch_index}")
    reconciliation = {
        "execution": {
            "action_id": ACTION_ID,
            "executor_id": RUNNER_ID,
            "batch_index": batch_index,
            "operation_id": operation_id,
            "family_id": family,
            "episode_id": str(config["episode_id"]),
            "logical_batch_id": logical_batch_id(batch_index),
            "unit_ids": expected_ids,
            "unit_membership_sha256": target["unit_membership_sha256"],
            "request_payload_sha256": target["request_payload_sha256"],
            "response_sha256": response_sha,
            "physical_attempt_id": attempt_id,
            "logical_call_id": call.logical_call_id,
            "terminal_state": state_json.get("state"),
            "model_calls": 1,
            "http_posts": 1,
            "retries": 0,
            "model": MODEL,
            "policy": POLICY,
        },
        "coverage_formalized": {
            "artifact": str(coverage_path),
            "found_ids": expected_ids,
            "missing_ids": [],
        },
        "authorization_lineage": {
            "episode_authorization_key": AUTHORIZATION_KEY,
            "runner": "AUTO-03E-EPISODE-RANGE-RUNNER under episode authorization",
        },
        "future_side_effects_authorized": False,
        "next_batch_started": False,
        "canonical_reconciliation_required": False,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    state[batch_recon_key(batch_index)] = reconciliation
    done = batch_index + 1
    if done >= total:
        state["state"] = f"SUBTRANSLATE_V238_ZLS_S01E08_COMPLETE_BATCHES_0_{batch_index}_ALL_PARSED_VALID_ZERO_RETRY"
        state["status"] = "E08_MISSION_COMPLETE_ALL_BATCHES_PARSED_VALID_COVERAGE_FORMALIZED_ZERO_RETRY"
        state["next_action"] = "E08_ASSEMBLY_REQUIRED"
    else:
        state["state"] = f"SUBTRANSLATE_V238_ZLS_S01E08_IN_PROGRESS_BATCHES_0_{batch_index}_ALL_PARSED_VALID_ZERO_RETRY"
        state["status"] = f"E08_BATCHES_DONE_{done}_OF_{total}_ZERO_RETRY"
        state["next_action"] = decision_pointer(done)
    atomic_write_json(PROJECT, state)
    return {
        "batch_index": batch_index,
        "status": "COMPLETED",
        "operation_id": operation_id,
        "family_id": family,
        "physical_attempt_id": attempt_id,
        "logical_call_id": call.logical_call_id,
        "unit_ids": expected_ids,
        "coverage_artifact": str(coverage_path),
        "canonical_backup_root": str(backup_dir),
        "executor_backup_root": str(executor_backup),
    }


def validate_translation(value: Any, unit_ids: list[int], batch_index: int) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, dict) or set(value) != {"translations"}:
        raise RunnerBlocked(f"BATCH_RESPONSE_ROOT_SCHEMA_INVALID:b{batch_index}")
    rows = value.get("translations")
    if not isinstance(rows, list) or len(rows) != len(unit_ids):
        raise RunnerBlocked(f"BATCH_RESPONSE_CARDINALITY_INVALID:b{batch_index}")
    projected = []
    normalized = False
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int) \
                or not isinstance(row.get("text"), str) or not row["text"].strip():
            raise RunnerBlocked(f"BATCH_RESPONSE_ITEM_INVALID:b{batch_index}")
        extra = set(row) - {"id", "text"}
        normalized = normalized or bool(extra)
        projected.append({"id": row["id"], "text": row["text"]})
    if [r["id"] for r in projected] != unit_ids:
        raise RunnerBlocked(f"BATCH_RESPONSE_MEMBERSHIP_INVALID:b{batch_index}")
    return {"translations": projected}, normalized


def load_authorization(config_path: Path) -> tuple[dict[str, Any], dict[str, Any], int]:
    config = load_config(config_path)
    state = load_json(PROJECT)
    record = state.get(AUTHORIZATION_KEY)
    if not isinstance(record, dict):
        raise RunnerBlocked("EPISODE_AUTHORIZATION_ABSENT:run --mode authorize first")
    required = {
        "action_id": ACTION_ID,
        "runner_id": RUNNER_ID,
        "apply_permission_active": True,
        "pipeline_model_call_authorized": True,
        "external_transport_authorized": True,
        "runtime_write_authorized": True,
        "production_write_authorized": False,
        "automatic_retry_authorized": False,
        "max_retries_per_batch": 0,
        "max_client_calls_per_batch": 1,
        "max_http_posts_per_batch": 1,
        "model": MODEL,
        "model_digest": MODEL_DIGEST,
        "future_episodes_authorized": False,
        "episode_label": config.get("episode_label", "E08"),
        "episode_id": config["episode_id"],
        "series_id": config["series_id"],
        "source_sha256": config["source_sha256"],
        "engine_revision": config["engine_revision"],
    }
    for key, value in required.items():
        if record.get(key) != value:
            raise RunnerBlocked(f"EPISODE_AUTHORIZATION_CONTRACT_MISMATCH:{key}")
    if record.get("config_sha256") != sha256_bytes(config_path.read_bytes()):
        raise RunnerBlocked("EPISODE_AUTHORIZATION_CONFIG_CHANGED")
    fingerprint = toolchain_fingerprint(config_path)
    if record.get("execution_toolchain_fingerprint") != fingerprint:
        raise RunnerBlocked("EPISODE_AUTHORIZATION_TOOLCHAIN_CHANGED")
    total = int(record.get("expected_total_batches", -1))
    if total < 1:
        raise RunnerBlocked("EPISODE_AUTHORIZATION_TOTAL_INVALID")
    return config, record, total


def status(config_path: Path) -> dict[str, Any]:
    _, record, total = load_authorization(config_path)
    state = load_json(PROJECT)
    reconciled = [n for n in range(total) if batch_recon_key(n) in state]
    interrupted = [n for n in range(total)
                   if batch_auth_key(n) in state and batch_recon_key(n) not in state]
    next_expected = None
    for n in range(total):
        if batch_recon_key(n) not in state:
            next_expected = n
            break
    return {"status": "PASS", "mode": "STATUS_READ_ONLY", "side_effects_performed": False,
            "episode_label": record.get("episode_label"), "expected_total_batches": total,
            "reconciled_count": len(reconciled), "reconciled_batches": reconciled,
            "interrupted_batches": interrupted,
            "next_expected_batch": next_expected,
            "canonical_state": state.get("state"), "canonical_next_action": state.get("next_action")}


def execute(config_path: Path, max_batches: int | None = None) -> dict[str, Any]:
    config, auth_record, total = load_authorization(config_path)
    results: list[dict[str, Any]] = []
    stopped = False
    blocker: str | None = None
    ran_this_call = 0
    try:
        fresh_probe()
        for batch_index in range(total):
            state = load_json(PROJECT)
            classification = classify(state, batch_index)
            if classification == "SKIP":
                results.append({"batch_index": batch_index, "status": "SKIPPED_ALREADY_RECONCILED"})
                continue
            if max_batches is not None and ran_this_call >= max_batches:
                results.append({"batch_index": batch_index,
                                "status": "DEFERRED_MAX_BATCHES_LIMIT",
                                "note": f"resume with --mode execute (limit {max_batches}/call)"})
                continue
            result = execute_batch(config, config_path, batch_index, auth_record, total)
            results.append(result)
            ran_this_call += 1
            report = progress_report(results, stopped, blocker, total)
            persist_progress(report)
            if state.get("next_action") == "E08_ASSEMBLY_REQUIRED":
                break
    except Exception as exc:
        stopped = True
        blocker = f"{type(exc).__name__}:{exc}"
    report = progress_report(results, stopped, blocker, total)
    persist_progress(report)
    if stopped:
        print(json.dumps({"status": "FAIL_STOP", **report}, sort_keys=True, ensure_ascii=False))
        return {"status": "FAIL_STOP", "report": report}
    summary = {**report, "status": "PASS",
               "next_action": "E08_ASSEMBLY_REQUIRED"}
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    return summary


def recover_batch(config_path: Path, batch_index: int) -> dict[str, Any]:
    """Clean a pre-transport orphan batch: an authorization object exists but
    NO physical attempt was ever created (no calls/ entries in the family
    directory).  Backs up both authority documents, removes the orphan
    per-batch authorization object and the materialized payload, restores the
    decision pointer so --mode execute can run the batch from scratch."""
    config = load_config(config_path)
    state = load_json(PROJECT)
    if batch_recon_key(batch_index) in state:
        raise RunnerBlocked(f"BATCH_RECOVER_ALREADY_RECONCILED:b{batch_index}")
    if batch_auth_key(batch_index) not in state:
        raise RunnerBlocked(f"BATCH_RECOVER_NOT_INTERRUPTED:b{batch_index}")
    if state.get("next_action") != authorized_pointer(batch_index):
        raise RunnerBlocked(
            f"BATCH_RECOVER_POINTER_UNEXPECTED:b{batch_index}:{state.get('next_action')}")
    auth_record = state[batch_auth_key(batch_index)]
    family = str(auth_record.get("family_id") or family_id_for(config, batch_index))
    family_dir = AUTHORITY_ROOT / "runtime-evidence" / family
    calls_dir = family_dir / "calls"
    if calls_dir.exists() and any(calls_dir.iterdir()):
        raise RunnerBlocked(
            f"BATCH_RECOVER_ATTEMPTS_PRESENT:b{batch_index}:manual assessment required")
    payload_path = Path(str(auth_record.get("request_payload_path") or ""))
    before_project_bytes = PROJECT.read_bytes()
    before_handoff = HANDOFF.read_bytes()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_PARENT / f"subtranslate-e08-b{batch_index}-recover-{stamp}"
    manifest_files = backup_canonical(backup_dir)

    after = json.loads(before_project_bytes)
    del after[batch_auth_key(batch_index)]
    after["next_action"] = decision_pointer(batch_index)
    atomic_write_json(PROJECT, after)

    removed_payload = False
    if payload_path.is_file():
        payload_path.unlink()
        removed_payload = True
    planning_root = payload_path.parent
    try:
        planning_root.rmdir()
        planning_root.parent.rmdir()
    except OSError:
        pass

    title = f"AUTO-03E-E08-B{batch_index}-PRETRANSPORT-ORPHAN-RECOVERY-R1"
    summary = (f"Lote {batch_index} interrompido pre-transporte (bug de toolchain corrigido em "
               "commit 071ca18+); nenhum attempt fisico existia; objeto de autorizacao orfao e "
               "payload materializados removidos com backup; lote pronto para re-execucao.")
    after_handoff = write_handoff_addendum(before_handoff, title, summary,
                                           digest(before_project_bytes), decision_pointer(batch_index))
    handoff_info = regular(HANDOFF)
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=f".{HANDOFF.name}.auto03e-", dir=str(HANDOFF.parent))
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(os.open(tmp_name, os.O_WRONLY), "wb") as stream:
            stream.write(after_handoff)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, HANDOFF)
        fsync_dir(HANDOFF.parent)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"status": "PASS", "transition": "recover-batch", "batch_index": batch_index,
            "removed_orphan_authorization": True, "removed_materialized_payload": removed_payload,
            "canonical_backup_hashes": manifest_files, "backup_root": str(backup_dir),
            "next_action": decision_pointer(batch_index)}


def reconcile_existing_batch(config_path: Path, batch_index: int) -> dict[str, Any]:
    """Retroactive canonical reconciliation for a batch whose model call and
    derived_coverage.json are already complete on disk but whose canonical
    reconciliation object was never written (runner died between the durable
    call and the documentary write).  Also removes an orphaned ledger lock."""
    config = load_config(config_path)
    state = load_json(PROJECT)
    if batch_recon_key(batch_index) in state:
        raise RunnerBlocked(f"BATCH_RECONCILE_ALREADY_RECONCILED:b{batch_index}")
    if batch_auth_key(batch_index) not in state:
        raise RunnerBlocked(f"BATCH_RECONCILE_NOT_INTERRUPTED:b{batch_index}")
    auth_record = state[batch_auth_key(batch_index)]
    family = str(auth_record.get("family_id") or family_id_for(config, batch_index))
    family_dir = AUTHORITY_ROOT / "runtime-evidence" / family
    calls_dir = family_dir / "calls"
    if not calls_dir.is_dir():
        raise RunnerBlocked(f"BATCH_RECONCILE_NO_ATTEMPTS:b{batch_index}:use recover-batch instead")
    attempts = sorted(p for p in calls_dir.iterdir() if p.is_dir())
    if len(attempts) != 1:
        raise RunnerBlocked(
            f"BATCH_RECONCILE_ATTEMPT_COUNT_UNEXPECTED:b{batch_index}:{len(attempts)}")
    attempt_dir = attempts[0]
    attempt_id = attempt_dir.name
    state_json = json.loads((attempt_dir / "state.json").read_text(encoding="utf-8"))
    terminal = state_json.get("state")
    if terminal not in ("PARSED_VALID", "DERIVED_PARSED_VALID"):
        raise RunnerBlocked(
            f"BATCH_RECONCILE_ATTEMPT_NOT_TERMINAL:b{batch_index}:{terminal}")
    coverage_path = attempt_dir / "derived_coverage.json"
    if not coverage_path.is_file():
        raise RunnerBlocked(f"BATCH_RECONCILE_COVERAGE_MISSING:b{batch_index}")
    response_meta = json.loads((attempt_dir / "response_metadata.json").read_text(encoding="utf-8"))
    response_sha = str(response_meta.get("response_sha256") or "")

    before_project_bytes = PROJECT.read_bytes()
    before_handoff = HANDOFF.read_bytes()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_PARENT / f"subtranslate-e08-b{batch_index}-reconcile-{stamp}"
    manifest_files = backup_canonical(backup_dir)

    reconciliation = {
        "execution": {
            "action_id": ACTION_ID,
            "executor_id": RUNNER_ID,
            "batch_index": batch_index,
            "operation_id": str(state_json.get("operation_ids", [""])[0]) if state_json.get("operation_ids") else auth_record.get("operation_id", ""),
            "family_id": family,
            "episode_id": str(config["episode_id"]),
            "logical_batch_id": logical_batch_id(batch_index),
            "unit_ids": list(auth_record["unit_ids"]),
            "unit_membership_sha256": auth_record["unit_membership_sha256"],
            "request_payload_sha256": auth_record["request_payload_sha256"],
            "response_sha256": response_sha,
            "physical_attempt_id": attempt_id,
            "logical_call_id": str(state_json.get("logical_call_id") or ""),
            "terminal_state": terminal,
            "model_calls": 1,
            "http_posts": 1,
            "retries": 0,
            "model": MODEL,
            "policy": POLICY,
        },
        "coverage_formalized": {
            "artifact": str(coverage_path),
            "found_ids": expected_from_coverage(coverage_path),
            "missing_ids": [],
        },
        "authorization_lineage": {
            "episode_authorization_key": AUTHORIZATION_KEY,
            "runner": "AUTO-03E-EPISODE-RANGE-RUNNER retroactive reconciliation",
        },
        "future_side_effects_authorized": False,
        "next_batch_started": False,
        "canonical_reconciliation_required": False,
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    after = json.loads(before_project_bytes)
    after[batch_recon_key(batch_index)] = reconciliation

    orphan_lock = family_dir / "episode-budget.json.lock"
    removed_lock = False
    if orphan_lock.is_file():
        orphan_lock.unlink()
        removed_lock = True

    total = int(load_authorization_total(before))
    done_count = sum(1 for n in range(total)
                     if f"auto03e_e08_b{n}_post_execution_reconciliation_r1" in after)
    pending = [n for n in range(total)
               if f"auto03e_e08_b{n}_post_execution_reconciliation_r1" not in after]
    if not pending:
        after["state"] = f"SUBTRANSLATE_V238_ZLS_S01E08_COMPLETE_BATCHES_0_{total - 1}_ALL_PARSED_VALID_ZERO_RETRY"
        after["status"] = "E08_MISSION_COMPLETE_ALL_BATCHES_PARSED_VALID_COVERAGE_FORMALIZED_ZERO_RETRY"
        after["next_action"] = "E08_ASSEMBLY_REQUIRED"
    else:
        after["state"] = f"SUBTRANSLATE_V238_ZLS_S01E08_IN_PROGRESS_BATCHES_0_{max(pending) - 1}_ALL_PARSED_VALID_ZERO_RETRY"
        after["status"] = f"E08_BATCHES_DONE_{done_count}_OF_{total}_ZERO_RETRY"
        after["next_action"] = decision_pointer(min(pending))

    after_handoff = write_handoff_addendum(
        before_handoff,
        f"AUTO-03E-E08-B{batch_index}-RETROACTIVE-RECONCILIATION-R1",
        f"Lote {batch_index} executado (PARSED_VALID, 1 call, 0 retry) com reconciliacao "
        f"canonica aplicada retroativamente apos interrupcao do runner."
        + (" Lock de ledger orfao removido." if removed_lock else ""),
        digest(before_project_bytes), after["next_action"])
    publish_both(before_project_bytes, after_project_bytes(after), before_handoff, after_handoff, backup_dir)
    return {"status": "PASS", "transition": "reconcile-batch", "batch_index": batch_index,
            "terminal_state": terminal, "removed_orphan_lock": removed_lock,
            "canonical_backup_hashes": manifest_files, "backup_root": str(backup_dir),
            "next_action": after["next_action"]}


def expected_from_coverage(coverage_path: Path) -> list[int]:
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    return list(coverage.get("found_ids", []))


def after_project_bytes(after: dict[str, Any]) -> bytes:
    return (json.dumps(after, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def load_authorization_total(before: dict[str, Any]) -> int:
    record = before.get(AUTHORIZATION_KEY)
    if not isinstance(record, dict):
        raise RunnerBlocked("EPISODE_AUTHORIZATION_ABSENT_FOR_TOTAL")
    total = int(record.get("expected_total_batches", -1))
    if total < 1:
        raise RunnerBlocked("EPISODE_AUTHORIZATION_TOTAL_INVALID")
    return total


def progress_report(results: list[dict[str, Any]], stopped: bool, blocker: str | None, total: int) -> dict[str, Any]:
    completed = [r for r in results if r["status"] == "COMPLETED"]
    skipped = [r for r in results if r["status"] == "SKIPPED_ALREADY_RECONCILED"]
    return {
        "episode_label": "E08",
        "expected_total_batches": total,
        "batches_completed": [r["batch_index"] for r in completed],
        "batches_skipped_resumed": [r["batch_index"] for r in skipped],
        "completed_count": len(completed),
        "stopped_early": stopped,
        "blocker": blocker,
        "results": results,
    }


def persist_progress(report: dict[str, Any]) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(f"/tmp/opencode/e08_episode_report_{stamp}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"progress report persisted: {path}", file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG))
    parser.add_argument("--mode", required=True,
                        choices=("authorize", "status", "execute", "recover-batch", "reconcile-batch"))
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("--max-batches", type=int, default=None,
                        help="execute at most K pending batches, then exit cleanly")
    args = parser.parse_args(argv)
    # Resolve BEFORE any fingerprint derivation so the config path component of
    # the toolchain manifest is invocation-independent (absolute == relative).
    config_path = Path(args.config).resolve()
    try:
        if args.mode == "authorize":
            print(json.dumps(authorize(config_path), sort_keys=True, ensure_ascii=False))
            return 0
        if args.mode == "status":
            print(json.dumps(status(config_path), sort_keys=True, ensure_ascii=False))
            return 0
        if args.mode == "recover-batch":
            if args.batch is None:
                raise RunnerBlocked("RECOVER_REQUIRES_BATCH")
            print(json.dumps(recover_batch(config_path, args.batch), sort_keys=True, ensure_ascii=False))
            return 0
        if args.mode == "reconcile-batch":
            if args.batch is None:
                raise RunnerBlocked("RECONCILE_REQUIRES_BATCH")
            print(json.dumps(reconcile_existing_batch(config_path, args.batch), sort_keys=True, ensure_ascii=False))
            return 0
        result = execute(config_path, max_batches=args.max_batches)
        return 0 if result.get("status") == "PASS" else 1
        result = execute(config_path)
        return 0 if result.get("status") == "PASS" else 1
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "action_id": ACTION_ID,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
