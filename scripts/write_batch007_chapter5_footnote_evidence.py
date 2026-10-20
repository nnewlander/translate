"""Write bounded exact D050 evidence for the remaining selected Chapter 5 markers."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_5_batch007_verified_footnote_evidence_v1.json"
PAIRS = [
    ("9", "body.p.02251", "body.p.02260", 131, "9"),
    ("10", "body.p.02266", "body.p.02275", 132, "10"),
    ("11", "body.p.02267", "body.p.02276", 132, "11"),
    ("12", "body.p.02268", "body.p.02277", 132, "12"),
    ("13", "body.p.02269", "body.p.02278", 132, "13"),
    ("14", "body.p.02282", "body.p.02296", 133, "14"),
]


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (
        TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}
    records = []
    for number, marker_id, body_id, pdf_page, marker_token in PAIRS:
        marker, body = views[marker_id], views[body_id]
        segment = next((item for item in marker.segments if marker_token in item.text), None)
        if segment is None or not body.source_text.startswith(number + " "):
            raise RuntimeError(f"CHAPTER_5_D050_EXACT_EVIDENCE_MISMATCH:{number}:{marker_id}")
        records.append({
            "verification_status": "VERIFIED", "unit_uid": marker.unit_uid, "logical_work_order_id": marker_id,
            "exact_source_marker_token": marker_token, "footnote_number": number, "pre_footnote_numeric_tokens": [],
            "source_segment_binding": [{"anchor_ref": segment.anchor_ref, "text": segment.text, "source_run_locator": segment.template_locator.xml_path, "paragraph_ordinal": segment.template_locator.paragraph_ordinal}],
            "corresponding_footnote_body": {"display_id": body_id, "unit_uid": body.unit_uid, "source_run_locator": body.base_template_locator.xml_path, "source_text": body.source_text},
            "original_pdf_page": pdf_page, "pdf_corroboration": "Original English PDF page was read and matched to the numbered footnote body and reader-visible marker.",
        })
    value = {
        "artifact_type": "CHAPTER_5_BATCH_007_VERIFIED_FOOTNOTE_EVIDENCE", "schema": "D050_EXACT_CLOSED_WORLD",
        "source_docx": str(SOURCE), "source_sha256": source_sha, "original_pdf": str(ROOT / "Software Engineering at Google.pdf"),
        "candidate_count": len(PAIRS), "verified_count": len(records), "ambiguous_count": 0, "unverified_candidates": [],
        "records": records, "scope_guard": "Only listed exact records may be exported as D050 evidence. No inference from style, punctuation, position, raised-text, or arbitrary numbers is authorized.",
        "preflight_scope": "Bounded Batch 007 Chapter 5 continuation preflight for its six selected reader-visible marker units; existing Chapter 5 evidence remains immutable.",
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
