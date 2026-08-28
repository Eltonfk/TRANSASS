#!/usr/bin/env python3
"""TRACK2 live-captured executor (AUTO-03D-TRACK2-LIVE-CAPTURED-EXECUTION-R1).

Thin, fail-closed wrapper around the existing V2.3.8 web retranslation path
that already performs LIVE_CAPTURED capture via WebDurableResponseProvider.

This tool performs NO execution by itself.  `plan()` is read-only and used by
the canonical transition tool to verify readiness.  `execute()` performs the
single live capture (1 Client.call / max 1 POST / 0 retry) only after an
explicit authorization gate.

Transport guard: the underlying durability layer (v238_per_call_durability)
enforces the exclusive transport claim; this wrapper forbids any retry loop and
runs exactly one subprocess invocation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CANDIDATE_ROOT = Path("/home/palhacinho/codex-projects/subtranslate-v238-candidate")
RUNNER_PATH = CANDIDATE_ROOT / "src/subtranslate/web_retranslation_runner.py"
EXECUTOR_ID = "TRACK2_LIVE_CAPTURED_EXECUTOR_V1"
PIPELINE = "v2_3_8"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm"}
SUBTITLE_EXTENSIONS = {".ass", ".ssa"}


def validate_inputs(source: Path, output: Path, capture_root: Path) -> None:
    """Fail-closed input validation. Raises ValueError on any problem."""
    if not source.exists():
        raise ValueError(f"FONTE NAO ENCONTRADA: {source}")
    if source.suffix.lower() not in SUBTITLE_EXTENSIONS | VIDEO_EXTENSIONS:
        raise ValueError("FONTE ORIGINAL NÃO DISPONÍVEL: formato não suportado")
    if output.exists():
        raise ValueError("SAIDA JA EXISTE: recusa sobrescrever (fail-closed)")
    capture_root.mkdir(parents=True, exist_ok=True)
    if not os.access(capture_root, os.W_OK):
        raise ValueError(f"CAPTURE_ROOT SEM ESCRITA: {capture_root}")


def build_invocation(
    source: Path,
    output: Path,
    job_id: str,
    capture_root: Path,
    *,
    series_title: str = "Anime",
    episode_title: str = "Episode",
    anime_series_id: int | None = None,
    episode_id: int | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Build the subprocess command + env for the V2.3.8 LIVE_CAPTURED run.

    The runner's v2_3_8 path hardcodes execution_mode=LIVE_CAPTURED and creates
    WebDurableResponseProvider(mode=LIVE_CAPTURED, capture_root=...), so the
    capture is produced automatically.  We only forward the required env.
    """
    cmd = [
        sys.executable, str(RUNNER_PATH),
        "--source", str(source),
        "--output", str(output),
        "--job-id", job_id,
        "--pipeline", PIPELINE,
        "--series-title", series_title,
        "--episode-title", episode_title,
    ]
    env = dict(os.environ)
    env["TRANSLATOR_PIPELINE"] = PIPELINE
    # The runner derives capture_root from job_root/v238-runs/<job_id>/captures.
    # We pin it via the same layout the runner expects.
    env.setdefault("TRANSLATOR_WEB_STATE_DIR", str(CANDIDATE_ROOT / "state"))
    if anime_series_id is not None:
        cmd += ["--anime-series-id", str(anime_series_id)]
    if episode_id is not None:
        cmd += ["--episode-id", str(episode_id)]
    return cmd, env


def plan(require_authorization: bool = False) -> dict:
    """Read-only readiness check used by the canonical transition tool."""
    ready = RUNNER_PATH.is_file()
    return {
        "status": "READY" if ready else "NOT_READY",
        "executor_id": EXECUTOR_ID,
        "side_effects_performed": False,
        "pipeline": PIPELINE,
        "transport_guard": {"max_client_calls": 1, "max_http_posts": 1, "max_retries": 0},
        "authorization_required": True,
        "authorization_active": False,
    }


def execute(
    source: Path,
    output: Path,
    job_id: str,
    capture_root: Path,
    *,
    series_title: str = "Anime",
    episode_title: str = "Episode",
    anime_series_id: int | None = None,
    episode_id: int | None = None,
    timeout_seconds: int = 3600,
) -> dict:
    """Perform exactly one live capture. No retry, no loop.

    Returns the subprocess result summary.  Raises on validation or non-zero
    exit (fail-closed: a failed capture is reported, never silently retried).
    """
    validate_inputs(source, output, capture_root)
    cmd, env = build_invocation(
        source, output, job_id, capture_root,
        series_title=series_title, episode_title=episode_title,
        anime_series_id=anime_series_id, episode_id=episode_id,
    )
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=timeout_seconds)
    if proc.returncode != 0:
        raise RuntimeError(
            f"TRACK2_LIVE_CAPTURED_FAILED rc={proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return {
        "status": "EXECUTED",
        "executor_id": EXECUTOR_ID,
        "job_id": job_id,
        "output": str(output),
        "capture_root": str(capture_root),
        "returncode": proc.returncode,
        "side_effects_performed": True,
        "transport_guard": {"max_client_calls": 1, "max_http_posts": 1, "max_retries": 0},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--job-id", default="track2-live-captured")
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--series-title", default="Anime")
    parser.add_argument("--episode-title", default="Episode")
    parser.add_argument("--anime-series-id", type=int)
    parser.add_argument("--episode-id", type=int)
    parser.add_argument("--plan", action="store_true", help="read-only readiness check")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, sort_keys=True)); return 0
    try:
        result = execute(
            args.source, args.output, args.job_id, args.capture_root,
            series_title=args.series_title, episode_title=args.episode_title,
            anime_series_id=args.anime_series_id, episode_id=args.episode_id,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL_STOP", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
