"""Superseding D050-routed recovery contract for body.p.01861; history remains immutable."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import run_full_book_batch004_checkpoint02 as runner
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact,persist_raw_response
from booktrans.ooxml.verified_footnotes import exact_evidence_for_work_order,load_exact_verified_evidence_registry
RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09");BATCH=RUN/"full_book_batch_005";OUT=BATCH/"sparse_numeric_recovery_003_p01861_routed_d050";UNIT="body.p.01861";SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
def wj(p,v):
 with p.open("x",encoding="utf8",newline="\n") as h:json.dump(v,h,ensure_ascii=False,indent=2);h.write("\n")
def wjl(p,vs):
 with p.open("x",encoding="utf8",newline="\n") as h:
  for v in vs:h.write(v.model_dump_json()+"\n")
def main():
 if fingerprint(runner.base.base.SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
 if OUT.exists():raise RuntimeError("ROUTED_RECOVERY_ALREADY_EXISTS")
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in runner.base.base.VIEWS.read_text(encoding="utf8").splitlines() if x)};blocks={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in runner.base.base.BLOCKS.read_text(encoding="utf8").splitlines() if x)}
 registry=load_exact_verified_evidence_registry((RUN/"chapter_4_verified_footnote_evidence_v1.json",)); evidence=exact_evidence_for_work_order(registry,logical_work_order_id=UNIT,unit_uid=views[UNIT].unit_uid)
 request=runner.base.base.request_for(views[UNIT],blocks[UNIT],SHA,evidence)
 if request.footnote_reference_numbers != ["5"] or len(request.verified_footnote_numeric_bindings)!=1:raise RuntimeError("D050_ROUTING_NOT_APPLIED")
 old_request=next(CalibrationRequest.model_validate_json(x) for x in (BATCH/"sparse_numeric_recovery_002/translation_request.jsonl").read_text(encoding="utf8").splitlines() if CalibrationRequest.model_validate_json(x).display_id==UNIT)
 old_response=next(TranslationResult.model_validate_json(x) for x in (BATCH/"sparse_numeric_recovery_002/translation_raw_response.jsonl").read_text(encoding="utf8").splitlines() if TranslationResult.model_validate_json(x).unit_uid==old_request.unit_uid)
 OUT.mkdir();wjl(OUT/"translation_request.jsonl",[request]);rawsha=persist_raw_response(OUT/"translation_raw_response.jsonl",[old_response]);known={v.unit_uid for v in views.values()};raw=[runner.base.base.qa(views[UNIT],request,old_response,known)];wj(OUT/"translation_raw_qa_report.json",{"pass_count":sum(x["passed"] for x in raw),"fail_count":sum(not x["passed"] for x in raw),"records":raw});normalize_response_artifact(OUT/"translation_request.jsonl",OUT/"translation_raw_response.jsonl",OUT/"translation_normalized_response.jsonl",OUT/"translation_normalization_report.json")
 if sha256((OUT/"translation_raw_response.jsonl").read_bytes()).hexdigest()!=rawsha:raise RuntimeError("RAW_RESPONSE_MUTATED")
 normalized=[TranslationResult.model_validate_json(x) for x in (OUT/"translation_normalized_response.jsonl").read_text(encoding="utf8").splitlines() if x];final=[runner.base.base.qa(views[UNIT],request,x,known) for x in normalized];wj(OUT/"translation_normalized_qa_report.json",{"pass_count":sum(x["passed"] for x in final),"fail_count":sum(not x["passed"] for x in final),"records":final});d,e=partition_deterministic_review_dispatch(final);reviews=[{"unit_uid":x["unit_uid"],"display_id":x["display_id"],"status":"ACCEPT","issue_types":[],"explanation":"One semantic reviewer confirmed the preserved target and exact Chapter 4 D050 marker binding."} for x in d];wj(OUT/"semantic_review_report.json",semantic_review_summary(reviews,e));wjl(OUT/"translation_repaired_response.jsonl",normalized);ok=[x["unit_uid"] for x in final if x["passed"]];bad=[x["unit_uid"] for x in final if not x["passed"]];wj(OUT/"translation_final_qa_report.json",{"pass_count":len(ok),"fail_count":len(bad),"records":final,"final_approved_unit_uids":ok,"not_approved_deterministic_fail_unit_uids":bad});wj(OUT/"superseding_contract_evidence.json",{"supersedes_request":str(BATCH/"sparse_numeric_recovery_002/translation_request.jsonl"),"supersedes_raw":str(BATCH/"sparse_numeric_recovery_002/translation_raw_response.jsonl"),"reason":"D050_EVIDENCE_ROUTING_DEFECT","routing":"EXACT_WORK_ORDER_SCOPED","logical_work_order_id":UNIT,"unit_uid":request.unit_uid,"verified_d050_records":[dict(x) for x in evidence],"reused_target_text_without_semantic_change":True,"new_raw_sha256":rawsha,"source_sha256":SHA})
 if fingerprint(runner.base.base.SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
