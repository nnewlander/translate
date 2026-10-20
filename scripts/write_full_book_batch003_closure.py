"""Seal Batch 003 evidence and derive the authoritative progress v5 snapshot."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH = RUN / "full_book_batch_003"
SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    if fingerprint(SOURCE).sha256 != SHA:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "COMPLETE" or manifest["completed_count"] != 150:
        raise RuntimeError("BATCH_003_NOT_COMPLETE")
    checkpoint_stats = []
    failures = []
    total_approved = 0
    for checkpoint in manifest["checkpoints"]:
        report = json.loads((BATCH / checkpoint["id"] / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        raw = json.loads((BATCH / checkpoint["id"] / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
        checkpoint_stats.append({
            "checkpoint": checkpoint["id"],
            "logical_count": checkpoint["actual_count"],
            "raw_qa_pass": raw["pass_count"],
            "final_approved": report["pass_count"],
            "deterministic_fail": report["fail_count"],
            "human_required": 0,
        })
        total_approved += report["pass_count"]
        for record in report["records"]:
            if not record["passed"]:
                failures.append({
                    "logical_work_order_id": record["display_id"],
                    "classification": "TARGET_OUTPUT_NUMBER_REPRESENTATION",
                    "raw_status": "IMMUTABLE_FIRST_PASS_FAIL",
                    "review_status": "NOT_REVIEWED_DETERMINISTIC_FAIL",
                    "diagnostics": record["diagnostics"],
                    "recovery_policy": "LATER_RECOVERY_QUEUE",
                })
    if total_approved != 147 or len(failures) != 3:
        raise RuntimeError("UNEXPECTED_BATCH_003_ACCOUNTING")
    selection = json.loads((RUN / "full_book_batch_003_selection.json").read_text(encoding="utf-8"))
    if len(selection["selected_logical_work_order_ids"]) != 150:
        raise RuntimeError("SELECTION_COUNT_MISMATCH")
    scope = json.loads((RUN / "full_book_translation_scope.json").read_text(encoding="utf-8"))
    all_ids = [item["logical_work_order_id"] for item in scope["logical_work_orders"]]
    last_index = all_ids.index(selection["selected_logical_work_order_ids"][-1])
    next_resume = all_ids[last_index + 1]
    completion = {
        "artifact_type": "FULL_BOOK_BATCH_COMPLETION_REPORT",
        "batch": "FULL_BOOK_BATCH_003",
        "status": "COMPLETE",
        "current_manifest": str(BATCH / "batch_manifest.json"),
        "macro_target": 150,
        "completed_logical_work_orders": 150,
        "checkpoint_stats": checkpoint_stats,
        "final_approved_count": total_approved,
        "deterministic_fail_count": len(failures),
        "deterministic_failures": failures,
        "human_required_count": 0,
        "source_sha256": SHA,
        "pdf_assisted_units": ["body.p.01102"],
        "pdf_assisted_evidence_pages": [52],
        "throughput_promotion_ready": True,
        "throughput_promotion_reason": "Batch 001 and Batch 002 were stable; Chapter 1 exact D050 evidence is now complete; Batch 003 reached 150 terminal first-pass work orders. The three remaining failures are sparse target-output numeric-representation defects and do not form a systemic contract blocker.",
        "next_resume_point_derived_from_authoritative_scope": next_resume,
        "next_state": "FULL_BOOK_PRODUCTION_IN_PROGRESS",
    }
    write_once(RUN / "full_book_batch_003_completion_report.json", completion)
    previous = json.loads((RUN / "full_book_progress_v4.json").read_text(encoding="utf-8"))
    phases = previous["current_progress_by_phase"]
    phases["front_matter"] = {"logical_total": 26, "final_approved": 26, "deterministic_fail": 0, "unstarted": 0}
    phases["chapter_1"] = {"logical_total": 200, "final_approved": 197, "deterministic_fail": 3, "unstarted": 0}
    phases["chapter_3"] = {"logical_total": 263, "final_approved": 9, "deterministic_fail": 0, "unstarted": 254}
    progress = {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS",
        "supersedes": "full_book_progress_v4.json",
        "total_physical_work_orders": 8104,
        "total_logical_work_orders": 8096,
        "current_release_set_count": 974,
        "unresolved_deterministic_fail": 3,
        "human_required": 0,
        "remaining_unstarted_first_pass": 7119,
        "recovery_queue_size": 3,
        "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS",
        "latest_completed_batch": "FULL_BOOK_BATCH_003",
        "source_sha256": SHA,
        "current_progress_by_phase": phases,
        "recovery_queue": [item["logical_work_order_id"] for item in failures],
        "throughput_promotion_ready": True,
        "throughput_promotion_reason": completion["throughput_promotion_reason"],
        "next_resume_point_derived_from_authoritative_scope": next_resume,
    }
    if sum(item["final_approved"] for item in phases.values()) != 974:
        raise RuntimeError("PROGRESS_PHASE_ACCOUNTING_MISMATCH")
    write_once(RUN / "full_book_progress_v5.json", progress)
    write_once(RUN / "full_book_verified_footnote_evidence_operation_rule_v1.json", {
        "artifact_type": "FULL_BOOK_PRODUCTION_OPERATION_RULE",
        "status": "ACTIVE",
        "rule": "When production first enters a new chapter containing reader-visible footnotes, perform a bounded chapter-specific exact D050 verified-footnote-evidence preflight before translating its footnote-marker units. If the chapter has no reader-visible footnotes, no added preflight is required.",
        "non_expansions": ["No generic footnote detector", "No superscript or punctuation inference", "No D050 contract change", "No numeric-validator relaxation"],
        "evidence_precedent": str(RUN / "chapter_1_verified_footnote_evidence_v1.json"),
        "source_sha256": SHA,
    })
    if fingerprint(SOURCE).sha256 != SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
