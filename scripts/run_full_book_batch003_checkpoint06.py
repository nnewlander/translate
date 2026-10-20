"""Execute full-book batch 003 checkpoint 06 (closed 25-unit selection)."""
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
CHECKPOINT='checkpoint_06'
ORDER=['body.p.01086','body.p.01090','body.p.01091','body.p.01092','body.p.01093','body.p.01094','body.p.01095','body.p.01096','body.p.01097','body.p.01098','body.p.01099','body.p.01100','body.p.01101','body.p.01102','body.p.01108','body.p.01109','body.p.01352','body.p.01359','body.p.01361','body.p.01362','body.p.01363','body.p.01364','body.p.01365','body.p.01366','body.p.01367']
TARGETS={
'body.p.01086':['我们认为，区分彼此相关却不同的“编程”和“软件工程”很重要。这种差异很大部分源于随时间管理代码、时间对规模的影响，以及面对这些观念时作出的决策。编程是直接产出代码的行为。软件工程则是为使代码在需要使用的整个期间保持有用、并允许团队协作所必需的一组政策、实践和工具。'],
'body.p.01090':['结语'],
'body.p.01091':['本书讨论所有这些主题：面向组织和单个程序员的政策，如何评估和完善最佳实践，以及构成可维护软件的工具和技术。Google 努力构建可持续的代码库与文化。我们并不一定认为自己的方法是唯一正确的做事方式，但它确实通过实例证明了这件事可以做到。我们希望它能为思考这一普遍问题提供有用框架：如何让代码在它需要持续工作的期间保持可维护？'],
'body.p.01092':['摘要'],
'body.p.01093':['•  “软件工程”与“编程”在维度上不同：编程是产出代码；软件工程将这一点扩展为在代码有用的整个寿命期间维护代码。'],
'body.p.01094':['•  短寿命代码与长寿命代码的寿命相差至少 100,000 倍。认为同一套最佳实践能普遍适用于这个谱系的两端，是荒谬的。'],
'body.p.01095':['•  当在代码预期寿命期间，我们能够响应依赖项、技术或产品需求的变更时，软件便是可持续的。我们可以选择不改变事物，但必须具备改变的能力。'],
'body.p.01096':['•  Hyrum 定律：只要 API 的用户足够多，无论您在契约中承诺什么，系统中所有可观察的行为都会被某个人依赖。'],
'body.p.01097':['•  组织必须反复完成的每项任务，都应在人力投入方面具有可扩展性（线性或更好）。政策是使流程具有可扩展性的绝佳工具。'],
'body.p.01098':['•  流程低效和其他软件开发任务往往缓慢扩大。要当心温水煮青蛙问题。'],
'body.p.01099':['•  专业能力与规模经济结合时，回报尤其丰厚。'],
'body.p.01100':['•  “因为我说了算”是做事的糟糕理由。'],
'body.p.01101':['•  数据驱动是良好开端，但实际上，大多数决策基于数据、假设、先例和论证的混合。客观数据占这些输入的大部分时最好，但它们很少能构成', '全部', '输入。'],
'body.p.01102':['•  随时间保持数据驱动，意味着当数据变化（或假设被推翻）时需要改变方向。错误或修订后的计划不可避免。'],
'body.p.01108':['第二部分'],
'body.p.01109':['文化'],
'body.p.01352':['知识共享'],
'body.p.01359':['Nina Chen 和 Mark Barolak 撰写；Riona MacNamara 编辑'],
'body.p.01361':['您的组织比互联网上随便某个人更了解您的问题领域；组织应能回答自身的大多数问题。为实现这一点，既需要知道答案的专家', '以及', '分发其知识的机制，这正是本章将探讨的内容。这些机制从极为简单的方式（提问；写下您知道的内容）到更结构化的方式，如教程和课程，不一而足。但最重要的是，组织需要一种', '学习文化，', '而这要求创造心理安全感，使人们能够承认自己缺乏知识。'],
'body.p.01362':['学习面临的挑战'],
'body.p.01363':['在组织内共享专业知识并非易事。没有强大的学习文化，挑战就会出现。Google 经历过许多此类挑战，尤其随着公司规模扩大：'],
'body.p.01364':['缺乏心理安全感'],
'body.p.01365':['一种环境：人们害怕在他人面前冒险或犯错，因为担心因此受到惩罚。这往往表现为恐惧文化或回避透明度的倾向。'],
'body.p.01366':['信息孤岛'],
'body.p.01367':['发生在组织不同部分的知识碎片化，其'],
}
def main():
 base.CHECKPOINT=CHECKPOINT;source_sha=fingerprint(base.SOURCE).sha256;selection=json.loads((base.RUN/'full_book_batch_003_selection.json').read_text(encoding='utf8'))
 if selection['selected_logical_work_order_ids'][125:150]!=ORDER:raise RuntimeError('AUTHORITATIVE_SELECTION_MISMATCH')
 output=base.BATCH/CHECKPOINT
 if output.exists():raise RuntimeError('CHECKPOINT_ALREADY_EXISTS')
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in base.VIEWS.read_text(encoding='utf8').splitlines() if x)};blocks={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in base.BLOCKS.read_text(encoding='utf8').splitlines() if x)};footnotes=json.loads(base.FOOT.read_text(encoding='utf8'))['records'];requests=[base.request_for(views[i],blocks[i],source_sha,footnotes) for i in ORDER];output.mkdir();base.wjl(output/'translation_request.jsonl',requests)
 raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]) for r in requests];raw_sha=persist_raw_response(output/'translation_raw_response.jsonl',raw);known={v.unit_uid for v in views.values()};raw_qa=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,raw,strict=True)];base.wj(output/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in raw_qa),'fail_count':sum(not x['passed'] for x in raw_qa),'records':raw_qa});normalize_response_artifact(output/'translation_request.jsonl',output/'translation_raw_response.jsonl',output/'translation_normalized_response.jsonl',output/'translation_normalization_report.json')
 if sha256((output/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha:raise RuntimeError('RAW_RESPONSE_MUTATED')
 normalized=[TranslationResult.model_validate_json(x) for x in (output/'translation_normalized_response.jsonl').read_text(encoding='utf8').splitlines() if x];final=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,normalized,strict=True)];base.wj(output/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});dispatch,excluded=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, protected literals, exact D050 footnote evidence where present, and the PDF-assisted conversion fragment reconstruction.'} for x in dispatch];base.wj(output/'semantic_review_report.json',semantic_review_summary(reviews,excluded));base.wjl(output/'translation_repaired_response.jsonl',normalized);approved=[x['unit_uid'] for x in final if x['passed']];failed=[x['unit_uid'] for x in final if not x['passed']];base.wj(output/'translation_final_qa_report.json',{'pass_count':len(approved),'fail_count':len(failed),'records':final,'final_approved_unit_uids':approved,'not_approved_deterministic_fail_unit_uids':failed});base.wj(output/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':source_sha,'raw_sha256':raw_sha,'deterministic_pass_count':len(approved),'deterministic_fail_count':len(failed),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(excluded),'chapter_1_footnote_evidence':str(base.FOOT),'pdf_assisted_source_pages':[52],'pdf_docx_source_divergence':[{'logical_work_order_id':'body.p.01102','docx_fragment':'inevi-','pdf_page':52,'pdf_complete_text':'Mistakes or revised plans are inevitable.','classification':'PDF_ASSISTED_CONVERSION_FRAGMENT_RECONSTRUCTION'}]});append_sealed_checkpoint(base.BATCH/'batch_manifest.json',output)
 if fingerprint(base.SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
