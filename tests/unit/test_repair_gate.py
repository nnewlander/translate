from __future__ import annotations

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import IntentionalEmptyAuthorization, ReviewIssue, ReviewResult
from booktrans.providers.repair_gate import SEMANTIC_NARROW_POLICY, decide_approved_repair_contract
from booktrans.providers.translation_provider import TranslationProviderRequest


def _provider_request(*, protected: bool = False) -> TranslationProviderRequest:
    segments = [
        CalibrationSourceSegment(kind="anchored", text="Alpha 2 tokens.", anchor_ref="a0", island_ref="island.001"),
        CalibrationSourceSegment(
            kind="protected" if protected else "anchored",
            text="https://example.test" if protected else "Beta segment.",
            anchor_ref="a1",
            island_ref="island.002",
        ),
    ]
    request = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-repair",
        display_id="body.p.00002",
        story="body",
        content_role="body_paragraph",
        source_text="Alpha 2 tokens. Beta segment.",
        source_segments=segments,
    )
    return TranslationProviderRequest(request=request)


def _current(provider_request: TranslationProviderRequest) -> TranslationResult:
    request = provider_request.request
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="甲", kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment in request.source_segments
        ],
    )


def _review(*issues: ReviewIssue, verdict: str = "REPAIR") -> ReviewResult:
    return ReviewResult(schema_version="booktrans.structured-review.v1", unit_uid="unit-repair", verdict=verdict, issues=list(issues))


def test_reviewer_repair_without_indices_or_instruction_is_human_required() -> None:
    provider_request = _provider_request()
    missing = decide_approved_repair_contract(
        provider_request,
        _current(provider_request),
        review=_review(ReviewIssue(code="SENSE", affected_segment_indices=[], reason="vague", repair_instruction="fix it")),
    )
    assert missing.action == "HUMAN_REQUIRED"
    assert missing.contract is None
    no_instruction = decide_approved_repair_contract(
        provider_request,
        _current(provider_request),
        review=_review(ReviewIssue(code="SENSE", affected_segment_indices=[0], reason="vague", repair_instruction=None)),
    )
    assert no_instruction.action == "HUMAN_REQUIRED"


def test_protected_scope_cannot_enter_repair_contract() -> None:
    provider_request = _provider_request(protected=True)
    decision = decide_approved_repair_contract(
        provider_request,
        _current(provider_request),
        review=_review(ReviewIssue(code="SENSE", affected_segment_indices=[1], reason="touch url", repair_instruction="do not")),
    )
    assert decision.action == "HUMAN_REQUIRED"
    assert decision.reason == "REPAIR_SCOPE_AMBIGUOUS"


def test_explicit_semantic_narrow_gate_builds_contract() -> None:
    provider_request = _provider_request()
    decision = decide_approved_repair_contract(
        provider_request,
        _current(provider_request),
        review=_review(ReviewIssue(code="SENSE", affected_segment_indices=[0], reason="kickback", repair_instruction="Use 分成, not 回扣.")),
    )
    assert decision.action == "REPAIR"
    assert decision.policy_id == SEMANTIC_NARROW_POLICY
    assert decision.contract is not None
    assert decision.contract.permitted_segment_indices == [0]


def test_structural_empty_requires_current_review_scope_and_evidence() -> None:
    provider_request = _provider_request()
    review = _review(ReviewIssue(
        code="SEGMENT_MISALIGNMENT_DUPLICATION", affected_segment_indices=[1],
        reason="The final fragment is duplicated by segment 0.", repair_instruction="Remove the duplicate fragment.",
    ))
    authorization = IntentionalEmptyAuthorization(
        empty_index=1, carrier_segment_indices=[0], evidence_codes=["SEGMENT_MISALIGNMENT_DUPLICATION"],
    )
    decision = decide_approved_repair_contract(
        provider_request, _current(provider_request), review=review,
        intentional_empty_authorizations=[authorization],
    )
    assert decision.action == "REPAIR"
    assert decision.contract is not None
    assert decision.contract.intentional_empty_authorizations == [authorization]
    wrong = decide_approved_repair_contract(
        provider_request, _current(provider_request), review=review,
        intentional_empty_authorizations=[IntentionalEmptyAuthorization(
            empty_index=0, carrier_segment_indices=[1], evidence_codes=["SEGMENT_MISALIGNMENT_DUPLICATION"],
        )],
    )
    assert wrong.action == "HUMAN_REQUIRED"
    assert wrong.reason == "REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_SCOPE_INVALID"


def test_registry_number_repair_is_accepted_only_with_explicit_indices() -> None:
    provider_request = _provider_request()
    evidence = {
        "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED",
        "source_sha_valid": True,
        "raw_required": True,
        "raw_sha_valid": True,
        "sparse_local": True,
        "qa_codes": ["NUMBER_PRESERVATION"],
        "repair_contract": {"unit_uid": "unit-repair"},
    }
    missing_scope = decide_approved_repair_contract(
        provider_request, _current(provider_request), qa_codes=["NUMBER_PRESERVATION"], registry_evidence=evidence
    )
    assert missing_scope.action == "HUMAN_REQUIRED"
    allowed = decide_approved_repair_contract(
        provider_request,
        _current(provider_request),
        qa_codes=["NUMBER_PRESERVATION"],
        qa_segment_indices=[0],
        registry_evidence=evidence,
    )
    assert allowed.action == "REPAIR"
    assert allowed.policy_id == "NUMERIC_REPRESENTATION"


def test_reviewer_cannot_expand_registry_recovery_to_non_allowlisted_qa() -> None:
    provider_request = _provider_request()
    decision = decide_approved_repair_contract(
        provider_request,
        _current(provider_request),
        qa_codes=["URL_PRESERVATION"],
        review=_review(ReviewIssue(code="URL", affected_segment_indices=[0], reason="url", repair_instruction="rewrite all")),
        registry_evidence={
            "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED",
            "source_sha_valid": True,
            "raw_required": True,
            "raw_sha_valid": True,
            "sparse_local": True,
            "qa_codes": ["URL_PRESERVATION"],
            "repair_contract": {"unit_uid": "unit-repair"},
        },
    )
    assert decision.action == "HUMAN_REQUIRED"
    assert decision.reason == "QA_REPAIR_NOT_REGISTRY_APPROVED"
