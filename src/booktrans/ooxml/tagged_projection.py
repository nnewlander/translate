"""Minimal M4.7 tagged text projection for structurally approved paragraphs."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.ir import SourceLocator
from booktrans.domain.models import Diagnostic
from booktrans.domain.roundtrip import PatchReport
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.ooxml.locator import LocatorResolutionError, RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.structural_islands import equivalent_alternate_paragraphs, inspect_paragraph


class TaggedProjectionError(RuntimeError):
    """The approved view cannot be projected without touching unknown structure."""


@dataclass(frozen=True)
class TaggedProjection:
    view: TranslationView
    candidate: TaggedSyntheticCandidate


def apply_tagged_projections(
    source_docx: Path,
    candidate_docx: Path,
    projections: list[TaggedProjection],
) -> PatchReport:
    """Copy source package and replace only approved paragraph text containers.

    The function intentionally rebuilds runs/hyperlink wrappers *inside* an
    approved paragraph, while retaining paragraph properties and every other
    package entry.  Capability gating has already rejected fields, alternate
    branches and non-text structural children.
    """
    if candidate_docx.resolve() == source_docx.resolve():
        raise TaggedProjectionError("CANDIDATE_PATH_EQUALS_SOURCE: refusing to overwrite immutable source DOCX.")
    candidate_docx.parent.mkdir(parents=True, exist_ok=True)
    by_part: dict[str, list[TaggedProjection]] = defaultdict(list)
    for projection in projections:
        by_part[projection.view.base_template_locator.part_name].append(projection)
    report = PatchReport(
        planned_blocks=len(projections),
        planned_spans=sum(len(item.candidate.target_segments) for item in projections),
        planned_text_nodes=sum(len(item.candidate.target_segments) for item in projections),
        touched_ooxml_parts=sorted(by_part),
    )
    with zipfile.ZipFile(source_docx, "r") as source, zipfile.ZipFile(candidate_docx, "w") as destination:
        names = set(source.namelist())
        for part in sorted(set(by_part) - names):
            for projection in by_part[part]:
                report.resolution_failures.append(_diagnostic(
                    "LOCATOR_PART_MISSING", "blocker", "Tagged projection refers to a missing OOXML part.", part,
                    projection.view.base_template_locator.xml_path,
                ))
        for info in source.infolist():
            payload = source.read(info.filename)
            part_projections = by_part.get(info.filename, [])
            if part_projections:
                root = etree.fromstring(payload)
                index = RootLocatorIndex(root)
                for projection in part_projections:
                    try:
                        attempted, verified = _project_one(root, index, projection)
                        report.locators_resolved += 1
                        report.writes_attempted += attempted
                        report.writes_verified += verified
                    except (LocatorResolutionError, TaggedProjectionError) as exc:
                        report.resolution_failures.append(_diagnostic(
                            "TAGGED_PROJECTION_FAILED", "blocker",
                            "Could not project an approved tagged target into its OOXML paragraph.",
                            info.filename, f"locator={projection.view.base_template_locator.xml_path}; detail={exc}",
                        ))
                payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            destination.writestr(info, payload)
    return report


def _project_one(root: etree._Element, index: RootLocatorIndex, projection: TaggedProjection) -> tuple[int, int]:
    view = projection.view
    candidate = projection.candidate
    paragraph = resolve_locator_in_root(root, _block_locator(view), index=index)
    alternate_paragraphs = equivalent_alternate_paragraphs(paragraph)
    if _ancestor(paragraph, qn("mc", "AlternateContent")) is not None and alternate_paragraphs is None:
        raise TaggedProjectionError("UNPROVEN_ALTERNATE_CONTENT_MAPPING")
    layout = inspect_paragraph(paragraph, view.unit_uid)
    if layout.unsupported_subtypes:
        raise TaggedProjectionError(f"UNSUPPORTED_ISLAND_STRUCTURE: {sorted(layout.unsupported_subtypes)}")
    if [(anchor.kind, anchor.fingerprint, anchor.relative_position) for anchor in layout.anchors] != [
        (anchor.kind, anchor.fingerprint, anchor.relative_position) for anchor in view.structural_anchors
    ]:
        raise TaggedProjectionError("STRUCTURAL_ANCHOR_SOURCE_MISMATCH")
    anchor_templates = {anchor.anchor_id: anchor.template_locator for anchor in view.anchors}
    targets_by_island: dict[str, list[StructuredTargetSegment]] = defaultdict(list)
    for segment in candidate.target_segments:
        targets_by_island[segment.island_ref].append(segment)
    attempted = verified = 0
    expected_islands = [island.island_id for island in layout.islands if island.text_nodes]
    if set(targets_by_island) != set(expected_islands):
        raise TaggedProjectionError(f"ISLAND_TARGET_SET_MISMATCH: expected={expected_islands}; actual={sorted(targets_by_island)}")
    for island in layout.islands:
        targets = targets_by_island.get(island.island_id)
        if not targets:
            continue
        replacement: list[etree._Element] = []
        hyperlink_parent = _hyperlink_parent_for_island(island.children)
        for segment in targets:
            template_locator = _template_for(segment, view, anchor_templates)
            node = resolve_locator_in_root(root, template_locator, index=index)
            built = _build_run(node, segment.text, generated=segment.kind != "protected") if hyperlink_parent is not None else _build_container(node, segment.text, generated=segment.kind != "protected")
            replacement.append(built)
            attempted += 1
            text_node = built.xpath(".//w:t", namespaces=NS)[0]
            verified += int((text_node.text or "") == segment.text)
        container = hyperlink_parent if hyperlink_parent is not None else paragraph
        insertion_index = list(container).index(island.children[0])
        for child in island.children:
            container.remove(child)
        for offset, child in enumerate(replacement):
            container.insert(insertion_index + offset, child)
    # Equivalent AlternateContent Choice/Fallback paragraphs are a single
    # source location with two rendering branches.  The proof above guarantees
    # matching skeletons and source text; copy only the now-projected paragraph
    # children so both branches retain their own enclosing markup.
    if alternate_paragraphs is not None:
        for counterpart in alternate_paragraphs:
            if counterpart is paragraph:
                continue
            for child in list(counterpart):
                counterpart.remove(child)
            for child in paragraph:
                counterpart.append(deepcopy(child))
    return attempted, verified


def _block_locator(view: TranslationView) -> SourceLocator:
    """All segment locators carry the paragraph path in ``container_path``."""
    template = view.base_template_locator
    if not template.container_path:
        raise TaggedProjectionError("VIEW_BASE_TEMPLATE_HAS_NO_PARAGRAPH_CONTAINER")
    return SourceLocator(
        part_name=template.part_name,
        story=template.story,
        xml_path=template.container_path,
        paragraph_ordinal=template.paragraph_ordinal,
    )


def _template_for(
    segment: StructuredTargetSegment,
    view: TranslationView,
    anchor_templates: dict[str, SourceLocator],
) -> SourceLocator:
    if segment.kind == "plain":
        if segment.anchor_ref is not None:
            raise TaggedProjectionError("PLAIN_SEGMENT_HAS_ANCHOR_REF")
        return view.base_template_locator
    if not segment.anchor_ref or segment.anchor_ref not in anchor_templates:
        raise TaggedProjectionError("UNKNOWN_OR_MISSING_ANCHOR_REF")
    return anchor_templates[segment.anchor_ref]


def _build_container(template_text: etree._Element, target_text: str, *, generated: bool) -> etree._Element:
    new_run = _build_run(template_text, target_text, generated=generated)
    hyperlink = _ancestor(template_text, qn("w", "hyperlink"))
    if hyperlink is None:
        return new_run
    new_hyperlink = etree.Element(qn("w", "hyperlink"), attrib=dict(hyperlink.attrib))
    new_hyperlink.append(new_run)
    return new_hyperlink


def _build_run(template_text: etree._Element, target_text: str, *, generated: bool) -> etree._Element:
    run = _ancestor(template_text, qn("w", "r"))
    if run is None:
        raise TaggedProjectionError("TEMPLATE_RUN_MISSING")
    new_run = etree.Element(qn("w", "r"))
    properties = run.find("w:rPr", namespaces=NS)
    if properties is not None:
        copied = deepcopy(properties)
        if generated and _contains_cjk(target_text):
            for micro_layout in list(copied):
                if micro_layout.tag in {qn("w", "spacing"), qn("w", "w")}:
                    copied.remove(micro_layout)
        new_run.append(copied)
    text_node = etree.SubElement(new_run, qn("w", "t"))
    text_node.text = target_text
    if target_text[:1].isspace() or target_text[-1:].isspace():
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return new_run


def _hyperlink_parent_for_island(children: list[etree._Element]) -> etree._Element | None:
    """Return a single direct hyperlink parent, or fail closed.

    The writer may replace runs inside a hyperlink only when the island is
    wholly made of its direct child runs.  This leaves the hyperlink container,
    its relationship/anchor attributes, and every non-text child (notably a
    tab) untouched.
    """
    if not children:
        return None
    parent = children[0].getparent()
    if parent is None or parent.tag != qn("w", "hyperlink"):
        return None
    if any(child.getparent() is not parent or child.tag != qn("w", "r") for child in children):
        raise TaggedProjectionError("HYPERLINK_ISLAND_PARENT_MISMATCH")
    return parent


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff" for character in text)


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def _diagnostic(code: str, severity: str, message: str, part: str | None, detail: str) -> Diagnostic:
    return Diagnostic(code=code, severity=severity, message=message, part=part, detail=detail)
