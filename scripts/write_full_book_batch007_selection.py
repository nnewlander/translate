"""Freeze Batch 007 from the latest authoritative remaining-first-pass order."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SCOPE = RUN / "full_book_translation_scope.json"
PROGRESS = RUN / "full_book_progress_v8.json"
OUT = RUN / "full_book_batch_007_selection.json"


def read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_ids(path: Path) -> set[str]:
    value = read(path)
    explicit = value.get("selected_logical_work_order_ids")
    if isinstance(explicit, list) and all(isinstance(item, str) for item in explicit):
        return set(explicit)
    orders = value.get("selected_work_orders")
    if isinstance(orders, list):
        return {item["logical_work_order_id"] for item in orders if isinstance(item, dict) and isinstance(item.get("logical_work_order_id"), str)}
    raise RuntimeError(f"SELECTION_ID_SCHEMA_INVALID:{path}")


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_SELECTION_ALREADY_EXISTS")
    progress, scope = read(PROGRESS), read(SCOPE)
    if progress["recovery_queue_size"] != 0 or progress["unresolved_deterministic_fail"] != 0 or progress["human_required"] != 0:
        raise RuntimeError("BATCH_007_PRECONDITION_FAILED")
    if not progress["200_UNIT_PRODUCTION_STEADY_STATE"]:
        raise RuntimeError("BATCH_007_STEADY_STATE_REQUIRED")
    prior = set().union(*(selected_ids(RUN / f"full_book_batch_{number:03d}_selection.json") for number in range(1, 7)))
    pending = [
        item for item in scope["logical_work_orders"]
        if item["release_status"] == "PENDING_FIRST_PASS" and item["logical_work_order_id"] not in prior and item["phase"] != "toc_late_stage"
    ]
    selected = pending[:200]
    ids = [item["logical_work_order_id"] for item in selected]
    if len(ids) != 200 or len(set(ids)) != 200 or ids[0] != progress["next_resume_point_derived_from_authoritative_scope"]:
        raise RuntimeError("AUTHORITATIVE_BATCH_007_SELECTION_MISMATCH")
    value = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": "FULL_BOOK_BATCH_007", "macro_target": 200, "micro_checkpoint_size": 25,
        "authoritative_scope": str(SCOPE), "authoritative_scope_sha256": hashlib.sha256(SCOPE.read_bytes()).hexdigest(),
        "source_docx": scope["source_docx"], "source_sha256": scope["source_sha256"], "prior_progress": str(PROGRESS),
        "selection_order": "authoritative remaining-first-pass source order after closed Batch 006; TOC remains late-stage content",
        "selected_logical_work_order_ids": ids,
        "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in selected],
        "selected_id_set_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "selection_validation": {
            "selected_logical_count": 200, "duplicate_logical_count": 0,
            "previous_approved_overlap_count": len(set(ids) & prior), "existing_recovery_overlap_count": 0,
            "toc_late_stage_count": sum(item["phase"] == "toc_late_stage" for item in selected),
            "phase_counts": {phase: sum(item["phase"] == phase for item in selected) for phase in sorted({item["phase"] for item in selected})},
        },
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
