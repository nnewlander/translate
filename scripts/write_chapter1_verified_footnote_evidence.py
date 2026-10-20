"""Write the exact, closed-world Chapter 1 D050 footnote evidence artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_1_verified_footnote_evidence_v1.json"

# This is an audited, finite matrix, not a marker detector.  Page associations
# were corroborated by direct review of the original PDF pages 31–47.
PAIRS = [
    ("1", "body.p.00803", "body.p.00806", 31),
    ("2", "body.p.00811", "body.p.00815", 32),
    ("3", "body.p.00811", "body.p.00816", 32),
    ("4", "body.p.00812", "body.p.00817", 32),
    ("5", "body.p.00821", "body.p.00832", 33),
    ("6", "body.p.00837", "body.p.00844", 34),
    ("7", "body.p.00850", "body.p.00857", 35),
    ("8", "body.p.00867", "body.p.00871", 36),
    ("9", "body.p.00868", "body.p.00872", 36),
    ("10", "body.p.00886", "body.p.00892", 37),
    ("11", "body.p.00911", "body.p.00915", 39),
    ("12", "body.p.00919", "body.p.00927", 40),
    ("13", "body.p.00944", "body.p.00956", 42),
    ("14", "body.p.00961", "body.p.00967", 43),
    ("15", "body.p.00979", "body.p.00989", 44),
    ("16", "body.p.00982", "body.p.00990", 44),
    ("17", "body.p.01029", "body.p.01035", 47),
]


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"immutable evidence already exists: {OUT}")
    source_sha = fingerprint(SOURCE).sha256
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    records = []
    for number, marker_id, body_id, page in PAIRS:
        marker = views[marker_id]
        body = views[body_id]
        marker_segment = next((segment for segment in marker.segments if segment.text == number), None)
        if marker_segment is None:
            raise RuntimeError(f"MARKER_SEGMENT_NOT_EXACT: {number} {marker_id}")
        if not body.source_text.startswith(number + " "):
            raise RuntimeError(f"FOOTNOTE_BODY_NOT_EXACT: {number} {body_id}")
        records.append({
            "verification_status": "VERIFIED",
            "unit_uid": marker.unit_uid,
            "logical_work_order_id": marker_id,
            "exact_source_marker_token": number,
            "footnote_number": number,
            "source_segment_binding": [{
                "anchor_ref": marker_segment.anchor_ref,
                "text": marker_segment.text,
                "source_run_locator": marker_segment.template_locator.xml_path,
                "paragraph_ordinal": marker_segment.template_locator.paragraph_ordinal,
            }],
            "corresponding_footnote_body": {
                "display_id": body_id,
                "unit_uid": body.unit_uid,
                "source_run_locator": body.base_template_locator.xml_path,
                "source_text": body.source_text,
            },
            "original_pdf_page": page,
            "pdf_corroboration": "Marker and matching numbered footnote body were directly reviewed on this original English PDF page.",
        })
    artifact = {
        "artifact_type": "CHAPTER_1_VERIFIED_FOOTNOTE_EVIDENCE",
        "schema": "D050_EXACT_CLOSED_WORLD",
        "source_docx": str(SOURCE),
        "source_sha256": source_sha,
        "original_pdf": str(ROOT / "Software Engineering at Google.pdf"),
        "chapter_boundary_authority": str(ROOT / "runs" / "chapter-pilot-20260914T040000Z-decb09" / "chapter_pilot_manifest.json"),
        "candidate_count": 18,
        "verified_count": len(records),
        "ambiguous_count": 0,
        "unverified_candidates": [{
            "display_id": "body.p.01082",
            "source_text": "... Chapters 5 and 6.",
            "classification": "UNVERIFIED_NON_FOOTNOTE_NUMERIC_REFERENCE",
            "reason": "Direct chapter references, not a footnote marker; it is explicitly excluded from D050 evidence."
        }],
        "records": records,
        "scope_guard": "Only the exact records listed here may be supplied as D050 footnote evidence. No style, punctuation, position, raised-text, or generic numeric inference is authorized.",
    }
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")
    print(OUT)
    print(json.dumps({"candidate_count": 18, "verified_count": len(records), "ambiguous_count": 0}))


if __name__ == "__main__":
    main()
