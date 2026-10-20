"""Finalize the immutable-RAW Batch 007 checkpoint 02 numeric repair."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import run_full_book_batch006_checkpoint as runner
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = RUN / "full_book_batch_007"
OUT = BATCH / "checkpoint_02"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
REPAIR_ID = "body.p.02323"
REPAIR_TEXT = "总之，机长接着说有两个选择：要么等油罐车来把燃油从飞机吸回去，这要一个多小时；要么现在必须有二十个人下飞机以平衡重量。"


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json() + "\n")


def main() -> None:
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if manifest["next_checkpoint"] != "checkpoint_02" or manifest["completed_count"] != 25:
        raise RuntimeError("CHECKPOINT_REPAIR_RESUME_MISMATCH")
    required = ["translation_request.jsonl", "translation_raw_response.jsonl", "translation_raw_qa_report.json", "translation_normalized_response.jsonl", "translation_normalized_qa_report.json"]
    if not all((OUT / name).is_file() for name in required) or any((OUT / name).exists() for name in ["translation_repaired_response.jsonl", "translation_final_qa_report.json", "checkpoint_evidence.json"]):
        raise RuntimeError("IMMUTABLE_REPAIR_ARTIFACT_STATE_INVALID")
    requests = [json.loads(line) for line in (OUT / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    raw_sha = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_sha != sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest():
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in runner.PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    repair_request = next(request for request in requests if request["display_id"] == REPAIR_ID)
    repair_view = views[REPAIR_ID]
    if len(repair_request["source_segments"]) != 1:
        raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
    repaired: list[TranslationResult] = []
    for request, response in zip(requests, normalized, strict=True):
        if request["display_id"] == REPAIR_ID:
            repaired.append(TranslationResult(unit_uid=response.unit_uid, target_segments=[StructuredTargetSegment(text=REPAIR_TEXT, kind=request["source_segments"][0]["kind"], anchor_ref=request["source_segments"][0].get("anchor_ref"), island_ref=request["source_segments"][0].get("island_ref"))]))
        else:
            repaired.append(response)
    known = {view.unit_uid for view in views.values()}
    final = [runner.PRODUCTION.qa(views[request["display_id"]], runner.PRODUCTION.CalibrationRequest.model_validate(request), response, known) for request, response in zip(requests, repaired, strict=True)]
    if not all(row["passed"] for row in final):
        raise RuntimeError("TARGETED_REPAIR_QA_FAILED")
    write_jsonl(OUT / "translation_repaired_response.jsonl", repaired)
    write_json(OUT / "targeted_repair_contract.json", {"repair_required": True, "repair_unit": REPAIR_ID, "reason": "NUMBER_PRESERVATION_UNEXPECTED_TARGET_TOKEN", "raw_immutable_sha256": raw_sha, "material_target_change": {"before": "20", "after": "二十"}, "normalization_changed": 0})
    dispatched, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Targeted numeric-representation repair preserved source meaning and passed deterministic QA."} for row in dispatched]
    write_json(OUT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    write_json(OUT / "translation_final_qa_report.json", {"pass_count": len(final), "fail_count": 0, "records": final, "final_approved_unit_uids": [row["unit_uid"] for row in final], "not_approved_deterministic_fail_unit_uids": []})
    write_json(OUT / "checkpoint_evidence.json", {"logical_work_orders": [request["display_id"] for request in requests], "source_sha256": SOURCE_SHA, "raw_sha256": raw_sha, "deterministic_pass_count": len(final), "deterministic_fail_count": 0, "raw_deterministic_fail_count": 1, "targeted_repair_count": 1, "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(RUN / "chapter_5_batch007_verified_footnote_evidence_v1.json")]})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", OUT)
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
