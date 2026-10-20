"""Derive telemetry only from append-only operational events; never writes state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from booktrans.production_control.event_telemetry import derive_event_metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    args = parser.parse_args()
    rows = []
    if args.events.exists():
        for line in args.events.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try: rows.append(json.loads(line))
                except json.JSONDecodeError: pass  # interrupted append: operational telemetry only
    print(json.dumps(derive_event_metrics(rows), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
