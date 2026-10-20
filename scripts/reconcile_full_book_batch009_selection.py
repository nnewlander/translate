"""Reconcile and, if necessary, supersede the immutable Batch 009 selection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from booktrans.harness.release_membership import derive_release_membership, pending_in_source_order
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
SCOPE_PATH = RUN / "full_book_translation_scope.json"
PROGRESS_PATH = RUN / "full_book_progress_v10.json"
OLD = RUN / "full_book_batch_009_selection.json"
RECONCILIATION = RUN / "full_book_batch_009_selection_reconciliation_v1.json"
NEW = RUN / "full_book_batch_009_selection_reconciled_v1.json"


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    scope = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
    progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    old = json.loads(OLD.read_text(encoding="utf-8"))
    released = derive_release_membership(RUN, scope)
    pending = pending_in_source_order(scope, released)
    old_ids = old["selected_logical_work_order_ids"]
    overlap = [item for item in old_ids if item in released]
    phase_counts: dict[str, int] = {}
    for item in released:
        order = next(order for order in scope["logical_work_orders"] if order["logical_work_order_id"] == item)
        phase_counts[order["phase"]] = phase_counts.get(order["phase"], 0) + 1
    expected_progress_phases = {
        phase: values["final_approved"] for phase, values in progress["current_progress_by_phase"].items()
    }
    mismatch = {phase: {"evidence": phase_counts.get(phase, 0), "progress": count} for phase, count in expected_progress_phases.items() if phase_counts.get(phase, 0) != count}
    if len(released) != progress["current_release_set_count"] or mismatch:
        raise RuntimeError("PROGRESS_RELEASE_EVIDENCE_MISMATCH")
    if not overlap:
        raise RuntimeError("SELECTION_NOT_INVALIDATED")
    selected = pending[:200]
    ids = [item["logical_work_order_id"] for item in selected]
    if len(ids) != 200 or len(set(ids)) != 200:
        raise RuntimeError("RECONCILED_SELECTION_DERIVATION_MISMATCH")
    if set(ids) & released:
        raise RuntimeError("RECONCILED_SELECTION_RELEASE_OVERLAP")
    reconciliation = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION_RECONCILIATION",
        "batch": "FULL_BOOK_BATCH_009",
        "classification": "REMAINING_SELECTOR_DEFECT",
        "old_selection": str(OLD),
        "old_selection_immutable": True,
        "invalidated_reason": "Selector used prior frozen-selection membership with incomplete historical-schema decoding instead of authoritative immutable release membership.",
        "old_selection_release_approved_overlap_count": len(overlap),
        "old_selection_release_approved_overlap_ids": overlap,
        "old_selection_overlap_by_phase": {phase: sum(next(order for order in scope["logical_work_orders"] if order["logical_work_order_id"] == item)["phase"] == phase for item in overlap) for phase in sorted({next(order for order in scope["logical_work_orders"] if order["logical_work_order_id"] == item)["phase"] for item in overlap})},
        "current_release_set_count_from_evidence": len(released),
        "progress_v10_release_set_count": progress["current_release_set_count"],
        "progress_phase_accounting_matches_evidence": True,
        "remaining_first_pass_from_evidence": len(pending),
        "progress_v10_remaining_first_pass": progress["remaining_unstarted_first_pass"],
        "progress_v10_next_resume_point": progress["next_resume_point_derived_from_authoritative_scope"],
        "next_resume_point": ids[0],
        "source_sha256": source_sha,
    }
    replacement = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION",
        "batch": "FULL_BOOK_BATCH_009",
        "selection_revision": "RECONCILED_V1",
        "supersedes_invalid_selection": str(OLD),
        "reconciliation_evidence": str(RECONCILIATION),
        "macro_target": 200,
        "micro_checkpoint_size": 25,
        "authoritative_scope": str(SCOPE_PATH),
        "authoritative_scope_sha256": hashlib.sha256(SCOPE_PATH.read_bytes()).hexdigest(),
        "authoritative_release_membership_method": "scope ALREADY_APPROVED plus immutable passing final-QA records",
        "prior_progress": str(PROGRESS_PATH),
        "selected_logical_work_order_ids": ids,
        "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in selected],
        "selected_id_set_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "selection_validation": {"selected_logical_count": 200, "duplicate_logical_count": 0, "previous_approved_overlap_count": 0, "existing_recovery_overlap_count": 0, "toc_late_stage_count": 0, "phase_counts": {phase: sum(item["phase"] == phase for item in selected) for phase in sorted({item["phase"] for item in selected})}},
    }
    write_once(RECONCILIATION, reconciliation)
    write_once(NEW, replacement)
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
