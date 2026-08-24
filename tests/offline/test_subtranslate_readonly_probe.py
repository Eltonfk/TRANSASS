import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("readonly_probe", ROOT / ".opencode/tools/subtranslate_readonly_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.authority = self.root / "authority"
        self.runtime_parent = self.authority / "runtime-evidence"
        self.runtime = self.runtime_parent / "B4"
        self.runtime.mkdir(parents=True)
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.candidate / ".git").mkdir()
        for relative in set(probe.EXECUTION_TOOLCHAIN_COMPONENTS + probe.B4_EXECUTION_TOOLCHAIN_COMPONENTS + probe.B5_EXECUTION_TOOLCHAIN_COMPONENTS):
            component = self.candidate / relative
            component.parent.mkdir(parents=True, exist_ok=True)
            component.write_text(f"fixture:{relative}\n", encoding="utf-8")
        self.state_path = self.authority / "PROJECT_STATE.json"
        self.handoff_path = self.authority / "HANDOFF_CHATGPT.md"
        self.write_state(e1_top=False, e1_nested=True)
        self.handoff_path.write_text("PHASE E1 RECONCILIATION PREFLIGHT A C2 criou fisicamente operation_id BLOCKED_BEFORE_RESERVATION_AND_TRANSPORT", encoding="utf-8")
        self.write_runtime()
        self.patch = mock.patch.multiple(probe, CANDIDATE_ROOT=str(self.candidate), AUTHORITY_ROOT=str(self.authority), RUNTIME_PARENT=str(self.runtime_parent))
        self.patch.start()
        self.git = mock.patch.object(probe.subprocess, "run", side_effect=self.git_run)
        self.git.start()

    def tearDown(self):
        self.git.stop(); self.patch.stop(); self.tmp.cleanup()

    def write_state(self, e1_top=False, e1_nested=True, e1_value=None):
        e1 = e1_value or {"runtime_root": str(self.runtime)}
        proto = {"r6c_batch4_recovery_e1_reconciliation": e1} if e1_nested else {}
        state = {"current_operation": "op", "state": "state", "status": "status", "latest_decision": "decision", "next_action": "action",
                 "r6c_batch4_recovery_protocol": proto}
        if e1_top: state["r6c_batch4_recovery_e1_reconciliation"] = e1
        self.authority.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def write_runtime(self, budget=None):
        (self.runtime / "operation.json").write_text(json.dumps({"operation_id": "op-id"}), encoding="utf-8")
        (self.runtime / "episode-budget.json").write_text(json.dumps(budget or {"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1, "initial_consumed": 0, "retry_consumed": 0, "reservations": []}), encoding="utf-8")
        (self.runtime / "episode-budget.json.lock").write_bytes(b"")

    def git_run(self, argv, **kwargs):
        out = {"--show-current": "candidate/v2.3.8\n", "HEAD": "h\n", "HEAD^{tree}": "t\n", "--porcelain": ""}
        return subprocess.CompletedProcess(argv, 0, stdout=out.get(argv[2], ""), stderr="")

    def probe_run(self): return probe.probe()

    def test_normal_nested_and_empty_reservations(self):
        self.write_runtime({"episode_id": 79, "episode_family_id": "fam", "family_contract": "c", "family_contract_sha256": "h", "logical_calls": [], "updated_at": "u", "planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1, "initial_consumed": 0, "retry_consumed": 0, "reservations": []})
        r = self.probe_run(); self.assertFalse(r["blockers"]); self.assertEqual(r["canonical"]["r6c_batch4_recovery_e1_reconciliation"]["occurrence_count"], 1); self.assertEqual(r["runtime"]["episode_budget"]["reservation_count"], 0)

    def test_e1_top_nested_both_neither(self):
        for top, nested, count in [(True, False, 1), (True, True, 2), (False, False, 0)]:
            self.write_state(top, nested); r = self.probe_run(); info = r["canonical"]["r6c_batch4_recovery_e1_reconciliation"]; self.assertEqual(info["present_top_level"], top); self.assertEqual(info["occurrence_count"], count)

    def test_e1_top_level_only_real_equivalent(self):
        self.write_state(e1_top=True, e1_nested=False)
        r = self.probe_run()
        info = r["canonical"]["r6c_batch4_recovery_e1_reconciliation"]
        self.assertTrue(info["present_top_level"])
        self.assertFalse(info["nested_inside_recovery_protocol"])
        self.assertEqual(info["occurrence_count"], 1)

    def test_complete_ledger_and_reservations(self):
        budget = {"episode_id": 79, "episode_family_id": "fam", "family_contract": "c", "family_contract_sha256": "h", "logical_calls": [], "updated_at": "u", "planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1, "initial_consumed": 1, "retry_consumed": 0, "reservations": [{"id": "r"}]}
        self.write_runtime(budget); r = self.probe_run(); self.assertEqual(r["runtime"]["episode_budget"]["reservation_count"], 1); self.assertTrue(all(r["runtime"]["episode_budget"]["fields_present"].values()))

    def test_missing_ledger_identity_is_block_and_known_absences_are_facts(self):
        r = self.probe_run()
        self.assertIn("RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE", {x["code"] for x in r["blockers"]})
        self.assertEqual(r["unknowns"], [])
        self.assertFalse(r["runtime"]["calls_attempts"]["calls_dir_exists"])
        self.assertEqual(r["runtime"]["calls_attempts"]["attempt_count"], 0)
        evidence = r["runtime"]["B5_B6_B7_evidence"]
        self.assertFalse(evidence["b5_evidence_exists"]); self.assertFalse(evidence["b6_evidence_exists"]); self.assertFalse(evidence["b7_evidence_exists"])

    def test_invalid_and_missing_files_are_not_pass(self):
        (self.runtime / "operation.json").write_text("{", encoding="utf-8"); r = self.probe_run(); self.assertIn("INVALID_JSON", {x["code"] for x in r["blockers"]})
        (self.runtime / "operation.json").unlink(); r = self.probe_run(); self.assertTrue(r["unknowns"])

    def test_symlink_and_traversal(self):
        (self.runtime / "operation.json").unlink(); (self.runtime / "operation.json").symlink_to(self.state_path); r = self.probe_run(); self.assertIn("UNEXPECTED_SYMLINK", {x["code"] for x in r["unknowns"]})
        self.write_state(e1_nested=True, e1_value={"runtime_root": str(self.authority / "outside")}); r = self.probe_run(); self.assertIn("RUNTIME_ROOT_OUTSIDE_AUTHORITY", {x["code"] for x in r["blockers"]})

    def test_attempt_present_and_mismatch(self):
        (self.runtime / "calls").mkdir(); (self.runtime / "calls" / "attempt-1").mkdir(); self.write_runtime({"planned_initial_calls": 1, "retry_reserve": 0, "physical_ceiling": 1, "initial_consumed": 1, "retry_consumed": 1, "reservations": []}); r = self.probe_run(); self.assertEqual(r["runtime"]["calls_attempts"]["attempt_count"], 1); self.assertEqual(r["accounting"]["comparisons"]["initial_consumed"], "UNKNOWN")

    def test_git_dirty_known_unknown_timeout_failure(self):
        def dirty(argv, **kwargs): return subprocess.CompletedProcess(argv, 0, stdout=" M file\n?? .opencode/foo\n?? mystery\n" if argv[1] == "status" else "x\n", stderr="")
        with mock.patch.object(probe.subprocess, "run", side_effect=dirty): r = self.probe_run()
        self.assertIn("CANDIDATE_TRACKED_DIRTY", {x["code"] for x in r["blockers"]}); self.assertIn("mystery", r["candidate_git"]["untracked_unknown"])
        with mock.patch.object(probe.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)): r = self.probe_run()
        self.assertIn("GIT_TIMEOUT", {x["code"] for x in r["unknowns"]})

    def test_determinism_and_zero_writes(self):
        before = {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}; a = json.dumps(self.probe_run(), sort_keys=True, separators=(",", ":")); b = json.dumps(self.probe_run(), sort_keys=True, separators=(",", ":")); self.assertEqual(a, b); self.assertEqual(before, {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}); self.assertFalse(self.probe_run()["integrity"]["side_effects_performed"])

    def test_argument_exit_and_policy_source(self):
        self.assertEqual(probe.main(["unexpected"]), 4)
        source = (ROOT / ".opencode/tools/subtranslate_readonly_probe.py").read_text()
        self.assertNotIn("shell=True", source); self.assertIn("shell=False", source); self.assertNotIn("git commit", source); self.assertNotIn("git add", source); self.assertNotIn("git reset", source); self.assertNotIn("git clean", source); self.assertNotIn("urlopen", source)

    def test_large_file_permission_and_read_change(self):
        with mock.patch.object(probe, "MAX_RUNTIME_BYTES", 1):
            r = self.probe_run()
        self.assertIn("IMPORTANT_FILE_TOO_LARGE", {x["code"] for x in r["unknowns"]})
        with mock.patch("builtins.open", side_effect=PermissionError()):
            r = self.probe_run()
        self.assertEqual(r["integrity"]["side_effects_performed"], False)
        unknown_codes = {x["code"] for x in r["unknowns"]}
        self.assertTrue(unknown_codes or r["blockers"])

    def test_git_failure_and_runtime_canonical_mismatch_is_observable(self):
        self.write_state(e1_nested=True, e1_value={"runtime_root": str(self.runtime), "accounting": {"canonical_before": {"recovery_family_consumed": 99, "r6c_retries": 0}}})
        with mock.patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="failure")):
            r = self.probe_run()
        self.assertIn("GIT_FAILURE", {x["code"] for x in r["unknowns"]})
        self.assertEqual(r["accounting"]["comparisons"]["initial_consumed"], "MISMATCH")

    def test_file_changes_during_read(self):
        target = self.runtime / "changing.json"
        target.write_bytes(b"{}")
        before = mock.Mock(st_mode=stat.S_IFREG, st_size=2, st_dev=1, st_ino=1, st_mtime_ns=1)
        after = mock.Mock(st_mode=stat.S_IFREG, st_size=2, st_dev=1, st_ino=1, st_mtime_ns=2)
        issues = []
        with mock.patch.object(probe.os, "lstat", side_effect=[before, after]):
            data, _ = probe.read_consistent(str(target), 100, issues, [])
        self.assertIsNone(data); self.assertEqual(issues[0]["code"], "FILE_CHANGED_DURING_READ")

    def test_handoff_marker_variation_does_not_make_global_unknown(self):
        self.handoff_path.write_text("E1 addendum; C2 created runtime operation_id; Phase D blocked before reservation", encoding="utf-8")
        r = self.probe_run()
        self.assertEqual(r["unknowns"], [])
        self.assertFalse(r["canonical"]["handoff"]["e1_addendum_present"])
        self.assertEqual(r["canonical"]["handoff"]["indeterminate"], [])

    def test_exit_policy_zero_two_three(self):
        base = {"unknowns": [], "blockers": []}
        with mock.patch.object(probe, "probe", return_value={**base, "unknowns": [], "blockers": []}): self.assertEqual(probe.main([]), 0)
        with mock.patch.object(probe, "probe", return_value={**base, "unknowns": [], "blockers": [{"code": "B"}]}): self.assertEqual(probe.main([]), 2)
        with mock.patch.object(probe, "probe", return_value={**base, "unknowns": [{"code": "U"}], "blockers": [{"code": "B"}]}): self.assertEqual(probe.main([]), 3)

    def test_execution_toolchain_section_and_allowlist(self):
        result = self.probe_run()
        toolchain = result["execution_toolchain"]
        self.assertEqual(toolchain["action_id"], "RECOVERY_LEDGER_REPREPARATION")
        self.assertEqual(toolchain["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")
        self.assertEqual([item["path"] for item in toolchain["components"]], list(probe.EXECUTION_TOOLCHAIN_COMPONENTS))
        self.assertTrue(all(len(item["sha256"]) == 64 for item in toolchain["components"]))
        self.assertEqual(len(toolchain["execution_toolchain_fingerprint"]), 64)
        self.assertEqual(
            toolchain["components"][-1]["path"],
            ".opencode/commands/subtranslate-next.md",
        )

    def test_b4_execution_toolchain_is_separate_and_materialized(self):
        result = self.probe_run()
        toolchain = result["execution_toolchains"]["B4_RECOVERY_CALL_EXECUTION"]
        self.assertEqual(toolchain["executor_id"], "B4_RECOVERY_CALL_EXECUTOR_V1")
        self.assertTrue(toolchain["materialized"])
        self.assertEqual(toolchain["model_binding"]["model_tag"], "qwen3.5:9b")
        self.assertEqual(toolchain["transport_guard"]["max_http_posts"], 1)
        self.assertEqual(toolchain["transport_guard"]["max_retries"], 0)
        self.assertNotEqual(
            toolchain["execution_toolchain_fingerprint"],
            result["execution_toolchain"]["execution_toolchain_fingerprint"],
        )

    def test_b4_next_action_selects_b4_current_toolchain(self):
        state = json.loads(self.state_path.read_text())
        state["next_action"] = "B4_RECOVERY_CALL_PREFLIGHT_READ_ONLY_REQUIRED"
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        result = self.probe_run()
        self.assertEqual(result["current_execution_toolchain"]["action_id"], "B4_RECOVERY_CALL_EXECUTION")

    def test_b5_execution_toolchain_is_separate_and_materialized(self):
        result = self.probe_run()
        toolchain = result["execution_toolchains"]["B5_BATCH_EXECUTION"]
        self.assertEqual(toolchain["executor_id"], "B5_BATCH_EXECUTOR_V1")
        self.assertTrue(toolchain["materialized"])
        self.assertEqual(toolchain["model_binding"]["model_tag"], "qwen3.5:9b")
        self.assertEqual(toolchain["transport_guard"]["max_http_posts"], 1)
        self.assertEqual(toolchain["transport_guard"]["max_retries"], 0)
        self.assertNotEqual(
            toolchain["execution_toolchain_fingerprint"],
            result["execution_toolchains"]["B4_RECOVERY_CALL_EXECUTION"]["execution_toolchain_fingerprint"],
        )

    def test_b5_next_action_selects_b5_current_toolchain(self):
        state = json.loads(self.state_path.read_text())
        state["next_action"] = "B5_PREFLIGHT_READ_ONLY_REQUIRED"
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        result = self.probe_run()
        self.assertEqual(result["current_execution_toolchain"]["action_id"], "B5_BATCH_EXECUTION")

    def test_b5_missing_component_is_fail_closed(self):
        component = self.candidate / probe.B5_EXECUTION_TOOLCHAIN_COMPONENTS[0]
        component.unlink()
        result = self.probe_run()
        toolchain = result["execution_toolchains"]["B5_BATCH_EXECUTION"]
        self.assertIsNone(toolchain["execution_toolchain_fingerprint"])
        self.assertFalse(toolchain["materialized"])
        self.assertIn(
            "EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE",
            {item["code"] for item in result["unknowns"] + result["blockers"]},
        )

    def test_execution_toolchain_fingerprint_is_deterministic(self):
        first = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        second = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        self.assertEqual(first, second)

    def test_executor_change_changes_toolchain_only(self):
        component = self.candidate / probe.EXECUTION_TOOLCHAIN_COMPONENTS[0]
        before = self.probe_run()
        original = component.read_bytes()
        component.write_bytes(original + b"changed")
        try:
            after = self.probe_run()
        finally:
            component.write_bytes(original)
        self.assertNotEqual(before["execution_toolchain"]["execution_toolchain_fingerprint"], after["execution_toolchain"]["execution_toolchain_fingerprint"])
        self.assertIn("execution_toolchain", after)

    def test_durability_source_change_changes_toolchain(self):
        component = self.candidate / probe.EXECUTION_TOOLCHAIN_COMPONENTS[3]
        original = component.read_bytes()
        before = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        component.write_bytes(original + b"changed")
        try:
            after = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        finally:
            component.write_bytes(original)
        self.assertNotEqual(before, after)

    def test_orchestrator_change_changes_toolchain(self):
        component = self.candidate / probe.EXECUTION_TOOLCHAIN_COMPONENTS[1]
        original = component.read_bytes()
        before = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        component.write_bytes(original + b"changed")
        try:
            after = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        finally:
            component.write_bytes(original)
        self.assertNotEqual(before, after)

    def test_probe_change_changes_toolchain(self):
        component = self.candidate / probe.EXECUTION_TOOLCHAIN_COMPONENTS[2]
        original = component.read_bytes()
        before = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        component.write_bytes(original + b"changed")
        try:
            after = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        finally:
            component.write_bytes(original)
        self.assertNotEqual(before, after)

    def test_command_one_byte_change_and_restore_changes_then_restores_toolchain(self):
        component = self.candidate / ".opencode/commands/subtranslate-next.md"
        original = component.read_bytes()
        before = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        component.write_bytes(original + b"x")
        changed = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        component.write_bytes(original)
        restored = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        self.assertNotEqual(before, changed)
        self.assertEqual(before, restored)

    def test_command_missing_is_fail_closed(self):
        component = self.candidate / ".opencode/commands/subtranslate-next.md"
        component.unlink()
        result = self.probe_run()
        self.assertIsNone(result["execution_toolchain"]["execution_toolchain_fingerprint"])
        self.assertIn(
            "EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE",
            {item["code"] for item in result["unknowns"] + result["blockers"]},
        )

    def test_command_symlink_is_fail_closed(self):
        component = self.candidate / ".opencode/commands/subtranslate-next.md"
        component.unlink()
        component.symlink_to(self.candidate / ".opencode/agents/subtranslate-orchestrator.md")
        result = self.probe_run()
        codes = {item["code"] for item in result["unknowns"] + result["blockers"]}
        self.assertIsNone(result["execution_toolchain"]["execution_toolchain_fingerprint"])
        self.assertIn("UNEXPECTED_SYMLINK", codes)
        self.assertIn("EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE", codes)

    def test_command_toctou_is_fail_closed(self):
        command_path = self.candidate / ".opencode/commands/subtranslate-next.md"
        command = str(command_path)
        saved = command_path.with_suffix(".saved")
        target = self.candidate / ".opencode/agents/subtranslate-orchestrator.md"
        real_open = probe.os.open

        def swap_before_open(path, flags, *args):
            if path != command:
                return real_open(path, flags, *args)
            command_path.rename(saved)
            command_path.symlink_to(target)
            try:
                return real_open(path, flags, *args)
            finally:
                command_path.unlink()
                saved.rename(command_path)

        with mock.patch.object(probe.os, "open", side_effect=swap_before_open):
            result = self.probe_run()
        codes = {item["code"] for item in result["unknowns"] + result["blockers"]}
        self.assertIsNone(result["execution_toolchain"]["execution_toolchain_fingerprint"])
        self.assertTrue({"FILE_CHANGED_DURING_READ", "FILE_READ_ERROR"} & codes)
        self.assertIn("EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE", codes)

    def test_irrelevant_file_does_not_change_toolchain(self):
        before = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        (self.candidate / "irrelevant.txt").write_text("irrelevant", encoding="utf-8")
        after = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        self.assertEqual(before, after)

    def test_unrelated_test_file_does_not_change_toolchain(self):
        before = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        test_file = self.candidate / "tests/offline/unrelated_test.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("# unrelated\n", encoding="utf-8")
        after = self.probe_run()["execution_toolchain"]["execution_toolchain_fingerprint"]
        self.assertEqual(before, after)

    def test_missing_toolchain_component_is_unknown_or_block(self):
        (self.candidate / probe.EXECUTION_TOOLCHAIN_COMPONENTS[0]).unlink()
        result = self.probe_run()
        codes = {item["code"] for item in result["unknowns"] + result["blockers"]}
        self.assertIn("EXECUTION_TOOLCHAIN_COMPONENT_UNAVAILABLE", codes)

    def test_snapshot_and_toolchain_fingerprints_are_separate(self):
        result = self.probe_run()
        self.assertIn("snapshot_fingerprint", result)
        self.assertIn("execution_toolchain_fingerprint", result["execution_toolchain"])
        self.assertNotEqual(result["snapshot_fingerprint"], result["execution_toolchain"]["execution_toolchain_fingerprint"])

    def test_toolchain_has_no_external_hash_or_network_path(self):
        source = (ROOT / ".opencode/tools/subtranslate_readonly_probe.py").read_text(encoding="utf-8")
        self.assertNotIn("sha256sum", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__": unittest.main(verbosity=2)
