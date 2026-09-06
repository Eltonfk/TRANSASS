"""Collection policy for archived-evidence and expensive stress tests."""

from __future__ import annotations

from pathlib import Path


HISTORICAL_FILES = {
    "test_subtranslate_b5_planner.py",
    "test_subtranslate_b6_planner.py",
    "test_subtranslate_b7_planner.py",
    "test_subtranslate_batch_planner.py",
}
STRESS_NODE_SUFFIX = "test_v238_per_call_durability.py::PerCallDurabilityTests::test_canonical_runner_233_initials_restart_mid_batches_without_retransport"


def pytest_collection_modifyitems(config, items):
    import pytest

    for item in items:
        if Path(str(item.fspath)).name in HISTORICAL_FILES:
            item.add_marker(pytest.mark.historical)
        if str(item.nodeid).endswith(STRESS_NODE_SUFFIX):
            item.add_marker(pytest.mark.stress)

