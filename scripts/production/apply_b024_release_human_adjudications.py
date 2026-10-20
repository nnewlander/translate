"""Apply the owner-supplied B024 human release decisions; no provider calls."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.production_worker.human_adjudication import HumanAllowedRepair, HumanApprovedPatch, apply_human_adjudication_to_production_checkpoint, build_human_adjudication
from booktrans.production_worker.pipeline import enrich_request
from booktrans.production_worker.production_checkpoint import _production_review_group, _resolve_checkpoint_authoritative_candidate
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import load_raw_by_uid
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs

RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09"); BATCH="FULL_BOOK_BATCH_024"; DIR=RUN/"full_book_batch_024"
TARGETS={
"body.p.07518":["与其他变更一样，Rosie 生成的变更也应经过标准的代码审查流程。在实践中，我们发现本地所有者并不总是像对待常规变更那样严格对待 LSC——他们过于信任生成 LSC 的工程师。理想情况下，这些变更应像其他变更一样接受审查，但在实践中，本地项目所有者已经信任基础设施团队到这种程度：这些变更往往只得到粗略的审查。我们已改为仅将那些需要本地所有者提供上下文审查（而不仅仅是审批权限）的变更发送给本地所有者。所有其他变更都可以交给“全局审批人”：即有权批准代码库中的","任何","变更的人。"],
"body.p.07531":["不同的 LSC 对“完成”有不同的定义，范围可以从彻底移除旧系统，到仅迁移高价值引用并让旧引用自然消失。","16","在几乎所有情况下，建立一个系统来防止再次引入大规模变更努力移除的符号或系统，都非常重要。在 Google，我们使用 Tricorder 框架（见第","20","章和第","19","章），在代码审查时标记工程师何时新增对已弃用对象的使用；事实证明，这能有效防止回退。关于整个弃用流程，我们在","第15章","中有更详细的讨论。"],
"body.p.07614":["实验","和","功能开关","是极其强大的反馈回路。它们将变更隔离在可在生产环境中动态切换的模块化组件内，从而降低部署风险。大量依赖功能开关进行保护，是持续交付的一种常见范式；我们将在","第24章","中进一步讨论这一点。"],
"body.p.07628":["持续","构建","（CB）会集成 head 上的最新代码变更","4","，并执行自动构建和测试。由于 CB 在构建代码的同时也运行测试，因此“破坏构建”或“使构建失败”既包括测试失败，也包括编译失败。"],
"body.p.07631":["持续交付（CD，详见","第24章","）的第一步是","发布自动化","，它会持续将 head 上的最新代码和配置组装成发布候选版本。在 Google，大多数团队会在 green head 上切出这些候选版本，而不是在 true head 上。"],
"body.p.07643":["相反，我们的意思是，你现有的任何静态配置","都","应作为发布候选版本的一部分一起推进，以便与其对应的代码一起接受测试。请记住，生产环境中的很大一部分缺陷都由“低级”的配置问题造成，因此测试配置与测试代码同样重要（而且应让配置","与","实际使用它的同一份代码一起测试）。版本偏差往往会在发布候选版本的推进过程中被发现。当然，这有一个前提：静态配置已纳入版本控制——在 Google，静态配置和代码一起受版本控制，因此也经过相同的代码审查流程。"],
"body.p.07660":["让我们看看，在对代码变更的整个生命周期应用持续测试（CT）时，CB 和 CD 是如何融入其中的，如","图23-2","所示。"],
"body.p.07663":["向右的箭头展示了单个代码变更从本地开发到生产环境的推进过程。同样，我们在持续集成（CI）中的一个关键目标，是确定在这一推进过程中","测试什么、","以及","何时","进行测试。在本章后面，我们将介绍不同的测试阶段，并讨论预提交与后提交阶段、以及 RC 及后续阶段分别应该测试什么。我们将看到，随着向右推进，代码变更会接受范围逐步扩大的自动化测试。"],
"body.p.07734":["•","预提交优化","，包括：考虑到我们已经说明的潜在问题，","哪些","测试应在预提交阶段运行，以及","如何","运行这些测试。"],
"body.p.07737":["此外，还有","失败管理这一挑战——","即测试失败时该怎么办。较小的问题通常可以很快修复，但许多团队发现，只要涉及大型端到端测试，就很难让测试套件始终保持全绿。这类测试天然容易出错或出现不稳定（flaky）现象，而且难以调试；因此需要一种机制，能够暂时禁用并跟踪这些测试，以便发布继续进行。Google 常见的做法是使用由值班工程师或发布工程师提交的 bug“热点列表”（hotlists），并将其分派给相应团队处理。更理想的是自动生成并提交这些 bug——我们的一些大型产品，如 Google Web Server（GWS）和 Google Assistant，就采用这种方式。应持续维护这些热点列表，确保所有阻塞发布的 bug 都能立即修复。非发布阻塞问题也应该修复；它们虽然没那么紧急，但同样应排定优先级，以保持测试套件的有效性，避免它逐渐变成一堆被禁用的陈旧测试。很多时候，端到端测试失败暴露的问题其实出在测试本身，而不是代码。"],
}
PRESERVE="body.p.07652"
def main():
 packet=json.loads((DIR/'human_release_packet_v3.json').read_text(encoding='utf-8')); items={x['work_order_id']:x for x in packet['items']}
 manifest=json.loads((DIR/'release_debt_manifest_v3.json').read_text(encoding='utf-8')); checkpoints={x['logical_work_order_id']:x['checkpoint'] for x in manifest['entries']}
 report=[]
 for display_id in [*TARGETS,PRESERVE]:
  item=items[display_id]; cp=checkpoints[display_id]; loaded=frozen_batch_checkpoint_inputs(RUN,batch_label=BATCH,checkpoint=cp); directory=DIR/cp; raws=load_raw_by_uid(directory/'translation_raw_response.jsonl'); req=next(x for x in loaded['requests'] if x.display_id==display_id); head=_resolve_checkpoint_authoritative_candidate(directory,display_id,raws[req.unit_uid]);
  if item['binding']['batch']!=BATCH or item['binding']['checkpoint']!=cp or item['binding']['unit_uid']!=req.unit_uid or item['binding']['source_sha256']!=req.source_sha256 or item['binding']['current_authoritative_candidate_sha256']!=head.candidate_sha256: raise RuntimeError(f'PACKET_BINDING_MISMATCH:{display_id}')
  overrides={display_id:head.candidate,req.unit_uid:head.candidate}; group=_production_review_group(req,loaded=loaded,run=RUN,checkpoint=cp,existing_raw=raws,candidate_overrides=overrides)
  if group is None or item['canonical_context']['semantic_review_group_id']!=group.semantic_review_group_id or item['canonical_context']['group_hash']!=group.group_hash: raise RuntimeError(f'PACKET_CONTEXT_MISMATCH:{display_id}')
  bible=loaded['bible'] if Path(loaded['bible']).is_file() else None; members=[]
  for member in group.members:
   r=next(x for x in loaded['requests'] if x.display_id==member.display_id); h=_resolve_checkpoint_authoritative_candidate(directory,member.display_id,raws[r.unit_uid]); members.append((enrich_request(r,translation_bible=bible),h.candidate))
  provider=next(x for x in members if x[0].request.display_id==display_id)[0]; allowed=[HumanAllowedRepair(logical_work_order_id=display_id,unit_uid=req.unit_uid,allowed_segment_indices=list(range(len(head.candidate.target_segments))),source_fingerprint=provider.request_binding_sha256)]
  texts=TARGETS.get(display_id) or [x.text for x in head.candidate.target_segments]
  if len(texts)!=len(head.candidate.target_segments): raise RuntimeError(f'SEGMENT_COUNT_MISMATCH:{display_id}')
  patches=[HumanApprovedPatch(logical_work_order_id=display_id,unit_uid=req.unit_uid,index=i,target_text=text) for i,text in enumerate(texts)]
  adjudication=build_human_adjudication(group,decision='HUMAN_APPROVED',reason='Owner release-boundary semantic adjudication.',allowed_repairs=allowed,source_hashes={x[0].request.display_id:x[0].request_binding_sha256 for x in members},original_candidate_hashes={x[0].request.display_id:__import__('booktrans.production_worker.candidate_authority',fromlist=['candidate_sha']).candidate_sha(raws[x[0].request.unit_uid]) for x in members},approved_patches=patches)
  result=apply_human_adjudication_to_production_checkpoint(checkpoint_dir=directory,production_run=RUN,requests=loaded['requests'],views=loaded['views'],known_unit_ids=loaded['known_unit_ids'],qa_fn=harness_qa_record,adjudication=adjudication,group=group,members=members,translation_bible=bible,triage_classification='TRUE_SEMANTIC_HUMAN',batch_label=BATCH,checkpoint=cp)
  report.append({'id':display_id,'approval_mode':'PRESERVE_CURRENT' if display_id==PRESERVE else 'MUTATION','result':result})
 print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__': main()
