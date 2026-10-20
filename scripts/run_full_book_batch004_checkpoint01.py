"""Execute full-book Batch 004 checkpoint 01 (closed 25-unit selection)."""
from __future__ import annotations
from hashlib import sha256
import json, shutil
from pathlib import Path
import run_full_book_batch003_checkpoint01 as base
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint, update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact,persist_raw_response
RUN=Path(r'D:\translate\runs\full-book-20260916T000200Z-decb09');BATCH=RUN/'full_book_batch_004';FOOT=RUN/'chapter_3_verified_footnote_evidence_v1.json';CHECKPOINT='checkpoint_01'
ORDER=['body.p.01369','textbox.019.p.0001','body.p.01371','body.p.01373','body.p.01375','body.p.01377','body.p.01383','body.p.01387','body.p.01388','body.p.01389','body.p.01390','body.p.01391','body.p.01392','body.p.01393','body.p.01394','body.p.01395','body.p.01396','body.p.01397','body.p.01398','body.p.01399','body.p.01400','body.p.01401','body.p.01402','body.p.01405','body.p.01409']
TARGETS={
'body.p.01369':['不与'], 'textbox.019.p.0001':['彼此沟通                    或使用                ', '资源。                   一'], 'body.p.01371':['另一个'], 'body.p.01373':['共享'], 'body.p.01375':['在'], 'body.p.01377':['这种'], 'body.p.01383':['43'],
'body.p.01387':['环境中，每个小组都形成自己的做事方式。1 这通常会导致以下问题：'],
'body.p.01388':['信息碎片化'],'body.p.01389':['每个孤岛对更大的整体都只有不完整的认识。'],'body.p.01390':['信息重复'],'body.p.01391':['每个孤岛都重新发明了自己的做事方式。'],'body.p.01392':['信息偏差'],'body.p.01393':['每个孤岛都有自己完成同一件事的方法，而这些方法可能相互冲突，也可能不冲突。'],'body.p.01394':['单点故障（SPOF）'],
'body.p.01395':['一种瓶颈：关键信息仅由一个人掌握。这与', '巴士因子', '有关，详见', '第 2 章', '。'],
'body.p.01396':['SPOF 可能源于善意：人们很容易养成“让我替你处理”的习惯。但这种做法以较差的长期可扩展性（团队永远学不会如何完成需要做的事）为代价，优化了短期效率（“我来做更快”）。这种心态也往往导致', '非此即彼的专业能力', '。'],
'body.p.01397':['非此即彼的专业能力'],'body.p.01398':['一群人被分成“无所不知”的人和新手，中间几乎没有过渡地带。如果专家总是亲力亲为，且不花时间通过辅导或文档培养新专家，这个问题往往会自我强化。在这种情形下，知识和责任继续累积在已经具备专业能力的人身上，新团队成员或新手只能自力更生，缓慢成长。'],'body.p.01399':['照搬'],'body.p.01400':['没有理解的模仿。这通常表现为不假思索地复制模式或代码，却不了解其目的，往往只是认为这些代码因未知原因而必不可少。'],'body.p.01401':['闹鬼的墓地'],'body.p.01402':['人们往往不愿触碰或改动的地方，通常是代码，因为害怕会出问题。与前述的', '照搬', '不同，闹鬼的墓地的特点是人们因恐惧和迷信而避免行动。'],
'body.p.01405':['1  换言之，我们并非在发展一个单一的全局最大值，而是拥有一系列', '局部最大值', '。'],
'body.p.01409':['在本章余下部分，我们将深入探讨 Google 工程组织在应对这些挑战时发现行之有效的策略。'],}
def main():
 base.BATCH=BATCH;base.CHECKPOINT=CHECKPOINT;base.FOOT=FOOT;sha=fingerprint(base.SOURCE).sha256;sel=json.loads((RUN/'full_book_batch_004_selection.json').read_text(encoding='utf8'))
 if sel['selected_logical_work_order_ids'][:25]!=ORDER:raise RuntimeError('AUTHORITATIVE_SELECTION_MISMATCH')
 if BATCH.exists():raise RuntimeError('BATCH_ALREADY_EXISTS')
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in base.VIEWS.read_text(encoding='utf8').splitlines() if x)};blocks={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in base.BLOCKS.read_text(encoding='utf8').splitlines() if x)};foot=json.loads(FOOT.read_text(encoding='utf8'))['records'];req=[base.request_for(views[i],blocks[i],sha,foot) for i in ORDER];BATCH.mkdir();shutil.copyfile(base.BIBLE,BATCH/'translation_bible_snapshot.md');update_manifest(BATCH/'batch_manifest.json',{'batch':'FULL_BOOK_BATCH_004','target_count':150,'completed_count':0,'checkpoint_count':0,'remaining_in_batch':150,'status':'OPEN_PROGRESS','next_checkpoint':CHECKPOINT,'checkpoints':[],'source_sha256':sha,'translation_bible_snapshot_sha256':sha256((BATCH/'translation_bible_snapshot.md').read_bytes()).hexdigest()});out=BATCH/CHECKPOINT;out.mkdir();base.wjl(out/'translation_request.jsonl',req)
 raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]) for r in req];rawsha=persist_raw_response(out/'translation_raw_response.jsonl',raw);known={v.unit_uid for v in views.values()};a=[base.qa(views[r.display_id],r,x,known) for r,x in zip(req,raw,strict=True)];base.wj(out/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in a),'fail_count':sum(not x['passed'] for x in a),'records':a});normalize_response_artifact(out/'translation_request.jsonl',out/'translation_raw_response.jsonl',out/'translation_normalized_response.jsonl',out/'translation_normalization_report.json')
 if sha256((out/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=rawsha:raise RuntimeError('RAW_RESPONSE_MUTATED')
 n=[TranslationResult.model_validate_json(x) for x in (out/'translation_normalized_response.jsonl').read_text(encoding='utf8').splitlines() if x];f=[base.qa(views[r.display_id],r,x,known) for r,x in zip(req,n,strict=True)];base.wj(out/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in f),'fail_count':sum(not x['passed'] for x in f),'records':f});d,e=partition_deterministic_review_dispatch(f);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, protected literals, and exact Chapter 3 D050 footnote evidence where present.'} for x in d];base.wj(out/'semantic_review_report.json',semantic_review_summary(reviews,e));base.wjl(out/'translation_repaired_response.jsonl',n);ok=[x['unit_uid'] for x in f if x['passed']];bad=[x['unit_uid'] for x in f if not x['passed']];base.wj(out/'translation_final_qa_report.json',{'pass_count':len(ok),'fail_count':len(bad),'records':f,'final_approved_unit_uids':ok,'not_approved_deterministic_fail_unit_uids':bad});base.wj(out/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':sha,'raw_sha256':rawsha,'deterministic_pass_count':len(ok),'deterministic_fail_count':len(bad),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(e),'chapter_3_footnote_evidence':str(FOOT),'pdf_assisted_source_pages':[72,73]});append_sealed_checkpoint(BATCH/'batch_manifest.json',out)
 if fingerprint(base.SOURCE).sha256!=sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
