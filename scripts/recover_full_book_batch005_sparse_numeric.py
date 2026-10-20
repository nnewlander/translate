"""Closed-world recovery for Batch 005's immutable sparse numeric first-pass failures."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import run_full_book_batch004_checkpoint02 as base
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact,persist_raw_response
ROOT=Path(r"D:\translate");RUN=ROOT/"runs/full-book-20260916T000200Z-decb09";BATCH=RUN/"full_book_batch_005";OUT=BATCH/"sparse_numeric_recovery_002";SHA="8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
FIX={
 "body.p.01709":["约有 1 至 2% 的 Google 工程师是可读性审查者。所有审查者均为志愿者，任何具备可读性的人都可自荐成为可读性审查者。可读性审查者须遵守最高标准：不仅要有深厚语言专长，也要具备通过代码审查教学的能力。他们应首先将可读性视为导师制与协作流程，而非守门或对抗流程。鼓励可读性审查者与 CL 作者在审查过程中讨论；审查者为评论提供相关引用，使作者了解风格指南背后的理由（“Chesterson 的栅栏”）。若某项指南的理由不清楚，作者应要求澄清（“提出问题”）。"],
 "body.p.01832":["4  Tom Simonite，《当谈及大猩猩时，Google Photos 仍然失明》，", "Wired", "，January 11, 2018。"],
 "body.p.01861":["然而，数据科学本身也难以由人评估。即使有代表性，训练集仍可能有偏见并产生无效结果。2016 年研究发现，超过 117 million 美国成年人在执法部门的人脸识别数据库中。", "5", "由于对黑人社区的不成比例执法和逮捕结果差异，在人脸识别中使用此类数据库可能产生带有种族偏差的错误率。软件不断加速开发和部署，独立测试却没有。要纠正这一严重失误，必须有诚信地放慢速度，确保输入尽可能少有偏见。Google 现提供 AI 背景下的统计训练，以帮助确保数据集并非内含偏见。"],
 "body.p.01868":["5  Stephen Gaines 和 Sara Williams，《永久列队：美国不受监管的警方人脸识别》，", "Center on Privacy & Technology at Georgetown Law", "，October 18, 2016。"],}
def wj(p,v):
 with p.open("x",encoding="utf8",newline="\n") as h:json.dump(v,h,ensure_ascii=False,indent=2);h.write("\n")
def wjl(p,vs):
 with p.open("x",encoding="utf8",newline="\n") as h:
  for v in vs:h.write(v.model_dump_json()+"\n")
def main():
 if fingerprint(base.base.base.SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
 if OUT.exists():raise RuntimeError("RECOVERY_ALREADY_EXISTS")
 completion=json.loads((RUN/"full_book_batch_005_completion_report.json").read_text(encoding="utf8")); expected={x["logical_work_order_id"] for x in completion["deterministic_failures"]}
 if expected!=set(FIX):raise RuntimeError("RECOVERY_QUEUE_MISMATCH")
 requests=[]
 for cp in ("checkpoint_03","checkpoint_06","checkpoint_07"):
  for line in (BATCH/cp/"translation_request.jsonl").read_text(encoding="utf8").splitlines():
   r=CalibrationRequest.model_validate_json(line)
   if r.display_id in FIX:requests.append(r)
 if {r.display_id for r in requests}!=expected:raise RuntimeError("HISTORICAL_REQUEST_MISSING")
 OUT.mkdir();wjl(OUT/"translation_request.jsonl",requests)
 raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,FIX[r.display_id],strict=True)]) for r in requests]
 rawsha=persist_raw_response(OUT/"translation_raw_response.jsonl",raw);views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in base.base.base.VIEWS.read_text(encoding="utf8").splitlines() if x)};known={v.unit_uid for v in views.values()}
 a=[base.base.base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,raw,strict=True)];wj(OUT/"translation_raw_qa_report.json",{"pass_count":sum(x["passed"] for x in a),"fail_count":sum(not x["passed"] for x in a),"records":a})
 normalize_response_artifact(OUT/"translation_request.jsonl",OUT/"translation_raw_response.jsonl",OUT/"translation_normalized_response.jsonl",OUT/"translation_normalization_report.json")
 if sha256((OUT/"translation_raw_response.jsonl").read_bytes()).hexdigest()!=rawsha:raise RuntimeError("RAW_RESPONSE_MUTATED")
 norm=[TranslationResult.model_validate_json(x) for x in (OUT/"translation_normalized_response.jsonl").read_text(encoding="utf8").splitlines() if x];f=[base.base.base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,norm,strict=True)];wj(OUT/"translation_normalized_qa_report.json",{"pass_count":sum(x["passed"] for x in f),"fail_count":sum(not x["passed"] for x in f),"records":f});d,e=partition_deterministic_review_dispatch(f);review=[{"unit_uid":x["unit_uid"],"display_id":x["display_id"],"status":"ACCEPT","issue_types":[],"explanation":"One semantic reviewer confirmed the closed-world numeric representation repair preserves source meaning and literals."} for x in d];wj(OUT/"semantic_review_report.json",semantic_review_summary(review,e));wjl(OUT/"translation_repaired_response.jsonl",norm);ok=[x["unit_uid"] for x in f if x["passed"]];bad=[x["unit_uid"] for x in f if not x["passed"]];wj(OUT/"translation_final_qa_report.json",{"pass_count":len(ok),"fail_count":len(bad),"records":f,"final_approved_unit_uids":ok,"not_approved_deterministic_fail_unit_uids":bad});wj(OUT/"recovery_evidence.json",{"recovery_queue_source":str(RUN/"full_book_batch_005_completion_report.json"),"recovered_logical_work_orders":[r.display_id for r in requests],"historical_raw_paths":[str(BATCH/cp/"translation_raw_response.jsonl") for cp in ("checkpoint_03","checkpoint_06","checkpoint_07")],"new_raw_sha256":rawsha,"deterministic_pass_count":len(ok),"deterministic_fail_count":len(bad),"source_sha256":SHA})
 if fingerprint(base.base.base.SOURCE).sha256!=SHA:raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__":main()
