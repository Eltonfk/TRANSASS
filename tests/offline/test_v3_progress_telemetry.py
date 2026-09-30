from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "subtranslate"))

import app as web  # noqa: E402


def test_v3_job_telemetry_and_public_shape():
    job = {
        "id": "v3-job-123",
        "name": "Series - S01E01.mkv",
        "status": "TRANSLATING",
        "stage": "TRANSLATING",
        "pipeline": "v3",
        "progress": {
            "scope": "episode",
            "current": 14,
            "total": 54,
            "label": "Lote 14/54 (Series - S01E01.mkv)",
        },
    }

    telemetry = web._job_telemetry(job)
    assert telemetry["total_units"] == 54
    assert telemetry["resolved_units"] == 14
    assert telemetry["stage"] == "TRANSLATING"

    public = web._public_job(job)
    assert public["total_units"] == 54
    assert public["resolved_units"] == 14
    assert public["progress"]["current"] == 14
    assert public["progress"]["total"] == 54
    assert public["progress"]["label"] == "Lote 14/54 (Series - S01E01.mkv)"
    assert public["progress"]["scope"] == "episode"


def test_v3_job_does_not_prematurely_switch_to_semantic_reconstruction():
    job = {
        "id": "v3-job-completed-batches",
        "name": "Series - S01E01.mkv",
        "status": "TRANSLATING",
        "stage": "TRANSLATING",
        "pipeline": "v3",
        "progress": {
            "scope": "episode",
            "current": 54,
            "total": 54,
            "label": "Lote 54/54 (Series - S01E01.mkv)",
        },
    }

    telemetry = web._job_telemetry(job)
    assert telemetry["total_units"] == 54
    assert telemetry["resolved_units"] == 54
    assert telemetry["stage"] == "TRANSLATING"
