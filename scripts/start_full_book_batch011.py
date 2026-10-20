"""Create the Batch 011 derived manifest from its frozen immutable selection."""
from __future__ import annotations

from hashlib import sha256
import json
import shutil
from pathlib import Path

import run_full_book_batch006_checkpoint as runner
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.source_integrity import fingerprint

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH, SELECTION = RUN / "full_book_batch_011", RUN / "full_book_batch_011_selection.json"
SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"

def main() -> None:
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")
    selection = json.loads(SELECTION.read_text(encoding="utf-8")); ids = selection.get("selected_logical_work_order_ids")
    if selection.get("batch") != "FULL_BOOK_BATCH_011" or not isinstance(ids, list) or len(ids) != 200 or len(set(ids)) != 200: raise RuntimeError("BATCH_011_SELECTION_INVALID")
    if BATCH.exists(): raise RuntimeError("BATCH_ALREADY_EXISTS")
    BATCH.mkdir(); shutil.copyfile(runner.PRODUCTION.BIBLE, BATCH / "translation_bible_snapshot.md")
    snapshot = BATCH / "translation_bible_snapshot.md"
    update_manifest(BATCH / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_011", "target_count": 200, "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": 200, "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "checkpoints": [], "source_sha256": SHA, "translation_bible_snapshot_sha256": sha256(snapshot.read_bytes()).hexdigest()})
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")

if __name__ == "__main__": main()
