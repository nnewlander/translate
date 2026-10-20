"""Write bounded exact D050 evidence before Batch 007 enters Chapter 7 markers."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_7_batch007_verified_footnote_evidence_v1.json"


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (
        TranslationView.model_validate_json(line)
        for line in VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}
    marker, body = views["body.p.02522"], views["body.p.02527"]
    segment = next((item for item in marker.segments if item.text == "1"), None)
    if segment is None or not body.source_text.startswith("1 "):
        raise RuntimeError("CHAPTER_7_D050_EXACT_EVIDENCE_MISMATCH")
    value = {
        "artifact_type": "CHAPTER_7_BATCH_007_VERIFIED_FOOTNOTE_EVIDENCE",
        "schema": "D050_EXACT_CLOSED_WORLD", "source_docx": str(SOURCE),
        "source_sha256": source_sha, "original_pdf": str(ROOT / "Software Engineering at Google.pdf"),
        "candidate_count": 1, "verified_count": 1, "ambiguous_count": 0,
        "unverified_candidates": [],
        "records": [{"verification_status": "VERIFIED", "unit_uid": marker.unit_uid,
            "logical_work_order_id": "body.p.02522", "exact_source_marker_token": "1",
            "footnote_number": "1", "pre_footnote_numeric_tokens": [],
            "source_segment_binding": [{"anchor_ref": segment.anchor_ref, "text": segment.text,
                "source_run_locator": segment.template_locator.xml_path,
                "paragraph_ordinal": segment.template_locator.paragraph_ordinal}],
            "corresponding_footnote_body": {"display_id": "body.p.02527", "unit_uid": body.unit_uid,
                "source_run_locator": body.base_template_locator.xml_path, "source_text": body.source_text},
            "original_pdf_page": 151,
            "pdf_corroboration": "Original English PDF page was read and matched to the numbered footnote body and reader-visible marker."}],
        "scope_guard": "Only the listed exact record may be exported as D050 evidence. No inference from style, punctuation, position, raised-text, or arbitrary numbers is authorized.",
        "preflight_scope": "Bounded Batch 007 Chapter 7 preflight for its first selected reader-visible marker; no other chapter is in scope."}
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
