from __future__ import annotations

import pytest

from booktrans.production_worker.human_required_triage import (
    TRIAGE_SYSTEMIC_PIPELINE_GAP,
    TRIAGE_TRUE_SEMANTIC_HUMAN,
    TRIAGE_UNKNOWN,
    HumanRequiredTriageError,
    assert_human_adjudication_allowed,
    classify_human_required,
    retain_review_issues,
)
from booktrans.providers.qwen_max import ReviewIssue, ReviewResult


def _review(*issues: ReviewIssue) -> ReviewResult:
    return ReviewResult(schema_version="booktrans.structured-review.v1", unit_uid="u1", verdict="REPAIR", issues=list(issues))


def test_missing_payload_is_systemic_and_cannot_be_human_approved() -> None:
    result = classify_human_required(
        required_context={"continuation_relation": "BODY_CONTINUATION", "review_contract_version": "booktrans.review-group.v1"},
        persisted_payload=None,
    )
    assert result["classification"] == TRIAGE_SYSTEMIC_PIPELINE_GAP
    assert result["human_adjudication_allowed"] is False
    with pytest.raises(HumanRequiredTriageError, match="HUMAN_ADJUDICATION_NOT_ALLOWED_FOR_PIPELINE_GAP"):
        assert_human_adjudication_allowed(result["classification"])


def test_complete_context_requires_explicit_reason_before_true_semantic_human() -> None:
    payload = {
        "semantic_review_group": {"review_contract_version": "booktrans.review-group.v1"},
        "unit_context": {
            "review_context_version": "booktrans.review-context.v2",
            "review_contract_version": "booktrans.review-group.v1",
            "continuation_relation": "BODY_CONTINUATION",
            "current_lane": "MAIN_BODY",
            "previous_semantic_unit_uid": "prev",
            "source_structural_identity": {"review_disposition": "TRANSLATE"},
        },
    }
    required = {
        "continuation_relation": "BODY_CONTINUATION",
        "review_contract_version": "booktrans.review-group.v1",
        "current_lane": "MAIN_BODY",
        "previous_semantic_unit_uid": "prev",
        "source_structural_identity": {"review_disposition": "TRANSLATE"},
    }
    result = classify_human_required(required_context=required, persisted_payload=payload, reprocess_terminal="HUMAN_REQUIRED", auto_repair_exhausted=True)
    assert result["classification"] == TRIAGE_SYSTEMIC_PIPELINE_GAP
    assert result["human_adjudication_allowed"] is False
    result = classify_human_required(
        required_context=required, persisted_payload=payload,
        reprocess_terminal="HUMAN_REQUIRED", auto_repair_exhausted=True,
        escalation_reason="RECOVERY_EXHAUSTED_MATERIAL_DEFECT",
    )
    assert result["classification"] == TRIAGE_TRUE_SEMANTIC_HUMAN
    assert result["escalation_reason"] == "RECOVERY_EXHAUSTED_MATERIAL_DEFECT"
    assert_human_adjudication_allowed(result["classification"])


def test_contract_or_unexplained_human_terminal_cannot_become_true_semantic_human() -> None:
    result = classify_human_required(
        required_context={}, persisted_payload={"unit_context": {"review_context_version": "booktrans.review-context.v2"}},
        reprocess_terminal="HUMAN_REQUIRED", auto_repair_exhausted=False,
    )
    assert result["classification"] == TRIAGE_SYSTEMIC_PIPELINE_GAP
    assert result["gaps"] == ["HUMAN_ESCALATION_REASON_MISSING"]


def test_unknown_when_no_gaps_and_no_reprocess_terminal() -> None:
    payload = {
        "semantic_review_group": {"review_contract_version": "booktrans.review-group.v1"},
        "unit_context": {
            "review_context_version": "booktrans.review-context.v2",
            "review_contract_version": "booktrans.review-group.v1",
            "continuation_relation": "BODY_CONTINUATION",
            "current_lane": "MAIN_BODY",
            "previous_semantic_unit_uid": "prev",
        },
    }
    required = {
        "continuation_relation": "BODY_CONTINUATION",
        "review_contract_version": "booktrans.review-group.v1",
        "current_lane": "MAIN_BODY",
        "previous_semantic_unit_uid": "prev",
    }
    result = classify_human_required(required_context=required, persisted_payload=payload)
    assert result["classification"] == TRIAGE_UNKNOWN
    assert result["human_adjudication_allowed"] is False


def test_numeric_literal_deletion_issue_is_dropped() -> None:
    review = _review(
        ReviewIssue(
            code="UNTRANSLATED_TEXT",
            affected_segment_indices=[0],
            reason="page number artifact",
            repair_instruction="Delete the segment entirely.",
        )
    )
    kept = retain_review_issues(review, {"source_structural_identity": {"review_disposition": "PRESERVE_NUMERIC_LITERAL"}})
    assert kept.verdict == "ACCEPT"
    assert kept.issues == []


def test_footnote_marker_false_positive_is_dropped_without_relaxing_terminology() -> None:
    review = _review(
        ReviewIssue(
            code="E_OMISSION",
            affected_segment_indices=[0],
            reason="The source begins with a footnote marker 2 without context.",
            repair_instruction="If it is a layout artifact, remove it from the target.",
        ),
        ReviewIssue(
            code="E_TERMINOLOGY",
            affected_segment_indices=[0],
            reason="security bugs vs 安全漏洞",
            repair_instruction="Change 安全漏洞 to 安全缺陷.",
        ),
    )
    kept = retain_review_issues(review, {"current_lane": "FOOTNOTE"})
    assert kept.verdict == "REPAIR"
    assert [issue.code for issue in kept.issues] == ["E_TERMINOLOGY"]
