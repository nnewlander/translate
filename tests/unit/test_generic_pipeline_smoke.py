"""A book-neutral source-to-output smoke test; it never contacts a model or network."""

from pathlib import Path
from zipfile import ZipFile

from booktrans.harness.tagged_roundtrip import TaggedSyntheticRoundTrip
from booktrans.ooxml.inspector import OOXMLInspector


FIXTURE = Path(__file__).parents[1] / "fixtures" / "generic_pipeline_smoke.docx"


def test_generic_fixture_exercises_core_source_to_reconstruction_pipeline(tmp_path):
    assert FIXTURE.is_file()
    source_before = FIXTURE.read_bytes()
    _, content, stories, special, _, _ = OOXMLInspector().inspect(FIXTURE)
    assert content.body_paragraphs >= 5
    assert content.headings >= 1
    assert content.tables == 1
    assert content.images == 1
    assert content.hyperlinks == 1
    assert stories.headers.parts == 0
    assert stories.footnotes.parts == 1
    assert special.drawings >= 1

    result = TaggedSyntheticRoundTrip().run(FIXTURE, tmp_path / "output" / "generic_pipeline_smoke_zh.docx")

    assert source_before == FIXTURE.read_bytes()
    assert result.source_document.blocks
    body_ordinals = [
        block.locator.paragraph_ordinal
        for block in result.source_document.blocks
        if block.story == "body" and block.locator.paragraph_ordinal is not None
    ]
    assert body_ordinals == sorted(body_ordinals)
    assert result.views  # prepared, structurally anchored authority
    assert result.candidates
    assert result.patch_report.writes_verified > 0
    assert result.validation_report.passed
    assert result.candidate_path.is_file()
    _, candidate_content, candidate_stories, candidate_special, _, _ = OOXMLInspector().inspect(result.candidate_path)
    assert candidate_content.headings == content.headings
    assert candidate_content.tables == content.tables
    assert candidate_content.images == content.images
    assert candidate_content.hyperlinks == content.hyperlinks
    assert candidate_stories.footnotes.parts == stories.footnotes.parts
    assert candidate_special.drawings == special.drawings
    with ZipFile(result.candidate_path) as package:
        assert package.testzip() is None
        assert "word/document.xml" in package.namelist()
        assert "word/footnotes.xml" in package.namelist()
        assert any(name.startswith("word/media/") for name in package.namelist())
        assert package.read("word/_rels/document.xml.rels")
        assert b"footnotes" in package.read("word/_rels/document.xml.rels")
        assert b"Native footnote text" in package.read("word/footnotes.xml")
        document_xml = package.read("word/document.xml")
        assert document_xml.count(b"<w:sectPr") >= 2
        assert b"ListBullet" in document_xml
        assert b"w:hyperlink" in document_xml
