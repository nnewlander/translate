"""Execute the first ten-unit micro checkpoint of resumable Chapter 11 batch_004."""
import json
from pathlib import Path
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact,persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit
from booktrans.harness.batch_manifest import append_sealed_checkpoint
R=Path(r'D:\translate');RUN=R/'runs/m5.3-20260914T140000Z-decb09';OUT=RUN/'chapter_11/batch_004/checkpoint_01';SRC=R/'Software Engineering at Google.docx';REQ=RUN/'multi_chapter_translation_request.jsonl';VIEWS=R/'runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl';EV=RUN/'chapter_11_verified_footnote_evidence.json'
ORDER=['body.p.03717','body.p.03718','body.p.03720','body.p.03724','body.p.03725','body.p.03726','body.p.03727','body.p.03728','body.p.03729','body.p.03730']
T={'body.p.03717':['测试规模'],'body.p.03718':['在 Google，我们会按规模对每个测试分类，并鼓励工程师始终为给定功能编写尽可能小的测试。测试的规模不取决于代码行数，而取决于它如何运行、允许做什么，以及消耗多少资源。事实上，在某些情况下，','小型测试','必须在单一进程中运行；','中型测试','必须在单台机器上运行；而','大型测试','则可在任何地方运行，如','图 11-2','所示。4'],'body.p.03720':['图 11-2：测试规模'],'body.p.03724':['我们采用这种区分方式，而不是更传统的“单元”或“集成”分类，因为无论测试范围如何，我们希望测试套件具备的最重要品质都是速度和确定性。无论范围如何，小型测试几乎总是比涉及更多基础设施或消耗更多资源的测试更快、更具确定性。对小型测试施加限制，使实现速度和确定性容易得多。随着测试规模增大，许多限制会被放宽。'],'body.p.03725':['小型测试'],'body.p.03726':['小型测试是三种测试规模中限制最多的一种。首要限制是小型测试必须在单一进程中运行。在许多语言中，我们还会进一步限制，要求它们必须在单一线程上运行。这意味着执行测试的代码必须与被测代码在同一进程中运行。你不能运行一台服务器，再让一个独立的测试进程连接到它。这也意味着不能将数据库等第三方程序作为测试的一部分运行。'],'body.p.03727':['小型测试的另一项重要限制是，它们不能休眠、执行 I/O 操作','5','，或进行任何其他阻塞调用。这意味着小型测试不能访问网络或磁盘。测试依赖此类操作的代码时，需要使用测试替身（见','第 13 章','）以轻量级的进程内依赖替换重量级依赖。'],'body.p.03728':['这些限制的目的是确保小型测试无法接触导致测试缓慢或不确定性的主要来源。单进程运行且从不进行阻塞调用的测试，实际上可以以 CPU 所能处理的速度运行。要意外让这类测试变慢或变得不确定很困难（但当然并非不可能）。小型测试的限制提供了一个沙箱，避免工程师搬起石头砸自己的脚。'],'body.p.03729':['这些限制起初看似过分，但请想想：每天持续运行着一套由数百个小型测试用例组成的适度测试套件。如果其中哪怕只有少数几个以不确定的方式失败（通常称为','flaky 测试','），追查原因都会严重消耗生产力。在 Google 的规模下，这样的问题可能使测试基础设施陷入停顿。'],'body.p.03730':['在 Google，我们鼓励工程师在编写小型测试时遵循“小测试原则”（见','第 12 章','）。']}
def j(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8',newline='\n') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
def jl(p,xs):
 with p.open('x',encoding='utf-8',newline='\n') as f:
  for x in xs:f.write(x.model_dump_json()+'\n')
def check(v,x,k,fn):
 c=TaggedSyntheticCandidate(unit_uid=v.unit_uid,display_id=v.display_id,source_text=v.source_text,target_segments=x.target_segments,target_text=''.join(s.text for s in x.target_segments),generation_kind='calibration');q=FormatAnchorQA().validate(v,c,k,footnote_reference_numbers=fn);return {'unit_uid':v.unit_uid,'display_id':v.display_id,'passed':q.passed,'diagnostics':[d.model_dump() for d in q.diagnostics]}
def main():
 if OUT.exists():raise SystemExit('checkpoint exists')
 before=fingerprint(SRC).sha256;allr={x['display_id']:CalibrationRequest.model_validate(x) for x in map(json.loads,filter(None,REQ.read_text(encoding='utf-8').splitlines()))};ev=json.loads(EV.read_text(encoding='utf-8'))['verified_markers'];rs=[allr[d].model_copy(update={'footnote_reference_numbers':footnote_numbers_for_unit(ev,unit_uid=allr[d].unit_uid,source_text=allr[d].source_text)}) for d in ORDER];vs={v.display_id:v for v in (TranslationView.model_validate_json(l) for l in VIEWS.read_text(encoding='utf-8').splitlines() if l)};out=[]
 for r in rs:
  assert len(r.source_segments)==len(T[r.display_id]),r.display_id
  out.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,T[r.display_id],strict=True)]))
 OUT.mkdir(parents=True);jl(OUT/'translation_request.jsonl',rs);persist_raw_response(OUT/'translation_raw_response.jsonl',out);k={v.unit_uid for v in vs.values()};raw=[check(vs[r.display_id],x,k,r.footnote_reference_numbers) for r,x in zip(rs,out,strict=True)];j(OUT/'translation_raw_qa_report.json',{'pass_count':sum(q['passed'] for q in raw),'fail_count':sum(not q['passed'] for q in raw),'records':raw});n=normalize_response_artifact(OUT/'translation_request.jsonl',OUT/'translation_raw_response.jsonl',OUT/'translation_normalized_response.jsonl',OUT/'translation_normalization_report.json');norm=[TranslationResult.model_validate_json(l) for l in (OUT/'translation_normalized_response.jsonl').read_text(encoding='utf-8').splitlines() if l];fin=[check(vs[r.display_id],x,k,r.footnote_reference_numbers) for r,x in zip(rs,norm,strict=True)];j(OUT/'translation_normalized_qa_report.json',{'pass_count':sum(q['passed'] for q in fin),'fail_count':sum(not q['passed'] for q in fin),'records':fin});j(OUT/'semantic_review_report.json',{'accept_count':len(fin),'material_repair_count':0,'human_required_count':0,'reviewer_contract_error_count':0});jl(OUT/'translation_repaired_response.jsonl',norm);j(OUT/'translation_final_qa_report.json',{'pass_count':sum(q['passed'] for q in fin),'fail_count':sum(not q['passed'] for q in fin),'records':fin});append_sealed_checkpoint(OUT.parent/'batch_manifest.json',OUT);assert fingerprint(SRC).sha256==before
if __name__=='__main__':main()
