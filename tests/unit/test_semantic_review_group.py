from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.ir import BlockIR
from booktrans.providers.qwen_max import ApprovedRepairContract
from booktrans.production_worker.reviewer_context import (
    LANE_FOOTNOTE,
    LANE_MAIN_BODY,
    RELATION_BODY_CONTINUATION,
    ReviewerContextUnit,
    footnote_body_identities,
)
from booktrans.production_worker.semantic_review_group import (
    REVIEW_GROUP_CONTRACT_VERSION,
    ROLE_CURRENT_REPAIRABLE,
    ROLE_PREVIOUS_SEALED,
    ROLE_PREVIOUS_UNSEALED,
    build_semantic_review_group,
    reject_sealed_member_repair,
)
from booktrans.production_worker.selection import (
    DEFAULT_BLOCKS,
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


PREVIOUS_BODY = _unit("body.p.00010", "uid-010", "keep in mind that libbase might actually be")
FOOTNOTE = _unit("body.p.00020", "uid-020", "2  For instance, security bugs, deprecations.")
TEXTBOX = _unit(
    "body.p.00025",
    "uid-025",
    "Sidebar callout about tooling.",
    role="textbox",
    block_type="textbox_paragraph",
)
CURRENT_FRAGMENT = _unit(
    "body.p.00030",
    "uid-030",
    "absolutely any piece of software involved in the construction of two or more nodes in your dependency network.",
)
STANDALONE = _unit("body.p.00040", "uid-040", "The diamond dependency problem requires three layers.")
FOOTNOTE_BODIES = frozenset({"body.p.00020", "uid-020"})


def test_standalone_sentence_is_its_own_group() -> None:
    group = build_semantic_review_group(STANDALONE, [PREVIOUS_BODY, STANDALONE], footnote_bodies=FOOTNOTE_BODIES)
    assert group.member_work_order_ids == ("body.p.00040",)
    assert group.repairable_work_order_ids == ("body.p.00040",)
    assert group.continuation_relation != RELATION_BODY_CONTINUATION
    assert group.members[0].role == ROLE_CURRENT_REPAIRABLE
    assert group.full_source_span == STANDALONE.source_text


def test_index_entry_group_excludes_physical_neighbors_and_reserves_locator_for_harness() -> None:
    previous = _unit("body.p.09000", "uid-index-a", "facial-recognition software, 73", role="index_entry")
    current = _unit("body.p.09001", "uid-index-b", "seed data, biases in, 282", role="index_entry")
    following = _unit("body.p.09002", "uid-index-c", "airplane, parable of, 108", role="index_entry")
    group = build_semantic_review_group(current, [previous, current, following])
    assert group.member_work_order_ids == ("body.p.09001",)
    assert group.continuation_review_rules["entry_interpretation"] == "TERM_QUALIFIER_LOCATOR"
    assert group.continuation_review_rules["numeric_locator_owner"] == "HARNESS_DETERMINISTIC"


def test_body_continuation_groups_previous_and_current() -> None:
    group = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, CURRENT_FRAGMENT],
        footnote_bodies=FOOTNOTE_BODIES,
        candidate_texts={"body.p.00010": "请记住 libbase 实际上可能是", "body.p.00030": "依赖网络中的任何软件。"},
        segment_counts={"body.p.00010": 1, "body.p.00030": 1},
    )
    assert group.member_work_order_ids == ("body.p.00010", "body.p.00030")
    assert group.repairable_work_order_ids == ("body.p.00030",)
    assert group.continuation_relation == RELATION_BODY_CONTINUATION
    assert group.members[0].role == ROLE_PREVIOUS_SEALED
    assert group.members[0].sealed is True
    assert group.members[0].repairable is False
    assert group.members[1].role == ROLE_CURRENT_REPAIRABLE
    assert "libbase might actually be absolutely any piece of software" in group.full_source_span
    assert group.full_candidate_span is not None
    assert "请记住" in group.full_candidate_span
    assert group.continuation_review_rules["punctuation_completeness_scope"] == "GROUP"


def test_continuation_skips_intervening_footnote() -> None:
    group = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, FOOTNOTE, CURRENT_FRAGMENT],
        footnote_bodies=FOOTNOTE_BODIES,
    )
    assert "body.p.00020" not in group.member_work_order_ids
    assert group.member_work_order_ids == ("body.p.00010", "body.p.00030")
    assert group.context.previous_semantic_display_id == "body.p.00010"


def test_continuation_skips_intervening_textbox() -> None:
    group = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, TEXTBOX, CURRENT_FRAGMENT],
        footnote_bodies=frozenset(),
    )
    assert "body.p.00025" not in group.member_work_order_ids
    assert group.member_work_order_ids == ("body.p.00010", "body.p.00030")


def test_fragment_without_subject_is_complete_in_group() -> None:
    fragment = _unit("body.p.00031", "uid-031", "any software involved in construction.")
    group = build_semantic_review_group(fragment, [PREVIOUS_BODY, fragment], footnote_bodies=frozenset())
    assert group.continuation_relation == RELATION_BODY_CONTINUATION
    assert not fragment.source_text[:1].isupper()
    assert group.continuation_review_rules["current_fragment_need_not_be_complete_clause"] is True
    assert group.continuation_review_rules["copula_subject_predicate_completeness_scope"] == "GROUP"
    assert group.full_source_span.endswith("any software involved in construction.")
    assert group.full_source_span.startswith("keep in mind")


def test_fragment_without_terminal_punctuation_is_complete_in_group() -> None:
    fragment = _unit("body.p.00032", "uid-032", "absolutely any piece of software")
    previous = _unit("body.p.00011", "uid-011", "libbase might actually be")
    group = build_semantic_review_group(fragment, [previous, fragment], footnote_bodies=frozenset())
    assert not fragment.source_text.endswith((".", "?", "!", "。"))
    assert group.continuation_review_rules["punctuation_completeness_scope"] == "GROUP"
    assert group.continuation_review_rules["false_positive_if_fragment_lacks_subject_copula_or_terminal_punctuation"] is False


def test_chapter_boundary_does_not_join_groups() -> None:
    other = _unit("body.p.01000", "uid-ch20", "previous chapter body continues as", chapter="chapter_20")
    current = _unit("body.p.00033", "uid-033", "absolutely leftover text.")
    group = build_semantic_review_group(current, [other, current], footnote_bodies=frozenset())
    assert group.member_work_order_ids == ("body.p.00033",)
    assert group.context.previous_semantic_unit_uid is None


def test_wrong_story_does_not_join_groups() -> None:
    other = _unit(
        "footnote.1.p.0001",
        "uid-fn",
        "footnote story continues as",
        story="footnotes",
        part="word/footnotes.xml",
    )
    current = _unit("body.p.00034", "uid-034", "absolutely leftover text.")
    group = build_semantic_review_group(current, [other, current], footnote_bodies=frozenset())
    assert group.member_work_order_ids == ("body.p.00034",)
    assert group.context.previous_semantic_unit_uid is None


def test_footnote_unit_is_not_body_continuation_group() -> None:
    earlier = _unit("body.p.00019", "uid-019", "1  Language version note.")
    group = build_semantic_review_group(
        FOOTNOTE,
        [earlier, FOOTNOTE, CURRENT_FRAGMENT],
        footnote_bodies=FOOTNOTE_BODIES | frozenset({"body.p.00019", "uid-019"}),
    )
    assert group.context.current_lane == LANE_FOOTNOTE
    assert group.continuation_relation != RELATION_BODY_CONTINUATION
    assert "body.p.00010" not in group.member_work_order_ids
    assert group.repairable_work_order_ids == ("body.p.00020",)


def test_group_hash_and_version_are_stable() -> None:
    first = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, FOOTNOTE, CURRENT_FRAGMENT],
        footnote_bodies=FOOTNOTE_BODIES,
        candidate_texts={"body.p.00030": "第一版候选"},
    )
    second = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, FOOTNOTE, CURRENT_FRAGMENT],
        footnote_bodies=FOOTNOTE_BODIES,
        candidate_texts={"body.p.00030": "另一候选不应进入 group hash"},
    )
    assert first.group_hash == second.group_hash
    assert first.review_contract_version == REVIEW_GROUP_CONTRACT_VERSION
    assert first.semantic_review_group_id == second.semantic_review_group_id
    assert first.context.current_lane == LANE_MAIN_BODY


def test_unsealed_previous_in_same_checkpoint_is_group_repairable() -> None:
    group = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, CURRENT_FRAGMENT],
        footnote_bodies=frozenset(),
        segment_counts={"body.p.00010": 1, "body.p.00030": 1},
        unsealed_work_order_ids=frozenset({"body.p.00010", "body.p.00030"}),
    )
    assert group.members[0].role == ROLE_PREVIOUS_UNSEALED
    assert group.members[0].sealed is False
    assert group.members[0].repairable is True
    assert group.repairable_work_order_ids == ("body.p.00010", "body.p.00030")
    assert group.continuation_review_rules["unsealed_group_members_may_be_repaired"] is True


def test_repair_scope_is_current_unit_only() -> None:
    group = build_semantic_review_group(
        CURRENT_FRAGMENT,
        [PREVIOUS_BODY, CURRENT_FRAGMENT],
        footnote_bodies=frozenset(),
        segment_counts={"body.p.00010": 1, "body.p.00030": 1},
    )
    sealed = ApprovedRepairContract(
        policy_id="SEMANTIC_NARROW_SEGMENT",
        unit_uid="uid-010",
        source_fingerprint="a" * 64,
        permitted_segment_indices=[0],
        repair_instruction="Do not edit sealed previous.",
    )
    current = ApprovedRepairContract(
        policy_id="SEMANTIC_NARROW_SEGMENT",
        unit_uid="uid-030",
        source_fingerprint="a" * 64,
        permitted_segment_indices=[0],
        repair_instruction="Patch only the current fragment.",
    )
    oversized = ApprovedRepairContract(
        policy_id="SEMANTIC_NARROW_SEGMENT",
        unit_uid="uid-030",
        source_fingerprint="a" * 64,
        permitted_segment_indices=[0, 1],
        repair_instruction="Illegal previous-as-group-index patch.",
    )
    assert reject_sealed_member_repair(group, sealed) == "GROUP_REPAIR_SEALED_MEMBER_FORBIDDEN"
    assert reject_sealed_member_repair(group, current) is None
    assert reject_sealed_member_repair(group, oversized) == "GROUP_REPAIR_SCOPE_OUTSIDE_CURRENT"
    assert all(not item["repairable"] for item in group.segment_ownership if item["member_display_id"] == "body.p.00010")
    assert all(item["repairable"] for item in group.segment_ownership if item["member_display_id"] == "body.p.00030")


def test_live_06968_group_is_06961_not_footnote() -> None:
    run = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
    registry = load_checkpoint_d050_registry(run, "checkpoint_03")
    bodies = footnote_body_identities(registry)
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
    group = build_semantic_review_group(units[2], units, footnote_bodies=bodies)
    assert group.member_work_order_ids == ("body.p.06961", "body.p.06968")
    assert group.repairable_work_order_ids == ("body.p.06968",)
    assert "body.p.06964" not in group.member_work_order_ids
    assert group.continuation_relation == RELATION_BODY_CONTINUATION
    canary_request = json.loads(
        next(
            line
            for line in Path(r"D:\translate\runs\qwen-production-canary-20260917T085341Z\translation_request.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("display_id") == "body.p.06968"
        )
    )
    assert (canary_request.get("previous_text") or "").startswith("2  For instance")
    assert not group.full_source_span.startswith("2  For instance")
