"""Read-only OOXML package inspection.

This module intentionally returns domain models, never lxml elements, so a future
Document IR can remain independent of the package implementation.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree

from booktrans.domain.models import (
    ContentStats,
    Diagnostic,
    OtherStories,
    PackageStats,
    SpecialConstructStats,
    StoryStats,
)
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.package import is_ooxml_part, open_docx_package
from booktrans.ooxml.parts import read_xml_part, xml_part_names


class OOXMLInspector:
    """Inspect a DOCX exclusively through read-only ZIP/XML operations."""

    def inspect(self, source_path: Path) -> tuple[PackageStats, ContentStats, OtherStories, SpecialConstructStats, list[Diagnostic], dict[str, int]]:
        warnings: list[Diagnostic] = []
        with open_docx_package(source_path) as package:
            names = package.namelist()
            part_sizes = {info.filename: info.file_size for info in package.infolist()}
            relationship_parts = [name for name in names if name.lower().endswith(".rels")]
            relationship_count = 0
            parsed_parts: dict[str, etree._Element] = {}

            for name in xml_part_names(package):
                try:
                    root = read_xml_part(package, name)
                except etree.XMLSyntaxError as exc:
                    warnings.append(
                        Diagnostic(
                            code="MALFORMED_OPTIONAL_XML_PART",
                            severity="warning",
                            message="An optional XML part could not be parsed and was not structurally counted.",
                            part=name,
                            detail=str(exc),
                        )
                    )
                    continue
                if root is not None:
                    parsed_parts[name] = root
                    if name.lower().endswith(".rels"):
                        relationship_count += len(root.findall("pr:Relationship", namespaces=NS))

            package_stats = PackageStats(
                zip_entries=len(names),
                ooxml_parts=sum(is_ooxml_part(name) for name in names),
                relationship_parts=len(relationship_parts),
                relationships=relationship_count,
                xml_parts=len(parsed_parts),
                part_names=sorted(names),
            )
            styles = self._heading_style_ids(parsed_parts.get("word/styles.xml"), warnings)
            main_root = parsed_parts["word/document.xml"]
            content = self._main_content(main_root, styles)
            stories = self._other_stories(parsed_parts)
            special = self._special_constructs(parsed_parts, names)
            self._add_construct_warnings(special, warnings)
            self._report_unclassified_risky_parts(names, warnings)
            return package_stats, content, stories, special, warnings, part_sizes

    @staticmethod
    def _count(root: etree._Element, expression: str) -> int:
        return len(root.xpath(expression, namespaces=NS))

    def _heading_style_ids(self, styles_root: etree._Element | None, warnings: list[Diagnostic]) -> set[str]:
        if styles_root is None:
            warnings.append(
                Diagnostic(
                    code="STYLES_PART_MISSING",
                    severity="warning",
                    message="word/styles.xml is absent; heading detection cannot be performed reliably.",
                )
            )
            return set()
        heading_ids: set[str] = set()
        for style in styles_root.findall("w:style", namespaces=NS):
            if style.get(qn("w", "type")) != "paragraph":
                continue
            style_id = style.get(qn("w", "styleId"))
            name = style.find("w:name", namespaces=NS)
            style_name = name.get(qn("w", "val"), "") if name is not None else ""
            # Built-in style ids are locale-independent; display names cover custom aliases.
            if style_id and (style_id.lower().startswith("heading") or style_name.lower().startswith("heading")):
                heading_ids.add(style_id)
        return heading_ids

    def _main_content(self, root: etree._Element, heading_style_ids: set[str]) -> ContentStats:
        body = root.find("w:body", namespaces=NS)
        if body is None:
            # Validation guarantees XML, but a DOCX lacking a body is not safely inspectable as normal content.
            return ContentStats()
        paragraphs = body.findall(".//w:p", namespaces=NS)
        headings = 0
        for paragraph in paragraphs:
            style = paragraph.find("w:pPr/w:pStyle", namespaces=NS)
            if style is not None and style.get(qn("w", "val")) in heading_style_ids:
                headings += 1
        return ContentStats(
            body_paragraphs=len(paragraphs),
            headings=headings,
            tables=self._count(body, ".//w:tbl"),
            table_cells=self._count(body, ".//w:tc"),
            # Count visual references, not media ZIP files: one file may appear many times.
            images=self._count(body, ".//a:blip") + self._count(body, ".//v:imagedata"),
            hyperlinks=self._count(body, ".//w:hyperlink"),
        )

    def _story_stats(self, roots: list[etree._Element]) -> StoryStats:
        return StoryStats(
            parts=len(roots),
            paragraphs=sum(self._count(root, ".//w:p") for root in roots),
            tables=sum(self._count(root, ".//w:tbl") for root in roots),
        )

    def _other_stories(self, parsed_parts: dict[str, etree._Element]) -> OtherStories:
        def collect(prefix: str, exact: str | None = None) -> list[etree._Element]:
            if exact is not None:
                return [root for name, root in parsed_parts.items() if name == exact]
            return [root for name, root in parsed_parts.items() if name.startswith(prefix)]

        return OtherStories(
            headers=self._story_stats(collect("word/header")),
            footers=self._story_stats(collect("word/footer")),
            footnotes=self._story_stats(collect("", "word/footnotes.xml")),
            endnotes=self._story_stats(collect("", "word/endnotes.xml")),
            comments=self._story_stats(collect("", "word/comments.xml")),
        )

    def _special_constructs(self, parsed_parts: dict[str, etree._Element], names: list[str]) -> SpecialConstructStats:
        word_roots = [root for name, root in parsed_parts.items() if name.startswith("word/")]
        total: Counter[str] = Counter()
        for root in word_roots:
            total["simple_fields"] += self._count(root, ".//w:fldSimple")
            total["field_characters"] += self._count(root, ".//w:fldChar")
            total["field_instruction_texts"] += self._count(root, ".//w:instrText")
            total["bookmarks"] += self._count(root, ".//w:bookmarkStart")
            total["tracked_insertions"] += self._count(root, ".//w:ins")
            total["tracked_deletions"] += self._count(root, ".//w:del")
            total["content_controls"] += self._count(root, ".//w:sdt")
            total["text_boxes"] += self._count(root, ".//w:txbxContent")
            total["vml_text_boxes"] += self._count(root, ".//v:textbox")
            total["equations"] += self._count(root, ".//m:oMath")
            total["ole_objects"] += self._count(root, ".//o:OLEObject")
            total["drawings"] += self._count(root, ".//w:drawing")
            total["anchored_shapes"] += self._count(root, ".//wp:anchor")
            total["alternate_content"] += len(root.xpath(".//*[local-name()='AlternateContent']"))
            total["alt_chunks"] += self._count(root, ".//w:altChunk")
            total["subdocuments"] += self._count(root, ".//w:subDoc")
            total["custom_xml_ranges"] += self._count(root, ".//w:customXml")
        smartart_parts = sum(name.startswith("word/diagrams/") for name in names)
        embedded_objects = sum(name.startswith("word/embeddings/") for name in names)
        return SpecialConstructStats(
            **total,
            smartart_parts=smartart_parts,
            embedded_objects=embedded_objects,
            detection_confidence={
                "fields": "medium: complex fields are counted as field characters/instruction text, not reconstructed into logical fields",
                "headings": "high when styles.xml is present; custom or localized style semantics may differ",
                "images": "medium: counts DrawingML/VML image references in the main body, not every visual object",
                "text_boxes": "medium: detects w:txbxContent and VML textbox elements; other shape text may be missed",
                "smartart": "medium: package-part heuristic; not a semantic SmartArt graph parse",
                "ole_objects": "medium: detects OLE XML markers and embeddings, but does not inspect payloads",
                "unknown_visible_content": "limited: flagged known risky package constructs; this is not a complete OOXML semantic renderer",
            },
        )

    @staticmethod
    def _add_construct_warnings(special: SpecialConstructStats, warnings: list[Diagnostic]) -> None:
        risky = {
            "field_characters": "FIELD_CODE_PRESENT",
            "tracked_insertions": "TRACKED_CHANGE_PRESENT",
            "tracked_deletions": "TRACKED_CHANGE_PRESENT",
            "content_controls": "CONTENT_CONTROL_PRESENT",
            "text_boxes": "TEXT_BOX_PRESENT",
            "vml_text_boxes": "VML_TEXT_BOX_PRESENT",
            "equations": "OFFICE_MATH_PRESENT",
            "smartart_parts": "SMARTART_PART_PRESENT",
            "ole_objects": "OLE_OBJECT_PRESENT",
            "embedded_objects": "EMBEDDED_OBJECT_PRESENT",
            "alternate_content": "ALTERNATE_CONTENT_PRESENT",
            "alt_chunks": "ALT_CHUNK_PRESENT",
            "subdocuments": "SUBDOCUMENT_REFERENCE_PRESENT",
            "custom_xml_ranges": "CUSTOM_XML_RANGE_PRESENT",
        }
        for field_name, code in risky.items():
            count = getattr(special, field_name)
            if count:
                warnings.append(
                    Diagnostic(
                        code=code,
                        severity="warning",
                        message=f"Detected {count} {field_name.replace('_', ' ')}; later processing must preserve or explicitly support it.",
                    )
                )

    @staticmethod
    def _report_unclassified_risky_parts(names: list[str], warnings: list[Diagnostic]) -> None:
        known_safe_prefixes = (
            "word/document.xml", "word/styles", "word/settings", "word/fontTable", "word/theme/",
            "word/media/", "word/_rels/", "word/header", "word/footer", "word/footnotes",
            "word/endnotes", "word/comments", "word/numbering", "word/webSettings", "word/diagrams/",
            "word/embeddings/", "word/glossary/", "word/people", "word/customXml",
        )
        for name in names:
            if name.startswith("word/") and not name.endswith("/") and not name.startswith(known_safe_prefixes):
                warnings.append(
                    Diagnostic(
                        code="UNCLASSIFIED_WORD_PART",
                        severity="warning",
                        message="An unclassified word/ package part may carry content; it was not silently treated as supported.",
                        part=name,
                    )
                )
