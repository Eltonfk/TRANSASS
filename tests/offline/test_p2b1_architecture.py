import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pipeline_orchestrator as orchestrator
from pipeline_registry import (
    UnsupportedPipelineError,
    get_pipeline_plan,
)


class RegistryTests(unittest.TestCase):
    def test_known_plans_and_unknown_fail_closed(self):
        for plan_id in (
            "legacy", "v2_1_2", "v2_1_3", "v2_2_0", "v2_2_1", "v2_2_2",
            "v2_2_3", "v2_2_4", "v2_2_5", "v2_2_6", "v2_3_0",
        ):
            self.assertEqual(get_pipeline_plan(plan_id).id, plan_id)
        with self.assertRaises(UnsupportedPipelineError):
            get_pipeline_plan("v9_unknown")
        with self.assertRaises(UnsupportedPipelineError):
            get_pipeline_plan("")

    def test_legacy_requires_explicit_token(self):
        self.assertEqual(get_pipeline_plan("legacy").id, "legacy")
        with self.assertRaises(UnsupportedPipelineError):
            get_pipeline_plan("unknown")

    def test_v224_v225_v226_and_v230_plans(self):
        self.assertEqual(get_pipeline_plan("v2_2_4").stages, ("FULL_TRANSLATION_V224",))
        self.assertEqual(get_pipeline_plan("v2_2_5").stages, ("FULL_TRANSLATION_V225",))
        self.assertEqual(get_pipeline_plan("v2_2_6").stages, ("FULL_TRANSLATION_V226",))
        self.assertEqual(
            get_pipeline_plan("v2_3_0").stages,
            ("FULL_TRANSLATION_V226", "KARAOKE_AUGMENTATION_V230"),
        )


class DispatchTests(unittest.TestCase):
    def _dispatch_stub(self, plan_id):
        calls = []

        def full(plan_name, source, output, context=None, **kwargs):
            calls.append((plan_id, source, output, kwargs))
            Path(output).write_text("full", encoding="utf-8")
            return {"stage": plan_id}

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.ass"
            output = Path(tmp) / "output.ass"
            source.write_text("source", encoding="utf-8")
            with patch.object(orchestrator, "_call_full_adapter", side_effect=full):
                result = orchestrator.execute_pipeline_plan(plan_id, source, output, {})
            self.assertTrue(output.is_file())
            self.assertEqual(result["stages"][0]["id"], get_pipeline_plan(plan_id).stages[0])
            self.assertEqual(len(calls), 1)

    def test_v224_dispatches_v224(self):
        self._dispatch_stub("v2_2_4")

    def test_v225_dispatches_v225(self):
        self._dispatch_stub("v2_2_5")

    def test_v226_dispatches_v226(self):
        self._dispatch_stub("v2_2_6")

    def test_v230_calls_v226_then_v230(self):
        calls = []

        def full(plan_name, source, output, context=None, **kwargs):
            calls.append("v226")
            Path(output).write_text("intermediate", encoding="utf-8")
            return {"stage": "v226"}

        def import_fake(name):
            self.assertEqual(name, "production_v2_3_0_adapter")

            def augment(source, output, **kwargs):
                calls.append("v230")
                Path(output).write_text("final", encoding="utf-8")
                return {"stage": "v230"}

            return SimpleNamespace(augment_karaoke_candidate_v2_3_0=augment)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.ass"
            output = Path(tmp) / "output.ass"
            source.write_text("source", encoding="utf-8")
            with patch.object(orchestrator, "_call_full_adapter", side_effect=full), patch.object(orchestrator.importlib, "import_module", side_effect=import_fake):
                result = orchestrator.execute_pipeline_plan("v2_3_0", source, output, {})
            self.assertEqual(calls, ["v226", "v230"])
            self.assertTrue(output.is_file())
            self.assertEqual([x["id"] for x in result["stages"]], ["FULL_TRANSLATION_V226", "KARAOKE_AUGMENTATION_V230"])
            self.assertEqual(list(Path(tmp).glob(".p2b1-v226-*")), [])

    def test_v226_failure_skips_v230_and_final(self):
        calls = []

        def full(*args, **kwargs):
            calls.append("v226")
            raise RuntimeError("v226 failure")

        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "source.ass", Path(tmp) / "output.ass"
            source.write_text("source", encoding="utf-8")
            with patch.object(orchestrator, "_call_full_adapter", side_effect=full), patch.object(orchestrator.importlib, "import_module") as imported:
                with self.assertRaises(RuntimeError):
                    orchestrator.execute_pipeline_plan("v2_3_0", source, output, {})
            imported.assert_not_called()
            self.assertEqual(calls, ["v226"])
            self.assertFalse(output.exists())

    def test_v230_failure_cleans_intermediate_and_final(self):
        def full(plan_name, source, output, context=None, **kwargs):
            Path(output).write_text("intermediate", encoding="utf-8")
            return {"stage": "v226"}

        def import_fake(name):
            def augment(source, output, **kwargs):
                raise RuntimeError("v230 failure")
            return SimpleNamespace(augment_karaoke_candidate_v2_3_0=augment)

        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "source.ass", Path(tmp) / "output.ass"
            source.write_text("source", encoding="utf-8")
            with patch.object(orchestrator, "_call_full_adapter", side_effect=full), patch.object(orchestrator.importlib, "import_module", side_effect=import_fake):
                with self.assertRaises(RuntimeError):
                    orchestrator.execute_pipeline_plan("v2_3_0", source, output, {})
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(tmp).glob(".p2b1-v226-*")), [])


class QueueAndConvergenceTests(unittest.TestCase):
    def test_normal_and_retranslation_import_same_orchestrator(self):
        import anime_subtitle_translator as normal
        import web_retranslation_runner as retranslation
        self.assertIs(normal.execute_pipeline_plan, retranslation.execute_pipeline_plan)

    def test_queue_counts_are_exact_and_ids_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from queue_helpers import build_job_batch
            for n in (1, 2, 3, 12):
                sources = []
                for i in range(n):
                    p = root / f"source-{i}.ass"
                    p.write_text("x", encoding="utf-8")
                    sources.append(p)
                counter = iter(range(n))
                jobs = build_job_batch(
                    sources, session_id=f"session-{n}", folder=".", dry_run=False,
                    safe_relative=lambda p: p.name, friendly_number=lambda name: name,
                    now=lambda: "now", id_factory=lambda: f"job-{next(counter)}",
                )
                self.assertEqual(len(jobs), n)
                self.assertEqual(len({job["id"] for job in jobs}), n)

    def test_queue_persistence_reload_does_not_introduce_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from queue_helpers import build_job_batch
            sources = []
            for i in range(3):
                p = root / f"source-{i}.ass"
                p.write_text("x", encoding="utf-8")
                sources.append(p)
            counter = iter(("job-1", "job-2", "job-3"))
            jobs = build_job_batch(
                sources, session_id="session", folder=".", dry_run=False,
                safe_relative=lambda p: p.name, friendly_number=lambda name: name,
                now=lambda: "now", id_factory=lambda: next(counter),
            )
            state_file = root / "jobs.json"
            state_file.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            reloaded = json.loads(state_file.read_text(encoding="utf-8"))["jobs"]
            self.assertEqual(len(reloaded), 3)
            self.assertEqual(len({job["id"] for job in reloaded}), 3)


if __name__ == "__main__":
    unittest.main()
