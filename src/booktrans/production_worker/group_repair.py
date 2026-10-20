"""Closed-world Semantic Group Repair eligibility and contract construction."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Sequence

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.production_worker.semantic_review_group import SemanticReviewGroup
from booktrans.providers.qwen_max import (
    SEMANTIC_GROUP_POLICY,
    GroupReviewResult,
    IntentionalEmptyAuthorization,
    SemanticGroupAllowedRepair,
    SemanticGroupRepairContract,
)
from booktrans.providers.translation_provider import TranslationProviderRequest


@dataclass(frozen=True)
class GroupRepairEligibility:
    allowed: bool
    reason: str | None
    repairable_work_order_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroupRepairGateDecision:
    action: str
    reason: str
    contract: SemanticGroupRepairContract | None = None
    policy_id: str | None = None


def production_sealed_work_order_ids(run: Path, batch_dir: str = "full_book_batch_022") -> frozenset[str]:
    """Read sealed production checkpoint evidence only.  Never infers from canary."""
    folder = run / batch_dir
    found: set[str] = set()
    if not folder.is_dir():
        return frozenset()
    for checkpoint in folder.glob("checkpoint_*"):
        evidence = checkpoint / "checkpoint_evidence.json"
        if not evidence.is_file():
            continue
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        orders = payload.get("logical_work_orders") or payload.get("selected_logical_work_order_ids") or []
        if isinstance(orders, list):
            found.update(str(item) for item in orders if item)
    return frozenset(found)


def decide_group_repair_eligibility(
    group: SemanticReviewGroup,
    *,
    frozen_selection_ids: set[str],
    unsealed_checkpoint_ids: set[str],
    production_sealed_ids: set[str],
    requests: Mapping[str, CalibrationRequest],
    raws: Mapping[str, TranslationResult],
    stored_raw_shas: Mapping[str, str],
    raw_line_shas: Mapping[str, str],
) -> GroupRepairEligibility:
    """Allow group repair only when every member is an unsealed current-checkpoint unit."""
    if len(group.member_work_order_ids) < 2:
        return GroupRepairEligibility(False, "GROUP_REPAIR_NOT_A_CONTINUATION_PAIR", tuple(group.repairable_work_order_ids))
    for member in group.members:
        if member.display_id not in frozen_selection_ids:
            return GroupRepairEligibility(False, "GROUP_REPAIR_OUTSIDE_FROZEN_SELECTION", ())
        if member.display_id in production_sealed_ids or member.unit_uid in production_sealed_ids:
            return GroupRepairEligibility(False, "GROUP_REPAIR_PRODUCTION_SEALED", ())
        if member.display_id not in unsealed_checkpoint_ids:
            return GroupRepairEligibility(False, "GROUP_REPAIR_OUTSIDE_CURRENT_CHECKPOINT", ())
        if member.sealed:
            return GroupRepairEligibility(False, "GROUP_REPAIR_SEALED_MEMBER", ())
        request = requests.get(member.display_id)
        if request is None or request.unit_uid != member.unit_uid:
            return GroupRepairEligibility(False, "GROUP_REPAIR_REQUEST_BINDING_MISMATCH", ())
        raw = raws.get(request.unit_uid)
        if raw is None or raw.unit_uid != request.unit_uid:
            return GroupRepairEligibility(False, "GROUP_REPAIR_RAW_MISSING", ())
        if len(raw.target_segments) != len(request.source_segments):
            return GroupRepairEligibility(False, "GROUP_REPAIR_RAW_SEGMENT_MISMATCH", ())
        expected_sha = stored_raw_shas.get(member.display_id) or stored_raw_shas.get(request.unit_uid)
        actual_sha = raw_line_shas.get(request.unit_uid)
        if expected_sha and actual_sha and expected_sha != actual_sha:
            return GroupRepairEligibility(False, "GROUP_REPAIR_RAW_SHA_MISMATCH", ())
    repairable = tuple(member.display_id for member in group.members if member.repairable and not member.sealed)
    if group.current_display_id not in repairable or len(repairable) < 2:
        return GroupRepairEligibility(False, "GROUP_REPAIR_SCOPE_NOT_UNSEALED_PAIR", repairable)
    return GroupRepairEligibility(True, None, repairable)


def non_protected_indices(request: CalibrationRequest) -> list[int]:
    return [index for index, segment in enumerate(request.source_segments) if segment.kind != "protected"]


def decide_semantic_group_repair_contract(
    group: SemanticReviewGroup,
    review: GroupReviewResult,
    *,
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    raw_shas: Mapping[str, str],
    intentional_empty_authorizations: Mapping[str, Sequence[IntentionalEmptyAuthorization]] | None = None,
) -> GroupRepairGateDecision:
    """Build a group contract from a group review.  Sealed/protected/outside scope fail closed."""
    if review.verdict == "HUMAN_REQUIRED":
        return GroupRepairGateDecision("HUMAN_REQUIRED", "REVIEWER_HUMAN_REQUIRED")
    if review.verdict != "REPAIR":
        return GroupRepairGateDecision("NONE", "NO_REPAIR_REQUESTED")
    if review.review_group_id != group.semantic_review_group_id:
        return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_GROUP_ID_MISMATCH")
    by_id = {item[0].request.display_id: item[0] for item in members}
    repairable = set(group.repairable_work_order_ids)
    instructions: list[str] = []
    for issue in review.issues:
        if not issue.repair_instruction or not str(issue.repair_instruction).strip():
            return GroupRepairGateDecision("HUMAN_REQUIRED", "REPAIR_INSTRUCTION_OR_INDICES_MISSING")
        if issue.logical_work_order_id not in by_id:
            return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_THIRD_WORK_ORDER")
        if issue.logical_work_order_id not in repairable:
            return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_SEALED_MEMBER")
        provider_request = by_id[issue.logical_work_order_id]
        if issue.unit_uid != provider_request.request.unit_uid:
            return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_UNIT_UID_MISMATCH")
        protected = {
            index for index, segment in enumerate(provider_request.request.source_segments) if segment.kind == "protected"
        }
        valid = set(range(len(provider_request.request.source_segments)))
        if not issue.affected_segment_indices:
            return GroupRepairGateDecision("HUMAN_REQUIRED", "REPAIR_INSTRUCTION_OR_INDICES_MISSING")
        if any(index not in valid for index in issue.affected_segment_indices):
            return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_SCOPE_OUTSIDE_MEMBER")
        if any(index in protected for index in issue.affected_segment_indices):
            return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_PROTECTED_SCOPE")
        instructions.append(str(issue.repair_instruction).strip())
    named_indices: dict[str, set[int]] = {}
    for issue in review.issues:
        named_indices.setdefault(issue.logical_work_order_id, set()).update(issue.affected_segment_indices)
    requested_empty = dict(intentional_empty_authorizations or {})
    allowed: list[SemanticGroupAllowedRepair] = []
    for member in group.members:
        if not member.repairable or member.sealed:
            continue
        provider_request = by_id.get(member.display_id)
        if provider_request is None:
            return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_REQUEST_BINDING_MISMATCH")
        indices = non_protected_indices(provider_request.request)
        if not indices:
            continue
        named = named_indices.get(member.display_id, set())
        if member.display_id == group.current_display_id:
            permitted = indices
        else:
            permitted = sorted({indices[-1]} | (named & set(indices)))
        if not permitted:
            continue
        authorizations = list(requested_empty.get(member.display_id, ()))
        review_indices = named_indices.get(member.display_id, set())
        review_codes = {issue.code.casefold() for issue in review.issues if issue.logical_work_order_id == member.display_id}
        for authorization in authorizations:
            if authorization.empty_index not in set(permitted) or authorization.empty_index not in review_indices:
                return GroupRepairGateDecision("HUMAN_REQUIRED", "REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_SCOPE_INVALID")
            if not {code.casefold() for code in authorization.evidence_codes} & review_codes:
                return GroupRepairGateDecision("HUMAN_REQUIRED", "REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_EVIDENCE_MISSING")
        allowed.append(
            SemanticGroupAllowedRepair(
                logical_work_order_id=member.display_id,
                unit_uid=member.unit_uid,
                allowed_segment_indices=permitted,
                source_fingerprint=provider_request.request_binding_sha256,
                intentional_empty_authorizations=authorizations,
            )
        )
    if not allowed:
        return GroupRepairGateDecision("HUMAN_REQUIRED", "GROUP_REPAIR_NO_PERMITTED_SEGMENTS")
    contract = SemanticGroupRepairContract(
        policy_id=SEMANTIC_GROUP_POLICY,
        review_group_id=group.semantic_review_group_id,
        member_work_order_ids=list(group.member_work_order_ids),
        member_unit_uids=[member.unit_uid for member in group.members],
        group_hash=group.group_hash,
        source_span_hash=sha256(group.full_source_span.encode("utf-8")).hexdigest(),
        allowed_repairs=allowed,
        immutable_raw_shas={key: value for key, value in raw_shas.items() if key in set(group.member_work_order_ids)},
        repair_instruction=" | ".join(instructions)
        if instructions
        else "Redistribute natural Chinese across allowed unsealed group members. Keep segment ownership.",
    )
    return GroupRepairGateDecision("REPAIR", "SEMANTIC_GROUP_GATE", contract=contract, policy_id=SEMANTIC_GROUP_POLICY)
