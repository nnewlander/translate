"""Resolution of serialized, part-relative locators against OOXML XML trees."""

import re
from pathlib import Path

from lxml import etree

from booktrans.domain.ir import SourceLocator
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.package import open_docx_package
from booktrans.ooxml.parts import read_xml_part


_LOCATOR = re.compile(r"^/(?P<part>.+)/paragraph\[(?P<paragraph>\d+)\](?:/text\[(?P<text>\d+)\])?$")
_TEXT_TAGS = {qn("w", "t"), qn("w", "delText")}


class LocatorResolutionError(ValueError):
    pass


class RootLocatorIndex:
    """Small per-part index used by extraction tests and the M3 writer.

    It deliberately stores only transient lxml references inside ``ooxml``.  It
    is never returned through the domain IR.
    """

    def __init__(self, root: etree._Element) -> None:
        self.root = root
        self.paragraphs = list(root.xpath(".//w:p", namespaces=NS))
        self._text_nodes: dict[int, list[etree._Element]] = {}

    def paragraph(self, ordinal: int, xml_path: str) -> etree._Element:
        index = ordinal - 1
        if index < 0 or index >= len(self.paragraphs):
            raise LocatorResolutionError(f"LOCATOR_PARAGRAPH_MISSING: {xml_path}")
        return self.paragraphs[index]

    def text_node(self, paragraph_ordinal: int, text_ordinal: int, xml_path: str) -> etree._Element:
        paragraph = self.paragraph(paragraph_ordinal, xml_path)
        nodes = self._text_nodes.get(paragraph_ordinal)
        if nodes is None:
            nodes = [
                node for node in paragraph.iter()
                if node.tag in _TEXT_TAGS and _nearest_paragraph(node) is paragraph
            ]
            self._text_nodes[paragraph_ordinal] = nodes
        index = text_ordinal - 1
        if index < 0 or index >= len(nodes):
            raise LocatorResolutionError(f"LOCATOR_TEXT_MISSING: {xml_path}")
        return nodes[index]


def resolve_locator_in_root(
    root: etree._Element,
    locator: SourceLocator,
    *,
    index: RootLocatorIndex | None = None,
) -> etree._Element:
    """Resolve a locator against an already parsed XML part.

    M3 uses this to locate candidate nodes without ever retaining lxml objects
    in its Pydantic IR.
    """
    match = _LOCATOR.match(locator.xml_path)
    if match is None or match.group("part") != locator.part_name:
        raise LocatorResolutionError(f"UNSUPPORTED_LOCATOR: {locator.xml_path}")
    locator_index = index or RootLocatorIndex(root)
    paragraph_ordinal = int(match.group("paragraph"))
    if match.group("text") is None:
        return locator_index.paragraph(paragraph_ordinal, locator.xml_path)
    return locator_index.text_node(paragraph_ordinal, int(match.group("text")), locator.xml_path)


def resolve_locator(source_path: Path, locator: SourceLocator) -> etree._Element:
    """Re-find a block or span in the immutable original OOXML package."""
    match = _LOCATOR.match(locator.xml_path)
    if match is None or match.group("part") != locator.part_name:
        raise LocatorResolutionError(f"UNSUPPORTED_LOCATOR: {locator.xml_path}")
    with open_docx_package(source_path) as package:
        if locator.part_name not in package.namelist():
            raise LocatorResolutionError(f"LOCATOR_PART_MISSING: {locator.part_name}")
        root = read_xml_part(package, locator.part_name)
        return resolve_locator_in_root(root, locator)


def _nearest_paragraph(element: etree._Element) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == qn("w", "p"):
            return current
        current = current.getparent()
    return None
