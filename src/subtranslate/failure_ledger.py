"""Bounded, append-friendly execution evidence for translation jobs.

This module is deliberately outside the linguistic engine.  It records the
inputs/outputs already produced by a runner and never changes a prompt,
classifier, retry decision, or validator result.  A job gets its own
immutable directory so a later run cannot overwrite an earlier diagnosis.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_TEXT = int(os.environ.get("TRANSLATOR_FAILURE_LEDGER_MAX_TEXT", "20000"))
MAX_RESPONSE = int(os.environ.get("TRANSLATOR_FAILURE_LEDGER_MAX_RESPONSE", "50000"))
DEFAULT_MAX_JOBS = int(os.environ.get("TRANSLATOR_FAILURE_LEDGER_MAX_JOBS", "20"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _safe(value: Any, *, limit: int = MAX_TEXT, key: str = "") -> Any:
    """Make JSON evidence bounded and remove credential-like fields."""
    lowered = key.lower()
    if any(token in lowered for token in ("password", "token", "authorization", "cookie", "secret", "credential", "api_key", "apikey")):
        return "[REDACTED]"
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"…[truncated; sha256={_sha(value)}]"
    if isinstance(value, dict):
        return {str(k): _safe(v, limit=limit, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item, limit=limit) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_safe(value, limit=MAX_RESPONSE), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        try:
            os.unlink(raw)
        except FileNotFoundError:
            pass


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_safe(value, limit=MAX_RESPONSE), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class FailureLedger:
    """Persistent per-job ledger used by V2.2.4 observability hooks."""

    def __init__(self, job_id: str, metadata: dict[str, Any], root: str | Path | None = None):
        self.job_id = str(job_id)
        base = Path(root or os.environ.get("TRANSLATOR_FAILURE_LEDGER_ROOT", "/app/state/failure-ledger"))
        self.root = base
        self.job_dir = base / "jobs" / self.job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.metadata = _safe(dict(metadata))
        self._source_path: Path | None = None
        self._started = _now()
        _write_json(self.job_dir / "manifest.json", {
            "schema": "failure-ledger-manifest-v1",
            "job_id": self.job_id,
            "started_at": self._started,
            "status": "RUNNING",
            "metadata": self.metadata,
            "retention_max_jobs": int(os.environ.get("TRANSLATOR_FAILURE_LEDGER_MAX_JOBS", DEFAULT_MAX_JOBS)),
        })

    @property
    def path(self) -> str:
        return str(self.job_dir)

    def set_source(self, source: str | Path) -> None:
        path = Path(source)
        try:
            path = path.resolve()
        except OSError:
            pass
        self._source_path = path
        _write_json(self.job_dir / "source-reference.json", {
            "path": str(path),
            "exists": path.is_file(),
            "sha256": _sha(path.read_bytes()) if path.is_file() else None,
            "size": path.stat().st_size if path.is_file() else None,
        })

    def record_call(self, observation: dict[str, Any], *, budget_before: dict[str, Any] | None = None, budget_after: dict[str, Any] | None = None) -> None:
        item = dict(observation)
        item["attempt_id"] = item.get("call_id") or f"attempt-{self.job_id}-{Path(self.job_dir).stat().st_mtime_ns}"
        item["input_hash"] = _sha(item.get("response_content") or item.get("call_id"))
        if isinstance(item.get("response_content"), str):
            item["raw_response_hash"] = _sha(item["response_content"])
            item["response_content"] = item["response_content"][:MAX_RESPONSE]
        if budget_before is not None:
            item["retry_budget_before"] = budget_before
        if budget_after is not None:
            item["retry_budget_after"] = budget_after
        _append_jsonl(self.job_dir / "attempts.jsonl", item)

    @staticmethod
    def _budget(runner: Any) -> dict[str, Any]:
        budget = getattr(runner, "retry_budget", None)
        if budget is None:
            return {"configured": None, "consumed": None, "remaining": None, "max_depth": None}
        return {
            "configured": int(budget.consumed + budget.remaining),
            "consumed": int(budget.consumed),
            "remaining": int(budget.remaining),
            "exhausted": bool(budget.exhausted),
            "max_depth": int(budget.max_depth),
            "last_reason": budget.last_reason,
        }

    def _event_record(self, runner: Any, event: Any, *, failed_only: bool = False, result_override: dict[str, Any] | None = None) -> dict[str, Any]:
        result = getattr(runner, "results", {}).get(event.id)
        result_dict = result_override or (result if isinstance(result, dict) else getattr(result, "__dict__", {}))
        failed = result_dict.get("status") != "resolved"
        include_source = (not failed_only) or failed
        context = getattr(runner, "contexts", {}).get(event.id, {}) or {}
        record: dict[str, Any] = {
            "event_id": event.id,
            "semantic_unit_id": f"event-{event.id}",
            "original_index": event.original_index,
            "timestamp": {"start_ms": event.start, "end_ms": event.end},
            "style": event.style,
            "name": event.name,
            "effect": event.effect,
            "classification": event.classification,
            "source_linguistic": event.clean_text if include_source else None,
            "source_linguistic_sha256": _sha(event.clean_text),
            "source_raw": event.original_text if include_source else None,
            "source_raw_sha256": _sha(event.original_text),
            "context_ids": {
                "previous": [item.get("id") for item in context.get("previous", []) if isinstance(item, dict)],
                "next": [item.get("id") for item in context.get("next", []) if isinstance(item, dict)],
            },
            "status": result_dict.get("status"),
            "attempt_count": result_dict.get("retry_count", 0),
            "retry_history": result_dict.get("attempts", []),
            "final_output": result_dict.get("final_text") if include_source else None,
            "final_output_sha256": _sha(result_dict.get("final_text")),
            "flags": result_dict.get("flags", []),
            "failure_reason": result_dict.get("failure_reason", ""),
            "updated_at": _now(),
        }
        if failed:
            record["context_before"] = context.get("previous", [])
            record["context_after"] = context.get("next", [])
        return _safe(record)

    def register_runner(self, runner: Any) -> None:
        events = getattr(runner, "events", None) or getattr(runner, "v221_original_events", [])
        for event in events:
            _append_jsonl(self.job_dir / "units.jsonl", self._event_record(runner, event))

    def sync_runner(self, runner: Any, summary: dict[str, Any] | None = None) -> None:
        events = getattr(runner, "events", None) or getattr(runner, "v221_original_events", [])
        result_map = {item.get("id"): item for item in (summary or {}).get("results", []) if isinstance(item, dict)}
        records = [self._event_record(runner, event, result_override=result_map.get(event.id)) for event in events]
        _write_json(self.job_dir / "units.json", records)
        for event in events:
            _append_jsonl(self.job_dir / "units.jsonl", self._event_record(runner, event, failed_only=True, result_override=result_map.get(event.id)))

    def record_unit_update(self, runner: Any, event: Any) -> None:
        """Persist the latest state without duplicating the full ASS file."""
        _append_jsonl(self.job_dir / "unit-updates.jsonl", self._event_record(runner, event))

    def snapshot(self, runner: Any, summary: dict[str, Any] | None, *, stage: str, error: str | None = None, blocking: bool = True) -> str:
        self.sync_runner(runner, summary)
        snapshot: dict[str, Any] = {
            "schema": "failure-diagnostic-snapshot-v1",
            "job_id": self.job_id,
            "created_at": _now(),
            "status": "FAILED" if blocking else "COMPLETED",
            "stage_of_failure": stage,
            "error": error,
            "metadata": self.metadata,
            "retry_budget": self._budget(runner),
            "summary": summary,
            "ledger_dir": self.path,
            "attempts_file": str(self.job_dir / "attempts.jsonl"),
            "units_file": str(self.job_dir / "units.json"),
            "source_reference": str(self.job_dir / "source-reference.json"),
            "retention": {"max_jobs": int(os.environ.get("TRANSLATOR_FAILURE_LEDGER_MAX_JOBS", DEFAULT_MAX_JOBS))},
        }
        if self._source_path and self._source_path.is_file():
            destination = self.job_dir / "source.ass"
            if not destination.exists():
                shutil.copy2(self._source_path, destination)
            snapshot["source_snapshot"] = {"path": str(destination), "sha256": _sha(destination.read_bytes())}
        _write_json(self.job_dir / "snapshot.json", snapshot)
        _write_json(self.job_dir / "manifest.json", {
            "schema": "failure-ledger-manifest-v1", "job_id": self.job_id,
            "started_at": self._started, "finished_at": _now(),
            "status": snapshot["status"], "stage_of_failure": stage,
            "snapshot": str(self.job_dir / "snapshot.json"), "metadata": self.metadata,
        })
        self.prune()
        return str(self.job_dir / "snapshot.json")

    def complete(self, runner: Any, summary: dict[str, Any] | None) -> str:
        self.sync_runner(runner, summary)
        result = self.snapshot(runner, summary, stage="completed", blocking=False)
        return result

    def prune(self) -> None:
        jobs_root = self.root / "jobs"
        if not jobs_root.exists():
            return
        max_jobs = max(1, int(os.environ.get("TRANSLATOR_FAILURE_LEDGER_MAX_JOBS", DEFAULT_MAX_JOBS)))
        jobs = sorted((item for item in jobs_root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in jobs[max_jobs:]:
            shutil.rmtree(old, ignore_errors=True)


def retain_staging(staging_root: str | Path, ledger_dir: str | Path) -> str | None:
    source = Path(staging_root)
    if not source.exists():
        return None
    destination = Path(ledger_dir) / "staging"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    shutil.move(str(source), str(destination))
    return str(destination)
