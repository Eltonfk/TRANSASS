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
    "b4_executor", ROOT / ".opencode/tools/subtranslate_b4_recovery_call.py"
)
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)


class FakeDurableCall:
    last = None

    def __init__(self, context, payload, metadata):
        self.logical_call_id = executor.EXPECTED_LOGICAL_CALL_ID
        self.physical_attempt_id = executor.EXPECTED_PHYSICAL_ATTEMPT_ID
        self._state_value = "PLANNED"
        FakeDurableCall.last = self

    def prepare_request(self): self._state_value = "REQUEST_DURABLE"; return {"state": self._state_value}

    @contextmanager
    def exclusive_transport_claim(self): yield True

    def begin_transport(self): self._state_value = "TRANSPORT_IN_PROGRESS"
    def record_response(self, raw, status_code): self._state_value = "RESPONSE_DURABLE"
    def mark_parsed(self, valid, error=None): self._state_value = "PARSED_VALID" if valid else "PARSED_INVALID"
    def record_derived_normalization(self, value, audit): self._state_value = "DERIVED_NORMALIZATION_RECORDED"
    def mark_derived_parsed_valid(self): self._state_value = "DERIVED_PARSED_VALID"
    def state(self): return self._state_value


class B4ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.authority = root / "authority"
        self.runtime = self.authority / "runtime-evidence/B4"
        self.runtime.mkdir(parents=True)
        self.payload_path = self.authority / "historical/request_payload.json"
        self.payload_path.parent.mkdir(parents=True)
        schema = {"type": "object", "additionalProperties": False, "required": ["translations"],
                  "properties": {"translations": {"type": "array", "minItems": 8, "maxItems": 8,
                      "items": {"type": "object", "additionalProperties": False,
                                "required": ["id", "text"], "properties": {"id": {"type": "integer"}, "text": {"type": "string"}}}}}}
        self.payload = {"format": schema, "keep_alive": "30m", "messages": [{"role": "user", "content": "fixed"}],
                        "model": executor.MODEL, "options": {"num_ctx": 2560, "num_predict": 1024, "temperature": 0.0},
                        "stream": False, "think": False}
        self.payload_path.write_bytes(executor.canonical_bytes(self.payload))
        (self.runtime / "operation.json").write_text(json.dumps({"operation_id": executor.OPERATION_ID}))
        contract = {"anime_series_id": "3", "episode_id": executor.EPISODE_ID,
                    "source_sha256": executor.SOURCE_SHA256, "pipeline_id": "v2_3_8",
                    "stage_id": "FULL_TRANSLATION_V238", "model_tag": executor.MODEL,
                    "model_digest": executor.MODEL_DIGEST,
                    "prompt_schema_hash": "05911c99936b46be9cd4d8878407a8e8986351e086f3414bf297d880b4b46f63",
                    "glossary_hash": "64b0f676fed3bc495903f290b69a3290eebe2d52f8e726886a1ae7ea813b360e",
                    "configuration_hash": "0248eaff2384681e6bbf24e6e43eb4ca6cac123579fb68b7de42f3d5f5cba444",
                    "candidate_execution_contract": "d9dbaa8264992903c1c008461c5ae3ab4cc4fc84"}
        contract_sha = executor.digest(executor.canonical_bytes(contract))
        contract.update({"episode_family_id": executor.FAMILY_ID, "family_contract_sha256": contract_sha})
        self.contract = contract
        logical_identity = {"family_contract_sha256": contract_sha,
                            "logical_batch_id": executor.LOGICAL_BATCH_ID,
                            "unit_membership_sha256": executor.MEMBERSHIP_SHA256,
                            "model_tag": executor.MODEL, "model_digest": executor.MODEL_DIGEST}
        expected_logical = "v226-logical-" + executor.digest(executor.canonical_bytes(logical_identity))[:32]
        physical_identity = {"logical_call_id": expected_logical, "attempt_type": "INITIAL",
                             "attempt_ordinal": 1, "parent_attempt_id": None,
                             "request_payload_sha256": executor.digest(executor.canonical_bytes(self.payload))}
        expected_physical = "v226-attempt-" + executor.digest(executor.canonical_bytes(physical_identity))[:32]
        ledger = {"episode_id": executor.EPISODE_ID, "episode_family_id": executor.FAMILY_ID,
                  "planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1,
                  "initial_consumed": 0, "retry_consumed": 0, "reservations": [],
                  "family_contract_sha256": contract_sha, "family_contract": contract}
        (self.runtime / "episode-budget.json").write_text(json.dumps(ledger))
        (self.runtime / "episode-budget.json.lock").write_bytes(b"")
        self.project = self.authority / "PROJECT_STATE.json"
        self.project.write_text(json.dumps({"next_action": "none"}))
        self.backup_parent = root / "backups"; self.backup_parent.mkdir()
        self.patches = mock.patch.multiple(
            executor,
            AUTHORITY_ROOT=self.authority,
            PROJECT_STATE=self.project,
            RUNTIME_ROOT=self.runtime,
            LEDGER=self.runtime / "episode-budget.json",
            OPERATION=self.runtime / "operation.json",
            HISTORICAL_PAYLOAD=self.payload_path,
            BACKUP_PARENT=self.backup_parent,
            BACKUP_ROOT=self.backup_parent / "execution",
            PAYLOAD_SHA256=executor.digest(self.payload_path.read_bytes()),
            EXPECTED_LOGICAL_CALL_ID=expected_logical,
            EXPECTED_PHYSICAL_ATTEMPT_ID=expected_physical,
        )
        self.patches.start()

    def tearDown(self): self.patches.stop(); self.temp.cleanup()

    def authorize(self):
        record = {"action_id": executor.ACTION_ID, "executor_id": executor.EXECUTOR_ID,
                  "apply_permission_active": True, "pipeline_model_call_authorized": True,
                  "external_transport_authorized": True, "runtime_write_authorized": True,
                  "production_write_authorized": False, "automatic_retry_authorized": False,
                  "max_retries": 0, "max_client_calls": 1, "max_http_posts": 1,
                  "family_id": executor.FAMILY_ID, "operation_id": executor.OPERATION_ID,
                  "expected_request_payload_sha256": executor.PAYLOAD_SHA256,
                  "future_batches_authorized": False,
                  "execution_toolchain_fingerprint": "a" * 64, "snapshot_fingerprint": "b" * 64}
        self.project.write_text(json.dumps({executor.AUTHORIZATION_KEY: record,
                                            "next_action": executor.AUTHORIZED_NEXT_ACTION}))

    def test_plan_is_read_only_and_ready(self):
        before = {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}
        result = executor.plan(require_authorization=False)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["side_effects_performed"])
        self.assertEqual(before, {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()})

    def test_plan_rejects_missing_anime_series_id_before_backup(self):
        original = executor.build_context
        def missing():
            value = original(); value.pop("anime_series_id"); return value
        with mock.patch.object(executor, "build_context", side_effect=missing):
            with self.assertRaisesRegex(executor.ExecutionBlocked, "FAMILY_CONTRACT_INVALID"):
                executor.plan(require_authorization=False)
        self.assertFalse(executor.BACKUP_ROOT.exists())

    def test_apply_without_canonical_authorization_stops_before_backup_or_network(self):
        with mock.patch.dict(sys.modules, {"requests": mock.Mock()}):
            with self.assertRaises(executor.ExecutionBlocked): executor.execute()
        self.assertFalse(executor.BACKUP_ROOT.exists())

    def test_apply_performs_exactly_one_post_and_zero_retry(self):
        self.authorize()
        content = json.dumps({"translations": [{"id": item, "text": f"pt-{item}"} for item in executor.UNIT_IDS]})
        response = mock.Mock(status_code=200, content=json.dumps({"message": {"content": content}}).encode())
        requests = types.SimpleNamespace(post=mock.Mock(return_value=response))
        durability = types.SimpleNamespace(DurableV226Call=FakeDurableCall,
                                           _family_contract=lambda context: self.contract)
        with mock.patch.dict(sys.modules, {"requests": requests, "v238_per_call_durability": durability}), \
             mock.patch.object(executor, "git_value", return_value="commit"), \
             mock.patch.object(executor, "current_toolchain_fingerprint", return_value="a" * 64):
            result = executor.execute()
        self.assertEqual(requests.post.call_count, 1)
        self.assertEqual(result["physical_transport_count"], 1)
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(result["final_state"], "PARSED_VALID")
        self.assertTrue((executor.BACKUP_ROOT / "manifest.json").is_file())

    def test_response_membership_or_extra_root_is_rejected(self):
        with self.assertRaises(executor.ExecutionBlocked):
            executor.validate_translation({"translations": [{"id": 999, "text": "x"}] * 8})
        with self.assertRaises(executor.ExecutionBlocked):
            executor.validate_translation({"translations": [], "extra": True})


if __name__ == "__main__": unittest.main()
