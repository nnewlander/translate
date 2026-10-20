"""Write the immutable, source-order selection for full-book production batch 002."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SCOPE = RUN / "full_book_translation_scope.json"
BATCH_001 = RUN / "full_book_batch_001_selection.json"
OUT = RUN / "full_book_batch_002_selection.json"
TARGET_COUNT = 100


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"immutable selection already exists: {OUT}")

    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    batch_001 = json.loads(BATCH_001.read_text(encoding="utf-8"))
    batch_001_ids = set(batch_001["selected_logical_work_order_ids"])
    if len(batch_001_ids) != 100:
        raise SystemExit("batch 001 selection is not a unique 100-work-order terminal set")

    eligible = []
    for order in scope["logical_work_orders"]:
        if order["release_status"] != "PENDING_FIRST_PASS":
            continue
        if order["logical_work_order_id"] in batch_001_ids:
            # This includes body.p.00056, retained exclusively in recovery queue.
            continue
        if order.get("phase") == "toc_late_stage":
            continue
        eligible.append(order)

    if len(eligible) < TARGET_COUNT:
        raise SystemExit(f"only {len(eligible)} eligible unstarted work orders remain")
    selected = eligible[:TARGET_COUNT]
    ids = [item["logical_work_order_id"] for item in selected]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate logical work order selected")

    artifact = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION",
        "batch_id": "FULL_BOOK_BATCH_002",
        "created_at": "2026-09-16T00:31:00Z",
        "authoritative_scope": str(SCOPE),
        "authoritative_scope_sha256": sha256(SCOPE),
        "source_docx": scope["source_docx"],
        "source_sha256": scope["source_sha256"],
        "target_count": TARGET_COUNT,
        "checkpoint_size": 20,
        "selection_policy": (
            "Authoritative source order; PENDING_FIRST_PASS only; skip all Batch 001 "
            "terminal selections (including recovery-only body.p.00056) and defer TOC late-stage work."
        ),
        "excluded_recovery_work_orders": ["body.p.00056"],
        "selected_work_orders": selected,
        "selection_validation": {
            "selected_logical_count": len(selected),
            "duplicate_logical_count": len(ids) - len(set(ids)),
            "batch_001_overlap_count": len(set(ids) & batch_001_ids),
            "toc_late_stage_count": sum(item.get("phase") == "toc_late_stage" for item in selected),
        },
    }
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)
    print(json.dumps(artifact["selection_validation"], ensure_ascii=False))
    print("FIRST", ids[0])
    print("LAST", ids[-1])


if __name__ == "__main__":
    main()
