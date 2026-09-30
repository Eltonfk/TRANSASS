"""Behavioral frontend regressions using real JavaScript and a fake API."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


def test_pagination_selection_errors_and_source_labels() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for the frontend offline gate")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([node, str(root / "tests/offline/ui_pagination_probe.cjs")],
                            cwd=root, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "UI_PAGINATION_OK" in result.stdout
