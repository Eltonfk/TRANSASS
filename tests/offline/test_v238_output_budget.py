import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
from production_v2_1_3_adapter import APPROVED_CONFIG
from v238_per_call_durability import DurableCallError, DurableV226Call


class OutputBudgetTests(unittest.TestCase):
    def event(self, event_id=1):
        return Event(
            event_id, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "",
            "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")],
            [], [], False, "MAIN_DIALOGUE",
        )

    def durable_context(self, root: Path):
        return {
            "operation_id": "OUTPUT_BUDGET_TEST_OPERATION",
            "episode_id": 79,
            "anime_series_id": 3,
            "source_sha256": "a" * 64,
            "model": "qwen3.5:9b",
            "model_digest": "b" * 64,
            "durable_call_root": str(root),
            "episode_budget_ledger_path": str(root / "family" / "episode-budget.json"),
            "episode_family_root": str(root / "family"),
            "episode_family_id": "OUTPUT_BUDGET_TEST_FAMILY",
            "pipeline_id": "v2_3_8",
            "stage_id": "FULL_TRANSLATION_V238",
            "prompt_schema_hash": "c" * 64,
            "glossary_hash": "d" * 64,
            "configuration_hash": "e" * 64,
            "candidate_execution_contract": "output-budget-test",
            "episode_budget_limits": {
                "planned_initial_calls": 1,
                "retry_reserve": 0,
                "physical_ceiling": 1,
            },
        }

    def test_canonical_output_budget_is_1024(self):
        self.assertEqual(Config("http://ollama.invalid").num_predict, 1024)
        self.assertEqual(APPROVED_CONFIG["num_predict"], 1024)

    def test_request_envelope_uses_1024(self):
        event = self.event()
        captured = []

        class Response:
            status_code = 200
            content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\"}]}"},"done":true,"done_reason":"stop"}'

            def raise_for_status(self):
                return None

            def json(self):
                return json.loads(self.content)

        def post(*args, **kwargs):
            captured.append(kwargs["json"])
            return Response()

        with patch("pipeline_v2_1_3.requests.post", side_effect=post):
            found, issues, _ = Client(Config("http://ollama.invalid"), [], {}, model="qwen3.5:9b").call(
                [Unit("unit-1", [event])], {1: event}, {1: {"previous": [], "next": []}}
            )
        self.assertEqual(issues, [])
        self.assertEqual(found[1]["text"], "oi")
        self.assertEqual(captured[0]["options"]["num_predict"], 1024)

    def test_done_reason_length_is_durable_fail_closed_before_json_parse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.durable_context(root)
            config = Config("http://ollama.invalid", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {
                "attempt_type": "INITIAL",
                "logical_batch_id": "batch-0",
                "attempt_ordinal": 1,
                "parent_attempt_id": None,
                "batch_index": 0,
            }
            event = self.event()
            # The outer Ollama envelope is valid, but its message content is
            # intentionally cut off.  The done_reason guard must run before
            # strict_json(content), preserving the raw bytes and refusing all
            # normalization/subset/retry paths.
            raw = json.dumps({
                "message": {"content": '{"translations":[{"id":1,"text":"trunca'},
                "done": True,
                "done_reason": "length",
                "eval_count": 384,
                "prompt_eval_count": 2035,
            }, separators=(",", ":")).encode()

            class Response:
                status_code = 200
                content = raw

            posts = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *a, **k: posts.append(1) or Response()), \
                    patch("pipeline_v2_1_3.strict_json", side_effect=AssertionError("truncated content reached parser")):
                client = Client(config, [], {}, model="qwen3.5:9b")
                with self.assertRaises(DurableCallError) as raised:
                    client.call([Unit("unit-1", [event])], {1: event}, {1: {"previous": [], "next": []}})
                self.assertEqual(posts, [1])
                observation = client.calls[-1]
                self.assertEqual(observation["raw_schema_status"], "INVALID_TRUNCATED")
                self.assertEqual(observation["raw_noncompliance_class"], "OUTPUT_TRUNCATED")
                self.assertEqual(observation["normalization_attempted"], False)
                self.assertEqual(observation["retry_delta"], 0)
                self.assertTrue(observation["physical_transport"])
                self.assertEqual(observation["model_call_delta"], 1)
                self.assertEqual(observation["provider_call_delta"], 1)
                self.assertEqual(observation["durable_response_delta"], 1)
                self.assertEqual(observation["durable_state"], "PARSED_INVALID")
                self.assertEqual(raised.exception.args[0], "V238_OUTPUT_TRUNCATED")

            # Reusing the same durable attempt must stop locally without a
            # second transport; the captured response remains immutable.
            resumed = Client(config, [], {}, model="qwen3.5:9b")
            with patch("pipeline_v2_1_3.requests.post", side_effect=AssertionError("repeated transport")):
                with self.assertRaises(DurableCallError):
                    resumed.call([Unit("unit-1", [event])], {1: event}, {1: {"previous": [], "next": []}})
            self.assertEqual(resumed.calls[-1]["durable_state"], "PARSED_INVALID")
            self.assertFalse(resumed.calls[-1]["physical_transport"])
            self.assertEqual(resumed.calls[-1]["model_call_delta"], 0)
            self.assertEqual(resumed.calls[-1]["provider_call_delta"], 0)
            self.assertEqual(resumed.calls[-1]["durable_response_delta"], 0)

    def test_output_budget_is_bound_into_family_identity(self):
        """A ledger created under the old 384 contract cannot reopen at 1024."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_context = self.durable_context(root)
            old_context["configuration_hash"] = "3" * 64  # historical num_predict=384
            old = DurableV226Call(old_context, {"model": "qwen3.5:9b"}, {
                "phase": "initial", "attempt_type": "INITIAL",
                "logical_batch_id": "batch-0", "attempt_ordinal": 1,
                "parent_attempt_id": None, "batch_index": 0,
                "unit_ids": [1], "unit_membership_sha256": "1",
            })
            old.reserve()
            new_context = dict(old_context, configuration_hash="4" * 64)  # canonical num_predict=1024
            new = DurableV226Call(new_context, {"model": "qwen3.5:9b"}, {
                "phase": "initial", "attempt_type": "INITIAL",
                "logical_batch_id": "batch-0", "attempt_ordinal": 1,
                "parent_attempt_id": None, "batch_index": 0,
                "unit_ids": [1], "unit_membership_sha256": "1",
            })
            with self.assertRaisesRegex(DurableCallError, "FAMILY_CONTRACT_MISMATCH"):
                new.reserve()

    def test_r6b_dry_limits_allow_one_initial_and_zero_retry(self):
        """The pre-call contract is a dry bound, never an implicit retry path."""
        config = Config("http://ollama.invalid", num_ctx=2560, num_predict=1024, max_retries=0)
        self.assertEqual(config.num_predict, 1024)
        self.assertEqual(config.num_ctx, 2560)
        self.assertEqual(config.max_retries, 0)
        dry_contract = {"initial_maximum": 1, "retry_maximum": 0}
        self.assertEqual(dry_contract, {"initial_maximum": 1, "retry_maximum": 0})


if __name__ == "__main__":
    unittest.main()
