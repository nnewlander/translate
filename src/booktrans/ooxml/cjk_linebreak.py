"""Narrow, paragraph-local CJK line-break policy for proven visual failures.

This is deliberately not a typography engine.  Callers must provide an
explicit closed-world list of already translated, normal-flow prose blocks.
The writer changes only paragraph properties: it never changes text, runs,
relationships, shapes, or document-wide settings.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import re
import zipfile

from lxml import etree

from booktrans.domain.ir import SourceLocator
from booktrans.ooxml.locator import RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_URL_RE = re.compile(r"(?:https?|ftp)://\S+|www\.\S+", re.IGNORECASE)


@dataclass(frozen=True)
class CJKLineBreakTarget:
    """One specifically audited paragraph eligible for the policy."""

    display_id: str
    locator: SourceLocator
    content_role: str
    target_text: str
    observed_issues: tuple[str, ...] = ()


@dataclass
class CJKLineBreakReport:
    planned: int = 0
    applied: list[dict[str, object]] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    touched_ooxml_parts: list[str] = field(default_factory=list)


class CJKLineBreakPolicyError(RuntimeError):
    pass


def apply_cjk_line_break_policy(
    input_docx: Path,
    output_docx: Path,
    targets: list[CJKLineBreakTarget],
) -> CJKLineBreakReport:
    """Apply the visually validated paragraph-local ``kinsoku=true`` setting.

    ``kinsoku=true`` applies the OOXML Simplified-Chinese first/last-character
    rules to the paragraph, including the prohibition on a line starting with
    U+3002 (。).  Earlier M5.2d experimentation also wrote ``wordWrap=false``;
    real Word/PDF evidence showed it did *not* stop mixed-script token splits,
    so it is deliberately no longer a production mutation.  The policy remains
    restricted to an audit-provided closed world of normal-flow CJK prose.
    """
    if input_docx.resolve() == output_docx.resolve():
        raise CJKLineBreakPolicyError("CANDIDATE_PATH_EQUALS_INPUT")
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    by_part: dict[str, list[CJKLineBreakTarget]] = defaultdict(list)
    seen_locators: set[tuple[str, str]] = set()
    for target in targets:
        key = (target.locator.part_name, target.locator.xml_path)
        if key in seen_locators:
            raise CJKLineBreakPolicyError(
                f"DUPLICATE_CJK_LINEBREAK_TARGET: {target.display_id} at {target.locator.xml_path}"
            )
        seen_locators.add(key)
        by_part[target.locator.part_name].append(target)
    report = CJKLineBreakReport(planned=len(targets), touched_ooxml_parts=sorted(by_part))

    # Validate the complete closed-world set before creating any candidate.
    # A rejected URL/code/header target must not leave a partial DOCX behind.
    with zipfile.ZipFile(input_docx, "r") as source:
        for part_name, part_targets in by_part.items():
            if part_name not in source.namelist():
                raise CJKLineBreakPolicyError(f"CJK_LINEBREAK_PART_MISSING: {part_name}")
            root = etree.fromstring(source.read(part_name))
            index = RootLocatorIndex(root)
            for target in part_targets:
                paragraph = resolve_locator_in_root(root, target.locator, index=index)
                rejection = _ineligible_reason(paragraph, target)
                if rejection:
                    report.rejected.append({"display_id": target.display_id, "reason": rejection})
        if report.rejected:
            details = "; ".join(f"{item['display_id']}={item['reason']}" for item in report.rejected)
            raise CJKLineBreakPolicyError(f"CJK_LINEBREAK_TARGET_REJECTED: {details}")

    with zipfile.ZipFile(input_docx, "r") as source, zipfile.ZipFile(output_docx, "w") as candidate:
        for info in source.infolist():
            payload = source.read(info.filename)
            part_targets = by_part.get(info.filename, [])
            if part_targets:
                root = etree.fromstring(payload)
                index = RootLocatorIndex(root)
                for target in part_targets:
                    paragraph = resolve_locator_in_root(root, target.locator, index=index)
                    _set_on_off_property(paragraph, "kinsoku", "1")
                    report.applied.append(
                        {
                            "display_id": target.display_id,
                            "locator": target.locator.model_dump(mode="json"),
                            "properties": {"w:kinsoku": "1"},
                            "observed_issues": list(target.observed_issues),
                        }
                    )
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            candidate.writestr(info, payload)

    return report


def _ineligible_reason(paragraph: etree._Element, target: CJKLineBreakTarget) -> str | None:
    if target.locator.story != "body" or target.content_role != "body_paragraph":
        return "NOT_NORMAL_FLOW_BODY_PROSE"
    if not _CJK_RE.search(target.target_text):
        return "TARGET_NOT_CJK_PROSE"
    if _URL_RE.search(target.target_text):
        return "URL_OR_PROTECTED_LITERAL_PRESENT"
    if paragraph.xpath(".//w:hyperlink | .//w:instrText", namespaces=NS):
        return "HYPERLINK_OR_FIELD_CONTEXT"
    if paragraph.xpath(".//w:tab | .//w:br | .//w:drawing | .//w:pict", namespaces=NS):
        return "NON_TEXT_RUN_STRUCTURE"
    return None


def _set_on_off_property(paragraph: etree._Element, local_name: str, value: str) -> None:
    properties = paragraph.find("w:pPr", namespaces=NS)
    if properties is None:
        properties = etree.Element(qn("w", "pPr"))
        paragraph.insert(0, properties)
    property_element = properties.find(f"w:{local_name}", namespaces=NS)
    if property_element is None:
        property_element = etree.SubElement(properties, qn("w", local_name))
    property_element.set(qn("w", "val"), value)
