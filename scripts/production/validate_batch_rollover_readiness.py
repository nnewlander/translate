"""Validate that a newly opened full-book batch is safe to give a worker.

This is intentionally read-only.  It verifies the derived campaign index
against the immutable progress, selection, and current manifest before a
fresh worker can create a checkpoint directory or RAW response.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any

from booktrans.harness.source_integrity import fingerprint
from booktrans.production_worker.selection import BATCH_TARGET_MAX, CHECKPOINT_SIZE
from booktrans.production_worker.selection import D050PreflightError, ensure_checkpoint_d050_preflight
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


ROOT = Path(r"D:\translate")
DEFAULT_RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
DEFAULT_SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH_RE = re.compile(r"FULL_BOOK_BATCH_(\d{3})$")


def load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{code}:{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{code}:{path}")
    return value


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def expected_batch_after(completed: str) -> str:
    match = BATCH_RE.fullmatch(completed)
    if not match:
        raise RuntimeError("BATCH_READY_PROGRESS_BATCH_INVALID")
    return f"FULL_BOOK_BATCH_{int(match.group(1)) + 1:03d}"


def _d050_checkpoint_scopes(selection: dict[str, Any], manifest: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Derive every frozen D050 scope, including the legal final remainder."""
    """Build/reuse exact D050 evidence for every frozen checkpoint once.

    This is intentionally an extension of the existing exact checkpoint
    preflight: there is no batch-local D050 authority.  A batch can be marked
    ready only after every statically reachable frozen slice has received one
    of the existing exact outcomes (non-risk or exact-ready).  Any ambiguous
    slice fails before a production writer is launched.
    """
    ids = selection.get("selected_logical_work_order_ids")
    target = manifest.get("target_count")
    if not isinstance(ids, list) or not ids or not isinstance(target, int) or target != len(ids):
        raise RuntimeError("BATCH_READY_D050_SELECTION_INVALID")
    checkpoint_count = (target + CHECKPOINT_SIZE - 1) // CHECKPOINT_SIZE
    scopes = [(f"checkpoint_{ordinal:02d}", ids[(ordinal - 1) * CHECKPOINT_SIZE : ordinal * CHECKPOINT_SIZE]) for ordinal in range(1, checkpoint_count + 1)]
    if any(len(scope) != CHECKPOINT_SIZE for _checkpoint, scope in scopes[:-1]) or not (1 <= len(scopes[-1][1]) <= CHECKPOINT_SIZE) or sum(len(scope) for _checkpoint, scope in scopes) != target:
        raise RuntimeError("BATCH_READY_D050_SCOPE_INVALID")
    return scopes


def prepare_batch_d050_readiness(run: Path, source: Path, active: str, selection: dict[str, Any], manifest: dict[str, Any]) -> dict[str, int]:
    """Build/reuse exact D050 evidence for every manifest-derived scope once."""
    scopes = _d050_checkpoint_scopes(selection, manifest)
    source_pdf = source.with_suffix(".pdf")
    if not source_pdf.is_file():
        raise RuntimeError("BATCH_READY_D050_SOURCE_PDF_MISSING")
    source_pdf_sha256 = fingerprint(source_pdf).sha256
    risk_records = 0
    checked = 0
    for checkpoint, expected_ids in scopes:
        try:
            frozen = frozen_batch_checkpoint_inputs(run, batch_label=active, checkpoint=checkpoint)
            result = ensure_checkpoint_d050_preflight(
                run=run,
                checkpoint=checkpoint,
                order=list(frozen["order"]),
                views=frozen["views"],
                source_docx=source,
                source_pdf=source_pdf,
                source_sha256=str(frozen["source_sha256"]),
                source_pdf_sha256=source_pdf_sha256,
                batch_label=active,
            )
        except (D050PreflightError, OSError, ValueError) as exc:
            raise RuntimeError(f"BATCH_READY_D050_UNRESOLVED:{checkpoint}:{exc}") from exc
        if list(frozen["order"]) != expected_ids:
            raise RuntimeError(f"BATCH_READY_D050_SCOPE_MEMBERSHIP_MISMATCH:{checkpoint}")
        checked += len(expected_ids)
        risk_records += int(result.get("candidate_count", 0))
    return {"risk_records": risk_records, "selected_units_checked": checked}


def validate(
    run: Path,
    source: Path,
    state_path: Path,
    *,
    allow_current_partial: bool = False,
    prepare_d050_readiness: bool = False,
) -> str:
    state = load(state_path, "BATCH_READY_STATE_INVALID")
    if state.get("artifact_type") != "PRODUCTION_CAMPAIGN_STATE" or state.get("derived_from_immutable_artifacts") is not True:
        raise RuntimeError("BATCH_READY_STATE_TYPE_INVALID")
    active = state.get("active_batch")
    if not isinstance(active, str) or not BATCH_RE.fullmatch(active):
        raise RuntimeError("BATCH_READY_ACTIVE_BATCH_INVALID")
    progress_ref = state.get("latest_authoritative_progress")
    manifest_ref = state.get("active_manifest")
    selection_ref = state.get("active_selection")
    if not all(isinstance(item, dict) for item in (progress_ref, manifest_ref, selection_ref)):
        raise RuntimeError("BATCH_READY_STATE_BINDING_INVALID")
    progress_path = Path(progress_ref.get("path", ""))
    manifest_path = Path(manifest_ref.get("path", ""))
    selection_path = Path(selection_ref.get("path", ""))
    if not all(path.is_file() for path in (progress_path, manifest_path, selection_path)):
        raise RuntimeError("BATCH_READY_BOUND_ARTIFACT_MISSING")
    if digest(progress_path) != progress_ref.get("sha256"):
        raise RuntimeError("BATCH_READY_PROGRESS_SHA_MISMATCH")
    if digest(manifest_path) != manifest_ref.get("sha256"):
        raise RuntimeError("BATCH_READY_MANIFEST_SHA_MISMATCH")
    if digest(selection_path) != selection_ref.get("sha256"):
        raise RuntimeError("BATCH_READY_SELECTION_SHA_MISMATCH")
    progress = load(progress_path, "BATCH_READY_PROGRESS_INVALID")
    selection = load(selection_path, "BATCH_READY_SELECTION_INVALID")
    manifest = load(manifest_path, "BATCH_READY_MANIFEST_INVALID")
    source_sha = fingerprint(source).sha256
    if not all(item.get("source_sha256") == source_sha for item in (state, progress, selection, manifest)):
        raise RuntimeError("BATCH_READY_SOURCE_SHA_MISMATCH")
    if progress.get("artifact_type") != "FULL_BOOK_TRANSLATION_PROGRESS" or expected_batch_after(progress.get("latest_completed_batch")) != active:
        raise RuntimeError("BATCH_READY_PROGRESS_ACTIVE_BATCH_MISMATCH")
    ids = selection.get("selected_logical_work_order_ids")
    if selection.get("artifact_type") != "FULL_BOOK_BATCH_SELECTION" or selection.get("batch") != active:
        raise RuntimeError("BATCH_READY_SELECTION_BATCH_ID_MISMATCH")
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= BATCH_TARGET_MAX
        or len(set(ids)) != len(ids)
        or not all(isinstance(item, str) for item in ids)
    ):
        raise RuntimeError("BATCH_READY_SELECTION_TARGET_COUNT_MISMATCH")
    if manifest.get("batch") != active:
        raise RuntimeError("BATCH_READY_MANIFEST_BATCH_ID_MISMATCH")
    completed = manifest.get("completed_count")
    checkpoint_count = manifest.get("checkpoint_count")
    expected_checkpoint_count = (completed + CHECKPOINT_SIZE - 1) // CHECKPOINT_SIZE if isinstance(completed, int) and completed else 0
    if (
        manifest.get("target_count") != len(ids)
        or not isinstance(completed, int)
        or not isinstance(checkpoint_count, int)
        or completed < 0
        or completed > len(ids)
        or (completed != len(ids) and completed % CHECKPOINT_SIZE != 0)
        or checkpoint_count != expected_checkpoint_count
    ):
        raise RuntimeError("BATCH_READY_MANIFEST_TARGET_COUNT_MISMATCH")
    expected_checkpoint = None if completed == len(ids) else f"checkpoint_{checkpoint_count + 1:02d}"
    expected_status = "COMPLETE" if completed == len(ids) else "OPEN_PROGRESS"
    if manifest.get("status") != expected_status or manifest.get("next_checkpoint") != expected_checkpoint:
        raise RuntimeError("BATCH_READY_MANIFEST_NEXT_CHECKPOINT_MISMATCH")
    if manifest.get("frozen_selection") != str(selection_path) or manifest.get("frozen_selection_sha256") != digest(selection_path):
        raise RuntimeError("BATCH_READY_MANIFEST_SELECTION_BINDING_MISMATCH")
    if state.get("target_count") != len(ids) or state.get("completed_count") != completed or state.get("next_checkpoint") != expected_checkpoint:
        raise RuntimeError("BATCH_READY_STATE_NEXT_CHECKPOINT_MISMATCH")
    expected_work_order = None if completed == len(ids) else ids[completed]
    if state.get("next_logical_work_order_id") != expected_work_order or (completed == 0 and progress.get("next_resume_point_derived_from_authoritative_scope") != ids[0]):
        raise RuntimeError("BATCH_READY_NEXT_WORK_ORDER_MISMATCH")
    if state.get("recovery_queue") != [] or progress.get("recovery_queue") != []:
        raise RuntimeError("BATCH_READY_RECOVERY_QUEUE_NOT_EMPTY")
    batch_path = run / f"full_book_batch_{BATCH_RE.fullmatch(active).group(1)}"
    if manifest_path.parent != batch_path or selection_path != run / f"full_book_batch_{BATCH_RE.fullmatch(active).group(1)}_selection.json":
        raise RuntimeError("BATCH_READY_PATH_BINDING_MISMATCH")
    if expected_checkpoint and (batch_path / expected_checkpoint).exists():
        partial = batch_path / expected_checkpoint
        # Fresh translation still requires an absent directory.  A supervisor
        # resume may admit only its one authoritative current cursor partial;
        # a seal or any other unsealed checkpoint remains a hard conflict.
        if not allow_current_partial or (partial / "checkpoint_evidence.json").exists():
            raise RuntimeError(f"BATCH_READY_UNEXPECTED_PARTIAL_{expected_checkpoint.upper()}")
        checkpoint_dirs = [item.name for item in batch_path.glob("checkpoint_*") if item.is_dir()]
        sealed_names = {str(item.get("id")) for item in manifest.get("checkpoints") or [] if isinstance(item, dict)}
        unexpected = sorted(name for name in checkpoint_dirs if name not in sealed_names and name != expected_checkpoint)
        if unexpected:
            raise RuntimeError("BATCH_READY_COMPETING_PARTIAL_CHECKPOINT")
    if prepare_d050_readiness:
        coverage = prepare_batch_d050_readiness(run, source, active, selection, manifest)
        if coverage["selected_units_checked"] != len(ids):
            raise RuntimeError("BATCH_READY_D050_COVERAGE_INCOMPLETE")
    return active


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--allow-current-partial", action="store_true", help="Permit only the current unsealed cursor checkpoint for a supervised resume.")
    parser.add_argument("--prepare-d050-readiness", action="store_true", help="Build/reuse exact D050 evidence for every frozen checkpoint before initial batch production.")
    args = parser.parse_args()
    state = args.state or args.run / "production_campaign_state.json"
    try:
        batch = validate(
            args.run, args.source, state,
            allow_current_partial=args.allow_current_partial,
            prepare_d050_readiness=args.prepare_d050_readiness,
        )
    except RuntimeError as exc:
        print(f"BATCH_READY FAIL {exc}")
        raise SystemExit(2) from exc
    print(f"BATCH_READY PASS batch={batch}")


if __name__ == "__main__":
    main()
