import zipfile

from booktrans.harness.identity_roundtrip import IdentityRoundTrip
from booktrans.harness.source_integrity import fingerprint
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.locator import resolve_locator
from booktrans.ooxml.patcher import write_text_at_locator


def _document(docx):
    snapshot = fingerprint(docx)
    from booktrans.domain.models import SourceSnapshot
    return DocumentExtractor().extract(docx, SourceSnapshot(
        path=str(docx), sha256=snapshot.sha256, size_bytes=snapshot.size_bytes, mtime_utc=snapshot.mtime_utc,
    ))[0]


def _block(document, text):
    return next(block for block in document.blocks if block.source_plain_text == text)


def test_writer_primitive_resolves_and_changes_text_without_breaking_hyperlink_structure(rich_docx, tmp_path):
    document = _document(rich_docx)
    span = next(span for span in _block(document, "Plain paragraph with a hyperlink").spans if span.text == "a hyperlink")
    candidate = tmp_path / "primitive.docx"
    report = write_text_at_locator(rich_docx, candidate, span.locator, "a test link")
    assert report.writes_attempted == report.writes_verified == 1
    assert resolve_locator(candidate, span.locator).text == "a test link"
    with zipfile.ZipFile(candidate) as package:
        xml = package.read("word/document.xml")
        assert b"w:hyperlink" in xml


def test_equivalent_alternate_content_writer_mirrors_physical_branches(m2_docx, tmp_path):
    document = _document(m2_docx)
    span = next(span for span in _block(document, "Same alternate text").spans if span.kind == "text")
    candidate = tmp_path / "alternate.docx"
    report = write_text_at_locator(m2_docx, candidate, span.locator, "\u4f60\u597d\u6d4b\u8bd5")
    assert report.writes_attempted == 2
    assert report.mirrored_alternate_nodes == 1
    with zipfile.ZipFile(candidate) as package:
        text = package.read("word/document.xml").decode("utf-8")
    assert text.count("\u4f60\u597d\u6d4b\u8bd5") == 2


def test_identity_roundtrip_uses_real_writes_and_preserves_source(identity_docx, tmp_path):
    before = fingerprint(identity_docx)
    result = IdentityRoundTrip().run(identity_docx, tmp_path / "candidate" / "identity.docx")
    after = fingerprint(identity_docx)
    assert before.sha256 == after.sha256
    assert result.patch_report.planned_text_nodes > 0
    assert result.patch_report.writes_attempted > 0
    assert result.patch_report.writes_attempted == result.patch_report.writes_verified
    assert not result.patch_report.resolution_failures
    assert result.roundtrip_report.passed
    assert result.source_coverage.coverage_complete is True
    assert result.candidate_coverage.coverage_complete is True


def test_identity_roundtrip_handles_table_stories_textboxes_hyperlink_and_protected_field(identity_docx, tmp_path):
    result = IdentityRoundTrip().run(identity_docx, tmp_path / "candidate" / "identity.docx")
    candidate = result.candidate_document
    for text in (
        "Identity cell", "Identity header", "Identity footer", "Identity drawing textbox", "Identity VML textbox",
        "Identity hyperlink",
    ):
        assert any(block.source_plain_text == text or text in block.source_plain_text for block in candidate.blocks)
    assert result.patch_report.skipped_protected > 0
    with zipfile.ZipFile(result.candidate_path) as package:
        assert b"PAGE" in package.read("word/document.xml")
    assert result.roundtrip_report.preservation.relationship_entries_checked > 0
    assert not result.roundtrip_report.preservation.relationship_payload_mismatches
    assert result.roundtrip_report.preservation.media_entries_checked == 1
    assert not result.roundtrip_report.preservation.media_hash_mismatches


def test_identity_roundtrip_candidate_logically_equals_source(identity_docx, tmp_path):
    result = IdentityRoundTrip().run(identity_docx, tmp_path / "candidate" / "identity.docx")
    report = result.roundtrip_report
    assert report.logical_mismatch_count == 0
    assert report.preservation.touched_xml_structure_mismatches == []
    assert report.preservation.unchanged_payload_mismatches == []
    assert report.passed
