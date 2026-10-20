"""Create an immutable successor manifest for the B015 rollover identity fix."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH = RUN / "full_book_batch_015"
HISTORICAL = BATCH / "batch_manifest.json"
CURRENT = BATCH / "batch_manifest_v2.json"
RECONCILIATION = RUN / "full_book_batch_015_manifest_reconciliation_v1.json"
SELECTION = RUN / "full_book_batch_015_selection.json"
PROGRESS = RUN / "full_book_progress_v16.json"
EXPECTED_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"


def read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_once(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--what-if", action="store_true")
    args = parser.parse_args()
    if any(path.exists() for path in (CURRENT, RECONCILIATION)):
        raise RuntimeError("B015_MANIFEST_RECONCILIATION_ALREADY_EXISTS")
    if fingerprint(SOURCE).sha256 != EXPECTED_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    historical, selection, progress = read(HISTORICAL), read(SELECTION), read(PROGRESS)
    ids = selection.get("selected_logical_work_order_ids")
    if historical.get("batch") != "FULL_BOOK_BATCH_014":
        raise RuntimeError("B015_HISTORICAL_MANIFEST_UNEXPECTED")
    if (
        selection.get("artifact_type") != "FULL_BOOK_BATCH_SELECTION"
        or selection.get("batch") != "FULL_BOOK_BATCH_015"
        or not isinstance(ids, list)
        or len(ids) != 200
        or len(set(ids)) != 200
        or ids[0] != "body.p.04535"
    ):
        raise RuntimeError("B015_SELECTION_NOT_UNIQUELY_RECONCILABLE")
    if progress.get("latest_completed_batch") != "FULL_BOOK_BATCH_014" or progress.get("next_resume_point_derived_from_authoritative_scope") != ids[0]:
        raise RuntimeError("B015_PROGRESS_NOT_UNIQUELY_RECONCILABLE")
    if any(item.get("source_sha256") != EXPECTED_SHA for item in (historical, selection, progress)):
        raise RuntimeError("B015_SOURCE_BINDING_MISMATCH")
    if historical.get("target_count") != 200 or historical.get("completed_count") != 0 or historical.get("checkpoint_count") != 0 or historical.get("next_checkpoint") != "checkpoint_01" or historical.get("status") != "OPEN_PROGRESS":
        raise RuntimeError("B015_HISTORICAL_MANIFEST_CONTENT_UNEXPECTED")
    if (BATCH / "checkpoint_01").exists():
        raise RuntimeError("B015_PARTIAL_CHECKPOINT_PRESENT")
    selection_sha = sha256(SELECTION.read_bytes()).hexdigest()
    historical_sha = sha256(HISTORICAL.read_bytes()).hexdigest()
    current = dict(historical)
    current.update({
        "manifest_version": 2,
        "batch": "FULL_BOOK_BATCH_015",
        "supersedes_manifest": {
            "path": str(HISTORICAL), "sha256": historical_sha,
            "reason": "ACTIVE_MANIFEST_BATCH_ID_MISMATCH",
        },
        "frozen_selection": str(SELECTION),
        "frozen_selection_sha256": selection_sha,
        "reconciliation_evidence": str(RECONCILIATION),
    })
    evidence = {
        "artifact_type": "FULL_BOOK_BATCH_MANIFEST_RECONCILIATION",
        "batch": "FULL_BOOK_BATCH_015",
        "status": "CURRENT_MANIFEST_RECONCILED",
        "historical_manifest": {"path": str(HISTORICAL), "sha256": historical_sha, "declared_batch": historical["batch"]},
        "current_manifest": {"path": str(CURRENT), "declared_batch": "FULL_BOOK_BATCH_015"},
        "selection": {"path": str(SELECTION), "sha256": selection_sha, "declared_batch": selection["batch"], "target_count": len(ids), "first_logical_work_order_id": ids[0]},
        "progress": {"path": str(PROGRESS), "latest_completed_batch": progress["latest_completed_batch"], "next_logical_work_order_id": progress["next_resume_point_derived_from_authoritative_scope"]},
        "source_sha256": EXPECTED_SHA,
        "correction": "Only the successor manifest batch identity changes from FULL_BOOK_BATCH_014 to FULL_BOOK_BATCH_015; historical manifest is preserved.",
        "checkpoint_01_existed_before_reconciliation": False,
    }
    if args.what_if:
        print("WHATIF PASS: B015 successor manifest is uniquely reconcilable; checkpoint_01 absent")
        return
    write_once(CURRENT, current)
    evidence["current_manifest"]["sha256"] = sha256(CURRENT.read_bytes()).hexdigest()
    write_once(RECONCILIATION, evidence)
    if fingerprint(SOURCE).sha256 != EXPECTED_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    print("PASS: B015 successor manifest and reconciliation evidence written")


if __name__ == "__main__":
    main()
