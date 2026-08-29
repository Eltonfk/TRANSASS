#!/usr/bin/env python3
"""AUTO-03D B5 preflight (read-only).

Determines whether the B5 batch contract can be materialized without UNKNOWN:
validates the canonical prestate, the B4 recovery closure, the absence of
B5-B7 evidence, the materialized B5 execution toolchain and the accounting,
then derives the B5 target facts from a fresh probe.

READY never authorizes execution: B5 remains NOT_STARTED_NOT_AUTHORIZED and
any real action requires a future separate HUMAN_GATE, a literal ``AUTORIZAR``
token and a fresh post-token probe.

This helper is deliberately action-specific.  It accepts no paths or content
and exposes only ``--plan`` (read-only).  There is no ``--apply`` surface.
It never invokes the B5 executor, a model, transport, retry, B4, B6 or B7.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
from pathlib import Path
from typing import Any

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
AUTHORITY_ROOT = Path("/home/palhacinho/codex-projects/anime-subtitle-translator-review")
PROBE_PATH = CANDIDATE_ROOT / ".opencode/tools/subtranslate_readonly_probe.py"

PRE_STATE = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_CALL_SUCCEEDED_POST_EXECUTION_AUDITED_B5_PREFLIGHT_READ_ONLY_REQUIRED"
PRE_DECISION = "B4_RECOVERY_CALL_POST_EXECUTION_AUDIT_PASS_CANONICAL_RECONCILED_B5_PREFLIGHT_READ_ONLY_REQUIRED"
PRE_NEXT = "B5_PREFLIGHT_READ_ONLY_REQUIRED"

B5_ACTION_ID = "B5_BATCH_EXECUTION"
B5_EXECUTOR_ID = "B5_BATCH_EXECUTOR_V1"
B5_MODEL = "qwen3.5:9b"
B5_MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
# Pipeline convention from src/subtranslate/pipeline_v2_1_3.py:
# logical_batch_id = f"v226-initial-{batch_index:06d}" -> batch 5.
B5_LOGICAL_BATCH_ID = "v226-initial-000005"
B5_POLICY = "V238_ITEM_EXTRA_PROPERTY_PROJECTION_V3_OPAQUE_CONTEXT_METADATA"
# Facts that the fresh probe does not expose (or that must be re-bound by the
# canonical authorization object) before any B5 execution.
REQUIRED_FROM_CANONICAL_AUTHORIZATION = (
    "operation_id",
    "family_id",
    "episode_id",
    "unit_ids",
    "request_payload_sha256",
    "unit_membership_sha256",
)


class Blocked(RuntimeError):
    pass


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Blocked(f"UNSAFE_REGULAR_FILE:{path}")
    return info


def fresh_probe() -> dict[str, Any]:
    regular(PROBE_PATH)
    spec = importlib.util.spec_from_file_location("auto03d_b5_fresh_probe", PROBE_PATH)
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


def validate(probe: dict[str, Any]) -> dict[str, Any]:
    canonical = probe.get("canonical", {})
    project_state = canonical.get("project_state", {})
    if (project_state.get("state"), project_state.get("latest_decision"),
            project_state.get("next_action")) != (PRE_STATE, PRE_DECISION, PRE_NEXT):
        raise Blocked("CANONICAL_POINTER_PRESTATE_MISMATCH")

    runtime = probe.get("runtime", {})
    budget = runtime.get("episode_budget", {})
    attempts = runtime.get("calls_attempts", {})
    if (budget.get("initial_consumed") != 1 or budget.get("retry_consumed") != 0 or
            budget.get("reservation_count") != 1):
        raise Blocked("B4_LEDGER_TERMINAL_FACTS_MISMATCH")
    reservations = budget.get("reservations")
    if not isinstance(reservations, list) or len(reservations) != 1:
        raise Blocked("B4_RESERVATION_CARDINALITY_MISMATCH")
    if reservations[0].get("state") != "PARSED_VALID":
        raise Blocked("B4_RESERVATION_NOT_TERMINAL")
    if attempts.get("attempt_count") != 1:
        raise Blocked("B4_ATTEMPT_EVIDENCE_MISMATCH")
    future = runtime.get("B5_B6_B7_evidence", {})
    if (future.get("present") is not False or future.get("b5_evidence_exists") is not False or
            future.get("b6_evidence_exists") is not False or future.get("b7_evidence_exists") is not False):
        raise Blocked("B5_B6_B7_NOT_ABSENT")

    toolchains = probe.get("execution_toolchains", {})
    toolchain = toolchains.get(B5_ACTION_ID)
    if not isinstance(toolchain, dict) or toolchain.get("executor_id") != B5_EXECUTOR_ID:
        raise Blocked("B5_TOOLCHAIN_UNAVAILABLE")
    fingerprint = toolchain.get("execution_toolchain_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or not toolchain.get("materialized"):
        raise Blocked("B5_TOOLCHAIN_NOT_MATERIALIZED")
    binding = toolchain.get("model_binding", {})
    guard = toolchain.get("transport_guard", {})
    if (binding.get("model_tag") != B5_MODEL or binding.get("model_digest") != B5_MODEL_DIGEST or
            guard.get("max_client_calls") != 1 or guard.get("max_http_posts") != 1 or
            guard.get("max_retries") != 0):
        raise Blocked("B5_TOOLCHAIN_BINDING_MISMATCH")

    reservation = reservations[0]
    model_tag = reservation.get("model_tag")
    model_digest = reservation.get("model_digest")
    if model_tag != B5_MODEL or model_digest != B5_MODEL_DIGEST:
        raise Blocked("B4_RESERVATION_MODEL_MISMATCH")

    accounting = probe.get("accounting", {})
    comparisons = accounting.get("comparisons", {})
    if any(value == "MISMATCH" for value in comparisons.values()):
        raise Blocked("B5_ACCOUNTING_MISMATCH")
    snapshot_fingerprint = probe.get("snapshot_fingerprint")
    if not isinstance(snapshot_fingerprint, str) or len(snapshot_fingerprint) != 64:
        raise Blocked("B5_SNAPSHOT_FINGERPRINT_UNBOUND")
    operation_id = project_state.get("current_operation")
    if not isinstance(operation_id, str) or not operation_id:
        raise Blocked("B5_OPERATION_ID_NOT_DERIVABLE")
    return {
        "snapshot_fingerprint": snapshot_fingerprint,
        "operation_id": operation_id,
        "logical_batch_id": B5_LOGICAL_BATCH_ID,
        "model_tag": model_tag,
        "model_digest": model_digest,
        "policy": B5_POLICY,
        "toolchain_fingerprint": fingerprint,
        "required_from_canonical_authorization": list(REQUIRED_FROM_CANONICAL_AUTHORIZATION),
        "accounting": accounting,
    }


def plan() -> dict[str, Any]:
    probe = fresh_probe()
    facts = validate(probe)
    return {
        "status": "READY",
        "mode": "PLAN_READ_ONLY",
        "action_id": B5_ACTION_ID,
        "executor_id": B5_EXECUTOR_ID,
        "side_effects_performed": False,
        "snapshot_fingerprint": facts["snapshot_fingerprint"],
        "target": {
            "operation_id": facts["operation_id"],
            "logical_batch_id": facts["logical_batch_id"],
            "model_tag": facts["model_tag"],
            "model_digest": facts["model_digest"],
            "policy": facts["policy"],
        },
        "required_from_canonical_authorization": facts["required_from_canonical_authorization"],
        "toolchain": {"action_id": B5_ACTION_ID, "executor_id": B5_EXECUTOR_ID,
                      "execution_toolchain_fingerprint": facts["toolchain_fingerprint"],
                      "materialized": True},
        "accounting": facts["accounting"],
        "b5_execution_authorized": False,
        "b6_execution_authorized": False,
        "b7_execution_authorized": False,
        "b4_reexecution": False,
        "model_call": False,
        "transport": False,
        "runtime_write": False,
        "next_gate": "B5_EXECUTION_AUTHORIZATION_REQUIRED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        result = plan()
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "mode": "PLAN_READ_ONLY",
                          "side_effects_performed": False,
                          "blocker": f"{type(exc).__name__}:{exc}"},
                         sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())