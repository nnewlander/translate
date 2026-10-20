import pytest
import zipfile

from booktrans.ooxml.inspector import OOXMLInspector
from booktrans.ooxml.package import PackageValidationError


def test_non_docx_fails_with_actionable_error(tmp_path):
    text_file = tmp_path / "not-a-docx.txt"
    text_file.write_text("not a zip", encoding="utf-8")
    with pytest.raises(PackageValidationError, match="INVALID_ZIP"):
        OOXMLInspector().inspect(text_file)


def test_zip_without_required_ooxml_parts_fails_as_invalid_docx(tmp_path):
    archive = tmp_path / "not-a-docx.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("arbitrary.txt", "not OOXML")
    with pytest.raises(PackageValidationError, match="INVALID_DOCX: Missing required OOXML"):
        OOXMLInspector().inspect(archive)


def test_minimal_docx_is_recognized(minimal_docx):
    package, content, stories, special, warnings, _ = OOXMLInspector().inspect(minimal_docx)
    assert package.zip_entries > 0
    assert package.ooxml_parts > 0
    assert content.body_paragraphs >= 1
    assert stories.headers.parts == 0
    assert special.drawings == 0
    assert isinstance(warnings, list)


def test_common_docx_structures_are_counted(rich_docx):
    _, content, _, _, _, _ = OOXMLInspector().inspect(rich_docx)
    assert content.body_paragraphs >= 3
    assert content.headings == 1
    assert content.tables == 1
    assert content.table_cells == 2
    assert content.images == 1
    assert content.hyperlinks == 1
