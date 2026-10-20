"""Narrow semantic-repair gate.  Reviewer recommendations never auto-apply."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from booktrans.domain.calibration import TranslationResult
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy
from booktrans.providers.qwen_max import ApprovedRepairContract, IntentionalEmptyAuthorization, ReviewResult
from booktrans.providers.translation_provider import TranslationProviderRequest


REGISTRY_REPAIR_POLICIES = frozenset({"NUMERIC_REPRESENTATION", "DOI_PRESERVATION"})
SEMANTIC_NARROW_POLICY = "SEMANTIC_NARROW_SEGMENT"
QA_CODE_TO_POLICY = {
    "NUMBER_PRESERVATION": "NUMERIC_REPRESENTATION",
    "DOI_PRESERVATION": "DOI_PRESERVATION",
}


@dataclass(frozen=True)
class RepairGateDecision:
    """Either an approved contract or an explicit fail-closed outcome."""

    action: str
    reason: str
    contract: ApprovedRepairContract | None = None
    policy_id: str | None = None


def decide_approved_repair_contract(
    provider_request: TranslationProviderRequest,
    current: TranslationResult,
    *,
    qa_codes: Sequence[str] = (),
    qa_segment_indices: Sequence[int] = (),
    review: ReviewResult | None = None,
    registry_evidence: Mapping[str, Any] | None = None,
    allow_semantic_narrow: bool = True,
    intentional_empty_authorizations: Sequence[IntentionalEmptyAuthorization] = (),
) -> RepairGateDecision:
    """Build a contract only when scope, protection, instruction, and policy are exact.

    Reviewer REPAIR never expands Registry recovery.  NUMBER/DOI repair is
    accepted only when ``decide_recovery_policy`` returns an allowlisted policy
    and the affected indices are already explicit.  Unconditional auto-edit is
    refused.
    """
    request = provider_request.request
    if current.unit_uid != request.unit_uid:
        return RepairGateDecision("HUMAN_REQUIRED", "REPAIR_UNIT_MISMATCH")
    protected = {
        index for index, segment in enumerate(request.source_segments) if segment.kind == "protected"
    }
    valid = set(range(len(request.source_segments)))

    registry_decision = decide_recovery_policy(dict(registry_evidence or {})) if registry_evidence else None
    if registry_decision and registry_decision.get("eligible") and registry_decision.get("policy_id") in REGISTRY_REPAIR_POLICIES:
        indices = _unique_indices(qa_segment_indices, valid=valid, protected=protected)
        if indices is None:
            return RepairGateDecision("HUMAN_REQUIRED", "REGISTRY_REPAIR_SCOPE_AMBIGUOUS", policy_id=str(registry_decision["policy_id"]))
        instruction = _registry_instruction(str(registry_decision["policy_id"]), qa_codes)
        return RepairGateDecision(
            "REPAIR",
            "REGISTRY_APPROVED_CONTRACT",
            contract=_contract(provider_request, str(registry_decision["policy_id"]), indices, instruction),
            policy_id=str(registry_decision["policy_id"]),
        )

    codes = [str(code) for code in qa_codes if code]
    if codes and not (registry_decision and registry_decision.get("eligible") and registry_decision.get("policy_id") in REGISTRY_REPAIR_POLICIES):
        # QA failures cannot be widened by a reviewer recommendation.
        return RepairGateDecision("HUMAN_REQUIRED", "QA_REPAIR_NOT_REGISTRY_APPROVED")

    if review is None or review.verdict != "REPAIR":
        if review is not None and review.verdict == "HUMAN_REQUIRED":
            return RepairGateDecision("HUMAN_REQUIRED", "REVIEWER_HUMAN_REQUIRED")
        return RepairGateDecision("NONE", "NO_REPAIR_REQUESTED")

    if not allow_semantic_narrow:
        return RepairGateDecision("HUMAN_REQUIRED", "SEMANTIC_REPAIR_NOT_POLICY_ALLOWED")
    if not review.issues:
        return RepairGateDecision("HUMAN_REQUIRED", "REPAIR_SCOPE_AMBIGUOUS")
    indices: list[int] = []
    instructions: list[str] = []
    for issue in review.issues:
        if not issue.affected_segment_indices or not issue.repair_instruction or not str(issue.repair_instruction).strip():
            return RepairGateDecision("HUMAN_REQUIRED", "REPAIR_INSTRUCTION_OR_INDICES_MISSING")
        indices.extend(issue.affected_segment_indices)
        instructions.append(str(issue.repair_instruction).strip())
    unique = _unique_indices(indices, valid=valid, protected=protected)
    if unique is None:
        return RepairGateDecision("HUMAN_REQUIRED", "REPAIR_SCOPE_AMBIGUOUS")
    try:
        structural_authorizations = _validated_structural_authorizations(
            intentional_empty_authorizations, review=review, permitted=unique
        )
    except ValueError as exc:
        return RepairGateDecision("HUMAN_REQUIRED", str(exc))
    return RepairGateDecision(
        "REPAIR",
        "SEMANTIC_NARROW_GATE",
        contract=_contract(
            provider_request, SEMANTIC_NARROW_POLICY, unique, " | ".join(instructions),
            intentional_empty_authorizations=structural_authorizations,
        ),
        policy_id=SEMANTIC_NARROW_POLICY,
    )


def _unique_indices(values: Sequence[int], *, valid: set[int], protected: set[int]) -> list[int] | None:
    if not values:
        return None
    unique = sorted(set(int(value) for value in values))
    if any(index not in valid for index in unique):
        return None
    if any(index in protected for index in unique):
        return None
    return unique


def _registry_instruction(policy_id: str, qa_codes: Sequence[str]) -> str:
    if policy_id == "NUMERIC_REPRESENTATION":
        return "Restore only the exact source numeric representation in the permitted segments. Do not retranslate."
    if policy_id == "DOI_PRESERVATION":
        return "Restore the exact source DOI literal in the permitted segments. Do not retranslate."
    return "Apply only the registry-approved repair in the permitted segments. Codes: " + ",".join(qa_codes)


def _validated_structural_authorizations(
    authorizations: Sequence[IntentionalEmptyAuthorization], *, review: ReviewResult, permitted: Sequence[int]
) -> list[IntentionalEmptyAuthorization]:
    """Accept only harness-supplied authorizations supported by this review.

    The provider never supplies this collection: it is an explicit input to the
    local gate and is bound to the exact reviewer issue scope/evidence.
    """
    review_indices = {index for issue in review.issues for index in issue.affected_segment_indices}
    review_codes = {issue.code.casefold() for issue in review.issues}
    permitted_set = set(permitted)
    for authorization in authorizations:
        if authorization.empty_index not in permitted_set or authorization.empty_index not in review_indices:
            raise ValueError("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_SCOPE_INVALID")
        if not {code.casefold() for code in authorization.evidence_codes} & review_codes:
            raise ValueError("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_EVIDENCE_MISSING")
    return list(authorizations)


def _contract(
    provider_request: TranslationProviderRequest, policy_id: str, indices: Sequence[int], instruction: str,
    *, intentional_empty_authorizations: Sequence[IntentionalEmptyAuthorization] = (),
) -> ApprovedRepairContract:
    return ApprovedRepairContract(
        policy_id=policy_id,
        unit_uid=provider_request.request.unit_uid,
        source_fingerprint=provider_request.request_binding_sha256,
        permitted_segment_indices=list(indices),
        repair_instruction=instruction,
        intentional_empty_authorizations=list(intentional_empty_authorizations),
    )
