"""Seal Batch 020 checkpoint 02 through one exact immutable-RAW DOI repair."""

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
BATCH = RUN / "full_book_batch_020"
CHECKPOINT = BATCH / "checkpoint_02"
SELECTION = RUN / "full_book_batch_020_selection.json"
D050 = RUN / "chapter_17_batch020_checkpoint02_verified_footnote_evidence_v1.json"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
RAW_SHA = "92caad8899cf5d3aef161daf197ba56e3967a40df9a4c89fb313ceaacbb8cb63"
FAILURE = "body.p.05951"
DOI = "https://doi.org/10.1145/2786805.2786855"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def load_jsonl(path: Path, model: type[CalibrationRequest] | type[TranslationResult]) -> list:
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl_new(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def repair(request: CalibrationRequest, response: TranslationResult) -> tuple[TranslationResult, dict]:
    if request.display_id != FAILURE:
        return response, {}
    if len(request.source_segments) != len(response.target_segments):
        raise RuntimeError("DOI_REPAIR_SEGMENT_COUNT_MISMATCH")
    if len(request.source_segments) <= 4:
        raise RuntimeError("DOI_REPAIR_SEGMENT_LAYOUT_MISMATCH")
    source_url, source_suffix = request.source_segments[3], request.source_segments[4]
    target_url, target_suffix = response.target_segments[3], response.target_segments[4]
    if (source_url.kind, source_url.text, source_suffix.text, target_url.text, target_suffix.text) != ("protected", DOI, ".", DOI, "。"):
        raise RuntimeError("DOI_REPAIR_BASELINE_MISMATCH")
    text = [segment.text for segment in response.target_segments]
    text[4] = "."
    repaired = TranslationResult(
        unit_uid=response.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=value, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
            for source, value in zip(request.source_segments, text, strict=True)
        ],
    )
    return repaired, {"segments": {"4": {"before": "。", "after": ".", "source_bound_doi": DOI}}}


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
    manifest, selection = load_json(BATCH / "batch_manifest.json"), load_json(SELECTION)
    order = selection.get("selected_logical_work_order_ids", [])[25:50]
    if len(order) != 25 or FAILURE not in order or (manifest.get("completed_count"), manifest.get("checkpoint_count"), manifest.get("next_checkpoint")) != (25, 1, "checkpoint_02"):
        raise RuntimeError("CHECKPOINT_RESUME_STATE_MISMATCH")
    requests = load_jsonl(CHECKPOINT / "translation_request.jsonl", CalibrationRequest)
    raw = load_jsonl(CHECKPOINT / "translation_raw_response.jsonl", TranslationResult)
    normalized = load_jsonl(CHECKPOINT / "translation_normalized_response.jsonl", TranslationResult)
    if [item.display_id for item in requests] != order or [item.unit_uid for item in raw] != [item.unit_uid for item in requests] or normalized != raw:
        raise RuntimeError("EXACT_SELECTION_OR_RAW_CHAIN_MISMATCH")
    raw_sha = sha256((CHECKPOINT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_sha != RAW_SHA:
        raise RuntimeError("IMMUTABLE_RAW_SHA_MISMATCH")
    normalization = load_json(CHECKPOINT / "translation_normalization_report.json")
    if normalization.get("raw_artifact", {}).get("sha256_before") != raw_sha or normalization.get("raw_artifact", {}).get("sha256_after") != raw_sha:
        raise RuntimeError("NORMALIZATION_RAW_SHA_MISMATCH")
    historical = load_json(CHECKPOINT / "translation_raw_qa_report.json")
    if historical != load_json(CHECKPOINT / "translation_normalized_qa_report.json"):
        raise RuntimeError("HISTORICAL_QA_REPORT_CONFLICT")
    failed = [item for item in historical.get("records", []) if not item.get("passed")]
    if [item.get("display_id") for item in failed] != [FAILURE] or any({diagnostic.get("code") for diagnostic in item.get("diagnostics", [])} != {"DOI_PRESERVATION"} for item in failed):
        raise RuntimeError("HISTORICAL_QA_FAILURE_SET_MISMATCH")
    repaired, changes = [], {}
    for request, response in zip(requests, normalized, strict=True):
        result, change = repair(request, response)
        repaired.append(result)
        if change:
            changes[request.display_id] = change
    if list(changes) != [FAILURE]:
        raise RuntimeError("TARGETED_REPAIR_SCOPE_MISMATCH")
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    final = [harness.harness.base.base.qa(views[request.display_id], request, response, known) for request, response in zip(requests, repaired, strict=True)]
    if not all(item["passed"] for item in final):
        raise RuntimeError("TARGETED_REPAIR_FINAL_DETERMINISTIC_QA_FAILED")
    if args.what_if:
        print("WHATIF PASS: RAW preserved; DOI punctuation-boundary repair=body.p.05951; final QA=25/25")
        return
    write_json_new(CHECKPOINT / "targeted_repair_contract.json", {
        "artifact_type": "CHECKPOINT_TARGETED_DOI_PRESERVATION_REPAIR_CONTRACT", "checkpoint": "checkpoint_02",
        "classification": "LOCAL_DETERMINISTIC_QA_FAILURE", "policy": "DOI_PRESERVATION", "repair_units": [FAILURE],
        "historical_raw": str(CHECKPOINT / "translation_raw_response.jsonl"), "historical_raw_sha256": raw_sha,
        "historical_raw_immutable": True, "historical_qa_artifacts_preserved": [str(CHECKPOINT / "translation_raw_qa_report.json"), str(CHECKPOINT / "translation_normalized_qa_report.json")],
        "source_doi_url": DOI, "material_target_changes": changes, "normalization_changed": 0,
    })
    dispatch, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Bounded DOI punctuation-boundary repair preserves the exact protected source DOI, source meaning, verified D050 bindings, and anchors."} for row in dispatch]
    write_json_new(CHECKPOINT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    write_jsonl_new(CHECKPOINT / "translation_repaired_response.jsonl", repaired)
    approved = [row["unit_uid"] for row in final]
    write_json_new(CHECKPOINT / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [], "final_status": "RECOVERED_AFTER_DOI_PRESERVATION_REPAIR"})
    write_json_new(CHECKPOINT / "checkpoint_evidence.json", {"logical_work_orders": order, "source_sha256": SOURCE_SHA, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": 0, "raw_deterministic_fail_count": 1, "targeted_repair_count": 1, "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": [str(D050)], "recovery_contract": str(CHECKPOINT / "targeted_repair_contract.json")})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", CHECKPOINT)
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print("RECOVERY PASS: FULL_BOOK_BATCH_020/checkpoint_02 sealed; completed=50/200; next=checkpoint_03")


if __name__ == "__main__":
    main()
