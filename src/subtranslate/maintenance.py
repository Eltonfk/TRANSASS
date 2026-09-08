"""Safe, operator-driven retention for Transass runtime artifacts.

The command is intentionally dry-run by default.  It only considers generated
runtime artifacts under the state directory; media, subtitle objects, the
SQLite library, glossary and transport configuration are never targets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ACTIVE_JOB_STATES = {
    "WAITING",
    "STARTING",
    "TRANSLATING",
    "VALIDATING",
    "PUBLISHING",
    "PAUSING",
}
TRANSIENT_NAMES = (".jobs-*", ".jobs.json.reconcile-*")


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention windows for generated runtime artifacts."""

    run_days: int = 30
    staging_days: int = 7
    transient_hours: int = 24
    failure_ledger_jobs: int = 20
    config_backups: int = 5

    def __post_init__(self) -> None:
        if self.run_days < 1 or self.staging_days < 1 or self.transient_hours < 1:
            raise ValueError("janelas de retenção devem ser positivas")
        if self.failure_ledger_jobs < 1 or self.config_backups < 1:
            raise ValueError("limites de retenção devem ser positivos")

    @classmethod
    def from_environment(cls) -> "RetentionPolicy":
        def integer(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            run_days=integer("TRANSASS_RETENTION_RUN_DAYS", 30),
            staging_days=integer("TRANSASS_RETENTION_STAGING_DAYS", 7),
            transient_hours=integer("TRANSASS_RETENTION_TRANSIENT_HOURS", 24),
            failure_ledger_jobs=integer(
                "TRANSASS_RETENTION_FAILURE_LEDGER_JOBS",
                integer("TRANSLATOR_FAILURE_LEDGER_MAX_JOBS", 20),
            ),
            config_backups=integer("TRANSASS_RETENTION_CONFIG_BACKUPS", 5),
        )


@dataclass(frozen=True)
class MaintenanceCandidate:
    """One generated artifact eligible for removal under a policy."""

    kind: str
    path: str
    size_bytes: int
    modified_at: str
    reason: str


@dataclass(frozen=True)
class MaintenanceReport:
    """Read-only result of a retention scan."""

    state_dir: str
    generated_at: str
    service_busy: bool
    active_job_ids: tuple[str, ...]
    candidates: tuple[MaintenanceCandidate, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state_dir": self.state_dir,
            "generated_at": self.generated_at,
            "service_busy": self.service_busy,
            "active_job_ids": list(self.active_job_ids),
            "candidate_count": len(self.candidates),
            "candidate_bytes": self.total_bytes,
            "candidates": [asdict(item) for item in self.candidates],
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if path.is_dir() and not path.is_symlink():
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    return total


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def _load_state(state_dir: Path) -> dict[str, Any]:
    path = state_dir / "jobs.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _job_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = payload.get("jobs", [])
    return [item for item in jobs if isinstance(item, dict)] if isinstance(jobs, list) else []


def _active_job_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    active = []
    for job in _job_list(payload):
        if str(job.get("status", "")).upper() in ACTIVE_JOB_STATES:
            if job.get("id") is not None:
                active.append(str(job["id"]))
    return tuple(sorted(set(active)))


def _referenced_job_ids(payload: dict[str, Any]) -> set[str]:
    """Return job IDs still represented in the durable jobs projection."""

    result = set()
    for job in _job_list(payload):
        if job.get("id") is not None:
            result.add(str(job["id"]))
    return result


def _candidate(kind: str, path: Path, reason: str) -> MaintenanceCandidate:
    stat = path.stat()
    return MaintenanceCandidate(
        kind=kind,
        path=str(path),
        size_bytes=_size(path),
        modified_at=_timestamp(stat.st_mtime),
        reason=reason,
    )


def _direct_children(path: Path, *, directories: bool | None = None) -> Iterable[Path]:
    if not path.is_dir():
        return ()
    try:
        children = tuple(path.iterdir())
    except OSError:
        return ()
    return tuple(
        child
        for child in children
        if not child.is_symlink()
        and (directories is None or child.is_dir() == directories)
    )


def scan_state(state_dir: str | Path, policy: RetentionPolicy | None = None) -> MaintenanceReport:
    """Scan generated artifacts without modifying the state directory."""

    root = Path(state_dir).expanduser().resolve()
    policy = policy or RetentionPolicy.from_environment()
    payload = _load_state(root)
    active_ids = _active_job_ids(payload)
    referenced_ids = _referenced_job_ids(payload)
    busy = bool(active_ids)
    current = time.time()
    candidates: list[MaintenanceCandidate] = []

    for pattern in TRANSIENT_NAMES:
        for path in root.glob(pattern):
            if path.is_file() and _older_than(path, current - policy.transient_hours * 3600):
                candidates.append(_candidate("transient", path, "arquivo temporário de persistência antigo"))

    runs_root = root / "v238-runs"
    run_cutoff = current - policy.run_days * 86400
    for path in _direct_children(runs_root, directories=True):
        if _older_than(path, run_cutoff) and path.name not in referenced_ids and path.name not in active_ids:
            candidates.append(_candidate("run", path, f"run sem referência com mais de {policy.run_days} dias"))

    staging_root = root / "staging"
    staging_cutoff = current - policy.staging_days * 86400
    for path in _direct_children(staging_root, directories=True):
        if _older_than(path, staging_cutoff) and not any(
            path.name in str(job) for job in _job_list(payload)
        ):
            candidates.append(_candidate("staging", path, f"staging sem referência com mais de {policy.staging_days} dias"))

    ledger_root = root / "failure-ledger" / "jobs"
    ledger_jobs = sorted(
        _direct_children(ledger_root, directories=True),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in ledger_jobs[policy.failure_ledger_jobs:]:
        if path.name not in active_ids:
            candidates.append(_candidate("failure-ledger", path, f"ledger além do limite de {policy.failure_ledger_jobs} jobs"))

    backups = sorted(
        (
            path
            for path in _direct_children(root, directories=False)
            if path.name.startswith("transport_config.json.bak-")
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in backups[policy.config_backups:]:
        candidates.append(_candidate("config-backup", path, f"backup antigo além dos {policy.config_backups} mais recentes"))

    return MaintenanceReport(
        state_dir=str(root),
        generated_at=_now().isoformat(),
        service_busy=busy,
        active_job_ids=active_ids,
        candidates=tuple(sorted(candidates, key=lambda item: item.path)),
    )


def apply_report(report: MaintenanceReport) -> int:
    """Apply a previously generated report after a final idle-state check.

    The caller must scan immediately before applying.  The guard refuses to
    mutate state while a job is active and revalidates every target beneath
    the state directory to avoid path traversal or stale-target surprises.
    """

    if report.service_busy:
        raise RuntimeError("limpeza bloqueada: há tradução em execução")
    root = Path(report.state_dir).resolve()
    removed = 0
    allowed_kinds = {"transient", "run", "staging", "failure-ledger", "config-backup"}
    for item in report.candidates:
        if item.kind not in allowed_kinds:
            raise ValueError(f"tipo de candidato não permitido: {item.kind}")
        target = Path(item.path).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise ValueError(f"alvo fora do state dir: {target}") from error
        if not target.exists() or target.is_symlink():
            continue
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed += 1
    return removed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Relatório/limpeza segura de artefatos temporários do Transass."
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--run-days", type=int)
    parser.add_argument("--staging-days", type=int)
    parser.add_argument("--transient-hours", type=int)
    parser.add_argument("--failure-ledger-jobs", type=int)
    parser.add_argument("--config-backups", type=int)
    parser.add_argument("--apply", action="store_true", help="remove os candidatos após confirmar o serviço ocioso")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    base = RetentionPolicy.from_environment()
    policy = RetentionPolicy(
        run_days=args.run_days if args.run_days is not None else base.run_days,
        staging_days=args.staging_days if args.staging_days is not None else base.staging_days,
        transient_hours=args.transient_hours if args.transient_hours is not None else base.transient_hours,
        failure_ledger_jobs=args.failure_ledger_jobs if args.failure_ledger_jobs is not None else base.failure_ledger_jobs,
        config_backups=args.config_backups if args.config_backups is not None else base.config_backups,
    )
    report = scan_state(args.state_dir, policy)
    if args.apply:
        if report.service_busy:
            print("limpeza bloqueada: há tradução em execução")
            return 2
        removed = apply_report(report)
        payload = report.as_dict()
        payload["applied"] = True
        payload["removed_count"] = removed
    else:
        payload = report.as_dict()
        payload["applied"] = False
        payload["dry_run"] = True
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "APLICADO" if args.apply else "DRY-RUN"
        print(f"Transass maintenance · {mode}")
        print(f"State: {report.state_dir}")
        print(f"Serviço ocupado: {'sim' if report.service_busy else 'não'}")
        print(f"Candidatos: {len(report.candidates)} · {_format_bytes(report.total_bytes)}")
        for item in report.candidates:
            print(f"- [{item.kind}] {item.path} · {_format_bytes(item.size_bytes)} · {item.reason}")
    return 0


def _format_bytes(value: int) -> str:
    number = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if number < 1024 or suffix == "GiB":
            return f"{number:.1f} {suffix}"
        number /= 1024
    return f"{value} B"


__all__ = ["MaintenanceCandidate", "MaintenanceReport", "RetentionPolicy", "apply_report", "main", "scan_state"]
