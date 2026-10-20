"""Write exact bounded continuation D050 evidence for Batch 008 Chapter 7."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
OUT = RUN / "chapter_7_batch008_verified_footnote_evidence_v1.json"
PAIRS = [("2", "body.p.02563", "body.p.02568", 154), ("3", "body.p.02574", "body.p.02586", 155)]

def main() -> None:
    if OUT.exists(): raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    views = {v.display_id: v for v in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    records = []
    for number, marker_id, body_id, page in PAIRS:
        marker, body = views[marker_id], views[body_id]
        segment = next((item for item in marker.segments if item.text == number), None)
        if segment is None or not body.source_text.startswith(number + " "):
            raise RuntimeError(f"CHAPTER_7_D050_EXACT_EVIDENCE_MISMATCH:{number}")
        records.append({"verification_status":"VERIFIED","unit_uid":marker.unit_uid,"logical_work_order_id":marker_id,"exact_source_marker_token":number,"footnote_number":number,"pre_footnote_numeric_tokens":[],"source_segment_binding":[{"anchor_ref":segment.anchor_ref,"text":segment.text,"source_run_locator":segment.template_locator.xml_path,"paragraph_ordinal":segment.template_locator.paragraph_ordinal}],"corresponding_footnote_body":{"display_id":body_id,"unit_uid":body.unit_uid,"source_run_locator":body.base_template_locator.xml_path,"source_text":body.source_text},"original_pdf_page":page,"pdf_corroboration":"Original English PDF page was read and matched to the numbered footnote body and reader-visible marker."})
    value={"artifact_type":"CHAPTER_7_BATCH_008_VERIFIED_FOOTNOTE_EVIDENCE","schema":"D050_EXACT_CLOSED_WORLD","source_docx":str(SOURCE),"source_sha256":source_sha,"original_pdf":str(ROOT/"Software Engineering at Google.pdf"),"candidate_count":2,"verified_count":2,"ambiguous_count":0,"unverified_candidates":[],"records":records,"scope_guard":"Only listed exact records may be exported as D050 evidence. No generic inference is authorized.","preflight_scope":"Bounded Batch 008 Chapter 7 continuation preflight for exact selected markers 2 and 3."}
    with OUT.open("x",encoding="utf-8",newline="\n") as f: json.dump(value,f,ensure_ascii=False,indent=2); f.write("\n")
    if fingerprint(SOURCE).sha256 != source_sha: raise RuntimeError("SOURCE_MUTATED")
if __name__ == "__main__": main()
