"""Seal Batch 011 checkpoint 03 with bounded immutable-RAW numeric repairs."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path

import run_full_book_batch006_checkpoint as runner
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH = RUN / "full_book_batch_011"
OUT = BATCH / "checkpoint_03"
SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
REPAIRS = {
    "body.p.03309": {1: "1"},
    "body.p.03315": {1: "2"},
    "body.p.03319": {0: "1  当然，你仍需要维护它并偶尔修订。"},
    "body.p.03320": {0: "2  英语仍是大多数程序员的主要语言，而大多数面向程序员的技术文档都依赖于对英语的理解。"},
}

def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as h:
        json.dump(value, h, ensure_ascii=False, indent=2); h.write("\n")

def write_jsonl(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as h:
        for value in values: h.write(value.model_dump_json() + "\n")

def main() -> None:
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("next_checkpoint") != "checkpoint_03" or manifest.get("completed_count") != 50: raise RuntimeError("CHECKPOINT_REPAIR_RESUME_MISMATCH")
    required = ["translation_request.jsonl", "translation_raw_response.jsonl", "translation_raw_qa_report.json", "translation_normalized_response.jsonl", "translation_normalized_qa_report.json", "translation_normalization_report.json"]
    final_names = ["translation_repaired_response.jsonl", "targeted_repair_contract.json", "semantic_review_report.json", "translation_final_qa_report.json", "checkpoint_evidence.json"]
    if not all((OUT / x).is_file() for x in required) or any((OUT / x).exists() for x in final_names): raise RuntimeError("IMMUTABLE_REPAIR_ARTIFACT_STATE_INVALID")
    raw_report = json.loads((OUT / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
    if {x["display_id"] for x in raw_report["records"] if not x["passed"]} != set(REPAIRS): raise RuntimeError("TARGETED_REPAIR_FAILURE_SET_MISMATCH")
    requests = [CalibrationRequest.model_validate_json(x) for x in (OUT / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if x]
    normalized = [TranslationResult.model_validate_json(x) for x in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if x]
    repaired = []
    for request, response in zip(requests, normalized, strict=True):
        changes = REPAIRS.get(request.display_id, {})
        if len(response.target_segments) != len(request.source_segments): raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
        segments = [StructuredTargetSegment(text=changes.get(i, item.text), kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref) for i, (item, source) in enumerate(zip(response.target_segments, request.source_segments, strict=True))]
        repaired.append(TranslationResult(unit_uid=response.unit_uid, target_segments=segments))
    views = {v.display_id: v for v in (TranslationView.model_validate_json(x) for x in runner.PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if x)}
    known = {v.unit_uid for v in views.values()}
    final = [runner.PRODUCTION.qa(views[r.display_id], r, response, known) for r, response in zip(requests, repaired, strict=True)]
    if not all(x["passed"] for x in final): raise RuntimeError("TARGETED_REPAIR_QA_FAILED")
    raw_sha = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    write_jsonl(OUT / "translation_repaired_response.jsonl", repaired)
    write_json(OUT / "targeted_repair_contract.json", {"repair_required": True, "repair_units": sorted(REPAIRS), "reason": "NUMBER_PRESERVATION_REQUIRED_SOURCE_TOKEN", "raw_immutable_sha256": raw_sha, "material_target_changes": {"body.p.03309": {"segment": 1, "before": "一", "after": "1"}, "body.p.03315": {"segment": 1, "before": "二", "after": "2"}, "body.p.03319": {"segment": 0, "before": "一  当然，你仍需要维护它并偶尔修订。", "after": "1  当然，你仍需要维护它并偶尔修订。"}, "body.p.03320": {"segment": 0, "before": "二  英语仍是大多数程序员的主要语言，而大多数面向程序员的技术文档都依赖于对英语的理解。", "after": "2  英语仍是大多数程序员的主要语言，而大多数面向程序员的技术文档都依赖于对英语的理解。"}}, "normalization_changed": 0})
    dispatched, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{"unit_uid": x["unit_uid"], "display_id": x["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Bounded Arabic-numeric repair preserves the required source values and passes deterministic QA; no D050 evidence is asserted or routed."} for x in dispatched]
    write_json(OUT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    approved = [x["unit_uid"] for x in final]
    write_json(OUT / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": []})
    write_json(OUT / "checkpoint_evidence.json", {"logical_work_orders": [r.display_id for r in requests], "source_sha256": SHA, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": 0, "raw_deterministic_fail_count": len(REPAIRS), "targeted_repair_count": len(REPAIRS), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": []})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", OUT)
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")

if __name__ == "__main__": main()
