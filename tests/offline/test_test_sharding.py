from pathlib import Path

from scripts.run_test_shard import partition_test_files, test_file_weight as _test_file_weight


def test_shards_are_deterministic_disjoint_and_complete(tmp_path: Path) -> None:
    files = []
    for index, size in enumerate((100, 90, 80, 70, 60, 50, 40), 1):
        path = tmp_path / f"test_{index}.py"
        path.write_bytes(b"x" * size)
        files.append(path)

    first = partition_test_files(files, 3)
    second = partition_test_files(list(reversed(files)), 3)

    assert first == second
    flattened = [path for shard in first for path in shard]
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(files)
    weights = [sum(path.stat().st_size for path in shard) for shard in first]
    assert max(weights) - min(weights) <= max(path.stat().st_size for path in files)


def test_durability_file_receives_runtime_weight(tmp_path: Path) -> None:
    path = tmp_path / "test_v238_per_call_durability.py"
    path.write_text("pass\n", encoding="utf-8")

    assert _test_file_weight(path) == 600_000


def test_offline_runner_executes_pytest_functions_and_propagates_failure(tmp_path: Path) -> None:
    import subprocess
    import sys

    test = tmp_path / "test_gate_failure.py"
    test.write_text("def test_function_is_collected():\n    assert False, 'fixture failure'\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/run_offline_tests.py"), str(test)],
                            cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "1 failed" in result.stdout and "fixture failure" in result.stdout


def test_offline_runner_does_not_report_pass_for_zero_tests(tmp_path: Path) -> None:
    import subprocess
    import sys

    test = tmp_path / "test_empty.py"
    test.write_text("# no test functions\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/run_offline_tests.py"), str(test)],
                            cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 5
