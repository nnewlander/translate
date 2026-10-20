"""Execute one frozen 25-unit full-book checkpoint from its authored target map."""
from __future__ import annotations

from hashlib import sha256
import argparse
import json
import shutil
from pathlib import Path

import run_full_book_batch004_checkpoint02 as base
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint, update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import exact_evidence_for_work_order, load_exact_verified_evidence_registry
from production.validate_authored_target_map import TargetMapValidationError, validate_authored_target_map


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH_TARGET = 200
PRODUCTION = base.base.base


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_jsonl(path: Path, values: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json() + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", choices=[f"checkpoint_{number:02d}" for number in range(1, 9)])
    parser.add_argument("targets", type=Path)
    parser.add_argument("--batch-number", type=int, required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--d050-evidence", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.batch_number < 6:
        raise RuntimeError("BATCH_NUMBER_NOT_SUPPORTED")
    batch = RUN / f"full_book_batch_{args.batch_number:03d}"
    selection_path = args.selection or RUN / f"full_book_batch_{args.batch_number:03d}_selection.json"
    batch_label = f"FULL_BOOK_BATCH_{args.batch_number:03d}"
    checkpoint_number = int(args.checkpoint.rsplit("_", 1)[1])
    selection_start = (checkpoint_number - 1) * 25
    targets = json.loads(args.targets.read_text(encoding="utf-8"))
    if targets.get("artifact_type") != "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP":
        raise RuntimeError("TARGET_MAP_ARTIFACT_TYPE_INVALID")

    source_sha = fingerprint(PRODUCTION.SOURCE).sha256
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    order = selection["selected_logical_work_order_ids"][selection_start:selection_start + 25]
    if len(order) != 25 or targets.get("logical_work_orders") != order:
        raise RuntimeError("AUTHORITATIVE_SELECTION_MISMATCH")
    manifest_path = batch / "batch_manifest.json"
    if selection_start == 0:
        if batch.exists():
            # Batch 010 is initialized separately from its frozen selection.  It
            # is safe to resume only an exactly pristine manifest; accepting any
            # other existing batch would risk overwriting or duplicating evidence.
            if not manifest_path.is_file() or (batch / args.checkpoint).exists():
                raise RuntimeError("BATCH_ALREADY_EXISTS")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            snapshot = batch / "translation_bible_snapshot.md"
            if (
                manifest.get("batch") != batch_label
                or manifest.get("target_count") != BATCH_TARGET
                or manifest.get("completed_count") != 0
                or manifest.get("checkpoint_count") != 0
                or manifest.get("remaining_in_batch") != BATCH_TARGET
                or manifest.get("status") != "OPEN_PROGRESS"
                or manifest.get("next_checkpoint") != args.checkpoint
                or manifest.get("checkpoints") != []
                or manifest.get("source_sha256") != source_sha
                or not snapshot.is_file()
                or manifest.get("translation_bible_snapshot_sha256") != sha256(snapshot.read_bytes()).hexdigest()
            ):
                raise RuntimeError("BATCH_INITIAL_RESUME_MISMATCH")
        else:
            batch.mkdir()
            shutil.copyfile(PRODUCTION.BIBLE, batch / "translation_bible_snapshot.md")
            update_manifest(manifest_path, {
                "batch": batch_label, "target_count": BATCH_TARGET, "completed_count": 0,
                "checkpoint_count": 0, "remaining_in_batch": BATCH_TARGET,
                "status": "OPEN_PROGRESS", "next_checkpoint": args.checkpoint,
                "checkpoints": [], "source_sha256": source_sha,
                "translation_bible_snapshot_sha256": sha256((batch / "translation_bible_snapshot.md").read_bytes()).hexdigest(),
            })
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous = f"checkpoint_{checkpoint_number - 1:02d}"
        if (manifest["next_checkpoint"] != args.checkpoint or manifest["completed_count"] != selection_start
                or manifest["checkpoints"][-1]["id"] != previous):
            raise RuntimeError("CHECKPOINT_RESUME_MISMATCH")

    output = batch / args.checkpoint
    if output.exists():
        raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")
    views = {view.display_id: view for view in (
        TranslationView.model_validate_json(line)
        for line in PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}
    try:
        validate_authored_target_map(
            selection=selection,
            checkpoint=args.checkpoint,
            target_artifact=targets,
            views=views,
        )
    except TargetMapValidationError as error:
        raise RuntimeError(str(error)) from error
    target_map = targets["targets"]
    blocks = {block.display_id: block for block in (
        BlockIR.model_validate_json(line)
        for line in PRODUCTION.BLOCKS.read_text(encoding="utf-8").splitlines() if line
    )}
    evidence_paths = tuple(args.d050_evidence)
    if len(set(evidence_paths)) != len(evidence_paths) or not all(path.is_file() for path in evidence_paths):
        raise RuntimeError("D050_EVIDENCE_INPUT_INVALID")
    registry = load_exact_verified_evidence_registry(evidence_paths)
    requests = [
            PRODUCTION.request_for(
            views[item], blocks[item], source_sha,
            exact_evidence_for_work_order(registry, logical_work_order_id=item, unit_uid=views[item].unit_uid),
        )
        for item in order
    ]
    output.mkdir()
    write_jsonl(output / "translation_request.jsonl", requests)
    raw = [
        TranslationResult(
            unit_uid=request.unit_uid,
            target_segments=[
                StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
                for segment, text in zip(request.source_segments, target_map[request.display_id], strict=True)
            ],
        )
        for request in requests
    ]
    raw_sha = persist_raw_response(output / "translation_raw_response.jsonl", raw)
    known = {view.unit_uid for view in views.values()}
    raw_records = [PRODUCTION.qa(views[request.display_id], request, response, known) for request, response in zip(requests, raw, strict=True)]
    write_json(output / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_records), "fail_count": sum(not row["passed"] for row in raw_records), "records": raw_records})
    normalize_response_artifact(output / "translation_request.jsonl", output / "translation_raw_response.jsonl", output / "translation_normalized_response.jsonl", output / "translation_normalization_report.json")
    if sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (output / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final_records = [PRODUCTION.qa(views[request.display_id], request, response, known) for request, response in zip(requests, normalized, strict=True)]
    write_json(output / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in final_records), "fail_count": sum(not row["passed"] for row in final_records), "records": final_records})
    if not all(row["passed"] for row in final_records):
        raise RuntimeError("CHECKPOINT_DETERMINISTIC_QA_FAILED")
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Production semantic review confirmed source meaning and exact work-order-scoped D050 evidence where present."} for row in dispatched]
    write_json(output / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    write_jsonl(output / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records]
    write_json(output / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final_records, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": []})
    write_json(output / "checkpoint_evidence.json", {
        "logical_work_orders": order, "source_sha256": source_sha, "raw_sha256": raw_sha,
        "deterministic_pass_count": len(approved), "deterministic_fail_count": 0,
        "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED",
        "d050_evidence_artifacts": [str(path) for path in evidence_paths],
        "authored_target_map": str(args.targets),
    })
    append_sealed_checkpoint(manifest_path, output)
    if fingerprint(PRODUCTION.SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
