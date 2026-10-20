"""Closed-world human semantic adjudication.  Never mutates RAW or calls providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import TranslationView
from booktrans.production_worker.artifacts import write_json_once, write_json_replace
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.canary import PRODUCTION_BATCH, PRODUCTION_CHECKPOINT
from booktrans.production_worker.continuation import (
    canary_immutable_fingerprints,
    overlay_verified_d050_bindings,
)
from booktrans.production_worker.qa import QaFn
from booktrans.production_worker.selection import (
    assert_not_production_checkpoint,
    load_checkpoint_d050_registry,
)
from booktrans.production_worker.semantic_review_group import SemanticReviewGroup
from booktrans.production_worker.unit_store import load_raw_by_uid, load_stage, persist_stage_once
from booktrans.providers.qwen_max import (
    SemanticGroupAllowedRepair,
    SemanticGroupRepairContract,
    StructuredGroupRepairPatch,
    StructuredGroupRepairResult,
    apply_group_repair,
    StructuredTranslationTextArray,
    text_array_candidate_to_raw,
)
from booktrans.providers.translation_provider import ProviderContractGap, TranslationProviderRequest


HUMAN_ADJUDICATION_SCHEMA_VERSION = "booktrans.human-semantic-adjudication.v1"
HUMAN_POLICY_ID = "HUMAN_SEMANTIC_ADJUDICATION"
SEMANTIC_TERMINAL_MODEL_ACCEPTED = "MODEL_ACCEPTED"
SEMANTIC_TERMINAL_HUMAN_APPROVED = "HUMAN_APPROVED"
SEMANTIC_TERMINAL_HUMAN_REJECTED = "HUMAN_REJECTED"
SEMANTIC_TERMINAL_HUMAN_REQUIRED = "HUMAN_REQUIRED"
SEMANTIC_PASS_TERMINALS = frozenset({SEMANTIC_TERMINAL_MODEL_ACCEPTED, SEMANTIC_TERMINAL_HUMAN_APPROVED})
SEMANTIC_ESCALATION_TERMINALS = frozenset(
    {SEMANTIC_TERMINAL_HUMAN_APPROVED, SEMANTIC_TERMINAL_HUMAN_REJECTED, SEMANTIC_TERMINAL_HUMAN_REQUIRED}
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HumanAllowedRepair(_StrictModel):
    logical_work_order_id: str
    unit_uid: str
    allowed_segment_indices: list[int] = Field(min_length=1)
    source_fingerprint: str


class HumanApprovedPatch(_StrictModel):
    logical_work_order_id: str
    unit_uid: str
    index: int
    target_text: str


class HumanSemanticAdjudication(_StrictModel):
    schema_version: Literal[HUMAN_ADJUDICATION_SCHEMA_VERSION] = HUMAN_ADJUDICATION_SCHEMA_VERSION
    policy_id: str = HUMAN_POLICY_ID
    semantic_review_group_id: str
    member_work_order_ids: list[str] = Field(min_length=1)
    member_unit_uids: list[str] = Field(min_length=1)
    group_hash: str
    source_span_hash: str
    source_hashes: dict[str, str]
    original_candidate_hashes: dict[str, str]
    # Empty for legacy evidence.  A supplied value is the current-head CAS
    # contract; historical raw/request hashes above remain audit only.
    expected_current_heads: dict[str, str] = Field(default_factory=dict)
    allowed_repairs: list[HumanAllowedRepair] = Field(min_length=1)
    decision: Literal["HUMAN_APPROVED", "HUMAN_REJECTED"]
    approved_patches: list[HumanApprovedPatch] = Field(default_factory=list)
    approved_group_text: str | None = None
    reason: str
    decided_at: str


class HumanAdjudicationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def apply_human_initial_candidate_to_production_checkpoint(
    *, checkpoint_dir: Path, request: CalibrationRequest, provider_request: TranslationProviderRequest,
    target_texts: Sequence[str], view: TranslationView, known_unit_ids: set[str], qa_fn: QaFn,
) -> dict[str, Any]:
    """Create the one permitted human root candidate after acquisition exhaustion.

    This is intentionally narrower than semantic adjudication: it is available
    only when no provider RAW or current head exists, and it writes a bound
    human candidate rather than fabricating provider RAW.
    """
    raw_file = checkpoint_dir / "translation_raw_response.jsonl"
    if request.unit_uid in load_raw_by_uid(raw_file):
        raise HumanAdjudicationError("HUMAN_INITIAL_CANDIDATE_RAW_ALREADY_EXISTS")
    if load_stage(checkpoint_dir, "human_approved_candidates", request.display_id) is not None:
        raise HumanAdjudicationError("HUMAN_INITIAL_CANDIDATE_ALREADY_EXISTS")
    terminal = load_stage(checkpoint_dir, "candidate_acquisition_terminals", request.display_id)
    failures_dir = checkpoint_dir / "provider_contract_failures"
    failures = sorted(failures_dir.glob(f"{request.display_id}.attempt_*.json")) if failures_dir.is_dir() else []
    if not (
        isinstance(terminal, Mapping)
        and terminal.get("terminal_class") == "NO_SAFE_AUTHORITATIVE_CANDIDATE"
        and terminal.get("root_cause_class") == "PROVIDER_ATTEMPTS_EXHAUSTED"
        and terminal.get("unit_uid") == request.unit_uid
        and terminal.get("source_sha256") == request.source_sha256
        and terminal.get("request_binding_sha256") == provider_request.request_binding_sha256
        and len(failures) >= 2
    ):
        raise HumanAdjudicationError("HUMAN_INITIAL_CANDIDATE_ACQUISITION_NOT_EXHAUSTED")
    try:
        candidate = text_array_candidate_to_raw(
            provider_request, StructuredTranslationTextArray(target_texts=list(target_texts)),
        )
    except ProviderContractGap as exc:
        raise HumanAdjudicationError(exc.code) from exc
    qa = qa_fn(view, request, candidate, known_unit_ids)
    if not qa.get("passed"):
        raise HumanAdjudicationError("HUMAN_INITIAL_CANDIDATE_QA_FAILED")
    persist_stage_once(
        checkpoint_dir, "human_approved_candidates", request.display_id,
        {
            "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE",
            "schema_version": 3,
            "origin": "HUMAN_INITIAL_CANDIDATE",
            "current_display_id": request.display_id,
            "decision": "HUMAN_APPROVED",
            "semantic_terminal": "HUMAN_APPROVED",
            "batch_label": checkpoint_dir.parent.name.upper(),
            "checkpoint": checkpoint_dir.name,
            "logical_work_order_id": request.display_id,
            "unit_uid": request.unit_uid,
            "source_sha256": request.source_sha256,
            "request_binding_sha256": provider_request.request_binding_sha256,
            "source_segments": [segment.model_dump() for segment in request.source_segments],
            "target_segments": [segment.model_dump() for segment in candidate.target_segments],
            "candidate_binding_sha256": candidate_sha(candidate),
            "parent_candidate_sha256": None,
            "working": candidate.model_dump(),
            "qa": qa,
            "provider_failure_evidence": [str(path) for path in failures],
            "candidate_acquisition_terminal": str(checkpoint_dir / "candidate_acquisition_terminals" / f"{request.display_id}.json"),
        },
    )
    return {"candidate": candidate, "qa": qa}


def candidate_sha(result: TranslationResult) -> str:
    return sha256(result.model_dump_json().encode("utf-8")).hexdigest()


def source_span_sha(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def allowed_from_group_contract(contract: SemanticGroupRepairContract) -> list[HumanAllowedRepair]:
    return [
        HumanAllowedRepair(
            logical_work_order_id=item.logical_work_order_id,
            unit_uid=item.unit_uid,
            allowed_segment_indices=list(item.allowed_segment_indices),
            source_fingerprint=item.source_fingerprint,
        )
        for item in contract.allowed_repairs
    ]


def build_human_adjudication(
    group: SemanticReviewGroup,
    *,
    decision: Literal["HUMAN_APPROVED", "HUMAN_REJECTED"],
    reason: str,
    allowed_repairs: Sequence[HumanAllowedRepair],
    source_hashes: Mapping[str, str],
    original_candidate_hashes: Mapping[str, str],
    expected_current_heads: Mapping[str, str] | None = None,
    approved_patches: Sequence[HumanApprovedPatch] = (),
    approved_group_text: str | None = None,
    decided_at: str | None = None,
) -> HumanSemanticAdjudication:
    return HumanSemanticAdjudication(
        semantic_review_group_id=group.semantic_review_group_id,
        member_work_order_ids=list(group.member_work_order_ids),
        member_unit_uids=[member.unit_uid for member in group.members],
        group_hash=group.group_hash,
        source_span_hash=source_span_sha(group.full_source_span),
        source_hashes=dict(source_hashes),
        original_candidate_hashes=dict(original_candidate_hashes),
        expected_current_heads=dict(expected_current_heads or {}),
        allowed_repairs=list(allowed_repairs),
        decision=decision,
        approved_patches=list(approved_patches),
        approved_group_text=approved_group_text,
        reason=reason,
        decided_at=decided_at or datetime.now(timezone.utc).isoformat(),
    )


def _as_group_contract(adjudication: HumanSemanticAdjudication) -> SemanticGroupRepairContract:
    return SemanticGroupRepairContract(
        policy_id=HUMAN_POLICY_ID,
        review_group_id=adjudication.semantic_review_group_id,
        member_work_order_ids=list(adjudication.member_work_order_ids),
        member_unit_uids=list(adjudication.member_unit_uids),
        group_hash=adjudication.group_hash,
        source_span_hash=adjudication.source_span_hash,
        allowed_repairs=[
            SemanticGroupAllowedRepair(
                logical_work_order_id=item.logical_work_order_id,
                unit_uid=item.unit_uid,
                allowed_segment_indices=list(item.allowed_segment_indices),
                source_fingerprint=item.source_fingerprint,
            )
            for item in adjudication.allowed_repairs
        ],
        immutable_raw_shas=dict(adjudication.original_candidate_hashes),
        repair_instruction=adjudication.reason,
    )


def validate_human_adjudication(
    adjudication: HumanSemanticAdjudication,
    group: SemanticReviewGroup,
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    *,
    raws: Mapping[str, TranslationResult],
    current_rebuilt: bool = False,
) -> None:
    # Historical group/source/provider bindings are immutable audit evidence.
    # Production apply can opt into a freshly rebuilt current group; legacy
    # callers retain the original strict validation behavior.
    if not current_rebuilt:
        if adjudication.semantic_review_group_id != group.semantic_review_group_id:
            raise HumanAdjudicationError("HUMAN_ADJUDICATION_GROUP_ID_MISMATCH")
        if adjudication.group_hash != group.group_hash:
            raise HumanAdjudicationError("HUMAN_ADJUDICATION_GROUP_HASH_MISMATCH")
    if list(adjudication.member_work_order_ids) != list(group.member_work_order_ids):
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_MEMBER_MISMATCH")
    if list(adjudication.member_unit_uids) != [member.unit_uid for member in group.members]:
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_UNIT_UID_MISMATCH")
    if not current_rebuilt and adjudication.source_span_hash != source_span_sha(group.full_source_span):
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_SOURCE_HASH_MISMATCH")
    by_id = {item[0].request.display_id: item for item in members}
    if adjudication.decision == "HUMAN_REJECTED":
        if adjudication.approved_patches:
            raise HumanAdjudicationError("HUMAN_ADJUDICATION_REJECTED_WITH_PATCHES")
        return
    if adjudication.decision != "HUMAN_APPROVED":
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_DECISION_INVALID")
    if not adjudication.approved_patches:
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_APPROVED_WITHOUT_PATCHES")
    try:
        apply_human_patches(members, adjudication)
    except ProviderContractGap as exc:
        raise HumanAdjudicationError(exc.code) from exc


def apply_human_patches(
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    adjudication: HumanSemanticAdjudication,
) -> dict[str, TranslationResult]:
    """Return in-memory approved patches.  Never mutates RAW inputs."""
    snapshots = {item[0].request.display_id: item[1].model_dump() for item in members}
    contract = _as_group_contract(adjudication)
    result = StructuredGroupRepairResult(
        schema_version="booktrans.structured-group-repair.v1",
        review_group_id=adjudication.semantic_review_group_id,
        patches=[
            StructuredGroupRepairPatch(
                logical_work_order_id=item.logical_work_order_id,
                unit_uid=item.unit_uid,
                index=item.index,
                target_text=item.target_text,
            )
            for item in adjudication.approved_patches
        ],
    )
    repaired = apply_group_repair(members, contract, result)
    for provider_request, current in members:
        if current.model_dump() != snapshots[provider_request.request.display_id]:
            raise HumanAdjudicationError("HUMAN_ADJUDICATION_RAW_MUTATED")
    return repaired


def semantic_terminal_of(
    *,
    final_status: str | None = None,
    human_required: bool = False,
    human_decision: str | None = None,
    stored_terminal: str | None = None,
) -> str:
    if stored_terminal in SEMANTIC_PASS_TERMINALS | {SEMANTIC_TERMINAL_HUMAN_REJECTED, SEMANTIC_TERMINAL_HUMAN_REQUIRED}:
        return stored_terminal
    if human_decision == "HUMAN_APPROVED" or final_status == "HUMAN_APPROVED":
        return SEMANTIC_TERMINAL_HUMAN_APPROVED
    if human_decision == "HUMAN_REJECTED" or final_status == "HUMAN_REJECTED":
        return SEMANTIC_TERMINAL_HUMAN_REJECTED
    if final_status in {"ACCEPT", "MODEL_ACCEPTED"}:
        return SEMANTIC_TERMINAL_MODEL_ACCEPTED
    if human_required or final_status == "HUMAN_REQUIRED":
        return SEMANTIC_TERMINAL_HUMAN_REQUIRED
    return "UNRESOLVED"


def auto_repair_exhausted(group_second_verdict: str | None, *, repair_applied: bool) -> bool:
    return bool(repair_applied and group_second_verdict in {"REPAIR", "HUMAN_REQUIRED"})


@dataclass(frozen=True)
class HumanAdjudicationApplyResult:
    adjudication: HumanSemanticAdjudication
    repaired: dict[str, TranslationResult]
    qa_records: dict[str, dict[str, Any]]
    raw_unchanged: bool


def apply_human_adjudication_to_canary(
    *,
    output_dir: Path,
    production_run: Path,
    requests: Sequence[CalibrationRequest],
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    frozen_order: Sequence[str],
    selection_sha256: str,
    source_sha256: str,
    adjudication: HumanSemanticAdjudication,
    group: SemanticReviewGroup,
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
) -> dict[str, Any]:
    """Persist one human adjudication and approved candidates.  Never translates or reviews."""
    assert_not_production_checkpoint(
        output_dir, run=production_run, batch_label=PRODUCTION_BATCH, checkpoint=PRODUCTION_CHECKPOINT
    )
    before = canary_immutable_fingerprints(output_dir)
    raw_file = output_dir / "translation_raw_response.jsonl"
    raw_before = sha256(raw_file.read_bytes()).hexdigest() if raw_file.is_file() else ""
    existing_raw = load_raw_by_uid(raw_file)
    request_by_id = {item.display_id: item for item in requests}
    validate_human_adjudication(adjudication, group, members, raws=existing_raw)
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    repaired: dict[str, TranslationResult] = {}
    qa_records: dict[str, dict[str, Any]] = {}
    if adjudication.decision == "HUMAN_APPROVED":
        repaired = apply_human_patches(members, adjudication)
        for display_id, working in repaired.items():
            request = request_by_id[display_id]
            qa_request = overlay_verified_d050_bindings(request, registry_map)
            record = qa_fn(views[display_id], qa_request, working, known_unit_ids)
            qa_records[display_id] = record
            if not record.get("passed"):
                raise HumanAdjudicationError("HUMAN_ADJUDICATION_QA_FAILED")
    current_id = group.current_display_id
    member_by_display_id = {provider_request.request.display_id: (provider_request, current) for provider_request, current in members}
    if load_stage(output_dir, "human_adjudications", current_id) is None:
        persist_stage_once(
            output_dir,
            "human_adjudications",
            current_id,
            {
                "artifact_type": "CANARY_HUMAN_SEMANTIC_ADJUDICATION",
                "schema_version": 1,
                **adjudication.model_dump(),
            },
        )
    snapshots = {item[0].request.display_id: item[1].model_dump() for item in members}
    for display_id, working in repaired.items():
        raw = existing_raw[request_by_id[display_id].unit_uid]
        if working.model_dump() == raw.model_dump():
            continue
        if load_stage(output_dir, "human_approved_candidates", display_id) is None:
            persist_stage_once(
                output_dir,
                "human_approved_candidates",
                display_id,
                {
                    "artifact_type": "CANARY_HUMAN_APPROVED_CANDIDATE",
                    "schema_version": 1,
                    "current_display_id": current_id,
                    "semantic_review_group_id": group.semantic_review_group_id,
                    "decision": adjudication.decision,
                    "working": working.model_dump(),
                    "qa": qa_records.get(display_id),
                },
            )
    if sha256(raw_file.read_bytes()).hexdigest() != raw_before:
        raise RuntimeError("CANARY_RAW_MUTATED")
    if canary_immutable_fingerprints(output_dir) != before:
        raise RuntimeError("CANARY_IMMUTABLE_EVIDENCE_MUTATED")
    write_json_replace(
        output_dir / "canary_human_adjudication_report.json",
        {
            "artifact_type": "CANARY_HUMAN_ADJUDICATION_RUN",
            "schema_version": 1,
            "display_id": current_id,
            "decision": adjudication.decision,
            "member_work_order_ids": list(adjudication.member_work_order_ids),
            "patched_work_order_ids": [
                display_id
                for display_id, working in repaired.items()
                if working.model_dump() != existing_raw[request_by_id[display_id].unit_uid].model_dump()
            ],
            "translation_api_calls": 0,
            "reviewer_api_calls": 0,
            "repair_api_calls": 0,
            "qa_pass": all(row.get("passed") for row in qa_records.values()) if qa_records else adjudication.decision == "HUMAN_REJECTED",
        },
    )
    return {
        "decision": adjudication.decision,
        "semantic_terminal": adjudication.decision,
        "patched": [display_id for display_id, working in repaired.items() if working.model_dump() != existing_raw[request_by_id[display_id].unit_uid].model_dump()],
        "qa": qa_records,
        "translation_api_calls": 0,
        "reviewer_api_calls": 0,
        "repair_api_calls": 0,
    }


def apply_human_adjudication_to_production_checkpoint(
    *,
    checkpoint_dir: Path,
    production_run: Path,
    requests: Sequence[CalibrationRequest],
    views: Mapping[str, TranslationView],
    known_unit_ids: set[str],
    qa_fn: QaFn,
    adjudication: HumanSemanticAdjudication,
    group: SemanticReviewGroup,
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
    triage_classification: str,
    batch_label: str = PRODUCTION_BATCH,
    checkpoint: str = PRODUCTION_CHECKPOINT,
    human_group_transaction_id: str | None = None,
) -> dict[str, Any]:
    """Apply HUMAN_APPROVED only for TRUE_SEMANTIC_HUMAN.  Never mutates RAW or units."""
    from booktrans.production_worker.human_required_triage import assert_human_adjudication_allowed
    from booktrans.production_worker.selection import production_checkpoint_path

    assert_human_adjudication_allowed(triage_classification)
    expected = production_checkpoint_path(production_run, batch_label, checkpoint)
    if checkpoint_dir.resolve() != expected.resolve():
        raise HumanAdjudicationError("PRODUCTION_ADJUDICATION_PATH_MISMATCH")
    raw_file = checkpoint_dir / "translation_raw_response.jsonl"
    raw_before = sha256(raw_file.read_bytes()).hexdigest() if raw_file.is_file() else ""
    existing_raw = load_raw_by_uid(raw_file)
    request_by_id = {item.display_id: item for item in requests}
    from booktrans.production_worker.effective_source import resolve_effective_source
    effective_by_id = {request.display_id: resolve_effective_source(checkpoint_dir=checkpoint_dir, request=request) for request in requests}
    current_effective = effective_by_id[group.current_display_id]
    current_member = next((member for member in group.members if member.display_id == group.current_display_id), None)
    if current_member is None or current_member.source_text != current_effective.effective_source_text:
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_EFFECTIVE_GROUP_REQUIRED")
    if any(provider.request.source_text != effective_by_id[provider.request.display_id].effective_source_text for provider, _ in members):
        raise HumanAdjudicationError("HUMAN_ADJUDICATION_EFFECTIVE_SOURCE_REQUIRED")
    validate_human_adjudication(adjudication, group, members, raws=existing_raw, current_rebuilt=True)
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, checkpoint, batch_label=batch_label)
    repaired: dict[str, TranslationResult] = {}
    qa_records: dict[str, dict[str, Any]] = {}
    if adjudication.decision == "HUMAN_APPROVED":
        repaired = apply_human_patches(members, adjudication)
        for display_id, working in repaired.items():
            request = effective_by_id[display_id].request
            qa_request = overlay_verified_d050_bindings(request, registry_map)
            record = qa_fn(views[display_id], qa_request, working, known_unit_ids)
            qa_records[display_id] = record
            if not record.get("passed"):
                raise HumanAdjudicationError("HUMAN_ADJUDICATION_QA_FAILED")
    current_id = group.current_display_id
    member_by_display_id = {provider_request.request.display_id: (provider_request, current) for provider_request, current in members}
    # Human approval binds the candidate that was actually authoritative at
    # adjudication time, not merely the immutable RAW root.  This matters for
    # a late release decision after an accepted deterministic repair.
    from booktrans.production_worker.production_checkpoint import _resolve_checkpoint_authoritative_candidate

    current_heads = {
        display_id: _resolve_checkpoint_authoritative_candidate(
            checkpoint_dir, display_id, existing_raw[request_by_id[display_id].unit_uid]
        )
        for display_id in member_by_display_id
    }
    for display_id, expected_head in adjudication.expected_current_heads.items():
        if display_id not in current_heads or current_heads[display_id].candidate_sha256 != expected_head:
            raise HumanAdjudicationError("HUMAN_ADJUDICATION_CURRENT_HEAD_STALE")
    current_head = current_heads[current_id]
    current_working = repaired.get(current_id)
    approval_mode = (
        "PRESERVE_CURRENT"
        if current_working is None or candidate_sha(current_working) == current_head.candidate_sha256
        else "MUTATION"
    )
    existing_adjudication = load_stage(checkpoint_dir, "human_adjudications", current_id)
    if existing_adjudication is None:
        persist_stage_once(
            checkpoint_dir,
            "human_adjudications",
            current_id,
            {
            "artifact_type": "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION",
            "schema_version": 1,
            "triage_classification": triage_classification,
            "batch_label": batch_label,
            "logical_work_order_id": current_id,
            "unit_uid": request_by_id[current_id].unit_uid,
            "source_sha256": request_by_id[current_id].source_sha256,
            "effective_source_binding_sha256": current_effective.effective_source_binding_sha256,
            "effective_context_binding_sha256": current_effective.effective_context_binding_sha256,
            "source_authority_provenance": dict(current_effective.source_authority_provenance),
            "request_binding_sha256": member_by_display_id[current_id][0].request_binding_sha256,
            "approval_mode": approval_mode,
            "approved_candidate_sha256": current_head.candidate_sha256 if approval_mode == "PRESERVE_CURRENT" else None,
                **adjudication.model_dump(),
            },
        )
    for display_id, working in repaired.items():
        head = current_heads[display_id]
        if candidate_sha(working) == head.candidate_sha256:
            continue
        parent = member_by_display_id.get(display_id)
        if parent is None:
            raise HumanAdjudicationError("HUMAN_ADJUDICATION_PARENT_CANDIDATE_MISSING")
        provider_request, current = parent
        record = {
                "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE",
                "schema_version": 2,
                "current_display_id": current_id,
                "semantic_review_group_id": group.semantic_review_group_id,
                "decision": adjudication.decision,
                "semantic_terminal": "HUMAN_APPROVED",
                "parent_candidate_sha256": head.candidate_sha256,
                "candidate_binding_sha256": candidate_sha(working),
                "source_sha256": request_by_id[display_id].source_sha256,
                "effective_source_binding_sha256": effective_by_id[display_id].effective_source_binding_sha256,
                "request_binding_sha256": provider_request.request_binding_sha256,
                "working": working.model_dump(),
                "qa": qa_records.get(display_id),
                "human_group_transaction_id": human_group_transaction_id,
            }
        # A legacy singleton is historical evidence.  Later Human corrections
        # are append-only descendants in that same candidate lineage.
        legacy = load_stage(checkpoint_dir, "human_approved_candidates", display_id)
        if legacy is None:
            persist_stage_once(checkpoint_dir, "human_approved_candidates", display_id, record)
        else:
            write_json_once(
                checkpoint_dir / "human_approved_candidates" / display_id / f"{record['candidate_binding_sha256']}.json",
                {
                    **record,
                    "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE_DESCENDANT",
                    "schema_version": 3,
                    "human_candidate_generation": "DESCENDANT",
                    "human_decision_identity_sha256": sha256(
                        adjudication.model_dump_json().encode("utf-8")
                    ).hexdigest(),
                },
            )
    if human_group_transaction_id:
        commit_members = [
            {
                "display_id": display_id,
                "unit_uid": request_by_id[display_id].unit_uid,
                "candidate_binding_sha256": candidate_sha(working),
                "parent_candidate_sha256": current_heads[display_id].candidate_sha256,
            }
            for display_id, working in repaired.items()
        ]
        if len(commit_members) != len(member_by_display_id):
            raise HumanAdjudicationError("HUMAN_GROUP_TRANSACTION_MEMBER_INCOMPLETE")
        write_json_once(checkpoint_dir / "human_group_commits" / f"{human_group_transaction_id}.json", {
            "artifact_type": "PRODUCTION_HUMAN_GROUP_COMMIT", "schema_version": 1,
            "transaction_id": human_group_transaction_id,
            "members": commit_members,
            "decision_sha256": sha256(adjudication.model_dump_json().encode("utf-8")).hexdigest(),
        })
    if sha256(raw_file.read_bytes()).hexdigest() != raw_before:
        raise RuntimeError("PRODUCTION_RAW_MUTATED")
    return {
        "decision": adjudication.decision,
        "semantic_terminal": adjudication.decision,
        "patched": [
            display_id
            for display_id, working in repaired.items()
            if working.model_dump() != existing_raw[request_by_id[display_id].unit_uid].model_dump()
        ],
        "qa": qa_records,
        "translation_api_calls": 0,
        "reviewer_api_calls": 0,
        "repair_api_calls": 0,
    }


def adjudication_from_mapping(payload: Mapping[str, Any]) -> HumanSemanticAdjudication:
    return HumanSemanticAdjudication.model_validate(
        {key: payload[key] for key in HumanSemanticAdjudication.model_fields if key in payload}
    )


def prepare_canary_group_members(
    *,
    output_dir: Path,
    production_run: Path,
    requests: Sequence[CalibrationRequest],
    frozen_order: Sequence[str],
    current_display_id: str,
    translation_bible: Path | None = None,
    d050_registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]] | None = None,
):
    from booktrans.production_worker.continuation import _binding
    from booktrans.production_worker.group_repair import production_sealed_work_order_ids
    from booktrans.production_worker.semantic_review_run import (
        build_unit_group,
        candidate_text_map,
        context_units_for_order,
        segment_count_map,
    )

    request_by_id = {item.display_id: item for item in requests}
    current = request_by_id[current_display_id]
    existing_raw = load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    registry_map = d050_registry if d050_registry is not None else load_checkpoint_d050_registry(production_run, PRODUCTION_CHECKPOINT)
    unsealed = frozenset(str(item) for item in frozen_order)
    sealed = production_sealed_work_order_ids(production_run)
    group = build_unit_group(
        current,
        context_units_for_order(frozen_order),
        registry_map,
        candidate_texts=candidate_text_map(requests, existing_raw),
        segment_counts=segment_count_map(requests),
        unsealed_work_order_ids=unsealed,
        sealed_work_order_ids=sealed,
    )
    members = []
    for member in group.members:
        request = request_by_id[member.display_id]
        members.append((_binding(request, translation_bible), existing_raw[request.unit_uid]))
    return group, members, existing_raw
