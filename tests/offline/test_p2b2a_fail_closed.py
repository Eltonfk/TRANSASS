import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline_orchestrator as orchestrator
from pipeline_lineage import LineageContractError, archive_v230_records
from pipeline_orchestrator import PipelineStageValidationError
from pipeline_registry import UnsupportedPipelineError, get_pipeline_plan


class FailClosedReplayTests(unittest.TestCase):
    def test_unknown_pipeline_fails_closed(self):
        with self.assertRaises(UnsupportedPipelineError):
            get_pipeline_plan("unknown-test-token")

    def test_v230_blockers_fail_closed_without_final(self):
        cases = (
            {"unsupported": 1, "song_units": 0, "translated_units": 0},
            {"unsupported": 0, "song_units": 2, "translated_units": 1},
            {"unsupported": 0, "song_units": 0, "translated_units": 0, "structural_failures": [1]},
            {"unsupported": 0, "song_units": 0, "translated_units": 0, "failures": [{"reason": "stage"}]},
        )
        for value in cases:
            value.setdefault("failures", [])
            value.setdefault("structural_failures", [])
            with self.subTest(value=value), tempfile.TemporaryDirectory() as td:
                root = Path(td); source, output = root / "s.ass", root / "f.ass"
                source.write_text("source", encoding="utf-8")
                def full(_plan, _source, out, _context):
                    Path(out).write_text("stage", encoding="utf-8")
                    return {"events": 1}
                def importer(_name):
                    def augment(_src, out, **_kwargs):
                        Path(out).write_text("final", encoding="utf-8")
                        return value
                    return SimpleNamespace(augment_karaoke_candidate_v2_3_0=augment)
                with patch.object(orchestrator, "_call_full_adapter", side_effect=full), patch.object(orchestrator.importlib, "import_module", side_effect=importer):
                    with self.assertRaises(PipelineStageValidationError):
                        orchestrator.execute_pipeline_plan("v2_3_0", source, output, {})
                self.assertFalse(output.exists())

    def test_missing_stage_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); final = root / "final.ass"; final.write_text("final", encoding="utf-8")
            class FakeLibrary:
                def ingest_file(self, *_args, **_kwargs):
                    raise AssertionError("stage ingest must not run without a stage file")
            with self.assertRaises(LineageContractError):
                archive_v230_records(FakeLibrary(), source_record={"id": 1, "episode_id": 1}, stage_artifact=root / "missing.ass", final_output=final, stage_summary={}, final_summary={}, publish=False)


if __name__ == "__main__":
    unittest.main()
