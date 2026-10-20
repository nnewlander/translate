"""Apply only B026's two remaining owner-authorized Human decisions.

No provider calls.  RAW and prior Human evidence remain immutable.
"""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.calibration import ExplicitNumericRepresentationEquivalence
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair, HumanApprovedPatch, adjudication_from_mapping,
    apply_human_adjudication_to_production_checkpoint, build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _candidate_binding, _production_review_group, _qa_with_d051,
    _resolve_checkpoint_authoritative_candidate, _rescue_working_overrides,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, load_stage, persist_stage_once
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_026"
DECISIONS = Path(r"C:\Users\Lenovo\Downloads\b026_human_adjudication_decisions_v2.json")


def _loaded(checkpoint: str):
    directory = RUN / BATCH.lower() / checkpoint
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
    return directory, loaded, load_raw_by_uid(directory / "translation_raw_response.jsonl")


def _members(directory, loaded, raws, display_id):
    request = next(item for item in loaded["requests"] if item.display_id == display_id)
    group = _production_review_group(
        request, loaded=loaded, run=RUN, checkpoint=directory.name,
        existing_raw=raws, candidate_overrides=_rescue_working_overrides(directory, loaded, raws),
    )
    if group is None:
        raise RuntimeError(f"B026_FINAL_TWO_GROUP_MISSING:{display_id}")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    requests = {item.display_id: item for item in loaded["requests"]}
    members = []
    for member in group.members:
        member_request = requests[member.display_id]
        current = _resolve_checkpoint_authoritative_candidate(
            directory, member.display_id, raws[member_request.unit_uid]
        ).candidate
        members.append((enrich_request(member_request, translation_bible=bible), current))
    return request, group, members, bible


def _decision(display_id: str) -> dict:
    payload = json.loads(DECISIONS.read_text(encoding="utf-8"))
    return next(item for item in payload["decisions"] if item["work_order_id"] == display_id)


def _assert_live_head(directory, request, raws, decision):
    head = _resolve_checkpoint_authoritative_candidate(directory, request.display_id, raws[request.unit_uid])
    if head.candidate_sha256 != decision["expected_current_head"]:
        raise RuntimeError(f"B026_FINAL_TWO_STALE_DECISION:{request.display_id}:{head.candidate_sha256}")
    return head


def _apply_08235() -> dict:
    directory, loaded, raws = _loaded("checkpoint_04")
    decision = _decision("body.p.08235")
    request, group, members, bible = _members(directory, loaded, raws, request.display_id if False else "body.p.08235")
    head = _assert_live_head(directory, request, raws, decision)
    evidence = load_stage(directory, "human_adjudications", request.display_id)
    if evidence is None:
        raise RuntimeError("B026_FINAL_TWO_08235_DECISION_NOT_RECORDED")
    adjudication = adjudication_from_mapping(evidence)
    patches = [patch for patch in adjudication.approved_patches if patch.logical_work_order_id == request.display_id]
    if [patch.target_text for patch in patches] != decision["target_segments"]:
        raise RuntimeError("B026_FINAL_TWO_08235_RECORDED_DECISION_MISMATCH")
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"], views=loaded["views"],
        known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record, adjudication=adjudication,
        group=group, members=members, translation_bible=bible,
        triage_classification="TRUE_SEMANTIC_HUMAN", batch_label=BATCH, checkpoint=directory.name,
    )
    current = _resolve_checkpoint_authoritative_candidate(directory, request.display_id, raws[request.unit_uid])
    if current.candidate_sha256 == head.candidate_sha256:
        raise RuntimeError("B026_FINAL_TWO_08235_DESCENDANT_NOT_AUTHORITATIVE")
    return {"work_order": request.display_id, "parent": head.candidate_sha256, "head": current.candidate_sha256, "qa": result["qa"]}


def _d051_08252(request, candidate):
    equivalence = ExplicitNumericRepresentationEquivalence(
        unit_uid=request.unit_uid, exact_source_span="a thousand", source_numeric_tokens=[],
        approved_target_representation="1,000",
        reviewed_source_semantic_numeric_value="1000",
        reviewed_target_semantic_numeric_value="1000",
        semantic_unit="GOOGLE_COMPUTE_ENGINE_VMS",
        equivalence_kind="ENGLISH_CARDINAL_TO_LITERAL",
    )
    if request.source_text.count(equivalence.exact_source_span) != 1:
        raise RuntimeError("B026_FINAL_TWO_08252_D051_SOURCE_BINDING_MISMATCH")
    if equivalence.approved_target_representation not in "".join(item.text for item in candidate.target_segments):
        raise RuntimeError("B026_FINAL_TWO_08252_D051_TARGET_BINDING_MISMATCH")
    return equivalence


def _apply_08252() -> dict:
    directory, loaded, raws = _loaded("checkpoint_05")
    decision = _decision("body.p.08252")
    request, group, members, bible = _members(directory, loaded, raws, "body.p.08252")
    head = _assert_live_head(directory, request, raws, decision)
    provider = next(item for item, _ in members if item.request.display_id == request.display_id)
    targets = decision["target_segments"]
    intended = head.candidate.model_copy(update={"target_segments": [
        segment.model_copy(update={"text": targets[index]})
        for index, segment in enumerate(head.candidate.target_segments)
    ]})
    equivalence = _d051_08252(request, intended)
    persist_stage_once(directory, "d051_reviewed_equivalences", request.display_id, {
        "artifact_type": "D051_REVIEWED_EQUIVALENCE", "schema_version": 2,
        "decision_authority": "HUMAN_APPROVED_NUMERIC_REPRESENTATION",
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256,
        "request_binding_sha256": provider.request_binding_sha256,
        "candidate_binding_sha256": _candidate_binding(intended),
        "equivalence": equivalence.model_dump(), "translation_api_calls": 0,
    })
    adjudication = build_human_adjudication(
        group, decision="HUMAN_APPROVED", reason="Owner-approved B026 final release adjudication.",
        allowed_repairs=[HumanAllowedRepair(logical_work_order_id=request.display_id, unit_uid=request.unit_uid,
            allowed_segment_indices=list(range(len(targets))), source_fingerprint=provider.request_binding_sha256)],
        source_hashes={item.request.display_id: item.request_binding_sha256 for item, _ in members},
        original_candidate_hashes={item.request.display_id: candidate_sha(raws[item.request.unit_uid]) for item, _ in members},
        approved_patches=[HumanApprovedPatch(logical_work_order_id=request.display_id, unit_uid=request.unit_uid, index=i, target_text=value) for i, value in enumerate(targets)],
    )
    def qa(view, qa_request, working, known_unit_ids):
        return _qa_with_d051(harness_qa_record, view, qa_request, working, known_unit_ids, equivalence)
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"], views=loaded["views"],
        known_unit_ids=loaded["known_unit_ids"], qa_fn=qa, adjudication=adjudication, group=group,
        members=members, translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
        batch_label=BATCH, checkpoint=directory.name,
    )
    current = _resolve_checkpoint_authoritative_candidate(directory, request.display_id, raws[request.unit_uid])
    if current.candidate_sha256 == head.candidate_sha256:
        raise RuntimeError("B026_FINAL_TWO_08252_HUMAN_DESCENDANT_NOT_AUTHORITATIVE")
    return {"work_order": request.display_id, "parent": head.candidate_sha256, "head": current.candidate_sha256, "qa": result["qa"], "d051": equivalence.model_dump()}


if __name__ == "__main__":
    print(json.dumps([_apply_08235(), _apply_08252()], ensure_ascii=False, indent=2))
