import importlib.util
import json
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "b5_preflight", ROOT / ".opencode/tools/subtranslate_b5_preflight.py"
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def probe_fixture(**overrides):
    base = {
        "canonical": {
            "project_state": {
                "state": preflight.PRE_STATE,
                "latest_decision": preflight.PRE_DECISION,
                "next_action": preflight.PRE_NEXT,
                "current_operation": "SUBTRANSLATE_V238_E07_R6C_BATCHES_1_7",
            }
        },
        "runtime": {
            "episode_budget": {
                "initial_consumed": 1,
                "retry_consumed": 0,
                "reservation_count": 1,
                "reservations": [{
                    "state": "PARSED_VALID",
                    "model_tag": preflight.B5_MODEL,
                    "model_digest": preflight.B5_MODEL_DIGEST,
                }],
            },
            "calls_attempts": {"attempt_count": 1},
            "B5_B6_B7_evidence": {"present": False, "observable": [],
                                  "b5_evidence_exists": False, "b6_evidence_exists": False,
                                  "b7_evidence_exists": False},
        },
        "execution_toolchains": {
            "B5_BATCH_EXECUTION": {
                "executor_id": preflight.B5_EXECUTOR_ID,
                "materialized": True,
                "execution_toolchain_fingerprint": "a" * 64,
                "model_binding": {"model_tag": preflight.B5_MODEL, "model_digest": preflight.B5_MODEL_DIGEST},
                "transport_guard": {"max_client_calls": 1, "max_http_posts": 1, "max_retries": 0},
            }
        },
        "accounting": {"canonical_accounting": {}, "runtime_observed_accounting": {},
                       "comparisons": {"initial_consumed": "MATCH", "retry_consumed": "MATCH"}},
        "snapshot_fingerprint": "b" * 64,
        "blockers": [],
        "unknowns": [],
        "integrity": {"snapshot_consistent": True, "side_effects_performed": False},
    }
    return _deep_merge(base, overrides)


def _deep_merge(base, overrides):
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def run_plan(probe_result):
    with mock.patch.object(preflight, "fresh_probe", return_value=probe_result):
        return preflight.plan()


def test_plan_is_read_only_and_ready():
    result = run_plan(probe_fixture())
    assert result["status"] == "READY"
    assert result["mode"] == "PLAN_READ_ONLY"
    assert result["side_effects_performed"] is False
    assert result["action_id"] == "B5_BATCH_EXECUTION"
    assert result["executor_id"] == "B5_BATCH_EXECUTOR_V1"
    assert result["b5_execution_authorized"] is False
    assert result["b6_execution_authorized"] is False
    assert result["b7_execution_authorized"] is False
    assert result["b4_reexecution"] is False
    assert result["model_call"] is False
    assert result["transport"] is False
    assert result["runtime_write"] is False


def test_ready_declares_target_and_required_from_canonical_authorization():
    result = run_plan(probe_fixture())
    target = result["target"]
    assert target["operation_id"] == "SUBTRANSLATE_V238_E07_R6C_BATCHES_1_7"
    assert target["logical_batch_id"] == "v226-initial-000005"
    assert target["model_tag"] == "qwen3.5:9b"
    assert target["model_digest"] == preflight.B5_MODEL_DIGEST
    assert result["required_from_canonical_authorization"] == [
        "operation_id", "family_id", "episode_id", "unit_ids",
        "request_payload_sha256", "unit_membership_sha256"
    ]
    assert result["toolchain"]["materialized"] is True
    assert result["toolchain"]["execution_toolchain_fingerprint"] == "a" * 64
    assert result["next_gate"] == "B5_EXECUTION_AUTHORIZATION_REQUIRED"


def test_prestate_mismatch_blocks():
    probe = probe_fixture()
    probe["canonical"]["project_state"]["next_action"] = "SOMETHING_ELSE"
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "CANONICAL_POINTER_PRESTATE_MISMATCH"
    else:
        raise AssertionError("prestate mismatch accepted")


def test_b4_not_closed_blocks():
    probe = probe_fixture()
    probe["runtime"]["episode_budget"]["initial_consumed"] = 0
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B4_LEDGER_TERMINAL_FACTS_MISMATCH"
    else:
        raise AssertionError("B4 not closed accepted")


def test_b5_b6_b7_evidence_present_blocks():
    probe = probe_fixture()
    probe["runtime"]["B5_B6_B7_evidence"] = {"present": True, "observable": ["B5"],
                                             "b5_evidence_exists": True,
                                             "b6_evidence_exists": False, "b7_evidence_exists": False}
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B5_B6_B7_NOT_ABSENT"
    else:
        raise AssertionError("B5 evidence accepted")


def test_toolchain_not_materialized_blocks():
    probe = probe_fixture()
    probe["execution_toolchains"]["B5_BATCH_EXECUTION"]["materialized"] = False
    probe["execution_toolchains"]["B5_BATCH_EXECUTION"]["execution_toolchain_fingerprint"] = None
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B5_TOOLCHAIN_NOT_MATERIALIZED"
    else:
        raise AssertionError("unmaterialized toolchain accepted")


def test_toolchain_binding_mismatch_blocks():
    probe = probe_fixture()
    probe["execution_toolchains"]["B5_BATCH_EXECUTION"]["transport_guard"]["max_http_posts"] = 2
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B5_TOOLCHAIN_BINDING_MISMATCH"
    else:
        raise AssertionError("binding mismatch accepted")


def test_accounting_mismatch_blocks():
    probe = probe_fixture()
    probe["accounting"]["comparisons"]["initial_consumed"] = "MISMATCH"
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B5_ACCOUNTING_MISMATCH"
    else:
        raise AssertionError("accounting mismatch accepted")


def test_snapshot_fingerprint_unbound_blocks():
    probe = probe_fixture()
    probe["snapshot_fingerprint"] = None
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B5_SNAPSHOT_FINGERPRINT_UNBOUND"
    else:
        raise AssertionError("unbound snapshot accepted")


def test_operation_id_not_derivable_blocks():
    probe = probe_fixture()
    probe["canonical"]["project_state"]["current_operation"] = None
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B5_OPERATION_ID_NOT_DERIVABLE"
    else:
        raise AssertionError("missing operation_id accepted")


def test_reservation_model_mismatch_blocks():
    probe = probe_fixture()
    probe["runtime"]["episode_budget"]["reservations"][0]["model_tag"] = "other-model"
    try:
        run_plan(probe)
    except preflight.Blocked as exc:
        assert str(exc) == "B4_RESERVATION_MODEL_MISMATCH"
    else:
        raise AssertionError("reservation model mismatch accepted")


def test_cli_contract_has_no_apply_surface():
    source = (ROOT / ".opencode/tools/subtranslate_b5_preflight.py").read_text(encoding="utf-8")
    assert 'add_argument("--plan"' in source
    assert 'add_argument("--apply"' not in source
    assert "requests" not in source
    assert "urlopen" not in source
    assert "subtranslate_b5_executor" not in source


def test_plan_never_writes():
    probe = probe_fixture()
    with mock.patch.object(preflight, "fresh_probe", return_value=probe):
        result = preflight.plan()
    assert result["side_effects_performed"] is False
    assert result["status"] == "READY"
