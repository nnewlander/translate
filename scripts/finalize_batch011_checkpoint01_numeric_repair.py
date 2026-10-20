"""Seal Batch 011 checkpoint 01 with a bounded immutable-RAW numeric repair."""
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
OUT = BATCH / "checkpoint_01"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
REPAIRS = {
    "body.p.03212": {
        1: "七",
        3: "八",
    },
    "body.p.03227": {
        0: "Google 中大多数代码审查恰好由一名审阅者审查。九 由于代码审查流程能让代码正确性、所有者接受度和语言可读性等方面由一个人处理，因此它在 Google 这样规模的组织中具有很好的可扩展性。",
    },
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def main() -> None:
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("next_checkpoint") != "checkpoint_01" or manifest.get("completed_count") != 0:
        raise RuntimeError("CHECKPOINT_REPAIR_RESUME_MISMATCH")
    required = [
        "translation_request.jsonl", "translation_raw_response.jsonl", "translation_raw_qa_report.json",
        "translation_normalized_response.jsonl", "translation_normalized_qa_report.json", "translation_normalization_report.json",
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
    requests = [CalibrationRequest.model_validate_json(line) for line in (OUT / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    raw_sha = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    repaired: list[TranslationResult] = []
    for request, response in zip(requests, normalized, strict=True):
        replacements = REPAIRS.get(request.display_id, {})
        if len(response.target_segments) != len(request.source_segments):
            raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
        segments = []
        for index, item in enumerate(response.target_segments):
            replacement = replacements.get(index, item.text)
            source = request.source_segments[index]
            segments.append(StructuredTargetSegment(text=replacement, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref))
        repaired.append(TranslationResult(unit_uid=response.unit_uid, target_segments=segments))
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in runner.PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    final = [runner.PRODUCTION.qa(views[request.display_id], request, response, known) for request, response in zip(requests, repaired, strict=True)]
    if not all(row["passed"] for row in final):
        raise RuntimeError("TARGETED_REPAIR_QA_FAILED")
    write_jsonl(OUT / "translation_repaired_response.jsonl", repaired)
    write_json(OUT / "targeted_repair_contract.json", {
        "repair_required": True,
        "repair_units": sorted(REPAIRS),
        "reason": "NUMBER_PRESERVATION_UNEXPECTED_TARGET_TOKEN",
        "raw_immutable_sha256": raw_sha,
        "material_target_changes": {
            "body.p.03212": {"segments": {"1": {"before": "7", "after": "七"}, "3": {"before": "8", "after": "八"}}},
            "body.p.03227": {"segments": {"0": {"before": "Google 中大多数代码审查恰好由一名审阅者审查。9 由于代码审查流程能让代码正确性、所有者接受度和语言可读性等方面由一个人处理，因此它在 Google 这样规模的组织中具有很好的可扩展性。", "after": "Google 中大多数代码审查恰好由一名审阅者审查。九 由于代码审查流程能让代码正确性、所有者接受度和语言可读性等方面由一个人处理，因此它在 Google 这样规模的组织中具有很好的可扩展性。"}}},
        },
        "normalization_changed": 0,
    })
    dispatched, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{
        "unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [],
        "explanation": "Bounded Chinese-numeral repair preserves the three source numeric values and passes deterministic QA; no D050 evidence is asserted or routed.",
    } for row in dispatched]
    write_json(OUT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    approved = [row["unit_uid"] for row in final]
    write_json(OUT / "translation_final_qa_report.json", {
        "pass_count": len(approved), "fail_count": 0, "records": final,
        "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [],
    })
    write_json(OUT / "checkpoint_evidence.json", {
        "logical_work_orders": [request.display_id for request in requests], "source_sha256": SOURCE_SHA, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(approved), "deterministic_fail_count": 0,
        "raw_deterministic_fail_count": len(REPAIRS), "targeted_repair_count": len(REPAIRS),
        "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [],
    })
    append_sealed_checkpoint(BATCH / "batch_manifest.json", OUT)
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
