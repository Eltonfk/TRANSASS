import json
import tempfile
import unittest
from pathlib import Path

from anime_subtitle_library import AnimeSubtitleLibrary
from pipeline_lineage import (
    KARAOKE_AUGMENTED_FROM,
    LineageContractError,
    TRANSLATED_FROM,
    archive_v230_records,
    public_summary,
)


class DurableStageLineageTests(unittest.TestCase):
    def test_lineage_module_is_in_candidate_docker_copy(self):
        dockerfile = Path(__file__).parents[1] / "Dockerfile"
        self.assertIn("pipeline_lineage.py", dockerfile.read_text(encoding="utf-8"))

    def _fixture(self):
        root = Path(tempfile.mkdtemp())
        media = root / "media"
        media.mkdir()
        source = media / "source.ass"
        stage = media / "stage.ass"
        final = media / "final.ass"
        source.write_text("[Script Info]\n[Events]\n", encoding="utf-8")
        stage.write_text("stage", encoding="utf-8")
        final.write_text("final", encoding="utf-8")
        library = AnimeSubtitleLibrary(root / "library", media_roots=[media])
        series = library.register_series("technical", "series", classification="ANIME")
        episode = library.register_episode(
            int(series["id"]), season="01", episode="01", episode_title="technical",
            media_relative_path="series/video.mkv", media_filename="video.mkv",
        )
        source_record = library.ingest_file(
            source, episode_id=int(episode["id"]), language="eng", source_kind="EXTRACTED",
            source_language="eng", validation_status="VALIDATED", review_status="VALIDATED",
            require_authorized_path=False,
        )
        return root, media, source, stage, final, library, source_record

    def test_v230_edges_point_to_exact_stage_record(self):
        root, _media, _source, stage, final, library, source_record = self._fixture()
        try:
            result = archive_v230_records(
                library, source_record=source_record, stage_artifact=stage,
                final_output=final, stage_summary={"events": 1}, final_summary={"events": 1},
                publish=False,
            )
            stage_id = result["stage_record_id"]
            final_id = result["final_record_id"]
            edges = library.lineage(final_id)
            final_edges = {(r["parent_record_id"], r["relation_type"]) for r in edges if r["source_record_id"] == final_id}
            self.assertIn((source_record["id"], TRANSLATED_FROM), final_edges)
            self.assertIn((stage_id, KARAOKE_AUGMENTED_FROM), final_edges)
            self.assertNotIn((source_record["id"], KARAOKE_AUGMENTED_FROM), final_edges)
            stage_edges = {(r["parent_record_id"], r["relation_type"]) for r in library.lineage(stage_id) if r["source_record_id"] == stage_id}
            self.assertEqual(stage_edges, {(source_record["id"], TRANSLATED_FROM)})
            self.assertEqual(library.get_record(stage_id)["pipeline_version"], "v2_2_6")
            json.dumps(public_summary({"_internal": {"stage_artifact_path": "/secret/path"}, "output": "final.ass"}))
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_stage_hash_change_fails_closed(self):
        root, _media, _source, stage, final, library, source_record = self._fixture()
        try:
            expected = __import__("hashlib").sha256(stage.read_bytes()).hexdigest()
            stage.write_text("changed", encoding="utf-8")
            with self.assertRaises(LineageContractError):
                archive_v230_records(
                    library, source_record=source_record, stage_artifact=stage,
                    final_output=final, stage_summary={"events": 1}, final_summary={"events": 1},
                    expected_stage_sha256=expected, publish=False,
                )
            records = library.list_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source_kind"], "EXTRACTED")
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)

    def test_retranslation_keeps_augmentation_and_old_record_edges_separate(self):
        root, _media, source, stage, final, library, source_record = self._fixture()
        try:
            old = library.ingest_file(
                final, episode_id=int(source_record["episode_id"]), language="pt-BR",
                source_kind="TRANSLATED", validation_status="VALIDATED", review_status="VALIDATED",
                require_authorized_path=False,
            )
            result = archive_v230_records(
                library, source_record=source_record, stage_artifact=stage,
                final_output=final, stage_summary={"events": 1}, final_summary={"events": 1},
                publish=False, retranslated_from=int(old["id"]),
            )
            edges = {(r["parent_record_id"], r["relation_type"]) for r in library.lineage(result["final_record_id"]) if r["source_record_id"] == result["final_record_id"]}
            self.assertIn((int(source_record["id"]), TRANSLATED_FROM), edges)
            self.assertIn((result["stage_record_id"], KARAOKE_AUGMENTED_FROM), edges)
            self.assertIn((int(old["id"]), "RETRANSLATED_FROM"), edges)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
