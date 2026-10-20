"""Write the bounded, manually audited Chapter 4 D050 evidence matrix."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_4_verified_footnote_evidence_v1.json"

# Closed audit matrix. Original-PDF pages 98–102 were read to associate each
# reader-visible marker with its numbered footnote body; this is not a detector.
PAIRS = [
    ("1", "body.p.01810", "body.p.01814", 98),
    ("2", "textbox.027.p.0002", "body.p.01815", 98),
    ("3", "textbox.028.p.0001", "body.p.01831", 99),
    ("4", "textbox.028.p.0003", "body.p.01832", 99),
    ("5", "body.p.01861", "body.p.01868", 102),
]


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    records = []
    for number, marker_id, body_id, pdf_page in PAIRS:
        marker, body = views[marker_id], views[body_id]
        segment = next((item for item in marker.segments if item.text == number), None)
        if segment is None:
            raise RuntimeError(f"MARKER_SEGMENT_NOT_EXACT:{number}:{marker_id}")
        if not body.source_text.startswith(number + " "):
            raise RuntimeError(f"FOOTNOTE_BODY_NOT_EXACT:{number}:{body_id}")
        records.append({"verification_status": "VERIFIED", "unit_uid": marker.unit_uid, "logical_work_order_id": marker_id, "exact_source_marker_token": number, "footnote_number": number, "pre_footnote_numeric_tokens": [], "source_segment_binding": [{"anchor_ref": segment.anchor_ref, "text": segment.text, "source_run_locator": segment.template_locator.xml_path, "paragraph_ordinal": segment.template_locator.paragraph_ordinal}], "corresponding_footnote_body": {"display_id": body_id, "unit_uid": body.unit_uid, "source_run_locator": body.base_template_locator.xml_path, "source_text": body.source_text}, "original_pdf_page": pdf_page, "pdf_corroboration": "Original English PDF page was read and matched to the numbered footnote body and reader-visible marker."})
    artifact = {"artifact_type": "CHAPTER_4_VERIFIED_FOOTNOTE_EVIDENCE", "schema": "D050_EXACT_CLOSED_WORLD", "source_docx": str(SOURCE), "source_sha256": source_sha, "original_pdf": str(ROOT / "Software Engineering at Google.pdf"), "candidate_count": 7, "verified_count": len(records), "ambiguous_count": 0, "unverified_candidates": [{"display_id": "body.p.01803", "source_text": "69", "classification": "EXCLUDE_RUNNING_DECORATION", "reason": "Reader page number, not a footnote marker."}, {"display_id": "body.p.01902", "source_text": "1.", "classification": "EXCLUDE_ORDERED_LIST_ITEM", "reason": "Numbered recommendation, not a footnote marker."}], "records": records, "scope_guard": "Only listed exact records may be exported as D050 evidence. No inference from style, punctuation, superscript position, or arbitrary numbers is authorized."}
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
