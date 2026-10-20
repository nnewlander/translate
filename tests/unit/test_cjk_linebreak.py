from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree

from booktrans.domain.ir import SourceLocator
from booktrans.ooxml.cjk_linebreak import (
    CJKLineBreakPolicyError,
    CJKLineBreakTarget,
    apply_cjk_line_break_policy,
)
from booktrans.ooxml.namespaces import NS, qn


def _docx(path: Path) -> None:
    document = """<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>
    <w:p><w:r><w:t>中文 Compiling 正文。</w:t></w:r></w:p>
    <w:p><w:r><w:t>中文 fork 正文。</w:t></w:r></w:p>
    <w:p><w:r><w:t>中文句子。</w:t></w:r></w:p>
    <w:p><w:r><w:t>Plain English source paragraph.</w:t></w:r></w:p>
    <w:p><w:hyperlink><w:r><w:t>中文 https://example.com</w:t></w:r></w:hyperlink></w:p>
    <w:p><w:r><w:t>代码 foo()</w:t></w:r></w:p><w:sectPr/></w:body></w:document>"""
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def _target(ordinal: int, text: str, *, role: str = "body_paragraph") -> CJKLineBreakTarget:
    return CJKLineBreakTarget(
        display_id=f"body.p.{ordinal:04d}",
        locator=SourceLocator(part_name="word/document.xml", story="body", xml_path=f"/word/document.xml/paragraph[{ordinal}]", paragraph_ordinal=ordinal),
        content_role=role,
        target_text=text,
    )


def _root(path: Path) -> etree._Element:
    with ZipFile(path) as archive:
        return etree.fromstring(archive.read("word/document.xml"))


def _value(root: etree._Element, ordinal: int, property_name: str) -> str | None:
    node = root.xpath(f".//w:p[{ordinal}]/w:pPr/w:{property_name}", namespaces=NS)
    return node[0].get(qn("w", "val")) if node else None


@pytest.mark.parametrize(
    ("ordinal", "text"),
    [(1, "中文 Compiling 正文。"), (2, "中文 fork 正文。"), (3, "若没有，就存在规模化问题。")],
)
def test_cjk_prose_enforces_validated_kinsoku_only(tmp_path, ordinal, text):
    source = tmp_path / "source.docx"; output = tmp_path / "out.docx"; _docx(source)
    before = source.read_bytes()
    report = apply_cjk_line_break_policy(source, output, [_target(ordinal, text)])
    root = _root(output)
    assert source.read_bytes() == before
    assert report.rejected == []
    assert _value(root, ordinal, "wordWrap") is None
    assert _value(root, ordinal, "kinsoku") == "1"
    source_root = _root(source)
    assert "".join(root.xpath(f".//w:p[{ordinal}]//w:t/text()", namespaces=NS)) == "".join(source_root.xpath(f".//w:p[{ordinal}]//w:t/text()", namespaces=NS))


def test_policy_does_not_touch_normal_english_or_protected_url(tmp_path):
    source = tmp_path / "source.docx"; output = tmp_path / "out.docx"; _docx(source)
    with pytest.raises(CJKLineBreakPolicyError, match="TARGET_NOT_CJK_PROSE"):
        apply_cjk_line_break_policy(source, output, [_target(4, "Plain English source paragraph.")])
    with pytest.raises(CJKLineBreakPolicyError, match="URL_OR_PROTECTED_LITERAL_PRESENT"):
        apply_cjk_line_break_policy(source, output, [_target(5, "中文 https://example.com")])
    assert not output.exists()


def test_code_role_is_not_automatically_eligible(tmp_path):
    source = tmp_path / "source.docx"; output = tmp_path / "out.docx"; _docx(source)
    with pytest.raises(CJKLineBreakPolicyError, match="NOT_NORMAL_FLOW_BODY_PROSE"):
        apply_cjk_line_break_policy(source, output, [_target(6, "代码 foo()", role="code")])


def test_duplicate_locator_is_rejected_before_candidate_creation(tmp_path):
    source = tmp_path / "source.docx"; output = tmp_path / "out.docx"; _docx(source)
    with pytest.raises(CJKLineBreakPolicyError, match="DUPLICATE_CJK_LINEBREAK_TARGET"):
        apply_cjk_line_break_policy(source, output, [_target(1, "中文 Compiling 正文。"), _target(1, "中文 Compiling 正文。")])
    assert not output.exists()
