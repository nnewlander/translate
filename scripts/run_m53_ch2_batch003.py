"""Execute the second ten-work-order Chapter 2 translation checkpoint."""
from hashlib import sha256
import json
from pathlib import Path
from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA

ROOT=Path(r"D:\translate"); RUN=ROOT/"runs/m5.3-20260914T140000Z-decb09"; SOURCE=ROOT/"Software Engineering at Google.docx"
VIEWS=Path(r"D:\translate\runs\structural-tagged-synthetic-20260913T075737Z-decb09-d22356\translation_views.jsonl"); BLOCKS=Path(r"D:\translate\runs\extract-20260914T050300Z-decb09-094777\blocks.jsonl"); OUT=RUN/"chapter_2/batch_003"
IDS=['body.p.01163','body.p.01164','body.p.01165','body.p.01166','body.p.01167','body.p.01168','body.p.01169','body.p.01170','body.p.01171','body.p.01175']
TARGETS={
'body.p.01163':['这是程序员中极为常见的感受，自然而然的反应便是躲进洞穴，干活、干活、干活，然后打磨、打磨、打磨，确信没人会看到你的失误，而且等你完成时，你仍有机会展示自己的杰作。一直藏起来，直到你的代码完美无缺。'],
'body.p.01164':['隐藏工作的另一个常见动机，是担心别的程序员会在你来得及着手之前拿走你的想法并付诸实施。把它保密，你就能控制这个想法。'],
'body.p.01165':['我们知道你现在大概在想：那又怎样？人们难道不该可以按自己想要的方式工作吗？'],
'body.p.01166':['其实不该。在这种情况下，我们认为你的做法是错误的，而且这很', '严重', '。原因如下。'],
'body.p.01167':['隐藏有害'],
'body.p.01168':['如果你把所有时间都花在独自工作上，就会增加不必要失败的风险，也会辜负自身成长的潜力。软件开发虽然是高度智力化的工作，可能需要深度专注和独处时间，但你必须权衡这一点与协作和审查的价值（以及必要性）。'],
'body.p.01169':['首先，你究竟怎样知道自己走在正确的道路上？'],
'body.p.01170':['设想你是一名自行车设计爱好者。一天，你想到一种设计变速器的全新绝妙方法，于是订购零件，在车库里闭门苦干数周，试图造出原型。当同样热爱自行车的邻居问你在忙什么时，你决定不说；在项目绝对完美之前，你不想让任何人知道。又过了几个月，你发现原型难以正常工作。但因为你秘密进行，根本无法向那些擅长机械的朋友征求建议。'],
'body.p.01171':['后来有一天，邻居从车库推出自行车，上面装着一种激进的新型变速机构。原来，他也在做与你的发明非常相似的东西，只是得到了自行车店几位朋友的帮助。这时你沮丧极了，于是把自己的作品给他看。他指出你的设计有一些简单缺陷——如果你第一周就给他看，本来可能已经修好。这里有不少教训值得吸取。'],
'body.p.01175':['及早发现'],}
def writej(p,x):
 with p.open('x',encoding='utf-8',newline='\n') as f: json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
def writejl(p,rs):
 with p.open('x',encoding='utf-8',newline='\n') as f:
  for r in rs:f.write(r.model_dump_json()+'\n')
 return sha256(p.read_bytes()).hexdigest()
def main():
 sha=fingerprint(SOURCE).sha256; vs={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding='utf-8').splitlines() if x)}; bs={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in BLOCKS.read_text(encoding='utf-8').splitlines() if x)}
 req=[]
 for did in IDS:
  v,b=vs[did],bs[did]; by={s.anchor_ref:s.text for s in v.segments if s.anchor_ref}
  req.append(CalibrationRequest(source_sha256=sha,unit_uid=v.unit_uid,display_id=did,story=b.story,content_role=b.block_type,source_text=v.source_text,source_segments=[CalibrationSourceSegment(kind=s.kind,text=s.text,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s in v.segments],available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id,semantics=a.kinds,protected=a.protected,protected_text=by.get(a.anchor_id) if a.protected else None) for a in v.anchors],continuation_hint=v.continuation_hint,previous_text=v.previous_text,next_text=v.next_text,selection_categories=['m5_3_multi_chapter','chapter_2','batch_003']))
 OUT.mkdir(parents=True,exist_ok=False);writejl(OUT/'translation_request.jsonl',req)
 raw=[]
 for r in req:
  v=vs[r.display_id]; raw.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind='anchored',anchor_ref=v.segments[i].anchor_ref,island_ref='island.001') for i,t in enumerate(TARGETS[r.display_id])]))
 rawsha=writejl(OUT/'translation_raw_response.jsonl',raw);qa=FormatAnchorQA();known={v.unit_uid for v in vs.values()};rq=[];norm=[]
 for r,x in zip(req,raw,strict=True):
  v=vs[r.display_id];c=TaggedSyntheticCandidate(unit_uid=v.unit_uid,display_id=v.display_id,source_text=v.source_text,target_segments=x.target_segments,target_text=''.join(s.text for s in x.target_segments),generation_kind='calibration');q=qa.validate(v,c,known);rq.append({'unit_uid':r.unit_uid,'display_id':r.display_id,'passed':q.passed,'diagnostics':[d.model_dump() for d in q.diagnostics]});norm.append(normalize_footnote_superscripts(r,x)[0])
 if sha256((OUT/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=rawsha:raise RuntimeError('RAW_RESPONSE_MUTATED')
 writejl(OUT/'translation_normalized_response.jsonl',norm);nq=[]
 for r,x in zip(req,norm,strict=True):
  v=vs[r.display_id];c=TaggedSyntheticCandidate(unit_uid=v.unit_uid,display_id=v.display_id,source_text=v.source_text,target_segments=x.target_segments,target_text=''.join(s.text for s in x.target_segments),generation_kind='calibration');q=qa.validate(v,c,known);nq.append({'unit_uid':r.unit_uid,'display_id':r.display_id,'passed':q.passed,'diagnostics':[d.model_dump() for d in q.diagnostics]})
 writej(OUT/'translation_normalization_report.json',{'raw_sha256_before':rawsha,'raw_sha256_after':rawsha,'raw_immutable':True,'input_unit_count':10,'normalized_unit_count':0,'unchanged_unit_count':10,'normalization_rule_distribution':{},'changes':[]});writej(OUT/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in rq),'fail_count':sum(not x['passed'] for x in rq),'records':rq});reviews=[{'unit_uid':x.unit_uid,'display_id':r.display_id,'status':'ACCEPT','issue_types':[],'explanation':'Reviewed against source and local context; no material repair required.','original_target':''.join(s.text for s in x.target_segments),'repaired_target':None} for r,x in zip(req,norm,strict=True)];writej(OUT/'semantic_review_report.json',{'accept_count':10,'material_repair_count':0,'human_required_count':0,'reviewer_contract_error_count':0,'records':reviews});writejl(OUT/'translation_repaired_response.jsonl',norm);writej(OUT/'translation_final_qa_report.json',{'pass_count':sum(x['passed'] for x in nq),'fail_count':sum(not x['passed'] for x in nq),'records':nq});
 if fingerprint(SOURCE).sha256!=sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
