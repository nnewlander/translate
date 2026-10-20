from pathlib import Path
import zipfile

from lxml import etree

from booktrans.ooxml.namespaces import NS


class PackageValidationError(ValueError):
    """Input is not a safe, recognizable DOCX OOXML package."""


def open_docx_package(path: Path) -> zipfile.ZipFile:
    """Validate the package using read-only ZIP access and required OOXML parts."""
    if not path.is_file():
        raise PackageValidationError(f"INPUT_NOT_FOUND: File does not exist: {path}")
    try:
        package = zipfile.ZipFile(path, mode="r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageValidationError(f"INVALID_ZIP: Input is not a readable ZIP package: {path}") from exc
    names = set(package.namelist())
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    missing = sorted(required - names)
    if missing:
        package.close()
        raise PackageValidationError(
            "INVALID_DOCX: Missing required OOXML part(s): " + ", ".join(missing)
        )
    try:
        etree.fromstring(package.read("[Content_Types].xml"))
        root_relationships = etree.fromstring(package.read("_rels/.rels"))
        etree.fromstring(package.read("word/document.xml"))
        has_main_document_relationship = any(
            relationship.get("Type", "").endswith("/officeDocument")
            for relationship in root_relationships.findall("pr:Relationship", namespaces=NS)
        )
        if not has_main_document_relationship:
            raise PackageValidationError(
                "INVALID_DOCX: _rels/.rels does not declare an officeDocument relationship."
            )
        corrupt_entry = package.testzip()
        if corrupt_entry is not None:
            raise PackageValidationError(f"INVALID_ZIP: Corrupt ZIP entry: {corrupt_entry}")
    except (KeyError, etree.XMLSyntaxError, zipfile.BadZipFile) as exc:
        package.close()
        raise PackageValidationError("INVALID_DOCX: Required OOXML XML is malformed.") from exc
    except PackageValidationError:
        package.close()
        raise
    return package


def is_xml_part(name: str) -> bool:
    return name.lower().endswith((".xml", ".rels"))


def is_ooxml_part(name: str) -> bool:
    return name != "[Content_Types].xml" and not name.endswith("/")
