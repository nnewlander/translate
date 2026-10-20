"""Deterministically close sealed Batch 018 and initialize Batch 019."""
from __future__ import annotations

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import advance_full_book_batch013 as policy

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
policy.PRIOR = RUN / "full_book_progress_v19.json"
policy.BATCH = RUN / "full_book_batch_018"
policy.SELECTION = RUN / "full_book_batch_018_selection.json"
policy.NEXT_BATCH = RUN / "full_book_batch_019"
policy.NEXT_SELECTION = RUN / "full_book_batch_019_selection.json"
policy.COMPLETION = RUN / "full_book_batch_018_completion_report.json"
policy.PROGRESS = RUN / "full_book_progress_v20.json"
policy.BATCH_LABEL = "FULL_BOOK_BATCH_018"
policy.NEXT_BATCH_LABEL = "FULL_BOOK_BATCH_019"

if __name__ == "__main__":
    policy.main()
