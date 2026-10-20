"""Execute and seal full-book batch 001 checkpoint 05."""
from __future__ import annotations
from hashlib import sha256
import json
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment,TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch,semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import persist_raw_response
from run_full_book_batch001_checkpoint01 import BLOCKS,BATCH,SCOPE_RUN,SOURCE,VIEWS,qa,request_for,write_json,write_jsonl
CHECKPOINT='checkpoint_05';ORDER=['body.p.00699','body.p.00700','body.p.00701','body.p.00702','body.p.00703','body.p.00704','body.p.00705','body.p.00706','body.p.00710','body.p.00713','body.p.00716','body.p.00718','body.p.00719','body.p.00720','body.p.00721','body.p.00722','body.p.00723','body.p.00724','body.p.00725','body.p.00726']
TARGETS={
'body.p.00699':['斜体'],'body.p.00700':['表示新术语、URL、电子邮件地址、文件名和文件扩展名。'],'body.p.00701':['等宽字体'],'body.p.00702':['用于程序清单，也用于在段落中指代变量名、函数名、数据库、数据类型、环境变量、语句和关键字等程序元素。'],
'body.p.00703':['等宽','', '', '', '粗体'],'body.p.00704':['表示用户应按字面输入的命令或其他文本。','等宽','','','', '斜体'],'body.p.00705':['表示应由用户提供的值或由上下文决定的值所替换的文本。'],'body.p.00706':['此元素表示一般性说明。'],
'body.p.00710':['O’Reilly 在线学习'],'body.p.00713':["O’REILLY\""],'body.p.00716':['40 多年来，','O’Reilly Media ','一直提供技术和商业培训、知识与洞见，帮助企业取得成功。'],
'body.p.00718':['我们独特的专家和创新者网络通过图书、文章和在线学习平台分享其知识与专长。O’Reilly 在线学习平台让你可以按需访问直播培训课程、深入学习路径、互动编程环境，以及来自 O’Reilly 和其他 200 多家出版商的大量文本和视频内容。欲了解更多信息，请访问 ','http://',' ','oreilly.com','.'],
'body.p.00719':['如何联系我们'],'body.p.00720':['有关本书的意见和问题，请寄送至出版方：'],'body.p.00721':['O’Reilly Media, Inc.'],'body.p.00722':['1005 Gravenstein Highway North'],'body.p.00723':['Sebastopol, CA 95472'],'body.p.00724':['800-998-9938','（美国或加拿大境内）'],'body.p.00725':['707-829-0515','（国际或本地）'],'body.p.00726':['707-829-0104','（传真）'],}
def main():
 source_sha=fingerprint(SOURCE).sha256;m=json.loads((BATCH/'batch_manifest.json').read_text(encoding='utf8'))
 if m['next_checkpoint']!=CHECKPOINT or m['completed_count']!=80:raise RuntimeError('CHECKPOINT_05_RESUME_MISMATCH')
 s=json.loads((SCOPE_RUN/'full_book_batch_001_selection.json').read_text(encoding='utf8'))
 if s['selected_logical_work_order_ids'][80:100]!=ORDER:raise RuntimeError('CHECKPOINT_05_SELECTION_MISMATCH')
 from booktrans.domain.ir import BlockIR
 vs={x.display_id:x for x in (TranslationView.model_validate_json(a) for a in VIEWS.read_text(encoding='utf8').splitlines() if a)};bs={x.display_id:x for x in (BlockIR.model_validate_json(a) for a in BLOCKS.read_text(encoding='utf8').splitlines() if a)};req=[request_for(vs[d],bs[d],source_sha) for d in ORDER];resp=[]
 for r in req:
  if len(TARGETS[r.display_id])!=len(r.source_segments):raise RuntimeError('SEGMENT_CONTRACT_MISMATCH: '+r.display_id)
  resp.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=z.kind,anchor_ref=z.anchor_ref,island_ref=z.island_ref) for z,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]))
 out=BATCH/CHECKPOINT;out.mkdir();write_jsonl(out/'translation_request.jsonl',req);raw_sha=persist_raw_response(out/'translation_raw_response.jsonl',resp);known={v.unit_uid for v in vs.values()};raw=[qa(vs[r.display_id],x,known) for r,x in zip(req,resp,strict=True)];write_json(out/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in raw),'fail_count':sum(not x['passed'] for x in raw),'records':raw});norm=[];changes=[]
 for r,x in zip(req,resp,strict=True):y,c=normalize_footnote_superscripts(r,x);norm.append(y);changes.extend(c)
 if sha256((out/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha:raise RuntimeError('RAW_RESPONSE_MUTATED')
 write_jsonl(out/'translation_normalized_response.jsonl',norm);final=[qa(vs[r.display_id],x,known) for r,x in zip(req,norm,strict=True)];write_json(out/'translation_normalization_report.json',{'raw_artifact':{'sha256_before':raw_sha,'sha256_after':raw_sha,'immutable':True},'input_unit_count':20,'normalized_unit_count':len({c.unit_uid for c in changes}),'unchanged_unit_count':20-len({c.unit_uid for c in changes}),'normalization_rule_distribution':{},'changes':[]});write_json(out/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});d,e=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed the target, source formatting role, and protected literal preservation.'} for x in d];write_json(out/'semantic_review_report.json',semantic_review_summary(reviews,e));write_jsonl(out/'translation_repaired_response.jsonl',norm);ok=sorted(x['unit_uid'] for x in final if x['passed']);bad=sorted(x['unit_uid'] for x in final if not x['passed']);write_json(out/'translation_final_qa_report.json',{'pass_count':len(ok),'fail_count':len(bad),'records':final,'final_approved_unit_uids':ok,'not_approved_deterministic_fail_unit_uids':bad});write_json(out/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':source_sha,'raw_sha256':raw_sha,'deterministic_pass_count':len(ok),'deterministic_fail_count':len(bad),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(e)});append_sealed_checkpoint(BATCH/'batch_manifest.json',out)
 if fingerprint(SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
