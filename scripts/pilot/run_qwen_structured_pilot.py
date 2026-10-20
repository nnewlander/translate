"""Run an opt-in, isolated comparison of Qwen structured-provider pilot arms.

The plan must have been built from sealed passing checkpoints.  This script
never writes under a production run.  It is deliberately not a production
worker and cannot select/advance a checkpoint.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.pilot.qwen_structured_runtime import PilotGlobalError, main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PilotGlobalError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2) from exc
