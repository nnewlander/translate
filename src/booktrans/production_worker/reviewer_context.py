"""Semantic reviewer context.  Physical document neighbors are not authority.

This builder is closed-world: it classifies lanes from frozen IR story/role
facts plus verified D050 footnote-body identities.  It does not infer footnotes
from punctuation, raised text, or numeric prefixes.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


REVIEW_CONTEXT_VERSION = "booktrans.review-context.v2"

LANE_MAIN_BODY = "MAIN_BODY"
LANE_FOOTNOTE = "FOOTNOTE"
LANE_ENDNOTE = "ENDNOTE"
LANE_TEXTBOX = "TEXTBOX"
LANE_INDEX = "INDEX"
LANE_OTHER = "OTHER"

RELATION_BODY_CONTINUATION = "BODY_CONTINUATION"
RELATION_BODY_ADJACENT = "BODY_ADJACENT"
RELATION_FOOTNOTE_LANE = "FOOTNOTE_LANE"
RELATION_INDEX_ENTRY = "INDEX_ENTRY"
RELATION_NONE = "NONE"

_BYPASS_STORIES = {"footnotes", "endnotes", "comments"}
_TEXTBOX_TYPES = {"textbox_paragraph"}
_TEXTBOX_ROLES = {"textbox"}


@dataclass(frozen=True)
class ReviewerContextUnit:
    """One frozen IR/story fact the builder may inspect."""

    display_id: str
    unit_uid: str
    story: str
    part_name: str
    content_role: str
    block_type: str
    source_text: str
    chapter_id: str | None = None
    translation_scope: str | None = None


@dataclass(frozen=True)
class ReviewerSemanticContext:
    review_context_version: str
    current_display_id: str
    current_unit_uid: str
    current_lane: str
    previous_semantic_display_id: str | None
    previous_semantic_unit_uid: str | None
    previous_text: str | None
    next_semantic_display_id: str | None
    next_semantic_unit_uid: str | None
    next_text: str | None
    continuation_relation: str
    auxiliary_footnote: tuple[dict[str, str], ...]
    context_hash: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "review_context_version": self.review_context_version,
            "current_display_id": self.current_display_id,
            "current_unit_uid": self.current_unit_uid,
            "current_lane": self.current_lane,
            "previous_semantic_display_id": self.previous_semantic_display_id,
            "previous_semantic_unit_uid": self.previous_semantic_unit_uid,
            "previous_text": self.previous_text,
            "next_semantic_display_id": self.next_semantic_display_id,
            "next_semantic_unit_uid": self.next_semantic_unit_uid,
            "next_text": self.next_text,
            "continuation_relation": self.continuation_relation,
            "auxiliary_footnote": [dict(item) for item in self.auxiliary_footnote],
            "context_hash": self.context_hash,
        }


DISPOSITION_TRANSLATE = "TRANSLATE"
DISPOSITION_PRESERVE_NUMERIC_LITERAL = "PRESERVE_NUMERIC_LITERAL"
DISPOSITION_PRESERVE_EXACT = "PRESERVE_EXACT"
DISPOSITION_EXCLUDE_FROM_SEMANTIC_REVIEW = "EXCLUDE_FROM_SEMANTIC_REVIEW"


def structural_identity_for(unit: ReviewerContextUnit) -> dict[str, Any]:
    """Closed-world IR identity.  Digit-only source is a fact, not a page-number guess."""
    scope = unit.translation_scope
    text = (unit.source_text or "").strip()
    numeric = bool(text) and text.isdigit()
    if scope == "EXCLUDED_BY_POLICY":
        disposition = DISPOSITION_EXCLUDE_FROM_SEMANTIC_REVIEW
        rule = "EXCLUDE"
    elif scope == "PROTECTED":
        disposition = DISPOSITION_PRESERVE_EXACT
        rule = "PROTECTED"
    elif numeric:
        disposition = DISPOSITION_PRESERVE_NUMERIC_LITERAL
        rule = "PRESERVE"
    else:
        disposition = DISPOSITION_TRANSLATE
        rule = "TRANSLATE"
    return {
        "story": unit.story,
        "content_role": unit.content_role,
        "block_type": unit.block_type,
        "translation_scope": scope,
        "source_is_numeric_literal": numeric,
        "review_disposition": disposition,
        "preservation_rule": rule,
    }


def footnote_body_identities(registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None) -> frozenset[str]:
    """Exact D050 corresponding footnote-body identities; never inferred."""
    found: set[str] = set()
    for records in (registry or {}).values():
        for record in records:
            body = record.get("corresponding_footnote_body")
            if not isinstance(body, dict):
                continue
            display_id = body.get("display_id")
            unit_uid = body.get("unit_uid")
            if isinstance(display_id, str) and display_id:
                found.add(display_id)
            if isinstance(unit_uid, str) and unit_uid:
                found.add(unit_uid)
    return frozenset(found)


def classify_lane(unit: ReviewerContextUnit, footnote_bodies: frozenset[str]) -> str:
    if unit.story == "endnotes":
        return LANE_ENDNOTE
    if unit.story == "footnotes" or unit.display_id in footnote_bodies or unit.unit_uid in footnote_bodies:
        return LANE_FOOTNOTE
    if unit.block_type in _TEXTBOX_TYPES or unit.content_role in _TEXTBOX_ROLES:
        return LANE_TEXTBOX
    # ``index_entry`` is an OOXML/IR document-section fact.  It must be
    # checked before the generic body fallback: consecutive index rows are
    # independent TERM / QUALIFIER / LOCATOR records, never prose fragments.
    if unit.story == "body" and unit.content_role == "index_entry":
        return LANE_INDEX
    if unit.story == "body" and unit.story not in _BYPASS_STORIES:
        return LANE_MAIN_BODY
    return LANE_OTHER


def context_hash_for(fields: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def build_reviewer_semantic_context(
    current: ReviewerContextUnit,
    units: Sequence[ReviewerContextUnit],
    *,
    footnote_bodies: frozenset[str] = frozenset(),
    auxiliary_footnote: Sequence[Mapping[str, str]] = (),
) -> ReviewerSemanticContext:
    """Return semantic previous/next for one work order.

    Main-body units skip footnote/endnote/textbox/empty neighbors in the same
    story+part+chapter.  Footnote units stay on the footnote lane.  Chapter or
    story mismatches never leak.
    """
    current_lane = classify_lane(current, footnote_bodies)
    if current_lane == LANE_INDEX:
        # Do not even expose physical neighbours as semantic context.  They
        # remain document-order evidence, but cannot form a prose group.
        previous = following = None
    else:
        walk_lane = current_lane if current_lane in {LANE_FOOTNOTE, LANE_ENDNOTE, LANE_TEXTBOX} else LANE_MAIN_BODY
        previous = _adjacent(current, units, footnote_bodies, walk_lane, direction=-1)
        following = _adjacent(current, units, footnote_bodies, walk_lane, direction=1)
    relation = _relation(current, current_lane, previous)
    hash_fields = {
        "review_context_version": REVIEW_CONTEXT_VERSION,
        "current_unit_uid": current.unit_uid,
        "previous_semantic_unit_uid": None if previous is None else previous.unit_uid,
        "next_semantic_unit_uid": None if following is None else following.unit_uid,
        "previous_text": None if previous is None else previous.source_text,
        "next_text": None if following is None else following.source_text,
        "continuation_relation": relation,
        "current_lane": current_lane,
    }
    return ReviewerSemanticContext(
        review_context_version=REVIEW_CONTEXT_VERSION,
        current_display_id=current.display_id,
        current_unit_uid=current.unit_uid,
        current_lane=current_lane,
        previous_semantic_display_id=None if previous is None else previous.display_id,
        previous_semantic_unit_uid=None if previous is None else previous.unit_uid,
        previous_text=None if previous is None else previous.source_text,
        next_semantic_display_id=None if following is None else following.display_id,
        next_semantic_unit_uid=None if following is None else following.unit_uid,
        next_text=None if following is None else following.source_text,
        continuation_relation=relation,
        auxiliary_footnote=tuple(dict(item) for item in auxiliary_footnote),
        context_hash=context_hash_for(hash_fields),
    )


def units_from_blocks(blocks: Sequence[Any], *, chapter_id: str | None = None) -> list[ReviewerContextUnit]:
    rows: list[ReviewerContextUnit] = []
    for block in blocks:
        rows.append(
            ReviewerContextUnit(
                display_id=str(block.display_id),
                unit_uid=str(block.unit_uid),
                story=str(block.story),
                part_name=str(block.part_name),
                content_role=str(block.content_role),
                block_type=str(block.block_type),
                source_text=str(block.source_plain_text or ""),
                chapter_id=chapter_id,
                translation_scope=None if getattr(block, "translation_scope", None) is None else str(block.translation_scope),
            )
        )
    return rows


def auxiliary_footnote_for_work_order(
    registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None,
    *,
    logical_work_order_id: str,
    unit_uid: str,
) -> tuple[dict[str, str], ...]:
    records = (registry or {}).get((logical_work_order_id, unit_uid), ())
    found: list[dict[str, str]] = []
    for record in records:
        if record.get("verification_status") != "VERIFIED":
            continue
        body = record.get("corresponding_footnote_body") if isinstance(record.get("corresponding_footnote_body"), dict) else {}
        item = {
            "logical_work_order_id": logical_work_order_id,
            "unit_uid": unit_uid,
            "footnote_number": str(record.get("footnote_number") or ""),
            "corresponding_footnote_body_display_id": str((body or {}).get("display_id") or ""),
        }
        if item["footnote_number"]:
            found.append(item)
    return tuple(found)


def _same_boundary(current: ReviewerContextUnit, other: ReviewerContextUnit) -> bool:
    if other.story != current.story or other.part_name != current.part_name:
        return False
    if current.chapter_id is not None and other.chapter_id is not None and other.chapter_id != current.chapter_id:
        return False
    return True


def _adjacent(
    current: ReviewerContextUnit,
    units: Sequence[ReviewerContextUnit],
    footnote_bodies: frozenset[str],
    walk_lane: str,
    *,
    direction: int,
) -> ReviewerContextUnit | None:
    index = next((position for position, unit in enumerate(units) if unit.unit_uid == current.unit_uid), None)
    if index is None:
        index = next((position for position, unit in enumerate(units) if unit.display_id == current.display_id), None)
    if index is None:
        return None
    cursor = index + direction
    while 0 <= cursor < len(units):
        candidate = units[cursor]
        cursor += direction
        if not candidate.source_text.strip():
            continue
        if not _same_boundary(current, candidate):
            return None
        if classify_lane(candidate, footnote_bodies) != walk_lane:
            continue
        return candidate
    return None


def _relation(current: ReviewerContextUnit, current_lane: str, previous: ReviewerContextUnit | None) -> str:
    if current_lane == LANE_INDEX:
        return RELATION_INDEX_ENTRY
    if current_lane == LANE_FOOTNOTE:
        return RELATION_FOOTNOTE_LANE
    if previous is None or current_lane != LANE_MAIN_BODY:
        return RELATION_NONE
    first = next((character for character in current.source_text if character.isalpha()), "")
    if first.islower():
        return RELATION_BODY_CONTINUATION
    return RELATION_BODY_ADJACENT
