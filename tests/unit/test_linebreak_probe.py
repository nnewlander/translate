import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from booktrans.ooxml.linebreak_probe import build_linebreak_probe, extract_probe_payload_texts
from booktrans.ooxml.namespaces import NS, qn


def _template_docx(path: Path) -> None:
    document = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:pPr><w:spacing w:line="240"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial"/><w:sz w:val="22"/></w:rPr><w:t>Template 中文 body text.</w:t></w:r></w:p>
    <w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>
    </w:body></w:document>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")


def _root(path: Path) -> etree._Element:
    with ZipFile(path) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def test_probe_variants_preserve_visible_payload_and_template(tmp_path):
    template = tmp_path / "production-candidate.docx"
    output = tmp_path / "linebreak_probe.docx"
    manifest_path = tmp_path / "linebreak_probe_manifest.json"
    _template_docx(template)
    before = template.read_bytes()

    manifest = build_linebreak_probe(template, output, manifest_path, body_paragraph_ordinal=1)

    assert template.read_bytes() == before
    assert manifest["template_immutable"] is True
    assert manifest["text_preservation_passed"] is True
    payloads = extract_probe_payload_texts(output)
    assert len({tuple(texts) for texts in payloads.values()}) == 1
    assert all(variant["approval_status"] == "EXPERIMENT_ONLY" for variant in manifest["variants"])
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["visible_text_sha256"] == manifest["visible_text_sha256"]


def test_language_metadata_experiment_is_text_neutral(tmp_path):
    template = tmp_path / "template.docx"
    output = tmp_path / "probe.docx"
    manifest = tmp_path / "probe.json"
    _template_docx(template)
    build_linebreak_probe(template, output, manifest, body_paragraph_ordinal=1)

    root = _root(output)
    paragraphs = root.xpath(".//w:p", namespaces=NS)
    c_label = next(index for index, paragraph in enumerate(paragraphs) if "C_EXPLICIT_SCRIPT_LANGUAGE" in "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)))
    c_payload = paragraphs[c_label + 1]
    runs = c_payload.xpath("w:r", namespaces=NS)
    assert len(runs) == 3
    languages = [run.find("w:rPr/w:lang", namespaces=NS) for run in runs]
    assert [node.get(qn("w", "val")) for node in languages] == ["zh-CN", "en-US", "zh-CN"]
    assert all(node.get(qn("w", "eastAsia")) == "zh-CN" for node in languages)
    assert "".join(c_payload.xpath(".//w:t/text()", namespaces=NS)) == extract_probe_payload_texts(output)["A_BASELINE"][0]


def test_probe_does_not_mutate_urls_code_or_protected_literals(tmp_path):
    template = tmp_path / "template.docx"
    output = tmp_path / "probe.docx"
    manifest = tmp_path / "probe.json"
    _template_docx(template)
    before = template.read_bytes()

    build_linebreak_probe(template, output, manifest, body_paragraph_ordinal=1)

    assert template.read_bytes() == before
    visible_probe_text = "\n".join(text for texts in extract_probe_payload_texts(output).values() for text in texts)
    assert "https://" not in visible_probe_text
    assert "foo()" not in visible_probe_text
