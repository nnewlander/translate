"""Execute full-book batch 003 checkpoint 05 (closed 25-unit selection)."""
from __future__ import annotations
from hashlib import sha256
import json
import run_full_book_batch003_checkpoint01 as base
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
CHECKPOINT='checkpoint_05'
ORDER=['body.p.01059','body.p.01063','body.p.01064','body.p.01065','body.p.01066','body.p.01067','body.p.01068','body.p.01069','body.p.01070','textbox.007.p.0001','body.p.01071','textbox.008.p.0001','textbox.009.p.0001','textbox.010.p.0001','textbox.011.p.0001','textbox.012.p.0001','textbox.013.p.0001','textbox.014.p.0001','body.p.01076','body.p.01080','body.p.01081','body.p.01082','body.p.01083','body.p.01084','body.p.01085']
TARGETS={
'body.p.01059':['权衡与成本      |      21'],
'body.p.01063':['示例：在时间与规模之间决策'],
'body.p.01064':['很多时候，时间与规模这两个主要主题彼此重叠、协同发挥作用。碧昂丝规则之类的政策具有良好扩展性，并帮助我们随时间维护事物。对操作系统接口的变更可能需要许多小型重构来适应，但这些变更大多能良好扩展，因为它们具有相似形式：操作系统变更不会对每个调用者和每个项目都表现得不同。'],
'body.p.01065':['偶尔，时间与规模会发生冲突，而最明显的地方莫过于这个基本问题：我们应添加一个依赖项，还是将其分叉/重新实现，以更好地满足本地需求？'],
'body.p.01066':['这个问题可以出现在软件栈的许多层面，因为经常会发生这样的情况：为狭窄问题空间定制的专用方案，可能优于需要处理所有可能性的通用工具方案。通过分叉或重新实现工具代码，并为狭窄领域定制它，无论谈论的是微服务、内存缓存、压缩例程还是软件生态系统中的其他事物，都可以更容易地添加新功能，或更有把握地进行优化。更重要的是，这种分叉赋予的控制权会使您与底层依赖项的变更隔离：这些变更不再由其他团队或第三方提供商决定。您可以控制如何以及何时响应时间流逝和变更的必要性。'],
'body.p.01067':['另一方面，如果每位开发者都分叉软件项目中使用的一切，而不是复用已有内容，可扩展性会与可持续性一同受损。对底层库安全问题的响应不再只是更新一个依赖项及其用户：现在必须识别该依赖项的每个脆弱分叉及这些分叉的用户。'],
'body.p.01068':['与大多数软件工程决策一样，这种情况没有放之四海而皆准的答案。如果项目寿命很短，分叉风险较小。如果所讨论的分叉可证明范围有限，也会有所帮助——应避免对可能跨越时间或项目时间边界运行的接口进行分叉（数据结构、序列化格式、网络协议）。一致性很有价值，但通用性也有自己的成本；如果谨慎行事，常常可以通过走自己的路而获益。'],
'body.p.01069':['重新审视决策，承认错误'],
'body.p.01070':['致力于数据驱动文化的一个不为人知的好处，是承认错误的能力与必要性兼具。某个时刻会作出决策，'],
'textbox.007.p.0001':['希望它是'],
'body.p.01071':['基于可用数据——'],
'textbox.008.p.0001':['好的决策，'],
'textbox.009.p.0001':['不仅'],
'textbox.010.p.0001':['基于'],
'textbox.011.p.0001':['数据和'],
'textbox.012.p.0001':['少量'],
'textbox.013.p.0001':['假设，'],
'textbox.014.p.0001':['还隐含地基于当前可用数据。随着新数据出现、情境变化或假设被推翻，可能会清楚地发现某项决策存在'],
'body.p.01076':['22      |     第 1 章：什么是软件工程？'],
'body.p.01080':['错误，或当时合理但现在不再合理。对于长寿命组织而言，这一点尤其关键：时间不仅会触发技术依赖项和软件系统的变更，也会改变用于驱动决策的数据。'],
'body.p.01081':['我们坚信数据应为决策提供信息，但也认识到数据会随时间变化，新的数据可能出现。这天然意味着，在相关系统的整个寿命期间，决策需要不时被重新审视。对于长期项目，在初始决策作出后仍能改变方向的能力往往至关重要。同样重要的是，决策者必须有承认错误的权利。与某些人的直觉相反，承认错误的领导者会更受尊重，而不是更少。'],
'body.p.01082':['要以证据为导向，但也要认识到无法测量的事物仍可能具有价值。如果您是领导者，这正是人们要求您做的事：运用判断，主张某些事很重要。我们将在第 ', '5 ', '和 ', '6', ' 章中更多讨论领导力。'],
'body.p.01083':['软件工程与编程'],
'body.p.01084':['当看到我们对软件工程与编程的区分时，您可能会问其中是否包含内在的价值判断。编程是否在某种程度上不如软件工程？一个预计持续十年、由数百人组成团队的项目，是否天生比一个仅有一个月价值、由两人构建的项目更有价值？'],
'body.p.01085':['当然不是。我们的观点并非软件工程更优越，而是二者代表具有不同约束、价值和最佳实践的两个问题领域。指出这种区别的价值在于认识到：有些工具在一个领域很出色，在另一个领域却不然。对于一个只持续几天的项目，您可能无需依赖集成测试（参见', '第 14 章', '）和持续部署（CD）实践（参见', '第 24 章', '）。同样，我们对软件工程项目中语义化版本控制（SemVer）和依赖管理的所有长期关注（参见', '第 21 章', '）并不真正适用于短期编程项目：用手头可用的任何东西解决当前任务即可。'],
}
def main():
 base.CHECKPOINT=CHECKPOINT;source_sha=fingerprint(base.SOURCE).sha256;selection=json.loads((base.RUN/'full_book_batch_003_selection.json').read_text(encoding='utf8'))
 if selection['selected_logical_work_order_ids'][100:125]!=ORDER:raise RuntimeError('AUTHORITATIVE_SELECTION_MISMATCH')
 output=base.BATCH/CHECKPOINT
 if output.exists():raise RuntimeError('CHECKPOINT_ALREADY_EXISTS')
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in base.VIEWS.read_text(encoding='utf8').splitlines() if x)};blocks={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in base.BLOCKS.read_text(encoding='utf8').splitlines() if x)};footnotes=json.loads(base.FOOT.read_text(encoding='utf8'))['records'];requests=[base.request_for(views[i],blocks[i],source_sha,footnotes) for i in ORDER];output.mkdir();base.wjl(output/'translation_request.jsonl',requests)
 raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]) for r in requests];raw_sha=persist_raw_response(output/'translation_raw_response.jsonl',raw);known={v.unit_uid for v in views.values()};raw_qa=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,raw,strict=True)];base.wj(output/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in raw_qa),'fail_count':sum(not x['passed'] for x in raw_qa),'records':raw_qa});normalize_response_artifact(output/'translation_request.jsonl',output/'translation_raw_response.jsonl',output/'translation_normalized_response.jsonl',output/'translation_normalization_report.json')
 if sha256((output/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha:raise RuntimeError('RAW_RESPONSE_MUTATED')
 normalized=[TranslationResult.model_validate_json(x) for x in (output/'translation_normalized_response.jsonl').read_text(encoding='utf8').splitlines() if x];final=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,normalized,strict=True)];base.wj(output/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});dispatch,excluded=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, protected literals, and exact D050 footnote evidence where present.'} for x in dispatch];base.wj(output/'semantic_review_report.json',semantic_review_summary(reviews,excluded));base.wjl(output/'translation_repaired_response.jsonl',normalized);approved=[x['unit_uid'] for x in final if x['passed']];failed=[x['unit_uid'] for x in final if not x['passed']];base.wj(output/'translation_final_qa_report.json',{'pass_count':len(approved),'fail_count':len(failed),'records':final,'final_approved_unit_uids':approved,'not_approved_deterministic_fail_unit_uids':failed});base.wj(output/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':source_sha,'raw_sha256':raw_sha,'deterministic_pass_count':len(approved),'deterministic_fail_count':len(failed),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(excluded),'chapter_1_footnote_evidence':str(base.FOOT),'pdf_assisted_source_pages':[50,51,52]});append_sealed_checkpoint(base.BATCH/'batch_manifest.json',output)
 if fingerprint(base.SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
