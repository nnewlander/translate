"""Derive the Batch 005 checkpoint 03 accounting reconciliation from sealed evidence."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH = RUN / "full_book_batch_005"
CHECKPOINT = BATCH / "checkpoint_03"
SOURCE = ROOT / "Software Engineering at Google.docx"
SOURCE_SHA256 = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    if fingerprint(SOURCE).sha256 != SOURCE_SHA256:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    evidence = json.loads((CHECKPOINT / "checkpoint_evidence.json").read_text(encoding="utf-8"))
    raw = json.loads((CHECKPOINT / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
    normalized = json.loads((CHECKPOINT / "translation_normalized_qa_report.json").read_text(encoding="utf-8"))
    review = json.loads((CHECKPOINT / "semantic_review_report.json").read_text(encoding="utf-8"))
    final = json.loads((CHECKPOINT / "translation_final_qa_report.json").read_text(encoding="utf-8"))
    if len(evidence["logical_work_orders"]) != 25:
        raise RuntimeError("CHECKPOINT_03_LOGICAL_COUNT_MISMATCH")
    for name, report in (("RAW", raw), ("NORMALIZED", normalized), ("FINAL", final)):
        if report["pass_count"] + report["fail_count"] != 25:
            raise RuntimeError(f"CHECKPOINT_03_{name}_ACCOUNTING_MISMATCH")
    failed = [record for record in final["records"] if not record["passed"]]
    if raw["pass_count"] != 24 or normalized["pass_count"] != 24 or final["pass_count"] != 24 or len(failed) != 1:
        raise RuntimeError("CHECKPOINT_03_UNEXPECTED_IMMUTABLE_RESULT")
    excluded = review.get("dispatch_exclusions", [])
    if len(excluded) != 1 or excluded[0]["unit_uid"] != failed[0]["unit_uid"]:
        raise RuntimeError("CHECKPOINT_03_REVIEW_DISPATCH_MISMATCH")
    checkpoint = next(item for item in manifest["checkpoints"] if item["id"] == "checkpoint_03")
    if checkpoint["raw_pass"] != raw["pass_count"] or checkpoint["final_pass"] != final["pass_count"]:
        raise RuntimeError("CHECKPOINT_03_MANIFEST_EVIDENCE_DRIFT")
    write_once(BATCH / "checkpoint_03_accounting_reconciliation.json", {
        "artifact_type": "CHECKPOINT_ACCOUNTING_RECONCILIATION",
        "batch": "FULL_BOOK_BATCH_005",
        "checkpoint": "checkpoint_03",
        "result": "IMMUTABLE_EVIDENCE_CONFIRMS_24_APPROVED_PLUS_1_DETERMINISTIC_FAIL",
        "prior_user_report_claim": {"raw_pass": 25, "final_pass": 25, "deterministic_fail": 0},
        "preserved_derived_manifest_record": checkpoint,
        "immutable_evidence": {
            "logical_count": 25,
            "raw": {"pass": raw["pass_count"], "fail": raw["fail_count"]},
            "normalized": {"pass": normalized["pass_count"], "fail": normalized["fail_count"]},
            "review": {"accept": review["accept_count"], "not_reviewed_deterministic_fail": review["not_reviewed_deterministic_fail_count"]},
            "final": {"pass": final["pass_count"], "fail": final["fail_count"]},
        },
        "recovery_queue_addition": {
            "logical_work_order_id": failed[0]["display_id"], "unit_uid": failed[0]["unit_uid"],
            "classification": "TARGET_OUTPUT_NUMBER_REPRESENTATION",
            "raw_status": "IMMUTABLE_FIRST_PASS_FAIL",
            "review_status": "NOT_REVIEWED_DETERMINISTIC_FAIL",
            "diagnostics": failed[0]["diagnostics"],
            "recovery_policy": "LATER_RECOVERY_QUEUE",
        },
        "accounting_interpretation": "completed_count remains 75 terminal first-pass work orders; release-approved is 74 and recovery queue is 1.",
        "source_sha256": SOURCE_SHA256,
    })
    if fingerprint(SOURCE).sha256 != SOURCE_SHA256:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
