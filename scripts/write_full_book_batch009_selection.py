"""Freeze Batch 009 from progress v10."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(r"D:\translate");RUN=ROOT/"runs"/"full-book-20260916T000200Z-decb09";SCOPE=RUN/"full_book_translation_scope.json";PROGRESS=RUN/"full_book_progress_v10.json";OUT=RUN/"full_book_batch_009_selection.json"
def get(p:Path)->set[str]:return set(json.loads(p.read_text(encoding="utf-8")).get("selected_logical_work_order_ids",[]))
def main()->None:
 if OUT.exists():raise RuntimeError("IMMUTABLE_SELECTION_ALREADY_EXISTS")
 p=json.loads(PROGRESS.read_text(encoding="utf-8"));s=json.loads(SCOPE.read_text(encoding="utf-8"));prior=set().union(*(get(RUN/f"full_book_batch_{n:03d}_selection.json") for n in range(1,9)))
 if p["recovery_queue_size"] or p["unresolved_deterministic_fail"] or p["human_required"]:raise RuntimeError("BATCH_009_PRECONDITION_FAILED")
 selected=[x for x in s["logical_work_orders"] if x["release_status"]=="PENDING_FIRST_PASS" and x["logical_work_order_id"] not in prior and x["phase"]!="toc_late_stage"][:200];ids=[x["logical_work_order_id"] for x in selected]
 if len(ids)!=200 or len(set(ids))!=200 or ids[0]!=p["next_resume_point_derived_from_authoritative_scope"]:raise RuntimeError("AUTHORITATIVE_BATCH_009_SELECTION_MISMATCH")
 v={"artifact_type":"FULL_BOOK_BATCH_SELECTION","batch":"FULL_BOOK_BATCH_009","macro_target":200,"micro_checkpoint_size":25,"authoritative_scope":str(SCOPE),"authoritative_scope_sha256":hashlib.sha256(SCOPE.read_bytes()).hexdigest(),"source_docx":s["source_docx"],"source_sha256":s["source_sha256"],"prior_progress":str(PROGRESS),"selection_order":"authoritative remaining-first-pass source order after closed Batch 008; TOC remains late-stage content","selected_logical_work_order_ids":ids,"selected_orders":[{k:x.get(k) for k in ("logical_work_order_id","physical_member_display_ids","composite_group_id","phase","release_status")} for x in selected],"selected_id_set_sha256":hashlib.sha256("\n".join(ids).encode()).hexdigest(),"selection_validation":{"selected_logical_count":200,"duplicate_logical_count":0,"previous_approved_overlap_count":len(set(ids)&prior),"existing_recovery_overlap_count":0,"toc_late_stage_count":sum(x["phase"]=="toc_late_stage" for x in selected),"phase_counts":{q:sum(x["phase"]==q for x in selected) for q in sorted({x["phase"] for x in selected})}}}
 with OUT.open("x",encoding="utf-8",newline="\n") as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write("\n")
if __name__=="__main__":main()
