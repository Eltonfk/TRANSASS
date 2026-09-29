"""Candidate-only source staging must not create or mutate Library records."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import web_audit_retranslation as audit  # noqa: E402


class ReadOnlyLibrary:
    def __init__(self, video: Path, library_staging: Path):
        self.video = video
        self.staging_root = library_staging
        self.ingest_calls = []

    def _episode_video(self, _episode_id: int) -> Path:
        return self.video

    def ingest_file(self, *args, **kwargs):
        self.ingest_calls.append((args, kwargs))
        raise AssertionError("candidate-only source staging must not ingest into Library")


def test_candidate_sidecar_source_is_copied_to_isolated_staging(tmp_path):
    video = tmp_path / "media" / "episode.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    sidecar = video.with_name("episode.fr.ass")
    payload = b"[Script Info]\nTitle: FR source\n"
    sidecar.write_bytes(payload)
    state_staging = tmp_path / "state" / "candidate-source-staging" / "job-1"
    library_staging = tmp_path / "library" / "staging"
    library = ReadOnlyLibrary(video, library_staging)

    result = audit.resolve_episode_source(
        library,
        297,
        materialize=True,
        source_language="francês",
        refresh_from_media=True,
        staging_root=state_staging,
    )

    staged = Path(result["path"])
    assert result["available"] is True
    assert result["record_id"] is None
    assert result["status"] == "SOURCE_AVAILABLE_SIDECAR"
    assert staged.read_bytes() == payload
    assert staged.parent == Path(result["staging_path"])
    assert staged.parent.is_relative_to(state_staging)
    assert not library_staging.exists()
    assert library.ingest_calls == []
    assert sidecar.read_bytes() == payload


def test_candidate_embedded_source_extracts_without_library_ingest(tmp_path, monkeypatch):
    video = tmp_path / "media" / "episode.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    state_staging = tmp_path / "state" / "candidate-source-staging" / "job-2"
    library_staging = tmp_path / "library" / "staging"
    library = ReadOnlyLibrary(video, library_staging)
    track = {
        "index": 2,
        "codec": "ass",
        "language": "fre",
        "title": "FR Full ASS",
        "textual": True,
        "bitmap": False,
    }
    monkeypatch.setattr(audit, "_probe_subtitle_tracks", lambda _path: [track])
    monkeypatch.setattr(
        audit,
        "_select_track_for_language",
        lambda *_args, **_kwargs: (track, None, []),
    )
    payload = b"[Script Info]\nTitle: extracted source\n"

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(audit.subprocess, "run", fake_ffmpeg)

    result = audit.resolve_episode_source(
        library,
        297,
        materialize=True,
        source_language="francês",
        refresh_from_media=True,
        staging_root=state_staging,
    )

    staged = Path(result["path"])
    assert result["available"] is True
    assert result["record_id"] is None
    assert result["track"]["index"] == 2
    assert staged.read_bytes() == payload
    assert staged.parent == Path(result["staging_path"])
    assert staged.parent.is_relative_to(state_staging)
    assert not library_staging.exists()
    assert library.ingest_calls == []


def test_candidate_source_staging_copy_hash_matches_source(tmp_path):
    source = tmp_path / "source.ass"
    source.write_bytes(b"subtitle source")
    staged_root = tmp_path / "state" / "candidate-source-staging"

    result = audit._stage_source_file(source, staged_root, episode_id=297)

    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    assert hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest() == expected
    assert result["record_id"] is None
