"""Write the immutable authoritative selection for promoted full-book batch 003."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SCOPE = RUN / "full_book_translation_scope.json"
BATCH_001 = RUN / "full_book_batch_001_selection.json"
BATCH_002 = RUN / "full_book_batch_002_selection.json"
RECOVERY = RUN / "full_book_recovery_sweep_001" / "recovery_sweep_closure.json"
OUT = RUN / "full_book_batch_003_selection.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"immutable selection already exists: {OUT}")
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    batch_001 = json.loads(BATCH_001.read_text(encoding="utf-8"))
    batch_002 = json.loads(BATCH_002.read_text(encoding="utf-8"))
    recovery = json.loads(RECOVERY.read_text(encoding="utf-8"))
    if recovery["unresolved_count"] != 0 or recovery["final_approved_count"] != 4:
        raise SystemExit("RECOVERY_SWEEP_NOT_CLOSED")
    completed = set(batch_001["selected_logical_work_order_ids"])
    completed.update(item["logical_work_order_id"] for item in batch_002["selected_work_orders"])
    eligible = [item for item in scope["logical_work_orders"] if item["release_status"] == "PENDING_FIRST_PASS" and item["logical_work_order_id"] not in completed and item.get("phase") != "toc_late_stage"]
    selected = eligible[:150]
    ids = [item["logical_work_order_id"] for item in selected]
    if len(selected) != 150 or len(ids) != len(set(ids)):
        raise SystemExit("BATCH_003_SELECTION_NOT_UNIQUE_150")
    artifact = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION",
        "batch": "FULL_BOOK_BATCH_003",
        "macro_target": 150,
        "micro_checkpoint_size": 25,
        "authoritative_scope": str(SCOPE),
        "authoritative_scope_sha256": digest(SCOPE),
        "source_docx": scope["source_docx"],
        "source_sha256": scope["source_sha256"],
        "current_release_recovery_closure": str(RECOVERY),
        "selection_order": "authoritative remaining-first-pass source order after Batch 001, Batch 002, and closed recovery sweep; TOC remains explicit late-stage content",
        "selected_logical_work_order_ids": ids,
        "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in selected],
        "selected_id_set_sha256": hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "selection_validation": {"selected_logical_count": len(selected), "duplicate_logical_count": len(ids) - len(set(ids)), "previous_batch_overlap_count": len(set(ids) & completed), "toc_late_stage_count": sum(item.get("phase") == "toc_late_stage" for item in selected)},
    }
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)
    print(json.dumps(artifact["selection_validation"], ensure_ascii=False))
    print(ids[0], ids[-1])


if __name__ == "__main__":
    main()
