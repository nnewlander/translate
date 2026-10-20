import pytest

from booktrans.domain.composite_translation import (
    CompositeTranslationRequest,
    CompositeTranslationResult,
    CompositeValidationEnvelope,
    flat_target_eligibility,
)
from booktrans.harness.composite_contract import validate_flat_composite_with_ordinary_qa
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment


def _request(*, footnotes: list[str] | None = None, protected: list[str] | None = None) -> CompositeTranslationRequest:
    return CompositeTranslationRequest(envelope=CompositeValidationEnvelope(
        source_sha256="sha", group_id="g", member_unit_uids=["a", "b"],
        member_display_ids=["body.p.1", "footer.1.p.1"], source_text="Ben1 sees http://example.com.",
        logical_editable_island_count=1, semantic_anchor_kinds=["font_size"],
        protected_literals=protected or [], footnote_reference_numbers=footnotes or [],
        numeric_source_tokens=["1"], url_source_tokens=["http://example.com"],
        email_source_tokens=[], doi_source_tokens=[], isbn_source_tokens=[],
        flat_target_eligible=True, required_response_contract="FLAT_TEXT",
    ))


def test_flat_composite_reuses_ordinary_numeric_url_and_protected_qa() -> None:
    request = _request()
    bad = CompositeTranslationResult(group_id="g", target_text="Ben¹ 看见 http://example.com。")
    assert "UNEVIDENCED_FOOTNOTE_SUPERSCRIPT" in validate_flat_composite_with_ordinary_qa(bad, request, approved_group_ids={"g"})
    valid = CompositeTranslationResult(group_id="g", target_text="Ben1 看见 http://example.com。")
    assert validate_flat_composite_with_ordinary_qa(valid, request, approved_group_ids={"g"}) == []
    changed_number = CompositeTranslationResult(group_id="g", target_text="Ben2 看见 http://example.com。")
    assert "NUMBER_PRESERVATION" in validate_flat_composite_with_ordinary_qa(changed_number, request, approved_group_ids={"g"})
    protected = _request(protected=["http://example.com"])
    changed = CompositeTranslationResult(group_id="g", target_text="Ben1 看见 http://example.org。")
    assert "PROTECTED_TEXT" in validate_flat_composite_with_ordinary_qa(changed, protected, approved_group_ids={"g"})


def test_evidenced_superscript_can_reach_existing_normalizer_contract() -> None:
    request = _request(footnotes=["1"])
    response = CompositeTranslationResult(group_id="g", target_text="Ben¹ 看见 http://example.com。")
    # RAW is allowed to show its representation-only numeric mismatch, then
    # only the approved normalizer can turn the evidenced superscript into 1.
    assert "NUMBER_PRESERVATION" in validate_flat_composite_with_ordinary_qa(response, request, approved_group_ids={"g"})
    normalized, changes = normalize_footnote_superscripts(
        request.envelope.as_ordinary_request(),
        TranslationResult(unit_uid="g", target_segments=[StructuredTargetSegment(text=response.target_text, kind="plain")]),
    )
    assert changes and "Ben1" in normalized.target_segments[0].text


def test_flat_target_is_not_a_qa_shortcut() -> None:
    eligible, reasons = flat_target_eligibility(
        logical_editable_island_count=1, semantic_anchor_kinds={"italic"},
        protected_literals=[], structural_segmentation_required=False,
    )
    assert not eligible and reasons == ["COMPOSITE_FLAT_TARGET_MOVABLE_ANCHOR"]
    nonflat = CompositeTranslationRequest(envelope=CompositeValidationEnvelope(
        source_sha256="sha", group_id="g", member_unit_uids=["a"], member_display_ids=["body.p.1"],
        source_text="x", logical_editable_island_count=1, semantic_anchor_kinds=["italic"],
        protected_literals=[], footnote_reference_numbers=[], numeric_source_tokens=[], url_source_tokens=[],
        email_source_tokens=[], doi_source_tokens=[], isbn_source_tokens=[], flat_target_eligible=False,
        required_response_contract="STRUCTURED_SEGMENTS",
    ))
    assert validate_flat_composite_with_ordinary_qa(CompositeTranslationResult(group_id="g", target_text="中文"), nonflat, approved_group_ids={"g"}) == ["COMPOSITE_STRUCTURED_RESPONSE_REQUIRED"]


def test_closed_world_response_schema() -> None:
    with pytest.raises(Exception):
        CompositeTranslationResult(group_id="g", target_text="x", invented="no")
