import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline_orchestrator as orchestrator
from pipeline_orchestrator import PipelineStageValidationError


def v230_result(*, song_units=0, translated_units=0, failures=None, unsupported=0, structural_failures=None):
    return {
        "song_units": song_units,
        "translated_units": translated_units,
        "translated_events": translated_units,
        "unsupported": unsupported,
        "failures": [] if failures is None else failures,
        "structural_failures": [] if structural_failures is None else structural_failures,
        "ollama_calls": 0,
        "input_sha256": "input",
        "output_sha256": "output",
    }


class V230EligibilityTests(unittest.TestCase):
    def _run(self, result):
        def full(plan_name, source, output, context=None, **kwargs):
            Path(output).write_text("intermediate", encoding="utf-8")
            return {
                "events": 10, "linguistic_events": 9, "resolved": 10, "failed": 0,
                "flags": {}, "critical_flags": [], "retry_budget": {"remaining": 2},
                "calls": 4, "retry_calls": 1, "total_ollama_calls": 4,
                "actual_retry_ollama_calls": 1, "pipeline": "v2_2_6", "output": Path(output).name,
            }

        def imports(name):
            def augment(source, output, **kwargs):
                Path(output).write_text("final", encoding="utf-8")
                return result
            return SimpleNamespace(augment_karaoke_candidate_v2_3_0=augment)

        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "source.ass", Path(tmp) / "final.ass"
            source.write_text("source", encoding="utf-8")
            with patch.object(orchestrator, "_call_full_adapter", side_effect=full), patch.object(orchestrator.importlib, "import_module", side_effect=imports):
                result = orchestrator.execute_pipeline_plan("v2_3_0", source, output, {})
                return result, output.exists()

    def test_all_success_passes_and_preserves_base_summary(self):
        result, output_exists = self._run(v230_result(song_units=2, translated_units=2))
        self.assertTrue(output_exists)
        self.assertEqual(result["pipeline"], "v2_3_0")
        self.assertEqual(result["plan_id"], "v2_3_0")
        self.assertEqual(result["output"], "final.ass")
        self.assertEqual(result["events"], 10)
        self.assertEqual(result["resolved"], 10)
        self.assertEqual(result["critical_flags"], [])
        self.assertEqual(result["retry_budget"], {"remaining": 2})
        self.assertEqual(result["retry_calls"], 1)
        self.assertEqual(result["calls"], 4)
        self.assertEqual(result["karaoke"]["song_units"], 2)
        json.dumps(result)

    def test_zero_song_units_is_valid(self):
        result, output_exists = self._run(v230_result())
        self.assertTrue(output_exists)
        self.assertEqual(result["karaoke"]["translated_units"], 0)

    def test_negative_cases_fail_closed(self):
        cases = [
            v230_result(unsupported=1),
            v230_result(failures=[{"reason": "KARAOKE_TRANSLATION_TIMING_UNSUPPORTED"}]),
            v230_result(structural_failures=[1]),
            v230_result(song_units=2, translated_units=1),
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(PipelineStageValidationError):
                    self._run(invalid)


class ContractAndControlPlaneTests(unittest.TestCase):
    def test_registry_archive_policy_and_neutral_model_metadata(self):
        from pipeline_registry import PLANS
        self.assertFalse(PLANS["legacy"].archive_translation)
        self.assertFalse(PLANS["v2_1_2"].archive_translation)
        for plan_id in ("v2_1_3", "v2_2_0", "v2_2_1", "v2_2_2", "v2_2_3", "v2_2_4", "v2_2_5", "v2_2_6", "v2_3_0"):
            self.assertTrue(PLANS[plan_id].archive_translation)
            self.assertIsNone(PLANS[plan_id].model_family)

    def test_canonical_summary_parser_wins(self):
        import app
        job = {"name": "technical", "summary": None, "flags": {}, "critical_flags": [], "progress": None, "stage": None, "status": None}
        canonical = {"pipeline": "v2_3_0", "plan_id": "v2_3_0", "events": 10, "resolved": 10, "flags": {"x": 1}, "critical_flags": [], "stages": [{"id": "KARAOKE_AUGMENTATION_V230"}]}
        app._parse_progress(job, "SUBTRANSLATE_PIPELINE_SUMMARY " + json.dumps(canonical))
        self.assertEqual(job["summary"], canonical)
        self.assertEqual(job["flags"], {"x": 1})
        self.assertEqual(job["progress"]["total"], 10)
        self.assertEqual(job["status"], "VALIDATING")
        self.assertEqual(job["stage"], "KARAOKE_AUGMENTATION_V230")

    def test_invalid_pipeline_health_separation_and_start_rejection(self):
        import app
        with patch.dict(os.environ, {"TRANSLATOR_PIPELINE": "invalid-for-test"}):
            info = app._pipeline_info()
            self.assertFalse(info["supported"])
            self.assertTrue(info["service_available"])
            self.assertFalse(info["service_available_for_mutation"])
            with tempfile.TemporaryDirectory() as tmp:
                old_base = app.BASE_LIBRARY
                try:
                    app.BASE_LIBRARY = Path(tmp)
                    (Path(tmp) / "source.mkv").write_bytes(b"x")
                    with app.app.test_client() as client, patch.object(app, "_start_worker_locked") as start_worker:
                        response = client.post("/start", json={"folder": ".", "episodes": ["source.mkv"]})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.get_json()["code"], "unsupported_pipeline")
                    start_worker.assert_not_called()
                finally:
                    app.BASE_LIBRARY = old_base

    def test_start_route_n3_adds_three_jobs_and_starts_once(self):
        import app
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = []
            for i in range(3):
                p = root / f"source-{i}.mkv"
                p.write_bytes(b"x")
                sources.append(p)
            old_base = app.BASE_LIBRARY
            old_env = os.environ.get("TRANSLATOR_PIPELINE")
            try:
                app.BASE_LIBRARY = root
                os.environ["TRANSLATOR_PIPELINE"] = "v2_3_0"
                with app.state_lock:
                    app.state.update({"running": False, "worker": None, "session_id": None, "jobs": [], "log": app.deque(maxlen=app.MAX_LOGS)})
                with app.app.test_client() as client, patch.object(app, "_persist_locked"), patch.object(app, "_start_worker_locked") as start_worker:
                    response = client.post("/start", json={"folder": ".", "episodes": [p.name for p in sources]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["queued"], 3)
                self.assertEqual(len(app.state["jobs"]), 3)
                self.assertEqual(len({job["id"] for job in app.state["jobs"]}), 3)
                start_worker.assert_called_once()
            finally:
                app.BASE_LIBRARY = old_base
                if old_env is None:
                    os.environ.pop("TRANSLATOR_PIPELINE", None)
                else:
                    os.environ["TRANSLATOR_PIPELINE"] = old_env

    def test_normal_archive_receives_final_v230_output(self):
        import anime_subtitle_translator as translator
        import anime_library_hooks
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "source.mkv"
            video.write_bytes(b"video")
            archived = []
            def execute(plan, source, output, context):
                Path(output).write_text("final", encoding="utf-8")
                return {"pipeline": plan, "plan_id": plan, "output": Path(output).name, "events": 1, "resolved": 1, "flags": {}, "critical_flags": [], "retry_budget": {}, "retry_calls": 0, "calls": 0, "stages": []}
            def archive_translation(*args, **kwargs):
                archived.append((args, kwargs))
            with patch.dict(os.environ, {"TRANSLATOR_PIPELINE": "v2_3_0"}), patch.object(translator, "TRANSLATOR_PIPELINE", "v2_3_0"), patch.object(translator, "VIDEO_EXTENSIONS", {".mkv"}), patch.object(translator, "has_pt_subtitle", return_value=False), patch.object(translator, "is_ready_for_translation", return_value=True), patch.object(translator, "find_subtitle_stream", return_value=(0, "eng", ".ass")), patch.object(translator, "extract_subtitle", side_effect=lambda v, i, p: Path(p).write_text("source", encoding="utf-8")), patch.object(translator, "load_glossary_for_folder", return_value={}), patch.object(anime_library_hooks, "archive_source", return_value={"series_id": 1, "episode_id": 2}), patch.object(anime_library_hooks, "archive_translation", side_effect=archive_translation), patch.object(translator, "execute_pipeline_plan", side_effect=execute):
                self.assertEqual(translator.process_folder(root), 0)
            self.assertEqual(len(archived), 1)
            self.assertEqual(Path(archived[0][0][1]).name, "source.pt-BR.ass")
            self.assertEqual(archived[0][1]["pipeline_version"], "v2_3_0")


class DockerClosureTests(unittest.TestCase):
    def test_required_modules_are_in_docker_copy(self):
        root = Path(__file__).parents[1]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        for name in ("pipeline_registry.py", "pipeline_orchestrator.py", "queue_helpers.py"):
            self.assertTrue((root / name).is_file())
            self.assertIn(name, dockerfile)


if __name__ == "__main__":
    unittest.main()
