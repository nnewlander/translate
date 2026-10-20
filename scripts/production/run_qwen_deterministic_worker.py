"""Supervisor-facing deterministic Qwen worker.  Codex is not invoked."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.production_worker.worker import main


if __name__ == "__main__":
    raise SystemExit(main())
