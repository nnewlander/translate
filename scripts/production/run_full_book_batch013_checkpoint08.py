"""Seal frozen Batch 013 checkpoint 08 through the established Harness chain."""

from __future__ import annotations

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch013_checkpoint07 as runner


runner.CHECKPOINT_NAME = "checkpoint_08"
runner.CHECKPOINT = runner.base.BATCH / runner.CHECKPOINT_NAME
runner.TARGET_MAP = runner.base.RUN / "full_book_batch_013_checkpoint_08_authored_targets.json"
runner.SELECTION_START = 175
runner.EXPECTED_FIRST_WORK_ORDER = "body.p.04262"
runner.EXPECTED_COMPLETED_COUNT = 175
runner.EXPECTED_CHECKPOINT_COUNT = 7
runner.SUCCESS_MESSAGE = "PASS: FULL_BOOK_BATCH_013/checkpoint_08 sealed; completed=200/200"


if __name__ == "__main__":
    runner.main()
