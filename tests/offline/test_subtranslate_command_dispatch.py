import copy
import unittest
from pathlib import Path

from tests.offline.test_subtranslate_next_routing import (
    CURRENT_PROBE_EQUIVALENT,
    _build_current_gate_from_probe,
)


ROOT = Path(__file__).resolve().parents[2]
COMMAND_PATH = ROOT / ".opencode/commands/subtranslate-next.md"
ORCHESTRATOR_PATH = ROOT / ".opencode/agents/subtranslate-orchestrator.md"
PROBE_PATH = ROOT / ".opencode/tools/subtranslate_readonly_probe.py"
COMMAND = COMMAND_PATH.read_text(encoding="utf-8")
ORCHESTRATOR = ORCHESTRATOR_PATH.read_text(encoding="utf-8")

FORBIDDEN_COMMAND_AUTHORITIES = (
    "AUTO03A",
    "AUTO03B1",
    "AUTO03B2A",
    "EXECUTION_DISABLED_",
    "AUTHORIZATION_CONTRACT_SCHEMA",
    "schema_version",
    "RECOVERY_LEDGER_REPREPARATION_V1",
    "HANDOFF",
)


def command_agent(command_text):
    if not command_text.startswith("---\n"):
        return None
    frontmatter = command_text.split("---\n", 2)[1]
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "agent":
            return value.strip()
    return None


def dispatch_command(command_text, snapshot, handoff_text=""):
    """Executable model of the physical command-to-agent delegation chain."""
    if command_agent(command_text) != "subtranslate-orchestrator":
        return {"decision": "COMMAND_DISPATCH_INVALID", "terminal": "FAIL_STOP"}
    if any(token in command_text for token in FORBIDDEN_COMMAND_AUTHORITIES):
        return {"decision": "COMMAND_PROFILE_AUTHORITY_LEAK", "terminal": "FAIL_STOP"}
    if "somente dispatch" not in command_text or "state machine do agent selecionado" not in command_text:
        return {"decision": "COMMAND_DISPATCH_INVALID", "terminal": "FAIL_STOP"}
    return _build_current_gate_from_probe(snapshot, handoff_text=handoff_text)


class CommandDispatchTraceTests(unittest.TestCase):
    def test_command_selects_orchestrator_directly(self):
        self.assertEqual(command_agent(COMMAND), "subtranslate-orchestrator")

    def test_delegating_command_is_bound_into_execution_toolchain(self):
        probe_source = PROBE_PATH.read_text(encoding="utf-8")
        self.assertEqual(command_agent(COMMAND), "subtranslate-orchestrator")
        self.assertIn('".opencode/commands/subtranslate-next.md"', probe_source)

    def test_command_is_dispatch_only(self):
        self.assertIn("somente dispatch", COMMAND)
        self.assertIn("state machine do agent selecionado", COMMAND)

    def test_command_has_no_profile_authority(self):
        self.assertFalse(any(token in COMMAND for token in ("AUTO03A", "AUTO03B1", "AUTO03B2A")))

    def test_command_has_no_terminal_authority(self):
        self.assertNotIn("EXECUTION_DISABLED_", COMMAND)

    def test_command_has_no_schema_authority(self):
        self.assertNotIn("schema_version", COMMAND)
        self.assertNotIn("AUTHORIZATION_CONTRACT_SCHEMA", COMMAND)

    def test_command_has_no_executor_authority(self):
        self.assertNotIn("RECOVERY_LEDGER_REPREPARATION_V1", COMMAND)

    def test_command_has_no_handoff_profile_authority(self):
        self.assertNotIn("HANDOFF", COMMAND)

    def test_physical_current_dispatch_builds_schema_04(self):
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["schema_version"], "0.4.0")

    def test_physical_current_dispatch_builds_b2a(self):
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")

    def test_physical_current_dispatch_binds_executor(self):
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["executor_id"], "RECOVERY_LEDGER_REPREPARATION_V1")

    def test_physical_current_dispatch_keeps_permission_inactive(self):
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT)
        self.assertIs(gate["apply_permission_active"], False)

    def test_historical_b1_text_cannot_downgrade_dispatch(self):
        handoff = "AUTO03B1 COMPLETE\nEXECUTION_DISABLED_AUTO03B1"
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT, handoff)
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")

    def test_historical_b2a_not_authorized_cannot_downgrade_dispatch(self):
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT, "AUTO-03B2A = NOT_AUTHORIZED")
        self.assertEqual(gate["execution_profile"], "AUTO03B2A_VALIDATE_ONLY")

    def test_command_hardcoded_b1_fails_closed(self):
        result = dispatch_command(COMMAND + "\nAUTO03B1_VALIDATE_ONLY", CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(result, {"decision": "COMMAND_PROFILE_AUTHORITY_LEAK", "terminal": "FAIL_STOP"})

    def test_command_hardcoded_b2a_fails_closed(self):
        result = dispatch_command(COMMAND + "\nAUTO03B2A_VALIDATE_ONLY", CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(result, {"decision": "COMMAND_PROFILE_AUTHORITY_LEAK", "terminal": "FAIL_STOP"})

    def test_intermediate_agent_change_fails_closed(self):
        changed = COMMAND.replace("agent: subtranslate-orchestrator", "agent: plan")
        self.assertEqual(dispatch_command(changed, CURRENT_PROBE_EQUIVALENT)["terminal"], "FAIL_STOP")

    def test_profile_authority_is_orchestrator(self):
        self.assertIn("CURRENT_TECHNICAL_PROFILE_AUTHORITY", ORCHESTRATOR)
        self.assertNotIn("CURRENT_TECHNICAL_PROFILE_AUTHORITY", COMMAND)

    def test_b1_regression_is_fail_stop(self):
        from tests.offline.test_subtranslate_next_routing import _build_current_gate_from_probe
        result = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, requested_profile="AUTO03B1_VALIDATE_ONLY")
        self.assertEqual(result, {"decision": "AUTOMATION_PROFILE_REGRESSION", "terminal": "FAIL_STOP"})

    def test_auto03a_regression_is_fail_stop(self):
        from tests.offline.test_subtranslate_next_routing import _build_current_gate_from_probe
        result = _build_current_gate_from_probe(CURRENT_PROBE_EQUIVALENT, requested_profile="AUTO03A")
        self.assertEqual(result, {"decision": "AUTOMATION_PROFILE_REGRESSION", "terminal": "FAIL_STOP"})

    def test_current_terminal_is_b2a_disabled(self):
        gate = dispatch_command(COMMAND, CURRENT_PROBE_EQUIVALENT)
        self.assertEqual(gate["terminal"], "EXECUTION_DISABLED_AUTO03B2A")

    def test_command_cannot_call_apply_or_create_backup(self):
        self.assertNotIn("--apply", COMMAND)
        self.assertNotIn("backup", COMMAND.lower())

    def test_dispatch_contract_has_zero_external_effects(self):
        gate = dispatch_command(COMMAND, copy.deepcopy(CURRENT_PROBE_EQUIVALENT))
        self.assertFalse(gate["effects"]["pipeline_model_call"])
        self.assertFalse(gate["effects"]["external_transport"])
        self.assertEqual(gate["retry"]["max_retries"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
