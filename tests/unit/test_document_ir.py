from booktrans.domain.enums import CoverageClassification
from booktrans.harness.coverage import build_coverage_ledger
from booktrans.harness.source_integrity import fingerprint
from booktrans.ooxml.extractor import DocumentExtractor
from booktrans.ooxml.locator import resolve_locator


def extract(docx):
    snapshot = fingerprint(docx)
    source = {
        "path": str(docx), "sha256": snapshot.sha256, "size_bytes": snapshot.size_bytes,
        "mtime_utc": snapshot.mtime_utc,
    }
    from booktrans.domain.models import SourceSnapshot
    return DocumentExtractor().extract(docx, SourceSnapshot(**source))


def text_blocks(document):
    return {block.source_plain_text: block for block in document.blocks if block.source_plain_text}


def test_repeat_extraction_has_stable_units_and_locators(m2_docx):
    first, _, _ = extract(m2_docx)
    second, _, _ = extract(m2_docx)
    assert [(block.unit_uid, block.display_id, block.locator.xml_path) for block in first.blocks] == [
        (block.unit_uid, block.display_id, block.locator.xml_path) for block in second.blocks
    ]


def test_paragraph_heading_table_hyperlink_and_stories_enter_ir(m2_docx):
    document, _, _ = extract(m2_docx)
    blocks = text_blocks(document)
    assert blocks["M2 heading"].content_role == "heading"
    assert blocks["Cell text"].block_type == "table_cell_paragraph"
    link = blocks["Linked visible link"]
    link_span = next(span for span in link.spans if span.text == "visible link")
    assert link_span.container_context["hyperlink_target"] == "https://example.com/linked"
    assert "Header text" in blocks
    assert "Footer text" in blocks
    assert "Footnote text" in blocks
    assert "Endnote text" in blocks


def test_textboxes_and_content_controls_are_not_black_boxes(m2_docx):
    document, _, _ = extract(m2_docx)
    blocks = text_blocks(document)
    assert blocks["Drawing textbox text"].block_type == "textbox_paragraph"
    assert blocks["VML textbox text"].block_type == "textbox_paragraph"
    controlled = blocks["Controlled text"]
    assert any(span.container_context.get("content_control") == "true" for span in controlled.spans)
    assert sum(block.source_plain_text == "Drawing textbox text" for block in document.blocks) == 1
    assert sum(block.source_plain_text == "VML textbox text" for block in document.blocks) == 1


def test_fields_do_not_mix_instruction_with_displayed_result(m2_docx):
    document, uncertainty, _ = extract(m2_docx)
    field_block = text_blocks(document)["Field result"]
    assert "UNKNOWNFIELD" not in field_block.source_plain_text
    result = next(span for span in field_block.spans if span.text == "Field result")
    assert result.classification == CoverageClassification.PROTECTED
    assert any(span.kind == "field_boundary" for span in field_block.spans)
    ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
    assert ledger.coverage_complete is False
    assert ledger.unknown_potential_visible_content > 0
    known = text_blocks(document)["7"]
    known_result = next(span for span in known.spans if span.text == "7")
    assert known_result.classification == CoverageClassification.PROTECTED
    assert any(span.kind == "field_boundary" for span in known.spans)


def test_alternate_content_uses_fallback_without_double_counting(m2_docx):
    document, uncertainty, _ = extract(m2_docx)
    text = [block.source_plain_text for block in document.blocks]
    assert "Fallback text" in text
    assert "Choice text" not in text
    assert text.count("Same alternate text") == 1
    ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
    alternate = [entry for entry in ledger.entries if entry.code == "ALTERNATE_CONTENT_UNCERTAIN_EFFECTIVE_BRANCH"]
    assert len(alternate) == 1
    assert any(diagnostic.code == "ALTERNATE_CONTENT_EQUIVALENT_BRANCHES" for diagnostic in document.diagnostics)
    assert ledger.coverage_complete is False


def test_comments_are_explicitly_excluded_and_ir_round_trips(m2_docx):
    document, _, _ = extract(m2_docx)
    comment = text_blocks(document)["Comment text"]
    assert comment.story == "comments"
    assert all(span.classification == CoverageClassification.EXCLUDED_BY_POLICY for span in comment.spans if span.text)
    deleted = text_blocks(document)["Deleted text"]
    assert all(span.classification == CoverageClassification.EXCLUDED_BY_POLICY for span in deleted.spans if span.text)
    restored = type(document).model_validate_json(document.model_dump_json())
    assert [block.unit_uid for block in restored.blocks] == [block.unit_uid for block in document.blocks]


def test_unknown_shape_prevents_complete_coverage(unknown_shape_docx):
    document, uncertainty, _ = extract(unknown_shape_docx)
    ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
    assert any(entry.code == "UNSUPPORTED_DRAWING_SHAPE" for entry in ledger.entries)
    assert ledger.coverage_complete is False


def test_unclassified_visible_text_is_a_harness_blocker(rich_docx):
    document, uncertainty, _ = extract(rich_docx)
    span = next(span for block in document.blocks for span in block.spans if span.text)
    span.classification = CoverageClassification.UNCLASSIFIED
    ledger = build_coverage_ledger(document, uncertainty, source_integrity_pass=True)
    assert ledger.unclassified_visible_text == 1
    assert ledger.coverage_complete is False
    assert any(blocker.code == "UNCLASSIFIED_VISIBLE_TEXT" for blocker in ledger.blockers)


def test_extraction_does_not_mutate_source(m2_docx):
    before = fingerprint(m2_docx)
    extract(m2_docx)
    after = fingerprint(m2_docx)
    assert before.sha256 == after.sha256


def test_serialized_locator_resolves_against_original_ooxml(m2_docx):
    document, _, _ = extract(m2_docx)
    block = text_blocks(document)["Linked visible link"]
    span = next(span for span in block.spans if span.text == "visible link")
    assert "visible link" in "".join(resolve_locator(m2_docx, block.locator).xpath(".//w:t/text()", namespaces={"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}))
    assert resolve_locator(m2_docx, span.locator).text == "visible link"
