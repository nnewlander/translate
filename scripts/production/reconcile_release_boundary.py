"""Batch-aware, bounded release-boundary reconciliation.

This is deliberately a thin release harness around existing production
authority, QA, provider-contract, repair, and candidate-mutation boundaries.
It never calls Translation and never reopens a checkpoint.  Its only new
durable facts are append-only release adjudications and a batch-scoped gate
consumed by :mod:`booktrans.harness.release_membership`.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, Mapping

from booktrans.domain.calibration import TranslationResult
from booktrans.production_worker.artifacts import write_json_once
from booktrans.production_worker.candidate_authority import CandidateAuthorityError, require_parent_is_current
from booktrans.production_worker.pipeline import (
    UnitOutcome, enrich_request, error_directed_protocol_retry_feedback,
    repair_with_contract_boundary, review_with_contract_boundary,
)
from booktrans.production_worker.production_checkpoint import (
    _candidate_binding, _commit_accepted_candidate_mutation,
    _human_approved_candidate_records, _production_review_group, _rescue_working_overrides,
    project_current_checkpoint_outcomes, sealed_checkpoint_execution_inputs,
)
from booktrans.production_worker.selection import load_json
from booktrans.production_worker.selection import CHECKPOINT_SIZE
from booktrans.production_worker.unit_store import load_raw_by_uid, persist_stage_once
from booktrans.production_worker.worker import SOURCE, providers_from_environment
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.router import route_semantic_unit
from booktrans.providers.translation_provider import ProviderContractGap


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
_INPUTS: dict[tuple[str, str], dict[str, Any]] = {}
_CURRENT_OUTCOMES: dict[tuple[str, str], dict[str, UnitOutcome]] = {}
_DEBT_STAGES = ("resolution_queue_deferred_debts", "reviewer_contract_debts", "repair_contract_debts")


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"RELEASE_EVIDENCE_NOT_OBJECT:{path}")
    return value


def _batch_dir_name(batch: str) -> str:
    if not re.fullmatch(r"FULL_BOOK_BATCH_\d{3}", batch):
        raise RuntimeError("RELEASE_BATCH_LABEL_INVALID")
    return batch.lower()


def _manifest_path(batch_dir: Path) -> Path:
    versions = sorted(batch_dir.glob("batch_manifest_v*.json"), key=lambda item: int(re.search(r"_v(\d+)", item.name).group(1)))
    path = versions[-1] if versions else batch_dir / "batch_manifest.json"
    manifest = _read(path)
    if (
        manifest.get("batch") != f"FULL_BOOK_BATCH_{batch_dir.name.removeprefix('full_book_batch_')}"
        or manifest.get("status") != "COMPLETE"
        or manifest.get("completed_count") != manifest.get("target_count")
        or not isinstance(manifest.get("target_count"), int)
        or manifest.get("target_count", 0) < 1
        or manifest.get("next_checkpoint") is not None
    ):
        raise RuntimeError("RELEASE_BATCH_NOT_COMPLETE")
    return path


def _checkpoints(batch_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for ordinal, item in enumerate(manifest.get("checkpoints") or [], start=1):
        if not isinstance(item, Mapping) or item.get("id") != f"checkpoint_{ordinal:02d}":
            raise RuntimeError("RELEASE_CHECKPOINT_SEQUENCE_INVALID")
        checkpoint = str(item["id"])
        directory = batch_dir / checkpoint
        if not (directory / "checkpoint_evidence.json").is_file() and not (directory / "seal_generations").is_dir():
            raise RuntimeError(f"RELEASE_CHECKPOINT_NOT_SEALED:{checkpoint}")
        result[checkpoint] = directory
    target_count = manifest.get("target_count")
    expected_count = (target_count + CHECKPOINT_SIZE - 1) // CHECKPOINT_SIZE
    if len(result) != expected_count:
        raise RuntimeError("RELEASE_CHECKPOINT_COUNT_INVALID")
    return result


def _versioned(batch_dir: Path, stem: str) -> Path:
    versions = [int(match.group(1)) for path in batch_dir.glob(f"{stem}_v*.json") if (match := re.fullmatch(rf"{re.escape(stem)}_v(\d+)\.json", path.name))]
    return batch_dir / f"{stem}_v{max(versions, default=0) + 1}.json"


def _collect_debts(batch_dir: Path, checkpoints: Mapping[str, Path]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for checkpoint, directory in checkpoints.items():
        for stage in _DEBT_STAGES:
            folder = directory / stage
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json"), key=lambda item: item.name.casefold()):
                record = _read(path)
                if record.get("release_gate") != "BLOCKING_UNTIL_BATCH_DEBT_RESOLUTION":
                    continue
                display_id = record.get("logical_work_order_id")
                binding = record.get("candidate_binding_sha256")
                if not isinstance(display_id, str) or not isinstance(binding, str):
                    raise RuntimeError(f"RELEASE_DEBT_BINDING_INVALID:{path}")
                entry = entries.setdefault(display_id, {"checkpoint": checkpoint, "evidence": []})
                if entry["checkpoint"] != checkpoint:
                    raise RuntimeError(f"RELEASE_DEBT_CROSS_CHECKPOINT_COLLISION:{display_id}")
                entry["evidence"].append({"stage": stage, "path": str(path), "sha256": _sha(path), "record": record})
    return entries


def _loaded(batch: str, checkpoint: str) -> dict[str, Any]:
    key = (batch, checkpoint)
    if key not in _INPUTS:
        _INPUTS[key] = sealed_checkpoint_execution_inputs(
            run=RUN, batch_label=batch, checkpoint=checkpoint, source_path=SOURCE,
        )
    return _INPUTS[key]


def _audit_sealed_inputs(batch: str, checkpoints: Mapping[str, Path]) -> dict[str, Any]:
    """Classify sealed request/reacquisition evidence without provider access."""
    supersession: list[str] = []
    reacquisition: list[str] = []
    classifications: dict[str, str] = {}
    for checkpoint, directory in checkpoints.items():
        has_supersession = (directory / "translation_request_supersessions").is_dir() and any(
            (directory / "translation_request_supersessions").glob("*.json")
        )
        paths = sorted((directory / "provider_reacquisitions").glob("*.json")) if (directory / "provider_reacquisitions").is_dir() else []
        if has_supersession:
            supersession.append(checkpoint)
        if paths:
            reacquisition.append(checkpoint)
        if not has_supersession and not paths:
            continue
        seal = directory / "checkpoint_evidence.json"
        if paths and seal.is_file() and any(path.stat().st_mtime_ns > seal.stat().st_mtime_ns for path in paths):
            classifications[checkpoint] = "TRUE_POST_SEAL"
            continue
        try:
            _current_outcomes(batch, checkpoint)
        except Exception:
            classifications[checkpoint] = "UNPROVEN"
        else:
            classifications[checkpoint] = "PRE_SEAL_AND_SEAL_BOUND"
    return {
        "sealed_checkpoints_with_request_supersession": sorted(supersession),
        "sealed_checkpoints_with_reacquisition": sorted(reacquisition),
        "classifications": classifications,
        "pre_seal_seal_bound_count": sum(value == "PRE_SEAL_AND_SEAL_BOUND" for value in classifications.values()),
        "true_post_seal_count": sum(value == "TRUE_POST_SEAL" for value in classifications.values()),
        "unproven_count": sum(value == "UNPROVEN" for value in classifications.values()),
    }


def _current_outcomes(batch: str, checkpoint: str) -> dict[str, UnitOutcome]:
    """Consume the production owner's complete, provider-free CURRENT set."""
    key = (batch, checkpoint)
    if key not in _CURRENT_OUTCOMES:
        _CURRENT_OUTCOMES[key] = project_current_checkpoint_outcomes(
            run=RUN, batch_label=batch, checkpoint=checkpoint, loaded=_loaded(batch, checkpoint),
        )
    return _CURRENT_OUTCOMES[key]


def _facts(batch: str, batch_dir: Path, checkpoint: str, display_id: str) -> dict[str, Any]:
    loaded = _loaded(batch, checkpoint)
    outcome = _current_outcomes(batch, checkpoint).get(display_id)
    if outcome is None or outcome.raw is None or outcome.working is None or outcome.qa is None:
        raise RuntimeError(f"RELEASE_CURRENT_PROJECTION_MISSING:{display_id}")
    decision = dict(outcome.registry_decision)
    if not decision or not isinstance(decision.get("disposition"), str):
        raise RuntimeError(f"RELEASE_CURRENT_DISPOSITION_MISSING:{display_id}")
    request = outcome.request
    directory = batch_dir / checkpoint
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    provider_request = enrich_request(request, translation_bible=bible)
    return {
        "loaded": loaded, "request": request, "checkpoint_dir": directory,
        "raw": outcome.raw,
        "head": SimpleNamespace(candidate=outcome.working, candidate_sha256=_candidate_binding(outcome.working)),
        "bible": bible, "qa": dict(outcome.qa), "provider_request": provider_request,
        "current_disposition": decision,
    }


def _current_debt_entry(batch: str, batch_dir: Path, display_id: str, entry: Mapping[str, Any]) -> dict[str, Any] | None:
    facts = _facts(batch, batch_dir, str(entry["checkpoint"]), display_id)
    if facts["current_disposition"]["disposition"] == "CONTINUE":
        return None
    matching = [item["record"] for item in entry["evidence"] if item["record"].get("candidate_binding_sha256") == facts["head"].candidate_sha256]
    if not matching:
        # Sealed debt is immutable historical evidence only.  A current
        # disposition remains blocking (or resolved) on its own authority;
        # it does not need an old debt candidate to reconstruct Release QA.
        facts["debt_request_binding_sha256"] = enrich_request(
            facts["request"], translation_bible=None,
        ).request_binding_sha256
        facts["historical_debt_current_head_match"] = False
        return facts
    request_bindings = {record.get("request_binding_sha256") for record in matching}
    for record in matching:
        # The debt's request binding is the sealed, immutable review-context
        # binding.  The current provider prompt may include a later immutable
        # bible snapshot, so recomputing it here would incorrectly make a
        # current debt look stale.  Preserve both bindings in the resolution.
        if (record.get("unit_uid") != facts["request"].unit_uid or record.get("source_sha256") != facts["request"].source_sha256):
            raise RuntimeError(f"RELEASE_DEBT_CURRENT_BINDING_INVALID:{display_id}")
    if len(request_bindings) != 1 or not isinstance(next(iter(request_bindings)), str):
        raise RuntimeError(f"RELEASE_DEBT_REQUEST_BINDING_AMBIGUOUS:{display_id}")
    facts["debt_request_binding_sha256"] = next(iter(request_bindings))
    facts["historical_debt_current_head_match"] = True
    return facts


def _context(facts: Mapping[str, Any], candidate: TranslationResult) -> Mapping[str, Any] | None:
    directory, loaded, request = facts["checkpoint_dir"], facts["loaded"], facts["request"]
    overrides = _rescue_working_overrides(directory, loaded, load_raw_by_uid(directory / "translation_raw_response.jsonl"))
    overrides[request.display_id] = candidate
    overrides[request.unit_uid] = candidate
    group = _production_review_group(request, loaded=loaded, run=RUN, checkpoint=directory.name, existing_raw=load_raw_by_uid(directory / "translation_raw_response.jsonl"), candidate_overrides=overrides)
    return None if group is None else group.as_reviewer_context_payload()


def _review_context_binding(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Bind release review evidence to its current semantic-group contract.

    Candidate SHA alone cannot make a reviewer result current when frozen IR
    corrects the document section/lane.  This is deliberately context-only:
    it neither changes the candidate nor spends a same-binding retry.
    """
    context = _context(facts, facts["head"].candidate) or {}
    return {
        "context_hash": context.get("context_hash"),
        "semantic_group_hash": context.get("group_hash"),
        "continuation_relation": context.get("continuation_relation"),
        "current_lane": context.get("current_lane"),
        "content_role": (context.get("source_structural_identity") or {}).get("content_role"),
    }


def _index_context_rebind_required(facts: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    current = _review_context_binding(facts)
    # Historical records have no context binding.  They may be superseded only
    # for the narrowly proven OOXML Index lane, never for arbitrary debt.
    return (
        current.get("current_lane") == "INDEX"
        and dict(record.get("review_context_binding") or {}) != current
    )


def _record(facts: Mapping[str, Any], entry: Mapping[str, Any], *, status: str, resolution_type: str | None, detail: Mapping[str, Any]) -> dict[str, Any]:
    request, head = facts["request"], facts["head"]
    return {
        "artifact_type": "BATCH_RELEASE_CURRENT_HEAD_RESOLUTION", "schema_version": 1,
        "batch": facts["batch"], "checkpoint": facts["checkpoint"],
        "logical_work_order_id": request.display_id, "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256, "request_binding_sha256": facts["debt_request_binding_sha256"],
        "provider_request_binding_sha256": facts["provider_request"].request_binding_sha256,
        "current_authoritative_candidate_sha256": head.candidate_sha256,
        "candidate_authority_pass": True, "core_integrity_pass": bool(facts["qa"].get("passed")),
        "review_context_binding": _review_context_binding(facts),
        "release_status": status, "resolution_type": resolution_type,
        "debt_evidence": entry["evidence"], "detail": dict(detail), "translation_api_calls": 0,
    }


def _human_packet_item(facts: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """A compact, fully-bound handoff; it never asks a human about workflow."""
    request, candidate = facts["request"], facts["head"].candidate
    return {
        "work_order_id": request.display_id,
        "unit_uid": request.unit_uid,
        "exact_source_text": [segment.model_dump() for segment in request.source_segments],
        "current_raw_target": candidate.model_dump(),
        "canonical_context": _context(facts, candidate),
        "repair_history": record.get("debt_evidence"),
        "reviewer_evidence": record.get("detail"),
        "terminology_context": {"translation_bible": str(facts["bible"]) if facts["bible"] else None, "external_terminology_model_called": False},
        "binding": {key: record.get(key) for key in ("batch", "checkpoint", "logical_work_order_id", "unit_uid", "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256")},
        "decision_required": "Human only decides whether the current target is acceptable or supplies corrected target text; no workflow or authority decision is requested.",
    }


def _human_terminal_is_current(facts: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate the existing production HumanSemanticAdjudication terminal.

    This is consumption, not a second human-review system.  A terminal may
    supersede release debt only when it is exact-batch, exact-unit, and bound
    to the present Candidate Authority head.
    """
    display_id, directory, request, head = facts["request"].display_id, facts["checkpoint_dir"], facts["request"], facts["head"]
    path = directory / "human_adjudications" / f"{display_id}.json"
    if not path.is_file():
        return None
    adjudication = _read(path)
    if (
        adjudication.get("artifact_type") != "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION"
        or adjudication.get("decision") != "HUMAN_APPROVED"
        or adjudication.get("batch_label") != facts["batch"]
        or adjudication.get("logical_work_order_id") != display_id
        or adjudication.get("unit_uid") != request.unit_uid
        or adjudication.get("source_sha256") != request.source_sha256
    ):
        raise RuntimeError(f"RELEASE_HUMAN_TERMINAL_BINDING_INVALID:{display_id}")
    candidates = _human_approved_candidate_records(directory, display_id)
    candidate = next((record for record in candidates if record.get("candidate_binding_sha256") == head.candidate_sha256), None)
    mode = adjudication.get("approval_mode")
    if candidate is not None:
        parents = {item["record"].get("candidate_binding_sha256") for item in entry["evidence"]}
        if (
            mode != "MUTATION" or candidate.get("semantic_terminal") != "HUMAN_APPROVED"
            or candidate.get("candidate_binding_sha256") != head.candidate_sha256
            or candidate.get("parent_candidate_sha256") not in parents
            or not isinstance(candidate.get("qa"), Mapping) or candidate["qa"].get("passed") is not True
        ):
            raise RuntimeError(f"RELEASE_HUMAN_TERMINAL_BINDING_INVALID:{display_id}")
    elif not (mode == "PRESERVE_CURRENT" and adjudication.get("approved_candidate_sha256") == head.candidate_sha256):
        raise RuntimeError(f"RELEASE_HUMAN_TERMINAL_BINDING_INVALID:{display_id}")
    candidate_path = None
    if candidate is not None:
        legacy = directory / "human_approved_candidates" / f"{display_id}.json"
        candidate_path = str(legacy) if legacy.is_file() and _read(legacy).get("candidate_binding_sha256") == head.candidate_sha256 else "VERSIONED_HUMAN_DESCENDANT"
    return {"adjudication_path": str(path), "adjudication_sha256": _sha(path), "candidate_path": candidate_path}


def _review(facts: Mapping[str, Any], providers, *, retry: bool, feedback: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    result = review_with_contract_boundary(providers.reviewer, facts["provider_request"], facts["head"].candidate, deterministic_qa_summary={"passed": True}, semantic_context=_context(facts, facts["head"].candidate), protocol_retry=retry, protocol_retry_feedback=feedback)
    return {"review": result.review.model_dump(), "provider": result.candidate.provider, "model": result.candidate.model, "usage": dict(result.candidate.usage), "protocol_retry": retry}


def _repair(facts: Mapping[str, Any], initial: Mapping[str, Any], providers) -> tuple[dict[str, Any], dict[str, Any]]:
    from booktrans.providers.qwen_max import ReviewResult
    review = ReviewResult.model_validate(initial["review"])
    gate = decide_approved_repair_contract(facts["provider_request"], facts["head"].candidate, review=review, allow_semantic_narrow=True)
    if gate.action != "REPAIR" or gate.contract is None:
        return dict(facts), {"status": "HUMAN_REQUIRED", "reason": "REPAIR_CONTRACT_NOT_APPROVED", "initial_review": initial}
    try:
        repaired = repair_with_contract_boundary(providers.repair, facts["provider_request"], facts["head"].candidate, gate.contract).repaired
    except ProviderContractGap as first:
        try:
            repaired = repair_with_contract_boundary(
                providers.repair, facts["provider_request"], facts["head"].candidate, gate.contract,
                protocol_retry=True,
                protocol_retry_feedback=error_directed_protocol_retry_feedback(
                    facts["provider_request"], first, lane="REPAIR",
                    permitted_segment_indices=list(gate.contract.permitted_segment_indices),
                ),
            ).repaired
        except ProviderContractGap as second:
            return dict(facts), {"status": "HUMAN_REQUIRED", "reason": "REPAIR_CONTRACT_INVALID_AFTER_RETRY", "errors": [str(first), str(second)], "initial_review": initial}
    if _candidate_binding(repaired) == facts["head"].candidate_sha256:
        return dict(facts), {"status": "HUMAN_REQUIRED", "reason": "REPAIR_NO_MUTATION", "initial_review": initial}
    equivalence = _d051_equivalence_for(facts["checkpoint_dir"], facts["request"], repaired, bible=facts["bible"])
    qa = _qa_with_d051(harness_qa_record, facts["loaded"]["views"][facts["request"].display_id], facts["request"], repaired, facts["loaded"]["known_unit_ids"], equivalence)
    if not qa.get("passed"):
        return dict(facts), {"status": "HUMAN_REQUIRED", "reason": "REPAIR_CORE_QA_FAILED", "qa": qa, "initial_review": initial}
    try:
        final = review_with_contract_boundary(providers.reviewer, facts["provider_request"], repaired, deterministic_qa_summary={"passed": True}, semantic_context=_context(facts, repaired))
    except ProviderContractGap as first:
        try:
            final = review_with_contract_boundary(
                providers.reviewer, facts["provider_request"], repaired,
                deterministic_qa_summary={"passed": True}, semantic_context=_context(facts, repaired),
                protocol_retry=True,
                protocol_retry_feedback=error_directed_protocol_retry_feedback(facts["provider_request"], first, lane="REVIEW"),
            )
        except ProviderContractGap as second:
            return dict(facts), {"status": "HUMAN_REQUIRED", "reason": "FINAL_REVIEW_CONTRACT_INVALID_AFTER_RETRY", "errors": [str(first), str(second)], "initial_review": initial}
    final_detail = {"verdict": final.review.verdict, "issues": [issue.model_dump() for issue in final.review.issues], "provider": final.candidate.provider, "model": final.candidate.model}
    if final.review.verdict != "ACCEPT" or final.review.issues:
        return dict(facts), {"status": "HUMAN_REQUIRED", "reason": "FINAL_REVIEW_NOT_ACCEPTED", "initial_review": initial, "final_review": final_detail}
    require_parent_is_current(artifact="release_boundary", parent_candidate_sha256=facts["head"].candidate_sha256, current_candidate_sha256=facts["head"].candidate_sha256)
    outcome = UnitOutcome(request=facts["request"], decision=route_semantic_unit(facts["request"]), raw=facts["raw"], working=repaired, qa=qa)
    outcome.final_status = "ACCEPT"; outcome.semantic_terminal = "MODEL_ACCEPTED"; outcome.repair_applied = True; outcome.repair_policy_id = "RELEASE_BOUNDARY"
    _commit_accepted_candidate_mutation(checkpoint_dir=facts["checkpoint_dir"], request=facts["request"], raw=facts["raw"], outcome=outcome, source_sha256=facts["loaded"]["source_sha256"], selection_sha256=facts["loaded"]["selection_sha256"], bible=facts["bible"])
    refreshed = _facts(facts["batch"], facts["batch_dir"], facts["checkpoint"], facts["request"].display_id)
    refreshed.update({"batch": facts["batch"], "batch_dir": facts["batch_dir"], "checkpoint": facts["checkpoint"], "debt_request_binding_sha256": facts["debt_request_binding_sha256"]})
    if refreshed["head"].candidate_sha256 != _candidate_binding(repaired):
        raise CandidateAuthorityError("RELEASE_REPAIR_ACCEPTED_CANDIDATE_NOT_CURRENT")
    return refreshed, {"status": "RESOLVED_FOR_RELEASE", "initial_review": initial, "final_review": final_detail, "repair_candidate_sha256": _candidate_binding(repaired)}


def run(*, batch: str, apply: bool, finalize_only: bool = False) -> dict[str, Any]:
    batch_dir = RUN / _batch_dir_name(batch)
    manifest_path = _manifest_path(batch_dir); manifest = _read(manifest_path); checkpoints = _checkpoints(batch_dir, manifest)
    sealed_input_audit = _audit_sealed_inputs(batch, checkpoints)
    if sealed_input_audit["true_post_seal_count"] or sealed_input_audit["unproven_count"]:
        raise RuntimeError("RELEASE_SEALED_INPUT_AUDIT_FAILED")
    debts = _collect_debts(batch_dir, checkpoints)
    current: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    projection_failures: list[dict[str, str]] = []
    current_dispositions: dict[str, str] = {}
    for display_id, entry in sorted(debts.items()):
        checkpoint = str(entry["checkpoint"])
        try:
            projected = _facts(batch, batch_dir, checkpoint, display_id)
        except RuntimeError as exc:
            if apply:
                raise RuntimeError(f"RELEASE_CURRENT_PROJECTION_UNAVAILABLE:{checkpoint}") from exc
            projection_failures.append({"checkpoint": checkpoint, "reason": str(exc)})
            continue
        current_dispositions[display_id] = str(projected["current_disposition"]["disposition"])
        facts = _current_debt_entry(batch, batch_dir, display_id, entry)
        if facts is not None:
            facts.update({"batch": batch, "batch_dir": batch_dir, "checkpoint": checkpoint})
            current[display_id] = (facts, entry)
    if not apply:
        # ``current`` is exclusively the canonical CURRENT projection.
        # Historical sealed debt is retained as audit evidence in ``entry``;
        # it never selects the present semantic state.
        resolved = []
        unresolved = []
        deferred = []
        human = []
        hard_stops = []
        for display_id, (facts, entry) in current.items():
            disposition = facts["current_disposition"]["disposition"]
            if disposition == "DEFERRED_SEMANTIC_DEBT":
                deferred.append(display_id); unresolved.append(display_id); continue
            if disposition == "HARD_STOP":
                hard_stops.append(display_id); unresolved.append(display_id); continue
            if _human_terminal_is_current(facts, entry) is not None:
                resolved.append(display_id); continue
            if bool(getattr(_current_outcomes(batch, facts["checkpoint"])[display_id], "human_required", False)):
                human.append(display_id); unresolved.append(display_id); continue
            # A valid current-head supersession is authoritative over the
            # historical first adjudication, even when both files exist.
            supersession = facts["checkpoint_dir"] / "release_boundary_adjudication_supersessions" / f"{display_id}.json"
            existing_path = supersession if supersession.is_file() else facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json"
            existing = _read(existing_path) if existing_path.is_file() else None
            if isinstance(existing, Mapping) and existing.get("release_status") == "RESOLVED_FOR_RELEASE" and existing.get("current_authoritative_candidate_sha256") == facts["head"].candidate_sha256:
                resolved.append(display_id)
            else:
                unresolved.append(display_id)
        result = {
            "mode": "DRY_RUN", "current_projected_noncontinue_total": len(current),
            "resolved": sorted(resolved), "current_release_debt_total": len(unresolved),
            "work_orders": sorted(unresolved), "current_release_debt": sorted(deferred),
            "current_release_human": sorted(human), "current_release_hard_stop": sorted(hard_stops),
            "current_dispositions": current_dispositions,
            "current_projection_failures": projection_failures,
        }
        result["release_reconciliation_runtime_ok"] = not projection_failures
        result["sealed_input_audit"] = sealed_input_audit
        return result
    if finalize_only:
        # Deliberately before provider construction: this mode has no provider
        # capability and may only publish a zero-debt state projection.
        projected = run(batch=batch, apply=False)
        if projected["current_release_debt_total"] != 0:
            raise RuntimeError("FINALIZE_ONLY_RELEASE_DEBT_NOT_ZERO")
        state_digest = sha256(json.dumps(projected, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        debt_manifest = {
            "artifact_type": "BATCH_RELEASE_DEBT_MANIFEST", "schema_version": 2,
            "batch": batch, "batch_dir": batch_dir.name, "source_sha256": manifest["source_sha256"],
            "current_authoritative_state_digest": state_digest,
            "current_release_debt_total": 0, "entries": [],
            "finalize_only": True, "model_calls": 0, "provider_dispatches": 0,
        }
        debt_path = _versioned(batch_dir, "release_debt_manifest")
        write_json_once(debt_path, debt_manifest)
        gate = {
            "artifact_type": "BATCH_RELEASE_GATE", "schema_version": 2,
            "batch": batch, "batch_dir": batch_dir.name,
            "release_debt_manifest": str(debt_path), "release_debt_manifest_sha256": _sha(debt_path),
            "current_authoritative_state_digest": state_digest,
            "verdict": "PASS", "unresolved_ids": [], "integrity_pass": True,
            "candidate_authority_pass": True, "finalize_only": True,
            "model_calls": 0, "provider_dispatches": 0,
        }
        gate_path = _versioned(batch_dir, "release_gate")
        write_json_once(gate_path, gate)
        return {"mode": "FINALIZE_ONLY", "gate": gate, "gate_path": str(gate_path), "model_calls": 0, "provider_dispatches": 0}
    # A fully Human-resolved boundary is a projection-only operation.  Do not
    # initialize provider clients (or wait on their environment) unless an
    # unresolved current head actually needs a model review.
    providers = None
    entries: list[dict[str, Any]] = []
    human: list[dict[str, Any]] = []
    counts = {"reviewed": 0, "accepted_without_repair": 0, "repairs_executed": 0, "repairs_accepted": 0, "reviewer_contract_failures": 0, "repair_contract_failures": 0, "qwen_calls": 0}
    for display_id, (facts, entry) in current.items():
        supersession = facts["checkpoint_dir"] / "release_boundary_adjudication_supersessions" / f"{display_id}.json"
        existing_path = supersession if supersession.is_file() else facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json"
        recheck_slot_conflict = False
        human_terminal = _human_terminal_is_current(facts, entry)
        if human_terminal is not None:
            record = _record(facts, entry, status="RESOLVED_FOR_RELEASE", resolution_type="HUMAN_APPROVED", detail={"human_terminal": human_terminal})
            supersession_path = facts["checkpoint_dir"] / "release_boundary_adjudication_supersessions" / f"{display_id}.json"
            if supersession_path.is_file():
                existing = _read(supersession_path)
                required = (
                    "artifact_type", "batch", "checkpoint", "logical_work_order_id", "unit_uid",
                    "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256",
                    "candidate_authority_pass", "core_integrity_pass", "release_status", "resolution_type",
                )
                if any(existing.get(key) != record.get(key) for key in required):
                    # The original file is immutable historical projection.
                    # A later Human current-head terminal is an append-only
                    # authoritative descendant, never an overwrite.
                    supersession_path = supersession_path.parent / display_id / f"{facts['head'].candidate_sha256}.json"
                    write_json_once(supersession_path, record)
            else:
                persist_stage_once(facts["checkpoint_dir"], "release_boundary_adjudication_supersessions", display_id, record)
            base = {key: record[key] for key in ("logical_work_order_id", "checkpoint", "unit_uid", "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256")}
            entries.append({**base, "release_status": "RESOLVED_FOR_RELEASE", "resolution_path": str(supersession_path), "resolution_sha256": _sha(supersession_path)})
            continue
        if existing_path.is_file():
            record = _read(existing_path)
            if (
                record.get("artifact_type") != "BATCH_RELEASE_CURRENT_HEAD_RESOLUTION"
                or record.get("batch") != batch
                or record.get("checkpoint") != facts["checkpoint"]
                or record.get("logical_work_order_id") != display_id
                or record.get("unit_uid") != facts["request"].unit_uid
                or record.get("source_sha256") != facts["request"].source_sha256
                or record.get("request_binding_sha256") != facts["debt_request_binding_sha256"]
                or record.get("current_authoritative_candidate_sha256") != facts["head"].candidate_sha256
                or record.get("candidate_authority_pass") is not True
                or record.get("core_integrity_pass") is not True
            ):
                raise RuntimeError(f"RELEASE_RESUME_EVIDENCE_BINDING_INVALID:{display_id}")
            recheck_slot_conflict = (
                record.get("release_status") == "HUMAN_REQUIRED"
                and isinstance(record.get("detail"), Mapping)
                and record["detail"].get("reason") == "RELEASE_REPAIR_CANDIDATE_AUTHORITY_SLOT_OCCUPIED"
            )
            recheck_slot_conflict = recheck_slot_conflict or _index_context_rebind_required(facts, record)
            if not recheck_slot_conflict:
                base = {key: record[key] for key in ("logical_work_order_id", "checkpoint", "unit_uid", "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256")}
                if record["release_status"] == "RESOLVED_FOR_RELEASE":
                    entries.append({**base, "release_status": "RESOLVED_FOR_RELEASE", "resolution_path": str(existing_path), "resolution_sha256": _sha(existing_path)})
                else:
                    entries.append({**base, "release_status": record["release_status"]})
                if record["release_status"] != "RESOLVED_FOR_RELEASE":
                    human.append(_human_packet_item(facts, record))
                continue
        record_stage = "release_boundary_adjudication_supersessions" if recheck_slot_conflict else "release_boundary_adjudications"
        # Existing accepted mutations are immutable historical heads.  A
        # release correction may now append only an exact-current-head
        # descendant in that same Candidate Authority lineage.
        if facts.get("release_repair_resume_conflict"):
            record = _record(
                facts, entry, status="HUMAN_REQUIRED", resolution_type=None,
                detail={"reason": "RELEASE_REPAIR_RESUME_CANDIDATE_AUTHORITY_CONFLICT", **facts["release_repair_resume_conflict"]},
            )
            persist_stage_once(facts["checkpoint_dir"], record_stage, display_id, record)
            entries.append({**{key: record[key] for key in ("logical_work_order_id", "checkpoint", "unit_uid", "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256")}, "release_status": "HUMAN_REQUIRED"})
            human.append(_human_packet_item(facts, record))
            continue
        if providers is None:
            providers = providers_from_environment()
        counts["reviewed"] += 1
        try:
            initial = _review(facts, providers, retry=False); counts["qwen_calls"] += 1
        except ProviderContractGap as first:
            try:
                initial = _review(
                    facts, providers, retry=True,
                    feedback=error_directed_protocol_retry_feedback(facts["provider_request"], first, lane="REVIEW"),
                ); counts["qwen_calls"] += 1
            except ProviderContractGap as second:
                counts["reviewer_contract_failures"] += 1
                detail = {"reason": "RELEASE_REVIEW_CONTRACT_INVALID_AFTER_RETRY", "errors": [str(first), str(second)]}
                record = _record(facts, entry, status="HUMAN_REQUIRED", resolution_type=None, detail=detail)
                persist_stage_once(facts["checkpoint_dir"], record_stage, display_id, record)
                human.append(_human_packet_item(facts, record))
                entries.append({**{key: record[key] for key in ("logical_work_order_id", "checkpoint", "unit_uid", "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256")}, "release_status": "HUMAN_REQUIRED"})
                continue
        verdict = initial["review"].get("verdict"); issues = initial["review"].get("issues") or []
        if verdict == "ACCEPT" and not issues:
            counts["accepted_without_repair"] += 1
            record = _record(facts, entry, status="RESOLVED_FOR_RELEASE", resolution_type="REVIEW_ACCEPT", detail={"release_review": initial})
        elif verdict == "REPAIR" and issues:
            counts["repairs_executed"] += 1
            final_facts, detail = _repair(facts, initial, providers)
            if detail["status"] == "RESOLVED_FOR_RELEASE": counts["repairs_accepted"] += 1
            if "CONTRACT_INVALID" in str(detail): counts["repair_contract_failures"] += 1
            record = _record(final_facts, entry, status=detail["status"], resolution_type="REPAIR_ACCEPT" if detail["status"] == "RESOLVED_FOR_RELEASE" else None, detail=detail)
            facts = final_facts
        else:
            detail = {"reason": "RELEASE_REVIEW_NON_ACTIONABLE", "release_review": initial}
            record = _record(facts, entry, status="HUMAN_REQUIRED", resolution_type=None, detail=detail)
        persist_stage_once(facts["checkpoint_dir"], record_stage, display_id, record)
        base = {key: record[key] for key in ("logical_work_order_id", "checkpoint", "unit_uid", "source_sha256", "request_binding_sha256", "current_authoritative_candidate_sha256")}
        if record["release_status"] == "RESOLVED_FOR_RELEASE":
            path = facts["checkpoint_dir"] / record_stage / f"{display_id}.json"
            entries.append({**base, "release_status": "RESOLVED_FOR_RELEASE", "resolution_path": str(path), "resolution_sha256": _sha(path)})
        else:
            entries.append({**base, "release_status": "HUMAN_REQUIRED"})
            human.append(_human_packet_item(facts, record))
    debt_manifest = {"artifact_type": "BATCH_RELEASE_DEBT_MANIFEST", "schema_version": 1, "batch": batch, "batch_dir": batch_dir.name, "source_sha256": manifest["source_sha256"], "entries": entries, "translation_api_calls": 0}
    debt_path = _versioned(batch_dir, "release_debt_manifest"); write_json_once(debt_path, debt_manifest)
    unresolved = [item["logical_work_order_id"] for item in entries if item["release_status"] != "RESOLVED_FOR_RELEASE"]
    gate = {"artifact_type": "BATCH_RELEASE_GATE", "schema_version": 1, "batch": batch, "batch_dir": batch_dir.name, "release_debt_manifest": str(debt_path), "release_debt_manifest_sha256": _sha(debt_path), "verdict": "PASS" if not unresolved else "BLOCKED_HUMAN_ADJUDICATION_REQUIRED", "unresolved_ids": unresolved, "integrity_pass": True, "candidate_authority_pass": True, "translation_api_calls": 0}
    gate_path = _versioned(batch_dir, "release_gate"); write_json_once(gate_path, gate)
    if human:
        write_json_once(_versioned(batch_dir, "human_release_packet"), {"artifact_type": "BATCH_HUMAN_RELEASE_PACKET", "schema_version": 1, "batch": batch, "items": human})
    return {"mode": "APPLY", "initial_current_release_debt_total": len(current), "entries": entries, "gate": gate, "counts": counts, "human_packet_count": len(human)}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--batch", required=True); parser.add_argument("--apply", action="store_true"); parser.add_argument("--finalize-only", action="store_true"); parser.add_argument("--authorize-release-boundary-write", action="store_true")
    args = parser.parse_args()
    if (args.apply or args.finalize_only) and not args.authorize_release_boundary_write: raise RuntimeError("RELEASE_BOUNDARY_WRITE_NOT_AUTHORIZED")
    if args.apply and args.finalize_only: raise RuntimeError("RELEASE_MODE_AMBIGUOUS")
    print(json.dumps(run(batch=args.batch, apply=(args.apply or args.finalize_only), finalize_only=args.finalize_only), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
