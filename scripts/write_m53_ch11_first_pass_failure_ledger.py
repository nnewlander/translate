"""Freeze Chapter 11 first-pass terminal failures without altering RAW evidence."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs/m5.3-20260914T140000Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
OUT = RUN / "chapter_11/chapter_11_first_pass_failure_ledger.json"


def main() -> None:
    source_before = sha256(SOURCE.read_bytes()).hexdigest()
    records = []
    for report in sorted((RUN / "chapter_11").glob("batch_*/**/translation_final_qa_report.json")):
        payload = json.loads(report.read_text(encoding="utf-8"))
        for item in payload.get("records", []):
            if item.get("passed") is False:
                records.append({"unit_uid": item["unit_uid"], "display_id": item["display_id"], "terminal_state": "DETERMINISTIC_QA_FAIL", "diagnostics": item["diagnostics"], "final_qa_report": str(report.relative_to(RUN))})
    by_id = {record["display_id"]: record for record in records}
    for display_id, family, note in [
        ("body.p.03718", "VERIFIED_FOOTNOTE_NUMERIC_TOKENIZATION_COLLISION", "Verified footnote 4 follows Figure 11-2; flattening produces 11-2.4, which the current numeric tokenizer treats as an ordinary numeric token."),
        ("body.p.03803", "NUMERIC_REPRESENTATION_FALSE_POSITIVE", "English cardinal phrase ‘two billion’ was expressed as Arabic 20 亿 in Chinese target; current contract sees unexpected 20."),
        ("body.p.03804", "NUMERIC_REPRESENTATION_FALSE_POSITIVE", "Quantity phrased with ‘25 million’ was expressed as 2500 万; current contract sees unexpected 2500 rather than a semantic numeric equivalence."),
        ("body.p.03866", "NUMERIC_REPRESENTATION_FALSE_POSITIVE", "English month word ‘April’ was expressed as Arabic 4 月 beside source year 2006; current contract sees unexpected 4."),
    ]:
        if display_id not in by_id: raise RuntimeError(f"EXPECTED_FIRST_PASS_FAILURE_MISSING: {display_id}")
        by_id[display_id]["failure_family"] = family
        by_id[display_id]["closure_note"] = note
    payload = {"stage": "CHAPTER_11_FIRST_PASS_DETERMINISTIC_FAILURE_LEDGER", "source_sha256": source_before, "chapter": "chapter_11", "first_pass_metrics_frozen": {"FIRST_PASS_TOTAL": 207, "FIRST_PASS_FINAL_APPROVED": 203, "FIRST_PASS_DETERMINISTIC_FAIL": 4, "FIRST_PASS_HUMAN_REQUIRED": 0}, "records": records, "policy": {"historical_raw_immutable": True, "retry_or_repair_now": False, "numeric_validator_change_now": False, "future_action": "Chapter 11 Deterministic Failure Closure in a separate phase."}}
    with OUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2); stream.write("\n")
    source_after = sha256(SOURCE.read_bytes()).hexdigest()
    if source_before != source_after: raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__": main()
