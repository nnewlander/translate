"""Apply the owner-approved B029 release correction for body.p.08974.

The decision is CAS-bound to the current Candidate Authority head.  This
script uses the existing Human adjudication transaction and makes no provider
calls; its writes are append-only Human decision and candidate evidence.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from booktrans.production_worker.artifacts import write_json_once
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.effective_source import resolve_effective_source
from booktrans.production_worker.human_adjudication import (
    HumanAllowedRepair,
    HumanApprovedPatch,
    apply_human_adjudication_to_production_checkpoint,
    build_human_adjudication,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import (
    _rescue_working_overrides,
    _resolve_checkpoint_authoritative_candidate,
    build_current_review_group,
)
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, load_stage
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = "FULL_BOOK_BATCH_029"
BATCH_DIR = RUN / BATCH.lower()
DECISION_PATH = BATCH_DIR / "b029_human_adjudication_decision_08974_v1.json"
REPORT_PATH = BATCH_DIR / "b029_human_adjudication_apply_report_08974_v1.json"

DECISION = {
    "artifact_type": "B029_HUMAN_ADJUDICATION_DECISION",
    "schema_version": 1,
    "batch": BATCH,
    "work_order_id": "body.p.08974",
    "checkpoint": "checkpoint_03",
    "action": "REPLACE",
    "expected_current_head": "5c51c20d0c377c455b4115f7ace0307bee681cd19a068e9633d893496ed1dc81",
    "target_segments": ["将类中的所有成员替换为测试替身，", "265", "测试范围和，", "219"],
    "reason": "Owner-approved final B029 release correction.",
}


def _write_decision() -> dict:
    if DECISION_PATH.is_file():
        if json.loads(DECISION_PATH.read_text(encoding="utf-8")) != DECISION:
            raise RuntimeError("B029_HUMAN_DECISION_ARTIFACT_CONFLICT")
    else:
        write_json_once(DECISION_PATH, DECISION)
    return json.loads(DECISION_PATH.read_text(encoding="utf-8"))


def _apply(decision: dict) -> dict:
    display_id, checkpoint = decision["work_order_id"], decision["checkpoint"]
    directory = BATCH_DIR / checkpoint
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
    requests = {request.display_id: request for request in loaded["requests"]}
    request = requests.get(display_id)
    if request is None:
        raise RuntimeError("B029_HUMAN_REQUEST_MISSING")
    raws = load_raw_by_uid(directory / "translation_raw_response.jsonl")
    raw = raws.get(request.unit_uid)
    if raw is None:
        raise RuntimeError("B029_HUMAN_RAW_MISSING")
    head = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    expected_head = decision["expected_current_head"]
    if head.candidate_sha256 != expected_head:
        raise RuntimeError(f"B029_HUMAN_CURRENT_HEAD_STALE:{head.candidate_sha256}")
    if load_stage(directory, "human_adjudications", display_id) is not None:
        raise RuntimeError("B029_HUMAN_ALREADY_APPLIED")

    effective = resolve_effective_source(checkpoint_dir=directory, request=request).request
    overrides = _rescue_working_overrides(directory, loaded, raws)
    group = build_current_review_group(
        effective, loaded=loaded, run=RUN, checkpoint=checkpoint,
        existing_raw=raws, candidate_overrides=overrides,
    )
    if group is None or list(group.member_work_order_ids) != [display_id]:
        raise RuntimeError("B029_HUMAN_CURRENT_GROUP_UNSAFE")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    provider_request = enrich_request(effective, translation_bible=bible)
    target_texts = decision["target_segments"]
    if len(target_texts) != len(effective.source_segments) or any(not text.strip() for text in target_texts):
        raise RuntimeError("B029_HUMAN_TARGET_CONTRACT_INVALID")
    adjudication = build_human_adjudication(
        group, decision="HUMAN_APPROVED", reason=decision["reason"],
        allowed_repairs=[HumanAllowedRepair(
            logical_work_order_id=display_id, unit_uid=effective.unit_uid,
            allowed_segment_indices=list(range(len(target_texts))),
            source_fingerprint=provider_request.request_binding_sha256,
        )],
        source_hashes={display_id: provider_request.request_binding_sha256},
        original_candidate_hashes={display_id: candidate_sha(raw)},
        expected_current_heads={display_id: expected_head},
        approved_patches=[HumanApprovedPatch(
            logical_work_order_id=display_id, unit_uid=effective.unit_uid, index=index, target_text=text,
        ) for index, text in enumerate(target_texts)],
    )
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"],
        views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record,
        adjudication=adjudication, group=group, members=[(provider_request, head.candidate)],
        translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
        batch_label=BATCH, checkpoint=checkpoint,
    )
    refreshed = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    if refreshed.candidate_sha256 == expected_head:
        raise RuntimeError("B029_HUMAN_MUTATION_NOT_AUTHORITATIVE")
    actual_targets = [segment.text for segment in refreshed.candidate.target_segments]
    if actual_targets != target_texts:
        raise RuntimeError("B029_HUMAN_CURRENT_CONTENT_MISMATCH")
    if not all(record.get("passed") for record in result["qa"].values()):
        raise RuntimeError("B029_HUMAN_QA_NOT_PASS")
    return {
        "work_order_id": display_id,
        "checkpoint": checkpoint,
        "status": "PASS",
        "parent_candidate_sha256": expected_head,
        "authoritative_candidate_sha256": refreshed.candidate_sha256,
        "target_segments": actual_targets,
        "core_qa_pass": True,
        "deterministic_qa_pass": True,
        "translation_api_calls": result["translation_api_calls"],
        "reviewer_api_calls": result["reviewer_api_calls"],
        "repair_api_calls": result["repair_api_calls"],
    }


def main() -> None:
    decision = _write_decision()
    report = {
        "artifact_type": "B029_HUMAN_ADJUDICATION_APPLY_REPORT",
        "schema_version": 1,
        "decision_artifact_path": str(DECISION_PATH),
        "decision_artifact_sha256": sha256(DECISION_PATH.read_bytes()).hexdigest(),
        "result": _apply(decision),
    }
    write_json_once(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
