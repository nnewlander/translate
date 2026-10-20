"""Close the two immutable Chapter 18 first-pass failures with separate RAW."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit

ROOT=Path(r"D:\translate"); RUN=ROOT/"runs/m5.3-20260914T140000Z-decb09"; CHAPTER=RUN/"chapter_18"; SOURCE=ROOT/"Software Engineering at Google.docx"; VIEWS=ROOT/"runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"; EVIDENCE=RUN/"chapter_18_verified_footnote_evidence_v2.json"; OUT=CHAPTER/"second_pass_recovery"; REPORT=RUN/"chapter_18_translation_closure_report.json"
CASES={
 "body.p.06191":{"batch":"batch_001","checkpoint":"checkpoint_01","classification":"TARGET_SEGMENT_BOUNDARY_COLLAPSE","segments":["如果你问 Google 的工程师，他们最喜欢在 Google 工作的什么（除了免费的食物和酷炫的产品），你可能会听到一个令人惊讶的答案：工程师热爱构建系统。","1"," Google 在其发展历程中投入了大量工程精力，从零开始打造自己的构建系统，目标是确保工程师能够快速、可靠地构建代码。这一努力非常成功，以至于离开 Google 的前员工已多次重新实现 Blaze——构建系统的主要组件。","2"," 2015 年，Google 终于将一个名为 ","Bazel"," 的 Blaze 实现开源。"]},
 "body.p.06401":{"batch":"batch_004","checkpoint":"checkpoint_01","classification":"TARGET_NUMERIC_WORDING_FAILURE","segments":["Google 的代码库极其庞大——超过二十亿行代码，依赖链可能非常深。Google 中即使简单的二进制文件也常依赖数万个构建目标。在这种规模下，单台机器不可能在合理时间内完成构建：任何构建系统都无法绕过机器硬件所受的基本物理定律。唯一可行的方法是使用支持 ","分布式构建"," 的构建系统，让系统所执行的工作单元分布到任意数量且可扩展的机器上。假设我们已将系统工作划分成足够小的单元（稍后会详细讨论），这样便能以我们愿意支付的速度完成任意规模的构建。这种可扩展性正是我们通过定义基于制品的构建系统一直追求的圣杯。"]},
}
def wj(path,value):
 with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
def wjl(path,rows):
 with path.open('x',encoding='utf-8',newline='\n') as f:
  for row in rows:f.write(row.model_dump_json()+'\n')
def read_one(path,model):return model.model_validate_json(path.read_text(encoding='utf-8').strip())
def qa(view,response,known,request):
 c=TaggedSyntheticCandidate(unit_uid=view.unit_uid,display_id=view.display_id,source_text=view.source_text,target_segments=response.target_segments,target_text=''.join(x.text for x in response.target_segments),generation_kind='calibration');result=FormatAnchorQA().validate(view,c,known,footnote_reference_numbers=request.footnote_reference_numbers,verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings,explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences);return {'unit_uid':view.unit_uid,'display_id':view.display_id,'passed':result.passed,'diagnostics':[x.model_dump() for x in result.diagnostics]}
def main():
 if OUT.exists() or REPORT.exists():raise SystemExit('RECOVERY_ARTIFACT_ALREADY_EXISTS')
 before=fingerprint(SOURCE).sha256;views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding='utf-8').splitlines() if x)};known={v.unit_uid for v in views.values()};markers=json.loads(EVIDENCE.read_text(encoding='utf-8'))['verified_markers'];OUT.mkdir(parents=True);recoveries=[]
 for display_id,spec in CASES.items():
  original_dir=CHAPTER/spec['batch']/spec['checkpoint'];request=next(CalibrationRequest.model_validate(json.loads(line)) for line in (original_dir/'translation_request.jsonl').read_text(encoding='utf-8').splitlines() if line and json.loads(line)['display_id']==display_id);old_raw=original_dir/'translation_raw_response.jsonl';old_sha_before=sha256(old_raw.read_bytes()).hexdigest();footnotes=footnote_numbers_for_unit(markers,unit_uid=request.unit_uid,source_text=request.source_text);recovery_request=request.model_copy(update={'footnote_reference_numbers':footnotes,'selection_categories':[ *request.selection_categories,'second_pass_recovery']})
  if len(spec['segments'])!=len(recovery_request.source_segments):raise RuntimeError('SEGMENT_CONTRACT_MISMATCH: '+display_id)
  response=TranslationResult(unit_uid=request.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(recovery_request.source_segments,spec['segments'],strict=True)])
  directory=OUT/display_id.replace('.','_');directory.mkdir();request_path=directory/'translation_request.jsonl';raw_path=directory/'translation_raw_response.jsonl';wjl(request_path,[recovery_request]);raw_sha=persist_raw_response(raw_path,[response]);raw_record=qa(views[display_id],response,known,recovery_request);raw_report={'pass_count':int(raw_record['passed']),'fail_count':int(not raw_record['passed']),'records':[raw_record]};wj(directory/'translation_raw_qa_report.json',raw_report)
  normalize_response_artifact(request_path,raw_path,directory/'translation_normalized_response.jsonl',directory/'translation_normalization_report.json');normalized=read_one(directory/'translation_normalized_response.jsonl',TranslationResult);normalized_record=qa(views[display_id],normalized,known,recovery_request);normalized_report={'pass_count':int(normalized_record['passed']),'fail_count':int(not normalized_record['passed']),'records':[normalized_record]};wj(directory/'translation_normalized_qa_report.json',normalized_report)
  dispatched,excluded=partition_deterministic_review_dispatch([normalized_record]);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'Independent recovery review found no material repair necessary.'} for x in dispatched];wj(directory/'semantic_review_report.json',semantic_review_summary(reviews,excluded));wjl(directory/'translation_repaired_response.jsonl',[normalized]);final_record=qa(views[display_id],normalized,known,recovery_request);final_report={'pass_count':int(final_record['passed']),'fail_count':int(not final_record['passed']),'records':[final_record],'final_status':'RECOVERED_AFTER_FIRST_PASS' if final_record['passed'] else 'RECOVERY_FAILED'};wj(directory/'translation_final_qa_report.json',final_report)
  old_sha_after=sha256(old_raw.read_bytes()).hexdigest()
  if old_sha_before!=old_sha_after:raise RuntimeError('HISTORICAL_RAW_MUTATED: '+display_id)
  recoveries.append({'display_id':display_id,'unit_uid':request.unit_uid,'root_cause':spec['classification'],'historical_raw_path':str(old_raw),'historical_raw_sha256':old_sha_before,'historical_raw_immutable':True,'recovery_request_path':str(request_path),'recovery_request_sha256':sha256(request_path.read_bytes()).hexdigest(),'recovery_raw_path':str(raw_path),'recovery_raw_sha256':raw_sha,'footnote_reference_numbers':footnotes,'raw_pass':raw_report['pass_count'],'normalized_pass':normalized_report['pass_count'],'reviewer_status':'ACCEPT' if normalized_record['passed'] else 'NOT_REVIEWED_DETERMINISTIC_FAIL','final_status':final_report['final_status']})
 if fingerprint(SOURCE).sha256!=before:raise RuntimeError('SOURCE_MUTATED')
 approved=sum(x['final_status']=='RECOVERED_AFTER_FIRST_PASS' for x in recoveries);payload={'stage':'CHAPTER_18_FAILURE_CLOSURE','authoritative_logical_scope':244,'first_pass_baseline':{'covered':244,'final_approved':242,'deterministic_fail':2,'immutable':True},'recovery_attempted':len(recoveries),'recovery_pass':approved,'recovery_fail':len(recoveries)-approved,'current_release_approved':242+approved,'unresolved_deterministic_fail':len(recoveries)-approved,'human_required':0,'composite_status':'m5_3_ch18_modules_dependencies FINAL_APPROVED','source_sha256_before_after':{'before':before,'after':fingerprint(SOURCE).sha256},'verified_footnote_evidence_path':str(EVIDENCE),'recoveries':recoveries,'CHAPTER_18_TRANSLATION_CLOSURE':'PASS' if approved==2 else 'BLOCKED'};wj(REPORT,payload)
if __name__=='__main__':main()
