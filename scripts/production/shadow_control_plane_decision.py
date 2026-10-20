"""JSON CLI for pure control-plane decision adapters; used only in shadow mode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from booktrans.production_control.cursor_decision import decide_cursor
from booktrans.production_control.handoff_decision import decide_handoff
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("cursor", "handoff", "policy"), required=True)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    if args.adapter == "cursor":
        result = decide_cursor(before=payload["before"], after=payload["after"], evidence=payload["evidence"])
    elif args.adapter == "handoff":
        result = decide_handoff(current_worker_id=payload["current_worker_id"], handoff=payload.get("handoff"), post_cursor=payload["post_cursor"], facts=payload["facts"])
    else:
        result = decide_recovery_policy(payload)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
