#!/usr/bin/env python3
"""Fixed read-only summary for /subtranslate-fix and context hygiene.

The command accepts no paths and never writes. It prevents diagnostic agents
from substituting unrestricted ``python3 -c`` snippets merely to parse the
canonical JSON or report context size.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

CANDIDATE = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
PROBE_PATH = CANDIDATE / ".opencode/tools/subtranslate_readonly_probe.py"


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true", required=True)
    parser.parse_args()
    try:
        print(json.dumps(summary(probe()), sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "mode": "READ_ONLY_SUMMARY", "side_effects_performed": False,
                          "blocker": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
