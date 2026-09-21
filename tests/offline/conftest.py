"""Collection policy for archived-evidence and expensive stress tests."""

from __future__ import annotations

STRESS_NODE_SUFFIX = "test_v238_per_call_durability.py::PerCallDurabilityTests::test_canonical_runner_233_initials_restart_mid_batches_without_retransport"


def pytest_collection_modifyitems(config, items):
    import pytest

    for item in items:
        if str(item.nodeid).endswith(STRESS_NODE_SUFFIX):
            item.add_marker(pytest.mark.stress)
