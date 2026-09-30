#!/usr/bin/env python3
"""Canonical offline gate: pytest collection/policy shared by local and CI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_test_shard import OFFLINE_TEST_ROOT, _isolated_environment


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = [sys.executable, "-m", "pytest", "-q", *arguments]
    if not arguments:
        command.append(str(OFFLINE_TEST_ROOT))
    # Keep pytest exit code 5: collecting no tests is not success.
    return subprocess.run(command, cwd=ROOT,
                          env=_isolated_environment(0), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
