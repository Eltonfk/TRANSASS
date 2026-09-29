import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_subtitle_library import AnimeSubtitleLibrary, LineageIntegrityError
from pipeline_lineage import LineageContractError, archive_v230_records


ASS = "[Script Info]\n[Events]\n"


def _load_app_isolated(root: Path):
    """Import the web app with all runtime paths confined to this fixture."""
    if "app" in sys.modules:
        return sys.modules["app"]
    with patch.dict(os.environ, {
        "TRANSASS_DATA_DIR": str(root / "app-data"),
        "TRANSLATOR_WEB_STATE_DIR": str(root / "app-state"),
        "TRANSLATOR_BASE_LIBRARY": str(root / "media"),
    }):
        import app
    return app


class P2B3BIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="p2b3b-integrity-"))
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "a.mkv").write_bytes(b"a")
        (self.media / "b.mkv").write_bytes(b"b")
        self.lib = AnimeSubtitleLibrary(self.root / "library", media_roots=[self.media])
        series = self.lib.register_series("fixture", "fixture", classification="ANIME")
        self.ep_a = self.lib.register_episode_for_path(series["id"], self.media / "a.mkv", season="S", episode="A", episode_title="A")
        self.ep_b = self.lib.register_episode_for_path(series["id"], self.media / "b.mkv", season="S", episode="B", episode_title="B")
        source = self.root / "source.ass"
        source.write_text(ASS, encoding="utf-8")
        self.a1 = self.lib.ingest_file(source, episode_id=self.ep_a["id"], language="eng", source_kind="EXTRACTED", source_language="eng", require_authorized_path=False)
        self.a2 = self.lib.ingest_file(source, episode_id=self.ep_a["id"], language="pt-BR", source_kind="TRANSLATED", source_language="eng", require_authorized_path=False)
        self.b1 = self.lib.ingest_file(source, episode_id=self.ep_b["id"], language="pt-BR", source_kind="TRANSLATED", source_language="eng", require_authorized_path=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_all_supported_relations_stay_within_episode(self):
        for relation in ("TRANSLATED_FROM", "AUGMENTED_FROM", "KARAOKE_AUGMENTED_FROM", "RETRANSLATED_FROM"):
            self.lib.add_lineage(self.a2["id"], self.a1["id"], relation)
        self.assertEqual(len(self.lib.lineage(self.a2["id"])), 4)

    def test_cross_episode_and_missing_records_fail_closed(self):
        for relation in ("TRANSLATED_FROM", "AUGMENTED_FROM", "KARAOKE_AUGMENTED_FROM", "RETRANSLATED_FROM"):
            with self.assertRaises(LineageIntegrityError) as ctx:
                self.lib.add_lineage(self.a2["id"], self.b1["id"], relation)
            self.assertEqual(ctx.exception.code, "lineage_episode_mismatch")
        for child, parent, code in ((self.a2["id"], 999999, "lineage_parent_record_missing"), (999999, self.a1["id"], "lineage_child_record_missing")):
            with self.assertRaises(LineageIntegrityError) as ctx:
                self.lib.add_lineage(child, parent, "TRANSLATED_FROM")
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(len(self.lib.lineage(self.a2["id"])), 0)

    def test_publish_rejects_concurrent_destination_without_overwriting(self):
        from anime_subtitle_library import PublicationConflict

        candidate = self.root / "candidate.ass"
        candidate.write_text("new translated bytes", encoding="utf-8")
        record = self.lib.ingest_file(
            candidate, episode_id=self.ep_a["id"], language="pt-BR",
            source_kind="TRANSLATED", validation_status="VALIDATED",
            review_status="VALIDATED", require_authorized_path=False,
        )
        destination = (self.media / "a.mkv").with_suffix(".pt-BR.ass")
        real_link = os.link

        def create_competing_file(staged, target):
            Path(target).write_bytes(b"concurrent operator file")
            return real_link(staged, target)

        with patch("anime_subtitle_library.os.link", side_effect=create_competing_file):
            with self.assertRaises(PublicationConflict):
                self.lib.publish(int(record["id"]), target_path=destination, allow_replace=False)

        self.assertEqual(destination.read_bytes(), b"concurrent operator file")

    def test_retranslation_wrong_episode_rejected_before_orchestrator(self):
        try:
            app = _load_app_isolated(self.root)
        except ModuleNotFoundError as exc:
            self.skipTest(f"candidate dependency unavailable outside image: {exc.name}")
        old_library = app.subtitle_library
        old_persist = app._persist_locked
        old_append = app._append_log
        before = len(self.lib.list_records())
        job = {
            "id": "wrong-episode", "episode_id": self.ep_a["id"],
            "source_record_id": self.a1["id"], "old_record_id": self.b1["id"],
            "source_abs": str(self.root / "missing.ass"), "name": "fixture",
            "status": "WAITING", "stage": "WAITING", "flags": [], "critical_flags": [],
        }
        try:
            app.subtitle_library = self.lib
            app._persist_locked = lambda: None
            app._append_log = lambda *args, **kwargs: None
            with patch.object(app.subprocess, "Popen", side_effect=AssertionError("orchestrator must not run")):
                app._run_retranslation_episode(job)
        finally:
            app.subtitle_library = old_library
            app._persist_locked = old_persist
            app._append_log = old_append
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["reason"], "retranslation_integrity_failed")
        self.assertEqual(len(self.lib.list_records()), before)
        self.assertEqual(len(self.lib.lineage(self.a2["id"])), 0)

    def test_retranslation_failure_preserves_primary_error_and_staging(self):
        try:
            app = _load_app_isolated(self.root)
        except ModuleNotFoundError as exc:
            self.skipTest(f"candidate dependency unavailable outside image: {exc.name}")

        class FailedProcess:
            def __init__(self, summary_line):
                self.stdout = iter([summary_line])

            @staticmethod
            def wait():
                return 1

        for retention_fails in (False, True):
            with self.subTest(retention_fails=retention_fails):
                job_id = f"failed-retranslation-{retention_fails}"
                state_dir = self.root / job_id / "state"
                ledger_dir = self.root / job_id / "ledger"
                summary = {
                    "reason": "v2_2_5_not_eligible",
                    "ledger_dir": str(ledger_dir),
                    "failure_snapshot": str(ledger_dir / "snapshot.json"),
                }
                line = "WEB_RETRANSLATION_SUMMARY " + json.dumps(summary)
                job = {
                    "id": job_id,
                    "name": "fixture.ass",
                    "source_abs": str(self.root / "source.ass"),
                    "status": "WAITING",
                    "stage": "WAITING",
                }

                def start_failed_process(command, **_kwargs):
                    output = Path(command[command.index("--output") + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text("partial candidate", encoding="utf-8")
                    return FailedProcess(line)

                state = {"process": None, "thermal_stop_requested": False, "stop_requested": False}
                retention_patch = (
                    patch.object(app, "retain_staging", side_effect=OSError("fixture retention failure"))
                    if retention_fails
                    else patch.object(app, "retain_staging", wraps=app.retain_staging)
                )
                with (
                    patch.object(app, "STATE_DIR", state_dir),
                    patch.object(app, "state", state),
                    patch.object(app, "_validate_retranslation_job_integrity"),
                    patch.object(app, "_effective_pipeline", return_value="v2_3_8"),
                    patch.object(app, "_apply_canonical_pipeline_summary"),
                    patch.object(app, "_summary_log_line", return_value="runner summary"),
                    patch.object(app, "_append_log"),
                    patch.object(app, "_persist_locked"),
                    patch.object(app.subprocess, "Popen", side_effect=start_failed_process),
                    retention_patch,
                ):
                    app._run_retranslation_episode(job)

                self.assertEqual(job["status"], "FAILED")
                self.assertEqual(job["reason"], "retranslation_failed")
                self.assertIn("v2_2_5_not_eligible", job["error"])
                self.assertEqual(job["failure_snapshot"], summary["failure_snapshot"])
                retained = Path(job["failure_staging_path"])
                self.assertTrue((retained / "fixture.ass.pt-BR.ass").is_file())
                if retention_fails:
                    self.assertIn("fixture retention failure", job["failure_artifact_retention_error"])
                else:
                    self.assertEqual(retained, ledger_dir / "staging")

    def _run_completed_fake_retranslation(self, app, *, episode, source_record, old_record, job_id):
        staged_source_root = self.root / f"source-staging-{job_id}"
        staged_source_root.mkdir()
        staged_source = staged_source_root / "source.ass"
        staged_source.write_bytes((self.root / "source.ass").read_bytes())
        state_dir = self.root / f"state-{job_id}"
        state = {"process": None, "thermal_stop_requested": False, "stop_requested": False}
        job = {
            "id": job_id,
            "name": f"episode-{episode['id']}",
            "source_abs": str(staged_source),
            "source_staging_path": str(staged_source_root),
            "source_record_id": int(source_record["id"]),
            "old_record_id": int(old_record["id"]),
            "episode_id": int(episode["id"]),
            "source_language": str(source_record.get("language") or "eng"),
            "status": "WAITING",
            "stage": "WAITING",
        }

        class CompletedProcess:
            def __init__(self, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"translated candidate bytes")
                self.stdout = iter(['WEB_RETRANSLATION_SUMMARY {"pipeline":"v3"}'])

            @staticmethod
            def wait():
                return 0

        def start_completed_process(command, **_kwargs):
            return CompletedProcess(Path(command[command.index("--output") + 1]))

        with (
            patch.object(app, "subtitle_library", self.lib),
            patch.object(app, "STATE_DIR", state_dir),
            patch.object(app, "state", state),
            patch.object(app, "_validate_retranslation_job_integrity"),
            patch.object(app, "_effective_pipeline", return_value="v3"),
            patch.object(app, "_apply_canonical_pipeline_summary"),
            patch.object(app, "_summary_log_line", return_value="runner summary"),
            patch.object(app, "audit_record", return_value={"status": "OK"}),
            patch.object(app, "archive_eligibility", return_value={"eligible_for_archive": True}),
            patch.object(app, "_append_log"),
            patch.object(app, "_persist_locked"),
            patch.object(app.subprocess, "Popen", side_effect=start_completed_process),
        ):
            app._run_retranslation_episode(job)

        return job, staged_source_root

    def test_retranslation_refuses_to_replace_existing_media_sidecar(self):
        try:
            app = _load_app_isolated(self.root)
        except ModuleNotFoundError as exc:
            self.skipTest(f"candidate dependency unavailable outside image: {exc.name}")
        destination = (self.media / "a.mkv").with_suffix(".pt-BR.ass")
        destination.write_bytes(b"previously published subtitle")
        french_source = self.lib.ingest_file(
            self.root / "source.ass", episode_id=int(self.ep_a["id"]), language="fre",
            source_kind="EXTRACTED", source_language="fre", require_authorized_path=False,
        )
        job, staged_source_root = self._run_completed_fake_retranslation(
            app, episode=self.ep_a, source_record=french_source, old_record=self.a2,
            job_id="retranslation-existing-publication",
        )

        self.assertEqual(job["status"], "COMPLETED")
        self.assertFalse(job["published"])
        self.assertEqual(job["reason"], "library_record_created_no_publication")
        self.assertIn("sidecar existente", job["error"])
        self.assertEqual(destination.read_bytes(), b"previously published subtitle")
        self.assertEqual(self.lib.publications(record_id=job["new_record_id"]), [])
        translated = self.lib.get_record(int(job["new_record_id"]))
        self.assertEqual(translated["source_language"], "fre")
        self.assertFalse(staged_source_root.exists())

    def test_retranslation_publishes_via_library_when_media_target_is_absent(self):
        try:
            app = _load_app_isolated(self.root)
        except ModuleNotFoundError as exc:
            self.skipTest(f"candidate dependency unavailable outside image: {exc.name}")
        source_path = self.root / "source.ass"
        source_record = self.lib.ingest_file(
            source_path, episode_id=int(self.ep_b["id"]), language="eng",
            source_kind="EXTRACTED", source_language="eng", require_authorized_path=False,
        )
        destination = (self.media / "b.mkv").with_suffix(".pt-BR.ass")
        self.assertFalse(destination.exists())
        job, staged_source_root = self._run_completed_fake_retranslation(
            app, episode=self.ep_b, source_record=source_record, old_record=self.b1,
            job_id="retranslation-new-publication",
        )

        self.assertEqual(job["status"], "COMPLETED")
        self.assertTrue(job["published"])
        self.assertEqual(job["reason"], "library_record_created_and_published")
        self.assertTrue(destination.is_file())
        publication_rows = self.lib.publications(record_id=job["new_record_id"])
        self.assertEqual(len(publication_rows), 1)
        self.assertEqual(publication_rows[0]["status"], "PUBLISHED")
        self.assertEqual(
            self.lib._hash_file(destination)[0],
            self.lib.get_record(int(job["new_record_id"]))["sha256"],
        )
        self.assertFalse(staged_source_root.exists())

    def test_v230_archive_revalidates_retranslated_parent_before_stage_commit(self):
        stage = self.root / "stage.ass"
        final = self.root / "final.ass"
        stage.write_text(ASS, encoding="utf-8")
        final.write_text(ASS, encoding="utf-8")
        before = len(self.lib.list_records())
        with self.assertRaises(LineageContractError):
            archive_v230_records(
                self.lib, source_record=self.a1, stage_artifact=stage, final_output=final,
                stage_summary={}, final_summary={}, publish=False, retranslated_from=self.b1["id"],
            )
        self.assertEqual(len(self.lib.list_records()), before)


if __name__ == "__main__":
    unittest.main()
