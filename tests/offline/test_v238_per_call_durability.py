import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v238_per_call_durability import (
    DurableCallFault,
    DurableCallOutcomeUnknown,
    DurableCallError,
    DurableV226Call,
)


class PerCallDurabilityTests(unittest.TestCase):
    def context(self, root: Path, **overrides):
        value = {
            "operation_id": "SUBTRANSLATE_TEST_OPERATION",
            "episode_id": 79,
            "anime_series_id": 3,
            "source_sha256": "a" * 64,
            "model": "qwen3.5:9b",
            "model_digest": "b" * 64,
            "durable_call_root": str(root),
            "episode_budget_limits": {
                "planned_initial_calls": 2,
                "retry_reserve": 1,
                "physical_ceiling": 3,
            },
        }
        value.update(overrides)
        return value

    def payload(self, number=1):
        return {"model": "qwen3.5:9b", "messages": [{"role": "user", "content": f"unit-{number}"}], "stream": False}

    def metadata(self, number=1, phase="initial"):
        return {"phase": phase, "batch_index": number, "attempt_ordinal": number + 1, "unit_ids": [number], "unit_membership_sha256": str(number)}

    def complete(self, call, raw=None):
        call.reserve()
        call.prepare_request()
        call.begin_transport()
        call.record_response(raw or b'{"message":{"content":"{}"}}', status_code=200)
        call.mark_parsed(valid=True)

    def test_request_and_response_are_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve()
            call.prepare_request()
            self.assertEqual(call.state(), "REQUEST_DURABLE")
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(call.call_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((call.call_dir / "request_payload.json").stat().st_mode), 0o600)
            call.begin_transport()
            call.record_response(b'{"message":{"content":"{}"}}', status_code=200)
            self.assertEqual(call.state(), "RESPONSE_DURABLE")
            self.assertEqual(stat.S_IMODE((call.call_dir / "response.body").stat().st_mode), 0o600)
            call.mark_parsed(valid=True)
            self.assertEqual(call.state(), "PARSED_VALID")
            self.assertTrue((call.call_dir / "capture_state.json").is_file())
            snapshot = call.budget_ledger.snapshot()
            self.assertEqual(snapshot["physical_consumed"], 1)
            self.assertEqual(snapshot["successful_durable_responses"], 1)

    def test_response_durable_is_reused_without_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = DurableV226Call(self.context(root), self.payload(), self.metadata())
            first.reserve(); first.prepare_request(); first.begin_transport()
            first.record_response(b'{"message":{"content":"{}"}}', status_code=200)
            second = DurableV226Call(self.context(root), self.payload(), self.metadata())
            self.assertEqual(second.state(), "RESPONSE_DURABLE")
            self.assertEqual(second.load_raw(), b'{"message":{"content":"{}"}}')
            second.mark_parsed(valid=True)
            self.assertEqual(second.state(), "PARSED_VALID")
            self.assertEqual(second.budget_ledger.snapshot()["physical_consumed"], 1)

    def test_inflight_is_unknown_and_not_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = DurableV226Call(self.context(root), self.payload(), self.metadata())
            first.reserve(); first.prepare_request(); first.begin_transport()
            second = DurableV226Call(self.context(root), self.payload(), self.metadata())
            with self.assertRaises(DurableCallOutcomeUnknown):
                second.begin_transport()
            second.mark_unknown(RuntimeError("fault"))
            self.assertEqual(second.state(), "TRANSPORT_OUTCOME_UNKNOWN")
            with self.assertRaises(DurableCallOutcomeUnknown):
                second.begin_transport()
            self.assertEqual(second.budget_ledger.snapshot()["unknown_outcomes"], 1)

    def test_budget_overflow_is_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, episode_budget_limits={"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1})
            first = DurableV226Call(context, self.payload(1), self.metadata(1))
            first.reserve()
            second = DurableV226Call(context, self.payload(2), self.metadata(2))
            with self.assertRaises(DurableCallError):
                second.reserve()
            self.assertEqual(second.state(), "PLANNED")
            self.assertFalse(second.call_dir.exists())

    def test_fault_after_response_durable_can_resume_without_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fault_context = self.context(root, durability_fault_point="after_response_durable")
            first = DurableV226Call(fault_context, self.payload(), self.metadata())
            first.reserve(); first.prepare_request(); first.begin_transport()
            with self.assertRaises(DurableCallFault):
                first.record_response(b'{"message":{"content":"{}"}}', status_code=200)
            self.assertEqual(first.state(), "RESPONSE_DURABLE")
            resume = DurableV226Call(self.context(root), self.payload(), self.metadata())
            self.assertEqual(resume.load_raw(), b'{"message":{"content":"{}"}}')
            resume.mark_parsed(valid=True)
            self.assertEqual(resume.state(), "PARSED_VALID")

    def test_invalid_response_keeps_raw_and_marks_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve(); call.prepare_request(); call.begin_transport()
            raw = b"not-json"
            call.record_response(raw, status_code=200)
            call.mark_parsed(valid=False, error="JSON_DECODE")
            self.assertEqual(call.state(), "PARSED_INVALID")
            self.assertEqual(call.load_raw(), raw)
            self.assertTrue((call.call_dir / "parse_failure.json").is_file())
            self.assertEqual(call.budget_ledger.snapshot()["invalid_responses"], 1)

    def test_real_v226_client_path_reuses_durable_response_without_second_post(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
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

            calls = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: calls.append((args, kwargs)) or Response()):
                first = Client(config, [], {}, model="qwen3.5:9b")
                found, issues, _ = first.call([unit], {1: event}, contexts)
                second = Client(config, [], {}, model="qwen3.5:9b")
                found_again, issues_again, _ = second.call([unit], {1: event}, contexts)
            self.assertEqual(len(calls), 1)
            self.assertEqual(found[1]["text"], "oi")
            self.assertEqual(found_again[1]["text"], "oi")
            self.assertEqual(issues, [])
            self.assertEqual(issues_again, [])

    def test_durable_http_failure_is_terminal_until_reconciled(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context["model_digest"]
            config.durable_context = context
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 500
                content = b"server-error"

                def raise_for_status(self):
                    raise AssertionError("legacy raise_for_status must not be used")

            calls = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *args, **kwargs: calls.append(1) or Response()):
                client = Client(config, [], {}, model="qwen3.5:9b")
                with self.assertRaises(Exception) as raised:
                    client.call([unit], {1: event}, contexts)
            self.assertTrue(getattr(raised.exception, "durability_stop", False))
            self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
