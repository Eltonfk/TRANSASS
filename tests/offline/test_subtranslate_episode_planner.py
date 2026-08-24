"""Offline tests for the multi-episode planner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".opencode/tools/subtranslate_episode_planner.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("subtranslate_episode_planner", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def planner():
    return _load_tool()


def test_logical_batch_id(planner):
    assert planner.logical_batch_id(0) == "v226-initial-000000"
    assert planner.logical_batch_id(11) == "v226-initial-000011"


def test_membership_convention(planner):
    assert planner.membership_sha256([1, 2]) == planner.membership_sha256([1, 2])
    assert planner.membership_sha256([1]) != planner.membership_sha256([2])


def test_load_config_missing_key(planner, tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({"source_sha256": "x"}))
    with pytest.raises(planner.Blocked) as excinfo:
        planner.load_config(str(cfg))
    assert "CONFIG_MISSING_KEY" in str(excinfo.value)


def test_load_config_valid(planner, tmp_path):
    cfg = tmp_path / "good.json"
    cfg.write_text(json.dumps({
        "source_sha256": "abc",
        "source_candidates": ["/tmp/a.ass"],
        "episode_id": 80,
        "engine_revision": "d9dbaa82",
    }))
    result = planner.load_config(str(cfg))
    assert result["episode_id"] == 80


import json  # noqa: E402


def test_resolve_source_not_found(planner, tmp_path):
    config = {"source_candidates": ["/nonexistent/e08.ass"], "source_sha256": "deadbeef"}
    with pytest.raises(planner.Blocked) as excinfo:
        planner.resolve_source(config)
    assert "EPISODE_SOURCE_NOT_FOUND" in str(excinfo.value)


def test_no_apply_surface():
    source = TOOL_PATH.read_text(encoding="utf-8")
    assert "--apply" not in source
    assert "requests.post" not in source
