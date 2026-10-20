"""Versioned semantic-context reviews from persisted RAW.  Never retranslates."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from functools import lru_cache

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import TranslationView
from booktrans.providers.qwen_max import QWEN_MAX
from booktrans.production_worker.artifacts import write_json_replace
from booktrans.production_worker.canary import PRODUCTION_BATCH, PRODUCTION_CHECKPOINT
from booktrans.production_worker.continuation import (
    ForbiddenTranslationProvider,
    _binding,
    _blocked,
    _call_repair,
    _call_reviewer,
    _contract_from_stored,
    _review_from_payload,
    _terminal_payload,
    canary_immutable_fingerprints,
    overlay_verified_d050_bindings,
    verify_raw_unit,
)
from booktrans.production_worker.diagnostics import CanaryDiagnostics
from booktrans.production_worker.pipeline import UnitOutcome, WorkerProviders
from booktrans.production_worker.qa import QaFn, qa_codes
from booktrans.production_worker.reviewer_context import (
    ReviewerContextUnit,
    auxiliary_footnote_for_work_order,
    footnote_body_identities,
)
from booktrans.production_worker.group_repair import (
    decide_group_repair_eligibility,
    decide_semantic_group_repair_contract,
    production_sealed_work_order_ids,
)
from booktrans.production_worker.semantic_review_group import (
    REVIEW_GROUP_CONTRACT_VERSION,
    build_semantic_review_group,
    reject_sealed_member_repair,
)
from booktrans.providers.qwen_max import GroupReviewResult, SemanticGroupRepairContract
from booktrans.production_worker.selection import (
    DEFAULT_BLOCKS,
    assert_not_production_checkpoint,
    load_checkpoint_d050_registry,
    load_selected_jsonl_by_display_id,
)
from booktrans.production_worker.unit_store import (
    load_raw_by_uid,
    load_stage,
    load_unit_result,
    persist_stage_once,
    stage_path,
    unit_path,
)
from booktrans.providers.failures import classify_provider_exception
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.router import route_semantic_unit
from booktrans.providers.translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
)

_PROVIDER_ERRORS = (ProviderContractGap, ProviderRuntimeError, ProviderConfigurationError)
NOT_PERSISTED = "NOT_PERSISTED"


@lru_cache(maxsize=8)
def _index_section_first_body_ordinal(blocks_path: Path) -> int | None:
    """Return the physical start of the final OOXML Index section.

    This deliberately keys off the standalone ``Index`` section heading in
    the immutable body IR, then uses paragraph order.  It never classifies an
    item as an index entry merely because its text happens to resemble one.
    """
    start: int | None = None
    with blocks_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            payload = json.loads(line)
            if (
                payload.get("story") == "body"
                and payload.get("block_type") == "body_paragraph"
                and str(payload.get("source_plain_text") or "").strip() == "Index"
            ):
                display_id = str(payload.get("display_id") or "")
                try:
                    start = int(display_id.rsplit(".", 1)[1])
                except (IndexError, ValueError):
                    continue
    return start


def _is_index_section_body(display_id: str, block: BlockIR, *, blocks_path: Path) -> bool:
    if block.story != "body" or block.block_type != "body_paragraph":
        return False
    start = _index_section_first_body_ordinal(blocks_path)
    try:
        ordinal = int(display_id.rsplit(".", 1)[1])
    except (IndexError, ValueError):
        return False
    return start is not None and ordinal > start


def load_authoritative_review(output_dir: Path, display_id: str) -> dict[str, Any] | None:
    return (
        load_stage(output_dir, "group_reviews", display_id)
        or load_stage(output_dir, "reviews_v3", display_id)
        or load_stage(output_dir, "reviews_v2", display_id)
        or load_stage(output_dir, "reviews", display_id)
    )


def load_authoritative_second_review(output_dir: Path, display_id: str) -> dict[str, Any] | None:
    return (
        load_stage(output_dir, "group_re_reviews", display_id)
        or load_stage(output_dir, "re_reviews_v4", display_id)
        or load_stage(output_dir, "re_reviews_v3", display_id)
        or load_stage(output_dir, "re_reviews_v2", display_id)
        or load_stage(output_dir, "re_reviews", display_id)
    )


def context_units_for_order(order: Sequence[str], *, blocks_path: Path = DEFAULT_BLOCKS) -> list[ReviewerContextUnit]:
    """Load exact local IR context for the frozen work-order set.

    A textbox paragraph ordinal is local to its textbox, not a body ordinal.
    Its frozen ``textbox.<n>`` family is therefore the authoritative local
    context; this is IR reconstruction, not a text or position heuristic.
    """
    ordinals: list[int] = []
    textbox_families: set[str] = set()
    for display_id in order:
        parts = str(display_id).split(".")
        if len(parts) == 4 and parts[0] == "textbox" and parts[2] == "p":
            textbox_families.add(".".join(parts[:2]))
        if not str(display_id).startswith("body.p."):
            continue
        try:
            ordinals.append(int(str(display_id).rsplit(".", 1)[1]))
        except (IndexError, ValueError):
            continue
    wanted: list[str] = []
    if any(str(item).startswith("body.p.") for item in order) and ordinals:
        wanted.extend(f"body.p.{number:05d}" for number in range(min(ordinals) - 2, max(ordinals) + 3))
    if textbox_families:
        with blocks_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                payload = json.loads(line)
                display_id = str(payload.get("display_id") or "")
                if any(display_id.startswith(f"{family}.p.") for family in textbox_families):
                    wanted.append(display_id)
    if not wanted:
        return []
    wanted = list(dict.fromkeys(wanted))
    blocks = load_selected_jsonl_by_display_id(blocks_path, BlockIR, wanted)
    return [
        ReviewerContextUnit(
            display_id=blocks[item].display_id,
            unit_uid=blocks[item].unit_uid,
            story=blocks[item].story,
            part_name=blocks[item].part_name,
            content_role=("index_entry" if _is_index_section_body(item, blocks[item], blocks_path=blocks_path) else blocks[item].content_role),
            block_type=blocks[item].block_type,
            source_text=blocks[item].source_plain_text,
            chapter_id="chapter_21",
            translation_scope=None if blocks[item].translation_scope is None else str(blocks[item].translation_scope),
        )
        for item in wanted
        if item in blocks
    ]


def build_unit_group(
    request: CalibrationRequest,
    units: Sequence[ReviewerContextUnit],
    registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
    *,
    candidate_texts: Mapping[str, str] | None = None,
    segment_counts: Mapping[str, int] | None = None,
    sealed_work_order_ids: frozenset[str] | None = None,
    unsealed_work_order_ids: frozenset[str] | None = None,
):
    current = next((item for item in units if item.display_id == request.display_id or item.unit_uid == request.unit_uid), None)
    ordered = list(units)
    if current is None:
        current = ReviewerContextUnit(
            display_id=request.display_id,
            unit_uid=request.unit_uid,
            story=request.story,
            part_name="word/document.xml",
            content_role=request.content_role,
            block_type=request.content_role,
            source_text=request.source_text,
            chapter_id="chapter_21",
        )
        ordered.append(current)
    return build_semantic_review_group(
        current,
        ordered,
        footnote_bodies=footnote_body_identities(registry),
        auxiliary_footnote=auxiliary_footnote_for_work_order(
            registry, logical_work_order_id=request.display_id, unit_uid=request.unit_uid
        ),
        candidate_texts=candidate_texts,
        segment_counts=segment_counts,
        sealed_work_order_ids=sealed_work_order_ids,
        unsealed_work_order_ids=unsealed_work_order_ids,
    )


def candidate_text_map(
    requests: Sequence[CalibrationRequest],
    raw_by_uid: Mapping[str, TranslationResult],
) -> dict[str, str]:
    found: dict[str, str] = {}
    for request in requests:
        raw = raw_by_uid.get(request.unit_uid)
        if raw is None:
            continue
        found[request.display_id] = "".join(segment.text for segment in raw.target_segments)
        found[request.unit_uid] = found[request.display_id]
    return found


def segment_count_map(requests: Sequence[CalibrationRequest]) -> dict[str, int]:
    found: dict[str, int] = {}
    for request in requests:
        count = len(request.source_segments)
        found[request.display_id] = count
        found[request.unit_uid] = count
    return found


def repaired_candidate_text(result: TranslationResult) -> str:
    return "".join(segment.text for segment in result.target_segments)


def build_repaired_second_review_group(
    request: CalibrationRequest,
    units: Sequence[ReviewerContextUnit],
    registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]],
    *,
    candidate_texts: Mapping[str, str],
    segment_counts: Mapping[str, int],
    repaired: TranslationResult,
):
    texts = dict(candidate_texts)
    current = repaired_candidate_text(repaired)
    texts[request.display_id] = current
    texts[request.unit_uid] = current
    return build_unit_group(
        request,
        units,
        registry,
        candidate_texts=texts,
        segment_counts=segment_counts,
    )


def assert_repaired_second_review_payload(
    group,
    *,
    request: CalibrationRequest,
    repaired: TranslationResult,
    previous_display_id: str | None,
) -> None:
    """Fail closed if the second-review group still carries the original current slice."""
    repaired_text = repaired_candidate_text(repaired)
    if request.display_id not in group.member_work_order_ids:
        raise RuntimeError("CORRECTED_SECOND_REVIEW_CURRENT_NOT_MEMBER")
    if group.repairable_work_order_ids != (request.display_id,):
        raise RuntimeError("CORRECTED_SECOND_REVIEW_REPAIRABLE_SCOPE")
    current = next(member for member in group.members if member.display_id == request.display_id)
    if current.candidate_text != repaired_text:
        raise RuntimeError("CORRECTED_SECOND_REVIEW_CURRENT_SLICE_NOT_REPAIRED")
    if repaired_text not in (group.full_candidate_span or ""):
        raise RuntimeError("CORRECTED_SECOND_REVIEW_SPAN_MISSING_REPAIRED_TEXT")
    if previous_display_id:
        if previous_display_id not in group.member_work_order_ids:
            raise RuntimeError("CORRECTED_SECOND_REVIEW_PREVIOUS_NOT_MEMBER")
        previous = next(member for member in group.members if member.display_id == previous_display_id)
        if previous.repairable or not previous.sealed:
            raise RuntimeError("CORRECTED_SECOND_REVIEW_PREVIOUS_NOT_SEALED")


class ForbiddenRepairProvider:
    def repair(self, *args, **kwargs):
        raise RuntimeError("REPAIR_FORBIDDEN_ON_CORRECTED_SECOND_REVIEW")


def review_canary_units_with_semantic_context(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    providers: WorkerProviders,
    production_run: Path,
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    display_ids: Sequence[str],
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
    context_units: Sequence[ReviewerContextUnit] | None = None,
    diagnostics: CanaryDiagnostics | None = None,
    allow_semantic_narrow: bool = True,
) -> dict[str, Any]:
    """Review named units from original RAW with group-aware reviewer context.

    Never calls translation providers.  Never overwrites v1 or v2 review/repair files.
    """
    providers = WorkerProviders(
        mt=ForbiddenTranslationProvider(),
        structured=ForbiddenTranslationProvider(),
        reviewer=providers.reviewer,
        repair=providers.repair,
    )
    assert_not_production_checkpoint(
        output_dir, run=production_run, batch_label=PRODUCTION_BATCH, checkpoint=PRODUCTION_CHECKPOINT
    )
    before = canary_immutable_fingerprints(output_dir)
    prior_review_hashes = _prior_review_hashes(output_dir)
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    units = list(context_units) if context_units is not None else context_units_for_order(frozen_order)
    existing_raw = load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    texts = candidate_text_map(requests, existing_raw)
    counts = segment_count_map(requests)
    wanted = {str(item) for item in display_ids}
    started = datetime.now(timezone.utc)
    outcomes: list[UnitOutcome] = []
    reviewer_calls = 0
    repair_calls = 0
    for request in requests:
        if request.display_id not in wanted:
            continue
        stored = load_unit_result(unit_path(output_dir, request.display_id))
        raw = existing_raw.get(request.unit_uid)
        reasons = verify_raw_unit(
            request,
            raw,
            stored,
            source_sha256=source_sha256,
            selection_sha256=selection_sha256,
            translation_bible=translation_bible,
            original_line_sha256=None,
        )
        if reasons or raw is None:
            outcomes.append(_blocked(request, reasons))
            continue
        qa_request = overlay_verified_d050_bindings(request, registry_map)
        group = build_unit_group(
            request,
            units,
            registry_map,
            candidate_texts=texts,
            segment_counts=counts,
        )
        if load_stage(output_dir, "review_groups", request.display_id) is None:
            persist_stage_once(
                output_dir,
                "review_groups",
                request.display_id,
                {"artifact_type": "CANARY_REVIEW_GROUP", "schema_version": 1, **group.as_payload()},
            )
        decision = route_semantic_unit(request)
        outcome = UnitOutcome(request=request, decision=decision)
        outcome.raw = raw
        outcome.working = raw
        outcome.recovered_from = "raw"
        outcome.qa = qa_fn(views[request.display_id], qa_request, raw, known_unit_ids)
        if not outcome.qa.get("passed"):
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            outcome.failure_family = "LOCAL_CONTENT_QA_FAILURE"
            outcome.failure_code = ",".join(qa_codes(outcome.qa)) or "DETERMINISTIC_QA_FAILED"
            _write_terminal(output_dir, outcome)
            outcomes.append(outcome)
            continue
        provider_request = _binding(request, translation_bible)
        payload = group.as_reviewer_context_payload()
        stored_review = load_stage(output_dir, "reviews_v3", request.display_id)
        if stored_review is None:
            if diagnostics is not None:
                diagnostics.emit(
                    "REVIEW_GROUP_START",
                    id=request.display_id,
                    group_id=group.semantic_review_group_id,
                    group_hash=group.group_hash,
                )
            try:
                review, usage, latency = _call_reviewer(
                    providers, provider_request, raw, outcome.qa, semantic_context=payload
                )
            except _PROVIDER_ERRORS as error:
                classified = classify_provider_exception(error)
                outcome.final_status = classified.family
                outcome.failure_family = classified.family
                outcome.failure_code = classified.code
                outcome.human_required = True
                _write_terminal(output_dir, outcome)
                outcomes.append(outcome)
                continue
            reviewer_calls += 1
            outcome.reviewer_api_calls += 1
            outcome.review = review
            outcome.review_usage = usage
            outcome.review_latency_seconds = latency
            persist_stage_once(
                output_dir,
                "reviews_v3",
                request.display_id,
                {
                    "artifact_type": "CANARY_REVIEW_RESULT_V3",
                    "schema_version": 1,
                    "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "member_work_order_ids": list(group.member_work_order_ids),
                    "repairable_work_order_ids": list(group.repairable_work_order_ids),
                    "display_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "verdict": review.verdict,
                    "issues": [issue.model_dump() for issue in review.issues],
                    "reviewer": "QWEN_MAX_REVIEWER",
                    "model": QWEN_MAX,
                    "usage": usage or NOT_PERSISTED,
                    "latency_seconds": latency if latency is not None else NOT_PERSISTED,
                    "request_binding_sha256": provider_request.request_binding_sha256,
                    "context_hash": group.context.context_hash,
                    "group_hash": group.group_hash,
                    "previous_semantic_unit_uid": group.context.previous_semantic_unit_uid,
                    "next_semantic_unit_uid": group.context.next_semantic_unit_uid,
                },
            )
        else:
            outcome.review = _review_from_payload(stored_review, request.unit_uid)
            usage = stored_review.get("usage")
            if isinstance(usage, dict):
                outcome.review_usage = usage
        if outcome.review is None or outcome.review.verdict == "HUMAN_REQUIRED":
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            _write_terminal(output_dir, outcome)
            outcomes.append(outcome)
            continue
        if outcome.review.verdict == "ACCEPT":
            outcome.final_status = "ACCEPT"
            _write_terminal(output_dir, outcome)
            outcomes.append(outcome)
            continue
        stored_contract = load_stage(output_dir, "repair_contracts_v3", request.display_id)
        if stored_contract is None:
            gate = decide_approved_repair_contract(
                provider_request, raw, qa_codes=(), review=outcome.review, allow_semantic_narrow=allow_semantic_narrow
            )
            if gate.action != "REPAIR" or gate.contract is None:
                outcome.final_status = "HUMAN_REQUIRED"
                outcome.human_required = True
                _write_terminal(output_dir, outcome)
                outcomes.append(outcome)
                continue
            sealed_reason = reject_sealed_member_repair(group, gate.contract)
            if sealed_reason is not None:
                outcome.final_status = "HUMAN_REQUIRED"
                outcome.human_required = True
                outcome.failure_code = sealed_reason
                _write_terminal(output_dir, outcome)
                outcomes.append(outcome)
                continue
            persist_stage_once(
                output_dir,
                "repair_contracts_v3",
                request.display_id,
                {
                    "artifact_type": "CANARY_REPAIR_CONTRACT_V3",
                    "schema_version": 1,
                    "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "member_work_order_ids": list(group.member_work_order_ids),
                    "repairable_work_order_ids": list(group.repairable_work_order_ids),
                    "context_hash": group.context.context_hash,
                    "group_hash": group.group_hash,
                    **gate.contract.model_dump(),
                },
            )
            contract = gate.contract
            outcome.repair_policy_id = gate.policy_id
        else:
            contract = _contract_from_stored(stored_contract)
            sealed_reason = reject_sealed_member_repair(group, contract)
            if sealed_reason is not None:
                outcome.final_status = "HUMAN_REQUIRED"
                outcome.human_required = True
                outcome.failure_code = sealed_reason
                _write_terminal(output_dir, outcome)
                outcomes.append(outcome)
                continue
            outcome.repair_policy_id = contract.policy_id
        stored_repair = load_stage(output_dir, "repair_candidates_v3", request.display_id)
        if stored_repair is None:
            try:
                repaired, usage, latency = _call_repair(providers, provider_request, raw, contract)
            except _PROVIDER_ERRORS as error:
                classified = classify_provider_exception(error)
                outcome.final_status = classified.family
                outcome.failure_family = classified.family
                outcome.failure_code = classified.code
                outcome.human_required = True
                _write_terminal(output_dir, outcome)
                outcomes.append(outcome)
                continue
            repair_calls += 1
            outcome.repair_api_calls += 1
            persist_stage_once(
                output_dir,
                "repair_candidates_v3",
                request.display_id,
                {
                    "artifact_type": "CANARY_REPAIR_CANDIDATE_V3",
                    "schema_version": 1,
                    "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "repairable_work_order_ids": list(group.repairable_work_order_ids),
                    "context_hash": group.context.context_hash,
                    "group_hash": group.group_hash,
                    "working": repaired.model_dump(),
                    "usage": usage or NOT_PERSISTED,
                    "latency_seconds": latency if latency is not None else NOT_PERSISTED,
                },
            )
        else:
            repaired = TranslationResult.model_validate(stored_repair["working"])
            usage = stored_repair.get("usage")
            if isinstance(usage, dict):
                outcome.repair_usage = usage
        repaired_qa = qa_fn(views[request.display_id], qa_request, repaired, known_unit_ids)
        if not repaired_qa.get("passed"):
            outcome.qa = repaired_qa
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            _write_terminal(output_dir, outcome)
            outcomes.append(outcome)
            continue
        stored_second = load_stage(output_dir, "re_reviews_v3", request.display_id)
        if stored_second is None:
            second_group = build_repaired_second_review_group(
                request,
                units,
                registry_map,
                candidate_texts=texts,
                segment_counts=counts,
                repaired=repaired,
            )
            assert_repaired_second_review_payload(
                second_group,
                request=request,
                repaired=repaired,
                previous_display_id=second_group.context.previous_semantic_display_id,
            )
            second_payload_context = second_group.as_reviewer_context_payload()
            try:
                second, usage, latency = _call_reviewer(
                    providers, provider_request, repaired, repaired_qa, semantic_context=second_payload_context
                )
            except _PROVIDER_ERRORS as error:
                classified = classify_provider_exception(error)
                outcome.final_status = classified.family
                outcome.failure_family = classified.family
                outcome.failure_code = classified.code
                outcome.human_required = True
                _write_terminal(output_dir, outcome)
                outcomes.append(outcome)
                continue
            reviewer_calls += 1
            outcome.reviewer_api_calls += 1
            persist_stage_once(
                output_dir,
                "re_reviews_v3",
                request.display_id,
                {
                    "artifact_type": "CANARY_RE_REVIEW_RESULT_V3",
                    "schema_version": 1,
                    "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "member_work_order_ids": list(group.member_work_order_ids),
                    "repairable_work_order_ids": list(group.repairable_work_order_ids),
                    "display_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "verdict": second.verdict,
                    "issues": [issue.model_dump() for issue in second.issues],
                    "usage": usage or NOT_PERSISTED,
                    "latency_seconds": latency if latency is not None else NOT_PERSISTED,
                    "context_hash": group.context.context_hash,
                    "group_hash": group.group_hash,
                },
            )
            outcome.second_review = second
            outcome.second_review_usage = usage
            outcome.second_review_latency_seconds = latency
        else:
            outcome.second_review = _review_from_payload(stored_second, request.unit_uid)
        if outcome.second_review is None or outcome.second_review.verdict != "ACCEPT":
            outcome.final_status = "HUMAN_REQUIRED"
            outcome.human_required = True
            _write_terminal(output_dir, outcome)
            outcomes.append(outcome)
            continue
        outcome.working = repaired
        outcome.qa = repaired_qa
        outcome.repair_applied = True
        outcome.final_status = "ACCEPT"
        _write_terminal(output_dir, outcome)
        outcomes.append(outcome)
    if canary_immutable_fingerprints(output_dir) != before:
        raise RuntimeError("CANARY_IMMUTABLE_EVIDENCE_MUTATED")
    if _prior_review_hashes(output_dir) != prior_review_hashes:
        raise RuntimeError("CANARY_PRIOR_REVIEW_MUTATED")
    return {
        "artifact_type": "CANARY_SEMANTIC_REVIEW_GROUP_RUN",
        "schema_version": 1,
        "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
        "display_ids": list(display_ids),
        "reviewer_api_calls": reviewer_calls,
        "repair_api_calls": repair_calls,
        "translation_api_calls": 0,
        "started_at": started.isoformat(),
        "outcomes": [
            {
                "display_id": item.request.display_id,
                "final_status": item.final_status,
                "review": None if item.review is None else item.review.verdict,
                "second_review": None if item.second_review is None else item.second_review.verdict,
                "repair_applied": item.repair_applied,
                "human_required": item.human_required,
            }
            for item in outcomes
        ],
    }


def _write_terminal(output_dir: Path, outcome: UnitOutcome) -> None:
    write_json_replace(stage_path(output_dir, "continuation", outcome.request.display_id), _terminal_payload(outcome))


def _prior_review_hashes(output_dir: Path) -> dict[str, str]:
    return _stage_hashes(
        output_dir,
        (
            "reviews",
            "re_reviews",
            "repair_contracts",
            "repair_candidates",
            "reviews_v2",
            "re_reviews_v2",
            "repair_contracts_v2",
            "repair_candidates_v2",
            "review_contexts",
        ),
    )


def _stage_hashes(output_dir: Path, folders: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for folder in folders:
        directory = output_dir / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            found[f"{folder}/{path.name}"] = sha256(path.read_bytes()).hexdigest()
    return found


def review_corrected_second_review_only(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    providers: WorkerProviders,
    production_run: Path,
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    display_ids: Sequence[str],
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
    context_units: Sequence[ReviewerContextUnit] | None = None,
    diagnostics: CanaryDiagnostics | None = None,
) -> dict[str, Any]:
    """One corrected second review from the v3 repaired slice.  Never translates or repairs."""
    providers = WorkerProviders(
        mt=ForbiddenTranslationProvider(),
        structured=ForbiddenTranslationProvider(),
        reviewer=providers.reviewer,
        repair=ForbiddenRepairProvider(),
    )
    assert_not_production_checkpoint(
        output_dir, run=production_run, batch_label=PRODUCTION_BATCH, checkpoint=PRODUCTION_CHECKPOINT
    )
    before = canary_immutable_fingerprints(output_dir)
    prior = _prior_review_hashes(output_dir)
    v3_hashes = _stage_hashes(
        output_dir,
        ("reviews_v3", "re_reviews_v3", "repair_contracts_v3", "repair_candidates_v3", "review_groups"),
    )
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    units = list(context_units) if context_units is not None else context_units_for_order(frozen_order)
    existing_raw = load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    texts = candidate_text_map(requests, existing_raw)
    counts = segment_count_map(requests)
    wanted = {str(item) for item in display_ids}
    started = datetime.now(timezone.utc)
    outcomes: list[dict[str, Any]] = []
    reviewer_calls = 0
    for request in requests:
        if request.display_id not in wanted:
            continue
        stored = load_unit_result(unit_path(output_dir, request.display_id))
        raw = existing_raw.get(request.unit_uid)
        reasons = verify_raw_unit(
            request,
            raw,
            stored,
            source_sha256=source_sha256,
            selection_sha256=selection_sha256,
            translation_bible=translation_bible,
            original_line_sha256=None,
        )
        first = load_stage(output_dir, "reviews_v3", request.display_id)
        repair_payload = load_stage(output_dir, "repair_candidates_v3", request.display_id)
        invalid_second = load_stage(output_dir, "re_reviews_v3", request.display_id)
        if reasons or raw is None or first is None or repair_payload is None or invalid_second is None:
            outcomes.append(
                {
                    "display_id": request.display_id,
                    "final_status": "HUMAN_REQUIRED",
                    "reason": "CORRECTED_SECOND_REVIEW_PREREQUISITE_MISSING",
                }
            )
            continue
        repaired = TranslationResult.model_validate(repair_payload["working"])
        qa_request = overlay_verified_d050_bindings(request, registry_map)
        repaired_qa = qa_fn(views[request.display_id], qa_request, repaired, known_unit_ids)
        if not repaired_qa.get("passed"):
            outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "REPAIRED_QA_FAILED"})
            continue
        group = build_repaired_second_review_group(
            request,
            units,
            registry_map,
            candidate_texts=texts,
            segment_counts=counts,
            repaired=repaired,
        )
        assert_repaired_second_review_payload(
            group,
            request=request,
            repaired=repaired,
            previous_display_id=group.context.previous_semantic_display_id,
        )
        if load_stage(output_dir, "re_reviews_v3_supersession", request.display_id) is None:
            persist_stage_once(
                output_dir,
                "re_reviews_v3_supersession",
                request.display_id,
                {
                    "artifact_type": "CANARY_RE_REVIEW_SUPERSESSION",
                    "schema_version": 1,
                    "reason": "invalid-input-contract",
                    "status": "superseded",
                    "superseded_stage": "re_reviews_v3",
                    "replacement_stage": "re_reviews_v4",
                    "display_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "superseded_verdict": invalid_second.get("verdict"),
                    "superseded_sha256": sha256(stage_path(output_dir, "re_reviews_v3", request.display_id).read_bytes()).hexdigest(),
                    "detail": "full_candidate_span used original RAW instead of the repaired current slice",
                },
            )
        stored_v4 = load_stage(output_dir, "re_reviews_v4", request.display_id)
        provider_request = _binding(request, translation_bible)
        if stored_v4 is None:
            if diagnostics is not None:
                diagnostics.emit("CORRECTED_SECOND_REVIEW_START", id=request.display_id, group_hash=group.group_hash)
            review, usage, latency = _call_reviewer(
                providers,
                provider_request,
                repaired,
                repaired_qa,
                semantic_context=group.as_reviewer_context_payload(),
            )
            reviewer_calls += 1
            persist_stage_once(
                output_dir,
                "re_reviews_v4",
                request.display_id,
                {
                    "artifact_type": "CANARY_RE_REVIEW_RESULT_V4",
                    "schema_version": 1,
                    "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "member_work_order_ids": list(group.member_work_order_ids),
                    "repairable_work_order_ids": list(group.repairable_work_order_ids),
                    "display_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "verdict": review.verdict,
                    "issues": [issue.model_dump() for issue in review.issues],
                    "usage": usage or NOT_PERSISTED,
                    "latency_seconds": latency if latency is not None else NOT_PERSISTED,
                    "context_hash": group.context.context_hash,
                    "group_hash": group.group_hash,
                    "full_candidate_span": group.full_candidate_span,
                    "repaired_current_text": repaired_candidate_text(repaired),
                    "input_contract": "repaired-group-span",
                },
            )
            verdict = review.verdict
        else:
            verdict = str(stored_v4.get("verdict"))
            review = _review_from_payload(stored_v4, request.unit_uid)
        final_status = "ACCEPT" if verdict == "ACCEPT" else "HUMAN_REQUIRED"
        outcome = UnitOutcome(request=request, decision=route_semantic_unit(request))
        outcome.raw = raw
        outcome.working = repaired if verdict == "ACCEPT" else raw
        outcome.qa = repaired_qa
        outcome.review = _review_from_payload(first, request.unit_uid)
        outcome.second_review = review
        outcome.repair_applied = verdict == "ACCEPT"
        outcome.final_status = final_status
        outcome.human_required = final_status != "ACCEPT"
        _write_terminal(output_dir, outcome)
        outcomes.append(
            {
                "display_id": request.display_id,
                "final_status": final_status,
                "second_review": verdict,
                "repair_applied": verdict == "ACCEPT",
                "human_required": final_status != "ACCEPT",
                "member_work_order_ids": list(group.member_work_order_ids),
                "group_hash": group.group_hash,
            }
        )
    if canary_immutable_fingerprints(output_dir) != before:
        raise RuntimeError("CANARY_IMMUTABLE_EVIDENCE_MUTATED")
    if _prior_review_hashes(output_dir) != prior:
        raise RuntimeError("CANARY_PRIOR_REVIEW_MUTATED")
    if _stage_hashes(output_dir, ("reviews_v3", "re_reviews_v3", "repair_contracts_v3", "repair_candidates_v3", "review_groups")) != v3_hashes:
        raise RuntimeError("CANARY_V3_REVIEW_MUTATED")
    return {
        "artifact_type": "CANARY_CORRECTED_SECOND_REVIEW_RUN",
        "schema_version": 1,
        "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
        "display_ids": list(display_ids),
        "reviewer_api_calls": reviewer_calls,
        "repair_api_calls": 0,
        "translation_api_calls": 0,
        "started_at": started.isoformat(),
        "outcomes": outcomes,
    }


def _group_review_from_payload(payload: Mapping[str, Any]) -> GroupReviewResult | None:
    verdict = payload.get("verdict")
    if verdict not in {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}:
        return None
    from booktrans.providers.qwen_max import GroupReviewIssue

    issues = []
    for item in payload.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(
            GroupReviewIssue(
                code=str(item.get("code") or "UNKNOWN"),
                logical_work_order_id=str(item.get("logical_work_order_id") or ""),
                unit_uid=str(item.get("unit_uid") or ""),
                affected_segment_indices=list(item.get("affected_segment_indices") or []),
                reason=str(item.get("reason") or ""),
                repair_instruction=item.get("repair_instruction"),
            )
        )
    return GroupReviewResult(
        schema_version="booktrans.structured-group-review.v1",
        review_group_id=str(payload.get("review_group_id") or payload.get("semantic_review_group_id") or ""),
        verdict=verdict,
        issues=issues,
    )


def review_canary_semantic_group_repair(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    providers: WorkerProviders,
    production_run: Path,
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    display_ids: Sequence[str],
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
    context_units: Sequence[ReviewerContextUnit] | None = None,
    diagnostics: CanaryDiagnostics | None = None,
) -> dict[str, Any]:
    """One group review, optional one group repair, QA, one group second review.

    Never translates.  Never overwrites v1–v4 review/repair files or RAW.
    """
    providers = WorkerProviders(
        mt=ForbiddenTranslationProvider(),
        structured=ForbiddenTranslationProvider(),
        reviewer=providers.reviewer,
        repair=providers.repair,
    )
    assert_not_production_checkpoint(
        output_dir, run=production_run, batch_label=PRODUCTION_BATCH, checkpoint=PRODUCTION_CHECKPOINT
    )
    before = canary_immutable_fingerprints(output_dir)
    prior = _prior_review_hashes(output_dir)
    v3_hashes = _stage_hashes(
        output_dir,
        ("reviews_v3", "re_reviews_v3", "repair_contracts_v3", "repair_candidates_v3", "review_groups", "re_reviews_v4"),
    )
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    units = list(context_units) if context_units is not None else context_units_for_order(frozen_order)
    existing_raw = load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    from booktrans.production_worker.continuation import _raw_line_sha_by_uid

    raw_line_shas = _raw_line_sha_by_uid(output_dir / "translation_raw_response.jsonl")
    raw_file_before = sha256((output_dir / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    request_by_id = {item.display_id: item for item in requests}
    texts = candidate_text_map(requests, existing_raw)
    counts = segment_count_map(requests)
    unsealed = frozenset(str(item) for item in frozen_order)
    sealed = production_sealed_work_order_ids(production_run)
    started = datetime.now(timezone.utc)
    outcomes: list[dict[str, Any]] = []
    reviewer_calls = 0
    repair_calls = 0
    current_ids = [str(item) for item in display_ids]
    for current_id in current_ids:
        request = request_by_id.get(current_id)
        if request is None:
            outcomes.append({"display_id": current_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_REPAIR_CURRENT_MISSING"})
            continue
        stored = load_unit_result(unit_path(output_dir, request.display_id))
        raw = existing_raw.get(request.unit_uid)
        reasons = verify_raw_unit(
            request,
            raw,
            stored,
            source_sha256=source_sha256,
            selection_sha256=selection_sha256,
            translation_bible=translation_bible,
            original_line_sha256=raw_line_shas.get(request.unit_uid),
        )
        if reasons or raw is None:
            outcomes.append({"display_id": current_id, "final_status": "HUMAN_REQUIRED", "reason": ",".join(reasons) or "RAW_MISSING"})
            continue
        group = build_unit_group(
            request,
            units,
            registry_map,
            candidate_texts=texts,
            segment_counts=counts,
            unsealed_work_order_ids=unsealed,
            sealed_work_order_ids=sealed,
        )
        member_requests: list[CalibrationRequest] = []
        member_pairs: list[tuple[Any, TranslationResult]] = []
        stored_shas: dict[str, str] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        for member in group.members:
            member_request = request_by_id.get(member.display_id)
            member_raw = existing_raw.get(member.unit_uid) if member_request else None
            if member_request is None or member_raw is None:
                continue
            member_stored = load_unit_result(unit_path(output_dir, member.display_id)) or {}
            stored_shas[member.display_id] = str(member_stored.get("raw_sha256") or "")
            stored_shas[member.unit_uid] = stored_shas[member.display_id]
            member_requests.append(member_request)
            member_pairs.append((_binding(member_request, translation_bible), member_raw))
            snapshots[member.display_id] = member_raw.model_dump()
        eligibility = decide_group_repair_eligibility(
            group,
            frozen_selection_ids=set(unsealed),
            unsealed_checkpoint_ids=set(unsealed),
            production_sealed_ids=set(sealed),
            requests=request_by_id,
            raws=existing_raw,
            stored_raw_shas=stored_shas,
            raw_line_shas=raw_line_shas,
        )
        if load_stage(output_dir, "group_repair_eligibility", request.display_id) is None:
            persist_stage_once(
                output_dir,
                "group_repair_eligibility",
                request.display_id,
                {
                    "artifact_type": "CANARY_GROUP_REPAIR_ELIGIBILITY",
                    "schema_version": 1,
                    "display_id": request.display_id,
                    "allowed": eligibility.allowed,
                    "reason": eligibility.reason,
                    "member_work_order_ids": list(group.member_work_order_ids),
                    "repairable_work_order_ids": list(eligibility.repairable_work_order_ids),
                    "group_hash": group.group_hash,
                },
            )
        if not eligibility.allowed:
            outcomes.append(
                {
                    "display_id": request.display_id,
                    "final_status": "HUMAN_REQUIRED",
                    "reason": eligibility.reason,
                    "member_work_order_ids": list(group.member_work_order_ids),
                }
            )
            continue
        qa_pass = True
        for member_request in member_requests:
            member_raw = existing_raw[member_request.unit_uid]
            qa_request = overlay_verified_d050_bindings(member_request, registry_map)
            record = qa_fn(views[member_request.display_id], qa_request, member_raw, known_unit_ids)
            if not record.get("passed"):
                qa_pass = False
        if not qa_pass:
            outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_PRE_REPAIR_QA_FAILED"})
            continue
        stored_review = load_stage(output_dir, "group_reviews", request.display_id)
        if stored_review is None:
            review_fn = getattr(providers.reviewer, "review_group_with_provenance", None)
            if not callable(review_fn):
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_REVIEWER_MISSING"})
                continue
            if diagnostics is not None:
                diagnostics.emit("GROUP_REVIEW_START", id=request.display_id, group_id=group.semantic_review_group_id)
            try:
                result = review_fn(
                    member_pairs,
                    group_payload=group.as_payload(),
                    deterministic_qa_summary={"passed": True, "codes": []},
                )
            except _PROVIDER_ERRORS as error:
                classified = classify_provider_exception(error)
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": classified.code})
                continue
            reviewer_calls += 1
            review = result.review
            persist_stage_once(
                output_dir,
                "group_reviews",
                request.display_id,
                {
                    "artifact_type": "CANARY_GROUP_REVIEW_RESULT",
                    "schema_version": 1,
                    "review_contract_version": REVIEW_GROUP_CONTRACT_VERSION,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "review_group_id": review.review_group_id,
                    "member_work_order_ids": list(group.member_work_order_ids),
                    "repairable_work_order_ids": list(group.repairable_work_order_ids),
                    "verdict": review.verdict,
                    "issues": [issue.model_dump() for issue in review.issues],
                    "usage": dict(result.candidate.usage) if result.candidate.usage else NOT_PERSISTED,
                    "group_hash": group.group_hash,
                    "full_source_span": group.full_source_span,
                    "full_candidate_span": group.full_candidate_span,
                },
            )
        else:
            review = _group_review_from_payload(stored_review)
            if review is None:
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_REVIEW_PAYLOAD_INVALID"})
                continue
        if review.verdict != "REPAIR":
            outcomes.append(
                {
                    "display_id": request.display_id,
                    "final_status": "ACCEPT" if review.verdict == "ACCEPT" else "HUMAN_REQUIRED",
                    "group_review": review.verdict,
                    "member_work_order_ids": list(group.member_work_order_ids),
                }
            )
            continue
        stored_contract = load_stage(output_dir, "group_repair_contracts", request.display_id)
        if stored_contract is None:
            gate = decide_semantic_group_repair_contract(
                group,
                review,
                members=member_pairs,
                raw_shas={
                    **raw_line_shas,
                    **{item.display_id: raw_line_shas.get(item.unit_uid, "") for item in member_requests},
                },
            )
            if gate.action != "REPAIR" or gate.contract is None:
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": gate.reason})
                continue
            persist_stage_once(
                output_dir,
                "group_repair_contracts",
                request.display_id,
                {
                    "artifact_type": "CANARY_GROUP_REPAIR_CONTRACT",
                    "schema_version": 1,
                    **gate.contract.model_dump(),
                },
            )
            contract = gate.contract
        else:
            contract = SemanticGroupRepairContract.model_validate(
                {key: stored_contract[key] for key in SemanticGroupRepairContract.model_fields if key in stored_contract}
            )
        repaired_map: dict[str, TranslationResult] = {}
        need_repair = any(
            load_stage(output_dir, "group_repair_candidates", item.logical_work_order_id) is None for item in contract.allowed_repairs
        )
        if need_repair:
            repair_fn = getattr(providers.repair, "repair_group_with_provenance", None) or getattr(providers.repair, "repair_group", None)
            if not callable(repair_fn):
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_REPAIR_PROVIDER_MISSING"})
                continue
            if diagnostics is not None:
                diagnostics.emit("GROUP_REPAIR_START", id=request.display_id, group_id=group.semantic_review_group_id)
            try:
                repaired_result = repair_fn(member_pairs, contract)
            except _PROVIDER_ERRORS as error:
                classified = classify_provider_exception(error)
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": classified.code})
                continue
            repair_calls += 1
            repaired_map = repaired_result.repaired if hasattr(repaired_result, "repaired") else repaired_result
            for display_id, repaired in repaired_map.items():
                if load_stage(output_dir, "group_repair_candidates", display_id) is not None:
                    continue
                snapshot = snapshots.get(display_id)
                if snapshot is not None and repaired.model_dump() == snapshot:
                    continue
                persist_stage_once(
                    output_dir,
                    "group_repair_candidates",
                    display_id,
                    {
                        "artifact_type": "CANARY_GROUP_REPAIR_CANDIDATE",
                        "schema_version": 1,
                        "current_display_id": request.display_id,
                        "semantic_review_group_id": group.semantic_review_group_id,
                        "working": repaired.model_dump(),
                    },
                )
        else:
            for member in group.members:
                payload = load_stage(output_dir, "group_repair_candidates", member.display_id)
                if payload and payload.get("working"):
                    repaired_map[member.display_id] = TranslationResult.model_validate(payload["working"])
        raw_mutated = False
        for display_id, snapshot in snapshots.items():
            member_request = request_by_id[display_id]
            live = existing_raw[member_request.unit_uid]
            if live.model_dump() != snapshot:
                raw_mutated = True
        if raw_mutated:
            outcomes.append({"display_id": request.display_id, "final_status": "BLOCKED", "reason": "RAW_MUTATED"})
            continue
        qa_failed = False
        working_texts = dict(texts)
        for display_id, repaired in repaired_map.items():
            member_request = request_by_id[display_id]
            qa_request = overlay_verified_d050_bindings(member_request, registry_map)
            record = qa_fn(views[display_id], qa_request, repaired, known_unit_ids)
            if not record.get("passed"):
                qa_failed = True
            working_texts[display_id] = "".join(segment.text for segment in repaired.target_segments)
            working_texts[member_request.unit_uid] = working_texts[display_id]
        if qa_failed:
            outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_REPAIR_QA_FAILED", "repair_applied": True})
            continue
        second_group = build_unit_group(
            request,
            units,
            registry_map,
            candidate_texts=working_texts,
            segment_counts=counts,
            unsealed_work_order_ids=unsealed,
            sealed_work_order_ids=sealed,
        )
        second_pairs = []
        for member_request, member_raw in ((request_by_id[item.display_id], repaired_map.get(item.display_id) or existing_raw[item.unit_uid]) for item in group.members):
            second_pairs.append((_binding(member_request, translation_bible), member_raw))
        stored_second = load_stage(output_dir, "group_re_reviews", request.display_id)
        if stored_second is None:
            review_fn = getattr(providers.reviewer, "review_group_with_provenance", None)
            if not callable(review_fn):
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_REVIEWER_MISSING"})
                continue
            try:
                second_result = review_fn(
                    second_pairs,
                    group_payload=second_group.as_payload(),
                    deterministic_qa_summary={"passed": True, "codes": []},
                )
            except _PROVIDER_ERRORS as error:
                classified = classify_provider_exception(error)
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": classified.code, "repair_applied": True})
                continue
            reviewer_calls += 1
            second = second_result.review
            persist_stage_once(
                output_dir,
                "group_re_reviews",
                request.display_id,
                {
                    "artifact_type": "CANARY_GROUP_RE_REVIEW_RESULT",
                    "schema_version": 1,
                    "semantic_review_group_id": second_group.semantic_review_group_id,
                    "review_group_id": second.review_group_id,
                    "member_work_order_ids": list(second_group.member_work_order_ids),
                    "verdict": second.verdict,
                    "issues": [issue.model_dump() for issue in second.issues],
                    "usage": dict(second_result.candidate.usage) if second_result.candidate.usage else NOT_PERSISTED,
                    "group_hash": second_group.group_hash,
                    "full_candidate_span": second_group.full_candidate_span,
                    "input_contract": "group-repaired-span",
                },
            )
        else:
            second = _group_review_from_payload(stored_second)
            if second is None:
                outcomes.append({"display_id": request.display_id, "final_status": "HUMAN_REQUIRED", "reason": "GROUP_SECOND_REVIEW_INVALID", "repair_applied": True})
                continue
        final_status = "ACCEPT" if second.verdict == "ACCEPT" else "HUMAN_REQUIRED"
        outcomes.append(
            {
                "display_id": request.display_id,
                "final_status": final_status,
                "group_review": review.verdict,
                "group_second_review": second.verdict,
                "repair_applied": True,
                "member_work_order_ids": list(group.member_work_order_ids),
                "human_required": final_status != "ACCEPT",
            }
        )
    after = canary_immutable_fingerprints(output_dir)
    if after != before:
        raise RuntimeError("CANARY_IMMUTABLE_EVIDENCE_MUTATED")
    if _prior_review_hashes(output_dir) != prior:
        raise RuntimeError("CANARY_PRIOR_REVIEW_MUTATED")
    if _stage_hashes(output_dir, ("reviews_v3", "re_reviews_v3", "repair_contracts_v3", "repair_candidates_v3", "review_groups", "re_reviews_v4")) != v3_hashes:
        raise RuntimeError("CANARY_V3_REVIEW_MUTATED")
    raw_file_after = sha256((output_dir / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_file_after != raw_file_before:
        raise RuntimeError("CANARY_RAW_MUTATED")
    return {
        "artifact_type": "CANARY_GROUP_REPAIR_RUN",
        "schema_version": 1,
        "display_ids": current_ids,
        "reviewer_api_calls": reviewer_calls,
        "repair_api_calls": repair_calls,
        "translation_api_calls": 0,
        "started_at": started.isoformat(),
        "outcomes": outcomes,
    }
