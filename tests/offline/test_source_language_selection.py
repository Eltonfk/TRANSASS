"""Offline tests for configurable source-language detection and resolution.

These tests never call ffprobe/ffmpeg; they exercise the pure selection logic
and the sidecar discovery path with temporary files.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

from web_audit_retranslation import (  # noqa: E402
    _language_matches,
    _select_track_for_language,
    _sidecar_candidates,
    detect_source_options,
)


def _track(lang, *, textual=True, bitmap=False, title="", default=False, forced=False, index=0):
    return {
        "index": index,
        "codec": "ass" if textual else "hdmv_pgs_subtitle",
        "language": lang,
        "title": title,
        "forced": forced,
        "default": default,
        "textual": textual,
        "bitmap": bitmap,
    }


def test_language_matches_variants():
    assert _language_matches("eng", "inglês")
    assert _language_matches("en", "inglês")
    assert _language_matches("es", "espanhol")
    assert _language_matches("spa", "espanhol")
    assert _language_matches("ja", "japonês")
    assert not _language_matches("und", "inglês")
    assert not _language_matches("eng", "espanhol")


def test_select_track_for_language_picks_matching_dialogue():
    tracks = [
        _track("eng", title="Dialogue", index=2),
        _track("spa", title="Dialogue", index=3),
    ]
    selected, reason, bitmaps = _select_track_for_language(tracks, "espanhol")
    assert selected is not None and selected["index"] == 3
    assert reason is None


def test_select_track_for_language_excludes_signs_songs_only():
    tracks = [
        _track("spa", title="Signs & Songs", index=3),
        _track("eng", title="Dialogue", index=2),
    ]
    # When the configured language has only a signs/songs track, it is not chosen.
    selected, reason, _ = _select_track_for_language(tracks, "espanhol")
    assert selected is None
    assert "Signs/Songs" in (reason or "")


def test_select_track_for_language_ambiguous_without_default():
    tracks = [
        _track("spa", title="Dialogue A", index=3, default=False),
        _track("spa", title="Dialogue B", index=4, default=False),
    ]
    selected, reason, _ = _select_track_for_language(tracks, "espanhol")
    assert selected is None
    assert "múltiplas" in (reason or "")


def test_sidecar_candidates_filters_by_language(tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_text("")
    (tmp_path / "ep01.eng.ass").write_text("")
    (tmp_path / "ep01.esp.ass").write_text("")
    (tmp_path / "ep01.jpn.ass").write_text("")
    eng = _sidecar_candidates(video, "inglês")
    esp = _sidecar_candidates(video, "espanhol")
    allc = _sidecar_candidates(video, None)
    assert [p.name for p in eng] == ["ep01.eng.ass"]
    assert [p.name for p in esp] == ["ep01.esp.ass"]
    assert len(allc) == 3


def test_detect_source_options_sidecars(tmp_path):
    video = tmp_path / "ep01.mkv"
    video.write_text("")
    (tmp_path / "ep01.eng.ass").write_text("")
    (tmp_path / "ep01.esp.ass").write_text("")
    options = detect_source_options(video)
    langs = sorted(o["language"] for o in options if o["kind"] == "sidecar")
    assert langs == ["espanhol", "inglês"]


def test_main_flow_selects_configured_language(monkeypatch):
    import anime_subtitle_translator as at

    monkeypatch.setenv("TRANSLATOR_SOURCE_LANGUAGE", "espanhol")
    monkeypatch.setattr(at, "SOURCE_LANGUAGE", "espanhol")
    payload = {
        "streams": [
            {"codec_name": "ass", "tags": {"language": "eng", "title": "Dialogue"}, "index": 2},
            {"codec_name": "ass", "tags": {"language": "spa", "title": "Dialogue"}, "index": 3},
            {"codec_name": "ass", "tags": {"language": "spa", "title": "Signs & Songs"}, "index": 4},
        ]
    }

    class _R:
        stdout = __import__("json").dumps(payload)

    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: _R())
    idx, lang, ext = at.find_subtitle_stream(__import__("pathlib").Path("/tmp/x.mkv"))
    assert idx == 3 and lang == "spa"
