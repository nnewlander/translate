"""Derive Batch 008 closure and progress v10 from sealed evidence."""
from __future__ import annotations
import copy,json
from pathlib import Path
from booktrans.harness.source_integrity import fingerprint
ROOT=Path(r"D:\translate"); RUN=ROOT/"runs"/"full-book-20260916T000200Z-decb09"; BATCH=RUN/"full_book_batch_008"; SOURCE=ROOT/"Software Engineering at Google.docx"; SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
def once(p:Path,v:object)->None:
 with p.open("x",encoding="utf-8",newline="\n") as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write("\n")
def ids(p:Path)->set[str]:
 x=json.loads(p.read_text(encoding="utf-8")); return set(x.get("selected_logical_work_order_ids",[]))
def main()->None:
 if fingerprint(SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
 m=json.loads((BATCH/"batch_manifest.json").read_text(encoding="utf-8")); s=json.loads((RUN/"full_book_batch_008_selection.json").read_text(encoding="utf-8")); prior=json.loads((RUN/"full_book_progress_v9.json").read_text(encoding="utf-8")); scope=json.loads((RUN/"full_book_translation_scope.json").read_text(encoding="utf-8")); order=s["selected_logical_work_order_ids"]
 if m["status"]!="COMPLETE" or m["completed_count"]!=200 or len(m["checkpoints"])!=8:raise RuntimeError("BATCH_008_NOT_COMPLETE")
 observed=[]; stats=[]; final=raw_fail=human=0
 for c in m["checkpoints"]:
  d=BATCH/c["id"]; e=json.loads((d/"checkpoint_evidence.json").read_text(encoding="utf-8")); r=json.loads((d/"translation_raw_qa_report.json").read_text(encoding="utf-8")); f=json.loads((d/"translation_final_qa_report.json").read_text(encoding="utf-8")); review=json.loads((d/"semantic_review_report.json").read_text(encoding="utf-8"));
  if len(e["logical_work_orders"])!=25 or r["pass_count"]+r["fail_count"]!=25 or f["pass_count"]+f["fail_count"]!=25 or c["final_pass"]!=f["pass_count"]:raise RuntimeError(f"CHECKPOINT_EVIDENCE_DRIFT:{c['id']}")
  observed+=e["logical_work_orders"];final+=f["pass_count"];raw_fail+=r["fail_count"];human+=review.get("human_required_count",0);stats.append({"checkpoint":c["id"],"raw_qa_pass":r["pass_count"],"raw_qa_fail":r["fail_count"],"final_approved":f["pass_count"],"deterministic_fail":f["fail_count"],"human_required":review.get("human_required_count",0)})
 if observed!=order or len(set(observed))!=200 or final!=200 or human:raise RuntimeError("BATCH_008_ACCOUNTING_NOT_CLEAN")
 phases=copy.deepcopy(prior["current_progress_by_phase"]); by={x["logical_work_order_id"]:x for x in scope["logical_work_orders"]}
 for i in order:phases[by[i]["phase"]]["final_approved"]+=1;phases[by[i]["phase"]]["unstarted"]-=1
 completed=set().union(*(ids(RUN/f"full_book_batch_{n:03d}_selection.json") for n in range(1,9))); next_id=next(x["logical_work_order_id"] for x in scope["logical_work_orders"] if x["release_status"]=="PENDING_FIRST_PASS" and x["logical_work_order_id"] not in completed and x["phase"]!="toc_late_stage"); release=prior["current_release_set_count"]+200; rem=prior["remaining_unstarted_first_pass"]-200
 if sum(x["final_approved"] for x in phases.values())!=release or release+rem!=prior["total_logical_work_orders"]:raise RuntimeError("PROGRESS_ACCOUNTING_MISMATCH")
 comp={"artifact_type":"FULL_BOOK_BATCH_COMPLETION_REPORT","batch":"FULL_BOOK_BATCH_008","status":"COMPLETE","frozen_selection":str(RUN/"full_book_batch_008_selection.json"),"current_manifest":str(BATCH/"batch_manifest.json"),"macro_target":200,"completed_logical_work_orders":200,"checkpoint_stats":stats,"raw_deterministic_failure_count":raw_fail,"final_approved_count":200,"deterministic_fail_count":0,"human_required_count":0,"recovery_queue_size":0,"current_release_approved_count":release,"remaining_unstarted_first_pass":rem,"next_resume_point_derived_from_authoritative_scope":next_id,"source_sha256":SHA}
 prog={"artifact_type":"FULL_BOOK_TRANSLATION_PROGRESS","supersedes":"full_book_progress_v9.json","total_physical_work_orders":prior["total_physical_work_orders"],"total_logical_work_orders":prior["total_logical_work_orders"],"current_release_set_count":release,"unresolved_deterministic_fail":0,"human_required":0,"remaining_unstarted_first_pass":rem,"recovery_queue_size":0,"status":"FULL_BOOK_PRODUCTION_IN_PROGRESS","latest_completed_batch":"FULL_BOOK_BATCH_008","source_sha256":SHA,"current_progress_by_phase":phases,"recovery_queue":[],"200_UNIT_PRODUCTION_STEADY_STATE":True,"next_resume_point_derived_from_authoritative_scope":next_id,"derivation_evidence":{"prior_progress":str(RUN/"full_book_progress_v9.json"),"batch_manifest":str(BATCH/"batch_manifest.json"),"frozen_selection":str(RUN/"full_book_batch_008_selection.json")}}
 once(RUN/"full_book_batch_008_completion_report.json",comp);once(RUN/"full_book_progress_v10.json",prog)
 if fingerprint(SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
