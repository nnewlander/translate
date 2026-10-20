"""Materialize the frozen Chapter 18 final logical selector without touching RAW."""

from __future__ import annotations

import json
from pathlib import Path

from booktrans.harness.logical_work_order_selector import select_final_logical_work_orders


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
SOURCE_REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
SCOPE = RUN / "multi_chapter_scope_final.json"
OUT = RUN / "chapter_18_final_logical_work_order_selector.json"


def main() -> None:
    if OUT.exists():
        raise SystemExit("SELECTOR_ALREADY_EXISTS")
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    chapter = next(item for item in scope["chapters"] if item["chapter_id"] == "chapter_18")
    requests = [json.loads(line) for line in SOURCE_REQUESTS.read_text(encoding="utf-8").splitlines() if line]
    body_ids = [item["display_id"] for item in requests if "chapter_18" in item["selection_categories"]]
    groups = [item for item in scope["proven_composite_groups"] if item["chapter_id"] == "chapter_18"]
    selected = select_final_logical_work_orders(body_ids, chapter["included"], groups)
    payload = {
        "stage": "M5.3B_FINAL_SCOPE_LOGICAL_SELECTOR",
        "scope_artifact": str(SCOPE),
        "chapter": "chapter_18",
        "physical_scope_count": chapter["final_scope_count"],
        "logical_work_order_count": len(selected),
        "body_baseline_count": chapter["body_baseline_count"],
        "added_chapter_content_count": chapter["added_chapter_content_count"],
        "work_orders": [
            {"id": item.work_order_id, "members": list(item.member_display_ids), "composite_group_id": item.composite_group_id}
            for item in selected
        ],
    }
    if payload["physical_scope_count"] != 245 or payload["logical_work_order_count"] != 244:
        raise RuntimeError("FINAL_SCOPE_SELECTOR_COUNT_MISMATCH")
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
