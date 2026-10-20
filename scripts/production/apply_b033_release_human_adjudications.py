"""Apply the owner-approved B033 release Human adjudications.

Each decision is immutable and exact-current-head CAS bound.  The script
uses the existing HumanSemanticAdjudication transaction; it makes no provider
calls and only appends Human decision/current-candidate evidence.
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
BATCH = "FULL_BOOK_BATCH_033"
BATCH_DIR = RUN / BATCH.lower()
DECISION_PATH = BATCH_DIR / "b033_human_adjudication_decisions_v1.json"
REPORT_PATH = BATCH_DIR / "b033_human_adjudication_apply_report_v1.json"

DECISIONS = {
    "artifact_type": "B033_HUMAN_ADJUDICATION_DECISIONS",
    "schema_version": 1,
    "batch": BATCH,
    "decision_count": 2,
    "binding_rule": "Each decision is exact-current-head CAS bound; locator and protected ownership remain Harness-deterministic.",
    "decisions": [
        {
            "work_order_id": "body.p.09948",
            "checkpoint": "checkpoint_04",
            "action": "REPLACE",
            "expected_current_head": "d495bd0b1ab6782446a7f8e9e512c62cf5ad4520307cd60b8155bd954f0b6cb1",
            "target_segments": ["堆栈帧中的 Code Search 集成，", "357 ", "分阶段推出，", "512"],
            "approved_patch_indices": [0],
            "reason": "Owner-approved release correction preserving the INDEX term/qualifier relation; numeric locators remain Harness-owned.",
        },
        {
            "work_order_id": "body.p.09986",
            "checkpoint": "checkpoint_05",
            "action": "REPLACE",
            "expected_current_head": "ba30fff95988151df9fb9a113a2986afd64bd265ebeffcd5f49a6d411b14eeb5",
            "semantic_patches": {"0": "代码风格指南，"},
            "approved_patch_indices": [0],
            "reason": "Owner-approved INDEX semantic correction. Numeric locators and delimiter mapping remain Harness-owned.",
        },
    ],
}


def _write_decisions() -> dict:
    if DECISION_PATH.is_file():
        if json.loads(DECISION_PATH.read_text(encoding="utf-8")) != DECISIONS:
            raise RuntimeError("B033_HUMAN_DECISION_ARTIFACT_CONFLICT")
    else:
        write_json_once(DECISION_PATH, DECISIONS)
    return json.loads(DECISION_PATH.read_text(encoding="utf-8"))


def _apply_item(item: dict) -> dict:
    display_id, checkpoint = item["work_order_id"], item["checkpoint"]
    directory = BATCH_DIR / checkpoint
    loaded = frozen_batch_checkpoint_inputs(RUN, batch_label=BATCH, checkpoint=checkpoint)
    request = {request.display_id: request for request in loaded["requests"]}.get(display_id)
    if request is None:
        raise RuntimeError(f"B033_HUMAN_REQUEST_MISSING:{display_id}")
    raws = load_raw_by_uid(directory / "translation_raw_response.jsonl")
    raw = raws.get(request.unit_uid)
    if raw is None:
        raise RuntimeError(f"B033_HUMAN_RAW_MISSING:{display_id}")
    head = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    expected_head = item["expected_current_head"]
    if head.candidate_sha256 != expected_head:
        raise RuntimeError(f"B033_HUMAN_CURRENT_HEAD_STALE:{display_id}:{head.candidate_sha256}")
    existing = load_stage(directory, "human_adjudications", display_id)
    if existing is not None:
        raise RuntimeError(f"B033_HUMAN_ALREADY_APPLIED:{display_id}")

    effective = resolve_effective_source(checkpoint_dir=directory, request=request).request
    overrides = _rescue_working_overrides(directory, loaded, raws)
    group = build_current_review_group(
        effective, loaded=loaded, run=RUN, checkpoint=checkpoint,
        existing_raw=raws, candidate_overrides=overrides,
    )
    if group is None or list(group.member_work_order_ids) != [display_id]:
        raise RuntimeError(f"B033_HUMAN_CURRENT_GROUP_UNSAFE:{display_id}")
    bible = loaded["bible"] if Path(loaded["bible"]).is_file() else None
    provider_request = enrich_request(effective, translation_bible=bible)
    target_texts = [segment.text for segment in head.candidate.target_segments]
    if "target_segments" in item:
        target_texts = item["target_segments"]
    else:
        for index, text in item["semantic_patches"].items():
            target_texts[int(index)] = text
    if len(target_texts) != len(effective.source_segments) or any(not text.strip() for text in target_texts):
        raise RuntimeError(f"B033_HUMAN_TARGET_CONTRACT_INVALID:{display_id}")
    indices = item["approved_patch_indices"]
    if any(target_texts[index] == head.candidate.target_segments[index].text for index in indices):
        raise RuntimeError(f"B033_HUMAN_NO_SEMANTIC_CHANGE:{display_id}")
    allowed = HumanAllowedRepair(
        logical_work_order_id=display_id, unit_uid=effective.unit_uid,
        allowed_segment_indices=indices, source_fingerprint=provider_request.request_binding_sha256,
    )
    adjudication = build_human_adjudication(
        group, decision="HUMAN_APPROVED", reason=item["reason"],
        allowed_repairs=[allowed],
        source_hashes={display_id: provider_request.request_binding_sha256},
        original_candidate_hashes={display_id: candidate_sha(raw)},
        expected_current_heads={display_id: expected_head},
        approved_patches=[
            HumanApprovedPatch(logical_work_order_id=display_id, unit_uid=effective.unit_uid,
                               index=index, target_text=target_texts[index])
            for index in indices
        ],
    )
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=directory, production_run=RUN, requests=loaded["requests"],
        views=loaded["views"], known_unit_ids=loaded["known_unit_ids"], qa_fn=harness_qa_record,
        adjudication=adjudication, group=group, members=[(provider_request, head.candidate)],
        translation_bible=bible, triage_classification="TRUE_SEMANTIC_HUMAN",
        batch_label=BATCH, checkpoint=checkpoint,
    )
    refreshed = _resolve_checkpoint_authoritative_candidate(directory, display_id, raw)
    actual_targets = [segment.text for segment in refreshed.candidate.target_segments]
    if refreshed.candidate_sha256 == expected_head or actual_targets != target_texts:
        raise RuntimeError(f"B033_HUMAN_CURRENT_CONTENT_MISMATCH:{display_id}")
    if not all(record.get("passed") for record in result["qa"].values()):
        raise RuntimeError(f"B033_HUMAN_QA_NOT_PASS:{display_id}")
    if any(result.get(key, 0) != 0 for key in ("translation_api_calls", "reviewer_api_calls", "repair_api_calls")):
        raise RuntimeError(f"B033_HUMAN_PROVIDER_CALL_FORBIDDEN:{display_id}")
    return {
        "work_order_id": display_id, "checkpoint": checkpoint, "status": "PASS",
        "parent_candidate_sha256": expected_head,
        "authoritative_candidate_sha256": refreshed.candidate_sha256,
        "target_segments": actual_targets, "core_qa_pass": True,
        "deterministic_qa_pass": True, "candidate_authority_pass": True,
        "translation_api_calls": 0, "reviewer_api_calls": 0, "repair_api_calls": 0,
    }


def main() -> None:
    artifact = _write_decisions()
    report = {
        "artifact_type": "B033_HUMAN_ADJUDICATION_APPLY_REPORT", "schema_version": 1,
        "decision_artifact_path": str(DECISION_PATH),
        "decision_artifact_sha256": sha256(DECISION_PATH.read_bytes()).hexdigest(),
        "results": [_apply_item(item) for item in artifact["decisions"]],
        "translation_api_calls": 0, "reviewer_api_calls": 0, "repair_api_calls": 0,
    }
    write_json_once(REPORT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
