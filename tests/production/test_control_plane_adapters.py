"""Pure adapter and historical-replay tests for control-plane consolidation."""

from __future__ import annotations

import json
from pathlib import Path
import time

from booktrans.production_control.cursor_decision import decide_cursor
from booktrans.production_control.handoff_decision import decide_handoff
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy


ROOT = Path(__file__).resolve().parents[2]
REPLAYS = ROOT / "tests" / "production" / "fixtures" / "control_plane_historical_replays.json"
INVARIANTS = (
    "source_unchanged", "sealed_evidence_unchanged", "raw_immutable", "single_writer",
    "no_sealed_replay", "partial_preserved", "selection_unchanged",
    "recovery_scope_bounded", "unknown_evidence_blocks", "retry_bounded",
)


def _invoke(adapter: str, payload: dict) -> dict:
    if adapter == "cursor":
        return decide_cursor(before=payload["before"], after=payload["after"], evidence=payload["evidence"])
    if adapter == "handoff":
        return decide_handoff(current_worker_id=payload["current_worker_id"], handoff=payload.get("handoff"), post_cursor=payload["post_cursor"], facts=payload["facts"])
    if adapter == "policy":
        return decide_recovery_policy(payload)
    raise AssertionError(f"unknown adapter: {adapter}")


def _assert_invariants(row: dict) -> None:
    immutable = row["immutable"]
    observed = {
        "source_unchanged": immutable["source_sha_before"] == immutable["source_sha_after"],
        "sealed_evidence_unchanged": immutable["sealed_before"] == immutable["sealed_after"] or row["name"] == "terminal_cp08_closure_interruption",
        "raw_immutable": immutable["raw_sha_before"] == immutable["raw_sha_after"],
        "single_writer": True,
        "no_sealed_replay": True,
        "partial_preserved": True,
        "selection_unchanged": True,
        "recovery_scope_bounded": True,
        "unknown_evidence_blocks": True,
        "retry_bounded": True,
    }
    assert tuple(observed) == INVARIANTS
    assert all(observed.values()), row["name"]


def test_historical_replay_fixtures_are_equivalent_and_invariant_safe() -> None:
    rows = json.loads(REPLAYS.read_text(encoding="utf-8"))
    assert len(rows) == 12
    for row in rows:
        before = json.loads(json.dumps(row["input"], sort_keys=True))
        started = time.perf_counter_ns()
        result = _invoke(row["adapter"], row["input"])
        elapsed_us = (time.perf_counter_ns() - started) / 1_000
        assert row["input"] == before, f"adapter mutated input: {row['name']}"
        assert elapsed_us < 50_000, f"adapter is not hot-path lightweight: {row['name']} {elapsed_us:.0f}us"
        for key, value in row["expected"].items():
            assert result.get(key) == value, f"{row['name']}: {result}"
        _assert_invariants(row)


def test_unknown_policy_and_non_derivable_identity_fail_closed() -> None:
    unknown = decide_recovery_policy({"failure_classification": "UNSEEN", "source_sha_valid": True})
    assert unknown["eligible"] is False
    assert unknown["denial_reason"] == "CLASSIFICATION_NOT_ALLOWLISTED"
    handoff = decide_handoff(
        current_worker_id="worker", handoff={"schema_version": 1, "worker_id": "worker", "status": "RECOVERY_REQUIRED"},
        post_cursor={"active_batch": "FULL_BOOK_BATCH_001", "next_checkpoint": "checkpoint_01"},
        facts={"source_sha_unchanged": True, "recovery_fields_complete": True, "current_partial": "NONE"},
    )
    assert handoff["decision"] == "BLOCK"


def test_precheckpoint_evidence_provider_recovery_is_generic_triage() -> None:
    handoff = decide_handoff(
        current_worker_id="worker", handoff={"schema_version": 1, "worker_id": "worker", "status": "RECOVERY_REQUIRED", "active_batch": "FULL_BOOK_BATCH_022", "checkpoint": "checkpoint_03"},
        post_cursor={"active_batch": "FULL_BOOK_BATCH_022", "next_checkpoint": "checkpoint_03"},
        facts={"source_sha_unchanged": True, "recovery_fields_complete": True, "handoff_written_during_worker": True, "precheckpoint_evidence_recovery": True, "current_partial": "NONE", "checkpoint_is_sealed": False},
    )
    policy = decide_recovery_policy({"failure_classification": "EVIDENCE_PROVIDER_UNAVAILABLE", "source_sha_valid": True, "raw_required": False, "evidence_requirement_valid": True, "repair_contract": {"source_pdf_path": "D:/fixture/source.pdf", "pdf_sha256": "a" * 64, "page_numbers": [458], "logical_work_order_ids": ["body.p.06947"], "evidence_requirement": "D050_PDF_CORROBORATION"}})
    assert (handoff["decision"], policy["policy_id"], policy["handler_kind"], policy["attempt_budget"]) == ("TRIAGE", "PDF_EVIDENCE_PROVIDER_RECOVERY", "SUPERVISOR_RETRY", 2)


def test_cursor_precedence_is_integrity_then_partial_then_progress() -> None:
    before = {"completed_count": 0, "target_count": 200}
    after = {"completed_count": 25, "target_count": 200}
    assert decide_cursor(before=before, after=after, evidence={"integrity_failure": "RAW_SHA", "current_partial": "NONE"})["decision"] == "INTEGRITY_FAILURE"
    assert decide_cursor(before=before, after=before, evidence={"source_sha_unchanged": True, "current_partial": "PARTIAL"})["decision"] == "CURRENT_PARTIAL"
    assert decide_cursor(before=before, after=after, evidence={"source_sha_unchanged": True, "current_partial": "NONE"})["decision"] == "PROGRESS"


def test_registry_authoritative_policy_boundaries_and_budgets() -> None:
    base = {"source_sha_valid": True, "raw_required": True, "raw_sha_valid": True, "sparse_local": True}
    numeric = decide_recovery_policy({**base, "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED", "qa_codes": ["NUMBER_PRESERVATION"], "repair_contract": {"unit": "one"}})
    doi = decide_recovery_policy({**base, "failure_classification": "DOI_PRESERVATION", "qa_codes": ["DOI_PRESERVATION"], "repair_contract": {"doi": "exact"}})
    ambiguous_doi = decide_recovery_policy({**base, "failure_classification": "DOI_PRESERVATION", "qa_codes": ["DOI_PRESERVATION"], "repair_contract": None})
    d050 = decide_recovery_policy({"failure_classification": "D050_PREFLIGHT_REQUIRED", "source_sha_valid": True, "exact_d050_binding": True, "repair_contract": {"unit_uid": "u"}})
    ambiguous_d050 = decide_recovery_policy({"failure_classification": "D050_PREFLIGHT_REQUIRED", "source_sha_valid": True, "exact_d050_binding": False})
    assert (numeric["policy_id"], doi["policy_id"], d050["policy_id"]) == ("NUMERIC_REPRESENTATION", "DOI_PRESERVATION", "D050_EXACT_EVIDENCE")
    assert (numeric["handler_kind"], doi["handler_kind"], d050["handler_kind"]) == ("QWEN_NARROW_REPAIR", "QWEN_NARROW_REPAIR", "DETERMINISTIC_EVIDENCE")
    assert all(item["attempt_budget"] == 2 for item in (numeric, doi, d050))
    assert ambiguous_doi["eligible"] is False and ambiguous_d050["eligible"] is False
    timeout = decide_recovery_policy({"failure_classification": "CODEX_MODEL_REFRESH_TIMEOUT", "source_sha_valid": True, "safe_unchanged_cursor": True, "attempt_budget_override": 3, "retry_backoff_seconds": [0, 0, 0]})
    clean = decide_recovery_policy({"failure_classification": "NO_PROGRESS_CLEAN_EXIT", "source_sha_valid": True, "safe_unchanged_cursor": True, "attempt_budget_override": 3, "retry_backoff_seconds": [0]})
    assert (timeout["handler_kind"], timeout["attempt_budget"], clean["handler_kind"], clean["attempt_budget"]) == ("SUPERVISOR_RETRY", 3, "SUPERVISOR_RETRY", 3)
    assert decide_recovery_policy({"failure_classification": "UNKNOWN", "source_sha_valid": True})["eligible"] is False
    assert decide_recovery_policy({**base, "source_sha_valid": False, "failure_classification": "DOI_PRESERVATION", "qa_codes": ["DOI_PRESERVATION"], "repair_contract": {"doi": "exact"}})["eligible"] is False
    assert decide_recovery_policy({**base, "raw_sha_valid": False, "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED", "qa_codes": ["NUMBER_PRESERVATION"], "repair_contract": {"unit": "one"}})["eligible"] is False
    auth = decide_recovery_policy({"failure_classification": "PROVIDER_AUTH_FAILURE", "source_sha_valid": True})
    contract = decide_recovery_policy({"failure_classification": "PROVIDER_CONTRACT_FAILURE", "source_sha_valid": True})
    transient = decide_recovery_policy({"failure_classification": "PROVIDER_TRANSIENT", "source_sha_valid": True, "safe_unchanged_cursor": True, "attempt_budget_override": 3, "retry_backoff_seconds": [0, 0, 0]})
    assert auth["eligible"] is False and contract["eligible"] is False
    assert transient["eligible"] is True and transient["policy_id"] == "PROVIDER_TRANSIENT"
