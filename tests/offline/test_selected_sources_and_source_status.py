"""Offline tests for episode selection resolution and per-language source status.

Covers two fixes:
- Fix A: ``_selected_sources`` must accept both folder-relative names and full
  library-relative paths (the UI sends ``ep.source``), while staying fail-closed
  against path traversal outside the library root.
- Fix B: the source-status cache must be keyed per source language and the
  episode pipeline must forward the selected language instead of always using
  the global default ("inglês").

No ffprobe/ffmpeg, no real Library, no network.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

# app.py imports Flask at module level; inject a minimal fake so the test runs
# without Flask installed (same approach as test_auto_classify_anime.py).
_fake_flask = types.ModuleType("flask")
_fake_flask.Flask = MagicMock()
_fake_flask.Flask.return_value.route = lambda *a, **k: (lambda f: f)
_fake_flask.Response = MagicMock()
_fake_flask.jsonify = MagicMock()
_fake_flask.request = MagicMock()
_fake_flask.send_file = MagicMock()
sys.modules.setdefault("flask", _fake_flask)

_TMP = tempfile.TemporaryDirectory()
os.environ["TRANSLATOR_WEB_STATE_DIR"] = _TMP.name
os.environ["ANIME_SUBTITLE_LIBRARY_ROOT"] = os.path.join(_TMP.name, "lib")
os.environ["ANIME_LIBRARY_ROOTS"] = os.path.join(_TMP.name, "media")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import app as app_module  # noqa: E402


def _make_video(base: Path, rel: str) -> Path:
    video = base / rel
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_text("")
    return video


# ---------------------------------------------------------------------------
# Fix A: selection resolution
# ---------------------------------------------------------------------------


def test_selected_sources_accepts_full_library_relative_path(tmp_path, monkeypatch):
    """The UI sends ep.source (full library-relative path); it must resolve."""
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    expected = _make_video(tmp_path, "Paranoia Agent/Season 1/S01E01.mkv")
    folder = tmp_path / "Paranoia Agent" / "Season 1"
    result = app_module._selected_sources(folder, ["Paranoia Agent/Season 1/S01E01.mkv"])
    assert result == [expected.resolve()]


def test_selected_sources_accepts_folder_relative_path(tmp_path, monkeypatch):
    """Regression: the original folder-relative form keeps working."""
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    expected = _make_video(tmp_path, "Show/Season 1/S01E01.mkv")
    folder = tmp_path / "Show" / "Season 1"
    result = app_module._selected_sources(folder, ["S01E01.mkv"])
    assert result == [expected.resolve()]


def test_selected_sources_rejects_absolute_path_outside_library(tmp_path, monkeypatch):
    """Fail-closed: an existing video outside the library stays rejected."""
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    outside = tmp_path.parent / "outside-library.mkv"
    outside.write_text("")
    try:
        app_module._selected_sources(tmp_path / "Show", [str(outside)])
        raised = False
    except ValueError:
        raised = True
    finally:
        outside.unlink(missing_ok=True)
    assert raised


def test_selected_sources_rejects_traversal_via_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    _make_video(tmp_path.parent, "escape.mkv")
    try:
        app_module._selected_sources(tmp_path / "Show", ["../escape.mkv"])
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_selected_sources_rejects_non_video_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    _make_video(tmp_path, "Show/S01E01.txt")
    try:
        app_module._selected_sources(tmp_path / "Show", ["S01E01.txt"])
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_selected_sources_none_selects_all_not_translated(tmp_path, monkeypatch):
    """Regression: full-folder translation (values=None) is unchanged."""
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    rows = [
        {"source": "Show/S01E01.mkv", "status": "NOT_STARTED"},
        {"source": "Show/S01E02.mkv", "status": "ALREADY_TRANSLATED"},
    ]
    seen = {}
    def fake_records(folder, source_language=None):
        seen["folder"] = folder
        return rows
    monkeypatch.setattr(app_module, "_episode_records", fake_records)
    result = app_module._selected_sources(tmp_path / "Show", None)
    assert seen["folder"] == tmp_path / "Show"
    assert result == [(tmp_path / "Show/S01E01.mkv").resolve()]


# ---------------------------------------------------------------------------
# Fix B: per-language source status
# ---------------------------------------------------------------------------


def test_source_status_cache_key_includes_language(monkeypatch):
    """Different languages must not collide in the source-status cache."""
    calls = []

    def fake_resolve(lib, episode_id, record_id=None, materialize=False,
                     job_id=None, source_language=None):
        calls.append(source_language)
        return {
            "available": False,
            "status": "SOURCE_NOT_FOUND",
            "display": f"Fonte {source_language} não encontrada",
            "reason": "teste",
        }

    monkeypatch.setattr(app_module, "resolve_episode_source", fake_resolve)
    monkeypatch.setattr(app_module, "subtitle_library", MagicMock())
    app_module.state.setdefault("source_status", {}).clear()

    first = app_module._source_status_for_episode(85, source_language="francês")
    second = app_module._source_status_for_episode(85, source_language="espanhol")

    assert len(calls) == 2, "cache colidiu entre idiomas diferentes"
    assert "francês" in first["display"]
    assert "espanhol" in second["display"]

    cached = app_module._source_status_for_episode(85, source_language="francês")
    assert len(calls) == 2, "mesmo idioma deveria reusar o cache"


def test_episode_record_for_video_forwards_source_language(tmp_path, monkeypatch):
    """The selected language must reach the source-status computation."""
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    video = _make_video(tmp_path, "Show/S01E01.mkv")
    captured = {}

    def fake_status(episode_id, record_id=None, source_language=None,
                    materialize=False, job_id=None):
        captured["source_language"] = source_language
        return {"available": False, "status": "SOURCE_NOT_FOUND",
                "display": "d", "reason": "r"}

    monkeypatch.setattr(app_module, "_source_status_for_episode", fake_status)
    monkeypatch.setattr(app_module, "_library_episode_for_video", lambda v: None)
    monkeypatch.setattr(app_module, "_latest_job_for", lambda source: None)

    app_module._episode_record_for_video(video, source_language="francês")
    assert captured["source_language"] == "francês"

    app_module._episode_record_for_video(video)
    assert captured["source_language"] is None


def test_episode_records_forward_source_language(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BASE_LIBRARY", tmp_path)
    _make_video(tmp_path, "Show/S01E01.mkv")
    captured = {}

    def fake_status(episode_id, record_id=None, source_language=None,
                    materialize=False, job_id=None):
        captured["source_language"] = source_language
        return {"available": False, "status": "SOURCE_NOT_FOUND",
                "display": "d", "reason": "r"}

    monkeypatch.setattr(app_module, "_source_status_for_episode", fake_status)
    monkeypatch.setattr(app_module, "_library_episode_for_video", lambda v: None)
    monkeypatch.setattr(app_module, "_latest_job_for", lambda source: None)

    records = app_module._episode_records(tmp_path / "Show", source_language="espanhol")
    assert len(records) == 1
    assert captured["source_language"] == "espanhol"
