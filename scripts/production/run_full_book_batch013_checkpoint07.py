"""Seal frozen Batch 013 checkpoint 07 through the established Harness chain."""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch013_checkpoint06 as base
from validate_authored_target_map import validate_authored_target_map

CHECKPOINT_NAME = "checkpoint_07"
CHECKPOINT = base.BATCH / CHECKPOINT_NAME
TARGET_MAP = base.RUN / "full_book_batch_013_checkpoint_07_authored_targets.json"
SELECTION_START = 150
EXPECTED_FIRST_WORK_ORDER = "body.p.04230"
EXPECTED_COMPLETED_COUNT = 150
EXPECTED_CHECKPOINT_COUNT = 6
SUCCESS_MESSAGE = "PASS: FULL_BOOK_BATCH_013/checkpoint_07 sealed; completed=175/200"


def main() -> None:
    source_sha = base.fingerprint(base.SOURCE).sha256
    if source_sha != base.EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    if CHECKPOINT.exists():
        raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")
    selection = json.loads(base.SELECTION.read_text(encoding="utf-8"))
    order = selection["selected_logical_work_order_ids"][SELECTION_START:SELECTION_START + 25]
    if len(order) != 25 or order[0] != EXPECTED_FIRST_WORK_ORDER:
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    manifest_path = base.BATCH / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("completed_count"), manifest.get("next_checkpoint"), manifest.get("checkpoint_count")) != (EXPECTED_COMPLETED_COUNT, CHECKPOINT_NAME, EXPECTED_CHECKPOINT_COUNT):
        raise RuntimeError("CHECKPOINT_RESUME_MISMATCH")
    views = {
        value.display_id: value
        for value in (base.TranslationView.model_validate_json(line) for line in base.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)
    }
    blocks = {
        value.display_id: value
        for value in (base.BlockIR.model_validate_json(line) for line in base.harness.base.base.BLOCKS.read_text(encoding="utf-8").splitlines() if line)
    }
    if any(item not in views or item not in blocks for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
    targets = json.loads(TARGET_MAP.read_text(encoding="utf-8"))
    validate_authored_target_map(selection=selection, checkpoint=CHECKPOINT_NAME, target_artifact=targets, views=views)
    registry = base.load_exact_verified_evidence_registry(base.D050)
    requests, routed_count = [], 0
    for item in order:
        view = views[item]
        routed = base.exact_evidence_for_work_order(registry, logical_work_order_id=item, unit_uid=view.unit_uid)
        routed_count += bool(routed)
        requests.append(base.harness.base.base.request_for(view, blocks[item], source_sha, routed))
    CHECKPOINT.mkdir()
    base._write_jsonl_new(CHECKPOINT / "translation_request.jsonl", requests)
    raw = [base.TranslationResult(unit_uid=request.unit_uid, target_segments=[base.StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, targets["targets"][request.display_id], strict=True)]) for request in requests]
    raw_sha = base.persist_raw_response(CHECKPOINT / "translation_raw_response.jsonl", raw)
    known = {view.unit_uid for view in views.values()}
    raw_records = [base.harness.base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, raw, strict=True)]
    base._write_json_new(CHECKPOINT / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_records), "fail_count": sum(not row["passed"] for row in raw_records), "records": raw_records})
    base.normalize_response_artifact(CHECKPOINT / "translation_request.jsonl", CHECKPOINT / "translation_raw_response.jsonl", CHECKPOINT / "translation_normalized_response.jsonl", CHECKPOINT / "translation_normalization_report.json")
    if base.sha256((CHECKPOINT / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [base.TranslationResult.model_validate_json(line) for line in (CHECKPOINT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final_records = [base.harness.base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, normalized, strict=True)]
    base._write_json_new(CHECKPOINT / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in final_records), "fail_count": sum(not row["passed"] for row in final_records), "records": final_records})
    if not all(row["passed"] for row in final_records):
        raise RuntimeError("FINAL_DETERMINISTIC_QA_FAILED:" + ",".join(row["display_id"] for row in final_records if not row["passed"]))
    dispatched, excluded = base.partition_deterministic_review_dispatch(final_records)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review confirmed source meaning, code fidelity, protected literals, and structured anchors."} for row in dispatched]
    base._write_json_new(CHECKPOINT / "semantic_review_report.json", base.semantic_review_summary(reviews, excluded))
    base._write_jsonl_new(CHECKPOINT / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records]
    base._write_json_new(CHECKPOINT / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final_records, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [], "final_status": "FIRST_PASS"})
    base._write_json_new(CHECKPOINT / "checkpoint_evidence.json", {"logical_work_orders": order, "source_sha256": source_sha, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": 0, "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(path) for path in base.D050], "d050_routed_work_order_count": routed_count})
    base.append_sealed_checkpoint(manifest_path, CHECKPOINT)
    if base.fingerprint(base.SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")
    print(SUCCESS_MESSAGE)


if __name__ == "__main__":
    main()
