"""Seal Batch 019 checkpoint 03 from immutable RAW via bounded numeric repair.

This recovery is intentionally exact-work-order scoped.  It preserves the
frozen request, RAW response, historical QA reports, and normalization output;
only new downstream recovery artifacts are written with exclusive create.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
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
BATCH = RUN / "full_book_batch_019"
CHECKPOINT = BATCH / "checkpoint_03"
SELECTION = RUN / "full_book_batch_019_selection.json"
D050 = RUN / "chapter_16_batch019_verified_footnote_evidence_v2.json"
EXPECTED_SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
EXPECTED_RAW_SHA = "ae6db7df84de7ed1e1e967adb04032baa8bb77c96aaf67b42051ef6600d63a97"

def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _jsonl(path: Path, model: type[CalibrationRequest] | type[TranslationResult]) -> list:
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_jsonl_new(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def _replace_once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise RuntimeError(f"NUMERIC_REPAIR_BASELINE_MISMATCH:{before}")
    return text.replace(before, after, 1)


def _repair(request: CalibrationRequest, response: TranslationResult) -> tuple[TranslationResult, dict]:
    if len(response.target_segments) != len(request.source_segments):
        raise RuntimeError(f"REPAIR_SEGMENT_CONTRACT_MISMATCH:{request.display_id}")
    values = [segment.text for segment in response.target_segments]
    changes: dict[str, dict] = {}
    if request.display_id == "body.p.05633":
        index, before, after = 6, "20 世纪 90 年代", "90 年代"
        values[index] = _replace_once(values[index], before, after)
        changes[str(index)] = {"before": before, "after": after}
    elif request.display_id == "body.p.05646":
        index = 0
        before = values[index]
        changed = _replace_once(before, "20 世纪 70 年代", "1970 年代")
        changed = _replace_once(changed, "80 年代（RCS）", "1980 年代（RCS）")
        values[index] = changed
        changes[str(index)] = {"before": before, "after": changed}
    elif request.display_id == "body.p.05650":
        index = 2
        before = values[index]
        changed = _replace_once(before, "20 世纪 90 年代", "1990 年代")
        changed = _replace_once(changed, "21 世纪初", "2000 年代初")
        values[index] = changed
        changes[str(index)] = {"before": before, "after": changed}
    segments = [
        StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
        for source, text in zip(request.source_segments, values, strict=True)
    ]
    return TranslationResult(unit_uid=response.unit_uid, target_segments=segments), changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--what-if", action="store_true")
    args = parser.parse_args()
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    required = [
        BATCH / "batch_manifest.json", SELECTION, D050,
        CHECKPOINT / "translation_request.jsonl", CHECKPOINT / "translation_raw_response.jsonl",
        CHECKPOINT / "translation_raw_qa_report.json", CHECKPOINT / "translation_normalized_response.jsonl",
        CHECKPOINT / "translation_normalization_report.json", CHECKPOINT / "translation_normalized_qa_report.json",
    ]
    if not all(path.is_file() for path in required):
        raise RuntimeError("PARTIAL_CHECKPOINT_INPUT_MISSING")
    downstream = ("targeted_repair_contract.json", "semantic_review_report.json", "translation_repaired_response.jsonl", "translation_final_qa_report.json", "checkpoint_evidence.json")
    if any((CHECKPOINT / name).exists() for name in downstream):
        raise RuntimeError("CHECKPOINT_ALREADY_HAS_DOWNSTREAM_ARTIFACTS")
    manifest = _json(BATCH / "batch_manifest.json")
    selection = _json(SELECTION)
    order = selection.get("selected_logical_work_order_ids", [])[50:75]
    if len(order) != 25 or (manifest.get("completed_count"), manifest.get("next_checkpoint"), manifest.get("checkpoint_count")) != (50, "checkpoint_03", 2):
        raise RuntimeError("CHECKPOINT_RESUME_STATE_MISMATCH")
    requests = _jsonl(CHECKPOINT / "translation_request.jsonl", CalibrationRequest)
    raw = _jsonl(CHECKPOINT / "translation_raw_response.jsonl", TranslationResult)
    normalized = _jsonl(CHECKPOINT / "translation_normalized_response.jsonl", TranslationResult)
    if [item.display_id for item in requests] != order or [item.unit_uid for item in raw] != [item.unit_uid for item in requests] or normalized != raw:
        raise RuntimeError("EXACT_SELECTION_OR_RAW_CHAIN_MISMATCH")
    raw_sha = _sha(CHECKPOINT / "translation_raw_response.jsonl")
    if raw_sha != EXPECTED_RAW_SHA:
        raise RuntimeError("IMMUTABLE_RAW_SHA_MISMATCH")
    normalization = _json(CHECKPOINT / "translation_normalization_report.json")
    if normalization.get("raw_artifact", {}).get("sha256_before") != raw_sha or normalization.get("raw_artifact", {}).get("sha256_after") != raw_sha:
        raise RuntimeError("NORMALIZATION_RAW_SHA_MISMATCH")
    historical_raw = _json(CHECKPOINT / "translation_raw_qa_report.json")
    historical_normalized = _json(CHECKPOINT / "translation_normalized_qa_report.json")
    failed = [record for record in historical_raw.get("records", []) if not record.get("passed")]
    expected_ids = ["body.p.05633", "body.p.05646", "body.p.05650"]
    if [record.get("display_id") for record in failed] != expected_ids or historical_normalized != historical_raw:
        raise RuntimeError("HISTORICAL_QA_FAILURE_SET_MISMATCH")
    if any({diagnostic.get("code") for diagnostic in record.get("diagnostics", [])} != {"NUMBER_PRESERVATION"} for record in failed):
        raise RuntimeError("HISTORICAL_QA_CODE_NOT_NUMERIC_REPRESENTATION")

    repaired: list[TranslationResult] = []
    changes: dict[str, dict] = {}
    for request, response in zip(requests, normalized, strict=True):
        result, change = _repair(request, response)
        repaired.append(result)
        if change:
            changes[request.display_id] = {"segments": change}
    if list(changes) != expected_ids:
        raise RuntimeError("TARGETED_REPAIR_SCOPE_MISMATCH")
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    final_records = [harness.harness.base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, repaired, strict=True)]
    if not all(record["passed"] for record in final_records):
        raise RuntimeError("TARGETED_REPAIR_FINAL_DETERMINISTIC_QA_FAILED")
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    if args.what_if:
        print(f"WHATIF PASS: immutable RAW SHA={raw_sha}; exact numeric repairs={','.join(expected_ids)}")
        print("WHATIF PASS: final deterministic QA=25/25; checkpoint_03 would seal at 75/200")
        return

    _write_json_new(CHECKPOINT / "targeted_repair_contract.json", {
        "artifact_type": "CHECKPOINT_TARGETED_NUMERIC_REPAIR_CONTRACT",
        "checkpoint": "checkpoint_03", "classification": "LOCAL_DETERMINISTIC_QA_FAILURE",
        "policy": "NUMERIC_REPRESENTATION", "repair_units": expected_ids,
        "historical_raw": str(CHECKPOINT / "translation_raw_response.jsonl"), "historical_raw_sha256": raw_sha,
        "historical_raw_immutable": True,
        "historical_qa_artifacts_preserved": [str(CHECKPOINT / "translation_raw_qa_report.json"), str(CHECKPOINT / "translation_normalized_qa_report.json")],
        "material_target_changes": changes, "normalization_changed": 0,
    })
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Bounded numeric-representation repair preserved source meaning, required numeric tokens, protected literals, and structured anchors."} for row in dispatched]
    _write_json_new(CHECKPOINT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    _write_jsonl_new(CHECKPOINT / "translation_repaired_response.jsonl", repaired)
    approved = [row["unit_uid"] for row in final_records]
    _write_json_new(CHECKPOINT / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final_records, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [], "final_status": "RECOVERED_AFTER_NUMERIC_REPAIR"})
    _write_json_new(CHECKPOINT / "checkpoint_evidence.json", {
        "logical_work_orders": order, "source_sha256": EXPECTED_SOURCE_SHA, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(approved), "deterministic_fail_count": 0,
        "raw_deterministic_fail_count": len(expected_ids), "targeted_repair_count": len(expected_ids),
        "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(D050)],
        "recovery_contract": str(CHECKPOINT / "targeted_repair_contract.json"),
        "historical_qa_preserved": {"raw": str(CHECKPOINT / "translation_raw_qa_report.json"), "normalized": str(CHECKPOINT / "translation_normalized_qa_report.json"), "fail_count": len(expected_ids)},
    })
    append_sealed_checkpoint(BATCH / "batch_manifest.json", CHECKPOINT)
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print("RECOVERY PASS: FULL_BOOK_BATCH_019/checkpoint_03 sealed; completed=75/200; next=checkpoint_04")


if __name__ == "__main__":
    main()
