from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / ".opencode/tools/subtranslate_canonical_transition.py"
SPEC = importlib.util.spec_from_file_location("canonical_transition", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def probe() -> dict:
    return {
        "snapshot_fingerprint": "a" * 64,
        "execution_toolchains": {"B4_RECOVERY_CALL_EXECUTION": {
            "executor_id": module.EXECUTOR_ID,
            "execution_toolchain_fingerprint": "b" * 64,
            "materialized": True}},
        "runtime": {"episode_budget": {"initial_consumed": 1, "retry_consumed": 0},
                    "calls_attempts": {"attempt_count": 1}},
    }


class CanonicalTransitionTests(unittest.TestCase):
    def test_record_preflight_is_additive_and_disables_execution(self):
        before = {"state": module.PREFLIGHT_STATE, "latest_decision": "old",
                  "next_action": module.PREFLIGHT_NEXT, "history": {"keep": True}}
        with mock.patch.object(module, "call_plan", return_value={"status": "READY", "side_effects_performed": False}):
            after, _, _ = module.transition("record-preflight", before, probe())
        self.assertEqual(before["history"], {"keep": True})
        self.assertEqual(after["history"], before["history"])
        self.assertEqual(after["state"], module.AUTH_REQUIRED_STATE)
        self.assertEqual(after["next_action"], module.AUTH_REQUIRED_NEXT)
        self.assertFalse(after[module.PREFLIGHT_KEY]["execution_authorized"])

    def test_record_preflight_wrong_prestate_is_fail_closed(self):
        with mock.patch.object(module, "call_plan", return_value={"status": "READY", "side_effects_performed": False}):
            with self.assertRaisesRegex(module.TransitionBlocked, "PRESTATE_MISMATCH"):
                module.transition("record-preflight", {"state": "wrong", "next_action": "wrong"}, probe())

    def test_record_authorization_is_exactly_one_call_zero_retry(self):
        before = {"state": module.AUTH_REQUIRED_STATE, "next_action": module.AUTH_REQUIRED_NEXT}
        after, _, _ = module.transition("record-authorization", before, probe())
        auth = after[module.AUTHORIZATION_KEY]
        self.assertEqual(auth["max_client_calls"], 1)
        self.assertEqual(auth["max_http_posts"], 1)
        self.assertEqual(auth["max_retries"], 0)
        self.assertFalse(auth["production_write_authorized"])

    def test_record_post_execution_never_authorizes_b5_b7(self):
        before = {"state": module.AUTHORIZED_STATE, "next_action": module.AUTHORIZED_NEXT}
        after, _, _ = module.transition("record-post-execution", before, probe())
        observed = after[module.OBSERVATION_KEY]
        self.assertEqual(observed["attempt_count"], 1)
        self.assertEqual(observed["retry_consumed"], 0)
        self.assertFalse(observed["b5_authorized"])
        self.assertFalse(observed["b6_authorized"])
        self.assertFalse(observed["b7_authorized"])
        self.assertEqual(after["next_action"], module.POST_NEXT)

    def test_record_failure_consumes_authorization_without_retry(self):
        before = {"state": module.AUTHORIZED_STATE, "next_action": module.AUTHORIZED_NEXT}
        zero = probe()
        zero["runtime"] = {"episode_budget": {"initial_consumed": 0, "retry_consumed": 0, "reservations": []},
                           "calls_attempts": {"attempt_count": 0, "calls_dir_exists": False}}
        after, _, _ = module.transition("record-failure", before, zero)
        failure = after[module.FAILURE_KEY]
        self.assertTrue(failure["authorization_consumed"])
        self.assertFalse(failure["authorization_valid_for_reexecution"])
        self.assertFalse(failure["model_call_executed"])
        self.assertEqual(after["next_action"], module.FAILURE_NEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
