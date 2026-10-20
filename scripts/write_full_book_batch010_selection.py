"""Freeze Batch 010 selection from immutable release membership and progress v11."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.harness.release_membership import derive_release_membership, pending_in_source_order
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
SCOPE = RUN / "full_book_translation_scope.json"
PROGRESS = RUN / "full_book_progress_v11.json"
OUT = RUN / "full_book_batch_010_selection.json"


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_SELECTION_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    progress = json.loads(PROGRESS.read_text(encoding="utf-8"))
    if progress.get("status") != "FULL_BOOK_PRODUCTION_IN_PROGRESS" or progress.get("unresolved_deterministic_fail") or progress.get("human_required") or progress.get("recovery_queue_size"):
        raise RuntimeError("BATCH_010_PRECONDITION_FAILED")
    released = derive_release_membership(RUN, scope)
    selected = pending_in_source_order(scope, released)[:200]
    ids = [item["logical_work_order_id"] for item in selected]
    if len(ids) != 200 or len(set(ids)) != 200 or set(ids) & released or ids[0] != progress["next_resume_point_derived_from_authoritative_scope"]:
        raise RuntimeError("BATCH_010_SELECTION_DERIVATION_MISMATCH")
    artifact = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": "FULL_BOOK_BATCH_010", "macro_target": 200, "micro_checkpoint_size": 25,
        "authoritative_scope": str(SCOPE), "authoritative_scope_sha256": sha256(SCOPE.read_bytes()).hexdigest(), "source_docx": str(SOURCE), "source_sha256": source_sha,
        "prior_progress": str(PROGRESS), "authoritative_release_membership_method": "scope ALREADY_APPROVED plus immutable passing final-QA records",
        "selection_order": "authoritative remaining-first-pass source order; TOC remains late-stage content", "selected_logical_work_order_ids": ids,
        "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in selected],
        "selected_id_set_sha256": sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "selection_validation": {"selected_logical_count": 200, "duplicate_logical_count": 0, "previous_approved_overlap_count": 0, "existing_recovery_overlap_count": 0, "toc_late_stage_count": 0, "phase_counts": {phase: sum(item["phase"] == phase for item in selected) for phase in sorted({item["phase"] for item in selected})}},
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
