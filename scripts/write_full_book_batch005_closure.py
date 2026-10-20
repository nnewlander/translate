"""Derive Batch 005 first-pass closure from immutable checkpoint artifacts."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.harness.source_integrity import fingerprint
ROOT=Path(r"D:\translate");RUN=ROOT/"runs/full-book-20260916T000200Z-decb09";BATCH=RUN/"full_book_batch_005";SOURCE=ROOT/"Software Engineering at Google.docx";SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
def main() -> None:
 if fingerprint(SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
 m=json.loads((BATCH/"batch_manifest.json").read_text(encoding="utf8"));s=json.loads((RUN/"full_book_batch_005_selection.json").read_text(encoding="utf8"))
 if m["status"]!="COMPLETE" or m["completed_count"]!=200 or len(m["checkpoints"])!=8:raise RuntimeError("BATCH_005_NOT_COMPLETE")
 stats=[];fail=[];observed=[];approved=human=0
 for cp in m["checkpoints"]:
  d=BATCH/cp["id"]; e=json.loads((d/"checkpoint_evidence.json").read_text(encoding="utf8"));r=json.loads((d/"translation_raw_qa_report.json").read_text(encoding="utf8"));f=json.loads((d/"translation_final_qa_report.json").read_text(encoding="utf8"));v=json.loads((d/"semantic_review_report.json").read_text(encoding="utf8"));observed+=e["logical_work_orders"]
  if len(e["logical_work_orders"])!=25 or r["pass_count"]+r["fail_count"]!=25 or f["pass_count"]+f["fail_count"]!=25:raise RuntimeError(f"CHECKPOINT_ACCOUNTING_MISMATCH:{cp['id']}")
  if cp["raw_pass"]!=r["pass_count"] or cp["final_pass"]!=f["pass_count"]:raise RuntimeError(f"MANIFEST_EVIDENCE_DRIFT:{cp['id']}")
  approved+=f["pass_count"];human+=v.get("human_required_count",0);stats.append({"checkpoint":cp["id"],"logical_count":25,"raw_qa_pass":r["pass_count"],"final_approved":f["pass_count"],"deterministic_fail":f["fail_count"],"human_required":v.get("human_required_count",0)})
  for x in f["records"]:
   if not x["passed"]:fail.append({"logical_work_order_id":x["display_id"],"unit_uid":x["unit_uid"],"classification":"TARGET_OUTPUT_NUMBER_REPRESENTATION","raw_status":"IMMUTABLE_FIRST_PASS_FAIL","review_status":"NOT_REVIEWED_DETERMINISTIC_FAIL","diagnostics":x["diagnostics"],"recovery_policy":"BATCH_005_TARGETED_RECOVERY"})
 if observed!=s["selected_logical_work_order_ids"] or len(set(observed))!=200:raise RuntimeError("FROZEN_SELECTION_EVIDENCE_MISMATCH")
 out={"artifact_type":"FULL_BOOK_BATCH_COMPLETION_REPORT","batch":"FULL_BOOK_BATCH_005","status":"COMPLETE","current_manifest":str(BATCH/"batch_manifest.json"),"macro_target":200,"completed_logical_work_orders":200,"checkpoint_stats":stats,"final_approved_count":approved,"deterministic_fail_count":len(fail),"deterministic_failures":fail,"human_required_count":human,"recovery_queue_size":len(fail),"source_sha256":SHA,"chapter_5_d050_preflight":str(BATCH/"chapter_5_entry_d050_preflight.json"),"next_state":"RECOVERY_SWEEP_REQUIRED"}
 with (RUN/"full_book_batch_005_completion_report.json").open("x",encoding="utf8",newline="\n") as h:json.dump(out,h,ensure_ascii=False,indent=2);h.write("\n")
 if fingerprint(SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
