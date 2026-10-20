"""Low-frequency JSON entry point for the authoritative recovery registry."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy


def main() -> None:
    evidence = json.load(sys.stdin)
    result = decide_recovery_policy(evidence)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
