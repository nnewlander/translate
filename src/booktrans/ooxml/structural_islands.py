"""M4.8 paragraph text-island discovery and immutable structural anchors."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.ids import stable_uid
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuralAnchor, StructuralCapabilityReport, StructuralSubtypeEvidence
from booktrans.domain.translation_view import TaggedCapabilityRecord
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.locator import RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.parts import read_xml_part


_TEXT_RUN_CHILDREN = {qn("w", "rPr"), qn("w", "t")}
_PPR = qn("w", "pPr")
_RUN = qn("w", "r")
_HYPERLINK = qn("w", "hyperlink")
_STRUCTURAL_RUN_KINDS = {
    qn("w", "tab"): "tab",
    qn("w", "br"): "break",
    qn("w", "drawing"): "drawing",
    qn("w", "pict"): "vml_pict",
}
_STRUCTURAL_PARAGRAPH_KINDS = {
    qn("w", "bookmarkStart"): "bookmark_start",
    qn("w", "bookmarkEnd"): "bookmark_end",
    qn("w", "proofErr"): "proof_error",
}


@dataclass
class IslandChunk:
    island_id: str
    children: list[etree._Element] = field(default_factory=list)
    text_nodes: list[etree._Element] = field(default_factory=list)


@dataclass
class ParagraphIslandLayout:
    islands: list[IslandChunk] = field(default_factory=list)
    anchors: list[StructuralAnchor] = field(default_factory=list)
    unsupported_subtypes: set[str] = field(default_factory=set)
    # Observed, non-authoritative facts.  Consumers retain their own
    # capability decisions; a structural fact is not permission to write it.
    features: set[str] = field(default_factory=set)
    node_to_island: dict[etree._Element, str] = field(default_factory=dict)
    node_path_to_island: dict[str, str] = field(default_factory=dict)


def inspect_paragraph(paragraph: etree._Element, unit_uid: str) -> ParagraphIslandLayout:
    """Find replaceable direct-child text groups without moving structural XML."""
    layout = ParagraphIslandLayout()
    current: IslandChunk | None = None
    anchor_position = 0

    def ensure_island() -> IslandChunk:
        nonlocal current
        if current is None:
            current = IslandChunk(island_id=f"island.{len(layout.islands) + 1:03d}")
            layout.islands.append(current)
        return current

    def flush_island() -> None:
        nonlocal current
        current = None

    for child in paragraph:
        if child.tag == _PPR:
            continue
        if child.tag == _RUN:
            classification = _run_classification(child)
            if classification == "text":
                island = ensure_island()
                island.children.append(child)
                for text in child.findall("w:t", namespaces=NS):
                    island.text_nodes.append(text)
                    layout.node_to_island[text] = island.island_id
                    layout.node_path_to_island[_node_path(text)] = island.island_id
                continue
            if classification in {"tab", "break", "drawing", "vml_pict"}:
                flush_island()
                anchor_position += 1
                layout.anchors.append(_anchor(unit_uid, classification, child, anchor_position))
                continue
            if classification == "alternate_content_run" and _preservable_alternate_content_run(child):
                # This run has no text in the outer paragraph.  Its alternate
                # branches may contain independently extracted textbox
                # paragraphs, but the run itself is an immutable structural
                # sibling and can be retained while adjacent text is written.
                flush_island()
                anchor_position += 1
                layout.anchors.append(_anchor(unit_uid, classification, child, anchor_position))
                continue
            layout.unsupported_subtypes.add(classification)
            continue
        if child.tag == _HYPERLINK:
            if _hyperlink_is_text_only(child):
                island = ensure_island()
                island.children.append(child)
                for text in child.xpath(".//w:t", namespaces=NS):
                    island.text_nodes.append(text)
                    layout.node_to_island[text] = island.island_id
                    layout.node_path_to_island[_node_path(text)] = island.island_id
                continue
            if _hyperlink_is_text_and_tab_only(child):
                # A tab inside a hyperlink is still a fixed structural
                # delimiter.  Describe the surrounding editable text
                # coherently, but leave consumers to decide whether they can
                # safely project this structure.
                layout.features.add("hyperlink_with_tab")
                for hyperlink_child in child:
                    classification = _run_classification(hyperlink_child)
                    if classification == "text":
                        island = ensure_island()
                        island.children.append(hyperlink_child)
                        for text in hyperlink_child.findall("w:t", namespaces=NS):
                            island.text_nodes.append(text)
                            layout.node_to_island[text] = island.island_id
                            layout.node_path_to_island[_node_path(text)] = island.island_id
                        continue
                    # _hyperlink_is_text_and_tab_only proved this is a tab.
                    flush_island()
                    anchor_position += 1
                    layout.anchors.append(_anchor(unit_uid, "tab", hyperlink_child, anchor_position))
                continue
            layout.unsupported_subtypes.add("hyperlink_nontext_structure")
            continue
        kind = _STRUCTURAL_PARAGRAPH_KINDS.get(child.tag)
        if kind:
            flush_island()
            anchor_position += 1
            layout.anchors.append(_anchor(unit_uid, kind, child, anchor_position))
            continue
        if child.tag == qn("mc", "AlternateContent"):
            layout.unsupported_subtypes.add("alternate_content")
        elif child.tag == qn("w", "fldSimple"):
            layout.unsupported_subtypes.add("field_composite")
        else:
            layout.unsupported_subtypes.add(f"paragraph_child_{etree.QName(child).localname}")
    return layout


def structural_capability_report(
    blocks: list[BlockIR],
    layouts: dict[str, ParagraphIslandLayout],
    *,
    previous_supported: int,
    previous_unsupported: int,
) -> StructuralCapabilityReport:
    by_subtype: dict[str, list[BlockIR]] = defaultdict(list)
    supported_by_subtype: dict[str, list[BlockIR]] = defaultdict(list)
    for block in blocks:
        layout = layouts.get(block.unit_uid)
        if layout is None:
            continue
        for anchor in layout.anchors:
            supported_by_subtype[f"STRUCTURAL_ANCHOR_{anchor.kind.upper()}"] .append(block)
        for subtype in layout.unsupported_subtypes:
            by_subtype[subtype].append(block)
    rows: list[StructuralSubtypeEvidence] = []
    for subtype, records in sorted(supported_by_subtype.items(), key=lambda item: (-len({block.unit_uid for block in item[1]}), item[0])):
        rows.append(StructuralSubtypeEvidence(
            subtype=subtype,
            affected_blocks=len({block.unit_uid for block in records}),
            safely_supported=True,
            reason="Node is preserved byte-for-byte in place while adjacent editable text islands are projected.",
            samples=[_sample(block, status="TAGGED_SUPPORTED") for block in _unique_blocks(records)[:5]],
        ))
    for subtype, records in sorted(by_subtype.items(), key=lambda item: (-len({block.unit_uid for block in item[1]}), item[0])):
        rows.append(StructuralSubtypeEvidence(
            subtype=subtype,
            affected_blocks=len({block.unit_uid for block in records}),
            safely_supported=False,
            reason="Current thin island boundary cannot prove safe mutation without altering or redistributing this structure.",
            samples=[_sample(block, status="TAGGED_UNSUPPORTED") for block in _unique_blocks(records)[:5]],
        ))
    return StructuralCapabilityReport(
        previous_tagged_supported_blocks=previous_supported,
        previous_tagged_unsupported_blocks=previous_unsupported,
        subtype_evidence=rows,
    )


def analyze_structural_capability(
    source_path: Path,
    blocks: list[BlockIR],
) -> StructuralCapabilityReport:
    """Read source XML once and expose the actual M4.7 structural blockers."""
    roots: dict[str, etree._Element] = {}
    indexes: dict[str, RootLocatorIndex] = {}
    layouts: dict[str, ParagraphIslandLayout] = {}
    legacy_unsafe_units: set[str] = set()
    with zipfile.ZipFile(source_path, "r") as package:
        for block in blocks:
            root = roots.get(block.part_name)
            if root is None:
                root = read_xml_part(package, block.part_name)
                if root is None:
                    continue
                roots[block.part_name] = root
                indexes[block.part_name] = RootLocatorIndex(root)
            paragraph = resolve_locator_in_root(root, block.locator, index=indexes[block.part_name])
            layout = inspect_paragraph(paragraph, block.unit_uid)
            layouts[block.unit_uid] = layout
            # M4.7 rejected every non-text sibling/direct paragraph child,
            # which corresponds exactly to anchors and thin-model unsupported
            # subtypes here.  AlternateContent remains separately unsafe.
            if layout.anchors or layout.unsupported_subtypes or _inside_alternate(paragraph):
                legacy_unsafe_units.add(block.unit_uid)
    return structural_capability_report(
        blocks,
        layouts,
        previous_supported=len(blocks) - len(legacy_unsafe_units),
        previous_unsupported=len(legacy_unsafe_units),
    )


def _run_classification(run: etree._Element) -> str:
    children = [child for child in run if child.tag != qn("w", "rPr")]
    if not children:
        return "empty_run"
    if all(child.tag == qn("w", "t") for child in children):
        return "text"
    kinds = {_STRUCTURAL_RUN_KINDS.get(child.tag) for child in children}
    if len(children) == 1 and None not in kinds:
        return next(iter(kinds))
    if len(children) == 1 and children[0].tag == qn("mc", "AlternateContent"):
        return "alternate_content_run"
    if any(child.tag == qn("w", "t") for child in children):
        return "mixed_text_structural_run"
    return "run_nontext_structure"


def _node_path(node: etree._Element) -> str:
    """Stable within one parsed part, unlike lxml proxy object identity."""
    return node.getroottree().getpath(node)


def _inside_alternate(node: etree._Element) -> bool:
    current = node.getparent()
    while current is not None:
        if current.tag == qn("mc", "AlternateContent"):
            return True
        current = current.getparent()
    return False


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def equivalent_alternate_paragraphs(paragraph: etree._Element) -> list[etree._Element] | None:
    """Return provably parallel AlternateContent branch paragraphs.

    This intentionally recognizes only a Choice/Fallback pair with the same
    paragraph ordinal, identical pre-write text sequence, and an identical
    paragraph skeleton after text payload is blanked.  Anything less is not a
    writable AlternateContent mapping.
    """
    alternate = _ancestor(paragraph, qn("mc", "AlternateContent"))
    if alternate is None:
        return None
    branch = _alternate_branch(paragraph, alternate)
    if branch is None:
        return None
    branches = [child for child in alternate if child.tag in {qn("mc", "Choice"), qn("mc", "Fallback")}]
    if len(branches) != 2 or {child.tag for child in branches} != {qn("mc", "Choice"), qn("mc", "Fallback")}:
        return None
    own = branch.xpath(".//w:p", namespaces=NS)
    try:
        ordinal = own.index(paragraph)
    except ValueError:
        return None
    candidates: list[etree._Element] = []
    for candidate_branch in branches:
        paragraphs = candidate_branch.xpath(".//w:p", namespaces=NS)
        if len(paragraphs) != len(own) or ordinal >= len(paragraphs):
            return None
        candidates.append(paragraphs[ordinal])
    reference = _alternate_paragraph_signature(candidates[0])
    if any(_alternate_paragraph_signature(candidate) != reference for candidate in candidates[1:]):
        return None
    return candidates


def _alternate_branch(paragraph: etree._Element, alternate: etree._Element) -> etree._Element | None:
    current = paragraph.getparent()
    while current is not None and current is not alternate:
        if current.tag in {qn("mc", "Choice"), qn("mc", "Fallback")} and current.getparent() is alternate:
            return current
        current = current.getparent()
    return None


def _alternate_paragraph_signature(paragraph: etree._Element) -> tuple[bytes, tuple[str, ...]]:
    copied = deepcopy(paragraph)
    texts = tuple(node.text or "" for node in copied.xpath(".//w:t", namespaces=NS))
    for node in copied.xpath(".//w:t", namespaces=NS):
        node.text = ""
    return etree.tostring(copied, method="c14n"), texts


def _hyperlink_is_text_only(hyperlink: etree._Element) -> bool:
    return len(hyperlink) > 0 and all(
        child.tag == _RUN and _run_classification(child) == "text" for child in hyperlink
    )


def _hyperlink_is_text_and_tab_only(hyperlink: etree._Element) -> bool:
    """Recognize the one additional safe structural fact, not writer support."""
    if not len(hyperlink):
        return False
    classifications = [_run_classification(child) if child.tag == _RUN else "other" for child in hyperlink]
    return "tab" in classifications and all(value in {"text", "tab"} for value in classifications)


def _anchor(unit_uid: str, kind: str, element: etree._Element, position: int) -> StructuralAnchor:
    fingerprint = _structural_fingerprint(element, kind)
    return StructuralAnchor(
        anchor_id=stable_uid(unit_uid, kind, str(position), fingerprint),
        kind=kind,
        fingerprint=fingerprint,
        relative_position=position,
    )


def _structural_fingerprint(element: etree._Element, kind: str) -> str:
    """Fingerprint anchor identity without treating nested textbox text as anchor payload.

    A drawing/Pict anchor may contain an independently extracted ``w:txbxContent``
    paragraph.  Its text legitimately changes under its own projection, while the
    outer drawing relationship and position must remain fixed.
    """
    copied = etree.fromstring(etree.tostring(element))
    if kind in {"drawing", "vml_pict", "alternate_content_run"}:
        for content in copied.xpath(".//w:txbxContent", namespaces=NS):
            parent = content.getparent()
            if parent is not None:
                parent.remove(content)
    return sha256(etree.tostring(copied, method="c14n")).hexdigest()


def _preservable_alternate_content_run(run: etree._Element) -> bool:
    """Permit only a pure AlternateContent container as an outer anchor.

    Text directly owned by the outer run would make it editable content and is
    deliberately rejected.  Textbox text is independently located and is not
    part of this outer paragraph's write boundary.
    """
    alternate = run.find("mc:AlternateContent", namespaces=NS)
    if alternate is None:
        return False
    return not alternate.xpath(".//w:t[not(ancestor::w:txbxContent)]", namespaces=NS)


def _unique_blocks(blocks: list[BlockIR]) -> list[BlockIR]:
    seen: set[str] = set()
    result: list[BlockIR] = []
    for block in blocks:
        if block.unit_uid not in seen:
            seen.add(block.unit_uid)
            result.append(block)
    return result


def _sample(block: BlockIR, *, status: str) -> TaggedCapabilityRecord:
    return TaggedCapabilityRecord(
        unit_uid=block.unit_uid,
        display_id=block.display_id,
        part_name=block.part_name,
        locator=block.locator,
        status=status,
    )
