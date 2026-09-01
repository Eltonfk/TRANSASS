import json
import multiprocessing
import os
import stat
import tempfile
import time
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
            "episode_budget_ledger_path": str(root / "family" / "episode-budget.json"),
            "episode_family_root": str(root / "family"),
            "episode_family_id": "TEST_SERIES_3_EPISODE_79",
            "pipeline_id": "v2_3_8",
            "stage_id": "FULL_TRANSLATION_V238",
            "prompt_schema_hash": "c" * 64,
            "glossary_hash": "d" * 64,
            "configuration_hash": "e" * 64,
            "candidate_execution_contract": "candidate-test-contract",
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

    def metadata(self, number=1, phase="initial", *, attempt_type="INITIAL", parent_attempt_id=None, attempt_ordinal=1, unit_ids=None):
        return {
            "phase": phase, "attempt_type": attempt_type,
            "logical_batch_id": f"batch-{number}", "batch_index": number,
            "attempt_ordinal": attempt_ordinal, "parent_attempt_id": parent_attempt_id,
            "unit_ids": list(unit_ids if unit_ids is not None else [number]), "unit_membership_sha256": str(number),
        }

    def install_attempt(self, config, number=1, *, attempt_type="INITIAL", parent_attempt_id=None, attempt_ordinal=1):
        config.durable_attempt_contract = {
            "attempt_type": attempt_type,
            "logical_batch_id": f"batch-{number}",
            "batch_index": number,
            "attempt_ordinal": attempt_ordinal,
            "parent_attempt_id": parent_attempt_id,
        }

    def complete(self, call, raw=None):
        call.reserve()
        call.prepare_request()
        with call.exclusive_transport_claim() as owner:
            self.assertTrue(owner)
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
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner)
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
            first.reserve(); first.prepare_request()
            with first.exclusive_transport_claim() as owner:
                self.assertTrue(owner)
                first.begin_transport()
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
            first.reserve(); first.prepare_request()
            with first.exclusive_transport_claim() as owner:
                self.assertTrue(owner)
                first.begin_transport()
            second = DurableV226Call(self.context(root), self.payload(), self.metadata())
            with self.assertRaises(DurableCallOutcomeUnknown):
                second.prepare_request()
            self.assertEqual(second.state(), "TRANSPORT_OUTCOME_UNKNOWN")
            with self.assertRaises(DurableCallOutcomeUnknown):
                second.prepare_request()
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
            first.reserve(); first.prepare_request()
            with self.assertRaises(DurableCallFault):
                with first.exclusive_transport_claim() as owner:
                    self.assertTrue(owner)
                    first.begin_transport()
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
            call.reserve(); call.prepare_request()
            raw = b"not-json"
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner)
                call.begin_transport()
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
            self.install_attempt(config)
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
            self.install_attempt(config)
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

    def test_main_phase_with_explicit_initial_type_does_not_consume_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(
                self.context(root), self.payload(),
                self.metadata(phase="main", attempt_type="INITIAL"),
            )
            call.reserve()
            snapshot = call.budget_ledger.snapshot()
            self.assertEqual(snapshot["initial_consumed"], 1)
            self.assertEqual(snapshot["retry_consumed"], 0)

    def test_persistent_retry_caps_block_before_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, episode_budget_limits={
                "planned_initial_calls": 1, "retry_reserve": 9, "physical_ceiling": 10,
                "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1,
            })
            initial = DurableV226Call(context, self.payload(1), self.metadata(1))
            initial.reserve()
            retry_one = DurableV226Call(context, self.payload(2), self.metadata(2, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[1]))
            retry_one.reserve()
            with self.assertRaisesRegex(DurableCallError, "PER_EVENT_RETRY_TRANSPORT_CAP"):
                DurableV226Call(context, self.payload(3), self.metadata(3, phase="retry_local", attempt_type="RETRY", parent_attempt_id=retry_one.physical_attempt_id, attempt_ordinal=3, unit_ids=[1])).reserve()
            retry_two = DurableV226Call(context, self.payload(4), self.metadata(4, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[2]))
            retry_two.reserve()
            with self.assertRaisesRegex(DurableCallError, "OPERATION_RETRY_TRANSPORT_CAP"):
                DurableV226Call(context, self.payload(5), self.metadata(5, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[3])).reserve()
            snapshot = initial.budget_ledger.snapshot()
            self.assertEqual(snapshot["retry_consumed"], 2)

    def test_retry_cap_is_operation_scoped_and_identity_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, episode_budget_limits={
                "planned_initial_calls": 1, "retry_reserve": 9, "physical_ceiling": 10,
                "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 2,
            })
            initial = DurableV226Call(context, self.payload(1), self.metadata(1, unit_ids=[1]))
            initial.reserve()
            retry_a1 = DurableV226Call(context, self.payload(2), self.metadata(2, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[1]))
            retry_a1.reserve()
            retry_a2 = DurableV226Call(context, self.payload(3), self.metadata(3, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[2]))
            retry_a2.reserve()
            # A distinct operation has its own operation cap, while sharing
            # the family's nine-slot reserve.
            context_b = dict(context, operation_id="OTHER_OPERATION")
            retry_b1 = DurableV226Call(context_b, self.payload(4), self.metadata(4, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[3]))
            retry_b1.reserve()
            ledger = initial.budget_ledger.snapshot()
            self.assertEqual([row["operation_id"] for row in ledger["reservations"] if row["attempt_type"] == "RETRY"], [context["operation_id"], context["operation_id"], context_b["operation_id"]])

    def test_retry_boundary_missing_or_mismatched_reservation_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, episode_budget_limits={
                "planned_initial_calls": 1, "retry_reserve": 2, "physical_ceiling": 3,
                "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1,
            })
            initial = DurableV226Call(context, self.payload(1), self.metadata(1, unit_ids=[1]))
            initial.reserve()
            retry = DurableV226Call(context, self.payload(2), self.metadata(2, phase="retry_local", attempt_type="RETRY", parent_attempt_id=initial.physical_attempt_id, attempt_ordinal=2, unit_ids=[2]))
            retry.reserve(); retry.prepare_request()
            with self.assertRaisesRegex(DurableCallError, "RESERVATION_MISSING"):
                retry.budget_ledger.assert_retry_cap(attempt_id="missing", operation_id=context["operation_id"], attempt_type="RETRY", unresolved_ids=[2])
            with self.assertRaisesRegex(DurableCallError, "OPERATION_ID_MISMATCH"):
                retry.budget_ledger.assert_retry_cap(attempt_id=retry.physical_attempt_id, operation_id="WRONG", attempt_type="RETRY", unresolved_ids=[2])
            with self.assertRaisesRegex(DurableCallError, "UNRESOLVED_ID_SET_MISMATCH"):
                retry.budget_ledger.assert_retry_cap(attempt_id=retry.physical_attempt_id, operation_id=context["operation_id"], attempt_type="RETRY", unresolved_ids=[99])
            # The same checks are enforced while the claim is held, before a
            # transport owner can be established.
            row_path = root / "family" / "episode-budget.json"
            row_value = json.loads(row_path.read_text())
            row_value["reservations"][-1]["operation_id"] = "TAMPERED"
            row_path.write_text(json.dumps(row_value))
            with self.assertRaisesRegex(DurableCallError, "OPERATION_ID_MISMATCH"):
                with retry.exclusive_transport_claim():
                    pass

    def test_missing_or_unknown_attempt_type_fails_before_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = self.metadata()
            metadata.pop("attempt_type")
            with self.assertRaisesRegex(DurableCallError, "ATTEMPT_TYPE_REQUIRED"):
                DurableV226Call(self.context(root), self.payload(), metadata)
            metadata["attempt_type"] = "MAYBE"
            with self.assertRaisesRegex(DurableCallError, "ATTEMPT_TYPE_REQUIRED"):
                DurableV226Call(self.context(root), self.payload(), metadata)
            self.assertFalse((root / "family" / "episode-budget.json").exists())

    def test_233_initials_preserve_nine_retry_slots_and_enforce_both_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, episode_budget_limits={
                "planned_initial_calls": 233, "retry_reserve": 9, "physical_ceiling": 242,
            })
            initial = []
            for number in range(233):
                call = DurableV226Call(context, self.payload(number), self.metadata(number, phase="main"))
                call.reserve()
                initial.append(call)
            snapshot = initial[-1].budget_ledger.snapshot()
            self.assertEqual((snapshot["initial_consumed"], snapshot["retry_consumed"]), (233, 0))
            self.assertEqual(snapshot["retry_remaining"], 9)
            blocked_initial = DurableV226Call(context, self.payload(999), self.metadata(999, phase="main"))
            with self.assertRaisesRegex(DurableCallError, "INITIAL_ALLOCATION_EXHAUSTED"):
                blocked_initial.reserve()
            parent = initial[-1]
            retry_calls = []
            for number in range(9):
                retry = DurableV226Call(
                    context, self.payload(2000 + number),
                    self.metadata(
                        2000 + number, phase="retry_local", attempt_type="RETRY",
                        parent_attempt_id=parent.physical_attempt_id, attempt_ordinal=2,
                    ),
                )
                retry.reserve()
                retry_calls.append(retry)
            snapshot = retry_calls[-1].budget_ledger.snapshot()
            self.assertEqual((snapshot["initial_consumed"], snapshot["retry_consumed"], snapshot["physical_consumed"]), (233, 9, 242))
            tenth = DurableV226Call(
                context, self.payload(3000),
                self.metadata(3000, phase="retry_local", attempt_type="RETRY", parent_attempt_id=parent.physical_attempt_id, attempt_ordinal=2),
            )
            with self.assertRaisesRegex(DurableCallError, "BUDGET_EXHAUSTED|RETRY_RESERVE_EXHAUSTED"):
                tenth.reserve()

    def test_identity_is_stable_across_new_client_operation_and_durable_response_reused(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context_r1 = self.context(root, operation_id="OPERATION_R1")
            context_r2 = self.context(root, operation_id="OPERATION_R2")
            config = Config("http://ollama.invalid", model="qwen3.5:9b", strict_json=True)
            config.model_digest = context_r1["model_digest"]
            event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
            unit = Unit("unit-1", [event])
            contexts = {1: {"previous": [], "next": []}}

            class Response:
                status_code = 200
                content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\"}]}"}}'

            transports = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *a, **k: transports.append(1) or Response()):
                config.durable_context = context_r1
                self.install_attempt(config)
                first = Client(config, [], {}, model="qwen3.5:9b")
                first.call([unit], {1: event}, contexts, phase="main")
                first_id = first.calls[-1]["physical_attempt_id"] if first.calls else None
                config.durable_context = context_r2
                self.install_attempt(config)
                second = Client(config, [], {}, model="qwen3.5:9b")
                second.call([unit], {1: event}, contexts, phase="main")
                second_id = second.calls[-1]["physical_attempt_id"] if second.calls else None
            self.assertEqual(transports, [1])
            self.assertEqual(first_id, second_id)
            state = json.loads((root / "family" / "calls" / first_id / "state.json").read_text())
            self.assertEqual(state["operation_ids"], ["OPERATION_R1", "OPERATION_R2"])

    def test_operation_budget_rejection_is_terminal_across_restart(self):
        class RejectingBudget:
            def reserve(self, **kwargs):
                raise RuntimeError("budget rejected")

        class AcceptingBudget:
            def reserve(self, **kwargs):
                raise AssertionError("terminal reservation must not reach secondary budget")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = DurableV226Call(self.context(root, operation_id="R1"), self.payload(), self.metadata(), operation_budget=RejectingBudget())
            with self.assertRaises(RuntimeError):
                first.reserve()
            self.assertEqual(first.state(), "RESERVATION_FAILED")
            second = DurableV226Call(self.context(root, operation_id="R2"), self.payload(), self.metadata(), operation_budget=AcceptingBudget())
            with self.assertRaisesRegex(DurableCallError, "RESERVATION_FAILURE_IS_TERMINAL"):
                second.reserve()
            snapshot = second.budget_ledger.snapshot()
            self.assertEqual(snapshot["physical_consumed"], 0)
            self.assertEqual(snapshot["reservations"][0]["state"], "RESERVATION_FAILED")

    def test_family_contract_change_with_same_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = DurableV226Call(self.context(root), self.payload(), self.metadata())
            first.reserve()
            changed = self.context(root, source_sha256="f" * 64)
            second = DurableV226Call(changed, self.payload(), self.metadata())
            with self.assertRaisesRegex(DurableCallError, "FAMILY_CONTRACT_MISMATCH"):
                second.reserve()

    def test_capture_state_is_derived_and_reconciled_from_state_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve()
            alias = call.call_dir / "capture_state.json"
            alias.write_text('{"state":"WRONG"}', encoding="utf-8")
            resumed = DurableV226Call(self.context(root, operation_id="R2"), self.payload(), self.metadata())
            self.assertEqual(resumed.state(), "RESERVED")
            reconciled = json.loads(alias.read_text())
            self.assertTrue(reconciled["derived_alias"])
            self.assertEqual(reconciled["authority"], "state.json")
            self.assertEqual(reconciled["state"], "RESERVED")

    def test_twelve_explicit_initial_calls_are_twelve_zero_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, episode_budget_limits={
                "planned_initial_calls": 12, "retry_reserve": 2, "physical_ceiling": 14,
            })
            last = None
            for number in range(12):
                last = DurableV226Call(context, self.payload(number), self.metadata(number, phase="main"))
                last.reserve()
            snapshot = last.budget_ledger.snapshot()
            self.assertEqual(snapshot["initial_consumed"], 12)
            self.assertEqual(snapshot["retry_consumed"], 0)

    def test_fault_boundaries_preserve_authoritative_states(self):
        class Budget:
            def __init__(self):
                self.ids = set()

            def reserve(self, *, reservation_id=None, **kwargs):
                self.ids.add(reservation_id)

        # Before any reservation, no budget mutation exists.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root, durability_fault_point="before_reservation"), self.payload(), self.metadata())
            with self.assertRaises(DurableCallFault):
                call.reserve()
            self.assertFalse(Path(self.context(root)["episode_budget_ledger_path"]).exists())

        # Episode reservation and secondary-budget acceptance are each
        # restartable without creating a second persistent reservation.
        for point, expected_operation_state in (("after_ledger_reserve", "PENDING"), ("after_operation_budget_reserve", "ACCEPTED")):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                budget = Budget()
                call = DurableV226Call(self.context(root, durability_fault_point=point), self.payload(), self.metadata(), operation_budget=budget)
                with self.assertRaises(DurableCallFault):
                    call.reserve()
                row = call.budget_ledger.snapshot()["reservations"][0]
                self.assertEqual(row["operation_budget_state"], expected_operation_state)
                resumed = DurableV226Call(self.context(root, operation_id="RESTART"), self.payload(), self.metadata(), operation_budget=Budget())
                self.assertEqual(resumed.reserve()["state"], "RESERVED")
                self.assertEqual(resumed.budget_ledger.snapshot()["physical_consumed"], 1)

        # A torn request body is non-authoritative until metadata/state are
        # promoted, and can be recreated locally without transport.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root, durability_fault_point="after_request_body_fsync"), self.payload(), self.metadata())
            with self.assertRaises(DurableCallFault):
                call.prepare_request()
            self.assertEqual(call.state(), "RESERVED")
            self.assertTrue((call.call_dir / "request_payload.json").is_file())
            resumed = DurableV226Call(self.context(root, operation_id="RESTART"), self.payload(), self.metadata())
            self.assertEqual(resumed.prepare_request()["state"], "REQUEST_DURABLE")

        # Body alone is incomplete and becomes UNKNOWN.  Body plus valid
        # metadata is a complete capture and is promoted locally even when the
        # process faults before the state marker is written.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve(); call.prepare_request(); call._fault_point = "after_response_body_fsync"
            with self.assertRaises(DurableCallFault):
                with call.exclusive_transport_claim() as owner:
                    self.assertTrue(owner)
                    call.begin_transport()
                    call.record_response(b'{"message":{"content":"{}"}}', status_code=200)
            self.assertEqual(call.state(), "TRANSPORT_OUTCOME_UNKNOWN")
            self.assertTrue((call.call_dir / "response.body").is_file())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve(); call.prepare_request(); call._fault_point = "after_response_metadata_fsync"
            with self.assertRaises(DurableCallFault):
                with call.exclusive_transport_claim() as owner:
                    self.assertTrue(owner)
                    call.begin_transport()
                    call.record_response(b'{"message":{"content":"{}"}}', status_code=200)
            self.assertEqual(call.state(), "RESPONSE_DURABLE")
            self.assertTrue((call.call_dir / "response_metadata.json").is_file())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve(); call.prepare_request()
            with self.assertRaises(DurableCallFault):
                with call.exclusive_transport_claim() as owner:
                    self.assertTrue(owner)
                    call.begin_transport()
                    call._fault_point = "after_state_before_alias"
                    call.record_response(b'{"message":{"content":"{}"}}', status_code=200)
            resumed = DurableV226Call(self.context(root, operation_id="RESTART"), self.payload(), self.metadata())
            self.assertEqual(resumed.state(), "RESPONSE_DURABLE")
            self.assertEqual(resumed.load_raw(), b'{"message":{"content":"{}"}}')

    def test_canonical_runner_233_initials_restart_mid_batches_without_retransport(self):
        from pipeline_v2_1_3 import CleanSegment, Config, Event, Runner
        from v238_llama_policy import OperationCallBudget

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(
                root, operation_id="CANONICAL_DRY_R1",
                episode_budget_limits={"planned_initial_calls": 233, "retry_reserve": 9, "physical_ceiling": 242},
            )
            events = [
                Event(number, number, 0, number * 1000, number * 1000 + 900, "Default", "", 0, 0, 0, "", f"source {number}", f"source {number}", f"source {number}", [CleanSegment(0, f"source {number}", f"source {number}")], [], [], False, "MAIN_DIALOGUE")
                for number in range(1, 234)
            ]

            class Response:
                status_code = 200

                def __init__(self, payload):
                    content = next(message["content"] for message in payload["messages"] if "TARGET: " in message["content"])
                    target = json.loads(content.split("TARGET: ", 1)[1].split("\nGLOSSARY:", 1)[0])
                    translations = [{"id": item["id"], "text": "texto traduzido"} for item in target]
                    inner = json.dumps({"translations": translations}, ensure_ascii=False)
                    self.content = json.dumps({"message": {"content": inner}}, ensure_ascii=False).encode("utf-8")

            transports = []

            def local_provider(*args, **kwargs):
                content = next(message["content"] for message in kwargs["json"]["messages"] if "TARGET: " in message["content"])
                transports.append(tuple(item["id"] for item in json.loads(content.split("TARGET: ", 1)[1].split("\nGLOSSARY:", 1)[0])))
                return Response(kwargs["json"])

            def runner_for(operation_id):
                candidate_context = dict(context, operation_id=operation_id)
                config = Config("http://local-provider.invalid", model="qwen3.5:9b", strict_json=True, batch_target_size=1, retry_budget_calls=9)
                config.model_digest = candidate_context["model_digest"]
                config.durable_context = candidate_context
                config.operation_budget = OperationCallBudget(qwen_physical_maximum=242, llama_generation_maximum=0)
                return Runner(events, {}, config, {})

            with patch("pipeline_v2_1_3.requests.post", side_effect=local_provider):
                first = runner_for("CANONICAL_DRY_R1")
                for batch_index in range(100):
                    first._process_units(
                        [first.units[batch_index]], "initial", attempt_type="INITIAL",
                        logical_batch_id=f"v226-initial-{batch_index:06d}", batch_index=batch_index,
                    )
                first_ids = [row["physical_attempt_id"] for row in first.calls]
                second = runner_for("CANONICAL_DRY_R2")
                summary = second.run()
            self.assertEqual(len(transports), 233)
            self.assertEqual(summary["initial_batch_count"], 233)
            self.assertEqual(summary["initial_ollama_calls"], 233)
            self.assertEqual([row["physical_attempt_id"] for row in second.calls[:100]], first_ids)
            snapshot = json.loads(Path(context["episode_budget_ledger_path"]).read_text())
            self.assertEqual(snapshot["initial_consumed"], 233)
            self.assertEqual(snapshot["retry_consumed"], 0)
            self.assertEqual(len({row["logical_call_id"] for row in snapshot["reservations"]}), 233)

    def test_client_fault_matrix_transport_and_parse_boundaries(self):
        from pipeline_v2_1_3 import CleanSegment, Client, Config, Event, Unit

        event = Event(1, 0, 0, 0, 1000, "Default", "", 0, 0, 0, "", "hello", "hello", "hello", [CleanSegment(0, "hello", "hello")], [], [], False, "MAIN_DIALOGUE")
        unit = Unit("unit-1", [event])
        contexts = {1: {"previous": [], "next": []}}

        class Response:
            status_code = 200
            content = b'{"message":{"content":"{\\"translations\\":[{\\"id\\":1,\\"text\\":\\"oi\\"}]}"}}'

        expected = {
            "before_post": (0, "TRANSPORT_OUTCOME_UNKNOWN", False),
            "during_transport": (0, "TRANSPORT_OUTCOME_UNKNOWN", False),
            "after_response_received_before_capture": (1, "TRANSPORT_OUTCOME_UNKNOWN", False),
            "before_parse": (1, "RESPONSE_DURABLE", True),
            "after_parse": (1, "PARSED_VALID", True),
        }
        for point, (transport_count, state, has_raw) in expected.items():
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                context = self.context(root, durability_fault_point=point)
                config = Config("http://local.invalid", model="qwen3.5:9b", strict_json=True)
                config.model_digest = context["model_digest"]
                config.durable_context = context
                self.install_attempt(config)
                transports = []
                with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *a, **k: transports.append(1) or Response()):
                    client = Client(config, [], {}, model="qwen3.5:9b")
                    with self.assertRaises(DurableCallFault):
                        client.call([unit], {1: event}, contexts, phase="main")
                self.assertEqual(len(transports), transport_count)
                calls = list((root / "family" / "calls").iterdir())
                authority = json.loads((calls[0] / "state.json").read_text())
                self.assertEqual(authority["state"], state)
                self.assertEqual((calls[0] / "response.body").is_file(), has_raw)
                if state in {"RESPONSE_DURABLE", "PARSED_VALID"}:
                    resumed_context = self.context(root, operation_id="RESTART", durability_fault_point="")
                    resumed_config = Config("http://local.invalid", model="qwen3.5:9b", strict_json=True)
                    resumed_config.model_digest = resumed_context["model_digest"]
                    resumed_config.durable_context = resumed_context
                    self.install_attempt(resumed_config)
                    with patch("pipeline_v2_1_3.requests.post", side_effect=AssertionError("no repeated transport")):
                        found, issues, _ = Client(resumed_config, [], {}, model="qwen3.5:9b").call([unit], {1: event}, contexts, phase="main")
                    self.assertEqual(found[1]["text"], "oi")
                    self.assertEqual(issues, [])

    def test_canonical_runner_restart_with_first_batch_inflight_stops_before_retransport(self):
        from pipeline_v2_1_3 import CleanSegment, Config, Event, Runner
        from v238_llama_policy import OperationCallBudget

        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            durable_context = self.context(
                root, operation_id="CANONICAL_CRASH_OWNER",
                episode_budget_limits={"planned_initial_calls": 233, "retry_reserve": 9, "physical_ceiling": 242},
            )
            events = [
                Event(number, number, 0, number * 1000, number * 1000 + 900, "Default", "", 0, 0, 0, "", f"source {number}", f"source {number}", f"source {number}", [CleanSegment(0, f"source {number}", f"source {number}")], [], [], False, "MAIN_DIALOGUE")
                for number in range(1, 234)
            ]

            def runner_for(operation_id):
                candidate_context = dict(durable_context, operation_id=operation_id)
                config = Config("http://local-provider.invalid", model="qwen3.5:9b", strict_json=True, batch_target_size=1, retry_budget_calls=9)
                config.model_digest = candidate_context["model_digest"]
                config.durable_context = candidate_context
                config.operation_budget = OperationCallBudget(qwen_physical_maximum=242, llama_generation_maximum=0)
                return Runner(events, {}, config, {})

            entered_post = ctx.Event()

            def crash_owner():
                def hard_crash_post(*args, **kwargs):
                    entered_post.set()
                    os._exit(74)
                with patch("pipeline_v2_1_3.requests.post", side_effect=hard_crash_post):
                    runner_for("CANONICAL_CRASH_OWNER").run()

            process = ctx.Process(target=crash_owner)
            process.start()
            self.assertTrue(entered_post.wait(5))
            process.join(5)
            self.assertEqual(process.exitcode, 74)

            repeated_transports = []
            with patch("pipeline_v2_1_3.requests.post", side_effect=lambda *a, **k: repeated_transports.append(1)):
                with self.assertRaises(DurableCallOutcomeUnknown):
                    runner_for("CANONICAL_RESTART").run()
            self.assertEqual(repeated_transports, [])
            snapshot = json.loads(Path(durable_context["episode_budget_ledger_path"]).read_text())
            self.assertEqual(snapshot["initial_consumed"], 1)
            self.assertEqual(snapshot["retry_consumed"], 0)
            self.assertEqual(snapshot["reservations"][0]["state"], "TRANSPORT_OUTCOME_UNKNOWN")

    def test_transport_state_never_downgrades_and_initial_ordinal_is_one(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.reserve(); call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner)
                call.begin_transport()
            with self.assertRaisesRegex(DurableCallError, "STATE_TRANSITION_PROHIBITED"):
                call._transition("REQUEST_DURABLE")
            with self.assertRaises(DurableCallOutcomeUnknown):
                DurableV226Call(
                    self.context(root, operation_id="RESTART"), self.payload(), self.metadata()
                ).prepare_request()
            self.assertEqual(call.state(), "TRANSPORT_OUTCOME_UNKNOWN")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(DurableCallError, "INITIAL_ATTEMPT_ORDINAL_MUST_BE_ONE"):
                DurableV226Call(
                    self.context(root), self.payload(),
                    self.metadata(attempt_type="INITIAL", attempt_ordinal=2),
                )

    def test_exclusive_interprocess_claim_allows_exactly_one_transport(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, transport_claim_timeout_seconds=3.0)
            payload = self.payload()
            metadata = self.metadata()
            barrier = ctx.Barrier(2)
            transport_count = ctx.Value("i", 0)
            results = ctx.Queue()

            def worker(operation_id):
                try:
                    call = DurableV226Call(dict(context, operation_id=operation_id), payload, metadata)
                    call.prepare_request()
                    barrier.wait(timeout=3)
                    with call.exclusive_transport_claim() as owner:
                        if owner:
                            call.begin_transport()
                            with transport_count.get_lock():
                                transport_count.value += 1
                            time.sleep(0.15)
                            call.record_response(b'{"message":{"content":"{}"}}', status_code=200)
                        results.put("OWNER" if owner else "REUSED")
                except BaseException as exc:
                    results.put("ERROR:" + type(exc).__name__ + ":" + str(exc))

            workers = [ctx.Process(target=worker, args=(f"WORKER_{number}",)) for number in (1, 2)]
            for process in workers:
                process.start()
            for process in workers:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            observed = sorted(results.get(timeout=2) for _ in workers)
            self.assertEqual(transport_count.value, 1)
            self.assertEqual(observed, ["OWNER", "REUSED"])
            call = DurableV226Call(self.context(root, operation_id="VERIFY"), payload, metadata)
            self.assertEqual(call.state(), "RESPONSE_DURABLE")

    def test_real_hard_crash_during_transport_stops_restart_without_post(self):
        ctx = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root)
            payload = self.payload()
            metadata = self.metadata()
            entered_transport = ctx.Event()

            def crash_worker():
                call = DurableV226Call(dict(context, operation_id="CRASH_OWNER"), payload, metadata)
                call.prepare_request()
                with call.exclusive_transport_claim() as owner:
                    if not owner:
                        os._exit(91)
                    call.begin_transport()
                    entered_transport.set()
                    os._exit(73)

            process = ctx.Process(target=crash_worker)
            process.start()
            self.assertTrue(entered_transport.wait(5))
            process.join(5)
            self.assertEqual(process.exitcode, 73)
            state_path = next((root / "family" / "calls").iterdir()) / "state.json"
            self.assertEqual(json.loads(state_path.read_text())["state"], "TRANSPORT_IN_PROGRESS")

            restart_result = ctx.Queue()

            def restart_worker():
                transports = 0
                restarted = DurableV226Call(
                    self.context(root, operation_id="AFTER_HARD_CRASH"), payload, metadata
                )
                try:
                    restarted.prepare_request()
                    with restarted.exclusive_transport_claim() as owner:
                        if owner:
                            transports += 1
                    restart_result.put({"state": restarted.state(), "transports": transports})
                except BaseException as exc:
                    restart_result.put({
                        "state": restarted.state(), "transports": transports,
                        "error": type(exc).__name__,
                    })

            second = ctx.Process(target=restart_worker)
            second.start(); second.join(5)
            self.assertEqual(second.exitcode, 0)
            observed = restart_result.get(timeout=2)
            self.assertEqual(observed["transports"], 0)
            self.assertEqual(observed["state"], "TRANSPORT_OUTCOME_UNKNOWN")
            restarted = DurableV226Call(self.context(root, operation_id="VERIFY_UNKNOWN"), payload, metadata)
            self.assertEqual(restarted.state(), "TRANSPORT_OUTCOME_UNKNOWN")

    def test_response_capture_restart_reconciliation_complete_incomplete_and_corrupt(self):
        from v238_per_call_durability import _atomic_bytes, _atomic_json, sha256_bytes

        raw = b'{"message":{"content":"{}"}}'

        # Complete body + metadata are locally promoted without another POST.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner); call.begin_transport()
                _atomic_bytes(call.call_dir / "response.body", raw)
                _atomic_json(call.call_dir / "response_metadata.json", {
                    "http_status": 200, "response_bytes": len(raw),
                    "response_sha256": sha256_bytes(raw), "received_at": "test",
                })
            resumed = DurableV226Call(self.context(root, operation_id="COMPLETE_RESTART"), self.payload(), self.metadata())
            self.assertEqual(resumed.prepare_request()["state"], "RESPONSE_DURABLE")
            self.assertEqual(resumed.load_raw(), raw)

        # Body without authoritative metadata is unknown and never retried.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner); call.begin_transport()
                _atomic_bytes(call.call_dir / "response.body", raw)
            resumed = DurableV226Call(self.context(root, operation_id="INCOMPLETE_RESTART"), self.payload(), self.metadata())
            with self.assertRaises(DurableCallOutcomeUnknown):
                resumed.prepare_request()
            self.assertEqual(resumed.state(), "TRANSPORT_OUTCOME_UNKNOWN")

        # Mismatched body/metadata are explicitly corrupt and fail closed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata())
            call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner); call.begin_transport()
                _atomic_bytes(call.call_dir / "response.body", raw)
                _atomic_json(call.call_dir / "response_metadata.json", {
                    "http_status": 200, "response_bytes": len(raw),
                    "response_sha256": "0" * 64, "received_at": "test",
                })
            resumed = DurableV226Call(self.context(root, operation_id="CORRUPT_RESTART"), self.payload(), self.metadata())
            with self.assertRaisesRegex(DurableCallError, "CAPTURE_CORRUPT"):
                resumed.prepare_request()
            self.assertEqual(resumed.state(), "CORRUPT_CAPTURE")

    def test_valid_subset_manifest_self_hash_and_rows_are_fail_closed(self):
        from v238_per_call_durability import _manifest_sha256, canonical_bytes
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.context(root, valid_subset_policy="V238_VALID_SUBSET_V1")
            call = DurableV226Call(context, self.payload(), self.metadata(unit_ids=[1, 2, 3]))
            call.reserve(); call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner); call.begin_transport(); call.record_response(b'{"translations":[]}', status_code=200)
            call.mark_parsed(valid=False, error="synthetic partial response")
            call.record_valid_subset({1: {"id": 1, "text": "ok"}, 2: {"id": 2, "text": "ok"}}, [1, 2, 3], [3])
            manifest_path = call.call_dir / "valid_subset_manifest.json"
            original = json.loads(manifest_path.read_text())
            self.assertEqual(original["manifest_sha256"], _manifest_sha256(original))
            tampered = dict(original); tampered["kind"] = "OTHER"
            manifest_path.write_bytes(canonical_bytes(tampered))
            with self.assertRaisesRegex(DurableCallError, "MANIFEST_HASH|MANIFEST_KIND"):
                call.load_valid_subset()

    def test_valid_subset_recorded_cannot_transition_directly_to_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            call = DurableV226Call(self.context(root), self.payload(), self.metadata(unit_ids=[1, 2]))
            call.reserve(); call.prepare_request()
            with call.exclusive_transport_claim() as owner:
                self.assertTrue(owner); call.begin_transport(); call.record_response(b'{"translations":[]}', status_code=200)
            call.mark_parsed(valid=False, error="synthetic partial response")
            call.record_valid_subset({1: {"id": 1, "text": "ok"}}, [1, 2], [2])
            with self.assertRaisesRegex(DurableCallError, "STATE_TRANSITION_PROHIBITED"):
                call._transition("BATCH_COMPLETE")


if __name__ == "__main__":
    unittest.main()
