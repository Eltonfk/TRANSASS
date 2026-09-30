#!/usr/bin/env python3
"""Run one deterministic, approximately balanced shard of the offline suite."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OFFLINE_TEST_ROOT = PROJECT_ROOT / "tests" / "offline"
HEAVY_TEST_WEIGHTS = {
    # Durable fsync/fault matrices dominate wall time despite modest source size.
    "test_v238_per_call_durability.py": 600_000,
}


def test_file_weight(path: Path) -> int:
    return max(path.stat().st_size, HEAVY_TEST_WEIGHTS.get(path.name, 0))


def partition_test_files(files: Sequence[Path], total: int) -> list[list[Path]]:
    """Greedy size balancing with stable tie-breaking and complete coverage."""
    if total < 1:
        raise ValueError("total must be positive")
    shards: list[list[Path]] = [[] for _ in range(total)]
    weights = [0] * total
    ordered = sorted(files, key=lambda path: (-test_file_weight(path), path.name))
    for path in ordered:
        target = min(range(total), key=lambda index: (weights[index], index))
        shards[target].append(path)
        weights[target] += test_file_weight(path)
    for shard in shards:
        shard.sort(key=lambda path: path.name)
    return shards


def discover_test_files(root: Path = OFFLINE_TEST_ROOT) -> list[Path]:
    return sorted(root.glob("test_*.py"), key=lambda path: path.name)


def _isolated_environment(shard: int) -> dict[str, str]:
    environment = dict(os.environ)
    base = Path(environment.get("TRANSLATOR_WEB_STATE_DIR", "/tmp/transass-test-state"))
    state = base.with_name(f"{base.name}-shard-{shard}")
    environment["TRANSLATOR_WEB_STATE_DIR"] = str(state)
    environment["ANIME_SUBTITLE_LIBRARY_ROOT"] = str(state / "anime-subtitle-library")
    environment["TRANSLATOR_GLOSSARY_PATH"] = str(state / "glossary_v1.json")
    python_path = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src" / "subtranslate")]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    return environment


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="shard number, starting at 1")
    parser.add_argument("--total", type=int, default=4, help="total number of shards")
    parser.add_argument("--list", action="store_true", help="print selected files without running pytest")
    parser.add_argument("--junit-dir", type=Path, help="write JUnit XML and slow-test timing for CI")
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.shard <= arguments.total:
        parser.error("--shard must be between 1 and --total")
    selected = partition_test_files(discover_test_files(), arguments.total)[arguments.shard - 1]
    if arguments.list:
        for path in selected:
            print(path.relative_to(PROJECT_ROOT))
        return 0
    if not selected:
        parser.error("shard vazio: nenhuma suíte foi coletada")
    command = [sys.executable, "-m", "pytest", "-q"]
    if arguments.junit_dir is not None:
        arguments.junit_dir.mkdir(parents=True, exist_ok=True)
        command.extend([
            "--durations=15",
            f"--junitxml={arguments.junit_dir / f'offline-shard-{arguments.shard}.xml'}",
        ])
    command.extend(map(str, selected))
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=_isolated_environment(arguments.shard),
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
