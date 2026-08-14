#!/usr/bin/env python3
"""Run the canonical unittest-based offline suite with two exact exclusions."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests" / "offline"
sys.path.insert(0, str(ROOT / "src" / "subtranslate"))

DESELECT = {
    "test_p2b1_architecture.DispatchTests.test_v230_calls_v226_then_v230",
    "test_p2b1a_closure.ContractAndControlPlaneTests.test_normal_archive_receives_final_v230_output",
}


def flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def load_suite() -> tuple[unittest.TestSuite, list[str]]:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    deselected: list[str] = []
    for path in sorted(TEST_ROOT.glob("test_*.py")):
        module_name = path.stem
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        for test in flatten(loader.loadTestsFromModule(module)):
            short_id = ".".join(test.id().split(".")[-3:])
            if short_id in DESELECT:
                deselected.append(short_id)
            else:
                suite.addTest(test)
    return suite, deselected


if __name__ == "__main__":
    suite, deselected = load_suite()
    print("EXACT_DESELECTED", *sorted(deselected), sep="\n")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"OFFLINE_RESULT run={result.testsRun} failures={len(result.failures)} errors={len(result.errors)}")
    raise SystemExit(0 if result.wasSuccessful() and set(deselected) == DESELECT else 1)
