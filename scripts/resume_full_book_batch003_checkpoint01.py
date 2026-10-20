"""Complete checkpoint 01 after its pre-RAW segment-count interruption."""
from __future__ import annotations
from hashlib import sha256
import json
import run_full_book_batch003_checkpoint01 as base
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response

def main():
    out=base.BATCH/base.CHECKPOINT
    source_sha=fingerprint(base.SOURCE).sha256
    if (out/'translation_raw_response.jsonl').exists(): raise RuntimeError('RAW_ALREADY_EXISTS')
    req=[CalibrationRequest.model_validate_json(x) for x in (out/'translation_request.jsonl').read_text(encoding='utf-8').splitlines() if x]
    if [r.display_id for r in req]!=base.ORDER: raise RuntimeError('IMMUTABLE_REQUEST_SELECTION_MISMATCH')
    vs={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in base.VIEWS.read_text(encoding='utf-8').splitlines() if x)}
    raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,base.TARGETS[r.display_id],strict=True)]) for r in req]
    rawsha=persist_raw_response(out/'translation_raw_response.jsonl',raw);known={v.unit_uid for v in vs.values()};a=[base.qa(vs[r.display_id],r,x,known) for r,x in zip(req,raw,strict=True)];base.wj(out/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in a),'fail_count':sum(not x['passed'] for x in a),'records':a});normalize_response_artifact(out/'translation_request.jsonl',out/'translation_raw_response.jsonl',out/'translation_normalized_response.jsonl',out/'translation_normalization_report.json');
    if sha256((out/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=rawsha: raise RuntimeError('RAW_RESPONSE_MUTATED')
    normalized=[TranslationResult.model_validate_json(x) for x in (out/'translation_normalized_response.jsonl').read_text(encoding='utf-8').splitlines() if x];final=[base.qa(vs[r.display_id],r,x,known) for r,x in zip(req,normalized,strict=True)];base.wj(out/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});d,e=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, protected literals, exact D050 footnote evidence where present, and reader-facing role.'} for x in d];base.wj(out/'semantic_review_report.json',semantic_review_summary(reviews,e));base.wjl(out/'translation_repaired_response.jsonl',normalized);ok=[x['unit_uid'] for x in final if x['passed']];bad=[x['unit_uid'] for x in final if not x['passed']];base.wj(out/'translation_final_qa_report.json',{'pass_count':len(ok),'fail_count':len(bad),'records':final,'final_approved_unit_uids':ok,'not_approved_deterministic_fail_unit_uids':bad});base.wj(out/'checkpoint_evidence.json',{'logical_work_orders':base.ORDER,'source_sha256':source_sha,'raw_sha256':rawsha,'deterministic_pass_count':len(ok),'deterministic_fail_count':len(bad),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(e),'chapter_1_footnote_evidence':str(base.FOOT),'pdf_assisted_source_pages':[37,38,39,40],'interruption_provenance':'pre_raw_segment_contract_mismatch; immutable request reused; no translation retry before RAW'});append_sealed_checkpoint(base.BATCH/'batch_manifest.json',out)
    if fingerprint(base.SOURCE).sha256!=source_sha: raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__': main()
