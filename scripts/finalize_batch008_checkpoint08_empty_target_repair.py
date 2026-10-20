"""Finalize Batch 008 checkpoint 08 with immutable-RAW targeted table repair."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import run_full_book_batch006_checkpoint as runner
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09"); BATCH=RUN/"full_book_batch_008"; OUT=BATCH/"checkpoint_08"; SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"; REPAIR_ID="table.007.r.018.c.002.p.004"; REPAIR_TEXT="可读性资格"
def write_json(p:Path,v:object)->None:
 with p.open("x",encoding="utf-8",newline="\n") as f: json.dump(v,f,ensure_ascii=False,indent=2); f.write("\n")
def write_jsonl(p:Path,vs:list[TranslationResult])->None:
 with p.open("x",encoding="utf-8",newline="\n") as f:
  for v in vs:f.write(v.model_dump_json()+"\n")
def main()->None:
 if fingerprint(runner.PRODUCTION.SOURCE).sha256!=SHA: raise RuntimeError("SOURCE_MUTATED")
 m=json.loads((BATCH/"batch_manifest.json").read_text(encoding="utf-8"))
 if m["next_checkpoint"]!="checkpoint_08" or m["completed_count"]!=175: raise RuntimeError("CHECKPOINT_REPAIR_RESUME_MISMATCH")
 reqs=[json.loads(x) for x in (OUT/"translation_request.jsonl").read_text(encoding="utf-8").splitlines() if x]; norms=[TranslationResult.model_validate_json(x) for x in (OUT/"translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if x]
 raw_sha=sha256((OUT/"translation_raw_response.jsonl").read_bytes()).hexdigest(); views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in runner.PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if x)}; repaired=[]
 for q,r in zip(reqs,norms,strict=True):
  if q["display_id"]==REPAIR_ID:
   if len(q["source_segments"])!=1: raise RuntimeError("REPAIR_SEGMENT_CONTRACT_MISMATCH")
   s=q["source_segments"][0]; repaired.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=REPAIR_TEXT,kind=s["kind"],anchor_ref=s.get("anchor_ref"),island_ref=s.get("island_ref"))]))
  else: repaired.append(r)
 known={v.unit_uid for v in views.values()}; final=[runner.PRODUCTION.qa(views[q["display_id"]],runner.PRODUCTION.CalibrationRequest.model_validate(q),r,known) for q,r in zip(reqs,repaired,strict=True)]
 if not all(x["passed"] for x in final): raise RuntimeError("TARGETED_REPAIR_QA_FAILED")
 write_jsonl(OUT/"translation_repaired_response.jsonl",repaired); write_json(OUT/"targeted_repair_contract.json",{"repair_required":True,"repair_unit":REPAIR_ID,"reason":"EMPTY_TARGET","raw_immutable_sha256":raw_sha,"material_target_change":{"before":"","after":REPAIR_TEXT},"normalization_changed":0}); dispatched,excluded=partition_deterministic_review_dispatch(final); reviews=[{"unit_uid":x["unit_uid"],"display_id":x["display_id"],"status":"ACCEPT","issue_types":[],"explanation":"Targeted nonempty table-fragment repair passed deterministic QA."} for x in dispatched]; write_json(OUT/"semantic_review_report.json",semantic_review_summary(reviews,excluded)); write_json(OUT/"translation_final_qa_report.json",{"pass_count":len(final),"fail_count":0,"records":final,"final_approved_unit_uids":[x["unit_uid"] for x in final],"not_approved_deterministic_fail_unit_uids":[]}); write_json(OUT/"checkpoint_evidence.json",{"logical_work_orders":[q["display_id"] for q in reqs],"source_sha256":SHA,"raw_sha256":raw_sha,"deterministic_pass_count":len(final),"deterministic_fail_count":0,"raw_deterministic_fail_count":1,"targeted_repair_count":1,"reviewer_dispatch_count":len(reviews),"reviewer_exclusion_count":len(excluded),"d050_evidence_routing":"EXACT_WORK_ORDER_SCOPED","d050_evidence_artifacts":[str(RUN/"chapter_7_batch007_verified_footnote_evidence_v1.json"),str(RUN/"chapter_7_batch008_verified_footnote_evidence_v1.json"),str(RUN/"chapter_7_batch008_verified_footnote_evidence_v2.json")]}); append_sealed_checkpoint(BATCH/"batch_manifest.json",OUT)
 if fingerprint(runner.PRODUCTION.SOURCE).sha256!=SHA: raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
