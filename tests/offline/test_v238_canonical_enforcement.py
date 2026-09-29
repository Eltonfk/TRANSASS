from __future__ import annotations

import tempfile
import unittest
import json
import os
import re
from pathlib import Path

from pipeline_orchestrator import execute_pipeline_plan
from v238_llama_policy import LLAMA_MODEL_DIGEST, LLAMA_MODEL_TAG, OperationCallBudget
from v238_response_provider import DurableResponseProvider
from v238_base_materializer import CanonicalV226LiveMaterializer
from v238_base_materializer import BaseTranslationMaterializerError
from v238_full_translation_stage import reconcile_atomic_stage_output
from production_v2_3_8_adapter import _validate_base_presentation_envelope
from v238_semantic_style_ownership import (
    extract_semantic_style_ownership,
    identity_ownership_mapping,
    render_target_ownership,
)


ASS = """[Script Info]
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,2,1,2,2,2,10,10,10,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,world
"""


class Materializer:
    mode = "TEST_FIXTURE"

    def __init__(self, ledger):
        self.ledger = ledger

    def materialize(self, source, output, *, context):
        Path(output).write_bytes(Path(source).read_bytes())
        return {"mode": self.mode, "primary_ledger": self.ledger,
                "metrics": {"primary_requests": 2, "physical_attempts": 2}}


class LlamaSpy:
    def __init__(self):
        self.requests = []
        self.loads = 0
        self.unloads = 0

    def load(self):
        self.loads += 1

    def respond(self, request, *, capture_id=None):
        self.requests.append((request, capture_id))
        return {"candidates": [{"canonical_unit_id": row["canonical_unit_id"], "text": "candidate"} for row in request["units"]]}

    def unload(self):
        self.unloads += 1


def _ledger(statuses):
    return [{
        "episode_id": 79, "source_object_sha256": "source", "canonical_unit_id": unit,
        "primary_model_tag": "qwen3.5:9b", "primary_model_digest": "qwen-digest",
        "primary_attempts": 1, "status": status, "reason_code": reason,
        "objective_reason_code": reason, "capture_references": [f"capture-{unit}"],
    } for unit, status, reason in statuses]


class CanonicalV238EnforcementTests(unittest.TestCase):
    def test_base_validation_allows_target_resegment_with_same_style_transitions(self):
        """A translated styled word may change segment count without loss."""
        with tempfile.TemporaryDirectory(prefix="v238-style-resegment-") as raw:
            root = Path(raw)
            source, base = root / "source.ass", root / "base.ass"
            source.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Ah, Haraguchi-{\\i1}san{\\i0}!",
            ), encoding="utf-8")
            base.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Ah, Sr Haraguchi{\\i1}!{\\i0}",
            ), encoding="utf-8")
            _validate_base_presentation_envelope(source, base)

    def test_base_validation_allows_reflowed_per_character_style_gradient(self):
        """A translated sign may move, but must not alter, its style tokens."""
        with tempfile.TemporaryDirectory(prefix="v238-style-gradient-") as raw:
            root = Path(raw)
            source, base = root / "source.ass", root / "base.ass"
            source.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\c&H000000&}A{\c&H111111&}B{\c&H222222&}C{\c&H444444&}D",
            ), encoding="utf-8")
            base.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\c&H000000&}{\c&H111111&}{\c&H222222&}ABC{\c&H444444&}D",
            ), encoding="utf-8")
            _validate_base_presentation_envelope(source, base)

    def test_base_validation_rejects_changed_style_token_during_reflow(self):
        with tempfile.TemporaryDirectory(prefix="v238-style-gradient-invalid-") as raw:
            root = Path(raw)
            source, base = root / "source.ass", root / "base.ass"
            source.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\c&H000000&}A{\c&H111111&}B{\c&H222222&}C{\c&H444444&}D",
            ), encoding="utf-8")
            base.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\c&H000000&}{\c&H111111&}{\c&H333333&}ABC{\c&H444444&}D",
            ), encoding="utf-8")
            with self.assertRaises(Exception) as raised:
                _validate_base_presentation_envelope(source, base)
            self.assertIn("V238_BASE_SEMANTIC_STYLE_SEGMENTS_MISMATCH", str(raised.exception))

    def test_base_validation_allows_reflowed_font_size_gradient(self):
        """A translated styled event with font-size reflow across words must be accepted when tokens match."""
        with tempfile.TemporaryDirectory(prefix="v238-fs-gradient-") as raw:
            root = Path(raw)
            source, base = root / "source.ass", root / "base.ass"
            source.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\fs75}D{*\fs80}r{*\fs85}a{*\fs90}g",
            ), encoding="utf-8")
            base.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\fs75}{*\fs80}Arrasta{*\fs85}{*\fs90}",
            ), encoding="utf-8")
            _validate_base_presentation_envelope(source, base)

    def test_base_validation_allows_event_2818_style_reflow(self):
        """Regression test for multi-word font size gradient reflow (e.g. S01E04 event 2818)."""
        with tempfile.TemporaryDirectory(prefix="v238-event2818-") as raw:
            root = Path(raw)
            source, base = root / "source.ass", root / "base.ass"
            source_text = r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\c&HFF7D83&\frz296.5\fs75\b1\blur1.2\pos(509.475,1376.01)\fscx142.86\fscy142.86\frx10\fry8}D{*\fs77.608}r{*\fs80.217}a{*\fs82.826}g {*\fs88.044}D{*\fs90.653}r{*\fs93.261}a{*\fs95.869}g {*\fs101.087}D{*\fs103.695}r{*\fs106.305}a{*\fs108.913}g {*\fs114.131}D{*\fs116.739}r{*\fs119.347}a{*\fs121.956}g {*\fs127.174}D{*\fs129.783}r{*\fs132.392}a{\fs135}g"
            base_text = r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\c&HFF7D83&\frz296.5\fs75\b1\blur1.2\pos(509.475,1376.01)\fscx142.86\fscy142.86\frx10\fry8}{*\fs77.608}{*\fs80.217}Arrasta{*\fs82.826} {*\fs88.044}{*\fs90.653}{*\fs93.261}Arrasta{*\fs95.869} {*\fs101.087}{*\fs103.695}Arrasta{*\fs106.305}{*\fs108.913} {*\fs114.131}{*\fs116.739}Arrasta{*\fs119.347}{*\fs121.956} {*\fs127.174}{*\fs129.783}Arrasta{*\fs132.392}{\fs135}"
            source.write_text(ASS.replace("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello", source_text), encoding="utf-8")
            base.write_text(ASS.replace("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello", base_text), encoding="utf-8")
            _validate_base_presentation_envelope(source, base)

    def test_base_validation_rejects_corrupted_font_size_gradient(self):
        """A corrupted font-size token during reflow fails closed with V238_BASE_SEMANTIC_STYLE_SEGMENTS_MISMATCH."""
        with tempfile.TemporaryDirectory(prefix="v238-fs-corrupt-") as raw:
            root = Path(raw)
            source, base = root / "source.ass", root / "base.ass"
            source.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\fs75}D{*\fs80}r{*\fs85}a{*\fs90}g",
            ), encoding="utf-8")
            base.write_text(ASS.replace(
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello",
                r"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\fs75}{*\fs80}A{*\fs999}rrasta{*\fs90}",
            ), encoding="utf-8")
            with self.assertRaises(Exception) as raised:
                _validate_base_presentation_envelope(source, base)
            self.assertIn("V238_BASE_SEMANTIC_STYLE_SEGMENTS_MISMATCH", str(raised.exception))

    def test_checkpoint_fault_points_resume_without_repeating_v226(self):
        for fault in ("after_v226_return", "after_base_ass", "after_manifest", "before_complete"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(prefix="v238-resume-") as raw:
                root = Path(raw)
                source, first, resumed = root / "source.ass", root / "first.ass", root / "resumed.ass"
                source.write_text(ASS, encoding="utf-8")
                calls = {"count": 0}

                def fake_v226(src, dst, **kwargs):
                    calls["count"] += 1
                    Path(dst).write_bytes(Path(src).read_bytes())
                    return {"calls": 1, "results": [{"id": 0, "status": "resolved", "final_model": "qwen3.5:9b"}], "model": "qwen3.5:9b"}

                materializer = CanonicalV226LiveMaterializer()
                context = {"checkpoint_root": root / "state", "operation_id": f"op-{fault}", "episode_id": 79,
                           "anime_series_id": 1, "model": "qwen3.5:9b", "candidate_commit": "candidate",
                           "fault_injection": fault}
                from unittest.mock import patch
                with patch("v238_base_materializer.translate_subtitle_file_v2_2_6", fake_v226):
                    with self.assertRaises(Exception):
                        materializer.materialize(source, first, context=context)
                    resumed_result = materializer.materialize(source, resumed, context={**context, "fault_injection": None})
                self.assertEqual(calls["count"], 1)
                self.assertEqual(resumed_result["checkpoint_resumed"], 1)
                self.assertEqual(first.exists(), False)
                self.assertTrue(resumed.exists())

    def test_output_without_marker_reconciles_by_sha_and_validation(self):
        with tempfile.TemporaryDirectory(prefix="v238-marker-") as raw:
            root = Path(raw)
            source = root / "source.ass"
            output = root / "candidate.ass"
            source.write_text(ASS, encoding="utf-8")
            output.write_text(ASS, encoding="utf-8")
            result = reconcile_atomic_stage_output(source, output, context={"stage_completion_root": root / "markers"})
            self.assertEqual(result["state"], "COMPLETE")
            self.assertTrue((root / "markers" / "candidate.ass.complete.json").is_file())

    def test_live_checkpoint_identity_is_fail_closed_when_incomplete(self):
        with tempfile.TemporaryDirectory(prefix="v238-live-identity-") as raw:
            root = Path(raw)
            source = root / "source.ass"
            source.write_text(ASS, encoding="utf-8")
            with self.assertRaises(BaseTranslationMaterializerError):
                CanonicalV226LiveMaterializer().materialize(
                    source, root / "base.ass",
                    context={"execution_mode": "LIVE_CAPTURED", "operation_id": "op-live"},
                )
    def test_canonical_entrypoint_runs_primary_then_one_grouped_llama_phase(self):
        with tempfile.TemporaryDirectory(prefix="v238-canonical-policy-") as raw:
            root = Path(raw)
            source, output = root / "source.ass", root / "final.ass"
            source.write_text(ASS, encoding="utf-8")
            qwen = DurableResponseProvider("TEST_FAKE")
            llama = LlamaSpy()
            result = execute_pipeline_plan(
                "v2_3_8", source, output,
                {"response_provider": qwen, "base_materializer": Materializer(_ledger([
                    ("u1", "BLOCKED", "PRIMARY_SCHEMA_REJECTED"),
                    ("u2", "SUSPECT", "DETERMINISTIC_SUSPECT_FLAG"),
                ])), "llama_provider": llama, "llama_model_tag": LLAMA_MODEL_TAG,
                 "llama_model_digest": LLAMA_MODEL_DIGEST, "execution_mode": "TEST_FAKE",
                 "capture_root": root / "captures", "operation_id": "op-canonical-policy"},
            )
            stage = result["stages"][0]["result"]
            phase = stage["llama_phase"]
            self.assertEqual(len(llama.requests), 1)
            self.assertEqual(llama.loads, 1)
            self.assertEqual(llama.unloads, 1)
            self.assertEqual(phase["calls"], 1)
            self.assertEqual(len(phase["results"]), 2)
            self.assertEqual(phase["state"], "CANDIDATE_REVIEW_REQUIRED")
            self.assertFalse(phase["publishable"])
            self.assertTrue(all(row["publishable"] is False for row in phase["results"]))
            self.assertEqual(result["stages"][1]["id"], "KARAOKE_AUGMENTATION_V230")

    def test_canonical_entrypoint_uses_real_v226_materializer_ledger(self):
        """The policy proof must traverse the production materializer seam."""
        with tempfile.TemporaryDirectory(prefix="v238-canonical-live-seam-") as raw:
            root = Path(raw)
            source, output = root / "source.ass", root / "final.ass"
            source.write_text(ASS, encoding="utf-8")
            llama = LlamaSpy()
            primary_calls = []

            def fake_v226(src, dst, **kwargs):
                primary_calls.append(kwargs.get("execution_context", {}).get("model", ""))
                Path(dst).write_bytes(Path(src).read_bytes())
                return {
                    "model": "qwen3.5:9b",
                    "calls": [{"call_id": "qwen-primary-1", "event_ids": [0, 1], "model": "qwen3.5:9b"}],
                    "results": [
                        {"id": 0, "status": "failed", "failure_reason": "schema", "retry_count": 1},
                        {"id": 1, "status": "failed", "flags": ["DETERMINISTIC_SUSPECT_FLAG"], "retry_count": 0},
                    ],
                    "total_ollama_calls": 1,
                    "actual_retry_ollama_calls": 0,
                }

            from unittest.mock import patch
            context = {
                "response_provider": DurableResponseProvider("TEST_FAKE"),
                "base_materializer": CanonicalV226LiveMaterializer(),
                "llama_provider": llama,
                "llama_model_tag": LLAMA_MODEL_TAG,
                "llama_model_digest": LLAMA_MODEL_DIGEST,
                "execution_mode": "TEST_FAKE",
                "checkpoint_root": root / "state",
                "capture_root": root / "captures",
                "operation_id": "op-real-materializer",
                "episode_id": 79,
                "anime_series_id": 1,
                "model": "qwen3.5:9b",
            }
            with patch("v238_base_materializer.translate_subtitle_file_v2_2_6", fake_v226):
                result = execute_pipeline_plan("v2_3_8", source, output, context)
            phase = result["stages"][0]["result"]["llama_phase"]
            self.assertEqual(primary_calls, ["qwen3.5:9b"])
            self.assertEqual(len(llama.requests), 1)
            self.assertEqual(phase["eligible_count"], 2)
            self.assertEqual(len(phase["lineage"]), 2)
            self.assertEqual(phase["unload_calls"], 1)
            self.assertEqual(phase["publishable"], False)

    def test_canonical_zero_eligible_does_not_load_or_unload_llama(self):
        with tempfile.TemporaryDirectory(prefix="v238-canonical-zero-") as raw:
            root = Path(raw)
            source, output = root / "source.ass", root / "final.ass"
            source.write_text(ASS, encoding="utf-8")
            llama = LlamaSpy()
            result = execute_pipeline_plan(
                "v2_3_8", source, output,
                {"response_provider": DurableResponseProvider("TEST_FAKE"),
                 "base_materializer": Materializer(_ledger([("u1", "RESOLVED", "")])),
                 "llama_provider": llama, "execution_mode": "TEST_FAKE"},
            )
            phase = result["stages"][0]["result"]["llama_phase"]
            self.assertEqual(phase["eligible_count"], 0)
            self.assertEqual(phase["calls"], 0)
            self.assertEqual(llama.loads, 0)
            self.assertEqual(llama.unloads, 0)

    def test_canonical_group_respects_shared_llama_budget(self):
        with tempfile.TemporaryDirectory(prefix="v238-canonical-budget-") as raw:
            root = Path(raw)
            source, output = root / "source.ass", root / "final.ass"
            source.write_text(ASS, encoding="utf-8")
            with self.assertRaises(Exception):
                execute_pipeline_plan(
                    "v2_3_8", source, output,
                    {"response_provider": DurableResponseProvider("TEST_FAKE"),
                     "base_materializer": Materializer(_ledger([("u1", "BLOCKED", "PRIMARY_SCHEMA_REJECTED")])),
                     "llama_provider": LlamaSpy(), "operation_budget": OperationCallBudget(llama_generation_maximum=0),
                     "execution_mode": "TEST_FAKE"},
                )

    def test_memory_root_alias_reaches_real_v226_chain_without_model_transport(self):
        """The repaired V2.3.8 caller must cross the frozen seam and client."""
        with tempfile.TemporaryDirectory(prefix="v238-memory-seam-") as raw:
            root = Path(raw)
            source = root / "source.ass"
            source.write_text(ASS.split("Dialogue:", 1)[0] +
                              "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hello\n", encoding="utf-8")
            memory = root / "memory" / "db"
            memory.mkdir(parents=True)
            import sqlite3
            sqlite3.connect(memory / "subtitle_library.sqlite3").close()
            import production_v2_2_5_adapter as frozen_v225
            import pipeline_v2_1_3 as pipeline
            from unittest.mock import patch
            seam_kwargs = {}
            client_calls = {"count": 0}

            original_seam = frozen_v225.translate_subtitle_file_v2_2_5
            def seam_wrapper(*args, **kwargs):
                seam_kwargs.update(kwargs)
                return original_seam(*args, **kwargs)

            original_client = frozen_v225.V225MemoryClient.call
            def client_wrapper(self, *args, **kwargs):
                client_calls["count"] += 1
                return original_client(self, *args, **kwargs)

            class Response:
                status_code = 200
                def raise_for_status(self):
                    return None
                def json(self):
                    return self.body

            def fake_post(url, **kwargs):
                # The V2.2.x Ollama envelope keeps the system contract in
                # message 0 and the TARGET payload in the user message.  Do
                # not couple this seam test to list position: locate the
                # actual translation prompt by its fixed marker.
                content = next(
                    message["content"]
                    for message in kwargs["json"]["messages"]
                    if "TARGET: " in message.get("content", "")
                )
                match = re.search(r"TARGET: (\[.*?\])\nGLOSSARY:", content, re.S)
                targets = json.loads(match.group(1))
                translations = [{"id": item["id"], "text": "olá"} for item in targets]
                response = Response()
                response.body = {"message": {"content": json.dumps({"translations": translations})}}
                return response

            old_url, old_model = os.environ.get("TRANSLATOR_OLLAMA_URL"), os.environ.get("TRANSLATOR_OLLAMA_MODEL")
            os.environ["TRANSLATOR_OLLAMA_URL"] = "http://offline-fake"
            os.environ["TRANSLATOR_OLLAMA_MODEL"] = "qwen3.5:9b"
            try:
                context = {
                    "execution_mode": "TEST_FAKE", "operation_id": "op-memory-seam",
                    "checkpoint_root": root / "state", "memory_root": memory.parent,
                    "episode_id": 79, "anime_series_id": 1, "model": "qwen3.5:9b",
                    "candidate_commit": "candidate", "hard_call_budget": 242,
                }
                with patch.object(frozen_v225, "translate_subtitle_file_v2_2_5", seam_wrapper), \
                     patch.object(frozen_v225.V225MemoryClient, "call", client_wrapper), \
                     patch.object(pipeline.requests, "post", fake_post):
                    result = CanonicalV226LiveMaterializer().materialize(source, root / "base.ass", context=context)
            finally:
                if old_url is None:
                    os.environ.pop("TRANSLATOR_OLLAMA_URL", None)
                else:
                    os.environ["TRANSLATOR_OLLAMA_URL"] = old_url
                if old_model is None:
                    os.environ.pop("TRANSLATOR_OLLAMA_MODEL", None)
                else:
                    os.environ["TRANSLATOR_OLLAMA_MODEL"] = old_model
            self.assertEqual(client_calls["count"], 1)
            self.assertEqual(Path(seam_kwargs["memory_db_root"]).resolve(), memory.parent.resolve())
            self.assertNotIn("memory_root", seam_kwargs)
            self.assertTrue((Path(result["checkpoint"]) / "COMPLETE").is_file())

    def test_memory_root_conflict_fails_closed_and_context_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="v238-memory-roots-") as raw:
            root = Path(raw)
            source = root / "source.ass"
            source.write_text(ASS, encoding="utf-8")
            calls = []

            def fake_v226(src, dst, **kwargs):
                calls.append(kwargs)
                Path(dst).write_bytes(Path(src).read_bytes())
                return {"model": "qwen3.5:9b", "calls": [], "results": [{"id": 0, "status": "resolved"}]}

            from unittest.mock import patch
            materializer = CanonicalV226LiveMaterializer()
            common = {"execution_mode": "TEST_FAKE", "operation_id": "op-memory-roots",
                      "checkpoint_root": root / "state", "episode_id": 79,
                      "anime_series_id": 1, "model": "qwen3.5:9b",
                      "candidate_commit": "candidate", "model_digest": "qwen-digest",
                      "operation_budget": "budget-object"}
            with patch("v238_base_materializer.translate_subtitle_file_v2_2_6", fake_v226):
                with self.assertRaises(BaseTranslationMaterializerError) as error:
                    materializer.materialize(source, root / "diverged.ass", context={**common, "memory_root": root / "a", "memory_db_root": root / "b"})
                self.assertIn("V238_MEMORY_ROOTS_DIVERGE", str(error.exception))
                result = materializer.materialize(source, root / "equal.ass", context={**common, "memory_root": root / "same", "memory_db_root": root / "same"})
            self.assertEqual(len(calls), 1)
            self.assertEqual(Path(calls[0]["memory_db_root"]).resolve(), (root / "same").resolve())
            self.assertNotIn("memory_root", calls[0])
            self.assertEqual(calls[0]["execution_context"]["operation_budget"], "budget-object")
            self.assertEqual(calls[0]["execution_context"]["model_digest"], "qwen-digest")
            self.assertEqual(result["checkpoint_created"], 1)

    def test_identity_ownership_renders_styled_spans_with_asterisk_tags(self):
        """Identity mapping must render properly when override blocks contain Aegisub/karaoke asterisks."""
        source_text = r"{\blur2.25\3c&H6DD7E1&}Y{*\3c&H72D6DB&}o{*\3c&H77D5D5&}u {*\3c&H80D2C8&}t{*\3c&H85D1C2&}a{\3c&H8AD0BC&}u"
        program, details = extract_semantic_style_ownership(source_text, program_id="test", envelope_id=0)
        self.assertTrue(details.get("valid"))
        mapping, trace = identity_ownership_mapping(program, program.source_visible_text)
        rendered, validation = render_target_ownership(source_text, program.source_visible_text, program, mapping, line_break_template=source_text)
        self.assertTrue(validation.get("valid"))
        self.assertIsNotNone(rendered)
        self.assertNotIn("{*}", rendered)

    def test_line_break_inside_word_accepts_legitimate_short_words(self):
        """Legitimate short functional words around an ASS visual break must not trigger LINE_BREAK_INSIDE_WORD."""
        from pipeline_v2_1_3 import line_break_inside_word
        from production_v2_2_2_adapter import _has_unsafe_break

        self.assertFalse(line_break_inside_word(r"Festival de Música\Nda Escola Towa"))
        self.assertFalse(line_break_inside_word(r"Festival de Música da\NEscola Towa"))
        self.assertFalse(line_break_inside_word(r"O livro\Ndo meu pai"))
        self.assertFalse(line_break_inside_word(r"Wombat, e\N\N\Nlogo voltou"))
        self.assertFalse(_has_unsafe_break(r"Festival de Música\Nda Escola Towa"))
        self.assertFalse(_has_unsafe_break(r"Festival de Música da\NEscola Towa"))
        self.assertTrue(line_break_inside_word(r"vi\Nda"))

    def test_visual_glyph_primary_tokens_cleanup_and_fallback(self):
        """Visual glyph primary token removal cleans empty asterisk tags and falls back safely."""
        from v235_visual_glyph_program import _remove_primary_tokens
        from v238_full_translation_stage import _render_event

        source_text = r"{\blur0.75\fnCorbert\fs45\b1\pos(956.565,713.22)\1c&HC0BFE4&\fscx93\fscy93}T{*\1c&HBDC2DB&}w{*\1c&HBBC6D3&}o {*\1c&HB5CDC2&}o{*\1c&HB3D0B9&}f {*\1c&HADD7A8&}a {*\1c&HA8DE97&}K{*\1c&HA5E18E&}i{*\1c&HA3E586&}n{\1c&HA0E87D&}d"
        removed = _remove_primary_tokens(source_text)
        self.assertNotIn("{*}", removed)

        target_text = r"{\blur0.75\fnCorbert\fs45\b1\pos(956.565,713.22)\1c&HC0BFE4&\fscx93\fscy93}{*\1c&HBDC2DB&}Dois{*\1c&HBBC6D3&}{*\1c&HB5CDC2&}{*\1c&HB3D0B9&} {*\1c&HADD7A8&}Iguais{*\1c&HA8DE97&}{*\1c&HA5E18E&}{*\1c&HA3E586&}{\1c&HA0E87D&}"
        counters = {"source_payload": 0, "visual_detector": 0, "visual_reconstruction": 0, "styled_span_detector": 0, "semantic_ownership_detector": 0}
        provider = unittest.mock.NonCallableMagicMock(spec=[])
        rendered, details = _render_event(
            source_text, target_text, event_id=2592, provider=provider, model="test", counters=counters,
        )
        self.assertIsNotNone(rendered)
        self.assertEqual(details["path"], "VISUAL_GLYPH")

        with unittest.mock.patch("v238_full_translation_stage.reconstruct_visual_glyph_envelope", return_value=(None, {"valid": False})):
            fallback_rendered, fallback_details = _render_event(
                source_text, target_text, event_id=2592, provider=provider, model="test", counters=counters,
            )
            self.assertEqual(fallback_details["path"], "VISUAL_GLYPH_BASE_FALLBACK")
            self.assertEqual(fallback_rendered, target_text)
            self.assertEqual(counters.get("visual_glyph_fallback"), 1)


if __name__ == "__main__":
    unittest.main()
