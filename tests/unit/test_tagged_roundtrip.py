from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA, TaggedSyntheticRoundTrip
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.parts import read_xml_part
from booktrans.ooxml.tagged_projection import TaggedProjection, apply_tagged_projections
from booktrans.ooxml.translation_view import TaggedCapabilityAudit, TranslationViewBuilder


def _document(path: Path):
    snapshot = fingerprint(path)
    from booktrans.domain.models import SourceSnapshot
    return DocumentExtractor().extract(path, SourceSnapshot(
        path=str(path), sha256=snapshot.sha256, size_bytes=snapshot.size_bytes, mtime_utc=snapshot.mtime_utc,
    ))[0]


def _view_for(source: Path, text: str):
    document = _document(source)
    return next(view for view in TranslationViewBuilder().build(source, document) if view.source_text == text)


def _replace_xml(docx: Path, part: str, root) -> None:
    rebuilt = docx.with_suffix(".rebuilt.docx")
    with zipfile.ZipFile(docx, "r") as source, zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as destination:
        for info in source.infolist():
            payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True) if info.filename == part else source.read(info.filename)
            destination.writestr(info, payload)
    rebuilt.replace(docx)


def test_translation_view_exposes_semantic_anchors_not_spacing_or_scale(tagged_docx):
    view = _view_for(tagged_docx, "Only under these circumstances can it work.")
    assert [segment.kind for segment in view.segments] == ["plain", "anchored", "plain"]
    assert view.anchors[0].kinds == ["bold"]
    assert "spacing" not in view.anchors[0].kinds
    assert "character_scale" not in view.anchors[0].kinds
    hyperlink = _view_for(tagged_docx, "Read this article")
    assert hyperlink.anchors[0].kinds == ["hyperlink"]
    assert hyperlink.anchors[0].container_context["hyperlink_rid"]


def test_translation_view_uses_m46_fragment_evidence_without_grouping(format_audit_docx):
    document = _document(format_audit_docx)
    views = TranslationViewBuilder().build(format_audit_docx, document)
    continuing = next(view for view in views if view.source_text == "broken,")
    list_item = next(view for view in views if view.source_text == "item")
    assert continuing.continuation_hint is True
    assert continuing.previous_text == "Before fragment" and continuing.next_text == "continues after."
    assert list_item.continuation_hint is False


def test_tagged_projection_moves_bold_anchor_without_source_run_position(tagged_docx, tmp_path):
    before = fingerprint(tagged_docx)
    view = _view_for(tagged_docx, "Only under these circumstances can it work.")
    bold = view.anchors[0]
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=[
            StructuredTargetSegment(text="只有 ", kind="plain"),
            StructuredTargetSegment(text="在这些情况下", kind="anchored", anchor_ref=bold.anchor_id),
            StructuredTargetSegment(text=" 才能工作。", kind="plain"),
        ],
        target_text="只有 在这些情况下 才能工作。",
    )
    qa = FormatAnchorQA()
    assert qa.validate(view, candidate, {view.unit_uid}).passed
    output = tmp_path / "bold-moved.docx"
    patch = apply_tagged_projections(tagged_docx, output, [TaggedProjection(view, candidate)])
    assert patch.writes_attempted == patch.writes_verified == 3
    assert fingerprint(tagged_docx).sha256 == before.sha256
    assert qa.verify_projection(tagged_docx, output, view, candidate).passed
    with zipfile.ZipFile(output, "r") as package:
        root = read_xml_part(package, "word/document.xml")
    assert root.xpath(".//w:r[w:rPr/w:b]/w:t/text()", namespaces=NS) == ["在这些情况下"]


def test_tagged_projection_preserves_hyperlink_relationship_and_supports_short_unicode(tagged_docx, tmp_path):
    view = _view_for(tagged_docx, "Read this article")
    hyperlink = view.anchors[0]
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=[
            StructuredTargetSegment(text="阅读", kind="plain"),
            StructuredTargetSegment(text="这篇文章", kind="anchored", anchor_ref=hyperlink.anchor_id),
        ], target_text="阅读这篇文章",
    )
    qa = FormatAnchorQA()
    assert qa.validate(view, candidate, {view.unit_uid}).passed
    output = tmp_path / "hyperlink.docx"
    apply_tagged_projections(tagged_docx, output, [TaggedProjection(view, candidate)])
    assert qa.verify_projection(tagged_docx, output, view, candidate).passed
    with zipfile.ZipFile(output, "r") as package:
        root = read_xml_part(package, "word/document.xml")
    links = root.xpath(".//w:hyperlink", namespaces=NS)
    assert any(link.get(qn("r", "id")) == hyperlink.container_context["hyperlink_rid"] and "".join(link.xpath(".//w:t/text()", namespaces=NS)) == "这篇文章" for link in links)


def test_anchor_qa_rejects_missing_unknown_and_changed_protected_anchor(tagged_docx):
    document = _document(tagged_docx)
    views = TranslationViewBuilder().build(tagged_docx, document)
    protected_view = next(view for view in views if view.source_text == "Visit https://example.com/protected")
    protected = next(anchor for anchor in protected_view.anchors if anchor.protected)
    bad = TaggedSyntheticCandidate(
        unit_uid=protected_view.unit_uid, display_id=protected_view.display_id, source_text=protected_view.source_text,
        target_segments=[StructuredTargetSegment(text="访问", kind="plain"), StructuredTargetSegment(text="已删除", kind="protected", anchor_ref=protected.anchor_id)],
        target_text="访问已删除",
    )
    codes = {item.code for item in FormatAnchorQA().validate(protected_view, bad, {protected_view.unit_uid}).diagnostics}
    assert "PROTECTED_TEXT" in codes
    bold_view = next(view for view in views if view.source_text.startswith("Only under"))
    missing = TaggedSyntheticCandidate(unit_uid=bold_view.unit_uid, display_id=bold_view.display_id, source_text=bold_view.source_text, target_segments=[StructuredTargetSegment(text="短。", kind="plain")], target_text="短。")
    assert "MISSING_ANCHOR" in {item.code for item in FormatAnchorQA().validate(bold_view, missing, {bold_view.unit_uid}).diagnostics}


def test_tagged_roundtrip_reextracts_targets_and_rejects_field_context(tagged_docx, tmp_path):
    before = fingerprint(tagged_docx)
    result = TaggedSyntheticRoundTrip().run(tagged_docx, tmp_path / "candidate" / "tagged.docx")
    assert fingerprint(tagged_docx).sha256 == before.sha256
    assert result.capability.tagged_supported_blocks > 0
    assert all(candidate.source_text != "1" for candidate in result.candidates)
    assert any("STRUCTURAL_MIXED_TEXT_STRUCTURAL_RUN" in record.reasons for record in result.capability.records)
    assert all(candidate.source_text in candidate.target_text for candidate in result.candidates)
    assert all(item.passed for item in result.qa_results)
    assert result.patch_report.resolution_failures == []
    assert result.validation_report.passed
    assert result.candidate_coverage.coverage_complete is True
    assert any(block.source_plain_text == "1" for block in result.candidate_document.blocks)
    assert result.validation_report.preservation.media_hash_mismatches == []
    assert result.validation_report.preservation.relationship_payload_mismatches == []


def test_structural_islands_preserve_bookmark_tab_drawing_and_remove_cjk_micro_layout(tagged_docx, tmp_path):
    result = TaggedSyntheticRoundTrip().run(tagged_docx, tmp_path / "candidate" / "structural.docx")
    supported = {record.display_id for record in result.capability.records if record.status == "TAGGED_SUPPORTED"}
    unsupported = {record.display_id for record in result.capability.records if record.status == "TAGGED_UNSUPPORTED"}
    source_blocks = {block.display_id: block for block in result.source_document.blocks}
    assert source_blocks and result.validation_report.passed
    assert any(block.source_plain_text == "Before After" and block.display_id in supported for block in result.source_document.blocks)
    assert any(block.source_plain_text == "LeftRight" and block.display_id in supported for block in result.source_document.blocks)
    assert any(block.source_plain_text == "Caption  after image" and block.display_id in supported for block in result.source_document.blocks)
    assert any(block.source_plain_text == "Binary  image" and block.display_id in supported for block in result.source_document.blocks)
    assert any(block.source_plain_text == "Unsafe mixed" and block.display_id in unsupported for block in result.source_document.blocks)
    assert result.validation_report.structural_anchor_count >= 4
    assert result.validation_report.structural_anchor_qa_failure_count == 0
    with zipfile.ZipFile(result.candidate_path, "r") as package:
        root = read_xml_part(package, "word/document.xml")
    assert root.xpath("count(.//w:bookmarkStart[@w:id='77' and @w:name='keep-bookmark'])", namespaces=NS) == 1.0
    assert root.xpath("count(.//w:bookmarkEnd[@w:id='77'])", namespaces=NS) == 1.0
    assert len(root.xpath(".//w:p[w:r/w:t[contains(., '【测试】Left')]]/w:r/w:tab", namespaces=NS)) == 1
    assert root.xpath("count(.//w:drawing)", namespaces=NS) == 2.0
    cjk_runs = root.xpath(".//w:r[w:t[contains(., '【测试】')]]", namespaces=NS)
    assert cjk_runs
    assert all(not run.xpath("./w:rPr/w:spacing | ./w:rPr/w:w", namespaces=NS) for run in cjk_runs)
    protected_runs = root.xpath(".//w:r[w:t='https://example.com/protected']", namespaces=NS)
    assert len(protected_runs) == 1
    assert protected_runs[0].xpath("./w:rPr/w:spacing | ./w:rPr/w:w", namespaces=NS)
    assert result.validation_report.preservation.media_entries_checked == 1
    assert result.validation_report.preservation.media_hash_mismatches == []
    assert result.validation_report.preservation.relationship_payload_mismatches == []


def test_structural_anchor_removal_is_a_deterministic_failure(tagged_docx, tmp_path):
    view = _view_for(tagged_docx, "Before After")
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=[
            StructuredTargetSegment(text="前", kind="plain", island_ref="island.001"),
            StructuredTargetSegment(text="后", kind="plain", island_ref="island.002"),
        ], target_text="前后",
    )
    output = tmp_path / "bookmark.docx"
    apply_tagged_projections(tagged_docx, output, [TaggedProjection(view, candidate)])
    with zipfile.ZipFile(output, "r") as package:
        root = read_xml_part(package, "word/document.xml")
    bookmark = root.xpath(".//w:bookmarkStart[@w:id='77']", namespaces=NS)[0]
    bookmark.getparent().remove(bookmark)
    _replace_xml(output, "word/document.xml", root)
    codes = {diagnostic.code for diagnostic in FormatAnchorQA().verify_projection(tagged_docx, output, view, candidate).diagnostics}
    assert "STRUCTURAL_ANCHOR_SEQUENCE_CHANGED" in codes
