"""Apply the owner-approved B036 release Human adjudication for footer.83.p.0001.

This is an append-only, exact-current-head CAS transaction.  It uses the
existing HumanSemanticAdjudication machinery and performs no provider calls.
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
BATCH = "FULL_BOOK_BATCH_036"
BATCH_DIR = RUN / BATCH.lower()
DECISION_PATH = BATCH_DIR / "b036_human_adjudication_decision_footer83_v1.json"
REPORT_PATH = BATCH_DIR / "b036_human_adjudication_apply_report_footer83_v1.json"
DECISION = {
    "artifact_type": "B036_HUMAN_ADJUDICATION_DECISION",
    "schema_version": 1,
    "batch": BATCH,
    "work_order_id": "footer.83.p.0001",
    "checkpoint": "checkpoint_08",
    "action": "REPLACE",
    "expected_current_head": "a863664cbe90edece22a16d060e80dce15f3a867faa90eff228bb55077ce7dce",
    "target_text": "尽管你可能是推动团队达成共识并帮助明确方向的人，但如何实现这些目标的具体实施细节，最好还是由那些实际构建产品的人来决定。这不仅会让他们产生更强的感受",
    "approved_patch_indices": [0],
    "reason": "Owner release-boundary semantic adjudication: restore only the source-present noun 'sense', preserve the source truncation, and do not infer unprovided ownership, purpose, or responsibility semantics.",
    "binding_rule": "Exact-current-head CAS; append-only HumanSemanticAdjudication; no provider invocation.",
}


def main() -> None:
    if DECISION_PATH.is_file():
        if json.loads(DECISION_PATH.read_text(encoding="utf-8")) != DECISION:
            raise RuntimeError("B036_HUMAN_DECISION_ARTIFACT_CONFLICT")
    else:
        write_json_once(DECISION_PATH, DECISION)

    display_id, checkpoint = DECISION["work_order_id"], DECISION["checkpoint"]
    directory = BATCH_DIR / checkpoint
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
    request = next((item for item in loaded["requests"] if item.display_id == display_id), None)
    if request is None:
        raise RuntimeError("B036_HUMAN_REQUEST_MISSING")
    raws = load_raw_by_uid(directory / "translation_raw_response.jsonl")
    raw = raws.get(request.unit_uid)
    if raw is None:
        raise RuntimeError("B036_HUMAN_RAW_MISSING")
    head = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    if head.candidate_sha256 != DECISION["expected_current_head"]:
        raise RuntimeError(f"B036_HUMAN_CURRENT_HEAD_STALE:{head.candidate_sha256}")
    if load_stage(directory, "human_adjudications", display_id) is not None:
        raise RuntimeError("B036_HUMAN_ALREADY_APPLIED")

    effective = resolve_effective_source(checkpoint_dir=directory, request=request).request
    group = build_current_review_group(
        effective, loaded=loaded, run=RUN, checkpoint=checkpoint,
        existing_raw=raws, candidate_overrides=_rescue_working_overrides(directory, loaded, raws),
    )
    if group is None or list(group.member_work_order_ids) != [display_id]:
        raise RuntimeError("B036_HUMAN_CURRENT_GROUP_UNSAFE")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    provider_request = enrich_request(effective, translation_bible=bible)
    allowed = HumanAllowedRepair(
        logical_work_order_id=display_id, unit_uid=effective.unit_uid,
        allowed_segment_indices=DECISION["approved_patch_indices"],
        source_fingerprint=provider_request.request_binding_sha256,
    )
    adjudication = build_human_adjudication(
        group, decision="HUMAN_APPROVED", reason=DECISION["reason"],
        allowed_repairs=[allowed],
        source_hashes={display_id: provider_request.request_binding_sha256},
        original_candidate_hashes={display_id: candidate_sha(raw)},
        expected_current_heads={display_id: DECISION["expected_current_head"]},
        approved_patches=[HumanApprovedPatch(
            logical_work_order_id=display_id, unit_uid=effective.unit_uid, index=0,
            target_text=DECISION["target_text"],
        )],
    )
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"],
        views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record,
        adjudication=adjudication, group=group, members=[(provider_request, head.candidate)],
        translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
        batch_label=BATCH, checkpoint=checkpoint,
    )
    refreshed = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    target_texts = [segment.text for segment in refreshed.candidate.target_segments]
    if refreshed.candidate_sha256 == head.candidate_sha256 or target_texts != [DECISION["target_text"]]:
        raise RuntimeError("B036_HUMAN_CURRENT_CONTENT_MISMATCH")
    if not all(record.get("passed") for record in result["qa"].values()):
        raise RuntimeError("B036_HUMAN_QA_NOT_PASS")
    if any(result.get(key, 0) != 0 for key in ("translation_api_calls", "reviewer_api_calls", "repair_api_calls")):
        raise RuntimeError("B036_HUMAN_PROVIDER_CALL_FORBIDDEN")
    report = {
        "artifact_type": "B036_HUMAN_ADJUDICATION_APPLY_REPORT", "schema_version": 1,
        "decision_artifact_path": str(DECISION_PATH),
        "decision_artifact_sha256": sha256(DECISION_PATH.read_bytes()).hexdigest(),
        "work_order_id": display_id, "checkpoint": checkpoint, "status": "PASS",
        "parent_candidate_sha256": head.candidate_sha256,
        "authoritative_candidate_sha256": refreshed.candidate_sha256,
        "target_segments": target_texts, "core_qa_pass": True,
        "deterministic_qa_pass": True, "candidate_authority_pass": True,
        "translation_api_calls": 0, "reviewer_api_calls": 0, "repair_api_calls": 0,
    }
    write_json_once(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
