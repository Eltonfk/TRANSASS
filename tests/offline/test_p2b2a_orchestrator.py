import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline_orchestrator as orchestrator
from pipeline_lineage import public_summary
from pipeline_orchestrator import PipelineStageValidationError


def _v230(*, unsupported=0, failures=None, structural=None, song=0, translated=None):
    return {
        "song_units": song,
        "translated_units": song if translated is None else translated,
        "translated_events": song if translated is None else translated,
        "unsupported": unsupported,
        "failures": [] if failures is None else failures,
        "structural_failures": [] if structural is None else structural,
        "ollama_calls": 0,
    }


class V230DurableStageOrchestratorTests(unittest.TestCase):
    def _run(self, result, *, defer=True):
        def full(_plan, _source, output, _context):
            Path(output).write_text("V226", encoding="utf-8")
            return {"events": 2, "resolved": 2, "calls": 2, "retry_calls": 1}

        def importer(_name):
            def augment(_source, output, **_kwargs):
                Path(output).write_text("V230", encoding="utf-8")
                return result
            return SimpleNamespace(augment_karaoke_candidate_v2_3_0=augment)

        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        source, output = root / "source.ass", root / "final.ass"
        self._last_output = output
        source.write_text("source", encoding="utf-8")
        with patch.object(orchestrator, "_call_full_adapter", side_effect=full), patch.object(orchestrator.importlib, "import_module", side_effect=importer):
            return orchestrator.execute_pipeline_plan("v2_3_0", source, output, {"defer_intermediate_cleanup": defer}), output

    def test_success_retains_stage_for_persistence_and_public_summary_hides_path(self):
        result, output = self._run(_v230(song=2))
        self.assertTrue(output.is_file())
        self.assertIn("_internal", result)
        stage = Path(result["_internal"]["stage_artifact_path"])
        self.assertTrue(stage.is_file())
        public = public_summary(result)
        self.assertNotIn("_internal", public)
        self.assertNotIn("stage_artifact_path", json.dumps(public))
        self.assertEqual(public["output"], "final.ass")
        stage.unlink()

    def test_unsupported_or_partial_stage_fails_closed_and_cleans(self):
        for value in (_v230(unsupported=1), _v230(failures=[{"reason": "x"}]), _v230(song=2, translated=1), _v230(structural=[1])):
            with self.subTest(value=value):
                with self.assertRaises(PipelineStageValidationError):
                    self._run(value)
                self.assertFalse(self._last_output.exists())

    def test_zero_song_units_is_valid(self):
        result, output = self._run(_v230(song=0))
        self.assertTrue(output.exists())
        Path(result["_internal"]["stage_artifact_path"]).unlink()


if __name__ == "__main__":
    unittest.main()
