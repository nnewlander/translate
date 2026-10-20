"""Derive immutable Batch 009 closure and progress v11 from sealed evidence."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.harness.release_membership import derive_release_membership, pending_in_source_order
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH = RUN / "full_book_batch_009"
SOURCE = ROOT / "Software Engineering at Google.docx"
SELECTION = RUN / "full_book_batch_009_selection_reconciled_v1.json"
PRIOR = RUN / "full_book_progress_v10.json"
SCOPE = RUN / "full_book_translation_scope.json"
SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    if fingerprint(SOURCE).sha256 != SHA:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETE" or manifest.get("completed_count") != 200 or len(manifest.get("checkpoints", [])) != 8:
        raise RuntimeError("BATCH_009_NOT_COMPLETE")
    observed: list[str] = []
    checkpoint_stats: list[dict[str, object]] = []
    raw_failures = 0
    for checkpoint in manifest["checkpoints"]:
        directory = BATCH / checkpoint["id"]
        evidence = json.loads((directory / "checkpoint_evidence.json").read_text(encoding="utf-8"))
        raw = json.loads((directory / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
        final = json.loads((directory / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        review = json.loads((directory / "semantic_review_report.json").read_text(encoding="utf-8"))
        ids = evidence.get("logical_work_orders")
        if not isinstance(ids, list) or len(ids) != 25 or raw.get("pass_count", 0) + raw.get("fail_count", 0) != 25 or final.get("pass_count") != 25 or final.get("fail_count") != 0 or review.get("human_required_count", 0):
            raise RuntimeError(f"BATCH_009_CHECKPOINT_EVIDENCE_INVALID:{checkpoint['id']}")
        observed.extend(ids)
        raw_failures += raw["fail_count"]
        checkpoint_stats.append({"checkpoint": checkpoint["id"], "raw_qa_pass": raw["pass_count"], "raw_qa_fail": raw["fail_count"], "final_approved": final["pass_count"], "deterministic_fail": final["fail_count"], "human_required": review.get("human_required_count", 0)})
    selected = selection.get("selected_logical_work_order_ids")
    if observed != selected or len(set(observed)) != 200:
        raise RuntimeError("BATCH_009_SELECTION_EVIDENCE_MISMATCH")
    released = derive_release_membership(RUN, scope)
    pending = pending_in_source_order(scope, released)
    if set(selected) - released or len(released) != prior["current_release_set_count"] + 200:
        raise RuntimeError("BATCH_009_RELEASE_MEMBERSHIP_MISMATCH")
    phases: dict[str, dict[str, int]] = {}
    for order in scope["logical_work_orders"]:
        phase = order["phase"]
        values = phases.setdefault(phase, {"logical_total": 0, "final_approved": 0, "deterministic_fail": 0, "unstarted": 0})
        values["logical_total"] += 1
        if order["logical_work_order_id"] in released:
            values["final_approved"] += 1
        else:
            values["unstarted"] += 1
    if sum(values["final_approved"] for values in phases.values()) != len(released):
        raise RuntimeError("BATCH_009_PHASE_ACCOUNTING_MISMATCH")
    completion = {
        "artifact_type": "FULL_BOOK_BATCH_COMPLETION_REPORT", "batch": "FULL_BOOK_BATCH_009", "status": "COMPLETE",
        "frozen_selection": str(SELECTION), "current_manifest": str(BATCH / "batch_manifest.json"), "macro_target": 200,
        "completed_logical_work_orders": 200, "checkpoint_stats": checkpoint_stats, "raw_deterministic_failure_count": raw_failures,
        "final_approved_count": 200, "deterministic_fail_count": 0, "human_required_count": 0, "recovery_queue_size": 0,
        "current_release_approved_count": len(released), "remaining_unstarted_first_pass": sum(values["unstarted"] for values in phases.values()),
        "next_resume_point_derived_from_authoritative_scope": pending[0]["logical_work_order_id"], "source_sha256": SHA,
    }
    progress = {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "supersedes": "full_book_progress_v10.json",
        "total_physical_work_orders": prior["total_physical_work_orders"], "total_logical_work_orders": len(scope["logical_work_orders"]),
        "current_release_set_count": len(released), "unresolved_deterministic_fail": 0, "human_required": 0,
        "remaining_unstarted_first_pass": sum(values["unstarted"] for values in phases.values()), "recovery_queue_size": 0,
        "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_009", "source_sha256": SHA,
        "current_progress_by_phase": phases, "recovery_queue": [], "200_UNIT_PRODUCTION_STEADY_STATE": True,
        "next_resume_point_derived_from_authoritative_scope": pending[0]["logical_work_order_id"],
        "derivation_evidence": {"prior_progress": str(PRIOR), "batch_manifest": str(BATCH / "batch_manifest.json"), "frozen_selection": str(SELECTION), "release_membership": "scope ALREADY_APPROVED plus immutable passing final-QA records"},
    }
    write_once(RUN / "full_book_batch_009_completion_report.json", completion)
    write_once(RUN / "full_book_progress_v11.json", progress)
    if fingerprint(SOURCE).sha256 != SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
