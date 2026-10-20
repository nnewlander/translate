"""Advance sealed Batch 014 through the established immutable batch policy."""

from __future__ import annotations

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import advance_full_book_batch013 as policy


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")

policy.PRIOR = RUN / "full_book_progress_v15.json"
policy.BATCH = RUN / "full_book_batch_014"
policy.SELECTION = RUN / "full_book_batch_014_selection.json"
policy.NEXT_BATCH = RUN / "full_book_batch_015"
policy.NEXT_SELECTION = RUN / "full_book_batch_015_selection.json"
policy.COMPLETION = RUN / "full_book_batch_014_completion_report.json"
policy.PROGRESS = RUN / "full_book_progress_v16.json"
policy.BATCH_LABEL = "FULL_BOOK_BATCH_014"
policy.NEXT_BATCH_LABEL = "FULL_BOOK_BATCH_015"

_write_once = policy.write_once


def write_once(path: Path, value: object) -> None:
    if not isinstance(value, dict):
        raise RuntimeError("ADVANCEMENT_PAYLOAD_INVALID")
    if path == policy.COMPLETION:
        if value.get("batch") != policy.BATCH_LABEL:
            raise RuntimeError("ADVANCEMENT_COMPLETION_TEMPLATE_DRIFT")
    elif path == policy.PROGRESS:
        if value.get("latest_completed_batch") != policy.BATCH_LABEL:
            raise RuntimeError("ADVANCEMENT_PROGRESS_TEMPLATE_DRIFT")
    elif path == policy.NEXT_SELECTION:
        if value.get("batch") != policy.NEXT_BATCH_LABEL:
            raise RuntimeError("ADVANCEMENT_SELECTION_TEMPLATE_DRIFT")
    _write_once(path, value)


policy.write_once = write_once


if __name__ == "__main__":
    policy.main()
