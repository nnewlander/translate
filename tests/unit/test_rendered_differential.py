from __future__ import annotations

from zipfile import ZipFile

from booktrans.ooxml.rendered_differential import compare_docx_nontext_structure, normalized_rendered_text


DOC = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path, *, text: str, paragraph_properties: str = "", run_properties: str = "", section_type: str = "") -> None:
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="{DOC}"><w:body><w:p><w:pPr>{paragraph_properties}</w:pPr><w:r><w:rPr>{run_properties}</w:rPr><w:t>{text}</w:t></w:r></w:p>
    <w:sectPr>{section_type}</w:sectPr></w:body></w:document>'''.encode()
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
    rels = b'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def test_nontext_structure_allows_only_text_mutation(tmp_path) -> None:
    source = tmp_path / "source.docx"
    candidate = tmp_path / "candidate.docx"
    _docx(source, text="source", paragraph_properties='<w:keepNext/>')
    _docx(candidate, text="target", paragraph_properties='<w:keepNext/>')
    result = compare_docx_nontext_structure(source, candidate)
    assert result["nontext_structure_equal"] is True
    assert result["paragraph_property_changed_indices"] == []
    assert result["section_properties_equal"] is True


def test_nontext_structure_surfaces_layout_property_mutation(tmp_path) -> None:
    source = tmp_path / "source.docx"
    candidate = tmp_path / "candidate.docx"
    _docx(source, text="source")
    _docx(candidate, text="target", paragraph_properties='<w:pageBreakBefore/>')
    result = compare_docx_nontext_structure(source, candidate)
    assert result["nontext_structure_equal"] is False
    assert result["paragraph_property_changed_indices"] == [0]
    assert result["page_break_facts_equal"] is False


def test_cjk_micro_layout_removal_is_recorded_as_existing_approved_contract(tmp_path) -> None:
    source = tmp_path / "source.docx"
    candidate = tmp_path / "candidate.docx"
    # This models the tagged-projection removal of source Latin character
    # spacing for generated CJK, and nothing else.
    _docx(source, text="source", run_properties='<w:spacing w:val="-5"/>')
    _docx(candidate, text="中文")
    result = compare_docx_nontext_structure(source, candidate)
    assert result["approved_cjk_micro_layout_run_property_changed_indices"] == [0]
    assert result["unexpected_run_property_changed_indices"] == []


def test_render_text_normalization_handles_renderer_ligatures_and_soft_hyphens() -> None:
    assert normalized_rendered_text("con\u00ad trol  ﬁle") == "control file"
