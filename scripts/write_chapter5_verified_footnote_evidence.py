"""Write the bounded, exact Chapter 5 D050 evidence matrix before its first marker."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_5_verified_footnote_evidence_v1.json"

# Closed audit matrix. Each pair was corroborated against the original English
# PDF page; this deliberately authorizes no detection or general inference.
PAIRS = [
    ("1", "body.p.01994", "body.p.01998", 112, "1"),
    ("2", "body.p.02002", "body.p.02009", 113, "2"),
    ("3", "body.p.02018", "body.p.02024", 114, "3"),
    ("4", "body.p.02029", "body.p.02048", 115, "4"),
    ("5", "textbox.037.p.0003", "body.p.02063", 116, "5"),
    ("6", "body.p.02146", "footer.84.p.0001", 123, "answer,6"),
    ("7", "body.p.02153", "body.p.02159", 124, "7"),
    ("8", "body.p.02205", "body.p.02213", 128, "8"),
]


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (
        TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}
    records: list[dict[str, object]] = []
    for number, marker_id, body_id, pdf_page, marker_token in PAIRS:
        marker, body = views[marker_id], views[body_id]
        segment = next((item for item in marker.segments if marker_token in item.text), None)
        if segment is None:
            raise RuntimeError(f"MARKER_SEGMENT_NOT_EXACT:{number}:{marker_id}")
        if not body.source_text.startswith(number + " "):
            raise RuntimeError(f"FOOTNOTE_BODY_NOT_EXACT:{number}:{body_id}")
        records.append({
            "verification_status": "VERIFIED", "unit_uid": marker.unit_uid,
            "logical_work_order_id": marker_id, "exact_source_marker_token": marker_token,
            "footnote_number": number, "pre_footnote_numeric_tokens": [],
            "source_segment_binding": [{
                "anchor_ref": segment.anchor_ref, "text": segment.text,
                "source_run_locator": segment.template_locator.xml_path,
                "paragraph_ordinal": segment.template_locator.paragraph_ordinal,
            }],
            "corresponding_footnote_body": {
                "display_id": body_id, "unit_uid": body.unit_uid,
                "source_run_locator": body.base_template_locator.xml_path, "source_text": body.source_text,
            },
            "original_pdf_page": pdf_page,
            "pdf_corroboration": "Original English PDF page was read and matched to the numbered footnote body and reader-visible marker.",
        })
    artifact = {
        "artifact_type": "CHAPTER_5_VERIFIED_FOOTNOTE_EVIDENCE", "schema": "D050_EXACT_CLOSED_WORLD",
        "source_docx": str(SOURCE), "source_sha256": source_sha,
        "original_pdf": str(ROOT / "Software Engineering at Google.pdf"),
        "candidate_count": 8, "verified_count": len(records), "ambiguous_count": 0,
        "unverified_candidates": [
            {"display_id": "body.p.01950", "source_text": "81", "classification": "EXCLUDE_RUNNING_DECORATION", "reason": "Reader page number, not a footnote marker."},
            {"display_id": "body.p.01965", "source_text": "82 | Chapter 5: How to Lead a Team", "classification": "EXCLUDE_RUNNING_DECORATION", "reason": "Reader page heading, not a footnote marker."},
            {"display_id": "body.p.01985", "source_text": "Moving from an Individual Contributor Role to a Leadership Role | 83", "classification": "EXCLUDE_RUNNING_DECORATION", "reason": "Reader running heading, not a footnote marker."},
            {"display_id": "body.p.02033", "source_text": "9:00 to 5:00", "classification": "EXCLUDE_ORDINARY_NUMERIC_CONTENT", "reason": "Time of day, not a footnote marker."},
            {"display_id": "body.p.02145", "source_text": "24/7", "classification": "EXCLUDE_ORDINARY_NUMERIC_CONTENT", "reason": "Ordinary numeric expression, not a footnote marker."},
        ],
        "records": records,
        "scope_guard": "Only listed exact records may be exported as D050 evidence. No inference from style, punctuation, position, raised-text, or arbitrary numbers is authorized.",
        "preflight_scope": "Bounded Chapter 5 preflight triggered by the first Batch 006 reader-visible footnote marker; it is not a permanent Chapter 5 NOT_REQUIRED decision.",
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
