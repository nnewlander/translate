"""Freeze Batch 006 from the post-recovery authoritative remaining-first-pass order."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(r"D:\translate");RUN=ROOT/"runs/full-book-20260916T000200Z-decb09";SCOPE=RUN/"full_book_translation_scope.json";OUT=RUN/"full_book_batch_006_selection.json"
def read(p):return json.loads(p.read_text(encoding="utf8"))
def ids(p):
 v=read(p);return set(v.get("selected_logical_work_order_ids",[x["logical_work_order_id"] for x in v.get("selected_work_orders",[])]))
def main():
 if OUT.exists():raise RuntimeError("IMMUTABLE_SELECTION_ALREADY_EXISTS")
 progress=read(RUN/"full_book_progress_v7.json");closure=read(RUN/"full_book_batch_005_recovery_closure.json");scope=read(SCOPE)
 if progress["recovery_queue_size"] or closure["unresolved_deterministic_fail"] or not progress["200_UNIT_PRODUCTION_STEADY_STATE"]:raise RuntimeError("BATCH_006_PRECONDITION_FAILED")
 prior=set().union(*(ids(RUN/f"full_book_batch_{n:03d}_selection.json") for n in range(1,6)))
 pending=[x for x in scope["logical_work_orders"] if x["release_status"]=="PENDING_FIRST_PASS" and x["logical_work_order_id"] not in prior and x["phase"]!="toc_late_stage"]
 selected=pending[:200];selected_ids=[x["logical_work_order_id"] for x in selected]
 if len(selected_ids)!=200 or len(set(selected_ids))!=200 or selected_ids[0]!=progress["next_resume_point_derived_from_authoritative_scope"]:raise RuntimeError("AUTHORITATIVE_BATCH_006_SELECTION_MISMATCH")
 out={"artifact_type":"FULL_BOOK_BATCH_SELECTION","batch":"FULL_BOOK_BATCH_006","macro_target":200,"micro_checkpoint_size":25,"authoritative_scope":str(SCOPE),"authoritative_scope_sha256":hashlib.sha256(SCOPE.read_bytes()).hexdigest(),"source_docx":scope["source_docx"],"source_sha256":scope["source_sha256"],"prior_progress":str(RUN/"full_book_progress_v7.json"),"closed_recovery_evidence":str(RUN/"full_book_batch_005_recovery_closure.json"),"selection_order":"authoritative remaining-first-pass source order after closed Batch 005 recovery; TOC remains late-stage content","selected_logical_work_order_ids":selected_ids,"selected_orders":[{k:x.get(k) for k in ("logical_work_order_id","physical_member_display_ids","composite_group_id","phase","release_status")} for x in selected],"selected_id_set_sha256":hashlib.sha256("\n".join(selected_ids).encode()).hexdigest(),"selection_validation":{"selected_logical_count":200,"duplicate_logical_count":0,"previous_approved_overlap_count":len(set(selected_ids)&prior),"existing_recovery_overlap_count":0,"toc_late_stage_count":sum(x["phase"]=="toc_late_stage" for x in selected),"phase_counts":{p:sum(x["phase"]==p for x in selected) for p in sorted({x["phase"] for x in selected})}}}
 with OUT.open("x",encoding="utf8",newline="\n") as h:json.dump(out,h,ensure_ascii=False,indent=2);h.write("\n")
if __name__=="__main__":main()
