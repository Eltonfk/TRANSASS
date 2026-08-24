import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / ".opencode" / "agents"

OLD_ACTION = "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION"
CURRENT_ACTION = "EXTERNAL_DECISION_REQUIRED_NO_AUTOMATIC_RESEND"
PRE_HASH = "f434a4718e0d32cd8f4b3bd7548fbed6a1ce428b0a77264b396236b8928539cc"
POST_HASH = "32e641be94c59343f71259534049a250cf75ef89fee6bdf10beabf0842ad0d8e"
COMMIT = "d548553bc1bc5d43e69aa2ecab9a42a5102b0568"
TREE = "0d5e64f2b4645f356335769bb72a2f72cf5f36fa"
FAMILY = "V238_E07_R6C_B4_RECOVERY"
OPERATION = "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z"
JOURNAL = ["ISSUED_PENDING", "ISSUED", "CLAIMED", "EXECUTOR_STARTED", "EXECUTOR_EXITED", "SUCCEEDED"]
BACKUP = "/var/lib/subtranslate-guard/backups/RECOVERY_LEDGER_REPREPARATION_V2-SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z-20260822T114249.364695Z-030cc3404fe2a051"

R4_CLOSURE_EVIDENCE_SET = [
    {
        "path/source": "/home/palhacinho/codex-projects/anime-subtitle-translator-review/runtime-evidence/V238_E07_R6C_B4_RECOVERY/episode-budget.json",
        "identity": {"episode_id": 79, "family_id": FAMILY, "operation_id": OPERATION},
        "timestamp/ordering_data": "target post timestamp follows the terminal journal chain",
        "hash/fingerprint": {"pre_sha256": PRE_HASH, "post_sha256": POST_HASH},
        "relationship_to_B4": "B4 target identity and final ledger state",
        "terminal_status": "COMPLETE / target changed exactly once",
        "authority_role": "CURRENT_RUNTIME_TARGET",
    },
    {
        "path/source": f"{BACKUP}/manifest.json",
        "identity": {"action_id": "RECOVERY_LEDGER_REPREPARATION", "executor_id": "RECOVERY_LEDGER_REPREPARATION_V2", "family_id": FAMILY, "operation_id": OPERATION},
        "timestamp/ordering_data": "backup precedes target publish in the terminal chain",
        "hash/fingerprint": {"target_pre_sha256": PRE_HASH, "manifest": "PASS"},
        "relationship_to_B4": "persistent pre-write recovery backup",
        "terminal_status": "PASS",
        "authority_role": "BACKUP_INTEGRITY_EVIDENCE",
    },
    {
        "path/source": f"/var/lib/subtranslate-guard/terminal/9609c4187a03b967a07a0036223e206e6813b6def39b4b68780dab047289ee0a.json",
        "identity": {"capability_id": "9609c4187a03b967a07a0036223e206e6813b6def39b4b68780dab047289ee0a"},
        "timestamp/ordering_data": "terminal after claim and executor exit",
        "hash/fingerprint": {"capability_id": "9609c4187a03b967a07a0036223e206e6813b6def39b4b68780dab047289ee0a"},
        "relationship_to_B4": "one-shot authorization chain for this recovery object",
        "terminal_status": "ARMED_EMPTY_CLAIMED_EMPTY_TERMINAL_SUCCEEDED",
        "authority_role": "TERMINAL_CAPABILITY_AUTHORIZATION",
    },
    {
        "path/source": f"/var/lib/subtranslate-guard/journal/9609c4187a03b967a07a0036223e206e6813b6def39b4b68780dab047289ee0a.jsonl",
        "identity": {"capability_id": "9609c4187a03b967a07a0036223e206e6813b6def39b4b68780dab047289ee0a", "family_id": FAMILY, "operation_id": OPERATION},
        "timestamp/ordering_data": JOURNAL[:],
        "hash/fingerprint": {"event_count": 6},
        "relationship_to_B4": "claim, executor and terminal transition ordering",
        "terminal_status": "SUCCEEDED",
        "authority_role": "CAUSAL_ORDERING_EVIDENCE",
    },
    {
        "path/source": "supplied gate report AUTO03B2B_GUARD_B4_POST_EXECUTION_AUDIT_R4_RESULT",
        "identity": {"source_commit": COMMIT, "source_tree": TREE, "family_id": FAMILY, "operation_id": OPERATION},
        "timestamp/ordering_data": "after B4 execution and canonical reconciliation",
        "hash/fingerprint": {"manifest_sha256": "6820f3c69f1208ddd8adc585bd4960d5b81d3b508888f83b7f4a24f13d625407"},
        "relationship_to_B4": "read-only closure audit",
        "terminal_status": "PASS / NEXT_GATE=STOP",
        "authority_role": "POST_EXECUTION_CLOSURE_AUDIT",
    },
    {
        "path/source": "/home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/tools/subtranslate_readonly_probe.py",
        "identity": {"source_commit": COMMIT, "source_tree": TREE, "family_id": FAMILY, "operation_id": OPERATION},
        "timestamp/ordering_data": "fresh probe after canonical reconciliation",
        "hash/fingerprint": {"target_post_sha256": POST_HASH},
        "relationship_to_B4": "post-closure physical consistency check",
        "terminal_status": "exit=0 / blockers=[] / unknowns=[] / snapshot_consistent=true",
        "authority_role": "FRESH_READ_ONLY_PROBE",
    },
    {
        "path/source": "/home/palhacinho/codex-projects/anime-subtitle-translator-review/PROJECT_STATE.json",
        "identity": {"current_next_action": CURRENT_ACTION, "family_id": FAMILY, "operation_id": OPERATION},
        "timestamp/ordering_data": "canonical post-action follows R4 reconciliation",
        "hash/fingerprint": {"post_sha256": "c6c5eeca02c77d251a5ce5db32f2cba9f4a309cfa6e7807cda974d31e6acf905"},
        "relationship_to_B4": "current operational routing authority only",
        "terminal_status": "CURRENT_NEXT_ACTION",
        "authority_role": "CURRENT_CANONICAL_AUTHORITY",
    },
]


def _historical_snapshot():
    return {
        "canonical": {"next_action": OLD_ACTION},
        "runtime": {
            "episode_id": 79,
            "family_id": FAMILY,
            "operation_id": OPERATION,
            "identity_state": "INCOMPLETE",
            "target_sha256": PRE_HASH,
        },
        "candidate_git": {"head": COMMIT, "tree": TREE},
    }


def _current_snapshot():
    return {
        "canonical": {"next_action": CURRENT_ACTION},
        "runtime": {
            "episode_id": 79,
            "family_id": FAMILY,
            "operation_id": OPERATION,
            "identity_state": "COMPLETE",
            "target_sha256": POST_HASH,
        },
        "candidate_git": {"head": COMMIT, "tree": TREE},
    }


def _valid_r4_evidence():
    return {
        "source_commit": COMMIT,
        "source_tree": TREE,
        "family_id": FAMILY,
        "episode_id": 79,
        "operation_id": OPERATION,
        "target_pre_sha256": PRE_HASH,
        "target_post_sha256": POST_HASH,
        "backup_manifest": "PASS",
        "capability_state": "ARMED_EMPTY_CLAIMED_EMPTY_TERMINAL_SUCCEEDED",
        "journal_events": JOURNAL[:],
        "claim_count": 1,
        "apply_count": 1,
        "retry_count": 0,
        "rearm": False,
        "audit_complete": True,
        "next_gate": "STOP",
        "probe_exit_code": 0,
        "probe_blockers": [],
        "probe_unknowns": [],
        "probe_snapshot_consistent": True,
        "probe_side_effects": False,
        "canonical_post_action": CURRENT_ACTION,
        "evidence_set": R4_CLOSURE_EVIDENCE_SET,
        # The sequence is causal evidence from the supplied R4 reports; no
        # timestamp is treated as authority by itself.
        "ordering": {
            "causal_after_historical_snapshot": True,
            "basis": [
                "E1 historical snapshot",
                "ARM_R3 terminal capability",
                "B4_EXECUTION_R3 terminal journal",
                "POST_EXECUTION_AUDIT_R4 PASS",
            ],
        },
    }


def _recognize_r4_closure(historical, current, evidence):
    """Executable contract model for the orchestrator's R4 recognition."""
    reasons = []
    h_runtime = historical.get("runtime", {})
    c_runtime = current.get("runtime", {})
    checks = {
        "historical_incomplete": h_runtime.get("identity_state") == "INCOMPLETE",
        "current_complete": c_runtime.get("identity_state") == "COMPLETE",
        "same_family": evidence.get("family_id") == FAMILY == h_runtime.get("family_id") == c_runtime.get("family_id"),
        "same_episode": evidence.get("episode_id") == 79 == h_runtime.get("episode_id") == c_runtime.get("episode_id"),
        "same_operation": evidence.get("operation_id") == OPERATION == h_runtime.get("operation_id") == c_runtime.get("operation_id"),
        "source_binding": evidence.get("source_commit") == current.get("candidate_git", {}).get("head") == COMMIT
        and evidence.get("source_tree") == current.get("candidate_git", {}).get("tree") == TREE,
        "target_transition": evidence.get("target_pre_sha256") == h_runtime.get("target_sha256") == PRE_HASH
        and evidence.get("target_post_sha256") == c_runtime.get("target_sha256") == POST_HASH,
        "backup_manifest": evidence.get("backup_manifest") == "PASS",
        "terminal_capability": evidence.get("capability_state") == "ARMED_EMPTY_CLAIMED_EMPTY_TERMINAL_SUCCEEDED",
        "complete_journal": evidence.get("journal_events") == JOURNAL,
        "exactly_once": evidence.get("claim_count") == 1 and evidence.get("apply_count") == 1,
        "no_retry_rearm": evidence.get("retry_count") == 0 and evidence.get("rearm") is False,
        "audit_terminal": evidence.get("audit_complete") is True and evidence.get("next_gate") == "STOP",
        "probe_pass": evidence.get("probe_exit_code") == 0
        and evidence.get("probe_blockers") == []
        and evidence.get("probe_unknowns") == []
        and evidence.get("probe_snapshot_consistent") is True
        and evidence.get("probe_side_effects") is False,
        "canonical_current": evidence.get("canonical_post_action") == current.get("canonical", {}).get("next_action"),
        "structured_evidence": isinstance(evidence.get("evidence_set"), list)
        and len(evidence["evidence_set"]) >= 7
        and all(all(key in item for key in ("path/source", "identity", "timestamp/ordering_data", "hash/fingerprint", "relationship_to_B4", "terminal_status", "authority_role")) for item in evidence["evidence_set"]),
        "causal_order": evidence.get("ordering", {}).get("causal_after_historical_snapshot") is True,
    }
    reasons.extend(name for name, passed in checks.items() if not passed)
    if reasons:
        return {
            "status": "DIVERGENCE_BLOCK",
            "terminal": "FAIL_STOP",
            "R4_CLOSURE_EVIDENCE_SUFFICIENT": "NO",
            "reasons": reasons,
        }
    return {
        "status": "HISTORICALLY_VALID_SUPERSEDED_BY_TERMINAL_R4",
        "terminal": "DONE",
        "R4_CLOSURE_EVIDENCE_SUFFICIENT": "YES",
        "expected_transition": "EXPECTED_RECOVERY_RESULT",
        "expected_probe_transition": "EXPECTED_POST_RECOVERY_TRANSITION",
        "RECOVERY_B4_ROUTE": "CLOSED_TERMINAL",
        "past_execution_alone_counts_as_authorization": False,
        "runtime_newer_alone_counts_as_authority": False,
    }


def _route_after_closure(current, result):
    if result.get("R4_CLOSURE_EVIDENCE_SUFFICIENT") != "YES":
        return {"route": "DIVERGENCE_BLOCK", "terminal": "FAIL_STOP"}
    next_action = current.get("canonical", {}).get("next_action")
    return {
        "route": "CURRENT_NEXT_ACTION",
        "RECOVERY_B4_ROUTE": "CLOSED_TERMINAL",
        "next_action": next_action,
        "retroactive_ratification_requested": False,
        "B4_reprepare_requested": False,
        "B4_rearm_requested": False,
        "B4_reexecution_requested": False,
        "human_gate_only_if_current_action_requires_it": True,
    }


class R4ClosureContractTests(unittest.TestCase):
    def test_agent_contract_declares_temporal_authority(self):
        text = (AGENTS / "subtranslate-orchestrator.md").read_text(encoding="utf-8")
        for token in (
            "HISTORICAL_EVIDENCE",
            "CURRENT_OPERATIONAL_AUTHORITY",
            "POSTERIOR_TERMINAL_SUPERSESSION",
            "R4_CLOSURE_EVIDENCE_SET",
            "R4_CLOSURE_EVIDENCE_SUFFICIENT=YES",
            "R4_SUPERSESSION_ORDERING_POLICY",
            "RECOVERY_B4_ROUTE=CLOSED_TERMINAL",
            "PAST_EXECUTION_ALONE_COUNTS_AS_AUTHORIZATION=NO",
            "RUNTIME_NEWER_ALONE_COUNTS_AS_AUTHORITY=NO",
            "TERMINAL_CAPABILITY_CHAIN_REQUIRED=YES",
            "EXPECTED_RECOVERY_RESULT",
            "EXPECTED_POST_RECOVERY_TRANSITION",
        ):
            self.assertIn(token, text)

    def test_real_r4_evidence_set_has_required_fields(self):
        self.assertGreaterEqual(len(R4_CLOSURE_EVIDENCE_SET), 7)
        required = ("path/source", "identity", "timestamp/ordering_data", "hash/fingerprint", "relationship_to_B4", "terminal_status", "authority_role")
        for item in R4_CLOSURE_EVIDENCE_SET:
            for key in required:
                self.assertIn(key, item)

    def test_audit_and_review_contracts_are_closure_aware(self):
        for name in ("subtranslate-audit.md", "subtranslate-review.md"):
            text = (AGENTS / name).read_text(encoding="utf-8")
            self.assertIn("R4_CLOSURE_EVIDENCE_SET", text)
            self.assertIn("HISTORICALLY_VALID_SUPERSEDED_BY_TERMINAL_R4", text)
            self.assertIn("DIVERGENCE_BLOCK", text)
            self.assertIn("TERMINAL_CAPABILITY_CHAIN_REQUIRED=YES", text)

    def test_e1_without_later_evidence_blocks(self):
        result = _recognize_r4_closure(_historical_snapshot(), _current_snapshot(), {})
        self.assertEqual(result["status"], "DIVERGENCE_BLOCK")

    def test_raw_runtime_complete_alone_blocks(self):
        historical = _historical_snapshot()
        current = _current_snapshot()
        result = _recognize_r4_closure(historical, current, {"target_post_sha256": POST_HASH})
        self.assertEqual(result["status"], "DIVERGENCE_BLOCK")

    def test_nonterminal_capability_blocks(self):
        evidence = _valid_r4_evidence(); evidence["capability_state"] = "ARMED"
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_incomplete_journal_blocks(self):
        evidence = _valid_r4_evidence(); evidence["journal_events"] = JOURNAL[:-1]
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_target_mismatch_blocks(self):
        evidence = _valid_r4_evidence(); evidence["target_post_sha256"] = PRE_HASH
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_unknown_probe_blocks(self):
        evidence = _valid_r4_evidence(); evidence["probe_unknowns"] = ["UNKNOWN"]
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_complete_valid_r4_supersedes_e1_operationally(self):
        result = _recognize_r4_closure(_historical_snapshot(), _current_snapshot(), _valid_r4_evidence())
        self.assertEqual(result["status"], "HISTORICALLY_VALID_SUPERSEDED_BY_TERMINAL_R4")
        self.assertEqual(result["RECOVERY_B4_ROUTE"], "CLOSED_TERMINAL")

    def test_old_and_new_hashes_are_expected_under_valid_r4(self):
        result = _recognize_r4_closure(_historical_snapshot(), _current_snapshot(), _valid_r4_evidence())
        self.assertEqual(result["expected_transition"], "EXPECTED_RECOVERY_RESULT")

    def test_probe_exit_two_to_zero_is_expected_transition(self):
        evidence = _valid_r4_evidence()
        evidence["historical_probe_exit_code"] = 2
        evidence["historical_probe_blockers"] = ["RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE"]
        result = _recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)
        self.assertEqual(result["expected_probe_transition"], "EXPECTED_POST_RECOVERY_TRANSITION")

    def test_wrong_family_blocks(self):
        evidence = _valid_r4_evidence(); evidence["family_id"] = "WRONG_FAMILY"
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_wrong_episode_blocks(self):
        evidence = _valid_r4_evidence(); evidence["episode_id"] = 80
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_wrong_operation_blocks(self):
        evidence = _valid_r4_evidence(); evidence["operation_id"] = "WRONG_OPERATION"
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_closure_before_historical_snapshot_blocks(self):
        evidence = _valid_r4_evidence(); evidence["ordering"]["causal_after_historical_snapshot"] = False
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_multiple_claims_or_applies_block(self):
        for field in ("claim_count", "apply_count"):
            evidence = _valid_r4_evidence(); evidence[field] = 2
            with self.subTest(field=field):
                self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_auto_rearm_blocks(self):
        evidence = _valid_r4_evidence(); evidence["rearm"] = True
        self.assertEqual(_recognize_r4_closure(_historical_snapshot(), _current_snapshot(), evidence)["status"], "DIVERGENCE_BLOCK")

    def test_valid_r4_never_routes_to_b4_reexecution(self):
        current = _current_snapshot()
        route = _route_after_closure(current, _recognize_r4_closure(_historical_snapshot(), current, _valid_r4_evidence()))
        self.assertEqual(route["RECOVERY_B4_ROUTE"], "CLOSED_TERMINAL")
        self.assertFalse(route["B4_reexecution_requested"])
        self.assertFalse(route["B4_reprepare_requested"])
        self.assertFalse(route["B4_rearm_requested"])

    def test_valid_r4_never_requests_retroactive_ratification(self):
        current = _current_snapshot()
        route = _route_after_closure(current, _recognize_r4_closure(_historical_snapshot(), current, _valid_r4_evidence()))
        self.assertFalse(route["retroactive_ratification_requested"])

    def test_valid_r4_routes_only_current_human_gate(self):
        current = _current_snapshot()
        current["canonical"]["next_action"] = "HUMAN_DECISION_REQUIRED_FOR_NEXT_PHASE"
        evidence = _valid_r4_evidence(); evidence["canonical_post_action"] = current["canonical"]["next_action"]
        result = _recognize_r4_closure(_historical_snapshot(), current, evidence)
        route = _route_after_closure(current, result)
        self.assertEqual(route["route"], "CURRENT_NEXT_ACTION")
        self.assertEqual(route["next_action"], "HUMAN_DECISION_REQUIRED_FOR_NEXT_PHASE")
        self.assertFalse(route["B4_reprepare_requested"])

    def test_valid_r4_with_permissible_action_routes_forward(self):
        current = _current_snapshot(); current["canonical"]["next_action"] = "SAFE_READ_ONLY_NEXT_STEP"
        evidence = _valid_r4_evidence(); evidence["canonical_post_action"] = current["canonical"]["next_action"]
        result = _recognize_r4_closure(_historical_snapshot(), current, evidence)
        self.assertEqual(_route_after_closure(current, result)["next_action"], "SAFE_READ_ONLY_NEXT_STEP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
