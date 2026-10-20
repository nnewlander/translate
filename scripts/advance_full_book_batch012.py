"""Close sealed batch 012 and initialize the next immutable 200-unit batch."""
from __future__ import annotations

from hashlib import sha256
import json
import shutil
from pathlib import Path

import run_full_book_batch006_checkpoint as runner
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.release_membership import derive_release_membership, pending_in_source_order
from booktrans.harness.source_integrity import fingerprint

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")
SCOPE = RUN / "full_book_translation_scope.json"
PRIOR = RUN / "full_book_progress_v13.json"
BATCH = RUN / "full_book_batch_012"
SELECTION = RUN / "full_book_batch_012_selection.json"
NEXT_BATCH, NEXT_SELECTION = RUN / "full_book_batch_013", RUN / "full_book_batch_013_selection.json"
COMPLETION, PROGRESS = RUN / "full_book_batch_012_completion_report.json", RUN / "full_book_progress_v14.json"
SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"

def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2); handle.write("\n")

def main() -> None:
    if fingerprint(SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")
    if any(path.exists() for path in (COMPLETION, PROGRESS, NEXT_SELECTION, NEXT_BATCH)): raise RuntimeError("ADVANCEMENT_ARTIFACT_ALREADY_EXISTS")
    manifest, selection, scope, prior = (json.loads(path.read_text(encoding="utf-8")) for path in (BATCH / "batch_manifest.json", SELECTION, SCOPE, PRIOR))
    if manifest.get("status") != "COMPLETE" or manifest.get("completed_count") != 200 or len(manifest.get("checkpoints", [])) != 8: raise RuntimeError("BATCH_012_NOT_COMPLETE")
    observed: list[str] = []; stats: list[dict[str, object]] = []; raw_failures = 0
    for checkpoint in manifest["checkpoints"]:
        directory = BATCH / checkpoint["id"]
        evidence = json.loads((directory / "checkpoint_evidence.json").read_text(encoding="utf-8"))
        raw = json.loads((directory / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
        final = json.loads((directory / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        review = json.loads((directory / "semantic_review_report.json").read_text(encoding="utf-8"))
        ids = evidence.get("logical_work_orders")
        if not isinstance(ids, list) or len(ids) != 25 or raw.get("pass_count", 0) + raw.get("fail_count", 0) != 25 or final.get("pass_count") != 25 or final.get("fail_count") != 0 or review.get("human_required_count", 0): raise RuntimeError(f"BATCH_012_CHECKPOINT_EVIDENCE_INVALID:{checkpoint['id']}")
        observed.extend(ids); raw_failures += raw["fail_count"]
        stats.append({"checkpoint": checkpoint["id"], "raw_qa_pass": raw["pass_count"], "raw_qa_fail": raw["fail_count"], "final_approved": final["pass_count"], "deterministic_fail": final["fail_count"], "human_required": review.get("human_required_count", 0)})
    if observed != selection.get("selected_logical_work_order_ids") or len(set(observed)) != 200: raise RuntimeError("BATCH_012_SELECTION_EVIDENCE_MISMATCH")
    released = derive_release_membership(RUN, scope); pending = pending_in_source_order(scope, released)
    if set(observed) - released or len(released) != prior["current_release_set_count"] + 200: raise RuntimeError("BATCH_012_RELEASE_MEMBERSHIP_MISMATCH")
    next_selected = pending[:200]; ids = [item["logical_work_order_id"] for item in next_selected]
    if len(ids) != 200 or len(set(ids)) != 200 or set(ids) & released or ids[0] != pending[0]["logical_work_order_id"]: raise RuntimeError("BATCH_013_SELECTION_DERIVATION_MISMATCH")
    phases: dict[str, dict[str, int]] = {}
    for order in scope["logical_work_orders"]:
        row = phases.setdefault(order["phase"], {"logical_total": 0, "final_approved": 0, "deterministic_fail": 0, "unstarted": 0}); row["logical_total"] += 1; row["final_approved" if order["logical_work_order_id"] in released else "unstarted"] += 1
    completion = {"artifact_type": "FULL_BOOK_BATCH_COMPLETION_REPORT", "batch": "FULL_BOOK_BATCH_012", "status": "COMPLETE", "frozen_selection": str(SELECTION), "current_manifest": str(BATCH / "batch_manifest.json"), "macro_target": 200, "completed_logical_work_orders": 200, "checkpoint_stats": stats, "raw_deterministic_failure_count": raw_failures, "final_approved_count": 200, "deterministic_fail_count": 0, "human_required_count": 0, "recovery_queue_size": 0, "current_release_approved_count": len(released), "remaining_unstarted_first_pass": sum(row["unstarted"] for row in phases.values()), "next_resume_point_derived_from_authoritative_scope": ids[0], "source_sha256": SHA}
    progress = {"artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "supersedes": PRIOR.name, "total_physical_work_orders": prior["total_physical_work_orders"], "total_logical_work_orders": len(scope["logical_work_orders"]), "current_release_set_count": len(released), "unresolved_deterministic_fail": 0, "human_required": 0, "remaining_unstarted_first_pass": sum(row["unstarted"] for row in phases.values()), "recovery_queue_size": 0, "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_012", "source_sha256": SHA, "current_progress_by_phase": phases, "recovery_queue": [], "200_UNIT_PRODUCTION_STEADY_STATE": True, "next_resume_point_derived_from_authoritative_scope": ids[0], "derivation_evidence": {"prior_progress": str(PRIOR), "batch_manifest": str(BATCH / "batch_manifest.json"), "frozen_selection": str(SELECTION), "release_membership": "scope ALREADY_APPROVED plus immutable passing final-QA records"}}
    frozen = {"artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": "FULL_BOOK_BATCH_013", "macro_target": 200, "micro_checkpoint_size": 25, "authoritative_scope": str(SCOPE), "authoritative_scope_sha256": sha256(SCOPE.read_bytes()).hexdigest(), "source_docx": str(SOURCE), "source_sha256": SHA, "prior_progress": str(PROGRESS), "authoritative_release_membership_method": "scope ALREADY_APPROVED plus immutable passing final-QA records", "selection_order": "authoritative remaining-first-pass source order; TOC remains late-stage content", "selected_logical_work_order_ids": ids, "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in next_selected], "selected_id_set_sha256": sha256("\n".join(ids).encode("utf-8")).hexdigest(), "selection_validation": {"selected_logical_count": 200, "duplicate_logical_count": 0, "previous_approved_overlap_count": 0, "existing_recovery_overlap_count": 0, "toc_late_stage_count": 0, "phase_counts": {phase: sum(item["phase"] == phase for item in next_selected) for phase in sorted({item["phase"] for item in next_selected})}}}
    write_once(COMPLETION, completion); write_once(PROGRESS, progress); write_once(NEXT_SELECTION, frozen)
    NEXT_BATCH.mkdir(); shutil.copyfile(runner.PRODUCTION.BIBLE, NEXT_BATCH / "translation_bible_snapshot.md"); snapshot = NEXT_BATCH / "translation_bible_snapshot.md"
    update_manifest(NEXT_BATCH / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_013", "target_count": 200, "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": 200, "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "checkpoints": [], "source_sha256": SHA, "translation_bible_snapshot_sha256": sha256(snapshot.read_bytes()).hexdigest()})
    if fingerprint(SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")

if __name__ == "__main__": main()
