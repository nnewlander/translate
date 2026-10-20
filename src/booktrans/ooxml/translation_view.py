"""M4.7's minimal projection from M2 source mapping to tagged text segments."""

from collections import Counter, defaultdict
from pathlib import Path
import zipfile

from lxml import etree

from booktrans.domain.enums import CoverageClassification
from booktrans.domain.ids import stable_uid
from booktrans.domain.ir import BlockIR, DocumentIR, SourceLocator, SpanIR
from booktrans.domain.translation_view import (
    FormatAnchor,
    TaggedCapabilityRecord,
    TaggedCapabilityReport,
    EditableTextIsland,
    TranslationSegment,
    TranslationView,
)
from booktrans.ooxml.format_fragment_decision_audit import _classify_fragment, _neighbor_windows, _paragraph_evidence
from booktrans.ooxml.format_unit_audit import WORD as AUDIT_WORD, _is_fragment_candidate, _neighborhoods
from booktrans.ooxml.locator import LocatorResolutionError, RootLocatorIndex, resolve_locator_in_root
from booktrans.ooxml.namespaces import NS, qn
from booktrans.ooxml.parts import read_xml_part
from booktrans.ooxml.structural_islands import equivalent_alternate_paragraphs, inspect_paragraph


# Only observed, translation-visible semantic/container distinctions belong in
# the view.  Spacing and text scale deliberately remain in copied rPr templates.
_SEMANTIC_RPR = {
    "b": "bold",
    "i": "italic",
    "u": "underline",
    "color": "color",
    "highlight": "highlight",
    "rFonts": "font_family",
    "sz": "font_size",
    "rStyle": "character_style",
}


class TranslationViewBuilder:
    """Build views without changing BlockIR/SpanIR or retaining XML objects."""

    def build(self, source_path: Path, document: DocumentIR) -> list[TranslationView]:
        roots: dict[str, etree._Element] = {}
        indexes: dict[str, RootLocatorIndex] = {}
        views: list[TranslationView] = []
        neighbors = _neighbors(document)
        with zipfile.ZipFile(source_path, "r") as package:
            fragment_hints = _m46_fragment_hints(package, roots, indexes, document)
            for block in document.blocks:
                spans = _visible_spans(block)
                if not any(span.classification == CoverageClassification.TRANSLATABLE and span.text for span in spans):
                    continue
                anchors: list[FormatAnchor] = []
                segments: list[TranslationSegment] = []
                paragraph = _resolve(package, roots, indexes, block.locator)
                layout = inspect_paragraph(paragraph, block.unit_uid)
                logical: list[tuple[str, list[str], bool, SourceLocator, dict[str, str], str, str]] = []
                for span in spans:
                    # M2 deliberately coalesces physically adjacent equivalent
                    # nodes into one SpanIR.  Its ``source_run_locators`` keep
                    # the per-node map M4.8 needs to preserve island breaks.
                    for locator in [span.locator, *span.source_run_locators]:
                        node = _resolve(package, roots, indexes, locator)
                        text = node.text or ""
                        kinds = _semantic_kinds(node, span)
                        protected = span.classification == CoverageClassification.PROTECTED
                        kind = "protected" if protected else ("anchored" if kinds else "plain")
                        context = dict(span.container_context)
                        # lxml may expose an equivalent element through another
                        # proxy, so use its tree path rather than object identity.
                        island_ref = layout.node_path_to_island.get(
                            node.getroottree().getpath(node), "island.unsupported"
                        )
                        merge_key = (kind, tuple(kinds), tuple(sorted(context.items())), island_ref)
                        if logical and logical[-1][0:3] == (kind, kinds, protected) and tuple(sorted(logical[-1][4].items())) == merge_key[2] and logical[-1][6] == island_ref:
                            previous = logical[-1]
                            logical[-1] = (previous[0], previous[1], previous[2], previous[3], previous[4], previous[5] + text, previous[6])
                        else:
                            logical.append((kind, kinds, protected, locator, context, text, island_ref))
                for ordinal, (kind, kinds, protected, locator, context, text, island_ref) in enumerate(logical, start=1):
                    anchor_ref = None
                    if kinds or protected:
                        anchor_ref = stable_uid(block.unit_uid, str(ordinal), "format-anchor")
                        if protected:
                            kinds = [*kinds, "protected"]
                        anchors.append(FormatAnchor(
                            anchor_id=anchor_ref,
                            kinds=kinds,
                            template_locator=locator,
                            container_context=context,
                            protected=protected,
                        ))
                    segments.append(TranslationSegment(
                        segment_id=stable_uid(block.unit_uid, str(ordinal), "view-segment"),
                        text=text,
                        kind=kind,
                        anchor_ref=anchor_ref,
                        island_ref=island_ref,
                        template_locator=locator,
                        container_context=context,
                    ))
                previous, following = neighbors[block.unit_uid]
                views.append(TranslationView(
                    unit_uid=block.unit_uid,
                    display_id=block.display_id,
                    source_text=block.source_plain_text,
                    segments=segments,
                    text_islands=[EditableTextIsland(
                        island_id=island.island_id,
                        segment_ids=[segment.segment_id for segment in segments if segment.island_ref == island.island_id],
                    ) for island in layout.islands if any(segment.island_ref == island.island_id for segment in segments)],
                    structural_anchors=layout.anchors,
                    anchors=anchors,
                    base_template_locator=_base_template(segments),
                    continuation_hint=block.unit_uid in fragment_hints,
                    previous_text=previous,
                    next_text=following,
                ))
        return views


class TaggedCapabilityAudit:
    """Conservative structural gate for the small M4.7 tagged projector."""

    def audit(self, source_path: Path, document: DocumentIR, views: list[TranslationView]) -> TaggedCapabilityReport:
        records: list[TaggedCapabilityRecord] = []
        blocks = {block.unit_uid: block for block in document.blocks}
        roots: dict[str, etree._Element] = {}
        indexes: dict[str, RootLocatorIndex] = {}
        with zipfile.ZipFile(source_path, "r") as package:
            for view in views:
                block = blocks[view.unit_uid]
                reasons = self._reasons(package, roots, indexes, block, view)
                records.append(TaggedCapabilityRecord(
                    unit_uid=view.unit_uid,
                    display_id=view.display_id,
                    part_name=block.part_name,
                    locator=block.locator,
                    status="TAGGED_UNSUPPORTED" if reasons else "TAGGED_SUPPORTED",
                    reasons=sorted(reasons),
                ))
        counts = Counter(reason for record in records for reason in record.reasons)
        samples: dict[str, list[TaggedCapabilityRecord]] = defaultdict(list)
        for record in records:
            for reason in record.reasons:
                if len(samples[reason]) < 5:
                    samples[reason].append(record)
        supported = sum(record.status == "TAGGED_SUPPORTED" for record in records)
        return TaggedCapabilityReport(
            total_translatable_blocks=len(records),
            tagged_supported_blocks=supported,
            tagged_unsupported_blocks=len(records) - supported,
            tagged_supported_percentage=round((supported / len(records) * 100) if records else 0.0, 2),
            reason_counts=dict(sorted(counts.items())),
            reason_samples=dict(sorted(samples.items())),
            records=records,
        )

    def _reasons(self, package, roots, indexes, block: BlockIR, view: TranslationView) -> set[str]:
        reasons: set[str] = set()
        visible = _visible_spans(block)
        if any(span.classification not in {CoverageClassification.TRANSLATABLE, CoverageClassification.PROTECTED} for span in visible):
            reasons.add("NON_PROJECTABLE_SCOPE")
        if any(span.kind == "field_boundary" for span in block.spans):
            reasons.add("FIELD_CONTEXT")
        try:
            paragraph = _resolve(package, roots, indexes, block.locator)
        except (LocatorResolutionError, etree.XMLSyntaxError, ValueError):
            return {"LOCATOR_UNRESOLVABLE"}
        layout = inspect_paragraph(paragraph, block.unit_uid)
        reasons.update(f"STRUCTURAL_{subtype.upper()}" for subtype in layout.unsupported_subtypes)
        # Logical view segments deliberately collapse physical run fragmentation.
        # Safety still checks every underlying source span before allowing a
        # paragraph replacement.
        for span in _visible_spans(block):
            for locator in [span.locator, *span.source_run_locators]:
                try:
                    node = _resolve(package, roots, indexes, locator)
                except (LocatorResolutionError, etree.XMLSyntaxError, ValueError):
                    reasons.add("LOCATOR_UNRESOLVABLE")
                    continue
                run = _ancestor(node, qn("w", "r"))
                # ``inspect_paragraph`` owns the physical-run safety decision.
                # The prior M4.7 all-text-run guard would reject a paragraph even
                # when a non-text sibling is now an immutable structural anchor.
                if _ancestor(node, qn("mc", "AlternateContent")) is not None and equivalent_alternate_paragraphs(paragraph) is None:
                    reasons.add("ALTERNATE_MULTI_NODE")
                parent = run.getparent() if run is not None else None
                if parent is not paragraph and (parent is None or parent.tag != qn("w", "hyperlink")):
                    reasons.add("UNKNOWN_SEMANTIC_CONTAINER")
                if node.getroottree().getpath(node) not in layout.node_path_to_island:
                    reasons.add("STRUCTURAL_TEXT_OUTSIDE_EDITABLE_ISLAND")
        return reasons


def _visible_spans(block: BlockIR) -> list[SpanIR]:
    return [span for span in block.spans if span.kind == "text" and span.text]


def _resolve(package, roots, indexes, locator: SourceLocator) -> etree._Element:
    root = roots.get(locator.part_name)
    if root is None:
        root = read_xml_part(package, locator.part_name)
        if root is None:
            raise ValueError(f"VIEW_LOCATOR_PART_NOT_XML: {locator.part_name}")
        roots[locator.part_name] = root
        indexes[locator.part_name] = RootLocatorIndex(root)
    return resolve_locator_in_root(root, locator, index=indexes[locator.part_name])


def _semantic_kinds(node: etree._Element, span: SpanIR) -> list[str]:
    kinds: list[str] = []
    run = _ancestor(node, qn("w", "r"))
    properties = run.find("w:rPr", namespaces=NS) if run is not None else None
    if properties is not None:
        for property_node in properties:
            local = etree.QName(property_node).localname
            semantic = _SEMANTIC_RPR.get(local)
            if semantic and _property_enabled(property_node):
                kinds.append(semantic)
    if span.container_context.get("hyperlink_rid"):
        kinds.append("hyperlink")
    return kinds


def _property_enabled(node: etree._Element) -> bool:
    value = node.get(qn("w", "val"))
    return value not in {"0", "false", "False", "off"}


def _base_template(segments: list[TranslationSegment]) -> SourceLocator:
    for segment in segments:
        if segment.kind == "plain":
            return segment.template_locator
    return segments[0].template_locator


def _neighbors(document: DocumentIR) -> dict[str, tuple[str | None, str | None]]:
    grouped: dict[tuple[str, str], list[BlockIR]] = defaultdict(list)
    for block in document.blocks:
        grouped[(block.story, block.part_name)].append(block)
    result: dict[str, tuple[str | None, str | None]] = {}
    for blocks in grouped.values():
        for index, block in enumerate(blocks):
            previous = next((item.source_plain_text for item in reversed(blocks[:index]) if item.source_plain_text), None)
            following = next((item.source_plain_text for item in blocks[index + 1:] if item.source_plain_text), None)
            result[block.unit_uid] = (previous, following)
    return result


def _m46_fragment_hints(package, roots, indexes, document: DocumentIR) -> set[str]:
    """Reuse M4.6's evidence policy; a view hint must not be a broader guess."""
    neighborhoods = _neighborhoods(document)
    windows = _neighbor_windows(document)
    hints: set[str] = set()
    for block in document.blocks:
        words = len(AUDIT_WORD.findall(block.source_plain_text))
        if not _is_fragment_candidate(block, block.source_plain_text, words):
            continue
        _, evidence = _paragraph_evidence(package, roots, indexes, block)
        previous, following = neighborhoods[block.unit_uid]
        previous2, next2 = windows[block.unit_uid]
        decision, _ = _classify_fragment(block.source_plain_text, previous, following, previous2, next2, evidence)
        if decision == "LIKELY_TRUE_PROSE_FRAGMENT":
            hints.add(block.unit_uid)
    return hints


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None
