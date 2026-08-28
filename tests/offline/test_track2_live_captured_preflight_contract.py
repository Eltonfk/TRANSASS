"""Offline deterministic test for the TRACK2_LIVE_CAPTURED preflight contract.

Verifies that subtranslate-orchestrator.md defines the recognition rule that
routes next_action=TRACK2_LIVE_CAPTURED_PREFLIGHT_READ_ONLY_REQUIRED to a
read-only SAFE_PLAN, and that the contract forbids execution side effects
(model call, transport, B5-B7) in the preflight.
"""

from __future__ import annotations

import pathlib

ORCHESTRATOR = pathlib.Path(
    "/home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/agents/subtranslate-orchestrator.md"
)


def _content() -> str:
    assert ORCHESTRATOR.exists(), f"orchestrator.md not found at {ORCHESTRATOR}"
    return ORCHESTRATOR.read_text(encoding="utf-8")


def test_track2_live_captured_recognition_section_present():
    content = _content()
    assert "AUTO03D_TRACK2_LIVE_CAPTURED_PREFLIGHT_RECOGNITION" in content


def test_track2_live_captured_routes_to_safe_plan():
    content = _content()
    assert "SAFE_PLAN_TRACK2_LIVE_CAPTURED_PREFLIGHT_READ_ONLY" in content


def test_track2_live_captured_next_action_binding():
    content = _content()
    assert "next_action=TRACK2_LIVE_CAPTURED_PREFLIGHT_READ_ONLY_REQUIRED" in content


def test_track2_live_captured_preflight_is_read_only_no_side_effects():
    content = _content()
    section = content.split("AUTO03D_TRACK2_LIVE_CAPTURED_PREFLIGHT_RECOGNITION", 1)[1]
    # The preflight must be explicitly read-only and must forbid execution side effects.
    assert "read-only" in section.lower()
    for forbidden in ("model call", "transporte", "B5", "B6", "B7"):
        assert forbidden in section, f"preflight contract must mention prohibition of {forbidden}"
