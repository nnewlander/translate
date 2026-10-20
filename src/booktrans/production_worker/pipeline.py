"""In-memory unit pipeline: Router → provider → Harness QA → reviewer → gated repair.

Providers remain untrusted.  This module does not persist RAW, change selection,
or decide Control Plane policy; it only consults the existing registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import TranslationView
from booktrans.providers.failures import classify_provider_exception
from booktrans.providers.qwen_max import (
    ApprovedRepairContract,
    RepairProviderResult,
    ReviewResult,
    ReviewerProviderResult,
    _validate_review_result,
)
from booktrans.providers.qwen_mt import relevant_aids
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.router import (
    REJECTED_ROUTES,
    ROUTE_QWEN_MAX_STRUCTURED,
    ROUTE_QWEN_MT_PLUS,
    RouteDecision,
    route_semantic_unit,
)
from booktrans.providers.translation_provider import (
    ProviderContractGap,
    TranslationCandidate,
    TranslationProviderRequest,
    project_plain_candidate_to_raw,
)
from booktrans.production_worker.qa import QaFn, qa_codes
from booktrans.production_worker.numeric_recovery import failing_number_segment_indices
from booktrans.production_worker.human_required_triage import retain_review_issues
from booktrans.production_worker.review_issue_policy import AUTO_POLISH, BLOCKING_SEMANTIC, COSMETIC_DEBT, review_issue_severities
from booktrans.production_worker.reviewer_dispatch_policy import REVIEW_POLICY_V2_FASTPATH, reviewer_dispatch_reason


class MtProvider(Protocol):
    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate: ...


class MaxProvider(Protocol):
    def translate_structured(self, request: TranslationProviderRequest) -> Any: ...


class ReviewerProvider(Protocol):
    def review_with_provenance(
        self,
        request: TranslationProviderRequest,
        candidate: TranslationResult,
        *,
        deterministic_qa_summary: Mapping[str, Any],
        semantic_context: Mapping[str, Any] | None = None,
    ) -> ReviewerProviderResult: ...


class RepairProvider(Protocol):
    def repair(
        self,
        request: TranslationProviderRequest,
        current: TranslationResult,
        contract: Any,
    ) -> TranslationResult: ...


@dataclass
class WorkerProviders:
    mt: MtProvider
    structured: MaxProvider
    reviewer: ReviewerProvider
    repair: RepairProvider


def error_directed_protocol_retry_feedback(
    request: TranslationProviderRequest,
    error: ProviderContractGap,
    *,
    lane: str,
    permitted_segment_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Build the sole retry's structural feedback without supplying wording.

    The feedback is intentionally request-identity preserving.  It names only
    the rejected wire-contract field, any deterministically known indices, and
    exact Harness-owned source spans.  It never proposes translated text.
    """
    details = dict(error.details)
    rule = str(details.get("violated_contract_rule") or error.code)
    field = str(details.get("invalid_field") or {
        "REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING": "issues[*].repair_instruction",
        "REVIEWER_CONTRACT_GAP_SEGMENT_INDEX_INVALID": "issues[*].affected_segment_indices",
        "REPAIR_CONTRACT_GAP_EMPTY_TARGET_TEXT": "patches[*].target_text",
    }.get(error.code, "structured_response"))
    raw_indices = details.get("invalid_indices", details.get("invalid_index"))
    if raw_indices is None:
        indices = list(permitted_segment_indices or range(len(request.request.source_segments)))
    elif isinstance(raw_indices, int):
        indices = [raw_indices]
    else:
        indices = [int(index) for index in raw_indices if isinstance(index, int)]
    indices = [index for index in indices if 0 <= index < len(request.request.source_segments)]
    return {
        "lane": lane,
        "error_code": error.code,
        "invalid_field": field,
        "invalid_indices": indices,
        "violated_contract_rule": rule,
        "source_segment_bindings": [
            {"index": index, "source_fragment": request.request.source_segments[index].text}
            for index in indices
        ],
        "instruction": "Preserve the exact task identity and segment indices. Correct only the stated schema violation; do not redistribute source responsibility.",
    }


def review_with_contract_boundary(
    reviewer: ReviewerProvider,
    request: TranslationProviderRequest,
    candidate: TranslationResult,
    *,
    deterministic_qa_summary: Mapping[str, Any],
    semantic_context: Mapping[str, Any] | None = None,
    protocol_retry: bool = False,
    protocol_retry_feedback: Mapping[str, Any] | None = None,
) -> ReviewerProviderResult:
    """Return only a locally validated, task-bound reviewer result.

    The Qwen adapter validates its wire JSON before returning.  This small
    production boundary additionally refuses an adapter result whose local
    candidate/request binding is not the exact task supplied by the worker.
    Test adapters which do not implement the optional retry method retain the
    same task call; only the real provider receives the retry reminder.
    """
    if protocol_retry_feedback is not None:
        request = replace(request, protocol_retry_feedback=dict(protocol_retry_feedback))
    retry = getattr(reviewer, "review_retry_with_provenance", None)
    if protocol_retry and callable(retry):
        result = retry(
            request,
            candidate,
            deterministic_qa_summary=deterministic_qa_summary,
            semantic_context=semantic_context,
        )
    else:
        result = reviewer.review_with_provenance(
            request,
            candidate,
            deterministic_qa_summary=deterministic_qa_summary,
            semantic_context=semantic_context,
        )
    if not isinstance(result, ReviewerProviderResult):
        from booktrans.providers.translation_provider import ProviderContractGap

        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_RESULT_TYPE_INVALID")
    if (
        result.candidate.unit_uid != request.request.unit_uid
        or result.candidate.display_id != request.request.display_id
        or result.candidate.request_binding_sha256 != request.request_binding_sha256
    ):
        from booktrans.providers.translation_provider import ProviderContractGap

        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_BINDING_MISMATCH")
    _validate_review_result(request, result.review)
    return result


def repair_with_contract_boundary(
    repair: RepairProvider,
    request: TranslationProviderRequest,
    current: TranslationResult,
    contract: ApprovedRepairContract,
    *,
    protocol_retry: bool = False,
    protocol_retry_feedback: Mapping[str, Any] | None = None,
) -> RepairProviderResult:
    """Return only a closed-world, current-head-bound repair proposal.

    This is intentionally a small adapter boundary rather than another
    recovery engine.  Provider adapters validate their wire payload; the
    worker additionally verifies that the returned proposal still belongs to
    this request, current candidate, and pre-authorized segment allowlist.
    """
    from booktrans.providers.translation_provider import ProviderContractGap, TranslationCandidate

    if protocol_retry_feedback is not None:
        request = replace(request, protocol_retry_feedback=dict(protocol_retry_feedback))
    retry = getattr(repair, "repair_retry_with_provenance", None)
    with_provenance = getattr(repair, "repair_with_provenance", None)
    if protocol_retry and callable(retry):
        result = retry(request, current, contract)
    elif callable(with_provenance):
        result = with_provenance(request, current, contract)
    else:
        repaired = repair.repair(request, current, contract)
        result = RepairProviderResult(
            repaired=repaired,
            candidate=TranslationCandidate(
                provider="REPAIR_PROVIDER_WITHOUT_PROVENANCE",
                model="unknown",
                unit_uid=request.request.unit_uid,
                display_id=request.request.display_id,
                request_binding_sha256=request.request_binding_sha256,
                translation_options_sha256=request.translation_options_sha256,
                source_sha256=request.request.source_sha256,
                response_text="",
                response_id=None,
                requested_at="",
                responded_at="",
                attempt_count=0,
                usage={},
            ),
        )
    if not isinstance(result, RepairProviderResult):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_RESPONSE_SHAPE_INVALID")
    if (
        result.candidate.unit_uid != request.request.unit_uid
        or result.candidate.display_id != request.request.display_id
        or result.candidate.request_binding_sha256 != request.request_binding_sha256
    ):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_RESULT_BINDING_MISMATCH")
    if result.repaired.unit_uid != current.unit_uid or len(result.repaired.target_segments) != len(current.target_segments):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_CURRENT_CANDIDATE_MISMATCH")
    allowed = set(contract.permitted_segment_indices)
    for index, (before, after) in enumerate(zip(current.target_segments, result.repaired.target_segments, strict=True)):
        if (before.kind, before.anchor_ref, before.island_ref) != (after.kind, after.anchor_ref, after.island_ref):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_RESULT_BINDING_MISMATCH")
        if before.text != after.text and index not in allowed:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_SCOPE_EXPANDED")
    return result


@dataclass
class UnitOutcome:
    request: CalibrationRequest
    decision: RouteDecision
    raw: TranslationResult | None = None
    working: TranslationResult | None = None
    candidate: TranslationCandidate | None = None
    qa: dict[str, Any] | None = None
    review: ReviewResult | None = None
    second_review: ReviewResult | None = None
    review_model_echo_unit_uid: str | None = None
    review_model_echo_id_mismatch: bool = False
    reviewer_dispatch: str | None = None
    final_status: str = "PENDING"
    failure_family: str | None = None
    failure_code: str | None = None
    repair_contract_error: Exception | None = None
    repair_applied: bool = False
    repair_policy_id: str | None = None
    human_required: bool = False
    semantic_terminal: str | None = None
    blocked: bool = False
    latency_seconds: float | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    review_usage: dict[str, Any] = field(default_factory=dict)
    review_latency_seconds: float | None = None
    second_review_usage: dict[str, Any] = field(default_factory=dict)
    second_review_latency_seconds: float | None = None
    repair_usage: dict[str, Any] = field(default_factory=dict)
    repair_latency_seconds: float | None = None
    reviewer_api_calls: int = 0
    repair_api_calls: int = 0
    translation_api_calls: int = 0
    recovered_from: str | None = None
    reconstructed_provenance: dict[str, Any] = field(default_factory=dict)
    registry_decision: dict[str, Any] = field(default_factory=dict)


def enrich_request(
    request: CalibrationRequest,
    *,
    translation_bible: Path | None,
    approved_examples: tuple = (),
) -> TranslationProviderRequest:
    base = TranslationProviderRequest(request=request)
    if translation_bible is None:
        return base
    return relevant_aids(base, translation_bible=translation_bible, approved_examples=approved_examples)


def translate_unit(
    provider_request: TranslationProviderRequest,
    providers: WorkerProviders,
    decision: RouteDecision,
) -> tuple[TranslationCandidate, TranslationResult]:
    if decision.route in REJECTED_ROUTES:
        raise RuntimeError(f"REJECTED_PROVIDER_ROUTE:{decision.route}")
    if decision.route == ROUTE_QWEN_MT_PLUS:
        candidate = providers.mt.translate(provider_request)
        raw = project_plain_candidate_to_raw(provider_request, candidate)
        return candidate, raw
    if decision.route != ROUTE_QWEN_MAX_STRUCTURED:
        raise RuntimeError(f"UNKNOWN_PROVIDER_ROUTE:{decision.route}")
    result = providers.structured.translate_structured(replace(provider_request, output_contract_version="booktrans.structured-translation.v2"))
    return result.candidate, result.raw


def _latency_seconds(candidate: TranslationCandidate | None) -> float | None:
    if candidate is None:
        return None
    try:
        return (
            datetime.fromisoformat(candidate.responded_at) - datetime.fromisoformat(candidate.requested_at)
        ).total_seconds()
    except (TypeError, ValueError):
        return None


def _qa_summary(record: Mapping[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {"passed": False}
    return {
        "passed": bool(record.get("passed")),
        "codes": qa_codes(record),
        "display_id": record.get("display_id"),
        "unit_uid": record.get("unit_uid"),
    }


def _registry_evidence_for_qa(record: Mapping[str, Any], *, source_sha_valid: bool) -> dict[str, Any]:
    codes = qa_codes(record)
    unique = set(codes)
    classification = codes[0] if len(unique) == 1 else "FINAL_DETERMINISTIC_QA_FAILED"
    return {
        "failure_classification": classification if classification in {"DOI_PRESERVATION"} else "FINAL_DETERMINISTIC_QA_FAILED",
        "source_sha_valid": source_sha_valid,
        "raw_required": True,
        "raw_sha_valid": True,
        "sparse_local": True,
        "qa_codes": codes,
        "repair_contract": {"unit_uid": record.get("unit_uid"), "display_id": record.get("display_id"), "qa_codes": codes},
    }


def process_unit(
    request: CalibrationRequest,
    providers: WorkerProviders,
    *,
    view: TranslationView | None = None,
    known_unit_ids: set[str] | None = None,
    qa_fn: QaFn | None = None,
    translation_bible: Path | None = None,
    allow_semantic_narrow: bool = True,
    existing_raw: TranslationResult | None = None,
    existing_candidate: TranslationCandidate | None = None,
    semantic_context: Mapping[str, Any] | None = None,
    review_policy: str = "legacy",
    reviewer_protocol_retry: bool = False,
    repair_protocol_retry: bool = False,
    existing_review: ReviewResult | None = None,
) -> UnitOutcome:
    """Run one semantic unit.  When existing_raw is supplied, skip translation."""
    decision = route_semantic_unit(request)
    outcome = UnitOutcome(request=request, decision=decision)
    provider_request = enrich_request(request, translation_bible=translation_bible)
    if existing_raw is not None:
        candidate = existing_candidate
        raw = existing_raw
        outcome.recovered_from = "raw"
        outcome.translation_api_calls = 0
    else:
        try:
            candidate, raw = translate_unit(provider_request, providers, decision)
        except Exception as error:
            classified = classify_provider_exception(error)
            outcome.failure_family = classified.family
            outcome.failure_code = classified.code
            outcome.final_status = classified.family
            outcome.human_required = classified.family != "PROVIDER_TRANSIENT"
            return outcome
        outcome.translation_api_calls = 1
    outcome.candidate = candidate
    outcome.raw = raw
    outcome.working = raw
    outcome.latency_seconds = _latency_seconds(candidate)
    outcome.usage = dict(candidate.usage) if candidate is not None else {}

    if qa_fn is None:
        outcome.final_status = "TRANSLATED"
        return outcome
    if view is None or known_unit_ids is None:
        raise RuntimeError("QA_VIEW_REQUIRED")
    qa_record = qa_fn(view, request, raw, known_unit_ids)
    outcome.qa = qa_record
    working = raw
    if not qa_record.get("passed"):
        deterministic_codes = qa_codes(qa_record)
        qa_segment_indices = (
            failing_number_segment_indices(request, raw)
            if set(deterministic_codes) == {"NUMBER_PRESERVATION"}
            else None
        )
        gate = decide_approved_repair_contract(
            provider_request,
            working,
            qa_codes=deterministic_codes,
            # NUMBER recovery is intentionally bounded to the exact
            # non-protected segments identified from the immutable RAW.  An
            # absent or ambiguous mapping still fails closed in the gate.
            qa_segment_indices=() if qa_segment_indices is None else qa_segment_indices,
            review=None,
            registry_evidence=_registry_evidence_for_qa(qa_record, source_sha_valid=True),
            allow_semantic_narrow=False,
        )
        if gate.action != "REPAIR" or gate.contract is None:
            outcome.final_status = "DETERMINISTIC_QA_FAILED"
            outcome.failure_family = "LOCAL_CONTENT_QA_FAILURE"
            outcome.failure_code = ",".join(qa_codes(qa_record)) or "DETERMINISTIC_QA_FAILED"
            outcome.human_required = True
            return outcome
        try:
            repaired = repair_with_contract_boundary(
                providers.repair, provider_request, working, gate.contract,
                protocol_retry=repair_protocol_retry,
            ).repaired
            outcome.repair_api_calls += 1
        except Exception as error:
            classified = classify_provider_exception(error)
            outcome.final_status = classified.family
            outcome.failure_family = classified.family
            outcome.failure_code = classified.code
            if classified.code.startswith("REPAIR_CONTRACT_GAP_"):
                outcome.repair_contract_error = error
            outcome.human_required = True
            return outcome
        repaired_qa = qa_fn(view, request, repaired, known_unit_ids)
        if not repaired_qa.get("passed"):
            outcome.qa = repaired_qa
            outcome.final_status = "DETERMINISTIC_QA_FAILED"
            outcome.human_required = True
            return outcome
        working = repaired
        outcome.working = working
        outcome.qa = repaired_qa
        outcome.repair_applied = True
        outcome.repair_policy_id = gate.policy_id

    outcome.reviewer_dispatch = reviewer_dispatch_reason(request, decision, semantic_context=semantic_context, repaired=outcome.repair_applied)
    if review_policy == REVIEW_POLICY_V2_FASTPATH and outcome.reviewer_dispatch == "DIRECT_CORE_PASS":
        outcome.final_status = "ACCEPT"
        outcome.semantic_terminal = "MODEL_ACCEPTED"
        return outcome
    before_review = [(segment.text, segment.kind, segment.anchor_ref, segment.island_ref) for segment in working.target_segments]
    if existing_review is None:
        review_result = review_with_contract_boundary(
            providers.reviewer,
            provider_request,
            working,
            deterministic_qa_summary=_qa_summary(outcome.qa),
            semantic_context=semantic_context,
            protocol_retry=reviewer_protocol_retry,
        )
        outcome.reviewer_api_calls += 1
        outcome.review_model_echo_unit_uid = review_result.model_echo_unit_uid
        outcome.review_model_echo_id_mismatch = bool(
            review_result.model_echo_unit_uid
            and review_result.model_echo_unit_uid != request.unit_uid
        )
        outcome.review = retain_review_issues(review_result.review, semantic_context)
    else:
        outcome.review = existing_review
    after_review = [(segment.text, segment.kind, segment.anchor_ref, segment.island_ref) for segment in working.target_segments]
    if before_review != after_review:
        raise RuntimeError("REVIEWER_MUTATED_CANDIDATE")
    # The provider has already locally rebound the semantic response to this
    # request. A legacy LLM UID echo is diagnostic only, never identity.
    if outcome.review.verdict == "ACCEPT":
        outcome.final_status = "ACCEPT"
        outcome.semantic_terminal = "MODEL_ACCEPTED"
        return outcome
    if outcome.review.verdict == "HUMAN_REQUIRED":
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        outcome.semantic_terminal = "HUMAN_REQUIRED"
        return outcome

    gate = decide_approved_repair_contract(
        provider_request,
        working,
        qa_codes=(),
        review=outcome.review,
        allow_semantic_narrow=allow_semantic_narrow,
    )
    if gate.action != "REPAIR" or gate.contract is None:
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        return outcome
    original = working
    try:
        repaired = repair_with_contract_boundary(
            providers.repair, provider_request, working, gate.contract,
            protocol_retry=repair_protocol_retry,
        ).repaired
        outcome.repair_api_calls += 1
    except Exception as error:
        classified = classify_provider_exception(error)
        outcome.working = original
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.failure_family = classified.family
        outcome.failure_code = classified.code
        if classified.code.startswith("REPAIR_CONTRACT_GAP_"):
            outcome.repair_contract_error = error
        outcome.human_required = True
        return outcome
    repaired_qa = qa_fn(view, request, repaired, known_unit_ids)
    if not repaired_qa.get("passed"):
        outcome.working = original
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        return outcome
    second = review_with_contract_boundary(
        providers.reviewer,
        provider_request,
        repaired,
        deterministic_qa_summary=_qa_summary(repaired_qa),
        semantic_context=semantic_context,
        protocol_retry=reviewer_protocol_retry,
    )
    outcome.reviewer_api_calls += 1
    outcome.second_review = retain_review_issues(second.review, semantic_context)
    second_severities = review_issue_severities(issue.code for issue in outcome.second_review.issues)
    if (
        outcome.second_review.verdict != "HUMAN_REQUIRED"
        and BLOCKING_SEMANTIC not in second_severities
        and second_severities <= {AUTO_POLISH, COSMETIC_DEBT}
    ):
        # The preceding exact-scope Qwen repair is the one bounded local
        # polish. A remaining non-semantic style/spacing preference is debt,
        # not an escalation to the human semantic gate.
        outcome.working = repaired
        outcome.qa = repaired_qa
        outcome.repair_applied = True
        outcome.repair_policy_id = AUTO_POLISH if AUTO_POLISH in second_severities else COSMETIC_DEBT
        outcome.final_status = "ACCEPT"
        outcome.semantic_terminal = "MODEL_ACCEPTED"
        return outcome
    if outcome.second_review.verdict != "ACCEPT":
        outcome.working = original
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        return outcome
    outcome.working = repaired
    outcome.qa = repaired_qa
    outcome.repair_applied = True
    outcome.repair_policy_id = gate.policy_id
    outcome.final_status = "ACCEPT"
    outcome.semantic_terminal = "MODEL_ACCEPTED"
    return outcome
