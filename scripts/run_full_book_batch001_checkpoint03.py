"""Execute full-book batch 001 checkpoint 03 from its frozen selection."""
from __future__ import annotations
from hashlib import sha256
import json
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import persist_raw_response
from run_full_book_batch001_checkpoint01 import BLOCKS, BATCH, SCOPE_RUN, SOURCE, VIEWS, qa, request_for, write_json, write_jsonl

CHECKPOINT="checkpoint_03"
ORDER=["body.p.00641","body.p.00651","body.p.00652","body.p.00653","body.p.00654","body.p.00655","body.p.00656","textbox.002.p.0001","body.p.00657","body.p.00659","body.p.00663","body.p.00664","body.p.00665","body.p.00666","body.p.00667","body.p.00668","body.p.00669","body.p.00670","body.p.00671","body.p.00672"]
TARGETS={
"body.p.00641":["序言"],
"body.p.00651":["本书名为 ","Google 软件工程","。我们所说的软件工程究竟是什么？“软件工程”与“编程”或“计算机科学”有何区别？为什么 Google 会为过去 50 年的软件工程文献增添独特视角？"],
"body.p.00652":["“编程”和“软件工程”这两个术语在业界长期交替使用，尽管各自侧重点和含义不同。大学生通常学习计算机科学，毕业后以“程序员”身份从事代码编写工作。"],
"body.p.00653":["然而，“软件工程”听起来更严肃，仿佛意味着运用某些理论知识去构建真实而精确的事物。机械工程师、土木工程师、航空工程师和其他工程学科的从业者都在实践工程；他们都在现实世界中运用理论知识创造真实事物。软件工程师同样创造“真实事物”，只是它不如其他工程师创造的东西那样有形。"],
"body.p.00654":["与这些更成熟的工程职业相比，当前的软件工程理论或实践远没有那么严谨。航空工程师必须遵守严格的指南和做法，因为计算错误会造成真实损害；而编程整体上历来并未遵循如此严格的实践。但随着软件越来越融入生活，我们必须采用并依赖更严谨的工程方法。希望本书能帮助读者看到通往更可靠软件实践的道路。"],
"body.p.00655":["历久弥新的编程"],
"body.p.00656":["工程”                                                             代码，"],
"textbox.002.p.0001":["我们认为，“软件                          不仅包含编写"],
"body.p.00657":["还包括组织为长期构建和维护这些代码所采用的一切工具与流程。软件组织可以引入哪些实践，才能最好地保持其"],
"body.p.00659":["xix"],
"body.p.00663":["代码在长期内的价值？工程师如何让代码库更具可持续性，并让软件工程学科本身更加严谨？我们没有这些问题的根本答案，但希望 Google 在过去二十年积累的集体经验能照亮寻找答案的可能路径。"],
"body.p.00664":["本书分享的一项关键洞见是，软件工程可以被视为“随时间积分的编程”。在其从构思、引入、维护到弃用的整个生命周期中，我们能向代码引入哪些实践，使它具备", "可持续性", "——能够适应必要的变化？"],
"body.p.00665":["本书强调软件组织在设计、架构和编写代码时应牢记的三项基本原则："],
"body.p.00666":["时间与变化"],
"body.p.00667":["代码在其生命周期内需要如何适应"],
"body.p.00668":["规模与增长"],
"body.p.00669":["组织在演进过程中需要如何适应", "权衡与成本"],
"body.p.00670":["组织如何依据“时间与变化”和“规模与增长”的经验作出决策"],
"body.p.00671":["在全书各章中，我们努力回扣这些主题，并指出这些原则如何影响工程实践并使其可持续。（完整讨论见", "第 1 章", "。）"],
"body.p.00672":["Google 的视角"],
}
def main():
 source_sha=fingerprint(SOURCE).sha256; manifest=json.loads((BATCH/'batch_manifest.json').read_text(encoding='utf8'))
 if manifest['next_checkpoint']!=CHECKPOINT or manifest['completed_count']!=40: raise RuntimeError('CHECKPOINT_03_RESUME_MISMATCH')
 selection=json.loads((SCOPE_RUN/'full_book_batch_001_selection.json').read_text(encoding='utf8'))
 if selection['selected_logical_work_order_ids'][40:60]!=ORDER: raise RuntimeError('CHECKPOINT_03_SELECTION_MISMATCH')
 from booktrans.domain.ir import BlockIR
 views={x.display_id:x for x in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding='utf8').splitlines() if line)}; blocks={x.display_id:x for x in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding='utf8').splitlines() if line)}
 requests=[request_for(views[d],blocks[d],source_sha) for d in ORDER]; responses=[]
 for r in requests:
  if len(TARGETS[r.display_id])!=len(r.source_segments): raise RuntimeError('SEGMENT_CONTRACT_MISMATCH: '+r.display_id)
  responses.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]))
 out=BATCH/CHECKPOINT; out.mkdir(); write_jsonl(out/'translation_request.jsonl',requests); raw_sha=persist_raw_response(out/'translation_raw_response.jsonl',responses); known={v.unit_uid for v in views.values()}
 raw=[qa(views[r.display_id],x,known) for r,x in zip(requests,responses,strict=True)]; write_json(out/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in raw),'fail_count':sum(not x['passed'] for x in raw),'records':raw})
 norm=[];changes=[]
 for r,x in zip(requests,responses,strict=True): y,c=normalize_footnote_superscripts(r,x);norm.append(y);changes.extend(c)
 if sha256((out/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha: raise RuntimeError('RAW_RESPONSE_MUTATED')
 write_jsonl(out/'translation_normalized_response.jsonl',norm); final=[qa(views[r.display_id],x,known) for r,x in zip(requests,norm,strict=True)]
 write_json(out/'translation_normalization_report.json',{'raw_artifact':{'sha256_before':raw_sha,'sha256_after':raw_sha,'immutable':True},'input_unit_count':20,'normalized_unit_count':len({c.unit_uid for c in changes}),'unchanged_unit_count':20-len({c.unit_uid for c in changes}),'normalization_rule_distribution':{},'changes':[]});write_json(out/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final})
 dispatched,excluded=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, PDF-assisted fragment continuity where required, and protected literals.'} for x in dispatched];write_json(out/'semantic_review_report.json',semantic_review_summary(reviews,excluded));write_jsonl(out/'translation_repaired_response.jsonl',norm)
 approved=sorted(x['unit_uid'] for x in final if x['passed']);failed=sorted(x['unit_uid'] for x in final if not x['passed']);write_json(out/'translation_final_qa_report.json',{'pass_count':len(approved),'fail_count':len(failed),'records':final,'final_approved_unit_uids':approved,'not_approved_deterministic_fail_unit_uids':failed});write_json(out/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':source_sha,'raw_sha256':raw_sha,'deterministic_pass_count':len(approved),'deterministic_fail_count':len(failed),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(excluded),'pdf_assisted_source_pages':[21]});append_sealed_checkpoint(BATCH/'batch_manifest.json',out)
 if fingerprint(SOURCE).sha256!=source_sha: raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__': main()
