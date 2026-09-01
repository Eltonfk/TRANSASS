import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / ".opencode/tools/subtranslate_b4_post_execution_reconcile.py"
SPEC = importlib.util.spec_from_file_location("post_b4", MODULE_PATH)
post_b4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(post_b4)


def facts():
    return {
        "snapshot_fingerprint": "a" * 64,
        "ledger_sha256": post_b4.LEDGER_SHA,
        "attempt_id": post_b4.ATTEMPT_ID,
        "response_sha256": post_b4.RESPONSE_SHA,
        "http_posts": 1,
        "retry_count": 0,
        "execution_backup": {"root": "/fixed", "manifest_sha256": "b" * 64},
    }


def prestate():
    return {
        "state": post_b4.PRE_STATE,
        "latest_decision": post_b4.PRE_DECISION,
        "next_action": post_b4.PRE_NEXT,
        post_b4.AUTH_KEY: {"apply_permission_active": True},
        post_b4.OBS_KEY: {"attempt_count": 1},
        "historical": {"preserved": True},
    }


def test_build_after_is_additive_and_routes_only_to_b5_preflight():
    before = prestate()
    after = post_b4.build_after(before, facts(), "2026-08-22T00:00:00+00:00")
    assert before["next_action"] == post_b4.PRE_NEXT
    assert after["historical"] == before["historical"]
    assert after[post_b4.NEW_KEY]["audit_status"] == "PASS"
    assert after[post_b4.NEW_KEY]["future_side_effects_authorized"] is False
    assert after[post_b4.NEW_KEY]["b5_authorized"] is False
    assert after["state"] == post_b4.POST_STATE
    assert after["next_action"] == "B5_PREFLIGHT_READ_ONLY_REQUIRED"


def test_build_after_refuses_duplicate_reconciliation():
    before = prestate()
    before[post_b4.NEW_KEY] = {}
    try:
        post_b4.build_after(before, facts(), "2026-08-22T00:00:00+00:00")
    except post_b4.Blocked as exc:
        assert str(exc) == "RECONCILIATION_ALREADY_RECORDED"
    else:
        raise AssertionError("duplicate reconciliation accepted")


def test_cli_contract_contains_separate_plan_and_apply():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert 'add_argument("--plan"' in source
    assert 'add_argument("--apply"' in source
    assert "subtranslate_canonical_backup.py" not in source
    assert "subtranslate_b4_recovery_call.py" not in source
