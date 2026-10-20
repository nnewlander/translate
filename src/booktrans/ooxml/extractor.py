"""M2 read-only OOXML-to-Document-IR extraction.

The extractor is deliberately conservative: it extracts paragraph-like Word text
that it can locate, and creates coverage uncertainty entries for potentially
visible constructs it cannot prove are represented in the IR.
"""

from collections import defaultdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import re
import zipfile

from lxml import etree

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ids import stable_uid
from booktrans.domain.ir import BlockIR, CoverageEntry, DocumentIR, DocumentStoryIR, SourceLocator, SpanIR
from booktrans.domain.models import Diagnostic, SourceSnapshot
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.package import open_docx_package
from booktrans.ooxml.parts import read_xml_part, xml_part_names


URL_TOKEN = re.compile(r"(?:https?://|www\.)\S+|\bdoi:\s*\S+|\b10\.\d{4,9}/\S+|\b(?:ISBN(?:-1[03])?[: ]*)?[0-9][0-9\- ]{8,}[0-9Xx]\b", re.IGNORECASE)
KNOWN_PROTECTED_FIELDS = {"PAGE", "NUMPAGES", "REF", "PAGEREF", "TOC"}
TEXT_TAGS = {qn("w", "t"), qn("w", "delText")}


class DocumentExtractor:
    def extract(self, source_path: Path, source: SourceSnapshot) -> tuple[DocumentIR, list[CoverageEntry], dict[str, int]]:
        diagnostics: list[Diagnostic] = []
        uncertainty: list[CoverageEntry] = []
        with open_docx_package(source_path) as package:
            part_sizes = {info.filename: info.file_size for info in package.infolist()}
            parsed = self._parse_word_parts(package, diagnostics)
            styles = self._heading_style_ids(parsed.get("word/styles.xml"))
            relationships = self._relationships(package, diagnostics)
            stories: list[DocumentStoryIR] = []
            for part_name in self._story_part_names(parsed):
                root = parsed[part_name]
                story = self._story_name(part_name)
                uncertainty.extend(self._risk_entries(root, part_name, story, diagnostics))
                blocks = self._blocks_for_part(
                    root, part_name, story, source.sha256, styles, relationships.get(part_name, {})
                )
                stories.append(DocumentStoryIR(story=story, part_name=part_name, blocks=blocks))
        return DocumentIR(
            document_sha256=source.sha256,
            source=source,
            extracted_at_utc=datetime.now(UTC),
            stories=stories,
            diagnostics=diagnostics,
        ), uncertainty, part_sizes

    def _parse_word_parts(self, package: zipfile.ZipFile, diagnostics: list[Diagnostic]) -> dict[str, etree._Element]:
        parsed: dict[str, etree._Element] = {}
        for name in xml_part_names(package):
            if not name.startswith("word/"):
                continue
            try:
                root = read_xml_part(package, name)
            except etree.XMLSyntaxError as exc:
                diagnostics.append(Diagnostic(
                    code="MALFORMED_WORD_XML_PART", severity="warning",
                    message="A word XML part could not be parsed and cannot be proven covered.", part=name, detail=str(exc),
                ))
                continue
            if root is not None:
                parsed[name] = root
        if "word/document.xml" not in parsed:
            # M1 package validation guarantees this; a direct error remains more actionable than a KeyError.
            raise ValueError("INVALID_DOCX: word/document.xml could not be parsed for extraction.")
        return parsed

    @staticmethod
    def _story_part_names(parsed: dict[str, etree._Element]) -> list[str]:
        supported = ["word/document.xml", "word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"]
        names = [name for name in supported if name in parsed]
        names.extend(sorted(name for name in parsed if name.startswith("word/header") and name.endswith(".xml")))
        names.extend(sorted(name for name in parsed if name.startswith("word/footer") and name.endswith(".xml")))
        return names

    @staticmethod
    def _story_name(part_name: str) -> str:
        if part_name == "word/document.xml":
            return "body"
        if part_name.startswith("word/header"):
            return "headers"
        if part_name.startswith("word/footer"):
            return "footers"
        if part_name == "word/footnotes.xml":
            return "footnotes"
        if part_name == "word/endnotes.xml":
            return "endnotes"
        return "comments"

    def _heading_style_ids(self, styles_root: etree._Element | None) -> set[str]:
        if styles_root is None:
            return set()
        ids: set[str] = set()
        for style in styles_root.findall("w:style", namespaces=NS):
            style_id = style.get(qn("w", "styleId"))
            name = style.find("w:name", namespaces=NS)
            style_name = name.get(qn("w", "val"), "") if name is not None else ""
            if style.get(qn("w", "type")) == "paragraph" and style_id and (
                style_id.lower().startswith("heading") or style_name.lower().startswith("heading")
            ):
                ids.add(style_id)
        return ids

    def _relationships(self, package: zipfile.ZipFile, diagnostics: list[Diagnostic]) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for name in package.namelist():
            if not name.startswith("word/_rels/") or not name.endswith(".rels"):
                continue
            source_name = "word/" + name.removeprefix("word/_rels/").removesuffix(".rels")
            try:
                root = read_xml_part(package, name)
            except etree.XMLSyntaxError as exc:
                diagnostics.append(Diagnostic(code="MALFORMED_RELATIONSHIPS", severity="warning", message="Relationships could not be parsed.", part=name, detail=str(exc)))
                continue
            if root is not None:
                result[source_name] = {
                    relation.get("Id", ""): relation.get("Target", "")
                    for relation in root.findall("pr:Relationship", namespaces=NS)
                }
        return result

    def _blocks_for_part(
        self,
        root: etree._Element,
        part_name: str,
        story: str,
        document_sha: str,
        heading_ids: set[str],
        relationships: dict[str, str],
    ) -> list[BlockIR]:
        blocks: list[BlockIR] = []
        body_counter = 0
        ordinary_counter = 0
        for paragraph_ordinal, paragraph in enumerate(root.xpath(".//w:p", namespaces=NS), start=1):
            if self._in_non_effective_alternate(paragraph):
                continue
            block_type, display_id = self._block_identity(paragraph, root, story, part_name, body_counter, ordinary_counter)
            if block_type == "body_paragraph":
                body_counter += 1
            ordinary_counter += 1
            locator = SourceLocator(
                part_name=part_name,
                story=story,
                xml_path=f"/{part_name}/paragraph[{paragraph_ordinal}]",
                paragraph_ordinal=ordinary_counter,
            )
            role = self._content_role(paragraph, heading_ids, block_type)
            spans, block_diagnostics = self._spans_for_paragraph(
                paragraph, part_name, story, document_sha, relationships, locator
            )
            text_spans = [span for span in spans if span.kind == "text"]
            plain_text = "".join(span.text for span in text_spans)
            source_hash = sha256(plain_text.encode("utf-8")).hexdigest()
            structure_fingerprint = sha256(etree.tostring(paragraph, method="c14n")).hexdigest()
            classifications = {span.classification for span in text_spans}
            translation_scope = next(iter(classifications)) if len(classifications) == 1 else None
            unit_uid = stable_uid(document_sha, part_name, locator.xml_path, source_hash)
            blocks.append(BlockIR(
                unit_uid=unit_uid,
                display_id=display_id,
                story=story,
                part_name=part_name,
                block_type=block_type,
                content_role=role,
                locator=locator,
                source_plain_text=plain_text,
                source_hash=source_hash,
                structure_fingerprint=structure_fingerprint,
                spans=spans,
                translation_scope=translation_scope,
                diagnostics=block_diagnostics,
            ))
        return blocks

    def _block_identity(self, paragraph, root, story, part_name, body_counter, ordinary_counter) -> tuple[str, str]:
        textbox = self._ancestor(paragraph, qn("w", "txbxContent"))
        if textbox is not None:
            boxes = root.xpath(".//w:txbxContent", namespaces=NS)
            box_number = boxes.index(textbox) + 1
            p_number = len(textbox.xpath(".//w:p", namespaces=NS)[: textbox.xpath(".//w:p", namespaces=NS).index(paragraph) + 1])
            return "textbox_paragraph", f"textbox.{box_number:03d}.p.{p_number:04d}"
        cell = self._ancestor(paragraph, qn("w", "tc"))
        if cell is not None:
            table = self._ancestor(paragraph, qn("w", "tbl"))
            tables = root.xpath(".//w:tbl", namespaces=NS)
            table_number = tables.index(table) + 1 if table in tables else 0
            row = self._ancestor(paragraph, qn("w", "tr"))
            row_number = list(table.findall("w:tr", namespaces=NS)).index(row) + 1 if row is not None else 0
            cell_number = list(row.findall("w:tc", namespaces=NS)).index(cell) + 1 if row is not None else 0
            p_number = list(cell.findall("w:p", namespaces=NS)).index(paragraph) + 1 if paragraph in cell.findall("w:p", namespaces=NS) else 1
            return "table_cell_paragraph", f"table.{table_number:03d}.r.{row_number:03d}.c.{cell_number:03d}.p.{p_number:03d}"
        if story == "body":
            return "body_paragraph", f"body.p.{body_counter + 1:05d}"
        if story in {"headers", "footers"}:
            digits = "".join(character for character in Path(part_name).stem if character.isdigit()) or "0"
            prefix = "header" if story == "headers" else "footer"
            return "story_paragraph", f"{prefix}.{int(digits):02d}.p.{ordinary_counter + 1:04d}"
        if story in {"footnotes", "endnotes"}:
            container = self._ancestor(paragraph, qn("w", "footnote" if story == "footnotes" else "endnote"))
            note_id = container.get(qn("w", "id"), "unknown") if container is not None else "unknown"
            prefix = "footnote" if story == "footnotes" else "endnote"
            return "story_paragraph", f"{prefix}.{note_id}.p.{ordinary_counter + 1:04d}"
        return "comment_paragraph", f"comment.p.{ordinary_counter + 1:04d}"

    @staticmethod
    def _content_role(paragraph, heading_ids: set[str], block_type: str) -> str:
        if block_type == "textbox_paragraph":
            return "textbox"
        if block_type == "table_cell_paragraph":
            return "table_cell"
        style = paragraph.find("w:pPr/w:pStyle", namespaces=NS)
        if style is not None and style.get(qn("w", "val")) in heading_ids:
            return "heading"
        return "paragraph"

    def _spans_for_paragraph(self, paragraph, part_name, story, document_sha, relationships, block_locator):
        diagnostics: list[Diagnostic] = []
        spans: list[SpanIR] = []
        pending: SpanIR | None = None
        active_field: dict[str, object] | None = None
        text_ordinal = 0
        for text_node in paragraph.iter():
            # A text box paragraph is nested in the drawing's outer paragraph.  It
            # is its own BlockIR and must not be re-counted by that outer block.
            if self._ancestor(text_node, qn("w", "p")) is not paragraph:
                continue
            if text_node.tag == qn("w", "fldChar"):
                field_type = text_node.get(qn("w", "fldCharType"), "")
                if field_type == "begin":
                    active_field = {"instruction": "", "known": False, "separated": False}
                elif field_type == "separate" and active_field is not None:
                    active_field["known"] = self._field_is_known(str(active_field["instruction"]))
                    active_field["separated"] = True
                    field = {"instruction": str(active_field["instruction"]), "known": bool(active_field["known"])}
                    spans.append(self._field_boundary_span(document_sha, part_name, story, block_locator, len(spans), field))
                    if not field["known"]:
                        diagnostics.append(Diagnostic(code="UNKNOWN_FIELD_INSTRUCTION", severity="warning", message="Unknown field instruction has a displayed result treated as coverage uncertainty.", part=part_name, detail=field["instruction"]))
                elif field_type == "end":
                    active_field = None
                continue
            if text_node.tag == qn("w", "instrText"):
                if active_field is not None and not active_field["separated"]:
                    active_field["instruction"] = str(active_field["instruction"]) + (text_node.text or "")
                continue
            if text_node.tag == qn("w", "fldSimple"):
                instruction = text_node.get(qn("w", "instr"), "").strip()
                field = {"instruction": instruction, "known": self._field_is_known(instruction)}
                spans.append(self._field_boundary_span(document_sha, part_name, story, block_locator, len(spans), field))
                if not field["known"]:
                    diagnostics.append(Diagnostic(code="UNKNOWN_FIELD_INSTRUCTION", severity="warning", message="Unknown simple field instruction has a displayed result treated as coverage uncertainty.", part=part_name, detail=instruction))
                continue
            if text_node.tag not in TEXT_TAGS or self._in_non_effective_alternate(text_node):
                continue
            text_ordinal += 1
            locator = SourceLocator(part_name=part_name, story=story, xml_path=f"{block_locator.xml_path}/text[{text_ordinal}]", container_path=block_locator.xml_path, paragraph_ordinal=block_locator.paragraph_ordinal)
            text = text_node.text or ""
            field = self._simple_field_context(text_node)
            if field is None and active_field is not None and active_field["separated"]:
                field = {"instruction": str(active_field["instruction"]), "known": bool(active_field["known"])}
            classification, context, atom_diagnostics = self._classify_text(text_node, text, story, field, relationships)
            fmt = self._run_fingerprint(text_node)
            merge_key = (classification, fmt, tuple(sorted(context.items())))
            if pending is not None and pending.container_context.get("merge_key") == repr(merge_key):
                pending.text += text
                pending.source_run_locators.append(locator)
                pending.diagnostics.extend(atom_diagnostics)
                continue
            span = SpanIR(
                span_uid=stable_uid(document_sha, part_name, locator.xml_path, str(len(spans)), "text"),
                kind="text",
                locator=locator,
                text=text,
                classification=classification,
                source_run_locators=[],
                direct_format_fingerprint=fmt,
                container_context={**context, "merge_key": repr(merge_key)},
                diagnostics=atom_diagnostics,
            )
            spans.append(span)
            pending = span
        for span in spans:
            span.container_context.pop("merge_key", None)
        return spans, diagnostics

    def _simple_field_context(self, node) -> dict[str, object] | None:
        field = self._ancestor(node, qn("w", "fldSimple"))
        if field is None:
            return None
        instruction = field.get(qn("w", "instr"), "").strip()
        return {"instruction": instruction, "known": self._field_is_known(instruction)}

    @staticmethod
    def _field_is_known(instruction: str) -> bool:
        first = instruction.strip().split(maxsplit=1)
        return bool(first and first[0].upper() in KNOWN_PROTECTED_FIELDS)

    def _classify_text(self, node, text, story, fields, relationships):
        diagnostics: list[Diagnostic] = []
        context = self._context(node, relationships)
        if story == "comments":
            return CoverageClassification.EXCLUDED_BY_POLICY, context, diagnostics
        if self._ancestor(node, qn("w", "del")) is not None:
            diagnostics.append(Diagnostic(code="TRACKED_DELETION_EXCLUDED", severity="warning", message="Deleted revision text is counted as EXCLUDED_BY_POLICY, not current translatable content."))
            return CoverageClassification.EXCLUDED_BY_POLICY, context, diagnostics
        if fields is not None:
            if fields["known"]:
                context["field_instruction"] = str(fields["instruction"])
                return CoverageClassification.PROTECTED, context, diagnostics
            diagnostics.append(Diagnostic(code="UNKNOWN_FIELD_RESULT", severity="warning", message="Displayed text from an unknown field cannot be safely classified as normal translation text."))
            context["field_instruction"] = str(fields["instruction"])
            return CoverageClassification.PROTECTED, context, diagnostics
        if URL_TOKEN.search(text):
            return CoverageClassification.PROTECTED, context, diagnostics
        return CoverageClassification.TRANSLATABLE, context, diagnostics

    def _context(self, node, relationships: dict[str, str]) -> dict[str, str]:
        context: dict[str, str] = {}
        hyperlink = self._ancestor(node, qn("w", "hyperlink"))
        if hyperlink is not None:
            relation_id = hyperlink.get(qn("r", "id"), "")
            context["hyperlink_rid"] = relation_id
            if relation_id in relationships:
                context["hyperlink_target"] = relationships[relation_id]
        content_control = self._ancestor(node, qn("w", "sdt"))
        if content_control is not None:
            context["content_control"] = "true"
            tag = content_control.find("w:sdtPr/w:tag", namespaces=NS)
            if tag is not None:
                context["content_control_tag"] = tag.get(qn("w", "val"), "")
        return context

    def _field_boundary_span(self, document_sha, part_name, story, block_locator, ordinal, field):
        locator = SourceLocator(part_name=part_name, story=story, xml_path=f"{block_locator.xml_path}/field_boundary[{ordinal + 1}]", container_path=block_locator.xml_path, paragraph_ordinal=block_locator.paragraph_ordinal)
        return SpanIR(
            span_uid=stable_uid(document_sha, part_name, locator.xml_path, str(ordinal), "field_boundary"),
            kind="field_boundary",
            locator=locator,
            container_context={key: str(value) for key, value in field.items()},
        )

    def _risk_entries(self, root, part_name: str, story: str, diagnostics: list[Diagnostic]) -> list[CoverageEntry]:
        entries: list[CoverageEntry] = []
        ordinals: dict[str, int] = defaultdict(int)
        for alternate in root.xpath(".//mc:AlternateContent", namespaces=NS):
            fallback = alternate.find("mc:Fallback", namespaces=NS)
            choices = alternate.findall("mc:Choice", namespaces=NS)
            choice_texts = ["".join(choice.xpath(".//w:t/text()", namespaces=NS)) for choice in choices]
            choice_text = "".join(choice_texts)
            fallback_text = "".join(fallback.xpath(".//w:t/text()", namespaces=NS)) if fallback is not None else ""
            if fallback_text and not choice_text:
                diagnostics.append(Diagnostic(code="ALTERNATE_CONTENT_SINGLE_TEXT_BRANCH", severity="warning", message="AlternateContent Choice has no visible text; Fallback is the only extracted text branch.", part=part_name))
                continue
            if fallback_text and fallback_text == choice_text:
                diagnostics.append(Diagnostic(code="ALTERNATE_CONTENT_EQUIVALENT_BRANCHES", severity="warning", message="AlternateContent Choice and Fallback have identical visible text; extracted once.", part=part_name))
                continue
            if not fallback_text and len(choices) == 1 and choice_text:
                diagnostics.append(Diagnostic(code="ALTERNATE_CONTENT_SINGLE_TEXT_BRANCH", severity="warning", message="AlternateContent has one Choice text branch and no Fallback text; extracted once.", part=part_name))
                continue
            entries.append(self._uncertainty(
                part_name, story, "ALTERNATE_CONTENT_UNCERTAIN_EFFECTIVE_BRANCH",
                "Only Fallback is extracted as a baseline; Choice branch is not double-counted because its effective Word branch cannot be proven by M2.",
                choice_text if fallback is not None else fallback_text or choice_text, ordinals,
            ))
        for element in root.xpath(".//w:altChunk | .//m:oMath | .//w:object | .//o:OLEObject | .//w:subDoc", namespaces=NS):
            entries.append(self._uncertainty(part_name, story, "UNSUPPORTED_POTENTIAL_VISIBLE_CONSTRUCT", "This construct can carry visible or rendered content but M2 does not parse it into Document IR.", "", ordinals))
        for drawing in root.xpath(".//w:drawing", namespaces=NS):
            if drawing.xpath(".//w:txbxContent | .//pic:pic", namespaces=NS):
                continue
            entries.append(self._uncertainty(part_name, story, "UNSUPPORTED_DRAWING_SHAPE", "Drawing is not a known picture or w:txbxContent text box; possible shape text is not proven covered.", "", ordinals))
        for shape in root.xpath(".//v:shape", namespaces=NS):
            if shape.xpath(".//w:txbxContent | .//v:imagedata", namespaces=NS):
                continue
            entries.append(self._uncertainty(part_name, story, "UNSUPPORTED_VML_SHAPE", "VML shape is not an image or text box with Word paragraphs; possible visible text is not proven covered.", "", ordinals))
        for text_node in root.xpath(".//w:t | .//w:delText", namespaces=NS):
            if self._ancestor(text_node, qn("w", "p")) is None and not self._in_non_effective_alternate(text_node):
                entries.append(self._uncertainty(part_name, story, "TEXT_OUTSIDE_PARAGRAPH", "Word text is outside an extractable paragraph-like block.", text_node.text or "", ordinals))
        return entries

    def _uncertainty(self, part_name, story, code, message, text, ordinals):
        ordinals[code] += 1
        locator = SourceLocator(part_name=part_name, story=story, xml_path=f"/{part_name}/risk/{code}[{ordinals[code]}]")
        return CoverageEntry(
            classification=CoverageClassification.UNKNOWN_POTENTIAL_VISIBLE_CONTENT,
            locator=locator,
            code=code,
            message=message,
            text_snippet=text[:160],
            character_count=len(text),
        )

    def _in_non_effective_alternate(self, element) -> bool:
        alternate = self._ancestor(element, qn("mc", "AlternateContent"))
        if alternate is None:
            return False
        fallback = alternate.find("mc:Fallback", namespaces=NS)
        choices = alternate.findall("mc:Choice", namespaces=NS)
        fallback_text = "".join(fallback.xpath(".//w:t/text()", namespaces=NS)) if fallback is not None else ""
        choice_texts = ["".join(choice.xpath(".//w:t/text()", namespaces=NS)) for choice in choices]
        current = element
        while current is not None and current is not alternate:
            if current is fallback and fallback_text:
                return False
            if current.tag == qn("mc", "Choice") and not fallback_text and len(choices) == 1 and choice_texts[0]:
                return False
            current = current.getparent()
        return True

    @staticmethod
    def _ancestor(element, tag):
        current = element
        while current is not None:
            if current.tag == tag:
                return current
            current = current.getparent()
        return None

    def _run_fingerprint(self, node) -> str | None:
        run = self._ancestor(node, qn("w", "r"))
        if run is None:
            return None
        properties = run.find("w:rPr", namespaces=NS)
        if properties is None:
            return None
        return sha256(etree.tostring(properties, method="c14n")).hexdigest()
