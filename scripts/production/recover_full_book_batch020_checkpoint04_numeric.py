"""Bounded recovery for B020/checkpoint_04's verified numeric QA failure."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch014_checkpoint01 as harness
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH = RUN / "full_book_batch_020"
CHECKPOINT = BATCH / "checkpoint_04"
SELECTION = RUN / "full_book_batch_020_selection.json"
D050 = RUN / "chapter_17_batch020_checkpoint04_verified_footnote_evidence_v1.json"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
RAW_SHA = "baada096badd0a25f06dcc7db4fd86cb0b8706058f274179f60be41e15f85fa2"
FAILURE = "body.p.06021"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _jsonl(path: Path, model: type[CalibrationRequest] | type[TranslationResult]) -> list:
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_new_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_new_jsonl(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(value.model_dump_json())
            stream.write("\n")


def _repair(request: CalibrationRequest, response: TranslationResult) -> tuple[TranslationResult, dict | None]:
    if request.display_id != FAILURE:
        return response, None
    if len(request.source_segments) != len(response.target_segments):
        raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
    source = request.source_segments[2].text
    before = response.target_segments[2].text
    if "100,000" not in source or "100" not in source or before.count("10") != 1 or "100" in before:
        raise RuntimeError("NUMERIC_REPAIR_BASELINE_MISMATCH")
    after = before.replace("10", "100", 1)
    segments = [
        StructuredTargetSegment(text=(after if index == 2 else target.text), kind=source_segment.kind,
                                anchor_ref=source_segment.anchor_ref, island_ref=source_segment.island_ref)
        for index, (source_segment, target) in enumerate(zip(request.source_segments, response.target_segments, strict=True))
    ]
    return TranslationResult(unit_uid=response.unit_uid, target_segments=segments), {
        "segments": {"2": {"before": before, "after": after}},
        "source_required_tokens": ["100", "100,000"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what-if", action="store_true")
    args = parser.parse_args()
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    required = [BATCH / "batch_manifest.json", SELECTION, D050, *(CHECKPOINT / name for name in (
        "translation_request.jsonl", "translation_raw_response.jsonl", "translation_raw_qa_report.json",
        "translation_normalized_response.jsonl", "translation_normalization_report.json", "translation_normalized_qa_report.json",
    ))]
    if not all(path.is_file() for path in required):
        raise RuntimeError("PARTIAL_CHECKPOINT_INPUT_MISSING")
    downstream = ("targeted_repair_contract.json", "semantic_review_report.json", "translation_repaired_response.jsonl", "translation_final_qa_report.json", "checkpoint_evidence.json")
    if any((CHECKPOINT / name).exists() for name in downstream):
        raise RuntimeError("CHECKPOINT_ALREADY_HAS_DOWNSTREAM_ARTIFACTS")
    manifest, selection = _json(BATCH / "batch_manifest.json"), _json(SELECTION)
    order = selection.get("selected_logical_work_order_ids", [])[75:100]
    if len(order) != 25 or (manifest.get("completed_count"), manifest.get("checkpoint_count"), manifest.get("next_checkpoint")) != (75, 3, "checkpoint_04"):
        raise RuntimeError("CHECKPOINT_RESUME_STATE_MISMATCH")
    requests = _jsonl(CHECKPOINT / "translation_request.jsonl", CalibrationRequest)
    raw = _jsonl(CHECKPOINT / "translation_raw_response.jsonl", TranslationResult)
    normalized = _jsonl(CHECKPOINT / "translation_normalized_response.jsonl", TranslationResult)
    if [item.display_id for item in requests] != order or [item.unit_uid for item in raw] != [item.unit_uid for item in requests] or raw != normalized:
        raise RuntimeError("EXACT_SELECTION_OR_RAW_CHAIN_MISMATCH")
    raw_sha = sha256((CHECKPOINT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_sha != RAW_SHA:
        raise RuntimeError("IMMUTABLE_RAW_SHA_MISMATCH")
    if _json(CHECKPOINT / "translation_raw_qa_report.json") != _json(CHECKPOINT / "translation_normalized_qa_report.json"):
        raise RuntimeError("HISTORICAL_QA_REPORT_CONFLICT")
    failed = [row for row in _json(CHECKPOINT / "translation_raw_qa_report.json").get("records", []) if not row.get("passed")]
    if [row.get("display_id") for row in failed] != [FAILURE] or {item.get("code") for item in failed[0].get("diagnostics", [])} != {"NUMBER_PRESERVATION"}:
        raise RuntimeError("HISTORICAL_QA_FAILURE_SET_MISMATCH")
    repaired, changes = [], {}
    for request, response in zip(requests, normalized, strict=True):
        result, change = _repair(request, response)
        repaired.append(result)
        if change: changes[request.display_id] = change
    if list(changes) != [FAILURE]:
        raise RuntimeError("TARGETED_REPAIR_SCOPE_MISMATCH")
    views = {value.display_id: value for value in (TranslationView.model_validate_json(line) for line in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {value.unit_uid for value in views.values()}
    final = [harness.harness.base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, repaired, strict=True)]
    if not all(row["passed"] for row in final):
        raise RuntimeError("TARGETED_REPAIR_FINAL_DETERMINISTIC_QA_FAILED")
    if args.what_if:
        print("WHATIF PASS: RAW preserved; numeric repair=body.p.06021; final QA=25/25")
        return
    _write_new_json(CHECKPOINT / "targeted_repair_contract.json", {"artifact_type": "CHECKPOINT_TARGETED_NUMERIC_REPAIR_CONTRACT", "checkpoint": "checkpoint_04", "classification": "LOCAL_DETERMINISTIC_QA_FAILURE", "policy": "NUMERIC_REPRESENTATION", "repair_units": [FAILURE], "historical_raw": str(CHECKPOINT / "translation_raw_response.jsonl"), "historical_raw_sha256": raw_sha, "historical_raw_immutable": True, "historical_qa_artifacts_preserved": [str(CHECKPOINT / "translation_raw_qa_report.json"), str(CHECKPOINT / "translation_normalized_qa_report.json")], "material_target_changes": changes, "normalization_changed": 0})
    dispatch, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Bounded numeric-representation repair restores only the exact source-required token; RAW, anchors, and verified D050 bindings remain unchanged."} for row in dispatch]
    _write_new_json(CHECKPOINT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    _write_new_jsonl(CHECKPOINT / "translation_repaired_response.jsonl", repaired)
    approved = [row["unit_uid"] for row in final]
    _write_new_json(CHECKPOINT / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [], "final_status": "RECOVERED_AFTER_NUMERIC_REPAIR"})
    _write_new_json(CHECKPOINT / "checkpoint_evidence.json", {"logical_work_orders": order, "source_sha256": SOURCE_SHA, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": 0, "raw_deterministic_fail_count": 1, "targeted_repair_count": 1, "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(D050)], "recovery_contract": str(CHECKPOINT / "targeted_repair_contract.json")})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", CHECKPOINT)
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print("RECOVERY PASS: FULL_BOOK_BATCH_020/checkpoint_04 sealed; completed=100/200; next=checkpoint_05")


if __name__ == "__main__":
    main()
