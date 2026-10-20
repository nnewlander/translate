"""Durable B022 checkpoint write path.  RAW is append-once; Codex is not invoked."""

from __future__ import annotations

from hashlib import sha256
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping
import json
import re
from datetime import datetime, timezone

from booktrans.domain.calibration import ExplicitNumericRepresentationEquivalence, TranslationResult
from booktrans.harness.batch_manifest import (
    append_sealed_checkpoint, publish_manifest_successor,
    resolve_authoritative_manifest, sealed_checkpoint_manifest_projection,
)
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.translation_artifacts import normalize_or_validate_response_artifact
from booktrans.providers.qwen_max import ReviewIssue, ReviewResult, _validate_review_result, classify_source_segment_mode, reviewer_request_payload
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.router import route_semantic_unit
from booktrans.providers.translation_provider import ProviderContractGap
from booktrans.production_worker.artifacts import candidate_provenance, write_json_once, write_json_once_or_validate, write_jsonl_once, write_jsonl_once_or_validate
from booktrans.production_worker.candidate_authority import (
    CandidateAuthorityError,
    CandidateMutation,
    candidate_sha as _candidate_sha,
    require_parent_is_current,
    resolve_authoritative_candidate,
)
from booktrans.production_worker.canary import (
    PRODUCTION_BATCH,
    PRODUCTION_CHECKPOINT,
    SEMANTIC_PASS_TERMINALS,
    semantic_terminal_of_outcome,
)
from booktrans.production_worker.group_repair import production_sealed_work_order_ids
from booktrans.production_worker.deterministic_recovery import (
    correct_numeric_anchor_duplication,
    materialize_index_locator,
    restore_exact_source_urls,
)
from booktrans.production_worker.handoff import write_worker_handoff
from booktrans.production_worker.human_required_triage import (
    classify_human_required,
)
from booktrans.production_worker.pipeline import (
    UnitOutcome,
    WorkerProviders,
    enrich_request,
    process_unit,
    repair_with_contract_boundary,
    review_with_contract_boundary,
    translate_unit,
)
from booktrans.production_worker.qa import harness_qa_record, qa_codes
from booktrans.production_worker.review_issue_policy import AUTO_POLISH, BLOCKING_SEMANTIC, COSMETIC_DEBT, review_issue_severities
from booktrans.production_worker.reviewer_dispatch_policy import REVIEW_POLICY_V2_FASTPATH
from booktrans.production_worker.selection import (
    CHECKPOINT_SIZE,
    D050PreflightError,
    FrozenSelectionError,
    checkpoint_count_for_target,
    checkpoint_number,
    d050_paths_for_checkpoint,
    ensure_checkpoint_d050_preflight,
    load_checkpoint_d050_registry,
    load_json,
    production_checkpoint_path,
    validate_frozen_checkpoint_members,
)
from booktrans.production_worker.semantic_review_run import (
    build_unit_group,
    candidate_text_map,
    context_units_for_order,
    segment_count_map,
)
from booktrans.production_worker.unit_store import (
    append_jsonl_fsync,
    append_raw_response_line,
    ensure_requests_artifact,
    persist_request_bound_provider_reacquisition,
    resolve_effective_translation_request,
    resolve_effective_provider_acquisition,
    load_raw_by_uid,
    load_stage,
    load_unit_results,
    persist_stage_once,
    persist_unit_result,
    unit_path,
)


REVIEWER_CONTRACT_FAILURE = "PROVIDER_CONTRACT_FAILURE"
REVIEWER_CONTRACT_INVALID_AFTER_RETRY = "CONTRACT_INVALID_AFTER_RETRY"
MAX_REVIEWER_CONTRACT_ATTEMPTS = 2
REPAIR_CONTRACT_INVALID_AFTER_RETRY = "REPAIR_CONTRACT_INVALID_AFTER_RETRY"
MAX_REPAIR_CONTRACT_ATTEMPTS = 2
MAX_PROVIDER_CONTRACT_ATTEMPTS = 2
SEMANTIC_RESCUE_B_REVIEW_ONLY = frozenset({"body.p.06994", "body.p.07005"})
D051_FINAL_REVIEW_IDS = frozenset({"body.p.06997"})
D050_EXACT_MARKER_RECOVERY_FAMILY = "D050_EXACT_MARKER_REPLAY"
D050_EXACT_MARKER_RECOVERY_STAGE = "d050_exact_marker_recovery_attempts"
D050_EXACT_MARKER_FINAL_REVIEW_RETRY_STAGE = "d050_exact_marker_recovery_final_review_retries"
D050_EXACT_MARKER_FINAL_REVIEW_CONTRACT_FAILURE_STAGE = "d050_exact_marker_recovery_final_review_contract_failures"
MAX_SEMANTIC_CORRECTIVE_ROUNDS_PER_EPISODE = 2
CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX = "semantic_rescue_current_head_corrective_round_2"


@dataclass(frozen=True)
class CurrentRawQaPayload:
    """Transient QA projection for one resolver-validated checkpoint input."""
    source_sha256: str
    batch_id: str
    checkpoint_id: str
    selection_sha256: str
    manifest_sha256: str
    effective_request_sha256: str
    acquisition_set_sha256: str
    request_binding_by_uid: Mapping[str, str]
    acquisition_binding_by_uid: Mapping[str, str]
    records_by_uid: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class CurrentUnitControlState:
    """Non-persisted current control projection; historical unit fields are audit only."""
    semantic_terminal: str | None
    human_required: bool
    final_status: str
    canonical_disposition: str


def _current_raw_qa_payload(*, requests, acquisitions: Mapping[str, TranslationResult], base_acquisitions: Mapping[str, TranslationResult],
                            views, known_unit_ids, qa, source_sha256: str, batch_id: str, checkpoint_id: str,
                            selection_sha256: str, manifest_sha256: str, effective_request_sha256: str,
                            request_binding_by_uid: Mapping[str, str],
                            historical_raw_qa_path: Path) -> CurrentRawQaPayload:
    """Project RAW QA once from the already-validated effective acquisitions."""
    if set(acquisitions) != {request.unit_uid for request in requests}:
        raise RuntimeError("CURRENT_RAW_QA_ACQUISITION_SET_INCOMPLETE")
    changed = any(base_acquisitions.get(uid) is None or base_acquisitions[uid].model_dump() != raw.model_dump() for uid, raw in acquisitions.items())
    if not changed and historical_raw_qa_path.is_file():
        report = json.loads(historical_raw_qa_path.read_text(encoding="utf-8"))
        records = {str(row.get("unit_uid")): row for row in report.get("records", []) if isinstance(row, Mapping)}
        if set(records) != set(acquisitions):
            raise RuntimeError("CURRENT_RAW_QA_HISTORICAL_SET_MISMATCH")
    else:
        records = {request.unit_uid: qa(views[request.display_id], request, acquisitions[request.unit_uid], known_unit_ids) for request in requests}
    acquisition_set_sha = _canonical_sha256([
        {"unit_uid": uid, "raw_sha256": _candidate_binding(acquisitions[uid])} for uid in sorted(acquisitions)
    ])
    return CurrentRawQaPayload(
        source_sha256, batch_id, checkpoint_id, selection_sha256, manifest_sha256,
        effective_request_sha256, acquisition_set_sha,
        dict(request_binding_by_uid),
        {uid: _candidate_binding(raw) for uid, raw in acquisitions.items()},
        records,
    )


def _missing_effective_acquisitions(requests, acquisitions: Mapping[str, TranslationResult]) -> list:
    """Return current-request rows still awaiting a bound provider acquisition.

    This is lifecycle readiness only.  It does not authorize QA, select a
    candidate, or create durable state: the normal acquisition writer remains
    responsible for each returned request.
    """
    return [request for request in requests if request.unit_uid not in acquisitions]


def _sentence_clause_count(text: str, *, source: bool) -> int:
    """Count explicit sentence clauses for a conservative repair guard.

    This does not claim to decide Chinese semantics. It ensures that a narrow
    repair has not silently removed an explicitly delimited source clause
    before we spend the final Reviewer call.
    """
    if source:
        return len(re.findall(r"[.!?]+", text))
    return len(re.findall(r"[。！？]+", text))


_TOC_DECIMAL_ORDINAL_LABEL = re.compile(r"^\d+\.$")
_TOC_NAMED_ROMAN_ORDINAL_LABEL = re.compile(r"^[A-Za-z][A-Za-z -]*\s+[IVXLCDM]+\.$", re.IGNORECASE)


def _is_toc_ordinal_label_segment(request, index: int, source) -> bool:
    """Recognize an existing structurally-proven TOC ordinal label.

    This is intentionally narrower than punctuation or text matching.  The
    source segment must already be an anchored, bold initial label with a
    separately anchored title segment in the same non-continuation request.
    That is the structural shape exported for TOC part/chapter entries; prose
    and question-style TOC titles retain the ordinary clause counter.
    """
    if (
        getattr(request, "continuation_hint", False)
        or index != 0
        or getattr(source, "kind", None) != "anchored"
        or not getattr(source, "anchor_ref", None)
    ):
        return False
    text = source.text.strip()
    if not (
        _TOC_DECIMAL_ORDINAL_LABEL.fullmatch(text)
        or _TOC_NAMED_ROMAN_ORDINAL_LABEL.fullmatch(text)
    ):
        return False
    anchors = {
        anchor.anchor_id: set(anchor.semantics)
        for anchor in getattr(request, "available_anchors", ())
    }
    if "bold" not in anchors.get(source.anchor_ref, set()):
        return False
    segments = tuple(getattr(request, "source_segments", ()))
    if len(segments) < 2:
        return False
    # A following anchored textual title is the independent structural proof:
    # ``Part I.``/``1.`` alone is insufficient to suppress a prose clause.
    return any(
        item.kind == "anchored" and bool(item.text.strip())
        for item in segments[index + 1:]
    )


def _repair_clause_coverage(request, base: TranslationResult, repaired: TranslationResult) -> dict[str, Any]:
    """Create deterministic pre-review completeness evidence for a repair."""
    rows: list[dict[str, Any]] = []
    passed = True
    for index, (source, prior_target, target) in enumerate(zip(request.source_segments, base.target_segments, repaired.target_segments, strict=True)):
        source_count = 0 if _is_toc_ordinal_label_segment(request, index, source) else _sentence_clause_count(source.text, source=True)
        prior_count = _sentence_clause_count(prior_target.text, source=False)
        target_count = _sentence_clause_count(target.text, source=False)
        # The guard applies only when the immutable RAW candidate visibly
        # carries the source's sentence boundaries. It then proves a repair
        # cannot drop one of those already-covered clauses.
        guarded = source_count > 0 and prior_count >= source_count
        row_passed = not guarded or target_count >= source_count
        rows.append({
            "segment_index": index,
            "source_clause_count": source_count,
            "base_candidate_clause_count": prior_count,
            "candidate_clause_count": target_count,
            "guarded": guarded,
            "passed": row_passed,
        })
        passed = passed and row_passed
    return {
        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_COMPLETENESS",
        "schema_version": 1,
        "display_id": request.display_id,
        "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(repaired),
        "method": "EXPLICIT_SENTENCE_CLAUSE_COVERAGE",
        "rows": rows,
        "passed": passed,
        "translation_api_calls": 0,
    }


def _queue_repair_is_invalid_omission_attempt(checkpoint_dir: Path, display_id: str) -> bool:
    """A durable final OMISSION makes the preceding queue repair non-budgetable."""
    resolution = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", display_id)
    final_payload = resolution.get("second_review") if isinstance(resolution, Mapping) else None
    if not isinstance(final_payload, Mapping):
        return False
    try:
        final = ReviewResult.model_validate(final_payload)
    except Exception:
        return False
    return any(issue.code.casefold() in {"omission", "content_loss", "missing_content"} for issue in final.issues)


def _queue_repair_is_invalid_context_attempt(checkpoint_dir: Path, display_id: str, group) -> bool:
    """Identify a queue attempt invalidated by a missing exact context member."""
    if group is None:
        return False
    context = getattr(group, "context", None)
    if context is None or not getattr(context, "next_semantic_unit_uid", None):
        return False
    current = str(getattr(group, "full_source_span", "")).rstrip()
    following = str(getattr(context, "next_text", "")).lstrip()
    if not current or not following or not current[-1:].isalpha() or not following[:1].islower():
        return False
    resolution = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", display_id)
    final_payload = resolution.get("second_review") if isinstance(resolution, Mapping) else None
    if not isinstance(final_payload, Mapping):
        return False
    try:
        final = ReviewResult.model_validate(final_payload)
    except Exception:
        return False
    return any(issue.code.casefold() == "incomplete_source_handling" for issue in final.issues)

# This is deliberately narrower than the reviewer severity policy.  It is the
# small production-continuation exception: only a local linguistic issue with
# an otherwise exact Core Harness result may be deferred to the batch release
# gate.  Content loss, binding, and deterministic-integrity issues remain
# production hard blocks.  A bounded queue repair may leave a fully-bound,
# complete candidate with only a localized wording defect; that is release
# debt, not a reason to stop the next production checkpoint.
_DEFERRED_SEMANTIC_DEBT_CODES = frozenset({
    "syntax_error", "syntax_unnatural", "fluency", "naturalness",
    "word_order", "local_word_order", "spacing", "spacing_error",
    "punctuation", "punctuation_error", "punctuation_formatting", "punctuation_spacing",
    "punctuation_space_inconsistency", "register_and_style",
    # A literal explanatory expansion of an idiom can be release debt when
    # Core/completeness/binding are exact; it is not content loss.
    "fidelity_added_meaning", "semantic_fidelity_addition",
})
_PRODUCTION_HARD_BLOCK_ISSUE_CODES = frozenset({
    "omission", "content_loss", "missing_content", "semantic_mismatch",
    "semantic_drift", "mistranslation", "unsupported_addition",
    "meaning_ambiguity", "terminology_canonical_name", "term_mistranslation",
    "number", "numeric", "url", "doi", "protected", "integrity", "binding",
})
_LOCALIZED_POST_RESOLUTION_DEBT_CODES = frozenset({
    "term_inconsistency", "semantic_mismatch", "register", "sense", "sense_error", "semantic_fidelity",
    "duplicate_content", "duplication_error",
})
_CONTAINED_SEMANTIC_DEBT_UNSAFE_CODES = frozenset({
    # These signal an authority or deterministic-integrity risk, not merely
    # an unresolved semantic judgement.  They cannot enter release debt even
    # after all bounded semantic lanes have been consumed.
    "meaning_ambiguity", "source_ambiguity", "source_authority_ambiguity",
    # Actual source loss is never a production-continuable debt.  A reviewer
    # may describe it as a local issue, but the missing source content itself
    # is a correctness boundary, not a release-only wording disagreement.
    "untranslated_content",
    "number", "numeric", "url", "doi", "protected", "binding",
})
_EXHAUSTED_CORRECTIVE_FIDELITY_CODES = frozenset({
    # This is intentionally a small, explicit exception for the second and
    # final corrective round.  It does not admit content loss or an open-ended
    # semantic repair loop.
    "semantic_mismatch", "unsupported_addition", "terminology",
    "term_inconsistency", "semantic_fidelity", "semantic_fidelity_addition",
    "fidelity_added_meaning",
})
_REJECTED_QUEUE_REPAIR_CODES = frozenset({
    "segment_misalignment", "segmentation_mismatch", "target_text_segment_mismatch",
    "omission", "content_loss", "missing_content",
    "unsupported_addition", "incomplete_source_handling",
})
_LEGACY_LAST_KNOWN_GOOD_DEBT_CODES = frozenset({
    "semantic_drift", "terminology", "term_inconsistency", "register",
})
_REJECTED_REPAIR_RELEASE_DEBT_CODES = _DEFERRED_SEMANTIC_DEBT_CODES | frozenset({
    # A complete, bound RAW head may retain one localized terminology/wording
    # drift after its only current-bound repair candidate was rejected.  It is
    # release debt, never model acceptance, and remains visible in the queue.
    "semantic_drift",
})


def _semantic_auto_recovery_contract(request, candidate: TranslationResult, review: ReviewResult, *, bible, group):
    """Return the existing narrow repair contract only for a recoverable severe issue.

    Severity remains the existing reviewer policy.  Recoverability is a
    separate, closed-world question: Core-clean current head, exact issue
    indices, and (for a continuation) an already-built group whose current
    member is explicitly repairable.  This is not a second severity system.
    """
    if review.verdict != "REPAIR" or BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in review.issues):
        return None
    if any(not issue.affected_segment_indices or not issue.repair_instruction for issue in review.issues):
        return None
    if group is not None and getattr(group, "continuation_relation", None) == "BODY_CONTINUATION":
        if request.display_id not in set(getattr(group, "member_work_order_ids", ())):
            return None
        if request.display_id not in set(getattr(group, "repairable_work_order_ids", ())):
            return None
    provider_request = enrich_request(request, translation_bible=bible)
    gate = decide_approved_repair_contract(
        provider_request, candidate, qa_codes=(), review=review, allow_semantic_narrow=True,
    )
    return gate.contract if gate.action == "REPAIR" and gate.contract is not None else None


def _corrective_semantic_repair_is_eligible(original_repair: Mapping[str, Any], final_review: ReviewResult) -> bool:
    """Permit one LKG-based corrective repair only inside the original scope."""
    contract = original_repair.get("contract")
    original_scope = contract.get("permitted_segment_indices") if isinstance(contract, Mapping) else None
    if not isinstance(original_scope, list) or not original_scope:
        return False
    if final_review.verdict != "REPAIR" or BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in final_review.issues):
        return False
    final_scope = {index for issue in final_review.issues for index in issue.affected_segment_indices}
    return bool(final_scope) and final_scope.issubset(set(original_scope)) and all(
        issue.repair_instruction for issue in final_review.issues
    )


def _rejected_repair_final_is_not_current_head(
    checkpoint_dir: Path, display_id: str, current_head: TranslationResult,
) -> bool:
    """Identify historical final-review evidence that belongs only to a rejected repair."""
    final = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", display_id)
    if not isinstance(final, Mapping):
        return False
    candidate_sha = final.get("candidate_binding_sha256")
    return isinstance(candidate_sha, str) and candidate_sha != _candidate_binding(current_head)


def _only_missing_verified_d050_markers(request, candidate: TranslationResult, qa_record: Mapping[str, Any]) -> bool:
    """Admit one repair only for an exact D050 marker-preservation failure."""
    markers = [
        binding.footnote_number
        for binding in request.verified_footnote_numeric_bindings
        if binding.unit_uid == request.unit_uid and binding.exact_source_marker_token in request.source_text
    ]
    target = "".join(segment.text for segment in candidate.target_segments)
    missing = [marker for marker in markers if marker not in target]
    diagnostics = qa_record.get("diagnostics")
    return bool(missing) and isinstance(diagnostics, list) and all(
        isinstance(item, Mapping)
        and item.get("code") == "NUMBER_PRESERVATION"
        and item.get("message") == f"Required source token(s) are absent from target: {missing[:3]}"
        for item in diagnostics
    )


def _d050_exact_marker_recovery_consumed(checkpoint_dir: Path, display_id: str) -> bool:
    """Return whether this recovery family has spent its one append-only attempt.

    The record is deliberately family-scoped.  In particular, a legacy
    ``semantic_rescue`` record is historical evidence, not a consumed D050
    exact-marker replay attempt (and vice versa).
    """
    return load_stage(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, display_id) is not None


def _d050_final_review_is_acceptable(review: ReviewResult) -> bool:
    return (
        review.verdict != "HUMAN_REQUIRED"
        and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in review.issues)
    )


def _d050_effective_final_review(
    *, checkpoint_dir: Path, display_id: str, unit_uid: str, candidate_sha256: str, request_binding_sha256: str,
) -> ReviewResult | None:
    """Return the one valid, current-bound D050 final review.

    The original final-review artifact is immutable.  A contract-invalid first
    response is historical evidence only; the one protocol retry is the sole
    later record that can become effective.
    """
    records = [
        load_stage(checkpoint_dir, "d050_exact_marker_recovery_final_reviews", display_id),
        load_stage(checkpoint_dir, D050_EXACT_MARKER_FINAL_REVIEW_RETRY_STAGE, display_id),
    ]
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if (
            record.get("candidate_binding_sha256") != candidate_sha256
            or record.get("request_binding_sha256") != request_binding_sha256
        ):
            continue
        payload = record.get("review")
        if not isinstance(payload, Mapping):
            continue
        try:
            review = ReviewResult.model_validate(payload)
        except (ValueError, TypeError):
            continue
        if (
            review.unit_uid != unit_uid
            or (review.verdict == "ACCEPT" and review.issues)
            or (review.verdict == "REPAIR" and (
                not review.issues
                or any(
                    not isinstance(issue.repair_instruction, str)
                    or not issue.repair_instruction.strip()
                    or issue.repair_instruction.strip().casefold() in {"none", "null"}
                    for issue in review.issues
                )
            ))
        ):
            continue
        return review
    return None


def _persist_d050_final_review_contract_failure(
    *, checkpoint_dir: Path, request, candidate: TranslationResult, request_binding_sha256: str,
    semantic_group_hash: str | None, attempt: int, error: ProviderContractGap,
) -> None:
    """Preserve invalid final-review evidence without making it semantic authority."""
    key = f"{request.display_id}.attempt_{attempt:02d}"
    persist_stage_once(checkpoint_dir, D050_EXACT_MARKER_FINAL_REVIEW_CONTRACT_FAILURE_STAGE, key, {
        "artifact_type": "PRODUCTION_D050_EXACT_MARKER_FINAL_REVIEW_CONTRACT_FAILURE",
        "schema_version": 1, "display_id": request.display_id, "unit_uid": request.unit_uid,
        "attempt": attempt, "candidate_binding_sha256": _candidate_binding(candidate),
        "request_binding_sha256": request_binding_sha256, "semantic_group_hash": semantic_group_hash,
        "contract_error_code": error.code, "invalid_response_persisted_as_review": False,
        "translation_api_calls": 0, "recorded_at": datetime.now(timezone.utc).isoformat(),
    })


def _persist_d050_exact_marker_recovery_attempt(
    *, checkpoint_dir: Path, request, parent: TranslationResult, outcome: UnitOutcome, group, bible: Path | None,
) -> dict[str, Any]:
    """Seal the one D050 replay attempt without altering legacy rescue artifacts."""
    existing = load_stage(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id)
    if existing is not None:
        return existing
    candidate = outcome.working
    candidate_sha = None if candidate is None else _candidate_binding(candidate)
    final_evidence = load_stage(checkpoint_dir, "d050_exact_marker_recovery_final_reviews", request.display_id)
    final = outcome.second_review
    final_acceptable = final is not None and _d050_final_review_is_acceptable(final)
    provider_request = enrich_request(request, translation_bible=bible)
    payload = {
        "artifact_type": "PRODUCTION_D050_EXACT_MARKER_RECOVERY_ATTEMPT",
        "schema_version": 1,
        "recovery_family": D050_EXACT_MARKER_RECOVERY_FAMILY,
        "display_id": request.display_id,
        "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(parent),
        "candidate_binding_sha256": candidate_sha,
        "working": None if candidate is None else candidate.model_dump(),
        "qa": outcome.qa,
        "qa_candidate_binding_sha256": candidate_sha,
        "review": None if outcome.review is None else outcome.review.model_dump(),
        "final_review": None if final is None else final.model_dump(),
        "final_review_candidate_binding_sha256": (
            final_evidence.get("candidate_binding_sha256") if isinstance(final_evidence, Mapping) else None
        ),
        "request_binding_sha256": provider_request.request_binding_sha256,
        "final_review_request_binding_sha256": (
            final_evidence.get("request_binding_sha256") if isinstance(final_evidence, Mapping) else None
        ),
        "semantic_group_hash": None if group is None else group.group_hash,
        "final_review_accepted": final_acceptable,
        "accepted": bool(candidate is not None and outcome.qa and outcome.qa.get("passed") and final_acceptable),
        "final_status": outcome.final_status,
        "semantic_terminal": semantic_terminal_of_outcome(outcome),
        "human_required": outcome.human_required,
        "repair_applied": outcome.repair_applied,
        "repair_policy_id": outcome.repair_policy_id,
        "translation_api_calls": 0,
        "reviewer_api_calls": outcome.reviewer_api_calls,
        "repair_api_calls": outcome.repair_api_calls,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    persist_stage_once(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id, payload)
    return payload


def _blocked(
    handoff_path: Path,
    worker_id: str,
    *,
    checkpoint: str | None,
    classification: str,
    extra: Mapping[str, Any] | None = None,
    active_batch: str,
) -> int:
    payload = {
        "status": "BLOCKED",
        "worker_id": worker_id,
        "active_batch": active_batch,
        "checkpoint": checkpoint or PRODUCTION_CHECKPOINT,
        "failure_classification": classification,
        "stage_reached": "PREFLIGHT",
        "production_disposition": "HARD_STOP",
        "disposition_reason": classification,
    }
    # ``write_worker_handoff`` intentionally exposes a closed schema.  Keep
    # diagnostics local to the caller, but always emit a valid handoff rather
    # than letting an optional diagnostic field turn a known hard block into an
    # unclassifiable protocol violation.
    if extra and isinstance(extra.get("unresolved_raw_failure_ids"), list):
        payload["failing_work_order_ids"] = [str(item) for item in extra["unresolved_raw_failure_ids"]]
    write_worker_handoff(handoff_path, **payload)
    return 2


def _terminal_resolved(outcome: UnitOutcome) -> bool:
    return (
        bool(outcome.qa and outcome.qa.get("passed"))
        and semantic_terminal_of_outcome(outcome) in SEMANTIC_PASS_TERMINALS
    )


def _human_escalation_reason(outcome: UnitOutcome, *, recovery_exhausted: bool) -> str | None:
    """Return a terminal-human reason only after non-human paths are spent."""
    if not outcome.human_required or outcome.raw is None or outcome.working is None:
        return None
    if outcome.failure_family in {"PROVIDER_CONTRACT_FAILURE", "REVIEWER_EXECUTION_FAILURE", "REPAIR_EXECUTION_FAILURE"}:
        return None
    if outcome.failure_code and "CONTRACT_GAP" in outcome.failure_code:
        return None
    review = outcome.second_review or outcome.review
    if review is None:
        return None
    severities = review_issue_severities(issue.code for issue in review.issues)
    if BLOCKING_SEMANTIC not in severities:
        return None
    if not recovery_exhausted:
        return None
    # The recovery path validates parent/head, exact scope, Core, and a final
    # canonical review before leaving this unresolved terminal.
    return "RECOVERY_EXHAUSTED_MATERIAL_DEFECT"


def _safe_current_head_debt(
    outcome: UnitOutcome, *, checkpoint_dir: Path
) -> bool:
    """The old deferred predicate, kept private behind the disposition gate."""
    if not (
        outcome.qa and outcome.qa.get("passed")
        and outcome.raw is not None and outcome.working is not None
    ):
        return False
    try:
        current = _resolve_checkpoint_authoritative_candidate(
            checkpoint_dir, outcome.request.display_id, outcome.raw,
        )
    except CandidateAuthorityError:
        return False
    if current.candidate_sha256 != _candidate_binding(outcome.working):
        return False
    primary_repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", outcome.request.display_id)
    # A current-head rereview is the only review authority for containment.
    # ``outcome.second_review`` may be a historical rejected-repair review;
    # it must not turn a local, current-head-bound finding into a hard stop.
    final_review = _current_head_rereview(
        outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
    ) or outcome.second_review or outcome.review
    corrective_eligible = (
        isinstance(primary_repair, Mapping)
        and final_review is not None
        and _corrective_semantic_repair_is_eligible(primary_repair, final_review)
    )
    if corrective_eligible and load_stage(checkpoint_dir, "semantic_rescue_corrective", outcome.request.display_id) is None:
        return False
    recovery_exhausted = (
        load_stage(checkpoint_dir, "semantic_rescue_corrective", outcome.request.display_id) is not None
        or not corrective_eligible
    )
    # Contract/protocol debt remains safe under the existing rule.  A material
    # Reviewer finding is production-safe only when it is deterministically
    # contained to this current, non-continuation target.  Release still owns
    # its resolution; this merely avoids treating bounded-recovery exhaustion
    # as a second semantic stop policy.
    # A Core-clean candidate without an exact-current unresolved semantic
    # terminal is not release debt.  In particular, a resume projection may
    # deliberately clear historical terminal fields; treating that absence as
    # debt resurrects an older queue record after a later ACCEPT.  The only
    # remaining generic safe-debt route is a bounded, current-head textual
    # finding proved by the exact final review below.
    return _isolated_current_head_textual_debt(
        outcome, current=current.candidate, review=final_review,
    )


def _current_exact_terminal_accept(
    outcome: UnitOutcome, *, checkpoint_dir: Path, current: TranslationResult,
) -> bool:
    """Return whether immutable evidence accepts this exact current head.

    This is precedence validation, not a new candidate or disposition
    authority.  The existing reviewer-accept resolver covers all bound final
    review lanes; accepted candidate mutations cover the direct fast path.
    Both must bind the frozen unit, source, effective request, and resolved
    candidate.  A historical ACCEPT on another request/head is deliberately
    not promoted.
    """
    expected_request = enrich_request(outcome.request, translation_bible=None).request_binding_sha256
    head = _candidate_binding(current)
    accepted_review = _current_candidate_reviewer_accept(
        checkpoint_dir=checkpoint_dir,
        request=outcome.request,
        candidate_sha256=head,
        bible=None,
    )
    if accepted_review is not None:
        return True
    for record in _accepted_candidate_mutation_records(checkpoint_dir, outcome.request.display_id):
        if (
            record.get("display_id") == outcome.request.display_id
            and record.get("unit_uid") == outcome.request.unit_uid
            and record.get("source_sha256") == outcome.request.source_sha256
            and record.get("request_binding_sha256") == expected_request
            and record.get("candidate_binding_sha256") == head
            and record.get("accepted") is True
            and bool((record.get("qa") or {}).get("passed"))
            and (
                record.get("production_candidate_disposition") == "MODEL_ACCEPTED"
                or record.get("semantic_terminal") in SEMANTIC_PASS_TERMINALS
            )
        ):
            return True
    return False


def _current_recovery_terminal_exists(
    outcome: UnitOutcome, *, checkpoint_dir: Path, current: TranslationResult,
) -> bool:
    """Whether this current head has already consumed its bounded recovery.

    Rescue/rereview records are trigger evidence, not an enduring obligation.
    A current-bound terminal assessment plus exhausted triage is the durable
    execution boundary that prevents replay from reissuing that same R2.
    """
    assessment = load_stage(
        checkpoint_dir, "human_escalation_assessments", outcome.request.display_id,
    )
    triage = load_stage(checkpoint_dir, "human_required_triage", outcome.request.display_id)
    if isinstance(assessment, Mapping) and isinstance(triage, Mapping) and bool(
        assessment.get("candidate_binding_sha256") == _candidate_binding(current)
        and assessment.get("recovery_attempted") is True
        and triage.get("unit_uid") == outcome.request.unit_uid
        and triage.get("auto_repair_exhausted") is True
    ):
        return True

    # A reviewer-contract terminal is not a generic ``HUMAN_REQUIRED``
    # shortcut.  It consumes this episode only when the immutable corrective
    # chain proves the exact current head, group, draft, and final-review
    # request.  This is the alternate durable terminal form emitted when the
    # final reviewer cannot produce a contract-valid response.
    rereview_request = load_stage(
        checkpoint_dir, "semantic_rescue_rereview_requests", outcome.request.display_id,
    )
    rereview = load_stage(
        checkpoint_dir, "semantic_rescue_rereviews", outcome.request.display_id,
    )
    corrective = load_stage(
        checkpoint_dir, "semantic_rescue_corrective", outcome.request.display_id,
    )
    repair = load_stage(
        checkpoint_dir, "semantic_rescue_corrective_repairs", outcome.request.display_id,
    )
    final_request = load_stage(
        checkpoint_dir, "semantic_rescue_corrective_final_review_requests", outcome.request.display_id,
    )
    final_failure = load_stage(
        checkpoint_dir, "semantic_rescue_corrective_final_review_failures", outcome.request.display_id,
    )
    if not all(isinstance(value, Mapping) for value in (
        rereview_request, rereview, corrective, repair, final_request, final_failure,
    )):
        return False
    try:
        corrective_draft = TranslationResult.model_validate(repair.get("working"))
    except (TypeError, ValueError):
        return False
    current_sha = _candidate_binding(current)
    draft_sha = _candidate_binding(corrective_draft)
    group = rereview_request.get("semantic_group_hash")
    request_binding = repair.get("request_binding_sha256")
    return bool(
        rereview_request.get("display_id") == outcome.request.display_id
        and rereview_request.get("unit_uid") == outcome.request.unit_uid
        and rereview_request.get("candidate_binding_sha256") == current_sha
        and isinstance(group, str) and group
        and rereview.get("candidate_binding_sha256") == current_sha
        and rereview.get("semantic_group_hash") == group
        and rereview.get("context_sha256") == rereview_request.get("context_sha256")
        and corrective.get("display_id") == outcome.request.display_id
        and corrective.get("unit_uid") == outcome.request.unit_uid
        and corrective.get("candidate_binding_sha256") == current_sha
        and corrective.get("semantic_group_hash") == group
        and corrective.get("final_status") == "HUMAN_REQUIRED"
        and corrective.get("semantic_terminal") == "HUMAN_REQUIRED"
        and repair.get("parent_candidate_sha256") == current_sha
        and isinstance(request_binding, str) and request_binding
        and repair.get("semantic_group_hash", group) == group
        and final_request.get("display_id") == outcome.request.display_id
        and final_request.get("unit_uid") == outcome.request.unit_uid
        and final_request.get("candidate_binding_sha256") == draft_sha
        and final_request.get("semantic_group_hash") == group
        and final_failure.get("display_id") == outcome.request.display_id
        and final_failure.get("unit_uid") == outcome.request.unit_uid
        and final_failure.get("candidate_binding_sha256") == draft_sha
        and final_failure.get("request_binding_sha256") == request_binding
        and final_failure.get("semantic_group_hash") == group
        and str(final_failure.get("failure_code") or "").startswith("REVIEWER_CONTRACT_GAP_")
    )


def _exhausted_follow_up_corrective_round_is_safe_debt(
    outcome: UnitOutcome, *, checkpoint_dir: Path, current: TranslationResult,
) -> bool:
    """Accept only the fixed second-round, Core-clean fidelity boundary.

    The round-two draft is deliberately not Candidate Authority.  Its exact
    final review may therefore be release debt only after we prove both the
    authoritative parent and the prepared draft/review bindings.  This is not
    a generic escape from the current-head binding rule.
    """
    if not (
        outcome.raw is not None and outcome.working is not None
        and outcome.qa and outcome.qa.get("passed")
    ):
        return False
    marker = load_stage(
        checkpoint_dir, CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX,
        outcome.request.display_id,
    )
    final = load_stage(
        checkpoint_dir,
        f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews",
        outcome.request.display_id,
    )
    if not (isinstance(marker, Mapping) and isinstance(final, Mapping)):
        return False
    try:
        review = ReviewResult.model_validate(final.get("review"))
    except (TypeError, ValueError):
        return False
    try:
        prepared_draft = TranslationResult.model_validate(marker.get("working"))
    except (TypeError, ValueError):
        return False
    prepared_sha = _candidate_binding(prepared_draft)
    if not (
        marker.get("round") == MAX_SEMANTIC_CORRECTIVE_ROUNDS_PER_EPISODE
        and marker.get("authoritative_parent_candidate_sha256") == _candidate_binding(current)
        and final.get("candidate_binding_sha256") == prepared_sha
        and final.get("request_binding_sha256") == marker.get("request_binding_sha256")
        and final.get("semantic_group_hash") == marker.get("semantic_group_hash")
        and marker.get("display_id") == outcome.request.display_id
        and marker.get("unit_uid") == outcome.request.unit_uid
        and review.verdict == "REPAIR"
    ):
        return False
    codes = {str(issue.code or "").strip().lower() for issue in review.issues}
    if not codes or not codes <= _EXHAUSTED_CORRECTIVE_FIDELITY_CODES:
        return False
    segment_count = len(outcome.working.target_segments)
    return all(
        bool(issue.affected_segment_indices)
        and all(isinstance(index, int) and 0 <= index < segment_count for index in issue.affected_segment_indices)
        for issue in review.issues
    )


def _isolated_current_head_textual_debt(
    outcome: UnitOutcome, *, current: TranslationResult, review: ReviewResult | None = None,
) -> bool:
    """Whether an exhausted local text finding can wait for Release review.

    This is deliberately narrower than a Reviewer issue label.  Deterministic
    integrity failures are rejected by Core QA before this point; here we
    accept only an exact, current-head-bound local textual scope that cannot
    carry semantic authority into a continuation work order.
    """
    if (
        outcome.raw is None
        or outcome.working is None
        or not (outcome.qa and outcome.qa.get("passed"))
        or _candidate_binding(outcome.working) != _candidate_binding(current)
        or bool(outcome.request.continuation_hint)
    ):
        return False
    if outcome.failure_family in {
        "PROVIDER_CONTRACT_FAILURE", "REVIEWER_EXECUTION_FAILURE",
        "REPAIR_EXECUTION_FAILURE",
    }:
        return False
    review = review or outcome.second_review or outcome.review
    if review is None or review.verdict not in {"REPAIR", "HUMAN_REQUIRED"}:
        return False
    codes = {str(issue.code or "").strip().lower() for issue in review.issues}
    # Once the sole canonical boundary has proved a unique current head,
    # passed Core QA, and exact non-continuation scope, a bounded semantic
    # exhaustion (including an omission) is release debt.  Reviewer labels
    # cannot independently reclassify it as a production stop.  Explicit
    # source/authority and deterministic-integrity indicators remain closed.
    if not codes or codes & _CONTAINED_SEMANTIC_DEBT_UNSAFE_CODES:
        return False
    segment_count = len(current.target_segments)
    return all(
        bool(issue.affected_segment_indices)
        and all(isinstance(index, int) and 0 <= index < segment_count for index in issue.affected_segment_indices)
        for issue in review.issues
    )


def _current_head_rereview(
    outcome: UnitOutcome, *, checkpoint_dir: Path, current: TranslationResult,
) -> ReviewResult | None:
    """Return only rereview evidence bound to the resolved authoritative head.

    A rejected repair's final review is immutable historical evidence, but it
    cannot decide the production disposition of the restored RAW head.  The
    paired rereview request and result must both bind to that exact head.
    """
    return _current_candidate_rereview(
        checkpoint_dir=checkpoint_dir, request=outcome.request, current=current,
    )


def _current_candidate_rereview(
    *, checkpoint_dir: Path, request, current: TranslationResult,
) -> ReviewResult | None:
    """Resolve only a valid rereview for this exact authoritative candidate."""
    # Both are current-head final-review lanes.  The corrective lane is not a
    # historical rescue result: its request/result are already exactly bound
    # to Candidate Authority's current descendant and must therefore drive
    # canonical recovery projection as well as ordinary rereviews.
    for review_stage, request_stage in (
        ("semantic_rescue_rereviews", "semantic_rescue_rereview_requests"),
        ("semantic_rescue_corrective_final_reviews", "semantic_rescue_corrective_final_review_requests"),
    ):
        rereview = load_stage(checkpoint_dir, review_stage, request.display_id)
        request_evidence = load_stage(checkpoint_dir, request_stage, request.display_id)
        if not (isinstance(rereview, Mapping) and isinstance(request_evidence, Mapping)):
            continue
        review_payload = rereview.get("review")
        binding = _candidate_binding(current)
        expected_request = enrich_request(request, translation_bible=None).request_binding_sha256
        if not (
            isinstance(review_payload, Mapping)
            and request_evidence.get("display_id") == request.display_id
            and request_evidence.get("unit_uid") == request.unit_uid
            and request_evidence.get("candidate_binding_sha256") == binding
            and rereview.get("candidate_binding_sha256") == binding
            and request_evidence.get("request_binding_sha256") == expected_request
            and rereview.get("request_binding_sha256") == expected_request
            and rereview.get("context_sha256") == request_evidence.get("context_sha256")
            and rereview.get("semantic_group_hash") == request_evidence.get("semantic_group_hash")
        ):
            continue
        try:
            return ReviewResult.model_validate(review_payload)
        except Exception:
            continue
    return None


def _current_semantic_rescue_repair_trigger(
    outcome: UnitOutcome, *, checkpoint_dir: Path, current: TranslationResult,
) -> ReviewResult | None:
    """Return the one exact-current finding that may enter semantic rescue.

    Fastpath is an execution accelerator, not a source of recovery authority.
    In particular, an old rescue record, a missing review, or a current ACCEPT
    cannot manufacture a semantic-repair episode.
    """
    review = _current_head_rereview(
        outcome, checkpoint_dir=checkpoint_dir, current=current,
    )
    if review is None or review.verdict != "REPAIR":
        return None
    if BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in review.issues):
        return None
    if not review.issues or any(
        not issue.affected_segment_indices or not issue.repair_instruction
        for issue in review.issues
    ):
        return None
    return review


def _synthetic_semantic_rescue_is_historical_only(
    outcome: UnitOutcome, *, checkpoint_dir: Path, current: TranslationResult,
) -> bool:
    """Recognize an unexecuted rescue projection without mutating its evidence."""
    rescue = load_stage(checkpoint_dir, "semantic_rescue", outcome.request.display_id)
    if not isinstance(rescue, Mapping):
        return False
    if _current_semantic_rescue_repair_trigger(
        outcome, checkpoint_dir=checkpoint_dir, current=current,
    ) is not None:
        return False
    return bool(
        rescue.get("candidate_binding_sha256") == _candidate_binding(current)
        and rescue.get("repair_applied") is False
        and rescue.get("review") is None
        and rescue.get("second_review") is None
        and load_stage(checkpoint_dir, "semantic_rescue_repairs", outcome.request.display_id) is None
        and load_stage(checkpoint_dir, "semantic_rescue_corrective", outcome.request.display_id) is None
    )


def _current_rereview_corrective_scope(
    *, request, candidate: TranslationResult, review: ReviewResult, group,
) -> tuple[int, ...] | None:
    """Derive corrective scope from current rereview, never stale repair history."""
    if review.verdict != "REPAIR" or BLOCKING_SEMANTIC not in review_issue_severities(
        issue.code for issue in review.issues
    ):
        return None
    indices = {index for issue in review.issues for index in issue.affected_segment_indices}
    if (
        not indices
        or any(not issue.repair_instruction for issue in review.issues)
        or any(not isinstance(index, int) or index < 0 or index >= len(candidate.target_segments) for index in indices)
    ):
        return None
    if group is not None:
        members = set(getattr(group, "member_work_order_ids", ()))
        repairable = set(getattr(group, "repairable_work_order_ids", ()))
        if request.display_id not in members or request.display_id not in repairable:
            return None
    return tuple(sorted(indices))


def canonical_production_disposition(
    outcome: UnitOutcome, *, checkpoint_dir: Path
) -> dict[str, Any]:
    """The sole current-head production disposition boundary.

    Local Reviewer/HUMAN_REQUIRED labels are inputs only.  The result is
    intentionally small and serializable so checkpoint sealing, handoff, and
    supervisor consumption use exactly the same decision vocabulary.
    """
    references = [str(checkpoint_dir / "units" / f"{outcome.request.display_id}.json")]
    if outcome.raw is None or outcome.working is None:
        return {"disposition": "HARD_STOP", "reason": "NO_SAFE_AUTHORITATIVE_CANDIDATE", "evidence_references": references, "recovery_family": None}
    try:
        current = _resolve_checkpoint_authoritative_candidate(
            checkpoint_dir, outcome.request.display_id, outcome.raw,
        )
    except CandidateAuthorityError:
        return {"disposition": "HARD_STOP", "reason": "CANDIDATE_AUTHORITY_AMBIGUITY", "evidence_references": references, "recovery_family": None}
    # When Candidate Authority resolves the unchanged RAW head, its immutable
    # RAW-QA record is the exact current effective QA.  A resume reconstruction
    # may otherwise carry QA from a rejected repair or machine escalation and
    # manufacture a false integrity stop before canonical disposition can
    # inspect the current head.  For a descendant, callers already install
    # the bound current effective QA through ``_project_resolved_unit_record``.
    if current.candidate_sha256 == _candidate_binding(outcome.raw) and not str((outcome.qa or {}).get("effective_qa_reason") or "").startswith("RESOLVER_VALIDATED_EFFECTIVE_ACQUISITION"):
        raw_qa_path = checkpoint_dir / "translation_raw_qa_report.json"
        if raw_qa_path.is_file():
            try:
                raw_qa_report = json.loads(raw_qa_path.read_text(encoding="utf-8"))
                current_raw_qa = next(
                    (
                        row for row in raw_qa_report.get("records", [])
                        if isinstance(row, Mapping)
                        and row.get("display_id") == outcome.request.display_id
                        and row.get("unit_uid") == outcome.request.unit_uid
                    ),
                    None,
                )
            except (OSError, json.JSONDecodeError, TypeError):
                current_raw_qa = None
            if isinstance(current_raw_qa, Mapping):
                outcome = replace(outcome, qa=dict(current_raw_qa))
    # A real Human decision is terminal authority.  In contrast, the
    # historical ``human_required`` flag in a unit record is a machine
    # escalation label and must not consume an otherwise-safe, current
    # deterministic recovery.  Keep this exception deliberately limited to
    # the existing exact numeric-anchor ownership correction; all other Core
    # QA failures remain fail-closed below.
    human = load_stage(checkpoint_dir, "human_adjudications", outcome.request.display_id)
    human_terminal = isinstance(human, Mapping) and human.get("decision") in {"HUMAN_APPROVED", "HUMAN_REJECTED"}
    current_codes = set(qa_codes(outcome.qa or {}))
    numeric_anchor_recovery = (
        not human_terminal
        and not bool((outcome.qa or {}).get("passed"))
        and current_codes <= {"NUMBER_PRESERVATION", "NUMERIC_ANCHOR_DUPLICATION"}
        and "NUMERIC_ANCHOR_DUPLICATION" in current_codes
        and _candidate_binding(current.candidate) == _candidate_binding(outcome.raw)
        and correct_numeric_anchor_duplication(outcome.request, current.candidate) is not None
        and not _current_numeric_ownership_mutation_is_effective(
            checkpoint_dir=checkpoint_dir, request=outcome.request, raw=outcome.raw,
            current=current.candidate, raw_qa=outcome.qa or {}, current_qa=outcome.qa or {},
        )
    )
    if numeric_anchor_recovery:
        references.extend([
            str(checkpoint_dir / "translation_raw_qa_report.json"),
            str(checkpoint_dir / "accepted_candidate_mutations" / f"{outcome.request.display_id}.json"),
        ])
        return {
            "disposition": "RECOVERY_REQUIRED",
            "reason": "CURRENT_NUMERIC_ANCHOR_OWNERSHIP_RECOVERY_ELIGIBLE",
            "evidence_references": references,
            "recovery_family": "NUMERIC_ANCHOR_OWNERSHIP_CORRECTION",
        }
    url_recovery = (
        not human_terminal
        and not bool((outcome.qa or {}).get("passed"))
        and current_codes == {"URL_PRESERVATION"}
        and _candidate_binding(current.candidate) == _candidate_binding(outcome.raw)
        and restore_exact_source_urls(outcome.request, current.candidate) not in (None, current.candidate)
    )
    if url_recovery:
        references.extend([str(checkpoint_dir / "translation_raw_qa_report.json")])
        return {"disposition": "RECOVERY_REQUIRED", "reason": "CURRENT_EXACT_SOURCE_URL_RESTORATION_ELIGIBLE", "evidence_references": references, "recovery_family": "EXACT_SOURCE_URL_RESTORATION"}
    if not (outcome.qa and outcome.qa.get("passed")):
        return {"disposition": "HARD_STOP", "reason": "CORE_QA_OR_PROTECTED_INTEGRITY_FAILED", "evidence_references": references, "recovery_family": None}
    # An unexecuted rescue record is immutable diagnostic history, not a
    # current terminal.  Its presence must never turn a Core-clean head into
    # a fictitious exhausted recovery episode on resume.
    if _synthetic_semantic_rescue_is_historical_only(
        outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
    ):
        return {
            "disposition": "CONTINUE",
            "reason": "CURRENT_HEAD_SYNTHETIC_RESCUE_HISTORICAL_ONLY",
            "evidence_references": references,
            "recovery_family": None,
        }
    # The normal fastpath has two pre-existing success terminals.  They are
    # not recovery evidence: a current Reviewer ACCEPT is terminal, and a
    # Core-clean ``DIRECT_CORE_PASS`` is the policy's explicit no-review
    # success lane.  Resolve both before the generic unresolved fallback;
    # absence of a review remains non-terminal for every other dispatch lane.
    raw_head = current.candidate_sha256 == _candidate_binding(outcome.raw)
    if raw_head and outcome.review is not None and outcome.review.verdict == "ACCEPT":
        return {
            "disposition": "CONTINUE",
            "reason": "CURRENT_HEAD_ACCEPTED",
            "evidence_references": references,
            "recovery_family": None,
        }
    if raw_head and outcome.review is None and outcome.reviewer_dispatch == "DIRECT_CORE_PASS":
        return {
            "disposition": "CONTINUE",
            "reason": "CURRENT_HEAD_ACCEPTED",
            "evidence_references": references,
            "recovery_family": None,
        }
    # An exact current ACCEPT is terminal before any historical debt record.
    # This prevents a compatibility queue/debt artifact from being revived by
    # a later resume after Candidate Authority has retained the same head.
    if _current_exact_terminal_accept(outcome, checkpoint_dir=checkpoint_dir, current=current.candidate):
        return {
            "disposition": "CONTINUE",
            "reason": "CURRENT_HEAD_ACCEPTED",
            "evidence_references": references,
            "recovery_family": None,
        }
    # The accepted-mutation writer reaches this record only after the existing
    # current-head deferred-debt predicate has validated a completed recovery
    # episode.  It is therefore terminal Candidate Authority evidence, not a
    # second policy source.  Consume an exact-bound terminal before an older
    # rereview trigger can resurrect that already-completed recovery.
    expected_request_binding = enrich_request(
        outcome.request, translation_bible=None,
    ).request_binding_sha256
    debt_records = [
        record for record in _accepted_candidate_mutation_records(
            checkpoint_dir, outcome.request.display_id,
        )
        if (
            record.get("display_id") == outcome.request.display_id
            and record.get("unit_uid") == outcome.request.unit_uid
            and record.get("source_sha256") == outcome.request.source_sha256
            and record.get("request_binding_sha256") == expected_request_binding
            and record.get("candidate_binding_sha256") == current.candidate_sha256
            and record.get("production_candidate_disposition") == "DEFERRED_SEMANTIC_DEBT"
            and record.get("accepted") is True
        )
    ]
    if len(debt_records) > 1:
        return {"disposition": "HARD_STOP", "reason": "CURRENT_TERMINAL_DEBT_AMBIGUITY", "evidence_references": references, "recovery_family": None}
    if len(debt_records) == 1:
        references.append(
            str(checkpoint_dir / "accepted_candidate_mutations" / f"{outcome.request.display_id}.json")
        )
        return {
            "disposition": "DEFERRED_SEMANTIC_DEBT",
            "reason": "CURRENT_ACCEPTED_CANDIDATE_RELEASE_DEBT",
            "evidence_references": references,
            "recovery_family": None,
        }
    # Resolve the exact current-head rereview before consulting any historical
    # rejected-repair projection.  ``outcome.working`` can still be the
    # historical RAW/LKG while a resume reconstructs the unit, but a rereview
    # that is bound to Candidate Authority's current head is durable terminal
    # evidence.  Letting the mismatch branch run first re-issued the same
    # rereview forever after a worker restart.
    current_recovery_terminal = _current_recovery_terminal_exists(
        outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
    )
    current_rereview = _current_head_rereview(
        outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
    )
    # The prepared second-round draft is intentionally not Candidate
    # Authority.  Its exact final review is nevertheless the existing
    # exhaustion evidence for the current authoritative parent.  Project it
    # before the current rereview's REPAIR branch so a resume cannot replay
    # an already exhausted bounded corrective episode.
    if _exhausted_follow_up_corrective_round_is_safe_debt(
        outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
    ):
        references.extend([
            str(checkpoint_dir / CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX / f"{outcome.request.display_id}.json"),
            str(checkpoint_dir / f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews" / f"{outcome.request.display_id}.json"),
        ])
        return {
            "disposition": "DEFERRED_SEMANTIC_DEBT",
            "reason": "EXHAUSTED_CONTAINED_SEMANTIC_FIDELITY_DEBT",
            "evidence_references": references,
            "recovery_family": None,
        }
    if current_rereview is not None:
        references.append(str(checkpoint_dir / "semantic_rescue_rereviews" / f"{outcome.request.display_id}.json"))
        if current_rereview.verdict == "ACCEPT":
            return {"disposition": "CONTINUE", "reason": "CURRENT_HEAD_ACCEPTED", "evidence_references": references, "recovery_family": None}
        if current_rereview.verdict == "REPAIR" and not current_recovery_terminal:
            return {"disposition": "RECOVERY_REQUIRED", "reason": "CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE", "evidence_references": references, "recovery_family": "SEMANTIC_CORRECTIVE"}
    # A rejected rescue draft is immutable trigger history, but it still
    # leaves one existing current-head rereview obligation.  This branch is
    # deliberately before the historical terminal projection so the restored
    # RAW head reaches that existing recovery rather than inheriting an old
    # ``units/*.json`` HUMAN_REQUIRED label.
    if (
        not human_terminal
        and not current_recovery_terminal
        and _has_rejected_semantic_rescue_candidate(
            checkpoint_dir, outcome.request, outcome.raw,
        )
    ):
        references.extend([
            str(checkpoint_dir / "semantic_rescue" / f"{outcome.request.display_id}.json"),
            str(checkpoint_dir / "semantic_rescue_repairs" / f"{outcome.request.display_id}.json"),
            str(checkpoint_dir / "semantic_rescue_final_reviews" / f"{outcome.request.display_id}.json"),
        ])
        return {
            "disposition": "RECOVERY_REQUIRED",
            "reason": "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR",
            "evidence_references": references,
            "recovery_family": "SEMANTIC_REREVIEW",
        }
    # A historical unit result may record HUMAN_REQUIRED solely because its
    # then-current deterministic view failed.  It is evidence, not current
    # authority, once Candidate Authority still resolves the same head and
    # the current effective QA passes.  Keep this narrow: any current Human
    # decision, bound rereview/recovery terminal, review payload, or rejected
    # rescue obligation remains current and reaches the existing policy below.
    if (
        semantic_terminal_of_outcome(outcome) == "HUMAN_REQUIRED"
        and not human_terminal
        and not current_recovery_terminal
        and current_rereview is None
        and outcome.review is None
        and outcome.second_review is None
        and outcome.working is not None
        and _candidate_binding(current.candidate) == _candidate_binding(outcome.working)
    ):
        return {
            "disposition": "CONTINUE",
            "reason": "CURRENT_HEAD_CORE_QA_SUPERSEDES_HISTORICAL_TERMINAL",
            "evidence_references": references,
            "recovery_family": None,
        }
    if _exhausted_follow_up_corrective_round_is_safe_debt(
        outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
    ):
        references.extend([
            str(checkpoint_dir / CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX / f"{outcome.request.display_id}.json"),
            str(checkpoint_dir / f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews" / f"{outcome.request.display_id}.json"),
        ])
        return {
            "disposition": "DEFERRED_SEMANTIC_DEBT",
            "reason": "EXHAUSTED_CONTAINED_SEMANTIC_FIDELITY_DEBT",
            "evidence_references": references,
            "recovery_family": None,
        }
    if current.candidate_sha256 != _candidate_binding(outcome.working):
        # ``outcome.working`` is compatibility/reconstruction evidence, not
        # candidate authority.  A resumed worker can therefore carry the
        # rejected corrective draft while Candidate Authority has restored a
        # unique, Core-clean head.  Before escalating that historical mismatch
        # to a production stop, evaluate the existing contained-debt predicate
        # against the resolved current projection.  This deliberately remains
        # narrow: ambiguity, failed current QA, an unbound review, or any
        # unsafe semantic finding still follows the fail-closed path below.
        current_projection = replace(outcome, working=current.candidate)
        exhaustion_triage = load_stage(
            checkpoint_dir, "human_required_triage", outcome.request.display_id,
        )
        historical_escalation = load_stage(
            checkpoint_dir, "human_escalation_assessments", outcome.request.display_id,
        )
        if (
            current_rereview is not None
            # This re-projection is only the exhausted-current-head case.  A
            # missing corrective attempt remains an existing executable
            # recovery obligation and must reach the recovery branch below.
            and load_stage(checkpoint_dir, "semantic_rescue_corrective", outcome.request.display_id) is not None
            # The local terminal itself is not authority, but its immutable
            # triage proves this bounded corrective episode was actually
            # exhausted.  Without it, an old corrective artifact is only
            # trigger history and the existing rereview recovery remains due.
            and isinstance(exhaustion_triage, Mapping)
            and exhaustion_triage.get("unit_uid") == outcome.request.unit_uid
            and exhaustion_triage.get("auto_repair_exhausted") is True
            and isinstance(historical_escalation, Mapping)
            and historical_escalation.get("unit_uid") == outcome.request.unit_uid
            and historical_escalation.get("recovery_attempted") is True
            and _safe_current_head_debt(current_projection, checkpoint_dir=checkpoint_dir)
        ):
            outcome = current_projection
        else:
            # A final REPAIR can reject a bounded rescue candidate.  That
            # candidate is immutable evidence, but it is deliberately *not*
            # the current head: Candidate Authority restores the bound RAW/LKG.
            # Do not turn that normal rejection into a fictitious lineage
            # ambiguity.  The resume path already owns the one current-head
            # rereview and any remaining bounded recovery; request that path
            # here instead of letting stale candidate evidence stop the
            # supervisor.
            terminal_recovery = _current_recovery_terminal_exists(
                outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
            )
            if _has_rejected_semantic_rescue_candidate(
                checkpoint_dir, outcome.request, outcome.raw
            ) and not terminal_recovery:
                references.extend([
                    str(checkpoint_dir / "semantic_rescue" / f"{outcome.request.display_id}.json"),
                    str(checkpoint_dir / "semantic_rescue_repairs" / f"{outcome.request.display_id}.json"),
                    str(checkpoint_dir / "semantic_rescue_final_reviews" / f"{outcome.request.display_id}.json"),
                ])
                return {
                    "disposition": "RECOVERY_REQUIRED",
                    "reason": "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR",
                    "evidence_references": references,
                    "recovery_family": "SEMANTIC_REREVIEW",
                }
            if not terminal_recovery:
                return {"disposition": "HARD_STOP", "reason": "CURRENT_HEAD_BINDING_MISMATCH", "evidence_references": references, "recovery_family": None}
            # The old stored working candidate is historical reconstruction
            # input; terminal evidence proves its R2 successor already ran
            # against the resolved current head.  Continue the exhaustion
            # classification with that authoritative head rather than
            # reviving the old trigger.
            outcome = current_projection
    terminal = semantic_terminal_of_outcome(outcome)
    if terminal in SEMANTIC_PASS_TERMINALS:
        return {"disposition": "CONTINUE", "reason": "CURRENT_HEAD_ACCEPTED", "evidence_references": references, "recovery_family": None}
    primary_repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", outcome.request.display_id)
    final_review = current_rereview or outcome.second_review or outcome.review
    corrective_eligible = (
        isinstance(primary_repair, Mapping)
        and final_review is not None
        and _corrective_semantic_repair_is_eligible(primary_repair, final_review)
    )
    corrective_attempt = load_stage(checkpoint_dir, "semantic_rescue_corrective", outcome.request.display_id)
    if corrective_eligible and corrective_attempt is None:
        references.append(str(checkpoint_dir / "semantic_rescue_repairs" / f"{outcome.request.display_id}.json"))
        return {"disposition": "RECOVERY_REQUIRED", "reason": "CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE", "evidence_references": references, "recovery_family": "SEMANTIC_CORRECTIVE"}
    if _safe_current_head_debt(outcome, checkpoint_dir=checkpoint_dir):
        return {"disposition": "DEFERRED_SEMANTIC_DEBT", "reason": "SAFE_CURRENT_HEAD_RELEASE_DEBT", "evidence_references": references, "recovery_family": None}
    return {"disposition": "HARD_STOP", "reason": "CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_EXHAUSTED", "evidence_references": references, "recovery_family": None}


def _reviewer_contract_debt_eligible(outcome: UnitOutcome) -> bool:
    """Permit release deferral only for an exhausted reviewer protocol failure.

    This is deliberately independent of a review verdict: no malformed model
    response becomes semantic evidence.  The unchanged current candidate must
    already pass Core Harness and retain the exact RAW binding.
    """
    return (
        outcome.failure_code == REVIEWER_CONTRACT_INVALID_AFTER_RETRY
        and outcome.failure_family == "REVIEWER_EXECUTION_FAILURE"
        and bool(outcome.qa and outcome.qa.get("passed"))
        and outcome.raw is not None
        and outcome.working is not None
        and _candidate_binding(outcome.raw) == _candidate_binding(outcome.working)
    )


def _repair_contract_debt_eligible(outcome: UnitOutcome) -> bool:
    """Defer an exhausted scope failure only when the unchanged head is safe.

    A valid reviewer REPAIR remains a signal, but a provider's invalid patch is
    not evidence that the current candidate is catastrophically wrong.  Reuse
    the existing hard-issue allowlist: independent omission/integrity/binding
    evidence still blocks; every other Core-clean local issue is release debt.
    """
    if not (
        outcome.failure_code == REPAIR_CONTRACT_INVALID_AFTER_RETRY
        and outcome.failure_family == "REPAIR_EXECUTION_FAILURE"
        and bool(outcome.qa and outcome.qa.get("passed"))
        and outcome.raw is not None and outcome.working is not None
        and _candidate_binding(outcome.raw) == _candidate_binding(outcome.working)
    ):
        return False
    review = outcome.review
    codes = set() if review is None else {str(issue.code or "").strip().lower() for issue in review.issues}
    return bool(codes) and not bool(codes & _PRODUCTION_HARD_BLOCK_ISSUE_CODES)


def _persist_repair_contract_debt(*, checkpoint_dir: Path, item: UnitOutcome, bible: Path | None) -> dict[str, Any]:
    existing = load_stage(checkpoint_dir, "repair_contract_debts", item.request.display_id)
    if existing is not None:
        return existing
    assert item.raw is not None and item.working is not None and item.qa is not None and item.review is not None
    provider_request = enrich_request(item.request, translation_bible=bible)
    failures = _matching_repair_contract_failure_paths(
        checkpoint_dir, item.request, item.raw, item.review, bible=bible,
    )
    payload = {
        "artifact_type": "PRODUCTION_REPAIR_CONTRACT_DEBT", "schema_version": 1,
        "resolution_class": "DEFER_TO_RELEASE", "release_gate": "BLOCKING_UNTIL_BATCH_DEBT_RESOLUTION",
        "logical_work_order_id": item.request.display_id, "unit_uid": item.request.unit_uid,
        "source_sha256": item.request.source_sha256,
        "request_binding_sha256": provider_request.request_binding_sha256,
        "candidate_binding_sha256": _candidate_binding(item.working),
        "current_candidate": item.working.model_dump(), "core_qa": item.qa,
        "review": item.review.model_dump(),
        "repair_execution_failure": REPAIR_CONTRACT_INVALID_AFTER_RETRY,
        "repair_contract_attempt_paths": [str(path) for path in failures],
        "repair_contract_attempt_count": len(failures), "translation_api_calls": 0,
    }
    persist_stage_once(checkpoint_dir, "repair_contract_debts", item.request.display_id, payload)
    return payload


def _persist_reviewer_contract_debt(*, checkpoint_dir: Path, item: UnitOutcome, bible: Path | None) -> dict[str, Any]:
    """Persist a create-once, Core-clean reviewer protocol debt.

    It carries the same identity and current-candidate binding as normal
    release debt, but intentionally contains no untrusted reviewer verdict.
    """
    existing = load_stage(checkpoint_dir, "reviewer_contract_debts", item.request.display_id)
    if existing is not None:
        return existing
    assert item.raw is not None and item.working is not None and item.qa is not None
    provider_request = enrich_request(item.request, translation_bible=bible)
    failures = _matching_reviewer_contract_failure_paths(
        checkpoint_dir, item.request, item.raw, bible=bible, semantic_context=None,
    )
    payload = {
        "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_DEBT",
        "schema_version": 1,
        "resolution_class": "DEFER_TO_RELEASE",
        "release_gate": "BLOCKING_UNTIL_BATCH_DEBT_RESOLUTION",
        "logical_work_order_id": item.request.display_id,
        "unit_uid": item.request.unit_uid,
        "source_sha256": item.request.source_sha256,
        "request_binding_sha256": provider_request.request_binding_sha256,
        "candidate_binding_sha256": _candidate_binding(item.working),
        "current_candidate": item.working.model_dump(),
        "core_qa": item.qa,
        "reviewer_execution_failure": REVIEWER_CONTRACT_INVALID_AFTER_RETRY,
        "reviewer_contract_attempt_paths": [str(path) for path in failures],
        "reviewer_contract_attempt_count": len(failures),
        "translation_api_calls": 0,
    }
    persist_stage_once(checkpoint_dir, "reviewer_contract_debts", item.request.display_id, payload)
    return payload


def _deferred_semantic_debt_eligible(
    outcome: UnitOutcome,
    *,
    checkpoint_dir: Path | None = None,
) -> bool:
    """Return whether an unresolved unit can continue with release-only debt.

    Production continuity is deliberately simpler than release acceptance:
    the sole question here is whether the *current authoritative head* is
    safe to carry forward.  Reviewer/repair/provider execution terminals are
    evidence for release triage, not an independent production stop class.
    A current-head semantic defect whose bounded recovery is exhausted is
    release debt only when the canonical containment checks prove it is
    isolated and Core-clean.  Authority and deterministic-integrity threats
    remain hard stops.
    """
    # Compatibility callers that do not have a checkpoint authority context
    # retain the historical narrow predicate.  The production hot path always
    # supplies ``checkpoint_dir`` and therefore uses the single safe-head rule
    # below.
    if checkpoint_dir is None:
        return _legacy_deferred_semantic_debt_eligible(outcome)
    return canonical_production_disposition(
        outcome, checkpoint_dir=checkpoint_dir,
    )["disposition"] == "DEFERRED_SEMANTIC_DEBT"


def _legacy_deferred_semantic_debt_eligible(outcome: UnitOutcome) -> bool:
    """Legacy narrow predicate retained for context-free compatibility calls."""
    if not outcome.qa or not outcome.qa.get("passed") or outcome.raw is None or outcome.working is None:
        return False
    if not outcome.human_required or getattr(outcome, "failure_code", None):
        return False
    review = outcome.second_review or outcome.review
    if review is None or review.verdict not in {"REPAIR", "HUMAN_REQUIRED"}:
        return False
    codes = {str(issue.code or "").strip().lower() for issue in review.issues}
    if (
        outcome.repair_policy_id == "RESOLUTION_QUEUE_AUTO_RESOLUTION"
        and bool(codes)
        and codes <= _LOCALIZED_POST_RESOLUTION_DEBT_CODES
        and len(outcome.raw.target_segments) == len(outcome.working.target_segments)
        and all(
            raw_segment.kind == working_segment.kind
            and raw_segment.anchor_ref == working_segment.anchor_ref
            and raw_segment.island_ref == working_segment.island_ref
            for raw_segment, working_segment in zip(outcome.raw.target_segments, outcome.working.target_segments, strict=True)
        )
    ):
        return True
    if outcome.recovered_from == "last_known_good_rejected_repair":
        return bool(codes) and (
            not (codes & _PRODUCTION_HARD_BLOCK_ISSUE_CODES)
            or codes <= _REJECTED_REPAIR_RELEASE_DEBT_CODES
        )
    if outcome.recovered_from == "resolution_queue_rejected_repair":
        return bool(codes) and codes <= (_LOCALIZED_POST_RESOLUTION_DEBT_CODES | _DEFERRED_SEMANTIC_DEBT_CODES)
    if outcome.recovered_from == "legacy_unbound_last_known_good":
        return bool(codes) and codes <= _LEGACY_LAST_KNOWN_GOOD_DEBT_CODES
    if outcome.recovered_from == "d050_exact_marker_repair":
        return codes == {"fidelity_omission"}
    return bool(codes) and BLOCKING_SEMANTIC not in review_issue_severities(codes)


def _persist_deferred_semantic_debt(*, checkpoint_dir: Path, item: UnitOutcome, bible: Path | None) -> dict[str, Any]:
    """Persist a fully-bound release debt without mutating RAW or unit history."""
    existing = load_stage(checkpoint_dir, "resolution_queue_deferred_debts", item.request.display_id)
    if existing is not None:
        return existing
    assert item.raw is not None and item.working is not None
    # A final round-two draft remains prepared evidence.  The release debt is
    # deliberately bound to the unchanged CA head, while retaining the draft
    # and exact final review as the immutable exhaustion evidence.
    current = _resolve_checkpoint_authoritative_candidate(
        checkpoint_dir, item.request.display_id, item.raw,
    ).candidate
    exhausted_follow_up = _exhausted_follow_up_corrective_round_is_safe_debt(
        item, checkpoint_dir=checkpoint_dir, current=current,
    )
    debt_candidate = current if exhausted_follow_up else item.working
    review = item.second_review or item.review
    if exhausted_follow_up:
        final = load_stage(
            checkpoint_dir, f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews",
            item.request.display_id,
        )
        if isinstance(final, Mapping):
            review = _optional_review_result(final.get("review")) or review
    provider_request = enrich_request(item.request, translation_bible=bible)
    issues = [] if review is None else [issue.model_dump() for issue in review.issues]
    payload = {
        "artifact_type": "PRODUCTION_RESOLUTION_QUEUE_DEFERRED_SEMANTIC_DEBT",
        "schema_version": 1,
        "resolution_class": "DEFERRED_SEMANTIC_DEBT",
        "release_gate": "BLOCKING_UNTIL_BATCH_DEBT_RESOLUTION",
        "logical_work_order_id": item.request.display_id,
        "unit_uid": item.request.unit_uid,
        "source_sha256": item.request.source_sha256,
        "request_binding_sha256": provider_request.request_binding_sha256,
        "candidate_binding_sha256": _candidate_binding(debt_candidate),
        "source_segments": [segment.model_dump() for segment in item.request.source_segments],
        "current_candidate": debt_candidate.model_dump(),
        "core_qa": item.qa,
        "review": None if review is None else review.model_dump(),
        "review_issue_codes": [issue["code"] for issue in issues],
        "recommended_correction": [issue.get("repair_instruction") for issue in issues if issue.get("repair_instruction")],
        "reviewer_dispatch": item.reviewer_dispatch,
        "translation_api_calls": 0,
    }
    if exhausted_follow_up:
        payload.update({
            "candidate_disposition": "AUTHORITATIVE_HEAD_AFTER_EXHAUSTED_PREPARED_CORRECTIVE_ROUNDS",
            "recovery_rounds_exhausted": MAX_SEMANTIC_CORRECTIVE_ROUNDS_PER_EPISODE,
            "prepared_candidate_binding_sha256": _candidate_binding(item.working),
            "prepared_candidate": item.working.model_dump(),
            "final_review_evidence_stage": f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews",
        })
    if item.failure_family:
        payload["execution_failure_family"] = item.failure_family
    if item.failure_code:
        payload["execution_failure_code"] = item.failure_code
    if item.recovered_from == "last_known_good_rejected_repair":
        rescue = load_stage(checkpoint_dir, "semantic_rescue", item.request.display_id) or {}
        completeness = load_stage(checkpoint_dir, "semantic_rescue_completeness", item.request.display_id) or {}
        queue_repair = load_stage(checkpoint_dir, "resolution_queue_auto_repairs", item.request.display_id) or {}
        rejected = queue_repair.get("working") if isinstance(queue_repair.get("working"), Mapping) else None
        payload.update({
            "candidate_disposition": "LAST_KNOWN_GOOD_AFTER_REJECTED_REPAIR",
            "failed_repair_evidence": {
                "semantic_rescue_candidate_binding_sha256": rescue.get("candidate_binding_sha256"),
                "rejected_candidate_binding_sha256": (
                    _candidate_binding(TranslationResult.model_validate(rejected)) if rejected is not None
                    else completeness.get("candidate_binding_sha256")
                ),
                "completeness_passed": completeness.get("passed"),
                "failure_code": "REPAIR_COMPLETENESS_CLAUSE_COVERAGE_FAILED" if rescue else "REPAIR_DETERMINISTIC_QA_FAILED",
            },
        })
    persist_stage_once(checkpoint_dir, "resolution_queue_deferred_debts", item.request.display_id, payload)
    return payload


def _has_rejected_semantic_rescue_candidate(checkpoint_dir: Path, request, raw: TranslationResult) -> bool:
    """Return true when a current-bound rescue failed and RAW remains the head."""
    rescue = load_stage(checkpoint_dir, "semantic_rescue", request.display_id)
    completeness = load_stage(checkpoint_dir, "semantic_rescue_completeness", request.display_id)
    repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", request.display_id)
    final = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", request.display_id)
    if not isinstance(rescue, Mapping) or not isinstance(completeness, Mapping):
        return False
    completeness_rejected = (
        rescue.get("candidate_binding_sha256") == _candidate_binding(raw)
        and completeness.get("display_id") == request.display_id
        and completeness.get("unit_uid") == request.unit_uid
        and completeness.get("passed") is False
    )
    if completeness_rejected:
        return True
    if not isinstance(repair, Mapping) or not isinstance(final, Mapping):
        return False
    try:
        final_review = ReviewResult.model_validate(final.get("review"))
        blocking = BLOCKING_SEMANTIC in review_issue_severities(issue.code for issue in final_review.issues)
    except Exception:
        return False
    return (
        rescue.get("candidate_binding_sha256") == _candidate_binding(raw)
        and repair.get("authority_parent_verified") is True
        and repair.get("parent_candidate_sha256") == _candidate_binding(raw)
        and completeness.get("display_id") == request.display_id
        and completeness.get("unit_uid") == request.unit_uid
        and completeness.get("passed") is True
        and final_review.verdict in {"REPAIR", "HUMAN_REQUIRED"}
        and blocking
    )


def _optional_review_result(payload: Any) -> ReviewResult | None:
    """Decode append-only optional review evidence without inventing a verdict."""
    return ReviewResult.model_validate(payload) if isinstance(payload, Mapping) else None


def _reconstruct_outcome(request, stored: Mapping[str, Any], raw: TranslationResult) -> UnitOutcome:
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request))
    outcome.raw = raw
    working = stored.get("working")
    outcome.working = TranslationResult.model_validate(working) if isinstance(working, dict) else raw
    outcome.qa = stored.get("qa") if isinstance(stored.get("qa"), dict) else None
    review = stored.get("review")
    if isinstance(review, dict):
        outcome.review = ReviewResult.model_validate(review)
    elif review in {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}:
        outcome.review = ReviewResult(
            schema_version="booktrans.structured-review.v1",
            unit_uid=request.unit_uid,
            verdict=review,
            issues=[],
        )
    second = stored.get("second_review")
    outcome.second_review = _optional_review_result(second)
    outcome.final_status = str(stored.get("final_status") or "PENDING")
    outcome.failure_family = stored.get("failure_family")
    outcome.failure_code = stored.get("failure_code")
    outcome.semantic_terminal = stored.get("semantic_terminal")
    outcome.human_required = bool(stored.get("human_required"))
    outcome.repair_applied = bool(stored.get("repair_applied"))
    outcome.repair_policy_id = stored.get("repair_policy_id")
    outcome.reviewer_dispatch = stored.get("reviewer_dispatch")
    outcome.translation_api_calls = int(stored.get("translation_api_calls") or 0)
    outcome.reviewer_api_calls = int(stored.get("reviewer_api_calls") or 0)
    outcome.repair_api_calls = int(stored.get("repair_api_calls") or 0)
    outcome.recovered_from = "unit"
    return outcome


def _reprocess_record(outcome: UnitOutcome, *, source_sha256: str, selection_sha256: str) -> dict[str, Any]:
    return {
        "artifact_type": "PRODUCTION_REPROCESS_FROM_RAW",
        "schema_version": 1,
        "display_id": outcome.request.display_id,
        "unit_uid": outcome.request.unit_uid,
        "source_sha256": source_sha256,
        "selection_sha256": selection_sha256,
        "working": None if outcome.working is None else outcome.working.model_dump(),
        "qa": outcome.qa,
        "review": None if outcome.review is None else outcome.review.model_dump(),
        "second_review": None if outcome.second_review is None else outcome.second_review.model_dump(),
        "model_echo_unit_uid": outcome.review_model_echo_unit_uid,
        "model_echo_diagnostic": "MODEL_ECHO_ID_MISMATCH" if outcome.review_model_echo_id_mismatch else None,
        "final_status": outcome.final_status,
        "semantic_terminal": semantic_terminal_of_outcome(outcome),
        "human_required": outcome.human_required,
        "repair_applied": outcome.repair_applied,
        "repair_policy_id": outcome.repair_policy_id,
        "reviewer_dispatch": outcome.reviewer_dispatch,
        "translation_api_calls": outcome.translation_api_calls,
        "reviewer_api_calls": outcome.reviewer_api_calls,
        "repair_api_calls": outcome.repair_api_calls,
        "recovered_from": "raw",
    }


def _merge_latest_unit_record(checkpoint_dir: Path, display_id: str, stored: Mapping[str, Any]) -> dict[str, Any]:
    """Collect compatibility evidence without selecting current QA authority.

    Candidate Authority is resolved by the caller.  The many durable stages
    below are intentionally still merged for their review/recovery semantics,
    but their historical ``qa`` payload is retained only as provenance.  A
    caller that knows the current head must project QA with
    ``_project_current_effective_qa`` before constructing a current outcome.
    """
    merged = dict(stored)
    reprocess = load_stage(checkpoint_dir, "production_reprocess", display_id)
    if reprocess is not None:
        for key in (
            "working",
            "qa",
            "review",
            "second_review",
            "final_status",
            "semantic_terminal",
            "human_required",
            "repair_applied",
            "repair_policy_id",
            "reviewer_api_calls",
            "repair_api_calls",
            "translation_api_calls",
        ):
            if key in reprocess:
                merged[key] = reprocess[key]
        merged["recovered_from"] = "raw"
    d050_reprocess = load_stage(checkpoint_dir, "d050_preflight_reprocess", display_id)
    if d050_reprocess is not None:
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in d050_reprocess:
                merged[key] = d050_reprocess[key]
        merged["recovered_from"] = "d050_preflight_reprocess"
    auto_polish = load_stage(checkpoint_dir, "auto_polish_resolutions", display_id)
    if auto_polish is not None and isinstance(auto_polish.get("working"), dict):
        merged["working"] = auto_polish["working"]
        if isinstance(auto_polish.get("qa"), dict):
            merged["qa"] = auto_polish["qa"]
        merged["review"] = auto_polish.get("review")
        merged["second_review"] = auto_polish.get("second_review")
        merged["final_status"] = "ACCEPT"
        merged["semantic_terminal"] = "MODEL_ACCEPTED"
        merged["human_required"] = False
        merged["repair_applied"] = True
        merged["repair_policy_id"] = "AUTO_POLISH"
        merged["recovered_from"] = "auto_polish"
    human = load_stage(checkpoint_dir, "human_approved_candidates", display_id)
    if human is not None and isinstance(human.get("working"), dict):
        merged["working"] = human["working"]
        if isinstance(human.get("qa"), dict):
            merged["qa"] = human["qa"]
        merged["semantic_terminal"] = "HUMAN_APPROVED"
        merged["final_status"] = "HUMAN_APPROVED"
        merged["human_required"] = False
        merged["repair_policy_id"] = "HUMAN_SEMANTIC_ADJUDICATION"
    rescue = load_stage(checkpoint_dir, "semantic_rescue", display_id)
    rereview_for_incomplete_rescue = load_stage(checkpoint_dir, "semantic_rescue_rereviews", display_id)
    incomplete_rescue_rereviewed = (
        rescue is not None
        and rescue.get("repair_applied") is False
        and rescue.get("review") is None
        and rescue.get("second_review") is None
        and isinstance(rereview_for_incomplete_rescue, Mapping)
        and isinstance(rereview_for_incomplete_rescue.get("review"), Mapping)
        and rereview_for_incomplete_rescue["review"].get("verdict") == "ACCEPT"
    )
    if incomplete_rescue_rereviewed and isinstance(rescue.get("qa"), Mapping):
        merged["qa"] = rescue["qa"]
    if rescue is not None and not incomplete_rescue_rereviewed:
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in rescue:
                merged[key] = rescue[key]
        final_payload = rescue.get("second_review")
        if isinstance(final_payload, Mapping):
            try:
                final_review = ReviewResult.model_validate(final_payload)
                final_severities = review_issue_severities(issue.code for issue in final_review.issues)
                if final_review.verdict != "HUMAN_REQUIRED" and BLOCKING_SEMANTIC not in final_severities:
                    merged["final_status"] = "ACCEPT"
                    merged["semantic_terminal"] = "MODEL_ACCEPTED"
                    merged["human_required"] = False
            except Exception:
                pass
        merged["recovered_from"] = "semantic_rescue"
    rejected_queue_repair = load_stage(checkpoint_dir, "resolution_queue_rejected_repair_candidates", display_id)
    if rejected_queue_repair is not None and isinstance(rejected_queue_repair.get("working"), dict):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in rejected_queue_repair:
                merged[key] = rejected_queue_repair[key]
        merged["recovered_from"] = "resolution_queue_rejected_repair"
    queue_resolution = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", display_id)
    if queue_resolution is not None and isinstance(queue_resolution.get("working"), dict):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in queue_resolution:
                merged[key] = queue_resolution[key]
        merged["recovered_from"] = "resolution_queue_auto_resolution"
    queue_disposition = load_stage(checkpoint_dir, "resolution_queue_auto_dispositions", display_id)
    if queue_disposition is not None and isinstance(queue_disposition.get("working"), dict):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in queue_disposition:
                merged[key] = queue_disposition[key]
        merged["recovered_from"] = "resolution_queue_auto_disposition"
    d051_qa = load_stage(checkpoint_dir, "d051_qa", display_id)
    if d051_qa is not None and isinstance(d051_qa.get("qa"), dict):
        merged["qa"] = d051_qa["qa"]
        merged["recovered_from"] = "d051_approved"
    rereview = load_stage(checkpoint_dir, "semantic_rescue_rereviews", display_id)
    if rereview is not None:
        review = rereview.get("review")
        if isinstance(review, dict):
            merged["review"] = review
            # This is the latest valid review after a proven context/binding
            # gap; never let an older second review choose the next repair.
            merged["second_review"] = review
        if review and review.get("verdict") == "ACCEPT":
            merged["final_status"] = "ACCEPT"
            merged["semantic_terminal"] = "MODEL_ACCEPTED"
            merged["human_required"] = False
        else:
            merged["final_status"] = "HUMAN_REQUIRED"
            merged["semantic_terminal"] = "HUMAN_REQUIRED"
            merged["human_required"] = True
        merged["reviewer_api_calls"] = int(rereview.get("reviewer_api_calls") or 0)
        merged["recovered_from"] = "semantic_rescue_rereview"
    polish = load_stage(checkpoint_dir, "semantic_rescue_polish", display_id)
    polish_review = load_stage(checkpoint_dir, "semantic_rescue_polish_reviews", display_id)
    if polish is not None and isinstance(polish.get("working"), dict) and polish_review is not None:
        merged["working"] = polish["working"]
        merged["review"] = polish_review.get("review")
        if isinstance(d051_qa.get("qa") if d051_qa else None, dict):
            merged["qa"] = d051_qa["qa"]
        severities = set(polish_review.get("review_issue_severities") or [])
        verdict = (polish_review.get("review") or {}).get("verdict")
        if verdict != "HUMAN_REQUIRED" and BLOCKING_SEMANTIC not in severities:
            merged["final_status"] = "ACCEPT"; merged["semantic_terminal"] = "MODEL_ACCEPTED"; merged["human_required"] = False
        else:
            merged["final_status"] = "HUMAN_REQUIRED"; merged["semantic_terminal"] = "HUMAN_REQUIRED"; merged["human_required"] = True
        merged["repair_policy_id"] = "AUTO_POLISH"
        merged["recovered_from"] = "auto_polish"
    # A queue disposition is newer than any reviewer result it classifies, but
    # still remains below an explicit human adjudication.
    queue_disposition = load_stage(checkpoint_dir, "resolution_queue_auto_dispositions", display_id)
    if queue_disposition is not None and isinstance(queue_disposition.get("working"), dict):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in queue_disposition:
                merged[key] = queue_disposition[key]
        merged["recovered_from"] = "resolution_queue_auto_disposition"
    # A semantic rescue is created after the ordinary queue attempt. Its
    # durable terminal is therefore newer than the queue record; preserve that
    # ordering when deriving the active candidate for a resumed checkpoint.
    rescue = load_stage(checkpoint_dir, "semantic_rescue", display_id)
    rereview_for_late_rescue = load_stage(checkpoint_dir, "semantic_rescue_rereviews", display_id)
    late_incomplete_rescue_rereviewed = (
        rescue is not None
        and rescue.get("repair_applied") is False
        and rescue.get("review") is None
        and rescue.get("second_review") is None
        and isinstance(rereview_for_late_rescue, Mapping)
        and isinstance(rereview_for_late_rescue.get("review"), Mapping)
        and rereview_for_late_rescue["review"].get("verdict") == "ACCEPT"
    )
    if late_incomplete_rescue_rereviewed and isinstance(rescue.get("qa"), Mapping):
        merged["qa"] = rescue["qa"]
    if rescue is not None and not late_incomplete_rescue_rereviewed:
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in rescue:
                merged[key] = rescue[key]
        final_payload = rescue.get("second_review")
        if isinstance(final_payload, Mapping):
            try:
                final_review = ReviewResult.model_validate(final_payload)
                if (
                    final_review.verdict != "HUMAN_REQUIRED"
                    and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in final_review.issues)
                ):
                    merged["final_status"] = "ACCEPT"
                    merged["semantic_terminal"] = "MODEL_ACCEPTED"
                    merged["human_required"] = False
            except Exception:
                pass
        merged["recovered_from"] = "semantic_rescue"
    # Candidate Authority appends a verified queue repair after the older
    # semantic-rescue mutation.  Keep the resumed projection in that same
    # order: a rejected rescue must not overwrite the accepted queue head and
    # manufacture a fictitious lineage mismatch on the next worker.
    if (
        isinstance(queue_resolution, Mapping)
        and isinstance(queue_resolution.get("working"), Mapping)
        and queue_resolution.get("semantic_terminal") in SEMANTIC_PASS_TERMINALS
    ):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in queue_resolution:
                merged[key] = queue_resolution[key]
        merged["recovered_from"] = "resolution_queue_auto_resolution"
    # Exact D050 evidence can arrive after an earlier RAW-only semantic rescue
    # was rejected solely because its numeric overlay did not yet exist.  A
    # failed historical rescue is untrusted and cannot supersede the later,
    # Core-clean D050 reprocess of the same durable candidate.  A passing
    # rescue keeps normal precedence.
    d050_reprocess = load_stage(checkpoint_dir, "d050_preflight_reprocess", display_id)
    if (
        d050_reprocess is not None
        and isinstance(d050_reprocess.get("working"), dict)
        and isinstance(d050_reprocess.get("qa"), Mapping)
        and bool(d050_reprocess["qa"].get("passed"))
        and isinstance(rescue, Mapping)
        and isinstance(rescue.get("qa"), Mapping)
        and not bool(rescue["qa"].get("passed"))
    ):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in d050_reprocess:
                merged[key] = d050_reprocess[key]
        merged["recovered_from"] = "d050_preflight_reprocess"
    # This is intentionally after every legacy queue/rescue merge above: a
    # rejection record restores the last known-good candidate and is newer
    # than the invalid repair candidate it documents.
    rejected_queue_repair = load_stage(checkpoint_dir, "resolution_queue_rejected_repair_candidates", display_id)
    rescue = load_stage(checkpoint_dir, "semantic_rescue", display_id)
    rescue_supersedes_rejection = False
    if isinstance(rescue, Mapping) and isinstance(rescue.get("second_review"), Mapping):
        try:
            rescue_final = ReviewResult.model_validate(rescue["second_review"])
            rescue_supersedes_rejection = (
                rescue_final.verdict != "HUMAN_REQUIRED"
                and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in rescue_final.issues)
            )
        except Exception:
            rescue_supersedes_rejection = False
    if (
        not rescue_supersedes_rejection
        and rejected_queue_repair is not None
        and isinstance(rejected_queue_repair.get("working"), dict)
    ):
        for key in (
            "working", "qa", "review", "second_review", "final_status", "semantic_terminal",
            "human_required", "repair_applied", "repair_policy_id", "reviewer_api_calls",
            "repair_api_calls", "translation_api_calls",
        ):
            if key in rejected_queue_repair:
                merged[key] = rejected_queue_repair[key]
        merged["recovered_from"] = "resolution_queue_rejected_repair"
    # A typed D050 replay is intentionally independent of the legacy
    # semantic-rescue family.  It contributes compatibility state here, while
    # the authority resolver below remains the only candidate selector.
    d050_attempt = load_stage(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, display_id)
    if d050_attempt is not None and isinstance(d050_attempt.get("working"), Mapping):
        for key in (
            "working", "qa", "final_status", "semantic_terminal", "human_required",
            "repair_applied", "repair_policy_id", "reviewer_api_calls", "repair_api_calls",
            "translation_api_calls",
        ):
            if key in d050_attempt:
                merged[key] = d050_attempt[key]
        merged["review"] = d050_attempt.get("review")
        merged["second_review"] = d050_attempt.get("final_review")
        merged["recovered_from"] = "d050_exact_marker_replay"
    # A canonical rereview is explicitly bound to its request candidate. It
    # must therefore supersede a rejected repair's terminal verdict, while
    # retaining that rejected proposal as immutable historical evidence.
    rereview = load_stage(checkpoint_dir, "semantic_rescue_rereviews", display_id)
    rereview_request = load_stage(checkpoint_dir, "semantic_rescue_rereview_requests", display_id)
    if (
        isinstance(rereview, Mapping)
        and isinstance(rereview_request, Mapping)
        and isinstance(rereview.get("review"), Mapping)
        and isinstance(rereview_request.get("target_segments"), list)
    ):
        try:
            current = TranslationResult(unit_uid=str(rereview_request.get("unit_uid") or ""), target_segments=[StructuredTargetSegment.model_validate(segment) for segment in rereview_request["target_segments"]])
            bound = (
                rereview_request.get("display_id") == display_id
                and bool(rereview_request.get("unit_uid"))
                and rereview_request.get("candidate_binding_sha256") == _candidate_binding(current)
                and rereview.get("candidate_binding_sha256") == _candidate_binding(current)
                and rereview.get("context_sha256") == rereview_request.get("context_sha256")
                and rereview.get("semantic_group_hash") == rereview_request.get("semantic_group_hash")
            )
        except Exception:
            bound = False
        if bound:
            merged["working"] = current.model_dump()
            merged["review"] = rereview["review"]
            merged["second_review"] = rereview["review"]
            merged["final_status"] = "ACCEPT" if rereview["review"].get("verdict") == "ACCEPT" else "HUMAN_REQUIRED"
            merged["semantic_terminal"] = "MODEL_ACCEPTED" if rereview["review"].get("verdict") == "ACCEPT" else "HUMAN_REQUIRED"
            merged["human_required"] = rereview["review"].get("verdict") != "ACCEPT"
            merged["recovered_from"] = "semantic_rescue_rereview"
    # A later explicit human decision is authoritative over all prior model
    # rescue evidence; RAW remains untouched either way.
    human = load_stage(checkpoint_dir, "human_approved_candidates", display_id)
    if human is not None and isinstance(human.get("working"), dict):
        merged["working"] = human["working"]
        if isinstance(human.get("qa"), dict):
            merged["qa"] = human["qa"]
        merged["semantic_terminal"] = "HUMAN_APPROVED"
        merged["final_status"] = "HUMAN_APPROVED"
        merged["human_required"] = False
        merged["repair_policy_id"] = "HUMAN_SEMANTIC_ADJUDICATION"
        merged["recovered_from"] = "human_adjudication"
    # A unit result is historical, derived compatibility evidence.  When an
    # authority state exists, it is the only permitted source for the current
    # candidate.  This prevents an old ``working`` field from silently
    # overriding RAW or a bound accepted mutation during a later resume/seal.
    authoritative = load_stage(checkpoint_dir, "authoritative_candidate_states", display_id)
    normal_mutations = _accepted_candidate_mutation_records(checkpoint_dir, display_id)
    normal_mutation = normal_mutations[-1] if normal_mutations else None
    queue_resolution = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", display_id)
    rescue = load_stage(checkpoint_dir, "semantic_rescue", display_id)
    # A bound human-approved candidate is the latest semantic terminal.  It
    # must keep its materialized working text when a resume also sees an old
    # authoritative-state sidecar or rejected rescue history.
    newer_bound_authority = bool(
        isinstance(human, Mapping) and isinstance(human.get("working"), Mapping)
    ) or bool(
        isinstance(normal_mutation, Mapping) and normal_mutation.get("accepted") is True
    ) or bool(
        isinstance(queue_resolution, Mapping)
        and queue_resolution.get("semantic_terminal") in SEMANTIC_PASS_TERMINALS
    )
    if isinstance(rescue, Mapping):
        rescue_final = rescue.get("second_review")
        if isinstance(rescue_final, Mapping):
            try:
                parsed_final = ReviewResult.model_validate(rescue_final)
                newer_bound_authority = newer_bound_authority or (
                    parsed_final.verdict != "HUMAN_REQUIRED"
                    and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in parsed_final.issues)
                )
            except Exception:
                pass
    d050_attempt = load_stage(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, display_id)
    newer_bound_authority = newer_bound_authority or bool(
        isinstance(d050_attempt, Mapping) and d050_attempt.get("accepted") is True
    )
    # This sidecar records the resolver head observed at one earlier resume.
    # It is immutable diagnostic evidence, not a precedence source after a
    # later bound candidate mutation has been durably accepted.
    if (
        not newer_bound_authority
        and authoritative is not None
        and isinstance(authoritative.get("working"), dict)
    ):
        candidate = TranslationResult.model_validate(authoritative["working"])
        if authoritative.get("candidate_binding_sha256") == _candidate_binding(candidate):
            merged["working"] = candidate.model_dump()
            if isinstance(authoritative.get("qa"), Mapping):
                merged["qa"] = dict(authoritative["qa"])
            merged["recovered_from"] = "authoritative_candidate_state"
    # Candidate precedence is now final.  Only after it has selected the
    # current head may a canonical rereview become terminal evidence.
    if (
        human is None
        and isinstance(rereview, Mapping)
        and isinstance(rereview_request, Mapping)
        and isinstance(rereview.get("review"), Mapping)
        and isinstance(merged.get("working"), Mapping)
    ):
        try:
            current = TranslationResult.model_validate(merged["working"])
            bound = (
                rereview_request.get("display_id") == display_id
                and rereview_request.get("candidate_binding_sha256") == _candidate_binding(current)
                and rereview.get("candidate_binding_sha256") == _candidate_binding(current)
                and rereview.get("context_sha256") == rereview_request.get("context_sha256")
                and rereview.get("semantic_group_hash") == rereview_request.get("semantic_group_hash")
            )
        except Exception:
            bound = False
        if bound:
            merged["review"] = rereview["review"]
            merged["second_review"] = rereview["review"]
            merged["final_status"] = "ACCEPT" if rereview["review"].get("verdict") == "ACCEPT" else "HUMAN_REQUIRED"
            merged["semantic_terminal"] = "MODEL_ACCEPTED" if rereview["review"].get("verdict") == "ACCEPT" else "HUMAN_REQUIRED"
            merged["human_required"] = rereview["review"].get("verdict") != "ACCEPT"
            merged["recovered_from"] = "semantic_rescue_rereview"
    # ``qa`` in unit/recovery records is immutable historical evidence.  Do
    # not let the last file visited make it look like QA for the current
    # authoritative candidate.  Preserve it for compatibility/audit only;
    # current-state callers explicitly install effective QA after Candidate
    # Authority has selected the head.
    historical_qa = merged.pop("qa", None)
    if isinstance(historical_qa, Mapping):
        merged["qa_evidence"] = dict(historical_qa)
    return merged


def _project_current_effective_qa(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    resolved_candidate,
    qa,
    view,
    known_unit_ids,
    bible: Path | None,
    current_raw_qa: CurrentRawQaPayload | None = None,
) -> dict[str, Any]:
    """Project the one current QA truth after Candidate Authority resolves it.

    This is an adapter around the existing ``resolve_effective_qa`` authority,
    not a second QA resolver.  Historical RAW QA remains append-only audit
    evidence and is consulted only by that resolver.
    """
    candidate = resolved_candidate.candidate
    current_qa = _qa_with_d051(
        qa, view, request, candidate, known_unit_ids,
        _d051_equivalence_for(checkpoint_dir, request, candidate, bible=bible),
    )
    # When the checkpoint-level current-acquisition resolver has supplied a
    # re-evaluation for this exact RAW head, it is the sole RAW-QA input.
    # Historical raw QA is then audit evidence only.
    if current_raw_qa is not None:
        # This is the current checkpoint projection boundary. Once supplied,
        # its resolver-validated acquisition is the only RAW-QA input;
        # historical reports remain audit evidence and cannot decide current QA.
        resolved_raw_qa = current_raw_qa.records_by_uid.get(request.unit_uid)
        expected_request_binding = _provider_request_for_decision(
            request, bible=bible, decision=route_semantic_unit(request),
        ).request_binding_sha256
        candidate_binding = _candidate_binding(candidate)
        raw_binding = _candidate_binding(raw)
        raw_binding_valid = (
            isinstance(resolved_raw_qa, Mapping)
            and resolved_raw_qa.get("unit_uid") == request.unit_uid
            and resolved_raw_qa.get("display_id") == request.display_id
            and current_raw_qa.source_sha256 == request.source_sha256
            and current_raw_qa.request_binding_by_uid.get(request.unit_uid) == expected_request_binding
            and current_raw_qa.acquisition_binding_by_uid.get(request.unit_uid) == raw_binding
        )
        if not raw_binding_valid:
            return {
                "unit_uid": request.unit_uid,
                "display_id": request.display_id,
                "passed": False,
                "diagnostics": [{"code": "CURRENT_RAW_QA_BINDING_INVALID"}],
                "effective_qa_reason": "RESOLVER_VALIDATED_EFFECTIVE_ACQUISITION_INVALID_CURRENT_RAW_QA",
                "effective_qa_candidate_binding_sha256": resolved_candidate.candidate_sha256,
            }
        if candidate_binding == raw_binding:
            projected = dict(resolved_raw_qa)
            projected["effective_qa_reason"] = "RESOLVER_VALIDATED_EFFECTIVE_ACQUISITION_RAW_QA"
            projected["effective_qa_candidate_binding_sha256"] = resolved_candidate.candidate_sha256
            return projected
        # Candidate Authority has already proved this is the unique current
        # descendant.  Its QA belongs to the existing reconciliation path;
        # CurrentRawQaPayload authenticates the parent acquisition only.
    raw_qa_path = checkpoint_dir / "translation_raw_qa_report.json"
    historical_qa_record: Mapping[str, Any] | None = None
    if raw_qa_path.is_file():
        try:
            report = json.loads(raw_qa_path.read_text(encoding="utf-8"))
            historical_qa_record = next(
                (
                    row for row in report.get("records", [])
                    if isinstance(row, Mapping) and row.get("display_id") == request.display_id
                ),
                None,
            )
        except (OSError, json.JSONDecodeError, TypeError):
            historical_qa_record = None
    if historical_qa_record is None:
        effective = {
            "state": "PASS" if bool(current_qa.get("passed")) else "FAIL",
            "reason": "CURRENT_HEAD_CORE_QA_ONLY",
        }
    else:
        # Rebuild reaches canonical disposition before the checkpoint sealing
        # tail.  A valid descendant with exact current-head ACCEPT must create
        # its existing reconciliation *here*, otherwise an immutable RAW QA
        # failure is incorrectly projected as the current unresolved state and
        # prevents the later sealing path from ever running.
        _ensure_current_candidate_qa_reconciliation(
            checkpoint_dir=checkpoint_dir, request=request, raw=raw,
            resolved_candidate=resolved_candidate, original_qa_path=raw_qa_path,
            original_qa_record=historical_qa_record, rerun_qa=current_qa,
            bible=bible,
        )
        effective = resolve_effective_qa(
            checkpoint_dir=checkpoint_dir,
            request=request,
            raw=raw,
            current_head=resolved_candidate,
            historical_qa_record=historical_qa_record,
            current_qa=current_qa,
            original_qa_path=raw_qa_path,
            bible=bible,
        )
    projected = dict(current_qa)
    projected["passed"] = effective["state"] == "PASS"
    projected["effective_qa_reason"] = effective["reason"]
    projected["effective_qa_candidate_binding_sha256"] = resolved_candidate.candidate_sha256
    return projected


def _project_resolved_unit_record(
    merged: dict[str, Any],
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    resolved_candidate,
    qa,
    view,
    known_unit_ids,
    bible: Path | None,
    current_raw_qa: CurrentRawQaPayload | None = None,
) -> dict[str, Any]:
    """Attach current head and effective QA to collected historical evidence."""
    merged["working"] = resolved_candidate.candidate.model_dump()
    merged["qa"] = _project_current_effective_qa(
        checkpoint_dir=checkpoint_dir, request=request, raw=raw,
        resolved_candidate=resolved_candidate, qa=qa, view=view,
        known_unit_ids=known_unit_ids, bible=bible,
        current_raw_qa=current_raw_qa,
    )
    return merged


def _project_complete_current_outcomes(
    *, checkpoint_dir: Path, selected_requests, outcomes: list[UnitOutcome],
    qa, views, known_unit_ids, bible: Path | None,
    current_raw_qa: CurrentRawQaPayload | None,
) -> list[UnitOutcome]:
    """The sole in-memory current-head/QA projection before materialization.

    Recovery branches may construct outcomes, but never own effective-QA
    binding.  Incomplete outcomes remain untouched for the existing
    materializer to fail closed; every complete outcome is re-bound through
    Candidate Authority and the existing QA adapter exactly once here.
    """
    selected = {request.display_id: request for request in selected_requests}
    projected: list[UnitOutcome] = []
    for outcome in outcomes:
        request = selected.get(outcome.request.display_id)
        if request is None or request.unit_uid != outcome.request.unit_uid:
            raise RuntimeError("CURRENT_OUTCOME_IDENTITY_MISMATCH")
        if outcome.raw is None or outcome.working is None or outcome.qa is None:
            projected.append(outcome)
            continue
        if outcome.raw.unit_uid != request.unit_uid or outcome.request.source_sha256 != request.source_sha256:
            raise RuntimeError("CURRENT_OUTCOME_SOURCE_BINDING_MISMATCH")
        current = _resolve_checkpoint_authoritative_candidate(
            checkpoint_dir, request.display_id, outcome.raw,
        )
        outcome.working = current.candidate
        outcome.qa = _project_current_effective_qa(
            checkpoint_dir=checkpoint_dir, request=request, raw=outcome.raw,
            resolved_candidate=current, qa=qa,
            view=views[request.display_id], known_unit_ids=known_unit_ids,
            bible=bible, current_raw_qa=current_raw_qa,
        )
        projected.append(outcome)
    return projected


def _project_current_unit_control_state(*, checkpoint_dir: Path, request, raw: TranslationResult,
                                        projected: Mapping[str, Any]) -> CurrentUnitControlState:
    """Compose existing current-head policy before resume branches inspect a terminal."""
    # Do not let unbound historical reviews or terminal flags become current
    # control input. Canonical disposition re-loads only exact-head evidence.
    current = dict(projected)
    current.pop("review", None)
    current.pop("second_review", None)
    current.pop("semantic_terminal", None)
    current.pop("human_required", None)
    current.pop("final_status", None)
    outcome = _reconstruct_outcome(request, current, raw)
    disposition = canonical_production_disposition(outcome, checkpoint_dir=checkpoint_dir)
    decision = str(disposition["disposition"])
    human = load_stage(checkpoint_dir, "human_adjudications", request.display_id)
    human_approved = isinstance(human, Mapping) and human.get("decision") == "HUMAN_APPROVED"
    if human_approved:
        return CurrentUnitControlState("HUMAN_APPROVED", False, "HUMAN_APPROVED", decision)
    if decision == "CONTINUE":
        return CurrentUnitControlState("MODEL_ACCEPTED", False, "ACCEPT", decision)
    # Recovery, debt, and genuine current failure remain non-pass terminals;
    # their existing downstream branches retain the detailed policy.
    return CurrentUnitControlState("HUMAN_REQUIRED", True, "HUMAN_REQUIRED", decision)


def _materialize_current_outcomes_by_work_order(
    *, checkpoint_dir: Path, selected_requests, outcomes: list[UnitOutcome]
) -> dict[str, UnitOutcome]:
    """Build the one transient CURRENT outcome set used after unit processing.

    This is deliberately a projection, not an artifact writer or another
    policy owner.  Candidate Authority, effective QA, and canonical
    disposition already exist; this boundary binds those values to exactly one
    existing ``UnitOutcome`` per frozen work order.  Anything incomplete or
    ambiguous fails closed before handoff/final-QA can consult compatibility
    history.
    """
    selected = {request.display_id: request for request in selected_requests}
    if len(selected) != len(selected_requests):
        raise RuntimeError("CURRENT_OUTCOME_SELECTION_DUPLICATE")
    projected: dict[str, UnitOutcome] = {}
    for outcome in outcomes:
        display_id = outcome.request.display_id
        request = selected.get(display_id)
        if request is None or request.unit_uid != outcome.request.unit_uid:
            raise RuntimeError("CURRENT_OUTCOME_IDENTITY_MISMATCH")
        if display_id in projected:
            raise RuntimeError("CURRENT_OUTCOME_DUPLICATE")
        if outcome.raw is None or outcome.working is None or outcome.qa is None:
            raise RuntimeError("CURRENT_OUTCOME_INCOMPLETE")
        try:
            current = _resolve_checkpoint_authoritative_candidate(
                checkpoint_dir, display_id, outcome.raw,
            )
        except CandidateAuthorityError as exc:
            raise RuntimeError("CURRENT_OUTCOME_CANDIDATE_AUTHORITY_INVALID") from exc
        if _candidate_binding(outcome.working) != current.candidate_sha256:
            raise RuntimeError("CURRENT_OUTCOME_HEAD_BINDING_MISMATCH")
        effective_qa_binding = outcome.qa.get("effective_qa_candidate_binding_sha256")
        if effective_qa_binding not in (None, current.candidate_sha256):
            raise RuntimeError("CURRENT_OUTCOME_EFFECTIVE_QA_BINDING_MISMATCH")
        if effective_qa_binding is None:
            expected_request = enrich_request(request, translation_bible=None).request_binding_sha256
            qa_records = [
                record for record in _accepted_candidate_mutation_records(checkpoint_dir, display_id)
                if (
                    record.get("display_id") == display_id
                    and record.get("unit_uid") == request.unit_uid
                    and record.get("source_sha256") == request.source_sha256
                    and record.get("request_binding_sha256") == expected_request
                    and record.get("candidate_binding_sha256") == current.candidate_sha256
                    and record.get("accepted") is True
                    and bool((record.get("qa") or {}).get("passed"))
                )
            ]
            if (
                _candidate_binding(outcome.raw) == current.candidate_sha256
                and bool(outcome.qa.get("passed"))
            ):
                outcome = replace(
                    outcome,
                    qa={
                        **dict(outcome.qa),
                        "effective_qa_candidate_binding_sha256": current.candidate_sha256,
                        "effective_qa_reason": "CURRENT_RAW_HEAD_QA",
                    },
                )
            elif len(qa_records) == 1:
                outcome = replace(
                    outcome,
                    qa={
                        **dict(outcome.qa),
                        "effective_qa_candidate_binding_sha256": current.candidate_sha256,
                        "effective_qa_reason": "ACCEPTED_CURRENT_CANDIDATE_QA",
                    },
                )
            else:
                raise RuntimeError("CURRENT_OUTCOME_EFFECTIVE_QA_BINDING_MISSING")

        # A current-head rereview is exact-bound evidence.  For an unchanged
        # RAW head, the already-materialized normal review is also current;
        # no descendant may inherit a historical review just for compatibility.
        bound_review = _current_head_rereview(
            outcome, checkpoint_dir=checkpoint_dir, current=current.candidate,
        )
        raw_head = _candidate_binding(outcome.raw) == current.candidate_sha256
        current_review = bound_review or (outcome.review if raw_head else None)
        current_second_review = None if bound_review is not None else (
            outcome.second_review if raw_head else None
        )
        current_outcome = replace(
            outcome,
            request=request,
            working=current.candidate,
            review=current_review,
            second_review=current_second_review,
            # Historical terminal/final fields are provenance, never CURRENT
            # control inputs.  Canonical disposition below is the sole owner.
            final_status="PENDING",
            semantic_terminal=None,
            human_required=bool(current_review and current_review.verdict == "HUMAN_REQUIRED"),
            registry_decision={},
        )
        decision = canonical_production_disposition(
            current_outcome, checkpoint_dir=checkpoint_dir,
        )
        disposition = str(decision["disposition"])
        human = load_stage(checkpoint_dir, "human_adjudications", display_id)
        if isinstance(human, Mapping) and human.get("decision") == "HUMAN_APPROVED":
            terminal, status, requires_human = "HUMAN_APPROVED", "HUMAN_APPROVED", False
        elif disposition == "CONTINUE":
            terminal, status, requires_human = "MODEL_ACCEPTED", "ACCEPT", False
        elif disposition == "DEFERRED_SEMANTIC_DEBT":
            terminal, status, requires_human = "DEFERRED_SEMANTIC_DEBT", "DEFERRED_SEMANTIC_DEBT", False
        elif disposition == "RECOVERY_REQUIRED":
            terminal, status, requires_human = "RECOVERY_REQUIRED", "PENDING", False
        else:
            terminal, status, requires_human = "HARD_STOP", "HARD_STOP", bool(
                current_review and current_review.verdict == "HUMAN_REQUIRED"
            )
        projected[display_id] = replace(
            current_outcome, semantic_terminal=terminal, final_status=status,
            human_required=requires_human, registry_decision=dict(decision),
        )
    if set(projected) != set(selected):
        raise RuntimeError("CURRENT_OUTCOME_MISSING")
    return projected


def project_current_checkpoint_outcomes(
    *, run: Path, batch_label: str, checkpoint: str, loaded: Mapping[str, Any], qa=harness_qa_record,
) -> dict[str, UnitOutcome]:
    """Read the complete CURRENT outcome projection for a sealed checkpoint.

    This is provider-free and consumes the same Candidate Authority,
    effective-acquisition/QA, and canonical-disposition path used immediately
    before a production seal.  Consumers such as the Release Boundary must
    use this result rather than reconstructing a second current-head or
    Core-QA authority from historical debt evidence.
    """
    checkpoint_dir = production_checkpoint_path(run, batch_label, checkpoint)
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    effective_request_path = loaded.get("effective_translation_request_path")
    if not isinstance(effective_request_path, Path) or not effective_request_path.is_file():
        raise RuntimeError("CURRENT_OUTCOME_EFFECTIVE_REQUEST_MISSING")
    manifest_path = run / batch_label.lower() / "batch_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("CURRENT_OUTCOME_MANIFEST_MISSING")
    manifest_sha256 = str(loaded.get("sealed_execution_manifest_sha256") or sha256(manifest_path.read_bytes()).hexdigest())
    request_bindings = {
        request.unit_uid: _provider_request_for_decision(
            request, bible=bible, decision=route_semantic_unit(request),
        ).request_binding_sha256
        for request in loaded["requests"]
    }
    try:
        acquisitions, _historical = resolve_effective_provider_acquisition(
            checkpoint_dir=checkpoint_dir,
            requests=loaded["requests"],
            request_binding_by_uid=request_bindings,
            source_sha256=str(loaded["source_sha256"]),
            batch_id=batch_label,
            checkpoint_id=checkpoint,
            selection_sha256=str(loaded["selection_sha256"]),
            manifest_sha256=manifest_sha256,
            effective_request_artifact_sha256=sha256(effective_request_path.read_bytes()).hexdigest(),
        )
    except Exception as exc:
        raise RuntimeError("CURRENT_OUTCOME_EFFECTIVE_ACQUISITION_INVALID") from exc
    if _missing_effective_acquisitions(loaded["requests"], acquisitions):
        raise RuntimeError("CURRENT_OUTCOME_EFFECTIVE_ACQUISITION_INCOMPLETE")
    raw_by_uid = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl")
    current_raw_qa = _current_raw_qa_payload(
        requests=loaded["requests"], acquisitions=acquisitions,
        base_acquisitions=raw_by_uid, views=loaded["views"],
        known_unit_ids=loaded["known_unit_ids"], qa=qa,
        source_sha256=str(loaded["source_sha256"]), batch_id=batch_label,
        checkpoint_id=checkpoint, selection_sha256=str(loaded["selection_sha256"]),
        manifest_sha256=manifest_sha256,
        effective_request_sha256=sha256(effective_request_path.read_bytes()).hexdigest(),
        request_binding_by_uid=request_bindings,
        historical_raw_qa_path=checkpoint_dir / "translation_raw_qa_report.json",
    )
    stored_units = load_unit_results(checkpoint_dir)
    outcomes: list[UnitOutcome] = []
    for request in loaded["requests"]:
        raw = acquisitions.get(request.unit_uid)
        stored = stored_units.get(request.display_id)
        if raw is None or stored is None:
            raise RuntimeError("CURRENT_OUTCOME_PERSISTED_EVIDENCE_MISSING")
        try:
            current = _resolve_checkpoint_authoritative_candidate(
                checkpoint_dir, request.display_id, raw,
            )
        except CandidateAuthorityError as exc:
            raise RuntimeError("CURRENT_OUTCOME_CANDIDATE_AUTHORITY_INVALID") from exc
        projected = _project_resolved_unit_record(
            _merge_latest_unit_record(checkpoint_dir, request.display_id, stored),
            checkpoint_dir=checkpoint_dir, request=request, raw=raw,
            resolved_candidate=current, qa=qa,
            view=loaded["views"][request.display_id],
            known_unit_ids=loaded["known_unit_ids"], bible=bible,
            current_raw_qa=current_raw_qa,
        )
        outcomes.append(_reconstruct_outcome(request, projected, raw))
    outcomes = _project_complete_current_outcomes(
        checkpoint_dir=checkpoint_dir, selected_requests=loaded["requests"],
        outcomes=outcomes, qa=qa, views=loaded["views"],
        known_unit_ids=loaded["known_unit_ids"], bible=bible,
        current_raw_qa=current_raw_qa,
    )
    return _materialize_current_outcomes_by_work_order(
        checkpoint_dir=checkpoint_dir, selected_requests=loaded["requests"], outcomes=outcomes,
    )


def sealed_checkpoint_execution_inputs(
    *, run: Path, batch_label: str, checkpoint: str, source_path: Path,
) -> dict[str, Any]:
    """Load a sealed execution context without reopening request lifecycle.

    Active production continues to use ``current_batch_checkpoint_inputs`` and
    therefore retains its post-seal supersession guard.  This reader is only
    for an already-sealed checkpoint: it accepts a pre-seal request lineage
    only when its immutable derived request and all affected request-bound
    reacquisitions demonstrably predate the seal and share one sealed binding.
    """
    from booktrans.production_worker.unit_store import _read_request_rows, _same_physical_request, _sha256_file, _supersession_records
    from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs

    loaded = frozen_batch_checkpoint_inputs(
        run, batch_label=batch_label, checkpoint=checkpoint, source_path=source_path,
    )

    def bind_effective_views(requests) -> None:
        """Reuse the current mapper to make sealed request/view input atomic.

        Views are transient derived input, never sealed authority.  A frozen
        view is reused only after it regenerates the exact effective request;
        otherwise the existing source/IR mapper rebuilds that selected slice
        in memory and must prove the same topology before Core QA can run.
        """
        from booktrans.domain.ir import DocumentIR, DocumentStoryIR
        from booktrans.domain.models import SourceSnapshot
        from booktrans.harness.source_integrity import fingerprint
        from booktrans.ooxml.translation_view import TranslationViewBuilder
        from booktrans.production_worker.selection import build_calibration_request

        def structural_binding(request) -> tuple[object, ...]:
            return (
                request.source_sha256, request.unit_uid, request.display_id, request.source_text,
                tuple((segment.kind, segment.text, segment.anchor_ref, segment.island_ref)
                      for segment in request.source_segments),
                tuple((anchor.anchor_id, tuple(anchor.semantics), anchor.protected,
                       anchor.protected_text, anchor.container)
                      for anchor in request.available_anchors),
            )

        selected_blocks = [loaded["blocks"][request.display_id] for request in requests]
        snapshot = fingerprint(source_path)
        stories: dict[tuple[str, str], list] = {}
        for block in selected_blocks:
            stories.setdefault((block.story, block.part_name), []).append(block)
        document = DocumentIR(
            document_sha256=snapshot.sha256,
            source=SourceSnapshot(
                path=str(source_path), sha256=snapshot.sha256,
                size_bytes=snapshot.size_bytes, mtime_utc=snapshot.mtime_utc,
            ),
            extracted_at_utc=datetime.now(timezone.utc),
            stories=[DocumentStoryIR(story=story, part_name=part, blocks=blocks)
                     for (story, part), blocks in stories.items()],
        )
        rebuilt = {view.display_id: view for view in TranslationViewBuilder().build(source_path, document)}
        rebuilt_count = 0
        for request in requests:
            display_id = request.display_id
            frozen_view = loaded["views"].get(display_id)
            if frozen_view is None:
                raise RuntimeError("SEALED_EXECUTION_CONTEXT_VIEW_MISSING")
            frozen_request = build_calibration_request(
                frozen_view, loaded["blocks"][display_id], source_sha256=str(loaded["source_sha256"]),
            )
            if structural_binding(frozen_request) == structural_binding(request):
                continue
            view = rebuilt.get(display_id)
            if view is None:
                raise RuntimeError("SEALED_EXECUTION_CONTEXT_VIEW_REBUILD_MISSING")
            rebuilt_request = build_calibration_request(
                view, loaded["blocks"][display_id], source_sha256=str(loaded["source_sha256"]),
            )
            if structural_binding(rebuilt_request) != structural_binding(request):
                raise RuntimeError("SEALED_EXECUTION_CONTEXT_REQUEST_VIEW_MISMATCH")
            loaded["views"][display_id] = view
            rebuilt_count += 1
        loaded["sealed_view_rebuilt_count"] = rebuilt_count
        loaded["sealed_view_frozen_reuse_count"] = len(requests) - rebuilt_count

    checkpoint_dir = production_checkpoint_path(run, batch_label, checkpoint)
    records = _supersession_records(checkpoint_dir)
    if not records:
        # The ordinary immutable request is already the effective execution
        # input.  Keep the same explicit owner/path contract as the derived
        # branch below so Release never invents its own request projection.
        effective_path = checkpoint_dir / "translation_request.jsonl"
        rows = _read_request_rows(effective_path)
        if [request.display_id for request in rows] != [request.display_id for request in loaded["requests"]]:
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_ORIGINAL_MEMBERSHIP_MISMATCH")
        loaded["requests"] = rows
        loaded["effective_translation_request_path"] = effective_path
        bind_effective_views(rows)
        return loaded
    seal = checkpoint_dir / "checkpoint_evidence.json"
    if not seal.is_file():
        raise RuntimeError("SEALED_EXECUTION_CONTEXT_SEAL_MISSING")
    original = checkpoint_dir / "translation_request.jsonl"
    current_path = original
    current_rows = _read_request_rows(original)
    expected_order = [request.display_id for request in loaded["requests"]]
    if [request.display_id for request in current_rows] != expected_order:
        raise RuntimeError("SEALED_EXECUTION_CONTEXT_ORIGINAL_MEMBERSHIP_MISMATCH")
    by_parent: dict[str, tuple[dict[str, Any], Path, list]] = {}
    for record_path, record in records:
        required = {
            "artifact_type": "PRE_SEAL_DERIVED_REQUEST_SUPERSESSION",
            "source_sha256": str(loaded["source_sha256"]),
            "batch_id": batch_label,
            "checkpoint_id": checkpoint,
            "selection_sha256": str(loaded["selection_sha256"]),
        }
        if any(record.get(key) != value for key, value in required.items()):
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_BINDING_INVALID")
        parent_sha, child_sha, child_name = record.get("parent_request_sha256"), record.get("child_request_sha256"), record.get("child_request_artifact")
        if not all(isinstance(value, str) for value in (parent_sha, child_sha, child_name)):
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_BINDING_INVALID")
        child_path = checkpoint_dir / child_name
        child_rows = _read_request_rows(child_path)
        if _sha256_file(child_path) != child_sha or [request.display_id for request in child_rows] != expected_order:
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_CHILD_INVALID")
        if parent_sha in by_parent or record_path.stat().st_mtime_ns > seal.stat().st_mtime_ns or child_path.stat().st_mtime_ns > seal.stat().st_mtime_ns:
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_POST_SEAL_OR_FORK")
        by_parent[parent_sha] = (record, child_path, child_rows)
    seen: set[str] = set()
    terminal_parent: str | None = None
    while _sha256_file(current_path) in by_parent:
        parent_sha = _sha256_file(current_path)
        if parent_sha in seen:
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_CYCLE")
        seen.add(parent_sha)
        record, child_path, child_rows = by_parent[parent_sha]
        if len(current_rows) != len(child_rows) or any(
            not _same_physical_request(left, right)
            for left, right in zip(current_rows, child_rows, strict=True)
        ):
            raise RuntimeError("SEALED_EXECUTION_CONTEXT_SOURCE_IDENTITY_CHANGED")
        terminal_parent = parent_sha
        current_path, current_rows = child_path, child_rows
    if len(seen) != len(records):
        raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_UNREACHABLE")
    if terminal_parent is None:
        raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_UNREACHABLE")
    terminal_record = by_parent[terminal_parent][0]
    manifest_sha = terminal_record.get("manifest_sha256")
    if not isinstance(manifest_sha, str):
        raise RuntimeError("SEALED_EXECUTION_CONTEXT_SUPERSESSION_BINDING_INVALID")
    loaded["requests"] = current_rows
    loaded["effective_translation_request_path"] = current_path
    loaded["sealed_execution_manifest_sha256"] = manifest_sha
    bind_effective_views(current_rows)
    return loaded


def _rescue_record(outcome: UnitOutcome, *, mode: str, group, raw: TranslationResult) -> dict[str, Any]:
    record = {
        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE",
        "schema_version": 1,
        "display_id": outcome.request.display_id,
        "unit_uid": outcome.request.unit_uid,
        "mode": mode,
        "candidate_binding_sha256": sha256(raw.model_dump_json().encode("utf-8")).hexdigest(),
        "semantic_group_id": None if group is None else group.semantic_review_group_id,
        "semantic_group_hash": None if group is None else group.group_hash,
        "working": None if outcome.working is None else outcome.working.model_dump(),
        "qa": outcome.qa,
        "review": None if outcome.review is None else outcome.review.model_dump(),
        "second_review": None if outcome.second_review is None else outcome.second_review.model_dump(),
        "final_status": outcome.final_status,
        "semantic_terminal": semantic_terminal_of_outcome(outcome),
        "human_required": outcome.human_required,
        "repair_applied": outcome.repair_applied,
        "repair_policy_id": outcome.repair_policy_id,
        "translation_api_calls": 0,
        "reviewer_api_calls": outcome.reviewer_api_calls,
        "repair_api_calls": outcome.repair_api_calls,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if mode == "D050_EXACT_MARKER_REPAIR":
        record["deterministic_repair_basis"] = {
            "type": "D050_EXACT_VISIBLE_MARKER",
            "authority": "VERIFIED_D050_BINDING",
            "reviewer_decision_is_not_the_authority": True,
        }
    return record


def _post_repair_final_review_binding(request, candidate: TranslationResult, group) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Rebuild and verify the local binding for every post-repair final review."""
    candidate_text = "".join(segment.text for segment in candidate.target_segments)
    context = None if group is None else group.as_reviewer_context_payload()
    members = [] if group is None else [member.as_payload() for member in group.members]
    member_slice_sha = _canonical_sha256(
        [
            {"display_id": member["display_id"], "unit_uid": member["unit_uid"], "candidate_text": member["candidate_text"]}
            for member in members
        ]
    )
    if group is not None:
        current = next((member for member in members if member["unit_uid"] == request.unit_uid), None)
        rebuilt_span = " ".join(str(member["candidate_text"]).strip() for member in members if member.get("candidate_text")).strip()
        if (
            current is None
            or current.get("candidate_text") != candidate_text
            or group.full_candidate_span != rebuilt_span
            or context is None
            or context.get("full_candidate_span") != rebuilt_span
            or context.get("group_hash") != group.group_hash
        ):
            raise RuntimeError("FINAL_REVIEW_CANDIDATE_BINDING_MISMATCH")
    return context, {
        "candidate_binding_sha256": _candidate_binding(candidate),
        "member_slice_sha256": member_slice_sha,
        "group_span_sha256": sha256(str(None if group is None else group.full_candidate_span).encode("utf-8")).hexdigest(),
        "context_sha256": _canonical_sha256(dict(context or {})),
        "semantic_group_hash": None if group is None else group.group_hash,
    }


def finalize_recovery_candidate(
    *,
    request,
    base: TranslationResult,
    candidate: TranslationResult,
    parent_candidate_sha256: str | None,
    view,
    known_unit_ids,
    qa,
    group=None,
    rebuild_group: Callable[[TranslationResult], Any] | None = None,
    allow_index_owned_normalization: bool = False,
) -> tuple[TranslationResult, dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Shared fail-closed recovery tail before provider final-review persistence.

    Handlers may retain their distinct immutable artifact names and reviewer
    contract diagnostics, but none may bypass parent binding, Core QA, or the
    rebuilt candidate/group binding before proposing a final review.
    """
    require_parent_is_current(
        artifact="recovery_candidate",
        parent_candidate_sha256=parent_candidate_sha256,
        current_candidate_sha256=_candidate_binding(base),
    )
    # A provider repair is only a prepared candidate.  Before it can enter
    # Candidate Authority, the existing Index-owned deterministic normalizers
    # may complete one fixed two-step sequence.  Neither intermediate is
    # published: only the returned, Core-clean candidate reaches final review.
    prepared = candidate
    candidate_qa = qa(view, request, prepared, known_unit_ids)
    preparation_group = group if group is not None else (
        None if rebuild_group is None else rebuild_group(prepared)
    )
    if (
        allow_index_owned_normalization
        and not candidate_qa.get("passed")
        and set(qa_codes(candidate_qa)) == {"NUMBER_PRESERVATION"}
        and _index_locator_materialization_eligible(preparation_group, candidate_qa)
    ):
        materialized = materialize_index_locator(request, prepared)
        if materialized is not None:
            prepared = materialized
            candidate_qa = qa(view, request, prepared, known_unit_ids)
            # The fixed chain short-circuits as soon as this source-owned
            # transform satisfies every Core gate.  ``None`` from the next
            # allowlisted transform is therefore not a failure or a required
            # mutation; it merely has no work to do on this already-safe head.
            if not candidate_qa.get("passed"):
                corrected = correct_numeric_anchor_duplication(request, prepared)
                if corrected is not None:
                    prepared = corrected
                    candidate_qa = qa(view, request, prepared, known_unit_ids)
    if not candidate_qa.get("passed"):
        raise CandidateAuthorityError("RECOVERY_CANDIDATE_CORE_QA_FAILED")
    final_group = group if rebuild_group is None else rebuild_group(prepared)
    context, binding = _post_repair_final_review_binding(request, prepared, final_group)
    return prepared, candidate_qa, context, binding


def _stored_final_binding_matches(stored: Mapping[str, Any] | None, binding: Mapping[str, Any]) -> bool:
    return isinstance(stored, Mapping) and all(stored.get(key) == value for key, value in binding.items())


def _is_source_canonical_name(request) -> bool:
    """Closed-world fallback: a standalone, title-cased Latin proper name only."""
    source = " ".join(segment.text.strip() for segment in request.source_segments).strip()
    without_marker = re.sub(r"^\d+\s+", "", source)
    words = without_marker.split()
    return (
        len(words) >= 2
        and all(
            word.casefold() in {"and", "of", "the", "for", "in", "on", "to"}
            or re.fullmatch(r"[A-Z][A-Za-z0-9'’.-]*", word)
            for word in words
        )
        and source == request.source_text.strip()
    )


def _run_resolution_queue_auto_resolution(*, checkpoint_dir: Path, request, candidate: TranslationResult, review: ReviewResult, active: WorkerProviders, view, known_unit_ids, qa, bible, rebuild_group: Callable[[TranslationResult], Any] | None, mode: str) -> UnitOutcome:
    """One bounded repair from a durable Resolution Queue diagnostic; never translates."""
    provider_request = enrich_request(request, translation_bible=bible)
    qa_record = qa(view, request, candidate, known_unit_ids)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=candidate, working=candidate, qa=qa_record, recovered_from="resolution_queue_auto_resolution")
    if not qa_record.get("passed") or review.verdict != "REPAIR":
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    gate = decide_approved_repair_contract(provider_request, candidate, review=review, allow_semantic_narrow=True)
    if gate.action != "REPAIR" or gate.contract is None:
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    contract = gate.contract
    if mode == "CLOSED_WORLD_CANONICAL_NAME":
        if not _is_source_canonical_name(request):
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = "CANONICAL_NAME_AUTHORITY_AMBIGUOUS"; return outcome
        contract = contract.model_copy(update={
            "repair_instruction": "Replace only the permitted segment with the exact source canonical Latin-script proper name. Preserve any source-leading marker. Do not translate it, transliterate it, add an acronym, expansion, parentheses, or any unsupported text.",
        })
    saved_repair = load_stage(checkpoint_dir, "resolution_queue_auto_repairs", request.display_id)
    if saved_repair is None:
        try:
            result = repair_with_contract_boundary(active.repair, provider_request, candidate, contract)
            repaired = result.repaired
        except Exception as error:
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
        if mode == "CLOSED_WORLD_CANONICAL_NAME":
            expected = "".join(segment.text for segment in request.source_segments)
            actual = "".join(segment.text for segment in repaired.target_segments)
            if actual != expected:
                outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = "CANONICAL_NAME_PRESERVATION_MISMATCH"; return outcome
        persist_stage_once(checkpoint_dir, "resolution_queue_auto_repairs", request.display_id, {
            "artifact_type": "PRODUCTION_RESOLUTION_QUEUE_AUTO_REPAIR", "schema_version": 1,
            "mode": mode, "input_candidate_binding_sha256": _candidate_binding(candidate), "authority_parent_verified": True,
            "contract": contract.model_dump(), "working": repaired.model_dump(),
            "usage": dict(result.candidate.usage), "translation_api_calls": 0,
        })
        outcome.repair_api_calls = 1
    else:
        try:
            require_parent_is_current(
                artifact="resolution_queue_auto_repair",
                parent_candidate_sha256=str(saved_repair.get("input_candidate_binding_sha256") or "") or None,
                current_candidate_sha256=_candidate_binding(candidate),
            )
        except CandidateAuthorityError as error:
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = str(error); return outcome
        repaired = TranslationResult.model_validate(saved_repair["working"])
    try:
        repaired, repaired_qa, context, binding = finalize_recovery_candidate(
            request=request, base=candidate, candidate=repaired,
            parent_candidate_sha256=_candidate_binding(candidate), view=view,
            known_unit_ids=known_unit_ids, qa=qa, rebuild_group=rebuild_group,
        )
    except (CandidateAuthorityError, RuntimeError) as error:
        outcome.working = repaired; outcome.qa = qa(view, request, repaired, known_unit_ids)
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = str(error); return outcome
    saved_final = load_stage(checkpoint_dir, "resolution_queue_auto_final_reviews", request.display_id)
    if saved_final is None:
        try:
            result = review_with_contract_boundary(active.reviewer, provider_request, repaired, deterministic_qa_summary={"passed": True}, semantic_context=context)
        except Exception as error:
            outcome.working = repaired; outcome.qa = repaired_qa; outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
        final = result.review
        persist_stage_once(checkpoint_dir, "resolution_queue_auto_final_reviews", request.display_id, {
            "artifact_type": "PRODUCTION_RESOLUTION_QUEUE_AUTO_FINAL_REVIEW", "schema_version": 1,
            "mode": mode, **binding, "review": final.model_dump(),
            "usage": dict(result.candidate.usage), "request_binding_sha256": result.candidate.request_binding_sha256,
            "translation_api_calls": 0,
        })
        outcome.reviewer_api_calls = 1
    else:
        final = ReviewResult.model_validate(saved_final["review"])
    severities = review_issue_severities(issue.code for issue in final.issues)
    if _queue_repair_candidate_rejected(final):
        _persist_rejected_queue_repair_candidate(
            checkpoint_dir, request, base=candidate, base_qa=qa_record, original_review=review,
            rejected=repaired, rejected_qa=repaired_qa, final_review=final,
            input_candidate_binding_sha256=_candidate_binding(candidate),
        )
        outcome.working = candidate; outcome.qa = qa_record; outcome.review = review; outcome.second_review = None
        outcome.repair_applied = False; outcome.recovered_from = "resolution_queue_rejected_repair"
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
        return outcome
    outcome.working = repaired; outcome.qa = repaired_qa; outcome.review = review; outcome.second_review = final
    outcome.repair_applied = True; outcome.repair_policy_id = "RESOLUTION_QUEUE_AUTO_RESOLUTION"
    outcome.final_status = "ACCEPT" if final.verdict != "HUMAN_REQUIRED" and BLOCKING_SEMANTIC not in severities else "HUMAN_REQUIRED"
    outcome.human_required = outcome.final_status != "ACCEPT"
    if load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id) is None:
        persist_stage_once(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id, {
            "artifact_type": "PRODUCTION_RESOLUTION_QUEUE_AUTO_RESOLUTION", "schema_version": 1,
            "mode": mode, "working": repaired.model_dump(), "qa": repaired_qa,
            "review": review.model_dump(), "second_review": final.model_dump(),
            "final_status": outcome.final_status, "semantic_terminal": semantic_terminal_of_outcome(outcome),
            "human_required": outcome.human_required, "repair_applied": True,
            "repair_policy_id": outcome.repair_policy_id, "translation_api_calls": 0,
            "reviewer_api_calls": outcome.reviewer_api_calls, "repair_api_calls": outcome.repair_api_calls,
        })
    return outcome


def _resolution_queue_advisory_disposition(resolution: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve only a durable non-semantic reviewer false positive without a new call."""
    if not isinstance(resolution.get("working"), Mapping) or not isinstance(resolution.get("qa"), Mapping):
        return None
    review_payload = resolution.get("second_review")
    if not isinstance(review_payload, Mapping):
        return None
    final = ReviewResult.model_validate(review_payload)
    codes = {issue.code for issue in final.issues}
    severities = review_issue_severities(codes)
    mode = str(resolution.get("mode") or "")
    canonical_name_false_positive = mode == "CLOSED_WORLD_CANONICAL_NAME" and codes == {"UNTRANSLATED_CONTENT"}
    advisory_only = BLOCKING_SEMANTIC not in severities
    if not canonical_name_false_positive and not advisory_only:
        return None
    return {
        "artifact_type": "PRODUCTION_RESOLUTION_QUEUE_AUTO_DISPOSITION", "schema_version": 1,
        "mode": mode,
        "disposition": "CLOSED_WORLD_CANONICAL_NAME_AUTHORITY" if canonical_name_false_positive else "ADVISORY_REVIEWER_ISSUE",
        "working": resolution["working"], "qa": resolution["qa"],
        "review": resolution.get("review"), "second_review": review_payload,
        "final_status": "ACCEPT", "semantic_terminal": "MODEL_ACCEPTED", "human_required": False,
        "repair_applied": bool(resolution.get("repair_applied")),
        "repair_policy_id": resolution.get("repair_policy_id"),
        "translation_api_calls": 0, "reviewer_api_calls": 0, "repair_api_calls": 0,
        "review_issue_severities": sorted(severities),
    }


def _queue_repair_candidate_rejected(final: ReviewResult) -> bool:
    """A final structural/content diagnosis rejects the repair, never its input."""
    return bool({str(issue.code or "").strip().lower() for issue in final.issues} & _REJECTED_QUEUE_REPAIR_CODES)


def _persist_rejected_queue_repair_candidate(
    checkpoint_dir: Path,
    request,
    *,
    base: TranslationResult,
    base_qa: Mapping[str, Any],
    original_review: ReviewResult,
    rejected: TranslationResult,
    rejected_qa: Mapping[str, Any],
    final_review: ReviewResult,
    input_candidate_binding_sha256: str,
) -> None:
    """Keep invalid repair evidence while restoring the bound last-known-good input."""
    if input_candidate_binding_sha256 != _candidate_binding(base):
        raise CandidateAuthorityError("REJECTED_QUEUE_REPAIR_PARENT_MISMATCH")
    persist_stage_once(
        checkpoint_dir,
        "resolution_queue_rejected_repair_candidates",
        request.display_id,
        {
            "artifact_type": "PRODUCTION_RESOLUTION_QUEUE_REJECTED_REPAIR_CANDIDATE",
            "schema_version": 1,
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "input_candidate_binding_sha256": input_candidate_binding_sha256,
            "candidate_disposition": "LAST_KNOWN_GOOD_AFTER_REJECTED_REPAIR",
            "working": base.model_dump(),
            "qa": dict(base_qa),
            "review": original_review.model_dump(),
            "second_review": None,
            "final_status": "HUMAN_REQUIRED",
            "semantic_terminal": "HUMAN_REQUIRED",
            "human_required": True,
            "repair_applied": False,
            "repair_policy_id": "RESOLUTION_QUEUE_AUTO_RESOLUTION",
            "rejected_candidate_binding_sha256": _candidate_binding(rejected),
            "rejected_candidate": rejected.model_dump(),
            "rejected_candidate_qa": dict(rejected_qa),
            "rejected_candidate_final_review": final_review.model_dump(),
            "translation_api_calls": 0,
            "reviewer_api_calls": 0,
            "repair_api_calls": 0,
        },
    )


def _review_scope_contract_error(review: ReviewResult) -> ProviderContractGap | None:
    """Reject a review whose prose demands edits outside its structured scope."""
    if review.verdict != "REPAIR":
        return None
    structured = {index for issue in review.issues for index in issue.affected_segment_indices}
    mentioned: set[int] = set()
    for issue in review.issues:
        mentioned.update(int(value) for value in re.findall(r"\bsegments?\s*(?:index\s*)?(\d+)\b", issue.repair_instruction or "", flags=re.I))
    outside = sorted(mentioned - structured)
    if outside:
        return ProviderContractGap(
            "REVIEWER_CONTRACT_GAP_SCOPE_INCONSISTENT",
            details={"invalid_indices": outside, "structured_indices": sorted(structured)},
        )
    return None


def _run_semantic_rescue(*, checkpoint_dir: Path, request, base: TranslationResult, active: WorkerProviders, view, known_unit_ids, qa, bible, semantic_context, group, prior: Mapping[str, Any], rebuild_group: Callable[[TranslationResult], Any] | None = None, allow_verified_d050_marker_repair: bool = False, stage_prefix: str = "semantic_rescue", corrective_diagnosis: str | None = None, required_repair_scope: tuple[int, ...] | None = None, authority_raw: TranslationResult | None = None) -> UnitOutcome:
    """One create-once production rescue with separate workspace/authority identity.

    ``base`` is the candidate the repair worker edits.  A corrective episode
    may therefore use a non-authoritative prepared draft as its base.  In
    contrast, ``outcome.raw`` remains the immutable authority root consumed by
    Candidate Authority and canonical production disposition.
    """
    eligibility_stage = f"{stage_prefix}_eligibility"
    repair_stage = f"{stage_prefix}_repairs"
    completeness_stage = f"{stage_prefix}_completeness"
    final_request_stage = f"{stage_prefix}_final_review_requests"
    final_stage = f"{stage_prefix}_final_reviews"
    final_failure_stage = f"{stage_prefix}_final_review_failures"
    if load_stage(checkpoint_dir, eligibility_stage, request.display_id) is None:
        persist_stage_once(checkpoint_dir, eligibility_stage, request.display_id, {
            "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_ELIGIBILITY", "schema_version": 1,
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            "candidate_binding_sha256": sha256(base.model_dump_json().encode("utf-8")).hexdigest(),
            "semantic_group_hash": None if group is None else group.group_hash,
            "eligible": True, "translation_api_calls": 0,
        })
    provider_request = enrich_request(request, translation_bible=bible)
    qa_record = qa(view, request, base, known_unit_ids)
    outcome = UnitOutcome(
        request=request,
        decision=route_semantic_unit(request),
        raw=authority_raw or base,
        working=base,
        qa=qa_record,
        recovered_from="d050_exact_marker_repair" if allow_verified_d050_marker_repair else "raw",
    )
    if not qa_record.get("passed") and not (
        allow_verified_d050_marker_repair and _only_missing_verified_d050_markers(request, base, qa_record)
    ):
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    mode = "REVIEW_ONLY" if request.display_id in SEMANTIC_RESCUE_B_REVIEW_ONLY else "SEMANTIC_REPAIR"
    review = None
    if mode == "REVIEW_ONLY":
        saved = load_stage(checkpoint_dir, "semantic_rescue_reviews", request.display_id)
        if saved is None:
            try:
                result = review_with_contract_boundary(active.reviewer, provider_request, base, deterministic_qa_summary={"passed": True}, semantic_context=semantic_context)
            except Exception as error:  # Known provider/contract failures are an explicit terminal, never UNKNOWN.
                outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
            review = result.review
            persist_stage_once(checkpoint_dir, "semantic_rescue_reviews", request.display_id, {"artifact_type":"PRODUCTION_SEMANTIC_RESCUE_REVIEW","schema_version":1,"review":review.model_dump(),"usage":dict(result.candidate.usage),"request_binding_sha256":provider_request.request_binding_sha256})
        else: review = ReviewResult.model_validate(saved["review"])
        outcome.reviewer_api_calls += 1 if saved is None else 0
        if review.verdict == "ACCEPT":
            outcome.review = review; outcome.final_status = "ACCEPT"; outcome.semantic_terminal = "MODEL_ACCEPTED"; return outcome
    else:
        raw_review = prior.get("second_review") or prior.get("review")
        if not isinstance(raw_review, Mapping):
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
        review = ReviewResult.model_validate(raw_review)
    if review is None or review.verdict != "REPAIR":
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    gate = decide_approved_repair_contract(provider_request, base, qa_codes=(), review=review, allow_semantic_narrow=True)
    if gate.action != "REPAIR" or gate.contract is None:
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    # A rescue is a repair, not a new translation. Preserve all explicit
    # source clauses already represented by the complete RAW candidate.
    contract = gate.contract.model_copy(update={
        "repair_instruction": (
            f"{gate.contract.repair_instruction}\n\n"
            "Preserve a Chinese rendering for every explicit source sentence "
            "clause already covered by the input candidate. Do not delete, "
            "condense away, or replace later source clauses while repairing "
            "the approved issue."
            + (f"\n\nCorrective diagnosis from the rejected prior proposal (do not reuse its text): {corrective_diagnosis}. Start from the original last-known-good candidate only."
               if corrective_diagnosis else "")
            + (
                f"\n\nAuthoritative semantic-group context (read-only): group={group.semantic_review_group_id}; "
                f"full source={group.full_source_span}; full current candidate={group.full_candidate_span}. "
                "Modify only this unit's permitted segment indices; do not alter another group member."
                if group is not None and len(group.member_work_order_ids) > 1 else ""
            )
        ),
    })
    if required_repair_scope is not None and tuple(contract.permitted_segment_indices) != required_repair_scope:
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        outcome.failure_code = "CURRENT_REREVIEW_REPAIR_SCOPE_MISMATCH"
        return outcome
    saved_repair = load_stage(checkpoint_dir, repair_stage, request.display_id)
    if saved_repair is None:
        try:
            result = repair_with_contract_boundary(active.repair, provider_request, base, contract)
            repaired = result.repaired
        except Exception as error:  # Retain HUMAN_REQUIRED instead of escaping into an ambiguous recovery state.
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
        usage = dict(result.candidate.usage)
        persist_stage_once(checkpoint_dir, repair_stage, request.display_id, {"artifact_type":"PRODUCTION_SEMANTIC_RESCUE_REPAIR","schema_version":2,"parent_candidate_sha256":_candidate_binding(base),"authority_parent_verified":True,"contract":contract.model_dump(),"working":repaired.model_dump(),"usage":usage,"request_binding_sha256":provider_request.request_binding_sha256,"raw_repair_response":result.candidate.response_text,"raw_repair_response_sha256":sha256(result.candidate.response_text.encode("utf-8")).hexdigest()})
        outcome.repair_api_calls += 1
    else:
        try:
            require_parent_is_current(
                artifact="semantic_rescue_repair",
                parent_candidate_sha256=str(saved_repair.get("parent_candidate_sha256") or "") or None,
                current_candidate_sha256=_candidate_binding(base),
            )
        except CandidateAuthorityError as error:
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = str(error); return outcome
        repaired = TranslationResult.model_validate(saved_repair["working"])
    completeness = _repair_clause_coverage(request, base, repaired)
    saved_completeness = load_stage(checkpoint_dir, completeness_stage, request.display_id)
    if saved_completeness is None:
        persist_stage_once(checkpoint_dir, completeness_stage, request.display_id, completeness)
    elif (
        saved_completeness.get("candidate_binding_sha256") != completeness["candidate_binding_sha256"]
        or bool(saved_completeness.get("passed")) != bool(completeness["passed"])
    ):
        outcome.working = repaired; outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
        outcome.failure_code = "REPAIR_COMPLETENESS_EVIDENCE_BINDING_MISMATCH"; return outcome
    if not completeness["passed"]:
        outcome.working = repaired; outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
        outcome.failure_code = "REPAIR_COMPLETENESS_CLAUSE_COVERAGE_FAILED"; return outcome
    try:
        repaired, repaired_qa, final_context, final_binding = finalize_recovery_candidate(
            request=request, base=base, candidate=repaired,
            parent_candidate_sha256=_candidate_binding(base), view=view,
            known_unit_ids=known_unit_ids, qa=qa, group=group, rebuild_group=rebuild_group,
            allow_index_owned_normalization=True,
        )
    except (CandidateAuthorityError, RuntimeError) as error:
        outcome.working = repaired; outcome.qa = qa(view, request, repaired, known_unit_ids)
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = str(error); return outcome
    request_evidence = load_stage(checkpoint_dir, final_request_stage, request.display_id)
    if request_evidence is None:
        persist_stage_once(checkpoint_dir, final_request_stage, request.display_id, {
            "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_FINAL_REVIEW_REQUEST", "schema_version": 1,
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            **final_binding, "translation_api_calls": 0, "repair_api_calls": 0,
        })
    elif not _stored_final_binding_matches(request_evidence, final_binding):
        outcome.working = repaired; outcome.qa = repaired_qa; outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = "FINAL_REVIEW_CANDIDATE_BINDING_MISMATCH"; return outcome
    saved_final = load_stage(checkpoint_dir, final_stage, request.display_id)
    if saved_final is None:
        # A request evidence record with no result means a prior final-review
        # execution did not become a valid semantic result. It authorizes one
        # same-head/context protocol retry, not another repair and never a
        # semantic human escalation.
        prior_request_without_result = request_evidence is not None
        prior_failure = load_stage(checkpoint_dir, final_failure_stage, request.display_id)
        if prior_failure is not None:
            # The immutable first failure is diagnostic evidence, not the
            # terminal result of this episode.  A request without a valid
            # response has exactly one existing protocol retry on the same
            # repair candidate/context.  Do not repeat Repair merely because
            # the final reviewer was contract-invalid.
            if (
                prior_failure.get("candidate_binding_sha256") != _candidate_binding(repaired)
                or prior_failure.get("request_binding_sha256") != provider_request.request_binding_sha256
                or prior_failure.get("semantic_group_hash") != (None if group is None else group.group_hash)
                or int(prior_failure.get("attempt", 0)) != 0
            ):
                outcome.final_status = "HUMAN_REQUIRED"
                outcome.human_required = True
                outcome.failure_family = "REVIEWER_EXECUTION_FAILURE"
                outcome.failure_code = REVIEWER_CONTRACT_INVALID_AFTER_RETRY
                return outcome
        try:
            result = review_with_contract_boundary(
                active.reviewer, provider_request, repaired,
                deterministic_qa_summary={"passed": True}, semantic_context=final_context,
                protocol_retry=prior_request_without_result,
            )
        except Exception as error:
            if prior_failure is None:
                persist_stage_once(checkpoint_dir, final_failure_stage, request.display_id, {
                    "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_FINAL_REVIEW_EXECUTION_FAILURE",
                    "schema_version": 1, "display_id": request.display_id, "unit_uid": request.unit_uid,
                    "candidate_binding_sha256": _candidate_binding(repaired),
                    "request_binding_sha256": provider_request.request_binding_sha256,
                    "semantic_group_hash": None if group is None else group.group_hash,
                    "attempt": 0,
                    "failure_code": getattr(error, "code", type(error).__name__),
                })
            from booktrans.providers.failures import classify_provider_exception
            classified = classify_provider_exception(error)
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
            outcome.failure_family = "REVIEWER_EXECUTION_FAILURE"
            outcome.failure_code = classified.code
            return outcome
        final = result.review
        persist_stage_once(checkpoint_dir, final_stage, request.display_id, {"artifact_type":"PRODUCTION_SEMANTIC_RESCUE_FINAL_REVIEW","schema_version":2,"review":final.model_dump(),"usage":dict(result.candidate.usage),"request_binding_sha256":provider_request.request_binding_sha256, **final_binding})
        outcome.reviewer_api_calls += 1
    else:
        final = ReviewResult.model_validate(saved_final["review"])
        # Older runs could persist a syntactically non-empty but null-like
        # REPAIR instruction (for example the literal string ``"None"``).
        # Preserve that artifact, record it as contract-invalid, and spend one
        # final-review-only protocol retry for the same repaired candidate.
        if stage_prefix == "d050_exact_marker_recovery":
            try:
                _validate_review_result(provider_request, final)
            except ProviderContractGap as error:
                _persist_d050_final_review_contract_failure(
                    checkpoint_dir=checkpoint_dir, request=request, candidate=repaired,
                    request_binding_sha256=provider_request.request_binding_sha256,
                    semantic_group_hash=None if group is None else group.group_hash,
                    attempt=1, error=error,
                )
                saved_retry = load_stage(checkpoint_dir, D050_EXACT_MARKER_FINAL_REVIEW_RETRY_STAGE, request.display_id)
                if saved_retry is None:
                    try:
                        result = review_with_contract_boundary(
                            active.reviewer, provider_request, repaired,
                            deterministic_qa_summary={"passed": True}, semantic_context=final_context,
                            protocol_retry=True,
                        )
                        final = result.review
                    except ProviderContractGap as retry_error:
                        _persist_d050_final_review_contract_failure(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=repaired,
                            request_binding_sha256=provider_request.request_binding_sha256,
                            semantic_group_hash=None if group is None else group.group_hash,
                            attempt=2, error=retry_error,
                        )
                        outcome.working = repaired; outcome.qa = repaired_qa
                        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                        outcome.failure_family = "REVIEWER_EXECUTION_FAILURE"
                        outcome.failure_code = REVIEWER_CONTRACT_INVALID_AFTER_RETRY
                        return outcome
                    persist_stage_once(checkpoint_dir, D050_EXACT_MARKER_FINAL_REVIEW_RETRY_STAGE, request.display_id, {
                        "artifact_type": "PRODUCTION_D050_EXACT_MARKER_FINAL_REVIEW_RETRY",
                        "schema_version": 1, "attempt": 2,
                        "display_id": request.display_id, "unit_uid": request.unit_uid,
                        **final_binding, "review": final.model_dump(),
                        "request_binding_sha256": provider_request.request_binding_sha256,
                        "translation_api_calls": 0, "repair_api_calls": 0,
                        "reviewer_api_calls": 1,
                    })
                    outcome.reviewer_api_calls += 1
                else:
                    try:
                        final = ReviewResult.model_validate(saved_retry["review"])
                        _validate_review_result(provider_request, final)
                    except (ProviderContractGap, KeyError, TypeError, ValueError) as retry_error:
                        error = retry_error if isinstance(retry_error, ProviderContractGap) else ProviderContractGap("REVIEWER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID")
                        _persist_d050_final_review_contract_failure(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=repaired,
                            request_binding_sha256=provider_request.request_binding_sha256,
                            semantic_group_hash=None if group is None else group.group_hash,
                            attempt=2, error=error,
                        )
                        outcome.working = repaired; outcome.qa = repaired_qa
                        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                        outcome.failure_family = "REVIEWER_EXECUTION_FAILURE"
                        outcome.failure_code = REVIEWER_CONTRACT_INVALID_AFTER_RETRY
                        return outcome
        if stage_prefix.startswith("semantic_rescue_current_head_corrective"):
            scope_error = _review_scope_contract_error(final)
            if scope_error is not None:
                contract_failure_stage = f"{stage_prefix}_final_review_contract_failures"
                retry_stage = f"{stage_prefix}_final_review_retries"
                failure = load_stage(checkpoint_dir, contract_failure_stage, request.display_id)
                if failure is None:
                    persist_stage_once(checkpoint_dir, contract_failure_stage, request.display_id, {
                        "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_FAILURE", "schema_version": 1,
                        "display_id": request.display_id, "unit_uid": request.unit_uid,
                        "candidate_binding_sha256": _candidate_binding(repaired),
                        "request_binding_sha256": provider_request.request_binding_sha256,
                        "contract_error_code": scope_error.code,
                        "structured_indices": scope_error.details.get("structured_indices"),
                        "out_of_scope_indices": scope_error.details.get("invalid_indices"),
                    })
                saved_retry = load_stage(checkpoint_dir, retry_stage, request.display_id)
                if saved_retry is None:
                    try:
                        retry_result = review_with_contract_boundary(
                            active.reviewer, provider_request, repaired,
                            deterministic_qa_summary={"passed": True}, semantic_context=final_context,
                            protocol_retry=True,
                        )
                        retry_final = retry_result.review
                        retry_error = _review_scope_contract_error(retry_final)
                        if retry_error is not None:
                            raise retry_error
                    except ProviderContractGap as retry_error:
                        outcome.working = repaired; outcome.qa = repaired_qa
                        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                        outcome.failure_family = "REVIEWER_EXECUTION_FAILURE"
                        outcome.failure_code = REVIEWER_CONTRACT_INVALID_AFTER_RETRY
                        return outcome
                    persist_stage_once(checkpoint_dir, retry_stage, request.display_id, {
                        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_FINAL_REVIEW_RETRY", "schema_version": 1,
                        "display_id": request.display_id, "unit_uid": request.unit_uid,
                        **final_binding, "review": retry_final.model_dump(),
                        "request_binding_sha256": provider_request.request_binding_sha256,
                        "translation_api_calls": 0, "repair_api_calls": 0, "reviewer_api_calls": 1,
                    })
                    outcome.reviewer_api_calls += 1
                    final = retry_final
                else:
                    try:
                        final = ReviewResult.model_validate(saved_retry["review"])
                        retry_error = _review_scope_contract_error(final)
                        if retry_error is not None:
                            raise retry_error
                    except (ProviderContractGap, KeyError, TypeError, ValueError):
                        outcome.working = repaired; outcome.qa = repaired_qa
                        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                        outcome.failure_family = "REVIEWER_EXECUTION_FAILURE"
                        outcome.failure_code = REVIEWER_CONTRACT_INVALID_AFTER_RETRY
                        return outcome
    outcome.review=review; outcome.second_review=final; outcome.working=repaired; outcome.qa=repaired_qa; outcome.repair_applied=True; outcome.repair_policy_id=contract.policy_id
    final_severities = review_issue_severities(issue.code for issue in final.issues)
    outcome.final_status = "ACCEPT" if (
        final.verdict != "HUMAN_REQUIRED" and BLOCKING_SEMANTIC not in final_severities
    ) else "HUMAN_REQUIRED"
    outcome.human_required = outcome.final_status != "ACCEPT"
    return outcome


def _run_eligible_current_head_semantic_rescue(
    *, checkpoint_dir: Path, request, raw: TranslationResult, prior: Mapping[str, Any], active: WorkerProviders,
    loaded: Mapping[str, Any], run: Path, checkpoint: str, existing_raw, qa, bible,
) -> UnitOutcome | None:
    """Spend the existing one-shot rescue before a material REPAIR can stop.

    The helper is deliberately a thin admission gate over ``_run_semantic_rescue``:
    it does not classify severity or create a new repair mechanism.
    """
    if load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is not None:
        return None
    # Protocol/execution failures have their own bounded boundary and debt
    # policy. They are not evidence that a semantic rescue is warranted.
    if getattr(prior, "get", lambda _key, _default=None: None)("failure_family") in {
        "PROVIDER_CONTRACT_FAILURE", "REVIEWER_EXECUTION_FAILURE", "REPAIR_EXECUTION_FAILURE",
    } or str(getattr(prior, "get", lambda _key, _default=None: None)("failure_code") or "").startswith("REPAIR_CONTRACT_GAP_"):
        return None
    review_payload = prior.get("second_review") or prior.get("review")
    try:
        review = ReviewResult.model_validate(review_payload) if isinstance(review_payload, Mapping) else None
    except Exception:
        return None
    current_qa = qa(loaded["views"][request.display_id], request, raw, loaded["known_unit_ids"])
    group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
    if review is None or not current_qa.get("passed") or _semantic_auto_recovery_contract(request, raw, review, bible=bible, group=group) is None:
        return None

    def rebuild_group(repaired: TranslationResult):
        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
        overrides[request.display_id] = repaired
        overrides[request.unit_uid] = repaired
        return _production_review_group(
            request, loaded=loaded, run=run, checkpoint=checkpoint,
            existing_raw=existing_raw, candidate_overrides=overrides,
        )

    outcome = _run_semantic_rescue(
        checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
        bible=bible, semantic_context=None if group is None else group.as_reviewer_context_payload(),
        group=group, prior=prior, rebuild_group=rebuild_group,
    )
    persist_stage_once(
        checkpoint_dir, "semantic_rescue", request.display_id,
        _rescue_record(outcome, mode="SEVERE_SEMANTIC_AUTO_RECOVERY", group=group, raw=raw),
    )
    return outcome


def build_current_review_group(
    request,
    *,
    loaded: Mapping[str, Any],
    run: Path,
    checkpoint: str,
    existing_raw,
    candidate_overrides: Mapping[str, TranslationResult] | None = None,
):
    """Build the one current-head-bound group used by every production review.

    ``candidate_overrides`` is deliberately candidate objects rather than text:
    both display-id and unit-uid keys are projected here, at the authority
    boundary, before the group hash/context/member span is constructed.
    """
    if not loaded.get("blocks"):
        return None
    # Consume the single effective-source projection before deriving current
    # group source span/hash.  The immutable IR remains the context topology.
    from booktrans.production_worker.effective_source import resolve_effective_source
    checkpoint_dir = production_checkpoint_path(run, str(loaded.get("batch_label") or PRODUCTION_BATCH), checkpoint)
    effective = resolve_effective_source(checkpoint_dir=checkpoint_dir, request=request)
    request = effective.request
    batch_label = str(loaded.get("batch_label") or PRODUCTION_BATCH)
    registry = load_checkpoint_d050_registry(run, checkpoint, batch_label=batch_label)
    requests = list(loaded["requests"])
    order = list(loaded["order"])
    candidates = candidate_text_map(requests, existing_raw)
    for display_id, candidate in (candidate_overrides or {}).items():
        text = "".join(segment.text for segment in candidate.target_segments)
        candidates[display_id] = text
        candidates[candidate.unit_uid] = text
    context_units = context_units_for_order(order)
    context_units = [replace(item, source_text=request.source_text) if item.display_id == request.display_id else item for item in context_units]
    return build_unit_group(
        request,
        context_units,
        registry,
        candidate_texts=candidates,
        segment_counts=segment_count_map(requests),
        unsealed_work_order_ids=frozenset(str(item) for item in order),
        sealed_work_order_ids=production_sealed_work_order_ids(
            run, production_checkpoint_path(run, batch_label, checkpoint).parent.name
        ),
    )


# Compatibility entry point for release-only B023 tooling.  There is one
# implementation above; release tooling and the worker therefore cannot drift
# in group membership or candidate-text projection.
def _production_review_group(request, **kwargs):
    return build_current_review_group(request, **kwargs)


def _rescue_working_overrides(
    checkpoint_dir: Path, loaded: Mapping[str, Any], existing_raw: Mapping[str, TranslationResult]
) -> dict[str, TranslationResult]:
    """Rebuild groups from the same single authoritative candidate heads."""
    overrides: dict[str, TranslationResult] = {}
    for item in loaded["requests"]:
        raw = existing_raw.get(item.unit_uid)
        if raw is None:
            continue
        resolved = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, item.display_id, raw)
        overrides[item.display_id] = resolved.candidate
        overrides[item.unit_uid] = resolved.candidate
    return overrides


def _rescue_current_working(checkpoint_dir: Path, display_id: str, rescue: Mapping[str, Any] | None) -> TranslationResult | None:
    repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", display_id)
    working = repair.get("working") if repair and isinstance(repair.get("working"), dict) else (rescue.get("working") if rescue else None)
    return TranslationResult.model_validate(working) if isinstance(working, dict) else None


def _candidate_binding(candidate: TranslationResult) -> str:
    return _candidate_sha(candidate)


def _prepared_state_identity(outcomes) -> tuple[str, list[TranslationResult]]:
    prepared = [item.working or item.raw for item in outcomes]
    if any(item is None for item in prepared):
        raise CandidateAuthorityError("PREPARED_OUTPUT_CANDIDATE_MISSING")
    records = [item for item in prepared if item is not None]
    identity = [{"logical_work_order_id": outcome.request.display_id, "unit_uid": outcome.request.unit_uid,
                 "candidate_binding_sha256": _candidate_binding(candidate), "candidate": candidate.model_dump(mode="json")}
                for outcome, candidate in zip(outcomes, records, strict=True)]
    return _canonical_sha256(identity), records


def _select_prepared_output_generation(
    checkpoint_dir: Path, outcomes, explicit: Mapping[str, Any] | None,
    *, finalization_input: Mapping[str, Any] | None = None,
):
    if explicit is not None:
        return dict(explicit), None
    state_id, prepared = _prepared_state_identity(outcomes)
    root = checkpoint_dir / "translation_repaired_response.jsonl"
    payload = "".join(f"{item.model_dump_json()}\n" for item in prepared).encode("utf-8")
    # CURRENT finalization is generation-scoped even when its repaired output
    # happens to equal a legacy root snapshot: root finalization JSON may be
    # immutable historical evidence from a different current projection.
    if finalization_input is None and (not root.exists() or root.read_bytes() == payload):
        return None, prepared
    input_fingerprint = None if finalization_input is None else _canonical_sha256(finalization_input)
    generation_id = input_fingerprint or state_id
    relative = f"seal_generations/prepared_{generation_id}"
    generation = {"generation": 1, "path": relative, "prepared_state_id": state_id}
    if input_fingerprint is not None:
        generation["finalization_input_fingerprint"] = input_fingerprint
    metadata = checkpoint_dir / relative / "prepared_state.json"
    expected = {"artifact_type": "PRODUCTION_PREPARED_OUTPUT_STATE", "schema_version": 1, "prepared_state_id": state_id, "unit_count": len(prepared)}
    if input_fingerprint is not None:
        expected["finalization_input_fingerprint"] = input_fingerprint
        expected["finalization_input"] = dict(finalization_input)
    write_json_once_or_validate(metadata, expected)
    return generation, prepared


def _prepared_generation_ready_for_commit(
    checkpoint_dir: Path, generation: Mapping[str, Any], manifest: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the manifest projection only for a complete, valid precommit generation."""
    relative = generation.get("path")
    if not isinstance(relative, str):
        raise CandidateAuthorityError("PREPARED_OUTPUT_GENERATION_INVALID")
    seal_dir = (checkpoint_dir / relative).resolve()
    if checkpoint_dir.resolve() not in seal_dir.parents:
        raise CandidateAuthorityError("PREPARED_OUTPUT_GENERATION_PATH_ESCAPE")
    required = (
        "prepared_state.json",
        "translation_repaired_response.jsonl",
        "translation_normalized_qa_report.json",
        "semantic_review_report.json",
        "translation_final_qa_report.json",
        "checkpoint_evidence.json",
    )
    if not all((seal_dir / name).is_file() for name in required):
        return None
    # The existing manifest projection is the closure validator: it checks
    # membership, all QA/review counts, and evidence/final-QA bindings using
    # the selected seal directory.  A complete but invalid generation fails
    # closed rather than replaying any create-once producer.
    return sealed_checkpoint_manifest_projection(
        dict(manifest), checkpoint_dir, seal_generation=dict(generation),
    )


def _accepted_candidate_mutation_records(checkpoint_dir: Path, display_id: str) -> list[dict[str, Any]]:
    """Read legacy and append-only descendant records in one authority lineage.

    The original per-unit file remains immutable.  A later bounded corrective
    mutation therefore lives below that same existing stage rather than
    replacing the historical mutation or introducing another authority.
    """
    records: list[dict[str, Any]] = []
    legacy = load_stage(checkpoint_dir, "accepted_candidate_mutations", display_id)
    if isinstance(legacy, Mapping):
        records.append(dict(legacy))
    descendants = checkpoint_dir / "accepted_candidate_mutations" / display_id
    if descendants.is_dir():
        pending = [load_json(path) for path in sorted(descendants.glob("*.json"), key=lambda item: item.name.casefold())]
        known = {str(record.get("candidate_binding_sha256")) for record in records}
        while pending:
            next_record = next((record for record in pending if record.get("parent_candidate_sha256") in known), None)
            if next_record is None:
                # Preserve the malformed/stale evidence for the authority
                # resolver to reject; never silently choose a branch.
                records.extend(pending)
                break
            records.append(next_record)
            pending.remove(next_record)
            known.add(str(next_record.get("candidate_binding_sha256")))
    return records


def _human_approved_candidate_records(checkpoint_dir: Path, display_id: str) -> list[dict[str, Any]]:
    """Read the immutable singleton and versioned Human descendants in lineage order."""
    records: list[dict[str, Any]] = []
    legacy = load_stage(checkpoint_dir, "human_approved_candidates", display_id)
    if isinstance(legacy, Mapping):
        records.append(dict(legacy))
    descendants = checkpoint_dir / "human_approved_candidates" / display_id
    if descendants.is_dir():
        pending = [load_json(path) for path in sorted(descendants.glob("*.json"), key=lambda item: item.name.casefold())]
        known = {str(record.get("candidate_binding_sha256")) for record in records}
        while pending:
            next_record = next((record for record in pending if record.get("parent_candidate_sha256") in known), None)
            if next_record is None:
                # Do not choose a stale/forked Human candidate by filesystem
                # order.  Leave it for Candidate Authority to reject.
                records.extend(pending)
                break
            records.append(next_record)
            pending.remove(next_record)
            known.add(str(next_record.get("candidate_binding_sha256")))
    return records


def _accepted_mutation_path(checkpoint_dir: Path, display_id: str, candidate_sha256: str, *, descendant: bool) -> Path:
    if not descendant:
        return checkpoint_dir / "accepted_candidate_mutations" / f"{display_id}.json"
    return checkpoint_dir / "accepted_candidate_mutations" / display_id / f"{candidate_sha256}.json"


def _current_numeric_ownership_mutation_is_effective(
    *, checkpoint_dir: Path, request, raw: TranslationResult, current: TranslationResult, raw_qa: Mapping[str, Any], current_qa: Mapping[str, Any],
) -> bool:
    """Recognize one pre-existing deterministic correction, never a reviewer result."""
    raw_codes = set(qa_codes(raw_qa))
    if (
        "NUMERIC_ANCHOR_DUPLICATION" not in raw_codes
        or not raw_codes <= {"NUMBER_PRESERVATION", "NUMERIC_ANCHOR_DUPLICATION"}
        or not current_qa.get("passed")
    ):
        return False
    records = _accepted_candidate_mutation_records(checkpoint_dir, request.display_id)
    matching = [record for record in records if (
        record.get("mutation_kind") == "NUMERIC_REPRESENTATION"
        and record.get("parent_candidate_sha256") == _candidate_binding(raw)
        and record.get("candidate_binding_sha256") == _candidate_binding(current)
        and record.get("display_id") == request.display_id
        and record.get("unit_uid") == request.unit_uid
        and record.get("accepted") is True
    )]
    return len(matching) == 1


def _index_locator_materialization_eligible(group, current_qa: Mapping[str, Any]) -> bool:
    """Limit locator restoration to the existing Index ownership contract."""
    return bool(
        group is not None
        and group.continuation_relation == "INDEX_ENTRY"
        and group.continuation_review_rules.get("numeric_locator_owner") == "HARNESS_DETERMINISTIC"
        and set(qa_codes(current_qa)) == {"NUMBER_PRESERVATION"}
    )


def _bound_index_locator_semantic_base(
    *, checkpoint_dir: Path, request, current: TranslationResult, qa, view, known_unit_ids
) -> TranslationResult | None:
    """Reuse one exact-current-head semantic repair whose only remaining defect is its locator.

    The repair is already immutable, source/request-bound, and limited by its
    existing contract.  This function never promotes it by itself: the caller
    must materialize the source-owned locator and pass full deterministic QA.
    """
    for stage in ("semantic_rescue_corrective_repairs", "semantic_rescue_repairs"):
        record = load_stage(checkpoint_dir, stage, request.display_id)
        if not isinstance(record, Mapping) or record.get("authority_parent_verified") is not True:
            continue
        if record.get("parent_candidate_sha256") != _candidate_binding(current):
            continue
        working = record.get("working")
        contract = record.get("contract")
        if not isinstance(working, Mapping) or not isinstance(contract, Mapping):
            continue
        if contract.get("unit_uid") != request.unit_uid or contract.get("policy_id") != "SEMANTIC_NARROW_SEGMENT":
            continue
        candidate = TranslationResult.model_validate(working)
        candidate_qa = qa(view, request, candidate, known_unit_ids)
        if set(qa_codes(candidate_qa)) == {"NUMBER_PRESERVATION"}:
            return candidate
    return None


def _commit_accepted_candidate_mutation(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    outcome: UnitOutcome,
    source_sha256: str,
    selection_sha256: str,
    bible: Path | None,
) -> None:
    """Commit one normal candidate mutation against the current authority head.

    ``units/*.json`` remains compatibility evidence.  Any normal NUMBER/DOI,
    semantic/group, or local-polish mutation is authoritative only after this
    create-once commit has asserted its parent.  Specialized queue/rescue and
    human writers already carry their own bound evidence and are deliberately
    excluded here.
    """
    production_safe_debt = _deferred_semantic_debt_eligible(outcome, checkpoint_dir=checkpoint_dir)
    # A deterministic NUMBER/DOI correction may be Core-clean before a
    # subsequent semantic pass has concluded.  It is still a real candidate
    # mutation and must be bound to the same authority head before any later
    # semantic repair can consume it.  This does not mark the unit accepted.
    deterministic_pending_semantic = bool(
        outcome.qa and outcome.qa.get("passed")
        and outcome.repair_policy_id in {
            "NUMERIC_REPRESENTATION", "DOI_NARROW_REPAIR",
            "NUMERIC_ANCHOR_OWNERSHIP_CORRECTION", "INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION",
            "EXACT_SOURCE_URL_RESTORATION",
        }
    )
    if outcome.working is None or not (
        _terminal_resolved(outcome) or production_safe_debt or deterministic_pending_semantic
    ):
        return
    if outcome.recovered_from in {
        "resolution_queue_auto_resolution",
        "semantic_rescue",
        "human_adjudication",
        "d050_exact_marker_repair",
    }:
        return
    head = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, request.display_id, raw)
    candidate = outcome.working
    candidate_sha = _candidate_binding(candidate)
    if candidate_sha == head.candidate_sha256:
        return
    if not bool(outcome.qa and outcome.qa.get("passed")):
        raise CandidateAuthorityError("ACCEPTED_CANDIDATE_MUTATION_QA_FAILED")
    existing_records = _accepted_candidate_mutation_records(checkpoint_dir, request.display_id)
    for existing in existing_records:
        if (
            existing.get("parent_candidate_sha256") == head.candidate_sha256
            and existing.get("candidate_binding_sha256") == candidate_sha
        ):
            return
    # A historical accepted mutation is immutable, but it cannot consume the
    # lineage forever.  Commit a new candidate as its exact-current-head
    # descendant in the same accepted-mutation authority namespace.
    descendant = bool(existing_records)
    provider_request = enrich_request(request, translation_bible=bible)
    record = {
        "artifact_type": "PRODUCTION_ACCEPTED_CANDIDATE_MUTATION",
        "schema_version": 1,
        "display_id": request.display_id,
        "unit_uid": request.unit_uid,
        "mutation_kind": outcome.repair_policy_id or "NORMAL_PRODUCTION_MUTATION",
        "parent_candidate_sha256": head.candidate_sha256,
        "candidate_binding_sha256": candidate_sha,
        "source_sha256": source_sha256,
        "selection_sha256": selection_sha256,
        "request_binding_sha256": provider_request.request_binding_sha256,
        "working": candidate.model_dump(),
        "qa": outcome.qa,
        "semantic_terminal": semantic_terminal_of_outcome(outcome),
        "production_candidate_disposition": (
            "DEFERRED_SEMANTIC_DEBT" if production_safe_debt else
            "PENDING_SEMANTIC_REVIEW" if deterministic_pending_semantic else
            "MODEL_ACCEPTED"
        ),
        "accepted": True,
        "translation_api_calls": 0,
    }
    path = _accepted_mutation_path(checkpoint_dir, request.display_id, candidate_sha, descendant=descendant)
    write_json_once(path, record)


def _assert_accepted_candidate_mutation_binding(
    *, checkpoint_dir: Path, request, raw: TranslationResult, source_sha256: str, selection_sha256: str, bible: Path | None
) -> None:
    """Validate every normal mutation record before it enters any hot path."""
    records = _accepted_candidate_mutation_records(checkpoint_dir, request.display_id)
    if not records:
        return
    expected_request = enrich_request(request, translation_bible=bible).request_binding_sha256
    for record in records:
        if (
            record.get("display_id") != request.display_id
            or record.get("unit_uid") != request.unit_uid
            or record.get("source_sha256") != source_sha256
            or record.get("selection_sha256") != selection_sha256
            or record.get("request_binding_sha256") != expected_request
            or not record.get("parent_candidate_sha256")
        ):
            raise CandidateAuthorityError("ACCEPTED_CANDIDATE_MUTATION_REQUEST_BINDING_MISMATCH")
        if record.get("mutation_kind") != "NUMERIC_REPRESENTATION":
            continue
        working = record.get("working")
        if not isinstance(working, Mapping):
            raise CandidateAuthorityError("MUTATION_MATERIALIZATION_MISMATCH")
        candidate = TranslationResult.model_validate(working)
        if not _numeric_mutation_materialized(raw, candidate):
            # A later Human terminal may legitimately repair the actual
            # current candidate.  The immutable failed mutation remains
            # history, but cannot veto that exact, bound Human correction.
            human = load_stage(checkpoint_dir, "human_approved_candidates", request.display_id)
            if not (
                isinstance(human, Mapping)
                and human.get("semantic_terminal") == "HUMAN_APPROVED"
                and human.get("parent_candidate_sha256") == _candidate_binding(candidate)
            ):
                raise CandidateAuthorityError("MUTATION_MATERIALIZATION_MISMATCH")


def _numeric_mutation_materialized(parent: TranslationResult, candidate: TranslationResult) -> bool:
    """Reject a numeric mutation that reintroduces a localized number as English.

    A numeric-representation mutation is allowed to be authority evidence only
    when its materialized target preserves the parent's already-localized
    number representation.  This is intentionally narrow: it detects an
    English number phrase newly introduced by the mutation (for example,
    ``20 亿`` becoming ``two billion``) without imposing a global terminology
    rule on source quotations or protected content.
    """
    parent_text = "\n".join(segment.text for segment in parent.target_segments).casefold()
    candidate_text = "\n".join(segment.text for segment in candidate.target_segments).casefold()
    english_scale_number = re.compile(
        r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
        r"eighty|ninety)(?:[ -](?:one|two|three|four|five|six|seven|"
        r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
        r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
        r"fifty|sixty|seventy|eighty|ninety))*\s+"
        r"(?:hundred|thousand|million|billion|trillion)\b"
    )
    return all(match.group(0) in parent_text for match in english_scale_number.finditer(candidate_text))


def _resolve_checkpoint_authoritative_candidate(
    checkpoint_dir: Path, display_id: str, raw: TranslationResult
):
    """Resolve current candidate from RAW and bound accepted mutations only."""
    mutations: list[CandidateMutation] = []
    normals = _accepted_candidate_mutation_records(checkpoint_dir, display_id)
    for ordinal, normal in enumerate(normals):
        if not isinstance(normal.get("working"), Mapping):
            raise CandidateAuthorityError("ACCEPTED_CANDIDATE_MUTATION_WORKING_MISSING")
        candidate = TranslationResult.model_validate(normal["working"])
        if normal.get("candidate_binding_sha256") != _candidate_binding(candidate):
            raise CandidateAuthorityError("ACCEPTED_CANDIDATE_MUTATION_BINDING_MISMATCH")
        mutations.append(CandidateMutation(
            artifact="accepted_candidate_mutation" if ordinal == 0 else "accepted_candidate_mutation_descendant",
            candidate=candidate,
            parent_candidate_sha256=str(normal.get("parent_candidate_sha256") or "") or None,
            accepted=normal.get("accepted") is True and bool((normal.get("qa") or {}).get("passed")),
        ))
    queue_mutation = None
    queue_repair = load_stage(checkpoint_dir, "resolution_queue_auto_repairs", display_id)
    queue_resolution = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", display_id)
    if isinstance(queue_repair, Mapping) and isinstance(queue_repair.get("working"), Mapping):
        candidate = TranslationResult.model_validate(queue_repair["working"])
        parent = str(queue_repair.get("input_candidate_binding_sha256") or "") or None
        legacy_compatible = queue_repair.get("authority_parent_verified") is not True
        legacy_unbound = bool(legacy_compatible and parent != _candidate_binding(raw) and not isinstance(queue_resolution, Mapping))
        queue_mutation = CandidateMutation(
            artifact="resolution_queue_auto_repair",
            candidate=candidate,
            parent_candidate_sha256=parent,
            accepted=bool(
                isinstance(queue_resolution, Mapping)
                and queue_resolution.get("semantic_terminal") in SEMANTIC_PASS_TERMINALS
                and isinstance(queue_resolution.get("working"), Mapping)
                and _candidate_binding(TranslationResult.model_validate(queue_resolution["working"])) == _candidate_binding(candidate)
            ),
            legacy_unbound=legacy_unbound,
            legacy_compatible=legacy_compatible,
        )
    rescue = load_stage(checkpoint_dir, "semantic_rescue", display_id)
    rescue_repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", display_id)
    rescue_eligibility = load_stage(checkpoint_dir, "semantic_rescue_eligibility", display_id)
    rescue_completeness = load_stage(checkpoint_dir, "semantic_rescue_completeness", display_id)
    if (
        isinstance(rescue, Mapping)
        and isinstance(rescue_repair, Mapping)
        and isinstance(rescue_eligibility, Mapping)
        and isinstance(rescue_completeness, Mapping)
        and isinstance(rescue_repair.get("working"), Mapping)
    ):
        candidate = TranslationResult.model_validate(rescue_repair["working"])
        final_payload = rescue.get("second_review")
        final_nonblocking = False
        if isinstance(final_payload, Mapping):
            try:
                final_review = ReviewResult.model_validate(final_payload)
                final_nonblocking = (
                    final_review.verdict != "HUMAN_REQUIRED"
                    and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in final_review.issues)
                )
            except Exception:
                final_nonblocking = False
        accepted = bool(
            (rescue.get("semantic_terminal") in SEMANTIC_PASS_TERMINALS or final_nonblocking)
            and isinstance(rescue.get("working"), Mapping)
            and _candidate_binding(TranslationResult.model_validate(rescue["working"])) == _candidate_binding(candidate)
            and rescue_completeness.get("candidate_binding_sha256") == _candidate_binding(candidate)
            and rescue_completeness.get("passed") is True
        )
        mutations.append(CandidateMutation(
            artifact="semantic_rescue_repair",
            candidate=candidate,
            parent_candidate_sha256=str(rescue_repair.get("parent_candidate_sha256") or "") or None,
            accepted=accepted,
            legacy_unbound=bool(
                rescue_repair.get("authority_parent_verified") is not True
                and not rescue_repair.get("parent_candidate_sha256")
            ),
            legacy_compatible=rescue_repair.get("authority_parent_verified") is not True,
        ))
    d050_attempt = load_stage(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, display_id)
    if d050_attempt is not None:
        if (
            d050_attempt.get("artifact_type") != "PRODUCTION_D050_EXACT_MARKER_RECOVERY_ATTEMPT"
            or d050_attempt.get("recovery_family") != D050_EXACT_MARKER_RECOVERY_FAMILY
            or d050_attempt.get("display_id") != display_id
            or d050_attempt.get("unit_uid") != raw.unit_uid
        ):
            raise CandidateAuthorityError("D050_EXACT_MARKER_RECOVERY_ATTEMPT_IDENTITY_MISMATCH")
        working = d050_attempt.get("working")
        if working is None:
            if d050_attempt.get("candidate_binding_sha256") is not None:
                raise CandidateAuthorityError("D050_EXACT_MARKER_RECOVERY_ATTEMPT_EMPTY_CANDIDATE_MISMATCH")
        elif not isinstance(working, Mapping):
            raise CandidateAuthorityError("D050_EXACT_MARKER_RECOVERY_ATTEMPT_WORKING_INVALID")
        else:
            candidate = TranslationResult.model_validate(working)
            candidate_sha = _candidate_binding(candidate)
            if d050_attempt.get("candidate_binding_sha256") != candidate_sha:
                raise CandidateAuthorityError("D050_EXACT_MARKER_RECOVERY_ATTEMPT_BINDING_MISMATCH")
            qa_record = d050_attempt.get("qa")
            qa_bound = (
                isinstance(qa_record, Mapping)
                and qa_record.get("passed") is True
                and d050_attempt.get("qa_candidate_binding_sha256") == candidate_sha
            )
            final = _d050_effective_final_review(
                checkpoint_dir=checkpoint_dir, display_id=display_id, unit_uid=raw.unit_uid,
                candidate_sha256=candidate_sha, request_binding_sha256=str(d050_attempt.get("request_binding_sha256") or ""),
            )
            # Typed attempts written before final-review artifacts were split
            # remain readable through their embedded, fully-bound review.
            if final is None and (
                d050_attempt.get("final_review_candidate_binding_sha256") == candidate_sha
                and d050_attempt.get("final_review_request_binding_sha256") == d050_attempt.get("request_binding_sha256")
            ):
                payload = d050_attempt.get("final_review")
                if isinstance(payload, Mapping):
                    try:
                        proposed = ReviewResult.model_validate(payload)
                    except (TypeError, ValueError):
                        proposed = None
                    if proposed is not None and (
                        proposed.verdict != "REPAIR"
                        or (
                            bool(proposed.issues)
                            and all(
                                isinstance(issue.repair_instruction, str)
                                and bool(issue.repair_instruction.strip())
                                and issue.repair_instruction.strip().casefold() not in {"none", "null"}
                                for issue in proposed.issues
                            )
                        )
                    ):
                        final = proposed
            final_bound = bool(final is not None and _d050_final_review_is_acceptable(final))
            mutations.append(CandidateMutation(
                artifact="d050_exact_marker_recovery_attempt",
                candidate=candidate,
                parent_candidate_sha256=str(d050_attempt.get("parent_candidate_sha256") or "") or None,
                accepted=bool(qa_bound and final_bound),
            ))
    # A queue repair may be bound to a typed D050 head.  Project the typed
    # parent first; artifact directory order is not lineage order.
    if queue_mutation is not None:
        mutations.append(queue_mutation)
    polish = load_stage(checkpoint_dir, "semantic_rescue_polish", display_id)
    polish_review = load_stage(checkpoint_dir, "semantic_rescue_polish_reviews", display_id)
    if isinstance(polish, Mapping) and isinstance(polish_review, Mapping) and isinstance(polish.get("working"), Mapping):
        candidate = TranslationResult.model_validate(polish["working"])
        review = polish_review.get("review") or {}
        severities = set(polish_review.get("review_issue_severities") or [])
        mutations.append(CandidateMutation(
            artifact="semantic_rescue_polish",
            candidate=candidate,
            parent_candidate_sha256=str(polish.get("input_candidate_binding_sha256") or "") or None,
            accepted=bool(
                polish_review.get("candidate_binding_sha256") == _candidate_binding(candidate)
                and isinstance(review, Mapping)
                and review.get("verdict") != "HUMAN_REQUIRED"
                and BLOCKING_SEMANTIC not in severities
            ),
            legacy_unbound=bool(
                polish.get("authority_parent_verified") is not True
                and not polish.get("input_candidate_binding_sha256")
            ),
            legacy_compatible=polish.get("authority_parent_verified") is not True,
        ))
    human_records = _human_approved_candidate_records(checkpoint_dir, display_id)
    for ordinal, human in enumerate(human_records):
        # A multi-member Human transaction prepares ordinary versioned
        # descendants first.  They are audit evidence only until one immutable
        # group commit names every member and exact candidate SHA.  This keeps
        # the existing Candidate Authority lineage while preventing a partial
        # group from ever becoming a visible head after a crash.
        transaction = human.get("human_group_transaction_id")
        if isinstance(transaction, str) and transaction:
            commit = load_stage(checkpoint_dir, "human_group_commits", transaction)
            members = commit.get("members") if isinstance(commit, Mapping) else None
            visible = isinstance(members, list) and any(
                isinstance(item, Mapping)
                and item.get("display_id") == display_id
                and item.get("candidate_binding_sha256") == human.get("candidate_binding_sha256")
                for item in members
            )
            if not visible:
                continue
        if not isinstance(human.get("working"), Mapping):
            raise CandidateAuthorityError("HUMAN_APPROVED_CANDIDATE_WORKING_MISSING")
        candidate = TranslationResult.model_validate(human["working"])
        if human.get("candidate_binding_sha256") != _candidate_binding(candidate):
            # Schema-v1 Human packets predate immutable candidate/CAS
            # bindings.  They remain historical advisory evidence only: do
            # not admit them as a mutation and do not let them prevent a later
            # fully bound Human descendant from becoming the current head.
            if int(human.get("schema_version") or 1) == 1 and not human.get("candidate_binding_sha256"):
                continue
            raise CandidateAuthorityError("HUMAN_APPROVED_CANDIDATE_BINDING_MISMATCH")
        parent = str(human.get("parent_candidate_sha256") or "") or None
        initial = human.get("origin") == "HUMAN_INITIAL_CANDIDATE"
        if initial and not (
            int(human.get("schema_version") or 1) >= 3
            and human.get("current_display_id") == display_id
            and human.get("semantic_terminal") == "HUMAN_APPROVED"
        ):
            raise CandidateAuthorityError("HUMAN_INITIAL_CANDIDATE_BINDING_INVALID")
        if int(human.get("schema_version") or 1) >= 2 and parent is None and not initial:
            raise CandidateAuthorityError("HUMAN_APPROVED_CANDIDATE_PARENT_MISSING")
        mutations.append(CandidateMutation(
            "human_approved_candidate" if ordinal == 0 else "human_approved_candidate_descendant",
            candidate, parent, True, human_approved=True,
        ))
    first_human = human_records[0] if human_records else None
    root_artifact = "HUMAN_INITIAL_CANDIDATE" if isinstance(first_human, Mapping) and first_human.get("origin") == "HUMAN_INITIAL_CANDIDATE" else "RAW"
    return resolve_authoritative_candidate(raw, mutations, root_artifact=root_artifact)


def _current_candidate_reviewer_accept(*, checkpoint_dir: Path, request, candidate_sha256: str, bible: Path | None) -> dict[str, Any] | None:
    """Return current-head semantic acceptance, including a bound human terminal.

    A human-approved terminal is already authoritative semantic evidence.  It
    must be consumed before historical RAW QA is allowed to re-block its exact
    current candidate; it is deliberately not sent back to a model reviewer.
    """
    expected_request = enrich_request(request, translation_bible=bible).request_binding_sha256
    for stage in (
        "semantic_rescue_rereviews",
        "semantic_rescue_final_reviews",
        "semantic_rescue_corrective_final_reviews",
        "semantic_rescue_current_head_corrective_final_reviews",
        # A protocol retry is a fresh, exact-candidate reviewer result.  It
        # must be eligible to certify the candidate just like its initial
        # current-head final review; the contract-failure artifact itself
        # remains diagnostic history only.
        "semantic_rescue_current_head_corrective_final_review_retries",
        f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews",
        f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_review_retries",
        "d050_exact_marker_recovery_final_reviews",
        D050_EXACT_MARKER_FINAL_REVIEW_RETRY_STAGE,
        "resolution_queue_auto_final_reviews",
    ):
        record = load_stage(checkpoint_dir, stage, request.display_id)
        if not isinstance(record, Mapping):
            continue
        review_payload = record.get("review")
        if not isinstance(review_payload, Mapping):
            continue
        try:
            review = ReviewResult.model_validate(review_payload)
        except Exception:
            continue
        bound_candidate = record.get("candidate_binding_sha256")
        if stage == "resolution_queue_auto_final_reviews":
            bound_candidate = record.get("candidate_binding_sha256")
        if (
            review.verdict == "ACCEPT"
            and review.unit_uid == request.unit_uid
            and bound_candidate == candidate_sha256
            and record.get("request_binding_sha256") == expected_request
        ):
            return {"stage": stage, "review": review.model_dump(), "request_binding_sha256": expected_request}
    for human in _human_approved_candidate_records(checkpoint_dir, request.display_id):
        if not (
            isinstance(human, Mapping)
        and human.get("artifact_type") in {
            "PRODUCTION_HUMAN_APPROVED_CANDIDATE",
            "PRODUCTION_HUMAN_APPROVED_CANDIDATE_DESCENDANT",
        }
        and human.get("semantic_terminal") == "HUMAN_APPROVED"
        and human.get("current_display_id") == request.display_id
        and human.get("candidate_binding_sha256") == candidate_sha256
        and human.get("source_sha256") == request.source_sha256
        and human.get("request_binding_sha256") == expected_request
        and isinstance(human.get("working"), Mapping)
            and human["working"].get("unit_uid") == request.unit_uid
        ):
            continue
        return {
                "stage": "human_approved_candidates",
                "semantic_terminal": "HUMAN_APPROVED",
                "candidate_binding_sha256": candidate_sha256,
                "request_binding_sha256": expected_request,
            }
    return None


def _current_head_corrective_review(*, checkpoint_dir: Path, request, candidate_sha256: str, bible: Path | None) -> ReviewResult | None:
    """Return only an exact-current-head REPAIR from the corrective review lane."""
    expected_request = enrich_request(request, translation_bible=bible).request_binding_sha256
    for stage in ("semantic_rescue_rereviews", "semantic_rescue_corrective_final_reviews"):
        record = load_stage(checkpoint_dir, stage, request.display_id)
        if not isinstance(record, Mapping):
            continue
        try:
            review = ReviewResult.model_validate(record.get("review"))
        except Exception:
            continue
        if (
            review.verdict == "REPAIR"
            and review.unit_uid == request.unit_uid
            and record.get("candidate_binding_sha256") == candidate_sha256
            and record.get("request_binding_sha256") == expected_request
        ):
            return review
    return None


def _selected_current_recovery_execution_family(
    obligation: Mapping[str, Any] | None,
    *,
    request,
    candidate_sha256: str,
    review: ReviewResult | None,
) -> str | None:
    """Validate (but never select) the canonical recovery work handed to execution.

    Selection belongs to ``project_current_canonical_recovery``.  This small
    gateway deliberately only compares the immutable, exact binding supplied
    by that projection with the head currently resolved by the executor.
    """
    if not isinstance(obligation, Mapping):
        return None
    fingerprint = obligation.get("recovery_fingerprint")
    if not isinstance(fingerprint, Mapping):
        return None
    family = obligation.get("recovery_family")
    selected = obligation.get("failing_work_order_ids")
    if (
        family not in {"SEMANTIC_CORRECTIVE", "SEMANTIC_REREVIEW"}
        or selected != [request.display_id]
        or fingerprint.get("work_order") != request.display_id
        or fingerprint.get("current_head_sha256") != candidate_sha256
        or fingerprint.get("recovery_family") != family
    ):
        return None
    # Rereview is itself the next missing action.  Its canonical obligation
    # is therefore exactly bound to the current head before a ReviewResult
    # exists; treating that deliberate null binding as stale prevents the
    # existing rereview handler from ever being reached.
    if family == "SEMANTIC_REREVIEW" and review is None:
        return family if (
            fingerprint.get("bound_review_result_sha256") is None
            and fingerprint.get("affected_segment_indices") == []
            and int(fingerprint.get("recovery_budget_used", 0)) == 0
        ) else None
    if not isinstance(review, ReviewResult) or fingerprint.get("bound_review_result_sha256") != _canonical_sha256(review.model_dump()):
        return None
    scope = sorted({index for issue in review.issues for index in issue.affected_segment_indices})
    if fingerprint.get("affected_segment_indices") != scope:
        return None
    # A persisted current-head corrective attempt consumes this bounded
    # episode.  The existing handler independently checks the complete child
    # identity before it reuses anything durable.
    if family == "SEMANTIC_CORRECTIVE" and int(fingerprint.get("recovery_budget_used", 0)) != 0:
        return None
    return str(family)


def _current_head_corrective_attempt_matches(*, checkpoint_dir: Path, request, parent_candidate_sha256: str, review: ReviewResult, scope: tuple[int, ...], bible: Path | None) -> bool:
    """Whether the durable corrective child is for this exact authority transition.

    A stage record from another candidate is immutable history, never a
    create-once substitute for a new current-head transition.
    """
    record = load_stage(checkpoint_dir, "semantic_rescue_current_head_corrective", request.display_id)
    if record is None:
        return False
    if not isinstance(record, Mapping):
        return False
    return (
        record.get("parent_candidate_sha256") == parent_candidate_sha256
        and record.get("request_binding_sha256") == enrich_request(request, translation_bible=bible).request_binding_sha256
        and record.get("review_result_sha256") == _canonical_sha256(review.model_dump())
        and record.get("required_repair_scope") == list(scope)
    )


def _qa_reconciliation_payload(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    resolved_candidate,
    original_qa_path: Path,
    original_qa_record: Mapping[str, Any],
    rerun_qa: Mapping[str, Any],
    bible: Path | None,
) -> dict[str, Any] | None:
    """Build one fail-closed historical-QA reconciliation for the current head."""
    diagnostics = original_qa_record.get("diagnostics")
    failed_rules = [str(item.get("code")) for item in diagnostics if isinstance(item, Mapping) and item.get("code")] if isinstance(diagnostics, list) else []
    if (
        not original_qa_path.is_file()
        or original_qa_record.get("display_id") != request.display_id
        or original_qa_record.get("unit_uid") != request.unit_uid
        or bool(original_qa_record.get("passed"))
        or not failed_rules
        or not bool(rerun_qa.get("passed"))
    ):
        return None
    reviewer = _current_candidate_reviewer_accept(
        checkpoint_dir=checkpoint_dir, request=request,
        candidate_sha256=resolved_candidate.candidate_sha256, bible=bible,
    )
    if reviewer is None:
        return None
    lineage = list(resolved_candidate.lineage)
    parent = lineage[-2]["candidate_sha256"] if len(lineage) > 1 else None
    original_candidate_sha256 = _candidate_binding(raw)
    return {
        "artifact_type": "PRODUCTION_CURRENT_CANDIDATE_QA_RECONCILIATION",
        "schema_version": 1,
        "logical_work_order_id": request.display_id,
        "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256,
        "request_binding_sha256": enrich_request(request, translation_bible=bible).request_binding_sha256,
        "original_qa_artifact_path": str(original_qa_path),
        "original_qa_artifact_sha256": sha256(original_qa_path.read_bytes()).hexdigest(),
        "original_qa_record": dict(original_qa_record),
        "original_qa_record_sha256": _canonical_sha256(dict(original_qa_record)),
        "original_candidate_sha256": original_candidate_sha256,
        "current_candidate_sha256": resolved_candidate.candidate_sha256,
        "parent_candidate_sha256": parent,
        "candidate_lineage": lineage,
        "failed_qa_rules": failed_rules,
        "resolution_classification": (
            "DETERMINISTIC_FALSE_POSITIVE_CORRECTION"
            if original_candidate_sha256 == resolved_candidate.candidate_sha256
            else "VALID_BOUND_CANDIDATE_MUTATION"
        ),
        "rerun_qa": dict(rerun_qa),
        "core_harness": dict(rerun_qa),
        "reviewer": reviewer,
        "translation_api_calls": 0,
    }


def _ensure_current_candidate_qa_reconciliation(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    resolved_candidate,
    original_qa_path: Path,
    original_qa_record: Mapping[str, Any],
    rerun_qa: Mapping[str, Any],
    bible: Path | None,
) -> bool:
    """Create-once reconciliation preparation outside the effective-QA read."""
    record = load_stage(checkpoint_dir, "current_candidate_qa_reconciliations", request.display_id)
    expected = _qa_reconciliation_payload(
        checkpoint_dir=checkpoint_dir, request=request, raw=raw,
        resolved_candidate=resolved_candidate, original_qa_path=original_qa_path,
        original_qa_record=original_qa_record, rerun_qa=rerun_qa, bible=bible,
    )
    if record is None and expected is not None:
        persist_stage_once(checkpoint_dir, "current_candidate_qa_reconciliations", request.display_id, expected)
        record = expected
    if not isinstance(record, Mapping) or expected is None:
        return False
    required = (
        "logical_work_order_id", "unit_uid", "source_sha256", "request_binding_sha256",
        "original_qa_artifact_sha256", "original_qa_record_sha256", "original_candidate_sha256",
        "current_candidate_sha256", "parent_candidate_sha256", "candidate_lineage", "failed_qa_rules",
        "resolution_classification", "rerun_qa", "core_harness", "reviewer",
    )
    return all(record.get(key) == expected.get(key) for key in required)


def _valid_current_candidate_qa_reconciliation(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    resolved_candidate,
    original_qa_path: Path,
    original_qa_record: Mapping[str, Any],
    rerun_qa: Mapping[str, Any],
    bible: Path | None,
) -> bool:
    """Read-only validation of immutable historical-to-current QA evidence."""
    record = load_stage(checkpoint_dir, "current_candidate_qa_reconciliations", request.display_id)
    expected = _qa_reconciliation_payload(
        checkpoint_dir=checkpoint_dir, request=request, raw=raw,
        resolved_candidate=resolved_candidate, original_qa_path=original_qa_path,
        original_qa_record=original_qa_record, rerun_qa=rerun_qa, bible=bible,
    )
    if not isinstance(record, Mapping) or expected is None:
        return False
    required = (
        "logical_work_order_id", "unit_uid", "source_sha256", "request_binding_sha256",
        "original_qa_artifact_sha256", "original_qa_record_sha256", "original_candidate_sha256",
        "current_candidate_sha256", "parent_candidate_sha256", "candidate_lineage", "failed_qa_rules",
        "resolution_classification", "rerun_qa", "core_harness", "reviewer",
    )
    return all(record.get(key) == expected.get(key) for key in required)


def resolve_effective_qa(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    current_head,
    historical_qa_record: Mapping[str, Any],
    current_qa: Mapping[str, Any],
    original_qa_path: Path,
    bible: Path | None,
) -> dict[str, Any]:
    """Read effective QA without rewriting the immutable first-pass RAW QA."""
    if not bool(current_qa.get("passed")):
        return {"state": "FAIL", "reason": "CURRENT_HEAD_CORE_QA_FAIL", "evidence": [str(original_qa_path)]}
    if bool(historical_qa_record.get("passed")):
        return {"state": "PASS", "reason": "HISTORICAL_RAW_QA_PASS", "evidence": [str(original_qa_path)]}
    semantic_accept = _current_candidate_reviewer_accept(
        checkpoint_dir=checkpoint_dir,
        request=request,
        candidate_sha256=current_head.candidate_sha256,
        bible=bible,
    )
    # The human-approved candidate is itself the append-only current-head
    # semantic terminal.  It is sufficient to prevent a historical RAW-only
    # QA failure from reopening the exact, Core-clean human head, even before
    # the optional reconciliation projection is materialized by a worker.
    if semantic_accept is not None and semantic_accept.get("stage") == "human_approved_candidates":
        return {
            "state": "PASS",
            "reason": "HUMAN_APPROVED_CURRENT_HEAD_CORE_QA_PASS",
            "evidence": [str(original_qa_path), str(checkpoint_dir / "human_approved_candidates" / f"{request.display_id}.json")],
        }
    # A normalization/D051 rule can be hardened after immutable first-pass
    # RAW QA was written.  When Candidate Authority still resolves the exact
    # RAW candidate, the current deterministic QA is the production truth;
    # no candidate mutation occurred that would require an additional
    # reconciliation artifact.  This deliberately does not apply to a newer
    # candidate head, which continues through the bound terminal/reconciliation
    # checks below.  Keep the human-terminal check above so its explicit
    # terminal provenance remains visible in effective-QA reporting.
    if current_head.candidate_sha256 == _candidate_binding(raw):
        return {
            "state": "PASS",
            "reason": "CURRENT_RAW_HEAD_EFFECTIVE_QA_PASS",
            "evidence": [str(original_qa_path)],
        }
    if _valid_current_candidate_qa_reconciliation(
        checkpoint_dir=checkpoint_dir, request=request, raw=raw,
        resolved_candidate=current_head, original_qa_path=original_qa_path,
        original_qa_record=historical_qa_record, rerun_qa=current_qa, bible=bible,
    ):
        return {"state": "PASS", "reason": "CURRENT_HEAD_RECONCILIATION_PASS", "evidence": [str(original_qa_path)]}
    return {"state": "FAIL", "reason": "HISTORICAL_FAIL_NOT_RECONCILED_TO_CURRENT_HEAD", "evidence": [str(original_qa_path)]}


def _reprocess_binding_matches_current_group(
    checkpoint_dir: Path,
    request,
    candidate: TranslationResult,
    group,
) -> bool:
    """Accept a v2 RAW reprocess only when its review used today's exact group.

    This validates existing local envelopes; it is not a retry budget. A group
    can change when prior verified D050 evidence identifies an intervening
    footnote body. The historic response remains durable, but cannot certify
    the rebuilt semantic context.
    """
    if group is None:
        return True
    expected_candidate = _candidate_binding(candidate)
    expected_context = _canonical_sha256(group.as_reviewer_context_payload())
    for path in _reviewer_envelope_paths(checkpoint_dir, request.display_id):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            envelope.get("logical_work_order_id") == request.display_id
            and envelope.get("unit_uid") == request.unit_uid
            and envelope.get("candidate_sha256") == expected_candidate
            and envelope.get("semantic_group_sha256") == group.group_hash
            and envelope.get("context_sha256") == expected_context
        ):
            return True
    return False


def _d051_equivalence_for(checkpoint_dir: Path, request, candidate: TranslationResult, *, bible: Path | None) -> ExplicitNumericRepresentationEquivalence | None:
    evidence = load_stage(checkpoint_dir, "d051_reviewed_equivalences", request.display_id)
    if evidence is None:
        return None
    try:
        equivalence = ExplicitNumericRepresentationEquivalence.model_validate(evidence["equivalence"])
    except Exception:
        return None
    if (
        evidence.get("display_id") != request.display_id
        or evidence.get("unit_uid") != request.unit_uid
        or evidence.get("source_sha256") != request.source_sha256
        or evidence.get("request_binding_sha256") != enrich_request(request, translation_bible=bible).request_binding_sha256
        or evidence.get("candidate_binding_sha256") != _candidate_binding(candidate)
        or equivalence.unit_uid != request.unit_uid
    ):
        return None
    return equivalence


def _reuse_exact_d051_equivalence(*, approved: Mapping[str, Any], approved_path: Path, request, candidate: TranslationResult, bible: Path | None) -> dict[str, Any]:
    """Rebind one already-approved literal D051 pair; never infer a number."""
    if approved.get("artifact_type") != "D051_REVIEWED_EQUIVALENCE_APPROVAL":
        raise ValueError("D051_REUSE_SOURCE_NOT_APPROVED")
    equivalence = ExplicitNumericRepresentationEquivalence.model_validate(approved.get("equivalence"))
    if (
        equivalence.reviewed_source_semantic_numeric_value != equivalence.reviewed_target_semantic_numeric_value
        or equivalence.exact_source_span.count("\n") != 0
        or request.source_text.count(equivalence.exact_source_span) != 1
        or equivalence.approved_target_representation not in "".join(segment.text for segment in candidate.target_segments)
    ):
        raise ValueError("D051_REUSE_NOT_EXACT_MATCH")
    rebound = equivalence.model_copy(update={"unit_uid": request.unit_uid})
    return {
        "artifact_type": "D051_REVIEWED_EQUIVALENCE_REUSE",
        "schema_version": 1,
        "decision_authority": "REUSED_EXACT_USER_APPROVED_EQUIVALENCE",
        "reused_approved_evidence_sha256": sha256(approved_path.read_bytes()).hexdigest(),
        "reused_approved_display_id": approved.get("display_id"),
        "display_id": request.display_id,
        "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256,
        "request_binding_sha256": enrich_request(request, translation_bible=bible).request_binding_sha256,
        "candidate_binding_sha256": _candidate_binding(candidate),
        "equivalence": rebound.model_dump(),
        "translation_api_calls": 0,
    }


def _qa_with_d051(qa, view, request, candidate, known_unit_ids, equivalence: ExplicitNumericRepresentationEquivalence | None):
    if equivalence is None:
        return qa(view, request, candidate, known_unit_ids)
    verified = request.model_copy(update={"explicit_numeric_representation_equivalences": [equivalence]})
    return qa(view, verified, candidate, known_unit_ids)


def _rereview_stale_final_candidate(*, checkpoint_dir: Path, request, candidate: TranslationResult, active: WorkerProviders, view, known_unit_ids, qa, bible, group, classification: str = "STALE_FINAL_REVIEW_CANDIDATE", allow_verified_d050_marker_repair: bool = False, review_stage: str = "semantic_rescue_rereviews", review_request_stage: str = "semantic_rescue_rereview_requests", authority_raw: TranslationResult | None = None) -> UnitOutcome:
    """One reviewer-only correction for a proven stale final-review group context."""
    candidate_sha = _candidate_binding(candidate)
    qa_record = qa(view, request, candidate, known_unit_ids)
    # Review binds ``candidate``; Candidate Authority must continue from the
    # immutable original RAW root, not from a descendant under review.
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=authority_raw or candidate, working=candidate, qa=qa_record, recovered_from="semantic_rescue_rereview")
    if not qa_record.get("passed") and not (
        allow_verified_d050_marker_repair and _only_missing_verified_d050_markers(request, candidate, qa_record)
    ):
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    if group is None:
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = "STALE_FINAL_REVIEW_GROUP_MISSING"; return outcome
    try:
        context, binding = _post_repair_final_review_binding(request, candidate, group)
    except RuntimeError as error:
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = str(error); return outcome
    assert context is not None
    span_sha = binding["group_span_sha256"]
    audit = load_stage(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id)
    if audit is None:
        prior_group = load_stage(checkpoint_dir, "review_groups", request.display_id) or {}
        prior_span = str(prior_group.get("full_candidate_span") or "")
        persist_stage_once(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id, {
            "artifact_type": "D051_APPROVED_FINAL_REVIEW" if classification == "D051_APPROVED" else "SYSTEMIC_PIPELINE_GAP", "schema_version": 1,
            "classification": classification, "display_id": request.display_id,
            "durable_repaired_candidate_binding_sha256": candidate_sha,
            "prior_semantic_group_hash": prior_group.get("group_hash"),
            "prior_full_candidate_span_sha256": sha256(prior_span.encode("utf-8")).hexdigest(),
            "rebuilt_semantic_group_hash": group.group_hash,
            "rebuilt_full_candidate_span_sha256": span_sha, "translation_api_calls": 0,
        })
    stored_request = load_stage(checkpoint_dir, review_request_stage, request.display_id)
    if stored_request is None:
        persist_stage_once(checkpoint_dir, review_request_stage, request.display_id, {
            "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_REREVIEW_REQUEST", "schema_version": 1,
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            **binding,
            "full_candidate_span_sha256": span_sha,
            "full_candidate_span": context.get("full_candidate_span"),
            "target_segments": [segment.model_dump() for segment in candidate.target_segments],
        })
    else:
        if not _stored_final_binding_matches(stored_request, binding):
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = "FINAL_REVIEW_CANDIDATE_BINDING_MISMATCH"; return outcome
    saved = load_stage(checkpoint_dir, review_stage, request.display_id)
    if saved is None:
        try:
            result = review_with_contract_boundary(active.reviewer, enrich_request(request, translation_bible=bible), candidate, deterministic_qa_summary={"passed": True}, semantic_context=context)
        except Exception as error:
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
        review = result.review
        persist_stage_once(checkpoint_dir, review_stage, request.display_id, {
            "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_REREVIEW", "schema_version": 1,
            **binding,
            "full_candidate_span_sha256": span_sha, "review": review.model_dump(),
            "usage": dict(result.candidate.usage), "request_binding_sha256": result.candidate.request_binding_sha256,
            "translation_api_calls": 0, "repair_api_calls": 0,
        })
        outcome.reviewer_api_calls = 1
    else:
        review = ReviewResult.model_validate(saved["review"])
    outcome.review = review
    outcome.final_status = "ACCEPT" if review.verdict == "ACCEPT" else "HUMAN_REQUIRED"
    outcome.human_required = review.verdict != "ACCEPT"
    return outcome


def _run_auto_polish(*, checkpoint_dir: Path, request, candidate: TranslationResult, review: ReviewResult, active: WorkerProviders, view, known_unit_ids, qa, bible, group) -> UnitOutcome:
    """One exact-scope Qwen polish after an AUTO_POLISH-only review."""
    provider_request = enrich_request(request, translation_bible=bible)
    qa_record = qa(view, request, candidate, known_unit_ids)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=candidate, working=candidate, qa=qa_record, recovered_from="auto_polish")
    severities = review_issue_severities(issue.code for issue in review.issues)
    if not qa_record.get("passed") or BLOCKING_SEMANTIC in severities or AUTO_POLISH not in severities:
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
    saved = load_stage(checkpoint_dir, "semantic_rescue_polish", request.display_id)
    if saved is None:
        gate = decide_approved_repair_contract(provider_request, candidate, review=review, allow_semantic_narrow=True)
        if gate.action != "REPAIR" or gate.contract is None:
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; return outcome
        # The user-approved narrow intent prevents a general retranslation.
        contract = gate.contract.model_copy(update={"repair_instruction": "Only polish the exact affected segments so the joined Chinese reads ‘我们不愿意无限期地承诺保持兼容性：’. Preserve all meanings, numerals, anchors, protected text, URL, DOI, and D050 bindings."})
        try:
            result = repair_with_contract_boundary(active.repair, provider_request, candidate, contract)
            polished = result.repaired
        except Exception as error:
            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
        persist_stage_once(checkpoint_dir, "semantic_rescue_polish", request.display_id, {"artifact_type": "PRODUCTION_AUTO_POLISH", "schema_version": 1, "input_candidate_binding_sha256": _candidate_binding(candidate), "authority_parent_verified": True, "contract": contract.model_dump(), "working": polished.model_dump(), "review_issue_severities": sorted(severities), "usage": dict(result.candidate.usage), "translation_api_calls": 0})
        outcome.repair_api_calls = 1
    else:
        polished = TranslationResult.model_validate(saved["working"])
    try:
        polished, polished_qa, _, _ = finalize_recovery_candidate(
            request=request, base=candidate, candidate=polished,
            parent_candidate_sha256=(
                _candidate_binding(candidate) if saved is None
                else str(saved.get("input_candidate_binding_sha256") or "") or None
            ),
            view=view, known_unit_ids=known_unit_ids, qa=qa,
        )
    except (CandidateAuthorityError, RuntimeError) as error:
        outcome.working = polished; outcome.qa = qa(view, request, polished, known_unit_ids)
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = str(error); return outcome
    # The direct candidate is authoritative after polish.  Do not reuse a
    # pre-polish group span as reviewer context.
    final_context = None
    saved_final = load_stage(checkpoint_dir, "semantic_rescue_polish_reviews", request.display_id)
    if saved_final is None:
        failures = _reviewer_contract_failure_paths(checkpoint_dir, request.display_id)
        triage = load_stage(checkpoint_dir, "human_required_triage", request.display_id)
        prior_unpersisted_contract_gap = (
            isinstance(triage, Mapping)
            and "REVIEWER_PAYLOAD_NOT_PERSISTED" in set(triage.get("gaps") or [])
        )
        # Recover only a logged-but-unpersisted contract gap. A plain crash
        # between the durable polish and first review remains a normal first
        # review, rather than inventing a provider response.
        if not failures and prior_unpersisted_contract_gap:
            observed = ProviderContractGap("REVIEWER_CONTRACT_GAP_UNIT_UID_MISMATCH")
            _persist_reviewer_contract_failure(
                checkpoint_dir,
                request=request,
                raw=polished,
                bible=bible,
                semantic_context=final_context,
                qa_record=polished_qa,
                error=observed,
                attempt=1,
                reconstructed_from_worker_log=True,
                provider_name=getattr(active.reviewer, "provider_name", None),
                model_name=getattr(active.reviewer, "model", None),
            )
            failures = _reviewer_contract_failure_paths(checkpoint_dir, request.display_id)
        if len(failures) >= MAX_REVIEWER_CONTRACT_ATTEMPTS:
            outcome.working = polished; outcome.qa = polished_qa; outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = REVIEWER_CONTRACT_FAILURE; return outcome
        try:
            result = review_with_contract_boundary(active.reviewer, provider_request, polished, deterministic_qa_summary={"passed": True}, semantic_context=final_context)
        except ProviderContractGap as error:
            _persist_reviewer_contract_failure(
                checkpoint_dir,
                request=request,
                raw=polished,
                bible=bible,
                semantic_context=final_context,
                qa_record=polished_qa,
                error=error,
                attempt=len(failures) + 1,
                provider_name=getattr(active.reviewer, "provider_name", None),
                model_name=getattr(active.reviewer, "model", None),
            )
            outcome.working = polished; outcome.qa = polished_qa; outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True; outcome.failure_code = getattr(error, "code", type(error).__name__); return outcome
        final = result.review
        final_severities = review_issue_severities(issue.code for issue in final.issues)
        persist_stage_once(checkpoint_dir, "semantic_rescue_polish_reviews", request.display_id, {"artifact_type": "PRODUCTION_AUTO_POLISH_FINAL_REVIEW", "schema_version": 1, "candidate_binding_sha256": _candidate_binding(polished), "review": final.model_dump(), "review_issue_severities": sorted(final_severities), "usage": dict(result.candidate.usage), "translation_api_calls": 0})
        outcome.reviewer_api_calls = 1
    else:
        final = ReviewResult.model_validate(saved_final["review"])
        final_severities = set(saved_final.get("review_issue_severities") or [])
    outcome.working = polished; outcome.qa = polished_qa; outcome.review = final; outcome.repair_applied = True; outcome.repair_policy_id = "AUTO_POLISH"
    if final.verdict == "HUMAN_REQUIRED" or BLOCKING_SEMANTIC in final_severities:
        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
    else:
        outcome.final_status = "ACCEPT"  # ACCEPT, AUTO_POLISH-only REPAIR, and COSMETIC_DEBT are resolved terminals.
    return outcome


def _reviewer_envelope(request, candidate: TranslationResult, *, bible, semantic_context: Mapping[str, Any] | None, reviewer, attempt: int) -> dict[str, Any]:
    provider_request = enrich_request(request, translation_bible=bible)
    context = dict(semantic_context or {})
    return {
        "schema_version": 1,
        "logical_work_order_id": request.display_id,
        "unit_uid": request.unit_uid,
        "candidate_sha256": _candidate_binding(candidate),
        "request_binding_sha256": provider_request.request_binding_sha256,
        "context_sha256": _canonical_sha256(context),
        "semantic_group_sha256": context.get("group_hash"),
        "provider": getattr(reviewer, "provider_name", type(reviewer).__name__),
        "model": getattr(reviewer, "model", None),
        "attempt": attempt,
    }


def _persist_reviewer_payload(checkpoint_dir: Path, request, raw: TranslationResult, *, bible, semantic_context: Mapping[str, Any] | None, qa: Mapping[str, Any] | None, reviewer) -> None:
    if load_stage(checkpoint_dir, "reviewer_payloads", request.display_id) is not None:
        return
    provider_request = enrich_request(request, translation_bible=bible)
    payload = reviewer_request_payload(
        provider_request,
        raw,
        deterministic_qa_summary={"passed": bool(qa and qa.get("passed")), "codes": [], "display_id": request.display_id, "unit_uid": request.unit_uid},
        semantic_context=semantic_context,
    )
    persist_stage_once(
        checkpoint_dir,
        "reviewer_payloads",
        request.display_id,
        {
            "artifact_type": "PRODUCTION_REVIEWER_PAYLOAD",
            "schema_version": 1,
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "local_review_envelope": _reviewer_envelope(request, raw, bible=bible, semantic_context=semantic_context, reviewer=reviewer, attempt=1),
            **payload,
        },
    )


def _reviewer_contract_failure_paths(checkpoint_dir: Path, display_id: str) -> list[Path]:
    folder = checkpoint_dir / "reviewer_contract_failures"
    return sorted(folder.glob(f"{display_id}.attempt_*.json")) if folder.is_dir() else []


def _matching_reviewer_contract_failure_paths(
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    *,
    bible,
    semantic_context: Mapping[str, Any] | None,
) -> list[Path]:
    """Return only failures bound to this exact candidate and reviewer task."""
    expected_request = enrich_request(request, translation_bible=bible).request_binding_sha256
    expected_candidate = _candidate_binding(raw)
    expected_context = None if semantic_context is None else _canonical_sha256(dict(semantic_context))
    matched: list[Path] = []
    for path in _reviewer_contract_failure_paths(checkpoint_dir, request.display_id):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            record.get("unit_uid") == request.unit_uid
            and record.get("candidate_binding_sha256") == expected_candidate
            and record.get("request_binding_sha256") == expected_request
            and (expected_context is None or record.get("semantic_context_sha256") == expected_context)
        ):
            matched.append(path)
    return matched


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _provider_contract_failure_paths(checkpoint_dir: Path, display_id: str) -> list[Path]:
    folder = checkpoint_dir / "provider_contract_failures"
    return sorted(folder.glob(f"{display_id}.attempt_*.json")) if folder.is_dir() else []


def _provider_request_for_decision(request, *, bible, decision):
    """Use the route's output-contract version in bounded-attempt identity."""
    provider_request = enrich_request(request, translation_bible=bible)
    if decision.route == "QWEN_MAX_STRUCTURED":
        return replace(provider_request, output_contract_version="booktrans.structured-translation.v2")
    return provider_request


def _matching_provider_contract_failure_paths(checkpoint_dir: Path, request, *, bible, decision) -> list[Path]:
    provider_request = _provider_request_for_decision(request, bible=bible, decision=decision)
    matched: list[Path] = []
    for path in _provider_contract_failure_paths(checkpoint_dir, request.display_id):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            record.get("unit_uid") == request.unit_uid
            and record.get("source_sha256") == request.source_sha256
            and record.get("request_binding_sha256") == provider_request.request_binding_sha256
            and record.get("provider_route") == decision.route
            and record.get("model") == decision.model
        ):
            matched.append(path)
    return matched


def _persist_provider_contract_failure(
    checkpoint_dir: Path, *, request, bible, decision, error: ProviderContractGap, attempt: int,
    reconstructed_from_worker_handoff: bool = False,
) -> Path:
    """Persist an invalid provider response without allowing it into RAW."""
    provider_request = _provider_request_for_decision(request, bible=bible, decision=decision)
    candidate = getattr(error, "candidate", None)
    response_text = getattr(candidate, "response_text", None)
    key = f"{request.display_id}.attempt_{attempt:02d}"
    path = checkpoint_dir / "provider_contract_failures" / f"{key}.json"
    persist_stage_once(
        checkpoint_dir,
        "provider_contract_failures",
        key,
        {
            "artifact_type": "PRODUCTION_PROVIDER_CONTRACT_FAILURE",
            "schema_version": 1,
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "source_sha256": request.source_sha256,
            "request_binding_sha256": provider_request.request_binding_sha256,
            "provider_route": decision.route,
            "provider": getattr(candidate, "provider", None),
            "model": getattr(candidate, "model", None) or decision.model,
            "segment_modes": [classify_source_segment_mode(segment) for segment in request.source_segments],
            "provider_contract_attempt": attempt,
            "contract_error_code": error.code,
            "diagnostic_reason": error.code.removeprefix("PROVIDER_CONTRACT_GAP_"),
            "invalid_response": response_text,
            "response_sha256": sha256(response_text.encode("utf-8")).hexdigest() if isinstance(response_text, str) else None,
            "raw_provider_response_status": "PERSISTED_INVALID_RESPONSE" if isinstance(response_text, str) else "RESPONSE_UNAVAILABLE",
            "valid_raw_created": False,
            "failure_evidence_source": "WORKER_HANDOFF_RECONSTRUCTION" if reconstructed_from_worker_handoff else "PROVIDER_CONTRACT_DIAGNOSTIC",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return path


def _persist_no_safe_authoritative_candidate_terminal(
    checkpoint_dir: Path, *, request, bible, decision, provider_contract_evidence: Path | None,
) -> Path:
    """Bind a no-candidate terminal result to its exact acquisition failure."""
    upstream: Mapping[str, Any] = {}
    if provider_contract_evidence is not None:
        try:
            loaded = json.loads(provider_contract_evidence.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                upstream = loaded
        except (OSError, json.JSONDecodeError):
            pass
    provider_request = _provider_request_for_decision(request, bible=bible, decision=decision)
    path = checkpoint_dir / "candidate_acquisition_terminals" / f"{request.display_id}.json"
    persist_stage_once(
        checkpoint_dir,
        "candidate_acquisition_terminals",
        request.display_id,
        {
            "artifact_type": "NO_SAFE_AUTHORITATIVE_CANDIDATE_TERMINAL",
            "schema_version": 1,
            "terminal_class": "NO_SAFE_AUTHORITATIVE_CANDIDATE",
            "root_cause_class": "PROVIDER_ATTEMPTS_EXHAUSTED",
            "root_cause_evidence": str(provider_contract_evidence) if provider_contract_evidence else None,
            "root_cause_code": upstream.get("contract_error_code"),
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "source_sha256": request.source_sha256,
            "request_binding_sha256": provider_request.request_binding_sha256,
            "provider_route": decision.route,
            "provider": upstream.get("provider"),
            "model": upstream.get("model") or decision.model,
            "segment_modes": [classify_source_segment_mode(segment) for segment in request.source_segments],
            "valid_provider_result_exists": False,
            "candidate_constructed": False,
            "candidate_persisted": False,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return path


def _reconstruct_unpersisted_provider_contract_failure(
    checkpoint_dir: Path, *, run: Path, request, bible, decision,
) -> None:
    """Turn a prior worker handoff into append-only attempt-0 evidence once.

    Earlier workers stopped before provider contract failures had a durable
    boundary.  This generic resume bridge never invents a response; it only
    records a matching immutable worker handoff, so the sole remaining retry
    remains bounded and idempotent.
    """
    if _matching_provider_contract_failure_paths(checkpoint_dir, request, bible=bible, decision=decision):
        return
    logs = run / "production_campaign_logs"
    if not logs.is_dir():
        return
    for handoff in sorted(logs.glob("worker-handoff-*-consumed_blocked.json"), reverse=True):
        try:
            record = json.loads(handoff.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            record.get("checkpoint") == checkpoint_dir.name
            and record.get("stage_reached") == "PROVIDER"
            and record.get("failure_classification") == "PROVIDER_CONTRACT_FAILURE"
            and record.get("next_logical_work_order_id") == request.display_id
        ):
            error_code = "PROVIDER_CONTRACT_GAP_HISTORICAL_RESPONSE_UNAVAILABLE"
            worker_id = str(record.get("worker_id") or "")
            # The worker log is immutable operational evidence.  Recover the
            # exact diagnostic when it is available, but never pretend its
            # absent response body can be reconstructed.
            worker_marker = worker_id.split("-", 1)[1] if "-" in worker_id else ""
            for worker_log in sorted(logs.glob(f"worker-{worker_marker.split('-', 1)[0]}*.jsonl"), reverse=True):
                try:
                    text = worker_log.read_text(encoding="utf-8")
                except OSError:
                    continue
                match = re.search(r"PROVIDER_CONTRACT_FAILURE\s+(PROVIDER_CONTRACT_GAP_[A-Z0-9_]+)", text)
                if match:
                    error_code = match.group(1)
                    break
            _persist_provider_contract_failure(
                checkpoint_dir, request=request, bible=bible, decision=decision,
                error=ProviderContractGap(error_code),
                attempt=0, reconstructed_from_worker_handoff=True,
            )
            return


def _translate_with_bounded_provider_contract_retry(
    *, checkpoint_dir: Path, run: Path, request, bible, active: WorkerProviders, decision,
) -> tuple[Any | None, TranslationResult | None, Path | None]:
    """The only candidate-generating provider-contract consumer in production.

    A contract-invalid response is diagnostic evidence, never semantic input.
    Exactly one same-request retry is allowed; a valid RAW is returned only
    after the existing provider validators have accepted it.
    """
    _reconstruct_unpersisted_provider_contract_failure(
        checkpoint_dir, run=run, request=request, bible=bible, decision=decision,
    )
    failures = _matching_provider_contract_failure_paths(checkpoint_dir, request, bible=bible, decision=decision)
    retry_feedback: Mapping[str, Any] | None = None
    for attempt in range(len(failures), MAX_PROVIDER_CONTRACT_ATTEMPTS):
        try:
            provider_request = _provider_request_for_decision(request, bible=bible, decision=decision)
            if retry_feedback is not None:
                provider_request = replace(provider_request, protocol_retry_feedback=retry_feedback)
            candidate, raw = translate_unit(provider_request, active, decision)
            return candidate, raw, None
        except ProviderContractGap as error:
            evidence = _persist_provider_contract_failure(
                checkpoint_dir, request=request, bible=bible, decision=decision, error=error, attempt=attempt,
            )
            if attempt + 1 >= MAX_PROVIDER_CONTRACT_ATTEMPTS:
                return None, None, evidence
            # The retry is still the same bounded request.  Feed back only
            # validator facts; never synthesize a translation or move segment
            # ownership across indices.
            details = dict(error.details)
            retry_feedback = {
                "contract_error_code": error.code,
                "invalid_target_index": details.get("segment_index"),
                "source_fragment": details.get("source_text"),
                "requirement": "All target_texts must preserve supplied indices and every TEXT target must be nonempty. Do not redistribute content across segments.",
            }
    return None, None, failures[-1] if failures else None


def _persist_reviewer_envelope(checkpoint_dir: Path, request, candidate: TranslationResult, *, bible, semantic_context: Mapping[str, Any] | None, reviewer, attempt: int) -> None:
    key = f"{request.display_id}.attempt_{attempt:02d}"
    persist_stage_once(
        checkpoint_dir,
        "reviewer_envelopes",
        key,
        {
            "artifact_type": "PRODUCTION_LOCAL_REVIEW_ENVELOPE",
            # This write is the external-effect barrier: once it exists the
            # caller must conservatively treat the provider attempt as spent,
            # including after a crash or a killed process.
            "attempt_state": "DISPATCH_COMMITTED",
            "dispatch_committed_at": datetime.now(timezone.utc).isoformat(),
            **_reviewer_envelope(request, candidate, bible=bible, semantic_context=semantic_context, reviewer=reviewer, attempt=attempt),
        },
    )


def _reviewer_envelope_paths(checkpoint_dir: Path, display_id: str) -> list[Path]:
    folder = checkpoint_dir / "reviewer_envelopes"
    return sorted(folder.glob(f"{display_id}.attempt_*.json")) if folder.is_dir() else []


def _persist_reviewer_contract_failure(
    checkpoint_dir: Path,
    *,
    request,
    raw: TranslationResult,
    bible,
    semantic_context: Mapping[str, Any] | None,
    qa_record: Mapping[str, Any] | None,
    error: ProviderContractGap,
    attempt: int,
    storage_attempt: int | None = None,
    reconstructed_from_worker_log: bool = False,
    provider_name: str | None = None,
    model_name: str | None = None,
) -> Path:
    provider_request = enrich_request(request, translation_bible=bible)
    review_payload = reviewer_request_payload(
        provider_request,
        raw,
        deterministic_qa_summary={
            "passed": bool(qa_record and qa_record.get("passed")),
            "codes": [],
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
        },
        semantic_context=semantic_context,
    )
    diagnostic = getattr(error, "reviewer_contract_diagnostic", {})
    if not isinstance(diagnostic, Mapping):
        diagnostic = {}
    key = f"{request.display_id}.attempt_{(storage_attempt or attempt):02d}"
    path = checkpoint_dir / "reviewer_contract_failures" / f"{key}.json"
    persist_stage_once(
        checkpoint_dir,
        "reviewer_contract_failures",
        key,
        {
            "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_FAILURE",
            "schema_version": 1,
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "candidate_binding_sha256": sha256(raw.model_dump_json().encode("utf-8")).hexdigest(),
            "request_binding_sha256": provider_request.request_binding_sha256,
            "review_request_sha256": diagnostic.get("review_request_sha256") or _canonical_sha256(review_payload),
            "semantic_context_sha256": _canonical_sha256(dict(semantic_context or {})),
            "provider": diagnostic.get("provider") or provider_name,
            "model": diagnostic.get("model") or model_name,
            "contract_error_code": error.code,
            "response_sha256": diagnostic.get("response_sha256"),
            "response_id": diagnostic.get("response_id"),
            "response_hash_available": bool(diagnostic.get("response_sha256")),
            "provider_attempt_count": diagnostic.get("provider_attempt_count"),
            "reviewer_contract_attempt": attempt,
            "requested_at": diagnostic.get("requested_at"),
            "responded_at": diagnostic.get("responded_at"),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "invalid_response_persisted_as_review": False,
            "failure_evidence_source": "WORKER_LOG_RECONSTRUCTION" if reconstructed_from_worker_log else "PROVIDER_CONTRACT_DIAGNOSTIC",
        },
    )
    return path


def _repair_contract_failure_paths(checkpoint_dir: Path, display_id: str) -> list[Path]:
    folder = checkpoint_dir / "repair_contract_failures"
    return sorted(folder.glob(f"{display_id}.attempt_*.json")) if folder.is_dir() else []


def _repair_scope_and_issue_sha(request, raw: TranslationResult, review: ReviewResult, *, bible) -> tuple[str, str]:
    """Bind retry reuse to the exact current candidate, issue, and allowlist."""
    provider_request = enrich_request(request, translation_bible=bible)
    gate = decide_approved_repair_contract(provider_request, raw, qa_codes=(), review=review, allow_semantic_narrow=True)
    if gate.action != "REPAIR" or gate.contract is None:
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_ISSUE_SCOPE_NOT_COVERED")
    issue_sha = _canonical_sha256({"verdict": review.verdict, "issues": [issue.model_dump() for issue in review.issues]})
    return issue_sha, _canonical_sha256(gate.contract.model_dump())


def _matching_repair_contract_failure_paths(
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    review: ReviewResult,
    *,
    bible,
) -> list[Path]:
    provider_request = enrich_request(request, translation_bible=bible)
    issue_sha, scope_sha = _repair_scope_and_issue_sha(request, raw, review, bible=bible)
    matched: list[Path] = []
    for path in _repair_contract_failure_paths(checkpoint_dir, request.display_id):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            record.get("unit_uid") == request.unit_uid
            and record.get("candidate_binding_sha256") == _candidate_binding(raw)
            and record.get("request_binding_sha256") == provider_request.request_binding_sha256
            and record.get("issue_identity_sha256") == issue_sha
            and record.get("allowed_repair_scope_sha256") == scope_sha
        ):
            matched.append(path)
    return matched


def _persist_repair_contract_failure(
    checkpoint_dir: Path,
    *,
    request,
    raw: TranslationResult,
    review: ReviewResult,
    bible,
    error: ProviderContractGap,
    attempt: int,
    reconstructed_from_unit_result: bool = False,
) -> Path:
    provider_request = enrich_request(request, translation_bible=bible)
    issue_sha, scope_sha = _repair_scope_and_issue_sha(request, raw, review, bible=bible)
    diagnostic = getattr(error, "repair_contract_diagnostic", {})
    diagnostic = diagnostic if isinstance(diagnostic, Mapping) else {}
    key = f"{request.display_id}.attempt_{attempt:02d}"
    path = checkpoint_dir / "repair_contract_failures" / f"{key}.json"
    persist_stage_once(
        checkpoint_dir, "repair_contract_failures", key,
        {
            "artifact_type": "PRODUCTION_REPAIR_CONTRACT_FAILURE", "schema_version": 1,
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            "candidate_binding_sha256": _candidate_binding(raw),
            "request_binding_sha256": provider_request.request_binding_sha256,
            "issue_identity_sha256": issue_sha, "allowed_repair_scope_sha256": scope_sha,
            "semantic_group_hash": None,
            "contract_error_code": error.code,
            "repair_request_sha256": diagnostic.get("repair_request_sha256"),
            "response_sha256": diagnostic.get("response_sha256"),
            "response_id": diagnostic.get("response_id"),
            "provider": diagnostic.get("provider"), "model": diagnostic.get("model"),
            "provider_attempt_count": diagnostic.get("provider_attempt_count"),
            "requested_at": diagnostic.get("requested_at"), "responded_at": diagnostic.get("responded_at"),
            "repair_contract_attempt": attempt,
            "rejected_repair_response": diagnostic.get("response_text"),
            "response_payload_available": isinstance(diagnostic.get("response_text"), str),
            "failure_evidence_source": "UNIT_RESULT_RECONSTRUCTION" if reconstructed_from_unit_result else "PROVIDER_CONTRACT_DIAGNOSTIC",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return path


def _repair_contract_failure_outcome(request, raw: TranslationResult, qa_record: Mapping[str, Any], review: ReviewResult) -> UnitOutcome:
    return UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa=dict(qa_record),
        review=review, final_status="HUMAN_REQUIRED", failure_family="REPAIR_EXECUTION_FAILURE",
        failure_code=REPAIR_CONTRACT_INVALID_AFTER_RETRY, human_required=True,
        recovered_from="repair_contract_debt",
    )


def _process_with_bounded_repair_contract_retry(
    *, checkpoint_dir: Path, request, raw: TranslationResult, active: WorkerProviders,
    view, known_unit_ids, qa, bible, semantic_context, review_policy: str,
    outcome: UnitOutcome,
    initial_failure_already_persisted: bool = False,
) -> UnitOutcome:
    """Retry one invalid repair proposal without reusing or widening its scope."""
    if not (outcome.failure_code or "").startswith("REPAIR_CONTRACT_GAP_"):
        return outcome
    review = outcome.review
    if review is None or review.verdict != "REPAIR":
        return outcome
    try:
        failures = _matching_repair_contract_failure_paths(checkpoint_dir, request, raw, review, bible=bible)
    except ProviderContractGap:
        return outcome
    prior = len(failures)
    if prior >= MAX_REPAIR_CONTRACT_ATTEMPTS:
        return _repair_contract_failure_outcome(request, raw, qa(view, request, raw, known_unit_ids), review)
    if not initial_failure_already_persisted:
        observed = outcome.repair_contract_error
        error = observed if isinstance(observed, ProviderContractGap) else ProviderContractGap(
            outcome.failure_code or "REPAIR_CONTRACT_GAP_RESPONSE_SHAPE_INVALID"
        )
        _persist_repair_contract_failure(
            checkpoint_dir, request=request, raw=raw, review=review, bible=bible,
            error=error, attempt=prior + 1,
        )
        prior += 1
    if prior >= MAX_REPAIR_CONTRACT_ATTEMPTS:
        return _repair_contract_failure_outcome(request, raw, qa(view, request, raw, known_unit_ids), review)
    retried = process_unit(
        request, active, view=view, known_unit_ids=known_unit_ids, qa_fn=qa,
        translation_bible=bible, existing_raw=raw, semantic_context=semantic_context,
        review_policy=review_policy, repair_protocol_retry=True, existing_review=review,
    )
    if not (retried.failure_code or "").startswith("REPAIR_CONTRACT_GAP_"):
        return retried
    observed = retried.repair_contract_error
    error = observed if isinstance(observed, ProviderContractGap) else ProviderContractGap(
        retried.failure_code or "REPAIR_CONTRACT_GAP_RESPONSE_SHAPE_INVALID"
    )
    _persist_repair_contract_failure(
        checkpoint_dir, request=request, raw=raw, review=review, bible=bible,
        error=error, attempt=prior + 1,
    )
    return _repair_contract_failure_outcome(request, raw, qa(view, request, raw, known_unit_ids), review)


def _process_with_bounded_reviewer_contract_retry(
    *,
    checkpoint_dir: Path,
    request,
    raw: TranslationResult,
    active: WorkerProviders,
    view,
    known_unit_ids,
    qa,
    bible,
    semantic_context: Mapping[str, Any] | None,
    existing_candidate=None,
    review_policy: str = "legacy",
) -> tuple[UnitOutcome | None, Path | None]:
    """Isolate invalid reviewer wire responses from semantic policy.

    Exactly two task-bound reviewer attempts are permitted. The second is a
    protocol retry only; it reuses the same RAW candidate, request, and
    semantic context. Exhaustion returns an execution failure, never a
    partially valid reviewer verdict.
    """
    matching_failures = _matching_reviewer_contract_failure_paths(
        checkpoint_dir, request, raw, bible=bible, semantic_context=semantic_context,
    )
    prior = len(matching_failures)
    if prior >= MAX_REVIEWER_CONTRACT_ATTEMPTS:
        qa_record = qa(view, request, raw, known_unit_ids)
        return (
            UnitOutcome(
                request=request, decision=route_semantic_unit(request), raw=raw,
                working=raw, qa=qa_record, final_status="HUMAN_REQUIRED",
                failure_family="REVIEWER_EXECUTION_FAILURE",
                failure_code=REVIEWER_CONTRACT_INVALID_AFTER_RETRY,
                human_required=True, recovered_from="reviewer_contract_debt",
            ),
            matching_failures[-1],
        )
    while prior < MAX_REVIEWER_CONTRACT_ATTEMPTS:
        storage_attempt = max(
            len(_reviewer_contract_failure_paths(checkpoint_dir, request.display_id)) + 1,
            len(_reviewer_envelope_paths(checkpoint_dir, request.display_id)) + 1,
        )
        attempt = storage_attempt
        _persist_reviewer_envelope(
            checkpoint_dir,
            request,
            raw,
            bible=bible,
            semantic_context=semantic_context,
            reviewer=active.reviewer,
            attempt=attempt,
        )
        try:
            produced = process_unit(
                request,
                active,
                view=view,
                known_unit_ids=known_unit_ids,
                qa_fn=qa,
                translation_bible=bible,
                existing_raw=raw,
                existing_candidate=existing_candidate,
                semantic_context=semantic_context,
                review_policy=review_policy,
                reviewer_protocol_retry=prior > 0,
            )
            return (
                _process_with_bounded_repair_contract_retry(
                    checkpoint_dir=checkpoint_dir, request=request, raw=raw, active=active,
                    view=view, known_unit_ids=known_unit_ids, qa=qa, bible=bible,
                    semantic_context=semantic_context, review_policy=review_policy, outcome=produced,
                ),
                None,
            )
        except ProviderContractGap as error:
            if not error.code.startswith("REVIEWER_CONTRACT_GAP_"):
                raise
            prior += 1
            evidence = _persist_reviewer_contract_failure(
                checkpoint_dir,
                request=request,
                raw=raw,
                bible=bible,
                semantic_context=semantic_context,
                qa_record=qa(view, request, raw, known_unit_ids),
                error=error,
                attempt=prior,
                storage_attempt=storage_attempt,
            )
            if prior >= MAX_REVIEWER_CONTRACT_ATTEMPTS:
                qa_record = qa(view, request, raw, known_unit_ids)
                return (
                    UnitOutcome(
                        request=request, decision=route_semantic_unit(request), raw=raw,
                        working=raw, qa=qa_record, final_status="HUMAN_REQUIRED",
                        failure_family="REVIEWER_EXECUTION_FAILURE",
                        failure_code=REVIEWER_CONTRACT_INVALID_AFTER_RETRY,
                        human_required=True, recovered_from="reviewer_contract_debt",
                    ),
                    evidence,
                )
            # The retry uses the same immutable RAW and exact reviewer context.
            existing_candidate = None
    raise RuntimeError("REVIEWER_CONTRACT_RETRY_LOOP_EXHAUSTED")
    persist_stage_once(
        checkpoint_dir,
        "reviewer_payloads",
        request.display_id,
        {
            "artifact_type": "PRODUCTION_REVIEWER_PAYLOAD",
            "schema_version": 1,
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "payload": payload,
        },
    )


def run_production_checkpoint(
    *,
    root: Path,
    run: Path,
    worker_id: str,
    handoff_path: Path,
    authorize: bool,
    providers: WorkerProviders | None = None,
    payload: Mapping[str, Any] | None = None,
    semantic_rescue: bool = False,
    human_adjudication_path: Path | None = None,
    d051_approval_path: Path | None = None,
    d051_reuse_approval_path: Path | None = None,
    d051_reuse_display_id: str | None = None,
    review_policy: str = "legacy",
    qa_fn=None,
    seal_generation: Mapping[str, Any] | None = None,
    launch_batch_label: str | None = None,
    launch_checkpoint: str | None = None,
) -> int:
    """Seal the current authoritative checkpoint with append-once RAW."""
    from booktrans.production_worker.worker import (
        current_batch_checkpoint_inputs,
        frozen_batch_checkpoint_inputs,
        providers_from_environment,
    )

    if not authorize:
        raise FrozenSelectionError("PRODUCTION_CHECKPOINT_WRITE_NOT_AUTHORIZED")
    state = load_json(run / "production_campaign_state.json")
    checkpoint = str(state.get("next_checkpoint") or "")
    active_batch = str(state.get("active_batch") or "")
    if not re.fullmatch(r"FULL_BOOK_BATCH_\d{3}", active_batch):
        return _blocked(
            handoff_path,
            worker_id,
            checkpoint=checkpoint,
            classification="CURSOR_MISMATCH",
            extra={"active_batch": active_batch},
            active_batch=active_batch or None,
        )
    # An explicitly launched worker may never silently fall back to a legacy
    # batch/checkpoint.  Reject the invocation before the writer lock, RAW,
    # candidate authority, or checkpoint state can be touched.
    if launch_batch_label is not None and launch_batch_label != active_batch:
        return _blocked(
            handoff_path, worker_id, checkpoint=checkpoint,
            classification="WORKER_LAUNCH_BATCH_MISMATCH",
            extra={"launch_batch_label": launch_batch_label, "resolved_batch_label": active_batch},
            active_batch=active_batch,
        )
    if launch_checkpoint is not None and launch_checkpoint != checkpoint:
        return _blocked(
            handoff_path, worker_id, checkpoint=checkpoint,
            classification="WORKER_LAUNCH_CHECKPOINT_MISMATCH",
            extra={"launch_checkpoint": launch_checkpoint, "resolved_checkpoint": checkpoint},
            active_batch=active_batch,
        )
    try:
        ordinal = checkpoint_number(checkpoint)
    except FrozenSelectionError:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CURSOR_MISMATCH", active_batch=active_batch)
    expected_completed = (ordinal - 1) * CHECKPOINT_SIZE
    if int(state.get("completed_count", -1)) != expected_completed:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CURSOR_MISMATCH", active_batch=active_batch)
    checkpoint_dir = production_checkpoint_path(run, active_batch, checkpoint)
    seal_dir = checkpoint_dir
    if seal_generation is not None:
        relative = seal_generation.get("path")
        generation = seal_generation.get("generation")
        if not isinstance(relative, str) or not isinstance(generation, int) or generation < 1:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CHECKPOINT_SEAL_GENERATION_INVALID", active_batch=active_batch)
        seal_dir = (checkpoint_dir / relative).resolve()
        if checkpoint_dir.resolve() not in seal_dir.parents:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CHECKPOINT_SEAL_GENERATION_PATH_ESCAPE", active_batch=active_batch)
    try:
        manifest_path, manifest = resolve_authoritative_manifest(checkpoint_dir.parent)
    except (OSError, ValueError, json.JSONDecodeError):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="ACTIVE_BATCH_MANIFEST_INVALID", active_batch=active_batch)
    selection_path = run / f"{checkpoint_dir.parent.name}_selection.json"
    if not selection_path.is_file():
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="ACTIVE_BATCH_ARTIFACT_MISSING", active_batch=active_batch)
    if manifest.get("batch") != active_batch:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="ACTIVE_BATCH_MANIFEST_MISMATCH", active_batch=active_batch)
    try:
        selection = load_json(selection_path)
        selection_ids = selection.get("selected_logical_work_order_ids")
        target_count = manifest.get("target_count")
        if (
            not isinstance(selection_ids, list)
            or not all(isinstance(item, str) and item for item in selection_ids)
            or not isinstance(target_count, int)
            or target_count != len(selection_ids)
            or len(selection_ids) != len(set(selection_ids))
            or ordinal > checkpoint_count_for_target(target_count)
        ):
            raise FrozenSelectionError("CHECKPOINT_TARGET_OR_SELECTION_INVALID")
    except (OSError, ValueError, json.JSONDecodeError, FrozenSelectionError):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="ACTIVE_BATCH_SELECTION_INVALID", active_batch=active_batch)
    sealed_ids = {item.get("id") for item in manifest.get("checkpoints") or [] if isinstance(item, dict)}
    if checkpoint in sealed_ids:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CHECKPOINT_ALREADY_SEALED", active_batch=active_batch)
    if (manifest.get("completed_count"), manifest.get("next_checkpoint")) != (expected_completed, checkpoint):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CHECKPOINT_RESUME_MISMATCH", active_batch=active_batch)
    try:
        loaded = dict(payload) if payload is not None else frozen_batch_checkpoint_inputs(run, batch_label=active_batch, checkpoint=checkpoint)
    except (FrozenSelectionError, OSError, json.JSONDecodeError):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="ACTIVE_BATCH_SELECTION_INVALID", active_batch=active_batch)
    if str(loaded.get("batch_label") or active_batch) != active_batch:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="ACTIVE_BATCH_SELECTION_MISMATCH", active_batch=active_batch)
    if payload is None:
        source_docx = root / "Software Engineering at Google.docx"
        source_pdf = root / "Software Engineering at Google.pdf"
        try:
            ensure_checkpoint_d050_preflight(
                run=run,
                checkpoint=checkpoint,
                order=list(loaded["order"]),
                views=loaded["views"],
                source_docx=source_docx,
                source_pdf=source_pdf,
                source_sha256=str(loaded["source_sha256"]),
                source_pdf_sha256="4e567ce8f561cad28c0369df71a25cb2b64361473c64509c493cce79e293289e",
                batch_label=active_batch,
            )
            # Re-load only after the create-once registry has passed exact
            # source/PDF verification, so all downstream QA sees its overlay.
            loaded = frozen_batch_checkpoint_inputs(run, batch_label=active_batch, checkpoint=checkpoint)
        except D050PreflightError as exc:
            return _blocked(
                handoff_path,
                worker_id,
                checkpoint=checkpoint,
                classification="D050_PREFLIGHT_HARD_BLOCK",
                extra={"d050_error": str(exc)},
                active_batch=active_batch,
            )
    if list(loaded.get("order") or [None])[0] != state.get("next_logical_work_order_id"):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CURSOR_MISMATCH", active_batch=active_batch)
    # Frozen boundaries are derived from manifest target + selection + size.
    # Only the terminal derived checkpoint may contain fewer than 25 members.
    expected = len(loaded["order"])
    if payload is None and not validate_frozen_checkpoint_members(
        target_count=target_count, selection_count=len(selection_ids),
        checkpoint=checkpoint, member_count=expected,
    ):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CHECKPOINT_SELECTION_INVALID", active_batch=active_batch)
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    active = providers or providers_from_environment()
    qa = qa_fn or harness_qa_record
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    # The historical request is always retained as immutable evidence.  A
    # narrowly-authorized, pre-seal Harness correction may add one append-only
    # derived request lineage; execution may use only its fail-closed terminal.
    historical_request_path = checkpoint_dir / "translation_request.jsonl"
    if not historical_request_path.exists():
        try:
            from booktrans.production_worker.worker import fresh_checkpoint_requests_from_current_view
            _, fresh_requests = fresh_checkpoint_requests_from_current_view(
                run, batch_label=active_batch, checkpoint=checkpoint,
                frozen_inputs=loaded,
            )
            ensure_requests_artifact(historical_request_path, fresh_requests)
        except Exception as exc:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint,
                            classification="FRESH_REQUEST_CURRENT_VIEW_RESOLUTION_FAILED",
                            extra={"fresh_request_error": str(exc)}, active_batch=active_batch)
    manifest_path = run / active_batch.lower() / "batch_manifest.json"
    try:
        # Current execution has one coherent input boundary: the sole
        # effective-request resolver plus the matching derived view slice.
        # Historical frozen views remain audit evidence inside that helper.
        loaded = current_batch_checkpoint_inputs(
            run, batch_label=active_batch, checkpoint=checkpoint,
        )
        effective_request_path = loaded["effective_translation_request_path"]
        effective_requests = loaded["requests"]
        # Current execution validates its resolved effective request, never
        # a historical base request against a separately reloaded frozen view.
        # The latter is immutable audit evidence and can legitimately carry a
        # topology that the current-view boundary has already superseded.
        ensure_requests_artifact(effective_request_path, effective_requests)
    except Exception as exc:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint,
                        classification="EFFECTIVE_REQUEST_RESOLUTION_FAILED",
                        extra={"effective_request_error": str(exc)}, active_batch=active_batch)
    loaded["requests"] = effective_requests
    loaded["effective_translation_request_path"] = effective_request_path
    # Acquisition identity is the route-bound provider request, including the
    # structured route's output-contract version.  Do not substitute the
    # generic enrichment fingerprint here.
    request_bindings = {
        request.unit_uid: _provider_request_for_decision(
            request, bible=bible, decision=route_semantic_unit(request)
        ).request_binding_sha256
        for request in loaded["requests"]
    }
    try:
        existing_raw, historical_raw_uids = resolve_effective_provider_acquisition(
            checkpoint_dir=checkpoint_dir, requests=loaded["requests"], request_binding_by_uid=request_bindings,
            source_sha256=str(loaded["source_sha256"]), batch_id=active_batch, checkpoint_id=checkpoint,
            selection_sha256=str(loaded["selection_sha256"]), manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
            effective_request_artifact_sha256=sha256(effective_request_path.read_bytes()).hexdigest(),
        )
    except Exception as exc:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint,
                        classification="EFFECTIVE_PROVIDER_ACQUISITION_RESOLUTION_FAILED",
                        extra={"effective_provider_acquisition_error": str(exc)}, active_batch=active_batch)
    # A fresh checkpoint is already CURRENT execution once its effective
    # request is resolved, but it has no provider acquisitions yet.  Acquire
    # only the missing exact-bound rows before constructing CurrentRawQaPayload;
    # QA still fails closed below if the set is incomplete after this phase.
    effective_request_sha256 = sha256(effective_request_path.read_bytes()).hexdigest()
    manifest_sha256 = sha256(manifest_path.read_bytes()).hexdigest()
    for request in _missing_effective_acquisitions(loaded["requests"], existing_raw):
        decision = route_semantic_unit(request)
        try:
            candidate, raw, provider_contract_evidence = _translate_with_bounded_provider_contract_retry(
                checkpoint_dir=checkpoint_dir, run=run, request=request, bible=bible,
                active=active, decision=decision,
            )
        except Exception as error:
            from booktrans.providers.failures import classify_provider_exception

            classified = classify_provider_exception(error)
            print(f"{classified.family} {classified.code}", flush=True)
            if classified.family == "PROVIDER_TRANSIENT":
                return 5
            return _blocked(
                handoff_path, worker_id, checkpoint=checkpoint,
                classification=classified.family,
                extra={"provider_code": classified.code, "display_id": request.display_id},
                active_batch=active_batch,
            )
        if raw is None or candidate is None:
            terminal_evidence = _persist_no_safe_authoritative_candidate_terminal(
                checkpoint_dir, request=request, bible=bible, decision=decision,
                provider_contract_evidence=provider_contract_evidence,
            )
            return _blocked(
                handoff_path, worker_id, checkpoint=checkpoint,
                classification="NO_SAFE_AUTHORITATIVE_CANDIDATE",
                extra={"display_id": request.display_id, "evidence": str(terminal_evidence)},
                active_batch=active_batch,
            )
        provenance = candidate_provenance(candidate, decision)
        if request.unit_uid in historical_raw_uids:
            persist_request_bound_provider_reacquisition(
                checkpoint_dir=checkpoint_dir, request=request,
                request_binding_sha256=request_bindings[request.unit_uid],
                effective_request_artifact_sha256=effective_request_sha256,
                source_sha256=str(loaded["source_sha256"]), batch_id=active_batch,
                checkpoint_id=checkpoint, selection_sha256=str(loaded["selection_sha256"]),
                manifest_sha256=manifest_sha256, raw=raw, provenance=provenance,
            )
        else:
            append_raw_response_line(checkpoint_dir / "translation_raw_response.jsonl", raw)
            append_jsonl_fsync(checkpoint_dir / "translation_provider_provenance.jsonl", provenance)
    try:
        existing_raw, historical_raw_uids = resolve_effective_provider_acquisition(
            checkpoint_dir=checkpoint_dir, requests=loaded["requests"], request_binding_by_uid=request_bindings,
            source_sha256=str(loaded["source_sha256"]), batch_id=active_batch, checkpoint_id=checkpoint,
            selection_sha256=str(loaded["selection_sha256"]), manifest_sha256=manifest_sha256,
            effective_request_artifact_sha256=effective_request_sha256,
        )
    except Exception as exc:
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint,
                        classification="EFFECTIVE_PROVIDER_ACQUISITION_RESOLUTION_FAILED",
                        extra={"effective_provider_acquisition_error": str(exc)}, active_batch=active_batch)
    if _missing_effective_acquisitions(loaded["requests"], existing_raw):
        return _blocked(handoff_path, worker_id, checkpoint=checkpoint,
                        classification="CURRENT_RAW_QA_ACQUISITION_SET_INCOMPLETE", active_batch=active_batch)
    base_raw_for_current_qa = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl")
    current_raw_qa_payload = _current_raw_qa_payload(
        requests=loaded["requests"], acquisitions=existing_raw, base_acquisitions=base_raw_for_current_qa,
        views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa=qa,
        source_sha256=str(loaded["source_sha256"]), batch_id=active_batch, checkpoint_id=checkpoint,
        selection_sha256=str(loaded["selection_sha256"]), manifest_sha256=manifest_sha256,
        effective_request_sha256=effective_request_sha256,
        request_binding_by_uid=request_bindings,
        historical_raw_qa_path=checkpoint_dir / "translation_raw_qa_report.json",
    )
    loaded["current_raw_qa_payload"] = current_raw_qa_payload
    # The canonical projector is the sole policy owner for current recovery.
    # It returns at most one frozen-order obligation; execution below only
    # validates and dispatches that contract, rather than inspecting legacy
    # rescue history to choose recovery policy again.
    selected_current_recovery: Mapping[str, Any] | None = None
    if semantic_rescue:
        try:
            from booktrans.production_worker.continuation import project_current_canonical_recovery

            selected_current_recovery = project_current_canonical_recovery(
                output_dir=checkpoint_dir,
                requests=loaded["requests"],
                production_run=run,
                views=loaded["views"],
                known_unit_ids=loaded["known_unit_ids"],
                qa_fn=qa,
                frozen_order=selection_ids,
                selection_sha256=str(loaded["selection_sha256"]),
                source_sha256=str(loaded["source_sha256"]),
                active_batch=active_batch,
                checkpoint=checkpoint,
                translation_bible=bible,
                d050_registry=loaded.get("d050_registry"),
            )
        except Exception as exc:
            return _blocked(
                handoff_path, worker_id, checkpoint=checkpoint,
                classification="CURRENT_RECOVERY_PROJECTION_FAILED",
                extra={"current_recovery_projection_error": str(exc)}, active_batch=active_batch,
            )
    # ``current_batch_checkpoint_inputs``, effective acquisition, and the
    # transient RAW-QA payload have now all resolved.  Per-unit Candidate
    # Authority is resolved inside the loop below; if it cannot resolve, that
    # unit fails closed rather than entering compatibility recovery.
    current_mode = True
    stored_units = load_unit_results(checkpoint_dir)
    if d051_reuse_approval_path is not None:
        display_id = str(d051_reuse_display_id or "")
        request_by_id = {item.display_id: item for item in loaded["requests"]}
        request = request_by_id.get(display_id)
        raw = None if request is None else existing_raw.get(request.unit_uid)
        if request is None or raw is None:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_REUSE_BINDING_MISSING", active_batch=active_batch)
        try:
            head = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raw)
            reuse_record = _reuse_exact_d051_equivalence(
                approved=load_json(d051_reuse_approval_path), approved_path=d051_reuse_approval_path,
                request=request, candidate=head.candidate, bible=bible,
            )
        except (CandidateAuthorityError, ValueError, OSError, json.JSONDecodeError):
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_REUSE_NOT_EXACT", active_batch=active_batch)
        persist_stage_once(checkpoint_dir, "d051_reviewed_equivalences", display_id, reuse_record)
    if d051_approval_path is not None:
        approval = load_json(d051_approval_path)
        display_id = str(approval.get("display_id") or "")
        request_by_id = {item.display_id: item for item in loaded["requests"]}
        request = request_by_id.get(display_id)
        raw = None if request is None else existing_raw.get(request.unit_uid)
        if request is None or raw is None:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_APPROVAL_BINDING_MISSING", active_batch=active_batch)
        try:
            candidate = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raw).candidate
        except CandidateAuthorityError:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_APPROVAL_BINDING_MISMATCH", active_batch=active_batch)
        if (
            approval.get("artifact_type") != "D051_REVIEWED_EQUIVALENCE_APPROVAL"
            or approval.get("source_sha256") != request.source_sha256
            or approval.get("unit_uid") != request.unit_uid
            or approval.get("request_binding_sha256") != enrich_request(request, translation_bible=bible).request_binding_sha256
            or approval.get("candidate_binding_sha256") != _candidate_binding(candidate)
        ):
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_APPROVAL_BINDING_MISMATCH", active_batch=active_batch)
        try:
            equivalence = ExplicitNumericRepresentationEquivalence.model_validate(approval.get("equivalence"))
        except Exception:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_APPROVAL_INVALID", active_batch=active_batch)
        if equivalence.unit_uid != request.unit_uid:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D051_APPROVAL_BINDING_MISMATCH", active_batch=active_batch)
        persist_stage_once(checkpoint_dir, "d051_reviewed_equivalences", display_id, {"artifact_type": "D051_REVIEWED_EQUIVALENCE", "schema_version": 1, **approval})
    if human_adjudication_path is not None:
        from booktrans.production_worker.continuation import _binding
        from booktrans.production_worker.human_adjudication import adjudication_from_mapping, apply_human_adjudication_to_production_checkpoint

        adjudication = adjudication_from_mapping(load_json(human_adjudication_path))
        current_id = adjudication.member_work_order_ids[-1]
        if load_stage(checkpoint_dir, "human_adjudications", current_id) is None:
            request_by_id = {item.display_id: item for item in loaded["requests"]}
            current = request_by_id.get(current_id)
            if current is None:
                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="HUMAN_ADJUDICATION_CURRENT_UNIT_MISSING", active_batch=active_batch)
            group = _production_review_group(current, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
            if group is None:
                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="HUMAN_ADJUDICATION_GROUP_MISSING", active_batch=active_batch)
            members = [(_binding(request_by_id[item.display_id], bible), existing_raw[item.unit_uid]) for item in group.members]
            try:
                apply_human_adjudication_to_production_checkpoint(
                    checkpoint_dir=checkpoint_dir, production_run=run, requests=loaded["requests"], views=loaded["views"],
                    known_unit_ids=loaded["known_unit_ids"], qa_fn=qa, adjudication=adjudication, group=group,
                    members=members, translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
                    batch_label=active_batch, checkpoint=checkpoint,
                )
            except Exception as error:
                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification=getattr(error, "code", type(error).__name__), active_batch=active_batch)
    outcomes: list[UnitOutcome] = []
    # A candidate mutation changes the sole authority while this worker still
    # holds the selection contract captured at entry.  The resulting outcome
    # is evidence for the next checkpoint projection, never permission to
    # schedule another corrective step from this invocation's old contract.
    authoritative_transitions: set[str] = set()
    for request in loaded["requests"]:
        stored = stored_units.get(request.display_id)
        raw = existing_raw.get(request.unit_uid)
        if stored is not None and raw is not None:
            try:
                _assert_accepted_candidate_mutation_binding(
                    checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                    source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                )
            except CandidateAuthorityError:
                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
            # A crash after a bounded RAW reprocess but before the new common
            # mutation commit must resume from its durable result, not repeat
            # repair or fall back to the obsolete unit working field.
            if load_stage(checkpoint_dir, "accepted_candidate_mutations", request.display_id) is None:
                reprocess = load_stage(checkpoint_dir, "production_reprocess", request.display_id)
                if isinstance(reprocess, Mapping) and isinstance(reprocess.get("working"), Mapping):
                    reprocessed = _reconstruct_outcome(request, reprocess, raw)
                    try:
                        _commit_accepted_candidate_mutation(
                            checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=reprocessed,
                            source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                        )
                    except CandidateAuthorityError:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
            merged = _merge_latest_unit_record(checkpoint_dir, request.display_id, stored)
            try:
                resolved_candidate = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, request.display_id, raw)
            except CandidateAuthorityError:
                return _blocked(
                    handoff_path, worker_id, checkpoint=checkpoint,
                    classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch,
                )
            gateway_review = _current_candidate_rereview(
                checkpoint_dir=checkpoint_dir, request=request,
                current=resolved_candidate.candidate,
            )
            gateway_family = _selected_current_recovery_execution_family(
                selected_current_recovery,
                request=request,
                candidate_sha256=resolved_candidate.candidate_sha256,
                review=gateway_review,
            )
            # This is the sole CURRENT/LEGACY boundary for the early per-unit
            # recovery family.  Historical rescue records remain available to
            # Candidate Authority and audit, but only the canonical gateway
            # may grant execution authority to that family in CURRENT mode.
            current_family_execution = gateway_family
            selected_ids = (
                selected_current_recovery.get("failing_work_order_ids")
                if isinstance(selected_current_recovery, Mapping) else None
            )
            if selected_ids == [request.display_id] and gateway_family is None:
                # The projection's selected contract became stale during this
                # invocation.  Do not fall back into historical recovery
                # policy; a later invocation must reproject from evidence.
                return _blocked(
                    handoff_path, worker_id, checkpoint=checkpoint,
                    classification="CURRENT_RECOVERY_OBLIGATION_BINDING_STALE",
                    extra={"display_id": request.display_id}, active_batch=active_batch,
                )
            # A unit record and translation_raw_qa_report are immutable
            # history, not the QA authority for a resumed checkpoint.  Always
            # project the resolved current head through the one effective-QA
            # path, including D051.  Previously this ran only after a working
            # candidate mismatch, so an unchanged RAW head with a later Human
            # PRESERVE_CURRENT D051 terminal retained its stale RAW QA fail.
            resolved_equivalence = _d051_equivalence_for(
                checkpoint_dir, request, resolved_candidate.candidate, bible=bible,
            )
            merged = _project_resolved_unit_record(
                merged, checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                resolved_candidate=resolved_candidate, qa=qa,
                view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], bible=bible,
                current_raw_qa=loaded["current_raw_qa_payload"],
            )
            control = _project_current_unit_control_state(
                checkpoint_dir=checkpoint_dir, request=request, raw=raw, projected=merged,
            )
            # One control boundary: retain merged history, but never let its
            # historical terminal fields steer the resumed checkpoint.
            merged["semantic_terminal"] = control.semantic_terminal
            merged["human_required"] = control.human_required
            merged["final_status"] = control.final_status
            resolved_current_qa = merged["qa"]
            if resolved_equivalence is not None and load_stage(checkpoint_dir, "d051_qa", request.display_id) is None:
                persist_stage_once(checkpoint_dir, "d051_qa", request.display_id, {
                    "artifact_type": "D051_REUSED_DETERMINISTIC_QA", "schema_version": 1,
                    "qa": resolved_current_qa, "candidate_binding_sha256": resolved_candidate.candidate_sha256,
                    "translation_api_calls": 0,
                })
            # HumanSemanticAdjudication is the terminal semantic authority.
            # Once its create-once candidate has passed the resolver's parent
            # and binding checks, historical rescue/reviewer evidence may not
            # re-open this unit during a checkpoint resume.
            human = load_stage(checkpoint_dir, "human_approved_candidates", request.display_id)
            if (
                gateway_family is None
                and isinstance(human, Mapping)
                and human.get("semantic_terminal") == "HUMAN_APPROVED"
            ):
                try:
                    human_candidate = TranslationResult.model_validate(human.get("working"))
                except Exception:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="HUMAN_APPROVED_CANDIDATE_BINDING_MISMATCH", active_batch=active_batch)
                if _candidate_binding(human_candidate) != resolved_candidate.candidate_sha256:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="HUMAN_APPROVED_CANDIDATE_BINDING_MISMATCH", active_batch=active_batch)
                outcome = _reconstruct_outcome(request, merged, raw)
                outcome.working = human_candidate
                outcome.qa = human.get("qa") if isinstance(human.get("qa"), Mapping) else outcome.qa
                outcome.final_status = "HUMAN_APPROVED"
                outcome.semantic_terminal = "HUMAN_APPROVED"
                outcome.human_required = False
                outcome.repair_policy_id = "HUMAN_SEMANTIC_ADJUDICATION"
                outcome.recovered_from = "human_adjudication"
                outcomes.append(outcome)
                continue
            merged_working = merged.get("working")
            if not isinstance(merged_working, Mapping) or _candidate_binding(TranslationResult.model_validate(merged_working)) != resolved_candidate.candidate_sha256:
                # Unit-result fields are derived compatibility evidence, never
                # candidate authority.  Record the resolver result separately
                # and make every resumed QA/review/seal consumer use it.
                equivalence = _d051_equivalence_for(checkpoint_dir, request, resolved_candidate.candidate, bible=bible)
                current_qa = _qa_with_d051(
                    qa, loaded["views"][request.display_id], request, resolved_candidate.candidate,
                    loaded["known_unit_ids"], equivalence,
                )
                if equivalence is not None and load_stage(checkpoint_dir, "d051_qa", request.display_id) is None:
                    persist_stage_once(checkpoint_dir, "d051_qa", request.display_id, {
                        "artifact_type": "D051_REUSED_DETERMINISTIC_QA", "schema_version": 1,
                        "qa": current_qa, "candidate_binding_sha256": resolved_candidate.candidate_sha256,
                        "translation_api_calls": 0,
                    })
                if load_stage(checkpoint_dir, "authoritative_candidate_states", request.display_id) is None:
                    persist_stage_once(checkpoint_dir, "authoritative_candidate_states", request.display_id, {
                        "artifact_type": "PRODUCTION_AUTHORITATIVE_CANDIDATE_STATE", "schema_version": 1,
                        "display_id": request.display_id, "unit_uid": request.unit_uid,
                        "candidate_binding_sha256": resolved_candidate.candidate_sha256,
                        "working": resolved_candidate.candidate.model_dump(), "qa": current_qa,
                        "lineage": list(resolved_candidate.lineage), "translation_api_calls": 0,
                    })
                merged["working"] = resolved_candidate.candidate.model_dump()
                merged["qa"] = current_qa
            # An accepted typed D050 head may have replaced an older terminal
            # rescue result.  Project its effective (possibly retried) final
            # review before any resume terminal logic observes stale HUMAN.
            if any(item.get("artifact") == "d050_exact_marker_recovery_attempt" for item in resolved_candidate.lineage):
                d050_attempt = load_stage(checkpoint_dir, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id)
                effective_final = None
                if isinstance(d050_attempt, Mapping):
                    effective_final = _d050_effective_final_review(
                        checkpoint_dir=checkpoint_dir, display_id=request.display_id, unit_uid=request.unit_uid,
                        candidate_sha256=resolved_candidate.candidate_sha256,
                        request_binding_sha256=str(d050_attempt.get("request_binding_sha256") or ""),
                    )
                if effective_final is not None and current_qa.get("passed") and _d050_final_review_is_acceptable(effective_final):
                    merged["second_review"] = effective_final.model_dump()
                    merged["final_status"] = "ACCEPT"
                    merged["semantic_terminal"] = "MODEL_ACCEPTED"
                    merged["human_required"] = False
                    merged["recovered_from"] = "d050_exact_marker_recovery"
            legacy_unbound = any(item.get("status") == "LEGACY_UNBOUND_DERIVED" for item in resolved_candidate.lineage)
            # A unit-result working value from before the authority commit
            # rule is historical compatibility evidence only.  Treat it as an
            # unbound branch even when the resolver has selected RAW and
            # therefore has no mutation entry to enumerate.
            stored_working = stored.get("working")
            if (
                isinstance(stored_working, Mapping)
                and _candidate_binding(TranslationResult.model_validate(stored_working)) != resolved_candidate.candidate_sha256
                and load_stage(checkpoint_dir, "accepted_candidate_mutations", request.display_id) is None
                and not any(item.get("status") == "ACCEPTED_HEAD" for item in resolved_candidate.lineage)
            ):
                legacy_unbound = True
            # Exact D050 marker recovery is a current-head repair, not a
            # legacy-compatibility repair.  In particular, a clean RAW/ROOT
            # lineage can still have the one exact, verified visible marker
            # missing.  Keep this before the legacy branch so resume sees the
            # same eligibility as fresh processing.
            current_candidate = resolved_candidate.candidate
            current_equivalence = _d051_equivalence_for(checkpoint_dir, request, current_candidate, bible=bible)
            current_qa = _qa_with_d051(
                qa, loaded["views"][request.display_id], request, current_candidate,
                loaded["known_unit_ids"], current_equivalence,
            )
            # A persisted exact-current-head REPAIR is not a missing final
            # review.  It is the input to the bounded corrective executor
            # below, so the review-only branch must not consume this turn.
            current_head_repair = _current_head_corrective_review(
                checkpoint_dir=checkpoint_dir, request=request,
                candidate_sha256=_candidate_binding(current_candidate), bible=bible,
            )
            # An already-published exact numeric-anchor ownership correction
            # is its own deterministic terminal.  Its current QA binding is
            # the success proof; do not let a historical machine
            # HUMAN_REQUIRED projection request a new semantic pass before
            # this current recovery can be recognized.  The mutation helper
            # requires the exact RAW parent, one accepted descendant, and a
            # passing current head, so ambiguous/unrelated numeric failures
            # still reach the ordinary fail-closed paths below.
            raw_ownership_qa = _qa_with_d051(
                qa, loaded["views"][request.display_id], request, raw,
                loaded["known_unit_ids"], current_equivalence,
            )
            if _current_numeric_ownership_mutation_is_effective(
                checkpoint_dir=checkpoint_dir, request=request, raw=raw, current=current_candidate,
                raw_qa=raw_ownership_qa, current_qa=current_qa,
            ) and current_head_repair is None:
                outcomes.append(UnitOutcome(
                    request=request, decision=route_semantic_unit(request), raw=raw,
                    working=current_candidate, qa=current_qa, final_status="ACCEPT",
                    semantic_terminal="MODEL_ACCEPTED", repair_applied=True,
                    repair_policy_id="NUMERIC_ANCHOR_OWNERSHIP_CORRECTION",
                    recovered_from="existing_numeric_anchor_ownership_mutation",
                ))
                continue
            # Every candidate-changing authoritative head needs its own bound
            # semantic acceptance.  Historical reviews certify only their
            # exact candidate SHA; Core-clean current heads use the existing
            # corrective-final-review artifacts to obtain that acceptance.
            if (
                current_candidate != raw
                and current_qa.get("passed")
                and current_head_repair is None
                and _current_candidate_reviewer_accept(
                    checkpoint_dir=checkpoint_dir, request=request,
                    candidate_sha256=_candidate_binding(current_candidate), bible=bible,
                ) is None
            ):
                current_group = _production_review_group(
                    request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                    # The current CA descendant, not the historical RAW
                    # candidate map, is the object being bound for review.
                    candidate_overrides={
                        request.display_id: current_candidate,
                        request.unit_uid: current_candidate,
                    },
                )
                if current_group is None:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CURRENT_HEAD_FINAL_REVIEW_GROUP_MISSING", active_batch=active_batch)
                outcome = _rereview_stale_final_candidate(
                    checkpoint_dir=checkpoint_dir, request=request, candidate=current_candidate,
                    active=active, view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                    qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, current_equivalence),
                    bible=bible, group=current_group,
                    classification="CURRENT_HEAD_SEMANTIC_ACCEPTANCE_REQUIRED",
                    review_stage="semantic_rescue_corrective_final_reviews",
                    review_request_stage="semantic_rescue_corrective_final_review_requests",
                    authority_raw=raw,
                )
                outcomes.append(outcome)
                continue
            if current_head_repair is not None and current_family_execution == "SEMANTIC_CORRECTIVE":
                current_group = _production_review_group(
                    request, loaded=loaded, run=run, checkpoint=checkpoint,
                    existing_raw=existing_raw,
                    candidate_overrides={request.display_id: current_candidate, request.unit_uid: current_candidate},
                )
                scope = _current_rereview_corrective_scope(
                    request=request, candidate=current_candidate,
                    review=current_head_repair, group=current_group,
                )
                if scope is None:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CURRENT_HEAD_REPAIR_SCOPE_UNMAPPABLE", extra={"display_id": request.display_id}, active_batch=active_batch)
                saved_current_attempt = load_stage(
                    checkpoint_dir, "semantic_rescue_current_head_corrective", request.display_id,
                )
                if saved_current_attempt is not None and not _current_head_corrective_attempt_matches(
                    checkpoint_dir=checkpoint_dir, request=request,
                    parent_candidate_sha256=_candidate_binding(current_candidate),
                    review=current_head_repair, scope=scope, bible=bible,
                ):
                    return _blocked(
                        handoff_path, worker_id, checkpoint=checkpoint,
                        classification="CURRENT_HEAD_CORRECTIVE_IDENTITY_MISMATCH",
                        extra={"display_id": request.display_id}, active_batch=active_batch,
                    )
                # An episode owns one authoritative parent and may retain up
                # to two non-authoritative prepared repair rounds.  If round
                # one is Core-clean but its exact final review supplies a new,
                # legal semantic scope, repair that prepared draft directly.
                # It never becomes a CA head; only the eventual terminal
                # candidate is committed below against ``current_candidate``.
                round_one_repair = load_stage(
                    checkpoint_dir, "semantic_rescue_current_head_corrective_repairs", request.display_id,
                )
                round_one_final = load_stage(
                    checkpoint_dir, "semantic_rescue_current_head_corrective_final_reviews", request.display_id,
                )
                round_two_ready = (
                    isinstance(saved_current_attempt, Mapping)
                    and isinstance(round_one_repair, Mapping)
                    and isinstance(round_one_repair.get("working"), Mapping)
                    and isinstance(round_one_final, Mapping)
                    and isinstance(round_one_final.get("review"), Mapping)
                    and round_one_repair.get("parent_candidate_sha256") == _candidate_binding(current_candidate)
                )
                if round_two_ready:
                    try:
                        prepared_round_one = TranslationResult.model_validate(round_one_repair["working"])
                        prepared_sha = _candidate_binding(prepared_round_one)
                        follow_up_review = ReviewResult.model_validate(round_one_final["review"])
                    except (TypeError, ValueError):
                        prepared_round_one = None
                        follow_up_review = None
                        prepared_sha = None
                    expected_request = enrich_request(request, translation_bible=bible).request_binding_sha256
                    if (
                        prepared_round_one is not None
                        and follow_up_review is not None
                        and round_one_final.get("candidate_binding_sha256") == prepared_sha
                        and round_one_final.get("request_binding_sha256") == expected_request
                    ):
                        round_two_group = _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                            candidate_overrides={request.display_id: prepared_round_one, request.unit_uid: prepared_round_one},
                        )
                        round_two_scope = _current_rereview_corrective_scope(
                            request=request, candidate=prepared_round_one,
                            review=follow_up_review, group=round_two_group,
                        )
                        if round_two_scope is not None:
                            round_two_stage = CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX
                            saved_round_two = load_stage(checkpoint_dir, round_two_stage, request.display_id)
                            round_two_identity = {
                                "authoritative_parent_candidate_sha256": _candidate_binding(current_candidate),
                                "prepared_parent_candidate_sha256": prepared_sha,
                                "request_binding_sha256": expected_request,
                                "review_result_sha256": _canonical_sha256(follow_up_review.model_dump()),
                                "required_repair_scope": list(round_two_scope),
                                "round": MAX_SEMANTIC_CORRECTIVE_ROUNDS_PER_EPISODE,
                            }
                            if isinstance(saved_round_two, Mapping) and any(
                                saved_round_two.get(key) != value for key, value in round_two_identity.items()
                            ):
                                return _blocked(
                                    handoff_path, worker_id, checkpoint=checkpoint,
                                    classification="CURRENT_HEAD_CORRECTIVE_ROUND_2_IDENTITY_MISMATCH",
                                    extra={"display_id": request.display_id}, active_batch=active_batch,
                                )
                            def rebuild_round_two_group(repaired: TranslationResult):
                                overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                                overrides[request.display_id] = repaired
                                overrides[request.unit_uid] = repaired
                                return _production_review_group(
                                    request, loaded=loaded, run=run, checkpoint=checkpoint,
                                    existing_raw=existing_raw, candidate_overrides=overrides,
                                )
                            outcome = _run_semantic_rescue(
                                checkpoint_dir=checkpoint_dir, request=request, base=prepared_round_one,
                                active=active, view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                                qa=qa, bible=bible,
                                semantic_context=None if round_two_group is None else round_two_group.as_reviewer_context_payload(),
                                group=round_two_group, prior={"review": follow_up_review.model_dump()},
                                rebuild_group=rebuild_round_two_group, stage_prefix=round_two_stage,
                                corrective_diagnosis="; ".join(issue.reason for issue in follow_up_review.issues),
                                required_repair_scope=round_two_scope, authority_raw=raw,
                            )
                            if saved_round_two is None:
                                record = _rescue_record(
                                    outcome, mode="CURRENT_HEAD_SEMANTIC_CORRECTIVE_FOLLOW_UP",
                                    group=round_two_group, raw=prepared_round_one,
                                )
                                record.update(round_two_identity)
                                record["candidate_binding_sha256"] = prepared_sha
                                persist_stage_once(checkpoint_dir, round_two_stage, request.display_id, record)
                            try:
                                _commit_accepted_candidate_mutation(
                                    checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                                    source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]),
                                    bible=bible,
                                )
                            except CandidateAuthorityError:
                                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                            authoritative_transitions.add(request.display_id)
                            outcomes.append(outcome)
                            continue
                def rebuild_current_head_group(repaired: TranslationResult):
                    overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                    overrides[request.display_id] = repaired
                    overrides[request.unit_uid] = repaired
                    return _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                outcome = _run_semantic_rescue(
                    checkpoint_dir=checkpoint_dir, request=request, base=current_candidate,
                    active=active, view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible,
                    semantic_context=None if current_group is None else current_group.as_reviewer_context_payload(),
                    group=current_group, prior={"review": current_head_repair.model_dump()}, rebuild_group=rebuild_current_head_group,
                    stage_prefix="semantic_rescue_current_head_corrective",
                    corrective_diagnosis="; ".join(issue.reason for issue in current_head_repair.issues), required_repair_scope=scope,
                    authority_raw=raw,
                )
                if saved_current_attempt is None:
                    record = _rescue_record(
                        outcome, mode="CURRENT_HEAD_SEMANTIC_CORRECTIVE", group=current_group, raw=raw,
                    )
                    record.update({
                        "parent_candidate_sha256": _candidate_binding(current_candidate),
                        "request_binding_sha256": enrich_request(request, translation_bible=bible).request_binding_sha256,
                        "review_result_sha256": _canonical_sha256(current_head_repair.model_dump()),
                        "required_repair_scope": list(scope),
                    })
                    persist_stage_once(checkpoint_dir, "semantic_rescue_current_head_corrective", request.display_id, record)
                # A resume can reuse a durable child repair and its exact
                # final-review retry.  It still must admit that now-accepted
                # candidate; publication is not a create-once side effect of
                # writing the attempt record.
                try:
                    _commit_accepted_candidate_mutation(checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible)
                except CandidateAuthorityError:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                authoritative_transitions.add(request.display_id)
                outcomes.append(outcome)
                continue
            raw_ownership_qa = _qa_with_d051(
                qa, loaded["views"][request.display_id], request, raw,
                loaded["known_unit_ids"], current_equivalence,
            )
            # Index locators and their delimiters already have an explicit
            # Harness owner.  Materialize that owner before generic semantic
            # recovery, never by asking a model to reproduce page numbers.
            group = _production_review_group(
                request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
            )
            semantic_base = _bound_index_locator_semantic_base(
                checkpoint_dir=checkpoint_dir, request=request, current=current_candidate,
                qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, current_equivalence),
                view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
            )
            if (
                ( _index_locator_materialization_eligible(group, current_qa) or semantic_base is not None )
                and group is not None
                and group.continuation_relation == "INDEX_ENTRY"
                and group.continuation_review_rules.get("numeric_locator_owner") == "HARNESS_DETERMINISTIC"
            ):
                materialization_parent = current_candidate if semantic_base is None else semantic_base
                materialized = materialize_index_locator(request, materialization_parent)
                if materialized is None:
                    return _blocked(
                        handoff_path, worker_id, checkpoint=checkpoint,
                        classification="INDEX_LOCATOR_OWNERSHIP_AMBIGUOUS",
                        extra={"display_id": request.display_id}, active_batch=active_batch,
                    )
                if _candidate_binding(materialized) != _candidate_binding(materialization_parent):
                    materialized_qa = _qa_with_d051(
                        qa, loaded["views"][request.display_id], request, materialized,
                        loaded["known_unit_ids"], current_equivalence,
                    )
                    if not materialized_qa.get("passed"):
                        return _blocked(
                            handoff_path, worker_id, checkpoint=checkpoint,
                            classification="INDEX_LOCATOR_MATERIALIZATION_QA_FAILED",
                            extra={"display_id": request.display_id, "qa": materialized_qa}, active_batch=active_batch,
                        )
                    outcome = UnitOutcome(
                        request=request, decision=route_semantic_unit(request), raw=raw,
                        working=materialized, qa=materialized_qa, final_status="ACCEPT",
                        semantic_terminal="MODEL_ACCEPTED", repair_applied=True,
                        repair_policy_id="INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION",
                        recovered_from="index_locator_deterministic_materialization",
                    )
                    try:
                        _commit_accepted_candidate_mutation(
                            checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                            source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                        )
                    except CandidateAuthorityError:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                    outcomes.append(outcome)
                    continue
            if "NUMERIC_ANCHOR_DUPLICATION" in set(qa_codes(current_qa)):
                corrected = correct_numeric_anchor_duplication(request, current_candidate)
                if corrected is None:
                    return _blocked(
                        handoff_path, worker_id, checkpoint=checkpoint,
                        classification="NUMERIC_ANCHOR_OWNERSHIP_AMBIGUOUS",
                        extra={"display_id": request.display_id}, active_batch=active_batch,
                    )
                corrected_qa = _qa_with_d051(
                    qa, loaded["views"][request.display_id], request, corrected,
                    loaded["known_unit_ids"], current_equivalence,
                )
                if not corrected_qa.get("passed"):
                    return _blocked(
                        handoff_path, worker_id, checkpoint=checkpoint,
                        classification="NUMERIC_ANCHOR_OWNERSHIP_CORRECTION_QA_FAILED",
                        extra={"display_id": request.display_id, "qa": corrected_qa}, active_batch=active_batch,
                    )
                outcome = UnitOutcome(
                    request=request, decision=route_semantic_unit(request), raw=raw,
                    working=corrected, qa=corrected_qa, final_status="ACCEPT",
                    semantic_terminal="MODEL_ACCEPTED", repair_applied=True,
                    repair_policy_id="NUMERIC_ANCHOR_OWNERSHIP_CORRECTION",
                    recovered_from="numeric_anchor_ownership_correction",
                )
                try:
                    _commit_accepted_candidate_mutation(
                        checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                        source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                    )
                except CandidateAuthorityError:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                outcomes.append(outcome)
                continue
            if set(qa_codes(current_qa)) == {"URL_PRESERVATION"}:
                restored = restore_exact_source_urls(request, current_candidate)
                if restored is None or _candidate_binding(restored) == _candidate_binding(current_candidate):
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="EXACT_SOURCE_URL_RESTORATION_AMBIGUOUS", extra={"display_id": request.display_id}, active_batch=active_batch)
                restored_qa = _qa_with_d051(qa, loaded["views"][request.display_id], request, restored, loaded["known_unit_ids"], current_equivalence)
                if not restored_qa.get("passed"):
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="EXACT_SOURCE_URL_RESTORATION_QA_FAILED", extra={"display_id": request.display_id, "qa": restored_qa}, active_batch=active_batch)
                outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=restored, qa=restored_qa, final_status="ACCEPT", semantic_terminal="MODEL_ACCEPTED", repair_applied=True, repair_policy_id="EXACT_SOURCE_URL_RESTORATION", recovered_from="exact_source_url_restoration")
                try:
                    _commit_accepted_candidate_mutation(checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible)
                except CandidateAuthorityError:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                outcomes.append(outcome)
                continue
            if (
                not current_qa.get("passed")
                and _only_missing_verified_d050_markers(request, current_candidate, current_qa)
            ):
                group = _production_review_group(
                    request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                )
                if group is None:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D050_MARKER_REPAIR_GROUP_MISSING", active_batch=active_batch)
                def rebuild_group(repaired: TranslationResult):
                    overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                    overrides[request.display_id] = repaired
                    overrides[request.unit_uid] = repaired
                    return _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint,
                        existing_raw=existing_raw, candidate_overrides=overrides,
                    )
                marker_review = ReviewResult(
                    schema_version="booktrans.structured-review.v1",
                    unit_uid=request.unit_uid,
                    verdict="REPAIR",
                    issues=[ReviewIssue(
                        code="MISSING_FOOTNOTE_MARKER",
                        affected_segment_indices=list(range(len(current_candidate.target_segments))),
                        reason="Exact D050 evidence requires the visible source marker to be preserved.",
                        repair_instruction="Restore only the exact verified footnote marker at its source-relative location. Preserve every other source clause, anchor, protected value, URL, DOI, and numeric representation.",
                    )],
                )
                outcome = _run_semantic_rescue(
                    checkpoint_dir=checkpoint_dir, request=request, base=current_candidate, active=active,
                    view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                    qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, current_equivalence),
                    bible=bible, semantic_context=group.as_reviewer_context_payload(), group=group,
                    prior={"review": marker_review.model_dump()}, rebuild_group=rebuild_group,
                    allow_verified_d050_marker_repair=True,
                    stage_prefix="d050_exact_marker_recovery",
                )
                _persist_d050_exact_marker_recovery_attempt(
                    checkpoint_dir=checkpoint_dir, request=request, parent=current_candidate,
                    outcome=outcome, group=group, bible=bible,
                )
                outcomes.append(outcome)
                continue
            if legacy_unbound and not current_mode:
                # Old derived records may explain why a historical Reviewer
                # response exists, but they cannot select today's candidate or
                # veto its terminal.  A clean current head receives at most
                # one existing reviewer-only rereview; a failed Core head
                # remains a real hard block without any provider call.
                current_candidate = TranslationResult.model_validate(merged["working"])
                current_equivalence = _d051_equivalence_for(checkpoint_dir, request, current_candidate, bible=bible)
                current_qa = _qa_with_d051(
                    qa, loaded["views"][request.display_id], request, current_candidate,
                    loaded["known_unit_ids"], current_equivalence,
                )
                if not current_qa.get("passed"):
                    # A former normal numeric repair that lacked a parent
                    # binding is not authoritative.  Re-run exactly that
                    # bounded repair from RAW, and commit it before a unit
                    # compatibility record can observe the new candidate.
                    # This path never calls Translation.
                    current_codes = set(qa_codes(current_qa))
                    if (
                        current_codes == {"NUMBER_PRESERVATION"}
                        and stored.get("repair_policy_id") == "NUMERIC_REPRESENTATION"
                        and load_stage(checkpoint_dir, "production_reprocess", request.display_id) is None
                    ):
                        group = _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                        )
                        semantic_context = None if group is None else group.as_reviewer_context_payload()
                        outcome, reviewer_contract_evidence = _process_with_bounded_reviewer_contract_retry(
                            checkpoint_dir=checkpoint_dir, request=request, raw=raw, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=qa, bible=bible, semantic_context=semantic_context, review_policy=review_policy,
                        )
                        if reviewer_contract_evidence is not None and outcome is None:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification=REVIEWER_CONTRACT_FAILURE, active_batch=active_batch)
                        assert outcome is not None
                        try:
                            _commit_accepted_candidate_mutation(
                                checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                                source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                            )
                        except CandidateAuthorityError:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                        persist_stage_once(
                            checkpoint_dir, "production_reprocess", request.display_id,
                            _reprocess_record(outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"])),
                        )
                        outcomes.append(outcome)
                        continue
                    # Resume a completed exact-D050 marker repair before the
                    # raw head's expected missing-marker QA failure can take
                    # the generic legacy short-circuit.  This uses only its
                    # already-durable evidence and accepts no new mutation.
                    rescue = load_stage(checkpoint_dir, "semantic_rescue", request.display_id) or {}
                    if (
                        rescue.get("mode") == "D050_EXACT_MARKER_REPAIR"
                        and rescue.get("semantic_terminal") == "HUMAN_REQUIRED"
                        and isinstance(rescue.get("working"), Mapping)
                        and isinstance(rescue.get("second_review"), Mapping)
                    ):
                        repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", request.display_id) or {}
                        completeness = load_stage(checkpoint_dir, "semantic_rescue_completeness", request.display_id) or {}
                        final_evidence = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", request.display_id) or {}
                        try:
                            repaired = TranslationResult.model_validate(rescue["working"])
                            final_review = ReviewResult.model_validate(rescue["second_review"])
                            repair_working = TranslationResult.model_validate(repair["working"])
                            repaired_sha = _candidate_binding(repaired)
                            final_codes = {issue.code.casefold() for issue in final_review.issues}
                            repair_scope = repair.get("contract", {}).get("permitted_segment_indices")
                            same_segments = [segment.model_dump() for segment in repaired.target_segments] == [segment.model_dump() for segment in repair_working.target_segments]
                            repair_is_current_bound = (
                                repair.get("authority_parent_verified") is True
                                and repair.get("parent_candidate_sha256") == _candidate_binding(current_candidate)
                                and repaired_sha == _candidate_binding(repair_working)
                                and repair_scope == list(range(len(current_candidate.target_segments)))
                                and same_segments
                            )
                            final_is_current_bound = (
                                final_evidence.get("candidate_binding_sha256") == repaired_sha
                                and final_evidence.get("request_binding_sha256") == repair.get("request_binding_sha256")
                                and isinstance(final_evidence.get("member_slice_sha256"), str)
                                and isinstance(final_evidence.get("group_span_sha256"), str)
                                and isinstance(final_evidence.get("context_sha256"), str)
                            )
                            repaired_qa = _qa_with_d051(
                                qa, loaded["views"][request.display_id], request, repaired,
                                loaded["known_unit_ids"], _d051_equivalence_for(checkpoint_dir, request, repaired, bible=bible),
                            )
                            safe_debt_candidate = (
                                repair_is_current_bound
                                and final_is_current_bound
                                and completeness.get("passed") is True
                                and completeness.get("candidate_binding_sha256") == repaired_sha
                                and repaired_qa.get("passed")
                                and final_review.verdict == "REPAIR"
                                and final_codes == {"fidelity_omission"}
                            )
                        except Exception:
                            safe_debt_candidate = False
                        if safe_debt_candidate:
                            outcome = UnitOutcome(
                                request=request, decision=route_semantic_unit(request), raw=raw,
                                working=repaired, qa=repaired_qa,
                                recovered_from="d050_exact_marker_repair",
                            )
                            outcome.review = ReviewResult.model_validate(rescue["review"])
                            outcome.second_review = final_review
                            outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                            outcome.repair_applied = True; outcome.repair_policy_id = repair.get("contract", {}).get("policy_id")
                            outcomes.append(outcome)
                            continue
                    # A PDF-native/DOCX-exact D050 record proves that this is
                    # an omitted visible marker, not an ordinary number.  The
                    # old unbound repair cannot be reused, so give the current
                    # RAW head one normal bounded repair with a current-bound
                    # reviewer diagnostic.  No generic numeric bypass exists.
                    if (
                        _only_missing_verified_d050_markers(request, current_candidate, current_qa)
                        and not _d050_exact_marker_recovery_consumed(checkpoint_dir, request.display_id)
                    ):
                        group = _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                        )
                        if group is None:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="D050_MARKER_REPAIR_GROUP_MISSING", active_batch=active_batch)
                        def rebuild_group(repaired: TranslationResult):
                            overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                            overrides[request.display_id] = repaired
                            overrides[request.unit_uid] = repaired
                            return _production_review_group(
                                request, loaded=loaded, run=run, checkpoint=checkpoint,
                                existing_raw=existing_raw, candidate_overrides=overrides,
                            )
                        marker_review = ReviewResult(
                            schema_version="booktrans.structured-review.v1",
                            unit_uid=request.unit_uid,
                            verdict="REPAIR",
                            issues=[ReviewIssue(
                                code="MISSING_FOOTNOTE_MARKER",
                                affected_segment_indices=list(range(len(current_candidate.target_segments))),
                                reason="Exact D050 evidence requires the visible source marker to be preserved.",
                                repair_instruction="Restore only the exact verified footnote marker at its source-relative location. Preserve every other source clause, anchor, protected value, URL, DOI, and numeric representation.",
                            )],
                        )
                        outcome = _run_semantic_rescue(
                            checkpoint_dir=checkpoint_dir, request=request, base=current_candidate, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, current_equivalence),
                            bible=bible, semantic_context=group.as_reviewer_context_payload(), group=group,
                            prior={"review": marker_review.model_dump()}, rebuild_group=rebuild_group,
                            allow_verified_d050_marker_repair=True,
                            stage_prefix="d050_exact_marker_recovery",
                        )
                        _persist_d050_exact_marker_recovery_attempt(
                            checkpoint_dir=checkpoint_dir, request=request, parent=current_candidate,
                            outcome=outcome, group=group, bible=bible,
                        )
                        outcomes.append(outcome)
                        continue
                    projected_qa = merged.get("qa")
                    marker = str((projected_qa or {}).get("effective_qa_reason") or "")
                    payload = loaded["current_raw_qa_payload"]
                    expected_request_binding = _provider_request_for_decision(
                        request, bible=bible, decision=route_semantic_unit(request),
                    ).request_binding_sha256
                    marker_present = marker.startswith("RESOLVER_VALIDATED_EFFECTIVE_ACQUISITION")
                    marker_valid = (
                        marker == "RESOLVER_VALIDATED_EFFECTIVE_ACQUISITION_RAW_QA"
                        and isinstance(projected_qa, Mapping)
                        and projected_qa.get("unit_uid") == request.unit_uid
                        and projected_qa.get("display_id") == request.display_id
                        and projected_qa.get("effective_qa_candidate_binding_sha256") == resolved_candidate.candidate_sha256
                        and payload.source_sha256 == request.source_sha256
                        and payload.request_binding_by_uid.get(request.unit_uid) == expected_request_binding
                        and payload.acquisition_binding_by_uid.get(request.unit_uid) == _candidate_binding(raw)
                    )
                    if marker_present and not marker_valid:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint,
                                        classification="CURRENT_RAW_QA_BINDING_INVALID", active_batch=active_batch)
                    # A resolver-validated current QA is authoritative for
                    # this effective acquisition. Legacy reconstruction must
                    # not discard its marker by recomputing an unbound copy.
                    merged["qa"] = projected_qa if marker_valid else current_qa
                    outcomes.append(_reconstruct_outcome(request, merged, raw))
                    continue
                if load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id) is None:
                    group = _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                    )
                    if group is None:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="LEGACY_REREVIEW_GROUP_MISSING", active_batch=active_batch)
                    outcome = _rereview_stale_final_candidate(
                        checkpoint_dir=checkpoint_dir, request=request, candidate=current_candidate, active=active,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                        qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, current_equivalence),
                        bible=bible, group=group, classification="LEGACY_UNBOUND_DERIVED_REVIEW",
                    )
                    outcomes.append(outcome)
                    continue
                rereview = load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id) or {}
                rereview_payload = rereview.get("review")
                rescue = load_stage(checkpoint_dir, "semantic_rescue", request.display_id) or {}
                # A verified D050 marker repair is current-bound evidence, not
                # an old legacy-derived mutation.  Its final review can leave
                # only the approved release-debt shape (a local fidelity
                # qualifier) after completeness and every Core invariant pass.
                # Reconstruct that durable safe candidate without spending a
                # further provider budget.  Any missing/mismatched binding
                # deliberately falls through to the ordinary hard-block path.
                if (
                    rescue.get("mode") == "D050_EXACT_MARKER_REPAIR"
                    and rescue.get("semantic_terminal") == "HUMAN_REQUIRED"
                    and isinstance(rescue.get("working"), Mapping)
                    and isinstance(rescue.get("second_review"), Mapping)
                ):
                    repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", request.display_id) or {}
                    completeness = load_stage(checkpoint_dir, "semantic_rescue_completeness", request.display_id) or {}
                    final_evidence = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", request.display_id) or {}
                    try:
                        repaired = TranslationResult.model_validate(rescue["working"])
                        final_review = ReviewResult.model_validate(rescue["second_review"])
                        repair_working = TranslationResult.model_validate(repair["working"])
                        repaired_sha = _candidate_binding(repaired)
                        final_codes = {issue.code.casefold() for issue in final_review.issues}
                        repair_scope = repair.get("contract", {}).get("permitted_segment_indices")
                        same_segments = [segment.model_dump() for segment in repaired.target_segments] == [segment.model_dump() for segment in repair_working.target_segments]
                        repair_is_current_bound = (
                            repair.get("authority_parent_verified") is True
                            and repair.get("parent_candidate_sha256") == _candidate_binding(current_candidate)
                            and repaired_sha == _candidate_binding(repair_working)
                            and repair_scope == list(range(len(current_candidate.target_segments)))
                            and same_segments
                        )
                        final_is_current_bound = (
                            final_evidence.get("candidate_binding_sha256") == repaired_sha
                            and final_evidence.get("request_binding_sha256") == repair.get("request_binding_sha256")
                            and isinstance(final_evidence.get("member_slice_sha256"), str)
                            and isinstance(final_evidence.get("group_span_sha256"), str)
                            and isinstance(final_evidence.get("context_sha256"), str)
                        )
                        repaired_qa = _qa_with_d051(
                            qa, loaded["views"][request.display_id], request, repaired,
                            loaded["known_unit_ids"], _d051_equivalence_for(checkpoint_dir, request, repaired, bible=bible),
                        )
                        safe_debt_candidate = (
                            repair_is_current_bound
                            and final_is_current_bound
                            and completeness.get("passed") is True
                            and completeness.get("candidate_binding_sha256") == repaired_sha
                            and repaired_qa.get("passed")
                            and final_review.verdict == "REPAIR"
                            and final_codes == {"fidelity_omission"}
                        )
                    except Exception:
                        safe_debt_candidate = False
                    if safe_debt_candidate:
                        outcome = UnitOutcome(
                            request=request, decision=route_semantic_unit(request), raw=raw,
                            working=repaired, qa=repaired_qa,
                            recovered_from="d050_exact_marker_repair",
                        )
                        outcome.review = ReviewResult.model_validate(rescue["review"])
                        outcome.second_review = final_review
                        outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                        outcome.repair_applied = True; outcome.repair_policy_id = repair.get("contract", {}).get("policy_id")
                        outcomes.append(outcome)
                        continue
                if rescue.get("semantic_terminal") == "HUMAN_REQUIRED":
                    # The historical rescue candidate is not current authority.
                    # Once the current RAW head has a bound material rereview,
                    # spend the existing one-shot corrective contract from that
                    # RAW head before considering any debt or human terminal.
                    original_repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", request.display_id) or {}
                    current_review = _current_candidate_rereview(
                        checkpoint_dir=checkpoint_dir, request=request, current=current_candidate,
                    )
                    group = _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                    )
                    corrective_scope = None if current_review is None else _current_rereview_corrective_scope(
                        request=request, candidate=current_candidate, review=current_review, group=group,
                    )
                    if (
                        corrective_scope is not None
                        and load_stage(checkpoint_dir, "semantic_rescue_corrective", request.display_id) is None
                    ):
                        if group is None:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CORRECTIVE_SEMANTIC_GROUP_MISSING", active_batch=active_batch)
                        def rebuild_group(repaired: TranslationResult):
                            overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                            overrides[request.display_id] = repaired; overrides[request.unit_uid] = repaired
                            return _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                        outcome = _run_semantic_rescue(
                            checkpoint_dir=checkpoint_dir, request=request, base=current_candidate, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                            bible=bible, semantic_context=group.as_reviewer_context_payload(), group=group,
                            prior={"review": current_review.model_dump()}, rebuild_group=rebuild_group,
                            stage_prefix="semantic_rescue_corrective",
                            corrective_diagnosis="; ".join(issue.reason for issue in current_review.issues),
                            required_repair_scope=corrective_scope,
                        )
                        persist_stage_once(checkpoint_dir, "semantic_rescue_corrective", request.display_id, _rescue_record(outcome, mode="CORRECTIVE_SEMANTIC_REPAIR", group=group, raw=raw))
                        try:
                            _commit_accepted_candidate_mutation(checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible)
                        except CandidateAuthorityError:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                        outcomes.append(outcome)
                        continue
                    # The bounded repair was rejected by its current-bound
                    # final review. Restore the complete raw head; the
                    # remaining local wording issue is release debt, never a
                    # license for the rejected repair to become authoritative.
                    outcome = UnitOutcome(
                        request=request, decision=route_semantic_unit(request), raw=raw,
                        working=current_candidate, qa=current_qa,
                        recovered_from="legacy_unbound_last_known_good",
                    )
                    outcome.review = ReviewResult.model_validate(rereview_payload) if isinstance(rereview_payload, Mapping) else None
                    # Historical rescue evidence is append-only and older
                    # schema versions legitimately encode an absent final
                    # review as ``null``.  It is not current authority, and
                    # must not turn a recoverable canonical replay into a
                    # worker crash merely because it is optional.
                    outcome.second_review = _optional_review_result(rescue.get("second_review"))
                    outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
                    outcome.repair_applied = True; outcome.repair_policy_id = "LEGACY_AUTO_RESOLUTION"
                    outcomes.append(outcome)
                    continue
                if (
                    isinstance(rereview_payload, Mapping)
                    and rereview_payload.get("verdict") == "REPAIR"
                    and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is None
                ):
                    group = _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                    )
                    if group is None:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="LEGACY_REPAIR_GROUP_MISSING", active_batch=active_batch)
                    def rebuild_group(repaired: TranslationResult):
                        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                        overrides[request.display_id] = repaired
                        overrides[request.unit_uid] = repaired
                        return _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint,
                            existing_raw=existing_raw, candidate_overrides=overrides,
                        )
                    outcome = _run_semantic_rescue(
                        checkpoint_dir=checkpoint_dir, request=request, base=current_candidate, active=active,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                        qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, current_equivalence),
                        bible=bible, semantic_context=group.as_reviewer_context_payload(), group=group,
                        prior={"second_review": rereview_payload}, rebuild_group=rebuild_group,
                    )
                    persist_stage_once(
                        checkpoint_dir, "semantic_rescue", request.display_id,
                        _rescue_record(outcome, mode="SEMANTIC_REPAIR", group=group, raw=raw),
                    )
                    outcomes.append(outcome)
                    continue
            queue_resolution = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id)
            rejected_queue_repair = load_stage(checkpoint_dir, "resolution_queue_rejected_repair_candidates", request.display_id)
            if queue_resolution is not None and rejected_queue_repair is None:
                final_payload = queue_resolution.get("second_review") if isinstance(queue_resolution, Mapping) else None
                original_payload = queue_resolution.get("review") if isinstance(queue_resolution, Mapping) else None
                repair = load_stage(checkpoint_dir, "resolution_queue_auto_repairs", request.display_id)
                # A repair candidate that is byte-for-byte the input despite a
                # narrow AUTO_POLISH contract is untrusted and unsuccessful.
                # Restore the authoritative input; do not let a later review
                # of that unchanged candidate manufacture a new blocker.
                try:
                    original = ReviewResult.model_validate(original_payload) if isinstance(original_payload, Mapping) else None
                    final = ReviewResult.model_validate(final_payload) if isinstance(final_payload, Mapping) else None
                    repaired = TranslationResult.model_validate(repair["working"]) if isinstance(repair, Mapping) and isinstance(repair.get("working"), Mapping) else None
                    no_op_auto_polish = (
                        original is not None
                        and final is not None
                        and repaired is not None
                        and repair.get("input_candidate_binding_sha256") == _candidate_binding(resolved_candidate.candidate)
                        and _candidate_binding(repaired) == _candidate_binding(resolved_candidate.candidate)
                        and AUTO_POLISH in review_issue_severities(issue.code for issue in original.issues)
                        and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in original.issues)
                    )
                except Exception:
                    no_op_auto_polish = False
                if no_op_auto_polish:
                    _persist_rejected_queue_repair_candidate(
                        checkpoint_dir, request, base=resolved_candidate.candidate,
                        base_qa=qa(loaded["views"][request.display_id], request, resolved_candidate.candidate, loaded["known_unit_ids"]),
                        original_review=original, rejected=repaired,
                        rejected_qa=queue_resolution.get("qa") if isinstance(queue_resolution.get("qa"), Mapping) else {},
                        final_review=final,
                        input_candidate_binding_sha256=str(repair.get("input_candidate_binding_sha256") or ""),
                    )
                    merged = _project_resolved_unit_record(
                        _merge_latest_unit_record(checkpoint_dir, request.display_id, stored),
                        checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                        resolved_candidate=resolved_candidate, qa=qa,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], bible=bible,
                        current_raw_qa=loaded["current_raw_qa_payload"],
                    )
                    rejected_queue_repair = load_stage(checkpoint_dir, "resolution_queue_rejected_repair_candidates", request.display_id)
                if isinstance(final_payload, Mapping) and isinstance(original_payload, Mapping) and isinstance(repair, Mapping):
                    final = ReviewResult.model_validate(final_payload)
                    if _queue_repair_candidate_rejected(final):
                        try:
                            base = TranslationResult.model_validate(stored["working"])
                            original = ReviewResult.model_validate(original_payload)
                            base_qa = qa(loaded["views"][request.display_id], request, base, loaded["known_unit_ids"])
                            if not base_qa.get("passed"):
                                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="REJECTED_QUEUE_REPAIR_NO_SAFE_BASE", active_batch=active_batch)
                            _persist_rejected_queue_repair_candidate(
                                checkpoint_dir, request, base=base, base_qa=base_qa, original_review=original,
                                rejected=TranslationResult.model_validate(repair["working"]),
                                rejected_qa=queue_resolution.get("qa") if isinstance(queue_resolution.get("qa"), Mapping) else {},
                                final_review=final,
                                input_candidate_binding_sha256=str(repair.get("input_candidate_binding_sha256") or ""),
                            )
                            merged = _project_resolved_unit_record(
                                _merge_latest_unit_record(checkpoint_dir, request.display_id, stored),
                                checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                                resolved_candidate=resolved_candidate, qa=qa,
                                view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], bible=bible,
                                current_raw_qa=loaded["current_raw_qa_payload"],
                            )
                            rejected_queue_repair = load_stage(checkpoint_dir, "resolution_queue_rejected_repair_candidates", request.display_id)
                        except (CandidateAuthorityError, KeyError, ValueError, TypeError):
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="REJECTED_QUEUE_REPAIR_BINDING_MISMATCH", active_batch=active_batch)
            if queue_resolution is not None and load_stage(checkpoint_dir, "resolution_queue_auto_dispositions", request.display_id) is None:
                disposition = _resolution_queue_advisory_disposition(queue_resolution)
                if disposition is not None:
                    persist_stage_once(checkpoint_dir, "resolution_queue_auto_dispositions", request.display_id, disposition)
                    merged = _project_resolved_unit_record(
                        _merge_latest_unit_record(checkpoint_dir, request.display_id, stored),
                        checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                        resolved_candidate=resolved_candidate, qa=qa,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], bible=bible,
                        current_raw_qa=loaded["current_raw_qa_payload"],
                    )
            # A fresh reviewer-only re-review may prove that a prior queue
            # attempt used incomplete frozen context.  That invalid attempt
            # does not spend the one semantic-rescue repair budget; repair
            # once from the authoritative RAW head with the rebuilt context.
            context_audit = load_stage(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id)
            rereview = load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id)
            if (
                semantic_rescue
                and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is None
                and isinstance(context_audit, Mapping)
                and context_audit.get("classification") in {
                    "SYSTEMIC_CONTEXT_GAP", "MISSING_TEXTBOX_CONTINUATION_CONTEXT",
                }
                and isinstance(rereview, Mapping)
                and isinstance(rereview.get("review"), Mapping)
                and rereview["review"].get("verdict") == "REPAIR"
            ):
                group = _production_review_group(
                    request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                )
                if group is None:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="SYSTEMIC_CONTEXT_GROUP_MISSING", active_batch=active_batch)
                def rebuild_group(repaired: TranslationResult):
                    overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                    overrides[request.display_id] = repaired
                    overrides[request.unit_uid] = repaired
                    return _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint,
                        existing_raw=existing_raw, candidate_overrides=overrides,
                    )
                outcome = _run_semantic_rescue(
                    checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
                    view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                    bible=bible, semantic_context=group.as_reviewer_context_payload(), group=group,
                    prior={"second_review": rereview["review"]}, rebuild_group=rebuild_group,
                )
                persist_stage_once(
                    checkpoint_dir, "semantic_rescue", request.display_id,
                    _rescue_record(outcome, mode="SYSTEMIC_CONTEXT_GAP_REPAIR", group=group, raw=raw),
                )
                outcomes.append(outcome)
                continue
            terminal = merged.get("semantic_terminal")
            already_reprocessed = load_stage(checkpoint_dir, "production_reprocess", request.display_id) is not None
            deferred_debt = load_stage(checkpoint_dir, "resolution_queue_deferred_debts", request.display_id)
            if deferred_debt is not None and terminal == "HUMAN_REQUIRED":
                # The debt is an immutable, locally-bound continuation record.
                # Resume it from disk; do not spend a reviewer/repair budget
                # merely because the checkpoint is being sealed again.
                candidate_disposition = deferred_debt.get("candidate_disposition")
                durable_debt_candidate = deferred_debt.get("current_candidate")
                if (
                    deferred_debt.get("logical_work_order_id") != request.display_id
                    or deferred_debt.get("unit_uid") != request.unit_uid
                    or deferred_debt.get("source_sha256") != request.source_sha256
                    or not isinstance(durable_debt_candidate, Mapping)
                    or deferred_debt.get("candidate_binding_sha256") != _candidate_binding(TranslationResult.model_validate(durable_debt_candidate))
                ):
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="DEFERRED_DEBT_BINDING_MISMATCH", active_batch=active_batch)
                outcome = _reconstruct_outcome(request, merged, raw)
                if candidate_disposition == "LAST_KNOWN_GOOD_AFTER_REJECTED_REPAIR":
                    outcome.working = TranslationResult.model_validate(durable_debt_candidate)
                    outcome.qa = deferred_debt.get("core_qa") if isinstance(deferred_debt.get("core_qa"), Mapping) else outcome.qa
                    if isinstance(deferred_debt.get("review"), Mapping):
                        outcome.review = ReviewResult.model_validate(deferred_debt["review"])
                        outcome.second_review = None
                    outcome.recovered_from = "last_known_good_rejected_repair"
                outcomes.append(outcome)
                continue
            # Historical repair contract gaps predate the durable rejected
            # proposal sidecar.  They are replayable only when the stored
            # review and current head exactly still match RAW.  Materialize a
            # clearly-labelled reconstruction of attempt 0, then permit the
            # one remaining scope-constrained retry; never infer a patch or
            # promote a historical rejected candidate.
            historical_repair_gap = str(merged.get("failure_code") or "").startswith("REPAIR_CONTRACT_GAP_")
            if terminal == "HUMAN_REQUIRED" and historical_repair_gap:
                review_payload = merged.get("review")
                stored_working = merged.get("working")
                if not isinstance(review_payload, Mapping) or not isinstance(stored_working, Mapping):
                    outcomes.append(_reconstruct_outcome(request, merged, raw)); continue
                try:
                    review = ReviewResult.model_validate(review_payload)
                    current = TranslationResult.model_validate(stored_working)
                except Exception:
                    outcomes.append(_reconstruct_outcome(request, merged, raw)); continue
                if _candidate_binding(current) != _candidate_binding(raw) or review.verdict != "REPAIR":
                    outcomes.append(_reconstruct_outcome(request, merged, raw)); continue
                group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                semantic_context = None if group is None else group.as_reviewer_context_payload()
                raw_qa = qa(loaded["views"][request.display_id], request, raw, loaded["known_unit_ids"])
                historical = UnitOutcome(
                    request=request, decision=route_semantic_unit(request), raw=raw, working=raw,
                    qa=raw_qa, review=review, final_status="HUMAN_REQUIRED", human_required=True,
                    failure_family="REPAIR_EXECUTION_FAILURE", failure_code=str(merged.get("failure_code")),
                    recovered_from="repair_contract_historical_replay",
                )
                try:
                    failures = _matching_repair_contract_failure_paths(checkpoint_dir, request, raw, review, bible=bible)
                    if not failures:
                        _persist_repair_contract_failure(
                            checkpoint_dir, request=request, raw=raw, review=review, bible=bible,
                            error=ProviderContractGap(str(merged.get("failure_code"))), attempt=1,
                            reconstructed_from_unit_result=True,
                        )
                    outcome = _process_with_bounded_repair_contract_retry(
                        checkpoint_dir=checkpoint_dir, request=request, raw=raw, active=active,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                        bible=bible, semantic_context=semantic_context, review_policy=review_policy,
                        outcome=historical, initial_failure_already_persisted=True,
                    )
                except (ProviderContractGap, CandidateAuthorityError):
                    outcomes.append(_reconstruct_outcome(request, merged, raw)); continue
                try:
                    _commit_accepted_candidate_mutation(
                        checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                        source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                    )
                except CandidateAuthorityError:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                persist_stage_once(
                    checkpoint_dir, "production_reprocess", request.display_id,
                    _reprocess_record(outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"])),
                )
                outcomes.append(outcome)
                continue
            # A repair-induced severe finding can have one corrective attempt,
            # but only from the original last-known-good RAW head and only
            # when its exact scope is a subset of the original authority.
            # A diagnostic that reaches an immutable neighbor is deliberately
            # not "fixed" by expanding scope.
            if terminal == "HUMAN_REQUIRED" and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is not None and load_stage(checkpoint_dir, "semantic_rescue_corrective", request.display_id) is None:
                original_repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", request.display_id) or {}
                original_final = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", request.display_id) or {}
                final_payload = original_final.get("review") if isinstance(original_final, Mapping) else None
                try:
                    rejected_final = ReviewResult.model_validate(final_payload) if isinstance(final_payload, Mapping) else None
                except Exception:
                    rejected_final = None
                if (
                    _current_candidate_rereview(
                        checkpoint_dir=checkpoint_dir, request=request, current=current_candidate,
                    ) is None
                    and rejected_final is not None
                    and _corrective_semantic_repair_is_eligible(original_repair, rejected_final)
                ):
                    group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                    def rebuild_group(repaired: TranslationResult):
                        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                        overrides[request.display_id] = repaired
                        overrides[request.unit_uid] = repaired
                        return _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                    outcome = _run_semantic_rescue(
                        checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                        bible=bible, semantic_context=None if group is None else group.as_reviewer_context_payload(), group=group,
                        prior={"review": rejected_final.model_dump()}, rebuild_group=rebuild_group,
                        stage_prefix="semantic_rescue_corrective",
                        corrective_diagnosis="; ".join(issue.reason for issue in rejected_final.issues),
                    )
                    persist_stage_once(checkpoint_dir, "semantic_rescue_corrective", request.display_id, _rescue_record(outcome, mode="CORRECTIVE_SEMANTIC_REPAIR", group=group, raw=raw))
                    try:
                        _commit_accepted_candidate_mutation(checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible)
                    except CandidateAuthorityError:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                    outcomes.append(outcome)
                    continue
            # A corrective repair request is durable before its final review.
            # Resume a missing result exactly once through the same rescue
            # boundary; this is a reviewer protocol retry, never a new repair
            # or a new translation.
            if (
                terminal == "HUMAN_REQUIRED"
                and load_stage(checkpoint_dir, "semantic_rescue_corrective", request.display_id) is not None
                and load_stage(checkpoint_dir, "semantic_rescue_corrective_final_reviews", request.display_id) is None
                and load_stage(checkpoint_dir, "semantic_rescue_corrective_final_review_failures", request.display_id) is None
            ):
                original_final = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", request.display_id) or {}
                final_payload = original_final.get("review") if isinstance(original_final, Mapping) else None
                try:
                    rejected_final = ReviewResult.model_validate(final_payload) if isinstance(final_payload, Mapping) else None
                except Exception:
                    rejected_final = None
                if rejected_final is not None:
                    group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                    def rebuild_group(repaired: TranslationResult):
                        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                        overrides[request.display_id] = repaired
                        overrides[request.unit_uid] = repaired
                        return _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                    outcome = _run_semantic_rescue(
                        checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                        bible=bible, semantic_context=None if group is None else group.as_reviewer_context_payload(), group=group,
                        prior={"review": rejected_final.model_dump()}, rebuild_group=rebuild_group,
                        stage_prefix="semantic_rescue_corrective",
                        corrective_diagnosis="; ".join(issue.reason for issue in rejected_final.issues),
                    )
                    try:
                        _commit_accepted_candidate_mutation(checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible)
                    except CandidateAuthorityError:
                        return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                    outcomes.append(outcome)
                    continue
            # A final review of a rejected repair explains that rejected
            # proposal only.  It cannot carry its severity back onto the
            # restored head. Rebuild the canonical group and spend at most one
            # reviewer-only rereview of that exact current head.
            if (
                terminal == "HUMAN_REQUIRED"
                and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is not None
                and load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id) is None
                # The rejected final review must be compared with the resolver's
                # current authority head, never RAW.  A bound accepted mutation
                # can legitimately supersede RAW before a historical rescue
                # final is read.  Comparing to RAW makes this predicate false,
                # then the later rejected-rescue compatibility branch restores
                # RAW and emits the same recovery request on every resume.
                and _rejected_repair_final_is_not_current_head(
                    checkpoint_dir, request.display_id, resolved_candidate.candidate,
                )
            ):
                overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                group = _production_review_group(
                    request, loaded=loaded, run=run, checkpoint=checkpoint,
                    existing_raw=existing_raw, candidate_overrides=overrides,
                )
                outcome = _rereview_stale_final_candidate(
                    checkpoint_dir=checkpoint_dir, request=request,
                    candidate=resolved_candidate.candidate, active=active,
                    view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                    qa=qa, bible=bible, group=group,
                    classification="REJECTED_REPAIR_FINAL_VERDICT_NOT_CURRENT_HEAD",
                )
                outcomes.append(outcome)
                continue
            # A valid severe final REPAIR is not itself the production stop.
            # Before emitting a hard block, give a Core-clean RAW head one
            # existing, exact-scope semantic rescue.  The eligibility check
            # separates severity from recoverability and refuses ambiguous
            # continuation membership or empty scope.
            if terminal == "HUMAN_REQUIRED" and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is None:
                review_payload = merged.get("second_review") or merged.get("review")
                stored_working = merged.get("working")
                try:
                    final_review = ReviewResult.model_validate(review_payload) if isinstance(review_payload, Mapping) else None
                    current = TranslationResult.model_validate(stored_working) if isinstance(stored_working, Mapping) else None
                except Exception:
                    final_review = None; current = None
                if current is not None and _candidate_binding(current) == _candidate_binding(raw):
                    current_qa = qa(loaded["views"][request.display_id], request, raw, loaded["known_unit_ids"])
                    group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                    contract = None if final_review is None or not current_qa.get("passed") else _semantic_auto_recovery_contract(
                        request, raw, final_review, bible=bible, group=group,
                    )
                    if contract is not None:
                        def rebuild_group(repaired: TranslationResult):
                            overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                            overrides[request.display_id] = repaired
                            overrides[request.unit_uid] = repaired
                            return _production_review_group(
                                request, loaded=loaded, run=run, checkpoint=checkpoint,
                                existing_raw=existing_raw, candidate_overrides=overrides,
                            )
                        outcome = _run_semantic_rescue(
                            checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                            bible=bible, semantic_context=None if group is None else group.as_reviewer_context_payload(),
                            group=group, prior={"second_review": final_review.model_dump()}, rebuild_group=rebuild_group,
                        )
                        persist_stage_once(
                            checkpoint_dir, "semantic_rescue", request.display_id,
                            _rescue_record(outcome, mode="SEVERE_SEMANTIC_AUTO_RECOVERY", group=group, raw=raw),
                        )
                        try:
                            _commit_accepted_candidate_mutation(
                                checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                                source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                            )
                        except CandidateAuthorityError:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                        outcomes.append(outcome)
                        continue
            # A bounded checkpoint-queue resolution may have exhausted its
            # one repair/review budget and left only release debt.  Classify
            # that already-durable result before any legacy resume/re-review
            # branch so resuming solely to seal cannot spend another provider
            # call or replace its candidate.
            if queue_resolution is not None and terminal == "HUMAN_REQUIRED":
                outcome = _reconstruct_outcome(request, merged, raw)
                if _deferred_semantic_debt_eligible(outcome, checkpoint_dir=checkpoint_dir):
                    try:
                        _commit_accepted_candidate_mutation(
                            checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                            source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                        )
                    except CandidateAuthorityError:
                        return _blocked(
                            handoff_path, worker_id, checkpoint=checkpoint,
                            classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch,
                        )
                    outcomes.append(outcome)
                    continue
            if terminal in SEMANTIC_PASS_TERMINALS or terminal == "HUMAN_REJECTED":
                # A reprocess ACCEPT is reusable only if its local envelope
                # matches the currently rebuilt semantic group. This catches a
                # proven context gap without overwriting history or spending a
                # semantic-repair budget on the invalid attempt.
                if (
                    review_policy == REVIEW_POLICY_V2_FASTPATH
                    and already_reprocessed
                    and load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id) is None
                    and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is None
                    and load_stage(checkpoint_dir, "resolution_queue_auto_dispositions", request.display_id) is None
                    and isinstance(merged.get("working"), Mapping)
                ):
                    candidate = TranslationResult.model_validate(merged["working"])
                    group = _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                    )
                    if not _reprocess_binding_matches_current_group(checkpoint_dir, request, candidate, group):
                        outcome = _rereview_stale_final_candidate(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=candidate, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=qa, bible=bible, group=group,
                            classification="SYSTEMIC_CONTEXT_OR_GROUP_GAP",
                        )
                        outcomes.append(outcome)
                        continue
                outcomes.append(_reconstruct_outcome(request, merged, raw))
                continue
            # A completeness-rejected repair is untrusted and therefore may
            # not replace a complete, bound RAW candidate. Keep both pieces of
            # immutable evidence, restore the last known-good candidate, and
            # let the existing deferred-debt gate decide whether its remaining
            # local issue is safe to defer to release cleanup.
            if (
                semantic_rescue
                and _has_rejected_semantic_rescue_candidate(checkpoint_dir, request, raw)
                and _current_head_corrective_review(
                    checkpoint_dir=checkpoint_dir, request=request,
                    candidate_sha256=_candidate_binding(raw), bible=bible,
                ) is None
            ):
                raw_qa = qa(loaded["views"][request.display_id], request, raw, loaded["known_unit_ids"])
                if not raw_qa.get("passed"):
                    outcomes.append(_reconstruct_outcome(request, merged, raw))
                    continue
                # The rejected repair's review is trigger history, not a
                # review of this restored current RAW head.  Preserve it,
                # but discharge the existing exact-head rereview obligation
                # through the create-once rereview path.
                group = _production_review_group(
                    request, loaded=loaded, run=run, checkpoint=checkpoint,
                    existing_raw=existing_raw,
                )
                outcome = _rereview_stale_final_candidate(
                    checkpoint_dir=checkpoint_dir, request=request, candidate=raw,
                    active=active, view=loaded["views"][request.display_id],
                    known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible,
                    group=group,
                    classification="CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR",
                    authority_raw=raw,
                )
                outcomes.append(outcome)
                continue
            # A queue item first becomes durable as a normal unit result, not
            # as ``production_reprocess``.  Requiring that historical stage
            # here made the checkpoint-boundary AUTO_RESOLUTION path
            # unreachable for every newly produced queue item.  Each actual
            # repair/review remains create-once below, so removing this stale
            # gate does not add a retry loop or permit Translation.
            if terminal == "HUMAN_REQUIRED" and (
                already_reprocessed or review_policy == REVIEW_POLICY_V2_FASTPATH
            ):
                # A D050 registry may be created after RAW was durable.  Re-run
                # the normal Core/Reviewer path once from that same RAW under
                # the now-exact overlay; the immutable request remains intact.
                if (
                    request.verified_footnote_numeric_bindings
                    and load_stage(checkpoint_dir, "d050_preflight_reprocess", request.display_id) is None
                    and isinstance(merged.get("working"), Mapping)
                    and isinstance(merged.get("qa"), Mapping)
                    and not bool(merged["qa"].get("passed"))
                ):
                    candidate = TranslationResult.model_validate(merged["working"])
                    refreshed_qa = qa(loaded["views"][request.display_id], request, candidate, loaded["known_unit_ids"])
                    if refreshed_qa.get("passed"):
                        group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                        semantic_context = None if group is None else group.as_reviewer_context_payload()
                        _persist_reviewer_payload(checkpoint_dir, request, raw, bible=bible, semantic_context=semantic_context, qa=refreshed_qa, reviewer=active.reviewer)
                        outcome, reviewer_contract_evidence = _process_with_bounded_reviewer_contract_retry(
                            checkpoint_dir=checkpoint_dir, request=request, raw=raw, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=qa, bible=bible, semantic_context=semantic_context, review_policy=review_policy,
                        )
                        if reviewer_contract_evidence is not None and outcome is None:
                            raw_path = checkpoint_dir / "translation_raw_response.jsonl"
                            write_worker_handoff(
                                handoff_path, status="BLOCKED", worker_id=worker_id, active_batch=active_batch,
                                checkpoint=checkpoint, next_logical_work_order_id=request.display_id,
                                partial_checkpoint_path=str(checkpoint_dir), stage_reached="REVIEWER",
                                failure_classification=REVIEWER_CONTRACT_FAILURE, failing_work_order_ids=[request.display_id],
                                relevant_qa_artifact_paths=[str(reviewer_contract_evidence)], raw_exists=raw_path.is_file(),
                                raw_sha256=sha256(raw_path.read_bytes()).hexdigest() if raw_path.is_file() else None,
                            )
                            return 2
                        assert outcome is not None
                        try:
                            _commit_accepted_candidate_mutation(
                                checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                                source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                            )
                        except CandidateAuthorityError:
                            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                        persist_stage_once(
                            checkpoint_dir, "d050_preflight_reprocess", request.display_id,
                            _reprocess_record(outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"])),
                        )
                        outcomes.append(outcome)
                        continue
                prior_review_payload = merged.get("second_review") or merged.get("review")
                if isinstance(prior_review_payload, Mapping) and isinstance(merged.get("working"), dict) and isinstance(merged.get("qa"), Mapping):
                    try:
                        prior_review = ReviewResult.model_validate(prior_review_payload)
                        prior_severities = review_issue_severities(issue.code for issue in prior_review.issues)
                        if (
                            bool(merged["qa"].get("passed"))
                            and prior_review.verdict == "REPAIR"
                            and AUTO_POLISH in prior_severities
                            and BLOCKING_SEMANTIC not in prior_severities
                            and load_stage(checkpoint_dir, "auto_polish_resolutions", request.display_id) is None
                        ):
                            persist_stage_once(
                                checkpoint_dir,
                                "auto_polish_resolutions",
                                request.display_id,
                                {
                                    "artifact_type": "PRODUCTION_AUTO_POLISH_RESOLUTION",
                                    "schema_version": 1,
                                    "candidate_binding_sha256": _candidate_binding(TranslationResult.model_validate(merged["working"])),
                                    "working": merged["working"],
                                    "qa": merged["qa"],
                                    "review": merged.get("review"),
                                    "second_review": prior_review.model_dump(),
                                    "review_issue_severities": sorted(prior_severities),
                                    "translation_api_calls": 0,
                                },
                            )
                            outcomes.append(_reconstruct_outcome(
                                request,
                                _project_resolved_unit_record(
                                    _merge_latest_unit_record(checkpoint_dir, request.display_id, stored),
                                    checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                                    resolved_candidate=resolved_candidate, qa=qa,
                                    view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], bible=bible,
                                    current_raw_qa=loaded["current_raw_qa_payload"],
                                ),
                                raw,
                            ))
                            continue
                    except Exception:
                        pass
                # A correctly bound final OMISSION proves that the preceding
                # queue repair deleted source content. Keep that immutable
                # evidence, but do not charge it against the one RAW-derived
                # semantic rescue budget.
                if semantic_rescue:
                    rebuilt_context_group = _production_review_group(
                        request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                    )
                    if _queue_repair_is_invalid_context_attempt(
                        checkpoint_dir, request.display_id, rebuilt_context_group,
                    ):
                        if load_stage(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id) is None:
                            persist_stage_once(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id, {
                                "artifact_type": "SYSTEMIC_CONTEXT_GAP", "schema_version": 1,
                                "classification": "MISSING_TEXTBOX_CONTINUATION_CONTEXT",
                                "display_id": request.display_id, "unit_uid": request.unit_uid,
                                "raw_candidate_binding_sha256": _candidate_binding(raw),
                                "rebuilt_semantic_group_hash": rebuilt_context_group.group_hash,
                                "next_context_unit_uid": rebuilt_context_group.context.next_semantic_unit_uid,
                                "translation_api_calls": 0,
                            })
                        outcome = _rereview_stale_final_candidate(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=raw, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=qa, bible=bible, group=rebuilt_context_group,
                            classification="SYSTEMIC_CONTEXT_GAP",
                        )
                        outcomes.append(outcome)
                        continue
                if semantic_rescue and _queue_repair_is_invalid_omission_attempt(checkpoint_dir, request.display_id):
                    if load_stage(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id) is None:
                        invalid = load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id) or {}
                        invalid_candidate = invalid.get("working") if isinstance(invalid, Mapping) else None
                        persist_stage_once(checkpoint_dir, "semantic_rescue_binding_audits", request.display_id, {
                            "artifact_type": "INVALID_ATTEMPT_FOR_BUDGET", "schema_version": 1,
                            "classification": "REPAIR_OMISSION", "display_id": request.display_id,
                            "unit_uid": request.unit_uid,
                            "raw_candidate_binding_sha256": _candidate_binding(raw),
                            "invalid_repair_candidate_binding_sha256": _candidate_binding(TranslationResult.model_validate(invalid_candidate)) if isinstance(invalid_candidate, Mapping) else None,
                            "reason": "BOUND_FINAL_REVIEW_PROVED_SOURCE_CLAUSE_OMISSION",
                            "translation_api_calls": 0,
                        })
                    original_review = merged.get("review")
                    if not isinstance(original_review, Mapping):
                        outcomes.append(_reconstruct_outcome(request, merged, raw))
                        continue
                    group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                    context = None if group is None else group.as_reviewer_context_payload()
                    def rebuild_group(repaired: TranslationResult):
                        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                        overrides[request.display_id] = repaired
                        overrides[request.unit_uid] = repaired
                        return _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                    rescue_prior = dict(merged)
                    rescue_prior["second_review"] = original_review
                    outcome = _run_semantic_rescue(
                        checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
                        view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa,
                        bible=bible, semantic_context=context, group=group, prior=rescue_prior,
                        rebuild_group=rebuild_group,
                    )
                    persist_stage_once(checkpoint_dir, "semantic_rescue", request.display_id, _rescue_record(outcome, mode="SEMANTIC_REPAIR", group=group, raw=raw))
                    outcomes.append(outcome)
                    continue
                # A blocking reviewer finding with no permitted segment scope
                # cannot enter the repair gate.  Refresh that exact RAW head
                # once against the rebuilt SemanticReviewGroup; this is the
                # existing reviewer-only rescue followed by the existing
                # one-shot semantic rescue, never a translation retry.
                if (
                    semantic_rescue
                    and load_stage(checkpoint_dir, "semantic_rescue", request.display_id) is None
                    and load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id) is None
                    and isinstance(prior_review_payload, Mapping)
                ):
                    try:
                        scoped_review = ReviewResult.model_validate(prior_review_payload)
                        needs_scope_refresh = (
                            scoped_review.verdict == "REPAIR"
                            and BLOCKING_SEMANTIC in review_issue_severities(issue.code for issue in scoped_review.issues)
                            and any(not issue.affected_segment_indices for issue in scoped_review.issues)
                        )
                    except Exception:
                        needs_scope_refresh = False
                    if needs_scope_refresh:
                        group = _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                        )
                        rereview = _rereview_stale_final_candidate(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=raw, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=qa, bible=bible, group=group,
                            classification="CURRENT_BOUND_REPAIR_SCOPE_REFRESH",
                        )
                        refreshed = rereview.review
                        if (
                            refreshed is not None
                            and refreshed.verdict == "REPAIR"
                            and all(issue.affected_segment_indices for issue in refreshed.issues)
                        ):
                            def rebuild_group(repaired: TranslationResult):
                                overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                                overrides[request.display_id] = repaired
                                overrides[request.unit_uid] = repaired
                                return _production_review_group(
                                    request, loaded=loaded, run=run, checkpoint=checkpoint,
                                    existing_raw=existing_raw, candidate_overrides=overrides,
                                )
                            outcome = _run_semantic_rescue(
                                checkpoint_dir=checkpoint_dir, request=request, base=raw, active=active,
                                view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                                qa=qa, bible=bible, semantic_context=group.as_reviewer_context_payload(), group=group,
                                prior={"second_review": refreshed.model_dump()}, rebuild_group=rebuild_group,
                            )
                            persist_stage_once(
                                checkpoint_dir, "semantic_rescue", request.display_id,
                                _rescue_record(outcome, mode="CURRENT_BOUND_SCOPE_REFRESH", group=group, raw=raw),
                            )
                            outcomes.append(outcome)
                        else:
                            outcomes.append(rereview)
                        continue
                # The checkpoint queue is resolved after ordinary unit work.
                # One current, blocking review may spend the existing bounded
                # AUTO_RESOLUTION budget; it never calls Translation.
                if (
                    review_policy == REVIEW_POLICY_V2_FASTPATH
                    and load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id) is None
                    and isinstance(prior_review_payload, Mapping)
                    and isinstance(merged.get("working"), Mapping)
                    and isinstance(merged.get("qa"), Mapping)
                    and bool(merged["qa"].get("passed"))
                ):
                    try:
                        valid_review = ReviewResult.model_validate(prior_review_payload)
                        severities = review_issue_severities(issue.code for issue in valid_review.issues)
                    except Exception:
                        valid_review = None
                        severities = frozenset()
                    if valid_review is not None and valid_review.verdict == "REPAIR" and BLOCKING_SEMANTIC in severities:
                        candidate = TranslationResult.model_validate(merged["working"])
                        def rebuild_queue_group(repaired: TranslationResult):
                            overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                            overrides[request.display_id] = repaired
                            overrides[request.unit_uid] = repaired
                            return _production_review_group(
                                request, loaded=loaded, run=run, checkpoint=checkpoint,
                                existing_raw=existing_raw, candidate_overrides=overrides,
                            )
                        outcome = _run_resolution_queue_auto_resolution(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=candidate,
                            review=valid_review, active=active, view=loaded["views"][request.display_id],
                            known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible,
                            rebuild_group=rebuild_queue_group, mode="CHECKPOINT_QUEUE_AUTO_RESOLUTION",
                        )
                        outcomes.append(outcome)
                        continue
                rescue = load_stage(checkpoint_dir, "semantic_rescue", request.display_id)
                if rescue is not None:
                    rereview = load_stage(checkpoint_dir, "semantic_rescue_rereviews", request.display_id)
                    # A deterministic false positive can leave an earlier
                    # RAW-only rescue record with no review or mutation.  Its
                    # exact current candidate is Core-clean now, so obtain one
                    # bound reviewer verdict instead of treating that inert
                    # record as a semantic repair failure.
                    if (
                        semantic_rescue
                        and rereview is None
                        and rescue.get("repair_applied") is False
                        and rescue.get("review") is None
                        and rescue.get("second_review") is None
                    ):
                        candidate = _rescue_current_working(checkpoint_dir, request.display_id, rescue) or raw
                        group = _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
                        )
                        outcome = _rereview_stale_final_candidate(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=candidate, active=active,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                            qa=qa, bible=bible, group=group,
                            classification="DETERMINISTIC_FALSE_POSITIVE_REVIEW",
                        )
                        outcomes.append(outcome)
                        continue
                    if semantic_rescue and request.display_id in D051_FINAL_REVIEW_IDS and rereview is None:
                        candidate = _rescue_current_working(checkpoint_dir, request.display_id, rescue)
                        equivalence = None if candidate is None else _d051_equivalence_for(checkpoint_dir, request, candidate, bible=bible)
                        if candidate is not None and equivalence is not None:
                            d051_qa = _qa_with_d051(qa, loaded["views"][request.display_id], request, candidate, loaded["known_unit_ids"], equivalence)
                            if load_stage(checkpoint_dir, "d051_qa", request.display_id) is None:
                                persist_stage_once(checkpoint_dir, "d051_qa", request.display_id, {"artifact_type": "D051_APPROVED_DETERMINISTIC_QA", "schema_version": 1, "qa": d051_qa, "candidate_binding_sha256": _candidate_binding(candidate), "translation_api_calls": 0})
                            if d051_qa.get("passed"):
                                overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                                overrides[request.display_id] = candidate
                                overrides[request.unit_uid] = candidate
                                rebuilt_group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                                outcome = _rereview_stale_final_candidate(
                                    checkpoint_dir=checkpoint_dir, request=request, candidate=candidate, active=active,
                                    view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                                    qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, equivalence),
                                    bible=bible, group=rebuilt_group, classification="D051_APPROVED",
                                )
                                outcomes.append(outcome)
                                continue
                    if request.display_id in D051_FINAL_REVIEW_IDS and rereview is not None:
                        candidate = _rescue_current_working(checkpoint_dir, request.display_id, rescue)
                        equivalence = None if candidate is None else _d051_equivalence_for(checkpoint_dir, request, candidate, bible=bible)
                        review = ReviewResult.model_validate(rereview["review"])
                        if candidate is not None and equivalence is not None and AUTO_POLISH in review_issue_severities(issue.code for issue in review.issues) and BLOCKING_SEMANTIC not in review_issue_severities(issue.code for issue in review.issues):
                            overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                            overrides[request.display_id] = candidate
                            overrides[request.unit_uid] = candidate
                            group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw, candidate_overrides=overrides)
                            outcome = _run_auto_polish(
                                checkpoint_dir=checkpoint_dir, request=request, candidate=candidate, review=review, active=active,
                                view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"],
                                qa=lambda view, req, result, known: _qa_with_d051(qa, view, req, result, known, equivalence), bible=bible, group=group,
                            )
                            outcomes.append(outcome)
                            continue
                    # Historical rescue final reviews predating the local binding
                    # envelope are not reusable.  Re-review only non-terminology
                    # semantic findings: a terminology conflict still needs an
                    # authoritative decision before another model call can resolve it.
                    saved_final = load_stage(checkpoint_dir, "semantic_rescue_final_reviews", request.display_id)
                    prior_final = saved_final.get("review") if isinstance(saved_final, Mapping) else None
                    prior_codes = set()
                    if isinstance(prior_final, Mapping):
                        try:
                            prior_codes = {issue.code for issue in ReviewResult.model_validate(prior_final).issues}
                        except Exception:
                            prior_codes = set()
                    no_bound_final_request = load_stage(checkpoint_dir, "semantic_rescue_final_review_requests", request.display_id) is None
                    if (
                        (semantic_rescue or review_policy == REVIEW_POLICY_V2_FASTPATH)
                        and rereview is None
                        and saved_final is not None
                        and no_bound_final_request
                        and "TERM_MISTRANSLATION" not in prior_codes
                    ):
                        candidate = _rescue_current_working(checkpoint_dir, request.display_id, rescue)
                        if candidate is None:
                            outcomes.append(_reconstruct_outcome(request, merged, raw))
                            continue
                        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                        overrides[request.display_id] = candidate
                        overrides[request.unit_uid] = candidate
                        rebuilt_group = _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint,
                            existing_raw=existing_raw, candidate_overrides=overrides,
                        )
                        outcome = _rereview_stale_final_candidate(
                            checkpoint_dir=checkpoint_dir, request=request, candidate=candidate,
                            active=active, view=loaded["views"][request.display_id],
                            known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible,
                            group=rebuilt_group,
                        )
                        outcomes.append(outcome)
                        continue
                    if (semantic_rescue or review_policy == REVIEW_POLICY_V2_FASTPATH) and load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id) is None:
                        candidate = _rescue_current_working(checkpoint_dir, request.display_id, rescue)
                        valid_review = None
                        mode = None
                        if rereview is not None and isinstance(rereview.get("review"), Mapping):
                            valid_review = ReviewResult.model_validate(rereview["review"])
                            if valid_review.verdict == "REPAIR":
                                mode = "VALID_REREVIEW_CORRECTIVE"
                        elif isinstance(prior_final, Mapping):
                            possible_review = ReviewResult.model_validate(prior_final)
                            if (
                                possible_review.verdict == "REPAIR"
                                and "TERM_MISTRANSLATION" in {issue.code for issue in possible_review.issues}
                                and _is_source_canonical_name(request)
                            ):
                                valid_review = possible_review
                                mode = "CLOSED_WORLD_CANONICAL_NAME"
                        if candidate is not None and valid_review is not None and mode is not None:
                            def rebuild_queue_group(repaired: TranslationResult):
                                overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                                overrides[request.display_id] = repaired
                                overrides[request.unit_uid] = repaired
                                return _production_review_group(
                                    request, loaded=loaded, run=run, checkpoint=checkpoint,
                                    existing_raw=existing_raw, candidate_overrides=overrides,
                                )
                            outcome = _run_resolution_queue_auto_resolution(
                                checkpoint_dir=checkpoint_dir, request=request, candidate=candidate,
                                review=valid_review, active=active, view=loaded["views"][request.display_id],
                                known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible,
                                rebuild_group=rebuild_queue_group, mode=mode,
                            )
                            outcomes.append(outcome)
                            continue
                    outcomes.append(_reconstruct_outcome(
                        request,
                        _project_resolved_unit_record(
                            _merge_latest_unit_record(checkpoint_dir, request.display_id, stored),
                            checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                            resolved_candidate=resolved_candidate, qa=qa,
                            view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], bible=bible,
                            current_raw_qa=loaded["current_raw_qa_payload"],
                        ),
                        raw,
                    ))
                    continue
                prior_review_payload = merged.get("second_review") or merged.get("review")
                low_risk_auto_resume = False
                if isinstance(prior_review_payload, Mapping):
                    try:
                        prior_review = ReviewResult.model_validate(prior_review_payload)
                        prior_severities = review_issue_severities(issue.code for issue in prior_review.issues)
                        low_risk_auto_resume = (
                            prior_review.verdict == "REPAIR"
                            and AUTO_POLISH in prior_severities
                            and BLOCKING_SEMANTIC not in prior_severities
                        )
                    except Exception:
                        low_risk_auto_resume = False
                current_fastpath_trigger = _current_semantic_rescue_repair_trigger(
                    UnitOutcome(
                        request=request,
                        decision=route_semantic_unit(request),
                        raw=raw,
                        working=resolved_candidate.candidate,
                        qa=qa(loaded["views"][request.display_id], request, resolved_candidate.candidate, loaded["known_unit_ids"]),
                    ),
                    checkpoint_dir=checkpoint_dir,
                    current=resolved_candidate.candidate,
                )
                if not semantic_rescue and review_policy != REVIEW_POLICY_V2_FASTPATH and not low_risk_auto_resume:
                    outcomes.append(_reconstruct_outcome(request, merged, raw))
                    continue
                if semantic_rescue or (
                    review_policy == REVIEW_POLICY_V2_FASTPATH
                    and load_stage(checkpoint_dir, "resolution_queue_auto_resolutions", request.display_id) is None
                    and current_fastpath_trigger is not None
                ):
                    group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
                    context = None if group is None else group.as_reviewer_context_payload()
                    base = TranslationResult.model_validate(merged["working"]) if isinstance(merged.get("working"), dict) else raw
                    def rebuild_group(repaired: TranslationResult):
                        overrides = _rescue_working_overrides(checkpoint_dir, loaded, existing_raw)
                        overrides[request.display_id] = repaired
                        overrides[request.unit_uid] = repaired
                        return _production_review_group(
                            request, loaded=loaded, run=run, checkpoint=checkpoint,
                            existing_raw=existing_raw, candidate_overrides=overrides,
                        )
                    outcome = _run_semantic_rescue(checkpoint_dir=checkpoint_dir, request=request, base=base, active=active, view=loaded["views"][request.display_id], known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible, semantic_context=context, group=group, prior=merged, rebuild_group=rebuild_group)
                    persist_stage_once(checkpoint_dir, "semantic_rescue", request.display_id, _rescue_record(outcome, mode="REVIEW_ONLY" if request.display_id in SEMANTIC_RESCUE_B_REVIEW_ONLY else "SEMANTIC_REPAIR", group=group, raw=raw))
                    outcomes.append(outcome)
                    continue
            group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
            semantic_context = None if group is None else group.as_reviewer_context_payload()
            if group is not None and load_stage(checkpoint_dir, "review_groups", request.display_id) is None:
                persist_stage_once(
                    checkpoint_dir,
                    "review_groups",
                    request.display_id,
                    {"artifact_type": "PRODUCTION_REVIEW_GROUP", "schema_version": 1, **group.as_payload()},
                )
            _persist_reviewer_payload(checkpoint_dir, request, raw, bible=bible, semantic_context=semantic_context, qa=stored.get("qa") if isinstance(stored.get("qa"), dict) else None, reviewer=active.reviewer)
            outcome, reviewer_contract_evidence = _process_with_bounded_reviewer_contract_retry(
                checkpoint_dir=checkpoint_dir,
                request=request,
                raw=raw,
                active=active,
                view=loaded["views"][request.display_id],
                known_unit_ids=loaded["known_unit_ids"],
                qa=qa,
                bible=bible,
                semantic_context=semantic_context,
                review_policy=review_policy,
            )
            if reviewer_contract_evidence is not None and outcome is None:
                raw_path = checkpoint_dir / "translation_raw_response.jsonl"
                write_worker_handoff(
                    handoff_path,
                    status="BLOCKED",
                    worker_id=worker_id,
                    active_batch=active_batch,
                    checkpoint=checkpoint,
                    next_logical_work_order_id=request.display_id,
                    partial_checkpoint_path=str(checkpoint_dir),
                    stage_reached="REVIEWER",
                    failure_classification=REVIEWER_CONTRACT_FAILURE,
                    failing_work_order_ids=[request.display_id],
                    relevant_qa_artifact_paths=[str(reviewer_contract_evidence)],
                    raw_exists=raw_path.is_file(),
                    raw_sha256=sha256(raw_path.read_bytes()).hexdigest() if raw_path.is_file() else None,
                )
                return 2
            assert outcome is not None
            try:
                _commit_accepted_candidate_mutation(
                    checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                    source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                )
            except CandidateAuthorityError:
                return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
            persist_stage_once(
                checkpoint_dir,
                "production_reprocess",
                request.display_id,
                _reprocess_record(outcome, source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"])),
            )
            if load_stage(checkpoint_dir, "human_required_triage", request.display_id) is None:
                persist_stage_once(
                    checkpoint_dir,
                    "human_required_triage",
                    request.display_id,
                    {
                        "artifact_type": "HUMAN_REQUIRED_TRIAGE",
                        "schema_version": 1,
                        "display_id": request.display_id,
                        "unit_uid": request.unit_uid,
                        **classify_human_required(
                            required_context=semantic_context or {},
                            persisted_payload=load_stage(checkpoint_dir, "reviewer_payloads", request.display_id),
                            reprocess_terminal=semantic_terminal_of_outcome(outcome),
                            auto_repair_exhausted=outcome.human_required,
                        ),
                    },
                )
            outcomes.append(outcome)
            continue
        candidate = None
        live_translation = False
        if raw is None:
            human_initial = load_stage(checkpoint_dir, "human_approved_candidates", request.display_id)
            if isinstance(human_initial, Mapping) and human_initial.get("origin") == "HUMAN_INITIAL_CANDIDATE":
                try:
                    raw = TranslationResult.model_validate(human_initial["working"])
                    if (
                        raw.unit_uid != request.unit_uid
                        or human_initial.get("source_sha256") != request.source_sha256
                        or human_initial.get("request_binding_sha256") != _provider_request_for_decision(
                            request, bible=bible, decision=route_semantic_unit(request)
                        ).request_binding_sha256
                        or human_initial.get("candidate_binding_sha256") != _candidate_binding(raw)
                        or not isinstance(human_initial.get("qa"), Mapping)
                        or human_initial["qa"].get("passed") is not True
                    ):
                        raise ValueError("HUMAN_INITIAL_CANDIDATE_BINDING_INVALID")
                except (KeyError, TypeError, ValueError):
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="HUMAN_INITIAL_CANDIDATE_BINDING_INVALID", active_batch=active_batch)
                # This is not provider RAW and must never be appended to it.
                # Its human terminal is already bound evidence, so no model
                # reviewer or repair path is invoked.
                existing_raw[request.unit_uid] = raw
                outcome = UnitOutcome(
                    request=request, decision=route_semantic_unit(request), raw=raw, working=raw,
                    qa=dict(human_initial["qa"]), final_status="HUMAN_APPROVED",
                    semantic_terminal="HUMAN_APPROVED", recovered_from="human_initial_candidate",
                )
                try:
                    _commit_accepted_candidate_mutation(
                        checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                        source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
                    )
                except CandidateAuthorityError:
                    return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
                if not unit_path(checkpoint_dir, request.display_id).exists():
                    persist_unit_result(unit_path(checkpoint_dir, request.display_id), {
                        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
                        "display_id": request.display_id, "unit_uid": request.unit_uid,
                        "route": outcome.decision.route, "source_sha256": str(loaded["source_sha256"]),
                        "selection_sha256": str(loaded["selection_sha256"]), "working": raw.model_dump(),
                        "qa": outcome.qa, "final_status": "HUMAN_APPROVED", "semantic_terminal": "HUMAN_APPROVED",
                        "translation_api_calls": 0, "reviewer_api_calls": 0, "repair_api_calls": 0,
                        "recovered_from": "human_initial_candidate",
                    })
                outcomes.append(outcome)
                continue
            decision = route_semantic_unit(request)
            try:
                candidate, raw, provider_contract_evidence = _translate_with_bounded_provider_contract_retry(
                    checkpoint_dir=checkpoint_dir, run=run, request=request, bible=bible, active=active, decision=decision,
                )
            except Exception as error:
                from booktrans.providers.failures import classify_provider_exception

                classified = classify_provider_exception(error)
                print(f"{classified.family} {classified.code}", flush=True)
                if classified.family == "PROVIDER_TRANSIENT":
                    return 5
                write_worker_handoff(
                    handoff_path,
                    status="BLOCKED",
                    worker_id=worker_id,
                    active_batch=active_batch,
                    checkpoint=checkpoint,
                    next_logical_work_order_id=request.display_id,
                    failure_classification=classified.family,
                    stage_reached="PROVIDER",
                    failing_work_order_ids=[request.display_id],
                    relevant_qa_artifact_paths=[str(handoff_path)],
                    raw_exists=bool(existing_raw),
                    partial_checkpoint_path=str(checkpoint_dir) if existing_raw else None,
                )
                return 2
            if raw is None or candidate is None:
                # A fresh unit with no validator-accepted RAW has no safe
                # authoritative candidate. This is intentionally distinct
                # from the provider's non-semantic contract diagnosis.
                terminal_evidence = _persist_no_safe_authoritative_candidate_terminal(
                    checkpoint_dir,
                    request=request,
                    bible=bible,
                    decision=decision,
                    provider_contract_evidence=provider_contract_evidence,
                )
                write_worker_handoff(
                    handoff_path,
                    status="BLOCKED",
                    worker_id=worker_id,
                    active_batch=active_batch,
                    checkpoint=checkpoint,
                    next_logical_work_order_id=request.display_id,
                    failure_classification="NO_SAFE_AUTHORITATIVE_CANDIDATE",
                    stage_reached="PROVIDER",
                    failing_work_order_ids=[request.display_id],
                    relevant_qa_artifact_paths=[str(path) for path in (terminal_evidence, provider_contract_evidence) if path],
                    raw_exists=bool(existing_raw),
                    partial_checkpoint_path=str(checkpoint_dir) if existing_raw else None,
                )
                return 2
            provenance = candidate_provenance(candidate, decision)
            if request.unit_uid in historical_raw_uids:
                # The base RAW is immutable historical evidence for a
                # superseded request.  The replacement acquisition has a
                # separate, exact effective-request binding.
                persist_request_bound_provider_reacquisition(
                    checkpoint_dir=checkpoint_dir, request=request,
                    request_binding_sha256=request_bindings[request.unit_uid],
                    effective_request_artifact_sha256=sha256(effective_request_path.read_bytes()).hexdigest(),
                    source_sha256=str(loaded["source_sha256"]), batch_id=active_batch, checkpoint_id=checkpoint,
                    selection_sha256=str(loaded["selection_sha256"]), manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
                    raw=raw, provenance=provenance,
                )
            else:
                append_raw_response_line(checkpoint_dir / "translation_raw_response.jsonl", raw)
                append_jsonl_fsync(checkpoint_dir / "translation_provider_provenance.jsonl", provenance)
            existing_raw[request.unit_uid] = raw
            live_translation = True
        group = _production_review_group(request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw)
        semantic_context = None if group is None else group.as_reviewer_context_payload()
        _persist_reviewer_payload(
            checkpoint_dir,
            request,
            raw,
            bible=bible,
            semantic_context=semantic_context,
            qa=None,
            reviewer=active.reviewer,
        )
        outcome, reviewer_contract_evidence = _process_with_bounded_reviewer_contract_retry(
            checkpoint_dir=checkpoint_dir,
            request=request,
            raw=raw,
            active=active,
            view=loaded["views"][request.display_id],
            known_unit_ids=loaded["known_unit_ids"],
            qa=qa,
            bible=bible,
            semantic_context=semantic_context,
            existing_candidate=candidate,
            review_policy=review_policy,
        )
        if reviewer_contract_evidence is not None and outcome is None:
            raw_path = checkpoint_dir / "translation_raw_response.jsonl"
            write_worker_handoff(
                handoff_path,
                status="BLOCKED",
                worker_id=worker_id,
                active_batch=active_batch,
                checkpoint=checkpoint,
                next_logical_work_order_id=request.display_id,
                partial_checkpoint_path=str(checkpoint_dir),
                stage_reached="REVIEWER",
                failure_classification=REVIEWER_CONTRACT_FAILURE,
                failing_work_order_ids=[request.display_id],
                relevant_qa_artifact_paths=[str(reviewer_contract_evidence)],
                raw_exists=raw_path.is_file(),
                raw_sha256=sha256(raw_path.read_bytes()).hexdigest() if raw_path.is_file() else None,
            )
            return 2
        assert outcome is not None
        if live_translation:
            outcome.translation_api_calls = 1
            outcome.candidate = candidate
        # A material REPAIR on a Core-clean current RAW head is not yet a
        # human terminal. Spend the existing checkpoint-bound rescue now,
        # rather than serializing HUMAN_REQUIRED and waiting for a resume.
        if outcome.human_required:
            rescued = _run_eligible_current_head_semantic_rescue(
                checkpoint_dir=checkpoint_dir, request=request, raw=raw,
                prior={
                    "review": None if outcome.review is None else outcome.review.model_dump(),
                    "second_review": None if outcome.second_review is None else outcome.second_review.model_dump(),
                    "failure_family": outcome.failure_family,
                    "failure_code": outcome.failure_code,
                },
                active=active, loaded=loaded, run=run, checkpoint=checkpoint,
                existing_raw=existing_raw, qa=qa, bible=bible,
            )
            if rescued is not None:
                # The rescue is RAW-derived: retain the original translation
                # call accounting and candidate provenance.
                rescued.translation_api_calls = outcome.translation_api_calls
                rescued.candidate = outcome.candidate
                outcome = rescued
        try:
            _commit_accepted_candidate_mutation(
                checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
                source_sha256=str(loaded["source_sha256"]), selection_sha256=str(loaded["selection_sha256"]), bible=bible,
            )
        except CandidateAuthorityError:
            return _blocked(handoff_path, worker_id, checkpoint=checkpoint, classification="CANDIDATE_LINEAGE_INTEGRITY_HARD_STOP", active_batch=active_batch)
        if not unit_path(checkpoint_dir, request.display_id).exists():
            persist_unit_result(
                unit_path(checkpoint_dir, request.display_id),
                {
                    "artifact_type": "PRODUCTION_UNIT_RESULT",
                    "schema_version": 1,
                    "display_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "route": outcome.decision.route,
                    "source_sha256": str(loaded["source_sha256"]),
                    "selection_sha256": str(loaded["selection_sha256"]),
                    "working": None if outcome.working is None else outcome.working.model_dump(),
                    "qa": outcome.qa,
                    "review": None if outcome.review is None else outcome.review.model_dump(),
                    "second_review": None if outcome.second_review is None else outcome.second_review.model_dump(),
                    "model_echo_unit_uid": outcome.review_model_echo_unit_uid,
                    "model_echo_diagnostic": "MODEL_ECHO_ID_MISMATCH" if outcome.review_model_echo_id_mismatch else None,
                    "final_status": outcome.final_status,
                    "failure_family": outcome.failure_family,
                    "failure_code": outcome.failure_code,
                    "semantic_terminal": semantic_terminal_of_outcome(outcome),
                    "human_required": outcome.human_required,
                    "repair_applied": outcome.repair_applied,
                    "repair_policy_id": outcome.repair_policy_id,
                    "reviewer_dispatch": outcome.reviewer_dispatch,
                    "translation_api_calls": outcome.translation_api_calls,
                    "reviewer_api_calls": outcome.reviewer_api_calls,
                    "repair_api_calls": outcome.repair_api_calls,
                    "recovered_from": outcome.recovered_from,
                },
            )
        outcomes.append(outcome)

    # Seal is the final barrier.  Before it can interpret immutable RAW QA,
    # project every Core-clean authoritative descendant through its exact
    # current-head review transition.  Historical terminals never short-circuit
    # a different current CA head.
    current_head_corrective_pending: list[tuple[UnitOutcome, ReviewResult, str]] = []
    for index, item in enumerate(outcomes):
        if item.raw is None:
            continue
        try:
            current = _resolve_checkpoint_authoritative_candidate(
                checkpoint_dir, item.request.display_id, item.raw,
            )
        except CandidateAuthorityError:
            continue
        current_qa = _qa_with_d051(
            qa, loaded["views"][item.request.display_id], item.request, current.candidate,
            loaded["known_unit_ids"], _d051_equivalence_for(checkpoint_dir, item.request, current.candidate, bible=bible),
        )
        if (
            current.candidate_sha256 == _candidate_binding(item.raw)
            or not current_qa.get("passed")
            or _current_candidate_reviewer_accept(checkpoint_dir=checkpoint_dir, request=item.request, candidate_sha256=current.candidate_sha256, bible=bible) is not None
        ):
            continue
        group = _production_review_group(
            item.request, loaded=loaded, run=run, checkpoint=checkpoint, existing_raw=existing_raw,
            candidate_overrides={
                item.request.display_id: current.candidate,
                item.request.unit_uid: current.candidate,
            },
        )
        if group is None:
            continue
        outcomes[index] = _rereview_stale_final_candidate(
            checkpoint_dir=checkpoint_dir, request=item.request, candidate=current.candidate,
            authority_raw=item.raw, active=active, view=loaded["views"][item.request.display_id],
            known_unit_ids=loaded["known_unit_ids"], qa=qa, bible=bible, group=group,
            classification="CURRENT_HEAD_SEMANTIC_ACCEPTANCE_REQUIRED",
            review_stage="semantic_rescue_corrective_final_reviews",
            review_request_stage="semantic_rescue_corrective_final_review_requests",
        )
        # A REPAIR produced by the exact-current-head final-review lane is
        # executable work for the existing current-head corrective transition.
        # It must be scheduled before the immutable historical RAW-QA barrier:
        # RAW remains read-only evidence and cannot certify or suppress this
        # unfinished current-head transition.
        current_head_repair = _current_head_corrective_review(
            checkpoint_dir=checkpoint_dir, request=item.request,
            candidate_sha256=current.candidate_sha256, bible=bible,
        )
        if current_head_repair is not None:
            current_head_corrective_pending.append((item, current_head_repair, current.candidate_sha256))
    # Reproject transition outcomes through canonical disposition below.  Do
    # not carry an old selected corrective contract across a legal authority
    # advance; a later worker can execute only a newly projected, exact-bound
    # obligation.
    current_head_corrective_pending = [
        pending for pending in current_head_corrective_pending
        if pending[0].request.display_id not in authoritative_transitions
    ]
    # CURRENT_PARTIAL_RESUME executes the one canonical recovery contract
    # captured at entry.  Historical/current-head review artifacts remain
    # audit evidence here; they cannot manufacture a second current action
    # after the projector has resolved the checkpoint to debt or another
    # terminal disposition.
    current_head_corrective_pending = [
        pending for pending in current_head_corrective_pending
        if _selected_current_recovery_execution_family(
            selected_current_recovery,
            request=pending[0].request,
            candidate_sha256=pending[2],
            review=pending[1],
        ) == "SEMANTIC_CORRECTIVE"
    ]
    if current_head_corrective_pending:
        first, first_review, first_head_sha256 = current_head_corrective_pending[0]
        raw_path = checkpoint_dir / "translation_raw_response.jsonl"
        raw_sha = sha256(raw_path.read_bytes()).hexdigest() if raw_path.is_file() else ""
        write_worker_handoff(
            handoff_path,
            status="RECOVERY_REQUIRED",
            worker_id=worker_id,
            active_batch=active_batch,
            checkpoint=checkpoint,
            partial_checkpoint_path=str(checkpoint_dir),
            stage_reached="SEMANTIC_TERMINAL",
            failure_classification="CURRENT_HEAD_RECOVERY_REQUIRED",
            failing_work_order_ids=[item.request.display_id for item, _review, _head in current_head_corrective_pending],
            relevant_qa_artifact_paths=[
                str(checkpoint_dir / "translation_raw_qa_report.json"),
                str(checkpoint_dir / "semantic_rescue_corrective_final_reviews" / f"{first.request.display_id}.json"),
            ],
            raw_exists=raw_path.is_file(),
            raw_sha256=raw_sha or None,
            production_disposition="RECOVERY_REQUIRED",
            disposition_reason="CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE",
            disposition_evidence_references=[
                str(checkpoint_dir / "translation_raw_qa_report.json"),
                str(checkpoint_dir / "semantic_rescue_corrective_final_reviews" / f"{first.request.display_id}.json"),
            ],
            recovery_family="SEMANTIC_CORRECTIVE",
            recovery_fingerprint={
                "batch": active_batch,
                "checkpoint": checkpoint,
                "work_order": first.request.display_id,
                "current_head_sha256": first_head_sha256,
                "recovery_family": "SEMANTIC_CORRECTIVE",
                "canonical_reason": "CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_ELIGIBLE",
                "affected_segment_indices": sorted({index for issue in first_review.issues for index in issue.affected_segment_indices}),
                "bound_review_result_sha256": _canonical_sha256(first_review.model_dump()),
                "recovery_budget_used": int(load_stage(checkpoint_dir, "semantic_rescue_current_head_corrective", first.request.display_id) is not None),
            },
        )
        return 3
    # From this point onward CURRENT control, handoff, and finalization use
    # one exactly-complete projection.  Do not reconstruct a competing
    # outcome from ``units/`` or any legacy recovery family below this line.
    try:
        outcomes = _project_complete_current_outcomes(
            checkpoint_dir=checkpoint_dir,
            selected_requests=loaded["requests"],
            outcomes=outcomes,
            qa=qa,
            views=loaded["views"],
            known_unit_ids=loaded["known_unit_ids"],
            bible=bible,
            current_raw_qa=loaded["current_raw_qa_payload"],
        )
        current_outcomes_by_work_order = _materialize_current_outcomes_by_work_order(
            checkpoint_dir=checkpoint_dir,
            selected_requests=loaded["requests"],
            outcomes=outcomes,
        )
    except RuntimeError as exc:
        return _blocked(
            handoff_path, worker_id, checkpoint=checkpoint,
            classification=str(exc), active_batch=active_batch,
        )
    outcomes = [current_outcomes_by_work_order[request.display_id] for request in loaded["requests"]]
    if len(current_outcomes_by_work_order) != expected:
        return _blocked(
            handoff_path, worker_id, checkpoint=checkpoint,
            classification="CURRENT_OUTCOME_COUNT_MISMATCH", active_batch=active_batch,
        )
    raw_path = checkpoint_dir / "translation_raw_response.jsonl"
    raw_sha = sha256(raw_path.read_bytes()).hexdigest() if raw_path.is_file() else ""
    # ``registry_decision`` was set by the canonical projection above.  The
    # tail must consume it, rather than re-evaluating historical terminal
    # fields to manufacture another UnitOutcome/control policy.
    dispositions = {
        item.request.display_id: dict(item.registry_decision) for item in outcomes
    }
    if any(not decision for decision in dispositions.values()):
        return _blocked(
            handoff_path, worker_id, checkpoint=checkpoint,
            classification="CURRENT_OUTCOME_DISPOSITION_MISSING", active_batch=active_batch,
        )
    unresolved = [
        item for item in outcomes
        if dispositions[item.request.display_id]["disposition"] != "CONTINUE"
    ]
    # Production and release have distinct gates.  A uniquely-resolved,
    # Core-clean head carries every non-unsafe semantic/protocol remainder as
    # one append-only release debt.  Older specialized contract-debt records
    # remain readable historical evidence; the hot path no longer has three
    # competing continuation decisions.
    deferred_semantic_debts = [
        item for item in unresolved
        if dispositions[item.request.display_id]["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    ]
    recovery_required = [
        item for item in unresolved
        if dispositions[item.request.display_id]["disposition"] == "RECOVERY_REQUIRED"
    ]
    # Keep the older, specialized immutable artifacts for compatibility and
    # diagnostics.  They no longer decide whether a safe current head stops
    # production; that decision is the single list above.
    reviewer_contract_debts = [
        item for item in deferred_semantic_debts
        if _reviewer_contract_debt_eligible(item)
    ]
    repair_contract_debts = [
        item for item in deferred_semantic_debts
        if _repair_contract_debt_eligible(item)
    ]
    deferred_debts = deferred_semantic_debts
    production_hard_blocks = [
        item for item in unresolved
        if dispositions[item.request.display_id]["disposition"] == "HARD_STOP"
    ]
    for item in deferred_semantic_debts:
        _persist_deferred_semantic_debt(checkpoint_dir=checkpoint_dir, item=item, bible=bible)
    for item in reviewer_contract_debts:
        _persist_reviewer_contract_debt(checkpoint_dir=checkpoint_dir, item=item, bible=bible)
    for item in repair_contract_debts:
        _persist_repair_contract_debt(checkpoint_dir=checkpoint_dir, item=item, bible=bible)
    qa_records = [item.qa for item in outcomes]
    raw_qa_path = checkpoint_dir / "translation_raw_qa_report.json"
    # A material current-head signal with an unspent bounded corrective repair
    # is recovery-required, never a local BLOCKED terminal.  The Supervisor
    # restarts the ordinary worker, which resumes this exact branch from RAW;
    # no Translation call is authorized by this transition.
    if recovery_required:
        if not raw_qa_path.exists():
            write_json_once(
                raw_qa_path,
                {
                    "pass_count": sum(bool(row and row.get("passed")) for row in qa_records),
                    "fail_count": sum(not (row and row.get("passed")) for row in qa_records),
                    "records": qa_records,
                },
            )
        first = recovery_required[0]
        decision = dispositions[first.request.display_id]
        current_recovery_head = _resolve_checkpoint_authoritative_candidate(
            checkpoint_dir, first.request.display_id, first.raw,
        ).candidate
        current_recovery_review = _current_head_rereview(
            first, checkpoint_dir=checkpoint_dir, current=current_recovery_head,
        )
        recovery_scope = [] if current_recovery_review is None else sorted({
            index
            for issue in current_recovery_review.issues
            for index in issue.affected_segment_indices
        })
        recovery_budget_used = int(
            load_stage(checkpoint_dir, "semantic_rescue_corrective", first.request.display_id) is not None
        )
        write_worker_handoff(
            handoff_path,
            status="RECOVERY_REQUIRED",
            worker_id=worker_id,
            active_batch=active_batch,
            checkpoint=checkpoint,
            partial_checkpoint_path=str(checkpoint_dir),
            stage_reached="SEMANTIC_TERMINAL",
            failure_classification="CURRENT_HEAD_RECOVERY_REQUIRED",
            failing_work_order_ids=[item.request.display_id for item in recovery_required],
            relevant_qa_artifact_paths=decision["evidence_references"],
            raw_exists=raw_path.is_file(),
            raw_sha256=raw_sha or None,
            production_disposition=decision["disposition"],
            disposition_reason=decision["reason"],
            disposition_evidence_references=decision["evidence_references"],
            recovery_family=decision["recovery_family"],
            recovery_fingerprint={
                "batch": active_batch,
                "checkpoint": checkpoint,
                "work_order": first.request.display_id,
                "current_head_sha256": _candidate_binding(current_recovery_head),
                "recovery_family": decision["recovery_family"],
                "canonical_reason": decision["reason"],
                "affected_segment_indices": recovery_scope,
                "bound_review_result_sha256": (
                    None if current_recovery_review is None
                    else _canonical_sha256(current_recovery_review.model_dump())
                ),
                "recovery_budget_used": recovery_budget_used,
            },
        )
        return 3
    if production_hard_blocks or len(outcomes) != expected or any(item.raw is None for item in outcomes):
        for item in production_hard_blocks:
            primary_repair = load_stage(checkpoint_dir, "semantic_rescue_repairs", item.request.display_id)
            final_review = item.second_review or item.review
            corrective_eligible = (
                isinstance(primary_repair, Mapping)
                and final_review is not None
                and _corrective_semantic_repair_is_eligible(primary_repair, final_review)
            )
            escalation_reason = _human_escalation_reason(
                item,
                recovery_exhausted=(
                    load_stage(checkpoint_dir, "semantic_rescue_corrective", item.request.display_id) is not None
                    or not corrective_eligible
                ),
            )
            if load_stage(checkpoint_dir, "human_escalation_assessments", item.request.display_id) is None:
                review = item.second_review or item.review
                persist_stage_once(
                    checkpoint_dir, "human_escalation_assessments", item.request.display_id,
                    {
                        "artifact_type": "PRODUCTION_HUMAN_ESCALATION_ASSESSMENT",
                        "schema_version": 1,
                        "display_id": item.request.display_id,
                        "unit_uid": item.request.unit_uid,
                        "candidate_binding_sha256": None if item.working is None else _candidate_binding(item.working),
                        "core_qa_passed": bool(item.qa and item.qa.get("passed")),
                        "issue_codes": [] if review is None else [issue.code for issue in review.issues],
                        "recovery_attempted": load_stage(checkpoint_dir, "semantic_rescue", item.request.display_id) is not None,
                        "corrective_recovery_eligible": corrective_eligible,
                        "escalation_reason": escalation_reason,
                        "human_terminal_authorized": escalation_reason is not None,
                    },
                )
            if escalation_reason and load_stage(checkpoint_dir, "human_escalation_packets", item.request.display_id) is None:
                review = item.second_review or item.review
                persist_stage_once(
                    checkpoint_dir, "human_escalation_packets", item.request.display_id,
                    {
                        "artifact_type": "PRODUCTION_TRUE_SEMANTIC_HUMAN_PACKET",
                        "schema_version": 1,
                        "display_id": item.request.display_id,
                        "unit_uid": item.request.unit_uid,
                        "source_sha256": item.request.source_sha256,
                        "current_head_sha256": None if item.working is None else _candidate_binding(item.working),
                        "source": item.request.source_text,
                        "current_target": None if item.working is None else "".join(segment.text for segment in item.working.target_segments),
                        "issue": None if review is None else [issue.model_dump() for issue in review.issues],
                        "why_automatic_recovery_cannot_continue": escalation_reason,
                        "candidate_choices": ["Preserve current head", "Human-authored exact-scope correction"],
                    },
                )
            if item.human_required and load_stage(checkpoint_dir, "human_required_triage", item.request.display_id) is None:
                persist_stage_once(
                    checkpoint_dir, "human_required_triage", item.request.display_id,
                    {
                        "artifact_type": "HUMAN_REQUIRED_TRIAGE", "schema_version": 1,
                        "display_id": item.request.display_id, "unit_uid": item.request.unit_uid,
                        **classify_human_required(
                            required_context={},
                            persisted_payload=load_stage(checkpoint_dir, "reviewer_payloads", item.request.display_id),
                            reprocess_terminal=semantic_terminal_of_outcome(item),
                            auto_repair_exhausted=True,
                            escalation_reason=escalation_reason,
                        ),
                    },
                )
        if not raw_qa_path.exists():
            write_json_once(
                raw_qa_path,
                {
                    "pass_count": sum(bool(row and row.get("passed")) for row in qa_records),
                    "fail_count": sum(not (row and row.get("passed")) for row in qa_records),
                    "records": qa_records,
                },
            )
        queue_path = checkpoint_dir / "resolution_queue.json"
        if not queue_path.exists():
            queue_items = []
            for item in production_hard_blocks:
                review = item.second_review or item.review
                issue_codes = [] if review is None else [issue.code for issue in review.issues]
                severities = review_issue_severities(issue_codes)
                queue_items.append({
                    "logical_work_order_id": item.request.display_id,
                    "unit_uid": item.request.unit_uid,
                    "candidate_binding_sha256": None if item.working is None else _candidate_binding(item.working),
                    "classification": "BLOCKING_SEMANTIC" if BLOCKING_SEMANTIC in severities else "UNRESOLVED_SEMANTIC",
                    "issue_codes": issue_codes,
                    "reviewer_dispatch": item.reviewer_dispatch,
                    "core_qa_passed": bool(item.qa and item.qa.get("passed")),
                })
            write_json_once(queue_path, {"artifact_type": "PRODUCTION_RESOLUTION_QUEUE", "schema_version": 1, "checkpoint": checkpoint, "items": queue_items})
        write_worker_handoff(
            handoff_path,
            # ``production_hard_blocks`` is already the canonical disposition
            # result.  A failed deterministic QA may explain that hard stop,
            # but it must not relabel it as a recoverable handoff and let the
            # supervisor replay the same worker.
            status="BLOCKED",
            worker_id=worker_id,
            active_batch=active_batch,
            checkpoint=checkpoint,
            partial_checkpoint_path=str(checkpoint_dir),
            stage_reached="SEMANTIC_TERMINAL",
            failure_classification="HUMAN_REQUIRED" if any(item.human_required for item in production_hard_blocks) else "UNRESOLVED_SEMANTIC",
            failing_work_order_ids=[item.request.display_id for item in production_hard_blocks],
            relevant_qa_artifact_paths=[str(checkpoint_dir / "translation_raw_qa_report.json")],
            raw_exists=raw_path.is_file(),
            raw_sha256=raw_sha or None,
            production_disposition="HARD_STOP",
            disposition_reason=(
                dispositions[production_hard_blocks[0].request.display_id]["reason"]
                if production_hard_blocks else "CHECKPOINT_COMPLETENESS_OR_RAW_FAILURE"
            ),
            disposition_evidence_references=(
                dispositions[production_hard_blocks[0].request.display_id]["evidence_references"]
                if production_hard_blocks else [str(raw_qa_path)]
            ),
        )
        return 3

    if not raw_qa_path.exists():
        write_json_once(
            raw_qa_path,
            {"pass_count": expected, "fail_count": 0, "records": qa_records},
        )
    else:
        # RAW QA is immutable historical evidence.  A production semantic rescue
        # may deliberately supersede a RAW-derived working candidate, but only
        # after the new candidate has passed deterministic QA and semantic gate.
        # Historical RAW QA remains immutable audit evidence.  The current
        # acquisition projection is the seal input, so a historic view may
        # not veto a resolver-validated current view/request/acquisition set.
        raw_failed_records = [
            row for row in current_raw_qa_payload.records_by_uid.values()
            if isinstance(row, Mapping) and not row.get("passed")
        ]
        # This report records immutable, first-pass RAW QA.  It is deliberately
        # never rewritten after an exact D050/NUMBER/DOI evidence overlay or a
        # bounded downstream resolution.  It can therefore block sealing only
        # when the *current durable* result is still unresolved.
        unresolved_raw_failure = []
        request_by_display_id = {item.display_id: item for item in loaded["requests"]}
        for original_record in raw_failed_records:
            display_id = str(original_record.get("display_id") or "")
            request = request_by_display_id.get(display_id)
            raw = None if request is None else existing_raw.get(request.unit_uid)
            if request is None or raw is None:
                unresolved_raw_failure.append(display_id)
                continue
            try:
                current = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raw)
            except CandidateAuthorityError:
                unresolved_raw_failure.append(display_id)
                continue
            # RAW QA is immutable history.  Reconciliation must evaluate the
            # current authoritative candidate with its exact current-bound
            # D051 evidence; calling the bare QA function here re-opened a
            # Human-approved Chinese numeric representation during sealing.
            current_qa = _qa_with_d051(
                qa, loaded["views"][display_id], request, current.candidate,
                loaded["known_unit_ids"], _d051_equivalence_for(
                    checkpoint_dir, request, current.candidate, bible=bible,
                ),
            )
            # Evidence creation is explicitly separated from the read-only
            # effective-state resolver below.  Historical RAW QA remains
            # immutable even when a bound current head has superseded it.
            _ensure_current_candidate_qa_reconciliation(
                checkpoint_dir=checkpoint_dir, request=request, raw=raw, resolved_candidate=current,
                original_qa_path=raw_qa_path, original_qa_record=original_record,
                rerun_qa=current_qa, bible=bible,
            )
            effective_qa = resolve_effective_qa(
                checkpoint_dir=checkpoint_dir, request=request, raw=raw, current_head=current,
                historical_qa_record=original_record, current_qa=current_qa,
                original_qa_path=raw_qa_path, bible=bible,
            )
            if effective_qa["state"] == "PASS":
                continue
            unresolved_raw_failure.append(display_id)
        if unresolved_raw_failure:
            return _blocked(
                handoff_path, worker_id, checkpoint=checkpoint,
                classification="RAW_QA_IMMUTABLE_MISMATCH",
                extra={"unresolved_raw_failure_ids": unresolved_raw_failure},
                active_batch=active_batch,
            )
    finalization_input = {
        "schema_version": 1,
        "batch": active_batch,
        "checkpoint": checkpoint,
        "frozen_selection": list(loaded["order"]),
        "selection_sha256": loaded["selection_sha256"],
        "source_sha256": loaded["source_sha256"],
        "effective_request_sha256": effective_request_sha256,
        "outcomes": [
            {
                "logical_work_order_id": item.request.display_id,
                "unit_uid": item.request.unit_uid,
                "request_binding_sha256": request_bindings.get(item.request.unit_uid),
                "current_head_sha256": None if item.working is None else _candidate_binding(item.working),
                "effective_qa_sha256": _canonical_sha256(item.qa or {}),
                "canonical_disposition": item.registry_decision.get("disposition"),
                "outcome_binding_sha256": _canonical_sha256({
                    "unit_uid": item.request.unit_uid,
                    "source_sha256": item.request.source_sha256,
                    "current_head_sha256": None if item.working is None else _candidate_binding(item.working),
                    "effective_qa_sha256": _canonical_sha256(item.qa or {}),
                    "canonical_disposition": item.registry_decision.get("disposition"),
                }),
            }
            for item in outcomes
        ],
    }
    effective_generation, prepared = _select_prepared_output_generation(
        checkpoint_dir, outcomes, seal_generation,
        finalization_input=finalization_input if current_mode else None,
    )
    if effective_generation is not None:
        seal_dir = (checkpoint_dir / effective_generation["path"]).resolve()
        projection = _prepared_generation_ready_for_commit(checkpoint_dir, effective_generation, manifest)
        if projection is not None:
            publish_manifest_successor(checkpoint_dir.parent, projection)
            write_worker_handoff(
                handoff_path,
                status="CONTINUE",
                worker_id=worker_id,
                active_batch=active_batch,
                completed_checkpoint=checkpoint,
                next_checkpoint=(f"checkpoint_{ordinal + 1:02d}" if ordinal < 8 else None),
                next_logical_work_order_id=None,
                production_disposition=("DEFERRED_SEMANTIC_DEBT" if deferred_debts else "CONTINUE"),
                disposition_reason=("SAFE_CURRENT_HEAD_RELEASE_DEBT" if deferred_debts else "CHECKPOINT_SEALED"),
                disposition_evidence_references=[str(seal_dir / "checkpoint_evidence.json")],
            )
            return 0
    normalize_or_validate_response_artifact(
        checkpoint_dir / "translation_request.jsonl",
        checkpoint_dir / "translation_raw_response.jsonl",
        checkpoint_dir / "translation_normalized_response.jsonl",
        checkpoint_dir / "translation_normalization_report.json",
    )
    working = prepared if prepared is not None else [item.working or item.raw for item in outcomes]
    write_jsonl_once_or_validate(seal_dir / "translation_repaired_response.jsonl", [item for item in working if item is not None])
    dispatched, excluded = partition_deterministic_review_dispatch(qa_records)
    reviews = []
    for item in outcomes:
        terminal = semantic_terminal_of_outcome(item)
        deferred = item in deferred_debts
        contract_debt = item in reviewer_contract_debts
        repair_contract_debt = item in repair_contract_debts
        status = "ACCEPT" if terminal in SEMANTIC_PASS_TERMINALS else "HUMAN_REQUIRED"
        explanation = (
            "Reviewer contract debt: Core Harness and candidate authority passed; invalid reviewer responses were not consumed and release remains blocked pending debt resolution."
            if contract_debt
            else "Repair contract debt: Core Harness and candidate authority passed; rejected repair proposals did not replace the current head and release remains blocked pending debt resolution."
            if repair_contract_debt
            else "Deferred semantic debt: Core Harness passed and production may continue; release remains blocked pending batch debt resolution."
            if deferred
            else "HUMAN_APPROVED terminal; model Reviewer not re-voted."
            if terminal == "HUMAN_APPROVED"
            else "Qwen reviewer ACCEPT after Harness PASS."
        )
        reviews.append(
            {
                "unit_uid": item.request.unit_uid,
                "display_id": item.request.display_id,
                "status": status,
                "issue_types": [],
                "explanation": explanation,
                "semantic_terminal": "REVIEWER_CONTRACT_DEBT" if contract_debt else ("REPAIR_CONTRACT_DEBT" if repair_contract_debt else ("DEFERRED_SEMANTIC_DEBT" if deferred else terminal)),
            }
        )
    review_summary = semantic_review_summary(reviews, excluded)
    review_summary["deferred_semantic_debt_count"] = len(deferred_semantic_debts)
    review_summary["deferred_work_order_ids"] = [item.request.display_id for item in deferred_semantic_debts]
    review_summary["reviewer_contract_debt_count"] = len(reviewer_contract_debts)
    review_summary["reviewer_contract_debt_work_order_ids"] = [item.request.display_id for item in reviewer_contract_debts]
    review_summary["repair_contract_debt_count"] = len(repair_contract_debts)
    review_summary["repair_contract_debt_work_order_ids"] = [item.request.display_id for item in repair_contract_debts]
    write_json_once_or_validate(seal_dir / "semantic_review_report.json", review_summary)
    approved = [item.request.unit_uid for item in outcomes if item not in deferred_debts]
    write_json_once_or_validate(
        seal_dir / "translation_normalized_qa_report.json",
        {"pass_count": expected, "fail_count": 0, "records": qa_records},
    )
    write_json_once_or_validate(
        seal_dir / "translation_final_qa_report.json",
        {
            "pass_count": expected,
            "fail_count": 0,
            "records": qa_records,
            "final_approved_unit_uids": approved,
            "not_approved_deterministic_fail_unit_uids": [],
            "final_status": "FIRST_PASS",
            "semantic_terminal_model_accepted": sum(semantic_terminal_of_outcome(item) == "MODEL_ACCEPTED" for item in outcomes),
            "semantic_terminal_human_approved": sum(semantic_terminal_of_outcome(item) == "HUMAN_APPROVED" for item in outcomes),
            "deferred_semantic_debt_unit_uids": [item.request.unit_uid for item in deferred_semantic_debts],
            "deferred_semantic_debt_work_order_ids": [item.request.display_id for item in deferred_semantic_debts],
            "reviewer_contract_debt_unit_uids": [item.request.unit_uid for item in reviewer_contract_debts],
            "reviewer_contract_debt_work_order_ids": [item.request.display_id for item in reviewer_contract_debts],
            "repair_contract_debt_unit_uids": [item.request.unit_uid for item in repair_contract_debts],
            "repair_contract_debt_work_order_ids": [item.request.display_id for item in repair_contract_debts],
            "production_seal": "WITH_RELEASE_DEBT" if deferred_debts else "FULLY_RESOLVED",
            "current_outcome_contract": {
                "policy_owner": "canonical_current_projection",
                "worker_current_policy_owner_count": 1,
                "selection_count": expected,
                "current_outcome_count": len(current_outcomes_by_work_order),
                "competing_unitoutcomes": 0,
                "records": [
                    {
                        "logical_work_order_id": item.request.display_id,
                        "unit_uid": item.request.unit_uid,
                        "source_sha256": item.request.source_sha256,
                        "request_binding_sha256": request_bindings.get(item.request.unit_uid),
                        "current_head_sha256": None if item.working is None else _candidate_binding(item.working),
                        "effective_qa_candidate_binding_sha256": (item.qa or {}).get("effective_qa_candidate_binding_sha256"),
                        "canonical_disposition": item.registry_decision.get("disposition"),
                    }
                    for item in outcomes
                ],
            },
            "codex_exec_count": 0,
        },
    )
    write_json_once_or_validate(
        seal_dir / "checkpoint_evidence.json",
        {
            "logical_work_orders": list(loaded["order"]),
            "source_sha256": loaded["source_sha256"],
            "raw_sha256": raw_sha,
            "deterministic_pass_count": expected,
            "deterministic_fail_count": 0,
            "reviewer_dispatch_count": len(reviews),
            "reviewer_exclusion_count": len(excluded),
            "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED",
            "d050_evidence_artifacts": [str(path) for path in d050_paths_for_checkpoint(run, checkpoint, batch_label=active_batch)],
            "d050_routed_work_order_count": sum(len(items) for items in load_checkpoint_d050_registry(run, checkpoint, batch_label=active_batch).values()),
            "worker": "qwen_deterministic",
            "codex_exec_count": 0,
            "production_hard_block_count": 0,
            "deferred_semantic_debt_count": len(deferred_semantic_debts),
            "deferred_work_order_ids": [item.request.display_id for item in deferred_semantic_debts],
            "reviewer_contract_debt_count": len(reviewer_contract_debts),
            "reviewer_contract_debt_work_order_ids": [item.request.display_id for item in reviewer_contract_debts],
            "repair_contract_debt_count": len(repair_contract_debts),
            "repair_contract_debt_work_order_ids": [item.request.display_id for item in repair_contract_debts],
            "current_outcome_count": len(current_outcomes_by_work_order),
            "current_outcome_selection_count": expected,
            "current_competing_unitoutcomes": 0,
            "worker_current_policy_owner_count": 1,
        },
    )
    if effective_generation is None:
        append_sealed_checkpoint(manifest_path, checkpoint_dir)
    else:
        # Generation artifacts are already create-once and validated by the
        # projection builder.  Publish a new manifest pointer last; the prior
        # compensating manifest remains immutable safe authority on any crash.
        projection = sealed_checkpoint_manifest_projection(
            manifest, checkpoint_dir, seal_generation=dict(effective_generation),
        )
        publish_manifest_successor(checkpoint_dir.parent, projection)
    write_worker_handoff(
        handoff_path,
        status="CONTINUE",
        worker_id=worker_id,
        active_batch=active_batch,
        completed_checkpoint=checkpoint,
        next_checkpoint=(f"checkpoint_{ordinal + 1:02d}" if ordinal < 8 else None),
        next_logical_work_order_id=None,
        production_disposition=("DEFERRED_SEMANTIC_DEBT" if deferred_debts else "CONTINUE"),
        disposition_reason=("SAFE_CURRENT_HEAD_RELEASE_DEBT" if deferred_debts else "CHECKPOINT_SEALED"),
        disposition_evidence_references=[str(seal_dir / "checkpoint_evidence.json")],
    )
    return 0
