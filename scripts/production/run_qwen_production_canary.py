"""Isolated 25-unit Qwen production-shaped canary.

Reads the current FULL_BOOK_BATCH_022/checkpoint_03 frozen selection and writes
a new isolated run.  It never creates full_book_batch_022/checkpoint_03.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.production_worker.diagnostics import DEFAULT_WATCHDOG_SECONDS
from booktrans.production_worker.worker import DEFAULT_RUN, build_parser, run_canary_command


def main() -> int:
    parser = build_parser()
    parser.set_defaults(mode="canary")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.isolated_output or (ROOT / "runs" / f"qwen-production-canary-{stamp}")
    report = run_canary_command(
        root=args.root,
        run=args.run_path or DEFAULT_RUN,
        isolated_output=output,
        continue_from_raw=getattr(args, "continue_from_raw", False),
        resume=getattr(args, "resume", False),
        recalculate_from_evidence=getattr(args, "recalculate_from_evidence", False),
        semantic_review=getattr(args, "semantic_review", False),
        semantic_review_ids=[
            item.strip()
            for item in str(getattr(args, "semantic_review_ids", "") or "").split(",")
            if item.strip()
        ],
        corrected_second_review=getattr(args, "corrected_second_review", False),
        group_repair=getattr(args, "group_repair", False),
        human_adjudicate=getattr(args, "human_adjudicate", False),
        human_adjudication_path=getattr(args, "human_adjudication_path", None),
        watchdog_seconds=getattr(args, "watchdog_seconds", DEFAULT_WATCHDOG_SECONDS),
    )
    print(output)
    gate = report.get("gate") or {}
    print("CANARY_GATE", "PASS" if gate.get("passed") else "FAIL")
    return 0 if gate.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
