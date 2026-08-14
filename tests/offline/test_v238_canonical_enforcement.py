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


ASS = """[Script Info]
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,2,1,2,2,10,10,10,1
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
                content = kwargs["json"]["messages"][0]["content"]
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


if __name__ == "__main__":
    unittest.main()
