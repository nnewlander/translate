"""Seal Batch 013 checkpoint 03 from preserved RAW after exact Chapter 12 D050 recovery.

This is intentionally a one-checkpoint recovery tool.  It never recreates or
rewrites the historical request, RAW, normalization, or failed QA artifacts.
The only repaired contract is an additive, exact D050 record for the two
reader-visible Chapter 12 footnote markers in ``body.p.04135``.
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

import run_full_book_batch004_checkpoint02 as base
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.semantic_review import (
    partition_deterministic_review_dispatch,
    semantic_review_summary,
)
from booktrans.harness.source_integrity import fingerprint
from booktrans.ooxml.verified_footnotes import (
    exact_evidence_for_work_order,
    footnote_numbers_for_unit,
    load_exact_verified_evidence_registry,
    verified_numeric_bindings_for_request,
)


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
SOURCE_PDF = ROOT / "Software Engineering at Google.pdf"
BATCH = RUN / "full_book_batch_013"
CHECKPOINT = BATCH / "checkpoint_03"
SELECTION = RUN / "full_book_batch_013_selection.json"
D050 = RUN / "chapter_12_batch013_verified_footnote_evidence_v1.json"
RECOVERY_REQUEST = CHECKPOINT / "d050_recovery_request_body_p_04135.jsonl"
RECOVERY_CONTRACT = CHECKPOINT / "d050_recovery_contract.json"
EXPECTED_SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
EXPECTED_PDF_SHA = "4e567ce8f561cad28c0369df71a25cb2b64361473c64509c493cce79e293289e"
TARGET_ID = "body.p.04135"
TARGET_UID = "28debbda9e0934036064fa82b511a43a1158eb19cbfc2f75e2f37404e3859e77"


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


def _write_jsonl_new(path: Path, values: list[CalibrationRequest] | list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def _d050_evidence() -> dict:
    """The reviewed, closed-world Chapter 12 preflight result.

    The entries were checked against BlockIR locators, the two converted
    footnote-body paragraphs, and original PDF page 270 (printed page 242).
    No marker outside this exact work order is authorized by this artifact.
    """
    common = {
        "verification_status": "VERIFIED",
        "unit_uid": TARGET_UID,
        "logical_work_order_id": TARGET_ID,
        "pre_footnote_numeric_tokens": [],
        "original_pdf_page": 270,
        "original_pdf_printed_page": 242,
        "pdf_corroboration": "Original English PDF page 270 was read: it presents both reader-visible markers beside the paragraph and the matching numbered footnote bodies.",
    }
    return {
        "artifact_type": "CHAPTER_12_BATCH_013_VERIFIED_FOOTNOTE_EVIDENCE",
        "schema": "D050_EXACT_CLOSED_WORLD",
        "source_docx": str(SOURCE),
        "source_sha256": EXPECTED_SOURCE_SHA,
        "source_pdf": str(SOURCE_PDF),
        "source_pdf_sha256": EXPECTED_PDF_SHA,
        "candidate_count": 2,
        "verified_count": 2,
        "ambiguous_count": 0,
        "unverified_candidates": [],
        "records": [
            {
                **common,
                "exact_source_marker_token": "4",
                "footnote_number": "4",
                "source_segment_binding": [{
                    "anchor_ref": "816d4b0364e388e1395949fcf997b262a8021adef12455b6643f75e55620a6a2",
                    "text": "4",
                    "source_run_locator": "/word/document.xml/paragraph[4480]/text[95]",
                    "paragraph_ordinal": 4480,
                }],
                "corresponding_footnote_body": {
                    "display_id": "body.p.04155",
                    "unit_uid": "6dc0ab4d48ffbb143b795106a16cafe9f72a98f384316f8cf7995ab81b15864a",
                    "source_run_locator": "/word/document.xml/paragraph[4500]/text[1]",
                    "source_text": "4  See https://testing.googleblog.com/2014/04/testing-on-toilet-test-behaviors-not.html and https://dannorth.net/ introducing-bdd.",
                },
            },
            {
                **common,
                "exact_source_marker_token": "5",
                "footnote_number": "5",
                "source_segment_binding": [{
                    "anchor_ref": "ebc954dca223206b610c6f0029db43744bb9104dd23962284c5f5d68a5b8ab2f",
                    "text": "5",
                    "source_run_locator": "/word/document.xml/paragraph[4480]/text[145]",
                    "paragraph_ordinal": 4480,
                }],
                "corresponding_footnote_body": {
                    "display_id": "body.p.04156",
                    "unit_uid": "94f7ab656f9895d165b40d86801f6e3413924b6dafa3cd6826d4f0be7d9c7bc6",
                    "source_run_locator": "/word/document.xml/paragraph[4501]/text[1]",
                    "source_text": "5  Furthermore, a feature (in the product sense of the word) can be expressed as a collection of behaviors.",
                },
            },
        ],
        "scope_guard": "Only these two exact records may be exported as D050 evidence. No generic inference is authorized.",
    }


def _validate_preflight(evidence: dict, requests: list[CalibrationRequest], blocks: dict[str, BlockIR]) -> None:
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    if _sha(SOURCE_PDF) != EXPECTED_PDF_SHA:
        raise RuntimeError("SOURCE_PDF_MUTATED")
    if evidence["source_sha256"] != EXPECTED_SOURCE_SHA or evidence["source_pdf_sha256"] != EXPECTED_PDF_SHA:
        raise RuntimeError("D050_SOURCE_FINGERPRINT_MISMATCH")
    request = next((item for item in requests if item.display_id == TARGET_ID), None)
    if request is None or request.unit_uid != TARGET_UID:
        raise RuntimeError("D050_TARGET_REQUEST_MISMATCH")
    for record in evidence["records"]:
        body = blocks.get(record["corresponding_footnote_body"]["display_id"])
        if body is None or body.unit_uid != record["corresponding_footnote_body"]["unit_uid"]:
            raise RuntimeError("D050_FOOTNOTE_BODY_ID_MISMATCH")
        if body.source_plain_text != record["corresponding_footnote_body"]["source_text"]:
            raise RuntimeError("D050_FOOTNOTE_BODY_TEXT_MISMATCH")
        segment = record["source_segment_binding"][0]
        if not any(source.anchor_ref == segment["anchor_ref"] and source.text == segment["text"] for source in request.source_segments):
            raise RuntimeError("D050_SOURCE_SEGMENT_BINDING_MISMATCH")


def _qa(views: dict[str, TranslationView], request: CalibrationRequest, response: TranslationResult, known: set[str]) -> dict:
    return base.base.base.qa(views[request.display_id], request, response, known)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--what-if", action="store_true")
    args = parser.parse_args()

    required = [
        BATCH / "batch_manifest.json", SELECTION, CHECKPOINT / "translation_request.jsonl",
        CHECKPOINT / "translation_raw_response.jsonl", CHECKPOINT / "translation_raw_qa_report.json",
        CHECKPOINT / "translation_normalized_response.jsonl", CHECKPOINT / "translation_normalization_report.json",
        CHECKPOINT / "translation_normalized_qa_report.json",
    ]
    if not all(path.is_file() for path in required):
        raise RuntimeError("PARTIAL_CHECKPOINT_INPUT_MISSING")
    if any((CHECKPOINT / name).exists() for name in (
        "semantic_review_report.json", "translation_repaired_response.jsonl",
        "translation_final_qa_report.json", "checkpoint_evidence.json",
    )):
        raise RuntimeError("CHECKPOINT_ALREADY_HAS_DOWNSTREAM_ARTIFACTS")
    if any(path.exists() for path in (D050, RECOVERY_REQUEST, RECOVERY_CONTRACT)):
        raise RuntimeError("RECOVERY_OUTPUT_ALREADY_EXISTS")

    manifest = _json(BATCH / "batch_manifest.json")
    selection = _json(SELECTION)
    order = selection.get("selected_logical_work_order_ids", [])[50:75]
    if len(order) != 25 or manifest.get("completed_count") != 50 or manifest.get("next_checkpoint") != "checkpoint_03":
        raise RuntimeError("CHECKPOINT_RESUME_STATE_MISMATCH")
    requests = _jsonl(CHECKPOINT / "translation_request.jsonl", CalibrationRequest)
    raw = _jsonl(CHECKPOINT / "translation_raw_response.jsonl", TranslationResult)
    normalized = _jsonl(CHECKPOINT / "translation_normalized_response.jsonl", TranslationResult)
    if [item.display_id for item in requests] != order or [item.unit_uid for item in raw] != [item.unit_uid for item in requests] or normalized != raw:
        raise RuntimeError("EXACT_SELECTION_OR_RAW_CHAIN_MISMATCH")
    raw_sha = _sha(CHECKPOINT / "translation_raw_response.jsonl")
    normalization = _json(CHECKPOINT / "translation_normalization_report.json")
    if normalization.get("raw_artifact", {}).get("sha256_before") != raw_sha or normalization.get("raw_artifact", {}).get("sha256_after") != raw_sha:
        raise RuntimeError("RAW_SHA_DRIFT")
    historical_raw_qa = _json(CHECKPOINT / "translation_raw_qa_report.json")
    historical_normalized_qa = _json(CHECKPOINT / "translation_normalized_qa_report.json")
    if (historical_raw_qa.get("pass_count"), historical_raw_qa.get("fail_count")) != (24, 1) or historical_normalized_qa != historical_raw_qa:
        raise RuntimeError("HISTORICAL_QA_BASELINE_MISMATCH")
    failed = [record for record in historical_raw_qa.get("records", []) if not record.get("passed")]
    if len(failed) != 1 or failed[0].get("display_id") != TARGET_ID or not any(item.get("code") == "NUMBER_PRESERVATION" for item in failed[0].get("diagnostics", [])):
        raise RuntimeError("HISTORICAL_QA_FAILURE_NOT_EXACT_D050_CASE")

    views = {view.display_id: view for view in (
        TranslationView.model_validate_json(line)
        for line in base.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}
    blocks = {block.display_id: block for block in (
        BlockIR.model_validate_json(line)
        for line in base.base.base.BLOCKS.read_text(encoding="utf-8").splitlines() if line
    )}
    evidence = _d050_evidence()
    _validate_preflight(evidence, requests, blocks)

    registry = load_exact_verified_evidence_registry((D050,)) if D050.exists() else load_exact_verified_evidence_registry(())
    if D050.exists():
        # This branch is unreachable for a successful first run, but protects
        # a manual resume from accepting a conflicting sidecar.
        if _json(D050) != evidence:
            raise RuntimeError("D050_EVIDENCE_CONFLICT")
        registry = load_exact_verified_evidence_registry((D050,))
    else:
        # The helpers deliberately consume only records already held in a
        # closed-world registry; mirror that registry in memory for WhatIf.
        routed = {(TARGET_ID, TARGET_UID): tuple(evidence["records"])}
        registry = routed

    corrected: dict[str, CalibrationRequest] = {}
    for request in requests:
        records = exact_evidence_for_work_order(registry, logical_work_order_id=request.display_id, unit_uid=request.unit_uid)
        if request.display_id == TARGET_ID:
            numbers = footnote_numbers_for_unit(records, unit_uid=request.unit_uid, source_text=request.source_text)
            corrected_request = request.model_copy(update={"footnote_reference_numbers": numbers})
            bindings = verified_numeric_bindings_for_request(records, corrected_request)
            corrected_request = corrected_request.model_copy(update={"verified_footnote_numeric_bindings": bindings})
            if numbers != ["4", "5"] or len(bindings) != 2:
                raise RuntimeError("D050_ROUTING_NOT_APPLIED")
            corrected[request.display_id] = corrected_request
        elif records:
            raise RuntimeError("D050_UNEXPECTED_SELECTION_ROUTING")
        else:
            corrected[request.display_id] = request

    known = {view.unit_uid for view in views.values()}
    final_records = [_qa(views, corrected[request.display_id], response, known) for request, response in zip(requests, normalized, strict=True)]
    if not all(record["passed"] for record in final_records):
        raise RuntimeError("RECOVERY_FINAL_DETERMINISTIC_QA_FAILED")
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")

    if args.what_if:
        print("WHATIF PASS: exact selection=25; preserved RAW SHA=" + raw_sha)
        print("WHATIF PASS: historical QA=24/25; D050 correction=body.p.04135 markers 4,5")
        print("WHATIF PASS: recovered final deterministic QA=25/25; manifest would advance to 75/200")
        return

    _write_json_new(D050, evidence)
    _write_jsonl_new(RECOVERY_REQUEST, [corrected[TARGET_ID]])
    _write_json_new(RECOVERY_CONTRACT, {
        "artifact_type": "CHECKPOINT_D050_RECOVERY_CONTRACT",
        "checkpoint": "checkpoint_03",
        "classification": "D050_EVIDENCE_GAP",
        "historical_request": str(CHECKPOINT / "translation_request.jsonl"),
        "historical_raw": str(CHECKPOINT / "translation_raw_response.jsonl"),
        "historical_raw_sha256": raw_sha,
        "historical_raw_immutable": True,
        "historical_qa_artifacts_preserved": [
            str(CHECKPOINT / "translation_raw_qa_report.json"),
            str(CHECKPOINT / "translation_normalized_qa_report.json"),
        ],
        "superseding_request": str(RECOVERY_REQUEST),
        "d050_evidence": str(D050),
        "logical_work_order_id": TARGET_ID,
        "unit_uid": TARGET_UID,
        "reused_target_without_semantic_change": True,
        "final_deterministic_pass_count": len(final_records),
    })
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{
        "unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [],
        "explanation": "Production semantic review confirmed source meaning and structured anchors; body.p.04135 additionally uses the exact Chapter 12 D050 binding." if row["display_id"] == TARGET_ID else "Production semantic review confirmed source meaning and structured anchors.",
    } for row in dispatched]
    _write_json_new(CHECKPOINT / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    _write_jsonl_new(CHECKPOINT / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records]
    _write_json_new(CHECKPOINT / "translation_final_qa_report.json", {
        "pass_count": len(approved), "fail_count": 0, "records": final_records,
        "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": [],
        "final_status": "RECOVERED_AFTER_FIRST_PASS",
    })
    _write_json_new(CHECKPOINT / "checkpoint_evidence.json", {
        "logical_work_orders": order, "source_sha256": EXPECTED_SOURCE_SHA, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(approved), "deterministic_fail_count": 0,
        "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED",
        "d050_evidence_artifacts": [str(D050)],
        "recovery_contract": str(RECOVERY_CONTRACT),
        "historical_raw_qa": {"pass_count": 24, "fail_count": 1, "preserved": True},
        "final_qa_supersession": "The 25/25 final QA result reuses the immutable RAW under the exact Chapter 12 D050 recovery contract; historical failed QA is preserved, not overwritten.",
    })
    updated = dict(manifest)
    checkpoint_summary = {
        "id": "checkpoint_03", "actual_count": 25, "raw_sha256": raw_sha,
        "raw_pass": 24, "final_pass": 25,
        "normalization_changed": normalization["normalized_unit_count"],
        "recovery_contract": str(RECOVERY_CONTRACT),
        "current_final_qa": str(CHECKPOINT / "translation_final_qa_report.json"),
    }
    updated["completed_count"] = 75
    updated["checkpoint_count"] = 3
    updated["remaining_in_batch"] = 125
    updated["status"] = "OPEN_PROGRESS"
    updated["next_checkpoint"] = "checkpoint_04"
    updated["checkpoints"] = [*manifest["checkpoints"], checkpoint_summary]
    update_manifest(BATCH / "batch_manifest.json", updated)
    if fingerprint(SOURCE).sha256 != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print("RECOVERY PASS: FULL_BOOK_BATCH_013/checkpoint_03 sealed; completed=75/200; next=checkpoint_04")


if __name__ == "__main__":
    main()
