from booktrans.production_control.current_execution_projection import (
    project_current_execution,
    project_handoff_consumption,
)
import pytest


def _startup(**overrides):
    facts = {
        "campaign_status": "CONTINUE", "cursor_decision": "NO_PROGRESS", "partial_kind": "NONE",
        "batch_ready": True, "terminal_checkpoint": False, "canonical_recovery": False,
        "semantic_rescue": False, "reviewer_resume": False,
    }
    facts.update(overrides)
    return project_current_execution(facts)


def test_startup_projection_prefers_existing_canonical_recovery_over_partial_barrier():
    assert _startup(cursor_decision="CURRENT_PARTIAL", partial_kind="PARTIAL", batch_ready=False, canonical_recovery=True)["action"] == "RUN_CANONICAL_RECOVERY"


def test_startup_projection_keeps_nonterminal_sealed_drift_fail_closed():
    assert _startup(cursor_decision="SEALED_STATE_DRIFT", terminal_checkpoint=False)["action"] == "BLOCK_CURSOR"


def test_startup_projection_derives_terminal_closure_without_checkpoint_08():
    assert _startup(cursor_decision="SEALED_STATE_DRIFT", terminal_checkpoint=True)["action"] == "TERMINAL_CLOSURE"


def test_single_ready_snapshot_projects_worker_consistently():
    assert _startup(batch_ready=True, campaign_status="CONTINUE", cursor_decision="NO_PROGRESS")["action"] == "RUN_WORKER"


def test_current_rebuilt_blocker_blocks_the_same_snapshot():
    assert _startup(batch_ready=True, campaign_status="BLOCKED")["action"] == "BLOCK_CAMPAIGN_STATE"


def test_handoff_projection_consumes_canonical_and_hard_stop_without_reinterpreting_them():
    assert project_handoff_consumption({"handoff_kind": "CURRENT", "handoff_status": "RECOVERY_REQUIRED", "production_disposition": "RECOVERY_REQUIRED"})["action"] == "CONSUME_CANONICAL_RECOVERY"
    assert project_handoff_consumption({"handoff_kind": "CURRENT", "handoff_status": "BLOCKED", "production_disposition": "HARD_STOP"})["action"] == "CONSUME_HARD_STOP"


@pytest.mark.parametrize(("facts", "legacy_action"), [
    ({}, "RUN_WORKER"),  # R2/create-once and B035 known-good fresh cursor.
    ({"cursor_decision": "CURRENT_PARTIAL", "partial_kind": "PARTIAL", "batch_ready": True}, "RUN_CURRENT_PARTIAL"),  # R4 prepared-seal resume.
    ({"cursor_decision": "CURRENT_PARTIAL", "partial_kind": "PARTIAL", "batch_ready": False, "canonical_recovery": True}, "RUN_CANONICAL_RECOVERY"),  # R1/R5 current authority wins.
    ({"cursor_decision": "SEALED_STATE_DRIFT", "terminal_checkpoint": False}, "BLOCK_CURSOR"),  # stale sealed nonterminal.
    ({"cursor_decision": "SEALED_STATE_DRIFT", "terminal_checkpoint": True}, "TERMINAL_CLOSURE"),  # R6 terminal lifecycle.
])
def test_shadow_parity_for_existing_resume_contract_facts(facts, legacy_action):
    """Test-only legacy table; production contains no parallel interpreter."""
    assert _startup(**facts)["action"] == legacy_action
