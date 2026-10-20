"""Close Batch 005 recovery and derive authoritative progress v7."""
from __future__ import annotations
import copy,json
from pathlib import Path
from booktrans.harness.source_integrity import fingerprint
ROOT=Path(r"D:\translate");RUN=ROOT/"runs/full-book-20260916T000200Z-decb09";BATCH=RUN/"full_book_batch_005";SOURCE=ROOT/"Software Engineering at Google.docx";SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
def once(p,v):
 with p.open("x",encoding="utf8",newline="\n") as h:json.dump(v,h,ensure_ascii=False,indent=2);h.write("\n")
def main():
 if fingerprint(SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
 first=json.loads((RUN/"full_book_batch_005_completion_report.json").read_text(encoding="utf8")); recovery=BATCH/"sparse_numeric_recovery_002";final=json.loads((recovery/"translation_final_qa_report.json").read_text(encoding="utf8"));ev=json.loads((recovery/"recovery_evidence.json").read_text(encoding="utf8"));routed=BATCH/"sparse_numeric_recovery_003_p01861_routed_d050";routed_final=json.loads((routed/"translation_final_qa_report.json").read_text(encoding="utf8"));prior=json.loads((RUN/"full_book_progress_v6.json").read_text(encoding="utf8"));scope=json.loads((RUN/"full_book_translation_scope.json").read_text(encoding="utf8"));selection=json.loads((RUN/"full_book_batch_005_selection.json").read_text(encoding="utf8"))
 if first["deterministic_fail_count"]!=4 or final["pass_count"]!=3 or final["fail_count"]!=1 or routed_final["pass_count"]!=1 or routed_final["fail_count"]!=0 or set(ev["recovered_logical_work_orders"])!={x["logical_work_order_id"] for x in first["deterministic_failures"]}:raise RuntimeError("RECOVERY_CLOSURE_MISMATCH")
 ids=[x["logical_work_order_id"] for x in scope["logical_work_orders"]];last=ids.index(selection["selected_logical_work_order_ids"][-1]);next_id=ids[last+1]
 phases=copy.deepcopy(prior["current_progress_by_phase"]);phases["chapter_3"]["final_approved"]+=104;phases["chapter_3"]["unstarted"]-=104;phases["chapter_4"]["final_approved"]+=93;phases["chapter_4"]["unstarted"]-=93;phases["chapter_5"]["final_approved"]+=3;phases["chapter_5"]["unstarted"]-=3
 release=prior["current_release_set_count"]+200;remaining=prior["remaining_unstarted_first_pass"]-200
 out={"artifact_type":"FULL_BOOK_BATCH_RECOVERY_CLOSURE","batch":"FULL_BOOK_BATCH_005","first_pass_terminal":200,"first_pass_final_approved":first["final_approved_count"],"first_pass_deterministic_fail":4,"recovery_final_approved":4,"final_release_approved":200,"unresolved_deterministic_fail":0,"human_required":0,"recovery_queue":[],"recovery_evidence":[str(recovery/"recovery_evidence.json"),str(routed/"superseding_contract_evidence.json")],"incomplete_attempt_raw_preserved":str(BATCH/"sparse_numeric_recovery_001/translation_raw_response.jsonl"),"200_UNIT_PRODUCTION_STEADY_STATE":True,"steady_state_evidence":"Eight 25-unit savepoints reached 200 terminal; three local numeric repairs passed directly, and one was recovered with an exact work-order-scoped Chapter 4 D050 contract supersession. No numeric contract change was needed.","next_authoritative_resume_point":next_id,"source_sha256":SHA}
 once(RUN/"full_book_batch_005_recovery_closure.json",out)
 progress={"artifact_type":"FULL_BOOK_TRANSLATION_PROGRESS","supersedes":"full_book_progress_v6.json","total_physical_work_orders":prior["total_physical_work_orders"],"total_logical_work_orders":prior["total_logical_work_orders"],"current_release_set_count":release,"unresolved_deterministic_fail":0,"human_required":0,"remaining_unstarted_first_pass":remaining,"recovery_queue_size":0,"status":"FULL_BOOK_PRODUCTION_IN_PROGRESS","latest_completed_batch":"FULL_BOOK_BATCH_005","source_sha256":SHA,"current_progress_by_phase":phases,"recovery_queue":[],"200_UNIT_PRODUCTION_STEADY_STATE":True,"next_throughput_reassessment":"FULL_BOOK_BATCH_006 = 200 logical; do not auto-increase to 250.","next_resume_point_derived_from_authoritative_scope":next_id}
 if sum(x["final_approved"] for x in phases.values())!=release:raise RuntimeError("PROGRESS_PHASE_ACCOUNTING_MISMATCH")
 if release+remaining!=progress["total_logical_work_orders"]:raise RuntimeError("PROGRESS_TOTAL_ACCOUNTING_MISMATCH")
 once(RUN/"full_book_progress_v7.json",progress)
 if fingerprint(SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
