import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_subtitle_library import AnimeSubtitleLibrary, LineageIntegrityError
from pipeline_lineage import LineageContractError, archive_v230_records


ASS = "[Script Info]\n[Events]\n"


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

    def test_retranslation_wrong_episode_rejected_before_orchestrator(self):
        try:
            import app
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
