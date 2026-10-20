from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.ir import BlockIR
from booktrans.production_worker.reviewer_context import (
    LANE_FOOTNOTE,
    LANE_INDEX,
    LANE_MAIN_BODY,
    RELATION_BODY_CONTINUATION,
    RELATION_FOOTNOTE_LANE,
    RELATION_INDEX_ENTRY,
    REVIEW_CONTEXT_VERSION,
    ReviewerContextUnit,
    build_reviewer_semantic_context,
    classify_lane,
    context_hash_for,
    footnote_body_identities,
)
from booktrans.production_worker.selection import (
    DEFAULT_BLOCKS,
    d050_paths_for_checkpoint,
    load_checkpoint_d050_registry,
    load_selected_jsonl_by_display_id,
)


def _unit(
    display: str,
    uid: str,
    text: str,
    *,
    story: str = "body",
    part: str = "word/document.xml",
    role: str = "paragraph",
    block_type: str = "body_paragraph",
    chapter: str | None = "chapter_21",
) -> ReviewerContextUnit:
    return ReviewerContextUnit(
        display_id=display,
        unit_uid=uid,
        story=story,
        part_name=part,
        content_role=role,
        block_type=block_type,
        source_text=text,
        chapter_id=chapter,
    )


BODY_061 = _unit(
    "body.p.06961",
    "uid-061",
    "keep in mind that libbase might actually be",
)
FOOTNOTE_064 = _unit(
    "body.p.06964",
    "uid-064",
    "2  For instance, security bugs, deprecations, being in the dependency set of a higher-level dependency that has a security bug, and so on.",
)
BODY_068 = _unit(
    "body.p.06968",
    "uid-068",
    "absolutely any piece of software involved in the construction of two or more nodes in your dependency network.",
)
EMPTY = _unit("body.p.06965", "uid-065", "")
FOOTNOTE_BODIES = frozenset({"body.p.06964", "uid-064"})


def test_body_continuation_skips_footnote_block() -> None:
    units = [BODY_061, EMPTY, FOOTNOTE_064, EMPTY, BODY_068]
    context = build_reviewer_semantic_context(BODY_068, units, footnote_bodies=FOOTNOTE_BODIES)
    assert context.previous_semantic_display_id == "body.p.06961"
    assert context.previous_semantic_unit_uid == "uid-061"
    assert context.previous_text == BODY_061.source_text
    assert context.continuation_relation == RELATION_BODY_CONTINUATION
    assert context.current_lane == LANE_MAIN_BODY
    assert "security bugs" not in (context.previous_text or "")


def test_footnote_does_not_impersonate_body_previous() -> None:
    units = [BODY_061, FOOTNOTE_064, BODY_068]
    context = build_reviewer_semantic_context(BODY_068, units, footnote_bodies=FOOTNOTE_BODIES)
    assert context.previous_semantic_unit_uid != "uid-064"
    assert FOOTNOTE_064.source_text != context.previous_text


def test_footnote_unit_keeps_footnote_lane_context() -> None:
    earlier = _unit("body.p.06953", "uid-053", "1  This could be any of language version.")
    units = [earlier, BODY_061, FOOTNOTE_064, BODY_068]
    bodies = FOOTNOTE_BODIES | frozenset({"body.p.06953", "uid-053"})
    context = build_reviewer_semantic_context(FOOTNOTE_064, units, footnote_bodies=bodies)
    assert context.current_lane == LANE_FOOTNOTE
    assert context.continuation_relation == RELATION_FOOTNOTE_LANE
    assert context.previous_semantic_unit_uid == "uid-053"
    assert context.previous_text == earlier.source_text
    assert context.previous_semantic_unit_uid != "uid-061"


def test_index_entry_never_uses_adjacent_entry_as_prose_context() -> None:
    previous = _unit("body.p.09000", "uid-index-a", "facial-recognition software, 73", role="index_entry")
    current = _unit("body.p.09001", "uid-index-b", "seed data, biases in, 282", role="index_entry")
    following = _unit("body.p.09002", "uid-index-c", "airplane, parable of, 108", role="index_entry")
    context = build_reviewer_semantic_context(current, [previous, current, following])
    assert context.current_lane == LANE_INDEX
    assert context.continuation_relation == RELATION_INDEX_ENTRY
    assert context.previous_semantic_unit_uid is None
    assert context.next_semantic_unit_uid is None


def test_chapter_and_story_boundaries_do_not_leak() -> None:
    other_chapter = _unit(
        "body.p.06000",
        "uid-ch20",
        "previous chapter body.",
        chapter="chapter_20",
    )
    other_story = _unit(
        "footnote.1.p.0001",
        "uid-fnxml",
        "footnote story text.",
        story="footnotes",
        part="word/footnotes.xml",
        chapter="chapter_21",
    )
    units = [other_chapter, other_story, BODY_068]
    context = build_reviewer_semantic_context(BODY_068, units, footnote_bodies=frozenset())
    assert context.previous_semantic_unit_uid is None
    assert context.previous_text is None
    assert classify_lane(other_story, frozenset()) == LANE_FOOTNOTE


def test_context_hash_is_stable() -> None:
    units = [BODY_061, FOOTNOTE_064, BODY_068]
    first = build_reviewer_semantic_context(BODY_068, units, footnote_bodies=FOOTNOTE_BODIES)
    second = build_reviewer_semantic_context(BODY_068, units, footnote_bodies=FOOTNOTE_BODIES)
    assert first.context_hash == second.context_hash
    assert first.review_context_version == REVIEW_CONTEXT_VERSION
    assert first.context_hash == context_hash_for(
        {
            "review_context_version": first.review_context_version,
            "current_unit_uid": first.current_unit_uid,
            "previous_semantic_unit_uid": first.previous_semantic_unit_uid,
            "next_semantic_unit_uid": first.next_semantic_unit_uid,
            "previous_text": first.previous_text,
            "next_text": first.next_text,
            "continuation_relation": first.continuation_relation,
            "current_lane": first.current_lane,
        }
    )


def test_live_06968_previous_is_06961_not_footnote() -> None:
    run = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
    registry = load_checkpoint_d050_registry(run, "checkpoint_03")
    bodies = footnote_body_identities(registry)
    assert "body.p.06964" in bodies
    wanted = ["body.p.06961", "body.p.06964", "body.p.06968"]
    blocks = load_selected_jsonl_by_display_id(DEFAULT_BLOCKS, BlockIR, wanted)
    units = [
        ReviewerContextUnit(
            display_id=blocks[item].display_id,
            unit_uid=blocks[item].unit_uid,
            story=blocks[item].story,
            part_name=blocks[item].part_name,
            content_role=blocks[item].content_role,
            block_type=blocks[item].block_type,
            source_text=blocks[item].source_plain_text,
            chapter_id="chapter_21",
        )
        for item in wanted
    ]
    current = units[2]
    context = build_reviewer_semantic_context(current, units, footnote_bodies=bodies)
    assert context.previous_semantic_display_id == "body.p.06961"
    assert context.previous_semantic_unit_uid == units[0].unit_uid
    assert context.continuation_relation == RELATION_BODY_CONTINUATION
    assert not (context.previous_text or "").startswith("2  For instance")
    canary_request = json.loads(
        next(
            line
            for line in Path(r"D:\translate\runs\qwen-production-canary-20260917T085341Z\translation_request.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("display_id") == "body.p.06968"
        )
    )
    assert (canary_request.get("previous_text") or "").startswith("2  For instance")
    assert context.previous_text != canary_request.get("previous_text")
    assert d050_paths_for_checkpoint(run, "checkpoint_03")
