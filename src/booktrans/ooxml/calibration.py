"""Deterministic, representative M5.0 calibration request selection."""

from collections import defaultdict
from hashlib import sha256

from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, VisualReviewEntry
from booktrans.domain.ir import BlockIR, DocumentIR
from booktrans.domain.translation_view import TaggedCapabilityReport, TranslationView
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit


_STRATA = (
    "heading", "long_body", "body", "list_item", "table", "bold", "italic",
    "color_highlight", "hyperlink", "protected", "continuation", "structural_island",
    "drawing_or_textbox", "short_text", "title_like_heuristic", "multi_anchor",
)


def select_calibration_requests(
    document: DocumentIR,
    views: list[TranslationView],
    capability: TaggedCapabilityReport,
    *,
    sample_size: int = 40,
    verified_footnote_evidence: list[dict[str, object]] | None = None,
) -> list[CalibrationRequest]:
    """Select supported blocks by observed feature strata, then stable fill."""
    blocks = {block.unit_uid: block for block in document.blocks}
    supported = {record.unit_uid for record in capability.records if record.status == "TAGGED_SUPPORTED"}
    candidates = [view for view in views if view.unit_uid in supported]
    labels = {view.unit_uid: _categories(view, blocks[view.unit_uid]) for view in candidates}
    selected: list[TranslationView] = []
    seen: set[str] = set()
    for category in _STRATA:
        options = sorted((view for view in candidates if category in labels[view.unit_uid]), key=_stable_key)
        if options and options[0].unit_uid not in seen:
            selected.append(options[0])
            seen.add(options[0].unit_uid)
    for view in sorted(candidates, key=_stable_key):
        if len(selected) >= sample_size:
            break
        if view.unit_uid not in seen:
            selected.append(view)
            seen.add(view.unit_uid)
    return [
        _request(view, blocks[view.unit_uid], document.document_sha256, labels[view.unit_uid], verified_footnote_evidence)
        for view in selected[:sample_size]
    ]


def visual_review_manifest(requests: list[CalibrationRequest], views: list[TranslationView]) -> list[VisualReviewEntry]:
    by_uid = {view.unit_uid: view for view in views}
    entries: list[VisualReviewEntry] = []
    for request in requests:
        view = by_uid[request.unit_uid]
        anchor_kinds = {anchor.kind for anchor in view.structural_anchors}
        near_drawing = bool(anchor_kinds & {"drawing", "vml_pict"}) or request.story == "textboxes"
        entries.append(VisualReviewEntry(
            unit_uid=request.unit_uid, display_id=request.display_id, content_role=request.content_role,
            story=request.story, selection_categories=request.selection_categories,
            nearby_drawing_or_textbox=near_drawing,
            structural_island=len(view.text_islands) > 1,
            continuation_hint=view.continuation_hint,
            review_reason="Representative real-translation calibration sample; inspect layout after external visual review.",
        ))
    return entries


def _request(
    view: TranslationView,
    block: BlockIR,
    source_sha: str,
    categories: list[str],
    verified_footnote_evidence: list[dict[str, object]] | None = None,
) -> CalibrationRequest:
    anchors = []
    source_by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    for anchor in view.anchors:
        anchors.append(CalibrationAnchor(
            anchor_id=anchor.anchor_id, semantics=anchor.kinds, protected=anchor.protected,
            protected_text=source_by_anchor.get(anchor.anchor_id) if anchor.protected else None,
            container="hyperlink" if "hyperlink_rid" in anchor.container_context else None,
        ))
    return CalibrationRequest(
        source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id,
        story=block.story, content_role=block.block_type, source_text=view.source_text,
        source_segments=[CalibrationSourceSegment(kind=segment.kind, text=segment.text, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment in view.segments],
        available_anchors=anchors, continuation_hint=view.continuation_hint,
        previous_text=view.previous_text if _include_neighbor_context(block, view) else None,
        next_text=view.next_text if _include_neighbor_context(block, view) else None,
        selection_categories=categories,
        footnote_reference_numbers=footnote_numbers_for_unit(
            verified_footnote_evidence, unit_uid=view.unit_uid, source_text=view.source_text,
        ),
    )


def _categories(view: TranslationView, block: BlockIR) -> list[str]:
    kinds = {kind for anchor in view.anchors for kind in anchor.kinds}
    structural = {anchor.kind for anchor in view.structural_anchors}
    text = view.source_text.strip()
    categories: list[str] = []
    if block.content_role == "heading": categories.append("heading")
    if block.content_role == "table_cell": categories.append("table")
    if block.content_role == "paragraph" and block.story == "body": categories.append("body")
    if len(text) >= 260: categories.append("long_body")
    if len(text.split()) <= 3: categories.append("short_text")
    # This book has no extractor-confirmed Heading style blocks.  Keep an
    # explicitly heuristic title-like stratum for calibration, not a claim
    # that the paragraph is semantically a heading.
    if 1 <= len(text.split()) <= 10 and len(text) <= 100 and not text.endswith((".", "?", "!")):
        categories.append("title_like_heuristic")
    if text.lower().startswith(("figure ", "table ", "fig. ", "listing ")): categories.append("caption")
    if text.startswith(("•", "- ", "– ", "— ")): categories.append("list_item")
    for name in ("bold", "italic", "color", "highlight", "hyperlink"):
        if name in kinds: categories.append("color_highlight" if name in {"color", "highlight"} else name)
    if any(anchor.protected for anchor in view.anchors): categories.append("protected")
    if view.continuation_hint: categories.append("continuation")
    if len(view.text_islands) > 1: categories.append("structural_island")
    if structural & {"drawing", "vml_pict"} or block.story == "textboxes": categories.append("drawing_or_textbox")
    if len(view.anchors) >= 2: categories.append("multi_anchor")
    return sorted(set(categories)) or ["general"]


def _stable_key(view: TranslationView) -> tuple[str, str]:
    return sha256(view.unit_uid.encode("utf-8")).hexdigest(), view.unit_uid


def _include_neighbor_context(block: BlockIR, view: TranslationView) -> bool:
    """Provide safe local context for normal body prose without grouping it."""
    return (
        block.story == "body"
        and block.block_type == "body_paragraph"
        and (view.previous_text is not None or view.next_text is not None)
    )
