from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.synthetic_roundtrip import SyntheticRoundTrip
from booktrans.ooxml.capability import NonIdentityCapabilityAudit
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.locator import resolve_locator
from booktrans.ooxml.patcher import write_text_at_locator
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.production_worker.deterministic_recovery import correct_numeric_anchor_duplication
from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult


def _document(docx):
    snapshot = fingerprint(docx)
    from booktrans.domain.models import SourceSnapshot
    return DocumentExtractor().extract(docx, SourceSnapshot(
        path=str(docx), sha256=snapshot.sha256, size_bytes=snapshot.size_bytes, mtime_utc=snapshot.mtime_utc,
    ))[0]


def _codes(result):
    return {diagnostic.code for diagnostic in result.diagnostics}


def test_capability_audit_is_conservative_for_hyperlinks_and_multi_node_alternate(m2_docx):
    document = _document(m2_docx)
    report = NonIdentityCapabilityAudit().audit(m2_docx, document)
    hyperlink = next(record for record in report.records if record.display_id.startswith("body") and "HYPERLINK_BOUNDARY" in record.reasons)
    assert hyperlink.status == "COMPLEX_UNSUPPORTED"
    multi = next(record for record in report.records if any("ALTERNATE_MULTI_NODE" == reason for reason in record.reasons))
    assert multi.status == "COMPLEX_UNSUPPORTED"
    assert report.reason_counts["HYPERLINK_BOUNDARY"] >= 1
    assert report.reason_counts["ALTERNATE_MULTI_NODE"] >= 1


def test_deterministic_qa_blocks_empty_number_url_and_protected_loss():
    qa = DeterministicCandidateQA()
    known = {"unit"}
    assert "EMPTY_TARGET" in _codes(qa.validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="Hello", target_text=""), known_unit_ids=known))
    number = qa.validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="In 1987, usage reached 37%.", target_text="1989年，使用率达到37%。"), known_unit_ids=known)
    assert "NUMBER_PRESERVATION" in _codes(number)
    url = qa.validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="See https://example.com", target_text="请参阅网站"), known_unit_ids=known)
    assert "URL_PRESERVATION" in _codes(url)
    protected = qa.validate(SyntheticCandidate(unit_uid="unit", display_id="x", source_text="Reference", target_text="译文"), known_unit_ids=known, protected_text=["TOKEN-42"])
    assert "PROTECTED_TEXT" in _codes(protected)


def test_numeric_anchor_ownership_rejects_only_unowned_duplicate():
    qa = DeterministicCandidateQA()
    source = [
        StructuredTargetSegment(text="buildfiles, ", kind="anchored", anchor_ref="text", island_ref="island.001"),
        StructuredTargetSegment(text="376", kind="anchored", anchor_ref="number", island_ref="island.001"),
    ]
    clean = [
        StructuredTargetSegment(text="构建文件，", kind="anchored", anchor_ref="text", island_ref="island.001"),
        StructuredTargetSegment(text="376", kind="anchored", anchor_ref="number", island_ref="island.001"),
    ]
    duplicated = [
        clean[0].model_copy(update={"text": "构建文件，376"}), clean[1],
    ]
    candidate = SyntheticCandidate(unit_uid="unit", display_id="x", source_text="buildfiles, 376", target_text="构建文件，376376")
    assert "NUMERIC_ANCHOR_DUPLICATION" in _codes(qa.validate(candidate, known_unit_ids={"unit"}, source_segments=source, target_segments=duplicated))
    candidate = candidate.model_copy(update={"target_text": "构建文件，376"})
    assert qa.validate(candidate, known_unit_ids={"unit"}, source_segments=source, target_segments=clean).passed


def test_numeric_anchor_ownership_allows_two_distinct_source_owners():
    qa = DeterministicCandidateQA()
    source = [
        StructuredTargetSegment(text="376", kind="anchored", anchor_ref="one", island_ref="island.001"),
        StructuredTargetSegment(text=", ", kind="anchored", anchor_ref="separator", island_ref="island.001"),
        StructuredTargetSegment(text="376", kind="anchored", anchor_ref="two", island_ref="island.001"),
    ]
    target = [item.model_copy() for item in source]
    candidate = SyntheticCandidate(unit_uid="unit", display_id="x", source_text="376, 376", target_text="376, 376")
    assert qa.validate(candidate, known_unit_ids={"unit"}, source_segments=source, target_segments=target).passed


def test_numeric_anchor_ownership_correction_never_removes_legal_owner():
    request = CalibrationRequest(
        unit_uid="unit", display_id="x", story="body", content_role="body_paragraph", source_sha256="a" * 64, source_text="buildfiles, 376",
        source_segments=[
            CalibrationSourceSegment(text="buildfiles, ", kind="anchored", anchor_ref="text", island_ref="island.001"),
            CalibrationSourceSegment(text="376", kind="anchored", anchor_ref="number", island_ref="island.001"),
        ],
    )
    broken = TranslationResult(unit_uid="unit", target_segments=[
        StructuredTargetSegment(text="构建文件，376", kind="anchored", anchor_ref="text", island_ref="island.001"),
        StructuredTargetSegment(text="376", kind="anchored", anchor_ref="number", island_ref="island.001"),
    ])
    repaired = correct_numeric_anchor_duplication(request, broken)
    assert repaired is not None
    assert [item.text for item in repaired.target_segments] == ["构建文件，", "376"]
    owner_only = broken.model_copy(update={"target_segments": [broken.target_segments[0].model_copy(update={"text": "构建文件，"}), broken.target_segments[1]]})
    assert correct_numeric_anchor_duplication(request, owner_only) is None


def _range_duplicate_request(*, source_slots=None):
    return CalibrationRequest(
        unit_uid="range-unit", display_id="range", story="body", content_role="body_paragraph",
        source_sha256="a" * 64, source_text="deprecation tooling, 321-322",
        source_segments=source_slots or [
            CalibrationSourceSegment(text="deprecation tooling, ", kind="anchored", anchor_ref="text", island_ref="island.001"),
            CalibrationSourceSegment(text="321", kind="anchored", anchor_ref="left", island_ref="island.001"),
            CalibrationSourceSegment(text="-", kind="anchored", anchor_ref="dash", island_ref="island.001"),
            CalibrationSourceSegment(text="322", kind="anchored", anchor_ref="right", island_ref="island.001"),
        ],
    )


def _range_duplicate_candidate(text="弃用工具，321-322"):
    return TranslationResult(unit_uid="range-unit", target_segments=[
        StructuredTargetSegment(text=text, kind="anchored", anchor_ref="text", island_ref="island.001"),
        StructuredTargetSegment(text="321", kind="anchored", anchor_ref="left", island_ref="island.001"),
        StructuredTargetSegment(text="-", kind="anchored", anchor_ref="dash", island_ref="island.001"),
        StructuredTargetSegment(text="322", kind="anchored", anchor_ref="right", island_ref="island.001"),
    ])


def test_contiguous_multi_token_numeric_anchor_duplicate_is_removed_and_core_qa_passes():
    request = _range_duplicate_request()
    repaired = correct_numeric_anchor_duplication(request, _range_duplicate_candidate())
    assert repaired is not None
    assert [item.text for item in repaired.target_segments] == ["弃用工具，", "321", "-", "322"]
    qa = DeterministicCandidateQA().validate(
        SyntheticCandidate(unit_uid="range-unit", display_id="range", source_text=request.source_text, target_text="弃用工具，321-322"),
        known_unit_ids={"range-unit"}, source_segments=request.source_segments, target_segments=repaired.target_segments,
    )
    assert qa.passed


def test_contiguous_multi_token_numeric_anchor_duplicate_requires_unique_owners():
    request = _range_duplicate_request(source_slots=[
        CalibrationSourceSegment(text="deprecation tooling, ", kind="anchored", anchor_ref="text", island_ref="island.001"),
        CalibrationSourceSegment(text="321", kind="anchored", anchor_ref="left", island_ref="island.001"),
        CalibrationSourceSegment(text="-", kind="anchored", anchor_ref="dash", island_ref="island.001"),
        CalibrationSourceSegment(text="321", kind="anchored", anchor_ref="right", island_ref="island.001"),
    ])
    assert correct_numeric_anchor_duplication(request, _range_duplicate_candidate("弃用工具，321-321")) is None


def test_contiguous_multi_token_numeric_anchor_duplicate_rejects_non_contiguous_or_unrelated_numbers():
    request = _range_duplicate_request()
    assert correct_numeric_anchor_duplication(request, _range_duplicate_candidate("弃用工具，321-x-322")) is None
    assert correct_numeric_anchor_duplication(request, _range_duplicate_candidate("弃用工具，321-322-999")) is None


def test_contiguous_multi_token_numeric_anchor_duplicate_is_replay_idempotent():
    request = _range_duplicate_request()
    repaired = correct_numeric_anchor_duplication(request, _range_duplicate_candidate())
    assert repaired is not None
    assert correct_numeric_anchor_duplication(request, repaired) is None


def test_nonidentity_writer_supports_short_unicode_punctuation_target(identity_docx, tmp_path):
    document = _document(identity_docx)
    block = next(block for block in document.blocks if block.source_plain_text == "Identity heading")
    span = next(span for span in block.spans if span.text)
    candidate = tmp_path / "short.docx"
    report = write_text_at_locator(identity_docx, candidate, span.locator, "短。")
    assert report.writes_attempted == report.writes_verified == 1
    assert resolve_locator(candidate, span.locator).text == "短。"


def test_synthetic_roundtrip_patches_only_safe_blocks_and_preserves_complex_blocks(identity_docx, tmp_path):
    before = fingerprint(identity_docx)
    result = SyntheticRoundTrip().run(identity_docx, tmp_path / "candidate" / "synthetic.docx")
    assert fingerprint(identity_docx).sha256 == before.sha256
    assert result.capability.safe_simple_blocks > 0
    assert result.capability.complex_unsupported_blocks > 0
    assert all(result_item.passed for result_item in result.qa_results)
    assert result.validation_report.passed
    candidate_by_locator = {block.locator.xml_path: block for block in result.candidate_document.blocks}
    source_by_uid = {block.unit_uid: block for block in result.source_document.blocks}
    approved = {candidate.unit_uid: candidate for candidate in result.candidates if candidate.status == "approved"}
    assert approved
    for unit_uid, candidate in approved.items():
        assert candidate_by_locator[source_by_uid[unit_uid].locator.xml_path].source_plain_text == candidate.target_text
    hyperlink_source = next(block for block in result.source_document.blocks if "Identity hyperlink" in block.source_plain_text)
    assert candidate_by_locator[hyperlink_source.locator.xml_path].source_plain_text == hyperlink_source.source_plain_text
    protected = next(block for block in result.source_document.blocks if block.source_plain_text == "1")
    assert candidate_by_locator[protected.locator.xml_path].source_plain_text == "1"
    assert result.validation_report.expected_block_mismatch_count == 0
    assert result.candidate_coverage.coverage_complete is True
    assert result.validation_report.preservation.media_hash_mismatches == []
    assert result.validation_report.preservation.relationship_payload_mismatches == []
