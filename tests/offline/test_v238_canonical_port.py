from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path


class V238CanonicalPortTests(unittest.TestCase):
    def test_registry_plan_and_unknown_fail_closed(self):
        from pipeline_registry import UnsupportedPipelineError, resolve_pipeline

        plan = resolve_pipeline("v2_3_8")
        self.assertEqual(plan.stages, ("FULL_TRANSLATION_V238", "KARAOKE_AUGMENTATION_V230"))
        with self.assertRaises(UnsupportedPipelineError):
            resolve_pipeline("v2_3_8_unknown")

    def test_runtime_import_closure(self):
        modules = (
            "v238_source_payload", "v236_durable_response_capture", "v233_styled_spans",
            "v235_visual_glyph_program", "v237_temporal_transform", "v238_semantic_style_ownership",
            "v238_rc3_atom_owner_vector", "v238_rc4_sparse_visual_eligibility",
            "v238_rc5_contiguous_semantic_runs", "v238_rc6_finite_selector",
            "v238_rc7_factorized_selector", "v238_rc8_pairwise_boundary",
            "v238_rc9_independent_span", "v238_rc10_anchor_solver",
            "v238_full_translation_stage", "production_v2_3_8_adapter",
        )
        for name in modules:
            self.assertIsNotNone(importlib.import_module(name), name)

    def test_capture_roundtrip_is_generic_and_durable(self):
        from v236_durable_response_capture import DurableResponseCaptureV1

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            capture = DurableResponseCaptureV1(root / "call", call_id="synthetic")
            request = {"id": "synthetic", "source": "synthetic", "model": "configured"}
            state = capture.prepare(request, {"purpose": "offline-test"})
            self.assertEqual(state["state"], "REQUEST_DURABLE")
            self.assertTrue((root / "call" / "request_payload.json").exists())
            self.assertEqual(capture.reconcile()["next_action"], "EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND")

    def test_generic_ass_contract_is_deterministic(self):
        from v238_full_translation_stage import validate_v238_candidate

        ass = """[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\\\\b1}synthetic\n"""
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.ass"
            candidate = Path(raw) / "candidate.ass"
            source.write_text(ass, encoding="utf-8")
            candidate.write_text(ass, encoding="utf-8")
            result = validate_v238_candidate(source, candidate)
            self.assertEqual(result["stage_id"], "FULL_TRANSLATION_V238")
            self.assertTrue(result["durable_intermediate"])
            self.assertFalse(result["publishable"])

    def test_runtime_has_no_fixed_episode_literals(self):
        root = Path(__file__).resolve().parents[2] / "src" / "subtranslate"
        forbidden = ("zombieland", "source_record130", "staging_v238", "3314", "3315", "3317")
        for path in root.glob("v238*.py"):
            text = path.read_text(encoding="utf-8").casefold()
            self.assertFalse(any(token in text for token in forbidden), path.name)


if __name__ == "__main__":
    unittest.main()
