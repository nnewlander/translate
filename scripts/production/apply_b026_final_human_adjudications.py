"""Hydrate and apply B026 final Human decisions through existing authority."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.production_worker.artifacts import write_json_once
from booktrans.production_worker.candidate_authority import candidate_sha
from booktrans.production_worker.human_adjudication import HumanAllowedRepair, HumanApprovedPatch, apply_human_adjudication_to_production_checkpoint, build_human_adjudication
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import _production_review_group, _rescue_working_overrides, _resolve_checkpoint_authoritative_candidate
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid, load_stage
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs

RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH="FULL_BOOK_BATCH_026"
DECISIONS=Path(r"C:\Users\Lenovo\Downloads\b026_human_adjudication_decisions_v2.json")

def main():
    data=json.loads(DECISIONS.read_text(encoding="utf-8"))
    if data.get("schema_version")!=2 or data.get("batch")!=BATCH or len(data.get("decisions",[]))!=14: raise RuntimeError("B026_HUMAN_DECISION_SCHEMA_INVALID")
    packet=json.loads((RUN/BATCH.lower()/"human_release_packet_v2.json").read_text(encoding="utf-8"))
    packet_by={x["work_order_id"]:x for x in packet["items"]}
    report=[]
    for decision in data["decisions"]:
        display=str(decision.get("work_order_id")); item=packet_by.get(display)
        if item is None: report.append({"work_order":display,"status":"FAIL","reason":"DECISION_NOT_CURRENT_PACKET"}); continue
        checkpoint=str(item["binding"]["checkpoint"]); directory=RUN/BATCH.lower()/checkpoint
        loaded=frozen_batch_checkpoint_inputs(RUN,batch_label=BATCH,checkpoint=checkpoint)
        request=next((x for x in loaded["requests"] if x.display_id==display),None)
        if request is None: report.append({"work_order":display,"status":"FAIL","reason":"REQUEST_MISSING"}); continue
        raws=load_raw_by_uid(directory/"translation_raw_response.jsonl"); raw=raws[request.unit_uid]
        head=_resolve_checkpoint_authoritative_candidate(directory,display,raw)
        if decision.get("expected_current_head")!=head.candidate_sha256: report.append({"work_order":display,"status":"STALE","live":head.candidate_sha256}); continue
        # Idempotent checkpoint: a bound terminal for this head is already done.
        existing=load_stage(directory,"human_adjudications",display)
        if existing is not None and (existing.get("approved_candidate_sha256")==head.candidate_sha256 or existing.get("parent_candidate_sha256")==head.candidate_sha256): report.append({"work_order":display,"status":"SKIP","head":head.candidate_sha256}); continue
        overrides=_rescue_working_overrides(directory,loaded,raws)
        group=_production_review_group(request,loaded=loaded,run=RUN,checkpoint=checkpoint,existing_raw=raws,candidate_overrides=overrides)
        if group is None: report.append({"work_order":display,"status":"FAIL","reason":"AUTHORITATIVE_GROUP_MISSING"}); continue
        members=[]
        for member in group.members:
            req=next(x for x in loaded["requests"] if x.display_id==member.display_id); r=raws[req.unit_uid]; resolved=_resolve_checkpoint_authoritative_candidate(directory,req.display_id,r); members.append((enrich_request(req,translation_bible=loaded["bible"] if Path(loaded["bible"]).is_file() else None),resolved.candidate))
        targets=decision.get("target_segments")
        if decision.get("action")=="ACCEPT_CURRENT": targets=[s.text for s in head.candidate.target_segments]
        if not isinstance(targets,list) or len(targets)!=len(request.source_segments) or any(not isinstance(t,str) or not t.strip() for t in targets): report.append({"work_order":display,"status":"FAIL","reason":"HUMAN_TARGET_CONTRACT_INVALID"}); continue
        provider=next(x for x,_ in members if x.request.display_id==display)
        adj=build_human_adjudication(group,decision="HUMAN_APPROVED",reason="Owner-approved B026 final release adjudication.",allowed_repairs=[HumanAllowedRepair(logical_work_order_id=display,unit_uid=request.unit_uid,allowed_segment_indices=list(range(len(targets))),source_fingerprint=provider.request_binding_sha256)],source_hashes={p.request.display_id:p.request_binding_sha256 for p,_ in members},original_candidate_hashes={p.request.display_id:candidate_sha(raws[p.request.unit_uid]) for p,_ in members},approved_patches=[HumanApprovedPatch(logical_work_order_id=display,unit_uid=request.unit_uid,index=i,target_text=t) for i,t in enumerate(targets)])
        try:
            result=apply_human_adjudication_to_production_checkpoint(checkpoint_dir=directory,production_run=RUN,requests=loaded["requests"],views=loaded["views"],known_unit_ids=loaded["known_unit_ids"],qa_fn=harness_qa_record,adjudication=adj,group=group,members=members,translation_bible=loaded["bible"] if Path(loaded["bible"]).is_file() else None,triage_classification="TRUE_SEMANTIC_HUMAN",batch_label=BATCH,checkpoint=checkpoint)
            new=_resolve_checkpoint_authoritative_candidate(directory,display,raw); report.append({"work_order":display,"status":"PASS","action":decision["action"],"head":new.candidate_sha256,"qa":all(x.get("passed") for x in result["qa"].values())})
        except Exception as exc: report.append({"work_order":display,"status":"FAIL","reason":f"{type(exc).__name__}:{exc}"})
    write_json_once(RUN/BATCH.lower()/"b026_human_adjudication_apply_report_v1.json",{"artifact_type":"B026_HUMAN_ADJUDICATION_APPLY_REPORT","schema_version":1,"results":report,"translation_api_calls":0})
    print(json.dumps(report,ensure_ascii=False))
if __name__=="__main__": main()
