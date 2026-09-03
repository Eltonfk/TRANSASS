#!/usr/bin/env python3
"""Web control plane for the subtitle translator.

The V2.1.2 engine remains in ``anime_subtitle_translator.py`` and is invoked
unchanged.  This module owns only library discovery, an episode queue, state,
controls, and presentation.
"""

from __future__ import annotations

import html
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, stream_with_context

from anime_subtitle_library import (
    AnimeSubtitleLibrary,
    ClassificationError,
    LibraryError,
    PathSecurityError,
    PublicationConflict,
)
from human_feedback import HumanFeedbackService, ReviewError, SourceVersionMismatch
from translation_memory import TranslationMemory
from glossary import GlossaryStore
from failure_ledger import retain_staging
from pipeline_registry import UnsupportedPipelineError, get_pipeline_plan, pipeline_info
from pipeline_lineage import LineageContractError, archive_v230_records
from queue_helpers import build_job_batch
from web_audit_retranslation import (
    _public_source_status,
    archive_eligibility,
    audit_record,
    compare_records,
    record_audit_status,
    resolve_episode_source,
    resolve_source_record,
)


BASE_LIBRARY = Path(os.environ.get("TRANSLATOR_BASE_LIBRARY", "/shows")).resolve()
SCRIPT_PATH = Path(__file__).resolve().with_name("anime_subtitle_translator.py")
RUNNER_PATH = Path(__file__).resolve().with_name("web_retranslation_runner.py")
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm"}
TARGET_SUFFIX = "pt-BR"
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt"}
PIPELINE = os.environ.get("TRANSLATOR_PIPELINE", "legacy").strip().lower()
MODEL = os.environ.get("TRANSLATOR_OLLAMA_MODEL", "").strip()
STATE_DIR = Path(os.environ.get("TRANSLATOR_WEB_STATE_DIR", "/app/state"))
STATE_FILE = STATE_DIR / "jobs.json"
AUDIT_FILE = STATE_DIR / "audits.json"
TRANSPORT_CONFIG_PATH = STATE_DIR / "transport_config.json"
LIBRARY_ROOT = Path(os.environ.get("ANIME_SUBTITLE_LIBRARY_ROOT", str(STATE_DIR / "anime-subtitle-library")))
_library_roots = [Path(item.strip()) for item in os.environ.get("ANIME_LIBRARY_ROOTS", str(BASE_LIBRARY)).split(os.pathsep) if item.strip()]
subtitle_library = AnimeSubtitleLibrary(LIBRARY_ROOT, media_roots=_library_roots)
human_feedback = HumanFeedbackService(subtitle_library)
translation_memory = TranslationMemory(LIBRARY_ROOT)
GLOSSARY_PATH = Path(os.environ.get("TRANSLATOR_GLOSSARY_PATH", str(STATE_DIR / "glossary_v1.json")))
glossary_store = GlossaryStore(GLOSSARY_PATH)
MAX_LOGS = 3000
MAX_HISTORY = 100
EPISODE_PAGE_SIZE_DEFAULT = 40
EPISODE_PAGE_SIZE_MAX = 200
EPISODE_DISCOVERY_CACHE_TTL = 8.0


class StatePersistenceError(RuntimeError):
    """State could not be durably committed; callers must fail closed."""

_WEB_ASSET_ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(_WEB_ASSET_ROOT / "static"), static_url_path="/static")
state_lock = threading.RLock()
state_condition = threading.Condition(state_lock)
_episode_discovery_cache: dict[str, tuple[float, tuple[Path, ...]]] = {}

# Compact browser-sized adaptation of the official TransASS mark.  Keeping it
# inline avoids another runtime file while the full illustration remains the
# larger, local ``transass_logo.png`` used in the application header.
FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-labelledby="title"><title id="title">TransASS</title><defs><linearGradient id="edge" x1="8" y1="8" x2="56" y2="56"><stop stop-color="#ff8fcf"/><stop offset=".5" stop-color="#65dfff"/><stop offset="1" stop-color="#ff8fcf"/></linearGradient><linearGradient id="letter" x1="14" y1="20" x2="50" y2="48"><stop stop-color="#f7fbff"/><stop offset=".55" stop-color="#b8eaff"/><stop offset="1" stop-color="#f58fca"/></linearGradient></defs><rect x="2" y="2" width="60" height="60" rx="15" fill="#071426" stroke="url(#edge)" stroke-width="2"/><path d="M12 13h36a7 7 0 0 1 7 7v15a7 7 0 0 1-7 7H31l-8 8v-8h-5a7 7 0 0 1-7-7V20a7 7 0 0 1 7-7Z" fill="#111d3d" stroke="#5edbff" stroke-width="1.5"/><path d="M17 23h29M17 28h19" stroke="#f493cf" stroke-width="2.4" stroke-linecap="round"/><text x="32" y="48" text-anchor="middle" font-family="Arial,sans-serif" font-size="20" font-weight="900" letter-spacing="-2" fill="url(#letter)">T<tspan fill="#f58fca">A</tspan></text></svg>'''


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pipeline() -> str:
    return os.environ.get("TRANSLATOR_PIPELINE", "legacy").strip().lower()


def _effective_pipeline() -> str:
    """C4: pipeline efetivo — transport_config.json primeiro, env como fallback.

    Quando o arquivo de configuração não existe, o valor da variável de
    ambiente ``TRANSLATOR_PIPELINE`` é usado diretamente.  O DEFAULT_CONFIG
    do módulo ``transport_config_store`` define ``legacy`` como padrão e
    sobrescreveria a variável de ambiente se o arquivo não fosse verificado
    antes.
    """
    from transport_config_store import TransportConfigError, load_transport_config

    if TRANSPORT_CONFIG_PATH.is_file():
        try:
            config = load_transport_config(TRANSPORT_CONFIG_PATH)
            pipeline = str(config.get("pipeline") or "").strip().lower()
            if pipeline:
                return pipeline
        except TransportConfigError:
            pass
    return _pipeline()


def _model() -> str:
    return os.environ.get("TRANSLATOR_OLLAMA_MODEL", "").strip()


def _state_dir_ready() -> bool:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        return os.access(STATE_DIR, os.W_OK)
    except OSError:
        return False


def _append_log(line: str, *, level: str = "technical", job_id: str | None = None) -> None:
    state["log_sequence"] += 1
    state["log"].append({
        "id": state["log_sequence"],
        "line": line,
        "level": level,
        "job_id": job_id,
        "time": _now(),
    })


def _safe_relative(path: Path, base: Path | None = None) -> str:
    base = base or BASE_LIBRARY
    return path.resolve().relative_to(base.resolve()).as_posix()


def _resolve_relative(relative: str, base: Path | None = None) -> Path:
    base = base or BASE_LIBRARY
    candidate = (base / relative).resolve()
    candidate.relative_to(base.resolve())
    return candidate


def _output_for(video: Path) -> Path:
    return video.with_suffix(f".{TARGET_SUFFIX}.ass")


def _existing_output(video: Path) -> Path | None:
    """Bloqueia tradução apenas se já existe .pt-BR.ass (nosso formato).

    Sidecars .srt de ferramentas externas (Bazarr etc.) são ignorados —
    o Subtranslate produz .ass e pode coexistir com .srt legados.
    """
    for marker in ("pt-BR", "pt_br", "ptbr"):
        for extension in (".ass", ".ssa"):  # só formatos ASS/SSA
            candidate = video.with_suffix(f".{marker}{extension}")
            if candidate.exists():
                return candidate
    return None


def _library_episode_for_video(video: Path) -> dict | None:
    """Resolve a registered anime episode by internal relative media path."""
    try:
        relative = _safe_relative(video)
        with subtitle_library._db() as db:
            row = db.execute(
                "SELECT e.*,s.title AS series_title,s.classification FROM media_episode e "
                "JOIN media_series s ON s.id=e.series_id WHERE e.media_relative_path=? AND s.classification='ANIME'",
                (relative,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _episode_records_for_library(episode_id: int) -> list[dict]:
    try:
        return subtitle_library.list_records(episode_id=episode_id)
    except Exception:
        return []


def _preferred_library_record(episode_id: int | None) -> dict | None:
    if episode_id is None:
        return None
    records = _episode_records_for_library(int(episode_id))
    if not records:
        return None
    published = [item for item in records if any(p.get("status") == "PUBLISHED" for p in subtitle_library.publications(record_id=int(item["id"]))) ]
    return next((item for item in records if item.get("preferred")), None) or (published[0] if published else records[0])


def _global_source_language() -> str:
    """Read the configured source language from the transport config (global)."""
    try:
        from transport_config_store import public_transport_config

        return public_transport_config(TRANSPORT_CONFIG_PATH).get("source_language") or "inglês"
    except Exception:
        return "inglês"


def _source_status_for_episode(
    episode_id: int | None,
    record_id: int | None = None,
    *,
    materialize: bool = False,
    job_id: str | None = None,
    source_language: str | None = None,
) -> dict:
    """Resolve a trusted original without exposing filesystem paths.

    The browser only receives this sanitized status.  Discovery is cached for
    polling, while a materializing request always performs a fresh lookup and
    invalidates the cache after ingesting/extracting a source.
    """
    if source_language is None:
        source_language = _global_source_language()
    if episode_id is None:
        return {"available": False, "status": "SOURCE_NOT_FOUND", "display": "Fonte original não disponível", "reason": "episódio não catalogado"}
    key = f"{int(episode_id)}:{source_language}"
    if not materialize and key in state.get("source_status", {}):
        return dict(state["source_status"][key])
    try:
        result = resolve_episode_source(
            subtitle_library,
            int(episode_id),
            int(record_id) if record_id is not None else None,
            materialize=materialize,
            job_id=job_id,
            source_language=source_language,
        )
    except Exception as error:
        # A source/metadata problem belongs to the episode, never to service
        # health and never to the whole season response.
        result = {
            "available": False,
            "status": "SOURCE_STATUS_ERROR",
            "display": "Status da fonte indisponível",
            "reason": str(error)[:240],
        }
    public = _public_source_status(result)
    if not materialize:
        state.setdefault("source_status", {})[key] = public
    else:
        state.setdefault("source_status", {})[key] = public
    return public


def _audit_for_record(record_id: int | None) -> dict | None:
    if record_id is None:
        return None
    return state.get("audits", {}).get(str(int(record_id)))


def _public_audit(audit: dict | None) -> dict | None:
    """Keep episode polling compact; detailed events remain in audit APIs."""
    if not audit:
        return None
    return {
        key: audit.get(key)
        for key in (
            "record_id", "status", "flags", "blocking_flags", "review_flags",
            "eligible_for_archive", "checks", "reason", "audited_at",
            "source_available",
        )
        if key in audit
    }


def _decorate_library_record(record: dict | None) -> dict | None:
    """Add version-local audit/publication state for the web UI.

    Episode-level state is intentionally not copied onto a version.  A legacy
    record may have an audit problem while a newer validated record is clean.
    """
    if not record:
        return record
    result = dict(record)
    record_id = int(result["id"])
    audit = _public_audit(_audit_for_record(record_id))
    try:
        publications = subtitle_library.publications(record_id=record_id)
    except Exception:
        publications = []
    current = next((item for item in publications if item.get("status") == "PUBLISHED"), None)
    result["audit"] = audit
    result["audit_status"] = audit.get("status") if audit else "NÃO AUDITADA"
    result["published"] = bool(current)
    result["publication_status"] = current.get("status") if current else "NOT_PUBLISHED"
    result["publication"] = current or (publications[0] if publications else None)
    result["published_at"] = current.get("published_at") if current else None
    # A historical sidecar can predate the Library publication row.  Resolve
    # its hash read-only so the confirmation dialog still describes reality.
    try:
        target = subtitle_library._episode_video(int(result["episode_id"])).with_suffix(".pt-BR." + str(result.get("format") or "ass"))
        if target.is_file():
            target_sha = subtitle_library._hash_file(target)[0]
            result["target_present"] = True
            result["target_sha256"] = target_sha
            with subtitle_library._db() as db:
                target_row = db.execute(
                    "SELECT r.id,r.pipeline_version,r.source_kind FROM subtitle_record r JOIN subtitle_object o ON o.id=r.object_id WHERE r.episode_id=? AND o.sha256=? ORDER BY r.id DESC LIMIT 1",
                    (int(result["episode_id"]), target_sha),
                ).fetchone()
            result["target_record_id"] = int(target_row["id"]) if target_row else None
            result["target_record_pipeline"] = target_row["pipeline_version"] if target_row else None
            result["target_record_source_kind"] = target_row["source_kind"] if target_row else None
        else:
            result["target_present"] = False
            result["target_sha256"] = None
            result["target_record_id"] = None
    except Exception:
        result["target_present"] = False
        result["target_sha256"] = None
        result["target_record_id"] = None
    return result


def _version_summary(records: list[dict]) -> dict:
    """Summarize versions without conflating their individual audit states."""
    translated = [item for item in records if str(item.get("language") or "").casefold() not in {"eng", "en"}]
    validated = [item for item in translated if str(item.get("validation_status") or "").upper() in {"VALIDATED", "OK", "PUBLISHED"}]
    problems = [item for item in translated if (_public_audit(_audit_for_record(int(item["id"]))) or {}).get("status") == "PROBLEMAS DETECTADOS"]
    published = [item for item in translated if item.get("published")]
    return {
        "total_versions": len(records),
        "translated_versions": len(translated),
        "validated_versions": len(validated),
        "problem_versions": len(problems),
        "published_versions": len(published),
        "status": "VERSÕES SEPARADAS" if validated and problems else ("PROBLEMAS DETECTADOS" if problems else ("VALIDADA" if validated else "NÃO AUDITADA")),
    }


def _record_for_video(video: Path) -> dict | None:
    episode = _library_episode_for_video(video)
    if not episode:
        return None
    record = _preferred_library_record(int(episode["id"]))
    if record:
        record = _decorate_library_record(record)
        source = _source_status_for_episode(int(episode["id"]), int(record["id"]))
        record["source_status"] = source
        record["retranslation_available"] = bool(source.get("available"))
        record["retranslation_reason"] = source.get("reason")
        all_records = [_decorate_library_record(item) for item in _episode_records_for_library(int(episode["id"]))]
        record["version_summary"] = _version_summary(all_records)
        record["audit_status"] = record["version_summary"]["status"]
    return record


def _friendly_number(name: str) -> str | None:
    import re
    match = re.search(r"(?:S\d+)?E(\d+)", name, re.IGNORECASE)
    return f"E{int(match.group(1)):02d}" if match else None


def _latest_job_for(source: str) -> dict | None:
    candidates = [job for job in state["jobs"] if job.get("source") == source]
    return candidates[-1] if candidates else None


def _technical_history(item: dict) -> bool:
    """Technical smoke/fixture sessions remain stored but hidden by default."""
    raw = " ".join(str(item.get(key) or "") for key in ("folder", "name", "source", "reason"))
    lowered = raw.casefold()
    return any(token in lowered for token in (".subtranslate-web-smoke", "fixture", "health-test", "health_test", "smoke"))


def _episode_record_for_video(video: Path, source_language: str | None = None) -> dict:
    """Build one episode row; callers isolate failures to that episode."""
    source = _safe_relative(video)
    output = _existing_output(video)
    previous = _latest_job_for(source)
    episode_info = _library_episode_for_video(video)
    library_record = _preferred_library_record(int(episode_info["id"])) if episode_info else None
    audit = _public_audit(_audit_for_record(int(library_record["id"]))) if library_record else None
    source_resolution = _source_status_for_episode(
        int(episode_info["id"]) if episode_info else None,
        int(library_record["id"]) if library_record else None,
        source_language=source_language,
    )
    status = "ALREADY_TRANSLATED" if output else (previous.get("status") if previous else "NOT_STARTED")
    return {
        "id": source,
        "source": source,
        "name": video.name,
        "episode": _friendly_number(video.name),
        "ptbr": _safe_relative(output) if output else None,
        "source_available": True,
        "status": status,
        "last_job": _public_job(previous) if previous else None,
        "library_episode_id": episode_info.get("id") if episode_info else None,
        "library_record_id": library_record.get("id") if library_record else None,
        "library_source_kind": library_record.get("source_kind") if library_record else None,
        "library_pipeline": library_record.get("pipeline_version") if library_record else None,
        "audit": audit,
        "audit_status": _version_summary([_decorate_library_record(item) for item in _episode_records_for_library(int(episode_info["id"]))])["status"] if episode_info else record_audit_status(audit),
        "version_summary": _version_summary([_decorate_library_record(item) for item in _episode_records_for_library(int(episode_info["id"]))]) if episode_info else None,
        "source_status": source_resolution,
        "retranslation_available": bool(source_resolution.get("available")),
        "retranslation_reason": source_resolution.get("reason"),
    }


def _discover_episode_videos(folder: Path) -> list[Path]:
    """Return a short-lived snapshot of episode files.

    Discovery is deliberately cached only for a few seconds: the library is
    user-writable and workers may publish a new subtitle while the browser is
    polling.  The cache avoids repeating the expensive recursive walk during
    normal refreshes without turning filesystem state into an authority.
    """
    key = str(folder)
    now = time.monotonic()
    cached = _episode_discovery_cache.get(key)
    if cached and now - cached[0] < EPISODE_DISCOVERY_CACHE_TTL:
        return list(cached[1])
    videos = tuple(sorted(
        path for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ))
    _episode_discovery_cache[key] = (now, videos)
    return list(videos)


def _episode_records(folder: Path, source_language: str | None = None, videos: list[Path] | None = None) -> list[dict]:
    records = []
    for video in (videos if videos is not None else _discover_episode_videos(folder)):
        try:
            records.append(_episode_record_for_video(video, source_language=source_language))
        except Exception as error:
            # A malformed legacy row must remain an episode-local state, not
            # turn a healthy library into a service-wide 500/unavailable page.
            try:
                source = _safe_relative(video)
            except Exception:
                source = video.name
            records.append({
                "id": source,
                "source": source,
                "name": video.name,
                "episode": _friendly_number(video.name),
                "ptbr": None,
                "source_available": False,
                "status": "METADATA_ERROR",
                "last_job": None,
                "library_episode_id": None,
                "library_record_id": None,
                "library_source_kind": None,
                "library_pipeline": None,
                "audit": None,
                "audit_status": "NÃO AUDITADA",
                "source_status": {
                    "available": False,
                    "status": "METADATA_ERROR",
                    "display": "Metadados do episódio indisponíveis",
                    "reason": str(error)[:240],
                },
                "retranslation_available": False,
                "retranslation_reason": str(error)[:240],
            })
    return records


def _read_jsonl(path: Path) -> list[dict]:
    """Read a bounded, best-effort ledger stream for status presentation.

    The web process must never fail a status request because a writer is in
    the middle of appending a line.  Invalid/truncated lines are skipped and
    the next poll will see the complete record.
    """
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


def _job_ledger_dir(job: dict) -> Path | None:
    explicit = job.get("failure_ledger_dir")
    if explicit:
        candidate = Path(str(explicit))
        if candidate.is_dir():
            return candidate
    root = Path(os.environ.get("TRANSLATOR_FAILURE_LEDGER_ROOT", str(STATE_DIR / "failure-ledger")))
    candidate = root / "jobs" / str(job.get("id", ""))
    return candidate if candidate.is_dir() else None


def _semantic_capture_telemetry(job: dict) -> dict[str, Any]:
    """Summarize V238 semantic captures without exposing request contents."""
    job_id = str(job.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id):
        return {"total": 0, "completed": 0, "in_progress": 0, "incomplete": 0,
                "current_event_id": None, "last_activity_at": None}
    capture_root = STATE_DIR / "v238-runs" / job_id / "captures"
    if not capture_root.is_dir():
        return {"total": 0, "completed": 0, "in_progress": 0, "incomplete": 0,
                "current_event_id": None, "last_activity_at": None}
    states: dict[str, int] = {}
    latest_mtime = 0.0
    latest_call_id = None
    active_mtime = 0.0
    active_call_id = None
    total = 0
    try:
        for call_dir in capture_root.iterdir():
            if not call_dir.is_dir():
                continue
            state_path = call_dir / "capture_state.json"
            if not state_path.is_file():
                continue
            try:
                value = json.loads(state_path.read_text(encoding="utf-8"))
                state_name = str(value.get("state") or "UNKNOWN")
                modified = state_path.stat().st_mtime
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            total += 1
            states[state_name] = states.get(state_name, 0) + 1
            if modified >= latest_mtime:
                latest_mtime = modified
                latest_call_id = str(value.get("call_id") or call_dir.name)
            if state_name == "TRANSPORT_IN_PROGRESS" and modified >= active_mtime:
                active_mtime = modified
                active_call_id = str(value.get("call_id") or call_dir.name)
    except OSError:
        pass
    event_match = re.search(r"event-(\d+)$", active_call_id or latest_call_id or "")
    completed = sum(states.get(name, 0) for name in ("RESPONSE_DURABLE", "VALIDATED_PASS"))
    in_progress = states.get("TRANSPORT_IN_PROGRESS", 0)
    incomplete = sum(states.get(name, 0) for name in (
        "CAPTURE_INCOMPLETE", "TRANSPORT_FAILED", "VALIDATED_FAIL",
    ))
    return {
        "total": total,
        "completed": completed,
        "in_progress": in_progress,
        "incomplete": incomplete,
        "current_event_id": int(event_match.group(1)) if event_match else None,
        "last_activity_at": (
            datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
            if latest_mtime else None
        ),
    }


def _job_telemetry(job: dict) -> dict:
    """Derive real progress from the job state and persistent unit ledger.

    This is intentionally read-only.  The frontend receives the canonical
    unit count and stage, while the ledger remains the writer-owned source of
    attempt/current-event/last-activity data.
    """
    summary = job.get("summary") if isinstance(job.get("summary"), dict) else {}
    ledger_dir = _job_ledger_dir(job)
    units: dict[str, dict] = {}
    attempts: list[dict] = []
    ledger_files: list[Path] = []
    if ledger_dir:
        units_json = ledger_dir / "units.json"
        if units_json.is_file():
            try:
                payload = json.loads(units_json.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict) and item.get("event_id") is not None:
                            units[str(item["event_id"])] = item
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            ledger_files.append(units_json)
        for path in (ledger_dir / "units.jsonl", ledger_dir / "unit-updates.jsonl"):
            for item in _read_jsonl(path):
                if item.get("event_id") is not None:
                    units[str(item["event_id"])] = item
            if path.is_file():
                ledger_files.append(path)
        attempts_path = ledger_dir / "attempts.jsonl"
        attempts = _read_jsonl(attempts_path)
        if attempts_path.is_file():
            ledger_files.append(attempts_path)
        for name in ("manifest.json", "snapshot.json"):
            path = ledger_dir / name
            if path.is_file():
                ledger_files.append(path)

    semantic = _semantic_capture_telemetry(job)
    status = str(job.get("status") or "QUEUED").upper()
    stage = str(job.get("stage") or "").upper() or {
        "WAITING": "QUEUED", "STARTING": "STARTING", "TRANSLATING": "TRANSLATING",
        "VALIDATING": "VALIDATING", "PUBLISHING": "ARCHIVING", "COMPLETED": "COMPLETED",
        "FAILED": "FAILED", "CANCELLED": "STOPPED", "SKIPPED": "SKIPPED",
        "SKIPPED_CURRENT_VALIDATED": "SKIPPED_CURRENT_VALIDATED",
        "NOT_STARTED_AFTER_FAILURE": "NOT_STARTED_AFTER_FAILURE",
    }.get(status, status)

    total = summary.get("events", summary.get("total_units"))
    resolved = summary.get("resolved", summary.get("resolved_units"))
    failed = summary.get("unresolved", summary.get("failed", summary.get("failed_units")))
    if total is None and units:
        total = len(units)
    if resolved is None and units:
        resolved = sum(1 for item in units.values() if str(item.get("status", "")).lower() == "resolved")
    if failed is None and units:
        failed = sum(1 for item in units.values() if str(item.get("status", "")).lower() in {"failed", "unresolved"})
    total = int(total) if isinstance(total, (int, float)) else None
    resolved = int(resolved) if isinstance(resolved, (int, float)) else None
    failed = int(failed) if isinstance(failed, (int, float)) else 0

    if (status == "TRANSLATING" and semantic["total"]
            and total is not None and resolved == total):
        stage = "SEMANTIC_RECONSTRUCTION"

    calls = summary.get("total_ollama_calls", summary.get("qwen_calls"))
    if calls is None and isinstance(summary.get("calls"), list):
        calls = len(summary["calls"])
    elif calls is None:
        calls = summary.get("calls")
    retries = summary.get("actual_retry_ollama_calls", summary.get("retry_calls", summary.get("retries")))
    if calls is None and attempts:
        calls = len(attempts)
    if retries is None and attempts:
        retries = sum(1 for item in attempts if str(item.get("phase", "initial")).lower() != "initial")
    calls = int(calls) if isinstance(calls, (int, float)) else 0
    retries = int(retries) if isinstance(retries, (int, float)) else 0

    current_event_id = None
    if attempts:
        ids = attempts[-1].get("event_ids") or attempts[-1].get("ledger_event_ids") or []
        if isinstance(ids, list) and ids:
            current_event_id = ids[-1]
        elif isinstance(ids, (int, str)):
            current_event_id = ids
    if semantic["current_event_id"] is not None:
        current_event_id = semantic["current_event_id"]
    budget = summary.get("retry_budget") if isinstance(summary.get("retry_budget"), dict) else None
    if budget is None and attempts:
        budget = attempts[-1].get("retry_budget_after")
    budget = budget or {}
    budget_used = budget.get("consumed", budget.get("used"))
    budget_total = budget.get("configured", budget.get("total"))
    budget_used = int(budget_used) if isinstance(budget_used, (int, float)) else None
    budget_total = int(budget_total) if isinstance(budget_total, (int, float)) else None

    started = job.get("started_at") or job.get("created_at")
    finished = job.get("finished_at")
    elapsed = None
    try:
        start_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00")) if started else None
        end_dt = datetime.fromisoformat(str(finished or _now()).replace("Z", "+00:00")) if start_dt else None
        if start_dt and end_dt:
            elapsed = max(0.0, (end_dt - start_dt).total_seconds())
    except (TypeError, ValueError):
        elapsed = None
    if ledger_files:
        try:
            latest = max(path.stat().st_mtime for path in ledger_files if path.exists())
            last_activity = datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()
        except (OSError, ValueError):
            last_activity = finished or started
    else:
        last_activity = finished or started
    semantic_activity = semantic.get("last_activity_at")
    if semantic_activity and (not last_activity or semantic_activity > last_activity):
        last_activity = semantic_activity
    v238_metrics = summary.get("v238_metrics") if isinstance(summary.get("v238_metrics"), dict) else None
    return {
        "stage": stage,
        "total_units": total,
        "resolved_units": resolved,
        "failed_units": failed,
        "current_event_id": current_event_id,
        "calls": calls,
        "retries": retries,
        "retry_budget_used": budget_used,
        "retry_budget_total": budget_total,
        "elapsed_seconds": elapsed,
        "last_activity_at": last_activity,
        "ledger_available": bool(ledger_dir),
        "semantic_calls": semantic["total"],
        "semantic_completed": semantic["completed"],
        "semantic_in_progress": semantic["in_progress"],
        "semantic_incomplete": semantic["incomplete"],
        "v238_metrics": v238_metrics,
    }


def _public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    telemetry = _job_telemetry(job)
    public = {
        key: job.get(key)
        for key in (
            "id", "source", "name", "episode", "folder", "status", "created_at",
            "started_at", "finished_at", "error", "reason", "progress",
            "attempt", "retry_count", "dry_run", "critical_flags", "flags",
            "operation", "source_record_id", "old_record_id", "bulk_fail_fast",
            "not_started_reason", "new_record_id", "audit", "diagnostic",
            "candidate_output_name", "candidate_output_sha256", "candidate_download_url",
        )
        if key in job
    }
    # A completed V2.3.8 summary can contain the full per-event ledgers and
    # reach tens of megabytes.  The UI needs counters, never forensic payloads;
    # those remain intact in the persisted state and durability directories.
    summary = job.get("summary")
    if isinstance(summary, dict):
        compact_summary = {
            key: summary[key]
            for key in (
                "pipeline", "pipeline_id", "status", "events", "total_units",
                "resolved", "resolved_units", "unresolved", "failed",
                "failed_units", "total_ollama_calls", "qwen_calls",
                "actual_retry_ollama_calls", "retry_calls", "retries",
            )
            if key in summary and isinstance(summary[key], (str, int, float, bool, type(None)))
        }
        if compact_summary:
            public["summary"] = compact_summary
    public.update(telemetry)
    # Keep the legacy progress shape for clients that already consume it, but
    # make its unit count real whenever the ledger has a known total.
    if telemetry["total_units"] is not None:
        public["progress"] = {
            "scope": "units", "current": telemetry["resolved_units"] or 0,
            "total": telemetry["total_units"], "label": job.get("name", ""),
        }
    return public


def _preserve_retranslation_diagnostic(
    job: dict,
    source: Path,
    output: Path,
    audit: dict,
    summary: dict | None,
) -> dict:
    """Persist a failed candidate outside the usable subtitle catalogue.

    The diagnostic copy is immutable evidence only: it creates no subtitle
    record, lineage or publication and therefore cannot become preferred or
    consumable by Jellyfin.
    """
    diagnostic_id = f"retranslation-{job['id']}"
    root = LIBRARY_ROOT / "diagnostics" / diagnostic_id
    root.mkdir(parents=True, exist_ok=True)
    candidate_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    candidate_tmp = root / "candidate.ass.tmp"
    candidate = root / "candidate.ass"
    with output.open("rb") as source_file, candidate_tmp.open("wb") as target_file:
        shutil.copyfileobj(source_file, target_file, length=1024 * 1024)
        target_file.flush()
        os.fsync(target_file.fileno())
    os.replace(candidate_tmp, candidate)
    payload = {
        "diagnostic_only": True,
        "job_id": job["id"],
        "episode_id": job.get("episode_id"),
        "source_record_id": job.get("source_record_id"),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "candidate_sha256": candidate_hash,
        "pipeline": _pipeline(),
        "model": _model(),
        "captured_at": _now(),
        "audit": audit,
        "summary": summary,
    }
    metadata_tmp = root / "diagnostic.json.tmp"
    metadata = root / "diagnostic.json"
    with metadata_tmp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(metadata_tmp, metadata)
    return {
        "diagnostic_only": True,
        "diagnostic_id": diagnostic_id,
        "candidate_sha256": candidate_hash,
        "audit_status": audit.get("status"),
        "blocking_flags": audit.get("blocking_flags", []),
        "review_flags": audit.get("review_flags", []),
    }


def _persist_locked() -> None:
    if not _state_dir_ready():
        raise StatePersistenceError("diretório de estado não está disponível para escrita")
    payload = {
        "version": 1,
        "updated_at": _now(),
        "jobs": state["jobs"][-500:],
        "history": state["history"][-MAX_HISTORY:],
        "folder": state.get("folder"),
        "queue_paused": state.get("queue_paused", False),
        "audits": state.get("audits", {}),
    }
    temporary: Path | None = None
    try:
        fd, raw = tempfile.mkstemp(prefix=".jobs-", suffix=".json", dir=str(STATE_DIR))
        temporary = Path(raw)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            # Compact serialization: jobs.json can reach hundreds of MB with
            # indent=2 (roughly 2x the compact size) and is rewritten on every
            # worker output line.  Compact output halves the file and the
            # in-memory dump peak without losing any field.
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STATE_FILE)
        directory_fd = os.open(str(STATE_DIR), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        # Wake SSE clients only after the durable state replacement completed.
        try:
            state_condition.notify_all()
        except RuntimeError:
            # A legacy caller may persist outside the lock; durability still
            # succeeds and the next heartbeat will refresh SSE clients.
            pass
    except OSError as exc:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except Exception:
            pass
        raise StatePersistenceError(f"falha ao persistir estado: {exc}") from exc


def _load_state() -> dict:
    try:
        payload = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"jobs": [], "history": [], "audits": {}}
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    history = payload.get("history", []) if isinstance(payload, dict) else []
    if not isinstance(jobs, list):
        jobs = []
    if not isinstance(history, list):
        history = []
    recovery_needed = False
    for job in jobs:
        if job.get("status") in {"STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING", "PAUSING"}:
            recovery_needed = True
            job["status"] = "FAILED"
            job["stage"] = "FAILED"
            job["error"] = "serviço reiniciado durante o job; nenhuma retomada automática"
            job["reason"] = "service_restarted"
            job["finished_at"] = _now()
    audits = payload.get("audits", {}) if isinstance(payload, dict) else {}
    if not isinstance(audits, dict):
        audits = {}
    return {
        "jobs": jobs[-500:], "history": history[-MAX_HISTORY:], "audits": audits,
        "recovery_needed": recovery_needed,
    }


loaded = _load_state()
state = {
    "running": False,
    "paused": False,
    "pause_requested": False,
    "stop_requested": False,
    "stopped_by_user": False,
    "folder": None,
    "jobs": loaded["jobs"],
    "history": loaded["history"],
    "audits": loaded.get("audits", {}),
    "log": deque(maxlen=MAX_LOGS),
    "log_sequence": 0,
    "process": None,
    "worker": None,
    "session_id": None,
    "finished_ok": None,
    "bulk_stop_reason": None,
    "bulk_failed_job_id": None,
    "queue_paused": False,
    "current_job_id": None,
    # Read-only source discovery is cached per process so status polling does
    # not repeatedly invoke ffprobe.  Materialization invalidates the entry.
    "source_status": {},
}

if loaded.get("recovery_needed"):
    with state_lock:
        _persist_locked()


def _send_process_group_signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        proc.send_signal(sig)


def _queue_counts() -> dict:
    jobs = [job for job in state["jobs"] if job.get("session_id") == state.get("session_id")]
    counts = {key: 0 for key in ("WAITING", "STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING", "COMPLETED", "FAILED", "PAUSED", "CANCELLED", "SKIPPED", "SKIPPED_CURRENT_VALIDATED", "ALREADY_TRANSLATED", "NOT_STARTED", "NOT_STARTED_AFTER_FAILURE")}
    for job in jobs:
        status = job.get("status", "NOT_STARTED")
        counts[status] = counts.get(status, 0) + 1
    return {
        "total": len(jobs),
        "waiting": counts["WAITING"],
        "running": sum(counts[key] for key in ("STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING")),
        "completed": counts["COMPLETED"],
        "failed": counts["FAILED"],
        "cancelled": counts["CANCELLED"],
        "skipped": counts["SKIPPED"] + counts["SKIPPED_CURRENT_VALIDATED"] + counts["ALREADY_TRANSLATED"],
        "not_started_after_failure": counts["NOT_STARTED_AFTER_FAILURE"],
        "states": counts,
    }


def _set_job(job: dict, **values) -> None:
    job.update(values)
    _persist_locked()


def _summary_level(line: str) -> str:
    lowered = line.lower()
    if "erro" in lowered or "falh" in lowered or "exception" in lowered:
        return "error"
    if "ok ->" in lowered or "conclu" in lowered or "resumo" in lowered:
        return "summary"
    return "technical"


def _apply_canonical_pipeline_summary(job: dict, summary: dict) -> None:
    """Apply the single normal-path summary contract to a job."""
    # Worker summaries may contain the complete per-event ledger under a
    # stage's ``base_materializer`` result.  That ledger is already durable
    # in the failure-ledger/checkpoint files; retaining it in jobs.json makes
    # every status response unnecessarily huge.  Keep the canonical counters
    # and forensic scalar fields, but drop only the duplicated ledger.
    summary = _compact_summary_for_state(summary)
    job["summary"] = summary
    job["flags"] = summary.get("flags", {})
    job["critical_flags"] = summary.get("critical_flags", [])
    job["progress"] = {
        "scope": "events",
        "current": summary.get("resolved", 0),
        "total": summary.get("events", 0),
        "label": job.get("name", ""),
    }
    stages = summary.get("stages")
    last_stage = stages[-1].get("id") if isinstance(stages, list) and stages and isinstance(stages[-1], dict) else None
    job["status"] = summary.get("status") or "VALIDATING"
    job["stage"] = summary.get("stage") or last_stage or "VALIDATING"


def _is_transport_error(exc: Exception) -> bool:
    """L1: detecta erros de transporte (rede/HTTP) que disparam fallback.

    Erros de validação/schema (BaseTranslationMaterializerError,
    LlamaPolicyError, RuntimeError de schema) NÃO são de transporte.
    """
    import requests

    if isinstance(exc, requests.exceptions.RequestException):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    message = str(exc).lower()
    return any(token in message for token in (
        "connectionerror", "timeout", "max retries", "connection refused",
        "name or service not known", "temporarily unavailable",
    ))


def _new_operation_id(job: dict) -> str:
    """M9: uuid.uuid4().hex por tentativa no fallback (R7)."""
    import uuid

    return uuid.uuid4().hex


def _v238_metrics_from_result(result: dict) -> dict:
    """C7: métricas v238 a partir do resultado do orchestrator."""
    budget = result.get("operation_budget") if isinstance(result.get("operation_budget"), dict) else {}
    qwen_max = int(budget.get("qwen_physical_maximum") or 131)
    qwen_reserved = int(budget.get("qwen_reserved") or 0)
    # V2.3.8 keeps the V2.2.6 measurements under ``base_materializer_metrics``
    # and aggregates stage totals under ``aggregated_metrics``.  Older code
    # looked only at the top-level ``calls`` field, which is absent for the
    # canonical live adapter and made a successful real translation report
    # zero model/network calls.
    provider_metrics = result.get("provider_metrics") if isinstance(result.get("provider_metrics"), dict) else result.get("metrics")
    provider_metrics = provider_metrics if isinstance(provider_metrics, dict) else {}
    base_metrics = result.get("base_materializer_metrics") if isinstance(result.get("base_materializer_metrics"), dict) else {}
    aggregated = result.get("aggregated_metrics") if isinstance(result.get("aggregated_metrics"), dict) else {}
    calls_value = result.get("calls")
    if not isinstance(calls_value, int) or calls_value <= 0:
        calls_value = aggregated.get("model_calls_total")
    if not isinstance(calls_value, int) or calls_value <= 0:
        calls_value = base_metrics.get("primary_requests", base_metrics.get("calls"))
    calls = int(calls_value or 0)
    physical = provider_metrics.get("physical_client_calls")
    if physical is None or int(physical or 0) <= 0:
        physical = aggregated.get("v226_physical_attempts", base_metrics.get("physical_attempts", calls))
    generation = provider_metrics.get("model_generation_calls")
    if generation is None or int(generation or 0) <= 0:
        generation = aggregated.get("v226_model_generation_attempts", base_metrics.get("model_generation_attempts", calls))
    requests = provider_metrics.get("provider_requests")
    if requests is None or int(requests or 0) <= 0:
        requests = physical
    return {
        "calls": calls,
        "physical_client_calls": int(physical or 0),
        "model_generation_calls": int(generation or 0),
        "provider_requests": int(requests or 0),
        "prompt_tokens": None,
        "completion_tokens": None,
        "elapsed_seconds": result.get("pipeline_wall_seconds"),
        "budget_used": int(budget.get("total_reserved", 0) or 0),
        "budget_remaining": max(0, qwen_max - qwen_reserved),
        "provider_mode": "LIVE_CAPTURED",
        "fallback_used": False,
    }


def _compact_v238_stages(stages: list) -> list:
    """Project stage results to counters, dropping the per-event ledger.

    The full primary_ledger is durable on disk (failure-ledger jobs/<id>/
    units.json and the materializer checkpoint primary-ledger.json).  Keeping
    it inside job["summary"] bloats jobs.json to tens of MB per completed job
    and keeps that whole structure resident in the web process.
    """
    compact = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        result = stage.get("result")
        if isinstance(result, dict):
            result = _compact_summary_value(result)
        compact.append({"id": stage.get("id"), "result": result})
    return compact


def _compact_summary_value(value):
    """Copy summary data while omitting duplicated per-event ledgers.

    This deliberately removes only fields named ``primary_ledger``.  The
    authoritative ledger remains on disk, while failure metadata and all
    other summary fields stay available to the UI and audit paths.
    """
    if isinstance(value, dict):
        return {
            key: _compact_summary_value(item)
            for key, item in value.items()
            if key != "primary_ledger"
        }
    if isinstance(value, list):
        return [_compact_summary_value(item) for item in value]
    return value


def _compact_summary_for_state(summary: dict) -> dict:
    if not isinstance(summary, dict):
        return summary
    return _compact_summary_value(summary)


def _summary_log_line(marker: str, summary: dict) -> str:
    """Return a bounded log projection for a structured worker summary."""
    safe = {
        key: summary.get(key)
        for key in ("status", "stage", "events", "resolved", "unresolved", "calls", "retry_calls")
        if key in summary
    }
    return f"{marker} {json.dumps(safe, ensure_ascii=False, separators=(',', ':'))}"


def _project_v238_summary(result: dict) -> dict:
    """M8: projeta o resultado do orchestrator v2_3_8 para o formato
    exigido por _apply_canonical_pipeline_summary (R6).

    O resultado v2_3_8 NÃO tem status/stage/resolved/events/flags no topo
    (pipeline_orchestrator.py:170-194); sem esta projeção o job ficaria preso
    em VALIDATING com progresso 0/0.
    """
    stages = result.get("stages") if isinstance(result.get("stages"), list) else []
    karaoke = result.get("karaoke") if isinstance(result.get("karaoke"), dict) else {}
    primary_ledger = result.get("primary_ledger") if isinstance(result.get("primary_ledger"), list) else []
    failures = karaoke.get("failures") if isinstance(karaoke.get("failures"), list) else []
    structural = karaoke.get("structural_failures") if isinstance(karaoke.get("structural_failures"), list) else []
    unresolved = [row for row in primary_ledger
                  if isinstance(row, dict) and str(row.get("status", "")).upper() in {"BLOCKED", "SUSPECT"}]
    events = len(primary_ledger) if primary_ledger else int(karaoke.get("song_units") or 0)
    resolved = max(0, events - len(unresolved)) if events else 0
    ok = not failures and not structural and not unresolved
    status = "COMPLETED" if ok else "FAILED"
    last_stage = stages[-1].get("id") if stages and isinstance(stages[-1], dict) else "FULL_TRANSLATION_V238"
    flags: dict = {}
    critical_flags: list[str] = []
    if unresolved:
        flags["v238_unresolved_units"] = len(unresolved)
        critical_flags.append("v238_unresolved_units")
    aggregated_metrics = result.get("aggregated_metrics") if isinstance(result.get("aggregated_metrics"), dict) else {}
    calls = result.get("calls")
    if not isinstance(calls, int) or calls <= 0:
        calls = result.get("model_calls")
    if not isinstance(calls, int) or calls <= 0:
        calls = aggregated_metrics.get("model_calls_total", 0)
    retry_calls = result.get("retry_calls")
    if not isinstance(retry_calls, int) or retry_calls < 0:
        retry_calls = aggregated_metrics.get("v226_retries", 0)
    return {
        "status": status,
        "stage": last_stage,
        "events": events,
        "resolved": resolved,
        "unresolved": len(unresolved),
        "flags": flags,
        "critical_flags": critical_flags,
        "stages": _compact_v238_stages(stages),
        "v238_metrics": _v238_metrics_from_result(result),
        "calls": int(calls or 0),
        "retry_calls": int(retry_calls or 0),
    }


def _project_primary_ledger_to_units(primary_ledger: list) -> list:
    """M6: projeta o primary_ledger v2_3_8 para o formato units.json."""
    units = []
    for row in primary_ledger:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "")).lower()
        units.append({
            "event_id": row.get("event_id"),
            "canonical_unit_id": row.get("canonical_unit_id"),
            "status": "resolved" if status == "resolved"
            else ("failed" if status in {"blocked", "suspect"} else status),
            "reason_code": row.get("objective_reason_code") or row.get("reason_code"),
            "flags": row.get("flags") or [],
            "failure_reason": row.get("failure_reason"),
            "primary_model_tag": row.get("primary_model_tag"),
            "primary_attempts": row.get("primary_attempts"),
        })
    return units


def _apply_gemini_profile(transport_cfg: dict) -> None:
    """Aplica otimizações do Gemini profile quando provider=gemini.

    Ajusta automaticamente:
    - BATCH_SIZE: mais unidades por chamada (menos chamadas totais)
    - retry_budget: menos retries (economiza quota)
    - delay entre chamadas: respeita 15 RPM do free tier
    - model: usa gemini-1.5-flash (mais barato e rápido)
    """
    import anime_subtitle_translator as translator
    primary = transport_cfg.get("primary") or {}
    provider = str(primary.get("provider", "")).lower()
    profile = transport_cfg.get("gemini_profile") or {}

    if provider != "gemini" or not profile.get("enabled", True):
        return

    # Aplica BATCH_SIZE otimizado
    new_batch = max(1, int(profile.get("batch_size", 16)))
    translator.BATCH_SIZE = new_batch

    # Aplica model se configurado (permite override via profile)
    gemini_model = profile.get("model", "").strip()
    if gemini_model:
        primary["model"] = gemini_model

    # Valida API key para gemini — sem key falha com 403/404
    keys = transport_cfg.get("keys") or {}
    if not keys.get("gemini"):
        _append_log("AVISO: Gemini selecionado mas sem API key (keys.gemini vazio) — fallback para ollama", level="warning")
        fallback = transport_cfg.get("fallback") or {"provider": "ollama", "model": "qwen3.5:9b"}
        if fallback and fallback.get("provider"):
            transport_cfg["primary"] = dict(fallback)
            _append_log(f"Fallback ativo: {fallback.get('provider')}/{fallback.get('model')}", level="info")
            return  # não aplica profile gemini
    # Garante budget mínimo para temporadas (perfis antigos tinham 8)
    retry_budget = int(profile.get("retry_budget", 32))
    if retry_budget < 16:
        retry_budget = 32
        profile["retry_budget"] = 32
    # Log das otimizações aplicadas
    delay = float(profile.get("delay_between_calls", 0.5))
    _append_log(
        f"Gemini Profile ativo: batch={new_batch}, retry_budget={retry_budget}, "
        f"delay={delay}s, model={gemini_model or primary.get('model', 'default')}",
        level="info",
    )


def _run_episode_v238(job: dict) -> None:
    """C5: caminho in-process V2.3.8 para _run_episode.

    Preserva staging de temporada (glossário por série, app.py:865-873),
    output_exists_race (app.py:933-936) e cancelamento cooperativo (M4).
    """
    from pipeline_orchestrator import execute_pipeline_plan
    from web_execution_context import build_v238_execution_context
    from web_durable_provider import WebDurableResponseProvider
    from transport_config_store import load_transport_config

    source = Path(job["source_abs"])
    destination = source.with_suffix(f".{TARGET_SUFFIX}.ass")
    temporary_root = Path(tempfile.mkdtemp(prefix=".subtranslate-v238-", dir=str(source.parent)))
    temporary_dir = temporary_root / source.parent.name
    temporary_dir.mkdir()
    linked_video = temporary_dir / source.name
    linked_video.symlink_to(source)
    staged_source = linked_video
    staged_output = temporary_dir / (source.stem + f".{TARGET_SUFFIX}.ass")

    # Extrair legenda do vídeo se necessário: os adapters V2.2.x só aceitam
    # ASS/SSA (production_v2_2_5_adapter.py:401). O caminho legacy extrai via
    # ffmpeg (anime_subtitle_translator.py:587,1117); o V2.3.8 precisa do
    # mesmo passo antes de materializar.
    if source.suffix.lower() in VIDEO_EXTENSIONS:
        from anime_subtitle_translator import extract_subtitle, find_subtitle_stream

        stream = find_subtitle_stream(source)
        if stream is None:
            raise RuntimeError("V238_NO_SUBTITLE_STREAM_FOUND")
        stream_index, _lang, ext = stream
        extracted = temporary_dir / (source.stem + ext)
        extract_subtitle(source, stream_index, extracted)
        staged_source = extracted

    transport_cfg = load_transport_config(TRANSPORT_CONFIG_PATH)
    # Aplica Gemini profile quando provider=gemini (batch_size, retry, delay)
    _apply_gemini_profile(transport_cfg)
    # Roots ÚNICOS por job: checkpoints/captures compartilhados entre jobs
    # causam DURABLE_CAPTURE_DUPLICATE_CALL_ID (call_id derivado do request
    # colide com captures de jobs anteriores).
    job_root = STATE_DIR / "v238-runs" / str(job["id"])
    capture_root = job_root / "captures"
    capture_root.mkdir(parents=True, exist_ok=True)
    provider = WebDurableResponseProvider(
        transport_cfg,
        mode="LIVE_CAPTURED",
        capture_root=capture_root,
    )
    # Campos de identidade LIVE_CAPTURED (v238_base_materializer.py:217):
    # prompt_schema_hash, configuration_hash, candidate_commit,
    # candidate_image_id vêm de build metadata (env); glossary_hash é
    # calculado do glossário efetivo.
    import hashlib as _hashlib

    glossary = job.get("glossary") or {}
    glossary_hash = _hashlib.sha256(
        json.dumps(glossary, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest() if glossary else _hashlib.sha256(b"no-glossary").hexdigest()
    # M2: resolver episode_id/anime_series_id via Library quando ausentes
    # do job dict (queue_helpers.build_job_batch não os inclui).  L2: se o
    # episódio não estiver registrado na Library, deriva IDs determinísticos
    # do path (nunca deixa vazio -> evita V238_LIVE_CHECKPOINT_IDENTITY_MISSING).
    if not job.get("episode_id") or not job.get("anime_series_id"):
        library_episode = _library_episode_for_video(source)
        if library_episode:
            job["episode_id"] = job.get("episode_id") or library_episode.get("id")
            job["anime_series_id"] = job.get("anime_series_id") or library_episode.get("series_id")
        else:
            series_name = source.parent.parent.name if source.parent.parent else source.parent.name
            episode_name = source.stem
            job["anime_series_id"] = job.get("anime_series_id") or (
                int(_hashlib.sha256(series_name.encode("utf-8")).hexdigest()[:8], 16) % 100000)
            job["episode_id"] = job.get("episode_id") or (
                int(_hashlib.sha256(episode_name.encode("utf-8")).hexdigest()[:8], 16) % 100000)
    ctx = build_v238_execution_context(
        job=job,
        transport_config=transport_cfg,
        source_language=job.get("source_language") or "inglês",
        operation_id=_new_operation_id(job),
        execution_mode="TEST_FAKE" if job.get("dry_run") else "LIVE_CAPTURED",
        capture_root=capture_root,
        authorized_primary_models=transport_cfg.get("authorized_primary_models") or ["qwen", "gemini"],
        glossary=glossary,
        glossary_hash=glossary_hash,
        stage_completion_root=job_root / "completions",
        checkpoint_root=job_root / "checkpoints",
        job_id=job.get("id"),
        prompt_schema_hash=os.environ.get("PROMPT_SCHEMA_HASH"),
        configuration_hash=os.environ.get("CONFIGURATION_HASH"),
        candidate_commit=os.environ.get("CANDIDATE_COMMIT"),
        candidate_image_id=os.environ.get("CANDIDATE_IMAGE_ID"),
        failure_ledger_root=STATE_DIR / "failure-ledger",
    )
    # Gemini profile: aplica retry_budget como OperationCallBudget
    # (131 é default qwen; para gemini free tier limita a 8 para respeitar 15 RPM)
    primary_provider = str((transport_cfg.get("primary") or {}).get("provider", "")).lower()
    gemini_profile = transport_cfg.get("gemini_profile") or {}
    if primary_provider == "gemini" and gemini_profile.get("enabled", True):
        from v238_llama_policy import OperationCallBudget
        retry_budget = max(1, int(gemini_profile.get("retry_budget", 8)))
        # só injeta se ainda não existe (orchestrator usa setdefault)
        if "operation_budget" not in ctx or ctx.get("operation_budget") is None:
            ctx["operation_budget"] = OperationCallBudget(qwen_physical_maximum=retry_budget, llama_generation_maximum=1)
            ctx["gemini_profile"] = gemini_profile  # expõe para orchestrator/metrics
            _append_log(f"Gemini budget ativo: qwen_physical_maximum={retry_budget} (profile)", level="info", job_id=job.get("id"))
    ctx["response_provider"] = provider
    ctx["operation"] = "TRANSLATE"
    ctx["defer_intermediate_cleanup"] = False
    # O pipeline V2.3.8 roda in-process; fornece uma consulta cooperativa para
    # parar antes da próxima chamada/retry sem matar o processo do servidor.
    ctx["cancel_check"] = lambda: bool(state.get("cancel_requested"))
    # Transport do provider primário para o V226 (config.transport):
    # o Client.call (pipeline_v2_1_3.py:1440-1463) usa config.transport se
    # presente; sem ele, cai no default Ollama (qwen3.5:9b do env).
    from transport_providers import transport_from_config

    primary_section = transport_cfg.get("primary") or {}
    if primary_section.get("provider"):
        section = dict(primary_section)
        provider_name = str(section.get("provider", "")).lower()
        keys = transport_cfg.get("keys") or {}
        if not section.get("api_key") and provider_name in keys and keys[provider_name]:
            section["api_key"] = keys[provider_name]
        # Ollama: base_url default do transport é 127.0.0.1 (inacessível no
        # container). Usa TRANSLATOR_OLLAMA_URL do env quando base_url é null.
        if provider_name == "ollama" and not section.get("base_url"):
            ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "")
            if ollama_url:
                section["base_url"] = ollama_url.rsplit("/api/chat", 1)[0]
        ctx["transport"] = transport_from_config(section, {"model": section.get("model")})

    with state_lock:
        job["status"] = "STARTING"
        job["stage"] = "STARTING"
        job["started_at"] = _now()
        job["progress"] = {"scope": "episode", "current": 0, "total": 1, "label": job["name"]}
        _append_log(f"Iniciando episódio (V2.3.8): {job['name']}", level="summary", job_id=job["id"])
        _persist_locked()
    try:
        with state_lock:
            job["status"] = "TRANSLATING"
            job["stage"] = "TRANSLATING"
            _persist_locked()
        # L1: fallback de transporte (D5) — se o primary falhar com erro de
        # transporte, re-executa com o fallback + novo operation_id
        # (web_retranslation_runner.py:102-107).  Erros de validação/schema
        # NÃO disparam fallback (fail-closed).
        result = None
        transport_error: Exception | None = None
        fallback_used = False
        for attempt_name, section in (("primary", transport_cfg.get("primary")),
                                      ("fallback", transport_cfg.get("fallback"))):
            if not section:
                continue
            if attempt_name == "fallback":
                fallback_used = True
                ctx["operation_id"] = _new_operation_id(job)
                section = dict(section)
                provider_name = str(section.get("provider", "")).lower()
                keys = transport_cfg.get("keys") or {}
                if not section.get("api_key") and provider_name in keys and keys[provider_name]:
                    section["api_key"] = keys[provider_name]
                if provider_name == "ollama" and not section.get("base_url"):
                    ollama_url = os.environ.get("TRANSLATOR_OLLAMA_URL", "")
                    if ollama_url:
                        section["base_url"] = ollama_url.rsplit("/api/chat", 1)[0]
                ctx["transport"] = transport_from_config(section, {"model": section.get("model")})
                # Rebuild the semantic provider for each transport attempt;
                # otherwise it keeps selecting the persisted primary engine.
                fallback_config = dict(transport_cfg)
                fallback_config["primary"] = dict(section)
                ctx["response_provider"] = WebDurableResponseProvider(
                    fallback_config, mode="LIVE_CAPTURED", capture_root=capture_root,
                )
                ctx["model"] = str(section.get("model") or "")
                # A digest pertence ao modelo, não à tentativa. Nunca herdar
                # a digest do primary para o fallback; sem digest próprio,
                # LIVE_CAPTURED deve falhar fechado.
                ctx["model_digest"] = (
                    section.get("model_digest")
                    or transport_cfg.get("fallback_model_digest")
                )
                if staged_output.exists():
                    staged_output.unlink()
            try:
                result = execute_pipeline_plan("v2_3_8", staged_source, staged_output, ctx)
                break
            except Exception as exc:
                if _is_transport_error(exc):
                    transport_error = exc
                    continue
                raise
        if result is None:
            raise transport_error or RuntimeError("V238_NO_TRANSPORT_ATTEMPT_SUCCEEDED")
        projected = _project_v238_summary(result)
        if fallback_used:
            projected["v238_metrics"]["fallback_used"] = True
        # L3: log estruturado das métricas v238 (C7 §8.3)
        _append_log(
            "V238_METRICS " + json.dumps(projected.get("v238_metrics", {}), ensure_ascii=False),
            level="technical", job_id=job["id"],
        )
        # M6: projeção primary_ledger -> units.json para a UI não mostrar
        # progresso vazio (o telemetry lê units.json do failure ledger).
        primary_ledger = result.get("primary_ledger") if isinstance(result.get("primary_ledger"), list) else []
        if primary_ledger:
            ledger_dir = _job_ledger_dir(job)
            if ledger_dir is None:
                root = Path(os.environ.get("TRANSLATOR_FAILURE_LEDGER_ROOT", str(STATE_DIR / "failure-ledger")))
                ledger_dir = root / "jobs" / str(job.get("id", ""))
            ledger_dir.mkdir(parents=True, exist_ok=True)
            units_path = ledger_dir / "units.json"
            units_path.write_text(
                json.dumps(_project_primary_ledger_to_units(primary_ledger), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        with state_lock:
            if state.get("cancel_requested"):
                job["status"] = "CANCELLED"
                job["stage"] = "STOPPED"
                job["reason"] = "stopped_by_user"
                job["error"] = "Job interrompido pelo usuário"
            elif job.get("dry_run"):
                job["status"] = "COMPLETED"
                job["stage"] = "COMPLETED"
                job["reason"] = "dry_run"
            elif destination.exists():
                job["status"] = "FAILED"
                job["reason"] = "output_exists_race"
                job["error"] = "A legenda final apareceu durante o job; nada foi sobrescrito"
            elif not staged_output.is_file():
                job["status"] = "FAILED"
                job["reason"] = "no_output_produced"
                job["error"] = "O tradutor terminou sem produzir uma legenda final"
            elif projected.get("resolved", 0) <= 0:
                # Nenhuma unidade traduzida: o staged_output é o base
                # source-preserving (texto-fonte).  Publicá-lo seria publicar
                # uma "tradução" que é o original (ex: francês não traduzido).
                job["status"] = "FAILED"
                job["reason"] = "no_units_resolved"
                job["error"] = "Nenhuma unidade foi traduzida (resolved=0); o base source-preserving não foi publicado"
            else:
                job["status"] = "PUBLISHING"
                job["stage"] = "ARCHIVING"
                _append_log(f"Publicando: {job['name']}", level="summary", job_id=job["id"])
                _persist_locked()
                os.replace(staged_output, destination)
                # Ingestão na Library: o caminho V2.3.8 publica o arquivo E
                # cria o registro (new_record_id) para a biblioteca refletir.
                try:
                    new_record = subtitle_library.ingest_file(
                        destination, episode_id=int(job["episode_id"]), language="pt-BR",
                        source_kind="TRANSLATED", source_language="eng",
                        original_filename=f"{job.get('name', 'subtitle')}.pt-BR.ass",
                        job_id=job["id"], pipeline_version="v2_3_8", model=_model(),
                        validation_status="VALIDATED", events_total=projected.get("events"),
                        preferred=True, review_status="VALIDATED", created_by="web-v238",
                        notes="Tradução V2.3.8 via web; publicação atômica.",
                        require_authorized_path=False,
                    )
                    job["new_record_id"] = int(new_record["id"])
                except Exception as ingest_error:
                    job["new_record_id"] = None
                    job["ingest_error"] = str(ingest_error)
                job["status"] = "COMPLETED"
                job["stage"] = "COMPLETED"
                job["reason"] = "atomic_publish"
            _apply_canonical_pipeline_summary(job, projected)
            job["finished_at"] = _now()
            if job["status"] == "COMPLETED":
                _append_log(f"Concluído: {job['name']}", level="summary", job_id=job["id"])
            elif job["status"] == "CANCELLED":
                _append_log(f"Cancelado: {job['name']}", level="summary", job_id=job["id"])
            else:
                _append_log(f"Falhou: {job['name']} — {job.get('error') or 'summary marcou FAILED (unidades não resolvidas sem SKIPPED_ALLOWED)'}", level="error", job_id=job["id"])
            _persist_locked()
    except Exception as error:
        with state_lock:
            job["status"] = "FAILED"
            job["stage"] = "FAILED"
            job["reason"] = "translator_exception"
            job["error"] = str(error) if error is not None else "exceção sem mensagem"
            job["finished_at"] = _now()
            _append_log(f"Falhou: {job['name']} — {job['error']}", level="error", job_id=job["id"])
            _persist_locked()
    finally:
        import shutil

        shutil.rmtree(temporary_root, ignore_errors=True)


def _parse_progress(job: dict, line: str) -> None:
    if line.startswith("@@PROGRESS@@"):
        try:
            progress = json.loads(line[len("@@PROGRESS@@"):])
            job["progress"] = progress
        except ValueError:
            pass
    if line.startswith("SUBTRANSLATE_PIPELINE_SUMMARY "):
        try:
            summary = json.loads(line.split(" ", 1)[1])
            if not isinstance(summary, dict):
                raise ValueError("canonical summary root must be an object")
            _apply_canonical_pipeline_summary(job, summary)
        except (ValueError, IndexError, TypeError):
            job["reason"] = "invalid_canonical_summary_json"
        return
    if line.startswith(("WEB_RETRANSLATION_SUMMARY ", "V2_2_4_FAILURE_SUMMARY ")):
        try:
            marker = next(item for item in (
                "WEB_RETRANSLATION_SUMMARY ", "V2_2_4_FAILURE_SUMMARY ",
            ) if line.startswith(item))
            summary = json.loads(line[len(marker):])
            _apply_canonical_pipeline_summary(job, summary)
        except ValueError:
            job["reason"] = "invalid_summary_json"


def _consume_worker_output_line(job: dict, line: str) -> None:
    """Consume one translator stdout line without overriding canonical state."""
    _parse_progress(job, line)
    if line.startswith("SUBTRANSLATE_PIPELINE_SUMMARY "):
        _append_log(f"Validando: {job['name']}", level="summary", job_id=job["id"])
    elif line.startswith(("WEB_RETRANSLATION_SUMMARY ", "V2_2_4_FAILURE_SUMMARY ")):
        _append_log(f"Validando: {job['name']}", level="summary", job_id=job["id"])
    elif "OK ->" in line:
        job["status"] = "PUBLISHING"
        job["stage"] = "ARCHIVING"
    if line.startswith(("WEB_RETRANSLATION_SUMMARY ", "V2_2_4_FAILURE_SUMMARY ")):
        try:
            marker, raw_summary = line.split(" ", 1)
            parsed = json.loads(raw_summary)
            line = _summary_log_line(marker, parsed) if isinstance(parsed, dict) else marker
        except (ValueError, TypeError):
            pass
    _append_log(line, level=_summary_level(line), job_id=job["id"])
    _persist_locked()


def _run_episode(job: dict) -> None:
    # C5: roteia para o caminho in-process V2.3.8 quando o pipeline efetivo
    # for v2_3_8 (seleção persistida no transport_config, C4).
    if _effective_pipeline() == "v2_3_8":
        _run_episode_v238(job)
        return
    source = Path(job["source_abs"])
    # Keep the original season directory name in the staged path.  The
    # approved adapter derives series/episode metadata from subtitle_path and
    # the translator resolves per-series glossary terms from its first path
    # component.  The engine therefore sees the same semantic names while the
    # final file is still published by same-directory rename.
    temporary_root = Path(tempfile.mkdtemp(prefix=".subtranslate-", dir=str(source.parent)))
    temporary_dir = temporary_root / source.parent.name
    temporary_dir.mkdir()
    linked_video = temporary_dir / source.name
    linked_video.symlink_to(source)
    command = ["python3", "-u", str(SCRIPT_PATH), str(temporary_dir)]
    env = dict(os.environ)
    env["TRANSLATOR_SOURCE_LANGUAGE"] = job.get("source_language") or "inglês"
    # Keep legacy subprocess ledgers beside the configured web state.  The
    # container default (/app/state) is not writable in local/system runs.
    env["TRANSLATOR_FAILURE_LEDGER_ROOT"] = str(STATE_DIR / "failure-ledger")
    if job.get("dry_run"):
        command.append("--dry-run")
    with state_lock:
        job["status"] = "STARTING"
        job["stage"] = "STARTING"
        job["started_at"] = _now()
        job["progress"] = {"scope": "episode", "current": 0, "total": 1, "label": job["name"]}
        _append_log(f"Iniciando episódio: {job['name']}", level="summary", job_id=job["id"])
        _persist_locked()
    proc = None
    try:
        proc = subprocess.Popen(
            command,
            cwd="/app",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        with state_lock:
            state["process"] = proc
            job["status"] = "TRANSLATING"
            job["stage"] = "TRANSLATING"
            _append_log(f"Traduzindo: {job['name']}", level="summary", job_id=job["id"])
            _persist_locked()
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            with state_lock:
                _consume_worker_output_line(job, line)
        return_code = proc.wait()
        with state_lock:
            state["process"] = None
            if state["stop_requested"]:
                job["status"] = "CANCELLED"
                job["stage"] = "STOPPED"
                job["reason"] = "stopped_by_user"
                job["error"] = "Job interrompido pelo usuário"
            elif return_code != 0:
                job["status"] = "FAILED"
                job["stage"] = "FAILED"
                job["reason"] = "translator_exit_nonzero"
                job["error"] = f"Tradutor terminou com código {return_code}"
            elif job.get("dry_run"):
                job["status"] = "COMPLETED"
                job["stage"] = "COMPLETED"
                job["reason"] = "dry_run"
            else:
                candidate = linked_video.with_suffix(f".{TARGET_SUFFIX}.ass")
                destination = source.with_suffix(f".{TARGET_SUFFIX}.ass")
                if not candidate.exists():
                    job["status"] = "FAILED"
                    job["reason"] = "no_output_produced"
                    job["error"] = "O tradutor terminou sem produzir uma legenda final"
                elif destination.exists():
                    job["status"] = "FAILED"
                    job["reason"] = "output_exists_race"
                    job["error"] = "A legenda final apareceu durante o job; nada foi sobrescrito"
                else:
                    job["status"] = "PUBLISHING"
                    job["stage"] = "ARCHIVING"
                    _append_log(f"Publicando: {job['name']}", level="summary", job_id=job["id"])
                    # Persist the intent before the filesystem publication.
                    # After a crash, recovery will fail the in-flight job
                    # without retrying or overwriting the destination.
                    _persist_locked()
                    os.replace(candidate, destination)
                    job["status"] = "COMPLETED"
                    job["stage"] = "COMPLETED"
                    job["reason"] = "atomic_publish"
            job["finished_at"] = _now()
            if job["status"] == "COMPLETED":
                _append_log(f"Concluído: {job['name']}", level="summary", job_id=job["id"])
            elif job["status"] == "CANCELLED":
                _append_log(f"Cancelado: {job['name']}", level="summary", job_id=job["id"])
            else:
                _append_log(f"Falhou: {job['name']} — {job.get('error', 'erro desconhecido')}", level="error", job_id=job["id"])
            _persist_locked()
    except Exception as error:
        with state_lock:
            state["process"] = None
            job["status"] = "CANCELLED" if state["stop_requested"] else "FAILED"
            job["stage"] = "STOPPED" if state["stop_requested"] else "FAILED"
            job["reason"] = "backend_exception"
            job["error"] = str(error)
            job["finished_at"] = _now()
            _append_log(f"Falhou: {job['name']} — {error}", level="error", job_id=job["id"])
            _persist_locked()
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _candidate_artifact(job: dict) -> Path | None:
    """Resolve a completed candidate under its fixed job staging root."""
    if job.get("stage") != "CANDIDATE_READY" or job.get("status") != "COMPLETED":
        return None
    job_id = str(job.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id):
        return None
    name = str(job.get("candidate_output_name") or "")
    if not name or Path(name).name != name:
        return None
    expected_sha = str(job.get("candidate_output_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        return None
    root = STATE_DIR / "staging" / f"retranslation-{job_id}"
    candidate = root / name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.resolve().parent != root.resolve():
            return None
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != expected_sha:
            return None
    except OSError:
        return None
    return candidate


def _run_retranslation_episode(job: dict) -> None:
    """Retranslate from an archived source into a new immutable library record."""
    try:
        _validate_retranslation_job_integrity(job)
    except Exception as error:
        with state_lock:
            state["process"] = None
            job["status"] = "FAILED"
            job["stage"] = "FAILED"
            job["reason"] = "retranslation_integrity_failed"
            job["error"] = str(error)
            job["finished_at"] = _now()
            _append_log(f"Falhou retradução por integridade: {job.get('name', '')} — {error}", level="error", job_id=job.get("id"))
            _persist_locked()
        return
    source = Path(job["source_abs"])
    staging_root = STATE_DIR / "staging" / f"retranslation-{job['id']}"
    staging_root.mkdir(parents=True, exist_ok=True)
    output = staging_root / f"{job.get('name', 'subtitle')}.pt-BR.ass"
    effective_pipeline = _effective_pipeline()
    command = [
        "python3", "-u", str(RUNNER_PATH),
        "--source", str(source), "--output", str(output),
        "--memory-root", str(LIBRARY_ROOT), "--pipeline", effective_pipeline,
        "--job-id", str(job["id"]),
    ]
    if job.get("series_id") is not None:
        command.extend(["--anime-series-id", str(job["series_id"])])
    if job.get("episode_id") is not None:
        command.extend(["--episode-id", str(job["episode_id"])])
    if job.get("series_title"):
        command.extend(["--series-title", str(job["series_title"])])
    if job.get("episode_title"):
        command.extend(["--episode-title", str(job["episode_title"])])
    if job.get("ollama_only"):
        command.append("--no-fallback")
    with state_lock:
        job["status"] = "STARTING"
        job["stage"] = "STARTING"
        job["started_at"] = _now()
        job["progress"] = {"scope": "phase", "phase": "STARTING", "label": job.get("name", "")}
        _append_log(f"Iniciando retradução: {job.get('name', '')}", level="summary", job_id=job["id"])
        _persist_locked()
    env = dict(os.environ)
    # Per-job source language wins over the global transport config inside the
    # runner; keep parity with the normal translation path (_run_episode).
    env["TRANSLATOR_SOURCE_LANGUAGE"] = job.get("source_language") or _global_source_language()
    proc = None
    summary = None
    try:
        proc = subprocess.Popen(command, cwd="/app", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        with state_lock:
            state["process"] = proc
            job["status"] = "TRANSLATING"
            job["stage"] = "TRANSLATING"
            job["progress"] = {"scope": "phase", "phase": "TRANSLATING", "label": job.get("name", "")}
            _append_log(f"Retraduzindo da fonte original: {job.get('name', '')}", level="summary", job_id=job["id"])
            _persist_locked()
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            if line.startswith(("WEB_RETRANSLATION_SUMMARY ", "V2_2_4_FAILURE_SUMMARY ")):
                try:
                    parsed_summary = json.loads(line.split(" ", 1)[1])
                    if not isinstance(parsed_summary, dict):
                        raise ValueError("summary root must be an object")
                    summary = parsed_summary
                    with state_lock:
                        _apply_canonical_pipeline_summary(job, summary)
                except (ValueError, IndexError, TypeError):
                    with state_lock:
                        job["reason"] = "invalid_compatibility_summary_json"
            log_line = line
            if line.startswith(("WEB_RETRANSLATION_SUMMARY ", "V2_2_4_FAILURE_SUMMARY ")):
                try:
                    marker, raw_summary = line.split(" ", 1)
                    parsed_for_log = json.loads(raw_summary)
                    log_line = _summary_log_line(marker, parsed_for_log) if isinstance(parsed_for_log, dict) else marker
                except (ValueError, TypeError):
                    pass
            with state_lock:
                _append_log(log_line, level=_summary_level(log_line), job_id=job["id"])
                _persist_locked()
        return_code = proc.wait()
        with state_lock:
            state["process"] = None
        if return_code != 0 or not output.is_file():
            # V2.2.4 emits a persisted failure summary before it exits.  Keep
            # that structured evidence on the exception path instead of
            # collapsing it into only "code 1".
            if isinstance(summary, dict) and summary.get("failure_snapshot"):
                raise RuntimeError(json.dumps(summary, ensure_ascii=False))
            raise RuntimeError(f"retradução terminou sem resultado válido (código {return_code})")
        with state_lock:
            job["status"] = "VALIDATING"
            job["stage"] = "VALIDATING"
            _append_log(f"Validando retradução: {job.get('name', '')}", level="summary", job_id=job["id"])
            _persist_locked()
        audit = audit_record(source, output, source_language=job.get("source_language") or "inglês")
        with state_lock:
            job["stage"] = "AUDITING"
            job["audit"] = {
                "status": audit.get("status"),
                "flags": audit.get("flags", []),
                "blocking_flags": audit.get("blocking_flags", []),
                "review_flags": audit.get("review_flags", []),
                "eligible_for_archive": bool(audit.get("eligible_for_archive")),
            }
            _persist_locked()
        archive_decision = archive_eligibility(audit)
        if not archive_decision["eligible_for_archive"]:
            diagnostic = _preserve_retranslation_diagnostic(job, source, output, audit, summary)
            with state_lock:
                job["diagnostic"] = diagnostic
                _persist_locked()
            raise RuntimeError(f"retradução reprovada pela auditoria: {audit.get('status')} {audit.get('flags', [])}")
        if job.get("candidate_only"):
            # Candidate-only mode intentionally records the job and keeps the
            # staged artifact available for web confirmation, but it must not
            # create a Library record or publish a sidecar.
            with state_lock:
                job["status"] = "COMPLETED"
                job["stage"] = "CANDIDATE_READY"
                job["reason"] = "candidate_only_no_library_no_publication"
                job["published"] = False
                job["library_record_created"] = False
                job["candidate_output_name"] = output.name
                job["candidate_output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
                job["candidate_download_url"] = f"/retranslation/candidates/{job['id']}/download"
                job["finished_at"] = _now()
                _append_log(
                    f"Candidato pronto; não arquivado nem publicado: {job.get('name', '')}",
                    level="summary", job_id=job["id"],
                )
                _persist_locked()
            return
        _validate_retranslation_job_integrity(job)
        source_id = int(job["source_record_id"])
        old_id = job.get("old_record_id")
        if effective_pipeline == "v2_3_0":
            handle = summary.get("stage_artifact_handle") if isinstance(summary, dict) else None
            if not isinstance(handle, str) or not handle or Path(handle).name != handle:
                raise LineageContractError("retranslation V2.3.0 summary missing safe stage handle")
            stage_path = (output.parent / handle).resolve()
            if stage_path.parent != output.parent.resolve():
                raise LineageContractError("retranslation stage handle escaped staging root")
            source_record = subtitle_library.get_record(source_id)
            lineage_result = archive_v230_records(
                subtitle_library,
                source_record=source_record,
                stage_artifact=stage_path,
                final_output=output,
                stage_summary=summary.get("stages", [{}])[0].get("result", {}) if isinstance(summary, dict) and summary.get("stages") else {},
                final_summary=summary,
                expected_stage_sha256=summary.get("stage_artifact_sha256") if isinstance(summary, dict) else None,
                job_id=job["id"], model=_model(), publish=False,
                retranslated_from=int(old_id) if old_id and int(old_id) != source_id else None,
            )
            new_record = lineage_result["final_record"]
        else:
            new_record = subtitle_library.ingest_file(
                output, episode_id=int(job["episode_id"]), language="pt-BR", source_kind="TRANSLATED",
                source_language="eng", original_filename=f"{job.get('name', 'subtitle')}.pt-BR.ass",
                job_id=job["id"], pipeline_version=effective_pipeline, model=_model(),
                validation_status="VALIDATED", events_total=summary.get("events") if isinstance(summary, dict) else audit.get("output_events"),
                preferred=False, review_status="VALIDATED", created_by="web-retranslation",
                notes="Retradução solicitada pela camada web; publicação separada.", require_authorized_path=False,
            )
            subtitle_library.add_lineage(int(new_record["id"]), source_id, "TRANSLATED_FROM")
            if old_id and int(old_id) != source_id:
                subtitle_library.add_lineage(int(new_record["id"]), int(old_id), "RETRANSLATED_FROM")
        with state_lock:
            job["status"] = "COMPLETED"
            job["stage"] = "COMPLETED"
            job["reason"] = "library_record_created_no_publication"
            job["new_record_id"] = int(new_record["id"])
            job["published"] = False
            job["audit"] = {
                "status": audit.get("status"),
                "flags": audit.get("flags", []),
                "blocking_flags": audit.get("blocking_flags", []),
                "review_flags": audit.get("review_flags", []),
                "eligible_for_archive": True,
            }
            job["finished_at"] = _now()
            _append_log(f"Retradução concluída; nova versão arquivada: {job.get('name', '')}", level="summary", job_id=job["id"])
            _persist_locked()
        # G2: publicar o arquivo no diretório do vídeo (publicação separada
        # do registro na Library).  O destino é o .pt-BR.ass ao lado do vídeo.
        try:
            destination = Path(job["source_abs"]).with_suffix(f".{TARGET_SUFFIX}.ass")
            if not destination.exists():
                os.replace(output, destination)
                with state_lock:
                    job["published"] = True
                    job["reason"] = "library_record_created_and_published"
                    _append_log(f"Publicado: {destination.name}", level="summary", job_id=job["id"])
                    _persist_locked()
            else:
                with state_lock:
                    job["published"] = False
                    job["reason"] = "library_record_created_no_publication"
                    job["error"] = "Destino já existe; nada sobrescrito"
                    _persist_locked()
        except Exception as publish_error:
            with state_lock:
                job["published"] = False
                job["error"] = f"Falha ao publicar: {publish_error}"
                _persist_locked()
    except Exception as error:
        failure_payload = {}
        try:
            parsed_error = json.loads(str(error))
            if isinstance(parsed_error, dict):
                failure_payload = parsed_error
        except (TypeError, ValueError, json.JSONDecodeError):
            failure_payload = {}
        ledger_dir = failure_payload.get("ledger_dir")
        snapshot = failure_payload.get("failure_snapshot")
        retained_staging = None
        if ledger_dir:
            retained_staging = retain_staging(staging_root, ledger_dir)
        elif staging_root.exists():
            # Failures before the adapter can create a ledger still retain a
            # bounded web-level staging reference; this is not a semantic
            # retry and is pruned with the normal failure-artifact policy.
            fallback_dir = STATE_DIR / "failure-ledger" / "web-jobs" / str(job["id"])
            retained_staging = retain_staging(staging_root, fallback_dir)
        with state_lock:
            state["process"] = None
            job["status"] = "CANCELLED" if state["stop_requested"] else "FAILED"
            job["stage"] = "STOPPED" if state["stop_requested"] else "FAILED"
            job["reason"] = "retranslation_failed"
            job["error"] = str(error)
            if ledger_dir:
                job["failure_ledger_dir"] = str(ledger_dir)
            if snapshot:
                job["failure_snapshot"] = str(snapshot)
            if retained_staging:
                job["failure_staging_path"] = str(retained_staging)
            job["finished_at"] = _now()
            _append_log(f"Falhou retradução: {job.get('name', '')} — {error}", level="error", job_id=job["id"])
            _persist_locked()
    finally:
        if not job.get("failure_staging_path") and _candidate_artifact(job) is None:
            shutil.rmtree(staging_root, ignore_errors=True)
        source_staging = job.get("source_staging_path")
        if source_staging:
            shutil.rmtree(str(source_staging), ignore_errors=True)


def _validate_retranslation_job_integrity(job: dict) -> None:
    """Fail closed before execution and again before any Library archive."""
    episode_id = job.get("episode_id")
    source_id = job.get("source_record_id")
    old_id = job.get("old_record_id")
    if episode_id is None or source_id is None or old_id is None:
        raise LineageContractError("retranslation_integrity_missing_record_reference")
    try:
        episode_id = int(episode_id); source_id = int(source_id); old_id = int(old_id)
    except (TypeError, ValueError) as exc:
        raise LineageContractError("retranslation_integrity_invalid_record_reference") from exc
    source_record = subtitle_library.get_record(source_id)
    old_record = subtitle_library.get_record(old_id)
    if not source_record or not old_record:
        raise LineageContractError("retranslation_integrity_record_not_found")
    source_episode = source_record.get("episode_id")
    old_episode = old_record.get("episode_id")
    if source_episode != episode_id or old_episode != episode_id:
        raise LineageContractError("retranslation_integrity_episode_mismatch")


def _finish_session_locked() -> None:
    counts = _queue_counts()
    if not state.get("session_id"):
        return
    if any(job.get("status") in {"WAITING", "STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING"} for job in state["jobs"] if job.get("session_id") == state["session_id"]):
        return
    session = next((item for item in state["history"] if item.get("id") == state["session_id"]), None)
    if session:
        return
    session = {
        "id": state["session_id"], "folder": state.get("folder"), "created_at": state.get("session_created_at"),
        "finished_at": _now(), "requested": counts["total"], "completed": counts["completed"],
        "failed": counts["failed"], "cancelled": counts["cancelled"], "skipped": counts["skipped"],
        "not_started_after_failure": counts.get("not_started_after_failure", 0),
        "interrupted": bool(state.get("bulk_stop_reason")),
        "stop_reason": state.get("bulk_stop_reason"),
    }
    state["history"].append(session)
    state["finished_ok"] = counts["failed"] == 0 and counts["cancelled"] == 0
    state["running"] = False
    state["current_job_id"] = None
    _persist_locked()


def _worker_loop() -> None:
    while True:
        with state_lock:
            if state["stop_requested"]:
                for job in state["jobs"]:
                    if job.get("session_id") == state.get("session_id") and job.get("status") == "WAITING":
                        job["status"] = "CANCELLED"
                        job["reason"] = "stopped_by_user"
                        job["finished_at"] = _now()
                state["running"] = False
                _finish_session_locked()
                state["worker"] = None
                return
            if state["pause_requested"]:
                state["paused"] = True
                state["queue_paused"] = True
                state["running"] = any(job.get("status") in {"STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING"} for job in state["jobs"] if job.get("session_id") == state.get("session_id"))
                _persist_locked()
                if not state["running"]:
                    _finish_session_locked()
                state["worker"] = None
                return
            job = next((item for item in state["jobs"] if item.get("session_id") == state.get("session_id") and item.get("status") == "WAITING"), None)
            if not job:
                _finish_session_locked()
                state["worker"] = None
                return
            state["current_job_id"] = job["id"]
            state["running"] = True
        if job.get("operation") == "RETRANSLATE":
            _run_retranslation_episode(job)
        else:
            _run_episode(job)
        with state_lock:
            # A bulk retranslation is serial *and* fail-fast.  The old worker
            # only guaranteed one process at a time, then immediately picked
            # the next WAITING episode after a FAILED result.  That turned one
            # structural/model failure into a season-wide run.  Mark the
            # untouched tail explicitly, preserving the distinction between
            # FAILED and NOT_STARTED_AFTER_FAILURE.
            if job.get("bulk_fail_fast") and job.get("status") == "FAILED":
                state["bulk_stop_reason"] = "STOPPED_ON_FAILURE"
                state["bulk_failed_job_id"] = job.get("id")
                remaining = [
                    item for item in state["jobs"]
                    if item.get("session_id") == state.get("session_id") and item.get("status") == "WAITING"
                ]
                for item in remaining:
                    item["status"] = "NOT_STARTED_AFTER_FAILURE"
                    item["reason"] = "failure_before_start"
                    item["error"] = "Temporada interrompida após falha anterior; episódio não iniciado"
                    item["finished_at"] = _now()
                if remaining:
                    _append_log(
                        f"Temporada interrompida após falha em {job.get('episode') or job.get('name')}; "
                        f"{len(remaining)} episódio(s) não iniciado(s)",
                        level="error",
                        job_id=job.get("id"),
                    )
                _persist_locked()
            if state["pause_requested"] and not state["stop_requested"]:
                state["running"] = False
                state["paused"] = True
                state["queue_paused"] = True
                _persist_locked()
                state["worker"] = None
                return


def _start_worker_locked() -> None:
    worker = state.get("worker")
    if worker and worker.is_alive():
        return
    state["worker"] = threading.Thread(target=_worker_loop, name="subtitle-queue", daemon=True)
    state["worker"].start()


def browse(rel_path: str):
    target = _resolve_relative(rel_path)
    if not target.is_dir():
        raise FileNotFoundError(str(target))
    subfolders = sorted(p.name for p in target.iterdir() if p.is_dir() and not p.name.startswith("."))
    has_videos = any(p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS for p in target.iterdir())
    return subfolders, has_videos


def _pipeline_info() -> dict:
    # Reporting must describe the same persisted selection used by the worker;
    # the environment value is only the fallback when transport_config is
    # unavailable.
    pipeline = _effective_pipeline()
    try:
        info = pipeline_info(pipeline, model=_model(), service_available_for_mutation=True)
    except UnsupportedPipelineError as error:
        return {
            "configured_pipeline": pipeline,
            "effective_pipeline_plan": None,
            "pipeline": pipeline,
            "pipeline_label": pipeline,
            "supported": False,
            "full_pipeline": False,
            "stages": [],
            "normal_translation_supported": False,
            "retranslation_supported": False,
            "verify_supported": False,
            "service_available_for_mutation": False,
            "service_available": True,
            "model": _model() or "não configurado",
            "dry_run_supported": True,
            "error": str(error),
        }
    info["service_available"] = True
    info["dry_run_supported"] = True
    return info


def _build_jobs(sources: list[Path], session_id: str, folder: str, dry_run: bool, source_languages: dict[str, str] | None = None) -> list[dict]:
    """Build exactly one job per selected source; caller appends once."""
    return build_job_batch(
        sources, session_id=session_id, folder=folder, dry_run=dry_run,
        safe_relative=_safe_relative, friendly_number=_friendly_number, now=_now,
        source_languages=source_languages,
    )


def _estimate_episode_units(video: Path) -> tuple[int | None, str | None]:
    """Estimate subtitle units without extracting embedded tracks or writing.

    Sidecar subtitles are safe to inspect in preflight.  Embedded-only media
    reports ``None`` rather than triggering ffmpeg extraction as a side effect.
    """
    sidecars = sorted(
        path for path in video.parent.glob(f"{video.stem}*")
        if path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS
        and TARGET_SUFFIX.lower() not in path.stem.lower()
    )
    if not sidecars:
        return None, None
    try:
        from anime_subtitle_translator import load_subtitles
        subtitles = load_subtitles(sidecars[0])
        units = sum(1 for event in subtitles if str(getattr(event, "text", "")).strip())
        return units, _safe_relative(sidecars[0])
    except Exception:
        return None, _safe_relative(sidecars[0])


@app.route("/preflight", methods=["POST"])
def translation_preflight():
    """Read-only preview of a normal translation queue."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON inválido"}), 400
    try:
        folder = _validate_folder(data.get("folder", ""))
        sources = _selected_sources(folder, data.get("episodes"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except FileNotFoundError:
        return jsonify({"error": "pasta não encontrada"}), 404
    try:
        plan = get_pipeline_plan(_pipeline())
    except UnsupportedPipelineError as error:
        return jsonify({"error": str(error), "code": "unsupported_pipeline"}), 400
    raw_langs = data.get("source_languages") or {}
    source_languages = {str(k): str(v) for k, v in raw_langs.items() if v} if isinstance(raw_langs, dict) else {}
    try:
        batch_size = max(1, int(os.environ.get("TRANSLATOR_BATCH_SIZE", "4")))
    except ValueError:
        batch_size = 4
    episodes = []
    total_units = 0
    known_units = True
    for source in sources:
        units, subtitle_path = _estimate_episode_units(source)
        if units is None:
            known_units = False
        else:
            total_units += units
        episodes.append({
            "source": _safe_relative(source),
            "name": source.name,
            "language": source_languages.get(_safe_relative(source)) or source_languages.get(source.name),
            "estimated_units": units,
            "estimated_batches": ((units + batch_size - 1) // batch_size) if units is not None else None,
            "subtitle_source": subtitle_path,
            "already_translated": bool(_existing_output(source)),
        })
    return jsonify({
        "folder": _safe_relative(folder),
        "pipeline": _pipeline_info(),
        "pipeline_id": plan.id,
        "model": _model() or "não configurado",
        "source_languages": source_languages,
        "batch_size": batch_size,
        "episodes": episodes,
        "counts": {
            "selected": len(sources),
            "estimated_units": total_units if known_units else None,
            "estimated_batches": ((total_units + batch_size - 1) // batch_size) if known_units else None,
            "estimation_complete": known_units,
        },
        "read_only": True,
    })


def _validate_folder(folder_name: str) -> Path:
    if not isinstance(folder_name, str) or not folder_name.strip():
        raise ValueError("pasta inválida")
    target = _resolve_relative(folder_name)
    if not target.is_dir():
        raise FileNotFoundError(str(target))
    return target


def _selected_sources(folder: Path, values: object) -> list[Path]:
    if values is None:
        return [_resolve_relative(item["source"]) for item in _episode_records(folder) if item["status"] != "ALREADY_TRANSLATED"]
    if not isinstance(values, list) or not values:
        return []
    selected = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("episódio inválido")
        # The UI may send either a path relative to the folder or a full
        # library-relative path (ep.source). Resolve both forms safely.
        candidate = folder / value
        if not (candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS):
            candidate = _resolve_relative(value)
        candidate = candidate.resolve()
        # Containment: the resolved file must stay inside the library root.
        # Symlinked episodes are allowed only when their target remains inside
        # the library; ordinary path traversal stays rejected.
        try:
            candidate.relative_to(BASE_LIBRARY.resolve())
        except ValueError:
            raise ValueError("episódio inválido")
        if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError("episódio inválido")
        selected.append(candidate)
    return list(dict.fromkeys(selected))


@app.route("/")
def index():
    # Details are rendered by the human-friendly modal; the JSON endpoint
    # remains available to programmatic clients but is never a normal UI link.
    body = PAGE.replace("__BASE_LIBRARY__", html.escape(str(BASE_LIBRARY))).replace('href="/library/records/${r.id}">Detalhes', 'data-details-record="${r.id}">Detalhes')
    return Response(body, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.route("/favicon.svg")
def favicon_svg():
    return Response(
        FAVICON_SVG,
        mimetype="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.route("/transass-logo.png")
def transass_logo():
    """Serve the bundled brand mark without depending on external assets."""
    logo_path = Path(__file__).resolve().with_name("transass_logo.png")
    if not logo_path.is_file():
        return Response(status=404)
    return Response(
        logo_path.read_bytes(),
        mimetype="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.route("/browse")
def browse_route():
    try:
        subfolders, has_videos = browse(request.args.get("path", ""))
    except ValueError:
        return jsonify({"error": "caminho inválido"}), 400
    except FileNotFoundError:
        return jsonify({"error": "pasta não encontrada"}), 404
    return jsonify({"subfolders": subfolders, "has_videos": has_videos})


@app.route("/episodes")
def episodes_route():
    try:
        folder = _validate_folder(request.args.get("path", ""))
    except ValueError:
        return jsonify({"error": "caminho inválido"}), 400
    except FileNotFoundError:
        return jsonify({"error": "pasta não encontrada"}), 404
    source_language = request.args.get("source_language")
    try:
        offset = max(0, int(request.args.get("offset", "0")))
        limit = int(request.args.get("limit", str(EPISODE_PAGE_SIZE_DEFAULT)))
    except ValueError:
        return jsonify({"error": "paginação inválida"}), 400
    if limit < 1 or limit > EPISODE_PAGE_SIZE_MAX:
        return jsonify({"error": f"limit deve estar entre 1 e {EPISODE_PAGE_SIZE_MAX}"}), 400
    with state_lock:
        videos = _discover_episode_videos(folder)
        total = len(videos)
        page = _episode_records(folder, source_language=source_language, videos=videos[offset:offset + limit])
        return jsonify({
            "folder": _safe_relative(folder),
            "episodes": page,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + len(page) < total,
        })


@app.route("/source-status")
def source_status_route():
    episode_id = request.args.get("episode_id", type=int)
    record_id = request.args.get("record_id", type=int)
    source_language = request.args.get("source_language")
    if episode_id is None:
        return jsonify({"error": "episode_id inválido"}), 400
    with state_lock:
        return jsonify(
            _source_status_for_episode(
                episode_id,
                int(record_id) if record_id is not None else None,
                source_language=source_language,
            )
        )


@app.route("/pipeline")
def pipeline_route():
    return jsonify(_pipeline_info())


@app.route("/pipeline-config", methods=["GET"])
def pipeline_config_get():
    """C4: retorna a seleção de pipeline persistida no transport_config."""
    from transport_config_store import TransportConfigError, load_transport_config

    try:
        config = load_transport_config(TRANSPORT_CONFIG_PATH)
    except TransportConfigError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({
        "pipeline": config.get("pipeline") or "legacy",
        "authorized_primary_models": config.get("authorized_primary_models") or ["qwen"],
        "model_digest": config.get("model_digest"),
    })


@app.route("/pipeline-config", methods=["POST"])
def pipeline_config_post():
    """C4: persiste a seleção de pipeline no transport_config (M5)."""
    from transport_config_store import (
        ALLOWED_PIPELINES,
        TransportConfigError,
        load_transport_config,
        save_transport_config,
    )

    payload = request.get_json(silent=True) or {}
    try:
        current = load_transport_config(TRANSPORT_CONFIG_PATH)
        pipeline = str(payload.get("pipeline") or current.get("pipeline") or "legacy").strip().lower()
        if pipeline not in ALLOWED_PIPELINES:
            return jsonify({"error": f"pipeline inválido: {pipeline}"}), 400
        current["pipeline"] = pipeline
        if "authorized_primary_models" in payload:
            current["authorized_primary_models"] = payload["authorized_primary_models"]
        if "model_digest" in payload:
            current["model_digest"] = payload.get("model_digest")
        saved = save_transport_config(TRANSPORT_CONFIG_PATH, current)
    except TransportConfigError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({
        "pipeline": saved.get("pipeline"),
        "authorized_primary_models": saved.get("authorized_primary_models"),
        "model_digest": saved.get("model_digest"),
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/version")
def version():
    from _version import __version__

    return jsonify({"version": __version__})


@app.route("/transport-config", methods=["GET"])
def transport_config_get():
    from transport_config_store import TransportConfigError, public_transport_config

    try:
        return jsonify(public_transport_config(TRANSPORT_CONFIG_PATH))
    except TransportConfigError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/transport-config", methods=["POST"])
def transport_config_post():
    from transport_config_store import TransportConfigError, save_transport_config

    data = request.get_json(silent=True) or {}
    try:
        return jsonify(save_transport_config(TRANSPORT_CONFIG_PATH, data))
    except TransportConfigError as error:
        return jsonify({"error": str(error)}), 400


@app.route("/source-options")
def source_options_route():
    """List every translatable subtitle source (any language) for an episode.

    Accepts either ``episode_id`` (cataloged episode) or ``path`` (relative
    video path inside the library). The ``path`` form lets the UI detect
    source languages for any browsed video, even when the series is not yet
    classified as ANIME in the Library.
    """
    from web_audit_retranslation import detect_source_options

    episode_id = request.args.get("episode_id")
    rel_path = request.args.get("path")
    video_path = None
    resolved_episode_id = None
    if episode_id and str(episode_id).isdigit():
        try:
            video_path = subtitle_library._episode_video(int(episode_id))
            resolved_episode_id = int(episode_id)
        except Exception as error:
            return jsonify({"error": f"episódio não catalogado: {error}"}), 404
    elif rel_path:
        try:
            video_path = _resolve_relative(rel_path)
        except Exception as error:
            return jsonify({"error": f"caminho inválido: {error}"}), 400
        if not video_path.is_file():
            return jsonify({"error": "arquivo de vídeo não encontrado"}), 404
    else:
        return jsonify({"error": "episode_id ou path obrigatório"}), 400
    try:
        options = detect_source_options(video_path)
    except Exception as error:
        return jsonify({"error": f"não foi possível inspecionar as faixas: {error}"}), 500
    return jsonify({"episode_id": resolved_episode_id, "path": str(video_path), "options": options})


_EPISODE_RE = re.compile(r"S(?P<season>\d{1,3})E(?P<episode>\d{1,3})", re.IGNORECASE)


def _parse_season_episode(name: str) -> tuple[str | None, str | None]:
    match = _EPISODE_RE.search(name)
    if not match:
        return None, None
    return f"{int(match.group('season')):02d}", f"{int(match.group('episode')):02d}"


def _auto_classify_anime(folder_name: str) -> dict:
    """Auto-classify a folder as ANIME when any video has embedded ASS/SSA.

    Registers the series (and its episodes) so Library features work without a
    manual backfill. Respects an explicit NON_ANIME classification set by the
    user. This is the heuristic the operator requested: any .mkv carrying an
    embedded ASS/SSA subtitle track is treated as anime.
    """
    from web_audit_retranslation import detect_source_options

    folder = _validate_folder(folder_name)
    videos = [_resolve_relative(item["source"]) for item in _episode_records(folder)]
    if not videos:
        return {"classified": False, "reason": "no_videos"}

    series_relative = folder_name.strip("/").split("/")[0]
    series_title = series_relative

    has_embedded_ass = False
    for video in videos:
        try:
            options = detect_source_options(video)
        except Exception:
            continue
        if any(str(opt.get("codec", "")).lower() in ("ass", "ssa") and opt.get("textual") for opt in options):
            has_embedded_ass = True
            break
    if not has_embedded_ass:
        return {"classified": False, "reason": "no_embedded_ass_ssa"}

    existing = next(
        (s for s in subtitle_library.list_series()
         if (s.get("library_relative_path") or "").strip("/") == series_relative),
        None,
    )
    if existing and existing.get("classification") == "NON_ANIME":
        return {"classified": False, "reason": "explicit_non_anime", "series_id": existing.get("id")}

    if existing and existing.get("classification") == "ANIME":
        series_id = existing["id"]
    elif existing:
        result = subtitle_library.set_classification(existing["id"], "ANIME", source="AUTO_EMBEDDED_SUBTITLE")
        series_id = result["id"]
    else:
        result = subtitle_library.register_series(
            series_title, series_relative, classification="ANIME", source="AUTO_EMBEDDED_SUBTITLE"
        )
        series_id = result["id"]

    registered = 0
    for video in videos:
        try:
            season, episode = _parse_season_episode(video.name)
            subtitle_library.register_episode_for_path(
                series_id, video, season=season, episode=episode, episode_title=video.stem
            )
            registered += 1
        except Exception:
            continue

    return {
        "classified": True,
        "series_id": series_id,
        "title": series_title,
        "classification": "ANIME",
        "episodes_registered": registered,
    }


@app.route("/library/auto-classify", methods=["POST"])
def library_auto_classify():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON inválido"}), 400
    folder = data.get("folder", "")
    try:
        result = _auto_classify_anime(folder)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except FileNotFoundError:
        return jsonify({"error": "pasta não encontrada"}), 404
    except Exception as error:
        return jsonify({"error": f"não foi possível classificar: {error}"}), 500
    return jsonify(result)


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON inválido"}), 400
    try:
        folder = _validate_folder(data.get("folder", ""))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except FileNotFoundError:
        return jsonify({"error": "pasta não encontrada"}), 404
    verify = bool(data.get("verify", False))
    dry_run = bool(data.get("dry_run", False))
    try:
        plan = get_pipeline_plan(_pipeline())
    except UnsupportedPipelineError as error:
        return jsonify({"error": str(error), "code": "unsupported_pipeline"}), 400
    if verify and not plan.supports_verify:
        pipeline = plan.id
        return jsonify({"error": f"Verificação de legendas existentes ainda não está disponível no {pipeline}.", "code": f"verify_unsupported_{pipeline}"}), 400
    try:
        sources = _selected_sources(folder, data.get("episodes"))
    except (ValueError, OSError):
        return jsonify({"error": "seleção de episódios inválida"}), 400
    if not sources and "episodes" in data:
        return jsonify({"error": "nenhum episódio sem PT-BR foi selecionado"}), 409
    raw_langs = data.get("source_languages") or {}
    source_languages = {str(k): str(v) for k, v in raw_langs.items() if v} if isinstance(raw_langs, dict) else {}
    with state_lock:
        if state["running"] or state.get("worker") and state["worker"].is_alive() or any(job.get("status") == "WAITING" for job in state["jobs"] if job.get("session_id") == state.get("session_id")):
            return jsonify({"error": "já existe uma fila em execução ou aguardando", "code": "queue_active"}), 409
        already = [str(path) for path in sources if _existing_output(path)]
        if already:
            return jsonify({"error": "episódio já possui PT-BR e não será sobrescrito", "code": "already_translated", "episodes": already}), 409
        state["session_id"] = uuid.uuid4().hex
        state["session_created_at"] = _now()
        state["folder"] = _safe_relative(folder)
        state["running"] = True
        state["paused"] = False
        state["pause_requested"] = False
        state["queue_paused"] = False
        state["stop_requested"] = False
        state["stopped_by_user"] = False
        state["finished_ok"] = None
        state["bulk_stop_reason"] = None
        state["bulk_failed_job_id"] = None
        state["log"].clear()
        jobs = _build_jobs(sources, state["session_id"], state["folder"], dry_run, source_languages)
        state["jobs"].extend(jobs)
        _append_log(f"Fila criada: {len(jobs)} episódio(s)", level="summary")
        _persist_locked()
        _start_worker_locked()
    return jsonify({"ok": True, "session_id": state["session_id"], "queued": len(jobs)})


@app.route("/pause", methods=["POST"])
def pause():
    with state_lock:
        if not state["running"] and not any(job.get("status") == "WAITING" for job in state["jobs"] if job.get("session_id") == state.get("session_id")):
            return jsonify({"error": "nenhuma fila ativa"}), 409
        state["pause_requested"] = True
        state["paused"] = False if state.get("process") else True
        state["queue_paused"] = True
        _append_log("Pausando fila; o episódio atual terminará antes da pausa segura", level="summary")
        _persist_locked()
        return jsonify({"ok": True, "action": "pausing" if state.get("process") else "paused"})


@app.route("/resume", methods=["POST"])
def resume():
    with state_lock:
        if not state.get("queue_paused") and not state.get("pause_requested"):
            return jsonify({"error": "fila não está pausada"}), 409
        state["pause_requested"] = False
        state["paused"] = False
        state["queue_paused"] = False
        state["stop_requested"] = False
        state["cancel_requested"] = False  # M4
        state["running"] = True
        _append_log("Fila retomada", level="summary")
        _persist_locked()
        _start_worker_locked()
    return jsonify({"ok": True, "action": "resumed"})


@app.route("/stop", methods=["POST"])
def stop():
    with state_lock:
        active = state.get("process")
        waiting = [job for job in state["jobs"] if job.get("session_id") == state.get("session_id") and job.get("status") == "WAITING"]
        if not active and not waiting and not state["running"]:
            return jsonify({"error": "nenhuma fila ativa"}), 409
        state["stop_requested"] = True
        state["cancel_requested"] = True  # M4: flag cooperativa para jobs in-process
        state["pause_requested"] = False
        state["stopped_by_user"] = True
        for job in waiting:
            job["status"] = "CANCELLED"
            job["reason"] = "stopped_by_user"
            job["error"] = "Fila parada pelo usuário"
            job["finished_at"] = _now()
        if active:
            _send_process_group_signal(active, signal.SIGTERM)
        else:
            state["running"] = False
        _append_log("Parando fila", level="summary")
        _persist_locked()
    return jsonify({"ok": True, "action": "stopping" if active else "stopped"})


@app.route("/retry-failed", methods=["POST"])
def retry_failed():
    with state_lock:
        if state["running"] or any(job.get("status") == "WAITING" for job in state["jobs"] if job.get("session_id") == state.get("session_id")):
            return jsonify({"error": "aguarde a fila terminar antes de reprocessar falhos"}), 409
        failed = [job for job in state["jobs"] if job.get("session_id") == state.get("session_id") and job.get("status") == "FAILED"]
        if not failed:
            return jsonify({"error": "não há episódios falhos para reprocessar"}), 409
        state["session_id"] = uuid.uuid4().hex
        state["session_created_at"] = _now()
        state["running"] = True
        state["stop_requested"] = False
        state["pause_requested"] = False
        state["paused"] = False
        state["bulk_stop_reason"] = None
        state["bulk_failed_job_id"] = None
        for old in failed:
            old["status"] = "SKIPPED"
            old["reason"] = "retry_scheduled"
            clone = dict(old)
            clone.update({"id": uuid.uuid4().hex, "session_id": state["session_id"], "status": "WAITING", "attempt": old.get("attempt", 1) + 1, "retry_count": old.get("retry_count", 0) + 1, "created_at": _now(), "started_at": None, "finished_at": None, "error": None, "reason": "retry_failed", "summary": None, "progress": None, "flags": {}, "critical_flags": []})
            state["jobs"].append(clone)
        _append_log(f"Reprocessando {len(failed)} episódio(s) falho(s)", level="summary")
        _persist_locked()
        _start_worker_locked()
        return jsonify({"ok": True, "queued": len(failed), "session_id": state["session_id"]})


@app.route("/history")
def history_route():
    technical = request.args.get("technical", "0").lower() in {"1", "true", "yes"}
    with state_lock:
        history = state["history"][-MAX_HISTORY:]
        if not technical:
            history = [item for item in history if not _technical_history(item)]
        return jsonify({"history": history, "technical_hidden": not technical})


def _inbox_categories_locked() -> dict[str, list[dict]]:
    """Group recent work into a small, actionable post-translation inbox."""
    categories = {"ready_to_publish": [], "needs_review": [], "failed": []}
    for raw in reversed(state.get("jobs", [])):
        job = _public_job(raw)
        if not job:
            continue
        status = str(raw.get("status") or "")
        audit = raw.get("audit") or {}
        flags = list(audit.get("blocking_flags") or audit.get("flags") or raw.get("blocking_flags") or [])
        if status in {"FAILED", "NOT_STARTED_AFTER_FAILURE"}:
            categories["failed"].append(job)
        elif flags or raw.get("review_required") or audit.get("status") in {"PROBLEMAS DETECTADOS", "REVISÃO NECESSÁRIA"}:
            categories["needs_review"].append(job)
        elif status == "COMPLETED" and (job.get("candidate_download_url") or raw.get("stage") in {"CANDIDATE_READY", "READY_TO_PUBLISH"}):
            categories["ready_to_publish"].append(job)
        if sum(len(items) for items in categories.values()) >= 120:
            break
    return categories


@app.route("/inbox")
def inbox_route():
    with state_lock:
        categories = _inbox_categories_locked()
    return jsonify({
        "categories": categories,
        "counts": {key: len(value) for key, value in categories.items()},
        "updated_at": _now(),
    })


@app.route("/retranslation/candidates/<job_id>/download")
def retranslation_candidate_download(job_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(job_id or "")):
        return jsonify({"error": "candidato inválido"}), 404
    with state_lock:
        job = next((dict(item) for item in state["jobs"] if str(item.get("id")) == job_id), None)
    candidate = _candidate_artifact(job or {})
    if candidate is None:
        return jsonify({"error": "candidato não encontrado ou integridade divergente"}), 404
    return send_file(candidate, as_attachment=True, download_name=candidate.name)


@app.route("/status")
def status():
    try:
        after = max(0, int(request.args.get("after", "0")))
    except ValueError:
        return jsonify({"error": "cursor inválido"}), 400
    with state_lock:
        return jsonify(_status_payload_locked(after))


def _status_payload_locked(after: int = 0) -> dict:
    """Build the browser status payload while ``state_lock`` is held."""
    current = next((job for job in state["jobs"] if job.get("id") == state.get("current_job_id")), None)
    session_jobs = [job for job in state["jobs"] if job.get("session_id") == state.get("session_id")]
    if current is None:
        current = next((job for job in session_jobs if job.get("status") in {
            "STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING",
        }), None)
    return {
        "running": state["running"], "paused": state["paused"], "pause_requested": state["pause_requested"],
        "stopped_by_user": state["stopped_by_user"], "folder": state["folder"], "session_id": state.get("session_id"),
        "log": [{"id": entry["id"], "line": entry["line"]} for entry in state["log"] if entry["id"] > after],
        "log_details": [entry for entry in state["log"] if entry["id"] > after], "last_log_id": state["log_sequence"],
        "finished_ok": state["finished_ok"], "progress": current.get("progress") if current else None,
        "current_job": _public_job(current), "jobs": [_public_job(job) for job in session_jobs],
        "queue": _queue_counts(), "queue_paused": state["queue_paused"],
        "bulk_stop_reason": state.get("bulk_stop_reason"), "bulk_failed_job_id": state.get("bulk_failed_job_id"),
        "pipeline": _pipeline_info(),
    }


@app.route("/events")
def events():
    """Stream status changes to the UI without forcing a tight poll loop."""
    try:
        cursor = max(0, int(request.args.get("after", "0")))
    except ValueError:
        return jsonify({"error": "cursor inválido"}), 400

    @stream_with_context
    def stream():
        nonlocal cursor
        first = True
        while True:
            with state_condition:
                if not first:
                    state_condition.wait(timeout=15.0)
                first = False
                payload = _status_payload_locked(cursor)
            cursor = payload["last_log_id"]
            yield f"event: status\ndata:{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"

    return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _save_audit(record_id: int, audit: dict[str, Any]) -> dict[str, Any]:
    with state_lock:
        state.setdefault("audits", {})[str(int(record_id))] = {**audit, "record_id": int(record_id), "audited_at": _now()}
        _persist_locked()
        return state["audits"][str(int(record_id))]


def _audit_record_id(record_id: int) -> dict[str, Any]:
    record = subtitle_library.get_record(int(record_id))
    if not record:
        raise FileNotFoundError(str(record_id))
    output_path = subtitle_library.object_path_for_record(int(record_id))
    source = resolve_episode_source(subtitle_library, int(record["episode_id"]), int(record_id), materialize=False)
    audit = audit_record(
        source.get("path") if source.get("available") else None,
        output_path,
        source_language=(source.get("record") or {}).get("source_language")
        or record.get("source_language")
        or "inglês",
    )
    audit["record_id"] = int(record_id)
    audit["source_record_id"] = source.get("record_id")
    audit["source_reason"] = source.get("reason")
    return _save_audit(int(record_id), audit)


@app.route("/audit/records/<int:record_id>", methods=["POST"])
def audit_record_route(record_id: int):
    try:
        return jsonify({"ok": True, "audit": _audit_record_id(record_id), "ollama_calls": 0})
    except Exception as error:
        return _library_error(error)


@app.route("/audit/records/<int:record_id>")
def audit_record_get(record_id: int):
    with state_lock:
        result = _audit_for_record(record_id)
    return jsonify({"audit": result, "status": record_audit_status(result)})


@app.route("/audit/episodes/<int:episode_id>", methods=["POST"])
def audit_episode_route(episode_id: int):
    try:
        data = request.get_json(silent=True) or {}
        record_id = int(data["record_id"]) if data.get("record_id") else None
        record = subtitle_library.get_record(record_id) if record_id else _preferred_library_record(episode_id)
        if not record:
            raise FileNotFoundError(str(episode_id))
        result = _audit_record_id(int(record["id"]))
        return jsonify({"ok": True, "episode_id": episode_id, "audit": result, "ollama_calls": 0})
    except Exception as error:
        return _library_error(error)


@app.route("/audit/episodes/<int:episode_id>")
def audit_episode_get(episode_id: int):
    record = _preferred_library_record(episode_id)
    audit = _audit_for_record(int(record["id"])) if record else None
    return jsonify({"episode_id": episode_id, "record_id": record.get("id") if record else None, "audit": audit, "status": record_audit_status(audit)})


@app.route("/audit/series/<int:series_id>", methods=["POST"])
def audit_series_route(series_id: int):
    try:
        series = next((item for item in subtitle_library.list_series() if int(item["id"]) == series_id), None)
        if not series:
            raise FileNotFoundError(str(series_id))
        season = request.args.get("season") or (request.get_json(silent=True) or {}).get("season")
        results = []
        for episode in subtitle_library.list_episodes(series_id):
            if season and str(episode.get("season")) != str(season):
                continue
            record = _preferred_library_record(int(episode["id"]))
            if not record:
                results.append({"episode_id": episode["id"], "status": "NÃO AUDITADA", "reason": "sem legenda arquivada"})
                continue
            results.append(_audit_record_id(int(record["id"])))
        counts = {key: sum(item.get("status") == key for item in results) for key in ("SEM PROBLEMAS DETECTADOS", "REVISÃO RECOMENDADA", "PROBLEMAS DETECTADOS", "AUDITORIA PARCIAL", "NÃO AUDITADA")}
        return jsonify({"ok": True, "series_id": series_id, "results": results, "counts": counts, "ollama_calls": 0})
    except Exception as error:
        return _library_error(error)


@app.route("/library/records/compare")
def library_records_compare():
    try:
        old_id, new_id = int(request.args["old_id"]), int(request.args["new_id"])
        return jsonify(compare_records(subtitle_library.object_path_for_record(old_id), subtitle_library.object_path_for_record(new_id)))
    except Exception as error:
        return _library_error(error)


def _episode_row(episode_id: int) -> dict:
    with subtitle_library._db() as db:
        row = db.execute(
            "SELECT e.*,s.classification,s.id AS series_id,s.title AS series_title "
            "FROM media_episode e JOIN media_series s ON s.id=e.series_id WHERE e.id=?",
            (int(episode_id),),
        ).fetchone()
    if not row:
        raise FileNotFoundError(str(episode_id))
    return dict(row)


def _current_validated_record(episode_id: int) -> dict | None:
    """Return a current-pipeline validated translation, independent of preferred flags."""
    current = _pipeline()
    records = _episode_records_for_library(int(episode_id))
    candidates = [
        item for item in records
        if str(item.get("language") or "").casefold() not in {"eng", "en", "english"}
        and str(item.get("pipeline_version") or "").casefold() == current.casefold()
        and str(item.get("validation_status") or "").upper() in {"VALIDATED", "OK", "PUBLISHED"}
    ]
    return max(candidates, key=lambda item: int(item.get("id", 0))) if candidates else None


def _retranslation_preflight(
    episode_ids: list[int], *, bulk: bool = False, force_current: bool = False,
    source_languages: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Resolve every requested episode before creating any queue job.

    This is deliberately read-only.  Source extraction/materialization only
    happens after the complete preflight has no blocking item.
    """
    selected_languages = source_languages or {}
    unique_ids = list(dict.fromkeys(int(item) for item in episode_ids))
    if not unique_ids:
        raise ValueError("nenhum episódio selecionado")
    results: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for episode_id in unique_ids:
        episode = _episode_row(episode_id)
        if episode.get("classification") != "ANIME":
            item = {"episode_id": episode_id, "status": "INCOMPATIBLE", "reason": "retradução disponível somente para ANIME"}
            results.append(item); blocked.append(item); continue
        current = _current_validated_record(episode_id)
        if bulk and current and not force_current:
            item = {
                "episode_id": episode_id, "episode": episode.get("episode"),
                "media_filename": episode.get("media_filename"),
                "status": "SKIP_CURRENT_VALIDATED", "action": "SKIPPED_CURRENT_VALIDATED",
                "current_record_id": int(current["id"]), "pipeline": current.get("pipeline_version"),
                "reason": "já existe versão VALIDATED do pipeline atual",
            }
            results.append(item); skipped.append(item); continue
        old = _preferred_library_record(episode_id)
        if not old:
            item = {"episode_id": episode_id, "episode": episode.get("episode"), "status": "SOURCE_NOT_FOUND", "reason": "episódio sem versão para retraduzir"}
            results.append(item); blocked.append(item); continue
        source_language = selected_languages.get(int(episode_id)) or _global_source_language()
        source = resolve_episode_source(subtitle_library, episode_id, int(old["id"]), materialize=False, source_language=source_language)
        state.setdefault("source_status", {})[str(int(episode_id))] = _public_source_status(source)
        if not source.get("available"):
            item = {
                "episode_id": episode_id, "episode": episode.get("episode"),
                "media_filename": episode.get("media_filename"), "old_record_id": int(old["id"]),
                "status": str(source.get("status") or "SOURCE_NOT_FOUND"),
                "source_status": _public_source_status(source),
                "reason": source.get("reason") or source.get("display") or "Fonte original não disponível",
            }
            results.append(item); blocked.append(item); continue
        item = {
            "episode_id": episode_id, "episode": episode.get("episode"),
            "media_filename": episode.get("media_filename"), "old_record_id": int(old["id"]),
            "status": "ELIGIBLE", "action": "QUEUE",
            "source_record_id": source.get("record_id"), "source_status": _public_source_status(source),
            "pipeline": _pipeline(),
        }
        results.append(item); eligible.append({"episode": episode, "old": old, "source_status": source, "preflight": item})
    return {
        "ok": not blocked, "bulk": bool(bulk), "force_current": bool(force_current),
        "pipeline": _pipeline(), "model": _model(), "total": len(unique_ids),
        "results": results, "eligible": eligible, "skipped": skipped, "blocked": blocked,
        "counts": {"eligible": len(eligible), "skipped_current_validated": len(skipped), "blocked": len(blocked)},
    }


def _queue_retranslation(
    episode_ids: list[int], *, confirm: bool = False, force_current: bool = False,
    source_languages: dict[int, str] | None = None,
    process_eligible_only: bool = False, candidate_only: bool = False,
    ollama_only: bool = False,
) -> dict[str, Any]:
    bulk = bool(confirm)
    partial_selection = bool(process_eligible_only and not bulk)
    selected_languages = source_languages or {}
    with state_lock:
        if state["running"] or any(job.get("status") == "WAITING" for job in state["jobs"] if job.get("session_id") == state.get("session_id")):
            raise LibraryError("já existe uma fila em execução ou aguardando")
        preflight = _retranslation_preflight(episode_ids, bulk=bulk, force_current=force_current, source_languages=selected_languages)
        if preflight["blocked"] and not partial_selection:
            raise LibraryError(json.dumps({
                "code": "retranslation_preflight_blocked",
                "message": "pré-flight bloqueou a operação; nenhum episódio foi iniciado",
                "preflight": {key: value for key, value in preflight.items() if key != "eligible"},
            }, ensure_ascii=False))
        if not preflight["eligible"]:
            raise LibraryError(json.dumps({
                "code": "retranslation_preflight_blocked",
                "message": "pré-flight não encontrou episódio elegível; nenhum episódio foi iniciado",
                "preflight": {key: value for key, value in preflight.items() if key != "eligible"},
            }, ensure_ascii=False))
        source_job_id = f"source-resolve-{uuid.uuid4().hex}"
        prepared = []
        for item in preflight["eligible"]:
            episode, old = item["episode"], item["old"]
            job_source_language = selected_languages.get(int(episode["id"])) or _global_source_language()
            source = resolve_episode_source(
                subtitle_library, int(episode["id"]), int(old["id"]), materialize=True, job_id=source_job_id,
                source_language=job_source_language,
            )
            if not source.get("available"):
                raise LibraryError(f"{episode.get('media_filename')}: fonte deixou de estar disponível após o pré-flight")
            state.setdefault("source_status", {})[str(int(episode["id"]))] = _public_source_status(source)
            prepared.append((episode, old, source, job_source_language))
        session_id = uuid.uuid4().hex
        state["session_id"] = session_id
        state["session_created_at"] = _now()
        state["folder"] = "library/retranslation"
        state["running"] = bool(prepared)
        state["paused"] = False
        state["pause_requested"] = False
        state["queue_paused"] = False
        state["stop_requested"] = False
        state["stopped_by_user"] = False
        state["finished_ok"] = None
        state["bulk_stop_reason"] = None
        state["bulk_failed_job_id"] = None
        state["log"].clear()
        jobs = []
        now = _now()
        for item in preflight["skipped"]:
            job = {
                "id": uuid.uuid4().hex, "session_id": session_id, "operation": "RETRANSLATE",
                "folder": state["folder"], "source": item.get("current_record_id"),
                "source_record_id": item.get("current_record_id"), "old_record_id": item.get("current_record_id"),
                "episode_id": item["episode_id"], "name": item.get("media_filename") or f"E{item.get('episode', '')}",
                "episode": item.get("episode"), "status": "SKIPPED_CURRENT_VALIDATED", "created_at": now,
                "started_at": None, "finished_at": now, "error": None,
                "reason": "current_pipeline_validated", "not_started_reason": item.get("reason"),
                "summary": None, "progress": None, "flags": {}, "critical_flags": [],
                "attempt": 0, "retry_count": 0, "dry_run": False, "published": False,
                "bulk_fail_fast": bulk,
                "candidate_only": bool(candidate_only),
                "ollama_only": bool(ollama_only),
            }
            jobs.append(job); state["jobs"].append(job)
        for episode, old, source, job_source_language in prepared:
            job = {
                "id": uuid.uuid4().hex, "session_id": session_id, "operation": "RETRANSLATE",
                "folder": state["folder"], "source": source.get("record_id"), "source_abs": source["path"],
                "source_record_id": source.get("record_id"), "old_record_id": old.get("id"),
                "source_staging_path": source.get("staging_path"),
                "episode_id": episode["id"], "series_id": episode["series_id"],
                "source_language": job_source_language,
                "series_title": episode.get("series_title") or "Anime", "episode_title": episode.get("episode_title") or episode.get("media_filename") or "Episode",
                "name": episode.get("media_filename") or f"E{episode.get('episode', '')}",
                "episode": episode.get("episode"), "status": "WAITING", "created_at": _now(),
                "started_at": None, "finished_at": None, "error": None, "reason": None,
                "summary": None, "progress": None, "flags": {}, "critical_flags": [],
                "attempt": 1, "retry_count": 0, "dry_run": False, "published": False,
                "bulk_fail_fast": bulk,
                "candidate_only": bool(candidate_only),
                "ollama_only": bool(ollama_only),
            }
            jobs.append(job); state["jobs"].append(job)
        _append_log(
            f"Fila de retradução criada: {len(prepared)} episódio(s) elegível(is); "
            f"{len(preflight['skipped'])} ignorado(s) por versão atual validada; "
            f"{len(preflight['blocked'])} não elegível(is) não enfileirado(s)",
            level="summary",
        )
        _persist_locked()
        if prepared:
            _start_worker_locked()
        else:
            _finish_session_locked()
        return {
            "ok": True, "session_id": session_id, "queued": len(prepared),
            "skipped_current_validated": len(preflight["skipped"]),
            "not_eligible": len(preflight["blocked"]),
            "published": False, "source": "original_library", "preflight": {
                key: value for key, value in preflight.items() if key != "eligible"
            },
        }


@app.route("/retranslate", methods=["POST"])
def retranslate_route():
    try:
        data = request.get_json(silent=True) or {}
        episode_ids = data.get("episode_ids") or []
        if not isinstance(episode_ids, list):
            return jsonify({"error": "episode_ids deve ser lista"}), 400
        if data.get("season_episode_ids"):
            episode_ids = data["season_episode_ids"]
        raw_langs = data.get("source_languages") or {}
        source_languages = {}
        if isinstance(raw_langs, dict):
            for key, value in raw_langs.items():
                try:
                    source_languages[int(key)] = str(value)
                except (TypeError, ValueError):
                    continue
        return jsonify(_queue_retranslation(
            episode_ids,
            confirm=bool(data.get("confirm") or data.get("bulk")),
            force_current=bool(data.get("force_current") or data.get("force_retranslation")),
            source_languages=source_languages,
            process_eligible_only=data.get("process_eligible_only") is True,
            candidate_only=data.get("candidate_only") is True,
            ollama_only=data.get("ollama_only") is True,
        ))
    except Exception as error:
        return _library_error(error)


@app.route("/retranslate/preflight", methods=["POST"])
def retranslate_preflight_route():
    try:
        data = request.get_json(silent=True) or {}
        episode_ids = data.get("episode_ids") or data.get("season_episode_ids") or []
        if not isinstance(episode_ids, list):
            return jsonify({"error": "episode_ids deve ser lista"}), 400
        raw_langs = data.get("source_languages") or {}
        source_languages = {}
        if isinstance(raw_langs, dict):
            for key, value in raw_langs.items():
                try:
                    source_languages[int(key)] = str(value)
                except (TypeError, ValueError):
                    continue
        result = _retranslation_preflight(
            episode_ids,
            bulk=bool(data.get("bulk", True)),
            force_current=bool(data.get("force_current") or data.get("force_retranslation")),
            source_languages=source_languages,
        )
        # Do not expose resolved filesystem paths or internal objects.
        return jsonify({key: value for key, value in result.items() if key != "eligible"})
    except Exception as error:
        return _library_error(error)


@app.route("/library/legacy")
def library_legacy():
    try:
        series_id = int(request.args["series_id"])
        season = request.args.get("season")
        selected = []
        for episode in subtitle_library.list_episodes(series_id):
            if season and str(episode.get("season")) != str(season):
                continue
            record = _preferred_library_record(int(episode["id"]))
            if not record:
                continue
            audit = _audit_for_record(int(record["id"]))
            current = _pipeline()
            legacy = record.get("source_kind") == "IMPORTED_EXISTING" or not record.get("pipeline_version") or record.get("pipeline_version") != current or record_audit_status(audit) == "PROBLEMAS DETECTADOS"
            if legacy:
                selected.append({"episode_id": episode["id"], "record_id": record["id"], "reason": "legacy_or_audit"})
        return jsonify({"series_id": series_id, "episode_ids": [item["episode_id"] for item in selected], "items": selected, "pipeline": _pipeline_info()})
    except Exception as error:
        return _library_error(error)


def _library_error(error: Exception):
    if isinstance(error, SourceVersionMismatch):
        return jsonify({"error": str(error), "code": error.code}), 409
    if isinstance(error, ReviewError):
        return jsonify({"error": str(error), "code": "review_error"}), 400
    if isinstance(error, (PathSecurityError, ClassificationError)):
        return jsonify({"error": str(error), "code": "library_path_or_classification"}), 403
    if isinstance(error, PublicationConflict):
        return jsonify({"error": str(error), "code": "publication_conflict"}), 409
    if isinstance(error, FileNotFoundError):
        return jsonify({"error": "objeto ou episódio não encontrado", "code": "library_not_found"}), 404
    return jsonify({"error": str(error), "code": "library_error"}), 400


@app.route("/review/<int:record_id>")
def review_page(record_id: int):
    if subtitle_library.get_record(record_id) is None:
        return "Registro não encontrado", 404
    return Response(
        REVIEW_PAGE.replace("__RECORD_ID__", str(record_id)),
        mimetype="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/review/sessions", methods=["POST"])
def review_session_create():
    try:
        data = request.get_json(silent=True) or {}
        result = human_feedback.open_session(int(data["record_id"]), source_record_id=int(data["source_record_id"]) if data.get("source_record_id") is not None else None, created_by=str(data.get("created_by") or "local_operator"), notes=data.get("notes"))
        return jsonify(result), 201
    except Exception as error:
        return _library_error(error)


@app.route("/review/sessions/<int:session_id>")
def review_session_detail(session_id: int):
    try:
        result = human_feedback.session(session_id)
        result["counts"] = human_feedback.review_counts(session_id)
        return jsonify(result)
    except Exception as error:
        return _library_error(error)


@app.route("/review/sessions/<int:session_id>/segments")
def review_session_segments(session_id: int):
    try:
        result = human_feedback.session(session_id)
        status = request.args.get("status")
        segments = result["segments"]
        if status:
            segments = [item for item in segments if item.get("status") == status.upper()]
        query = request.args.get("q", "").casefold()
        if query:
            segments = [item for item in segments if query in (item.get("source_text", "") + " " + item.get("generated_text", "")).casefold()]
        return jsonify({"session_id": session_id, "segments": segments, "counts": human_feedback.review_counts(session_id)})
    except Exception as error:
        return _library_error(error)


@app.route("/review/sessions/<int:session_id>/abandon", methods=["POST"])
def review_session_abandon(session_id: int):
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(human_feedback.abandon_session(session_id, actor=str(data.get("actor") or "local_operator"), reason=data.get("reason")))
    except Exception as error:
        return _library_error(error)


@app.route("/review/segments/<int:segment_id>/status", methods=["POST"])
def review_segment_status(segment_id: int):
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(human_feedback.mark_segment(segment_id, str(data.get("status", "")), actor=str(data.get("actor") or "local_operator"), reason=data.get("reason")))
    except Exception as error:
        return _library_error(error)


@app.route("/review/segments/<int:segment_id>/correction", methods=["POST"])
def review_correction_save(segment_id: int):
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data.get("corrected_text"), str):
            return jsonify({"error": "corrected_text é obrigatório"}), 400
        return jsonify(human_feedback.save_correction(segment_id, data["corrected_text"], reason=data.get("reason"), notes=data.get("notes"), created_by=str(data.get("created_by") or "local_operator")))
    except Exception as error:
        return _library_error(error)


@app.route("/review/corrections/<int:correction_id>/approve", methods=["POST"])
def review_correction_approve(correction_id: int):
    try:
        data = request.get_json(silent=True) or {}
        result = human_feedback.approve_correction(correction_id, approved_by=str(data.get("approved_by") or "local_operator"), reason=data.get("reason"))
        # Materialization is deliberately edge-triggered here.  The memory
        # service only accepts the explicit SEGMENT_APPROVED provenance and
        # never calls Ollama; an operational error must not undo the human
        # approval that was already committed.
        try:
            result["memory_sync"] = translation_memory.sync_approved(actor="review_approval")
        except Exception as memory_error:
            result["memory_sync_error"] = str(memory_error)
        return jsonify(result)
    except Exception as error:
        return _library_error(error)


@app.route("/review/corrections/<int:correction_id>/reject", methods=["POST"])
def review_correction_reject(correction_id: int):
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(human_feedback.reject_correction(correction_id, rejected_by=str(data.get("rejected_by") or "local_operator"), reason=data.get("reason")))
    except Exception as error:
        return _library_error(error)


@app.route("/review/sessions/<int:session_id>/materialize", methods=["POST"])
def review_materialize(session_id: int):
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(human_feedback.materialize(session_id, created_by=str(data.get("created_by") or "local_operator"))), 201
    except Exception as error:
        return _library_error(error)


@app.route("/review/versions/<int:record_id>/approve", methods=["POST"])
def review_version_approve(record_id: int):
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(human_feedback.approve_version(record_id, approved_by=str(data.get("approved_by") or "local_operator"), reason=data.get("reason")))
    except Exception as error:
        return _library_error(error)


@app.route("/review/versions/<int:record_id>/publish", methods=["POST"])
def review_version_publish(record_id: int):
    try:
        record = subtitle_library.get_record(record_id)
        if not record or record.get("source_kind") != "HUMAN_CORRECTED" or record.get("review_status") != "APPROVED":
            raise ReviewError("somente versão HUMAN_CORRECTED APPROVED pode ser publicada")
        data = request.get_json(silent=True) or {}
        allow_replace = bool(data.get("confirm_replace", False))
        publication = subtitle_library.publish(record_id, allow_replace=allow_replace)
        with subtitle_library._db() as db:
            human_feedback._audit(db, "version_published", "SUBTITLE_RECORD", record_id, "APPROVED", "PUBLISHED", str(data.get("published_by") or "local_operator"), {"publication_id": publication.get("id"), "allow_replace": allow_replace})
        return jsonify({"ok": True, "publication": publication, "qwen_called": False})
    except Exception as error:
        return _library_error(error)


@app.route("/review/audit")
def review_audit():
    try:
        target_type = request.args.get("target_type") or None
        target_id = int(request.args["target_id"]) if request.args.get("target_id") else None
        return jsonify({"events": human_feedback.audit_events(target_type=target_type, target_id=target_id)})
    except Exception as error:
        return _library_error(error)


@app.route("/memory")
def memory_index():
    try:
        series_id = int(request.args["series_id"]) if request.args.get("series_id") else None
        status = request.args.get("status") or None
        return jsonify({
            "scope": "ANIME",
            "counts": translation_memory.counts(),
            "items": translation_memory.list_items(series_id=series_id, status=status),
            "conflicts": translation_memory.conflicts(series_id=series_id),
            "pipeline": _pipeline_info(),
        })
    except Exception as error:
        return _library_error(error)


@app.route("/glossary", methods=["GET", "POST"])
def glossary_route():
    """Glossary 1.0 API; entries guide prompts and never post-replace text."""
    if request.method == "GET":
        entries = glossary_store.list(request.args.get("q", ""), request.args.get("scope"), request.args.get("status"))
        return jsonify({"schema_version": 1, "name": "Glossary 1.0", "entries": entries, "count": len(entries)})
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(glossary_store.add(payload)), 201
    except (ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/glossary/<entry_id>", methods=["PATCH"])
def glossary_update_route(entry_id: str):
    try:
        return jsonify(glossary_store.update(entry_id, request.get_json(silent=True) or {}))
    except (ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/glossary/<entry_id>/disable", methods=["POST"])
def glossary_disable_route(entry_id: str):
    try:
        return jsonify(glossary_store.update(entry_id, {"status": "DISABLED"}))
    except (ValueError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 404


@app.route("/glossary/export")
def glossary_export_route():
    return jsonify({"schema_version": 1, "name": "Glossary 1.0", "entries": glossary_store.list()})


@app.route("/glossary/ui")
def glossary_ui_route():
    template = (_WEB_ASSET_ROOT / "templates" / "glossary.html").read_text(encoding="utf-8")
    return Response(template, mimetype="text/html", headers={"Cache-Control": "no-store"})


@app.route("/memory/items/<int:item_id>")
def memory_item_detail(item_id: int):
    try:
        items = [item for item in translation_memory.list_items() if int(item["id"]) == item_id]
        if not items:
            raise FileNotFoundError(str(item_id))
        item = items[0]
        item["usage"] = translation_memory.usage(item_id=item_id)
        item["conflicts"] = [c for c in translation_memory.conflicts() if str(item_id) in str(c.get("memory_item_ids", ""))]
        return jsonify(item)
    except Exception as error:
        return _library_error(error)


@app.route("/memory/items/<int:item_id>/status", methods=["POST"])
def memory_item_status(item_id: int):
    try:
        data = request.get_json(silent=True) or {}
        status = str(data.get("status") or "").upper()
        # Text and provenance are immutable here.  Human changes go through
        # the review flow and produce a new correction/version.
        if status not in {"ACTIVE", "INACTIVE", "REVOKED"}:
            return jsonify({"error": "use revisão humana para substituir; API permite apenas ativar, desativar ou revogar", "code": "memory_status_controlled"}), 400
        result = translation_memory.set_status(item_id, status)
        return jsonify({"ok": True, "item": result})
    except Exception as error:
        return _library_error(error)


@app.route("/memory/sync", methods=["POST"])
def memory_sync():
    try:
        result = translation_memory.sync_approved(actor="local_operator")
        return jsonify({"ok": True, "result": result, "counts": translation_memory.counts()})
    except Exception as error:
        return _library_error(error)


@app.route("/memory/usage")
def memory_usage():
    try:
        return jsonify({"usage": translation_memory.usage(job_id=request.args.get("job_id"), item_id=int(request.args["item_id"]) if request.args.get("item_id") else None)})
    except Exception as error:
        return _library_error(error)


@app.route("/library")
def library_index():
    """JSON summary; the browser UI uses this endpoint without exposing paths."""
    return jsonify({
        "scope": "ANIME",
        "root_configured": True,
        "counts": subtitle_library.counts(),
        "pipeline": _pipeline_info(),
    })


@app.route("/library/series")
def library_series():
    try:
        classification = request.args.get("classification")
        if classification and classification.upper() not in {"ANIME", "NON_ANIME", "UNKNOWN"}:
            raise ClassificationError("classificação inválida")
        return jsonify({"series": subtitle_library.list_series(classification=classification)})
    except Exception as error:
        return _library_error(error)


@app.route("/library/records")
def library_records():
    try:
        published = request.args.get("published")
        published_value = None if published is None else published.lower() in {"1", "true", "yes"}
        return jsonify({"records": subtitle_library.list_records(
            series_id=int(request.args["series_id"]) if request.args.get("series_id") else None,
            episode_id=int(request.args["episode_id"]) if request.args.get("episode_id") else None,
            language=request.args.get("language") or None,
            source_kind=request.args.get("source_kind") or None,
            published=published_value,
        )})
    except (ValueError, TypeError) as error:
        return jsonify({"error": f"filtro inválido: {error}"}), 400
    except Exception as error:
        return _library_error(error)


@app.route("/library/series/<int:series_id>")
def library_series_detail(series_id: int):
    try:
        series = next((item for item in subtitle_library.list_series() if int(item["id"]) == series_id), None)
        if series is None:
            raise FileNotFoundError(str(series_id))
        series["episodes"] = subtitle_library.list_episodes(series_id)
        include_source_status = request.args.get("source_status", "0").lower() in {"1", "true", "yes"}
        for episode in series["episodes"]:
            episode["records"] = [_decorate_library_record(record) for record in episode.get("records", [])]
            episode["version_summary"] = _version_summary(episode["records"])
            preferred = _preferred_library_record(int(episode["id"]))
            if preferred:
                episode["preferred_record_id"] = preferred["id"]
                episode["audit"] = _public_audit(_audit_for_record(int(preferred["id"])))
                if include_source_status:
                    source = _source_status_for_episode(int(episode["id"]), int(preferred["id"]))
                    episode["source_status"] = source
                    episode["retranslation_available"] = bool(source.get("available"))
                    episode["retranslation_reason"] = source.get("reason")
                else:
                    episode["retranslation_available"] = False
                    episode["retranslation_reason"] = "source_status não solicitado"
            elif include_source_status:
                source = _source_status_for_episode(int(episode["id"]))
                episode["source_status"] = source
                episode["retranslation_available"] = bool(source.get("available"))
                episode["retranslation_reason"] = source.get("reason")
        return jsonify(series)
    except Exception as error:
        return _library_error(error)


@app.route("/library/episodes/<int:episode_id>")
def library_episode_detail(episode_id: int):
    try:
        with subtitle_library._db() as db:  # internal read-only lookup; paths stay server-side
            row = db.execute("SELECT e.*,s.title AS series_title,s.classification FROM media_episode e JOIN media_series s ON s.id=e.series_id WHERE e.id=?", (episode_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(str(episode_id))
        result = dict(row)
        result["records"] = [_decorate_library_record(record) for record in subtitle_library.list_records(episode_id=episode_id)]
        result["version_summary"] = _version_summary(result["records"])
        return jsonify(result)
    except Exception as error:
        return _library_error(error)


@app.route("/library/records/<int:record_id>")
def library_record_detail(record_id: int):
    try:
        record = _decorate_library_record(subtitle_library.get_record(record_id))
        if record is None:
            raise FileNotFoundError(str(record_id))
        record["lineage"] = subtitle_library.lineage(record_id)
        record["publications"] = subtitle_library.publications(record_id=record_id)
        return jsonify(record)
    except Exception as error:
        return _library_error(error)


@app.route("/library/records/<int:record_id>/download")
def library_record_download(record_id: int):
    try:
        record = subtitle_library.get_record(record_id)
        if record is None:
            raise FileNotFoundError(str(record_id))
        path = subtitle_library.object_path_for_record(record_id)
        return send_file(path, as_attachment=True, download_name=record.get("original_filename") or f"subtitle-{record_id}.{record.get('format', 'ass')}")
    except Exception as error:
        return _library_error(error)


@app.route("/library/records/<int:record_id>/publish", methods=["POST"])
@app.route("/library/records/<int:record_id>/republish", methods=["POST"])
def library_record_publish(record_id: int):
    try:
        # A target path is derived from the registered episode/video.  The
        # browser never gets authority to submit an arbitrary filesystem path.
        data = request.get_json(silent=True) or {}
        record = subtitle_library.get_record(record_id)
        if record is None:
            raise FileNotFoundError(str(record_id))
        current_publication = next((item for item in subtitle_library.publications(episode_id=int(record["episode_id"])) if item.get("status") == "PUBLISHED"), None)
        if current_publication:
            current_record = subtitle_library.get_record(int(current_publication["subtitle_record_id"]))
            if current_record and current_record.get("sha256") == record.get("sha256"):
                return jsonify({"ok": True, "noop": True, "qwen_called": False, "publication": current_publication, "record": _decorate_library_record(record)})
        result = subtitle_library.publish(record_id, allow_replace=bool(data.get("confirm_replace", False)))
        return jsonify({"ok": True, "noop": False, "publication": result, "record": _decorate_library_record(record), "qwen_called": False})
    except Exception as error:
        return _library_error(error)


@app.route("/library/records/<int:record_id>/publications")
def library_record_publications(record_id: int):
    try:
        return jsonify({"publications": subtitle_library.publications(record_id=record_id)})
    except Exception as error:
        return _library_error(error)


@app.route("/library/records/<int:record_id>/lineage")
def library_record_lineage(record_id: int):
    try:
        if subtitle_library.get_record(record_id) is None:
            raise FileNotFoundError(str(record_id))
        return jsonify({"lineage": subtitle_library.lineage(record_id)})
    except Exception as error:
        return _library_error(error)


@app.route("/library/classifications", methods=["GET", "POST"])
def library_classifications():
    try:
        if request.method == "GET":
            return jsonify({"series": subtitle_library.list_series()})
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "JSON inválido"}), 400
        classification = str(data.get("classification", "")).upper()
        if "series_id" in data:
            result = subtitle_library.set_classification(int(data["series_id"]), classification, source="USER")
        else:
            title = str(data.get("title", "")).strip()
            relative_path = str(data.get("relative_path", "")).strip()
            if not title or not relative_path:
                return jsonify({"error": "title e relative_path são obrigatórios"}), 400
            result = subtitle_library.register_series(title, relative_path, classification=classification, source="USER")
        return jsonify({"ok": True, "series": result})
    except Exception as error:
        return _library_error(error)


PAGE = (_WEB_ASSET_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
REVIEW_PAGE = (_WEB_ASSET_ROOT / "templates" / "review.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
