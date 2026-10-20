"""Evidence-only helpers for the explicitly deferred TOC phase.

These helpers deliberately do not change ordinary pending-selection semantics.
They make the already-scoped late phase selectable and classify only from
structural OOXML facts; they never infer a heading by text similarity.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from zipfile import ZipFile

from lxml import etree


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": WORD_NS}
W_NAME = f"{{{WORD_NS}}}name"


class TocLateStageError(RuntimeError):
    pass


@dataclass(frozen=True)
class TocClassification:
    deterministic_ids: tuple[str, ...]
    normal_ids: tuple[str, ...]
    unsafe_bookmark_ids: tuple[str, ...]


def select_toc_late_stage(scope: Mapping[str, object], released: set[str]) -> list[dict[str, object]]:
    """Return the exact unreleased TOC phase in scope order."""
    orders = scope.get("logical_work_orders")
    if not isinstance(orders, list):
        raise TocLateStageError("SCOPE_LOGICAL_WORK_ORDERS_INVALID")
    selected = [
        item for item in orders
        if isinstance(item, dict)
        and item.get("phase") == "toc_late_stage"
        and isinstance(item.get("logical_work_order_id"), str)
        and item["logical_work_order_id"] not in released
    ]
    ids = [str(item["logical_work_order_id"]) for item in selected]
    if len(ids) != len(set(ids)):
        raise TocLateStageError("TOC_SELECTION_DUPLICATE")
    return selected


def classify_toc_headings(
    *, source_docx: Path, source_sha256: str, expected_source_sha256: str,
    scope: Mapping[str, object], blocks: Sequence[Mapping[str, object]], released: set[str],
) -> TocClassification:
    """Classify only literal title identities reached by unique OOXML bookmarks.

    A unique bookmark alone is intentionally insufficient.  The heading source
    text must be an exact source-title prefix of the TOC text; no normalization
    or stitching is performed.
    """
    if source_sha256 != expected_source_sha256:
        raise TocLateStageError("TOC_SOURCE_SHA_MISMATCH")
    selected = select_toc_late_stage(scope, released)
    selected_ids = [str(item["logical_work_order_id"]) for item in selected]
    by_id = {str(item.get("display_id")): item for item in blocks if isinstance(item.get("display_id"), str)}
    by_ordinal = {
        int(locator["paragraph_ordinal"]): item
        for item in blocks
        if isinstance((locator := item.get("locator")), Mapping)
        and item.get("part_name") == "word/document.xml"
        and isinstance(locator.get("paragraph_ordinal"), int)
    }
    if any(item not in by_id for item in selected_ids):
        raise TocLateStageError("TOC_SOURCE_BLOCK_MISSING")
    with ZipFile(source_docx) as package:
        root = etree.fromstring(package.read("word/document.xml"))
    paragraphs = root.xpath(".//w:p", namespaces=NS)
    ordinal = {paragraph: index + 1 for index, paragraph in enumerate(paragraphs)}
    bookmarks: dict[str, int] = {}
    for node in root.xpath(".//w:bookmarkStart[@w:name]", namespaces=NS):
        parent = node.xpath("ancestor::w:p[1]", namespaces=NS)
        if parent:
            bookmarks[str(node.get(W_NAME))] = ordinal[parent[0]]
    deterministic: list[str] = []
    normal: list[str] = []
    unsafe: list[str] = []
    for display_id in selected_ids:
        block = by_id[display_id]
        locator = block.get("locator")
        if not isinstance(locator, Mapping) or not isinstance(locator.get("paragraph_ordinal"), int):
            raise TocLateStageError("TOC_SOURCE_LOCATOR_INVALID")
        paragraph = paragraphs[int(locator["paragraph_ordinal"]) - 1]
        anchors = list(dict.fromkeys(paragraph.xpath(".//w:hyperlink/@w:anchor", namespaces=NS)))
        targets = {
            str(by_ordinal[bookmarks[anchor]].get("display_id"))
            for anchor in anchors if anchor in bookmarks and bookmarks[anchor] in by_ordinal
        }
        if len(anchors) == 1 and len(targets) == 1:
            heading_id = next(iter(targets))
            heading = by_id.get(heading_id)
            toc_text = str(block.get("source_plain_text") or "")
            heading_text = "" if heading is None else str(heading.get("source_plain_text") or "")
            if heading_id in released and heading_text and toc_text.startswith(heading_text):
                deterministic.append(display_id)
            else:
                normal.append(display_id)
                unsafe.append(display_id)
        else:
            normal.append(display_id)
    if len(deterministic) + len(normal) != len(selected_ids) or set(deterministic) & set(normal):
        raise TocLateStageError("TOC_CLASSIFICATION_NOT_PARTITION")
    return TocClassification(tuple(deterministic), tuple(normal), tuple(unsafe))


def full_book_scope_complete(progress: Mapping[str, object]) -> bool:
    """The thin derived full-scope predicate; next_resume is intentionally absent."""
    phases = progress.get("current_progress_by_phase")
    if not isinstance(phases, Mapping) or not phases:
        return False
    for row in phases.values():
        if not isinstance(row, Mapping):
            return False
        if row.get("unstarted") != 0 or row.get("final_approved") != row.get("logical_total"):
            return False
    return bool(
        progress.get("current_release_set_count") == progress.get("total_logical_work_orders")
        and progress.get("unresolved_deterministic_fail") == 0
        and progress.get("human_required") == 0
        and progress.get("recovery_queue_size") == 0
    )
