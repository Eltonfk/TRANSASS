import importlib.util
import json
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "b5_executor", ROOT / ".opencode/tools/subtranslate_b5_executor.py"
)
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)


class FakeDurableCall:
    last = None

    def __init__(self, context, payload, metadata):
        self.logical_call_id = "v226-logical-" + "a" * 32
        self.physical_attempt_id = "v226-attempt-" + "b" * 32
        self._state_value = "PLANNED"
        FakeDurableCall.last = self

    def prepare_request(self):
        self._state_value = "REQUEST_DURABLE"
        return {"state": self._state_value}

    @contextmanager
    def exclusive_transport_claim(self):
        yield True

    def begin_transport(self):
        self._state_value = "TRANSPORT_IN_PROGRESS"

    def record_response(self, raw, status_code):
        self._state_value = "RESPONSE_DURABLE"

    def mark_parsed(self, valid, error=None):
        self._state_value = "PARSED_VALID" if valid else "PARSED_INVALID"

    def record_derived_normalization(self, value, audit):
        self._state_value = "DERIVED_NORMALIZATION_RECORDED"

    def mark_derived_parsed_valid(self):
        self._state_value = "DERIVED_PARSED_VALID"

    def state(self):
        return self._state_value


class B5ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.authority = root / "authority"
        self.authority.mkdir(parents=True)
        self.payload_path = self.authority / "batch5/request_payload.json"
        self.payload_path.parent.mkdir(parents=True)
        schema = {"type": "object", "additionalProperties": False, "required": ["translations"],
                  "properties": {"translations": {"type": "array", "minItems": 8, "maxItems": 8,
                      "items": {"type": "object", "additionalProperties": False,
                                "required": ["id", "text"], "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}}}}}
        self.payload = {"format": schema, "keep_alive": "30m", "messages": [{"role": "user", "content": "fixed"}],
                        "model": executor.MODEL, "options": {"num_ctx": 4096, "num_predict": 1024, "temperature": 0.0},
                        "stream": False, "think": False}
        self.payload_path.write_bytes(executor.canonical_bytes(self.payload))
        self.project = self.authority / "PROJECT_STATE.json"
        self.project.write_text(json.dumps({"next_action": "none"}))
        self.backup_parent = root / "backups"
        self.backup_parent.mkdir()
        self.unit_ids = [50, 51, 52, 53, 54, 55, 56, 57]
        self.patches = mock.patch.multiple(
            executor,
            AUTHORITY_ROOT=self.authority,
            PROJECT_STATE=self.project,
            BACKUP_PARENT=self.backup_parent,
            BACKUP_ROOT=self.backup_parent / "execution",
        )
        self.patches.start()

    def tearDown(self):
        self.patches.stop()
        self.temp.cleanup()

    def authorize(self):
        record = {
            "action_id": executor.ACTION_ID,
            "executor_id": executor.EXECUTOR_ID,
            "apply_permission_active": True,
            "pipeline_model_call_authorized": True,
            "external_transport_authorized": True,
            "runtime_write_authorized": True,
            "production_write_authorized": False,
            "automatic_retry_authorized": False,
            "max_retries": 0,
            "max_client_calls": 1,
            "max_http_posts": 1,
            "model": executor.MODEL,
            "model_digest": executor.MODEL_DIGEST,
            "future_batches_authorized": False,
            "operation_id": "SUBTRANSLATE_V238_E07_R6C_BATCHES_1_7",
            "family_id": "V238_E07_R6C_B5",
            "episode_id": "79",
            "unit_ids": self.unit_ids,
            "unit_membership_sha256": "c" * 64,
            "request_payload_sha256": executor.digest(self.payload_path.read_bytes()),
            "request_payload_path": str(self.payload_path),
            "logical_batch_id": "v226-initial-000005",
            "execution_toolchain_fingerprint": "a" * 64,
            "snapshot_fingerprint": "b" * 64,
        }
        self.project.write_text(json.dumps({executor.AUTHORIZATION_KEY: record,
                                            "next_action": executor.AUTHORIZED_NEXT_ACTION}))

    def test_plan_is_read_only_and_ready(self):
        before = {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}
        result = executor.plan(require_authorization=False)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["side_effects_performed"])
        self.assertEqual(result["action_id"], "B5_BATCH_EXECUTION")
        self.assertEqual(result["executor_id"], "B5_BATCH_EXECUTOR_V1")
        self.assertFalse(result["b5_execution_authorized"])
        self.assertEqual(result["max_http_posts"], 1)
        self.assertEqual(result["max_retries"], 0)
        self.assertEqual(before, {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()})

    def test_plan_declares_required_from_authorization(self):
        result = executor.plan(require_authorization=False)
        self.assertEqual(result["required_from_authorization"], [
            "operation_id", "family_id", "episode_id", "unit_ids", "unit_membership_sha256",
            "request_payload_sha256", "request_payload_path", "logical_batch_id",
        ])

    def test_authorization_rejects_wrong_logical_batch(self):
        self.authorize()
        record = json.loads(self.project.read_text())[executor.AUTHORIZATION_KEY]
        record["logical_batch_id"] = "v226-initial-000006"
        self.project.write_text(json.dumps({executor.AUTHORIZATION_KEY: record,
                                            "next_action": executor.AUTHORIZED_NEXT_ACTION}))
        with self.assertRaisesRegex(executor.ExecutionBlocked, "B5_AUTHORIZATION_LOGICAL_BATCH_MISMATCH"):
            executor.authorization()

    def test_payload_options_mismatch_rejected(self):
        self.authorize()
        payload = dict(self.payload)
        payload["options"] = {"num_ctx": 2048, "num_predict": 512, "temperature": 0.5}
        self.payload_path.write_bytes(executor.canonical_bytes(payload))
        record = json.loads(self.project.read_text())[executor.AUTHORIZATION_KEY]
        record["request_payload_sha256"] = executor.digest(self.payload_path.read_bytes())
        self.project.write_text(json.dumps({executor.AUTHORIZATION_KEY: record,
                                            "next_action": executor.AUTHORIZED_NEXT_ACTION}))
        with mock.patch.object(executor, "current_toolchain_fingerprint", return_value="a" * 64):
            with self.assertRaisesRegex(executor.ExecutionBlocked, "B5_PAYLOAD_OPTIONS_MISMATCH"):
                executor.execute()

    def test_apply_without_canonical_authorization_stops_before_backup_or_network(self):
        with mock.patch.dict(sys.modules, {"requests": mock.Mock()}):
            with self.assertRaises(executor.ExecutionBlocked):
                executor.execute()
        self.assertFalse(executor.BACKUP_ROOT.exists())

    def test_authorization_rejects_missing_fact(self):
        self.authorize()
        record = json.loads(self.project.read_text())[executor.AUTHORIZATION_KEY]
        del record["unit_ids"]
        self.project.write_text(json.dumps({executor.AUTHORIZATION_KEY: record,
                                            "next_action": executor.AUTHORIZED_NEXT_ACTION}))
        with self.assertRaisesRegex(executor.ExecutionBlocked, "B5_AUTHORIZATION_MISSING_FACT"):
            executor.authorization()

    def test_apply_performs_exactly_one_post_and_zero_retry(self):
        self.authorize()
        content = json.dumps({"translations": [{"id": item, "text": f"pt-{item}"} for item in self.unit_ids]})
        response = mock.Mock(status_code=200, content=json.dumps({"message": {"content": content}}).encode())
        requests = types.SimpleNamespace(post=mock.Mock(return_value=response))
        durability = types.SimpleNamespace(DurableV226Call=FakeDurableCall)
        with mock.patch.dict(sys.modules, {"requests": requests, "v238_per_call_durability": durability}), \
             mock.patch.object(executor, "current_toolchain_fingerprint", return_value="a" * 64), \
             mock.patch.object(executor, "git_value", return_value="commit"):
            result = executor.execute()
        self.assertEqual(requests.post.call_count, 1)
        self.assertEqual(result["physical_transport_count"], 1)
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(result["final_state"], "PARSED_VALID")
        self.assertTrue((executor.BACKUP_ROOT / "manifest.json").is_file())

    def test_response_membership_or_extra_root_is_rejected(self):
        with self.assertRaises(executor.ExecutionBlocked):
            executor.validate_translation({"translations": [{"id": 999, "text": "x"}] * 8}, self.unit_ids)
        with self.assertRaises(executor.ExecutionBlocked):
            executor.validate_translation({"translations": [], "extra": True}, self.unit_ids)

    def test_real_durable_call_context_is_complete(self):
        """The B5 executor context must satisfy the real DurableV226Call."""
        self.authorize()
        auth = json.loads(self.project.read_text())[executor.AUTHORIZATION_KEY]
        sys.path.insert(0, str(ROOT / "src/subtranslate"))
        from v238_per_call_durability import DurableV226Call
        unit_ids = list(auth["unit_ids"])
        context = {
            "operation_id": auth["operation_id"],
            "anime_series_id": "3",
            "episode_id": auth["episode_id"],
            "episode_family_id": auth["family_id"],
            "source_sha256": executor.SOURCE_SHA256,
            "model": executor.MODEL,
            "model_digest": executor.MODEL_DIGEST,
            "durable_call_root": str(self.authority / "runtime-evidence" / auth["family_id"]),
            "episode_family_root": str(self.authority / "runtime-evidence" / auth["family_id"]),
            "episode_budget_ledger_path": str(self.authority / "runtime-evidence" / auth["family_id"] / "episode-budget.json"),
            "episode_budget_limits": {"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1,
                                      "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1},
            "pipeline_id": "v2_3_8",
            "stage_id": "FULL_TRANSLATION_V238",
            "candidate_execution_contract": executor.CANDIDATE_EXECUTION_CONTRACT,
            "configuration_hash": executor.CONFIGURATION_HASH,
            "glossary_hash": executor.GLOSSARY_HASH,
            "prompt_schema_hash": executor.PROMPT_SCHEMA_HASH,
            "response_normalization_policy": executor.POLICY,
            "candidate_commit": "commit",
            "transport_claim_timeout_seconds": 0.0,
        }
        metadata = {"phase": "batch", "attempt_type": "INITIAL", "logical_batch_id": auth["logical_batch_id"],
                    "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 5, "unit_ids": unit_ids,
                    "event_count": len(unit_ids), "unit_membership_sha256": auth["unit_membership_sha256"],
                    "model": executor.MODEL, "model_digest": executor.MODEL_DIGEST,
                    "timeout_seconds": executor.TIMEOUT_SECONDS}
        call = DurableV226Call(context, self.payload, metadata)
        self.assertTrue(call.logical_call_id.startswith("v226-logical-"))
        self.assertTrue(call.physical_attempt_id.startswith("v226-attempt-"))


if __name__ == "__main__":
    unittest.main()