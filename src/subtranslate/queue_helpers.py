"""Pure candidate queue construction helpers."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Callable


def build_job_batch(
    sources: list[Path], *, session_id: str, folder: str, dry_run: bool,
    safe_relative: Callable[[Path], str], friendly_number: Callable[[str], str],
    now: Callable[[], str], id_factory: Callable[[], str] | None = None,
    source_languages: dict[str, str] | None = None,
) -> list[dict]:
    id_factory = id_factory or (lambda: uuid.uuid4().hex)
    source_languages = source_languages or {}
    jobs = []
    for source in sources:
        rel = safe_relative(source)
        jobs.append({
            "id": id_factory(), "session_id": session_id, "folder": folder,
            "source": rel, "source_abs": str(source),
            "name": source.name, "episode": friendly_number(source.name),
            "source_language": source_languages.get(rel) or source_languages.get(str(source)) or "inglês",
            "status": "WAITING", "created_at": now(), "started_at": None,
            "finished_at": None, "error": None, "reason": None,
            "summary": None, "progress": None, "flags": {}, "critical_flags": [],
            "attempt": 1, "retry_count": 0, "dry_run": dry_run,
        })
    ids = [job["id"] for job in jobs]
    if len(ids) != len(set(ids)):
        raise RuntimeError("candidate queue invariant violated: duplicate job id")
    return jobs
