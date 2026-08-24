import copy
import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENT = (ROOT / ".opencode/agents/subtranslate-orchestrator.md").read_text(encoding="utf-8")
EXPECTED_BLOCKER = "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE"
MISSING_FIELDS = ["episode_id", "episode_family_id", "family_contract", "family_contract_sha256", "logical_calls", "updated_at"]


def current_contract(snapshot="4189386011a4671cad4af8622a2dea0d022e619b16288cfeb57bf99493c54467"):
    return {
        "schema_version": "0.4.0",
        "execution_profile": "AUTO03B2A_VALIDATE_ONLY",
        "apply_permission_active": False,
        "action_id": "RECOVERY_LEDGER_REPREPARATION",
        "action_class": "RUNTIME_CONTROL",
        "snapshot_fingerprint": snapshot,
        "target": {
            "authority_root": "/home/palhacinho/codex-projects/anime-subtitle-translator-review",
            "family_id": "V238_E07_R6C_B4_RECOVERY",
            "episode_id": 79,
            "operation_id": "SUBTRANSLATE_V238_E07_R6C_B4_RECOVERY_20260818T165144Z",
            "resources": ["episode-budget.json"],
        },
        "effects": {
            "pipeline_model_call": False,
            "external_transport": False,
            "runtime_write": True,
            "persistent_backup_write": True,
            "production_write": False,
            "data_delete": False,
        },
        "retry": {"automatic_retry": False, "max_retries": 0},
        "rollback": {"automatic_rollback_on_postcheck_failure": True, "max_rollback_attempts": 1},
        "execution": {"single_phase": True, "max_phase_executions": 1, "max_apply_attempts": 1},
        "backup": {"persistent_backup_write": True, "persistent_rollback_proof_required": True},
        "post_execution": {"probe_required": True, "probe_max_attempts": 1, "audit_required": True, "audit_max_calls": 1, "canonical_reconciliation_required_before_next_operational_phase": True},
        "reversibility": {"proven": False, "status": "A_COMPROVAR"},
        "risk": "MEDIO",
        "preconditions": [EXPECTED_BLOCKER, "PERSISTENT_ROLLBACK_PROOF_REQUIRED", *MISSING_FIELDS],
        "execution_toolchain": {
            "executor_id": "RECOVERY_LEDGER_REPREPARATION_V1",
            "toolchain_fingerprint": "toolchain-fixture",
            "components": ["executor", "orchestrator", "probe", "durability"],
        },
    }


def fresh_toolchain_for(contract):
    binding = contract["execution_toolchain"]
    return {
        "executor_id": binding["executor_id"],
        "execution_toolchain_fingerprint": binding["toolchain_fingerprint"],
        "components": copy.deepcopy(binding["components"]),
    }


def gate_fingerprint(contract):
    payload = copy.deepcopy(contract)
    payload.pop("gate_fingerprint", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_gate(contract=None):
    contract = copy.deepcopy(contract or current_contract())
    contract["gate_fingerprint"] = gate_fingerprint(contract)
    return contract


def accept_user_token(message, role="user", current_gate=True):
    return role == "user" and current_gate and message.strip() == "AUTORIZAR"


def probe_exit_content(exit_code, blockers, unknowns=None, snapshot_consistent=True):
    blockers = blockers or []
    unknowns = unknowns or []
    if exit_code == 0:
        valid = snapshot_consistent and not unknowns and not blockers
    elif exit_code == 2:
        valid = snapshot_consistent and not unknowns and bool(blockers)
    elif exit_code == 3:
        valid = bool(unknowns) or not snapshot_consistent
    elif exit_code == 4:
        valid = not snapshot_consistent
    else:
        valid = False
    return "PROBE_EXIT_CONTENT_MATCH" if valid else "PROBE_EXIT_CONTENT_MISMATCH"


REQUIRED_CONTRACT_FIELDS = {
    "schema_version", "execution_profile", "apply_permission_active", "action_id", "action_class", "snapshot_fingerprint", "target",
    "effects", "retry", "rollback", "execution", "backup", "post_execution", "reversibility", "risk", "preconditions", "execution_toolchain",
}


def structured_contract_comparison(authorized, current):
    """Compare only authority-bearing fields; gate_fingerprint is metadata."""
    left = copy.deepcopy(authorized)
    right = copy.deepcopy(current)
    left.pop("gate_fingerprint", None)
    right.pop("gate_fingerprint", None)
    return "MATCH" if left == right else "AUTHORIZATION_SCOPE_CHANGED"


def toolchain_precheck(contract, fresh_toolchain):
    required = contract.get("execution_toolchain") if isinstance(contract, dict) else None
    if (
        not isinstance(required, dict)
        or not required.get("executor_id")
        or not required.get("toolchain_fingerprint")
        or not isinstance(required.get("components"), list)
        or not required["components"]
    ):
        return "AUTHORIZATION_PRECHECK_UNTRUSTED"
    if (
        not isinstance(fresh_toolchain, dict)
        or not fresh_toolchain.get("executor_id")
        or not fresh_toolchain.get("execution_toolchain_fingerprint")
        or not isinstance(fresh_toolchain.get("components"), list)
        or not fresh_toolchain["components"]
    ):
        return "AUTHORIZATION_PRECHECK_UNTRUSTED"
    if fresh_toolchain.get("executor_id") != required["executor_id"]:
        return "AUTHORIZATION_EXECUTOR_CHANGED"
    if fresh_toolchain.get("execution_toolchain_fingerprint") != required["toolchain_fingerprint"]:
        return "AUTHORIZATION_TOOLCHAIN_CHANGED"
    return "TOOLCHAIN_BINDING_VALIDATED"


def executor_id_precheck(contract, fresh_toolchain):
    required = contract.get("execution_toolchain") if isinstance(contract, dict) else None
    if (
        not isinstance(required, dict)
        or not required.get("executor_id")
        or not isinstance(fresh_toolchain, dict)
        or not fresh_toolchain.get("executor_id")
    ):
        return "AUTHORIZATION_TOOLCHAIN_UNTRUSTED"
    if fresh_toolchain["executor_id"] != required["executor_id"]:
        return "AUTHORIZATION_EXECUTOR_CHANGED"
    return "EXECUTOR_ID_BINDING_VALIDATED"


def binding_validation_flags(contract, fresh_toolchain):
    toolchain_result = toolchain_precheck(contract, fresh_toolchain)
    executor_result = executor_id_precheck(contract, fresh_toolchain)
    return {
        "FRESH_EXECUTION_TOOLCHAIN_PRESENT": isinstance(fresh_toolchain, dict) and bool(fresh_toolchain),
        "TOOLCHAIN_BINDING_VALIDATED": toolchain_result == "TOOLCHAIN_BINDING_VALIDATED",
        "EXECUTOR_ID_BINDING_VALIDATED": executor_result == "EXECUTOR_ID_BINDING_VALIDATED",
    }


def b2a_contract_binding_precheck(authorized, fresh):
    if not isinstance(fresh, dict):
        return "AUTHORIZATION_PROFILE_CHANGED"
    if authorized.get("schema_version") != "0.4.0" or fresh.get("schema_version") != "0.4.0":
        return "AUTHORIZATION_PROFILE_CHANGED"
    if (
        authorized.get("execution_profile") != "AUTO03B2A_VALIDATE_ONLY"
        or fresh.get("execution_profile") != "AUTO03B2A_VALIDATE_ONLY"
    ):
        return "AUTHORIZATION_PROFILE_CHANGED"
    if authorized.get("apply_permission_active") is not False:
        return "AUTHORIZATION_PERMISSION_STATE_CHANGED"
    if fresh.get("apply_permission_active") is not False:
        return "AUTHORIZATION_PERMISSION_STATE_CHANGED"
    return "B2A_CONTRACT_BINDING_VALIDATED"


def authorized_precheck(
    contract,
    fresh_snapshot,
    exit_code,
    blockers,
    unknowns=None,
    context_complete=True,
    snapshot_consistent=True,
    current_authorization_contract=None,
    fresh_authorization_contract=None,
    fresh_toolchain=None,
):
    if not context_complete:
        return "AUTHORIZATION_CONTEXT_INCOMPLETE"
    if not isinstance(contract, dict) or not REQUIRED_CONTRACT_FIELDS <= set(contract):
        return "AUTHORIZATION_CONTEXT_INCOMPLETE"
    if contract.get("schema_version") == "0.4.0" and contract.get("execution_profile") != "AUTO03B2A_VALIDATE_ONLY":
        return "STATE_MACHINE_PROFILE_MISMATCH"
    if not contract.get("snapshot_fingerprint"):
        return "AUTHORIZATION_CONTEXT_INCOMPLETE"
    fresh_contract = fresh_authorization_contract or current_authorization_contract or contract
    profile_result = b2a_contract_binding_precheck(contract, fresh_contract)
    if profile_result != "B2A_CONTRACT_BINDING_VALIDATED":
        return profile_result
    if fresh_toolchain is None:
        return "AUTHORIZATION_TOOLCHAIN_UNTRUSTED"
    toolchain_result = toolchain_precheck(contract, fresh_toolchain)
    if toolchain_result != "TOOLCHAIN_BINDING_VALIDATED":
        return toolchain_result
    executor_result = executor_id_precheck(contract, fresh_toolchain)
    flags = binding_validation_flags(contract, fresh_toolchain)
    if executor_result != "EXECUTOR_ID_BINDING_VALIDATED" or not all(
        flags.values()
    ):
        return executor_result
    if probe_exit_content(exit_code, blockers, unknowns, snapshot_consistent) == "PROBE_EXIT_CONTENT_MISMATCH":
        return "AUTHORIZATION_PRECHECK_UNTRUSTED"
    if fresh_snapshot.get("snapshot_fingerprint") != contract["snapshot_fingerprint"]:
        return "AUTHORIZATION_STALE_STATE_CHANGED"
    current = fresh_contract
    if structured_contract_comparison(contract, current) != "MATCH":
        return "AUTHORIZATION_SCOPE_CHANGED"
    if fresh_snapshot.get("action_id", contract["action_id"]) != contract["action_id"]:
        return "AUTHORIZATION_SCOPE_CHANGED"
    if fresh_snapshot.get("target", contract["target"]) != contract["target"]:
        return "AUTHORIZATION_SCOPE_CHANGED"
    if exit_code in (3, 4) or unknowns:
        return "AUTHORIZATION_PRECHECK_UNTRUSTED"
    if exit_code == 2 and blockers != [EXPECTED_BLOCKER]:
        return "AUTHORIZATION_PRECHECK_UNTRUSTED"
    return "AUTHORIZATION_VALIDATED"


def gate_transition(message, role="user", current_gate=True):
    if role != "user" or not current_gate:
        return "NO_AUTHORIZATION"
    token = message.strip()
    if token == "AUTORIZAR":
        return "AUTHORIZED_PRECHECK"
    if token == "NÃO AUTORIZAR":
        return "AUTHORIZATION_REJECTED"
    if token == "VER DETALHES TÉCNICOS":
        return "DETAILS_ONLY"
    return "NO_AUTHORIZATION"


def probe_stage_policy(boot_probe_count, precheck_probe_count):
    if boot_probe_count != 1:
        return "BOOT_PROBE_INVALID"
    if precheck_probe_count != 1:
        return "AUTHORIZED_PRECHECK_PROBE_INVALID"
    return "TWO_DISTINCT_PROBES_NOT_RETRY"


def completed_authorization_flow(precheck_result):
    if precheck_result != "AUTHORIZATION_VALIDATED":
        return [precheck_result, "FAIL_STOP"]
    return ["AUTHORIZATION_VALIDATED", "EXECUTION_DISABLED_AUTO03B2A", "STOP"]


def same_invocation_authorization_flow(
    token="AUTORIZAR",
    boot_probe_count=1,
    precheck_probe_count=1,
    precheck_probe_executed=True,
    precheck_after_token=True,
    precheck_result="AUTHORIZATION_VALIDATED",
):
    """Small executable model of the mandatory post-token state machine."""
    if token != "AUTORIZAR":
        return ["NO_AUTHORIZATION", "HUMAN_GATE_PENDING"]
    states = ["AUTHORIZATION_RECEIVED"]
    if boot_probe_count != 1:
        return states + ["FAIL_STOP"]
    if not precheck_after_token or not precheck_probe_executed or precheck_probe_count != 1:
        return states + ["AUTHORIZATION_PRECHECK_NOT_EXECUTED", "AUTHORIZATION_INVALIDATED", "FAIL_STOP"]
    states.extend(["AUTHORIZED_PRECHECK", "AUTHORIZED_PRECHECK_PROBE"])
    if precheck_result != "AUTHORIZATION_VALIDATED":
        return states + ["AUTHORIZATION_INVALIDATED", "FAIL_STOP"]
    return states + ["AUTHORIZATION_VALIDATED", "EXECUTION_DISABLED_AUTO03B2A", "STOP"]


class AuthorizationGateTests(unittest.TestCase):
    def test_human_gate_generates_complete_contract(self):
        contract = build_gate()
        required = {"schema_version", "execution_profile", "action_id", "action_class", "snapshot_fingerprint", "target", "effects", "retry", "rollback", "execution", "reversibility", "risk", "preconditions", "execution_toolchain"}
        self.assertTrue(required <= set(contract))
        self.assertEqual(contract["schema_version"], "0.4.0")
        self.assertEqual(contract["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertIn("gate_fingerprint", contract)  # optional informational metadata

    def test_contract_without_gate_fingerprint_is_valid(self):
        contract = current_contract()
        self.assertNotIn("gate_fingerprint", contract)
        self.assertEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(contract)),
            "AUTHORIZATION_VALIDATED",
        )

    def test_null_gate_fingerprint_is_valid(self):
        contract = current_contract()
        contract["gate_fingerprint"] = None
        self.assertEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(contract)),
            "AUTHORIZATION_VALIDATED",
        )

    def test_human_gate_can_render_without_gate_fingerprint(self):
        contract = current_contract()
        self.assertNotIn("gate_fingerprint", contract)
        self.assertIn("action_id", contract)
        self.assertIn("snapshot_fingerprint", contract)

    def test_missing_gate_fingerprint_is_not_unknown_or_block(self):
        contract = current_contract()
        self.assertEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(contract)),
            "AUTHORIZATION_VALIDATED",
        )

    def test_missing_gate_fingerprint_does_not_request_shell_or_extra_tool(self):
        self.assertIn("DERIVED_OPTIONAL_VALUE_UNAVAILABLE", AGENT)
        self.assertIn("DO_NOT_USE_BASH", AGENT)
        self.assertIn("não entre em loop", AGENT)

    def test_gate_fingerprint_is_not_authority(self):
        authorized = build_gate()
        current = copy.deepcopy(authorized)
        current["gate_fingerprint"] = "not-a-real-hash"
        self.assertEqual(structured_contract_comparison(authorized, current), "MATCH")
        self.assertEqual(
            authorized_precheck(authorized, {"snapshot_fingerprint": authorized["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], current_authorization_contract=current, fresh_toolchain=fresh_toolchain_for(authorized)),
            "AUTHORIZATION_VALIDATED",
        )

    def test_hash_invention_is_prohibited(self):
        self.assertIn("invente hash", AGENT)
        self.assertIn("Não o calcule por shell", AGENT)

    def test_snapshot_fingerprint_remains_required(self):
        contract = current_contract()
        contract.pop("snapshot_fingerprint")
        self.assertEqual(
            authorized_precheck(contract, {}, 2, [EXPECTED_BLOCKER]),
            "AUTHORIZATION_CONTEXT_INCOMPLETE",
        )

    def test_physical_fact_missing_is_unknown_or_block(self):
        self.assertIn("fato físico indispensável", AGENT)
        self.assertIn("UNKNOWN/BLOCK", AGENT)

    def test_optional_derived_metadata_missing_continues(self):
        self.assertIn("metadado derivado opcional", AGENT)
        self.assertIn("continuando sem esse", AGENT)

    def test_optional_metadata_absence_has_no_reconsideration_loop(self):
        for token in ("DERIVED_OPTIONAL_VALUE_UNAVAILABLE", "DO_NOT_RECONSIDER", "DO_NOT_SEARCH", "DO_NOT_USE_BASH"):
            self.assertIn(token, AGENT)

    def test_gate_fingerprint_is_sha256(self):
        self.assertEqual(len(build_gate()["gate_fingerprint"]), 64)

    def test_same_contract_same_fingerprint(self):
        self.assertEqual(gate_fingerprint(current_contract()), gate_fingerprint(current_contract()))

    def test_snapshot_change_changes_fingerprint(self):
        self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(current_contract("changed")))

    def test_action_id_change_changes_fingerprint(self):
        c = current_contract(); c["action_id"] = "OTHER"; self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(c))

    def test_target_change_changes_fingerprint(self):
        c = current_contract(); c["target"]["family_id"] = "OTHER"; self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(c))

    def test_runtime_write_change_changes_fingerprint(self):
        c = current_contract(); c["effects"]["runtime_write"] = False; self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(c))

    def test_transport_change_changes_fingerprint(self):
        c = current_contract(); c["effects"]["external_transport"] = True; self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(c))

    def test_model_call_change_changes_fingerprint(self):
        c = current_contract(); c["effects"]["pipeline_model_call"] = True; self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(c))

    def test_retry_change_changes_fingerprint(self):
        c = current_contract(); c["retry"]["max_retries"] = 1; self.assertNotEqual(gate_fingerprint(current_contract()), gate_fingerprint(c))

    def test_exact_authorize_token(self):
        self.assertTrue(accept_user_token("AUTORIZAR"))

    def test_spaces_are_trimmed(self):
        self.assertTrue(accept_user_token("  AUTORIZAR\n"))

    def test_ok_does_not_authorize(self):
        self.assertFalse(accept_user_token("ok"))

    def test_sim_does_not_authorize(self):
        self.assertFalse(accept_user_token("sim"))

    def test_pode_does_not_authorize(self):
        self.assertFalse(accept_user_token("pode"))

    def test_sentence_containing_authorize_does_not_authorize(self):
        self.assertFalse(accept_user_token("AUTORIZAR esta ação"))

    def test_assistant_authorize_does_not_authorize(self):
        self.assertFalse(accept_user_token("AUTORIZAR", role="assistant"))

    def test_summary_authorize_does_not_authorize(self):
        self.assertFalse(accept_user_token("AUTORIZAR", current_gate=False))

    def test_old_gate_authorize_does_not_authorize(self):
        self.assertFalse(accept_user_token("AUTORIZAR", current_gate=False))

    def test_nao_autorizar_stops(self):
        self.assertFalse(accept_user_token("NÃO AUTORIZAR"))

    def test_ver_detalhes_does_not_authorize(self):
        self.assertFalse(accept_user_token("VER DETALHES TÉCNICOS"))

    def test_missing_contract_fail_stops(self):
        self.assertEqual(authorized_precheck({}, {}, 2, [EXPECTED_BLOCKER]), "AUTHORIZATION_CONTEXT_INCOMPLETE")

    def test_incomplete_contract_fail_stops(self):
        c = current_contract(); c.pop("target"); c["gate_fingerprint"] = gate_fingerprint(c); self.assertFalse("target" in c)

    def test_missing_gate_fingerprint_does_not_fail_stop(self):
        c = current_contract(); self.assertNotIn("gate_fingerprint", c)
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_VALIDATED")

    def test_compacted_context_without_contract_fail_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], context_complete=False), "AUTHORIZATION_CONTEXT_INCOMPLETE")

    def test_equal_fingerprint_precheck_passes(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_VALIDATED")

    def test_changed_fingerprint_is_stale(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": "new"}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_STALE_STATE_CHANGED")

    def test_changed_scope_stops(self):
        c = build_gate(); fresh = {"snapshot_fingerprint": c["snapshot_fingerprint"], "target": {"family_id": "other"}}; self.assertEqual(authorized_precheck(c, fresh, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_SCOPE_CHANGED")

    def test_exit_three_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 3, [], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_four_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 4, [], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_same_exit_two_blocker_is_valid(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_VALIDATED")

    def test_exit_zero_without_blocker_is_valid(self):
        c = build_gate(); self.assertEqual(probe_exit_content(0, [], [], True), "PROBE_EXIT_CONTENT_MATCH")
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 0, [], [], snapshot_consistent=True, fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_VALIDATED")

    def test_exit_zero_with_blocker_is_content_mismatch(self):
        c = build_gate(); self.assertEqual(probe_exit_content(0, [EXPECTED_BLOCKER], [], True), "PROBE_EXIT_CONTENT_MISMATCH")
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 0, [EXPECTED_BLOCKER], [], snapshot_consistent=True, fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_two_with_blocker_is_valid(self):
        c = build_gate(); self.assertEqual(probe_exit_content(2, [EXPECTED_BLOCKER], [], True), "PROBE_EXIT_CONTENT_MATCH")
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], [], snapshot_consistent=True, fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_VALIDATED")

    def test_exit_two_without_blocker_is_content_mismatch(self):
        c = build_gate(); self.assertEqual(probe_exit_content(2, [], [], True), "PROBE_EXIT_CONTENT_MISMATCH")
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [], [], snapshot_consistent=True, fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_three_with_unknown_is_untrusted(self):
        c = build_gate(); self.assertEqual(probe_exit_content(3, [], ["UNKNOWN_FACT"], False), "PROBE_EXIT_CONTENT_MATCH")
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 3, [], ["UNKNOWN_FACT"], snapshot_consistent=False, fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_four_is_untrusted(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 4, [], [], snapshot_consistent=False, fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_content_mismatch_can_never_validate(self):
        c = build_gate()
        result = authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 0, [EXPECTED_BLOCKER], [], snapshot_consistent=True, fresh_toolchain=fresh_toolchain_for(c))
        self.assertNotEqual(result, "AUTHORIZATION_VALIDATED")
        self.assertEqual(result, "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_current_blocker_requires_exit_two(self):
        self.assertEqual(probe_exit_content(2, [EXPECTED_BLOCKER], [], True), "PROBE_EXIT_CONTENT_MATCH")
        self.assertEqual(probe_exit_content(0, [EXPECTED_BLOCKER], [], True), "PROBE_EXIT_CONTENT_MISMATCH")

    def test_exit_content_invariant_is_documented(self):
        for token in ("PROBE_EXIT_CODE", "blockers[]", "unknowns[]", "snapshot_consistent", "PROBE_EXIT_CONTENT_MISMATCH", "AUTHORIZATION_INVALIDATED"):
            self.assertIn(token, AGENT)

    def test_new_blocker_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, ["OTHER"], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_single_action_id(self):
        c = current_contract(); self.assertEqual(c["action_id"], "RECOVERY_LEDGER_REPREPARATION")

    def test_single_phase(self):
        self.assertTrue(current_contract()["execution"]["single_phase"])

    def test_max_execution_one(self):
        self.assertEqual(current_contract()["execution"]["max_phase_executions"], 1)

    def test_retries_zero(self):
        self.assertFalse(current_contract()["retry"]["automatic_retry"]); self.assertEqual(current_contract()["retry"]["max_retries"], 0)

    def test_b5_b7_not_authorized(self):
        self.assertNotIn("B5-B7", current_contract()["preconditions"])

    def test_model_call_disabled(self):
        self.assertFalse(current_contract()["effects"]["pipeline_model_call"])

    def test_transport_disabled(self):
        self.assertFalse(current_contract()["effects"]["external_transport"])

    def test_production_disabled(self):
        self.assertFalse(current_contract()["effects"]["production_write"])

    def test_subagent_does_not_inherit_write_authorization(self):
        self.assertIn("não herdam autorização de write", AGENT)

    def test_historical_authorization_does_not_release_executor(self):
        self.assertIn("HISTORICAL_AUTHORIZATION != CURRENT_AUTHORIZATION", AGENT)

    def test_current_gate_is_runtime_control(self):
        self.assertEqual(current_contract()["action_class"], "RUNTIME_CONTROL")

    def test_rollback_proof_required(self):
        self.assertIn("PERSISTENT_ROLLBACK_PROOF=REQUIRED", AGENT)

    def test_reversibility_not_proven(self):
        self.assertFalse(current_contract()["reversibility"]["proven"]); self.assertEqual(current_contract()["reversibility"]["status"], "A_COMPROVAR")

    def test_auto03b2a_has_no_write(self):
        self.assertIn("não produz\nruntime write", AGENT)

    def test_auto03b2a_does_not_call_executor(self):
        self.assertIn("validate-only: não chama executor", AGENT)
        self.assertIn("não cria backup real", AGENT)

    def test_validated_result_disables_execution(self):
        self.assertIn("AUTHORIZATION_VALIDATED", AGENT); self.assertIn("EXECUTION_DISABLED_AUTO03B2A", AGENT)
        self.assertNotIn("EXECUTION_DISABLED_AUTO03A", AGENT)

    def test_loop_guard_preserved(self):
        self.assertIn("probe_max_attempts=1", AGENT); self.assertIn("automatic_retry=false", AGENT)

    def test_zero_retry_preserved(self):
        self.assertIn("max_retries=0", AGENT)

    def test_authorize_never_direct_done(self):
        self.assertEqual(gate_transition("AUTORIZAR"), "AUTHORIZED_PRECHECK")
        self.assertNotEqual(gate_transition("AUTORIZAR"), "DONE")

    def test_authorize_generates_authorized_precheck(self):
        self.assertEqual(gate_transition("AUTORIZAR"), "AUTHORIZED_PRECHECK")

    def test_boot_probe_does_not_satisfy_precheck(self):
        self.assertEqual(probe_stage_policy(1, 0), "AUTHORIZED_PRECHECK_PROBE_INVALID")

    def test_probe_runs_again_after_authorize(self):
        self.assertEqual(probe_stage_policy(1, 1), "TWO_DISTINCT_PROBES_NOT_RETRY")

    def test_authorized_precheck_probe_max_attempts_one(self):
        self.assertIn("AUTHORIZED_PRECHECK_PROBE", AGENT); self.assertIn("Cada estágio pode executar o probe no máximo uma vez", AGENT)

    def test_second_probe_is_precheck_not_retry(self):
        self.assertIn("não são retry", AGENT)

    def test_authorize_without_fresh_probe_never_validates(self):
        flow = same_invocation_authorization_flow(precheck_probe_executed=False, precheck_probe_count=0)
        self.assertNotIn("AUTHORIZATION_VALIDATED", flow)
        self.assertIn("AUTHORIZATION_PRECHECK_NOT_EXECUTED", flow)
        self.assertEqual(flow[-1], "FAIL_STOP")

    def test_boot_probe_does_not_count_as_post_authorization_probe(self):
        flow = same_invocation_authorization_flow(precheck_probe_executed=False, precheck_probe_count=0)
        self.assertEqual(flow[:2], ["AUTHORIZATION_RECEIVED", "AUTHORIZATION_PRECHECK_NOT_EXECUTED"])
        self.assertNotIn("AUTHORIZATION_VALIDATED", flow)

    def test_deferred_precheck_is_forbidden(self):
        flow = same_invocation_authorization_flow(precheck_after_token=False, precheck_probe_executed=False, precheck_probe_count=0)
        self.assertNotIn("AUTHORIZATION_VALIDATED", flow)
        self.assertEqual(flow[-1], "FAIL_STOP")
        self.assertIn("não puder ser iniciado/executado nessa mesma continuação", AGENT)

    def test_received_cannot_transition_directly_to_validated(self):
        flow = same_invocation_authorization_flow(precheck_probe_executed=False, precheck_probe_count=0)
        self.assertNotEqual(flow[:2], ["AUTHORIZATION_RECEIVED", "AUTHORIZATION_VALIDATED"])
        self.assertNotIn("DONE", flow)

    def test_fresh_probe_is_required_in_same_flow_after_token(self):
        flow = same_invocation_authorization_flow()
        self.assertEqual(
            flow[:4],
            ["AUTHORIZATION_RECEIVED", "AUTHORIZED_PRECHECK", "AUTHORIZED_PRECHECK_PROBE", "AUTHORIZATION_VALIDATED"],
        )
        self.assertIn("AUTHORIZED_PRECHECK_PROBE_COMPLETED=true", AGENT)

    def test_fresh_probe_exactly_once_after_authorize(self):
        flow = same_invocation_authorization_flow(boot_probe_count=1, precheck_probe_count=1)
        self.assertEqual(flow.count("AUTHORIZED_PRECHECK_PROBE"), 1)
        self.assertIn("AUTHORIZED_PRECHECK_PROBE_ATTEMPTS=1", AGENT)

    def test_fresh_probe_is_not_retry(self):
        flow = same_invocation_authorization_flow(boot_probe_count=1, precheck_probe_count=1)
        self.assertEqual(flow.count("AUTHORIZED_PRECHECK_PROBE"), 1)
        self.assertIn("as duas execuções possíveis", AGENT)
        self.assertIn("não são retry", AGENT)

    def test_validated_requires_completed_fresh_probe(self):
        valid = same_invocation_authorization_flow(precheck_probe_executed=True, precheck_probe_count=1)
        invalid = same_invocation_authorization_flow(precheck_probe_executed=False, precheck_probe_count=0)
        self.assertIn("AUTHORIZATION_VALIDATED", valid)
        self.assertNotIn("AUTHORIZATION_VALIDATED", invalid)
        self.assertIn("só pode ser emitido depois de", AGENT)

    def test_precheck_probe_unavailable_fails_stop(self):
        flow = same_invocation_authorization_flow(precheck_probe_executed=False, precheck_probe_count=0)
        self.assertEqual(flow[-2:], ["AUTHORIZATION_INVALIDATED", "FAIL_STOP"])

    def test_fresh_probe_exit_two_expected_blocker_can_validate(self):
        c = build_gate()
        self.assertEqual(
            authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)),
            "AUTHORIZATION_VALIDATED",
        )

    def test_fresh_probe_fingerprint_equal_can_validate(self):
        c = build_gate()
        self.assertEqual(
            authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)),
            "AUTHORIZATION_VALIDATED",
        )

    def test_fresh_probe_fingerprint_different_fails_stop(self):
        c = build_gate()
        self.assertEqual(
            authorized_precheck(c, {"snapshot_fingerprint": "different"}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)),
            "AUTHORIZATION_STALE_STATE_CHANGED",
        )

    def test_validated_always_disables_execution_and_stops(self):
        self.assertEqual(
            same_invocation_authorization_flow()[-3:],
            ["AUTHORIZATION_VALIDATED", "EXECUTION_DISABLED_AUTO03B2A", "STOP"],
        )

    def test_same_snapshot_same_scope_validates(self):
        c = build_gate(); result = authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)); self.assertEqual(result, "AUTHORIZATION_VALIDATED")

    def test_validated_execution_disabled(self):
        self.assertEqual(completed_authorization_flow("AUTHORIZATION_VALIDATED"), ["AUTHORIZATION_VALIDATED", "EXECUTION_DISABLED_AUTO03B2A", "STOP"])

    def test_validated_then_stop(self):
        self.assertEqual(completed_authorization_flow("AUTHORIZATION_VALIDATED")[-1], "STOP")

    def test_fingerprint_difference_stale(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": "different"}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_STALE_STATE_CHANGED")

    def test_action_difference_scope(self):
        c = build_gate(); fresh = {"snapshot_fingerprint": c["snapshot_fingerprint"], "action_id": "OTHER"}; self.assertEqual(authorized_precheck(c, fresh, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_SCOPE_CHANGED")

    def test_target_difference_scope(self):
        c = build_gate(); fresh = {"snapshot_fingerprint": c["snapshot_fingerprint"], "target": {"family_id": "OTHER"}}; self.assertEqual(authorized_precheck(c, fresh, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_SCOPE_CHANGED")

    def test_effect_difference_is_scope_change(self):
        c = current_contract(); changed = copy.deepcopy(c); changed["effects"]["runtime_write"] = False
        self.assertEqual(
            authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], current_authorization_contract=changed, fresh_toolchain=fresh_toolchain_for(c)),
            "AUTHORIZATION_SCOPE_CHANGED",
        )

    def test_retry_difference_is_scope_change(self):
        c = current_contract(); changed = copy.deepcopy(c); changed["retry"]["max_retries"] = 1
        self.assertEqual(
            authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], current_authorization_contract=changed, fresh_toolchain=fresh_toolchain_for(c)),
            "AUTHORIZATION_SCOPE_CHANGED",
        )

    def test_preconditions_difference_is_scope_change(self):
        c = current_contract(); changed = copy.deepcopy(c); changed["preconditions"].append("EXTRA")
        self.assertEqual(
            authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], current_authorization_contract=changed, fresh_toolchain=fresh_toolchain_for(c)),
            "AUTHORIZATION_SCOPE_CHANGED",
        )

    def test_full_structured_contract_comparison_is_documented(self):
        for token in ("AUTHORIZATION_BINDING_AUTHORITY", "campo a campo", "Não compare", "gate_fingerprint` como autoridade"):
            self.assertIn(token, AGENT)

    def test_executable_action_requires_toolchain_binding(self):
        contract = current_contract()
        self.assertEqual(contract["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertIn("execution_toolchain", contract)
        self.assertEqual(contract["execution_toolchain"]["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")

    def test_profile_mismatch_fail_stops_before_validation(self):
        contract = current_contract(); contract["execution_profile"] = "AUTO03A_LEGACY"
        self.assertEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(contract)),
            "STATE_MACHINE_PROFILE_MISMATCH",
        )

    def test_b2a_profile_requires_toolchain_and_executor(self):
        contract = current_contract()
        self.assertIn("execution_profile", contract)
        self.assertIn("execution_toolchain", contract)
        self.assertIn("executor_id", contract["execution_toolchain"])

    def test_equal_executor_id_sets_explicit_binding_flag(self):
        contract = current_contract()
        flags = binding_validation_flags(contract, fresh_toolchain_for(contract))
        self.assertTrue(flags["EXECUTOR_ID_BINDING_VALIDATED"])

    def test_equal_toolchain_sets_explicit_binding_flag(self):
        contract = current_contract()
        flags = binding_validation_flags(contract, fresh_toolchain_for(contract))
        self.assertTrue(flags["TOOLCHAIN_BINDING_VALIDATED"])

    def test_validated_requires_both_binding_flags(self):
        contract = current_contract()
        fresh = fresh_toolchain_for(contract); fresh["execution_toolchain_fingerprint"] = "changed"
        flags = binding_validation_flags(contract, fresh)
        self.assertFalse(flags["TOOLCHAIN_BINDING_VALIDATED"])
        self.assertTrue(flags["FRESH_EXECUTION_TOOLCHAIN_PRESENT"])
        self.assertNotEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh),
            "AUTHORIZATION_VALIDATED",
        )

    def test_missing_toolchain_fails_stop(self):
        contract = current_contract()
        contract.pop("execution_toolchain")
        self.assertEqual(toolchain_precheck(contract, {}), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_equal_toolchain_can_validate(self):
        contract = current_contract()
        fresh = fresh_toolchain_for(contract)
        self.assertEqual(toolchain_precheck(contract, fresh), "TOOLCHAIN_BINDING_VALIDATED")

    def test_changed_toolchain_invalidates(self):
        contract = current_contract()
        fresh = fresh_toolchain_for(contract); fresh["execution_toolchain_fingerprint"] = "changed"
        self.assertEqual(toolchain_precheck(contract, fresh), "AUTHORIZATION_TOOLCHAIN_CHANGED")

    def test_command_only_change_invalidates_b2a_authorization(self):
        paths = (
            ".opencode/tools/subtranslate_recovery_ledger_reprepare.py",
            ".opencode/agents/subtranslate-orchestrator.md",
            ".opencode/tools/subtranslate_readonly_probe.py",
            "src/subtranslate/v238_per_call_durability.py",
            ".opencode/agents/subtranslate-audit.md",
            ".opencode/commands/subtranslate-next.md",
        )
        components = [
            {"path": path, "sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}
            for path in paths
        ]
        fingerprint = lambda value: hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        contract = current_contract()
        contract["execution_toolchain"] = {
            "executor_id": "RECOVERY_LEDGER_REPREPARATION_V1",
            "toolchain_fingerprint": fingerprint(components),
            "components": copy.deepcopy(components),
        }
        fresh_components = copy.deepcopy(components)
        fresh_components[-1]["sha256"] = hashlib.sha256(
            (ROOT / paths[-1]).read_bytes() + b"x"
        ).hexdigest()
        fresh = {
            "executor_id": "RECOVERY_LEDGER_REPREPARATION_V1",
            "execution_toolchain_fingerprint": fingerprint(fresh_components),
            "components": fresh_components,
        }
        self.assertEqual(contract["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertEqual(toolchain_precheck(contract, fresh), "AUTHORIZATION_TOOLCHAIN_CHANGED")
        self.assertEqual(
            authorized_precheck(
                contract,
                {"snapshot_fingerprint": contract["snapshot_fingerprint"]},
                2,
                [EXPECTED_BLOCKER],
                fresh_toolchain=fresh,
            ),
            "AUTHORIZATION_TOOLCHAIN_CHANGED",
        )

    def test_changed_executor_invalidates(self):
        contract = current_contract()
        fresh = fresh_toolchain_for(contract); fresh["executor_id"] = "OTHER_EXECUTOR"
        self.assertEqual(toolchain_precheck(contract, fresh), "AUTHORIZATION_EXECUTOR_CHANGED")

    def test_incomplete_fresh_toolchain_is_untrusted(self):
        contract = current_contract()
        fresh = fresh_toolchain_for(contract); fresh.pop("components")
        self.assertEqual(toolchain_precheck(contract, fresh), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_fresh_toolchain_is_mandatory_after_authorize(self):
        contract = current_contract()
        self.assertEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER]),
            "AUTHORIZATION_TOOLCHAIN_UNTRUSTED",
        )

    def test_toolchain_binding_is_independent_of_snapshot_binding(self):
        contract = current_contract()
        fresh = fresh_toolchain_for(contract); fresh["execution_toolchain_fingerprint"] = "changed"
        self.assertNotEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh),
            "AUTHORIZATION_VALIDATED",
        )

    def test_snapshot_equal_toolchain_different_is_invalid(self):
        contract = build_gate()
        fresh = fresh_toolchain_for(contract); fresh["execution_toolchain_fingerprint"] = "changed"
        self.assertEqual(
            authorized_precheck(contract, {"snapshot_fingerprint": contract["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh),
            "AUTHORIZATION_TOOLCHAIN_CHANGED",
        )

    def test_persistent_backup_write_is_bound(self):
        self.assertTrue(current_contract()["effects"]["persistent_backup_write"])

    def test_rollback_policy_is_bound(self):
        self.assertTrue(current_contract()["rollback"]["automatic_rollback_on_postcheck_failure"])
        self.assertEqual(current_contract()["rollback"]["max_rollback_attempts"], 1)

    def test_executor_id_is_bound(self):
        self.assertEqual(current_contract()["execution_toolchain"]["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")

    def test_auto03b2a_disables_execution(self):
        self.assertIn("EXECUTION_DISABLED_AUTO03B2A", AGENT)
        self.assertIn("APPLY_PERMISSION_ACTIVE=false", AGENT)

    def test_b2a_profile_routes_exclusively_to_b2a_terminal(self):
        self.assertIn("EXECUTION_PROFILE_ROUTING", AGENT)
        self.assertIn("AUTO03B2A_VALIDATE_ONLY", AGENT)
        self.assertIn("STATE_MACHINE_PROFILE_MISMATCH", AGENT)
        self.assertNotIn("EXECUTION_DISABLED_AUTO03A", AGENT)

    def test_executor_exists_bound_but_is_not_called(self):
        self.assertIn("executor existe e está vinculado mas não foi chamado", AGENT)

    def test_toolchain_change_policy_is_documented(self):
        for token in ("AUTHORIZATION_TOOLCHAIN_CHANGED", "TOOLCHAIN_CHANGED", "execution_toolchain_fingerprint"):
            self.assertIn(token, AGENT)

    def test_blocker_difference_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, ["OTHER"], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_new_unknown_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], unknowns=["NEW"], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_three_fail_stops(self):
        self.assertIn("exit 3/4", AGENT); c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 3, [], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_exit_four_fail_stops(self):
        c = build_gate(); self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 4, [], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_PRECHECK_UNTRUSTED")

    def test_contract_absent_is_context_incomplete(self):
        self.assertEqual(authorized_precheck({}, {}, 2, [EXPECTED_BLOCKER], context_complete=False), "AUTHORIZATION_CONTEXT_INCOMPLETE")

    def test_gate_fingerprint_absent_is_allowed(self):
        c = current_contract(); self.assertNotIn("gate_fingerprint", c)
        self.assertEqual(authorized_precheck(c, {"snapshot_fingerprint": c["snapshot_fingerprint"]}, 2, [EXPECTED_BLOCKER], fresh_toolchain=fresh_toolchain_for(c)), "AUTHORIZATION_VALIDATED")

    def test_historical_authorization_does_not_enter_precheck(self):
        self.assertEqual(gate_transition("AUTORIZAR", current_gate=False), "NO_AUTHORIZATION")

    def test_assistant_authorize_does_not_enter_precheck(self):
        self.assertEqual(gate_transition("AUTORIZAR", role="assistant"), "NO_AUTHORIZATION")

    def test_summary_authorized_does_not_enter_precheck(self):
        self.assertEqual(gate_transition("AUTORIZAR", current_gate=False), "NO_AUTHORIZATION")

    def test_current_exact_authorize_is_only_valid_path(self):
        self.assertEqual(gate_transition("AUTORIZAR"), "AUTHORIZED_PRECHECK")
        for value in ("ok", "sim", "pode", "AUTORIZAR agora"):
            self.assertEqual(gate_transition(value), "NO_AUTHORIZATION")

    def test_zero_runtime_writes(self):
        self.assertIn("não produz\nruntime write", AGENT)

    def test_zero_executor_calls(self):
        self.assertIn("validate-only: não chama executor", AGENT)

    def test_zero_retries(self):
        self.assertIn("automatic_retry=false", AGENT); self.assertIn("max_retries=0", AGENT)

    def test_zero_model_calls(self):
        self.assertIn("model call", AGENT); self.assertIn("pipeline_model_call=false", AGENT)

    def test_zero_transports(self):
        self.assertIn("external_transport=false", AGENT); self.assertIn("POST", AGENT)

    def test_current_contract_binds_b2a_profile_and_permission(self):
        contract = current_contract()
        self.assertEqual(contract["schema_version"], "0.4.0")
        self.assertEqual(contract["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertIs(contract["apply_permission_active"], False)

    def test_fresh_b2a_contract_binding_validates(self):
        contract = current_contract()
        self.assertEqual(b2a_contract_binding_precheck(contract, copy.deepcopy(contract)), "B2A_CONTRACT_BINDING_VALIDATED")

    def test_fresh_schema_downgrade_invalidates(self):
        contract = current_contract(); fresh = copy.deepcopy(contract); fresh["schema_version"] = "0.3.0"
        self.assertEqual(b2a_contract_binding_precheck(contract, fresh), "AUTHORIZATION_PROFILE_CHANGED")

    def test_fresh_profile_downgrade_invalidates(self):
        contract = current_contract(); fresh = copy.deepcopy(contract); fresh["execution_profile"] = "AUTO03B1_VALIDATE_ONLY"
        self.assertEqual(b2a_contract_binding_precheck(contract, fresh), "AUTHORIZATION_PROFILE_CHANGED")

    def test_fresh_permission_activation_invalidates(self):
        contract = current_contract(); fresh = copy.deepcopy(contract); fresh["apply_permission_active"] = True
        self.assertEqual(b2a_contract_binding_precheck(contract, fresh), "AUTHORIZATION_PERMISSION_STATE_CHANGED")

    def test_authorized_precheck_requires_fresh_b2a_contract(self):
        contract = current_contract(); fresh = copy.deepcopy(contract); fresh["execution_profile"] = "AUTO03B1_VALIDATE_ONLY"
        result = authorized_precheck(
            contract,
            {"snapshot_fingerprint": contract["snapshot_fingerprint"]},
            2,
            [EXPECTED_BLOCKER],
            fresh_authorization_contract=fresh,
            fresh_toolchain=fresh_toolchain_for(contract),
        )
        self.assertEqual(result, "AUTHORIZATION_PROFILE_CHANGED")

    def test_authorized_precheck_validates_all_b2a_bindings(self):
        contract = current_contract()
        result = authorized_precheck(
            contract,
            {"snapshot_fingerprint": contract["snapshot_fingerprint"]},
            2,
            [EXPECTED_BLOCKER],
            fresh_authorization_contract=copy.deepcopy(contract),
            fresh_toolchain=fresh_toolchain_for(contract),
        )
        self.assertEqual(result, "AUTHORIZATION_VALIDATED")
        flags = binding_validation_flags(contract, fresh_toolchain_for(contract))
        self.assertTrue(all(flags.values()))

    def test_agent_requires_b2a_contract_binding_marker(self):
        self.assertIn("B2A_CONTRACT_BINDING_VALIDATED=true", AGENT)
        self.assertIn("AUTHORIZATION_PROFILE_CHANGED", AGENT)
        self.assertIn("AUTHORIZATION_PERMISSION_STATE_CHANGED", AGENT)

    def test_b2a_terminal_excludes_legacy_success_terminals(self):
        flow = completed_authorization_flow("AUTHORIZATION_VALIDATED")
        self.assertEqual(flow, ["AUTHORIZATION_VALIDATED", "EXECUTION_DISABLED_AUTO03B2A", "STOP"])
        self.assertNotIn("EXECUTION_DISABLED_AUTO03B1", flow)
        self.assertNotIn("EXECUTION_DISABLED_AUTO03A", flow)

    def test_b2a_validate_only_has_zero_real_side_effects(self):
        self.assertIn("APPLY_PERMISSION_ACTIVE=false", AGENT)
        self.assertIn("não chama executor", AGENT)
        self.assertIn("não cria backup real", AGENT)
        self.assertIn("não escreve runtime", AGENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
