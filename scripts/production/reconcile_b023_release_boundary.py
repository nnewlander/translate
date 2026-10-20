"""Bounded B023 release-debt adjudication; it never starts Translation or B024.

The script intentionally reuses the sealed checkpoint's request, QA, provider,
and single-head candidate conventions.  It is not a second campaign pipeline:
its only durable outputs are append-only release evidence and, when an exact
repair is accepted, the existing ``accepted_candidate_mutations`` authority.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from booktrans.domain.calibration import TranslationResult
from booktrans.harness.human_release_consumer import (
    HumanReleaseConsumerError,
    consume_human_approval_for_release,
)
from booktrans.production_worker.candidate_authority import CandidateAuthorityError, require_parent_is_current
from booktrans.production_worker.pipeline import UnitOutcome, enrich_request
from booktrans.production_worker.production_checkpoint import (
    _candidate_binding,
    _commit_accepted_candidate_mutation,
    _d051_equivalence_for,
    _production_review_group,
    _qa_with_d051,
    _rescue_working_overrides,
    _resolve_checkpoint_authoritative_candidate,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.selection import load_json
from booktrans.production_worker.unit_store import (
    load_raw_by_uid,
    load_stage,
    persist_stage_once,
)
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs, providers_from_environment
from booktrans.providers.qwen_max import ReviewResult
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.router import route_semantic_unit
from booktrans.production_worker.artifacts import write_json_once


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH_LABEL = "FULL_BOOK_BATCH_023"
BATCH_DIR = RUN / "full_book_batch_023"

# This is a closed-world classification for the exact identities discovered
# from B023's sealed debt/triage evidence.  A missing or extra identity is a
# fail-closed condition, never a guess or a scan of historical runs.
SAFE_AS_IS = frozenset({"body.p.07226", "body.p.07375", "body.p.07440"})
DETERMINISTIC = frozenset({"body.p.07323", "body.p.07325", "textbox.154.p.0002"})
FLASH = frozenset({
    "body.p.07188", "body.p.07189", "body.p.07248", "body.p.07249",
    "body.p.07394", "textbox.152.p.0005", "body.p.07439",
})
TRUE_HUMAN = frozenset({"textbox.152.p.0002"})
EXPECTED_IDS = SAFE_AS_IS | DETERMINISTIC | FLASH | TRUE_HUMAN
_INPUTS_CACHE: dict[str, dict[str, Any]] = {}


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"RELEASE_EVIDENCE_NOT_OBJECT:{path}")
    return value


def _checkpoint_dirs() -> list[tuple[str, Path]]:
    manifest = load_json(BATCH_DIR / "batch_manifest.json")
    if (
        manifest.get("batch") != BATCH_LABEL
        or manifest.get("status") != "COMPLETE"
        or manifest.get("completed_count") != 200
        or manifest.get("next_checkpoint") is not None
    ):
        raise RuntimeError("B023_NOT_COMPLETE")
    result: list[tuple[str, Path]] = []
    for ordinal, item in enumerate(manifest.get("checkpoints") or [], start=1):
        if not isinstance(item, Mapping) or item.get("id") != f"checkpoint_{ordinal:02d}":
            raise RuntimeError("B023_CHECKPOINT_SEQUENCE_INVALID")
        checkpoint = str(item["id"])
        directory = BATCH_DIR / checkpoint
        if not (directory / "checkpoint_evidence.json").is_file():
            raise RuntimeError(f"B023_CHECKPOINT_NOT_SEALED:{checkpoint}")
        result.append((checkpoint, directory))
    if len(result) != 8:
        raise RuntimeError("B023_CHECKPOINT_COUNT_INVALID")
    return result


def _history(checkpoint_dir: Path, display_id: str) -> list[dict[str, str]]:
    """Only same-checkpoint, same-unit immutable evidence may inform release."""
    paths = sorted(checkpoint_dir.rglob(f"{display_id}.json"), key=lambda item: str(item).casefold())
    return [
        {"path": str(path), "sha256": _sha(path), "stage": path.parent.name}
        for path in paths
        if path.parent.name not in {"release_boundary_repairs", "release_boundary_final_reviews", "release_boundary_adjudications"}
    ]


def _classify(display_id: str) -> str:
    if display_id in SAFE_AS_IS:
        return "SAFE_AS_IS"
    if display_id in DETERMINISTIC:
        return "DETERMINISTIC_RESOLVABLE"
    if display_id in FLASH:
        return "FLASH_RESOLVABLE"
    if display_id in TRUE_HUMAN:
        return "TRUE_HUMAN_REQUIRED"
    raise RuntimeError(f"RELEASE_DEBT_CLASSIFICATION_MISSING:{display_id}")


def _current_head_already_resolves_deterministic(display_id: str, candidate: TranslationResult) -> dict[str, Any] | None:
    if display_id == "body.p.07323" and "模式或符号。" in candidate.target_segments[0].text:
        return {"rule": "CONTINUATION_TERMINAL_PERIOD_ALREADY_PRESENT", "segment_index": 0}
    if display_id == "textbox.154.p.0002":
        texts = [segment.text for segment in candidate.target_segments]
        if "每天" not in texts[0] and len(texts) > 1 and texts[1] == "每天":
            return {"rule": "CROSS_SEGMENT_DUPLICATE_ALREADY_ABSENT", "retained_segment_index": 1}
    return None


def _effective_adjudication(checkpoint_dir: Path, display_id: str) -> Mapping[str, Any] | None:
    return (
        load_stage(checkpoint_dir, "release_boundary_adjudication_supersessions", display_id)
        or load_stage(checkpoint_dir, "release_boundary_adjudications", display_id)
    )


def _versioned_path(stem: str) -> Path:
    versions: list[int] = []
    pattern = re.compile(rf"{re.escape(stem)}_v(\d+)\.json$")
    for path in BATCH_DIR.glob(f"{stem}_v*.json"):
        match = pattern.fullmatch(path.name)
        if match:
            versions.append(int(match.group(1)))
    return BATCH_DIR / f"{stem}_v{max(versions, default=0) + 1}.json"


def _collect_debts() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for checkpoint, directory in _checkpoint_dirs():
        for stage in ("resolution_queue_deferred_debts", "human_required_triage"):
            folder = directory / stage
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json"), key=lambda item: item.name.casefold()):
                record = _read(path)
                debt = stage == "resolution_queue_deferred_debts" and record.get("resolution_class") == "DEFERRED_SEMANTIC_DEBT"
                human = stage == "human_required_triage" and record.get("classification") == "TRUE_SEMANTIC_HUMAN"
                if not (debt or human):
                    continue
                display_id = str(record.get("logical_work_order_id") or record.get("display_id") or "")
                if not display_id:
                    raise RuntimeError(f"RELEASE_DEBT_ID_MISSING:{path}")
                item = entries.setdefault(display_id, {"checkpoint": checkpoint, "evidence": []})
                if item["checkpoint"] != checkpoint:
                    raise RuntimeError(f"RELEASE_DEBT_CROSS_CHECKPOINT_COLLISION:{display_id}")
                item["evidence"].append({"stage": stage, "path": str(path), "sha256": _sha(path), "record": record})
    if set(entries) != EXPECTED_IDS:
        raise RuntimeError(f"B023_RELEASE_DEBT_SET_MISMATCH:{sorted(entries)}")
    return entries


def _loaded_unit(checkpoint: str, display_id: str):
    # Loading the immutable IR/view catalog is expensive but purely read-only;
    # cache only the exact checkpoint slice, never a cross-run selection.
    loaded = _INPUTS_CACHE.get(checkpoint)
    if loaded is None:
        loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH_LABEL, checkpoint=checkpoint)
        _INPUTS_CACHE[checkpoint] = loaded
    request = next((item for item in loaded["requests"] if item.display_id == display_id), None)
    if request is None:
        raise RuntimeError(f"RELEASE_REQUEST_MISSING:{display_id}")
    checkpoint_dir = BATCH_DIR / checkpoint
    raw = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl").get(request.unit_uid)
    if raw is None:
        raise RuntimeError(f"RELEASE_RAW_MISSING:{display_id}")
    return loaded, request, checkpoint_dir, raw


def _current_facts(checkpoint: str, display_id: str) -> dict[str, Any]:
    loaded, request, checkpoint_dir, raw = _loaded_unit(checkpoint, display_id)
    head = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, display_id, raw)
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    equivalence = _d051_equivalence_for(checkpoint_dir, request, head.candidate, bible=bible)
    qa = _qa_with_d051(harness_qa_record, loaded["views"][display_id], request, head.candidate, loaded["known_unit_ids"], equivalence)
    # This one sealed B023 debt has a historical, exact D050 repair whose
    # output was legacy-unbound.  The current RAW head therefore correctly
    # fails only its visible marker check.  Release may repair from that RAW
    # head only when the new closed-world contract restores the marker and the
    # semantic qualifier together; every other current-Core failure is closed.
    allowed_d050_recovery = (
        display_id == "body.p.07248"
        and {str(item.get("code")) for item in qa.get("diagnostics", [])} == {"NUMBER_PRESERVATION"}
    )
    if not qa.get("passed") and not allowed_d050_recovery:
        raise RuntimeError(f"RELEASE_CURRENT_CORE_QA_FAILED:{display_id}")
    return {
        "loaded": loaded, "request": request, "checkpoint_dir": checkpoint_dir, "raw": raw,
        "head": head, "bible": bible, "qa": qa, "d050_recovery_required": allowed_d050_recovery,
    }


def _deterministic_candidate(display_id: str, base: TranslationResult) -> tuple[TranslationResult, dict[str, Any]]:
    segments = list(base.target_segments)
    if display_id == "body.p.07323":
        text = segments[0].text
        old, new = "模式或符号在", "模式或符号。在"
        if text.count(old) != 1:
            raise RuntimeError("DETERMINISTIC_PATCH_PRECONDITION_FAILED:body.p.07323")
        segments[0] = segments[0].model_copy(update={"text": text.replace(old, new, 1)})
        proof = {"rule": "CONTINUATION_TERMINAL_PERIOD", "segment_index": 0, "old": old, "new": new}
    elif display_id == "body.p.07325":
        if not segments[0].text.endswith(" ") or not segments[1].text.startswith("第 1 章"):
            raise RuntimeError("DETERMINISTIC_PATCH_PRECONDITION_FAILED:body.p.07325")
        old = segments[0].text
        segments[0] = segments[0].model_copy(update={"text": old.rstrip(" ")})
        proof = {"rule": "CONTINUATION_SPACE_REMOVAL", "segment_index": 0, "old": old, "new": segments[0].text}
    elif display_id == "textbox.154.p.0002":
        text = segments[0].text
        old, new = "我们每天持续生成", "我们持续生成"
        if text.count(old) != 1 or len(segments) < 2 or segments[1].text != "每天":
            raise RuntimeError("DETERMINISTIC_PATCH_PRECONDITION_FAILED:textbox.154.p.0002")
        corrected = text.replace(old, new, 1)
        segments[0] = segments[0].model_copy(update={"text": corrected})
        proof = {"rule": "CROSS_SEGMENT_DUPLICATE_TOKEN_REMOVAL", "segment_index": 0, "old": old, "new": new}
    else:
        raise RuntimeError(f"DETERMINISTIC_PATCH_NOT_DEFINED:{display_id}")
    return base.model_copy(update={"target_segments": segments}), proof


def _repair_context(facts: Mapping[str, Any], candidate: TranslationResult):
    loaded, request, checkpoint_dir, raw = facts["loaded"], facts["request"], facts["checkpoint_dir"], facts["raw"]
    overrides = _rescue_working_overrides(checkpoint_dir, loaded, load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl"))
    overrides[request.display_id] = candidate
    overrides[request.unit_uid] = candidate
    group = _production_review_group(
        request, loaded=loaded, run=RUN, checkpoint=checkpoint_dir.name,
        existing_raw=load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl"), candidate_overrides=overrides,
    )
    return None if group is None else group.as_reviewer_context_payload()


def _review_and_commit(*, facts: Mapping[str, Any], candidate: TranslationResult, mode: str, repair_record: Mapping[str, Any], providers) -> dict[str, Any]:
    request, raw, checkpoint_dir = facts["request"], facts["raw"], facts["checkpoint_dir"]
    parent = facts["head"].candidate_sha256
    require_parent_is_current(artifact="b023_release_boundary", parent_candidate_sha256=parent, current_candidate_sha256=facts["head"].candidate_sha256)
    if _candidate_binding(candidate) == parent:
        raise RuntimeError(f"RELEASE_REPAIR_NO_MUTATION:{request.display_id}")
    completeness = __import__("booktrans.production_worker.production_checkpoint", fromlist=["_repair_clause_coverage"])._repair_clause_coverage(request, facts["head"].candidate, candidate)
    if not completeness.get("passed"):
        raise RuntimeError(f"RELEASE_REPAIR_COMPLETENESS_FAILED:{request.display_id}")
    equivalence = _d051_equivalence_for(checkpoint_dir, request, candidate, bible=facts["bible"])
    qa = _qa_with_d051(harness_qa_record, facts["loaded"]["views"][request.display_id], request, candidate, facts["loaded"]["known_unit_ids"], equivalence)
    if not qa.get("passed"):
        raise RuntimeError(f"RELEASE_REPAIR_CORE_QA_FAILED:{request.display_id}")
    context = _repair_context(facts, candidate)
    provider_request = enrich_request(request, translation_bible=facts["bible"])
    before = [(item.text, item.kind, item.anchor_ref, item.island_ref) for item in candidate.target_segments]
    reviewed = providers.reviewer.review_with_provenance(provider_request, candidate, deterministic_qa_summary={"passed": True}, semantic_context=context)
    after = [(item.text, item.kind, item.anchor_ref, item.island_ref) for item in candidate.target_segments]
    if before != after:
        raise RuntimeError("RELEASE_REVIEWER_MUTATED_CANDIDATE")
    final = reviewed.review
    final_record = {
        "artifact_type": "B023_RELEASE_BOUNDARY_FINAL_REVIEW", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": parent, "candidate_binding_sha256": _candidate_binding(candidate),
        "request_binding_sha256": provider_request.request_binding_sha256, "mode": mode,
        "semantic_context": context, "review": final.model_dump(),
        "provider": getattr(providers.reviewer, "provider_name", type(providers.reviewer).__name__),
        "model": getattr(providers.reviewer, "model", None), "usage": dict(reviewed.candidate.usage),
        "translation_api_calls": 0,
    }
    persist_stage_once(checkpoint_dir, "release_boundary_final_reviews", request.display_id, final_record)
    if final.verdict != "ACCEPT" or final.issues:
        return {"status": "TRUE_HUMAN_REQUIRED", "qa": qa, "completeness": completeness, "final": final_record}
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=candidate, qa=qa)
    outcome.final_status = "ACCEPT"
    outcome.semantic_terminal = "MODEL_ACCEPTED"
    outcome.repair_applied = True
    outcome.repair_policy_id = f"B023_RELEASE_{mode}"
    _commit_accepted_candidate_mutation(
        checkpoint_dir=checkpoint_dir, request=request, raw=raw, outcome=outcome,
        source_sha256=facts["loaded"]["source_sha256"], selection_sha256=facts["loaded"]["selection_sha256"], bible=facts["bible"],
    )
    resolved = _resolve_checkpoint_authoritative_candidate(checkpoint_dir, request.display_id, raw)
    if resolved.candidate_sha256 != _candidate_binding(candidate):
        raise CandidateAuthorityError("RELEASE_ACCEPTED_CANDIDATE_NOT_CURRENT_HEAD")
    return {"status": "AUTO_RESOLVED", "qa": qa, "completeness": completeness, "final": final_record, "repair": repair_record}


def _run_auto(display_id: str, facts: Mapping[str, Any], source_record: Mapping[str, Any], providers) -> dict[str, Any]:
    request, checkpoint_dir, base = facts["request"], facts["checkpoint_dir"], facts["head"].candidate
    mode = _classify(display_id)
    saved = load_stage(checkpoint_dir, "release_boundary_repairs", display_id)
    if saved is not None:
        if (
            saved.get("parent_candidate_sha256") != facts["head"].candidate_sha256
            or not isinstance(saved.get("working"), Mapping)
        ):
            raise CandidateAuthorityError(f"RELEASE_REPAIR_BINDING_MISMATCH:{display_id}")
        candidate = TranslationResult.model_validate(saved["working"])
        if saved.get("candidate_binding_sha256") != _candidate_binding(candidate):
            raise CandidateAuthorityError(f"RELEASE_REPAIR_CANDIDATE_SHA_MISMATCH:{display_id}")
        repair_record = saved
    elif mode == "DETERMINISTIC_RESOLVABLE":
        candidate, proof = _deterministic_candidate(display_id, base)
        repair_record = {
            "artifact_type": "B023_RELEASE_BOUNDARY_DETERMINISTIC_REPAIR", "schema_version": 1,
            "display_id": display_id, "unit_uid": request.unit_uid, "mode": mode,
            "parent_candidate_sha256": facts["head"].candidate_sha256, "authority_parent_verified": True,
            "candidate_binding_sha256": _candidate_binding(candidate), "working": candidate.model_dump(),
            "proof": proof, "translation_api_calls": 0,
        }
    else:
        review_payload = source_record.get("review")
        if not isinstance(review_payload, Mapping):
            raise RuntimeError(f"RELEASE_FLASH_REVIEW_MISSING:{display_id}")
        review = ReviewResult.model_validate(review_payload)
        provider_request = enrich_request(request, translation_bible=facts["bible"])
        gate = decide_approved_repair_contract(provider_request, base, review=review, allow_semantic_narrow=True)
        if gate.action != "REPAIR" or gate.contract is None:
            raise RuntimeError(f"RELEASE_FLASH_CONTRACT_REJECTED:{display_id}")
        if display_id == "body.p.07248":
            gate_contract = gate.contract.model_copy(update={
                "repair_instruction": (
                    f"{gate.contract.repair_instruction}\n\n"
                    "Also restore the exact verified visible D050 marker '19' immediately after "
                    "'识别出' in the same permitted segment. Preserve all other markers, "
                    "numbers, anchors, protected values, URLs, and source clauses."
                ),
            })
        else:
            gate_contract = gate.contract
        repaired_with_provenance = providers.repair.repair_with_provenance(provider_request, base, gate_contract)
        candidate = repaired_with_provenance.repaired
        repair_record = {
            "artifact_type": "B023_RELEASE_BOUNDARY_FLASH_REPAIR", "schema_version": 1,
            "display_id": display_id, "unit_uid": request.unit_uid, "mode": mode,
            "parent_candidate_sha256": facts["head"].candidate_sha256, "authority_parent_verified": True,
            "candidate_binding_sha256": _candidate_binding(candidate), "working": candidate.model_dump(),
            "contract": gate_contract.model_dump(), "provider": repaired_with_provenance.candidate.provider,
            "model": repaired_with_provenance.candidate.model, "usage": dict(repaired_with_provenance.candidate.usage),
            "request_binding_sha256": repaired_with_provenance.candidate.request_binding_sha256, "translation_api_calls": 0,
        }
    if saved is None:
        persist_stage_once(checkpoint_dir, "release_boundary_repairs", display_id, repair_record)
    return _review_and_commit(facts=facts, candidate=candidate, mode=mode, repair_record=repair_record, providers=providers)


def _adjudication_record(*, facts: Mapping[str, Any], entry: Mapping[str, Any], status: str, detail: Mapping[str, Any]) -> dict[str, Any]:
    request, head = facts["request"], facts["head"]
    return {
        "artifact_type": "B023_RELEASE_BOUNDARY_ADJUDICATION", "schema_version": 1,
        "logical_work_order_id": request.display_id, "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256,
        "request_binding_sha256": enrich_request(request, translation_bible=facts["bible"]).request_binding_sha256,
        "current_authoritative_candidate_sha256": head.candidate_sha256,
        "current_candidate": head.candidate.model_dump(), "candidate_lineage": list(head.lineage),
        "historical_evidence": entry["evidence"], "history": _history(facts["checkpoint_dir"], request.display_id),
        "release_classification": _classify(request.display_id), "release_status": status,
        "core_harness": facts["qa"], "detail": dict(detail), "translation_api_calls": 0,
    }


def _source_record(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    for evidence in entry["evidence"]:
        record = evidence["record"]
        if isinstance(record.get("review"), Mapping):
            return record
    raise RuntimeError("RELEASE_REPAIR_SOURCE_REVIEW_MISSING")


def _human_packet_item(facts: Mapping[str, Any]) -> dict[str, Any]:
    request, candidate = facts["request"], facts["head"].candidate
    if request.display_id != "textbox.152.p.0002":
        final = load_stage(facts["checkpoint_dir"], "release_boundary_final_reviews", request.display_id) or {}
        review = final.get("review") if isinstance(final.get("review"), Mapping) else {}
        if not review:
            historical = load_stage(facts["checkpoint_dir"], "resolution_queue_deferred_debts", request.display_id) or {}
            review = historical.get("review") if isinstance(historical.get("review"), Mapping) else {}
        issues = review.get("issues") if isinstance(review.get("issues"), list) else []
        issue = issues[0] if issues and isinstance(issues[0], Mapping) else {}
        repair = load_stage(facts["checkpoint_dir"], "release_boundary_repairs", request.display_id) or {}
        repaired = repair.get("working") if isinstance(repair.get("working"), Mapping) else None
        return {
            "logical_work_order_id": request.display_id,
            "unit_uid": request.unit_uid,
            "source": "".join(segment.text for segment in request.source_segments),
            "current_target": "".join(segment.text for segment in candidate.target_segments),
            "exact_disputed_phrase": issue.get("repair_instruction") or issue.get("reason") or "bounded repair not accepted",
            "why_machine_cannot_decide": "One exact-scope Flash repair and its final bound review did not yield an ACCEPT terminal. The retry budget is exhausted; selecting either candidate would require an editorial semantic judgment.",
            "candidate_choices": [
                "Keep the current authoritative candidate.",
                "Adopt the bounded repair candidate only after an editor verifies the disputed phrase.",
            ],
            "repair_candidate": repaired,
            "evidence_context_required": "The bound release repair, Core QA result, and final Reviewer evidence for this unit.",
        }
    return {
        "logical_work_order_id": request.display_id,
        "unit_uid": request.unit_uid,
        "source": "".join(segment.text for segment in request.source_segments),
        "current_target": "".join(segment.text for segment in candidate.target_segments),
        "exact_disputed_phrase": "at the same",
        "why_machine_cannot_decide": "The authoritative DOCX/IR source span stops mid-phrase. Completing it would add text absent from the source; truncating it changes the current target. Existing historical triage confirms no pipeline-context gap and an exhausted bounded repair budget.",
        "candidate_choices": [
            "Preserve the current completion only if an editor supplies source-authoritative evidence for the missing continuation.",
            "Revise to an explicitly truncated Chinese fragment only if the release editor confirms that the DOCX span is the intended source boundary.",
        ],
        "evidence_context_required": "The authoritative DOCX/IR span plus a human editorial decision on whether the source is incomplete; PDF is corroborative only.",
    }


def run(*, apply: bool) -> dict[str, Any]:
    debts = _collect_debts()
    providers = providers_from_environment() if apply else None
    final_entries: list[dict[str, Any]] = []
    human_packet: list[dict[str, Any]] = []
    for display_id in sorted(debts):
        entry = debts[display_id]
        facts = _current_facts(entry["checkpoint"], display_id)
        classification = _classify(display_id)
        if not apply:
            final_entries.append({"logical_work_order_id": display_id, "checkpoint": entry["checkpoint"], "classification": classification, "current_candidate_sha256": facts["head"].candidate_sha256})
            continue
        prior_adjudication = load_stage(facts["checkpoint_dir"], "release_boundary_adjudications", display_id)
        existing_adjudication = _effective_adjudication(facts["checkpoint_dir"], display_id)
        existing_supersession = load_stage(facts["checkpoint_dir"], "release_boundary_adjudication_supersessions", display_id)
        human_adjudication_path = facts["checkpoint_dir"] / "human_adjudications" / f"{display_id}.json"
        human_candidate_path = facts["checkpoint_dir"] / "human_approved_candidates" / f"{display_id}.json"
        # This is the sole release-consumption path for local HUMAN_APPROVED
        # evidence.  It validates the exact debt, batch, request/source
        # identity, parent head, candidate binding, and create-once topology;
        # the runner intentionally does not duplicate that precedence logic.
        if human_adjudication_path.exists() or human_candidate_path.exists():
            if not isinstance(existing_adjudication, Mapping):
                raise RuntimeError(f"B023_HUMAN_RELEASE_DEBT_CONTEXT_MISSING:{display_id}")
            try:
                human_consumption = consume_human_approval_for_release(
                    checkpoint_dir=facts["checkpoint_dir"],
                    batch_label=BATCH_LABEL,
                    logical_work_order_id=display_id,
                    unit_uid=facts["request"].unit_uid,
                    source_sha256=facts["request"].source_sha256,
                    request_binding_sha256=enrich_request(
                        facts["request"], translation_bible=facts["bible"]
                    ).request_binding_sha256,
                    release_debt=existing_adjudication,
                    current_head=facts["head"],
                )
            except HumanReleaseConsumerError as error:
                raise RuntimeError(f"B023_HUMAN_RELEASE_CONSUMER_REJECTED:{display_id}:{error}") from error
            if human_consumption is not None:
                final_entries.append({
                    "logical_work_order_id": display_id,
                    "checkpoint": entry["checkpoint"],
                    "classification": classification,
                    "release_status": human_consumption.status,
                    "current_authoritative_candidate_sha256": facts["head"].candidate_sha256,
                    "human_release_consumer": {
                        "approval_mode": human_consumption.approval_mode,
                        "status": human_consumption.status,
                    },
                })
                continue
        if existing_supersession is not None:
            final_entries.append({
                "logical_work_order_id": display_id, "checkpoint": entry["checkpoint"], "classification": classification,
                "release_status": existing_supersession.get("release_status"),
                "current_authoritative_candidate_sha256": facts["head"].candidate_sha256,
                "adjudication_path": str(facts["checkpoint_dir"] / "release_boundary_adjudication_supersessions" / f"{display_id}.json"),
            })
            if existing_supersession.get("release_status") == "TRUE_HUMAN_REQUIRED":
                human_packet.append(_human_packet_item(facts))
            continue
        already_current = _current_head_already_resolves_deterministic(display_id, facts["head"].candidate)
        if classification == "DETERMINISTIC_RESOLVABLE" and already_current is not None and load_stage(facts["checkpoint_dir"], "release_boundary_adjudication_supersessions", display_id) is None:
            if prior_adjudication is None:
                raise RuntimeError(f"RELEASE_ADJUDICATION_SUPERSESSION_BASE_MISSING:{display_id}")
            supersession = _adjudication_record(
                facts=facts, entry=entry, status="AUTO_RESOLVED",
                detail={
                    "resolution": "CURRENT_AUTHORITATIVE_HEAD_ALREADY_SATISFIES_EXACT_DETERMINISTIC_REQUIREMENT",
                    "proof": already_current,
                    "supersedes_path": str(facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json"),
                    "supersedes_sha256": _sha(facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json"),
                },
            )
            supersession["artifact_type"] = "B023_RELEASE_BOUNDARY_ADJUDICATION_SUPERSESSION"
            persist_stage_once(facts["checkpoint_dir"], "release_boundary_adjudication_supersessions", display_id, supersession)
            existing_adjudication = supersession
        if classification == "DETERMINISTIC_RESOLVABLE" and already_current is not None and existing_adjudication is not None:
            final_entries.append({
                "logical_work_order_id": display_id, "checkpoint": entry["checkpoint"], "classification": classification,
                "release_status": existing_adjudication.get("release_status"),
                "current_authoritative_candidate_sha256": facts["head"].candidate_sha256,
                "adjudication_path": str(facts["checkpoint_dir"] / "release_boundary_adjudication_supersessions" / f"{display_id}.json"),
            })
            continue
        if existing_adjudication is not None and classification != "DETERMINISTIC_RESOLVABLE":
            final_entries.append({
                "logical_work_order_id": display_id, "checkpoint": entry["checkpoint"], "classification": classification,
                "release_status": existing_adjudication.get("release_status"),
                "current_authoritative_candidate_sha256": facts["head"].candidate_sha256,
                "adjudication_path": str(facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json"),
            })
            if existing_adjudication.get("release_status") == "TRUE_HUMAN_REQUIRED":
                human_packet.append(_human_packet_item(facts))
            continue
        if classification == "SAFE_AS_IS":
            detail: dict[str, Any] = {"reason": "Historical issue is limited to register, naturalness, or punctuation/spacing; current Core Harness and candidate authority pass."}
            status = "SAFE_AS_IS"
        elif classification == "TRUE_HUMAN_REQUIRED":
            detail = {"reason": "Closed-world source span is incomplete; no automatic mutation is safe."}
            status = "TRUE_HUMAN_REQUIRED"
            human_packet.append(_human_packet_item(facts))
        else:
            try:
                source_record = _source_record(entry) if classification == "FLASH_RESOLVABLE" else {}
                result = _run_auto(display_id, facts, source_record, providers)
                status = str(result["status"])
                detail = result
            except (CandidateAuthorityError, RuntimeError) as error:
                # A bounded candidate that cannot clear current-head QA or its
                # final Reviewer is immutable rejected evidence, not a retry
                # budget.  It proceeds only to the compact human packet.
                status = "TRUE_HUMAN_REQUIRED"
                detail = {"failure_code": str(error), "bounded_attempt_exhausted": True}
                human_packet.append(_human_packet_item(facts))
            # Accepted mutation changes the only candidate head; reload facts
            # before persisting release adjudication and final manifest.
            facts = _current_facts(entry["checkpoint"], display_id)
        record = _adjudication_record(facts=facts, entry=entry, status=status, detail=detail)
        if prior_adjudication is not None:
            record["artifact_type"] = "B023_RELEASE_BOUNDARY_ADJUDICATION_SUPERSESSION"
            record["supersedes_path"] = str(facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json")
            record["supersedes_sha256"] = _sha(facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json")
            persist_stage_once(facts["checkpoint_dir"], "release_boundary_adjudication_supersessions", display_id, record)
        else:
            persist_stage_once(facts["checkpoint_dir"], "release_boundary_adjudications", display_id, record)
        final_entries.append({
            "logical_work_order_id": display_id, "checkpoint": entry["checkpoint"], "classification": classification,
            "release_status": status, "current_authoritative_candidate_sha256": facts["head"].candidate_sha256,
            "adjudication_path": str(facts["checkpoint_dir"] / "release_boundary_adjudications" / f"{display_id}.json"),
        })
    if not apply:
        return {"mode": "DRY_RUN", "release_debt_total": len(final_entries), "entries": final_entries}
    manifest_path = _versioned_path("b023_release_debt_manifest")
    manifest = {
        "artifact_type": "B023_RELEASE_DEBT_MANIFEST", "schema_version": 1, "batch": BATCH_LABEL,
        "source_sha256": _read(BATCH_DIR / "batch_manifest.json")["source_sha256"],
        "release_debt_total": len(final_entries), "entries": final_entries,
        "authority_scope": "B023 sealed checkpoint evidence and current authoritative candidate resolver only",
        "translation_api_calls": 0,
    }
    write_json_once(manifest_path, manifest)
    if human_packet:
        write_json_once(_versioned_path("b023_human_release_packet"), {
            "artifact_type": "B023_HUMAN_RELEASE_PACKET", "schema_version": 1, "batch": BATCH_LABEL,
            "source_sha256": manifest["source_sha256"], "items": human_packet,
        })
    statuses = {str(item["release_status"]) for item in final_entries}
    gate_pass = statuses <= {"AUTO_RESOLVED", "SAFE_AS_IS", "RESOLVED_FOR_RELEASE"}
    gate = {
        "artifact_type": "B023_RELEASE_GATE", "schema_version": 1, "batch": BATCH_LABEL,
        "release_debt_manifest": str(manifest_path), "release_debt_manifest_sha256": _sha(manifest_path),
        "all_deferred_debt_resolved": gate_pass, "all_historical_human_adjudicated": not human_packet,
        "integrity_pass": True, "candidate_authority_pass": True,
        "verdict": "PASS" if gate_pass else "BLOCKED_HUMAN_ADJUDICATION_REQUIRED",
        "unresolved_ids": [item["logical_work_order_id"] for item in final_entries if item["release_status"] == "TRUE_HUMAN_REQUIRED"],
        "translation_api_calls": 0,
    }
    write_json_once(_versioned_path("b023_release_gate"), gate)
    return {"mode": "APPLY", "manifest": manifest, "gate": gate, "human_packet_count": len(human_packet)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorize-release-boundary-write", action="store_true")
    args = parser.parse_args()
    if args.apply and not args.authorize_release_boundary_write:
        raise RuntimeError("RELEASE_BOUNDARY_WRITE_NOT_AUTHORIZED")
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
