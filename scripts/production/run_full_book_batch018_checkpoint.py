"""Seal one preflight-validated Batch 018 checkpoint through the existing Harness chain."""

from __future__ import annotations

from pathlib import Path

import run_full_book_batch016_checkpoint as runner


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"


def main() -> None:
    runner.BATCH = RUN / "full_book_batch_018"
    runner.SELECTION = RUN / "full_book_batch_018_selection.json"
    runner.MANIFEST = runner.BATCH / "batch_manifest.json"
    runner.main()


if __name__ == "__main__":
    main()
