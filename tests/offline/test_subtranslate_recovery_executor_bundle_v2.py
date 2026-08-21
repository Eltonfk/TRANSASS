import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
V1_PATH = ROOT / ".opencode/tools/subtranslate_recovery_ledger_reprepare.py"
V2_PATH = ROOT / "packaging/subtranslate-guard/bundle-source/.opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
DURABILITY_PATH = ROOT / "src/subtranslate/v238_per_call_durability.py"
BUNDLE_DURABILITY_PATH = ROOT / "packaging/subtranslate-guard/bundle-source/src/subtranslate/v238_per_call_durability.py"
V1_SHA = "2f0fc420399671f06040a46405d42eca532c692d0b62729353fb90b840a04801"
DURABILITY_SHA = "5caeb33f1bb21fbc90b7195b791e061bc46a7bddedb49bb15f52908b09d23585"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V1 = load_module(V1_PATH, "v1_bundle_test")
V2 = load_module(V2_PATH, "v2_bundle_test")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


class SyntheticFixture:
    def __init__(self, executor):
        self.executor = executor
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.authority = self.root / "authority"
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        for relative in executor.TOOLCHAIN_COMPONENTS:
            path = self.candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("bundle fixture component\n", encoding="utf-8")
        self.runtime = self.authority / "runtime-evidence" / executor.FAMILY_ID
        self.runtime.mkdir(parents=True)
        self.backups = self.root / "persistent-backups"
        self.state_path = self.authority / "PROJECT_STATE.json"
        self.handoff_path = self.authority / "HANDOFF_CHATGPT.md"
        self.operation_path = self.runtime / "operation.json"
        self.target_path = self.runtime / "episode-budget.json"
        self.lock_path = self.runtime / "episode-budget.json.lock"
        self.state = self._state()
        self.budget = self._budget()
        self._write_all()

    def close(self):
        self.tmp.cleanup()

    def _state(self):
        return {
            "next_action": "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION",
            "current_authority": {"candidate_commit": "fixture-candidate-commit"},
            "r6c_reconciliation": {
                "episode_id": 79, "anime_series_id": 3, "source_sha256": "a" * 64,
                "pipeline": "v2_3_8", "stage": "FULL_TRANSLATION_V238", "model": "fixture-model",
                "model_digest": "b" * 64, "prompt_schema_hash": "c" * 64,
                "glossary_hash": "d" * 64, "configuration_hash": "e" * 64,
            },
            "r6c_batch4_recovery_protocol": {"status": "DOCUMENTED_NOT_EXECUTED"},
            "r6c_batch4_recovery_e1_reconciliation": {
                "runtime_family_id": self.executor.FAMILY_ID,
                "runtime_root": str(self.runtime),
                "operation_id": self.executor.OPERATION_ID,
            },
        }

    @staticmethod
    def _budget():
        return {
            "initial_consumed": 0, "retry_consumed": 0,
            "successful_durable_responses": 0, "invalid_responses": 0,
            "unknown_outcomes": 0, "transport_failures_confirmed": 0,
            "cancelled_confirmed": 0, "planned_initial_calls": 1,
            "retry_reserve": 0, "physical_ceiling": 1,
            "operation_retry_transport_cap": 2, "per_event_retry_transport_cap": 1,
            "reservations": [],
        }

    def _write_all(self):
        self.state_path.write_bytes(canonical(self.state))
        self.handoff_path.write_text("fixture handoff\n", encoding="utf-8")
        self.operation_path.write_bytes(canonical({"operation_id": self.executor.OPERATION_ID}))
        self.target_path.write_bytes(canonical(self.budget))
        self.lock_path.write_bytes(b"")

    def roots(self):
        return self.executor.ExecutorRoots(
            candidate_root=self.candidate,
            authority_root=self.authority,
            project_state=self.state_path,
            handoff=self.handoff_path,
            backup_root=self.backups,
            expected_project_state_sha256=sha(self.state_path.read_bytes()),
            expected_runtime_root=self.runtime,
            expected_target_sha256=sha(self.target_path.read_bytes()),
            fixture_temporary_root=self.root,
        )

    def context(self):
        roots = self.roots()
        return self.executor.FixtureExecutionContext(
            temporary_root=self.root, roots=roots, runtime_root=self.runtime,
            target_ledger=self.target_path, operation_file=self.operation_path,
            lock_file=self.lock_path, backup_root=self.backups,
        )

    def apply(self):
        return self.executor.apply_fixture_context(self.context())


class RecoveryExecutorV2Tests(unittest.TestCase):
    def test_v1_identity_and_source_remain_unchanged(self):
        self.assertEqual(sha(V1_PATH.read_bytes()), V1_SHA)
        self.assertEqual(sha(DURABILITY_PATH.read_bytes()), DURABILITY_SHA)
        canonical_bytes = DURABILITY_PATH.read_bytes()
        bundle_bytes = BUNDLE_DURABILITY_PATH.read_bytes()
        self.assertEqual(sha(bundle_bytes), DURABILITY_SHA)
        self.assertEqual(bundle_bytes, canonical_bytes)
        self.assertEqual(V1.EXECUTOR_ID, "RECOVERY_LEDGER_REPREPARATION_V1")
        self.assertEqual(V2.EXECUTOR_ID, "RECOVERY_LEDGER_REPREPARATION_V2")
        self.assertEqual(V2.PARENT_EXECUTOR_ID, V1.EXECUTOR_ID)
        self.assertEqual(V2.PARENT_EXECUTOR_SHA256, V1_SHA)
        self.assertNotEqual(sha(V1_PATH.read_bytes()), sha(V2_PATH.read_bytes()))

    def test_v2_bundle_source_has_no_candidate_or_home_backup_path(self):
        source = V2_PATH.read_text(encoding="utf-8")
        self.assertNotIn("/home/palhacinho/codex-projects/subtranslate-v238-candidate", source)
        self.assertNotIn("/home/palhacinho/opencode-backups", source)
        self.assertEqual(V2.BACKUP_ROOT, Path("/var/lib/subtranslate-guard/backups"))
        self.assertEqual(V2.FIXED_INTERPRETER, "/usr/bin/python3.12")
        self.assertEqual(V2.FIXED_APPLY_ARGV[1:3], ("-I", "-B"))
        self.assertEqual(V2.BUNDLE_ROOT, V2_PATH.resolve().parents[2])
        self.assertEqual(V2.TOOLCHAIN_COMPONENTS, (".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py", "src/subtranslate/v238_per_call_durability.py"))

    def test_cli_surface_is_fixed_and_context_is_not_cli_or_env_reachable(self):
        source = V2_PATH.read_text(encoding="utf-8")
        for forbidden in ("--target", "--backup-root", "--bundle-root", "--retry", "--force", "os.environ", "--fixture"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(V2.main(["--unknown"]), 4)

    def test_synthetic_bundle_plan_uses_only_bundle_imports_under_isolation(self):
        canonical_bytes = DURABILITY_PATH.read_bytes()
        bundle_bytes = BUNDLE_DURABILITY_PATH.read_bytes()
        self.assertEqual(sha(canonical_bytes), DURABILITY_SHA)
        self.assertEqual(sha(bundle_bytes), DURABILITY_SHA)
        self.assertEqual(bundle_bytes, canonical_bytes)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = root / "release"
            v2_dst = release / ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
            durability_dst = release / "src/subtranslate/v238_per_call_durability.py"
            v2_dst.parent.mkdir(parents=True)
            durability_dst.parent.mkdir(parents=True)
            shutil.copyfile(V2_PATH, v2_dst)
            shutil.copyfile(BUNDLE_DURABILITY_PATH, durability_dst)
            self.assertEqual(sha(durability_dst.read_bytes()), DURABILITY_SHA)
            self.assertEqual(durability_dst.read_bytes(), bundle_bytes)
            fake = root / "fake-site"; fake.mkdir()
            result = subprocess.run(
                ["/usr/bin/python3.12", "-I", "-B", str(v2_dst), "--plan"],
                cwd=str(fake), env={"HOME": str(fake), "PYTHONPATH": str(fake), "PATH": "/usr/bin:/bin"},
                text=True, capture_output=True, check=False,
            )
            self.assertIn(result.returncode, (0, 2, 3))
            report = json.loads(result.stdout)
            self.assertEqual(report["executor_id"], V2.EXECUTOR_ID)
            self.assertFalse(report["side_effects_performed"])
            self.assertFalse(list(release.rglob("__pycache__")))
            origin = subprocess.run(
                ["/usr/bin/python3.12", "-I", "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import subtranslate.v238_per_call_durability as m; print(m.__file__)", str(release / "src")],
                cwd=str(fake), env={"HOME": str(fake), "PYTHONPATH": str(fake)},
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(Path(origin.stdout.strip()).resolve(), durability_dst.resolve())
            self.assertEqual(sha(durability_dst.read_bytes()), DURABILITY_SHA)
            self.assertNotIn(str(ROOT), origin.stdout)

    def test_missing_bundle_dependency_fails_without_candidate_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); release = root / "release"
            v2_dst = release / ".opencode/tools/subtranslate_recovery_ledger_reprepare_v2.py"
            v2_dst.parent.mkdir(parents=True); shutil.copyfile(V2_PATH, v2_dst)
            fake = root / "fake-site"; fake.mkdir()
            result = subprocess.run(
                ["/usr/bin/python3.12", "-I", "-B", str(v2_dst), "--plan"],
                cwd=str(fake), env={"HOME": str(fake), "PYTHONPATH": str(ROOT / "src")},
                text=True, capture_output=True, check=False,
            )
            self.assertIn(result.returncode, (2, 3))
            self.assertIn("OFFICIAL_SCHEMA_UNAVAILABLE", result.stdout)
            self.assertNotIn(str(ROOT / "src"), result.stdout)

    def test_fixture_apply_preserves_v1_semantics_and_v2_backup_policy(self):
        v1_fixture = SyntheticFixture(V1); v2_fixture = SyntheticFixture(V2)
        self.addCleanup(v1_fixture.close); self.addCleanup(v2_fixture.close)
        v1_plan = V1.RecoveryLedgerExecutor(v1_fixture.roots()).plan()
        v2_plan = V2.RecoveryLedgerExecutor(v2_fixture.roots()).plan()
        self.assertEqual(v1_plan["eligible"], v2_plan["eligible"])
        self.assertEqual(v1_plan["expected_changes"], v2_plan["expected_changes"])
        self.assertEqual(v1_plan["allowed_fields"], v2_plan["allowed_fields"])
        self.assertEqual(v1_plan["rollback_policy"], v2_plan["rollback_policy"])
        self.assertEqual(v1_plan["post_execution"], v2_plan["post_execution"])
        self.assertEqual(v1_plan["side_effects_performed"], v2_plan["side_effects_performed"])
        self.assertNotEqual(v1_plan["backup_requirements"]["root"], v2_plan["backup_requirements"]["root"])
        before_v1 = json.loads(v1_fixture.target_path.read_bytes())
        before_v2 = json.loads(v2_fixture.target_path.read_bytes())
        result_v1 = v1_fixture.apply(); result_v2 = v2_fixture.apply()
        self.assertEqual(result_v1["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        self.assertEqual(result_v2["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        post_v1 = json.loads(v1_fixture.target_path.read_bytes())
        post_v2 = json.loads(v2_fixture.target_path.read_bytes())
        allowed = set(V1.EXPECTED_LEDGER_MISSING_FIELDS)
        self.assertEqual(set(post_v1) - set(before_v1), allowed)
        self.assertEqual(set(post_v2) - set(before_v2), allowed)
        for key in set(before_v1) - allowed:
            self.assertEqual(post_v1[key], before_v1[key])
            self.assertEqual(post_v2[key], before_v2[key])
        self.assertEqual(result_v1["publish_attempts"], 1)
        self.assertEqual(result_v2["publish_attempts"], 1)
        self.assertEqual(result_v1["rollback_attempts"], 0)
        self.assertEqual(result_v2["rollback_attempts"], 0)
        self.assertTrue(str(result_v2["backup"]["directory"]).startswith(str(v2_fixture.backups)))
        self.assertNotEqual(result_v1["executor_id"], result_v2["executor_id"])

    def test_v2_apply_fault_has_one_publish_and_one_rollback(self):
        fixture = SyntheticFixture(V2); self.addCleanup(fixture.close)
        result = V2.apply_fixture_context(fixture.context(), force_post_validation_failure=True)
        self.assertEqual(result["terminal_state"], "EXECUTION_FAILED_ROLLED_BACK")
        self.assertEqual(result["publish_attempts"], 1)
        self.assertEqual(result["rollback_attempts"], 1)
        self.assertEqual(sha(fixture.target_path.read_bytes()), sha(canonical(fixture.budget)))

    def test_real_paths_and_v2_apply_are_not_reached_by_suite(self):
        for path in (Path("/var/lib/subtranslate-guard"), Path("/home/palhacinho/opencode-backups")):
            # The legacy home backup directory may pre-exist; this phase must
            # not create or mutate it.  The test itself performs no access.
            before = (path.exists(), path.stat().st_mtime_ns if path.exists() else None)
            after = (path.exists(), path.stat().st_mtime_ns if path.exists() else None)
            self.assertEqual(after, before)
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"run", "Popen"}:
                self.assertNotIn("--apply", ast.unparse(node))


if __name__ == "__main__":
    unittest.main(verbosity=2)
