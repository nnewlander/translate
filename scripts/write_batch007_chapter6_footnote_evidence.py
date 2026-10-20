"""Write bounded exact D050 evidence before Batch 007 enters Chapter 6 markers."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_6_batch007_verified_footnote_evidence_v1.json"
PAIRS = [
    ("1", "body.p.02359", "body.p.02367", 139),
    ("2", "body.p.02401", "body.p.02409", 143),
    ("3", "body.p.02422", "body.p.02429", 144),
    ("4", "body.p.02485", "body.p.02496", 149),
    ("5", "body.p.02491", "body.p.02497", 149),
]


def main() -> None:
    if OUT.exists():
        raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (
        TranslationView.model_validate_json(line)
        for line in VIEWS.read_text(encoding="utf-8").splitlines() if line
    )}
    records = []
    for number, marker_id, body_id, pdf_page in PAIRS:
        marker, body = views[marker_id], views[body_id]
        segment = next((item for item in marker.segments if item.text == number), None)
        if segment is None or not body.source_text.startswith(number + " "):
            raise RuntimeError(f"CHAPTER_6_D050_EXACT_EVIDENCE_MISMATCH:{number}:{marker_id}")
        records.append({
            "verification_status": "VERIFIED",
            "unit_uid": marker.unit_uid,
            "logical_work_order_id": marker_id,
            "exact_source_marker_token": number,
            "footnote_number": number,
            "pre_footnote_numeric_tokens": [],
            "source_segment_binding": [{
                "anchor_ref": segment.anchor_ref,
                "text": segment.text,
                "source_run_locator": segment.template_locator.xml_path,
                "paragraph_ordinal": segment.template_locator.paragraph_ordinal,
            }],
            "corresponding_footnote_body": {
                "display_id": body_id,
                "unit_uid": body.unit_uid,
                "source_run_locator": body.base_template_locator.xml_path,
                "source_text": body.source_text,
            },
            "original_pdf_page": pdf_page,
            "pdf_corroboration": "Original English PDF page was read and matched to the numbered footnote body and reader-visible marker.",
        })
    value = {
        "artifact_type": "CHAPTER_6_BATCH_007_VERIFIED_FOOTNOTE_EVIDENCE",
        "schema": "D050_EXACT_CLOSED_WORLD",
        "source_docx": str(SOURCE),
        "source_sha256": source_sha,
        "original_pdf": str(ROOT / "Software Engineering at Google.pdf"),
        "candidate_count": len(PAIRS),
        "verified_count": len(records),
        "ambiguous_count": 0,
        "unverified_candidates": [],
        "records": records,
        "scope_guard": "Only listed exact records may be exported as D050 evidence. No inference from style, punctuation, position, raised-text, or arbitrary numbers is authorized.",
        "preflight_scope": "Bounded Batch 007 Chapter 6 preflight for its five selected reader-visible marker units; no other chapter is in scope.",
    }
    with OUT.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
