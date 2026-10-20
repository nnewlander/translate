"""Read-only CLI for :mod:`booktrans.production_control.current_execution_projection`."""
from __future__ import annotations

import json
import sys

from booktrans.production_control.current_execution_projection import (
    project_current_execution,
    project_handoff_consumption,
)


def main() -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or payload.get("kind") not in {"startup", "handoff"}:
        raise SystemExit("CURRENT_EXECUTION_PROJECTION_INPUT_INVALID")
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise SystemExit("CURRENT_EXECUTION_PROJECTION_FACTS_INVALID")
    result = project_current_execution(facts) if payload["kind"] == "startup" else project_handoff_consumption(facts)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
