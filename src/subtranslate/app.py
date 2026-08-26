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

app = Flask(__name__)
state_lock = threading.RLock()

# This is the same official Subtranslate mark used by the previous web build.
# It is served as a real asset so browser cache/path handling cannot remove it.
FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#2f6fed"/><text x="32" y="43" text-anchor="middle" font-size="38">🎬</text></svg>'''


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pipeline() -> str:
    return os.environ.get("TRANSLATOR_PIPELINE", "legacy").strip().lower()


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
    for marker in ("pt-BR", "pt_br", "ptbr"):
        for extension in SUBTITLE_EXTENSIONS:
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
    key = str(int(episode_id))
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


def _episode_record_for_video(video: Path) -> dict:
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


def _episode_records(folder: Path) -> list[dict]:
    records = []
    for video in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS):
        try:
            records.append(_episode_record_for_video(video))
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
    }


def _public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    telemetry = _job_telemetry(job)
    public = {
        key: job.get(key)
        for key in (
            "id", "source", "name", "episode", "folder", "status", "created_at",
            "started_at", "finished_at", "error", "reason", "summary", "progress",
            "attempt", "retry_count", "dry_run", "critical_flags", "flags",
            "operation", "source_record_id", "old_record_id", "bulk_fail_fast",
            "not_started_reason", "new_record_id", "audit", "diagnostic",
        )
        if key in job
    }
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
        return
    payload = {
        "version": 1,
        "updated_at": _now(),
        "jobs": state["jobs"][-500:],
        "history": state["history"][-MAX_HISTORY:],
        "folder": state.get("folder"),
        "queue_paused": state.get("queue_paused", False),
        "audits": state.get("audits", {}),
    }
    try:
        fd, raw = tempfile.mkstemp(prefix=".jobs-", suffix=".json", dir=str(STATE_DIR))
        os.close(fd)
        temporary = Path(raw)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, STATE_FILE)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass


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
    for job in jobs:
        if job.get("status") in {"STARTING", "TRANSLATING", "VALIDATING", "PUBLISHING", "PAUSING"}:
            job["status"] = "FAILED"
            job["stage"] = "FAILED"
            job["error"] = "serviço reiniciado durante o job; nenhuma retomada automática"
            job["reason"] = "service_restarted"
            job["finished_at"] = _now()
    audits = payload.get("audits", {}) if isinstance(payload, dict) else {}
    if not isinstance(audits, dict):
        audits = {}
    return {"jobs": jobs[-500:], "history": history[-MAX_HISTORY:], "audits": audits}


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
    _append_log(line, level=_summary_level(line), job_id=job["id"])
    _persist_locked()


def _run_episode(job: dict) -> None:
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
    command = [
        "python3", "-u", str(RUNNER_PATH),
        "--source", str(source), "--output", str(output),
        "--memory-root", str(LIBRARY_ROOT), "--pipeline", _pipeline(),
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
    with state_lock:
        job["status"] = "STARTING"
        job["stage"] = "STARTING"
        job["started_at"] = _now()
        job["progress"] = {"scope": "phase", "phase": "STARTING", "label": job.get("name", "")}
        _append_log(f"Iniciando retradução: {job.get('name', '')}", level="summary", job_id=job["id"])
        _persist_locked()
    proc = None
    summary = None
    try:
        proc = subprocess.Popen(command, cwd="/app", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
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
            with state_lock:
                _append_log(line, level=_summary_level(line), job_id=job["id"])
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
        audit = audit_record(source, output)
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
        _validate_retranslation_job_integrity(job)
        source_id = int(job["source_record_id"])
        old_id = job.get("old_record_id")
        if _pipeline() == "v2_3_0":
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
                job_id=job["id"], pipeline_version=_pipeline(), model=_model(),
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
        if not job.get("failure_staging_path"):
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
    pipeline = _pipeline()
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
        raw_candidate = folder / value
        candidate = raw_candidate.resolve()
        try:
            candidate.relative_to(folder.resolve())
        except ValueError:
            # A symlinked episode is allowed only when its resolved target stays
            # inside the library; ordinary path traversal remains rejected.
            if not raw_candidate.is_symlink():
                raise ValueError("episódio inválido")
            candidate.relative_to(BASE_LIBRARY.resolve())
        if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError("episódio inválido")
        selected.append(candidate)
    return list(dict.fromkeys(selected))


@app.route("/")
def index():
    # Details are rendered by the human-friendly modal; the JSON endpoint
    # remains available to programmatic clients but is never a normal UI link.
    return PAGE.replace("__BASE_LIBRARY__", html.escape(str(BASE_LIBRARY))).replace('href="/library/records/${r.id}">Detalhes', 'data-details-record="${r.id}">Detalhes')


@app.route("/favicon.svg")
def favicon_svg():
    return Response(
        FAVICON_SVG,
        mimetype="image/svg+xml",
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
    with state_lock:
        return jsonify({"folder": _safe_relative(folder), "episodes": _episode_records(folder)})


@app.route("/pipeline")
def pipeline_route():
    return jsonify(_pipeline_info())


@app.route("/health")
def health():
    from _version import __version__

    return jsonify({"status": "ok", "version": __version__})


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
    audit = audit_record(source.get("path") if source.get("available") else None, output_path)
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
) -> dict[str, Any]:
    """Resolve every requested episode before creating any queue job.

    This is deliberately read-only.  Source extraction/materialization only
    happens after the complete preflight has no blocking item.
    """
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
        source = resolve_episode_source(subtitle_library, episode_id, int(old["id"]), materialize=False)
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
) -> dict[str, Any]:
    bulk = bool(confirm)
    with state_lock:
        if state["running"] or any(job.get("status") == "WAITING" for job in state["jobs"] if job.get("session_id") == state.get("session_id")):
            raise LibraryError("já existe uma fila em execução ou aguardando")
        preflight = _retranslation_preflight(episode_ids, bulk=bulk, force_current=force_current)
        if preflight["blocked"]:
            raise LibraryError(json.dumps({
                "code": "retranslation_preflight_blocked",
                "message": "pré-flight bloqueou a operação; nenhum episódio foi iniciado",
                "preflight": {key: value for key, value in preflight.items() if key != "eligible"},
            }, ensure_ascii=False))
        source_job_id = f"source-resolve-{uuid.uuid4().hex}"
        prepared = []
        for item in preflight["eligible"]:
            episode, old = item["episode"], item["old"]
            source = resolve_episode_source(
                subtitle_library, int(episode["id"]), int(old["id"]), materialize=True, job_id=source_job_id,
                source_language=_global_source_language(),
            )
            if not source.get("available"):
                raise LibraryError(f"{episode.get('media_filename')}: fonte deixou de estar disponível após o pré-flight")
            state.setdefault("source_status", {})[str(int(episode["id"]))] = _public_source_status(source)
            prepared.append((episode, old, source))
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
            }
            jobs.append(job); state["jobs"].append(job)
        for episode, old, source in prepared:
            job = {
                "id": uuid.uuid4().hex, "session_id": session_id, "operation": "RETRANSLATE",
                "folder": state["folder"], "source": source.get("record_id"), "source_abs": source["path"],
                "source_record_id": source.get("record_id"), "old_record_id": old.get("id"),
                "source_staging_path": source.get("staging_path"),
                "episode_id": episode["id"], "series_id": episode["series_id"],
                "series_title": episode.get("series_title") or "Anime", "episode_title": episode.get("episode_title") or episode.get("media_filename") or "Episode",
                "name": episode.get("media_filename") or f"E{episode.get('episode', '')}",
                "episode": episode.get("episode"), "status": "WAITING", "created_at": _now(),
                "started_at": None, "finished_at": None, "error": None, "reason": None,
                "summary": None, "progress": None, "flags": {}, "critical_flags": [],
                "attempt": 1, "retry_count": 0, "dry_run": False, "published": False,
                "bulk_fail_fast": bulk,
            }
            jobs.append(job); state["jobs"].append(job)
        _append_log(
            f"Fila de retradução criada: {len(prepared)} episódio(s) elegível(is); "
            f"{len(preflight['skipped'])} ignorado(s) por versão atual validada",
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
        return jsonify(_queue_retranslation(
            episode_ids,
            confirm=bool(data.get("confirm") or data.get("bulk")),
            force_current=bool(data.get("force_current") or data.get("force_retranslation")),
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
        result = _retranslation_preflight(
            episode_ids,
            bulk=bool(data.get("bulk", True)),
            force_current=bool(data.get("force_current") or data.get("force_retranslation")),
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
    return REVIEW_PAGE.replace("__RECORD_ID__", str(record_id))


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
<title>Tradutor de Legendas</title>
<style>
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--panel2:#1c2430;--line:#30363d;--text:#e6edf3;--muted:#9da7b3;--blue:#3b82f6;--green:#3fb950;--yellow:#d29922;--red:#f85149;}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#0d1117,#111827);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1220px;margin:auto;padding:22px 16px 48px;min-width:0}h1,h2{margin:0}h1{font-size:1.35rem}.muted{color:var(--muted)}.top{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px}.chips{display:flex;gap:8px;flex-wrap:wrap}.chip,.badge{border:1px solid var(--line);border-radius:999px;padding:5px 10px;font-size:.78rem;background:var(--panel2)}.online{color:#b7f5c0;border-color:#2ea043}.grid{display:grid;grid-template-columns:minmax(220px,min(36vw,360px)) minmax(0,1fr);gap:14px;align-items:start}.panel{background:rgba(22,27,34,.92);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 12px 30px #0002;min-width:0}.panel h2{font-size:.95rem;margin-bottom:12px}.wide{grid-column:1/-1}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;min-width:0}.crumb{min-height:38px;padding:8px 10px;background:#0d1117;border:1px solid var(--line);border-radius:8px;overflow:auto;white-space:nowrap}.crumb button{background:none;border:0;color:#79c0ff;padding:0;cursor:pointer}.actions{margin-top:12px}.button,button{border:1px solid var(--line);border-radius:8px;padding:9px 13px;background:#21262d;color:var(--text);cursor:pointer;min-height:40px}.button.primary{background:#238636;border-color:#2ea043}.button.warn{background:#9e6a03}.button.danger{background:#8e1519}.button:disabled,button:disabled{opacity:.45;cursor:not-allowed}select{background:#0d1117;border:1px solid var(--line);color:var(--text);padding:9px;border-radius:8px;min-height:40px;max-width:100%}.episodes{margin-top:12px;display:grid;gap:7px;max-height:420px;overflow:auto}.episode{display:grid;grid-template-columns:26px 58px minmax(0,1fr) auto;gap:8px;align-items:center;padding:9px;border:1px solid var(--line);border-radius:9px;background:#111820;min-width:0}.episode input{width:18px;height:18px}.epname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}.badge.ok{color:#b7f5c0;border-color:#2ea043}.badge.fail{color:#ffaba8;border-color:#f85149}.badge.wait{color:#f2cc60;border-color:#9e6a03}.badge.neutral{color:var(--muted)}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}.stat{padding:10px;background:#111820;border:1px solid var(--line);border-radius:9px;min-width:0}.stat b{display:block;font-size:1.15rem}.progress{height:10px;background:#30363d;border-radius:9px;overflow:hidden}.progress i{display:block;height:100%;background:linear-gradient(90deg,#2f81f7,#3fb950);width:0}.jobline{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;border-bottom:1px solid #21262d;padding:8px 0;min-width:0}.jobline:last-child{border:0}.record-actions{display:flex;justify-content:flex-end;align-items:center;gap:6px;flex-wrap:wrap}.log{background:#080b0f;border:1px solid var(--line);border-radius:9px;padding:10px;max-height:290px;overflow:auto;font:12px/1.5 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap}.log .error{color:#ffaba8}.log .summary{color:#b7f5c0}.history{max-height:300px;overflow:auto}.note{padding:9px;background:#1f2937;border-left:3px solid var(--yellow);border-radius:5px;color:#e5e7eb}.hidden{display:none}@media(max-width:1020px){.grid{grid-template-columns:minmax(190px,29vw) minmax(0,1fr)}main{padding-left:12px;padding-right:12px}}@media(max-width:820px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}.nav-panel{max-height:none}.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.episode{grid-template-columns:24px 55px minmax(0,1fr) auto}}@media(max-width:480px){main{padding:12px 9px 32px}.top{display:block}.chips{margin-top:10px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.episode{grid-template-columns:24px 45px minmax(0,1fr)}.episode .badge{grid-column:3/-1;justify-self:start}.actions .button,.record-actions .button{flex:1 1 140px}.jobline{grid-template-columns:1fr}.record-actions{justify-content:flex-start}.panel{padding:12px}}
:focus-visible{outline:2px solid #58a6ff;outline-offset:2px}.job-progress-meta{display:flex;gap:8px;flex-wrap:wrap;font-size:.88rem}.job-progress-meta span{white-space:nowrap}
</style></head>
<body><main>
<header class="top"><div><h1>Transass</h1><div class="muted">Tradutor de legendas · fila segura · sem sobrescrever PT-BR existente</div></div><div class="chips"><span class="chip" id="pipelineChip">Pipeline…</span><span class="chip" id="modelChip">Modelo…</span><span class="chip" id="motorChip" title="Motor de tradução configurado">Motor…</span><span class="chip online" id="serviceChip">Serviço…</span><button class="button" id="openTransportConfig" style="margin-left:8px">⚙ Motor</button></div></header>
<div class="grid">
<section class="panel nav-panel"><h2>Biblioteca</h2><div id="crumb" class="crumb" aria-label="Caminho atual"></div><div class="row actions"><select id="folderSelect" aria-label="Subpastas"></select><button class="button" id="enterBtn">Entrar</button><button class="button" id="upBtn">Voltar</button></div><div class="row actions"><button class="button" id="useBtn">Usar esta pasta</button></div><div id="libraryNote" class="note hidden"></div></section>
<section class="panel"><h2>Fila e controles</h2><div class="stats"><div class="stat"><b id="doneCount">0/0</b><span class="muted">concluídos</span></div><div class="stat"><b id="waitCount">0</b><span class="muted">na fila</span></div><div class="stat"><b id="runCount">0</b><span class="muted">em execução</span></div><div class="stat"><b id="failCount">0</b><span class="muted">falhas</span></div><div class="stat"><b id="skipCount">0</b><span class="muted">ignorados</span></div><div class="stat"><b id="notStartedAfterFailureCount">0</b><span class="muted">não iniciados por falha</span></div></div><div class="row actions"><button class="button primary" id="startBtn">Traduzir selecionados</button><button class="button" id="retryBtn">Reprocessar falhos</button><button class="button warn" id="pauseBtn">Pausar</button><button class="button" id="resumeBtn">Continuar</button><button class="button danger" id="stopBtn">Parar fila</button></div><div class="row actions"><label title="Planejamento sem tradução nem publicação"><input type="checkbox" id="dryrun"> dry-run</label><button class="button" id="auditSeasonBtn">Auditar temporada</button><button class="button" id="retranslateSeasonBtn">Retraduzir temporada</button></div></section>
<section class="panel wide"><div class="row" style="justify-content:space-between"><h2>Episódios</h2><div class="row"><button class="button" id="selectMissing">Selecionar sem PT-BR</button><button class="button" id="selectLegacy">Selecionar legadas</button><button class="button" id="clearSelection">Limpar</button><button class="button" id="retranslateSelectedBtn">Retraduzir selecionados</button></div></div><div class="row" style="margin:6px 0 2px"><label class="muted" style="align-self:center">Idioma da temporada</label><select id="seasonLang" title="Aplica a todos os episódios da pasta" style="margin:0 4px"><option value="inglês">inglês</option></select><button class="button" id="detectSeasonLang" title="Detecta os idiomas disponíveis na temporada (usa o primeiro episódio catalogado)">Detectar</button><span class="muted" style="align-self:center">— aplica a todos de uma vez</span></div><div id="episodes" class="episodes"><span class="muted">Escolha uma pasta.</span></div></section>
<section class="panel"><h2>Progresso atual</h2><div id="currentTitle" class="muted">Nenhum episódio em execução.</div><div class="progress" aria-label="Progresso por unidade"><i id="progressBar"></i></div><div id="currentMeta" class="muted" style="margin-top:8px"></div><div id="currentTelemetry" class="muted job-progress-meta" style="margin-top:6px"></div><div id="queueList" style="margin-top:10px"></div></section>
<section class="panel"><div class="row" style="justify-content:space-between"><h2>Histórico</h2><label class="muted"><input type="checkbox" id="showTechnical"> mostrar jobs técnicos</label></div><div id="history" class="history muted">Nenhuma sessão registrada.</div></section>
<section class="panel wide"><div class="row" style="justify-content:space-between"><h2>Biblioteca de legendas · Anime</h2><span class="muted" id="libraryStats">Acervo persistente</span></div><div id="archiveSeries" class="history muted">Nenhuma série anime catalogada.</div><div id="archiveDetails" class="history" style="margin-top:10px"></div></section>
<section class="panel wide"><div class="row" style="justify-content:space-between"><h2>Memória de tradução · Anime</h2><button class="button" id="memorySync">Sincronizar aprovações</button></div><div id="memoryStats" class="muted">Memória local, limitada e alimentada somente por SEGMENT_APPROVED.</div><div id="memoryItems" class="history" style="margin-top:10px">Nenhuma memória ativa.</div></section>
<dialog id="versionDetailsDialog" style="width:min(720px,calc(100vw - 24px));max-width:720px;max-height:88vh;padding:0;border:1px solid #30363d;border-radius:14px;background:#161b22;color:#e6edf3;box-shadow:0 24px 80px #0008"><div style="padding:18px;overflow:auto;max-height:88vh"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><h2 id="versionDetailsTitle">Detalhes da versão</h2><div id="versionDetailsSubtitle" class="muted"></div></div><button class="button" id="closeVersionDetails" aria-label="Fechar detalhes">Fechar</button></div><div id="versionDetailsBody" style="margin-top:14px"><span class="muted">Carregando…</span></div></div></dialog><dialog id="transportConfigDialog" style="width:min(560px,calc(100vw - 24px));max-width:560px;padding:0;border:1px solid #30363d;border-radius:14px;background:#161b22;color:#e6edf3;box-shadow:0 24px 80px #0008"><div style="padding:18px"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><h2 style="margin:0">Motor de tradução</h2><div class="muted">Principal + fallback opcional · keys nunca expostas</div></div><button class="button" id="closeTransportConfig">Fechar</button></div><div style="margin-top:14px"><label>Idioma de origem da legenda</label><input id="tcSourceLanguage" placeholder="ex.: inglês, espanhol, japonês" style="width:100%;margin:4px 0"><label>Motor principal</label><select id="tcPrimaryProvider" style="width:100%;margin:4px 0"><option value="ollama">Ollama (local/GPU)</option><option value="openai_compat">OpenAI-compatível (Groq/OpenRouter/LM Studio)</option><option value="gemini">Gemini (Google)</option></select><input id="tcPrimaryModel" placeholder="modelo (ex.: qwen3.5:9b)" style="width:100%;margin:4px 0"><input id="tcPrimaryBaseUrl" placeholder="base_url (só openai_compat)" style="width:100%;margin:4px 0"><label style="margin-top:10px">Fallback (opcional — usado se o principal falhar)</label><select id="tcFallbackProvider" style="width:100%;margin:4px 0"><option value="">— sem fallback —</option><option value="ollama">Ollama</option><option value="openai_compat">OpenAI-compatível</option><option value="gemini">Gemini</option></select><input id="tcFallbackModel" placeholder="modelo do fallback" style="width:100%;margin:4px 0"><input id="tcFallbackBaseUrl" placeholder="base_url (só openai_compat)" style="width:100%;margin:4px 0"><div id="tcKeys" style="margin-top:10px"></div><div class="muted" style="margin-top:8px">Keys são salvas apenas no servidor (arquivo local, permissão 600). Para remover, deixe o campo vazio.</div><div class="row" style="justify-content:flex-end;margin-top:14px"><button class="button primary" id="tcSave">Salvar</button></div><div id="tcStatus" class="muted" style="margin-top:8px"></div></div></div></dialog>
<section class="panel wide"><details><summary>Logs técnicos</summary><div id="logs" class="log" style="margin-top:10px"></div></details></section>
</div></main>
<script>
const $=id=>document.getElementById(id);let path="",selectedFolder="",selectionFolder="",cursor=0,episodes=[],statusData=null,selectedEpisodeKeys=new Set(),refreshInFlight=false,renderedLogIds=new Set();let globalSourceLang="inglês";let seasonLangValue="inglês";const episodeSourceLang={};const langSelects={};
async function api(url,opt){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw new Error(d.error||`HTTP ${r.status}`);return d}
function esc(s){const d=document.createElement('div');d.textContent=s??'';return d.innerHTML}
function episodeKey(ep){return ep.library_episode_id?`id:${ep.library_episode_id}`:`source:${ep.source}`}
function selected(){return episodes.filter(ep=>selectedEpisodeKeys.has(episodeKey(ep))).map(ep=>ep.source)}
function selectedEpisodeIds(){return episodes.filter(ep=>selectedEpisodeKeys.has(episodeKey(ep))).map(ep=>ep.library_episode_id).filter(Boolean).map(Number)}
function bindSelection(){document.querySelectorAll('#episodes input[type=checkbox]').forEach(input=>{input.onchange=()=>{const key=input.dataset.selectionKey;if(input.checked)selectedEpisodeKeys.add(key);else selectedEpisodeKeys.delete(key)}});document.querySelectorAll('#episodes .srclang').forEach(sel=>{const key=sel.dataset.key;langSelects[key]=sel;sel.onfocus=()=>populateLangSelect(sel);sel.onchange=()=>{episodeSourceLang[key]=sel.value}})}
async function loadBrowse(){try{const d=await api('/browse?path='+encodeURIComponent(path));$('crumb').innerHTML=path?path.split('/').map((x,i)=>`<button data-p="${esc(path.split('/').slice(0,i+1).join('/'))}">${esc(x)}</button>`).join(' › '):'<span class="muted">Shows</span>';$('crumb').querySelectorAll('button').forEach(b=>b.onclick=()=>{path=b.dataset.p;loadBrowse()});const sel=$('folderSelect');sel.replaceChildren(...d.subfolders.map(folder=>{const option=document.createElement('option');option.value=folder;option.textContent = folder;return option}));$('upBtn').disabled=!path;$('useBtn').disabled=!d.has_videos;if(selectedFolder!==path){selectedEpisodeKeys.clear();selectionFolder=path}selectedFolder=path;await loadEpisodes()}catch(e){$('libraryNote').textContent='Biblioteca temporariamente indisponível.';$('libraryNote').classList.remove('hidden');console.error('browse/episodes',e)}}
let episodeRenderFingerprint='';
async function loadEpisodes(){if(!selectedFolder){$('episodes').innerHTML='<span class="muted">Escolha uma pasta.</span>';return}if(selectionFolder!==selectedFolder){selectedEpisodeKeys.clear();selectionFolder=selectedFolder}const d=await api('/episodes?path='+encodeURIComponent(selectedFolder));episodes=d.episodes;const valid=new Set(episodes.map(episodeKey));selectedEpisodeKeys=new Set([...selectedEpisodeKeys].filter(key=>valid.has(key)));const fingerprint=JSON.stringify(episodes.map(ep=>[episodeKey(ep),ep.status,ep.audit_status,ep.ptbr]));if(fingerprint!==episodeRenderFingerprint){episodeRenderFingerprint=fingerprint;renderEpisodes()}}
function badge(status){const cls=status==='COMPLETED'||status==='ALREADY_TRANSLATED'?'ok':status==='FAILED'?'fail':['WAITING','TRANSLATING','STARTING','VALIDATING','PUBLISHING','PAUSED'].includes(status)?'wait':'neutral';const label=status==='ALREADY_TRANSLATED'?'Já traduzido':status==='NOT_STARTED'?'Não iniciado':status;return `<span class="badge ${cls}">${esc(label)}</span>`}
function auditBadge(ep){const s=ep.audit_status||'NÃO AUDITADA';const cls=s==='SEM PROBLEMAS DETECTADOS'?'ok':s==='PROBLEMAS DETECTADOS'?'fail':['AUDITORIA PARCIAL','REVISÃO RECOMENDADA'].includes(s)?'wait':'neutral';const short=s==='SEM PROBLEMAS DETECTADOS'?'✓ sem problemas':s==='PROBLEMAS DETECTADOS'?'⚠ problemas':s==='AUDITORIA PARCIAL'?'◐ parcial':s==='REVISÃO RECOMENDADA'?'◐ revisão recomendada':'— não auditada';return `<span class="badge ${cls}" title="${esc(s)}">${short}</span>`}
function sourceBadge(ep){const s=ep.source_status||{};const status=s.status||'SOURCE_NOT_FOUND';const cls=s.available?'ok':status==='SOURCE_AMBIGUOUS'||status==='SOURCE_AVAILABLE_PGS_UNSUPPORTED'||status==='SOURCE_STATUS_ERROR'?'wait':'neutral';const text=s.display||(status==='SOURCE_AVAILABLE_LIBRARY'?'✓ Biblioteca':status==='SOURCE_AVAILABLE_SIDECAR'?'✓ Sidecar':status==='SOURCE_AVAILABLE_INTERNAL_TEXT'?'✓ Track interna':status==='SOURCE_AVAILABLE_PGS_UNSUPPORTED'?'⚠ PGS — OCR não suportado':status==='SOURCE_AMBIGUOUS'?'⚠ Fonte ambígua':status==='SOURCE_STATUS_ERROR'?'⚠ Metadata da fonte':'✕ Fonte não encontrada');return `<span class="badge ${cls}" title="${esc(s.reason||s.display||text)}">${text}</span>`}
function renderEpisodes(){const box=$('episodes');if(!episodes.length){box.innerHTML='<span class="muted">Nenhum vídeo encontrado nesta pasta.</span>';return}box.replaceChildren(...episodes.map(ep=>{const row=document.createElement('label');row.className='episode';const key=episodeKey(ep),disabled=['WAITING','TRANSLATING','STARTING','VALIDATING','PUBLISHING'].includes(ep.status);const lang=episodeSourceLang[key]||seasonLangValue||globalSourceLang;const sel=`<select class="srclang" data-key="${esc(key)}" data-epid="${esc(ep.library_episode_id||'')}" data-path="${esc(ep.source||'')}" title="Idioma de origem da legenda" style="min-height:30px;max-width:150px;padding:4px 6px"><option value="${esc(lang)}">${esc(lang)}</option></select>`;row.innerHTML=`<input type="checkbox" data-selection-key="${esc(key)}" data-source="${esc(ep.source)}" data-episode-id="${esc(ep.library_episode_id||'')}" ${selectedEpisodeKeys.has(key)?'checked':''} ${disabled?'disabled':''}><b>${esc(ep.episode||'—')}</b><span class="epname" title="${esc(ep.name)}">${esc(ep.name)}</span>${badge(ep.status)}${auditBadge(ep)}${sourceBadge(ep)}${sel}`;return row}));bindSelection()}
async function populateLangSelect(sel){const epid=sel.dataset.epid;const rel=sel.dataset.path;if((!epid&&!rel)||sel.dataset.loaded)return;sel.dataset.loaded='1';try{const q=epid?('episode_id='+encodeURIComponent(epid)):('path='+encodeURIComponent(rel));const d=await api('/source-options?'+q);const opts=Array.from(new Set(d.options.map(o=>o.language).filter(Boolean)));if(!opts.length)return;const cur=sel.value;sel.innerHTML=opts.map(l=>`<option value="${esc(l)}"${l===cur?' selected':''}>${esc(l)}</option>`).join('')}catch(e){console.error('source-options',e)}}
function applySeasonLang(lang){seasonLangValue=lang;episodes.forEach(ep=>{const key=episodeKey(ep);episodeSourceLang[key]=lang;const el=langSelects[key];if(el)el.value=lang})}
async function detectSeasonLang(){const ep=episodes.find(e=>e.source)||episodes[0];if(!ep||!ep.source)return alert('Nenhum vídeo selecionado para detectar.');try{const d=await api('/source-options?path='+encodeURIComponent(ep.source));const opts=Array.from(new Set(d.options.map(o=>o.language).filter(Boolean)));if(!opts.length)return alert('Nenhum idioma de legenda detectado na temporada.');const cur=$('seasonLang').value;const sel=$('seasonLang');sel.innerHTML=opts.map(l=>`<option value="${esc(l)}"${l===cur?' selected':''}>${esc(l)}</option>`).join('');applySeasonLang(cur||opts[0])}catch(e){alert('Não foi possível detectar os idiomas: '+e.message)}}
async function start(){try{const sel=episodes.filter(ep=>selected().includes(ep.source)&&ep.status!=='ALREADY_TRANSLATED');if(!sel.length)return alert('Selecione pelo menos um episódio sem PT-BR.');const source_languages={};sel.forEach(ep=>{const key=episodeKey(ep);const el=langSelects[key];source_languages[ep.source]=el?el.value:globalSourceLang});await api('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:selectedFolder,episodes:sel.map(ep=>ep.source),source_languages,dry_run:$('dryrun').checked})});await refresh()}catch(e){alert(e.message)}}
async function auditSelectedSeason(){try{const series=await seriesForFolder();if(!series)return alert('A pasta atual não está associada a uma série ANIME catalogada.');const m=selectedFolder.match(/(?:^|\/)Season\s*([0-9]+)/i);const body=m?{season:m[1]}:{};const d=await api('/audit/series/'+series.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});alert(`Auditoria concluída: ${d.counts['PROBLEMAS DETECTADOS']||0} com problemas, ${d.counts['REVISÃO RECOMENDADA']||0} para revisão, ${d.counts['AUDITORIA PARCIAL']||0} parciais.`);await loadEpisodes();await loadArchive()}catch(e){alert(e.message)}}
async function retranslate(ids,confirmBatch=false){try{if(!ids.length)return alert('Selecione episódios com fonte original arquivada.');if(confirmBatch){const preview=await api('/retranslate/preflight',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({episode_ids:ids,bulk:true})});const c=preview.counts||{};if(c.blocked){return alert(`Pré-flight bloqueado: ${c.blocked} episódio(s) sem fonte compatível. Nenhum job foi criado.`)}if(!confirm(`Retraduzir ${c.eligible||0} episódio(s) com ${c.skipped_current_validated||0} já atual(is) ignorado(s)? A regra é parar na primeira falha. A versão antiga será preservada e nada será publicado automaticamente.`))return;}await api('/retranslate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({episode_ids:ids,confirm:confirmBatch,bulk:confirmBatch})});await refresh()}catch(e){alert(e.message)}}
async function seriesForFolder(){const d=await api('/library/series?classification=ANIME');return d.series.find(s=>selectedFolder===s.library_relative_path||selectedFolder.startsWith(s.library_relative_path+'/'))}
async function action(url){try{await api(url,{method:'POST'});await refresh()}catch(e){alert(e.message)}}
function age(iso){if(!iso)return '—';const t=Date.parse(iso);if(!Number.isFinite(t))return esc(iso);const sec=Math.max(0,Math.floor((Date.now()-t)/1000));if(sec<60)return `há ${sec}s`;const min=Math.floor(sec/60);if(min<60)return `há ${min}min`;return `há ${Math.floor(min/60)}h ${min%60}min`}
function duration(sec){if(sec==null)return '—';sec=Math.max(0,Math.round(Number(sec)));const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`:`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`}
function renderStatus(d){statusData=d;const q=d.queue||{};$('doneCount').textContent=`${q.completed||0}/${q.total||0}`;$('waitCount').textContent=q.waiting||0;$('runCount').textContent=q.running||0;$('failCount').textContent=q.failed||0;$('skipCount').textContent=q.skipped||0;$('notStartedAfterFailureCount').textContent=q.not_started_after_failure||0;const cur=d.current_job;$('currentTitle').textContent=cur?`${cur.name} · ${cur.stage||cur.status}`:'Nenhum episódio em execução.';const t=cur||{};const total=t.total_units,resolved=t.resolved_units??0;const pct=total?Math.min(100,Math.round(100*resolved/total)):0;$('progressBar').style.width=pct+'%';const progressText=total!=null?`Unidades processadas: ${resolved}/${total}`:'PREPARANDO — total ainda não calculado';$('currentMeta').textContent=cur?`${progressText}${t.current_event_id!=null?` · unidade atual ${esc(t.current_event_id)}`:''}`:'';const details=cur?[`<span>Chamadas: <b>${t.calls??0}</b></span>`,`<span>Retries: <b>${t.retries??0}</b></span>`,t.retry_budget_total!=null?`<span>Budget: <b>${t.retry_budget_used??0}/${t.retry_budget_total}</b></span>`:'',`<span>Tempo: <b>${duration(t.elapsed_seconds)}</b></span>`,`<span>Última atividade: <b>${age(t.last_activity_at)}</b></span>`].filter(Boolean).join(' · '):'';$('currentTelemetry').innerHTML=details;if(cur&&cur.status==='FAILED'){const event=cur.current_event_id!=null?` · evento/unidade ${esc(cur.current_event_id)}`:'';$('currentTelemetry').innerHTML+=`<div class="note" style="margin-top:6px">Falha: ${esc(cur.reason||cur.error||'resultado reprovado')}${event}</div>`}const interrupted=d.bulk_stop_reason==='STOPPED_ON_FAILURE'?'<div class="note">Temporada interrompida após a primeira falha. Os episódios restantes não foram iniciados.</div>':'';$('queueList').innerHTML=interrupted+(d.jobs||[]).filter(j=>['WAITING','FAILED','COMPLETED','SKIPPED_CURRENT_VALIDATED','NOT_STARTED_AFTER_FAILURE'].includes(j.status)).map(j=>`<div class="jobline"><span>${esc(j.episode||j.name)}</span>${badge(j.status)}</div>`).join('');$('pauseBtn').disabled=!d.running||d.pause_requested;$('resumeBtn').disabled=!d.queue_paused;$('stopBtn').disabled=!d.running&&!q.waiting;$('retryBtn').disabled=!!d.running||!(q.failed);$('startBtn').disabled=!!d.running||!!q.waiting}
async function refresh(){if(refreshInFlight)return;refreshInFlight=true;try{try{const d=await api('/status?after='+cursor);cursor=d.last_log_id||cursor;renderStatus(d);(d.log_details||d.log||[]).forEach(x=>{if(x.id!=null&&renderedLogIds.has(x.id))return;if(x.id!=null)renderedLogIds.add(x.id);const line=document.createElement('div');line.className=x.level||'';line.dataset.logId=x.id??'';line.textContent=`${x.time||''} ${x.line}`;$('logs').append(line)});$('logs').scrollTop=$('logs').scrollHeight}catch(e){console.error('status',e)}if(selectedFolder){try{await loadEpisodes()}catch(e){$('libraryNote').textContent='Episódios temporariamente indisponíveis.';$('libraryNote').classList.remove('hidden');console.error('episodes',e)}}}finally{refreshInFlight=false}}
async function loadHealth(){try{const d=await api('/health');$('serviceChip').textContent=d.status==='ok'?'Serviço online':'Serviço indisponível';$('serviceChip').classList.toggle('online',d.status==='ok')}catch(e){$('serviceChip').textContent='Serviço indisponível';$('serviceChip').classList.remove('online')}}
async function loadPipeline(){try{const d=await api('/pipeline');$('pipelineChip').textContent=d.pipeline_label;$('modelChip').textContent=d.model}catch(e){$('pipelineChip').textContent='Pipeline indisponível';$('modelChip').textContent='Modelo indisponível'}await loadHealth()}
async function loadTransportConfig(){try{const d=await api('/transport-config');const p=d.primary||{};const f=d.fallback||{};globalSourceLang=d.source_language||'inglês';if($('seasonLang'))$('seasonLang').value=globalSourceLang;$('motorChip').textContent=(p.provider||'?')+(f&&f.provider?` + ${f.provider}`:'')}catch(e){$('motorChip').textContent='Motor indisponível'}}
async function openTransportConfig(){try{const d=await api('/transport-config');const p=d.primary||{},f=d.fallback||{};$('tcSourceLanguage').value=d.source_language||'inglês';$('tcPrimaryProvider').value=p.provider||'ollama';$('tcPrimaryModel').value=p.model||'';$('tcPrimaryBaseUrl').value=p.base_url||'';$('tcFallbackProvider').value=f?f.provider:'';$('tcFallbackModel').value=f?f.model:'';$('tcFallbackBaseUrl').value=f?f.base_url||'':'';const kc=d.keys_configured||{};let html='';for(const prov of ['ollama','openai_compat','gemini']){if(prov==='ollama')continue;html+=`<label>Key ${prov}${kc[prov]?' <span class="badge">configurada</span>':''}</label><input id="tcKey_${prov}" type="password" placeholder="${kc[prov]?'deixe vazio para manter':'cole a API key'}" style="width:100%;margin:4px 0">`}$('tcKeys').innerHTML=html;$('tcStatus').textContent='';const dialog=$('transportConfigDialog');if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','')}catch(e){alert('Não foi possível carregar a configuração de motor.')}}
async function saveTransportConfig(){const keys={};for(const prov of ['openai_compat','gemini']){const el=$('tcKey_'+prov);if(el&&el.value.trim())keys[prov]=el.value.trim()}const payload={primary:{provider:$('tcPrimaryProvider').value,model:$('tcPrimaryModel').value.trim(),base_url:$('tcPrimaryBaseUrl').value.trim()||null},fallback:null,keys,source_language:$('tcSourceLanguage').value.trim()||'inglês'};if($('tcFallbackProvider').value){payload.fallback={provider:$('tcFallbackProvider').value,model:$('tcFallbackModel').value.trim(),base_url:$('tcFallbackBaseUrl').value.trim()||null}}try{const r=await api('/transport-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});$('tcStatus').textContent='Salvo ✓';$('transportConfigDialog').close?.();$('transportConfigDialog').removeAttribute('open');await loadTransportConfig()}catch(e){$('tcStatus').textContent='Erro: '+(e.message||e)}}
$('openTransportConfig').onclick=openTransportConfig;$('closeTransportConfig').onclick=()=>{$('transportConfigDialog').close?.();$('transportConfigDialog').removeAttribute('open')};$('tcSave').onclick=saveTransportConfig;
async function loadHistory(){try{const d=await api('/history?technical='+($('showTechnical').checked?'1':'0'));$('history').innerHTML=d.history.length?d.history.slice().reverse().map(x=>`<div class="jobline"><span>${esc(x.folder||'')}<br><small>${esc(x.finished_at||x.created_at||'')}</small></span><span>${x.completed||0} concluídos · ${x.failed||0} falhos</span></div>`).join(''):'Nenhuma sessão registrada.'}catch(e){}}
async function openArchiveSeries(id){try{const x=await api('/library/series/'+id+'?source_status=1');$('archiveDetails').innerHTML=`<div class="note"><b>${esc(x.title)}</b> · ${esc(x.classification)}<br><small>${x.episodes.length} episódios catalogados · versões agrupadas por episódio</small></div>`+x.episodes.map(ep=>{const rs=ep.records||[],pref=rs.find(r=>String(r.id)===String(ep.preferred_record_id))||rs.find(r=>r.preferred)||rs[0],audit=ep.audit?.status||'NÃO AUDITADA',source=ep.source_status||{};const sourceText=source.display||source.reason||'Fonte não encontrada';const versions=rs.map(r=>`<div class="jobline"><span><b>${esc(r.pipeline_version||'pipeline desconhecido')}</b> · ${esc(r.language)} · <span class="badge">${esc(r.source_kind)}</span> <span class="badge">${esc(r.validation_status||'—')}</span> <span class="badge">${esc(r.review_status||'—')}</span></span><span class="record-actions"><a class="button" href="/library/records/${r.id}/download">Baixar</a><a class="button" href="/review/${r.id}">Revisar</a><a class="button" href="/library/records/${r.id}">Detalhes</a></span></div>`).join('');const compare=rs.length>1?`<button class="button" data-compare-episode="${ep.id}" data-old-id="${rs[rs.length-1].id}" data-new-id="${rs[0].id}">Comparar versões</button>`:'';return `<details class="jobline" style="display:block"><summary><b>${esc(ep.season||'')}${esc(ep.episode||'')}</b> ${esc(ep.episode_title||ep.media_filename)} · <span class="badge">${rs.length} versão(ões)</span> <span class="badge">${esc(audit)}</span> <span class="badge" title="${esc(source.reason||sourceText)}">${source.available?'✓ fonte disponível':esc(source.status||'fonte')}</span></summary><div class="record-actions" style="justify-content:flex-start;margin:8px 0"><button class="button" data-audit-episode="${ep.id}">Auditar</button>${pref&&ep.retranslation_available?`<button class="button warn" data-retranslate-episode="${ep.id}" title="${esc(sourceText)}">Retraduzir</button>`:''}${compare}<a class="button" href="${pref?`/review/${pref.id}`:'#'}" ${pref?'':'aria-disabled="true"'}>Revisar</a></div>${!source.available?`<div class="muted" style="margin:5px 0">Fonte: ${esc(sourceText)}</div>`:''}${versions||'<div class="muted">Sem legenda arquivada.</div>'}</details>`}).join('');$('archiveDetails').querySelectorAll('[data-audit-episode]').forEach(b=>b.onclick=async()=>{try{await api('/audit/episodes/'+b.dataset.auditEpisode,{method:'POST'});await openArchiveSeries(id)}catch(e){alert(e.message)}});$('archiveDetails').querySelectorAll('[data-retranslate-episode]').forEach(b=>b.onclick=()=>retranslate([Number(b.dataset.retranslateEpisode)],false));$('archiveDetails').querySelectorAll('[data-compare-episode]').forEach(b=>b.onclick=async()=>{try{const d=await api('/library/records/compare?old_id='+b.dataset.oldId+'&new_id='+b.dataset.newId);alert(`${d.changed_count||0} evento(s) alterado(s). Veja a comparação técnica no endpoint da Biblioteca.`)}catch(e){alert(e.message)}})}catch(e){$('archiveDetails').textContent='Detalhe indisponível';}}
async function loadArchive(){try{const d=await api('/library');const c=d.counts||{};$('libraryStats').textContent=`${c.records||0} versões · ${c.objects||0} objetos · ${c.publications||0} publicados`;
const s=await api('/library/series?classification=ANIME');$('archiveSeries').innerHTML=s.series.length?s.series.map(x=>`<div class="jobline"><span><b>${esc(x.title)}</b><br><small>${esc(x.library_relative_path||'')} · ${esc(x.classification)}</small></span><button class="button" data-library-series="${x.id}">Abrir</button></div>`).join(''):'Nenhuma série anime catalogada.';$('archiveSeries').querySelectorAll('[data-library-series]').forEach(b=>b.onclick=()=>openArchiveSeries(b.dataset.librarySeries));}catch(e){$('archiveSeries').textContent='Biblioteca indisponível';}}
async function loadMemory(){try{const d=await api('/memory');const c=d.counts||{};$('memoryStats').textContent=`${c.active||0} ativas · ${c.items||0} históricas · ${c.conflicts||0} conflitos · ${c.usages||0} usos · somente SEGMENT_APPROVED`;$('memoryItems').innerHTML=(d.items||[]).map(x=>`<div class="jobline"><span><b>${esc(x.source)}</b> → ${esc(x.approved_text)}</span><span>${esc(x.anime_title||'Anime')} · ${esc(x.status)} · ${x.usage_count||0} usos <button class="button" data-memory-status="${x.id}" data-next-status="${x.status==='ACTIVE'?'INACTIVE':'ACTIVE'}">${x.status==='ACTIVE'?'Desativar':'Ativar'}</button></span></div>`).join('')||'Nenhuma memória materializada.';$('memoryItems').querySelectorAll('[data-memory-status]').forEach(b=>b.onclick=async()=>{try{await api('/memory/items/'+b.dataset.memoryStatus+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:b.dataset.nextStatus})});await loadMemory()}catch(e){alert(e.message)}})}catch(e){$('memoryStats').textContent='Memória indisponível';$('memoryItems').textContent='Não foi possível carregar a memória local.'}}
 $('enterBtn').onclick=()=>{const v=$('folderSelect').value;if(v){path=path?path+'/'+v:v;loadBrowse()}};$('upBtn').onclick=()=>{path=path.split('/').slice(0,-1).join('/');loadBrowse()};$('useBtn').onclick=()=>{if(selectionFolder!==path){selectedEpisodeKeys.clear();selectionFolder=path}selectedFolder=path;loadEpisodes()};$('selectMissing').onclick=()=>{episodes.forEach(ep=>{if(!ep.ptbr)selectedEpisodeKeys.add(episodeKey(ep))});renderEpisodes()};$('selectLegacy').onclick=async()=>{try{const s=await seriesForFolder();if(!s)return alert('Série não catalogada como ANIME.');const d=await api('/library/legacy?series_id='+s.id);episodes.forEach(ep=>{if(d.episode_ids.includes(ep.library_episode_id))selectedEpisodeKeys.add(episodeKey(ep))});renderEpisodes()}catch(e){alert(e.message)}};$('clearSelection').onclick=()=>{selectedEpisodeKeys.clear();renderEpisodes()};$('seasonLang').onchange=()=>applySeasonLang($('seasonLang').value);$('detectSeasonLang').onclick=detectSeasonLang;$('startBtn').onclick=start;$('retranslateSelectedBtn').onclick=()=>retranslate(selectedEpisodeIds(),false);$('auditSeasonBtn').onclick=auditSelectedSeason;$('retranslateSeasonBtn').onclick=async()=>{try{const ids=episodes.map(ep=>ep.library_episode_id).filter(Boolean).map(Number);await retranslate(ids,true)}catch(e){alert(e.message)}};$('pauseBtn').onclick=()=>action('/pause');$('resumeBtn').onclick=()=>action('/resume');$('stopBtn').onclick=()=>{if(confirm('Parar a fila? O episódio atual terminará/cancelará com segurança.'))action('/stop')};$('retryBtn').onclick=()=>action('/retry-failed');$('showTechnical').onchange=loadHistory;loadPipeline();loadTransportConfig();loadBrowse();loadHistory();loadHealth();setInterval(()=>{refresh();loadHistory()},2000);setInterval(loadHealth,5000);
loadArchive();loadMemory();$('memorySync').onclick=async()=>{try{await api('/memory/sync',{method:'POST'});await loadMemory()}catch(e){alert(e.message)}};setInterval(()=>{loadArchive();loadMemory()},5000);

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
<title>Revisão humana · Subtranslate</title>
<style>
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--line:#30363d;--text:#e6edf3;--muted:#9da7b3;--green:#238636;--red:#8e1519;--blue:#1f6feb}*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#0d1117,#111827);color:var(--text);font:15px/1.45 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:20px 14px 48px}header{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:16px}h1{font-size:1.35rem;margin:0}.muted{color:var(--muted)}.panel{background:rgba(22,27,34,.95);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:14px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.button,button{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:#21262d;color:var(--text);cursor:pointer;min-height:38px}.button.primary{background:var(--green)}.button.approve{background:var(--blue)}.button.reject{background:var(--red)}button:disabled{opacity:.45;cursor:not-allowed}select,input,textarea{background:#0d1117;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:8px;font:inherit}input,textarea{width:100%}textarea{min-height:78px;resize:vertical}.segment{border:1px solid var(--line);border-radius:10px;padding:12px;margin:9px 0;background:#111820}.segment-head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.cols{display:grid;grid-template-columns:1fr 1fr;gap:10px}.box{border:1px solid var(--line);border-radius:7px;padding:9px;white-space:pre-wrap;overflow:auto}.source{border-left:3px solid #d29922}.generated{border-left:3px solid #58a6ff}.context{font-size:.88rem;color:var(--muted);margin:7px 0}.badge{border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:.78rem}.ok{color:#b7f5c0}.warn{color:#f2cc60}.fail{color:#ffaba8}@media(max-width:760px){.cols{grid-template-columns:1fr}main{padding:12px 9px 36px}}
</style></head><body><main>
<header><div><h1>Revisão humana</h1><div class="muted">Somente conteúdo linguístico · sem chamadas ao Qwen</div></div><div id="meta" class="muted">Carregando…</div></header>
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
