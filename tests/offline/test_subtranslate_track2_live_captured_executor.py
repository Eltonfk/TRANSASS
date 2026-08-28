"""Offline deterministic tests for the TRACK2 live-captured executor.

No network, model call, or transport.  subprocess.run is mocked so execute()
is exercised without running the real runner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import subtranslate_track2_live_captured_executor as ex


def test_plan_ready_when_runner_exists():
    assert ex.RUNNER_PATH.is_file(), "web_retranslation_runner.py must exist"
    result = ex.plan()
    assert result["status"] == "READY"
    assert result["executor_id"] == ex.EXECUTOR_ID
    assert result["side_effects_performed"] is False
    assert result["transport_guard"] == {"max_client_calls": 1, "max_http_posts": 1, "max_retries": 0}


def test_validate_inputs_rejects_missing_source(tmp_path):
    src = tmp_path / "missing.ass"
    out = tmp_path / "out.pt-BR.ass"
    cap = tmp_path / "captures"
    try:
        ex.validate_inputs(src, out, cap)
        assert False, "should have raised"
    except ValueError as exc:
        assert "FONTE NAO ENCONTRADA" in str(exc)


def test_validate_inputs_rejects_bad_format(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("x")
    out = tmp_path / "out.pt-BR.ass"
    cap = tmp_path / "captures"
    try:
        ex.validate_inputs(src, out, cap)
        assert False, "should have raised"
    except ValueError as exc:
        assert "formato não suportado" in str(exc)


def test_validate_inputs_rejects_existing_output(tmp_path):
    src = tmp_path / "src.ass"
    src.write_text("[Script Info]")
    out = tmp_path / "out.pt-BR.ass"
    out.write_text("existing")
    cap = tmp_path / "captures"
    try:
        ex.validate_inputs(src, out, cap)
        assert False, "should have raised"
    except ValueError as exc:
        assert "SAIDA JA EXISTE" in str(exc)


def test_build_invocation_uses_v2_3_8_and_runner():
    src = Path("/tmp/src.ass")
    out = Path("/tmp/out.pt-BR.ass")
    cap = Path("/tmp/captures")
    cmd, env = ex.build_invocation(src, out, "job-1", cap,
                                    series_title="S", episode_title="E")
    assert str(ex.RUNNER_PATH) in cmd
    assert "--pipeline" in cmd and "v2_3_8" in cmd
    assert env["TRANSLATOR_PIPELINE"] == "v2_3_8"


def test_execute_runs_once_and_returns_executed(tmp_path):
    src = tmp_path / "src.ass"
    src.write_text("[Script Info]")
    out = tmp_path / "out.pt-BR.ass"
    cap = tmp_path / "captures"
    cap.mkdir()

    fake = mock.Mock()
    fake.returncode = 0
    fake.stdout = "WEB_RETRANSLATION_SUMMARY {}"
    fake.stderr = ""

    with mock.patch.object(ex.subprocess, "run", return_value=fake) as run:
        result = ex.execute(src, out, "job-1", cap)
    assert result["status"] == "EXECUTED"
    assert result["side_effects_performed"] is True
    # Exactly one subprocess invocation, no retry loop.
    assert run.call_count == 1
    called_cmd = run.call_args.args[0]
    assert str(ex.RUNNER_PATH) in called_cmd


def test_execute_fails_closed_on_nonzero(tmp_path):
    src = tmp_path / "src.ass"
    src.write_text("[Script Info]")
    out = tmp_path / "out.pt-BR.ass"
    cap = tmp_path / "captures"
    cap.mkdir()

    fake = mock.Mock()
    fake.returncode = 1
    fake.stdout = ""
    fake.stderr = "boom"

    with mock.patch.object(ex.subprocess, "run", return_value=fake):
        try:
            ex.execute(src, out, "job-1", cap)
            assert False, "should have raised"
        except RuntimeError as exc:
            assert "TRACK2_LIVE_CAPTURED_FAILED" in str(exc)
