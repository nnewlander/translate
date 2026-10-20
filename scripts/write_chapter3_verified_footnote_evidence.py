"""Write the bounded, closed-world Chapter 3 D050 footnote evidence matrix."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.source_integrity import fingerprint

ROOT=Path(r"D:\translate"); RUN=ROOT/'runs'/'full-book-20260916T000200Z-decb09'; SOURCE=ROOT/'Software Engineering at Google.docx'; VIEWS=ROOT/'runs'/'structural-tagged-synthetic-20260913T075737Z-decb09-d22356'/'translation_views.jsonl'; OUT=RUN/'chapter_3_verified_footnote_evidence_v1.json'
# Closed audit matrix. PDF pages were corroborated through original-PDF text and page review; this is not a detector.
PAIRS=[('1','body.p.01387','body.p.01405',72,'things.1'),('2','body.p.01411','body.p.01420',73,None),('3','body.p.01463','body.p.01469',76,None),('4','body.p.01465','body.p.01471',76,None),('5','body.p.01521','body.p.01528',80,None),('6','body.p.01532','body.p.01544',81,None),('7','body.p.01554','body.p.01560',82,None),('8','body.p.01555','body.p.01561',82,None),('9','body.p.01581','body.p.01586',84,None),('10','body.p.01583','body.p.01587',84,None),('11','body.p.01620','body.p.01629',86,None),('12','body.p.01638','body.p.01643',87,None),('13','body.p.01638','body.p.01644',87,None),('14','body.p.01638','body.p.01645',87,None),('15','body.p.01638','body.p.01646',87,None),('16','body.p.01651','body.p.01662',88,None),('17','body.p.01656','body.p.01663',88,None),('18','body.p.01702','body.p.01713',91,None),('19','body.p.01717','body.p.01727',92,None),('20','body.p.01719','body.p.01728',92,None),('21','body.p.01738','body.p.01747',93,None),('22','body.p.01751','body.p.01768',94,None)]
def main():
 if OUT.exists():raise RuntimeError('IMMUTABLE_EVIDENCE_ALREADY_EXISTS')
 source_sha=fingerprint(SOURCE).sha256; views={x.display_id:x for x in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding='utf8').splitlines() if line)};records=[]
 for number,marker_id,body_id,page,flattened in PAIRS:
  marker=views[marker_id];body=views[body_id]
  if flattened:
   segment=next((item for item in marker.segments if flattened in item.text),None); marker_token=flattened
  else:
   segment=next((item for item in marker.segments if item.text.strip()==number),None); marker_token=number
  if segment is None:raise RuntimeError(f'MARKER_SEGMENT_NOT_EXACT:{number}:{marker_id}')
  if not body.source_text.startswith(number+' '):raise RuntimeError(f'FOOTNOTE_BODY_NOT_EXACT:{number}:{body_id}')
  records.append({'verification_status':'VERIFIED','unit_uid':marker.unit_uid,'logical_work_order_id':marker_id,'exact_source_marker_token':marker_token,'footnote_number':number,'pre_footnote_numeric_tokens':[],'source_segment_binding':[{'anchor_ref':segment.anchor_ref,'text':segment.text,'source_run_locator':segment.template_locator.xml_path,'paragraph_ordinal':segment.template_locator.paragraph_ordinal}],'corresponding_footnote_body':{'display_id':body_id,'unit_uid':body.unit_uid,'source_run_locator':body.base_template_locator.xml_path,'source_text':body.source_text},'original_pdf_page':page,'pdf_corroboration':'Original English PDF page was matched to the numbered footnote body and the reader-visible marker.'})
 artifact={'artifact_type':'CHAPTER_3_VERIFIED_FOOTNOTE_EVIDENCE','schema':'D050_EXACT_CLOSED_WORLD','source_docx':str(SOURCE),'source_sha256':source_sha,'original_pdf':str(ROOT/'Software Engineering at Google.pdf'),'candidate_count':23,'verified_count':len(records),'ambiguous_count':0,'unverified_candidates':[{'display_id':'body.p.01383','source_text':'43','classification':'EXCLUDE_RUNNING_DECORATION','reason':'Reader page number, not a footnote marker.'}],'records':records,'scope_guard':'Only listed exact records may be exported as D050 evidence. No inference from style, punctuation, superscript position, or arbitrary numbers is authorized.'}
 with OUT.open('x',encoding='utf8',newline='\n') as f:json.dump(artifact,f,ensure_ascii=False,indent=2);f.write('\n')
 if fingerprint(SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
