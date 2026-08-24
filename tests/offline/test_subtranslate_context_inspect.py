import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("context_inspect", ROOT / ".opencode/tools/subtranslate_context_inspect.py")
inspect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspect)


class CanonicalKeysTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "PROJECT_STATE.json"

    def tearDown(self):
        self.tmp.cleanup()

    def write_state(self, obj):
        self.state_path.write_text(json.dumps(obj), encoding="utf-8")

    def test_lists_targets_and_auto03_keys_sorted(self):
        self.write_state({
            "state": "x",
            "auto03d_b4_recovery_call_preflight_r2": {"a": 1},
            "auto03c_r4_closure_canonicalization_r1": {"b": 2},
            "auto03d_zeta_custom": True,
        })
        with mock.patch.object(inspect, "AUTHORITY_PROJECT_STATE", self.state_path):
            result = inspect.canonical_keys()
        self.assertEqual(result["top_level_key_count"], 4)
        self.assertEqual(
            result["auto03_top_level_keys"],
            [
                "auto03c_r4_closure_canonicalization_r1",
                "auto03d_b4_recovery_call_preflight_r2",
                "auto03d_zeta_custom",
            ],
        )
        self.assertTrue(result["target_keys_present"]["auto03d_b4_recovery_call_preflight_r2"])
        self.assertTrue(result["target_keys_present"]["auto03c_r4_closure_canonicalization_r1"])
        self.assertFalse(result["target_keys_present"]["auto03d_future_resend_decision_canonicalization_r1"])

    def test_sha256_matches_file_bytes(self):
        self.write_state({"k": 1})
        with mock.patch.object(inspect, "AUTHORITY_PROJECT_STATE", self.state_path):
            result = inspect.canonical_keys()
        self.assertEqual(result["project_state_sha256"], hashlib.sha256(self.state_path.read_bytes()).hexdigest())

    def test_invalid_json_fails_closed(self):
        self.state_path.write_text("{", encoding="utf-8")
        with mock.patch.object(inspect, "AUTHORITY_PROJECT_STATE", self.state_path):
            with self.assertRaises(inspect.InspectBlocked):
                inspect.canonical_keys()

    def test_non_object_root_fails_closed(self):
        self.state_path.write_text("[]", encoding="utf-8")
        with mock.patch.object(inspect, "AUTHORITY_PROJECT_STATE", self.state_path):
            with self.assertRaises(inspect.InspectBlocked):
                inspect.canonical_keys()


if __name__ == "__main__":
    unittest.main(verbosity=2)
