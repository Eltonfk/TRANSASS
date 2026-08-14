"""Read/copy/catalog backfill for an explicitly authorised anime series.

It never moves, renames, deletes or overwrites media.  Run once with
``--scan-only`` before ``--ingest``; both JSON files are intended as audit
artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from anime_subtitle_library import AnimeSubtitleLibrary


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm"}
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt"}
EPISODE_RE = re.compile(r"S(?P<season>\d{1,3})E(?P<episode>\d{1,3})", re.IGNORECASE)


def _episode_number(path: Path) -> tuple[str | None, str | None]:
    match = EPISODE_RE.search(path.name)
    if not match:
        return None, None
    return f"{int(match.group('season')):02d}", f"{int(match.group('episode')):02d}"


def _language(path: Path) -> str:
    lowered = path.name.casefold()
    if ".pt-br." in lowered or ".pt_br." in lowered or ".ptbr." in lowered:
        return "pt-BR"
    if any(token in lowered for token in (".eng.", ".en.", ".english.")):
        return "eng"
    return "unknown"


def scan(series_root: Path, media_root: Path) -> dict:
    series_root = series_root.resolve()
    media_root = media_root.resolve()
    series_relative = series_root.relative_to(media_root).as_posix()
    videos = sorted(path for path in series_root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)
    video_by_episode = {}
    for video in videos:
        season, episode = _episode_number(video)
        if season and episode:
            video_by_episode[(season, episode)] = video
    files = []
    unique_hashes = set()
    hashes = {}
    ambiguous = []
    for path in sorted(path for path in series_root.rglob("*") if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS):
        season, episode = _episode_number(path)
        video = video_by_episode.get((season, episode))
        confidence = "HIGH" if video else "LOW"
        if video is None:
            ambiguous.append({"path": path.relative_to(media_root).as_posix(), "reason": "no matching episode video"})
        data = path.read_bytes()
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        unique_hashes.add(sha)
        hashes.setdefault(sha, []).append(path.relative_to(media_root).as_posix())
        files.append({
            "path": path.relative_to(media_root).as_posix(),
            "episode": f"S{season}E{episode}" if season and episode else None,
            "video": video.relative_to(media_root).as_posix() if video else None,
            "bytes": len(data), "sha256": sha, "language": _language(path),
            "format": path.suffix.lower().lstrip("."), "confidence": confidence,
        })
    duplicates = [{"sha256": sha, "paths": paths} for sha, paths in hashes.items() if len(paths) > 1]
    return {
        "scope": "ANIME", "series": {"title": series_root.name, "relative_path": series_relative},
        "episodes": [{"path": video.relative_to(media_root).as_posix(), "episode": "S%sE%s" % _episode_number(video)} for video in videos],
        "files": files, "bytes": sum(item["bytes"] for item in files),
        "languages": sorted({item["language"] for item in files}),
        "formats": sorted({item["format"] for item in files}),
        "unique_hashes": len(unique_hashes), "duplicates": duplicates,
        "associations_confident": sum(item["confidence"] == "HIGH" for item in files),
        "associations_ambiguous": len(ambiguous), "ambiguous": ambiguous,
        "skipped_unknown": [], "skipped_non_anime": [],
    }


def ingest(scan_data: dict, *, media_root: Path, library_root: Path, pipeline_version: str | None = None, model: str | None = None, provenance_report: Path | None = None) -> dict:
    media_root = media_root.resolve()
    library = AnimeSubtitleLibrary(library_root, media_roots=[media_root])
    series_data = scan_data["series"]
    series = library.register_series(series_data["title"], series_data["relative_path"], classification="ANIME", source="SYSTEM_MIGRATION")
    videos = {item["path"]: item for item in scan_data.get("episodes", [])}
    result = {"scanned": len(scan_data.get("files", [])), "ingested": 0, "unique_objects": 0, "duplicates": len(scan_data.get("duplicates", [])), "ambiguous": scan_data.get("associations_ambiguous", 0), "failed": [], "skipped_unknown": 0, "skipped_non_anime": 0, "bytes_stored": 0, "records": [], "publications": []}
    proven_outputs = set()
    if provenance_report and provenance_report.is_file():
        try:
            payload = json.loads(provenance_report.read_text(encoding="utf-8"))
            for episode in payload.get("episodes", []):
                output = episode.get("output")
                if output:
                    # Historical report paths are host paths (/Tank/data/Shows)
                    # while the container sees /shows.  Basename matching is
                    # safe here because the episode association is already
                    # established from the same SxxExx video.
                    proven_outputs.add(Path(str(output)).name)
        except (OSError, ValueError):
            pass
    episode_ids = {}
    for video_rel, item in videos.items():
        video_path = media_root / video_rel
        season, episode = _episode_number(video_path)
        episode_ids[(season, episode)] = library.register_episode_for_path(int(series["id"]), video_path, season=season, episode=episode, episode_title=video_path.stem)
    before_objects = library.counts()["objects"]
    for item in scan_data.get("files", []):
        if item["confidence"] != "HIGH":
            result["failed"].append({"path": item["path"], "error": "ambiguous episode association"}); continue
        path = media_root / item["path"]
        season, episode = _episode_number(path)
        ep = episode_ids.get((season, episode))
        if not ep:
            result["failed"].append({"path": item["path"], "error": "episode not found"}); continue
        is_translated = item["language"] == "pt-BR" and (path.name in proven_outputs or not proven_outputs)
        source_kind = "TRANSLATED" if is_translated and pipeline_version else "IMPORTED_EXISTING"
        try:
            record = library.ingest_file(path, episode_id=int(ep["id"]), language=item["language"], source_kind=source_kind, source_language="eng" if item["language"] != "pt-BR" else "eng", original_filename=path.name, pipeline_version=pipeline_version if source_kind == "TRANSLATED" else None, model=model if source_kind == "TRANSLATED" else None, validation_status="VALIDATED" if source_kind == "TRANSLATED" else "IMPORTED", review_status="VALIDATED" if source_kind == "TRANSLATED" else "GENERATED", created_by="anime-library-backfill")
            result["ingested"] += 1; result["bytes_stored"] += item["bytes"]; result["records"].append(record)
            if source_kind == "TRANSLATED":
                parents = [candidate for candidate in library.list_records(episode_id=int(ep["id"])) if candidate.get("id") != record.get("id") and candidate.get("language") not in {"pt-BR", "pt_BR", "ptbr"}]
                if parents:
                    library.add_lineage(int(record["id"]), int(parents[0]["id"]), "TRANSLATED_FROM")
                result["publications"].append(library.publish(int(record["id"]), target_path=path))
        except Exception as error:
            result["failed"].append({"path": item["path"], "error": str(error)})
    result["unique_objects"] = library.counts()["objects"] - before_objects
    result["library_counts"] = library.counts()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series-root", required=True)
    parser.add_argument("--media-root", required=True)
    parser.add_argument("--library-root", required=True)
    parser.add_argument("--scan-output", required=True)
    parser.add_argument("--result-output")
    parser.add_argument("--ingest", action="store_true")
    parser.add_argument("--pipeline-version")
    parser.add_argument("--model")
    parser.add_argument("--provenance-report")
    args = parser.parse_args()
    data = scan(Path(args.series_root), Path(args.media_root))
    Path(args.scan_output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.ingest:
        result = ingest(data, media_root=Path(args.media_root), library_root=Path(args.library_root), pipeline_version=args.pipeline_version, model=args.model, provenance_report=Path(args.provenance_report) if args.provenance_report else None)
        if not args.result_output: raise SystemExit("--result-output obrigatório com --ingest")
        Path(args.result_output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
