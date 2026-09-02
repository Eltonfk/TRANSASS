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

from flask import Flask, Response, jsonify, request, send_file

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


class StatePersistenceError(RuntimeError):
    """State could not be durably committed; callers must fail closed."""

app = Flask(__name__)
state_lock = threading.RLock()

# This is the same official Subtranslate mark used by the previous web build.
# It is served as a real asset so browser cache/path handling cannot remove it.
FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#2f6fed"/><text x="32" y="43" text-anchor="middle" font-size="38">🎬</text></svg>'''


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


def _episode_records(folder: Path, source_language: str | None = None) -> list[dict]:
    records = []
    for video in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS):
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
    with state_lock:
        return jsonify({"folder": _safe_relative(folder), "episodes": _episode_records(folder, source_language=source_language)})


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
        current = next((job for job in state["jobs"] if job.get("id") == state.get("current_job_id")), None)
        session_jobs = [job for job in state["jobs"] if job.get("session_id") == state.get("session_id")]
        if current is None:
            current = next((job for job in session_jobs if job.get("status") in {
                "STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING",
            }), None)
        return jsonify({
            "running": state["running"], "paused": state["paused"], "pause_requested": state["pause_requested"],
            "stopped_by_user": state["stopped_by_user"], "folder": state["folder"], "session_id": state.get("session_id"),
            "log": [{"id": entry["id"], "line": entry["line"]} for entry in state["log"] if entry["id"] > after],
            "log_details": [entry for entry in state["log"] if entry["id"] > after], "last_log_id": state["log_sequence"],
            "finished_ok": state["finished_ok"], "progress": current.get("progress") if current else None,
            "current_job": _public_job(current), "jobs": [_public_job(job) for job in session_jobs],
            "queue": _queue_counts(), "queue_paused": state["queue_paused"],
            "bulk_stop_reason": state.get("bulk_stop_reason"), "bulk_failed_job_id": state.get("bulk_failed_job_id"),
            "pipeline": _pipeline_info(),
        })


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
    return Response("""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><title>Glossary</title><style>body{font:16px system-ui;max-width:900px;margin:1rem auto;padding:0 1rem}input,select{font:inherit;padding:.45rem;margin:.2rem;width:min(28rem,90%)}table{border-collapse:collapse;width:100%;margin-top:1rem}td,th{border-bottom:1px solid #ddd;text-align:left;padding:.4rem}small{color:#666}</style><h1>Glossário 1.0</h1><p><small>Orientação contextual para o modelo; não substitui nem alimenta automaticamente a Translation Memory.</small></p><input id=q placeholder='Buscar'><select id=s><option value=''>Todos os escopos</option><option>GLOBAL</option><option>ANIME</option><option>CHARACTER</option></select><table><thead><tr><th>Inglês</th><th>PT-BR</th><th>Categoria</th><th>Status</th></tr></thead><tbody id=rows></tbody></table><script>async function load(){let q=document.querySelector('#q').value,s=document.querySelector('#s').value;let d=await (await fetch('/glossary?q='+encodeURIComponent(q)+'&scope='+s)).json();document.querySelector('#rows').innerHTML=d.entries.map(e=>`<tr><td>${e.source_expression}</td><td>${e.preferred_pt_br}</td><td>${e.category}</td><td>${e.status}</td></tr>`).join('')}q.oninput=load;s.onchange=load;load()</script>""", mimetype="text/html")


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


PAGE = r'''<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1"><link rel="apple-touch-icon" href="/favicon.svg?v=1">
<title>Transass · Central de tradução</title>
<style>
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--panel2:#1c2430;--line:#30363d;--text:#e6edf3;--muted:#9da7b3;--blue:#3b82f6;--green:#3fb950;--yellow:#d29922;--red:#f85149;}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#0d1117,#111827);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1220px;margin:auto;padding:22px 16px 48px;min-width:0}h1,h2{margin:0}h1{font-size:1.35rem}.muted{color:var(--muted)}.top{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px}.chips{display:flex;gap:8px;flex-wrap:wrap}.chip,.badge{border:1px solid var(--line);border-radius:999px;padding:5px 10px;font-size:.78rem;background:var(--panel2)}.online{color:#b7f5c0;border-color:#2ea043}.grid{display:grid;grid-template-columns:minmax(220px,min(36vw,360px)) minmax(0,1fr);gap:14px;align-items:start}.panel{background:rgba(22,27,34,.92);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 12px 30px #0002;min-width:0}.panel h2{font-size:.95rem;margin-bottom:12px}.wide{grid-column:1/-1}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;min-width:0}.crumb{min-height:38px;padding:8px 10px;background:#0d1117;border:1px solid var(--line);border-radius:8px;overflow:auto;white-space:nowrap}.crumb button{background:none;border:0;color:#79c0ff;padding:0;cursor:pointer}.actions{margin-top:12px}.button,button{border:1px solid var(--line);border-radius:8px;padding:9px 13px;background:#21262d;color:var(--text);cursor:pointer;min-height:40px}.button.primary{background:#238636;border-color:#2ea043}.button.warn{background:#9e6a03}.button.danger{background:#8e1519}.button:disabled,button:disabled{opacity:.45;cursor:not-allowed}select{background:#0d1117;border:1px solid var(--line);color:var(--text);padding:9px;border-radius:8px;min-height:40px;max-width:100%}.episodes{margin-top:12px;display:grid;gap:7px;max-height:420px;overflow:auto}.episode{display:grid;grid-template-columns:26px 58px minmax(0,1fr) auto;gap:8px;align-items:center;padding:9px;border:1px solid var(--line);border-radius:9px;background:#111820;min-width:0}.episode input{width:18px;height:18px}.epname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}.badge.ok{color:#b7f5c0;border-color:#2ea043}.badge.fail{color:#ffaba8;border-color:#f85149}.badge.wait{color:#f2cc60;border-color:#9e6a03}.badge.neutral{color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}.stat{padding:10px;background:#111820;border:1px solid var(--line);border-radius:9px;min-width:0}.stat b{display:block;font-size:1.15rem}.progress{height:10px;background:#30363d;border-radius:9px;overflow:hidden}.progress i{display:block;height:100%;background:linear-gradient(90deg,#2f81f7,#3fb950);width:0}.jobline{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;border-bottom:1px solid #21262d;padding:8px 0;min-width:0}.jobline:last-child{border:0}.record-actions{display:flex;justify-content:flex-end;align-items:center;gap:6px;flex-wrap:wrap}.log{background:#080b0f;border:1px solid var(--line);border-radius:9px;padding:10px;max-height:290px;overflow:auto;font:12px/1.5 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap}.log .error{color:#ffaba8}.log .summary{color:#b7f5c0}.history{max-height:300px;overflow:auto}.note{padding:9px;background:#1f2937;border-left:3px solid var(--yellow);border-radius:5px;color:#e5e7eb}.hidden{display:none}@media(max-width:1020px){.grid{grid-template-columns:minmax(190px,29vw) minmax(0,1fr)}main{padding-left:12px;padding-right:12px}}@media(max-width:820px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}.nav-panel{max-height:none}.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.episode{grid-template-columns:24px 55px minmax(0,1fr) auto}}@media(max-width:480px){main{padding:12px 9px 32px}.top{display:block}.chips{margin-top:10px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.episode{grid-template-columns:24px 45px minmax(0,1fr)}.episode .badge{grid-column:3/-1;justify-self:start}.actions .button,.record-actions .button{flex:1 1 140px}.jobline{grid-template-columns:1fr}.record-actions{justify-content:flex-start}.panel{padding:12px}}
:focus-visible{outline:2px solid #58a6ff;outline-offset:2px}.job-progress-meta{display:flex;gap:8px;flex-wrap:wrap;font-size:.88rem}.job-progress-meta span{white-space:nowrap}
/* Product shell.  The primary translation workflow stays visible while
   secondary tools live in focused workspaces instead of one endless page. */
body{background:radial-gradient(circle at 12% -8%,#193252 0,#0b1119 36%),#0b1119;color:#edf4fb;min-height:100vh}
body:before{content:'';position:fixed;inset:0;pointer-events:none;background:linear-gradient(90deg,#4ea1ff08 1px,transparent 1px),linear-gradient(#4ea1ff06 1px,transparent 1px);background-size:36px 36px;mask-image:linear-gradient(to bottom,#0008,transparent 60%)}
main{position:relative;max-width:1460px;padding:24px 24px 56px}
.top{padding:18px 20px;margin-bottom:12px;border:1px solid #2c4661;border-radius:18px;background:linear-gradient(120deg,#17283ceF,#111b29f2);box-shadow:0 18px 46px #0005}
.brand{display:flex;align-items:center;gap:13px}.brand-logo{display:block;width:58px;height:58px;flex:none;object-fit:contain;filter:drop-shadow(0 8px 16px #0008)}.top h1{font-size:1.58rem;letter-spacing:-.035em}.top .muted{margin-top:3px}.brand-joke{color:#91a4b8;font-size:.82rem}
.chips{justify-content:flex-end}.chip{padding:6px 11px;background:#111c2a;border-color:#334a63}.chip.online{background:#10271c}.chip.offline{color:#ffaaa5;border-color:#763a3a;background:#2a1518}
.workspace-nav{display:flex;gap:6px;padding:5px;margin-bottom:16px;overflow-x:auto;border:1px solid #26394d;border-radius:13px;background:#0e1722d9;box-shadow:0 8px 26px #0002}.nav-button{flex:0 0 auto;min-height:38px;padding:8px 14px;border:0;background:transparent;color:#9fb0c2}.nav-button:hover:not(:disabled){transform:none;background:#172537;border-color:transparent}.nav-button.active{background:#20344c;color:#f4f8fc;box-shadow:inset 0 0 0 1px #3b5d80}.nav-button .nav-count{margin-left:5px;color:#7db5ff}
.view[hidden]{display:none}.view{animation:view-in .16s ease-out}@keyframes view-in{from{opacity:.35;transform:translateY(4px)}to{opacity:1;transform:none}}
.workflow-strip{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px;padding:12px 15px;border:1px solid #2e4966;border-radius:13px;background:#122034}.workflow-copy{display:flex;align-items:center;gap:10px;min-width:0}.workflow-step{display:grid;place-items:center;flex:0 0 auto;width:29px;height:29px;border-radius:50%;background:#2f81f7;color:white;font-weight:800}.workflow-hint{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.workflow-safety{color:#87d99a;font-size:.82rem;white-space:nowrap}
.grid{grid-template-columns:minmax(260px,310px) minmax(0,1fr);gap:16px}.panel{padding:18px;border-color:#293b4e;background:linear-gradient(145deg,#151f2bf7,#101821f7);box-shadow:0 12px 28px #0003}.panel h2{font-size:1.02rem;letter-spacing:-.015em}.panel-kicker{color:#6ea8fe;font-size:.69rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase;margin-bottom:7px}.panel-subtitle{margin:-6px 0 12px;font-size:.84rem;color:#91a1b2}.nav-panel{position:sticky;top:12px}.crumb{background:#0a121b;border-color:#26394d}.actions{margin-top:10px}.actions .button{flex:0 1 auto}
.button,button,select,input{border-radius:10px;transition:background .15s,border-color .15s,transform .15s,box-shadow .15s}.button:hover:not(:disabled),button:hover:not(:disabled){border-color:#5b7898;background:#29394c;transform:translateY(-1px)}.button:active:not(:disabled),button:active:not(:disabled){transform:translateY(0)}input{min-height:40px;padding:8px 10px;border:1px solid #303f50;background:#0a121b;color:var(--text);font:inherit}input::placeholder{color:#687c90}label{font-weight:600}label.muted,.muted label{font-weight:400}
.button.primary{background:linear-gradient(135deg,#247fdd,#326de0);border-color:#559bff;box-shadow:0 6px 17px #1f6feb38}.button.success{background:linear-gradient(135deg,#238636,#2e9f46);border-color:#43b95b}.button.warn{background:#79550c;border-color:#a97b1c}.button.danger{background:#782226;border-color:#ad4146}.button.quiet{background:transparent}.button.compact{min-height:34px;padding:6px 10px}
.folder-state{margin-top:12px;padding:10px 11px;border-radius:10px;background:#0e1823;border:1px solid #26384c}.folder-state strong{display:block;overflow-wrap:anywhere}.folder-actions{display:grid;grid-template-columns:1fr auto;gap:8px}.folder-actions select{width:100%}
.stats{grid-template-columns:repeat(6,minmax(76px,1fr));gap:9px}.stat{padding:11px 12px;background:#0f1924;border-color:#26394d}.stat b{font-size:1.2rem}.stat .muted{font-size:.76rem}.stat.problem b{color:#ffaaa5}.stat.active b{color:#84b8ff}
.control-groups{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.primary-controls,.queue-controls{display:flex;gap:8px;flex-wrap:wrap}.advanced-actions{margin-top:11px;padding-top:10px;border-top:1px solid #26384d}.advanced-actions summary{font-size:.86rem;color:#9eafc1}.advanced-actions .row{margin-top:9px}
.wide{grid-column:1/-1}.episodes{max-height:540px;gap:8px}.episode{grid-template-columns:24px 52px minmax(180px,1fr) auto auto minmax(110px,150px);padding:10px 11px;background:#0f1924;border-color:#26394d}.episode:hover{border-color:#456786;background:#132235}.episode:has(input:checked){border-color:#3977b9;background:#142a43}.epname{font-size:.92rem}.episode .badge{white-space:nowrap}
.episodes-panel{border-color:#304e6b;background:linear-gradient(145deg,#14263a,#101a25)}.episodes-toolbar{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.episodes-toolbar h2{margin-bottom:2px}.episodes-actions{display:flex;justify-content:flex-end;gap:7px;flex-wrap:wrap}.language-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px;padding:10px 12px;border-radius:10px;background:#0e1824;border:1px solid #263a50}
#episodeSearch{min-width:180px;width:min(260px,100%);padding:8px 10px;border:1px solid var(--line);background:#0a121b;color:var(--text)}#selectionSummary{font-size:.8rem;color:#80b8ff;white-space:nowrap}.empty-state{padding:28px 16px;text-align:center;border:1px dashed #33485e;border-radius:11px;color:#91a1b2;background:#0d151f}.empty-state b{display:block;color:#dfe9f3;margin-bottom:4px}
.progress-card{min-height:220px}.progress{height:12px;margin-top:13px;background:#09111a;border:1px solid #26384d}.progress i{background:linear-gradient(90deg,#3987ff,#38c172)}.history{max-height:430px}.jobline{padding:10px 0;border-color:#253748}.note{background:#1c2b3b;border-left-color:#d29922}.section-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:13px}.section-heading h2{margin-bottom:3px}.section-shell{max-width:1220px;margin:0 auto}.secondary-panel{min-height:480px}
details summary{cursor:pointer}details summary:hover{color:#79c0ff}dialog{color-scheme:dark}dialog::backdrop{background:#03070bcc;backdrop-filter:blur(3px)}.dialog-card{padding:20px;overflow:auto;max-height:88vh}.form-grid{display:grid;gap:10px}.form-field{display:grid;gap:5px}.form-help{font-size:.79rem;color:#8fa1b3}
.toast-region{position:fixed;z-index:30;right:18px;bottom:18px;display:grid;gap:8px;width:min(390px,calc(100vw - 36px));pointer-events:none}.toast{padding:12px 14px;border:1px solid #38526e;border-radius:11px;background:#132132f5;box-shadow:0 14px 34px #0007;color:#edf5fd;animation:toast-in .18s ease-out}.toast.ok{border-color:#317947}.toast.fail{border-color:#994047}.toast.warn{border-color:#916c26}@keyframes toast-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.app-footer{margin-top:18px;text-align:center;color:#6f8295;font-size:.78rem}.app-footer strong{color:#8fa4b8}
@media(max-width:1080px){main{padding:18px 16px 44px}.grid{grid-template-columns:minmax(230px,280px) minmax(0,1fr)}.stats{grid-template-columns:repeat(3,minmax(80px,1fr))}.episode{grid-template-columns:24px 48px minmax(140px,1fr) auto auto}.episode select{grid-column:3/-1;justify-self:end}}
@media(max-width:820px){.top{align-items:flex-start}.brand-joke{display:none}.nav-panel{position:static}.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.episode{grid-template-columns:24px 48px minmax(0,1fr) auto}.episode select{grid-column:3/-1;justify-self:stretch}.episode .badge{justify-self:start}.episodes-toolbar{display:block}.episodes-actions{justify-content:flex-start;margin-top:10px}#episodeSearch{flex:1 1 180px}.workflow-safety{display:none}.section-heading{display:block}.section-heading .button{margin-top:8px}}
@media(max-width:560px){main{padding:10px 9px 30px}.top{padding:14px}.top h1{font-size:1.35rem}.brand-logo{width:50px;height:50px}.chips{justify-content:flex-start;margin-top:11px}.chips .chip{display:none}.chips #serviceChip{display:inline-flex}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.episode{grid-template-columns:24px 42px minmax(0,1fr);gap:7px}.episode .badge,.episode select{grid-column:3/-1}.actions .button,.record-actions .button{flex:1 1 130px}.panel{padding:13px}.workflow-strip{align-items:flex-start}.workflow-hint{white-space:normal}.control-groups{display:block}.queue-controls{margin-top:8px}.folder-actions{grid-template-columns:1fr}.toast-region{right:9px;bottom:9px;width:calc(100vw - 18px)}}
</style></head>
<body><main>
<header class="top"><div class="brand"><img class="brand-logo" src="/transass-logo.png?v=1" alt="TransASS" width="58" height="58"><div><h1>Transass</h1><div class="muted">Central de tradução de legendas</div><div class="brand-joke">Troca o idioma. O nome continua questionável.</div></div></div><div class="chips"><span class="chip" id="pipelineChip">Pipeline…</span><span class="chip" id="modelChip">Modelo…</span><span class="chip" id="motorChip" title="Motor de tradução configurado">Motor…</span><span class="chip online" id="serviceChip">Serviço…</span><button class="button compact" id="openTransportConfig">⚙ Configurar motor</button></div></header>
<nav class="workspace-nav" aria-label="Áreas do Transass"><button class="nav-button active" data-view-button="translate" aria-current="page">▶ Traduzir <span class="nav-count" id="navQueueCount"></span></button><button class="nav-button" data-view-button="library">▣ Acervo e revisão</button><button class="nav-button" data-view-button="memory">✦ Memória aprovada</button><button class="nav-button" data-view-button="diagnostics">⌨ Diagnóstico</button></nav>
<div id="toastRegion" class="toast-region" aria-live="polite" aria-atomic="true"></div>
<section class="view" data-view-panel="translate">
<div class="workflow-strip"><div class="workflow-copy"><span class="workflow-step" id="workflowStep">1</span><div><b id="workflowTitle">Escolha uma temporada</b><div class="muted workflow-hint" id="workflowHint">Navegue até uma pasta com vídeos para começar.</div></div></div><div class="workflow-safety">✓ Sem sobrescrever PT-BR existente</div></div>
<div class="grid">
<section class="panel nav-panel"><div class="panel-kicker">Passo 1</div><h2>Origem</h2><p class="panel-subtitle">Encontre a temporada que vai ganhar uma bunda… digo, uma legenda nova.</p><div id="crumb" class="crumb" aria-label="Caminho atual"></div><div class="folder-actions actions"><select id="folderSelect" aria-label="Subpastas"></select><button class="button" id="enterBtn">Abrir pasta</button></div><div class="row actions"><button class="button quiet" id="upBtn">← Voltar</button><button class="button primary" id="useBtn">Carregar esta temporada</button></div><div class="folder-state"><span class="muted">Temporada ativa</span><strong id="selectedFolderLabel">Nenhuma carregada</strong></div><div id="libraryNote" class="note hidden"></div></section>
<section class="panel"><div class="panel-kicker">Passo 3</div><h2>Fila de tradução</h2><p class="panel-subtitle">Resumo honesto: se algo der ruim, a fila conta. Sem retry ninja.</p><div class="stats"><div class="stat"><b id="doneCount">0/0</b><span class="muted">concluídos</span></div><div class="stat active"><b id="waitCount">0</b><span class="muted">na fila</span></div><div class="stat active"><b id="runCount">0</b><span class="muted">em execução</span></div><div class="stat problem"><b id="failCount">0</b><span class="muted">falhas</span></div><div class="stat"><b id="skipCount">0</b><span class="muted">ignorados</span></div><div class="stat"><b id="notStartedAfterFailureCount">0</b><span class="muted">bloqueados pela falha</span></div></div><div class="control-groups actions"><div class="primary-controls"><button class="button primary" id="startBtn" disabled>Selecione episódios</button><button class="button" id="retryBtn">Reprocessar falhos</button></div><div class="queue-controls"><button class="button warn" id="pauseBtn">Pausar</button><button class="button" id="resumeBtn">Continuar</button><button class="button danger" id="stopBtn">Parar fila</button></div></div><details class="advanced-actions"><summary>Ações avançadas e de manutenção</summary><div class="row"><label title="Planejamento sem tradução nem publicação"><input type="checkbox" id="dryrun"> Simulação (zero tradução/publicação)</label><button class="button" id="auditSeasonBtn">Auditar temporada</button><button class="button warn" id="retranslateSeasonBtn">Retraduzir temporada inteira</button></div></details></section>
<section class="panel wide episodes-panel"><div class="panel-kicker">Passo 2</div><div class="episodes-toolbar"><div><h2>Escolha os episódios</h2><div class="panel-subtitle">A seleção fica aqui; a ansiedade pode esperar na fila.</div></div><div class="episodes-actions"><input id="episodeSearch" type="search" placeholder="Filtrar episódios…" aria-label="Filtrar episódios"><span id="selectionSummary" aria-live="polite">0 selecionados</span><button class="button compact" id="selectMissing">Sem PT-BR</button><button class="button compact" id="selectLegacy">Legadas</button><button class="button compact quiet" id="clearSelection">Limpar</button><button class="button compact warn" id="retranslateSelectedBtn" disabled>Retraduzir</button></div></div><div class="language-bar"><label for="seasonLang"><b>Idioma da fonte</b></label><select id="seasonLang" title="Aplica a todos os episódios da pasta"><option value="inglês">inglês</option></select><button class="button compact" id="detectSeasonLang" title="Detecta os idiomas disponíveis na temporada">Detectar automaticamente</button><span class="muted">Pode ser ajustado por episódio na lista.</span></div><div id="episodes" class="episodes"><div class="empty-state"><b>Nenhuma temporada carregada</b>Escolha uma pasta ao lado e clique em “Carregar esta temporada”.</div></div></section>
<section class="panel progress-card"><div class="section-heading"><div><div class="panel-kicker">Agora</div><h2>Progresso atual</h2></div></div><div id="currentTitle" class="muted">Nenhum episódio em execução.</div><div class="progress" aria-label="Progresso por unidade"><i id="progressBar"></i></div><div id="currentMeta" class="muted" style="margin-top:8px"></div><div id="currentTelemetry" class="muted job-progress-meta" style="margin-top:6px"></div><div id="queueList" style="margin-top:10px"></div></section>
<section class="panel"><div class="section-heading"><div><div class="panel-kicker">Recentes</div><h2>Histórico</h2></div><label class="muted"><input type="checkbox" id="showTechnical"> incluir jobs técnicos</label></div><div id="history" class="history muted">Nenhuma sessão registrada.</div></section>
</div></section>
<section class="view section-shell" data-view-panel="library" hidden><section class="panel secondary-panel"><div class="section-heading"><div><div class="panel-kicker">Biblioteca persistente</div><h2>Acervo e versões</h2><div class="muted">Compare, revise e publique sem apagar o que veio antes.</div></div><span class="chip" id="libraryStats">Carregando acervo…</span></div><div id="archiveSeries" class="history muted">Nenhuma série anime catalogada.</div><div id="archiveDetails" class="history" style="margin-top:12px"></div></section></section>
<section class="view section-shell" data-view-panel="memory" hidden><section class="panel secondary-panel"><div class="section-heading"><div><div class="panel-kicker">Conhecimento controlado</div><h2>Memória aprovada</h2><div class="muted" id="memoryStats">Somente correções humanas aprovadas entram aqui. O robô não vota na própria prova.</div></div><button class="button" id="memorySync">Sincronizar aprovações</button></div><div id="memoryItems" class="history">Nenhuma memória ativa.</div></section></section>
<section class="view section-shell" data-view-panel="diagnostics" hidden><section class="panel secondary-panel"><div class="section-heading"><div><div class="panel-kicker">Transparência operacional</div><h2>Diagnóstico e logs</h2><div class="muted">O lugar onde “deu ruim” vira evidência reproduzível.</div></div><button class="button compact" id="clearVisualLogs">Limpar visualização</button></div><div id="logs" class="log"></div></section></section>
<dialog id="versionDetailsDialog" style="width:min(720px,calc(100vw - 24px));max-width:720px;max-height:88vh;padding:0;border:1px solid #34495e;border-radius:16px;background:#121b26;color:#e6edf3;box-shadow:0 24px 80px #0008"><div class="dialog-card"><div class="section-heading"><div><div class="panel-kicker">Biblioteca</div><h2 id="versionDetailsTitle">Detalhes da versão</h2><div id="versionDetailsSubtitle" class="muted"></div></div><button class="button compact" id="closeVersionDetails" aria-label="Fechar detalhes">Fechar</button></div><div id="versionDetailsBody"><span class="muted">Carregando…</span></div></div></dialog>
<dialog id="transportConfigDialog" style="width:min(620px,calc(100vw - 24px));max-width:620px;max-height:90vh;padding:0;border:1px solid #34495e;border-radius:16px;background:#121b26;color:#e6edf3;box-shadow:0 24px 80px #0008"><div class="dialog-card"><div class="section-heading"><div><div class="panel-kicker">Configuração</div><h2>Motor de tradução</h2><div class="muted">Um principal, um plano B e zero key passeando pelo navegador.</div></div><button class="button compact" id="closeTransportConfig">Fechar</button></div><div class="form-grid"><div class="form-field"><label for="tcSourceLanguage">Idioma padrão da legenda fonte</label><input id="tcSourceLanguage" placeholder="ex.: inglês, espanhol, japonês"><div class="form-help">O destino é sempre português do Brasil.</div></div><div class="form-field"><label for="tcPrimaryProvider">Motor principal</label><select id="tcPrimaryProvider"><option value="ollama">Ollama · local/GPU</option><option value="openai_compat">OpenAI-compatível · Groq, OpenRouter, LM Studio</option><option value="gemini">Gemini · Google</option><option value="nvidia">NVIDIA NIM · build.nvidia.com</option></select><input id="tcPrimaryModel" placeholder="Modelo, ex.: qwen3.5:9b"><input id="tcPrimaryBaseUrl" placeholder="Base URL · somente para OpenAI-compatível"></div><div class="form-field"><label for="tcFallbackProvider">Fallback opcional</label><select id="tcFallbackProvider"><option value="">— sem fallback —</option><option value="ollama">Ollama</option><option value="openai_compat">OpenAI-compatível</option><option value="gemini">Gemini</option><option value="nvidia">NVIDIA NIM</option></select><input id="tcFallbackModel" placeholder="Modelo do fallback"><input id="tcFallbackBaseUrl" placeholder="Base URL · somente para OpenAI-compatível"><div class="form-help">O fallback só entra quando o principal falha e deixa evidência própria.</div></div><div id="tcKeys" class="form-grid"></div><div class="note">As keys ficam somente no servidor, em arquivo local com permissão 600. Campo vazio mantém a key atual.</div><div class="row" style="justify-content:flex-end"><button class="button primary" id="tcSave">Salvar configuração</button></div><div id="tcStatus" class="muted" role="status"></div></div></div></dialog>
<footer class="app-footer"><strong>Transass</strong> · durabilidade forense para arquivos cujo nome já começa com <i>ass</i>.</footer>
</main>
<script>
const $=id=>document.getElementById(id);let path="",selectedFolder="",selectionFolder="",cursor=0,episodes=[],statusData=null,selectedEpisodeKeys=new Set(),refreshInFlight=false,renderedLogIds=new Set(),activeView="translate",lastEpisodesRefreshAt=0;let globalSourceLang="inglês";let seasonLangValue="inglês";const episodeSourceLang={};const langSelects={};
async function api(url,opt){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.error||`HTTP ${r.status}`);return d}
function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML}
function notify(message,tone='info',timeout=4800){const region=$('toastRegion');if(!region)return;const toast=document.createElement('div');toast.className=`toast ${tone}`;toast.textContent=message;region.append(toast);setTimeout(()=>toast.remove(),timeout)}
function setView(name,{remember=true}={}){const target=document.querySelector(`[data-view-panel="${name}"]`);if(!target)return;activeView=name;document.querySelectorAll('[data-view-panel]').forEach(panel=>panel.hidden=panel!==target);document.querySelectorAll('[data-view-button]').forEach(button=>{const current=button.dataset.viewButton===name;button.classList.toggle('active',current);if(current)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current')});if(remember){try{localStorage.setItem('transass.activeView',name)}catch(e){}}if(name==='library')loadArchive();if(name==='memory')loadMemory()}
function episodeKey(ep){return ep.library_episode_id?`id:${ep.library_episode_id}`:`source:${ep.source}`}
function selected(){return episodes.filter(ep=>selectedEpisodeKeys.has(episodeKey(ep))).map(ep=>ep.source)}
function selectedEpisodeIds(){return episodes.filter(ep=>selectedEpisodeKeys.has(episodeKey(ep))).map(ep=>ep.library_episode_id).filter(Boolean).map(Number)}
function syncSelectionUi(){const count=selectedEpisodeKeys.size,busy=Boolean(statusData&&(statusData.running||(statusData.queue||{}).waiting));const summary=$('selectionSummary');if(summary){const query=($('episodeSearch')?.value||'').trim().toLowerCase(),visible=episodes.filter(ep=>!query||[ep.episode,ep.name,ep.status,ep.audit_status].some(value=>String(value||'').toLowerCase().includes(query))).length;summary.textContent=`${count} selecionado${count===1?'':'s'} · ${visible}/${episodes.length} exibidos`}$('startBtn').textContent=count?`Traduzir ${count} episódio${count===1?'':'s'}`:'Selecione episódios';$('startBtn').disabled=busy||count===0;$('retranslateSelectedBtn').disabled=busy||selectedEpisodeIds().length===0;$('clearSelection').disabled=count===0;if($('navQueueCount')){const q=statusData?.queue||{},pending=(q.waiting||0)+(q.running||0);$('navQueueCount').textContent=pending?String(pending):''}if(statusData?.running){$('workflowStep').textContent='3';$('workflowTitle').textContent='Tradução em andamento';$('workflowHint').textContent='Acompanhe o episódio atual. Se der ruim, o log não vai fingir demência.'}else if(!selectedFolder){$('workflowStep').textContent='1';$('workflowTitle').textContent='Escolha uma temporada';$('workflowHint').textContent='Navegue até uma pasta com vídeos e carregue a temporada.'}else if(!count){$('workflowStep').textContent='2';$('workflowTitle').textContent='Selecione os episódios';$('workflowHint').textContent='Use “Sem PT-BR” para pegar somente o que ainda precisa de tradução.'}else{$('workflowStep').textContent='3';$('workflowTitle').textContent='Pronto para traduzir';$('workflowHint').textContent=`${count} episódio${count===1?'':'s'} com fonte ${seasonLangValue||globalSourceLang} → português do Brasil.`}}
function bindSelection(){document.querySelectorAll('#episodes input[type=checkbox]').forEach(input=>{input.onchange=()=>{const key=input.dataset.selectionKey;if(input.checked)selectedEpisodeKeys.add(key);else selectedEpisodeKeys.delete(key);syncSelectionUi()}});document.querySelectorAll('#episodes .srclang').forEach(sel=>{const key=sel.dataset.key;langSelects[key]=sel;sel.onfocus=()=>populateLangSelect(sel);sel.onchange=()=>{episodeSourceLang[key]=sel.value;refreshSourceStatus(key,sel.dataset.epid,sel.value)}});syncSelectionUi()}
async function loadBrowse(){try{const d=await api('/browse?path='+encodeURIComponent(path));$('crumb').innerHTML=path?path.split('/').map((x,i)=>`<button data-p="${esc(path.split('/').slice(0,i+1).join('/'))}">${esc(x)}</button>`).join(' › '):'<span class="muted">Shows</span>';$('crumb').querySelectorAll('button').forEach(b=>b.onclick=()=>{path=b.dataset.p;loadBrowse()});const sel=$('folderSelect');sel.replaceChildren(...d.subfolders.map(folder=>{const option=document.createElement('option');option.value=folder;option.textContent = folder;return option}));if(!d.subfolders.length){const option=document.createElement('option');option.textContent='Nenhuma subpasta';option.disabled=true;sel.append(option)}$('upBtn').disabled=!path;$('enterBtn').disabled=!d.subfolders.length;$('useBtn').disabled=!d.has_videos;$('useBtn').textContent=selectedFolder===path?'Temporada carregada':'Carregar esta temporada';$('libraryNote').classList.add('hidden')}catch(e){$('libraryNote').textContent='Biblioteca temporariamente indisponível.';$('libraryNote').classList.remove('hidden');console.error('browse',e)}}
let episodeRenderFingerprint='';
function badge(status){const cls=status==='COMPLETED'||status==='ALREADY_TRANSLATED'?'ok':status==='FAILED'?'fail':['WAITING','TRANSLATING','STARTING','VALIDATING','PUBLISHING','PAUSED'].includes(status)?'wait':'neutral';const label=status==='ALREADY_TRANSLATED'?'Já traduzido':status==='NOT_STARTED'?'Não iniciado':status;return `<span class="badge ${cls}">${esc(label)}</span>`}
function sourceBadge(ep){const s=ep.source_status||{};const status=s.status||'SOURCE_NOT_FOUND';const cls=s.available?'ok':status==='SOURCE_AMBIGUOUS'||status==='SOURCE_AVAILABLE_PGS_UNSUPPORTED'||status==='SOURCE_STATUS_ERROR'?'wait':'neutral';const text=s.display||(status==='SOURCE_AVAILABLE_LIBRARY'?'✓ Biblioteca':status==='SOURCE_AVAILABLE_SIDECAR'?'✓ Sidecar':status==='SOURCE_AVAILABLE_INTERNAL_TEXT'?'✓ Track interna':status==='SOURCE_AVAILABLE_PGS_UNSUPPORTED'?'⚠ PGS — OCR não suportado':status==='SOURCE_AMBIGUOUS'?'⚠ Fonte ambígua':status==='SOURCE_STATUS_ERROR'?'⚠ Metadata da fonte':'✕ Fonte não encontrada');return `<span class="badge ${cls}" title="${esc(s.reason||s.display||text)}">${text}</span>`}
async function refreshSourceStatus(key, episodeId, lang){if(!episodeId)return;try{const d=await api('/source-status?episode_id='+encodeURIComponent(episodeId)+'&source_language='+encodeURIComponent(lang));const el=document.querySelector('[data-source-badge="'+key+'"]');if(el)el.innerHTML=sourceBadge(d)}catch(e){}}
function renderEpisodes(){const box=$('episodes');const query=($('episodeSearch')?.value||'').trim().toLowerCase();if(!episodes.length){box.innerHTML='<div class="empty-state"><b>Nenhum vídeo encontrado</b>Esta pasta não parece ser uma temporada. Ou os episódios estão muito bem escondidos.</div>';syncSelectionUi();return}const visible=episodes.filter(ep=>!query||[ep.episode,ep.name,ep.status,ep.audit_status].some(value=>String(value||'').toLowerCase().includes(query)));if(!visible.length){box.innerHTML='<div class="empty-state"><b>Nada por aqui</b>Nenhum episódio corresponde ao filtro atual.</div>';syncSelectionUi();return}box.replaceChildren(...visible.map(ep=>{const row=document.createElement('label');row.className='episode';const key=episodeKey(ep),disabled=['WAITING','TRANSLATING','STARTING','VALIDATING','PUBLISHING'].includes(ep.status);const lang=episodeSourceLang[key]||seasonLangValue||globalSourceLang;const sel=`<select class="srclang" data-key="${esc(key)}" data-epid="${esc(ep.library_episode_id||'')}" data-path="${esc(ep.source||'')}" title="Idioma de origem da legenda" aria-label="Idioma de origem de ${esc(ep.name)}" style="min-height:30px;max-width:150px;padding:4px 6px"><option value="${esc(lang)}">${esc(lang)}</option></select>`;row.innerHTML=`<input type="checkbox" data-selection-key="${esc(key)}" data-source="${esc(ep.source)}" data-episode-id="${esc(ep.library_episode_id||'')}" aria-label="Selecionar ${esc(ep.name)}" ${selectedEpisodeKeys.has(key)?'checked':''} ${disabled?'disabled':''}><b>${esc(ep.episode||'—')}</b><span class="epname" title="${esc(ep.name)}">${esc(ep.name)}</span>${badge(ep.status)}${auditBadge(ep)}<span data-source-badge="${esc(key)}">${sourceBadge(ep)}</span>${sel}`;return row}));bindSelection()}
async function populateLangSelect(sel){const epid=sel.dataset.epid;const rel=sel.dataset.path;if((!epid&&!rel)||sel.dataset.loaded)return;sel.dataset.loaded='1';try{const q=epid?('episode_id='+encodeURIComponent(epid)):('path='+encodeURIComponent(rel));const d=await api('/source-options?'+q);const opts=Array.from(new Set(d.options.map(o=>o.language).filter(Boolean)));if(!opts.length)return;const cur=sel.value;sel.replaceChildren(...opts.map(l=>{const option=document.createElement('option');option.value=l;option.textContent=l;option.selected=l===cur;return option}))}catch(e){console.error('source-options',e)}}
function applySeasonLang(lang){seasonLangValue=lang;episodes.forEach(ep=>{const key=episodeKey(ep);episodeSourceLang[key]=lang;const el=langSelects[key];if(el)el.value=lang});loadEpisodes()}
async function detectSeasonLang(){const ep=episodes.find(e=>e.source)||episodes[0];if(!ep||!ep.source)return notify('Carregue uma temporada antes de detectar o idioma.','warn');try{const d=await api('/source-options?path='+encodeURIComponent(ep.source));const opts=Array.from(new Set(d.options.map(o=>o.language).filter(Boolean)));if(!opts.length)return notify('Nenhum idioma textual foi detectado nesta temporada.','warn');const cur=$('seasonLang').value;const sel=$('seasonLang');sel.replaceChildren(...opts.map(l=>{const option=document.createElement('option');option.value=l;option.textContent=l;option.selected=l===cur;return option}));const chosen=opts.includes(cur)?cur:opts[0];applySeasonLang(chosen);notify(`Fonte detectada: ${chosen}.`,'ok')}catch(e){notify('Não foi possível detectar os idiomas: '+e.message,'fail')}}
async function start(){try{const sel=episodes.filter(ep=>selected().includes(ep.source)&&ep.status!=='ALREADY_TRANSLATED');if(!sel.length)return notify('Selecione pelo menos um episódio sem PT-BR.','warn');const source_languages={};sel.forEach(ep=>{const key=episodeKey(ep);const el=langSelects[key];source_languages[ep.source]=el?el.value:globalSourceLang});await api('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:selectedFolder,episodes:sel.map(ep=>ep.source),source_languages,dry_run:$('dryrun').checked})});notify($('dryrun').checked?'Simulação enfileirada. Nenhum modelo será chamado.':`${sel.length} episódio${sel.length===1?'':'s'} enviado${sel.length===1?'':'s'} para a fila.`,'ok');await refresh(true)}catch(e){notify(e.message,'fail')}}
async function auditSelectedSeason(){try{const series=await seriesForFolder();if(!series)return notify('A temporada atual ainda não está catalogada como ANIME.','warn');const m=selectedFolder.match(/(?:^|\/)Season\s*([0-9]+)/i);const body=m?{season:m[1]}:{};const d=await api('/audit/series/'+series.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});notify(`Auditoria: ${d.counts['PROBLEMAS DETECTADOS']||0} com problemas · ${d.counts['REVISÃO RECOMENDADA']||0} para revisar · ${d.counts['AUDITORIA PARCIAL']||0} parciais.`,'ok',7000);await loadEpisodes();await loadArchive()}catch(e){notify(e.message,'fail')}}
async function retranslate(ids,confirmBatch=false){try{if(!ids.length)return notify('Selecione episódios com fonte original arquivada.','warn');const langs={};episodes.forEach(ep=>{const id=Number(ep.library_episode_id);if(ids.includes(id)){const key=episodeKey(ep);langs[id]=episodeSourceLang[key]||seasonLangValue||globalSourceLang}});const langBody=JSON.stringify({episode_ids:ids,source_languages:langs,process_eligible_only:!confirmBatch});if(confirmBatch){const preview=await api('/retranslate/preflight',{method:'POST',headers:{'Content-Type':'application/json'},body:langBody});const c=preview.counts||{};if(c.blocked){return notify(`Pré-flight bloqueado: ${c.blocked} episódio(s) sem fonte compatível. Nenhum job foi criado.`,'warn')}if(!confirm(`Retraduzir ${c.eligible||0} episódio(s) com ${c.skipped_current_validated||0} já atual(is) ignorado(s)? A regra é parar na primeira falha. A versão antiga será preservada e nada será publicado automaticamente.`))return;}const queued=await api('/retranslate',{method:'POST',headers:{'Content-Type':'application/json'},body:langBody});const skipped=queued.not_eligible||queued.preflight?.counts?.blocked||0;if(skipped&&queued.queued){notify(`${queued.queued} episódio(s) enfileirado(s); ${skipped} seleção(ões) incompatível(is) foram ignoradas.`,'warn')}else notify(`${queued.queued||0} retradução(ões) enfileirada(s).`,'ok');await refresh(true)}catch(e){notify(e.message,'fail')}}
async function seriesForFolder(){const d=await api('/library/series?classification=ANIME');return d.series.find(s=>selectedFolder===s.library_relative_path||selectedFolder.startsWith(s.library_relative_path+'/'))}
async function autoClassifyFolder(folder){try{await api('/library/auto-classify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder})});await loadArchive()}catch(e){console.warn('auto-classify',e)}}
async function action(url){try{await api(url,{method:'POST'});await refresh(true)}catch(e){notify(e.message,'fail')}}
function age(iso){if(!iso)return '—';const t=Date.parse(iso);if(!Number.isFinite(t))return esc(iso);const sec=Math.max(0,Math.floor((Date.now()-t)/1000));if(sec<60)return `há ${sec}s`;const min=Math.floor(sec/60);if(min<60)return `há ${min}min`;return `há ${Math.floor(min/60)}h ${min%60}min`}
function duration(sec){if(sec==null)return '—';sec=Math.max(0,Math.round(Number(sec)));const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`}
function renderStatus(d){
  statusData=d;
  const q=d.queue||{};
  $('doneCount').textContent=`${q.completed||0}/${q.total||0}`;
  $('waitCount').textContent=q.waiting||0;
  $('runCount').textContent=q.running||0;
  $('failCount').textContent=q.failed||0;
  $('skipCount').textContent=q.skipped||0;
  $('notStartedAfterFailureCount').textContent=q.not_started_after_failure||0;
  const cur=d.current_job,t=cur||{},stage=t.stage==='SEMANTIC_RECONSTRUCTION'?'RECONSTRUÇÃO SEMÂNTICA':(t.stage||t.status);
  $('currentTitle').textContent=cur?`${cur.name} · ${stage}`:'Nenhum episódio em execução.';
  const total=t.total_units,resolved=t.resolved_units??0,pct=total?Math.min(100,Math.round(100*resolved/total)):0;
  $('progressBar').style.width=pct+'%';
  const semantic=t.semantic_calls??0;
  const semanticText=semantic?` · reconstrução semântica: ${t.semantic_completed??0} concluída(s)${t.semantic_in_progress?` + ${t.semantic_in_progress} em andamento`:''}${t.semantic_incomplete?` · ${t.semantic_incomplete} incompleta(s)`:''}`:'';
  const progressText=total!=null?`Unidades base: ${resolved}/${total}`:'PREPARANDO — total ainda não calculado';
  $('currentMeta').textContent=cur?`${progressText}${semanticText}${t.current_event_id!=null?` · evento atual ${esc(t.current_event_id)}`:''}`:'';
  const details=cur?[`<span>Chamadas base: <b>${t.calls??0}</b></span>`,semantic?`<span>Chamadas semânticas: <b>${semantic}</b></span>`:'',`<span>Retries: <b>${t.retries??0}</b></span>`,t.retry_budget_total!=null?`<span>Budget: <b>${t.retry_budget_used??0}/${t.retry_budget_total}</b></span>`:'',`<span>Tempo: <b>${duration(t.elapsed_seconds)}</b></span>`,`<span>Última atividade: <b>${age(t.last_activity_at)}</b></span>`].filter(Boolean).join(' · '):'';
  $('currentTelemetry').innerHTML=details;
  if(cur&&cur.status==='FAILED'){const event=cur.current_event_id!=null?` · evento/unidade ${esc(cur.current_event_id)}`:'';$('currentTelemetry').innerHTML+=`<div class="note" style="margin-top:6px">Falha: ${esc(cur.reason||cur.error||'resultado reprovado')}${event}</div>`}
  const interrupted=d.bulk_stop_reason==='STOPPED_ON_FAILURE'?'<div class="note">Temporada interrompida após a primeira falha. Os episódios restantes não foram iniciados.</div>':'';
  $('queueList').innerHTML=interrupted+(d.jobs||[]).filter(j=>['WAITING','FAILED','COMPLETED','SKIPPED_CURRENT_VALIDATED','NOT_STARTED_AFTER_FAILURE'].includes(j.status)).map(j=>{const candidate=j.candidate_download_url?`<a class="button compact" href="${esc(j.candidate_download_url)}">Baixar candidato</a>`:'';return `<div class="jobline"><span>${esc(j.episode||j.name)}</span><span>${badge(j.status)} ${candidate}</span></div>`}).join('');
  $('pauseBtn').disabled=!d.running||d.pause_requested;
  $('resumeBtn').disabled=!d.queue_paused;
  $('stopBtn').disabled=!d.running&&!q.waiting;
  $('retryBtn').disabled=!!d.running||!(q.failed);
  syncSelectionUi();
}
async function refresh(forceEpisodes=false){if(refreshInFlight)return;refreshInFlight=true;try{try{const d=await api('/status?after='+cursor);cursor=d.last_log_id||cursor;renderStatus(d);(d.log_details||d.log||[]).forEach(x=>{if(x.id!=null&&renderedLogIds.has(x.id))return;if(x.id!=null)renderedLogIds.add(x.id);const line=document.createElement('div');line.className=x.level||'';line.dataset.logId=x.id??'';line.textContent=`${x.time||''} ${x.line}`;$('logs').append(line)});$('logs').scrollTop=$('logs').scrollHeight}catch(e){console.error('status',e)}if(selectedFolder&&(forceEpisodes||Date.now()-lastEpisodesRefreshAt>=10000)){try{await loadEpisodes()}catch(e){$('libraryNote').textContent='Episódios temporariamente indisponíveis.';$('libraryNote').classList.remove('hidden');console.error('episodes',e)}}}finally{refreshInFlight=false}}
async function loadHealth(){try{const d=await api('/health');$('serviceChip').textContent=d.status==='ok'?'Serviço online':'Serviço indisponível';$('serviceChip').classList.toggle('online',d.status==='ok');$('serviceChip').classList.toggle('offline',d.status!=='ok')}catch(e){$('serviceChip').textContent='Serviço indisponível';$('serviceChip').classList.remove('online');$('serviceChip').classList.add('offline')}}
async function loadPipeline(){try{const d=await api('/pipeline');$('pipelineChip').textContent=d.pipeline_label;$('modelChip').textContent=d.model}catch(e){$('pipelineChip').textContent='Pipeline indisponível';$('modelChip').textContent='Modelo indisponível'}await loadHealth()}
async function loadTransportConfig(){try{const d=await api('/transport-config');const p=d.primary||{};const f=d.fallback||{};globalSourceLang=d.source_language||'inglês';if($('seasonLang'))$('seasonLang').value=globalSourceLang;$('motorChip').textContent=(p.provider||'?')+(f&&f.provider?` + ${f.provider}`:'')}catch(e){$('motorChip').textContent='Motor indisponível'}}
async function openTransportConfig(){try{const d=await api('/transport-config');const p=d.primary||{},f=d.fallback||{};$('tcSourceLanguage').value=d.source_language||'inglês';$('tcPrimaryProvider').value=p.provider||'ollama';$('tcPrimaryModel').value=p.model||'';$('tcPrimaryBaseUrl').value=p.base_url||'';$('tcFallbackProvider').value=f?f.provider:'';$('tcFallbackModel').value=f?f.model:'';$('tcFallbackBaseUrl').value=f?f.base_url||'':'';const kc=d.keys_configured||{};let html='';for(const prov of ['ollama','openai_compat','gemini','nvidia']){if(prov==='ollama')continue;html+=`<label>Key ${prov}${kc[prov]?' <span class="badge">configurada</span>':''}</label><input id="tcKey_${prov}" type="password" placeholder="${kc[prov]?'deixe vazio para manter':'cole a API key'}" style="width:100%;margin:4px 0">`}$('tcKeys').innerHTML=html;$('tcStatus').textContent='';const dialog=$('transportConfigDialog');if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','')}catch(e){alert('Não foi possível carregar a configuração de motor.')}}
async function saveTransportConfig(){const keys={};for(const prov of ['openai_compat','gemini','nvidia']){const el=$('tcKey_'+prov);if(el&&el.value.trim())keys[prov]=el.value.trim()}const payload={primary:{provider:$('tcPrimaryProvider').value,model:$('tcPrimaryModel').value.trim(),base_url:$('tcPrimaryBaseUrl').value.trim()||null},fallback:null,keys,source_language:$('tcSourceLanguage').value.trim()||'inglês'};if($('tcFallbackProvider').value){payload.fallback={provider:$('tcFallbackProvider').value,model:$('tcFallbackModel').value.trim(),base_url:$('tcFallbackBaseUrl').value.trim()||null}}try{await api('/transport-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});$('tcStatus').textContent='Salvo ✓';$('transportConfigDialog').close?.();$('transportConfigDialog').removeAttribute('open');await loadTransportConfig();notify('Motor de tradução atualizado. A bunda agora sabe para onde ir.','ok')}catch(e){$('tcStatus').textContent='Erro: '+(e.message||e)}}
$('openTransportConfig').onclick=openTransportConfig;$('closeTransportConfig').onclick=()=>{$('transportConfigDialog').close?.();$('transportConfigDialog').removeAttribute('open')};$('tcSave').onclick=saveTransportConfig;
async function loadHistory(){try{const d=await api('/history?technical='+($('showTechnical').checked?'1':'0'));$('history').innerHTML=d.history.length?d.history.slice().reverse().map(x=>`<div class="jobline"><span>${esc(x.folder||'')}<br><small>${esc(x.finished_at||x.created_at||'')}</small></span><span>${x.completed||0} concluídos · ${x.failed||0} falhos</span></div>`).join(''):'Nenhuma sessão registrada.'}catch(e){}}
async function loadArchive(){try{const d=await api('/library');const c=d.counts||{};$('libraryStats').textContent=`${c.records||0} versões · ${c.objects||0} objetos · ${c.publications||0} publicados`;
const s=await api('/library/series?classification=ANIME');$('archiveSeries').innerHTML=s.series.length?s.series.map(x=>`<div class="jobline"><span><b>${esc(x.title)}</b><br><small>${esc(x.library_relative_path||'')} · ${esc(x.classification)}</small></span><button class="button" data-library-series="${x.id}">Abrir</button></div>`).join(''):'Nenhuma série anime catalogada.';$('archiveSeries').querySelectorAll('[data-library-series]').forEach(b=>b.onclick=()=>openArchiveSeries(b.dataset.librarySeries));}catch(e){$('archiveSeries').textContent='Biblioteca indisponível';}}
async function loadMemory(){try{const d=await api('/memory');const c=d.counts||{};$('memoryStats').textContent=`${c.active||0} ativas · ${c.items||0} históricas · ${c.conflicts||0} conflitos · ${c.usages||0} usos · somente SEGMENT_APPROVED`;$('memoryItems').innerHTML=(d.items||[]).map(x=>`<div class="jobline"><span><b>${esc(x.source)}</b> → ${esc(x.approved_text)}</span><span>${esc(x.anime_title||'Anime')} · ${esc(x.status)} · ${x.usage_count||0} usos <button class="button" data-memory-status="${x.id}" data-next-status="${x.status==='ACTIVE'?'INACTIVE':'ACTIVE'}">${x.status==='ACTIVE'?'Desativar':'Ativar'}</button></span></div>`).join('')||'Nenhuma memória materializada.';$('memoryItems').querySelectorAll('[data-memory-status]').forEach(b=>b.onclick=async()=>{try{await api('/memory/items/'+b.dataset.memoryStatus+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:b.dataset.nextStatus})});await loadMemory()}catch(e){alert(e.message)}})}catch(e){$('memoryStats').textContent='Memória indisponível';$('memoryItems').textContent='Não foi possível carregar a memória local.'}}
$('enterBtn').onclick=()=>{const v=$('folderSelect').value;if(v){path=path?path+'/'+v:v;loadBrowse()}};$('upBtn').onclick=()=>{path=path.split('/').slice(0,-1).join('/');loadBrowse()};$('useBtn').onclick=async()=>{const button=$('useBtn');button.disabled=true;button.textContent='Carregando…';try{if(selectionFolder!==path){selectedEpisodeKeys.clear();selectionFolder=path}selectedFolder=path;episodeRenderFingerprint='';$('selectedFolderLabel').textContent=path||'Shows';await loadEpisodes();autoClassifyFolder(path);notify(`Temporada carregada: ${path||'Shows'}.`,'ok')}catch(e){notify('Não foi possível carregar a temporada: '+e.message,'fail')}finally{button.disabled=false;button.textContent='Temporada carregada'}};$('selectMissing').onclick=()=>{episodes.forEach(ep=>{if(!ep.ptbr)selectedEpisodeKeys.add(episodeKey(ep))});renderEpisodes()};$('selectLegacy').onclick=async()=>{try{const s=await seriesForFolder();if(!s)return notify('Série ainda não catalogada como ANIME.','warn');const d=await api('/library/legacy?series_id='+s.id);episodes.forEach(ep=>{if(d.episode_ids.includes(ep.library_episode_id))selectedEpisodeKeys.add(episodeKey(ep))});renderEpisodes()}catch(e){notify(e.message,'fail')}};$('clearSelection').onclick=()=>{selectedEpisodeKeys.clear();renderEpisodes()};$('episodeSearch').oninput=renderEpisodes;$('seasonLang').onchange=()=>applySeasonLang($('seasonLang').value);$('detectSeasonLang').onclick=detectSeasonLang;$('startBtn').onclick=start;$('retranslateSelectedBtn').onclick=()=>retranslate(selectedEpisodeIds(),false);$('auditSeasonBtn').onclick=auditSelectedSeason;$('retranslateSeasonBtn').onclick=async()=>{try{const ids=episodes.map(ep=>ep.library_episode_id).filter(Boolean).map(Number);await retranslate(ids,true)}catch(e){notify(e.message,'fail')}};$('pauseBtn').onclick=()=>action('/pause');$('resumeBtn').onclick=()=>action('/resume');$('stopBtn').onclick=()=>{if(confirm('Parar a fila? O episódio atual terminará/cancelará com segurança.'))action('/stop')};$('retryBtn').onclick=()=>action('/retry-failed');$('showTechnical').onchange=loadHistory;$('memorySync').onclick=async()=>{try{await api('/memory/sync',{method:'POST'});await loadMemory();notify('Memória sincronizada com as correções aprovadas.','ok')}catch(e){notify(e.message,'fail')}};$('clearVisualLogs').onclick=()=>{$('logs').replaceChildren();renderedLogIds.clear();notify('Visualização limpa; o histórico persistente continua intacto.','ok')};document.querySelectorAll('[data-view-button]').forEach(button=>button.onclick=()=>setView(button.dataset.viewButton));document.addEventListener('keydown',event=>{if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)){event.preventDefault();setView('translate');$('episodeSearch').focus()}if(event.key==='Escape'){for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close?.()}});let initialView='translate';try{initialView=localStorage.getItem('transass.activeView')||'translate'}catch(e){}setView(initialView,{remember:false});loadPipeline();loadTransportConfig();loadBrowse();loadHistory();loadHealth();refresh();setInterval(()=>{refresh();loadHistory()},2000);setInterval(loadHealth,5000);setInterval(()=>{if(activeView==='library')loadArchive();if(activeView==='memory')loadMemory()},15000);

 // A primeira carga costumava enviar o idioma global (inglês) antes de
 // descobrir que a temporada só possui tracks francesas. A declaração abaixo
 // substitui a versão anterior por uma versão que autodetecta o idioma da
 // primeira fonte; uma escolha manual posterior continua sendo respeitada.
 async function loadEpisodes(){
  if(!selectedFolder){episodes=[];$('episodes').innerHTML='<div class="empty-state"><b>Nenhuma temporada carregada</b>Escolha uma pasta e clique em “Carregar esta temporada”.</div>';syncSelectionUi();return}
  if(selectionFolder!==selectedFolder){selectedEpisodeKeys.clear();selectionFolder=selectedFolder}
  const lang=seasonLangValue||globalSourceLang;
  const d=await api('/episodes?path='+encodeURIComponent(selectedFolder)+'&source_language='+encodeURIComponent(lang));
  episodes=d.episodes;
  lastEpisodesRefreshAt=Date.now();
  const detectedFolder=globalThis.__subtranslateDetectedSourceFolder||'';
  if(detectedFolder!==selectedFolder){
   globalThis.__subtranslateDetectedSourceFolder=selectedFolder;
   const first=episodes.find(ep=>ep.source);
   if(first){
    try{
     const options=await api('/source-options?path='+encodeURIComponent(first.source));
     const languages=Array.from(new Set((options.options||[]).map(o=>o.language).filter(Boolean)));
     const detected=languages.includes(lang)?lang:languages[0];
     if(detected&&detected!==lang){
      seasonLangValue=detected;
      const season=$('seasonLang');
      if(season&&!Array.from(season.options).some(o=>o.value===detected)){
       const option=document.createElement('option');option.value=detected;option.textContent=detected;season.append(option)
      }
      if(season)season.value=detected;
      return loadEpisodes();
     }
    }catch(e){console.warn('source autodetect',e)}
   }
  }
  const valid=new Set(episodes.map(episodeKey));
  selectedEpisodeKeys=new Set([...selectedEpisodeKeys].filter(key=>valid.has(key)));
  const fingerprint=JSON.stringify(episodes.map(ep=>[episodeKey(ep),ep.status,ep.audit_status,ep.ptbr,ep.source_status]));
  if(fingerprint!==episodeRenderFingerprint){episodeRenderFingerprint=fingerprint;renderEpisodes()}else syncSelectionUi()
 }

 let currentArchiveSeriesId=null;
function auditBadge(ep){const s=ep.audit_status||'NÃO AUDITADA';const cls=s==='SEM PROBLEMAS DETECTADOS'?'ok':s==='PROBLEMAS DETECTADOS'?'fail':['AUDITORIA PARCIAL','REVISÃO RECOMENDADA','VERSÕES SEPARADAS'].includes(s)?'wait':'neutral';const short=s==='SEM PROBLEMAS DETECTADOS'?'✓ sem problemas':s==='PROBLEMAS DETECTADOS'?'⚠ problemas':s==='AUDITORIA PARCIAL'?'◐ parcial':s==='REVISÃO RECOMENDADA'?'◐ revisão recomendada':s==='VERSÕES SEPARADAS'?'◐ estados por versão':'— não auditada';return `<span class="badge ${cls}" title="${esc(s)}">${short}</span>`}
function recordValidated(record){return ['VALIDATED','OK','PUBLISHED'].includes(String(record.validation_status||'').toUpperCase())}
function recordPublished(record){return record.published===true||record.publication_status==='PUBLISHED'||(record.publications||[]).some(item=>item.status==='PUBLISHED')}
function recordLabel(record){if(record.pipeline_version)return record.pipeline_version;const k=(record.source_kind||'').toUpperCase();if(k==='IMPORTED_EXISTING')return 'Versão legada';if(k==='EXTRACTED')return 'Fonte extraída';return 'Pipeline desconhecido'}
function recordAuditLabel(record){const status=record.audit_status||record.audit?.status,flags=record.audit?.flags||[];if(status==='REVISÃO RECOMENDADA'||(status==='PROBLEMAS DETECTADOS'&&flags.length&&flags.every(flag=>flag==='POSSIBLE_UNTRANSLATED_OUTPUT')))return '◐ possível resíduo · revisar';if(status==='PROBLEMAS DETECTADOS')return '⚠ problemas nesta versão';if(status==='SEM PROBLEMAS DETECTADOS')return '✓ sem problemas nesta versão';if(recordValidated(record))return '✓ validada';return status||'não auditada'}
function recordStateBadge(record){const published=recordPublished(record);if(published)return '<span class="badge ok">✓ PUBLICADA</span>';if(recordValidated(record))return '<span class="badge wait">NÃO PUBLICADA</span>';return '<span class="badge neutral">'+esc(record.validation_status||'não validada')+'</span>'}
function versionDetailsHtml(record){
 const lineage=record.lineage||[];
 const parent=lineage.find(item=>String(item.source_record_id)===String(record.id)&&item.parent_record_id!=null);
 const sourceText=parent?`English · record ${parent.parent_record_id}`:(record.source_language||'não informada');
 const published=recordPublished(record);
 const publication=record.publication||(record.publications||[]).find(item=>item.status==='PUBLISHED');
 const targetState=record.target_present?(record.target_record_id?`sidecar atual: record ${record.target_record_id}`:'sidecar atual: versão não catalogada'):'nenhum sidecar atual';
 const events=record.events_total!=null?`${esc(record.events_total)}/${esc(record.events_total)}`:'não informado';
 const validation=recordValidated(record);
 return `<div class="note"><b>${esc(record.series_title||'Anime')} · S${esc(record.season||'—')}E${esc(record.episode||'—')} — ${esc(record.episode_title||record.media_filename||'Episódio')}</b></div><div class="row" style="margin-top:10px;gap:6px"><span class="badge">${esc(recordLabel(record))}</span><span class="badge">${esc(record.language||'—')}</span><span class="badge">${esc(record.source_kind||'—')}</span>${recordStateBadge(record)}</div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;margin-top:12px"><div class="stat"><b>Estado</b><span>${validation?'VALIDADA':'NÃO VALIDADA'}</span></div><div class="stat"><b>Modelo</b><span>${esc(record.model||'não informado')}</span></div><div class="stat"><b>Fonte</b><span>${esc(sourceText)}</span></div><div class="stat"><b>Eventos</b><span>${events}</span></div></div><div class="panel" style="margin-top:12px;padding:10px"><b>Validação</b><div class="muted" style="margin-top:5px">${validation?'✓ Estrutura registrada · ✓ conteúdo registrado · ✓ sem críticos registrados':'Validação técnica pendente ou incompatível'}</div><div style="margin-top:8px"><b>Publicação</b><div class="muted">${published?'✓ Publicada no Jellyfin'+(publication?.published_at?` · ${esc(publication.published_at)}`:''):'NÃO PUBLICADA'}</div><div class="muted">Destino: ${esc(targetState)}</div></div></div><details style="margin-top:10px"><summary>Detalhes técnicos</summary><dl style="display:grid;grid-template-columns:minmax(120px,auto) 1fr;gap:4px 12px;overflow-wrap:anywhere"><dt>Record</dt><dd>${esc(record.id)}</dd><dt>Object</dt><dd>${esc(record.object_id)}</dd><dt>SHA-256</dt><dd>${esc(record.sha256||'—')}</dd><dt>Formato</dt><dd>${esc(record.format||'—')}</dd><dt>Source record</dt><dd>${esc(parent?.parent_record_id||'—')}</dd><dt>Audit</dt><dd>${esc(record.audit_status||'NÃO AUDITADA')}</dd>${publication?`<dt>Target</dt><dd>${esc(publication.target_relative_path||'—')}</dd>`:''}</dl></details><div class="record-actions" style="justify-content:flex-start;margin-top:14px">${validation&&!published?`<button class="button primary" data-modal-publish="${record.id}">Publicar no Jellyfin</button>`:''}<a class="button" href="/library/records/${record.id}/download">Baixar</a></div>`;
}
async function showVersionDetails(recordId){try{const record=await api('/library/records/'+recordId);$('versionDetailsTitle').textContent=`Detalhes · ${recordLabel(record)}`;$('versionDetailsSubtitle').textContent=`${record.language||'—'} · ${record.source_kind||'—'} · ${recordPublished(record)?'publicada':'não publicada'}`;$('versionDetailsBody').innerHTML=versionDetailsHtml(record);const dialog=$('versionDetailsDialog');if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');dialog.querySelectorAll('[data-modal-publish]').forEach(button=>button.onclick=()=>publishRecord(Number(button.dataset.modalPublish)))}catch(e){alert('Não foi possível carregar os detalhes desta versão.')}}
async function publishRecord(recordId){try{const record=await api('/library/records/'+recordId);if(!recordValidated(record)){alert('Somente uma versão validada pode ser publicada.');return}const episode=await api('/library/episodes/'+record.episode_id);const current=(episode.records||[]).find(item=>recordPublished(item))||(record.target_record_id?(episode.records||[]).find(item=>String(item.id)===String(record.target_record_id)):null);const currentLabel=current?`${recordLabel(current)} / record ${current.id}`:(record.target_present?'sidecar atual não publicado como registro':'nenhuma versão publicada');const newLabel=`${recordLabel(record)} / record ${record.id}`;const currentHash=record.target_sha256||current?.sha256;const sameHash=currentHash&&currentHash===record.sha256;if(sameHash){alert('Esta versão já está publicada com o mesmo hash. Nenhuma ação adicional foi necessária.');return}const different=Boolean(currentHash&&currentHash!==record.sha256);const message=`Publicar ${newLabel} para ${record.series_title||'Anime'} S${record.season||'—'}E${record.episode||'—'}?\n\nVersão atualmente publicada: ${currentLabel}\nNova versão: ${newLabel}\n\nA versão anterior continuará preservada na Biblioteca.`;if(!confirm(message))return;const result=await api('/library/records/'+recordId+'/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_replace:different})});$('versionDetailsDialog').close?.();if(currentArchiveSeriesId!=null)await openArchiveSeries(currentArchiveSeriesId);await loadArchive();alert(result.noop?'Esta versão já estava publicada.':'V2.2.1 publicada no Jellyfin.')}catch(e){alert(e.message||'Não foi possível publicar esta versão.')}}
function versionRow(record, peers){const reviewable=String(record.language||'').toLowerCase()!=='eng';const peer=peers.find(item=>String(item.id)!==String(record.id));const compare=peer?`<button class="button" data-compare-old="${peer.id}" data-compare-new="${record.id}">Comparar</button>`:'';return `<div class="jobline" style="display:block;padding:11px 0"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><b>${esc(recordLabel(record))}</b> <span class="badge">${esc(record.language||'—')}</span> <span class="badge">${esc(record.source_kind||'—')}</span> <span class="badge">${esc(record.validation_status||'—')}</span> ${recordStateBadge(record)}<div class="muted" style="margin-top:4px">${esc(recordAuditLabel(record))}</div></div><div class="record-actions" style="justify-content:flex-start">${recordValidated(record)&&!recordPublished(record)?`<button class="button primary" data-publish-record="${record.id}">Publicar no Jellyfin</button>`:''}<button class="button" data-details-record="${record.id}">Detalhes</button></div></div><details style="margin-top:8px"><summary>Mais ações</summary><div class="record-actions" style="justify-content:flex-start;margin-top:8px"><a class="button" href="/library/records/${record.id}/download">Baixar</a>${reviewable?`<a class="button" href="/review/${record.id}">Revisar</a>`:''}${compare}</div></details></div>`}
async function openArchiveSeries(id){currentArchiveSeriesId=id;try{const x=await api('/library/series/'+id+'?source_status=1');$('archiveDetails').innerHTML=`<div class="note"><b>${esc(x.title)}</b> · ${esc(x.classification)}<br><small>${x.episodes.length} episódios catalogados · versões agrupadas por episódio</small></div>`+x.episodes.map(ep=>{const rs=ep.records||[],translated=rs.filter(item=>String(item.language||'').toLowerCase()!=='eng'),validated=translated.filter(recordValidated).length,problems=translated.filter(item=>{const a=item.audit||{};return a.status==='PROBLEMAS DETECTADOS'&&!(a.flags||[]).every(flag=>flag==='POSSIBLE_UNTRANSLATED_OUTPUT')}).length,possible=translated.filter(item=>(item.audit?.flags||[]).includes('POSSIBLE_UNTRANSLATED_OUTPUT')).length,published=translated.filter(recordPublished).length,summary=[`${rs.length} versões`,validated?`${validated} validada(s)`:null,problems?`${problems} com problemas (por versão)`:null,possible?`${possible} auditoria(s) com possível resíduo`:null,published?`${published} publicada(s)`:null].filter(Boolean).join(' · '),pref=rs.find(r=>String(r.id)===String(ep.preferred_record_id))||rs.find(r=>r.preferred)||rs[0],source=ep.source_status||{},sourceText=source.display||source.reason||'Fonte não encontrada',peers=translated,compare=translated.length>1?`<button class="button" data-compare-episode="${ep.id}" data-old-id="${translated[translated.length-1].id}" data-new-id="${translated[0].id}">Comparar versões</button>`:'';return `<details class="jobline" style="display:block"><summary><b>${esc(ep.season||'')}${esc(ep.episode||'')}</b> ${esc(ep.episode_title||ep.media_filename)} · <span class="badge">${esc(summary)}</span> <span class="badge" title="Estado agregado; os problemas pertencem a versões específicas">estado por versão</span> <span class="badge" title="${esc(source.reason||sourceText)}">${source.available?'✓ fonte disponível':esc(source.status||'fonte')}</span></summary><div class="record-actions" style="justify-content:flex-start;margin:8px 0"><button class="button" data-audit-episode="${ep.id}">Auditar</button>${pref&&ep.retranslation_available?`<button class="button warn" data-retranslate-episode="${ep.id}" title="${esc(sourceText)}">Retraduzir</button>`:''}${compare}</div>${!source.available?`<div class="muted" style="margin:5px 0">Fonte: ${esc(sourceText)}</div>`:''}${rs.length?rs.map(record=>versionRow(record,peers)).join(''):'<div class="muted">Sem legenda arquivada.</div>'}</details>`}).join('');$('archiveDetails').querySelectorAll('[data-audit-episode]').forEach(button=>button.onclick=async()=>{try{await api('/audit/episodes/'+button.dataset.auditEpisode,{method:'POST'});await openArchiveSeries(id)}catch(e){alert(e.message)}});$('archiveDetails').querySelectorAll('[data-retranslate-episode]').forEach(button=>button.onclick=()=>retranslate([Number(button.dataset.retranslateEpisode)],false));$('archiveDetails').querySelectorAll('[data-details-record]').forEach(button=>button.onclick=()=>showVersionDetails(Number(button.dataset.detailsRecord)));$('archiveDetails').querySelectorAll('[data-publish-record]').forEach(button=>button.onclick=()=>publishRecord(Number(button.dataset.publishRecord)));$('archiveDetails').querySelectorAll('[data-compare-episode],[data-compare-old]').forEach(button=>button.onclick=async()=>{try{const oldId=button.dataset.oldId||button.dataset.compareOld,newId=button.dataset.newId||button.dataset.compareNew;const d=await api('/library/records/compare?old_id='+oldId+'&new_id='+newId);alert(`${d.changed_count||0} evento(s) alterado(s).`)}catch(e){alert(e.message)}})}catch(e){$('archiveDetails').textContent='Detalhe indisponível';}}
$('closeVersionDetails').onclick=()=>{$('versionDetailsDialog').close?.();$('versionDetailsDialog').removeAttribute('open')};$('versionDetailsDialog').addEventListener('click',event=>{if(event.target===$('versionDetailsDialog'))$('versionDetailsDialog').close?.()});
</script></body></html>'''


REVIEW_PAGE = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" type="image/svg+xml" href="/favicon.svg?v=1"><link rel="apple-touch-icon" href="/favicon.svg?v=1">
<title>Revisão humana · Transass</title>
<style>
:root{color-scheme:dark;--bg:#0b1119;--panel:#131d28;--line:#2b3e52;--text:#edf4fb;--muted:#96a7b9;--green:#238636;--red:#8e2024;--blue:#247fdd}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% -8%,#193252 0,#0b1119 38%);color:var(--text);font:15px/1.48 system-ui,sans-serif;min-height:100vh}main{max-width:1320px;margin:auto;padding:22px 18px 52px}header{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:15px;padding:17px 19px;border:1px solid #304961;border-radius:16px;background:linear-gradient(120deg,#17283cef,#111b29f2);box-shadow:0 18px 44px #0004}h1{font-size:1.48rem;letter-spacing:-.025em;margin:2px 0}.back{display:inline-block;color:#80b7ff;text-decoration:none;font-size:.82rem;margin-bottom:2px}.muted{color:var(--muted)}.panel{background:linear-gradient(145deg,#151f2bf7,#101821f7);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 12px 28px #0003}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.button,button{border:1px solid var(--line);border-radius:9px;padding:8px 12px;background:#202e3e;color:var(--text);cursor:pointer;min-height:38px;text-decoration:none}.button:hover,button:hover{border-color:#5b7898;background:#293b4f}.button.primary{background:linear-gradient(135deg,#238636,#2e9f46)}.button.approve{background:linear-gradient(135deg,#247fdd,#326de0)}.button.reject{background:var(--red)}button:disabled{opacity:.45;cursor:not-allowed}select,input,textarea{background:#0a121b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px;font:inherit}input,textarea{width:100%}textarea{min-height:88px;resize:vertical}.segment{border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0;background:#0f1924}.segment:focus-within{border-color:#4778a8;box-shadow:0 0 0 3px #3275b322}.segment-head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.cols{display:grid;grid-template-columns:1fr 1fr;gap:10px}.box{border:1px solid var(--line);border-radius:9px;padding:10px;white-space:pre-wrap;overflow:auto;background:#0a121b}.source{border-left:3px solid #d29922}.generated{border-left:3px solid #58a6ff}.context{font-size:.88rem;color:var(--muted);margin:8px 0}.badge{border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:.78rem}.ok{color:#b7f5c0}.warn{color:#f2cc60}.fail{color:#ffaba8}@media(max-width:760px){.cols{grid-template-columns:1fr}main{padding:10px 9px 34px}header{padding:14px}.toolbar>*{flex:1 1 140px}}
</style></head><body><main>
<header><div><a class="back" href="/">← Voltar para a Central</a><h1>Revisão humana</h1><div class="muted">Você cuida do português; o robô fica proibido de corrigir a própria prova.</div></div><div id="meta" class="muted">Carregando…</div></header>
<section class="panel"><div class="toolbar"><label>Filtro <select id="filter"><option value="">Todos</option><option>UNREVIEWED</option><option>NEEDS_CORRECTION</option><option>CORRECTED</option><option>APPROVED</option><option>REJECTED</option></select></label><input id="search" placeholder="Buscar texto ou evento" style="max-width:320px"><button id="prev">Anterior</button><button id="next">Próximo</button><button class="button primary" id="materialize" disabled>Materializar versão corrigida</button><button class="button approve" id="approveVersion" disabled>Aprovar versão</button><button class="button" id="publishVersion" disabled>Publicar versão aprovada</button></div><div id="counts" class="muted" style="margin-top:8px"></div><div id="version" style="margin-top:8px"></div></section>
<section id="segments" class="panel"><div class="muted">Abrindo sessão…</div></section>
</main><script>
const RECORD_ID=Number('__RECORD_ID__');let session=null,segments=[],cursor=0,materialized=null,currentRows=[];
const $=id=>document.getElementById(id);function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML}
async function api(url,opt){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw Error(d.error||`HTTP ${r.status}`);return d}
function correction(s){return (s.corrections||[]).slice().reverse().find(x=>['DRAFT','REVIEWED','APPROVED'].includes(x.status))}
function editableValue(value){return String(value||'').replace(/^(?:\{[^{}]*\})+/,'').replace(/(?:\{[^{}]*\})+$/,'')}
function render(){const filter=$('filter').value,query=$('search').value.toLowerCase();currentRows=segments.filter(s=>(!filter||s.status===filter)&&(!query||(`${s.event_index} ${s.source_text} ${s.generated_text}`).toLowerCase().includes(query)));$('counts').textContent=`${currentRows.length} exibidos · ${segments.length} eventos totais`;$('segments').innerHTML=currentRows.map(s=>{const c=correction(s),value=editableValue(c?c.corrected_text:s.editable_text||s.generated_text),locked=!!s.editing_locked;return `<article class="segment" data-segment="${s.id}"><div class="segment-head"><b>Evento ${s.event_index} · ${esc(s.start_time||'')} → ${esc(s.end_time||'')}</b><span class="badge ${s.status==='APPROVED'?'ok':s.status==='REJECTED'?'fail':'warn'}">${esc(s.status)}</span></div><div class="context">Anterior: ${esc((s.context_before||[]).slice(-1).map(x=>x.source||x.text).join(' · ')||'—')}<br>Seguinte: ${esc((s.context_after||[]).slice(0,1).map(x=>x.source||x.text).join(' · ')||'—')}</div><div class="cols"><div class="box source"><b>ORIGINAL</b>\n${esc(s.source_text||'[fonte não arquivada]')}</div><div class="box generated"><b>GERADO</b>\n${esc(s.generated_text)}</div></div><div style="margin-top:8px"><label>CORREÇÃO (somente texto)<textarea data-edit="${s.id}" ${locked?'disabled':''}>${esc(value)}</textarea></label>${locked?`<div class="context">${esc(s.editing_lock_reason||'Edição bloqueada para preservar estrutura ASS.')}</div>`:''}</div><div class="toolbar" style="margin-top:8px"><input data-reason="${s.id}" placeholder="Motivo: naturalidade, contexto…" value="${esc(c&&c.reason||'')}"><button data-ok="${s.id}">Marcar OK</button><button data-save="${s.id}" class="button primary" ${locked?'disabled':''}>Salvar correção</button>${c&&c.status!=='APPROVED'?`<button data-approve="${c.id}" class="button approve">Aprovar correção</button>`:''}<button data-reject="${c&&c.id||''}" class="button reject" ${c?'':'disabled'}>Rejeitar</button></div></article>`}).join('')||'<div class="muted">Nenhum evento para este filtro.</div>';bind()}
function bind(){document.querySelectorAll('[data-ok]').forEach(b=>b.onclick=async()=>{try{await api('/review/segments/'+b.dataset.ok+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'OK'})});await load()}catch(e){alert(e.message)}});document.querySelectorAll('[data-save]').forEach(b=>b.onclick=async()=>{try{const id=b.dataset.save;const c=await api('/review/segments/'+id+'/correction',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({corrected_text:document.querySelector(`[data-edit="${id}"]`).value,reason:document.querySelector(`[data-reason="${id}"]`).value})});await load();alert('Draft salvo. A aprovação continua sendo uma ação separada.')}catch(e){alert(e.message)}});document.querySelectorAll('[data-approve]').forEach(b=>b.onclick=async()=>{if(!confirm('Aprovar esta correção humana?'))return;try{await api('/review/corrections/'+b.dataset.approve+'/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await load()}catch(e){alert(e.message)}});document.querySelectorAll('[data-reject]').forEach(b=>b.onclick=async()=>{if(!b.dataset.reject||!confirm('Rejeitar esta correção?'))return;try{await api('/review/corrections/'+b.dataset.reject+'/reject',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await load()}catch(e){alert(e.message)}})}
async function load(){const d=await api('/review/sessions/'+session.id);session=d;segments=d.segments;$('meta').textContent=`registro ${RECORD_ID} · sessão ${session.id} · ${d.status}`;$('version').textContent=d.materialized_record_id?`Versão HUMAN_CORRECTED: ${d.materialized_record_id}`:'';const c=d.counts||{};$('counts').textContent=Object.entries(c).map(([k,v])=>`${k}: ${v}`).join(' · ');$('materialize').disabled=!(c.APPROVED>0)||!!d.materialized_record_id;$('approveVersion').disabled=!d.materialized_record_id;$('publishVersion').disabled=!d.materialized_record_id;render()}
async function start(){try{session=await api('/review/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({record_id:RECORD_ID})});await load()}catch(e){$('segments').innerHTML=`<div class="fail">${esc(e.message)}</div>`}}
$('filter').onchange=render;$('search').oninput=render;$('materialize').onclick=async()=>{try{materialized=await api('/review/sessions/'+session.id+'/materialize',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await load();alert('Nova versão HUMAN_CORRECTED criada.')}catch(e){alert(e.message)}};$('approveVersion').onclick=async()=>{try{await api('/review/versions/'+(materialized?.id||session.materialized_record_id)+'/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await load();alert('Versão aprovada.')}catch(e){alert(e.message)}};$('publishVersion').onclick=async()=>{try{const id=materialized?.id||session.materialized_record_id;await api('/review/versions/'+id+'/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_replace:confirm('Se o destino tiver hash diferente, substituir explicitamente?')})});alert('Publicação registrada.')}catch(e){alert(e.message)}};
function move(delta){if(!currentRows.length)return;const current=document.querySelector('.segment:focus-within')||document.querySelector('.segment');let index=current?currentRows.findIndex(s=>String(s.id)===current.dataset.segment):-1;index=Math.max(0,Math.min(currentRows.length-1,index+delta));const target=document.querySelector(`[data-segment="${currentRows[index].id}"]`);if(target){target.scrollIntoView({behavior:'smooth',block:'center'});target.querySelector('textarea')?.focus()}}
$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);start();
</script></body></html>'''


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
