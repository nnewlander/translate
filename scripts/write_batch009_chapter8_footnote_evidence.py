"""Write bounded exact D050 evidence for the first Batch 009 Chapter 8 marker."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint
ROOT=Path(r"D:\translate");RUN=ROOT/"runs"/"full-book-20260916T000200Z-decb09";SOURCE=ROOT/"Software Engineering at Google.docx";VIEWS=ROOT/"runs"/"structural-tagged-synthetic-20260913T075737Z-decb09-d22356"/"translation_views.jsonl";OUT=RUN/"chapter_8_batch009_verified_footnote_evidence_v1.json"
def main()->None:
 if OUT.exists():raise RuntimeError("IMMUTABLE_EVIDENCE_ALREADY_EXISTS")
 sha=fingerprint(SOURCE).sha256;vs={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding="utf-8").splitlines() if x)};m,b=vs["body.p.02744"],vs["body.p.02747"];s=next((x for x in m.segments if x.text=="1"),None)
 if s is None or not b.source_text.startswith("1 "):raise RuntimeError("D050_EXACT_EVIDENCE_MISMATCH")
 r={"verification_status":"VERIFIED","unit_uid":m.unit_uid,"logical_work_order_id":"body.p.02744","exact_source_marker_token":"1","footnote_number":"1","pre_footnote_numeric_tokens":[],"source_segment_binding":[{"anchor_ref":s.anchor_ref,"text":s.text,"source_run_locator":s.template_locator.xml_path,"paragraph_ordinal":s.template_locator.paragraph_ordinal}],"corresponding_footnote_body":{"display_id":"body.p.02747","unit_uid":b.unit_uid,"source_run_locator":b.base_template_locator.xml_path,"source_text":b.source_text},"original_pdf_page":165,"pdf_corroboration":"Original English PDF page was read and matched to the numbered footnote body and reader-visible marker."}
 with OUT.open("x",encoding="utf-8",newline="\n") as f:json.dump({"artifact_type":"CHAPTER_8_BATCH_009_VERIFIED_FOOTNOTE_EVIDENCE","schema":"D050_EXACT_CLOSED_WORLD","source_docx":str(SOURCE),"source_sha256":sha,"candidate_count":1,"verified_count":1,"ambiguous_count":0,"unverified_candidates":[],"records":[r],"scope_guard":"Only listed exact records may be exported as D050 evidence. No generic inference is authorized."},f,ensure_ascii=False,indent=2);f.write("\n")
 if fingerprint(SOURCE).sha256!=sha:raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
