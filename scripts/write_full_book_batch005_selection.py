"""Freeze Batch 005's 200-work-order remaining-first-pass selection from release evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SCOPE = RUN / "full_book_translation_scope.json"
OUT = RUN / "full_book_batch_005_selection.json"


def read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def selection_ids(path: Path) -> set[str]:
    selection = read(path)
    if "selected_logical_work_order_ids" in selection:
        return set(selection["selected_logical_work_order_ids"])
    return {item["logical_work_order_id"] for item in selection["selected_work_orders"]}


def final_ids(batch: str) -> set[str]:
    manifest = read(RUN / batch / "batch_manifest.json")
    if manifest["status"] != "COMPLETE":
        raise RuntimeError(f"BATCH_NOT_COMPLETE:{batch}")
    result: set[str] = set()
    for checkpoint in manifest["checkpoints"]:
        evidence = read(RUN / batch / checkpoint["id"] / "checkpoint_evidence.json")
        report = read(RUN / batch / checkpoint["id"] / "translation_final_qa_report.json")
        approved = set(report["final_approved_unit_uids"])
        records = {record["unit_uid"]: record["display_id"] for record in report["records"]}
        result.update(records[unit_uid] for unit_uid in approved)
        if len(evidence["logical_work_orders"]) != checkpoint["actual_count"]:
            raise RuntimeError(f"CHECKPOINT_EVIDENCE_COUNT_MISMATCH:{batch}:{checkpoint['id']}")
    return result


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_SELECTION_ALREADY_EXISTS")
    scope = read(SCOPE)
    progress = read(RUN / "full_book_progress_v6.json")
    recovery = read(RUN / "full_book_recovery_sweep_002/recovery_sweep_closure.json")
    if progress["recovery_queue_size"] != 0 or recovery["unresolved_count"] != 0:
        raise RuntimeError("RECOVERY_QUEUE_NOT_CLOSED")
    prior_selected = set().union(*(selection_ids(RUN / f"full_book_batch_{number:03d}_selection.json") for number in range(1, 5)))
    # Batch 002's historical manifest remains OPEN because its local deterministic
    # failures were handled by the subsequent closed recovery evidence.  Its frozen
    # selection is nevertheless in prior_selected, so it cannot re-enter first pass.
    approved = set().union(*(final_ids(f"full_book_batch_{number:03d}") for number in (1, 3, 4)))
    recovered = {unit["logical_work_order_id"] for unit in recovery["units"] if unit["final_approved"]}
    prior_release = prior_selected | approved | recovered
    pending = [item for item in scope["logical_work_orders"] if item["release_status"] == "PENDING_FIRST_PASS" and item["logical_work_order_id"] not in prior_release and item["phase"] != "toc_late_stage"]
    selected = pending[:200]
    selected_ids = [item["logical_work_order_id"] for item in selected]
    if len(selected_ids) != 200 or len(set(selected_ids)) != 200 or selected_ids[0] != progress["next_resume_point_derived_from_authoritative_scope"]:
        raise RuntimeError("AUTHORITATIVE_BATCH_005_SELECTION_MISMATCH")
    chapter_4_count = sum(item["phase"] == "chapter_4" for item in selected)
    artifact = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": "FULL_BOOK_BATCH_005", "macro_target": 200, "micro_checkpoint_size": 25,
        "authoritative_scope": str(SCOPE), "authoritative_scope_sha256": hashlib.sha256(SCOPE.read_bytes()).hexdigest(),
        "source_docx": scope["source_docx"], "source_sha256": scope["source_sha256"], "prior_progress": str(RUN / "full_book_progress_v6.json"),
        "closed_recovery_evidence": str(RUN / "full_book_recovery_sweep_002/recovery_sweep_closure.json"),
        "selection_order": "authoritative remaining-first-pass source order after closed Batches 001–004 and closed recovery evidence; TOC remains late-stage content",
        "selected_logical_work_order_ids": selected_ids,
        "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in selected],
        "selected_id_set_sha256": hashlib.sha256("\n".join(selected_ids).encode("utf-8")).hexdigest(),
        "selection_validation": {"selected_logical_count": len(selected_ids), "duplicate_logical_count": len(selected_ids) - len(set(selected_ids)), "previous_approved_overlap_count": len(set(selected_ids) & approved), "existing_recovery_overlap_count": len(set(selected_ids) & recovered), "toc_late_stage_count": sum(item["phase"] == "toc_late_stage" for item in selected), "phase_counts": {phase: sum(item["phase"] == phase for item in selected) for phase in sorted({item["phase"] for item in selected})}, "chapter_4_selected_count": chapter_4_count},
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"first": selected_ids[0], "last": selected_ids[-1], **artifact["selection_validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
