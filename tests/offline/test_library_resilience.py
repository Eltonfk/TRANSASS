"""Offline tests for resilient library episode resolution and reconciliation."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import app as app_module
from anime_subtitle_library import AnimeSubtitleLibrary


class LibraryResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="transass-test-resilience-"))
        self.lib_dir = self.tmp / "library"
        self.media_dir = self.tmp / "media"
        self.shows_dir = self.media_dir / "shows"
        self.shows_dir.mkdir(parents=True)
        self.lib = AnimeSubtitleLibrary(self.lib_dir, media_roots=[self.shows_dir])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_season_episode_formats(self):
        # SxxExx in filename
        self.assertEqual(
            app_module._parse_season_episode("Full-Time Magister - S01E02 - The Release of The Seal HDTV-1080p.mkv"),
            ("01", "02")
        )
        # SxxExx with path
        self.assertEqual(
            app_module._parse_season_episode(Path("Show/Season 1/Show - S02E15.mkv")),
            ("02", "15")
        )
        # Parent folder Season + episode number prefix in filename
        self.assertEqual(
            app_module._parse_season_episode(Path("Show/Season 1/02 - The Release of The Seal.mkv")),
            ("01", "02")
        )
        # Temporada + EPxx
        self.assertEqual(
            app_module._parse_season_episode(Path("Anime/Temporada 2/EP05 - Battle.mp4")),
            ("02", "05")
        )
        # S3 folder + number
        self.assertEqual(
            app_module._parse_season_episode(Path("Anime/S3/10.mkv")),
            ("03", "10")
        )
        # Unparseable
        self.assertEqual(
            app_module._parse_season_episode("Random Movie.mkv"),
            (None, None)
        )

    def test_register_episode_reconciles_renamed_or_upgraded_release(self):
        series = self.lib.register_series("Test Anime", "Test Anime", classification="ANIME")
        series_id = series["id"]

        old_file = self.shows_dir / "Test Anime" / "Season 1" / "Test Anime - S01E02 WEBDL-1080p.mkv"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_bytes(b"old")

        ep1 = self.lib.register_episode_for_path(
            series_id, old_file, season="01", episode="02", episode_title="Episode 2"
        )
        ep1_id = ep1["id"]
        self.assertEqual(ep1["media_filename"], "Test Anime - S01E02 WEBDL-1080p.mkv")

        # Now replace with upgraded HDTV release
        new_file = self.shows_dir / "Test Anime" / "Season 1" / "Test Anime - S01E02 HDTV-1080p.mkv"
        new_file.write_bytes(b"new")

        ep2 = self.lib.register_episode_for_path(
            series_id, new_file, season="01", episode="02", episode_title="Episode 2 (HDTV)"
        )
        # Same episode ID must be preserved
        self.assertEqual(ep2["id"], ep1_id)
        self.assertEqual(ep2["media_filename"], "Test Anime - S01E02 HDTV-1080p.mkv")
        self.assertEqual(
            ep2["media_relative_path"],
            "Test Anime/Season 1/Test Anime - S01E02 HDTV-1080p.mkv"
        )

    def test_library_episode_for_video_resilient_fallback_and_require_v3(self):
        series = self.lib.register_series("Full-Time Magister", "Full-Time Magister", classification="ANIME")
        series_id = series["id"]

        # Register episode 2 with old WEBDL path
        old_path = "Full-Time Magister/Season 1/Full-Time Magister - S01E02 - The Release of The Seal WEBDL-1080p.mkv"
        ep = self.lib.register_episode(
            series_id,
            season="01",
            episode="02",
            episode_title="Full-Time Magister - S01E02 - The Release of The Seal WEBDL-1080p",
            media_relative_path=old_path,
            media_filename="Full-Time Magister - S01E02 - The Release of The Seal WEBDL-1080p.mkv",
        )
        orig_id = ep["id"]

        # File on disk is now HDTV
        new_video = self.shows_dir / "Full-Time Magister" / "Season 1" / "Full-Time Magister - S01E02 - The Release of The Seal HDTV-1080p.mkv"
        new_video.parent.mkdir(parents=True, exist_ok=True)
        new_video.write_bytes(b"video")

        with patch.object(app_module, "subtitle_library", self.lib), \
             patch.object(app_module, "BASE_LIBRARY", self.shows_dir):
            resolved = app_module._library_episode_for_video(new_video)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["id"], orig_id)
            self.assertEqual(
                resolved["media_relative_path"],
                "Full-Time Magister/Season 1/Full-Time Magister - S01E02 - The Release of The Seal HDTV-1080p.mkv"
            )

            # Test _require_v3_library_episode succeeds with this resolved episode
            job = {}
            v3_ep = app_module._require_v3_library_episode(new_video, job)
            self.assertEqual(v3_ep["id"], orig_id)
            self.assertEqual(job["episode_id"], orig_id)
            self.assertEqual(job["anime_series_id"], series_id)

    def test_library_episode_for_video_auto_registers_new_episode_in_anime_series(self):
        series = self.lib.register_series("Bleach", "Bleach", classification="ANIME")
        series_id = series["id"]

        new_video = self.shows_dir / "Bleach" / "Season 1" / "Bleach - S01E01 - Day 1.mkv"
        new_video.parent.mkdir(parents=True, exist_ok=True)
        new_video.write_bytes(b"bleach")

        with patch.object(app_module, "subtitle_library", self.lib), \
             patch.object(app_module, "BASE_LIBRARY", self.shows_dir):
            resolved = app_module._library_episode_for_video(new_video)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["series_id"], series_id)
            self.assertEqual(resolved["season"], "01")
            self.assertEqual(resolved["episode"], "01")
            self.assertEqual(resolved["media_relative_path"], "Bleach/Season 1/Bleach - S01E01 - Day 1.mkv")

    def test_library_episode_for_video_fails_closed_for_non_anime(self):
        series = self.lib.register_series("Breaking Bad", "Breaking Bad", classification="NON_ANIME")
        video = self.shows_dir / "Breaking Bad" / "Season 1" / "Breaking Bad - S01E01.mkv"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"bb")

        with patch.object(app_module, "subtitle_library", self.lib), \
             patch.object(app_module, "BASE_LIBRARY", self.shows_dir):
            resolved = app_module._library_episode_for_video(video)
            self.assertIsNone(resolved)
            job = {}
            with self.assertRaisesRegex(RuntimeError, "V3_LIBRARY_EPISODE_NOT_REGISTERED"):
                app_module._require_v3_library_episode(video, job)

    def test_is_untranslated_source_copy_exempts_pure_numeric_and_symbolic_lines(self):
        from pipeline_v3 import _is_untranslated_source_copy

        # Pure numbers and punctuation (like Full-Time Magister E02 event 210: "5... 6... 7!")
        self.assertFalse(_is_untranslated_source_copy("5... 6... 7!", "5... 6... 7!", "inglês"))
        self.assertFalse(_is_untranslated_source_copy("1, 2, 3.", "1, 2, 3.", "inglês"))
        self.assertFalse(_is_untranslated_source_copy("100%!", "100%!", "inglês"))
        self.assertFalse(_is_untranslated_source_copy("♪ ... ♪", "♪ ... ♪", "inglês"))

        # Actual English text copies must still be detected
        self.assertTrue(_is_untranslated_source_copy("Hello!", "Hello!", "inglês"))
        self.assertTrue(_is_untranslated_source_copy("Run away!", "Run away!", "inglês"))

