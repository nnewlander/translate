"""Semantic review grouping.  Physical neighbors and footnote blocks are not members.

A BODY_CONTINUATION current unit is reviewed with its previous main-body semantic
unit.  Previous members default to read-only.  They become group-repairable only
when the caller proves they are in the same unsealed checkpoint selection.
This module does not call providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from booktrans.providers.qwen_max import ApprovedRepairContract
from booktrans.production_worker.reviewer_context import (
    RELATION_BODY_CONTINUATION,
    RELATION_INDEX_ENTRY,
    ReviewerContextUnit,
    ReviewerSemanticContext,
    build_reviewer_semantic_context,
    classify_lane,
    context_hash_for,
    structural_identity_for,
)


REVIEW_GROUP_CONTRACT_VERSION = "booktrans.review-group.v1"

ROLE_PREVIOUS_SEALED = "PREVIOUS_SEALED"
ROLE_PREVIOUS_UNSEALED = "PREVIOUS_UNSEALED"
ROLE_CURRENT_REPAIRABLE = "CURRENT_REPAIRABLE"

CONTINUATION_REVIEW_RULES = {
    "evaluate_full_group_span": True,
    "current_fragment_need_not_be_complete_clause": True,
    "punctuation_completeness_scope": "GROUP",
    "copula_subject_predicate_completeness_scope": "GROUP",
    "source_emphasis_scope": "GROUP",
    "false_positive_if_fragment_lacks_subject_copula_or_terminal_punctuation": False,
    "sealed_previous_members_are_read_only": True,
    "unsealed_group_members_may_be_repaired": False,
}


@dataclass(frozen=True)
class SemanticReviewGroupMember:
    display_id: str
    unit_uid: str
    lane: str
    source_text: str
    candidate_text: str | None
    role: str
    repairable: bool
    sealed: bool
    local_segment_count: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "display_id": self.display_id,
            "unit_uid": self.unit_uid,
            "lane": self.lane,
            "source_text": self.source_text,
            "candidate_text": self.candidate_text,
            "role": self.role,
            "repairable": self.repairable,
            "sealed": self.sealed,
            "local_segment_count": self.local_segment_count,
        }


@dataclass(frozen=True)
class SemanticReviewGroup:
    review_contract_version: str
    semantic_review_group_id: str
    current_display_id: str
    current_unit_uid: str
    continuation_relation: str
    member_work_order_ids: tuple[str, ...]
    repairable_work_order_ids: tuple[str, ...]
    members: tuple[SemanticReviewGroupMember, ...]
    full_source_span: str
    full_candidate_span: str | None
    segment_ownership: tuple[dict[str, Any], ...]
    context: ReviewerSemanticContext
    group_hash: str
    continuation_review_rules: dict[str, Any]
    source_structural_identity: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "review_contract_version": self.review_contract_version,
            "semantic_review_group_id": self.semantic_review_group_id,
            "current_display_id": self.current_display_id,
            "current_unit_uid": self.current_unit_uid,
            "continuation_relation": self.continuation_relation,
            "member_work_order_ids": list(self.member_work_order_ids),
            "repairable_work_order_ids": list(self.repairable_work_order_ids),
            "members": [member.as_payload() for member in self.members],
            "full_source_span": self.full_source_span,
            "full_candidate_span": self.full_candidate_span,
            "segment_ownership": [dict(item) for item in self.segment_ownership],
            "group_hash": self.group_hash,
            "continuation_review_rules": dict(self.continuation_review_rules),
            "context": self.context.as_payload(),
            "source_structural_identity": dict(self.source_structural_identity),
        }

    def as_reviewer_context_payload(self) -> dict[str, Any]:
        payload = self.context.as_payload()
        payload.update(
            {
                "review_contract_version": self.review_contract_version,
                "semantic_review_group_id": self.semantic_review_group_id,
                "member_work_order_ids": list(self.member_work_order_ids),
                "repairable_work_order_ids": list(self.repairable_work_order_ids),
                "group_hash": self.group_hash,
                "full_source_span": self.full_source_span,
                "full_candidate_span": self.full_candidate_span,
                "segment_ownership": [dict(item) for item in self.segment_ownership],
                "continuation_review_rules": dict(self.continuation_review_rules),
                "semantic_review_group": self.as_payload(),
                "source_structural_identity": dict(self.source_structural_identity),
            }
        )
        return payload


def join_semantic_span(texts: Sequence[str | None]) -> str:
    return " ".join(text.strip() for text in texts if isinstance(text, str) and text.strip())


def _identity_in(ids: frozenset[str] | None, unit: ReviewerContextUnit) -> bool:
    if not ids:
        return False
    return unit.display_id in ids or unit.unit_uid in ids


def previous_member_is_sealed(
    previous: ReviewerContextUnit,
    *,
    sealed_work_order_ids: frozenset[str] | None = None,
    unsealed_work_order_ids: frozenset[str] | None = None,
) -> bool:
    """Default previous-to-current as sealed.  Unsealed only with an explicit checkpoint set."""
    if sealed_work_order_ids is not None:
        return _identity_in(sealed_work_order_ids, previous)
    if unsealed_work_order_ids is not None:
        return not _identity_in(unsealed_work_order_ids, previous)
    return True


def build_semantic_review_group(
    current: ReviewerContextUnit,
    units: Sequence[ReviewerContextUnit],
    *,
    footnote_bodies: frozenset[str] = frozenset(),
    auxiliary_footnote: Sequence[Mapping[str, str]] = (),
    candidate_texts: Mapping[str, str] | None = None,
    segment_counts: Mapping[str, int] | None = None,
    sealed_work_order_ids: frozenset[str] | None = None,
    unsealed_work_order_ids: frozenset[str] | None = None,
) -> SemanticReviewGroup:
    """Group previous MAIN_BODY continuation with current; never include bypass lanes."""
    context = build_reviewer_semantic_context(
        current,
        units,
        footnote_bodies=footnote_bodies,
        auxiliary_footnote=auxiliary_footnote,
    )
    texts = dict(candidate_texts or {})
    counts = dict(segment_counts or {})
    member_units: list[ReviewerContextUnit] = []
    if context.continuation_relation == RELATION_BODY_CONTINUATION and context.previous_semantic_unit_uid:
        previous = next(
            (item for item in units if item.unit_uid == context.previous_semantic_unit_uid),
            None,
        )
        if previous is None:
            previous = next(
                (item for item in units if item.display_id == context.previous_semantic_display_id),
                None,
            )
        if previous is not None:
            member_units.append(previous)
    member_units.append(current)
    members: list[SemanticReviewGroupMember] = []
    ownership: list[dict[str, Any]] = []
    group_index = 0
    for unit in member_units:
        is_current = unit.unit_uid == current.unit_uid or unit.display_id == current.display_id
        local_count = max(1, int(counts.get(unit.display_id) or counts.get(unit.unit_uid) or 1))
        if is_current:
            sealed = False
            repairable = True
            role = ROLE_CURRENT_REPAIRABLE
        else:
            sealed = previous_member_is_sealed(
                unit,
                sealed_work_order_ids=sealed_work_order_ids,
                unsealed_work_order_ids=unsealed_work_order_ids,
            )
            repairable = not sealed
            role = ROLE_PREVIOUS_SEALED if sealed else ROLE_PREVIOUS_UNSEALED
        members.append(
            SemanticReviewGroupMember(
                display_id=unit.display_id,
                unit_uid=unit.unit_uid,
                lane=classify_lane(unit, footnote_bodies),
                source_text=unit.source_text,
                candidate_text=texts.get(unit.display_id, texts.get(unit.unit_uid)),
                role=role,
                repairable=repairable,
                sealed=sealed,
                local_segment_count=local_count,
            )
        )
        for local in range(local_count):
            ownership.append(
                {
                    "group_segment_index": group_index,
                    "member_display_id": unit.display_id,
                    "member_unit_uid": unit.unit_uid,
                    "local_segment_index": local,
                    "repairable": repairable,
                    "sealed": sealed,
                }
            )
            group_index += 1
    member_ids = tuple(member.display_id for member in members)
    repairable_ids = tuple(member.display_id for member in members if member.repairable)
    source_span = join_semantic_span([member.source_text for member in members])
    candidate_span = join_semantic_span([member.candidate_text for member in members]) or None
    hash_fields = {
        "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
        "current_unit_uid": current.unit_uid,
        "member_work_order_ids": list(member_ids),
        "repairable_work_order_ids": list(repairable_ids),
        "full_source_span": source_span,
        "continuation_relation": context.continuation_relation,
        "segment_ownership": ownership,
    }
    group_hash = context_hash_for(hash_fields)
    return SemanticReviewGroup(
        review_contract_version=REVIEW_GROUP_CONTRACT_VERSION,
        semantic_review_group_id=f"srg:{current.display_id}:{group_hash[:16]}",
        current_display_id=current.display_id,
        current_unit_uid=current.unit_uid,
        continuation_relation=context.continuation_relation,
        member_work_order_ids=member_ids,
        repairable_work_order_ids=repairable_ids,
        members=tuple(members),
        full_source_span=source_span,
        full_candidate_span=candidate_span,
        segment_ownership=tuple(ownership),
        context=context,
        group_hash=group_hash,
        continuation_review_rules=_continuation_rules(
            context.continuation_relation,
            unsealed_previous=any(member.role == ROLE_PREVIOUS_UNSEALED for member in members),
        ),
        source_structural_identity=structural_identity_for(current),
    )


def _continuation_rules(relation: str, *, unsealed_previous: bool) -> dict[str, Any]:
    if relation == RELATION_INDEX_ENTRY:
        return {
            "evaluate_full_group_span": False,
            "entry_interpretation": "TERM_QUALIFIER_LOCATOR",
            "adjacent_index_entries_are_semantically_independent": True,
            "numeric_locator_owner": "HARNESS_DETERMINISTIC",
            "delimiter_and_entry_assembly": "HARNESS_DETERMINISTIC",
            "review_scope": "TERM_AND_QUALIFIER_ONLY",
            "current_fragment_need_not_be_complete_clause": True,
            "punctuation_completeness_scope": "ENTRY",
            "copula_subject_predicate_completeness_scope": "NOT_APPLICABLE",
            "source_emphasis_scope": "ENTRY",
            "false_positive_if_fragment_lacks_subject_copula_or_terminal_punctuation": False,
            "sealed_previous_members_are_read_only": True,
            "unsealed_group_members_may_be_repaired": False,
        }
    if relation != RELATION_BODY_CONTINUATION:
        return {
            "evaluate_full_group_span": True,
            "current_fragment_need_not_be_complete_clause": False,
            "punctuation_completeness_scope": "CURRENT",
            "copula_subject_predicate_completeness_scope": "CURRENT",
            "source_emphasis_scope": "CURRENT",
            "false_positive_if_fragment_lacks_subject_copula_or_terminal_punctuation": True,
            "sealed_previous_members_are_read_only": True,
            "unsealed_group_members_may_be_repaired": False,
        }
    rules = dict(CONTINUATION_REVIEW_RULES)
    rules["unsealed_group_members_may_be_repaired"] = unsealed_previous
    return rules


def reject_sealed_member_repair(group: SemanticReviewGroup, contract: ApprovedRepairContract) -> str | None:
    """Repair may patch only the current repairable work order."""
    if contract.unit_uid != group.current_unit_uid:
        return "GROUP_REPAIR_SEALED_MEMBER_FORBIDDEN"
    if group.current_display_id not in group.repairable_work_order_ids:
        return "GROUP_REPAIR_CURRENT_NOT_REPAIRABLE"
    current = next((member for member in group.members if member.display_id == group.current_display_id), None)
    if current is None or not current.repairable:
        return "GROUP_REPAIR_CURRENT_NOT_REPAIRABLE"
    permitted = list(contract.permitted_segment_indices)
    if any(index < 0 or index >= current.local_segment_count for index in permitted):
        return "GROUP_REPAIR_SCOPE_OUTSIDE_CURRENT"
    return None
