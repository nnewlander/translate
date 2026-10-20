"""Close a sealed batch and initialize the next immutable target-sized batch.

This is the existing batch-advance policy, made explicit for Batch 013.  It
does not run a worker or create a checkpoint in the newly opened batch.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import re
import shutil
import sys
from pathlib import Path

from booktrans.harness.batch_manifest import resolve_authoritative_manifest, resolve_checkpoint_seal_artifacts, update_manifest
from booktrans.harness.release_membership import derive_release_membership, pending_in_source_order
from booktrans.harness.toc_late_stage import full_book_scope_complete, select_toc_late_stage
from booktrans.harness.source_integrity import fingerprint
from booktrans.production_worker.selection import BATCH_TARGET_MAX, CHECKPOINT_SIZE


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
SOURCE = Path(r"D:\translate\Software Engineering at Google.docx")
SCOPE = RUN / "full_book_translation_scope.json"
PROGRESS_RE = re.compile(r"full_book_progress_v(\d+)\.json$")
BATCH_RE = re.compile(r"FULL_BOOK_BATCH_(\d+)$")


def _derive_rollover_context(run: Path) -> dict[str, object]:
    """Derive one successor identity solely from authoritative progress.

    Progress generations and batch labels are append-only lifecycle evidence;
    this adapter never supplies a batch-specific fallback.
    """
    progress_candidates = [
        (int(match.group(1)), path)
        for path in run.glob("full_book_progress_v*.json")
        if (match := PROGRESS_RE.fullmatch(path.name))
    ]
    if not progress_candidates:
        raise RuntimeError("AUTHORITATIVE_PROGRESS_MISSING")
    current_version, prior = max(progress_candidates)
    progress = json.loads(prior.read_text(encoding="utf-8"))
    if progress.get("artifact_type") != "FULL_BOOK_TRANSLATION_PROGRESS":
        raise RuntimeError("AUTHORITATIVE_PROGRESS_TYPE_INVALID")
    label = progress.get("latest_completed_batch")
    if not isinstance(label, str) or not (match := BATCH_RE.fullmatch(label)):
        raise RuntimeError("AUTHORITATIVE_PROGRESS_BATCH_INVALID")
    number = int(match.group(1))
    next_number = number + 1
    return {
        "PRIOR": prior,
        "BATCH": run / f"full_book_batch_{number:03d}",
        "SELECTION": run / f"full_book_batch_{number:03d}_selection.json",
        "NEXT_BATCH": run / f"full_book_batch_{next_number:03d}",
        "NEXT_SELECTION": run / f"full_book_batch_{next_number:03d}_selection.json",
        "COMPLETION": run / f"full_book_batch_{number:03d}_completion_report.json",
        "PROGRESS": run / f"full_book_progress_v{current_version + 1}.json",
        "BATCH_LABEL": label,
        "NEXT_BATCH_LABEL": f"FULL_BOOK_BATCH_{next_number:03d}",
    }


def _configure_from_authoritative_state(run: Path) -> None:
    global RUN, SCOPE, PRIOR, BATCH, SELECTION, NEXT_BATCH, NEXT_SELECTION, COMPLETION, PROGRESS, BATCH_LABEL, NEXT_BATCH_LABEL
    RUN = run.resolve()
    SCOPE = RUN / "full_book_translation_scope.json"
    context = _derive_rollover_context(RUN)
    for key, value in context.items():
        globals()[key] = value


_configure_from_authoritative_state(RUN)
SHA = fingerprint(SOURCE).sha256


def _checkpoint_count(target_count: int) -> int:
    if target_count < 1:
        raise RuntimeError("BATCH_TARGET_INVALID")
    return (target_count + CHECKPOINT_SIZE - 1) // CHECKPOINT_SIZE


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _release_gate_for_close() -> tuple[Path, dict, Path, dict] | None:
    """Return the only current, finalized release projection for this batch."""
    batch_path = getattr(BATCH, "path", BATCH)
    gates = sorted(batch_path.glob("release_gate_v*.json"))
    if not gates:
        # Historical combined rollovers predate the generic release gate. A
        # close-only operation, however, is always a release-boundary close
        # and therefore requires an explicit finalized gate.
        if "--close-only" not in sys.argv:
            return None
        raise RuntimeError("CANONICAL_CLOSE_RELEASE_GATE_MISSING")
    gate_path = gates[-1]
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    debt_path = Path(gate.get("release_debt_manifest", ""))
    if not debt_path.is_file():
        raise RuntimeError("CANONICAL_CLOSE_RELEASE_DEBT_MISSING")
    debt = json.loads(debt_path.read_text(encoding="utf-8"))
    if not (
        gate.get("artifact_type") == "BATCH_RELEASE_GATE"
        and gate.get("batch") == BATCH_LABEL
        and gate.get("verdict") == "PASS"
        and gate.get("unresolved_ids") == []
        and gate.get("integrity_pass") is True
        and gate.get("candidate_authority_pass") is True
        and gate.get("finalize_only") is True
        and gate.get("model_calls") == 0
        and gate.get("provider_dispatches") == 0
        and sha256(debt_path.read_bytes()).hexdigest() == gate.get("release_debt_manifest_sha256")
        and debt.get("artifact_type") == "BATCH_RELEASE_DEBT_MANIFEST"
        and debt.get("batch") == BATCH_LABEL
        and debt.get("current_release_debt_total") == 0
        and debt.get("entries") == []
        and debt.get("current_authoritative_state_digest") == gate.get("current_authoritative_state_digest")
    ):
        raise RuntimeError("CANONICAL_CLOSE_RELEASE_GATE_INVALID")
    return gate_path, gate, debt_path, debt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, help="canonical absolute run directory")
    parser.add_argument("--what-if", action="store_true")
    parser.add_argument("--close-only", action="store_true")
    parser.add_argument("--rollover-only", action="store_true")
    parser.add_argument("--phase", choices=("ordinary", "toc_late_stage"), default="ordinary")
    args = parser.parse_args()
    if args.run is not None:
        if not args.run.is_absolute():
            raise RuntimeError("CANONICAL_ABSOLUTE_RUN_PATH_REQUIRED")
        _configure_from_authoritative_state(args.run)
    if args.close_only and args.rollover_only:
        raise RuntimeError("CLOSURE_MODE_AMBIGUOUS")
    if fingerprint(SOURCE).sha256 != SHA:
        raise RuntimeError("SOURCE_MUTATED")
    if args.rollover_only:
        # A close-only progress snapshot is already the authoritative latest
        # progress; the wrapper's next-version output path is intentionally
        # unused during rollover-only.
        if not COMPLETION.is_file():
            raise RuntimeError("ROLLOVER_CANONICAL_CLOSE_MISSING")
        completion = json.loads(COMPLETION.read_text(encoding="utf-8"))
        if not (
            completion.get("batch") == BATCH_LABEL
            and completion.get("canonical_close_only") is True
            and completion.get("canonical_close") is True
        ):
            raise RuntimeError("ROLLOVER_CANONICAL_CLOSE_INVALID")
        # Rollover is a two-artifact commit: the frozen next-batch material
        # and the successor progress projection.  If an interruption occurs
        # after the former is durable but before the latter is published,
        # permit only an exact re-validation and completion of that commit.
        if NEXT_SELECTION.exists() != NEXT_BATCH.exists():
            raise RuntimeError("ROLLOVER_ARTIFACT_SET_INCOMPLETE")
    elif args.close_only:
        if NEXT_SELECTION.exists() or NEXT_BATCH.exists():
            raise RuntimeError("CLOSE_ONLY_NEXT_BATCH_ALREADY_EXISTS")
    elif any(path.exists() for path in (COMPLETION, PROGRESS, NEXT_SELECTION, NEXT_BATCH)):
        raise RuntimeError("ADVANCEMENT_ARTIFACT_ALREADY_EXISTS")
    batch_path = getattr(BATCH, "path", BATCH)
    manifest_path, manifest = resolve_authoritative_manifest(batch_path)
    selection, scope, prior = (
        json.loads(path.read_text(encoding="utf-8")) for path in (SELECTION, SCOPE, PRIOR)
    )
    selected_ids = selection.get("selected_logical_work_order_ids")
    if not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= BATCH_TARGET_MAX:
        raise RuntimeError("BATCH_SELECTION_TARGET_INVALID")
    target_count = len(selected_ids)
    if (
        manifest.get("batch") != BATCH_LABEL
        or manifest.get("status") != "COMPLETE"
        or manifest.get("target_count") != target_count
        or manifest.get("completed_count") != target_count
        or len(manifest.get("checkpoints", [])) != _checkpoint_count(target_count)
    ):
        raise RuntimeError("AUTHORITATIVE_BATCH_NOT_COMPLETE")

    observed: list[str] = []
    stats: list[dict[str, object]] = []
    raw_failures = 0
    for checkpoint_ordinal, checkpoint in enumerate(manifest["checkpoints"], start=1):
        directory = BATCH / checkpoint["id"]
        # A sealed checkpoint may keep its immutable seal evidence in an
        # append-only generation.  Use the same authoritative resolver as the
        # terminal-close adapter rather than assuming the legacy root path.
        batch_path = getattr(BATCH, "path", BATCH)
        _seal_directory, evidence_path, _final_path, _generation = resolve_checkpoint_seal_artifacts(
            batch_path, checkpoint
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        raw = json.loads((directory / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
        final = json.loads((directory / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        review = json.loads((directory / "semantic_review_report.json").read_text(encoding="utf-8"))
        ids = evidence.get("logical_work_orders")
        known_checkpoint_ids = set(ids) if isinstance(ids, list) else set()
        expected_ids = selected_ids[(checkpoint_ordinal - 1) * CHECKPOINT_SIZE : checkpoint_ordinal * CHECKPOINT_SIZE]
        expected_size = len(expected_ids)
        if (
            not isinstance(ids, list) or ids != expected_ids
            or raw.get("pass_count", 0) + raw.get("fail_count", 0) != expected_size
            or final.get("pass_count") != expected_size or final.get("fail_count") != 0
        ):
            raise RuntimeError(f"AUTHORITATIVE_CHECKPOINT_EVIDENCE_INVALID:{checkpoint['id']}")
        observed.extend(ids)
        raw_failures += raw["fail_count"]
        stats.append({
            "checkpoint": checkpoint["id"], "raw_qa_pass": raw["pass_count"], "raw_qa_fail": raw["fail_count"],
            "final_approved": final["pass_count"], "deterministic_fail": final["fail_count"],
            "human_required": review.get("human_required_count", 0),
        })
    if observed != selected_ids or len(set(observed)) != target_count:
        raise RuntimeError("AUTHORITATIVE_SELECTION_EVIDENCE_MISMATCH")

    released = derive_release_membership(RUN, scope)
    # Ordinary selection intentionally retains its historical meaning.  The
    # deferred TOC phase is entered only by an explicit lifecycle invocation.
    pending = (
        select_toc_late_stage(scope, released)
        if args.phase == "toc_late_stage" else pending_in_source_order(scope, released)
    )
    expected_release_count = prior["current_release_set_count"] if args.rollover_only else prior["current_release_set_count"] + target_count
    if set(observed) - released or len(released) != expected_release_count:
        raise RuntimeError("AUTHORITATIVE_RELEASE_MEMBERSHIP_MISMATCH")
    next_selected = pending[:BATCH_TARGET_MAX]
    ids = [item["logical_work_order_id"] for item in next_selected]
    # At the terminal end of the authoritative scope a close-only operation
    # has no successor to initialize.  It must still be able to materialize
    # the canonical close, while rollover modes continue to require a valid
    # non-empty successor selection.
    if (
        len(ids) > BATCH_TARGET_MAX
        or len(set(ids)) != len(ids)
        or set(ids) & released
        or (ids and ids[0] != pending[0]["logical_work_order_id"])
        or (not ids and not args.close_only)
    ):
        raise RuntimeError("SUCCESSOR_SELECTION_DERIVATION_MISMATCH")
    next_resume_point = ids[0] if ids else None

    phases: dict[str, dict[str, int]] = {}
    for order in scope["logical_work_orders"]:
        row = phases.setdefault(order["phase"], {"logical_total": 0, "final_approved": 0, "deterministic_fail": 0, "unstarted": 0})
        row["logical_total"] += 1
        row["final_approved" if order["logical_work_order_id"] in released else "unstarted"] += 1
    gate_binding = _release_gate_for_close()
    completion = {
        "artifact_type": "FULL_BOOK_BATCH_COMPLETION_REPORT", "batch": BATCH_LABEL, "status": "COMPLETE",
        "frozen_selection": str(SELECTION), "current_manifest": str(manifest_path),
        "macro_target": target_count, "completed_logical_work_orders": target_count, "checkpoint_stats": stats,
        "raw_deterministic_failure_count": raw_failures, "final_approved_count": target_count,
        "deterministic_fail_count": 0, "human_required_count": 0, "recovery_queue_size": 0,
        "current_release_approved_count": len(released),
        "remaining_unstarted_first_pass": sum(row["unstarted"] for row in phases.values()),
        "next_resume_point_derived_from_authoritative_scope": next_resume_point, "source_sha256": SHA,
        "canonical_close": True,
        "canonical_close_only": bool(args.close_only),
    }
    if gate_binding is not None:
        gate_path, gate, debt_path, _debt = gate_binding
        completion.update({
            "release_gate": str(gate_path),
            "release_gate_sha256": sha256(gate_path.read_bytes()).hexdigest(),
            "release_debt_manifest": str(debt_path),
            "release_debt_manifest_sha256": sha256(debt_path.read_bytes()).hexdigest(),
            "current_authoritative_state_digest": gate["current_authoritative_state_digest"],
        })
    progress = {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "supersedes": PRIOR.name,
        "total_physical_work_orders": prior["total_physical_work_orders"], "total_logical_work_orders": len(scope["logical_work_orders"]),
        "current_release_set_count": len(released), "unresolved_deterministic_fail": 0, "human_required": 0,
        "remaining_unstarted_first_pass": sum(row["unstarted"] for row in phases.values()), "recovery_queue_size": 0,
        "status": "FULL_BOOK_HARDENING_PENDING" if args.close_only else "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": BATCH_LABEL, "source_sha256": SHA,
        "current_progress_by_phase": phases, "recovery_queue": [], "batch_target_max": BATCH_TARGET_MAX,
        "next_resume_point_derived_from_authoritative_scope": next_resume_point,
        "derivation_evidence": {
            "prior_progress": str(PRIOR), "batch_manifest": str(manifest_path),
            "frozen_selection": str(SELECTION), "release_membership": "scope ALREADY_APPROVED plus immutable passing final-QA records",
        },
    }
    progress["full_book_scope_complete"] = full_book_scope_complete(progress)
    if progress["full_book_scope_complete"]:
        progress["status"] = "FULL_BOOK_PRODUCTION_COMPLETE"
    frozen = {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": NEXT_BATCH_LABEL, "macro_target": len(ids), "micro_checkpoint_size": CHECKPOINT_SIZE,
        "authoritative_scope": str(SCOPE), "authoritative_scope_sha256": sha256(SCOPE.read_bytes()).hexdigest(),
        "source_docx": str(SOURCE), "source_sha256": SHA, "prior_progress": str(PROGRESS),
        "authoritative_release_membership_method": "scope ALREADY_APPROVED plus immutable passing final-QA records",
        "selection_order": (
            "authoritative unreleased toc_late_stage source order"
            if args.phase == "toc_late_stage" else
            "authoritative remaining-first-pass source order; TOC remains late-stage content"
        ),
        "selected_logical_work_order_ids": ids,
        "selected_orders": [{key: item.get(key) for key in ("logical_work_order_id", "physical_member_display_ids", "composite_group_id", "phase", "release_status")} for item in next_selected],
        "selected_id_set_sha256": sha256("\n".join(ids).encode("utf-8")).hexdigest(),
        "selection_validation": {
            "selected_logical_count": len(ids), "duplicate_logical_count": 0, "previous_approved_overlap_count": 0,
            "existing_recovery_overlap_count": 0,
            "toc_late_stage_count": sum(item["phase"] == "toc_late_stage" for item in next_selected),
            "phase_counts": {phase: sum(item["phase"] == phase for item in next_selected) for phase in sorted({item["phase"] for item in next_selected})},
        },
    }
    if args.close_only and COMPLETION.exists():
        existing_completion = json.loads(COMPLETION.read_text(encoding="utf-8"))
        existing_progress = json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.is_file() else None
        if existing_completion == completion and existing_progress == progress:
            print(f"PASS: {BATCH_LABEL} canonical close reused; rollover withheld")
            return
        raise RuntimeError("CANONICAL_CLOSE_STATE_CONFLICT")
    if args.what_if:
        mode = "close-only" if args.close_only else ("rollover-only" if args.rollover_only else "combined")
        print(f"WHATIF PASS: batch={BATCH_LABEL}; mode={mode}; next={ids[0]}")
        return
    if args.close_only:
        write_once(COMPLETION, completion)
        write_once(PROGRESS, progress)
        if fingerprint(SOURCE).sha256 != SHA:
            raise RuntimeError("SOURCE_MUTATED")
        print(f"PASS: {BATCH_LABEL} canonically closed; rollover withheld")
        return
    # Do not expose the new batch through progress until its frozen selection
    # and initial manifest have both been written and validated together.
    if NEXT_SELECTION.exists():
        existing_frozen = json.loads(NEXT_SELECTION.read_text(encoding="utf-8"))
        if existing_frozen != frozen:
            raise RuntimeError("ROLLOVER_FROZEN_SELECTION_CONFLICT")
    else:
        write_once(NEXT_SELECTION, frozen)
        NEXT_BATCH.mkdir()
        bible = batch_path / "translation_bible_snapshot.md"
        if not bible.is_file():
            raise RuntimeError("AUTHORITATIVE_BIBLE_SNAPSHOT_MISSING")
        shutil.copyfile(bible, NEXT_BATCH / "translation_bible_snapshot.md")
        snapshot = NEXT_BATCH / "translation_bible_snapshot.md"
        update_manifest(NEXT_BATCH / "batch_manifest.json", {
            "batch": NEXT_BATCH_LABEL, "target_count": len(ids), "completed_count": 0, "checkpoint_count": 0,
            "remaining_in_batch": len(ids), "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "checkpoints": [],
            "source_sha256": SHA, "translation_bible_snapshot_sha256": sha256(snapshot.read_bytes()).hexdigest(),
            "frozen_selection": str(NEXT_SELECTION), "frozen_selection_sha256": sha256(NEXT_SELECTION.read_bytes()).hexdigest(),
        })
    next_manifest = json.loads((NEXT_BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if (
        frozen["batch"] != NEXT_BATCH_LABEL
        or not 1 <= len(frozen["selected_logical_work_order_ids"]) <= BATCH_TARGET_MAX
        or next_manifest.get("batch") != NEXT_BATCH_LABEL
        or next_manifest.get("target_count") != len(frozen["selected_logical_work_order_ids"])
        or next_manifest.get("next_checkpoint") != "checkpoint_01"
        or next_manifest.get("source_sha256") != SHA
        or next_manifest.get("frozen_selection") != str(NEXT_SELECTION)
        or next_manifest.get("frozen_selection_sha256") != sha256(NEXT_SELECTION.read_bytes()).hexdigest()
    ):
        raise RuntimeError("NEXT_BATCH_READINESS_VALIDATION_FAILED")
    if args.rollover_only:
        # The close-only projection remains immutable.  Rollover publishes a
        # distinct successor projection that makes the already validated next
        # batch the authoritative active batch.
        if PROGRESS.is_file():
            if json.loads(PROGRESS.read_text(encoding="utf-8")) != progress:
                raise RuntimeError("ROLLOVER_PROGRESS_STATE_CONFLICT")
        else:
            write_once(PROGRESS, progress)
    else:
        write_once(COMPLETION, completion)
        write_once(PROGRESS, progress)
    if fingerprint(SOURCE).sha256 != SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print(f"PASS: {BATCH_LABEL} closed; {NEXT_BATCH_LABEL} initialized; next={ids[0]}")


if __name__ == "__main__":
    main()
