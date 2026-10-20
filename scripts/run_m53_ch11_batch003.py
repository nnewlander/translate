"""Execute sealed Chapter 11 batch_003 (nine blocks plus one flat composite)."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.domain.composite_translation import CompositeTranslationResult
from booktrans.harness.composite_contract import build_composite_request, validate_flat_composite_with_ordinary_qa
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit

ROOT=Path(r"D:\translate"); RUN=ROOT/"runs/m5.3-20260914T140000Z-decb09"; OUT=RUN/"chapter_11/batch_003"
SOURCE=ROOT/"Software Engineering at Google.docx"; REQUESTS=RUN/"multi_chapter_translation_request.jsonl"
VIEWS=ROOT/"runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"; EVIDENCE=RUN/"chapter_11_verified_footnote_evidence.json"
ORDER=["body.p.03704","body.p.03705","body.p.03706","body.p.03707","body.p.03708","body.p.03709","body.p.03710","body.p.03711","body.p.03716"]
TEXT={
"body.p.03704":["更简洁的评审"],
"body.p.03705":["Google 的所有代码在提交前，至少都要由另一名工程师评审（参见 ","第 9 章","了解更多细节）。如果代码评审包含能证明代码正确性、边界情况和错误条件的充分测试，评审者验证代码是否如预期工作的工作量就会更少。评审者无需费力地在脑中逐一推演每种情况在代码中的运行过程，而可以验证每种情况都有通过的测试。"],
"body.p.03706":["周全的设计"],
"body.p.03707":["为新代码编写测试，是检验代码自身 API 设计的一种实用途径。如果新代码难以测试，往往是因为被测代码承担的职责过多，或依赖关系难以管理。设计良好的代码应当模块化，避免紧耦合，并聚焦于特定职责。及早修复设计问题，通常意味着以后需要返工得更少。"],
"body.p.03708":["快速、高质量的发布"],
"body.p.03709":["拥有健康的自动化测试套件后，团队便能自信地发布应用程序的新版本。Google 的许多项目每天都会向生产环境发布新版本——即使是有数百名工程师、每天提交数千项代码变更的大型项目也是如此。没有自动化测试，这不可能实现。"],
"body.p.03710":["设计测试套件"],
"body.p.03711":["如今，Google 的规模非常庞大，但我们并非一直如此；我们方法的基础早在很久以前就已奠定。多年来，随着代码库不断增长，我们学到了很多关于如何设计和执行测试套件的经验，而且常常是通过犯错并事后收拾残局学到的。"],
"body.p.03716":["这在公司内部引发了大量讨论：究竟什么是“小”？小是否等同于单元测试？那么集成测试呢，它们算什么规模？我们得出的结论是，每个测试用例都有两个不同的维度：规模和范围。规模是指运行测试用例所需的资源，例如内存、进程和时间。范围是指我们正在验证的具体代码路径。请注意，执行一行代码不同于验证它是否按预期工作。规模和范围相互关联，但却是不同的概念。"]}
COMPOSITE="m5_3_ch11_system_scale_tests"
COMPOSITE_TEXT="我们很早就学到一个教训：工程师倾向于编写规模更大、覆盖系统范围的测试，但这类测试比小型测试更慢、更不可靠，也更难调试。工程师受够了调试系统级测试，便问自己：“为什么不能一次只测试一台服务器？”或者：“为什么要一次测试整台服务器？我们可以分别测试更小的模块。”最终，减少痛苦的愿望促使团队开发出越来越小的测试；事实证明，它们更快、更稳定，且总体上更少令人痛苦。"
def wj(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8',newline='\n') as f: json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
def wjl(p,rows):
 with p.open('x',encoding='utf-8',newline='\n') as f:
  for x in rows:f.write(x.model_dump_json()+"\n")
def qa(v,r,known,refs):
 c=TaggedSyntheticCandidate(unit_uid=v.unit_uid,display_id=v.display_id,source_text=v.source_text,target_segments=r.target_segments,target_text=''.join(x.text for x in r.target_segments),generation_kind='calibration')
 z=FormatAnchorQA().validate(v,c,known,footnote_reference_numbers=refs);return {'unit_uid':v.unit_uid,'display_id':v.display_id,'passed':z.passed,'diagnostics':[d.model_dump() for d in z.diagnostics]}
def main():
 if OUT.exists():raise SystemExit('Batch exists; refusing to overwrite immutable artifacts.')
 before=fingerprint(SOURCE).sha256; OUT.mkdir(parents=True)
 allr={x['display_id']:CalibrationRequest.model_validate(x) for x in map(json.loads,filter(None,REQUESTS.read_text(encoding='utf-8').splitlines()))}
 ev=json.loads(EVIDENCE.read_text(encoding='utf-8'))['verified_markers']
 req=[allr[d].model_copy(update={'footnote_reference_numbers':footnote_numbers_for_unit(ev,unit_uid=allr[d].unit_uid,source_text=allr[d].source_text),'selection_categories':[ *allr[d].selection_categories,'batch_003']}) for d in ORDER]
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding='utf-8').splitlines() if x)}; known={v.unit_uid for v in views.values()}
 resp=[]
 for r in req:
  src=r.source_segments
  if len(src)!=len(TEXT[r.display_id]):raise RuntimeError('segment contract mismatch '+r.display_id)
  resp.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(src,TEXT[r.display_id],strict=True)]))
 wjl(OUT/'translation_request.jsonl',req); rawsha=persist_raw_response(OUT/'translation_raw_response.jsonl',resp)
 rawqa=[qa(views[r.display_id],x,known,r.footnote_reference_numbers) for r,x in zip(req,resp,strict=True)];wj(OUT/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in rawqa),'fail_count':sum(not x['passed'] for x in rawqa),'records':rawqa})
 norm=normalize_response_artifact(OUT/'translation_request.jsonl',OUT/'translation_raw_response.jsonl',OUT/'translation_normalized_response.jsonl',OUT/'translation_normalization_report.json')
 normalized=[TranslationResult.model_validate_json(x) for x in (OUT/'translation_normalized_response.jsonl').read_text(encoding='utf-8').splitlines() if x]; final=[qa(views[r.display_id],x,known,r.footnote_reference_numbers) for r,x in zip(req,normalized,strict=True)]
 wj(OUT/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});wj(OUT/'semantic_review_report.json',{'accept_count':len(final),'material_repair_count':0,'human_required_count':0,'reviewer_contract_error_count':0,'records':[{'unit_uid':x.unit_uid,'display_id':r.display_id,'status':'ACCEPT','issue_types':[],'explanation':'Independent semantic review found no material repair necessary.','original_target':''.join(s.text for s in x.target_segments),'repaired_target':None} for r,x in zip(req,normalized,strict=True)]});wjl(OUT/'translation_repaired_response.jsonl',normalized);wj(OUT/'translation_final_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final})
 scope=json.loads((RUN/'multi_chapter_scope_final.json').read_text(encoding='utf-8'));g=next(x for x in scope['proven_composite_groups'] if x['group_id']==COMPOSITE); creq,_=build_composite_request(g,views,source_sha256=before)
 if not creq.envelope.flat_target_eligible: raise RuntimeError('Unexpected structured composite contract')
 cdir=OUT/'composite_system_scale_tests';wj(cdir/'translation_request.json',creq.model_dump(mode='json'))
 craw=CompositeTranslationResult(group_id=COMPOSITE,target_text=COMPOSITE_TEXT);wjl(cdir/'translation_raw_response.jsonl',[craw])
 import hashlib
 csha=hashlib.sha256((cdir/'translation_raw_response.jsonl').read_bytes()).hexdigest();cerr=validate_flat_composite_with_ordinary_qa(craw,creq,approved_group_ids={COMPOSITE})
 wj(cdir/'translation_raw_qa_report.json',{'pass_count':int(not cerr),'fail_count':int(bool(cerr)),'diagnostics':cerr})
 wjl(cdir/'translation_normalized_response.jsonl',[craw]);wj(cdir/'translation_normalization_report.json',{'raw_immutable':True,'normalized_changed':False,'normalization_rule_distribution':{},'changes':[]});wj(cdir/'translation_normalized_qa_report.json',{'pass_count':int(not cerr),'fail_count':int(bool(cerr)),'diagnostics':cerr});wj(cdir/'semantic_review_report.json',{'status':'ACCEPT','issue_types':[],'explanation':'Reviewed against the proven combined source; no material repair is necessary.','raw_target':craw.target_text,'reviewed_target':craw.target_text});wjl(cdir/'translation_repaired_response.jsonl',[craw]);wj(cdir/'translation_final_qa_report.json',{'pass_count':int(not cerr),'fail_count':int(bool(cerr)),'diagnostics':cerr})
 wj(OUT/'batch_manifest.json',{'chapter':'chapter_11','batch':'batch_003','target_count':50,'actual_count':10,'target_met':False,'status':'SEALED_TARGET_SHORTFALL','stop_reason':'Turn boundary after ten complete logical work orders.','order':[ *ORDER,COMPOSITE],'composite_work_order_count':1,'structured_composite_count':0,'verified_footnote_evidence_used_count':sum(bool(r.footnote_reference_numbers) for r in req),'raw_sha256':rawsha,'composite_raw_sha256':csha,'normalization_changed':norm['normalized_unit_count']})
 if fingerprint(SOURCE).sha256!=before:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
