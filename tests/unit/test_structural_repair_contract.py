"""Fail-closed regression coverage for harness-governed structural repair."""

from __future__ import annotations

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import (
    ApprovedRepairContract,
    IntentionalEmptyAuthorization,
    StructuredRepairResult,
    apply_narrow_repair,
)
from booktrans.providers.translation_provider import ProviderContractGap, TranslationProviderRequest


def _request(*, protected: bool = False) -> TranslationProviderRequest:
    segments = [
        CalibrationSourceSegment(kind="anchored", text="The full clause.", anchor_ref="a0", island_ref="i.1"),
        CalibrationSourceSegment(kind="anchored", text="Duplicate fragment.", anchor_ref="a1", island_ref="i.1"),
    ]
    if protected:
        segments[1] = CalibrationSourceSegment(kind="protected", text="https://example.test", anchor_ref="a1", island_ref="i.1")
    request = CalibrationRequest(
        source_sha256="a" * 64, unit_uid="structural-unit", display_id="body.p.00001", story="body",
        content_role="body_paragraph", source_text="The full clause. Duplicate fragment.", source_segments=segments,
    )
    return TranslationProviderRequest(request=request)


def _current(request: TranslationProviderRequest) -> TranslationResult:
    return TranslationResult(unit_uid=request.request.unit_uid, target_segments=[
        StructuredTargetSegment(text="完整语义已经由这一段承载。", kind="anchored", anchor_ref="a0", island_ref="i.1"),
        StructuredTargetSegment(text="重复片段。", kind=request.request.source_segments[1].kind, anchor_ref="a1", island_ref="i.1"),
    ])


def _contract(request: TranslationProviderRequest, *, authorization: IntentionalEmptyAuthorization | None = None) -> ApprovedRepairContract:
    return ApprovedRepairContract(
        policy_id="SEMANTIC_NARROW_SEGMENT", unit_uid=request.request.unit_uid,
        source_fingerprint=request.request_binding_sha256, permitted_segment_indices=[1],
        repair_instruction="Remove only an explicitly duplicated target fragment.",
        intentional_empty_authorizations=[] if authorization is None else [authorization],
    )


def _result(request: TranslationProviderRequest, patch: dict) -> StructuredRepairResult:
    return StructuredRepairResult.model_validate({
        "schema_version": "booktrans.structured-repair.v1", "unit_uid": request.request.unit_uid,
        "source_fingerprint": request.request_binding_sha256, "patches": [patch],
    })


def test_explicit_harness_authorized_intentional_empty_projects_only_text() -> None:
    request = _request()
    auth = IntentionalEmptyAuthorization(
        empty_index=1, carrier_segment_indices=[0], evidence_codes=["SEGMENT_MISALIGNMENT_DUPLICATION"],
    )
    repaired = apply_narrow_repair(
        request, _current(request), _contract(request, authorization=auth),
        _result(request, {"index": 1, "operation": "INTENTIONAL_EMPTY"}),
    )
    assert [segment.text for segment in repaired.target_segments] == ["完整语义已经由这一段承载。", ""]
    assert repaired.target_segments[1].anchor_ref == "a1"


@pytest.mark.parametrize("patch, code", [
    ({"index": 1, "target_text": ""}, "EMPTY_TARGET_TEXT"),
    ({"index": 1, "operation": "INTENTIONAL_EMPTY"}, "INTENTIONAL_EMPTY_UNAUTHORIZED"),
])
def test_empty_provider_output_remains_fail_closed(patch: dict, code: str) -> None:
    request = _request()
    with pytest.raises(ProviderContractGap, match=code):
        apply_narrow_repair(request, _current(request), _contract(request), _result(request, patch))


def test_omission_without_a_nonempty_authorized_carrier_is_rejected() -> None:
    request = _request()
    auth = IntentionalEmptyAuthorization(empty_index=1, carrier_segment_indices=[0], evidence_codes=["duplication"])
    current = _current(request).model_copy(update={"target_segments": [
        StructuredTargetSegment(text="", kind="anchored", anchor_ref="a0", island_ref="i.1"),
        _current(request).target_segments[1],
    ]})
    with pytest.raises(ProviderContractGap, match="INTENTIONAL_EMPTY_CARRIER_MISSING"):
        apply_narrow_repair(request, current, _contract(request, authorization=auth), _result(request, {"index": 1, "operation": "INTENTIONAL_EMPTY"}))


def test_wrong_scope_and_protected_content_are_rejected() -> None:
    request = _request()
    wrong = IntentionalEmptyAuthorization(empty_index=0, carrier_segment_indices=[1], evidence_codes=["duplication"])
    with pytest.raises(ProviderContractGap, match="INTENTIONAL_EMPTY_SCOPE_INVALID"):
        apply_narrow_repair(request, _current(request), _contract(request, authorization=wrong), _result(request, {"index": 1, "operation": "REPLACE", "target_text": "修复"}))
    protected = _request(protected=True)
    auth = IntentionalEmptyAuthorization(empty_index=1, carrier_segment_indices=[0], evidence_codes=["duplication"])
    with pytest.raises(ProviderContractGap, match="PROTECTED_SCOPE_FORBIDDEN"):
        apply_narrow_repair(protected, _current(protected), _contract(protected, authorization=auth), _result(protected, {"index": 1, "operation": "INTENTIONAL_EMPTY"}))


def test_replay_is_idempotent_and_keeps_machine_identity() -> None:
    request = _request()
    auth = IntentionalEmptyAuthorization(empty_index=1, carrier_segment_indices=[0], evidence_codes=["semantic_redundancy"])
    contract = _contract(request, authorization=auth)
    repair = _result(request, {"index": 1, "operation": "INTENTIONAL_EMPTY"})
    once = apply_narrow_repair(request, _current(request), contract, repair)
    twice = apply_narrow_repair(request, once, contract, repair)
    assert twice == once
    assert twice.target_segments[1].anchor_ref == "a1"
