import json

import pytest
from pydantic import ValidationError

from booktrans.domain.calibration import ExplicitNumericRepresentationEquivalence, TranslationResult
from booktrans.domain.models import SourceSnapshot
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.harness.calibration import CalibrationImporter
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.source_integrity import fingerprint
from booktrans.ooxml.calibration import select_calibration_requests
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.translation_view import TaggedCapabilityAudit, TranslationViewBuilder


def _prepared(path):
    source = fingerprint(path)
    document = DocumentExtractor().extract(path, SourceSnapshot(path=str(path), sha256=source.sha256, size_bytes=source.size_bytes, mtime_utc=source.mtime_utc))[0]
    views = TranslationViewBuilder().build(path, document)
    capability = TaggedCapabilityAudit().audit(path, document, views)
    return source, document, views, capability


def test_calibration_selection_is_deterministic_and_request_is_sanitized(tagged_docx):
    source, document, views, capability = _prepared(tagged_docx)
    first = select_calibration_requests(document, views, capability, sample_size=8)
    second = select_calibration_requests(document, views, capability, sample_size=8)
    assert [item.unit_uid for item in first] == [item.unit_uid for item in second]
    assert all(item.unit_uid in {record.unit_uid for record in capability.records if record.status == "TAGGED_SUPPORTED"} for item in first)
    serialized = first[0].model_dump_json()
    assert "locator" not in serialized and "spacing" not in serialized and "w:w" not in serialized


def test_response_schema_is_strict_and_unknown_unit_is_rejected(tagged_docx, tmp_path):
    source, document, views, capability = _prepared(tagged_docx)
    request = select_calibration_requests(document, views, capability, sample_size=3)
    request_path = tmp_path / "request.jsonl"
    request_path.write_text("\n".join(item.model_dump_json() for item in request) + "\n", encoding="utf-8")
    response_path = tmp_path / "response.jsonl"
    response_path.write_text(json.dumps({"unit_uid": "unknown", "target_segments": [{"text": "中文", "kind": "plain"}]}) + "\n", encoding="utf-8")
    result = CalibrationImporter().run(tagged_docx, request_path, response_path, tmp_path / "candidate.docx")
    assert result.candidate_document is None
    assert "UNKNOWN_UNIT_ID" in {diagnostic.code for qa in result.qa_results for diagnostic in qa.diagnostics}
    with pytest.raises(ValidationError):
        TranslationResult.model_validate({"unit_uid": "x", "target_segments": [], "unexpected": True})


def test_valid_chinese_response_patches_only_qa_pass_unit(tagged_docx, tmp_path):
    source, document, views, capability = _prepared(tagged_docx)
    view = next(item for item in views if item.source_text == "Only under these circumstances can it work.")
    request = next(item for item in select_calibration_requests(document, views, capability, sample_size=50) if item.unit_uid == view.unit_uid)
    request_path = tmp_path / "request.jsonl"
    request_path.write_text(request.model_dump_json() + "\n", encoding="utf-8")
    bold = next(anchor for anchor in view.anchors if "bold" in anchor.kinds)
    response = TranslationResult(unit_uid=view.unit_uid, target_segments=[
        StructuredTargetSegment(text="只有", kind="plain"),
        StructuredTargetSegment(text="在这些情况下", kind="anchored", anchor_ref=bold.anchor_id),
        StructuredTargetSegment(text="才能生效。", kind="plain"),
    ])
    response_path = tmp_path / "response.jsonl"
    response_path.write_text(response.model_dump_json() + "\n", encoding="utf-8")
    result = CalibrationImporter().run(tagged_docx, request_path, response_path, tmp_path / "candidate.docx")
    assert result.approved_count == 1 and result.validation_report and result.validation_report.passed
    assert result.candidate_document is not None
    assert any(block.source_plain_text == "只有在这些情况下才能生效。" for block in result.candidate_document.blocks)


def test_translator_notes_require_review_and_remain_unpatched(tagged_docx, tmp_path):
    _, document, views, capability = _prepared(tagged_docx)
    good_view = next(item for item in views if item.source_text == "Only under these circumstances can it work.")
    held_view = next(item for item in views if item.source_text == "Visit https://example.com/protected")
    requests = [
        item for item in select_calibration_requests(document, views, capability, sample_size=50)
        if item.unit_uid in {good_view.unit_uid, held_view.unit_uid}
    ]
    request_path = tmp_path / "request.jsonl"
    request_path.write_text("\n".join(item.model_dump_json() for item in requests) + "\n", encoding="utf-8")
    bold = next(anchor for anchor in good_view.anchors if "bold" in anchor.kinds)
    responses = [
        TranslationResult(unit_uid=good_view.unit_uid, target_segments=[
            StructuredTargetSegment(text="只有", kind="plain"),
            StructuredTargetSegment(text="在这些情况下", kind="anchored", anchor_ref=bold.anchor_id),
            StructuredTargetSegment(text="才能生效。", kind="plain"),
        ]),
        TranslationResult(
            unit_uid=held_view.unit_uid,
            target_segments=[StructuredTargetSegment(text="请人工审核", kind="plain")],
            translator_notes="Protected URL context requires an explicit human decision.",
        ),
    ]
    response_path = tmp_path / "response.jsonl"
    response_path.write_text("\n".join(item.model_dump_json() for item in responses) + "\n", encoding="utf-8")
    result = CalibrationImporter().run(tagged_docx, request_path, response_path, tmp_path / "candidate.docx")
    statuses = {item.unit_uid: item for item in result.response_statuses}
    assert result.approved_count == 1
    assert statuses[good_view.unit_uid].status == "PASS"
    assert statuses[held_view.unit_uid].status == "REVIEW_REQUIRED"
    assert result.review_required == [statuses[held_view.unit_uid]]
    assert all(item.unit_uid != held_view.unit_uid for item in result.qa_results)
    assert any(block.source_plain_text == held_view.source_text for block in result.candidate_document.blocks)


def test_deterministic_fail_is_not_patched_when_other_response_passes(tagged_docx, tmp_path):
    _, document, views, capability = _prepared(tagged_docx)
    good_view = next(item for item in views if item.source_text == "Only under these circumstances can it work.")
    bad_view = next(item for item in views if item.source_text == "Visit https://example.com/protected")
    requests = [
        item for item in select_calibration_requests(document, views, capability, sample_size=50)
        if item.unit_uid in {good_view.unit_uid, bad_view.unit_uid}
    ]
    request_path = tmp_path / "request.jsonl"
    request_path.write_text("\n".join(item.model_dump_json() for item in requests) + "\n", encoding="utf-8")
    bold = next(anchor for anchor in good_view.anchors if "bold" in anchor.kinds)
    protected = next(anchor for anchor in bad_view.anchors if anchor.protected)
    responses = [
        TranslationResult(unit_uid=good_view.unit_uid, target_segments=[
            StructuredTargetSegment(text="只有", kind="plain"),
            StructuredTargetSegment(text="在这些情况下", kind="anchored", anchor_ref=bold.anchor_id),
            StructuredTargetSegment(text="才能生效。", kind="plain"),
        ]),
        TranslationResult(unit_uid=bad_view.unit_uid, target_segments=[
            StructuredTargetSegment(text="访问", kind="plain"),
            StructuredTargetSegment(text="已删除", kind="protected", anchor_ref=protected.anchor_id),
        ]),
    ]
    response_path = tmp_path / "response.jsonl"
    response_path.write_text("\n".join(item.model_dump_json() for item in responses) + "\n", encoding="utf-8")
    result = CalibrationImporter().run(tagged_docx, request_path, response_path, tmp_path / "candidate.docx")
    statuses = {item.unit_uid: item for item in result.response_statuses}
    assert statuses[good_view.unit_uid].status == "PASS"
    assert statuses[bad_view.unit_uid].status == "FAIL"
    assert result.approved_count == 1 and result.rejected_count == 1
    assert result.validation_report and result.validation_report.passed
    assert any(block.source_plain_text == bad_view.source_text for block in result.candidate_document.blocks)


def test_closed_world_rejects_missing_anchor_protected_mutation_and_number_loss(tagged_docx, tmp_path):
    source, document, views, capability = _prepared(tagged_docx)
    view = next(item for item in views if item.source_text == "Visit https://example.com/protected")
    request = next(item for item in select_calibration_requests(document, views, capability, sample_size=50) if item.unit_uid == view.unit_uid)
    request_path = tmp_path / "request.jsonl"
    request_path.write_text(request.model_dump_json() + "\n", encoding="utf-8")
    protected = next(anchor for anchor in view.anchors if anchor.protected)
    bad = TranslationResult(unit_uid=view.unit_uid, target_segments=[
        StructuredTargetSegment(text="访问", kind="plain"),
        StructuredTargetSegment(text="删除", kind="protected", anchor_ref=protected.anchor_id),
    ])
    response_path = tmp_path / "response.jsonl"
    response_path.write_text(bad.model_dump_json() + "\n", encoding="utf-8")
    result = CalibrationImporter().run(tagged_docx, request_path, response_path, tmp_path / "candidate.docx")
    codes = {diagnostic.code for qa in result.qa_results for diagnostic in qa.diagnostics}
    assert result.candidate_document is None
    assert "PROTECTED_TEXT" in codes


def test_numeric_semantic_value_is_required_and_continuation_is_input_only(format_audit_docx):
    qa = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text="In 1987, usage reached 37%.", target_text="1989年，使用率达到37%。"),
        known_unit_ids={"known"},
    )
    assert "NUMBER_PRESERVATION" in {diagnostic.code for diagnostic in qa.diagnostics}
    _, document, views, capability = _prepared(format_audit_docx)
    continuing = next(view for view in views if view.source_text == "broken,")
    request = next(item for item in select_calibration_requests(document, views, capability, sample_size=50) if item.unit_uid == continuing.unit_uid)
    assert request.continuation_hint and request.previous_text and request.next_text
    assert request.previous_text not in request.source_text and request.next_text not in request.source_text


def test_number_preservation_normalizes_grouped_thousands_literals():
    qa = DeterministicCandidateQA()
    equivalent = qa.validate(
        SyntheticCandidate(
            unit_uid="known", display_id="fixture",
            source_text="Split 1,000 documents into 1,000 chunks.",
            target_text="将1000份文档分为1000个分块。",
        ),
        known_unit_ids={"known"},
    )
    changed = qa.validate(
        SyntheticCandidate(
            unit_uid="known", display_id="fixture",
            source_text="Split 1,000 documents into 1,000 chunks.",
            target_text="将1001份文档分为1000个分块。",
        ),
        known_unit_ids={"known"},
    )
    assert equivalent.passed
    assert "NUMBER_PRESERVATION" in {diagnostic.code for diagnostic in changed.diagnostics}


def test_number_preservation_accepts_grouped_target_literal_after_normalization():
    qa = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid="known", display_id="fixture",
            source_text="The worker started 8000 processes.",
            target_text="工作进程启动了 8,000 个进程。",
        ),
        known_unit_ids={"known"},
    )
    assert qa.passed


@pytest.mark.parametrize(
    ("source", "target", "value", "passed"),
    [
        ("a thousand", "1,000", "1000", True),
        ("one thousand", "1000", "1000", True),
        ("a thousand", "1,001", "1000", False),
        ("a thousand", "10,000", "1000", False),
        ("thousands of", "2,000", "2000", False),
        ("roughly a thousand", "1,000", "1000", False),
    ],
)
def test_d051_english_cardinal_to_literal_is_narrow(source, target, value, passed):
    equivalence = ExplicitNumericRepresentationEquivalence(
        unit_uid="known", exact_source_span=source,
        approved_target_representation=target,
        reviewed_source_semantic_numeric_value=value,
        reviewed_target_semantic_numeric_value=value,
        equivalence_kind="ENGLISH_CARDINAL_TO_LITERAL",
    )
    result = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text=source, target_text=target),
        known_unit_ids={"known"}, explicit_numeric_representation_equivalences=[equivalence],
    )
    assert result.passed is passed


def test_d051_english_cardinal_rejects_additional_target_number():
    source = "a thousand"
    target = "1,000 台虚拟机和 8 个无关任务"
    equivalence = ExplicitNumericRepresentationEquivalence(
        unit_uid="known", exact_source_span=source,
        approved_target_representation="1,000",
        reviewed_source_semantic_numeric_value="1000",
        reviewed_target_semantic_numeric_value="1000",
        equivalence_kind="ENGLISH_CARDINAL_TO_LITERAL",
    )
    result = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text=source, target_text=target),
        known_unit_ids={"known"}, explicit_numeric_representation_equivalences=[equivalence],
    )
    assert not result.passed
    assert "NUMBER_PRESERVATION" in {item.code for item in result.diagnostics}


def test_number_preservation_keeps_range_endpoint_after_ellipsis():
    qa = DeterministicCandidateQA().validate(
        SyntheticCandidate(
            unit_uid="known", display_id="fixture",
            source_text="PIDs are allocated in the range 0...32,000.",
            target_text="PID 在 0 到 32,000 的范围内分配。",
        ),
        known_unit_ids={"known"},
    )
    assert qa.passed


def test_url_preservation_normalizes_terminal_punctuation_without_truncating_url_content():
    qa = DeterministicCandidateQA()
    known = {"known"}
    trailing = qa.validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text="See http://example.com.", target_text="参见 http://example.com。"),
        known_unit_ids=known,
    )
    path_dot = qa.validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text="See https://example.com/a.b", target_text="参见 https://example.com/a.b"),
        known_unit_ids=known,
    )
    query_dot = qa.validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text="See https://example.com/?q=a.b", target_text="参见 https://example.com/?q=a.b"),
        known_unit_ids=known,
    )
    assert trailing.passed and path_dot.passed and query_dot.passed


def test_protected_url_is_authoritative_and_mutation_still_fails():
    qa = DeterministicCandidateQA()
    valid = qa.validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text="See http://example.com.", target_text="参见 http://example.com。"),
        known_unit_ids={"known"}, protected_text=["http://example.com"],
    )
    changed = qa.validate(
        SyntheticCandidate(unit_uid="known", display_id="fixture", source_text="See http://example.com.", target_text="参见 http://example.org。"),
        known_unit_ids={"known"}, protected_text=["http://example.com"],
    )
    assert valid.passed
    assert "PROTECTED_TEXT" in {diagnostic.code for diagnostic in changed.diagnostics}


def test_protected_url_followed_by_chinese_closing_punctuation_is_not_mutated():
    """The terminal Chinese punctuation belongs to the following anchor, not the URL."""
    qa = DeterministicCandidateQA()
    result = qa.validate(
        SyntheticCandidate(
            unit_uid="known",
            display_id="fixture",
            source_text="(see https://talks.golang.org/2016/refactor.article).",
            target_text="（参见 https://talks.golang.org/2016/refactor.article）。",
        ),
        known_unit_ids={"known"},
        protected_text=["https://talks.golang.org/2016/refactor.article"],
    )
    assert result.passed


def test_normal_prose_request_has_neighbor_context_but_response_rejects_context_fields(format_audit_docx):
    _, document, views, capability = _prepared(format_audit_docx)
    normal = next(view for view in views if view.source_text == "Hello world")
    request = next(item for item in select_calibration_requests(document, views, capability, sample_size=50) if item.unit_uid == normal.unit_uid)
    assert request.continuation_hint is False
    assert request.previous_text and request.next_text
    with pytest.raises(ValidationError):
        TranslationResult.model_validate({
            "unit_uid": normal.unit_uid,
            "target_segments": [{"text": "你好世界", "kind": "plain"}],
            "previous_text": "must remain input-only",
        })
