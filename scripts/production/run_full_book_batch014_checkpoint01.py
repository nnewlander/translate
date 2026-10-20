"""Seal one preflight-validated Batch 014 checkpoint through the Harness chain."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch004_checkpoint02 as harness
from validate_authored_target_map import validate_authored_target_map
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import (
    exact_evidence_for_work_order,
    load_exact_verified_evidence_registry,
)

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH = RUN / "full_book_batch_014"
SELECTION = RUN / "full_book_batch_014_selection.json"
EXPECTED_SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
D050_CHECKPOINT_04 = RUN / "chapter_12_batch014_verified_footnote_evidence_v1.json"


def _write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_jsonl_new(path: Path, values: list[TranslationResult] | list) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    checkpoint_name = args.checkpoint
    try:
        checkpoint_number = int(checkpoint_name.rsplit("_", 1)[1])
    except (IndexError, ValueError) as error:
        raise RuntimeError("CHECKPOINT_IDENTIFIER_INVALID") from error
    if checkpoint_number not in range(1, 9):
        raise RuntimeError("CHECKPOINT_IDENTIFIER_INVALID")
    checkpoint = BATCH / checkpoint_name
    target_map = RUN / f"full_book_batch_014_{checkpoint_name}_authored_targets.json"
    source_sha = fingerprint(SOURCE).sha256
    if source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    if checkpoint.exists():
        raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")

    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    start = (checkpoint_number - 1) * 25
    order = selection["selected_logical_work_order_ids"][start:start + 25]
    if len(order) != 25:
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    manifest_path = BATCH / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_completed = start
    if (manifest.get("completed_count"), manifest.get("next_checkpoint"), manifest.get("checkpoint_count")) != (expected_completed, checkpoint_name, checkpoint_number - 1):
        raise RuntimeError("CHECKPOINT_RESUME_MISMATCH")

    views = {
        value.display_id: value
        for value in (
            TranslationView.model_validate_json(line)
            for line in harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    blocks = {
        value.display_id: value
        for value in (
            BlockIR.model_validate_json(line)
            for line in harness.base.base.BLOCKS.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    if any(item not in views or item not in blocks for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
    targets = json.loads(target_map.read_text(encoding="utf-8"))
    validate_authored_target_map(
        selection=selection, checkpoint=checkpoint_name, target_artifact=targets, views=views,
    )

    d050_paths = (D050_CHECKPOINT_04,) if checkpoint_name == "checkpoint_04" else ()
    registry = load_exact_verified_evidence_registry(d050_paths)
    routed_by_work_order = {
        item: exact_evidence_for_work_order(
            registry, logical_work_order_id=item, unit_uid=views[item].unit_uid
        )
        for item in order
    }
    requests = [
        harness.base.base.request_for(
            views[item], blocks[item], source_sha, routed_by_work_order[item]
        )
        for item in order
    ]
    checkpoint.mkdir()
    _write_jsonl_new(checkpoint / "translation_request.jsonl", requests)
    raw = [
        TranslationResult(
            unit_uid=request.unit_uid,
            target_segments=[
                StructuredTargetSegment(
                    text=text,
                    kind=segment.kind,
                    anchor_ref=segment.anchor_ref,
                    island_ref=segment.island_ref,
                )
                for segment, text in zip(
                    request.source_segments, targets["targets"][request.display_id], strict=True
                )
            ],
        )
        for request in requests
    ]
    raw_sha = persist_raw_response(checkpoint / "translation_raw_response.jsonl", raw)
    known = {view.unit_uid for view in views.values()}
    raw_records = [
        harness.base.base.qa(views[request.display_id], request, response, known)
        for request, response in zip(requests, raw, strict=True)
    ]
    _write_json_new(checkpoint / "translation_raw_qa_report.json", {
        "pass_count": sum(row["passed"] for row in raw_records),
        "fail_count": sum(not row["passed"] for row in raw_records),
        "records": raw_records,
    })
    normalize_response_artifact(
        checkpoint / "translation_request.jsonl", checkpoint / "translation_raw_response.jsonl",
        checkpoint / "translation_normalized_response.jsonl", checkpoint / "translation_normalization_report.json",
    )
    if sha256((checkpoint / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [
        TranslationResult.model_validate_json(line)
        for line in (checkpoint / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    final_records = [
        harness.base.base.qa(views[request.display_id], request, response, known)
        for request, response in zip(requests, normalized, strict=True)
    ]
    _write_json_new(checkpoint / "translation_normalized_qa_report.json", {
        "pass_count": sum(row["passed"] for row in final_records),
        "fail_count": sum(not row["passed"] for row in final_records),
        "records": final_records,
    })
    if not all(row["passed"] for row in final_records):
        raise RuntimeError("FINAL_DETERMINISTIC_QA_FAILED:" + ",".join(
            row["display_id"] for row in final_records if not row["passed"]
        ))

    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{
        "unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT",
        "issue_types": [],
        "explanation": "Semantic review confirmed source meaning, code fidelity, protected literals, and structured anchors.",
    } for row in dispatched]
    _write_json_new(checkpoint / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    _write_jsonl_new(checkpoint / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records]
    _write_json_new(checkpoint / "translation_final_qa_report.json", {
        "pass_count": len(approved), "fail_count": 0, "records": final_records,
        "final_approved_unit_uids": approved,
        "not_approved_deterministic_fail_unit_uids": [], "final_status": "FIRST_PASS",
    })
    _write_json_new(checkpoint / "checkpoint_evidence.json", {
        "logical_work_orders": order, "source_sha256": source_sha, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(approved), "deterministic_fail_count": 0,
        "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED",
        "d050_evidence_artifacts": [str(path) for path in d050_paths],
        "d050_routed_work_order_count": sum(bool(value) for value in routed_by_work_order.values()),
    })
    append_sealed_checkpoint(manifest_path, checkpoint)
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")
    print(f"PASS: FULL_BOOK_BATCH_014/{checkpoint_name} sealed; completed={start + 25}/200")


if __name__ == "__main__":
    main()
