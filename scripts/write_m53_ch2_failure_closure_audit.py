"""Write the read-only Chapter 2 first-pass deterministic-failure audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
CHAPTER = RUN / "chapter_2"
LEDGER = RUN / "chapter_2_deterministic_fail_ledger.json"
REPORT = RUN / "chapter_2_failure_closure_report.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evidence(batch: str, display_id: str) -> dict:
    directory = CHAPTER / batch
    request_path = directory / "translation_request.jsonl"
    raw_path = directory / "translation_raw_response.jsonl"
    request = next(row for row in jsonl(request_path) if row["display_id"] == display_id)
    raw = next(row for row in jsonl(raw_path) if row["unit_uid"] == request["unit_uid"])
    return {
        "batch": batch,
        "request_path": str(request_path),
        "request_sha256": sha256(request_path),
        "raw_path": str(raw_path),
        "raw_sha256": sha256(raw_path),
        "unit_uid": request["unit_uid"],
        "source_text": request["source_text"],
        "raw_target_segments": raw["target_segments"],
        "footnote_reference_numbers": request.get("footnote_reference_numbers", []),
        "available_anchor_ids": [anchor["anchor_id"] for anchor in request.get("available_anchors", [])],
    }


def main() -> None:
    if LEDGER.exists() or REPORT.exists():
        raise SystemExit("Audit outputs already exist; refusing to overwrite evidence artifacts.")

    failures = {
        "body.p.01136": evidence("batch_001", "body.p.01136"),
        "body.p.01176": evidence("batch_004", "body.p.01176"),
        "textbox.015.p.0002": evidence("batch_004", "textbox.015.p.0002"),
        "body.p.01234": evidence("batch_005", "body.p.01234"),
        "body.p.01276": evidence("batch_006", "body.p.01276"),
    }
    failures["body.p.01136"].update({
        "first_pass_failure": "MISSING_ANCHOR",
        "root_cause_classification": "ISOLATED_TRANSLATOR_CONTRACT_MISS",
        "translator_fault": True,
        "validator_fault": False,
        "conversion_evidence_gap": False,
        "source_evidence": {
            "required_anchor_count": 3,
            "raw_anchor_refs": [segment.get("anchor_ref") for segment in failures["body.p.01136"]["raw_target_segments"] if segment.get("anchor_ref")],
            "finding": "The request declares three anchors; RAW omits the first anchor for source text 'The answer is '.",
        },
        "proposed_minimal_resolution": "A separately persisted SECOND_PASS_RECOVERY using the same request contract; old RAW remains immutable.",
        "recovery_eligible": True,
        "first_pass_metric_remains_fail": True,
    })
    pdf_common = {
        "authority": "Read-only original-PDF corroboration plus exact DOCX source fragment and OOXML raised-run evidence.",
        "shared_pattern": "The WPS-converted DOCX stores a numeric footnote marker as a direct raised-text run (w:position=6), while exporter footnote_reference_numbers is empty.",
        "constraint": "Raised formatting alone is not a globally safe footnote detector.",
    }
    failures["body.p.01176"].update({
        "first_pass_failure": "NUMBER_PRESERVATION",
        "root_cause_classification": "FOOTNOTE_EVIDENCE_GAP",
        "translator_fault": False,
        "validator_fault": False,
        "conversion_evidence_gap": True,
        "source_evidence": {**pdf_common, "docx_tokens": ["wheels.2", "risk.3"], "ooxml": ["text[79]: 2, w:position=6", "text[227]: 3, w:position=6"], "matching_docx_footnotes": ["body.p.01184: 2 Literally, if you are, in fact, a bike designer.", "body.p.01185: 3 I should note that sometimes it’s dangerous to get too much feedback too early in the process if you’re still unsure of your general direction or goal."], "pdf_page": 59, "printed_page": 31, "pdf_footnotes": ["2 Literally, if you are, in fact, a bike designer.", "3 I should note that sometimes it’s dangerous to get too much feedback too early in the process if you’re still unsure of your general direction or goal."]},
        "proposed_minimal_resolution": "Future run-scoped explicit evidence mapping for these exact verified markers, propagated into the request validation envelope and consumed by numeric QA.",
        "recovery_eligible": True,
        "first_pass_metric_remains_fail": True,
    })
    failures["textbox.015.p.0002"].update({
        "first_pass_failure": "NUMBER_PRESERVATION",
        "root_cause_classification": "NUMERIC_REPRESENTATION_FALSE_POSITIVE",
        "translator_fault": False,
        "validator_fault": False,
        "conversion_evidence_gap": False,
        "source_evidence": {"pdf_page": 60, "printed_page": 32, "pdf_text": "Twenty-five years ago, conventional wisdom stated that for an engineer to be productive, they needed to have their own office with a door that closed.", "finding": "The source number is an English cardinal word, so current digit-only numeric extraction sees no source number but sees target '25'."},
        "proposed_minimal_resolution": "Do not implement now. A future explicit, reviewed value-equivalence mapping may permit only an exact cardinal-word-to-Arabic rendering when unit/meaning match and neither side is protected, date, range, ordinal, identifier, or version.",
        "recovery_eligible": True,
        "first_pass_metric_remains_fail": True,
    })
    failures["body.p.01234"].update({
        "first_pass_failure": "NUMBER_PRESERVATION",
        "root_cause_classification": "FOOTNOTE_EVIDENCE_GAP",
        "translator_fault": False,
        "validator_fault": False,
        "conversion_evidence_gap": True,
        "source_evidence": {**pdf_common, "docx_token": "appropriate.5", "ooxml": "text[26]: 5, w:position=6", "matching_docx_footnote": "body.p.01243: 5 This is incredibly difficult if you’ve been burned in the past by delegating to incompetent people.", "pdf_page": 63, "printed_page": 35, "pdf_footnote": "5 This is incredibly difficult if you’ve been burned in the past by delegating to incompetent people."},
        "proposed_minimal_resolution": "Same run-scoped explicit-evidence proposal as the other verified footnote markers; do not infer from punctuation or raised formatting alone.",
        "recovery_eligible": True,
        "first_pass_metric_remains_fail": True,
    })
    failures["body.p.01276"].update({
        "first_pass_failure": "NUMBER_PRESERVATION",
        "root_cause_classification": "FOOTNOTE_EVIDENCE_GAP",
        "translator_fault": False,
        "validator_fault": False,
        "conversion_evidence_gap": True,
        "source_evidence": {**pdf_common, "docx_token": "next go-around.7", "ooxml": "text[96]: 7, w:position=6", "matching_docx_footnote": "body.p.01281: 7 By the same token, if you do the same thing over and over and keep failing, it’s not failure, it’s incompetence.", "pdf_page": 66, "printed_page": 38, "pdf_footnote": "7 By the same token, if you do the same thing over and over and keep failing, it’s not failure, it’s incompetence."},
        "proposed_minimal_resolution": "Same run-scoped explicit-evidence proposal as the other verified footnote markers; do not infer from punctuation or raised formatting alone.",
        "recovery_eligible": True,
        "first_pass_metric_remains_fail": True,
    })
    ledger = {
        "schema_version": "m5.3-ch2-failure-closure-v1",
        "scope": "Read-only audit; no immutable RAW, source DOCX, validator, normalizer, or candidate was modified.",
        "frozen_first_pass_metrics": {"FIRST_PASS_TOTAL": 176, "FIRST_PASS_FINAL_APPROVED": 171, "FIRST_PASS_DETERMINISTIC_FAIL": 5, "policy": "All later successes are RECOVERED_AFTER_FIRST_PASS and never rewrite this baseline."},
        "source_docx": str(ROOT / "Software Engineering at Google.docx"),
        "source_docx_sha256": "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba",
        "source_pdf": str(ROOT / "Software Engineering at Google.pdf"),
        "source_pdf_sha256": "4e567ce8f561cad28c0369df71a25cb2b64361473c64509c493cce79e293289e",
        "failures": failures,
        "systemic_findings": {"verified_footnote_cases_share_pattern": True, "future_contract_direction": "Exporter must preserve only explicitly proven marker evidence; QA must consume that evidence. No generic footnote engine is approved.", "approved_normalizer_applicability": "FOOTNOTE_SUPERSCRIPT_TO_ASCII does not apply to these ASCII RAW markers and cannot invent absent evidence."},
    }
    LEDGER.open("x", encoding="utf-8").write(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# Chapter 2 deterministic failure closure audit",
        "",
        "## Frozen first-pass baseline",
        "",
        "- FIRST_PASS_TOTAL: 176",
        "- FIRST_PASS_FINAL_APPROVED: 171",
        "- FIRST_PASS_DETERMINISTIC_FAIL: 5",
        "- Later recovery is `RECOVERED_AFTER_FIRST_PASS`; it never rewrites first-pass metrics.",
        "",
        "## Findings",
        "",
        "| Display ID | Classification | Fault / gap | Recovery proposal |",
        "|---|---|---|---|",
        "| body.p.01136 | ISOLATED_TRANSLATOR_CONTRACT_MISS | Translator omitted one of three declared anchors. | Separate immutable second-pass response under the same contract. |",
        "| body.p.01176 | FOOTNOTE_EVIDENCE_GAP | Verified PDF footnotes 2/3; DOCX uses raised runs but exporter evidence is empty. | Explicit run-scoped marker evidence, then QA consumption. |",
        "| textbox.015.p.0002 | NUMERIC_REPRESENTATION_FALSE_POSITIVE | `Twenty-five years ago` and `25 年前` are semantically equivalent; digit-only source extraction rejects target 25. | Do not implement now; consider a future reviewed, narrow equivalence mapping. |",
        "| body.p.01234 | FOOTNOTE_EVIDENCE_GAP | Verified PDF footnote 5; DOCX raised run and empty exporter evidence. | Explicit run-scoped marker evidence, then QA consumption. |",
        "| body.p.01276 | FOOTNOTE_EVIDENCE_GAP | Verified PDF footnote 7; DOCX raised run and empty exporter evidence. | Explicit run-scoped marker evidence, then QA consumption. |",
        "",
        "## Footnote evidence",
        "",
        "Original PDF pages 59/63/66 (printed pages 31/35/38) contain matching footnotes 2+3/5/7. Corresponding DOCX runs use `w:position=6`, and matching converted footnote paragraphs are present in body.p.01184/.01185, body.p.01243, and body.p.01281. This corroborates a common WPS conversion/exporter evidence-loss pattern. `w:position=6` alone is not a globally safe detector.",
        "",
        "## Closure decision",
        "",
        "No production rule changes are made by this audit. The closure plan is ready: preserve all five immutable RAW artifacts; perform any recovery as separately immutable later evidence; keep the normalizer closed-world; and require explicit marker evidence before accepting footnote-number representation. No generic footnote engine, number-word parser, retry framework, or architecture expansion is justified.",
    ]
    REPORT.open("x", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"wrote {LEDGER}")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
