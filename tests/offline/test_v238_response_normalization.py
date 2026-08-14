import json
import tempfile
import unittest
from pathlib import Path

from v238_per_call_durability import DurableCallFault, DurableV226Call
from v238_response_normalization import NormalizationRejected, POLICY, project_extra_property_response


class ResponseNormalizationTests(unittest.TestCase):
    def value(self, *, extra=None, ids=(1, 2, 3, 4, 5, 6, 7, 8), root_extra=False, empty_id=None, duplicate=False):
        rows = []
        for item_id in ids:
            row = {"id": item_id, "text": "ok"}
            if extra is not None and item_id == ids[-1]:
                row.update(extra)
            if empty_id == item_id:
                row["text"] = ""
            rows.append(row)
        if duplicate:
            rows[-1]["id"] = rows[-2]["id"]
        value = {"translations": rows}
        if root_extra:
            value["extra"] = True
        return value

    def test_r4a_single_item_extra_property_projects_without_text_or_id_change(self):
        value = self.value(extra={"context_note": "private"})
        projected, audit = project_extra_property_response(value, range(1, 9))
        self.assertEqual(len(projected["translations"]), 8)
        self.assertEqual(projected["translations"][-1], {"id": 8, "text": "ok"})
        self.assertEqual(audit["policy"], POLICY)
        self.assertEqual(audit["raw_schema_status"], "INVALID_EXTRA_PROPERTY")
        self.assertEqual(audit["derived_schema_status"], "VALID_AFTER_DETERMINISTIC_PROJECTION")
        self.assertEqual(audit["extra_property_count"], 1)

    def test_root_extra_fails_closed(self):
        with self.assertRaises(NormalizationRejected):
            project_extra_property_response(self.value(root_extra=True), range(1, 9))

    def test_two_offending_items_fail_closed(self):
        value = self.value(extra={"context_note": "private"})
        value["translations"][0]["other"] = True
        with self.assertRaises(NormalizationRejected):
            project_extra_property_response(value, range(1, 9))

    def test_missing_duplicate_unknown_and_cardinality_fail_closed(self):
        cases = [
            self.value(ids=(1, 2, 3, 4, 5, 6, 7)),
            self.value(duplicate=True),
            self.value(ids=(1, 2, 3, 4, 5, 6, 7, 99)),
            self.value(ids=(1, 2, 3)),
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(NormalizationRejected):
                    project_extra_property_response(value, range(1, 9))

    def test_empty_text_and_wrong_types_fail_closed(self):
        empty = self.value(empty_id=8, extra={"context_note": "private"})
        with self.assertRaises(NormalizationRejected):
            project_extra_property_response(empty, range(1, 9))
        wrong = self.value(extra={"context_note": "private"})
        wrong["translations"][-1]["id"] = "8"
        with self.assertRaises(NormalizationRejected):
            project_extra_property_response(wrong, range(1, 9))

    def test_projection_is_idempotent_and_hash_stable(self):
        value = self.value(extra={"context_note": "private"})
        first, audit_first = project_extra_property_response(value, range(1, 9))
        second, audit_second = project_extra_property_response(first, range(1, 9))
        self.assertEqual(first, second)
        self.assertEqual(audit_first["normalized_response_sha256"], audit_second["normalized_response_sha256"])

    def context(self, root: Path, **overrides):
        data = {
            "operation_id": "NORMALIZATION_TEST_OPERATION",
            "episode_id": 79,
            "anime_series_id": 3,
            "source_sha256": "a" * 64,
            "model": "qwen3.5:9b",
            "model_digest": "b" * 64,
            "durable_call_root": str(root),
            "episode_budget_ledger_path": str(root / "family" / "episode-budget.json"),
            "episode_family_root": str(root / "family"),
            "episode_family_id": "NORMALIZATION_TEST_FAMILY",
            "pipeline_id": "v2_3_8",
            "stage_id": "FULL_TRANSLATION_V238",
            "prompt_schema_hash": "c" * 64,
            "glossary_hash": "d" * 64,
            "configuration_hash": "e" * 64,
            "candidate_execution_contract": "normalization-test",
            "episode_budget_limits": {"planned_initial_calls": 1, "retry_reserve": 1, "physical_ceiling": 2},
            "candidate_commit": "candidate-test",
        }
        data.update(overrides)
        return data

    def test_derived_state_is_atomic_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            metadata = {"attempt_type": "INITIAL", "logical_batch_id": "batch-0", "attempt_ordinal": 1, "parent_attempt_id": None, "unit_membership_sha256": "membership"}
            call = DurableV226Call(context, {"payload": 1}, metadata)
            call.reserve(); call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner)
                call.begin_transport(); call.record_response(b'{"translations":[]}', status_code=200)
            call.mark_parsed(valid=False, error="extra property")
            value, audit = project_extra_property_response(self.value(extra={"context_note": "private"}), range(1, 9))
            with self.assertRaises(DurableCallFault):
                DurableV226Call(self.context(root, durability_fault_point="after_derived_manifest"), {"payload": 1}, metadata).record_derived_normalization(value, audit)
            # The new process sees the durable manifest and promotes locally;
            # no transport is involved and the original response remains raw.
            resumed = DurableV226Call(self.context(root), {"payload": 1}, metadata)
            resumed.record_derived_normalization(value, audit)
            resumed.mark_derived_parsed_valid()
            self.assertEqual(resumed.state(), "DERIVED_PARSED_VALID")
            self.assertEqual(json.loads(resumed.load_derived_response())["translations"][-1]["id"], 8)
            self.assertEqual(resumed.budget_ledger.snapshot()["physical_consumed"], 1)

    def test_crash_before_derived_manifest_rebuilds_without_transport(self):
        for fault_point in ("before_derived_manifest", "after_derived_response"):
            with self.subTest(fault_point=fault_point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                context = self.context(root)
                metadata = {"attempt_type": "INITIAL", "logical_batch_id": "batch-0", "attempt_ordinal": 1, "parent_attempt_id": None, "unit_membership_sha256": "membership"}
                call = DurableV226Call(context, {"payload": 1}, metadata)
                call.reserve(); call.prepare_request()
                with call.exclusive_transport_claim() as owner:
                    self.assertTrue(owner)
                    call.begin_transport(); call.record_response(b'{"translations":[]}', status_code=200)
                call.mark_parsed(valid=False, error="extra property")
                value, audit = project_extra_property_response(self.value(extra={"context_note": "private"}), range(1, 9))
                with self.assertRaises(DurableCallFault):
                    DurableV226Call(self.context(root, durability_fault_point=fault_point), {"payload": 1}, metadata).record_derived_normalization(value, audit)
                resumed = DurableV226Call(self.context(root), {"payload": 1}, metadata)
                resumed.record_derived_normalization(value, audit)
                resumed.mark_derived_parsed_valid()
                self.assertEqual(resumed.state(), "DERIVED_PARSED_VALID")
                self.assertEqual(resumed.budget_ledger.snapshot()["physical_consumed"], 1)

    def test_canonical_client_replays_invalid_capture_without_post(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {"attempt_type": "INITIAL", "logical_batch_id": "batch-1", "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 0}
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\",\\"context_note\\":\\"x\\"}]}"}}'
                def raise_for_status(self):
                    return None
                def json(self):
                    return json.loads(self.content)

            posts = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: posts.append(1) or Response()):
                first = Client(config, [], {}, model="qwen3.5:9b")
                found, issues, _ = first.call([unit], {1: event}, contexts)
                self.assertEqual(posts, [1])
                self.assertTrue(issues)
                config.durable_context = {**context, "response_normalization_policy": POLICY}
                second = Client(config, [], {}, model="qwen3.5:9b")
                found_again, issues_again, observation = second.call([unit], {1: event}, contexts)
            self.assertEqual(posts, [1])
            self.assertEqual(found_again[1]["text"], "oi")
            self.assertEqual(issues_again, [])
            self.assertEqual(observation["normalization_status"], "DERIVED_PARSED_VALID")
            self.assertEqual(observation["model_call_delta"], 0)

    def test_fresh_live_opt_in_marks_raw_invalid_then_promotes_valid_derived(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {**self.context(root), "response_normalization_policy": POLICY}
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {"attempt_type": "INITIAL", "logical_batch_id": "live-batch", "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 0}
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\",\\"context_note\\":\\"x\\"}]}"}}'

            posts = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: posts.append(1) or Response()):
                found, issues, observation = Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)

            self.assertEqual(posts, [1])
            self.assertEqual(found[1]["text"], "oi")
            self.assertEqual(issues, [])
            self.assertEqual(observation["raw_durable_state"], "PARSED_INVALID")
            self.assertEqual(observation["derived_recorded_state"], "DERIVED_NORMALIZATION_RECORDED")
            self.assertEqual(observation["derived_state"], "DERIVED_PARSED_VALID")
            self.assertEqual(observation["structural_issues"], [])
            self.assertEqual(observation["retry_delta"], 0)

    def test_live_non_extra_error_does_not_create_derived_manifest(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {**self.context(root), "response_normalization_policy": POLICY}
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {"attempt_type": "INITIAL", "logical_batch_id": "bad-live-batch", "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 0}
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"\\",\\"context_note\\":\\"x\\"}]}"}}'

            with patch("pipeline_v2_1_3.requests.post", return_value=Response()):
                with self.assertRaises(NormalizationRejected):
                    Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)

            call_dir = root / "calls"
            derived = list(root.rglob("derived_normalization.json"))
            self.assertEqual(derived, [])

    def test_recorded_state_restart_promotes_through_canonical_client(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {**self.context(root), "response_normalization_policy": POLICY, "durability_fault_point": "after_derived_recorded"}
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {"attempt_type": "INITIAL", "logical_batch_id": "recorded-restart", "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 0}
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\",\\"context_note\\":\\"x\\"}]}"}}'

            posts = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: posts.append(1) or Response()):
                with self.assertRaises(DurableCallFault):
                    Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)
                context.pop("durability_fault_point")
                config.durable_context = context
                found, issues, observation = Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)

            self.assertEqual(posts, [1])
            self.assertEqual(found[1]["text"], "oi")
            self.assertEqual(issues, [])
            self.assertEqual(observation["reused_durable_response"], True)
            self.assertEqual(observation["normalization_status"], "DERIVED_PARSED_VALID_REUSED")
            self.assertEqual(observation["durable_state"], "DERIVED_PARSED_VALID")

    def test_recorded_invalid_derivation_fails_closed_without_projection_or_transport(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {**self.context(root), "response_normalization_policy": POLICY, "durability_fault_point": "after_derived_recorded"}
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {"attempt_type": "INITIAL", "logical_batch_id": "invalid-derived", "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 0}
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}
            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\",\\"context_note\\":\\"x\\"}]}"}}'

            posts = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: posts.append(1) or Response()):
                with self.assertRaises(DurableCallFault):
                    Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)
            call_dir = next(root.rglob("derived_response.json")).parent
            invalid_derived = {"translations": [{"id": 1}]}
            from v238_per_call_durability import canonical_bytes, sha256_bytes
            (call_dir / "derived_response.json").write_bytes(canonical_bytes(invalid_derived))
            manifest = json.loads((call_dir / "derived_normalization.json").read_text())
            manifest["normalized_response_sha256"] = sha256_bytes(canonical_bytes(invalid_derived))
            (call_dir / "derived_normalization.json").write_text(json.dumps(manifest))
            context.pop("durability_fault_point")
            config.durable_context = context
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: posts.append(1)):
                with self.assertRaises(NormalizationRejected):
                    Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)
            self.assertEqual(posts, [1])
            state = json.loads((call_dir / "state.json").read_text())
            self.assertEqual(state["state"], "DERIVED_NORMALIZATION_RECORDED")

    def test_opt_in_exact_response_skips_projection(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {**self.context(root), "response_normalization_policy": POLICY}
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            config.durable_attempt_contract = {"attempt_type": "INITIAL", "logical_batch_id": "batch-exact", "attempt_ordinal": 1, "parent_attempt_id": None, "batch_index": 0}
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\"}]}"}}'
                def raise_for_status(self):
                    return None
                def json(self):
                    return json.loads(self.content)

            with patch("pipeline_v2_1_3.requests.post", return_value=Response()):
                found, issues, observation = Client(config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts)
            self.assertEqual(found[1]["text"], "oi")
            self.assertEqual(issues, [])
            self.assertEqual(observation["normalization_status"], "NOT_NEEDED")
            self.assertEqual(observation["dropped_property_count"], 0)


if __name__ == "__main__":
    unittest.main()
