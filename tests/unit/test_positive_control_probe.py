import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.positive_control_probe import (
    PositiveControlCase,
    build_positive_control_probe,
    extract_positive_control_payloads,
)


def _template(path: Path) -> None:
    document = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:pPr><w:ind w:right="24"/><w:jc w:val="both"/><w:wordWrap w:val="0"/><w:kinsoku w:val="1"/></w:pPr>
      <w:r><w:rPr><w:color w:val="000000"/></w:rPr><w:t>这是 Comp</w:t></w:r><w:r><w:rPr><w:color w:val="990000"/></w:rPr><w:t>iling 示例。</w:t></w:r>
    </w:p>
    <w:p><w:pPr><w:wordWrap w:val="0"/><w:kinsoku w:val="1"/></w:pPr><w:r><w:t>这是 fork 与 15 以及 15 的示例。</w:t></w:r></w:p>
    <w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")


def _paragraphs(path: Path) -> list[etree._Element]:
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return root.xpath(".//w:p", namespaces=NS)


def test_positive_control_probe_preserves_real_paragraph_text_and_template(tmp_path):
    template = tmp_path / "chapter1_candidate.docx"
    output = tmp_path / "positive_control.docx"
    manifest_path = tmp_path / "manifest.json"
    _template(template)
    before = template.read_bytes()
    cases = [
        PositiveControlCase("body.p.00001", 1, "Compiling", "Compilin / g"),
        PositiveControlCase("body.p.00002", 2, "15", "1 / 5"),
    ]

    manifest = build_positive_control_probe(template, output, manifest_path, cases, width_deltas_twips=(0, 72))

    assert template.read_bytes() == before
    assert manifest["template_immutable"] is True
    assert len(manifest["fixtures"]) == 2 * 2 * 4
    payloads = extract_positive_control_payloads(output)
    for display_id in ("body.p.00001", "body.p.00002"):
        for width_id in ("W0", "W1"):
            hashes = {tuple(payloads[f"{display_id}:{width_id}:{variant}"]) for variant in (
                "A_REAL_BASELINE", "B_REAL_WORDWRAP", "C_REAL_EXPLICIT_LANGUAGE", "D_REAL_LANGUAGE_NO_WORDWRAP",
            )}
            assert len(hashes) == 1
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["positive_control_contract"].startswith("The experiment is diagnostic")
    assert all(item["approval_status"] == "EXPERIMENT_ONLY" for item in manifest["variants"])


def test_positive_control_variants_change_only_approved_probe_properties(tmp_path):
    template = tmp_path / "template.docx"
    output = tmp_path / "probe.docx"
    manifest = tmp_path / "manifest.json"
    _template(template)
    build_positive_control_probe(
        template,
        output,
        manifest,
        [PositiveControlCase("body.p.00001", 1, "Compiling", "Compilin / g")],
        width_deltas_twips=(0,),
    )
    paragraphs = _paragraphs(output)
    copied = [paragraph for paragraph in paragraphs if "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).startswith("这是")]
    assert len(copied) == 4
    assert [paragraph.find("w:pPr/w:wordWrap", namespaces=NS) is None for paragraph in copied] == [True, False, False, True]
    assert all(paragraph.find("w:pPr/w:kinsoku", namespaces=NS).get(qn("w", "val")) == "1" for paragraph in copied)
    # C/D preserve the color-anchor split while adding run-local language data.
    explicit = copied[2:]
    for paragraph in explicit:
        assert "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)) == "这是 Compiling 示例。"
        langs = paragraph.xpath(".//w:r/w:rPr/w:lang", namespaces=NS)
        assert langs
        assert any(node.get(qn("w", "val")) == "en-US" for node in langs)
        assert all(node.get(qn("w", "eastAsia")) == "zh-CN" for node in langs)
