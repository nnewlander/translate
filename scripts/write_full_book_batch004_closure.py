"""Derive immutable Batch 004 closure and the next authoritative progress snapshot."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH = RUN / "full_book_batch_004"
SOURCE = ROOT / "Software Engineering at Google.docx"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    selection = json.loads((RUN / "full_book_batch_004_selection.json").read_text(encoding="utf-8"))
    recovery = json.loads((RUN / "full_book_recovery_sweep_002/recovery_sweep_closure.json").read_text(encoding="utf-8"))
    prior = json.loads((RUN / "full_book_progress_v5.json").read_text(encoding="utf-8"))
    scope = json.loads((RUN / "full_book_translation_scope.json").read_text(encoding="utf-8"))
    if manifest["status"] != "COMPLETE" or manifest["completed_count"] != 150 or manifest["remaining_in_batch"] != 0:
        raise RuntimeError("BATCH_004_NOT_COMPLETE")
    ids = selection["selected_logical_work_order_ids"]
    if len(ids) != 150 or manifest["target_count"] != len(ids):
        raise RuntimeError("SELECTION_MANIFEST_COUNT_MISMATCH")
    stats, final_approved, deterministic_fail, human_required = [], 0, 0, 0
    observed_ids: list[str] = []
    for checkpoint in manifest["checkpoints"]:
        directory = BATCH / checkpoint["id"]
        evidence = json.loads((directory / "checkpoint_evidence.json").read_text(encoding="utf-8"))
        raw = json.loads((directory / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
        final = json.loads((directory / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        review = json.loads((directory / "semantic_review_report.json").read_text(encoding="utf-8"))
        order = evidence["logical_work_orders"]
        observed_ids.extend(order)
        if len(order) != 25 or raw["pass_count"] + raw["fail_count"] != 25 or final["pass_count"] + final["fail_count"] != 25:
            raise RuntimeError(f"CHECKPOINT_ACCOUNTING_MISMATCH:{checkpoint['id']}")
        if checkpoint["actual_count"] != 25 or checkpoint["raw_pass"] != raw["pass_count"] or checkpoint["final_pass"] != final["pass_count"]:
            raise RuntimeError(f"MANIFEST_EVIDENCE_DRIFT:{checkpoint['id']}")
        human = sum(record.get("status") == "HUMAN_REQUIRED" for record in review.get("records", []))
        stats.append({"checkpoint": checkpoint["id"], "logical_count": 25, "raw_qa_pass": raw["pass_count"], "final_approved": final["pass_count"], "deterministic_fail": final["fail_count"], "human_required": human})
        final_approved += final["pass_count"]
        deterministic_fail += final["fail_count"]
        human_required += human
    if observed_ids != ids or len(set(observed_ids)) != 150:
        raise RuntimeError("FROZEN_SELECTION_EVIDENCE_MISMATCH")
    if recovery["unresolved_count"] != 0 or recovery["final_approved_count"] != 3 or not all(unit["final_approved"] for unit in recovery["units"]):
        raise RuntimeError("RECOVERY_CLOSURE_MISMATCH")
    if final_approved != 150 or deterministic_fail != 0 or human_required != 0:
        raise RuntimeError("UNEXPECTED_BATCH_004_TERMINAL_ACCOUNTING")
    all_scope_ids = [item["logical_work_order_id"] for item in scope["logical_work_orders"]]
    last_index = all_scope_ids.index(ids[-1])
    next_resume = all_scope_ids[last_index + 1]
    completion = {
        "artifact_type": "FULL_BOOK_BATCH_COMPLETION_REPORT", "batch": "FULL_BOOK_BATCH_004", "status": "COMPLETE",
        "current_manifest": str(BATCH / "batch_manifest.json"), "macro_target": 150, "completed_logical_work_orders": 150,
        "checkpoint_stats": stats, "final_approved_count": final_approved, "deterministic_fail_count": deterministic_fail,
        "human_required_count": human_required, "recovery_queue_size": 0, "current_release_approved_count": prior["current_release_set_count"] + recovery["final_approved_count"] + final_approved,
        "remaining_unstarted_first_pass": prior["remaining_unstarted_first_pass"] - final_approved,
        "next_resume_point_derived_from_authoritative_scope": next_resume, "current_release_approved": True,
        "next_throughput_promotion_candidate": True,
        "throughput_promotion_reason": "Checkpoint resume completed six 25-logical savepoints without systemic contract blocker; all 150 raw and final QA records passed, HUMAN_REQUIRED is zero, and the pre-existing recovery queue is closed.",
        "source_sha256": SOURCE_SHA,
    }
    phases = copy.deepcopy(prior["current_progress_by_phase"])
    phases["chapter_1"]["final_approved"] += recovery["final_approved_count"]
    phases["chapter_1"]["deterministic_fail"] -= recovery["final_approved_count"]
    phases["chapter_3"]["final_approved"] += final_approved
    phases["chapter_3"]["unstarted"] -= final_approved
    progress = {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "supersedes": "full_book_progress_v5.json",
        "total_physical_work_orders": prior["total_physical_work_orders"], "total_logical_work_orders": prior["total_logical_work_orders"],
        "current_release_set_count": completion["current_release_approved_count"], "unresolved_deterministic_fail": 0,
        "human_required": 0, "remaining_unstarted_first_pass": completion["remaining_unstarted_first_pass"],
        "recovery_queue_size": 0, "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_004",
        "source_sha256": SOURCE_SHA, "current_progress_by_phase": phases, "recovery_queue": [],
        "next_throughput_promotion_candidate": True, "next_throughput_reassessment": "FULL_BOOK_BATCH_005 = 200 logical; do not start automatically.",
        "next_resume_point_derived_from_authoritative_scope": next_resume,
    }
    if sum(item["final_approved"] for item in phases.values()) != progress["current_release_set_count"]:
        raise RuntimeError("PROGRESS_PHASE_ACCOUNTING_MISMATCH")
    if progress["current_release_set_count"] + progress["unresolved_deterministic_fail"] + progress["human_required"] + progress["remaining_unstarted_first_pass"] != progress["total_logical_work_orders"]:
        raise RuntimeError("PROGRESS_TOTAL_ACCOUNTING_MISMATCH")
    write_once(RUN / "full_book_batch_004_completion_report.json", completion)
    write_once(RUN / "full_book_progress_v6.json", progress)
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
