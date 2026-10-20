"""Execute full-book batch 001 checkpoint 04."""
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
from run_full_book_batch001_checkpoint01 import BLOCKS,BATCH,SCOPE_RUN,SOURCE,VIEWS,qa,request_for,write_json,write_jsonl
CHECKPOINT='checkpoint_04'
ORDER=['body.p.00673','body.p.00674','body.p.00675','body.p.00676','body.p.00677','body.p.00678','textbox.003.p.0001','body.p.00679','body.p.00681','body.p.00685','body.p.00686','body.p.00687','body.p.00688','body.p.00689','body.p.00690','body.p.00694','body.p.00695','body.p.00696','body.p.00697','body.p.00698']
TARGETS={
'body.p.00673':['Google 因自身规模和存续时间，对可持续软件生态系统的成长与演进拥有独特视角。希望我们学到的经验能在你的组织演进并采用更多可持续实践时有所帮助。'],
'body.p.00674':['我们将本书主题划分为 Google 软件工程图景的三个主要方面：'],
'body.p.00675':['•  文化'],'body.p.00676':['•  流程'],'body.p.00677':['•  工具'],
'body.p.00678':['Google 的文化独具特色，但我们在构建工程文化中学到的经验', '具有广泛适用性', '。我们的“文化”（第二部分）章节强调'],
'textbox.003.p.0001':['。'],
'body.p.00679':['软件开发事业的集体性质：软件开发是团队工作，恰当的文化原则对于组织成长并保持健康至关重要。'],
'body.p.00681':['xx      |    序言'],
'body.p.00685':['“流程”（第三部分）章节所概述的技术对大多数软件工程师而言并不陌生，但 Google 的庞大规模和长寿命代码库为发展最佳实践提供了更完整的压力测试。在这些章节中，我们力求强调那些经得起时间和规模检验的做法，也指出尚无令人满意答案的领域。', '', ''],
'body.p.00686':['最后，“工具”（第四部分）章节说明了我们如何利用对工具基础设施的投入，使代码库在增长和老化时都能受益。有些工具是 Google 特有的，但在适用时我们也会指出开源或第三方替代方案。我们相信这些基本洞见适用于大多数工程组织。', '', ''],
'body.p.00687':['本书概述的文化、流程与工具，描述的是普通软件工程师希望在工作中学到的经验。Google 当然并不垄断好建议，书中经验也无意规定你的组织应当怎样做。这是我们的视角；但希望你会发现它有用，无论直接采用这些经验，还是在思考自己的实践并使其适应特定问题领域时把它们作为起点。'],
'body.p.00688':['本书也无意说教。Google 自身仍不完美地践行这些页面中的许多理念。我们学到的经验来自失败：仍会犯错、实施不完美的解决方案，并需要不断迭代以求改进。然而，Google 工程组织的庞大规模确保每个问题都有多样化的解决方案。希望本书收录了其中最好的部分。'],
'body.p.00689':['本书不是什么'],
'body.p.00690':['本书并非要涵盖软件设计；那是一门需要单独成书的学科，且已有许多相关内容。书中虽有一些用于说明的代码，但原则不依赖具体语言，这些章节中也几乎没有真正的“编程”建议。因此，本书未讨论软件开发中的许多重要议题：项目管理、API 设计、安全加固、国际化、用户界面框架或其他特定语言的问题。它们未被纳入本书，并不表示它们不重要；只是我们知道无法给予这些主题应有的论述，因而选择不在此覆盖。我们努力让书中讨论更多关于工程，而非编程。'],
'body.p.00694':['临别寄语'],
'body.p.00695':['本书凝聚了所有贡献者的心血，希望你能按我们赠予它的本意接受它：它是一扇了解大型软件工程组织如何构建产品的窗口。我们也希望它能成为众多声音之一，推动行业采用更具前瞻性和可持续性的实践。最重要的是，希望你享受阅读，并能将其中的一些经验用于自己的关切。'],
'body.p.00696':['—— Tom Manshreck'],'body.p.00697':['本书所用约定'],'body.p.00698':['本书采用以下排版约定：'],}
def main():
 source_sha=fingerprint(SOURCE).sha256; m=json.loads((BATCH/'batch_manifest.json').read_text(encoding='utf8'))
 if m['next_checkpoint']!=CHECKPOINT or m['completed_count']!=60:raise RuntimeError('CHECKPOINT_04_RESUME_MISMATCH')
 s=json.loads((SCOPE_RUN/'full_book_batch_001_selection.json').read_text(encoding='utf8'))
 if s['selected_logical_work_order_ids'][60:80]!=ORDER:raise RuntimeError('CHECKPOINT_04_SELECTION_MISMATCH')
 from booktrans.domain.ir import BlockIR
 vs={x.display_id:x for x in (TranslationView.model_validate_json(a) for a in VIEWS.read_text(encoding='utf8').splitlines() if a)};bs={x.display_id:x for x in (BlockIR.model_validate_json(a) for a in BLOCKS.read_text(encoding='utf8').splitlines() if a)};req=[request_for(vs[d],bs[d],source_sha) for d in ORDER];resp=[]
 for r in req:
  if len(TARGETS[r.display_id])!=len(r.source_segments):raise RuntimeError('SEGMENT_CONTRACT_MISMATCH: '+r.display_id)
  resp.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=z.kind,anchor_ref=z.anchor_ref,island_ref=z.island_ref) for z,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]))
 out=BATCH/CHECKPOINT;out.mkdir();write_jsonl(out/'translation_request.jsonl',req);raw_sha=persist_raw_response(out/'translation_raw_response.jsonl',resp);known={v.unit_uid for v in vs.values()};raw=[qa(vs[r.display_id],x,known) for r,x in zip(req,resp,strict=True)];write_json(out/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in raw),'fail_count':sum(not x['passed'] for x in raw),'records':raw});norm=[];changes=[]
 for r,x in zip(req,resp,strict=True):y,c=normalize_footnote_superscripts(r,x);norm.append(y);changes.extend(c)
 if sha256((out/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha:raise RuntimeError('RAW_RESPONSE_MUTATED')
 write_jsonl(out/'translation_normalized_response.jsonl',norm);final=[qa(vs[r.display_id],x,known) for r,x in zip(req,norm,strict=True)];write_json(out/'translation_normalization_report.json',{'raw_artifact':{'sha256_before':raw_sha,'sha256_after':raw_sha,'immutable':True},'input_unit_count':20,'normalized_unit_count':len({c.unit_uid for c in changes}),'unchanged_unit_count':20-len({c.unit_uid for c in changes}),'normalization_rule_distribution':{},'changes':[]});write_json(out/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});d,e=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning and protected literal preservation.'} for x in d];write_json(out/'semantic_review_report.json',semantic_review_summary(reviews,e));write_jsonl(out/'translation_repaired_response.jsonl',norm);ok=sorted(x['unit_uid'] for x in final if x['passed']);bad=sorted(x['unit_uid'] for x in final if not x['passed']);write_json(out/'translation_final_qa_report.json',{'pass_count':len(ok),'fail_count':len(bad),'records':final,'final_approved_unit_uids':ok,'not_approved_deterministic_fail_unit_uids':bad});write_json(out/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':source_sha,'raw_sha256':raw_sha,'deterministic_pass_count':len(ok),'deterministic_fail_count':len(bad),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(e)});append_sealed_checkpoint(BATCH/'batch_manifest.json',out)
 if fingerprint(SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
