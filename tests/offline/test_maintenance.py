"""Deterministic safety tests for state retention."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from maintenance import RetentionPolicy, apply_report, scan_state


def _old(path: Path, *, days: int = 60, directory: bool = True) -> Path:
    if directory:
        path.mkdir(parents=True, exist_ok=True)
        marker = path / "generated.txt"
        marker.write_text("generated\n", encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")
    timestamp = time.time() - days * 86400
    os.utime(path, (timestamp, timestamp))
    return path


def _state(tmp_path: Path, *, jobs: list[dict] | None = None) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    (state / "jobs.json").write_text(
        json.dumps({"jobs": jobs or []}), encoding="utf-8"
    )
    return state


def test_scan_finds_only_expired_generated_artifacts(tmp_path: Path) -> None:
    state = _state(
        tmp_path,
        jobs=[
            {"id": "active", "status": "TRANSLATING"},
            {"id": "referenced-run", "status": "COMPLETED"},
        ],
    )
    old_run = _old(state / "v238-runs" / "unreferenced-run")
    _old(state / "v238-runs" / "referenced-run")
    recent_run = state / "v238-runs" / "recent-run"
    recent_run.mkdir(parents=True)
    _old(state / "staging" / "orphan-staging")
    (state / "staging" / "recent-staging").mkdir(parents=True)
    _old(state / ".jobs-stale", directory=False)
    _old(state / "transport_config.json.bak-old", directory=False)
    ledger = state / "failure-ledger" / "jobs"
    for job_id in ("one", "two", "three"):
        (ledger / job_id).mkdir(parents=True)
    for index, child in enumerate(sorted(ledger.iterdir())):
        os.utime(child, (time.time() - index, time.time() - index))

    report = scan_state(
        state,
        RetentionPolicy(
            run_days=30,
            staging_days=7,
            transient_hours=24,
            failure_ledger_jobs=1,
            config_backups=1,
        ),
    )

    paths = {Path(item.path) for item in report.candidates}
    assert old_run in paths
    assert state / "v238-runs" / "referenced-run" not in paths
    assert recent_run not in paths
    assert state / "staging" / "orphan-staging" in paths
    assert state / ".jobs-stale" in paths
    assert state / "failure-ledger" / "jobs" / "one" not in paths
    assert len([item for item in report.candidates if item.kind == "failure-ledger"]) == 2
    assert report.service_busy is True
    assert report.active_job_ids == ("active",)


def test_scan_does_not_delete_or_candidate_media_or_library(tmp_path: Path) -> None:
    state = _state(tmp_path)
    media = state / "anime-subtitle-library"
    media.mkdir()
    _old(media / "episode.pt-BR.ass", directory=False)
    (state / "glossary_v1.json").write_text("{}", encoding="utf-8")

    report = scan_state(state)

    assert report.candidates == ()


def test_apply_removes_candidates_when_service_is_idle(tmp_path: Path) -> None:
    state = _state(tmp_path)
    transient = _old(state / ".jobs-old", directory=False)
    report = scan_state(state, RetentionPolicy(transient_hours=1))

    assert apply_report(report) == 1
    assert not transient.exists()
    assert (state / "jobs.json").exists()


def test_apply_refuses_active_service_and_outside_target(tmp_path: Path) -> None:
    state = _state(tmp_path, jobs=[{"id": "job", "status": "PUBLISHING"}])
    _old(state / ".jobs-old", directory=False)
    report = scan_state(state, RetentionPolicy(transient_hours=1))

    with pytest.raises(RuntimeError, match="tradução em execução"):
        apply_report(report)

    from maintenance import MaintenanceCandidate, MaintenanceReport

    unsafe = MaintenanceReport(
        state_dir=str(state),
        generated_at=report.generated_at,
        service_busy=False,
        active_job_ids=(),
        candidates=(
            MaintenanceCandidate(
                kind="transient",
                path=str(tmp_path / "outside"),
                size_bytes=0,
                modified_at=report.generated_at,
                reason="test",
            ),
        ),
    )
    with pytest.raises(ValueError, match="fora do state dir"):
        apply_report(unsafe)
