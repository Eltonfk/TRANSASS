"""Offline tests for automatic ANIME classification by embedded ASS/SSA.

These tests never call ffprobe/ffmpeg or touch a real Library. They exercise the
pure classification decision and the Library write paths via mocks.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

# The classification logic lives in app.py, which imports Flask at module
# level. Use the real dependency when available; only provide the historical
# minimal shim in environments where Flask is genuinely absent. Unconditional
# injection leaked a fake module into later tests and hid real integration
# regressions.
try:
    import flask as _flask  # noqa: F401
except ImportError:
    _fake_flask = types.ModuleType("flask")
    _fake_flask.Flask = MagicMock()
    _fake_flask.Response = MagicMock()
    _fake_flask.jsonify = MagicMock()
    _fake_flask.request = MagicMock()
    _fake_flask.send_file = MagicMock()
    sys.modules["flask"] = _fake_flask

_TMP = tempfile.TemporaryDirectory()
os.environ["TRANSLATOR_WEB_STATE_DIR"] = _TMP.name
os.environ["ANIME_SUBTITLE_LIBRARY_ROOT"] = os.path.join(_TMP.name, "lib")
os.environ["ANIME_LIBRARY_ROOTS"] = os.path.join(_TMP.name, "media")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import web_audit_retranslation  # noqa: E402
import app as app_module  # noqa: E402


def _ass_track(lang="francês"):
    return {"index": 3, "codec": "ass", "language": lang, "title": f"{lang} [Full]", "forced": False, "default": True, "textual": True, "bitmap": False}


def _pgs_track():
    return {"index": 3, "codec": "hdmv_pgs_subtitle", "language": "und", "title": "", "forced": False, "default": True, "textual": False, "bitmap": True}


def _run(scenario_series, tracks, folder="Show/Season 1"):
    lib = MagicMock()
    lib.list_series.return_value = scenario_series
    lib.set_classification.return_value = {"id": 7}
    lib.register_series.return_value = {"id": 99}
    video_source = "/x/Show/Season 1/S01E01.mkv"
    with patch.object(app_module, "subtitle_library", lib), \
         patch.object(app_module, "_validate_folder", return_value=Path("/x/Show/Season 1")), \
         patch.object(app_module, "_resolve_relative", side_effect=lambda r: Path(r)), \
         patch.object(app_module, "_episode_records", return_value=[{"source": video_source, "name": "S01E01.mkv"}]), \
         patch.object(web_audit_retranslation, "detect_source_options", return_value=tracks):
        return app_module._auto_classify_anime(folder), lib


def test_unknown_promoted_to_anime_and_episodes_registered():
    result, lib = _run([{"library_relative_path": "Show", "classification": "UNKNOWN", "id": 7}], [_ass_track()])
    assert result["classified"] is True
    assert result["classification"] == "ANIME"
    lib.set_classification.assert_called_once_with(7, "ANIME", source="AUTO_EMBEDDED_SUBTITLE")
    lib.register_episode_for_path.assert_called_once()
    assert result["episodes_registered"] == 1


def test_explicit_non_anime_respected():
    result, lib = _run([{"library_relative_path": "Show", "classification": "NON_ANIME", "id": 7}], [_ass_track()])
    assert result["classified"] is False
    assert result["reason"] == "explicit_non_anime"
    lib.set_classification.assert_not_called()
    lib.register_episode_for_path.assert_not_called()


def test_existing_anime_no_extra_classification():
    result, lib = _run([{"library_relative_path": "Show", "classification": "ANIME", "id": 7}], [_ass_track()])
    assert result["classified"] is True
    lib.set_classification.assert_not_called()
    lib.register_episode_for_path.assert_called_once()


def test_new_series_registered_as_anime():
    result, lib = _run([], [_ass_track()])
    assert result["classified"] is True
    lib.register_series.assert_called_once()
    _, kwargs = lib.register_series.call_args
    assert kwargs.get("classification") == "ANIME"
    assert kwargs.get("source") == "AUTO_EMBEDDED_SUBTITLE"
    lib.register_episode_for_path.assert_called_once()


def test_no_embedded_ass_not_classified():
    result, lib = _run([{"library_relative_path": "Show", "classification": "UNKNOWN", "id": 7}], [_pgs_track()])
    assert result["classified"] is False
    assert result["reason"] == "no_embedded_ass_ssa"
    lib.set_classification.assert_not_called()
    lib.register_episode_for_path.assert_not_called()


def test_no_videos_not_classified():
    lib = MagicMock()
    lib.list_series.return_value = []
    with patch.object(app_module, "subtitle_library", lib), \
         patch.object(app_module, "_validate_folder", return_value=Path("/x/Show/Season 1")), \
         patch.object(app_module, "_resolve_relative", side_effect=lambda r: Path(r)), \
         patch.object(app_module, "_episode_records", return_value=[]):
        result = app_module._auto_classify_anime("Show/Season 1")
    assert result["classified"] is False
    assert result["reason"] == "no_videos"
    lib.register_series.assert_not_called()
