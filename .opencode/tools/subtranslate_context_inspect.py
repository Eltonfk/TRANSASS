#!/usr/bin/env python3
"""Fixed read-only summary for /subtranslate-fix and context hygiene.

The command accepts no paths and never writes. It prevents diagnostic agents
from substituting unrestricted ``python3 -c`` snippets merely to parse the
canonical JSON or report context size.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

CANDIDATE = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
PROBE_PATH = CANDIDATE / ".opencode/tools/subtranslate_readonly_probe.py"
AUTHORITY_PROJECT_STATE = Path(
    "/home/palhacinho/codex-projects/anime-subtitle-translator-review/PROJECT_STATE.json"
)
TARGET_CANONICAL_KEYS = (
    "auto03d_b4_recovery_call_preflight_r2",
    "auto03d_b4_recovery_call_planning_decision_canonicalization_r1",
    "auto03d_b4_recovery_call_execution_observed_r2",
    "auto03d_b4_recovery_call_post_execution_canonical_reconciliation_r1",
    "auto03d_b4_recovery_call_route_correction_r1",
    "auto03d_future_resend_decision_canonicalization_r1",
    "auto03c_r4_closure_canonicalization_r1",
    "auto03c_canonical_reconciliation_preflight_r1",
)


class InspectBlocked(RuntimeError):
    pass


def probe() -> dict:
    spec = importlib.util.spec_from_file_location("subtranslate_fixed_probe", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise InspectBlocked("PROBE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.probe()
    if result.get("integrity", {}).get("side_effects_performed") is not False:
        raise InspectBlocked("READ_ONLY_INVARIANT_BROKEN")
    return result


def summary(result: dict) -> dict:
    canonical = result.get("canonical", {})
    project = canonical.get("project_state", {})
    runtime = result.get("runtime", {})
    hygiene = result.get("context_hygiene", {})
    return {
        "status": "PASS" if not result.get("blockers") and not result.get("unknowns") else "FAIL_STOP",
        "mode": "READ_ONLY_SUMMARY",
        "side_effects_performed": False,
        "snapshot_fingerprint": result.get("snapshot_fingerprint"),
        "canonical": {"state": project.get("state"), "latest_decision": project.get("latest_decision"),
                      "next_action": project.get("next_action"), "project_state_sha256": project.get("sha256"),
                      "handoff_sha256": canonical.get("handoff", {}).get("sha256")},
        "runtime": {"attempt_count": runtime.get("calls_attempts", {}).get("attempt_count"),
                    "initial_consumed": runtime.get("episode_budget", {}).get("initial_consumed"),
                    "retry_consumed": runtime.get("episode_budget", {}).get("retry_consumed"),
                    "b5_b6_b7": runtime.get("B5_B6_B7_evidence", {})},
        "context_hygiene": {"status": hygiene.get("status"), "review_reasons": hygiene.get("review_reasons", []),
                             "observed": hygiene.get("observed", {}), "hard_limit_exceeded": hygiene.get("hard_limit_exceeded", {}),
                             "recommended_next_gate": hygiene.get("recommended_next_gate"), "automatic_delete": False},
        "blockers": result.get("blockers", []), "unknowns": result.get("unknowns", []),
    }


def canonical_keys() -> dict:
    """Read-only top-level key inventory of the canonical PROJECT_STATE.json."""
    try:
        data = AUTHORITY_PROJECT_STATE.read_bytes()
        state = json.loads(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise InspectBlocked(f"CANONICAL_READ_FAILED:{type(exc).__name__}") from exc
    if not isinstance(state, dict):
        raise InspectBlocked("CANONICAL_ROOT_NOT_OBJECT")
    keys = sorted(state.keys())
    return {
        "project_state_sha256": hashlib.sha256(data).hexdigest(),
        "top_level_key_count": len(keys),
        "auto03_top_level_keys": [k for k in keys if k.startswith(("auto03c_", "auto03d_"))],
        "target_keys_present": {key: key in state for key in TARGET_CANONICAL_KEYS},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", required=True)
    parser.parse_args()
    try:
        payload = summary(probe())
        payload["canonical_keys"] = canonical_keys()
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "mode": "READ_ONLY_SUMMARY", "side_effects_performed": False,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
