"""Bounded D050 decision for the three Chapter 5 entry units in frozen Batch 005."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.harness.source_integrity import fingerprint

ROOT=Path(r"D:\translate"); RUN=ROOT/"runs/full-book-20260916T000200Z-decb09"; OUT=RUN/"full_book_batch_005"/"chapter_5_entry_d050_preflight.json"
SOURCE=ROOT/"Software Engineering at Google.docx"; SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
ENTRY=["body.p.01931","body.p.01938","body.p.01939"]
def main() -> None:
    if fingerprint(SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")
    selection=json.loads((RUN/"full_book_batch_005_selection.json").read_text(encoding="utf8"))
    if selection["selected_logical_work_order_ids"][-3:] != ENTRY: raise RuntimeError("FROZEN_CHAPTER_5_ENTRY_MISMATCH")
    views={json.loads(line)["display_id"]:json.loads(line) for line in (ROOT/"runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl").read_text(encoding="utf8").splitlines() if line}
    observed=[{"display_id":item,"source_text":views[item]["source_text"]} for item in ENTRY]
    if any(any(char.isdigit() for char in item["source_text"]) for item in observed): raise RuntimeError("CHAPTER_5_ENTRY_REQUIRES_D050_EVIDENCE")
    with OUT.open("x",encoding="utf8",newline="\n") as f: json.dump({"artifact_type":"BOUNDED_CHAPTER_5_ENTRY_D050_PREFLIGHT","decision":"NOT_REQUIRED_FOR_CURRENT_ENTRY_UNITS","scope":"Only the three frozen Batch 005 units that first enter Chapter 5; this is not a Chapter 5 audit.","entry_units":observed,"reason":"No reader-visible footnote marker candidate occurs in these entry units. A later selected footnote-marker unit, if any, requires a separate exact D050 preflight before translation.","source_sha256":SHA},f,ensure_ascii=False,indent=2);f.write("\n")
    if fingerprint(SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__": main()
