import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
EXECUTOR_PATH = ROOT / ".opencode/tools/subtranslate_recovery_ledger_reprepare.py"
spec = importlib.util.spec_from_file_location("reprepare_executor", EXECUTOR_PATH)
executor = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = executor
spec.loader.exec_module(executor)


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.authority = self.root / "authority"
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        for relative in executor.TOOLCHAIN_COMPONENTS:
            component = self.candidate / relative
            component.parent.mkdir(parents=True, exist_ok=True)
            component.write_text("fixture toolchain\n", encoding="utf-8")
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
                "episode_id": 79,
                "anime_series_id": 3,
                "source_sha256": "a" * 64,
                "pipeline": "v2_3_8",
                "stage": "FULL_TRANSLATION_V238",
                "model": "fixture-model",
                "model_digest": "b" * 64,
                "prompt_schema_hash": "c" * 64,
                "glossary_hash": "d" * 64,
                "configuration_hash": "e" * 64,
            },
            "r6c_batch4_recovery_protocol": {"status": "DOCUMENTED_NOT_EXECUTED"},
            "r6c_batch4_recovery_e1_reconciliation": {
                "runtime_family_id": executor.FAMILY_ID,
                "runtime_root": str(self.runtime),
                "operation_id": executor.OPERATION_ID,
            },
        }

    @staticmethod
    def _budget():
        return {
            "initial_consumed": 0,
            "retry_consumed": 0,
            "successful_durable_responses": 0,
            "invalid_responses": 0,
            "unknown_outcomes": 0,
            "transport_failures_confirmed": 0,
            "cancelled_confirmed": 0,
            "planned_initial_calls": 1,
            "retry_reserve": 0,
            "physical_ceiling": 1,
            "operation_retry_transport_cap": 2,
            "per_event_retry_transport_cap": 1,
            "reservations": [],
        }

    def _write_all(self):
        self.state_path.write_bytes(canonical_bytes(self.state))
        self.handoff_path.write_text("current handoff fixture\n", encoding="utf-8")
        self.operation_path.write_bytes(canonical_bytes({"operation_id": executor.OPERATION_ID}))
        self.target_path.write_bytes(canonical_bytes(self.budget))
        self.lock_path.write_bytes(b"")

    def rewrite_state(self, mutate, *, bind_expected=True):
        state = copy.deepcopy(self.state)
        mutate(state)
        self.state = state
        self.state_path.write_bytes(canonical_bytes(state))
        return self.roots(bind_expected=bind_expected)

    def rewrite_budget(self, mutate, *, bind_expected=True):
        budget = copy.deepcopy(self.budget)
        mutate(budget)
        self.budget = budget
        self.target_path.write_bytes(canonical_bytes(budget))
        return self.roots(bind_expected=bind_expected)

    def roots(self, *, bind_expected=True, backup_root=None):
        state_sha = sha(self.state_path.read_bytes()) if bind_expected else sha(canonical_bytes(self._state()))
        target_sha = sha(self.target_path.read_bytes()) if bind_expected else sha(canonical_bytes(self._budget()))
        return executor.ExecutorRoots(
            candidate_root=self.candidate,
            authority_root=self.authority,
            project_state=self.state_path,
            handoff=self.handoff_path,
            backup_root=backup_root or self.backups,
            expected_project_state_sha256=state_sha,
            expected_runtime_root=self.runtime,
            expected_target_sha256=target_sha,
            fixture_temporary_root=self.root,
        )

    def context(self, *, roots=None):
        selected = roots or self.roots()
        runtime = selected.expected_runtime_root
        assert runtime is not None
        return executor.FixtureExecutionContext(
            temporary_root=self.root,
            roots=selected,
            runtime_root=runtime,
            target_ledger=runtime / "episode-budget.json",
            operation_file=runtime / "operation.json",
            lock_file=runtime / "episode-budget.json.lock",
            backup_root=selected.backup_root,
        )

    def apply(self, *, roots=None, force_post_validation_failure=False):
        return executor.apply_fixture_context(
            self.context(roots=roots), force_post_validation_failure=force_post_validation_failure
        )

    def run_plan(self):
        return executor.RecoveryLedgerExecutor(self.roots()).plan()


class RecoveryLedgerReprepareTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)

    @staticmethod
    def code(plan):
        return (plan.get("blocked_reasons") or plan.get("unknowns") or [{"code": None}])[0]["code"]

    def test_plan_is_read_only(self):
        before = {path: path.read_bytes() for path in (self.fixture.state_path, self.fixture.target_path, self.fixture.operation_path, self.fixture.lock_path)}
        plan = self.fixture.run_plan()
        after = {path: path.read_bytes() for path in before}
        self.assertTrue(plan["eligible"])
        self.assertEqual(before, after)
        self.assertFalse(plan["side_effects_performed"])
        self.assertFalse(self.fixture.backups.exists())

    def test_cli_rejects_paths_and_arbitrary_action(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = executor.main(["--plan", "/tmp/other"])
        self.assertEqual(result, 4)
        self.assertIn("UNEXPECTED_ARGUMENT", output.getvalue())

    def test_cli_accepts_only_plan_or_apply_surface(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = executor.main(["--unknown"])
        self.assertEqual(result, 4)
        self.assertIn("UNEXPECTED_ARGUMENT", output.getvalue())

    def test_real_cli_apply_is_structural_only_and_never_executed(self):
        source = EXECUTOR_PATH.read_text(encoding="utf-8")
        self.assertIn('if args == ["--apply"]:', source)
        self.assertIn("ExecutorRoots.real()", source)
        self.assertNotIn("--fixture", source)
        self.assertNotIn("--backup-root", source)
        self.assertNotIn("--target", source)
        self.assertNotIn("environ", source)

    def test_executor_has_no_shell_network_or_model_surface(self):
        source = EXECUTOR_PATH.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "socket", "urllib", "requests", "http://", "Client.call", "Ollama", "OpenAI", "exec("):
            self.assertNotIn(forbidden, source)

    def test_offline_suite_has_no_real_apply_invocation(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

        def call_name(node):
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                parent = call_name(node.value)
                return f"{parent}.{node.attr}" if parent else node.attr
            return ""

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr == "main":
                args = [item.value for item in node.args if isinstance(item, ast.Constant)]
                self.assertNotIn("--apply", args)
            if call_name(node.func) in {"subprocess.run", "subprocess.Popen", "os.system", "os.popen"}:
                self.assertNotIn("--apply", ast.unparse(node))

    def test_official_initializer_is_used(self):
        source = EXECUTOR_PATH.read_text(encoding="utf-8")
        self.assertIn("EpisodeBudgetLedger", source)
        self.assertIn("ledger._initial()", source)
        self.assertIn("OFFICIAL_SCHEMA_SOURCE", "OFFICIAL_SCHEMA_SOURCE")

    def test_incomplete_expected_ledger_is_eligible(self):
        plan = self.fixture.run_plan()
        self.assertTrue(plan["eligible"])
        self.assertEqual(plan["blocked_reasons"], [])
        self.assertEqual(plan["unknowns"], [])

    def test_complete_ledger_is_already_reprepared(self):
        initial = executor._official_initial(self.fixture.state, self.fixture.budget)
        self.fixture.budget.update(initial)
        self.fixture.target_path.write_bytes(canonical_bytes(self.fixture.budget))
        plan = self.fixture.run_plan()
        self.assertFalse(plan["eligible"])
        self.assertEqual(self.code(plan), "ALREADY_REPREPARED")

    def test_nonzero_consumption_blocks(self):
        roots = self.fixture.rewrite_budget(lambda value: value.update(initial_consumed=1))
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "LEDGER_INITIAL_CONSUMPTION_NONZERO")

    def test_retry_consumption_blocks(self):
        roots = self.fixture.rewrite_budget(lambda value: value.update(retry_consumed=1))
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "LEDGER_RETRY_CONSUMPTION_NONZERO")

    def test_reservations_block(self):
        roots = self.fixture.rewrite_budget(lambda value: value.update(reservations=[{"id": "r"}]))
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "LEDGER_RESERVATIONS_PRESENT")

    def test_calls_directory_blocks(self):
        (self.fixture.runtime / "calls").mkdir()
        plan = self.fixture.run_plan()
        self.assertEqual(self.code(plan), "CALLS_OR_ATTEMPTS_PRESENT")

    def test_attempt_present_blocks(self):
        calls = self.fixture.runtime / "calls"
        calls.mkdir()
        (calls / "attempt-1").mkdir()
        plan = self.fixture.run_plan()
        self.assertEqual(self.code(plan), "CALLS_OR_ATTEMPTS_PRESENT")

    def test_wrong_family_blocks(self):
        roots = self.fixture.rewrite_state(lambda state: state["r6c_batch4_recovery_e1_reconciliation"].update(runtime_family_id="OTHER"))
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "CANONICAL_FAMILY_MISMATCH")

    def test_wrong_episode_blocks(self):
        roots = self.fixture.rewrite_state(lambda state: state["r6c_reconciliation"].update(episode_id=80))
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "CANONICAL_EPISODE_MISMATCH")

    def test_wrong_operation_blocks(self):
        self.fixture.operation_path.write_bytes(canonical_bytes({"operation_id": "OTHER"}))
        plan = self.fixture.run_plan()
        self.assertEqual(self.code(plan), "OPERATION_ID_MISMATCH")

    def test_canonical_prestate_hash_change_blocks(self):
        roots = self.fixture.rewrite_state(lambda state: state.update(status="changed"), bind_expected=False)
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "CANONICAL_PRESTATE_CHANGED")

    def test_ledger_hash_change_blocks(self):
        roots = self.fixture.roots()
        self.fixture.target_path.write_bytes(self.fixture.target_path.read_bytes() + b"\n")
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "LEDGER_PRESTATE_HASH_MISMATCH")

    def test_symlink_target_blocks(self):
        original = self.fixture.target_path.read_bytes()
        self.fixture.target_path.unlink()
        self.fixture.target_path.symlink_to(self.fixture.operation_path)
        plan = executor.RecoveryLedgerExecutor(self.fixture.roots(bind_expected=False)).plan()
        self.assertEqual(self.code(plan), "UNEXPECTED_SYMLINK")
        self.assertEqual(original, canonical_bytes(self.fixture.budget))

    def test_runtime_path_escape_blocks(self):
        roots = self.fixture.rewrite_state(lambda state: state["r6c_batch4_recovery_e1_reconciliation"].update(runtime_root=str(self.fixture.root / "outside")))
        plan = executor.RecoveryLedgerExecutor(roots).plan()
        self.assertEqual(self.code(plan), "RUNTIME_ROOT_OUTSIDE_AUTHORITY")

    def test_plan_allowed_fields_are_exact(self):
        plan = self.fixture.run_plan()
        self.assertEqual(plan["allowed_fields"], list(executor.EXPECTED_LEDGER_MISSING_FIELDS))
        self.assertEqual([item["field"] for item in plan["expected_changes"]], list(executor.EXPECTED_LEDGER_MISSING_FIELDS))

    def test_plan_expected_diff_uses_official_values(self):
        plan = self.fixture.run_plan()
        initial = executor._official_initial(self.fixture.state, self.fixture.budget)
        values = {item["field"]: item.get("expected_value") for item in plan["expected_changes"] if "expected_value" in item}
        self.assertEqual(values["episode_id"], initial["episode_id"])
        self.assertEqual(values["episode_family_id"], initial["episode_family_id"])
        self.assertEqual(values["family_contract_sha256"], initial["family_contract_sha256"])
        self.assertEqual(values["logical_calls"], {})
        self.assertEqual(plan["expected_changes"][-1]["value_policy"], "fresh_official_initializer_timestamp")

    def test_family_contract_hash_is_official(self):
        initial = executor._official_initial(self.fixture.state, self.fixture.budget)
        self.assertEqual(initial["family_contract_sha256"], sha(canonical_bytes({key: value for key, value in initial["family_contract"].items() if key not in ("episode_family_id", "family_contract_sha256")})))

    def test_envelope_and_operational_facts_are_preserved_in_plan(self):
        plan = self.fixture.run_plan()
        self.assertIn("ledger_envelope_1_1_0_and_zero_consumption", plan["preconditions"])
        self.assertIn("reservations_empty_and_calls_attempts_absent", plan["preconditions"])

    def test_backup_requirements_are_persistent_and_external(self):
        plan = self.fixture.run_plan()
        self.assertTrue(plan["backup_requirements"]["persistent"])
        self.assertTrue(plan["backup_requirements"]["outside_runtime_target"])
        self.assertIn("PROJECT_STATE.json", plan["backup_requirements"]["contents"])

    def test_rollback_policy_is_not_retry(self):
        policy = self.fixture.run_plan()["rollback_policy"]
        self.assertFalse(policy["automatic_retry"])
        self.assertEqual(policy["max_retries"], 0)
        self.assertEqual(policy["max_rollback_attempts"], 1)
        self.assertFalse(policy["rollback_is_retry"])

    def test_fixture_apply_creates_backup_before_atomic_publish(self):
        result = self.fixture.apply()
        self.assertEqual(result["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        backup = Path(result["backup"]["directory"])
        self.assertTrue(backup.is_dir())
        self.assertEqual((backup / "episode-budget.json.before").read_bytes(), canonical_bytes(self.fixture._budget()))
        self.assertEqual((backup / "operation.json").read_bytes(), self.fixture.operation_path.read_bytes())
        self.assertTrue((backup / "PROJECT_STATE.json").exists())
        self.assertTrue((backup / "HANDOFF_CHATGPT.md").exists())
        self.assertTrue((backup / "manifest.json").exists())
        post = json.loads(self.fixture.target_path.read_bytes())
        self.assertEqual(post["initial_consumed"], 0)
        self.assertEqual(post["reservations"], [])
        self.assertTrue(all(field in post for field in executor.EXPECTED_LEDGER_MISSING_FIELDS))

    def test_backup_manifest_hashes_are_correct(self):
        result = self.fixture.apply()
        backup = Path(result["backup"]["directory"])
        manifest = json.loads((backup / "manifest.json").read_bytes())
        for item in manifest["files"]:
            data = (backup / item["name"]).read_bytes()
            self.assertEqual(item["sha256"], sha(data))

    def test_existing_backup_is_not_overwritten(self):
        first = self.fixture.apply()
        original = canonical_bytes(self.fixture._budget())
        self.fixture.target_path.write_bytes(original)
        with mock.patch.object(executor.RecoveryLedgerExecutor, "_bundle_path", return_value=Path(first["backup"]["directory"])):
            second = self.fixture.apply()
        self.assertEqual(second["terminal_state"], "FAIL_STOP")
        self.assertEqual(second["prestate"], "BACKUP_ALREADY_EXISTS")
        self.assertEqual(self.fixture.target_path.read_bytes(), original)
        self.assertTrue(Path(first["backup"]["directory"]).exists())

    def test_post_validation_failure_rolls_back_exact_bytes(self):
        original = self.fixture.target_path.read_bytes()
        result = self.fixture.apply(force_post_validation_failure=True)
        self.assertEqual(result["terminal_state"], "EXECUTION_FAILED_ROLLED_BACK")
        self.assertEqual(self.fixture.target_path.read_bytes(), original)
        self.assertEqual(result["publish_attempts"], 1)
        self.assertEqual(result["rollback_attempts"], 1)

    def test_rollback_does_not_reapply(self):
        result = self.fixture.apply(force_post_validation_failure=True)
        self.assertNotEqual(result["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        self.assertEqual(result["publish_attempts"], 1)
        self.assertEqual(result["rollback_attempts"], 1)

    def test_backup_inside_runtime_blocks_before_publish(self):
        original = self.fixture.target_path.read_bytes()
        roots = self.fixture.roots(backup_root=self.fixture.runtime / "backup")
        result = self.fixture.apply(roots=roots)
        self.assertEqual(result["terminal_state"], "FAIL_STOP")
        self.assertEqual(self.fixture.target_path.read_bytes(), original)

    def test_mode_is_preserved(self):
        before = stat.S_IMODE(self.fixture.target_path.stat().st_mode)
        result = self.fixture.apply()
        self.assertEqual(result["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        self.assertEqual(stat.S_IMODE(self.fixture.target_path.stat().st_mode), before)

    def test_postwrite_validation_preserves_all_existing_values(self):
        original = copy.deepcopy(self.fixture.budget)
        result = self.fixture.apply()
        self.assertEqual(result["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        post = json.loads(self.fixture.target_path.read_bytes())
        for key, value in original.items():
            self.assertEqual(post[key], value)

    def test_failed_backup_leaves_target_unchanged(self):
        original = self.fixture.target_path.read_bytes()
        roots = self.fixture.roots(backup_root=self.fixture.runtime / "nested-backup")
        result = self.fixture.apply(roots=roots)
        self.assertEqual(result["terminal_state"], "FAIL_STOP")
        self.assertEqual(self.fixture.target_path.read_bytes(), original)

    def test_fixture_is_real_equivalent_transformation(self):
        original = json.loads(self.fixture.target_path.read_bytes())
        result = self.fixture.apply()
        self.assertEqual(result["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        post = json.loads(self.fixture.target_path.read_bytes())
        self.assertEqual(set(post) - set(original), set(executor.EXPECTED_LEDGER_MISSING_FIELDS))
        self.assertEqual(post["initial_consumed"], original["initial_consumed"])
        self.assertEqual(post["retry_consumed"], original["retry_consumed"])
        self.assertEqual(post["reservations"], original["reservations"])

    def test_fixture_context_is_temporary_and_explicit(self):
        context = self.fixture.context()
        root = context.temporary_root.resolve()
        for path in (context.roots.authority_root, context.roots.candidate_root,
                     context.runtime_root, context.target_ledger,
                     context.operation_file, context.lock_file, context.backup_root):
            path.resolve(strict=False).relative_to(root)
        self.assertIsNone(executor._fixture_context_issue(context))

    def test_fixture_root_escape_blocks_before_write(self):
        context = self.fixture.context()
        escaped = executor.FixtureExecutionContext(
            temporary_root=context.temporary_root,
            roots=context.roots,
            runtime_root=context.runtime_root,
            target_ledger=Path("/outside-temporary/episode-budget.json"),
            operation_file=context.operation_file,
            lock_file=context.lock_file,
            backup_root=context.backup_root,
        )
        original = self.fixture.target_path.read_bytes()
        result = executor.apply_fixture_context(escaped)
        self.assertEqual(result["terminal_state"], "FAIL_STOP")
        self.assertIn("TEST_FIXTURE_CONTEXT_INCOMPLETE", {x["code"] for x in result["unknowns"]})
        self.assertEqual(self.fixture.target_path.read_bytes(), original)

    def test_fixture_symlink_escape_blocks_before_write(self):
        self.fixture.target_path.unlink()
        self.fixture.target_path.symlink_to("/outside-temporary/episode-budget.json")
        result = executor.apply_fixture_context(self.fixture.context(roots=self.fixture.roots(bind_expected=False)))
        self.assertEqual(result["terminal_state"], "FAIL_STOP")
        self.assertIn("TEST_FIXTURE_ROOT_ESCAPE", {x["code"] for x in result["blockers"]})

    def test_fixture_context_incomplete_blocks_before_write(self):
        context = self.fixture.context()
        incomplete = executor.FixtureExecutionContext(
            temporary_root=context.temporary_root,
            roots=context.roots,
            runtime_root=context.runtime_root,
            target_ledger=context.target_ledger,
            operation_file=context.operation_file,
            lock_file=context.lock_file,
            backup_root=context.runtime_root / "not-the-roots-backup",
        )
        result = executor.apply_fixture_context(incomplete)
        self.assertEqual(result["terminal_state"], "FAIL_STOP")
        self.assertTrue(result["unknowns"])

    def test_lock_unavailable_has_zero_fixture_writes(self):
        original = self.fixture.target_path.read_bytes()
        def unavailable(_, operation):
            if operation & executor.fcntl.LOCK_UN:
                return None
            raise BlockingIOError()

        with mock.patch.object(executor.fcntl, "flock", side_effect=unavailable):
            result = self.fixture.apply()
        self.assertEqual(result["terminal_state"], "FAIL_STOP")
        self.assertIn("EXECUTION_LOCK_UNAVAILABLE", {x["code"] for x in result["blockers"]})
        self.assertEqual(self.fixture.target_path.read_bytes(), original)
        self.assertFalse(self.fixture.backups.exists())

    def test_rollback_failure_is_critical_fail_stop(self):
        original_write = executor.RecoveryLedgerExecutor._atomic_write
        calls = []

        def fail_only_rollback(path, data, original_stat):
            calls.append(path)
            if len(calls) == 1:
                return original_write(path, data, original_stat)
            raise executor.ExecutorIssue("ROLLBACK_INJECTED_FAILURE")

        with mock.patch.object(executor.RecoveryLedgerExecutor, "_atomic_write", side_effect=fail_only_rollback):
            result = self.fixture.apply(force_post_validation_failure=True)
        self.assertEqual(result["terminal_state"], "CRITICAL_FAIL_STOP")
        self.assertEqual(result["rollback_attempts"], 1)

    def test_fixture_apply_never_writes_outside_temporary_directory(self):
        context = self.fixture.context()
        result = executor.apply_fixture_context(context)
        self.assertEqual(result["terminal_state"], "EXECUTION_SUCCESS_PRE_AUDIT")
        context.temporary_root.resolve(strict=True)
        Path(result["backup"]["directory"]).resolve(strict=True).relative_to(context.temporary_root.resolve(strict=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
