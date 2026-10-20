"""Continue an isolated canary from persisted RAW.  Never retranslates."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.providers.qwen_max import QWEN_MAX
from booktrans.production_worker.artifacts import (
    ROUTE_PROVIDER_NAMES,
    reconstructed_provenance,
    write_json_once,
    write_json_replace,
)
from booktrans.production_worker.canary import (
    NOT_PERSISTED,
    PRODUCTION_BATCH,
    PRODUCTION_CHECKPOINT,
    SEMANTIC_PASS_TERMINALS,
    _integrity_codes,
    _report,
    _review_status,
    evaluate_canary_gate,
    semantic_terminal_of_outcome,
)
from booktrans.production_worker.diagnostics import CanaryDiagnostics
from booktrans.production_worker.numeric_recovery import approved_numeric_contract, decide_numeric_recovery_policy
from booktrans.production_worker.pipeline import (
    UnitOutcome,
    WorkerProviders,
    _latency_seconds,
    _qa_summary,
    enrich_request,
    repair_with_contract_boundary,
)
from booktrans.production_worker.qa import QaFn, qa_codes
from booktrans.production_worker.selection import (
    assert_not_production_checkpoint,
    d050_paths_for_checkpoint,
    load_checkpoint_d050_registry,
    overlay_verified_d050_bindings,
)
from booktrans.production_worker.unit_store import (
    append_jsonl_fsync,
    load_raw_by_uid,
    load_stage,
    load_unit_result,
    persist_stage_once,
    raw_sha256,
    resolve_effective_provider_acquisition,
    resolve_effective_translation_request,
    stage_path,
    unit_path,
)
from booktrans.providers.qwen_max import ApprovedRepairContract, ReviewIssue, ReviewResult
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.router import route_semantic_unit
from booktrans.providers.failures import classify_provider_exception
from booktrans.providers.translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationProviderRequest,
)

_PROVIDER_ERRORS = (ProviderContractGap, ProviderRuntimeError, ProviderConfigurationError)


class ForbiddenTranslationProvider:
    """Fail closed: continuation must never call a translation provider."""

    def translate(self, request: TranslationProviderRequest):
        raise RuntimeError("TRANSLATION_API_FORBIDDEN_ON_RESUME")

    def translate_structured(self, request: TranslationProviderRequest):
        raise RuntimeError("TRANSLATION_API_FORBIDDEN_ON_RESUME")


def _raw_line_sha_by_uid(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = TranslationResult.model_validate_json(line)
        found[raw.unit_uid] = sha256(line.encode("utf-8")).hexdigest()
    return found


def _binding(request: CalibrationRequest, translation_bible: Path | None) -> TranslationProviderRequest:
    return enrich_request(request, translation_bible=translation_bible)


def verify_raw_unit(
    request: CalibrationRequest,
    raw: TranslationResult | None,
    stored: Mapping[str, Any] | None,
    *,
    source_sha256: str,
    selection_sha256: str,
    translation_bible: Path | None,
    original_line_sha256: str | None = None,
) -> list[str]:
    reasons: list[str] = []
    if raw is None:
        return ["RAW_MISSING"]
    if request.unit_uid != raw.unit_uid:
        reasons.append("UNIT_UID_MISMATCH")
    if len(request.source_segments) != len(raw.target_segments):
        reasons.append("SEGMENT_COUNT_MISMATCH")
    if request.source_sha256 != source_sha256:
        reasons.append("SOURCE_SHA_MISMATCH")
    provider_request = _binding(request, translation_bible)
    decision = route_semantic_unit(request)
    expected_provider = ROUTE_PROVIDER_NAMES.get(decision.route)
    if expected_provider is None:
        reasons.append("ROUTE_PROVIDER_UNKNOWN")
    line_sha = sha256(raw.model_dump_json().encode("utf-8")).hexdigest()
    if stored:
        if stored.get("unit_uid") not in {None, request.unit_uid}:
            reasons.append("STORED_UNIT_UID_MISMATCH")
        if stored.get("source_sha256") not in {None, source_sha256, request.source_sha256}:
            reasons.append("STORED_SOURCE_SHA_MISMATCH")
        if stored.get("selection_sha256") not in {None, selection_sha256}:
            reasons.append("STORED_SELECTION_SHA_MISMATCH")
        if stored.get("model") not in {None, NOT_PERSISTED, decision.model}:
            reasons.append("STORED_MODEL_MISMATCH")
        binding = stored.get("candidate_binding") or {}
        if binding.get("request_binding_sha256") not in {None, provider_request.request_binding_sha256}:
            reasons.append("REQUEST_BINDING_MISMATCH")
        if binding.get("translation_options_sha256") not in {None, provider_request.translation_options_sha256}:
            reasons.append("OPTIONS_SHA_MISMATCH")
        if stored.get("raw_sha256") not in {None, line_sha, original_line_sha256}:
            reasons.append("RAW_SHA_MISMATCH")
        provenance = stored.get("provenance") if isinstance(stored.get("provenance"), dict) else {}
        if provenance.get("provider") not in {None, expected_provider}:
            reasons.append("PROVIDER_IDENTITY_MISMATCH")
        if provenance.get("model") not in {None, NOT_PERSISTED, decision.model}:
            reasons.append("PROVENANCE_MODEL_MISMATCH")
        if provenance.get("route") not in {None, decision.route}:
            reasons.append("ROUTE_PROVENANCE_MISMATCH")
        if provenance.get("request_binding_sha256") not in {None, provider_request.request_binding_sha256}:
            reasons.append("PROVENANCE_BINDING_MISMATCH")
    return reasons


def _blocked(request: CalibrationRequest, reasons: Sequence[str]) -> UnitOutcome:
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request))
    outcome.final_status = "BLOCKED"
    outcome.failure_family = "BLOCKED"
    outcome.failure_code = ",".join(reasons) or "BLOCKED"
    outcome.human_required = True
    outcome.blocked = True
    return outcome


def _review_from_payload(payload: Mapping[str, Any], unit_uid: str) -> ReviewResult | None:
    verdict = payload.get("verdict")
    if verdict not in {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}:
        return None
    issues = []
    for item in payload.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            ReviewIssue(
                code=str(item.get("code") or "UNKNOWN"),
                affected_segment_indices=list(item.get("affected_segment_indices") or []),
                reason=str(item.get("reason") or ""),
                repair_instruction=item.get("repair_instruction"),
            )
        )
    return ReviewResult(schema_version="booktrans.structured-review.v1", unit_uid=unit_uid, verdict=verdict, issues=issues)


def _contract_from_stored(stored: Mapping[str, Any]) -> ApprovedRepairContract:
    return ApprovedRepairContract.model_validate(
        {
            key: stored[key]
            for key in ("policy_id", "unit_uid", "source_fingerprint", "permitted_segment_indices", "repair_instruction")
            if key in stored
        }
    )


_CLOSED_REVIEW_VERDICTS = frozenset({"ACCEPT", "REPAIR", "HUMAN_REQUIRED"})
_NUMERIC_GATE_STOP_CODES = frozenset({"REGISTRY_REPAIR_SCOPE_AMBIGUOUS", "QA_REPAIR_NOT_REGISTRY_APPROVED"})


def _continuation_is_complete(output_dir: Path, display_id: str, terminal: Mapping[str, Any]) -> bool:
    """Skip only finished units.  Incomplete HUMAN_REQUIRED/contract terminals must resume.

    Completeness is judged from the terminal payload itself.  A newly persisted
    review file must not freeze an older HUMAN_REQUIRED/contract terminal.
    """
    if bool(terminal.get("blocked")) or str(terminal.get("final_status") or "") == "BLOCKED":
        return True
    status = str(terminal.get("final_status") or "")
    recorded = terminal.get("review") in _CLOSED_REVIEW_VERDICTS
    has_review = load_stage(output_dir, "reviews", display_id) is not None
    if status == "PROVIDER_CONTRACT_FAILURE" and terminal.get("failure_code"):
        return True
    if status == "HUMAN_REQUIRED" and not recorded and terminal.get("failure_code") in _NUMERIC_GATE_STOP_CODES:
        return True
    if not recorded or not has_review:
        return False
    return status in {"ACCEPT", "HUMAN_REQUIRED", "PROVIDER_CONTRACT_FAILURE"}


def _terminal_payload(outcome: UnitOutcome) -> dict[str, Any]:
    return {
        "artifact_type": "CANARY_CONTINUATION_UNIT",
        "schema_version": 1,
        "display_id": outcome.request.display_id,
        "unit_uid": outcome.request.unit_uid,
        "route": outcome.decision.route,
        "final_status": outcome.final_status,
        "terminal_status": outcome.final_status,
        "repair_applied": outcome.repair_applied,
        "repair_policy_id": outcome.repair_policy_id,
        "human_required": outcome.human_required,
        "semantic_terminal": getattr(outcome, "semantic_terminal", None),
        "blocked": outcome.blocked,
        "review": NOT_PERSISTED if outcome.review is None else outcome.review.verdict,
        "second_review": NOT_PERSISTED if outcome.second_review is None else outcome.second_review.verdict,
        "review_usage": outcome.review_usage or NOT_PERSISTED,
        "review_latency_seconds": outcome.review_latency_seconds if outcome.review_latency_seconds is not None else NOT_PERSISTED,
        "second_review_usage": outcome.second_review_usage or NOT_PERSISTED,
        "second_review_latency_seconds": outcome.second_review_latency_seconds if outcome.second_review_latency_seconds is not None else NOT_PERSISTED,
        "repair_usage": outcome.repair_usage or NOT_PERSISTED,
        "repair_latency_seconds": outcome.repair_latency_seconds if outcome.repair_latency_seconds is not None else NOT_PERSISTED,
        "qa_passed": None if outcome.qa is None else bool(outcome.qa.get("passed")),
        "failure_family": outcome.failure_family,
        "failure_code": outcome.failure_code,
    }


def _hydrate_complete_terminal(
    outcome: UnitOutcome,
    terminal: Mapping[str, Any],
    *,
    output_dir: Path,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
) -> UnitOutcome:
    outcome.final_status = str(terminal.get("final_status") or "ACCEPT")
    outcome.human_required = bool(terminal.get("human_required"))
    outcome.blocked = bool(terminal.get("blocked"))
    outcome.repair_applied = bool(terminal.get("repair_applied"))
    policy = terminal.get("repair_policy_id")
    if policy:
        outcome.repair_policy_id = str(policy)
    if terminal.get("failure_family"):
        outcome.failure_family = str(terminal["failure_family"])
    if terminal.get("failure_code"):
        outcome.failure_code = str(terminal["failure_code"])
    review_payload = load_stage(output_dir, "reviews", outcome.request.display_id)
    if review_payload:
        outcome.review = _review_from_payload(review_payload, outcome.request.unit_uid)
        usage = review_payload.get("usage")
        if isinstance(usage, dict):
            outcome.review_usage = usage
        latency = review_payload.get("latency_seconds")
        if isinstance(latency, (int, float)):
            outcome.review_latency_seconds = float(latency)
    second_payload = load_stage(output_dir, "re_reviews", outcome.request.display_id)
    if second_payload:
        outcome.second_review = _review_from_payload(second_payload, outcome.request.unit_uid)
        usage = second_payload.get("usage")
        if isinstance(usage, dict):
            outcome.second_review_usage = usage
        latency = second_payload.get("latency_seconds")
        if isinstance(latency, (int, float)):
            outcome.second_review_latency_seconds = float(latency)
    repair_payload = load_stage(output_dir, "repair_candidates", outcome.request.display_id)
    if repair_payload is None:
        repair_payload = load_stage(output_dir, "semantic_repair_candidates", outcome.request.display_id)
    if repair_payload:
        usage = repair_payload.get("usage")
        if isinstance(usage, dict):
            outcome.repair_usage = usage
        latency = repair_payload.get("latency_seconds")
        if isinstance(latency, (int, float)):
            outcome.repair_latency_seconds = float(latency)
        if outcome.repair_applied and repair_payload.get("working"):
            outcome.working = TranslationResult.model_validate(repair_payload["working"])
            outcome.qa = qa_fn(view, outcome.request, outcome.working, known_unit_ids)
    return outcome


def _persist_review(output_dir: Path, outcome: UnitOutcome, *, binding_sha: str) -> None:
    if outcome.review is None or load_stage(output_dir, "reviews", outcome.request.display_id) is not None:
        return
    persist_stage_once(
        output_dir,
        "reviews",
        outcome.request.display_id,
        {
            "artifact_type": "CANARY_REVIEW_RESULT",
            "schema_version": 1,
            "display_id": outcome.request.display_id,
            "unit_uid": outcome.request.unit_uid,
            "verdict": outcome.review.verdict,
            "issues": [issue.model_dump() for issue in outcome.review.issues],
            "reviewer": "QWEN_MAX_REVIEWER",
            "model": QWEN_MAX,
            "usage": outcome.review_usage or NOT_PERSISTED,
            "latency_seconds": outcome.review_latency_seconds if outcome.review_latency_seconds is not None else NOT_PERSISTED,
            "request_binding_sha256": binding_sha,
        },
    )
    append_jsonl_fsync(
        output_dir / "canary_progress.jsonl",
        {"stage": "REVIEW", "display_id": outcome.request.display_id, "verdict": outcome.review.verdict},
    )


def _persist_terminal(output_dir: Path, outcome: UnitOutcome) -> None:
    existing = load_stage(output_dir, "continuation", outcome.request.display_id)
    if existing is not None and _continuation_is_complete(output_dir, outcome.request.display_id, existing):
        return
    payload = _terminal_payload(outcome)
    path = stage_path(output_dir, "continuation", outcome.request.display_id)
    if existing is not None:
        write_json_replace(path, payload)
    else:
        persist_stage_once(output_dir, "continuation", outcome.request.display_id, payload)
    append_jsonl_fsync(
        output_dir / "canary_progress.jsonl",
        {"stage": "TERMINAL", "display_id": outcome.request.display_id, "final_status": outcome.final_status},
    )


def _fail_closed_provider(outcome: UnitOutcome, error: BaseException) -> UnitOutcome:
    classified = classify_provider_exception(error)
    outcome.final_status = classified.family
    outcome.failure_family = classified.family
    outcome.failure_code = classified.code
    outcome.human_required = True
    return outcome


def _call_reviewer(
    providers: WorkerProviders,
    provider_request: TranslationProviderRequest,
    working: TranslationResult,
    qa: Mapping[str, Any] | None,
    *,
    semantic_context: Mapping[str, Any] | None = None,
) -> tuple[ReviewResult, dict[str, Any], float | None]:
    started = time.perf_counter()
    result = providers.reviewer.review_with_provenance(
        provider_request,
        working,
        deterministic_qa_summary=_qa_summary(qa),
        semantic_context=semantic_context,
    )
    wall = time.perf_counter() - started
    candidate_latency = _latency_seconds(result.candidate)
    return result.review, dict(result.candidate.usage), candidate_latency if candidate_latency is not None else wall


def _call_repair(providers: WorkerProviders, provider_request: TranslationProviderRequest, current: TranslationResult, contract: ApprovedRepairContract) -> tuple[TranslationResult, dict[str, Any], float | None]:
    started = time.perf_counter()
    result = repair_with_contract_boundary(providers.repair, provider_request, current, contract)
    wall = time.perf_counter() - started
    candidate_latency = _latency_seconds(result.candidate)
    return result.repaired, dict(result.candidate.usage), candidate_latency if candidate_latency is not None else wall


def _apply_reviewer_decision(
    outcome: UnitOutcome,
    *,
    providers: WorkerProviders,
    provider_request: TranslationProviderRequest,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
    allow_semantic_narrow: bool,
    output_dir: Path,
    on_stage: Callable[..., None] | None,
) -> UnitOutcome:
    working = outcome.working or outcome.raw
    if working is None:
        outcome.final_status = "BLOCKED"
        outcome.blocked = True
        return outcome
    if outcome.review is None:
        if on_stage:
            on_stage("REVIEW_START")
        try:
            review, usage, latency = _call_reviewer(providers, provider_request, working, outcome.qa)
        except _PROVIDER_ERRORS as error:
            return _fail_closed_provider(outcome, error)
        outcome.reviewer_api_calls += 1
        if review.unit_uid != outcome.request.unit_uid:
            outcome.final_status = "PROVIDER_CONTRACT_FAILURE"
            outcome.human_required = True
            return outcome
        outcome.review = review
        outcome.review_usage = usage
        outcome.review_latency_seconds = latency
        _persist_review(output_dir, outcome, binding_sha=provider_request.request_binding_sha256)
        if on_stage:
            on_stage("REVIEW", verdict=review.verdict)
    if outcome.review.verdict == "ACCEPT":
        outcome.final_status = "ACCEPT"
        return outcome
    if outcome.review.verdict == "HUMAN_REQUIRED":
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        return outcome
    contract_stage = "semantic_repair_contracts" if outcome.repair_applied else "repair_contracts"
    candidate_stage = "semantic_repair_candidates" if outcome.repair_applied else "repair_candidates"
    stored_contract = load_stage(output_dir, contract_stage, outcome.request.display_id)
    if stored_contract is None:
        gate = decide_approved_repair_contract(
            provider_request, working, qa_codes=(), review=outcome.review, allow_semantic_narrow=allow_semantic_narrow
        )
        if gate.action != "REPAIR" or gate.contract is None:
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            return outcome
        persist_stage_once(
            output_dir,
            contract_stage,
            outcome.request.display_id,
            {"artifact_type": "CANARY_REPAIR_CONTRACT", "schema_version": 1, **gate.contract.model_dump()},
        )
        contract = gate.contract
        if not outcome.repair_applied:
            outcome.repair_policy_id = gate.policy_id
    else:
        contract = _contract_from_stored(stored_contract)
        if not outcome.repair_applied:
            outcome.repair_policy_id = contract.policy_id
    stored_repair = load_stage(output_dir, candidate_stage, outcome.request.display_id)
    original = working
    if stored_repair is None:
        if on_stage:
            on_stage("REPAIR_START", policy=contract.policy_id)
        try:
            repaired, usage, latency = _call_repair(providers, provider_request, working, contract)
        except _PROVIDER_ERRORS as error:
            return _fail_closed_provider(outcome, error)
        outcome.repair_api_calls += 1
        outcome.repair_usage = usage
        outcome.repair_latency_seconds = latency
        persist_stage_once(
            output_dir,
            candidate_stage,
            outcome.request.display_id,
            {
                "artifact_type": "CANARY_REPAIR_CANDIDATE",
                "schema_version": 1,
                "working": json.loads(repaired.model_dump_json()),
                "usage": usage or NOT_PERSISTED,
                "latency_seconds": latency if latency is not None else NOT_PERSISTED,
            },
        )
    else:
        repaired = TranslationResult.model_validate(stored_repair["working"])
        usage = stored_repair.get("usage")
        if isinstance(usage, dict):
            outcome.repair_usage = usage
        latency = stored_repair.get("latency_seconds")
        if isinstance(latency, (int, float)):
            outcome.repair_latency_seconds = float(latency)
    repaired_qa = qa_fn(view, outcome.request, repaired, known_unit_ids)
    if not repaired_qa.get("passed"):
        outcome.working = original
        outcome.qa = repaired_qa
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        return outcome
    stored_second = load_stage(output_dir, "re_reviews", outcome.request.display_id)
    if stored_second is None:
        try:
            second, usage, latency = _call_reviewer(providers, provider_request, repaired, repaired_qa)
        except _PROVIDER_ERRORS as error:
            return _fail_closed_provider(outcome, error)
        outcome.reviewer_api_calls += 1
        persist_stage_once(
            output_dir,
            "re_reviews",
            outcome.request.display_id,
            {
                "artifact_type": "CANARY_RE_REVIEW_RESULT",
                "schema_version": 1,
                "display_id": outcome.request.display_id,
                "unit_uid": outcome.request.unit_uid,
                "verdict": second.verdict,
                "issues": [issue.model_dump() for issue in second.issues],
                "usage": usage or NOT_PERSISTED,
                "latency_seconds": latency if latency is not None else NOT_PERSISTED,
            },
        )
        outcome.second_review = second
        outcome.second_review_usage = usage
        outcome.second_review_latency_seconds = latency
    else:
        outcome.second_review = _review_from_payload(stored_second, outcome.request.unit_uid)
        usage = stored_second.get("usage")
        if isinstance(usage, dict):
            outcome.second_review_usage = usage
        latency = stored_second.get("latency_seconds")
        if isinstance(latency, (int, float)):
            outcome.second_review_latency_seconds = float(latency)
    if outcome.second_review is None or outcome.second_review.verdict != "ACCEPT":
        outcome.working = original
        outcome.final_status = "HUMAN_REQUIRED"
        outcome.human_required = True
        return outcome
    outcome.working = repaired
    outcome.qa = repaired_qa
    outcome.repair_applied = True
    outcome.final_status = "ACCEPT"
    return outcome


def _recover_numeric_unit(
    outcome: UnitOutcome,
    *,
    providers: WorkerProviders,
    provider_request: TranslationProviderRequest,
    view,
    known_unit_ids: set[str],
    qa_fn: QaFn,
    registry: Mapping[str, Any],
    output_dir: Path,
    on_stage: Callable[..., None] | None,
    allow_semantic_narrow: bool,
) -> UnitOutcome:
    raw = outcome.raw
    if raw is None or outcome.qa is None:
        outcome.final_status = "BLOCKED"
        outcome.blocked = True
        return outcome
    stored_contract = load_stage(output_dir, "repair_contracts", outcome.request.display_id)
    if stored_contract is None:
        gate = approved_numeric_contract(provider_request, raw, outcome.qa, registry)
        if gate is None or gate.action != "REPAIR" or gate.contract is None:
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.failure_family = "LOCAL_CONTENT_QA_FAILURE"
            outcome.failure_code = (None if gate is None else gate.reason) or ",".join(qa_codes(outcome.qa)) or "NUMBER_PRESERVATION"
            outcome.human_required = True
            if on_stage:
                on_stage(
                    "NUMERIC_REPAIR_DENIED",
                    reason=outcome.failure_code,
                    eligible=(registry.get("decision") or {}).get("eligible"),
                )
            return outcome
        persist_stage_once(
            output_dir,
            "repair_contracts",
            outcome.request.display_id,
            {
                "artifact_type": "CANARY_REPAIR_CONTRACT",
                "schema_version": 1,
                "registry_policy_id": (registry.get("decision") or {}).get("policy_id"),
                **gate.contract.model_dump(),
            },
        )
        contract = gate.contract
        outcome.repair_policy_id = gate.policy_id
        outcome.registry_decision = dict(registry.get("decision") or {})
    else:
        contract = _contract_from_stored(stored_contract)
        outcome.repair_policy_id = contract.policy_id
    stored_repair = load_stage(output_dir, "repair_candidates", outcome.request.display_id)
    if stored_repair is None:
        if on_stage:
            on_stage("REPAIR_START", policy=contract.policy_id)
        try:
            repaired, usage, latency = _call_repair(providers, provider_request, raw, contract)
        except _PROVIDER_ERRORS as error:
            return _fail_closed_provider(outcome, error)
        outcome.repair_api_calls += 1
        outcome.repair_usage = usage
        outcome.repair_latency_seconds = latency
        persist_stage_once(
            output_dir,
            "repair_candidates",
            outcome.request.display_id,
            {
                "artifact_type": "CANARY_REPAIR_CANDIDATE",
                "schema_version": 1,
                "working": json.loads(repaired.model_dump_json()),
                "usage": usage or NOT_PERSISTED,
                "latency_seconds": latency if latency is not None else NOT_PERSISTED,
            },
        )
    else:
        repaired = TranslationResult.model_validate(stored_repair["working"])
        usage = stored_repair.get("usage")
        if isinstance(usage, dict):
            outcome.repair_usage = usage
        latency = stored_repair.get("latency_seconds")
        if isinstance(latency, (int, float)):
            outcome.repair_latency_seconds = float(latency)
    repaired_qa = qa_fn(view, outcome.request, repaired, known_unit_ids)
    outcome.qa = repaired_qa
    if not repaired_qa.get("passed"):
        outcome.working = raw
        outcome.final_status = "DETERMINISTIC_QA_FAILED"
        outcome.human_required = True
        return outcome
    outcome.working = repaired
    outcome.repair_applied = True
    return _apply_reviewer_decision(
        outcome,
        providers=providers,
        provider_request=provider_request,
        view=view,
        known_unit_ids=known_unit_ids,
        qa_fn=qa_fn,
        allow_semantic_narrow=allow_semantic_narrow,
        output_dir=output_dir,
        on_stage=on_stage,
    )


def continue_canary_from_raw(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    providers: WorkerProviders,
    production_run: Path,
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    translation_bible: Path | None,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    selection_ids: Sequence[str],
    diagnostics: CanaryDiagnostics | None = None,
    allow_semantic_narrow: bool = True,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
) -> dict[str, Any]:
    """Reuse persisted RAW.  Translation providers must not be invoked."""
    providers = WorkerProviders(
        mt=ForbiddenTranslationProvider(),
        structured=ForbiddenTranslationProvider(),
        reviewer=providers.reviewer,
        repair=providers.repair,
    )
    assert_not_production_checkpoint(
        output_dir, run=production_run, batch_label=PRODUCTION_BATCH, checkpoint=PRODUCTION_CHECKPOINT
    )
    raw_path = output_dir / "translation_raw_response.jsonl"
    if not raw_path.is_file():
        raise RuntimeError(f"CANARY_RAW_MISSING:{raw_path}")
    started = datetime.now(timezone.utc)
    existing_raw = load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    raw_line_sha = _raw_line_sha_by_uid(output_dir / "translation_raw_response.jsonl")
    raw_file_sha = raw_sha256(output_dir / "translation_raw_response.jsonl") or ""
    snapshot = output_dir / "canary_report.raw_recovery.json"
    existing_report = output_dir / "canary_report.json"
    if existing_report.is_file() and not snapshot.is_file():
        write_json_once(snapshot, json.loads(existing_report.read_text(encoding="utf-8")))
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    if diagnostics is not None:
        diagnostics.emit(
            "D050_PREFLIGHT_PASS",
            registries=len(d050_paths_for_checkpoint(production_run, PRODUCTION_CHECKPOINT)),
            records=len(registry_map),
        )


    verified: list[tuple[CalibrationRequest, TranslationResult, dict[str, Any] | None]] = []
    blocked: list[UnitOutcome] = []
    for request in requests:
        stored = load_unit_result(unit_path(output_dir, request.display_id))
        raw = existing_raw.get(request.unit_uid)
        reasons = verify_raw_unit(
            request,
            raw,
            stored,
            source_sha256=source_sha256,
            selection_sha256=selection_sha256,
            translation_bible=translation_bible,
            original_line_sha256=raw_line_sha.get(request.unit_uid),
        )
        if reasons or raw is None:
            blocked.append(_blocked(request, reasons))
            continue
        verified.append((request, raw, stored))

    registry = None
    raw_qa_path = output_dir / "translation_raw_qa_report.json"
    if raw_qa_path.is_file():
        qa_payload = json.loads(raw_qa_path.read_text(encoding="utf-8"))
        qa_fail_ids = [
            str(row["display_id"])
            for row in qa_payload.get("records") or []
            if isinstance(row, dict) and not row.get("passed") and row.get("display_id")
        ]
        if qa_fail_ids:
            registry = decide_numeric_recovery_policy(
                output_dir=output_dir,
                source_sha256=source_sha256,
                selection_ids=selection_ids,
                raw_sha256=raw_file_sha,
                active_batch=PRODUCTION_BATCH,
                checkpoint=PRODUCTION_CHECKPOINT,
            )
            if diagnostics is not None:
                decision = registry.get("decision") or {}
                diagnostics.emit(
                    "REGISTRY_DECISION",
                    policy=decision.get("policy_id"),
                    eligible=decision.get("eligible"),
                    failing=len(qa_fail_ids),
                )
            registry_path = output_dir / "recovery_policy_decision.json"
            if not registry_path.exists():
                write_json_once(
                    registry_path,
                    {
                        "artifact_type": "CANARY_RECOVERY_POLICY_DECISION",
                        "schema_version": 1,
                        "failing_work_order_ids": registry.get("failing_work_order_ids"),
                        "decision": registry.get("decision"),
                        "canonical": registry.get("canonical"),
                    },
                )

    outcomes: list[UnitOutcome] = list(blocked)
    total = len(requests)
    for index, (request, raw, stored) in enumerate(verified, start=1):
        view = views[request.display_id]
        provider_request = _binding(request, translation_bible)
        decision = route_semantic_unit(request)

        def on_stage(stage: str, *, _index=index, _display=request.display_id, **fields: Any) -> None:
            if diagnostics is None:
                return
            diagnostics.emit(stage, index=_index, total=total, id=_display, **fields)

        terminal = load_stage(output_dir, "continuation", request.display_id)
        outcome = UnitOutcome(request=request, decision=decision)
        outcome.raw = raw
        outcome.working = raw
        outcome.recovered_from = "raw"
        outcome.reconstructed_provenance = reconstructed_provenance(
            request=request,
            decision=decision,
            request_binding_sha256=provider_request.request_binding_sha256,
            translation_options_sha256=provider_request.translation_options_sha256,
            reconstruction_source="translation_raw_response.jsonl",
        )
        qa_request = overlay_verified_d050_bindings(request, registry_map)
        outcome.qa = qa_fn(view, qa_request, raw, known_unit_ids)
        if terminal is not None and _continuation_is_complete(output_dir, request.display_id, terminal):
            hydrated = _hydrate_complete_terminal(
                outcome,
                terminal,
                output_dir=output_dir,
                view=view,
                known_unit_ids=known_unit_ids,
                qa_fn=qa_fn,
            )
            working = hydrated.working or raw
            hydrated.qa = qa_fn(view, qa_request, working, known_unit_ids)
            outcomes.append(hydrated)
            continue
        on_stage("UNIT_START")
        stored_review = load_stage(output_dir, "reviews", request.display_id)
        if stored_review:
            outcome.review = _review_from_payload(stored_review, request.unit_uid)
            usage = stored_review.get("usage")
            if isinstance(usage, dict):
                outcome.review_usage = usage
            latency = stored_review.get("latency_seconds")
            if isinstance(latency, (int, float)):
                outcome.review_latency_seconds = float(latency)
        if outcome.qa and not outcome.qa.get("passed"):
            outcome = _recover_numeric_unit(
                outcome,
                providers=providers,
                provider_request=provider_request,
                view=view,
                known_unit_ids=known_unit_ids,
                qa_fn=qa_fn,
                registry=registry or {"decision": {"eligible": False, "policy_id": None}},
                output_dir=output_dir,
                on_stage=on_stage,
                allow_semantic_narrow=allow_semantic_narrow,
            )
        else:
            outcome = _apply_reviewer_decision(
                outcome,
                providers=providers,
                provider_request=provider_request,
                view=view,
                known_unit_ids=known_unit_ids,
                qa_fn=qa_fn,
                allow_semantic_narrow=allow_semantic_narrow,
                output_dir=output_dir,
                on_stage=on_stage,
            )
        _persist_terminal(output_dir, outcome)
        outcomes.append(outcome)

    order_index = {display_id: index for index, display_id in enumerate(frozen_order)}
    outcomes.sort(key=lambda item: order_index.get(item.request.display_id, 10_000))
    return _finalize_continuation(
        output_dir=output_dir,
        outcomes=outcomes,
        started=started,
        order=frozen_order,
        selection_sha256=selection_sha256,
        source_sha256=source_sha256,
        raw_sha=raw_file_sha,
        registry=registry,
    )


def _finalize_continuation(
    *,
    output_dir: Path,
    outcomes: Sequence[UnitOutcome],
    started: datetime,
    order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    raw_sha: str,
    registry: Mapping[str, Any] | None,
    write_derived: bool = True,
) -> dict[str, Any]:
    final_records = [
        item.qa
        or {
            "unit_uid": item.request.unit_uid,
            "display_id": item.request.display_id,
            "passed": item.final_status == "ACCEPT",
            "diagnostics": [{"code": item.failure_code or item.final_status}],
        }
        for item in outcomes
    ]
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = []
    for row in dispatched:
        outcome = next(item for item in outcomes if item.request.unit_uid == row["unit_uid"])
        status = _review_status(outcome) if outcome.review is not None else (outcome.final_status if outcome.blocked else NOT_PERSISTED)
        reviews.append(
            {
                "unit_uid": row["unit_uid"],
                "display_id": row["display_id"],
                "status": status,
                "issue_types": [issue.code for issue in (outcome.review.issues if outcome.review else [])],
                "explanation": outcome.final_status,
            }
        )
    semantic_report = semantic_review_summary(reviews, excluded)
    approved = [
        row["unit_uid"]
        for row in final_records
        if row.get("passed")
        and semantic_terminal_of_outcome(next(item for item in outcomes if item.request.unit_uid == row["unit_uid"]))
        in SEMANTIC_PASS_TERMINALS
    ]
    final_qa_report = {
            "pass_count": sum(bool(row.get("passed")) for row in final_records),
            "fail_count": sum(not row.get("passed") for row in final_records),
            "records": final_records,
            "final_approved_unit_uids": approved,
            "not_approved_deterministic_fail_unit_uids": [row["unit_uid"] for row in final_records if not row.get("passed")],
            "human_required_unit_uids": [item.request.unit_uid for item in outcomes if item.human_required],
            "blocked_unit_uids": [item.request.unit_uid for item in outcomes if item.blocked],
            "final_status": "CANARY_CONTINUATION",
    }
    if write_derived:
        write_json_replace(output_dir / "semantic_review_report.json", semantic_report)
        write_json_replace(output_dir / "translation_final_qa_report.json", final_qa_report)
    extra = {
        "immutable_provenance_violations": 0,
        "final_qa_pass": len(approved),
        "source_integrity_violations": sum(_integrity_codes(row)[0] for row in final_records),
        "protected_integrity_violations": sum(_integrity_codes(row)[1] for row in final_records),
        "review_not_persisted": sum(item.review is None and not item.blocked and item.raw is not None for item in outcomes),
        "reexecution_required": 0,
        "resumed": True,
        "api_calls": 0,
        "recovered_from_raw": sum(item.raw is not None for item in outcomes),
        "recovered_from_unit": 0,
    }
    report = _report(outcomes, started, order, selection_sha256, source_sha256, raw_sha=raw_sha, gate=None, extra=extra)
    review_latencies = [item.review_latency_seconds for item in outcomes if item.review_latency_seconds is not None]
    review_latencies.extend(item.second_review_latency_seconds for item in outcomes if item.second_review_latency_seconds is not None)
    repair_latencies = [item.repair_latency_seconds for item in outcomes if item.repair_latency_seconds is not None]
    review_usage = [item.review_usage for item in outcomes if item.review_usage]
    review_usage.extend(item.second_review_usage for item in outcomes if item.second_review_usage)
    repair_usage = [item.repair_usage for item in outcomes if item.repair_usage]
    report["translation_api_calls"] = sum(item.translation_api_calls for item in outcomes)
    report["reviewer_api_calls"] = sum(item.reviewer_api_calls for item in outcomes)
    report["repair_api_calls"] = sum(item.repair_api_calls for item in outcomes)
    report["raw_reused_count"] = sum(item.raw is not None for item in outcomes)
    report["blocked_units"] = sum(item.blocked for item in outcomes)
    raw_qa_path = output_dir / "translation_raw_qa_report.json"
    if raw_qa_path.is_file():
        raw_qa = json.loads(raw_qa_path.read_text(encoding="utf-8"))
        report["initial_deterministic_qa_pass"] = raw_qa.get("pass_count")
        report["initial_deterministic_qa_fail"] = raw_qa.get("fail_count")
    report["numeric_recovery"] = {
        "failing_work_order_ids": (registry or {}).get("failing_work_order_ids"),
        "policy_id": ((registry or {}).get("decision") or {}).get("policy_id"),
        "eligible": ((registry or {}).get("decision") or {}).get("eligible"),
        "denial_reason": ((registry or {}).get("decision") or {}).get("denial_reason"),
        "repaired": sum(item.repair_applied and item.repair_policy_id == "NUMERIC_REPRESENTATION" for item in outcomes),
        "success": sum(
            item.repair_applied and item.repair_policy_id == "NUMERIC_REPRESENTATION" and item.final_status == "ACCEPT"
            for item in outcomes
        ),
    }
    report["semantic_repair"] = {
        "attempted": sum(item.repair_policy_id == "SEMANTIC_NARROW_SEGMENT" or (item.second_review is not None) for item in outcomes),
        "success": sum(item.repair_applied and item.repair_policy_id == "SEMANTIC_NARROW_SEGMENT" and item.final_status == "ACCEPT" for item in outcomes),
    }
    second = Counter((item.second_review.verdict if item.second_review is not None else None) for item in outcomes)
    report["second_review_accept"] = second.get("ACCEPT", 0)
    report["second_review_repair"] = second.get("REPAIR", 0)
    report["second_review_human_required"] = second.get("HUMAN_REQUIRED", 0)
    report["recovery"] = {
        "resumed": True,
        "api_calls": 0,
        "recovered_from_raw": extra["recovered_from_raw"],
        "recovered_from_unit": 0,
    }
    report["continuation_latency"] = {
        "review_sample_count": len(review_latencies),
        "review_total_seconds": round(sum(review_latencies), 6) if review_latencies else 0,
        "repair_sample_count": len(repair_latencies),
        "repair_total_seconds": round(sum(repair_latencies), 6) if repair_latencies else 0,
    }
    report["continuation_usage"] = {
        "review_total_tokens": sum(int(row.get("total_tokens") or 0) for row in review_usage),
        "repair_total_tokens": sum(int(row.get("total_tokens") or 0) for row in repair_usage),
    }
    report["latency"]["provider_latency"] = NOT_PERSISTED
    report["usage"] = {"prompt_tokens": NOT_PERSISTED, "completion_tokens": NOT_PERSISTED, "total_tokens": NOT_PERSISTED}
    gate = evaluate_canary_gate(report)
    report["gate"] = gate
    if write_derived:
        write_json_replace(output_dir / "canary_report.json", report)
        write_json_replace(output_dir / "canary_gate.json", gate)
        write_json_replace(output_dir / "canary_continuation_report.json", report)
    return report


CANARY_IMMUTABLE_EVIDENCE = (
    "translation_request.jsonl",
    "translation_raw_response.jsonl",
    "translation_raw_qa_report.json",
    "translation_provider_provenance.jsonl",
)


def _current_acquisitions(*, output_dir: Path, requests: Sequence[CalibrationRequest], source_sha256: str,
                          selection_sha256: str, translation_bible: Path | None) -> dict[str, TranslationResult]:
    """Use the one request-bound acquisition resolver for production state.

    Ordinary canary folders retain their historical base-RAW behavior.  A
    production checkpoint with an append-only reacquisition sidecar must not.
    """
    if not (output_dir / "provider_reacquisitions").is_dir():
        return load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    batch_dir = output_dir.parent
    match = __import__("re").fullmatch(r"full_book_batch_(\d{3})", batch_dir.name)
    if match is None:
        raise RuntimeError("CURRENT_ACQUISITION_BATCH_CONTEXT_INVALID")
    batch_id = f"FULL_BOOK_BATCH_{match.group(1)}"
    manifest = batch_dir / "batch_manifest.json"
    selection = batch_dir.parent / f"{batch_dir.name}_selection.json"
    effective_path, effective_requests = resolve_effective_translation_request(
        checkpoint_dir=output_dir, source_sha256=source_sha256, batch_id=batch_id,
        checkpoint_id=output_dir.name, selection_sha256=selection_sha256,
        manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
        expected_order=[request.display_id for request in requests],
    )
    if [item.model_dump() for item in effective_requests] != [item.model_dump() for item in requests]:
        raise RuntimeError("CURRENT_ACQUISITION_EFFECTIVE_REQUEST_MISMATCH")
    bindings = {}
    for item in requests:
        provider_request = enrich_request(item, translation_bible=translation_bible)
        if route_semantic_unit(item).route == "QWEN_MAX_STRUCTURED":
            provider_request = replace(provider_request, output_contract_version="booktrans.structured-translation.v2")
        bindings[item.unit_uid] = provider_request.request_binding_sha256
    current, _historical = resolve_effective_provider_acquisition(
        checkpoint_dir=output_dir, requests=requests, request_binding_by_uid=bindings,
        source_sha256=source_sha256, batch_id=batch_id, checkpoint_id=output_dir.name,
        selection_sha256=selection_sha256, manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
        effective_request_artifact_sha256=sha256(effective_path.read_bytes()).hexdigest(),
    )
    return current


def canary_immutable_fingerprints(output_dir: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in CANARY_IMMUTABLE_EVIDENCE:
        path = output_dir / name
        if path.is_file():
            found[name] = sha256(path.read_bytes()).hexdigest()
    return found


def recalculate_canary_from_persisted_evidence(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    production_run: Path,
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
    write_derived: bool = True,
    include_outcomes: bool = False,
) -> dict[str, Any]:
    """Recompute Canary from RAW/review/repair evidence.  Never calls translation APIs."""
    before = canary_immutable_fingerprints(output_dir)
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    existing_raw = _current_acquisitions(
        output_dir=output_dir, requests=requests, source_sha256=source_sha256,
        selection_sha256=selection_sha256, translation_bible=translation_bible,
    )
    raw_line_sha = _raw_line_sha_by_uid(output_dir / "translation_raw_response.jsonl")
    raw_file_sha = raw_sha256(output_dir / "translation_raw_response.jsonl") or ""
    started = datetime.now(timezone.utc)
    outcomes: list[UnitOutcome] = []
    d050_records: list[dict[str, Any]] = []
    for request in requests:
        stored = load_unit_result(unit_path(output_dir, request.display_id))
        raw = existing_raw.get(request.unit_uid)
        reasons = verify_raw_unit(
            request,
            raw,
            stored,
            source_sha256=source_sha256,
            selection_sha256=selection_sha256,
            translation_bible=translation_bible,
            original_line_sha256=raw_line_sha.get(request.unit_uid),
        )
        if reasons or raw is None:
            outcomes.append(_blocked(request, reasons))
            continue
        qa_request = overlay_verified_d050_bindings(request, registry_map)
        decision = route_semantic_unit(request)
        outcome = UnitOutcome(request=request, decision=decision)
        outcome.raw = raw
        outcome.working = raw
        outcome.recovered_from = "raw"
        # Preserve the last locally evaluated candidate only as historical
        # evidence.  Candidate Authority is resolved later by canonical
        # disposition, so a rejected corrective candidate cannot become the
        # head merely because it is present in this immutable unit record.
        if isinstance(stored, Mapping) and isinstance(stored.get("working"), Mapping):
            outcome.working = TranslationResult.model_validate(stored["working"])
        terminal = load_stage(output_dir, "continuation", request.display_id) or {}
        group_candidate = load_stage(output_dir, "group_repair_candidates", request.display_id)
        group_review_direct = load_stage(output_dir, "group_reviews", request.display_id)
        human_candidate = load_stage(output_dir, "human_approved_candidates", request.display_id)
        human_direct = load_stage(output_dir, "human_adjudications", request.display_id)
        human_current_id = None
        if human_candidate is not None:
            human_current_id = str(human_candidate.get("current_display_id") or request.display_id)
        elif human_direct is not None:
            human_current_id = request.display_id
        group_current_id = None
        group_working = None
        if group_candidate is not None and group_candidate.get("working"):
            group_working = TranslationResult.model_validate(group_candidate["working"])
        patched_group = group_working is not None and group_working.model_dump() != raw.model_dump()
        if patched_group:
            group_current_id = str(group_candidate.get("current_display_id") or request.display_id)
        elif group_review_direct is not None:
            group_current_id = request.display_id
        v4_second = load_stage(output_dir, "re_reviews_v4", request.display_id)
        v3_review = load_stage(output_dir, "reviews_v3", request.display_id)
        v3_second = load_stage(output_dir, "re_reviews_v3", request.display_id)
        v3_repair = load_stage(output_dir, "repair_candidates_v3", request.display_id)
        v2_review = load_stage(output_dir, "reviews_v2", request.display_id)
        v2_second = load_stage(output_dir, "re_reviews_v2", request.display_id)
        v2_repair = load_stage(output_dir, "repair_candidates_v2", request.display_id)
        review_payload = None
        second_payload = None
        human_payload = load_stage(output_dir, "human_adjudications", human_current_id) if human_current_id else None
        if human_payload is not None:
            outcome.working = raw
            if human_candidate and human_candidate.get("working"):
                outcome.working = TranslationResult.model_validate(human_candidate["working"])
                outcome.repair_applied = True
                outcome.repair_policy_id = "HUMAN_SEMANTIC_ADJUDICATION"
            review_payload = load_stage(output_dir, "group_reviews", human_current_id) or v3_review
            second_payload = load_stage(output_dir, "group_re_reviews", human_current_id) or v4_second
        elif group_current_id:
            outcome.working = raw
            if patched_group and group_working is not None:
                outcome.working = group_working
                outcome.repair_applied = True
                outcome.repair_policy_id = "SEMANTIC_GROUP_SPAN"
            review_payload = load_stage(output_dir, "group_reviews", group_current_id)
            second_payload = load_stage(output_dir, "group_re_reviews", group_current_id)
        elif v3_review is not None:
            outcome.working = raw
            authoritative_second = v4_second if v4_second is not None else v3_second
            if v3_repair and v3_repair.get("working") and authoritative_second and authoritative_second.get("verdict") == "ACCEPT":
                outcome.working = TranslationResult.model_validate(v3_repair["working"])
                outcome.repair_applied = True
                if v3_repair.get("repair_policy_id") or terminal.get("repair_policy_id"):
                    outcome.repair_policy_id = str(v3_repair.get("repair_policy_id") or terminal.get("repair_policy_id"))
            review_payload = v3_review
            second_payload = authoritative_second
        elif v2_review is not None:
            outcome.working = raw
            if v2_repair and v2_repair.get("working") and v2_second and v2_second.get("verdict") == "ACCEPT":
                outcome.working = TranslationResult.model_validate(v2_repair["working"])
                outcome.repair_applied = True
                if v2_repair.get("repair_policy_id") or terminal.get("repair_policy_id"):
                    outcome.repair_policy_id = str(v2_repair.get("repair_policy_id") or terminal.get("repair_policy_id"))
            review_payload = v2_review
            second_payload = v2_second
        else:
            repair_payload = load_stage(output_dir, "repair_candidates", request.display_id)
            if repair_payload is None:
                repair_payload = load_stage(output_dir, "semantic_repair_candidates", request.display_id)
            if repair_payload and repair_payload.get("working"):
                outcome.working = TranslationResult.model_validate(repair_payload["working"])
                outcome.repair_applied = True
                if terminal.get("repair_policy_id"):
                    outcome.repair_policy_id = str(terminal["repair_policy_id"])
            review_payload = load_stage(output_dir, "reviews", request.display_id)
            second_payload = load_stage(output_dir, "re_reviews", request.display_id)
        # Production semantic-rescue evidence predates the generic canary
        # stages above but is equally durable.  During a time-sliced R1
        # resume, its final review is the only evidence that the primary
        # repair is eligible for the one corrective transition.  It is not
        # authority itself; Candidate Authority remains resolved downstream.
        if review_payload is None:
            rescue_review = load_stage(output_dir, "semantic_rescue_rereviews", request.display_id)
            if rescue_review is not None:
                review_payload = rescue_review.get("review") if isinstance(rescue_review.get("review"), Mapping) else rescue_review
        if second_payload is None:
            rescue_final = load_stage(output_dir, "semantic_rescue_final_reviews", request.display_id)
            if rescue_final is not None:
                second_payload = rescue_final.get("review") if isinstance(rescue_final.get("review"), Mapping) else rescue_final
        # A persisted unit result is immutable historical evidence of the
        # locally reviewed candidate.  Use it only when no newer stage record
        # exists; Candidate Authority independently resolves the current head.
        if review_payload is None and isinstance(stored, Mapping) and isinstance(stored.get("review"), Mapping):
            review_payload = stored["review"]
        if second_payload is None and isinstance(stored, Mapping) and isinstance(stored.get("second_review"), Mapping):
            second_payload = stored["second_review"]
        outcome.qa = qa_fn(views[request.display_id], qa_request, outcome.working or raw, known_unit_ids)
        d050_records.append(outcome.qa)
        if review_payload:
            outcome.review = _review_from_payload(review_payload, request.unit_uid)
            usage = review_payload.get("usage")
            if isinstance(usage, dict):
                outcome.review_usage = usage
            latency = review_payload.get("latency_seconds")
            if isinstance(latency, (int, float)):
                outcome.review_latency_seconds = float(latency)
        if second_payload:
            outcome.second_review = _review_from_payload(second_payload, request.unit_uid)
            usage = second_payload.get("usage")
            if isinstance(usage, dict):
                outcome.second_review_usage = usage
            latency = second_payload.get("latency_seconds")
            if isinstance(latency, (int, float)):
                outcome.second_review_latency_seconds = float(latency)
        if outcome.qa and not outcome.qa.get("passed"):
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            outcome.semantic_terminal = "HUMAN_REQUIRED"
            outcome.failure_family = "LOCAL_CONTENT_QA_FAILURE"
            outcome.failure_code = ",".join(qa_codes(outcome.qa)) or "DETERMINISTIC_QA_FAILED"
        elif human_payload is not None and human_payload.get("decision") == "HUMAN_APPROVED":
            outcome.final_status = "HUMAN_APPROVED"
            outcome.human_required = False
            outcome.semantic_terminal = "HUMAN_APPROVED"
        elif human_payload is not None and human_payload.get("decision") == "HUMAN_REJECTED":
            outcome.final_status = "HUMAN_REJECTED"
            outcome.human_required = True
            outcome.blocked = True
            outcome.semantic_terminal = "HUMAN_REJECTED"
        elif outcome.second_review is not None and outcome.second_review.verdict != "ACCEPT":
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            outcome.semantic_terminal = "HUMAN_REQUIRED"
        elif outcome.review is not None and outcome.review.verdict == "HUMAN_REQUIRED":
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            outcome.semantic_terminal = "HUMAN_REQUIRED"
        elif outcome.review is not None and outcome.review.verdict == "ACCEPT":
            outcome.final_status = "ACCEPT"
            outcome.semantic_terminal = "MODEL_ACCEPTED"
        elif outcome.review is not None and outcome.review.verdict == "REPAIR" and outcome.repair_applied and (
            outcome.second_review is not None and outcome.second_review.verdict == "ACCEPT"
        ):
            outcome.final_status = "ACCEPT"
            outcome.semantic_terminal = "MODEL_ACCEPTED"
        elif outcome.review is None:
            outcome.final_status = "QA_PASS_REVIEW_NOT_PERSISTED"
            outcome.human_required = False
            outcome.semantic_terminal = "UNRESOLVED"
        else:
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            outcome.semantic_terminal = "HUMAN_REQUIRED"
        outcomes.append(outcome)
        if write_derived:
            write_json_replace(stage_path(output_dir, "continuation", request.display_id), _terminal_payload(outcome))
    if write_derived:
        write_json_replace(
        output_dir / "translation_d050_aware_qa_report.json",
        {
            "artifact_type": "CANARY_D050_AWARE_QA_REPORT",
            "schema_version": 1,
            "pass_count": sum(bool(row.get("passed")) for row in d050_records),
            "fail_count": sum(not row.get("passed") for row in d050_records),
            "registry_keys": [f"{key[0]}:{key[1][:12]}" for key in registry_map],
            "records": d050_records,
        },
    )
    report = _finalize_continuation(
        output_dir=output_dir,
        outcomes=outcomes,
        started=started,
        order=frozen_order,
        selection_sha256=selection_sha256,
        source_sha256=source_sha256,
        raw_sha=raw_file_sha,
        registry=None,
        write_derived=write_derived,
    )
    report["translation_api_calls"] = 0
    report["d050_aware_qa_pass"] = sum(bool(row.get("passed")) for row in d050_records)
    report["d050_aware_qa_fail"] = sum(not row.get("passed") for row in d050_records)
    report["gate"] = evaluate_canary_gate(report)
    if write_derived:
        write_json_replace(output_dir / "canary_report.json", report)
        write_json_replace(output_dir / "canary_gate.json", report["gate"])
        write_json_replace(output_dir / "canary_continuation_report.json", report)
        after = canary_immutable_fingerprints(output_dir)
        if after != before:
            raise RuntimeError("CANARY_IMMUTABLE_EVIDENCE_MUTATED")
    return (report, outcomes) if include_outcomes else report


def reconstruct_current_production_evidence(**kwargs: Any) -> tuple[dict[str, Any], list[UnitOutcome]]:
    """Pure persisted-evidence reconstruction shared by reporting and recovery projection."""
    return recalculate_canary_from_persisted_evidence(**kwargs, write_derived=False, include_outcomes=True)


def project_current_canonical_recovery(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    production_run: Path,
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    active_batch: str,
    checkpoint: str,
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
) -> dict[str, Any] | None:
    """Project the one current recovery obligation from persisted evidence.

    This is deliberately a read-only consumer of the canonical production
    disposition boundary.  Handoffs, supervisor state, and artifact-shape
    heuristics are not inputs.  More than one simultaneous recovery is an
    authority ambiguity and therefore fails closed instead of choosing one.
    """
    from booktrans.production_worker.production_checkpoint import (
        _candidate_binding,
        _canonical_sha256,
        _current_head_rereview,
        _resolve_checkpoint_authoritative_candidate,
        canonical_production_disposition,
    )

    _report, outcomes = reconstruct_current_production_evidence(
        output_dir=output_dir,
        requests=requests,
        production_run=production_run,
        views=views,
        known_unit_ids=known_unit_ids,
        qa_fn=qa_fn,
        frozen_order=frozen_order,
        selection_sha256=selection_sha256,
        source_sha256=source_sha256,
        translation_bible=translation_bible,
        d050_registry=d050_registry,
    )
    recoveries: list[tuple[UnitOutcome, dict[str, Any]]] = []
    for outcome in outcomes:
        decision = canonical_production_disposition(outcome, checkpoint_dir=output_dir)
        if decision["disposition"] == "RECOVERY_REQUIRED":
            recoveries.append((outcome, decision))
    if not recoveries:
        return None
    # Each reconstructed outcome has one canonical disposition.  Several
    # different current work orders may therefore legitimately require a
    # bounded recovery at once; they are not competing authority for a single
    # unit.  Drain that derived worklist in the existing frozen checkpoint
    # order, one obligation per worker invocation.  Same-unit conflicts remain
    # impossible at this boundary because canonical disposition is singular.
    order_index = {display_id: index for index, display_id in enumerate(frozen_order)}
    recoveries.sort(key=lambda item: order_index.get(item[0].request.display_id, len(order_index)))
    outcome, decision = recoveries[0]
    if outcome.raw is None:
        raise RuntimeError("CURRENT_CANONICAL_RECOVERY_RAW_MISSING")
    current = _resolve_checkpoint_authoritative_candidate(
        output_dir, outcome.request.display_id, outcome.raw,
    ).candidate
    bound_review = _current_head_rereview(outcome, checkpoint_dir=output_dir, current=current)
    scope = [] if bound_review is None else sorted({
        index for issue in bound_review.issues for index in issue.affected_segment_indices
    })
    return {
        "schema_version": 1,
        "production_disposition": decision["disposition"],
        "disposition_reason": decision["reason"],
        "recovery_family": decision["recovery_family"],
        "failing_work_order_ids": [outcome.request.display_id],
        "disposition_evidence_references": decision["evidence_references"],
        "recovery_fingerprint": {
            "batch": active_batch,
            "checkpoint": checkpoint,
            "work_order": outcome.request.display_id,
            "current_head_sha256": _candidate_binding(current),
            "recovery_family": decision["recovery_family"],
            "canonical_reason": decision["reason"],
            "affected_segment_indices": scope,
            "bound_review_result_sha256": (
                None if bound_review is None else _canonical_sha256(bound_review.model_dump())
            ),
            "recovery_budget_used": int(
                load_stage(output_dir, "semantic_rescue_corrective", outcome.request.display_id) is not None
            ),
        },
    }



