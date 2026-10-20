"""Derive the resumable full-book campaign index from immutable artifacts."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any

from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.batch_manifest import (
    commit_prepared_checkpoint,
    manifest_sha256,
    resolve_authoritative_manifest,
    resolve_checkpoint_seal_artifacts,
)


ROOT = Path(r"D:\translate")
DEFAULT_RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
DEFAULT_SOURCE = ROOT / "Software Engineering at Google.docx"
STATE_NAME = "production_campaign_state.json"
PROGRESS_RE = re.compile(r"full_book_progress_v(\d+)\.json$")
BATCH_RE = re.compile(r"full_book_batch_(\d{3})$")


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"{code}:{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{code}:{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{code}:{path}")
    return value


def _sha(path: Path) -> str:
    return manifest_sha256(path)


def _latest_progress(run: Path, source_sha: str) -> tuple[Path, dict[str, Any]]:
    candidates = [(int(match.group(1)), path) for path in run.glob("full_book_progress_v*.json") if (match := PROGRESS_RE.fullmatch(path.name))]
    if not candidates:
        raise RuntimeError("AUTHORITATIVE_PROGRESS_MISSING")
    _, path = max(candidates)
    value = _load_json(path, "AUTHORITATIVE_PROGRESS_INVALID")
    if value.get("artifact_type") != "FULL_BOOK_TRANSLATION_PROGRESS":
        raise RuntimeError("AUTHORITATIVE_PROGRESS_TYPE_INVALID")
    if value.get("source_sha256") != source_sha:
        raise RuntimeError("AUTHORITATIVE_PROGRESS_SOURCE_MISMATCH")
    return path, value


def _current_manifest(batch_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Return the immutable current manifest, preserving a historical base.

    A successor manifest is valid only when it cryptographically names the
    historical base it supersedes.  This supports targeted reconciliations
    without allowing an arbitrary extra manifest to become current.
    """
    try:
        return resolve_authoritative_manifest(batch_dir)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _open_batch(run: Path, latest_completed_batch: int) -> tuple[int, Path, Path, dict[str, Any]]:
    candidates: list[tuple[int, Path, Path, dict[str, Any]]] = []
    for path in run.iterdir():
        match = BATCH_RE.fullmatch(path.name)
        historical_manifest_path = path / "batch_manifest.json"
        if path.is_dir() and match and historical_manifest_path.is_file():
            manifest_path, manifest = _current_manifest(path)
            expected_batch = f"FULL_BOOK_BATCH_{match.group(1)}"
            if manifest.get("batch") != expected_batch:
                raise RuntimeError("ACTIVE_MANIFEST_BATCH_ID_MISMATCH")
            if manifest.get("status") == "OPEN_PROGRESS":
                candidates.append((int(match.group(1)), path, manifest_path, manifest))
    current = [candidate for candidate in candidates if candidate[0] > latest_completed_batch]
    if len(current) != 1 or current[0][0] != latest_completed_batch + 1:
        raise RuntimeError(f"ACTIVE_BATCH_COUNT_INVALID:{len(current)}")
    # Earlier schema eras can retain incomplete historical manifests.  They are
    # immutable history once a later authoritative progress snapshot names a
    # completed batch, never a second current writer.
    return current[0]


def _terminal_complete_batch(run: Path, latest_completed_batch: int) -> tuple[int, Path, Path, dict[str, Any]] | None:
    """Return one numerically next, sealed-complete batch at a release boundary.

    A completed batch is not an additional resumable writer.  This narrow path
    exists for the intentional interval after its final checkpoint seals and
    before a later batch is explicitly initialized.  Authority remains the
    manifest/selection binding; no filesystem time ordering is involved.
    """
    candidates: list[tuple[int, Path, Path, dict[str, Any]]] = []
    for path in run.iterdir():
        match = BATCH_RE.fullmatch(path.name)
        historical_manifest_path = path / "batch_manifest.json"
        if not (path.is_dir() and match and historical_manifest_path.is_file()):
            continue
        number = int(match.group(1))
        if number != latest_completed_batch + 1:
            continue
        manifest_path, manifest = _current_manifest(path)
        if (
            manifest.get("batch") == f"FULL_BOOK_BATCH_{number:03d}"
            and manifest.get("status") == "COMPLETE"
        ):
            candidates.append((number, path, manifest_path, manifest))
    if len(candidates) > 1:
        raise RuntimeError(f"TERMINAL_BATCH_COUNT_INVALID:{len(candidates)}")
    return candidates[0] if candidates else None


def _canonical_closed_batch(run: Path, latest_completed_batch: int, progress: dict[str, Any]) -> tuple[int, Path, Path, dict[str, Any]] | None:
    """Recognize a close-only batch while certification intentionally withholds rollover."""
    if progress.get("status") != "FULL_BOOK_HARDENING_PENDING":
        return None
    batch_dir = run / f"full_book_batch_{latest_completed_batch:03d}"
    completion_path = run / f"full_book_batch_{latest_completed_batch:03d}_completion_report.json"
    if not batch_dir.is_dir() or not completion_path.is_file():
        return None
    completion = _load_json(completion_path, "CANONICAL_CLOSE_RECORD_INVALID")
    manifest_path, manifest = _current_manifest(batch_dir)
    if not (
        completion.get("artifact_type") == "FULL_BOOK_BATCH_COMPLETION_REPORT"
        and completion.get("batch") == f"FULL_BOOK_BATCH_{latest_completed_batch:03d}"
        and completion.get("canonical_close") is True
        and completion.get("canonical_close_only") is True
        # Completion records the authoritative manifest selected at close
        # time.  It can legitimately be a versioned successor rather than
        # the legacy root name, so compare it with this rebuild's own
        # authoritative resolver result.
        and completion.get("current_manifest") == str(manifest_path)
        and manifest.get("status") == "COMPLETE"
    ):
        raise RuntimeError("CANONICAL_CLOSE_RECORD_INVALID")
    return latest_completed_batch, batch_dir, manifest_path, manifest


def _selection_for(run: Path, batch_number: int) -> tuple[Path, dict[str, Any]]:
    stem = f"full_book_batch_{batch_number:03d}_selection"
    # Version is explicit authority metadata, never filesystem ordering.  A
    # lexical sort would incorrectly prefer v9 over v10 and could revive a
    # superseded selector during a later reconciliation.
    version_pattern = re.compile(rf"{re.escape(stem)}_reconciled_v(\d+)\.json$")
    candidates = [
        (int(match.group(1)), path)
        for path in run.glob(f"{stem}_reconciled_v*.json")
        if (match := version_pattern.fullmatch(path.name))
    ]
    path = max(candidates, default=(0, run / f"{stem}.json"), key=lambda item: item[0])[1]
    selection = _load_json(path, "ACTIVE_SELECTION_INVALID")
    ids = selection.get("selected_logical_work_order_ids")
    if selection.get("batch") != f"FULL_BOOK_BATCH_{batch_number:03d}" or not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        raise RuntimeError("ACTIVE_SELECTION_SCHEMA_INVALID")
    if len(ids) != len(set(ids)):
        raise RuntimeError("ACTIVE_SELECTION_DUPLICATE_ID")
    return path, selection


def _checkpoint_ids(checkpoint: dict[str, Any], directory: Path) -> list[str]:
    try:
        checkpoint_dir, evidence_path, final_path, _generation = resolve_checkpoint_seal_artifacts(directory.parent, checkpoint)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    evidence = _load_json(evidence_path, "CHECKPOINT_EVIDENCE_INVALID")
    ids = evidence.get("logical_work_orders")
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        raise RuntimeError(f"CHECKPOINT_EVIDENCE_IDS_INVALID:{directory.name}")
    raw = checkpoint_dir / "translation_raw_response.jsonl"
    if not raw.is_file() or checkpoint.get("raw_sha256") != _sha(raw):
        raise RuntimeError(f"CHECKPOINT_RAW_SHA_MISMATCH:{directory.name}")
    final = _load_json(final_path, "CHECKPOINT_FINAL_QA_INVALID")
    if final.get("pass_count") != len(ids) or final.get("fail_count") != 0:
        raise RuntimeError(f"CHECKPOINT_FINAL_QA_NOT_SEALED:{directory.name}")
    if checkpoint.get("actual_count") != len(ids) or checkpoint.get("final_pass") != len(ids):
        raise RuntimeError(f"CHECKPOINT_MANIFEST_DRIFT:{directory.name}")
    return ids


def _validate_manifest(batch_dir: Path, manifest: dict[str, Any], selection_ids: list[str]) -> list[Path]:
    target, completed, checkpoints = manifest.get("target_count"), manifest.get("completed_count"), manifest.get("checkpoints")
    if not isinstance(target, int) or not isinstance(completed, int) or not isinstance(checkpoints, list):
        raise RuntimeError("BATCH_MANIFEST_SCHEMA_INVALID")
    if target != len(selection_ids) or completed < 0 or completed > target:
        raise RuntimeError("BATCH_SELECTION_MANIFEST_COUNT_MISMATCH")
    observed: list[str] = []
    d050_paths: list[Path] = []
    for index, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict) or checkpoint.get("id") != f"checkpoint_{index:02d}":
            raise RuntimeError("CHECKPOINT_SEQUENCE_INVALID")
        directory = batch_dir / checkpoint["id"]
        observed.extend(_checkpoint_ids(checkpoint, directory))
        _, evidence_path, _, _ = resolve_checkpoint_seal_artifacts(batch_dir, checkpoint)
        artifacts = _load_json(evidence_path, "CHECKPOINT_EVIDENCE_INVALID").get("d050_evidence_artifacts", [])
        if not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts):
            raise RuntimeError(f"CHECKPOINT_D050_EVIDENCE_INVALID:{checkpoint['id']}")
        d050_paths.extend(Path(item) for item in artifacts)
    if observed != selection_ids[:completed] or len(observed) != completed:
        raise RuntimeError("SEALED_CHECKPOINT_SELECTION_PREFIX_MISMATCH")
    if manifest.get("checkpoint_count") != len(checkpoints):
        raise RuntimeError("BATCH_MANIFEST_CHECKPOINT_COUNT_MISMATCH")
    expected_next = None if completed == target else f"checkpoint_{len(checkpoints) + 1:02d}"
    if manifest.get("next_checkpoint") != expected_next:
        raise RuntimeError("BATCH_MANIFEST_NEXT_CHECKPOINT_MISMATCH")
    return d050_paths


def _existing_paths(paths: list[Path], run: Path, batch_number: int) -> list[dict[str, str]]:
    paths.extend(run.glob(f"chapter_*_batch{batch_number:03d}_verified_footnote_evidence_v*.json"))
    unique: dict[Path, None] = {}
    for path in paths:
        absolute = path if path.is_absolute() else (ROOT / path)
        if not absolute.is_file():
            raise RuntimeError(f"D050_REGISTRY_MISSING:{absolute}")
        unique[absolute.resolve()] = None
    return [{"path": str(path), "sha256": _sha(path)} for path in sorted(unique, key=lambda item: str(item).lower())]


def derive_campaign_state(run: Path, source: Path, *, baseline_test_count: int = 119) -> dict[str, Any]:
    source_sha = fingerprint(source).sha256
    progress_path, progress = _latest_progress(run, source_sha)
    completed_label = progress.get("latest_completed_batch")
    match = re.fullmatch(r"FULL_BOOK_BATCH_(\d{3})", completed_label) if isinstance(completed_label, str) else None
    if not match:
        raise RuntimeError("AUTHORITATIVE_PROGRESS_LATEST_BATCH_INVALID")
    latest_completed_batch = int(match.group(1))
    terminal_boundary = False
    canonical_closed = False
    try:
        batch_number, batch_dir, manifest_path, manifest = _open_batch(run, latest_completed_batch)
    except RuntimeError as error:
        # Zero OPEN_PROGRESS batches is normal only for a fully sealed next
        # batch awaiting release-boundary work.  Any other open-batch error
        # remains fail-closed.
        if str(error) != "ACTIVE_BATCH_COUNT_INVALID:0":
            raise
        terminal = _terminal_complete_batch(run, latest_completed_batch)
        if terminal is None:
            closed = _canonical_closed_batch(run, latest_completed_batch, progress)
            if closed is None:
                raise
            batch_number, batch_dir, manifest_path, manifest = closed
            canonical_closed = True
        else:
            batch_number, batch_dir, manifest_path, manifest = terminal
            terminal_boundary = True
    selection_path, selection = _selection_for(run, batch_number)
    ids = selection["selected_logical_work_order_ids"]
    d050_paths = _validate_manifest(batch_dir, manifest, ids)
    completed = manifest["completed_count"]
    recovery = progress.get("recovery_queue", [])
    if not isinstance(recovery, list):
        raise RuntimeError("PROGRESS_RECOVERY_QUEUE_INVALID")
    if canonical_closed:
        status, stop_reason = "BATCH_CANONICALLY_CLOSED", "HARDENING_CERTIFICATION_REQUIRED"
    elif terminal_boundary:
        status, stop_reason = "BATCH_BOUNDARY_READY", "LATEST_BATCH_COMPLETE"
    elif progress.get("unresolved_deterministic_fail", 0):
        status, stop_reason = "SYSTEMIC_BLOCKER", "UNRESOLVED_DETERMINISTIC_FAILURE"
    elif progress.get("human_required", 0):
        status, stop_reason = "HUMAN_REQUIRED", "PROGRESS_HUMAN_REQUIRED"
    elif progress.get("status") != "FULL_BOOK_PRODUCTION_IN_PROGRESS":
        status, stop_reason = "SYSTEMIC_BLOCKER", "PROGRESS_STATUS_NOT_CONTINUABLE"
    else:
        status, stop_reason = "CONTINUE", None
    return {
        "schema_version": 1, "artifact_type": "PRODUCTION_CAMPAIGN_STATE", "derived_from_immutable_artifacts": True,
        "source_sha256": source_sha,
        "latest_authoritative_progress": {"path": str(progress_path), "sha256": _sha(progress_path)},
        "active_batch": f"FULL_BOOK_BATCH_{batch_number:03d}",
        "active_manifest": {"path": str(manifest_path), "sha256": _sha(manifest_path)},
        "active_selection": {"path": str(selection_path), "sha256": _sha(selection_path)},
        "target_count": manifest["target_count"], "completed_count": completed,
        "completed_checkpoints": [item["id"] for item in manifest["checkpoints"]],
        "next_checkpoint": manifest["next_checkpoint"],
        "next_logical_work_order_id": None if completed == len(ids) else ids[completed],
        "recovery_queue": recovery, "latest_d050_registries": _existing_paths(d050_paths, run, batch_number),
        "baseline_test_count": baseline_test_count, "campaign_status": status, "stop_reason": stop_reason,
    }


def write_state_atomic(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise RuntimeError(f"CAMPAIGN_STATE_TEMP_ALREADY_EXISTS:{temporary}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-test-count", type=int, default=119)
    parser.add_argument("--commit-prepared-checkpoint")
    args = parser.parse_args()
    if args.commit_prepared_checkpoint:
        before = derive_campaign_state(args.run, args.source, baseline_test_count=args.baseline_test_count)
        match = re.fullmatch(r"FULL_BOOK_BATCH_(\d{3})", str(before["active_batch"]))
        if not match:
            raise RuntimeError("ACTIVE_BATCH_ID_INVALID")
        batch_dir = args.run / f"full_book_batch_{match.group(1)}"
        checkpoint_dir = batch_dir / args.commit_prepared_checkpoint
        try:
            manifest_path, manifest, committed = commit_prepared_checkpoint(batch_dir, checkpoint_dir)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        print(json.dumps({"prepared_checkpoint_commit": args.commit_prepared_checkpoint,
                          "committed": committed, "manifest": str(manifest_path),
                          "completed_count": manifest["completed_count"]}, ensure_ascii=False))
    write_state_atomic(args.output or args.run / STATE_NAME, derive_campaign_state(args.run, args.source, baseline_test_count=args.baseline_test_count))


if __name__ == "__main__":
    main()
