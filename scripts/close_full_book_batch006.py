"""Derive immutable Batch 006 closure and the next authoritative progress snapshot."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from booktrans.harness.source_integrity import fingerprint
from booktrans.ooxml.verified_footnotes import exact_evidence_for_work_order, load_exact_verified_evidence_registry


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH = RUN / "full_book_batch_006"
SOURCE = ROOT / "Software Engineering at Google.docx"
SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def selection_ids(path: Path) -> set[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    explicit = value.get("selected_logical_work_order_ids")
    if isinstance(explicit, list) and all(isinstance(item, str) for item in explicit):
        return set(explicit)
    orders = value.get("selected_work_orders")
    if isinstance(orders, list):
        return {item["logical_work_order_id"] for item in orders if isinstance(item, dict) and isinstance(item.get("logical_work_order_id"), str)}
    raise RuntimeError(f"SELECTION_ID_SCHEMA_INVALID:{path}")


def main() -> None:
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    selection = json.loads((RUN / "full_book_batch_006_selection.json").read_text(encoding="utf-8"))
    prior = json.loads((RUN / "full_book_progress_v7.json").read_text(encoding="utf-8"))
    scope = json.loads((RUN / "full_book_translation_scope.json").read_text(encoding="utf-8"))
    evidence_path = RUN / "chapter_5_verified_footnote_evidence_v1.json"
    registry = load_exact_verified_evidence_registry((evidence_path,))
    if manifest["status"] != "COMPLETE" or manifest["completed_count"] != 200 or manifest["remaining_in_batch"] != 0 or len(manifest["checkpoints"]) != 8:
        raise RuntimeError("BATCH_006_NOT_COMPLETE")
    ids = selection["selected_logical_work_order_ids"]
    if len(ids) != 200 or manifest["target_count"] != len(ids):
        raise RuntimeError("SELECTION_MANIFEST_COUNT_MISMATCH")
    view_path = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
    views = {json.loads(line)["display_id"]: json.loads(line) for line in view_path.read_text(encoding="utf-8").splitlines() if line}
    observed_ids: list[str] = []
    checkpoint_stats: list[dict[str, object]] = []
    approved = deterministic_fail = human_required = 0
    routed_d050: list[dict[str, object]] = []
    for checkpoint in manifest["checkpoints"]:
        directory = BATCH / checkpoint["id"]
        evidence = json.loads((directory / "checkpoint_evidence.json").read_text(encoding="utf-8"))
        raw = json.loads((directory / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
        final = json.loads((directory / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        review = json.loads((directory / "semantic_review_report.json").read_text(encoding="utf-8"))
        requests = [json.loads(line) for line in (directory / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
        order = evidence["logical_work_orders"]
        if len(order) != 25 or raw["pass_count"] + raw["fail_count"] != 25 or final["pass_count"] + final["fail_count"] != 25:
            raise RuntimeError(f"CHECKPOINT_ACCOUNTING_MISMATCH:{checkpoint['id']}")
        if checkpoint["actual_count"] != 25 or checkpoint["raw_pass"] != raw["pass_count"] or checkpoint["final_pass"] != final["pass_count"]:
            raise RuntimeError(f"MANIFEST_EVIDENCE_DRIFT:{checkpoint['id']}")
        if [request["display_id"] for request in requests] != order:
            raise RuntimeError(f"REQUEST_ORDER_EVIDENCE_MISMATCH:{checkpoint['id']}")
        observed_ids.extend(order)
        checkpoint_human = review.get("human_required_count", 0)
        checkpoint_stats.append({"checkpoint": checkpoint["id"], "logical_count": 25, "raw_qa_pass": raw["pass_count"], "final_approved": final["pass_count"], "deterministic_fail": final["fail_count"], "human_required": checkpoint_human})
        approved += final["pass_count"]
        deterministic_fail += final["fail_count"]
        human_required += checkpoint_human
        for request in requests:
            numbers = request.get("footnote_reference_numbers", [])
            if not numbers:
                continue
            view = views[request["display_id"]]
            exact = exact_evidence_for_work_order(registry, logical_work_order_id=request["display_id"], unit_uid=view["unit_uid"])
            if [record["footnote_number"] for record in exact] != numbers:
                raise RuntimeError(f"D050_EXACT_ROUTING_DRIFT:{request['display_id']}")
            routed_d050.extend({"checkpoint": checkpoint["id"], "logical_work_order_id": request["display_id"], "unit_uid": view["unit_uid"], "footnote_number": number} for number in numbers)
    if observed_ids != ids or len(set(observed_ids)) != 200:
        raise RuntimeError("FROZEN_SELECTION_EVIDENCE_MISMATCH")
    if approved != 200 or deterministic_fail != 0 or human_required != 0:
        raise RuntimeError("BATCH_006_TERMINAL_ACCOUNTING_NOT_CLEAN")
    if prior["recovery_queue_size"] != 0 or prior["unresolved_deterministic_fail"] != 0 or prior["human_required"] != 0:
        raise RuntimeError("PRIOR_STATE_NOT_CLOSED")
    phase = next(item["phase"] for item in scope["logical_work_orders"] if item["logical_work_order_id"] == ids[0])
    if any(next(item["phase"] for item in scope["logical_work_orders"] if item["logical_work_order_id"] == item_id) != phase for item_id in ids):
        raise RuntimeError("BATCH_006_PHASE_NOT_CLOSED_WORLD")
    phases = copy.deepcopy(prior["current_progress_by_phase"])
    phases[phase]["final_approved"] += approved
    phases[phase]["unstarted"] -= approved
    completed_selection_ids = set().union(*(selection_ids(RUN / f"full_book_batch_{number:03d}_selection.json") for number in range(1, 7)))
    next_resume = next(
        item["logical_work_order_id"] for item in scope["logical_work_orders"]
        if item["release_status"] == "PENDING_FIRST_PASS" and item["logical_work_order_id"] not in completed_selection_ids and item["phase"] != "toc_late_stage"
    )
    current_release = prior["current_release_set_count"] + approved
    remaining = prior["remaining_unstarted_first_pass"] - approved
    completion = {
        "artifact_type": "FULL_BOOK_BATCH_COMPLETION_REPORT", "batch": "FULL_BOOK_BATCH_006", "status": "COMPLETE",
        "current_manifest": str(BATCH / "batch_manifest.json"), "frozen_selection": str(RUN / "full_book_batch_006_selection.json"),
        "macro_target": 200, "completed_logical_work_orders": 200, "checkpoint_stats": checkpoint_stats,
        "final_approved_count": approved, "deterministic_fail_count": deterministic_fail,
        "human_required_count": human_required, "recovery_queue_size": 0,
        "current_release_approved_count": current_release, "remaining_unstarted_first_pass": remaining,
        "current_release_approved": True, "unresolved_deterministic_fail": 0,
        "chapter_5_d050_registry": str(evidence_path), "chapter_5_d050_verified_count": len(registry),
        "chapter_5_d050_routed_records": routed_d050,
        "next_resume_point_derived_from_authoritative_scope": next_resume, "source_sha256": SOURCE_SHA,
    }
    progress = {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "supersedes": "full_book_progress_v7.json",
        "total_physical_work_orders": prior["total_physical_work_orders"], "total_logical_work_orders": prior["total_logical_work_orders"],
        "current_release_set_count": current_release, "unresolved_deterministic_fail": 0, "human_required": 0,
        "remaining_unstarted_first_pass": remaining, "recovery_queue_size": 0,
        "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_006",
        "source_sha256": SOURCE_SHA, "current_progress_by_phase": phases, "recovery_queue": [],
        "200_UNIT_PRODUCTION_STEADY_STATE": True, "next_throughput_reassessment": "FULL_BOOK_BATCH_007 = 200 logical; do not auto-increase to 250.",
        "next_resume_point_derived_from_authoritative_scope": next_resume,
        "derivation_evidence": {"prior_progress": str(RUN / "full_book_progress_v7.json"), "batch_completion": str(RUN / "full_book_batch_006_completion_report.json"), "batch_manifest": str(BATCH / "batch_manifest.json"), "frozen_selection": str(RUN / "full_book_batch_006_selection.json")},
    }
    if sum(item["final_approved"] for item in phases.values()) != current_release:
        raise RuntimeError("PROGRESS_PHASE_ACCOUNTING_MISMATCH")
    if current_release + progress["unresolved_deterministic_fail"] + progress["human_required"] + remaining != progress["total_logical_work_orders"]:
        raise RuntimeError("PROGRESS_TOTAL_ACCOUNTING_MISMATCH")
    write_once(RUN / "full_book_batch_006_completion_report.json", completion)
    write_once(RUN / "full_book_progress_v8.json", progress)
    if fingerprint(SOURCE).sha256 != SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
