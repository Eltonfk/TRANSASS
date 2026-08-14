"""Small boundary hooks used by the existing job runner.

The hooks deliberately do not import or modify the V2.1.3 engine.  They are
no-ops for an unregistered/UNKNOWN series, so a mixed media root cannot be
ingested accidentally.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from anime_subtitle_library import AnimeSubtitleLibrary, ClassificationError, LibraryError
from pipeline_lineage import archive_v230_records


_LIBRARY: AnimeSubtitleLibrary | None = None


def _library() -> AnimeSubtitleLibrary:
    global _LIBRARY
    if _LIBRARY is None:
        state_dir = Path(os.environ.get("TRANSLATOR_WEB_STATE_DIR", "/app/state"))
        root = Path(os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", str(state_dir / "anime-subtitle-library")))
        roots_raw = os.environ.get("ANIME_LIBRARY_ROOTS", os.environ.get("TRANSLATOR_BASE_LIBRARY", "/shows"))
        roots = [Path(item.strip()) for item in roots_raw.split(os.pathsep) if item.strip()]
        _LIBRARY = AnimeSubtitleLibrary(root, media_roots=roots)
    return _LIBRARY


def reset_library_for_tests() -> None:
    global _LIBRARY
    _LIBRARY = None


def _episode_metadata(video: Path) -> tuple[str | None, str | None, str | None]:
    match = re.search(r"S(\d{1,3})E(\d{1,3})", video.stem, re.IGNORECASE)
    season = f"{int(match.group(1)):02d}" if match else None
    episode = f"{int(match.group(2)):02d}" if match else None
    return season, episode, video.stem


def _series_and_episode(video: Path, library: AnimeSubtitleLibrary) -> tuple[dict[str, Any], dict[str, Any]] | None:
    resolved = video.resolve()
    for root in library.media_roots:
        try:
            relative = resolved.relative_to(root)
            break
        except ValueError:
            continue
    else:
        return None
    if len(relative.parts) < 2:
        return None
    series_rel = relative.parts[0]
    series = next((item for item in library.list_series() if item.get("library_relative_path") == series_rel), None)
    if not series or series.get("classification") != "ANIME":
        return None
    season, episode, title = _episode_metadata(video)
    media_rel = relative.as_posix()
    episode_row = library.register_episode(
        int(series["id"]), season=season, episode=episode, episode_title=title,
        media_relative_path=media_rel, media_filename=video.name,
    )
    return series, episode_row


def archive_source(video: Path, source_path: Path, *, language: str = "eng", job_id: str | None = None) -> dict[str, Any] | None:
    """Archive an extracted source before the runner deletes its temporary file."""
    library = _library()
    association = _series_and_episode(video, library)
    if association is None:
        return None
    _series, episode = association
    try:
        result = library.ingest_file(
            source_path, episode_id=int(episode["id"]), language=language,
            source_language=language, source_kind="EXTRACTED",
            original_filename=source_path.name, job_id=job_id,
            validation_status="VALIDATED", review_status="VALIDATED",
            created_by="v2_1_3-source-hook",
        )
        result.update({"series_id": int(_series["id"]), "episode_id": int(episode["id"])})
        return result
    except ClassificationError:
        return None


def archive_external_source(video: Path, source_path: Path, *, language: str = "eng", job_id: str | None = None) -> dict[str, Any] | None:
    """Archive an external sidecar selected by a future/source adapter."""
    library = _library()
    association = _series_and_episode(video, library)
    if association is None:
        return None
    _series, episode = association
    return library.ingest_file(
        source_path, episode_id=int(episode["id"]), language=language,
        source_language=language, source_kind="EXTERNAL",
        original_filename=source_path.name, job_id=job_id,
        validation_status="VALIDATED", review_status="VALIDATED",
        created_by="external-source-hook",
    )


def archive_translation(
    video: Path,
    output_path: Path,
    *,
    source_record: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    job_id: str | None = None,
    pipeline_version: str = "v2_1_3",
    model: str = "qwen3.5:9b",
) -> dict[str, Any] | None:
    """Archive an eligible V2.1.3 output and record its lineage/publication."""
    library = _library()
    association = _series_and_episode(video, library)
    if association is None:
        return None
    _series, episode = association
    summary = summary or {}
    record = library.ingest_file(
        output_path, episode_id=int(episode["id"]), language="pt-BR",
        source_language="eng", source_kind="TRANSLATED",
        original_filename=output_path.name, job_id=job_id,
        pipeline_version=pipeline_version, model=model,
        validation_status="VALIDATED", review_status="VALIDATED",
        events_total=summary.get("events"), preferred=True,
        created_by=f"{pipeline_version}-translation-hook",
    )
    if source_record and source_record.get("id"):
        library.add_lineage(int(record["id"]), int(source_record["id"]), "TRANSLATED_FROM")
    library.publish(int(record["id"]), target_path=output_path)
    return record


def archive_v230_pipeline(
    video: Path,
    final_output: Path,
    *,
    source_record: dict[str, Any] | None,
    execution_result: dict[str, Any],
    job_id: str | None = None,
    model: str | None = None,
    publish: bool = True,
    retranslated_from: int | None = None,
) -> dict[str, Any] | None:
    """Persist the V2.2.6 durable checkpoint and V2.3.0 final atomically.

    The lineage primitive owns edge names and ordering; this hook only
    resolves the active Library and episode association.
    """
    library = _library()
    association = _series_and_episode(video, library)
    if association is None:
        return None
    internal = (execution_result or {}).get("_internal") or {}
    stage_path = internal.get("stage_artifact_path")
    if not stage_path:
        raise LibraryError("V2.3.0 result missing durable stage artifact")
    result = archive_v230_records(
        library,
        source_record=source_record,
        stage_artifact=stage_path,
        final_output=final_output,
        stage_summary=internal.get("stage_result"),
        final_summary=execution_result,
        expected_stage_sha256=internal.get("stage_sha256"),
        job_id=job_id,
        model=model,
        publish=publish,
        retranslated_from=retranslated_from,
    )
    return result
