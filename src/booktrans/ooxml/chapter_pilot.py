"""Deliberately narrow M5.2 boundary selection for the approved source book.

This is not a general chapter parser.  The two literal markers are an explicit,
auditable boundary contract for the currently approved Chapter 1 pilot only.
"""

from booktrans.domain.calibration import CalibrationRequest
from booktrans.domain.ir import DocumentIR
from booktrans.domain.translation_view import TaggedCapabilityReport, TranslationView
from booktrans.ooxml.calibration import _categories, _request


CHAPTER_ONE_START = "What Is Software Engineering?"
PART_TWO_START = "PART II"
CHAPTER_TWO_START = "How to Work Well on Teams"


def select_chapter_one(
    document: DocumentIR,
    views: list[TranslationView],
    capability: TaggedCapabilityReport,
) -> tuple[list[CalibrationRequest], dict]:
    """Return supported Chapter 1 requests and an explicit selection manifest.

    Both markers must occur exactly once as exact body paragraphs.  All body
    stories/parts whose stable paragraph ordinal lies in the resulting
    half-open interval belong to the pilot; unsupported units remain visible
    in the manifest rather than being silently dropped.
    """
    blocks = {block.unit_uid: block for block in document.blocks}
    start, end, next_chapter = _chapter_bounds(document)
    views_by_uid = {view.unit_uid: view for view in views}
    capability_by_uid = {record.unit_uid: record for record in capability.records}
    included = [
        block for block in document.blocks
        if block.part_name == "word/document.xml"
        and block.locator.paragraph_ordinal is not None
        and start.locator.paragraph_ordinal <= block.locator.paragraph_ordinal < end.locator.paragraph_ordinal
        and block.translation_scope is not None
    ]
    records = []
    requests: list[CalibrationRequest] = []
    for block in included:
        record = capability_by_uid.get(block.unit_uid)
        status = record.status if record else "NOT_TRANSLATABLE_OR_NO_VIEW"
        reasons = record.reasons if record else []
        eligible = status == "TAGGED_SUPPORTED" and block.unit_uid in views_by_uid
        records.append({
            "unit_uid": block.unit_uid, "display_id": block.display_id,
            "story": block.story, "part_name": block.part_name,
            "paragraph_ordinal": block.locator.paragraph_ordinal,
            "translation_scope": block.translation_scope.value,
            "tagged_status": status, "reasons": reasons,
            "included_for_translation": eligible,
        })
        if eligible:
            view = views_by_uid[block.unit_uid]
            requests.append(_request(view, block, document.document_sha256, ["chapter_1_pilot", *_categories(view, block)]))
    manifest = {
        "pilot": "M5.2 Chapter 1 only",
        "boundary_policy": "Explicit literal markers for this known book; not a general chapter parser.",
        "start_marker": {"text": CHAPTER_ONE_START, "display_id": start.display_id, "paragraph_ordinal": start.locator.paragraph_ordinal},
        "end_marker_exclusive": {"text": PART_TWO_START, "display_id": end.display_id, "paragraph_ordinal": end.locator.paragraph_ordinal},
        "next_chapter_marker_verified": {"text": CHAPTER_TWO_START, "display_id": next_chapter.display_id, "paragraph_ordinal": next_chapter.locator.paragraph_ordinal},
        "story": "body / word/document.xml ordinal interval",
        "included_block_count": len(included),
        "tagged_supported_count": len(requests),
        "unsupported_count": sum(item["tagged_status"] == "TAGGED_UNSUPPORTED" for item in records),
        "units": records,
    }
    return requests, manifest


def _chapter_bounds(document: DocumentIR):
    candidates = [
        block for block in document.blocks
        if block.story == "body" and block.part_name == "word/document.xml" and block.locator.paragraph_ordinal is not None
    ]
    starts = [block for block in candidates if block.source_plain_text.strip() == CHAPTER_ONE_START]
    part_two = [block for block in candidates if block.source_plain_text.strip() == PART_TWO_START]
    next_chapters = [block for block in candidates if block.source_plain_text.strip() == CHAPTER_TWO_START]
    if len(starts) != 1 or len(part_two) != 1 or len(next_chapters) != 1:
        raise ValueError(
            "CHAPTER_BOUNDARY_AMBIGUOUS: expected one exact body paragraph for each approved marker; "
            f"start_matches={len(starts)}; part_two_matches={len(part_two)}; chapter_two_matches={len(next_chapters)}"
        )
    start, end, next_chapter = starts[0], part_two[0], next_chapters[0]
    if not start.locator.paragraph_ordinal < end.locator.paragraph_ordinal < next_chapter.locator.paragraph_ordinal:
        raise ValueError("CHAPTER_BOUNDARY_ORDER_INVALID: expected Chapter 1, PART II, then Chapter 2 markers.")
    return start, end, next_chapter
