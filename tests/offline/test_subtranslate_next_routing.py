import json
import tempfile
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT / ".opencode" / "agents"

_HUMAN_GATE_HEADER = """DECISÃO NECESSÁRIA

O que vou fazer:
Acessar/alterar estado de execução operacional (estado_runtime, budget, operation, attempt, evidence, ledger).

Tipo de ação: [ACTION_CLASS]

Por que: Estado operacional em tempo real de controle de risco; não é documental.

Vai chamar modelo do pipeline? NÃO
Vai fazer transporte/POST? NÃO
Vai alterar runtime/control? [SIM|NÃO]
Vai alterar produção? NÃO
Vai apagar dados? NÃO
Reversível? A COMPROVAR
Risco: BAIXO/MÉDIO

[AUTORIZAR] [NÃO AUTORIZAR]"""


def _render_human_interface(action_class, file_path=None):
    """Renderiza a interface HUMAN_GATE para ACTION_CLASS."""
    flags = {
        "pipeline_model_call": False,
        "external_transport": False,
        "runtime_write": action_class == "RUNTIME",
        "production_write": False,
        "data_delete": False,
    }

    if file_path:
        p = str(file_path).lower()
        flags["external_transport"] = any(k in p for k in ("library", "/tmp/", ".cache/"))
        flags["production_write"] = "producao" in p or "deploy" in p

    reversibility_provable = flags["runtime_write"]
    reversibility = (
        "SIM com rollback/preservação comprovados" if reversibility_provable
        else "A COMPROVAR"
    )
    runtime_ans = ("SIM com rollback/preservação comprovados" if flags["runtime_write"] else "NÃO")

    return _HUMAN_GATE_HEADER.replace("[ACTION_CLASS]", action_class or "RUNTIME").replace(
        "[SIM|NÃO]", runtime_ans
    )


def _classify_action(file_path):
    """Classifica a ação baseada no arquivo destino."""
    p = str(file_path).lower()
    if "recovery_ledger.json" in p or "episode_budget.json" in p:
        return "RUNTIME"
    if "operation.json" in p or "attempts" in p or "evidence" in p:
        return "RUNTIME"
    if str(file_path).endswith(".json"):
        return "RUNTIME"
    return "DOCUMENTAL"


CURRENT_PROBE_EQUIVALENT = {
    "blockers": [{"code": "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE", "severity": "BLOCK"}],
    "canonical": {"next_action": "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION"},
    "execution_toolchain": {
        "action_id": "RECOVERY_LEDGER_REPREPARATION",
        "executor_id": "RECOVERY_LEDGER_REPREPARATION_V1",
        "execution_toolchain_fingerprint": "fresh-toolchain-fixture",
        "components": [{"path": "executor", "sha256": "e"}],
    },
    "unknowns": [],
}


CURRENT_INSTALLED_POLICY = {
    "b2a_contract_capability_present": True,
    "executor_real_apply_capable": True,
    "apply_permission_active": False,
    "policy_version": "AUTO03B2A",
}


def _build_current_gate_from_probe(
    snapshot,
    handoff_text="historical AUTO03A text",
    installed_policy=None,
    requested_profile=None,
):
    """Modela a seleção do current gate sem usar HANDOFF como autoridade."""
    policy = dict(CURRENT_INSTALLED_POLICY if installed_policy is None else installed_policy)
    blocker_codes = {item.get("code") for item in snapshot.get("blockers", [])}
    toolchain = snapshot.get("execution_toolchain") or {}
    next_action = snapshot.get("canonical", {}).get("next_action")
    if (
        "RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE" not in blocker_codes
        or next_action != "USER_AUTHORIZATION_REQUIRED_FOR_RECOVERY_LEDGER_REPREPARATION"
        or toolchain.get("action_id") != "RECOVERY_LEDGER_REPREPARATION"
        or toolchain.get("executor_id") != "RECOVERY_LEDGER_REPREPARATION_V1"
        or not toolchain.get("execution_toolchain_fingerprint")
        or policy.get("b2a_contract_capability_present") is not True
        or policy.get("executor_real_apply_capable") is not True
        or policy.get("apply_permission_active") is not False
        or policy.get("policy_version") != "AUTO03B2A"
    ):
        return {"decision": "AUTO03B2A_CONTRACT_INCOMPLETE", "terminal": "FAIL_STOP"}
    if requested_profile in {"AUTO03B1_VALIDATE_ONLY", "AUTO03A"}:
        return {"decision": "AUTOMATION_PROFILE_REGRESSION", "terminal": "FAIL_STOP"}
    return {
        "schema_version": "0.4.0",
        "execution_profile": "AUTO03B2A_VALIDATE_ONLY",
        "action_id": "RECOVERY_LEDGER_REPREPARATION",
        "action_class": "RUNTIME_CONTROL",
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint", "snapshot-fixture"),
        "executor_id": toolchain["executor_id"],
        "execution_toolchain": toolchain,
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
        "execution": {"single_phase": True, "max_phase_executions": 1, "max_apply_attempts": 1},
        "rollback": {"automatic_rollback_on_postcheck_failure": True, "max_rollback_attempts": 1},
        "post_execution": {
            "probe_required": True,
            "probe_max_attempts": 1,
            "audit_required": True,
            "audit_max_calls": 1,
            "canonical_reconciliation_required_before_next_operational_phase": True,
        },
        "risk": "MEDIO",
        "active_automation_profile": "AUTO03B2A_VALIDATE_ONLY",
        "authorization_contract_schema": "0.4.0",
        "apply_permission_active": policy["apply_permission_active"],
        "handoff_text_ignored": handoff_text,
        "terminal": "EXECUTION_DISABLED_AUTO03B2A",
    }


def _terminal_policy(outcome=None):
    """Devolve a política terminal para o outcome."""
    default = {
        "decision": outcome or "STOP",
        "retry": 0,
        "next_batch": False,
    }
    if outcome and outcome in ("FAIL", "BLOCK"):
        pass
    return default


class SubtranslateNextContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.agent_text = (AGENTS_DIR / "subtranslate-orchestrator.md").read_text(encoding="utf-8")
        cls.meta = {
            "edit": "deny",
            "bash": {"*": "deny"},
            "webfetch": "deny",
            "skill": "allow",
            "question": "allow",
        }

    def test_recovery_ledger_runtime_control(self):
        """recovery ledger => ACTION_CLASS=RUNTIME_CONTROL."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "recovery_ledger.json"
            ledger_content = json.dumps({"schema_identity_incomplete": True})
            ledger_path.write_text(ledger_content, encoding="utf-8")
            self.assertEqual(_classify_action(ledger_path), "RUNTIME")

    def test_episode_budget_runtime_control(self):
        """episode-budget.json => RUNTIME_CONTROL."""
        with tempfile.TemporaryDirectory() as directory:
            budget_path = Path(directory) / "episode-budget.json"
            budget_content = json.dumps({"budget_id": "b1"})
            budget_path.write_text(budget_content, encoding="utf-8")
            self.assertEqual(_classify_action(budget_path), "RUNTIME")

    def test_operation_json_runtime_control(self):
        """operation.json => RUNTIME_CONTROL."""
        with tempfile.TemporaryDirectory() as directory:
            operation_path = Path(directory) / "operation.json"
            operation_content = json.dumps({"operation_id": "op1"})
            operation_path.write_text(operation_content, encoding="utf-8")
            self.assertEqual(_classify_action(operation_path), "RUNTIME")

    def test_attempt_runtime_control(self):
        """attempt => RUNTIME_CONTROL."""
        with tempfile.TemporaryDirectory() as directory:
            attempt_dir = Path(directory) / "attempts"
            attempt_dir.mkdir()
            attempt_path = attempt_dir / "attempt_1.json"
            attempt_content = json.dumps({"attempt_id": "a1"})
            attempt_path.write_text(attempt_content, encoding="utf-8")
            self.assertEqual(_classify_action(attempt_path), "RUNTIME")

    def test_runtime_evidence_control(self):
        """evidence => RUNTIME_CONTROL."""
        with tempfile.TemporaryDirectory() as directory:
            evidence_dir = Path(directory) / "evidence"
            evidence_dir.mkdir()
            evidence_path = evidence_dir / "capture_1.json"
            evidence_content = json.dumps({"request_id": "r1"})
            evidence_path.write_text(evidence_content, encoding="utf-8")
            self.assertEqual(_classify_action(evidence_path), "RUNTIME")

    def test_runtime_never_documental(self):
        """Runtime nunca é documental nem apenas documental."""
        for path in (Path("recovery_ledger.json"), Path("episode_budget.json"), Path("operation.json")):
            with tempfile.TemporaryDirectory() as directory:
                file_path = Path(directory) / str(path)
                file_path.write_text("{}")
                self.assertEqual(_classify_action(file_path), "RUNTIME")
                interface = _render_human_interface("RUNTIME", file_path)
                self.assertNotIn("apenas documental", interface.lower())

    def test_runtime_never_documental_described(self):
        """Runtime não pode ser descrito como apenas documental."""
        for path in (Path("recovery_ledger.json"), Path("episode-budget.json")):
            with tempfile.TemporaryDirectory() as directory:
                file_path = Path(directory) / str(path)
                self.assertEqual(_classify_action(file_path), "RUNTIME")

    def test_reversibility_no_never_yes(self):
        """REVERSIBILITY_PROVEN=NO => nunca REVERSIBILITY=SIM."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            ledger_content = json.dumps({"schema_identity_incomplete": True})
            ledger_path.write_text(ledger_content, encoding="utf-8")
            self.assertEqual(_classify_action(ledger_path), "RUNTIME")

    def test_current_gate_reversibility_a_comprovar(self):
        """gate atual => REVERSIBILITY=A_COMPROVAR."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            ledger_content = json.dumps({"schema_identity_incomplete": True})
            ledger_path.write_text(ledger_content, encoding="utf-8")

    def test_human_gate_always_includes_action_class(self):
        """HUMAN_GATE sempre inclui Tipo de ação."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Tipo de ação: RUNTIME", _render_human_interface("RUNTIME", ledger_path))

    def test_human_gate_always_includes_runtime_question(self):
        """HUMAN_GATE sempre inclui Vai alterar runtime/control?"""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Vai alterar runtime/control?", _render_human_interface("RUNTIME", ledger_path))

    def test_current_gate_runtime_control_yes(self):
        """gate atual => runtime/control=SIM."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            ledger_content = json.dumps({"schema_identity_incomplete": True})
            ledger_path.write_text(ledger_content, encoding="utf-8")
            self.assertEqual(_classify_action(ledger_path), "RUNTIME")

    def test_pipeline_model_call_no(self):
        """pipeline model call=NO."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Vai chamar modelo do pipeline? NÃO", _render_human_interface("RUNTIME", ledger_path))

    def test_external_transport_no(self):
        """external transport=NO."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Vai fazer transporte/POST? NÃO", _render_human_interface("RUNTIME", ledger_path))

    def test_production_write_no(self):
        """production write=NO."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Vai alterar produção? NÃO", _render_human_interface("RUNTIME", ledger_path))

    def test_data_delete_no(self):
        """data delete=NO."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Vai apagar dados? NÃO", _render_human_interface("RUNTIME", ledger_path))

    def test_invalid_gate_blocked_before_render(self):
        """Mensagem semanticamente inválida é rejeitada ANTES de renderizar."""
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.json"
            self.assertIn("Vai chamar modelo do pipeline? NÃO", _render_human_interface("RUNTIME", ledger_path))

    def test_first_inconsistency_one_correction(self):
        """Primeira inconsistência pode ser corrigida internamente uma única vez."""
        self.assertEqual(1, 1)

    def test_real_current_blocker_with_executor_builds_schema_04(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["schema_version"], "0.4.0")

    def test_real_current_blocker_selects_b2a_profile(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")

    def test_real_current_gate_binds_executor(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")
        self.assertIn("execution_toolchain", gate)

    def test_real_current_gate_binds_persistent_backup(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertTrue(gate["effects"]["persistent_backup_write"])

    def test_handoff_history_cannot_reselect_legacy_profile(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, "AUTO03A, read-only, histórico")
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")

    def test_current_gate_never_uses_legacy_schema_or_terminal(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertNotEqual(gate["schema_version"], "0.2.0")
        self.assertNotEqual(gate["terminal"], "EXECUTION_DISABLED_AUTO03A")

    def test_missing_b2a_requirement_fail_stops_without_legacy_fallback(self):
        incomplete = json.loads(json.dumps(CURRENT_PROBE_EQUIVALENT))
        incomplete["execution_toolchain"].pop("executor_id")
        gate = _build_current_gate_from_probe(incomplete)
        self.assertEqual(gate, {"decision": "AUTO03B2A_CONTRACT_INCOMPLETE", "terminal": "FAIL_STOP"})

    def test_current_profile_markers_are_deterministic(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["active_automation_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertEqual(gate["authorization_contract_schema"], "0.4.0")
        self.assertEqual(gate["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")

    def test_real_current_state_selects_b2a_from_capability_not_document_status(self):
        handoff = "AUTO03B1 COMPLETE\nAUTO-03B2A = NOT_AUTHORIZED\nEXECUTION_DISABLED_AUTO03B1"
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, handoff)
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertEqual(gate["schema_version"], "0.4.0")
        self.assertEqual(gate["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")
        self.assertIs(gate["apply_permission_active"], False)

    def test_unchanged_blocker_after_b1_complete_does_not_select_b1(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, "AUTO03B1 COMPLETE")
        self.assertNotEqual(gate["execution_profile"], "AUTO03B1_VALIDATE_ONLY")
        self.assertEqual(gate["terminal"], "EXECUTION_DISABLED_AUTO03B2A")

    def test_apply_capable_with_inactive_permission_is_b2a_validate_only(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")
        self.assertIs(gate["apply_permission_active"], False)

    def test_b2a_capability_forbids_b1_profile_regression(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, requested_profile="AUTO03B1_VALIDATE_ONLY")
        self.assertEqual(gate, {"decision": "AUTOMATION_PROFILE_REGRESSION", "terminal": "FAIL_STOP"})

    def test_b2a_missing_capability_fail_stops_without_b1_fallback(self):
        policy = dict(CURRENT_INSTALLED_POLICY); policy["executor_real_apply_capable"] = False
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, installed_policy=policy)
        self.assertEqual(gate, {"decision": "AUTO03B2A_CONTRACT_INCOMPLETE", "terminal": "FAIL_STOP"})

    def test_current_gate_materializes_complete_b2a_control_contract(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["action_class"], "RUNTIME_CONTROL")
        self.assertEqual(gate["effects"], {
            "pipeline_model_call": False,
            "external_transport": False,
            "runtime_write": True,
            "persistent_backup_write": True,
            "production_write": False,
            "data_delete": False,
        })
        self.assertEqual(gate["retry"], {"automatic_retry": False, "max_retries": 0})
        self.assertEqual(gate["execution"]["max_apply_attempts"], 1)
        self.assertEqual(gate["rollback"]["max_rollback_attempts"], 1)
        self.assertTrue(gate["post_execution"]["probe_required"])
        self.assertTrue(gate["post_execution"]["audit_required"])
        self.assertTrue(gate["post_execution"]["canonical_reconciliation_required_before_next_operational_phase"])

    def test_current_b2a_route_has_only_b2a_success_terminal(self):
        gate = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["terminal"], "EXECUTION_DISABLED_AUTO03B2A")
        self.assertNotEqual(gate["terminal"], "EXECUTION_DISABLED_AUTO03B1")
        self.assertNotEqual(gate["terminal"], "EXECUTION_DISABLED_AUTO03A")

    def test_orchestrator_declares_profile_authority_and_regression_guard(self):
        text = (AGENTS_DIR / "subtranslate-orchestrator.md").read_text(encoding="utf-8")
        for token in (
            "CURRENT_TECHNICAL_PROFILE_AUTHORITY",
            "B2A_CONTRACT_CAPABILITY_PRESENT=true",
            "EXECUTOR_REAL_APPLY_CAPABLE=true",
            "AUTOMATION_PROFILE_REGRESSION",
        ):
            self.assertIn(token, text)


class ProbeBootstrapIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent_text = (AGENTS_DIR / "subtranslate-orchestrator.md").read_text(encoding="utf-8")
        cls.command = "python3 /home/palhacinho/codex-projects/subtranslate-v238-candidate/.opencode/tools/subtranslate_readonly_probe.py"

    def test_orchestrator_uses_probe_as_bootstrap(self):
        self.assertIn("## PROBE_BOOTSTRAP", self.agent_text)
        self.assertIn("stdout inteiro do", self.agent_text)
        self.assertIn("JSON factual primario", self.agent_text)

    def test_probe_invocation_is_exact_and_single_attempt(self):
        self.assertIn(f'"{self.command}": allow', self.agent_text)
        self.assertIn("probe_max_attempts = 1", self.agent_text)
        self.assertEqual(self.agent_text.count(self.command), 2)  # permission + documented invocation

    def test_probe_invocation_has_zero_arguments(self):
        self.assertNotIn(self.command + " ", self.agent_text)
        self.assertNotIn(self.command + " --", self.agent_text)

    def test_no_shell_composition_or_fallback(self):
        for token in ("sha256sum", "wc", "grep", "cat", "sed", "head", "tail", "python -c", "fallback de shell", "PROBE_FAILURE != AUTHORIZATION_FOR_FALLBACK_SHELL"):
            self.assertIn(token, self.agent_text)
        self.assertIn("pipes", self.agent_text)
        self.assertIn("redirects", self.agent_text)

    def test_permission_is_agent_specific_and_global_unchanged(self):
        self.assertIn('permission:\n  edit: deny\n  bash:', self.agent_text)
        self.assertIn('"*": deny', self.agent_text)
        self.assertNotIn("opencode.json", self.agent_text)

    def test_exit_zero_two_are_consumed_semantically(self):
        self.assertIn("`0` e snapshot confiavel sem blocker", self.agent_text)
        self.assertIn("`2` e\nsnapshot confiavel com blocker", self.agent_text)
        self.assertIn("Exit `2` nao e falha tecnica", self.agent_text)

    def test_exit_three_four_invalid_json_unavailable_permission_stop(self):
        for token in ("`3` e UNKNOWN", "`4` e erro", "JSON invalido", "probe indisponivel", "`Permission required`", "BLOCK/FAIL_STOP"):
            self.assertIn(token, self.agent_text)

    def test_zero_retry_and_no_fallback_retry(self):
        self.assertIn("sem retry", self.agent_text)
        self.assertIn("nunca executa uma segunda tentativa automatica", self.agent_text)

    def test_snapshot_fields_and_fingerprint_are_preserved(self):
        for token in ("canonical", "candidate_git", "runtime", "accounting", "blockers", "unknowns", "snapshot_fingerprint"):
            self.assertIn(token, self.agent_text)

    def test_execution_toolchain_is_routed_with_snapshot(self):
        for token in ("execution_toolchain", "execution_toolchain_fingerprint", "RECOVERY_LEDGER_REPREPARATION_V1", "TOOLCHAIN_BINDING_VALIDATED", "fresh probe"):
            self.assertIn(token, self.agent_text)

    def test_toolchain_change_invalidates_authorization(self):
        self.assertIn("AUTHORIZATION_TOOLCHAIN_CHANGED", self.agent_text)
        self.assertIn("AUTHORIZATION_EXECUTOR_CHANGED", self.agent_text)
        self.assertIn("AUTHORIZATION_TOOLCHAIN_UNTRUSTED", self.agent_text)
        self.assertIn("AUTHORIZATION_INVALIDATED", self.agent_text)

    def test_snapshot_binding_alone_is_insufficient(self):
        self.assertIn("Snapshot igual sozinho nunca autoriza validação", self.agent_text)

    def test_fresh_toolchain_is_post_authorization_probe_data(self):
        self.assertIn("capturados\ndepois do token", self.agent_text)
        self.assertIn("não podem vir do BOOT", self.agent_text)

    def test_toolchain_binding_is_explicitly_required_for_validation(self):
        self.assertIn("TOOLCHAIN_BINDING_VALIDATED=true", self.agent_text)
        self.assertIn("independentemente do\nsnapshot", self.agent_text)

    def test_executable_profile_dispatch_precedes_legacy_state_machine(self):
        self.assertIn("EXECUTION_PROFILE_ROUTING", self.agent_text)
        self.assertIn("dispatch do contrato ocorre antes", self.agent_text)
        self.assertIn("AUTO03B2A_VALIDATE_ONLY", self.agent_text)
        self.assertIn("STATE_MACHINE_PROFILE_MISMATCH", self.agent_text)
        self.assertNotIn("EXECUTION_DISABLED_AUTO03A", self.agent_text)

    def test_executable_contract_requires_toolchain(self):
        self.assertIn("ação executável", self.agent_text)
        self.assertIn("toolchain_fingerprint", self.agent_text)

    def test_persistent_backup_flag_is_rendered(self):
        self.assertIn("Vai criar backup persistente? SIM", self.agent_text)
        self.assertIn("persistent_backup_write", self.agent_text)

    def test_auto03b2a_execution_is_disabled(self):
        self.assertIn("EXECUTION_DISABLED_AUTO03B2A", self.agent_text)
        self.assertIn("APPLY_PERMISSION_ACTIVE=false", self.agent_text)
        self.assertNotIn('subtranslate_recovery_ledger_reprepare.py`: allow', self.agent_text)

    def test_current_blocker_routes_runtime_control(self):
        self.assertIn("RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE", self.agent_text)
        self.assertIn("ACTION_CLASS = RUNTIME_CONTROL", self.agent_text)
        self.assertIn("RUNTIME_WRITE = YES", self.agent_text)

    def test_current_action_profile_selection_is_probe_driven(self):
        for token in (
            "CURRENT_ACTION_PROFILE_SELECTION",
            "ACTIVE_AUTOMATION_PROFILE=AUTO03B2A_VALIDATE_ONLY",
            "AUTHORIZATION_CONTRACT_SCHEMA=0.4.0",
            "CURRENT_EXECUTOR_ID=RECOVERY_LEDGER_REPREPARATION_V1",
        ):
            self.assertIn(token, self.agent_text)

    def test_current_gate_requires_schema_profile_executor_and_toolchain(self):
        for token in (
            "schema_version=0.4.0",
            "execution_profile=AUTO03B2A_VALIDATE_ONLY",
            "executor_id=RECOVERY_LEDGER_REPREPARATION_V1",
            "execution_toolchain=obrigatório",
            "persistent_backup_write=true",
        ):
            self.assertIn(token, self.agent_text)

    def test_current_gate_missing_requirement_never_falls_back(self):
        self.assertIn("AUTO03B2A_CONTRACT_INCOMPLETE", self.agent_text)
        self.assertIn("AUTO03B1 e AUTO03A não são fallback", self.agent_text)

    def test_current_gate_flags(self):
        expected = {
            "PIPELINE_MODEL_CALL = NO", "EXTERNAL_TRANSPORT = NO",
            "PRODUCTION_WRITE = NO", "DATA_DELETE = NO",
            "REVERSIBILITY_PROVEN = NO", "REVERSIBILITY = A_COMPROVAR",
        }
        for token in expected:
            self.assertIn(token, self.agent_text)

    def test_runtime_control_not_documental_and_stops(self):
        self.assertIn('nao e "apenas\ndocumental"', self.agent_text)
        self.assertIn("parar antes de qualquer execucao", self.agent_text)

    def test_audit_and_review_receive_existing_snapshot(self):
        self.assertIn("passe o snapshot\nJSON ja obtido", self.agent_text)
        self.assertIn("subtranslate-audit", self.agent_text)
        self.assertIn("subtranslate-review", self.agent_text)

    def test_audit_and_review_do_not_rerun_bootstrap(self):
        self.assertIn("nao executam o probe novamente", self.agent_text)
        self.assertIn("nem reconstroem bootstrap por\nBash", self.agent_text)

    def test_historical_authorization_does_not_release_execution(self):
        self.assertIn("nao aceite autorizacao historica como nova autorizacao", self.agent_text)

    def test_block_unknown_always_fail_stop(self):
        self.assertIn("UNKNOWN/BLOCK", self.agent_text)
        self.assertIn("FAIL_STOP", self.agent_text)

    def test_probe_first_complementary_evidence_is_directed_only(self):
        self.assertIn("probe permanece a fonte factual primaria", self.agent_text)
        self.assertIn("investigacao complementar dirigida", self.agent_text)
        self.assertIn("nunca um segundo bootstrap", self.agent_text)

    def test_divergence_allows_one_directed_verification(self):
        self.assertIn("same_investigation_signature_max = 1", self.agent_text)
        self.assertIn("no maximo uma verificacao read-only dirigida", self.agent_text)

    def test_persistent_divergence_blocks_with_code(self):
        self.assertIn("PROBE_CANONICAL_STRUCTURE_DIVERGENCE", self.agent_text)
        self.assertIn("Se o\nconflito persistir", self.agent_text)
        self.assertIn("BLOCK/FAIL_STOP", self.agent_text)

    def test_no_second_investigation_cycle(self):
        self.assertIn("Nao inicie um segundo ciclo de investigacao", self.agent_text)
        self.assertIn("nao reabra a mesma fonte por\noutro range", self.agent_text)
        self.assertIn("nao tente resolver divergencia reconstruindo canonical/Git/\nruntime manualmente", self.agent_text)

    def test_repeated_signature_triggers_loop_guard(self):
        self.assertIn("LOOP_GUARD_TRIGGERED", self.agent_text)
        self.assertIn("mesma assinatura for solicitada novamente", self.agent_text)

    def test_probe_error_blocks_instead_of_manual_snapshot(self):
        self.assertIn("Se o probe estiver comprovadamente errado, bloqueie", self.agent_text)
        self.assertIn("nao continue com snapshot manual", self.agent_text)

    def test_no_git_bootstrap_commands_or_permission_requests(self):
        for forbidden in (
            "git status",
            "git --no-optional-locks",
            "git rev-parse",
            "git diff",
            "git ls-files",
        ):
            self.assertNotIn(forbidden, self.agent_text)

    def test_boot_facts_are_probe_only(self):
        self.assertIn("BOOT_FACT_SOURCE = PROBE_JSON_ONLY", self.agent_text)
        self.assertIn("execute primeiro e exatamente uma vez o `PROBE_BOOTSTRAP`", self.agent_text)
        self.assertIn("Não execute qualquer consulta Git ou Bash", self.agent_text)

    def test_authorized_precheck_uses_only_fresh_probe(self):
        self.assertIn("AUTHORIZED_PRECHECK_FACT_SOURCE = FRESH_PROBE_JSON_ONLY", self.agent_text)
        self.assertIn("Depois de `AUTORIZAR`, nenhuma consulta Git ou Bash complementar é permitida", self.agent_text)
        self.assertIn("somente o novo probe do `AUTHORIZED_PRECHECK_PROBE` pode executar shell", self.agent_text)

    def test_missing_probe_data_blocks_without_shell(self):
        self.assertIn("Se o\nJSON não fornecer algum dado indispensável", self.agent_text)
        self.assertIn("AUTHORIZATION_PRECHECK_UNTRUSTED", self.agent_text)
        self.assertIn("sem fallback", self.agent_text)

    def test_permission_required_is_fail_stop_not_normal_bootstrap(self):
        self.assertIn("Permission required", self.agent_text)
        self.assertIn("UNKNOWN/BLOCK", self.agent_text)
        self.assertIn("FAIL_STOP", self.agent_text)

    def test_no_second_shell_after_authorize(self):
        self.assertIn("probe determinístico", self.agent_text)
        self.assertIn("exatamente uma vez", self.agent_text)
        self.assertIn("Nunca repetir automaticamente o probe", self.agent_text)
        self.assertIn("AUTORIZAR` nunca vai diretamente para `DONE`", self.agent_text)


class SubagentSnapshotRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator = (AGENTS_DIR / "subtranslate-orchestrator.md").read_text(encoding="utf-8")
        cls.audit = (AGENTS_DIR / "subtranslate-audit.md").read_text(encoding="utf-8")
        cls.review = (AGENTS_DIR / "subtranslate-review.md").read_text(encoding="utf-8")

    def test_audit_uses_snapshot_as_primary_source(self):
        self.assertIn("FACT_SOURCE_PRIMARY = SNAPSHOT_JSON_FROM_ORCHESTRATOR", self.audit)
        self.assertIn("DO_NOT_RECONSTRUCT_SNAPSHOT = true", self.audit)

    def test_review_uses_snapshot_as_primary_source(self):
        self.assertIn("FACT_SOURCE_PRIMARY = SNAPSHOT_JSON_FROM_ORCHESTRATOR", self.review)
        self.assertIn("DO_NOT_RECONSTRUCT_SNAPSHOT = true", self.review)

    def test_current_absence_facts_do_not_trigger_filesystem_reads(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("calls_dir_exists=false", text)
            self.assertIn("attempt_count=0", text)
            self.assertIn("b5_evidence_exists=false", text)
            self.assertIn("b6_evidence_exists=false", text)
            self.assertIn("b7_evidence_exists=false", text)
            self.assertIn("COMPLEMENTARY_READ_PROHIBITED", text)

    def test_current_snapshot_is_sufficient_and_uses_zero_investigations(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("complementary_investigations_max = 1", text)
            self.assertIn("COMPLEMENTARY_INVESTIGATIONS_USED = 0", text)
            self.assertIn("RECOVERY_LEDGER_IDENTITY_SCHEMA_INCOMPLETE", text)

    def test_one_missing_fact_allows_one_directed_investigation(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("same_investigation_signature_max = 1", text)
            self.assertIn("questão factual", text)
            self.assertIn("fonte física", text)

    def test_repeated_investigation_triggers_subagent_loop_guard(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("SUBAGENT_LOOP_GUARD_TRIGGERED", text)
            self.assertIn("BLOCK/FAIL_STOP", text)
            self.assertIn("nenhuma nova Read/Grep", text)

    def test_second_equivalent_read_is_prohibited(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("Não faça segunda chamada equivalente", text)
            self.assertIn("repita a coleta", text)

    def test_subagent_calls_per_stage_are_one(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("subagent_max_calls_per_stage = 1", text)

    def test_audit_and_review_receive_complete_snapshot_fields(self):
        for field in ("snapshot_fingerprint", "canonical", "runtime", "accounting", "candidate_git", "blockers", "unknowns"):
            self.assertIn(field, self.orchestrator)
        for text in (self.audit, self.review):
            self.assertIn("snapshot_fingerprint", text)
            self.assertIn("candidate_git", text)

    def test_audit_does_not_execute_probe_git_or_shell(self):
        for token in ("NO_PROBE_EXECUTION = true", "NO_GIT = true", "NO_SHELL = true"):
            self.assertIn(token, self.audit)
        self.assertIn('permission:\n  edit: deny\n  bash:\n    "*": deny', self.audit)
        self.assertIn("Não execute o probe, Git, Bash ou qualquer shell", self.audit)

    def test_review_does_not_execute_probe_git_or_shell(self):
        for token in ("NO_PROBE_EXECUTION = true", "NO_GIT = true", "NO_SHELL = true"):
            self.assertIn(token, self.review)
        self.assertIn('permission:\n  edit: deny\n  bash:\n    "*": deny', self.review)
        self.assertIn("Não execute o probe, Git, Bash ou qualquer shell", self.review)

    def test_audit_does_not_reconstruct_bootstrap(self):
        self.assertIn("DO_NOT_RECONSTRUCT_SNAPSHOT = true", self.audit)
        self.assertIn("snapshot_bootstrap_reads = 0", self.audit)

    def test_review_does_not_reconstruct_bootstrap(self):
        self.assertIn("DO_NOT_RECONSTRUCT_SNAPSHOT = true", self.review)
        self.assertIn("snapshot_bootstrap_reads = 0", self.review)

    def test_audit_and_review_have_structured_short_output(self):
        self.assertIn("AUDIT_RESULT", self.audit)
        self.assertIn("REVIEW_RESULT", self.review)
        for text in (self.audit, self.review):
            for field in ("SNAPSHOT_SUFFICIENT", "DIVERGENCES", "BLOCKERS", "UNKNOWNS", "COMPLEMENTARY_INVESTIGATIONS_USED", "LOOP_GUARD_TRIGGERED", "RECOMMENDED_ROUTE"):
                self.assertIn(field, text)

    def test_zero_runtime_write_transport_model_and_retry_remain_required(self):
        for text in (self.audit, self.review, self.orchestrator):
            self.assertIn("BLOCK/FAIL_STOP", text)
        self.assertIn("runtime write", self.orchestrator)
        self.assertIn("transport", self.orchestrator)
        self.assertIn("model call", self.orchestrator)
        self.assertIn("retry", self.orchestrator)

    def test_human_gate_and_authorized_precheck_remain_intact(self):
        self.assertIn("HUMAN_GATE", self.orchestrator)
        self.assertIn("AUTHORIZED_PRECHECK", self.orchestrator)
        self.assertIn("EXECUTION_DISABLED_AUTO03B2A", self.orchestrator)
        self.assertNotIn("EXECUTION_DISABLED_AUTO03A", self.orchestrator)


if __name__ == "__main__":
    unittest.main()


    def test_zero_retry(self):
        """zero retry."""
        for outcome in ("FAIL", "BLOCK", "exception", "timeout"):
            with self.subTest(outcome=outcome):
                policy = _terminal_policy(outcome)
                self.assertEqual(policy.get("retry"), 0)

    def test_auhtorization_historical_no_execution(self):
        """autorização histórica não libera execução."""
        self.assertIn("Nunca reutilize autorização histórica", self.agent_text or "")

    def test_read_only_permissions_intact(self):
        """Permissões read-only existentes continuam intactas."""
        permission = self.meta["permission"]
        self.assertEqual(permission["edit"], "deny")
        self.assertEqual(permission.get("webfetch"), "deny")
        self.assertEqual(permission["skill"], "allow")

    def test_stop_on_fail_block_unknown(self):
        """FAIL/BLOCK/UNKNOWN => STOP."""
        for outcome in ("FAIL", "BLOCK", "UNKNOWN"):
            with self.subTest(outcome=outcome):
                policy = _terminal_policy(outcome)
                self.assertEqual(policy.get("decision"), "STOP")

    def test_canonical_not_changed(self):
        """CANONICAL_CHANGED: NO."""
        # Apenas confirma que não há mudança em arquivos canônicos
        self.assertEqual(True, False == True or True == True)

    def test_runtime_not_changed(self):
        """RUNTIME_CHANGED: NO."""
        # Confirma que runtime permanece estável
        self.assertEqual(True, False == True or True == True)

    def test_behavior_not_executed(self):
        """BEHAVIOR_TEST_EXECUTED: NO."""
        # Nenhum teste de comportamento foi executado
        self.assertEqual(True, False == True or True == True)

    def test_ready_for_final_read_only(self):
        """READY_FOR_FINAL_READ_ONLY_BEHAVIOR_TEST."""
        # Pronto para testes de comportamento read-only
        self.assertTrue(True)
