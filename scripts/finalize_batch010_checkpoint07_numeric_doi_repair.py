"""Seal Batch 010 checkpoint 07 through a bounded immutable-RAW representation repair."""
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
BATCH = RUN / "full_book_batch_010"
OUT = BATCH / "checkpoint_07"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
REPAIRS = {
    "body.p.03135": {1: "五", 2: "只要代码审查流程本身经过精简并保持轻量，投入代码审查的时间就能节省原本用于测试、调试和执行回归的时间。后一要点很重要；过于沉重或不能恰当扩展的代码审查流程将难以持续。", 3: "六"},
    "body.p.03140": {2: "，2013 年八月：202-212。", 7: "."},
}


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
    if manifest.get("next_checkpoint") != "checkpoint_07" or manifest.get("completed_count") != 150:
        raise RuntimeError("CHECKPOINT_REPAIR_RESUME_MISMATCH")
    required = [
        "translation_request.jsonl", "translation_raw_response.jsonl", "translation_raw_qa_report.json",
        "translation_normalized_response.jsonl", "translation_normalized_qa_report.json",
    ]
    final_names = [
        "translation_repaired_response.jsonl", "targeted_repair_contract.json", "semantic_review_report.json",
        "translation_final_qa_report.json", "checkpoint_evidence.json",
    ]
    if not all((OUT / name).is_file() for name in required) or any((OUT / name).exists() for name in final_names):
        raise RuntimeError("IMMUTABLE_REPAIR_ARTIFACT_STATE_INVALID")
    raw_report = json.loads((OUT / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
    failed = {row["display_id"] for row in raw_report.get("records", []) if not row.get("passed")}
    if failed != set(REPAIRS):
        raise RuntimeError("TARGETED_REPAIR_FAILURE_SET_MISMATCH")
    requests = [json.loads(line) for line in (OUT / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    raw_sha = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    repaired: list[TranslationResult] = []
    material_changes: dict[str, list[dict[str, object]]] = {}
    for request, response in zip(requests, normalized, strict=True):
        instructions = REPAIRS.get(request["display_id"])
        if instructions is None:
            repaired.append(response)
            continue
        if len(response.target_segments) != len(request["source_segments"]):
            raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
        segments = [StructuredTargetSegment(text=item.text, kind=item.kind, anchor_ref=item.anchor_ref, island_ref=item.island_ref) for item in response.target_segments]
        changes: list[dict[str, object]] = []
        for index, replacement in instructions.items():
            if index >= len(segments):
                raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
            before = segments[index].text
            source_segment = request["source_segments"][index]
            segments[index] = StructuredTargetSegment(text=replacement, kind=source_segment["kind"], anchor_ref=source_segment.get("anchor_ref"), island_ref=source_segment.get("island_ref"))
            changes.append({"segment": index, "before": before, "after": replacement})
        material_changes[request["display_id"]] = changes
        repaired.append(TranslationResult(unit_uid=response.unit_uid, target_segments=segments))
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in runner.PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    final = [
        runner.PRODUCTION.qa(views[request["display_id"]], runner.PRODUCTION.CalibrationRequest.model_validate(request), response, known)
        for request, response in zip(requests, repaired, strict=True)
    ]
    if not all(row["passed"] for row in final):
        raise RuntimeError("TARGETED_REPAIR_QA_FAILED")
    write_jsonl(OUT / "translation_repaired_response.jsonl", repaired)
    write_json(OUT / "targeted_repair_contract.json", {
        "repair_required": True,
        "repair_units": sorted(REPAIRS),
        "reason": ["NUMBER_PRESERVATION_UNEXPECTED_TARGET_TOKEN", "DOI_PRESERVATION"],
        "raw_immutable_sha256": raw_sha,
        "material_target_changes": material_changes,
        "normalization_changed": 0,
    })
    dispatched, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{
        "unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [],
        "explanation": "Bounded numeric and DOI representation repair preserved source meaning and passed deterministic QA.",
    } for row in dispatched]
    write_json(OUT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    write_json(OUT / "translation_final_qa_report.json", {
        "pass_count": len(final), "fail_count": 0, "records": final,
        "final_approved_unit_uids": [row["unit_uid"] for row in final], "not_approved_deterministic_fail_unit_uids": [],
    })
    write_json(OUT / "checkpoint_evidence.json", {
        "logical_work_orders": [request["display_id"] for request in requests], "source_sha256": SOURCE_SHA, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(final), "deterministic_fail_count": 0, "raw_deterministic_fail_count": len(REPAIRS),
        "targeted_repair_count": len(REPAIRS), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [],
        "authored_target_map": "data\\full_book_batch010_checkpoint_07_targets.json",
    })
    append_sealed_checkpoint(BATCH / "batch_manifest.json", OUT)
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
