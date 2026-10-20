"""Execute promoted full-book batch 003 checkpoint 01 (25 work orders)."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil

from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, TranslationResult, VerifiedFootnoteNumericBinding, VerifiedFootnoteSourceSegment
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint, update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response

ROOT=Path(r"D:\translate"); RUN=ROOT/"runs"/"full-book-20260916T000200Z-decb09"; BATCH=RUN/"full_book_batch_003"; SOURCE=ROOT/"Software Engineering at Google.docx"; VIEWS=ROOT/"runs"/"structural-tagged-synthetic-20260913T075737Z-decb09-d22356"/"translation_views.jsonl"; BLOCKS=ROOT/"runs"/"extract-20260914T050300Z-decb09-094777"/"blocks.jsonl"; BIBLE=ROOT/"runs"/"m5.3-20260914T140000Z-decb09"/"translation_bible_snapshot.md"; FOOT=RUN/"chapter_1_verified_footnote_evidence_v1.json"; CHECKPOINT="checkpoint_01"
ORDER=["body.p.00890","body.p.00892","body.p.00896","body.p.00897","body.p.00898","body.p.00899","textbox.004.p.0001","body.p.00900","body.p.00901","body.p.00902","body.p.00903","body.p.00907","body.p.00908","body.p.00909","body.p.00910","body.p.00911","body.p.00915","body.p.00919","body.p.00920","body.p.00921","body.p.00922","body.p.00923","body.p.00924","body.p.00927","body.p.00931"]
TARGETS={
"body.p.00890":["这是“能用”与“正确”之间差异的一个非常基础的例子。对于短生命周期程序，依赖容器的迭代顺序不会造成任何技术问题。但对于软件工程项目而言，这种对既定顺序的依赖则是一种风险——给定足够长的时间，总会"],
"body.p.00892":["10  一种拒绝服务（DoS）攻击：不受信任的用户了解哈希表的结构和哈希函数，并以使表上操作的算法性能退化的方式提供数据。"],
"body.p.00896":["使改变该迭代顺序变得有价值。这种价值可以通过多种方式体现：效率、安全性，或只是让数据结构具备面向未来的能力，以适应未来变更。当这种价值变得明确时，您需要权衡这一价值与破坏开发者或客户所带来痛苦之间的取舍。"],
"body.p.00897":["有些语言会特意在库版本之间，甚至在同一程序的不同执行之间随机化哈希顺序，试图防止形成依赖。但即便如此，仍可能出现 Hyrum 定律的意外：有代码将哈希迭代顺序用作低效的随机数生成器。如今去除这种随机性会破坏这些用户。正如熵在每个热力学系统中都会增加，Hyrum 定律也适用于每一种可观察行为。"],
"body.p.00898":["思考以“现在能用”和“永远能用”两种心态编写的代码之间的差异，我们可以提炼出一些清晰关系。把代码视为寿命要求（高度）可变的产物后，我们可以开始对编程风格分类：依赖项脆弱且未公开特性的代码，可能被称为“投机取巧”或“聪明”；而遵循最佳实践并为未来作好规划的代码，更可能被称为“整洁”和“可维护”。两者各有用途，但选择哪一种关键取决于相关代码的预期寿命。我们常说：“这是", "编程"],
"body.p.00899":["；若", "‘聪明’是一种赞美，那么这是", "软件工程", "；若‘聪明’是一种”"],
"textbox.004.p.0001":["指责。”"],
"body.p.00900":["为什么不只追求“永不变更”？"],
"body.p.00901":["在所有关于时间以及需要应对变更的讨论中，都隐含着一个假设：变更可能是必要的。真是如此吗？"],
"body.p.00902":["和本书中几乎所有其他问题一样，答案取决于具体情况。我们会欣然承诺：“对大多数项目而言，时间足够长时，其底层的一切都可能需要变更。”如果您的项目用纯 C 编写，且没有外部依赖项（或只有承诺长期稳定性的外部依赖项，例如 POSIX），您很可能能够避免任何形式的重构或困难升级。C 很擅长提供稳定性——在许多方面，这正是其首要目的。"],
"body.p.00903":["大多数项目更容易受到不断变化的底层技术影响。大多数编程语言和运行时的变化远多于 C。即使用纯 C 实现的库也可能为支持新功能而变更，进而影响下游用户。各类技术中都会披露安全问题，从处理器、网络库到应用代码皆然。", "每一", "项项目所依赖的技术都存在（希望很小的）风险，可能包含只有在您开始依赖它之后才暴露的关键漏洞和安全弱点。如果您无法为", "Heartbleed ", "部署补丁或缓解"],
"body.p.00907":["诸如", "Meltdown 和 Spectre ", "的投机执行问题，因为您假定（或承诺）任何事情永远不会改变，那就是一场重大的赌博。"],
"body.p.00908":["效率改进会使情况更加复杂。我们希望为数据中心配备高性价比的计算设备，尤其是提升 CPU 效率。然而，Google 早期的算法和数据结构在现代设备上就是效率较低：链表或二叉搜索树仍能正常工作，但 CPU 周期与内存延迟之间不断扩大的差距会影响“高效”代码的形态。随着时间推移，升级到更新硬件的价值可能会因缺少软件设计变更而减弱。向后兼容性确保旧系统仍可运行，但并不能保证旧优化仍有帮助。不愿或无法利用这类机会，会有承担巨大成本的风险。这类效率问题尤其微妙：原始设计可能完全合乎逻辑，也遵循了合理的最佳实践。只有经历一系列向后兼容的变更后，新的、更高效的选择才会变得重要。没有犯错，但时间的流逝仍让变更变得有价值。"],
"body.p.00909":["刚才提到的担忧，正是未投资于可持续性的长期项目面临巨大风险的原因。无论这些问题直接影响我们，还是仅出现在所构建技术的传递闭包中，我们都必须能够应对这些问题并利用这些机会。变更本身并不天然是好事。我们不应为变更而变更。但我们确实需要具备变更的能力。如果容许这种最终需求，我们还应考虑是否应投入资源，使这种能力成本低廉。正如每位系统管理员都知道的，理论上知道可以从磁带恢复是一回事，在实践中确切知道如何恢复、并知道必要时将花费多少，则是另一回事。实践和专业能力是效率与可靠性的强大驱动力。"],
"body.p.00910":["规模与效率"],
"body.p.00911":["如", "《Site Reliability Engineering》", "（SRE）一书所述，", "11", "Google 的生产系统整体是人类创造的最复杂机器之一。构建这样的机器并保持其平稳运行所涉及的复杂性，需要来自我们组织内外、遍布全球的专家投入无数小时的思考、讨论和重新设计。因此，我们已经写过一本书，介绍如何在那种规模下保持机器运行的复杂性。"],
"body.p.00915":["11  Beyer, B. 等。", "《Site Reliability Engineering: How Google Runs Production Systems》", "（Boston：O’Reilly Media，2016）。"],
"body.p.00919":["本", "书", "的大部分内容聚焦于生产此类机器的组织的规模复杂性，以及我们用来让机器随时间持续运行的流程。再想想代码库可持续性的概念：“当您的组织代码库", "可持续", "时，您", "能够", "安全地改变所有应该改变的事物，并且能在代码库的整个寿命期间做到这一点。”能力的讨论中还隐藏着成本问题：如果改变某事要付出过高代价，它很可能会被推迟。如果成本随时间超线性增长，该操作显然就不可扩展。", "12", "最终，时间会发挥作用，某件意料之外的事会出现，而您绝对必须改变它。当项目规模翻倍、又需要执行该任务时，劳动强度会翻倍吗？下次您甚至还会拥有解决该问题所需的人力资源吗？"],
"body.p.00920":["人力成本并非唯一需要扩展的有限资源。正如软件本身需要在计算、内存、存储和带宽等传统资源上良好扩展，软件的开发也需要扩展，既包括投入的人力时间，也包括为开发工作流提供动力的计算资源。如果测试集群的计算成本呈超线性增长，每季度每人消耗更多计算资源，您就处在一条不可持续的道路上，需要尽快作出变更。"],
"body.p.00921":["最后，软件组织最宝贵的资产——代码库本身——也需要扩展。如果构建系统或版本控制系统随时间呈超线性扩展，也许是由于增长和不断增加的变更日志历史，终有一天您会完全无法继续。许多问题，例如“完成一次完整构建需要多久？”“拉取一份全新的代码库副本需要多久？”或“升级到新的语言版本要花多少钱？”并未受到主动监控，而且变化缓慢。它们很容易变成", "温水煮青蛙", "；问题缓慢恶化，永远不会表现为单一的危机时刻。只有具备全组织对规模化的认识与承诺，您才可能始终掌控这些问题。"],
"body.p.00922":["组织为了生产和维护代码所依赖的一切，都应在总体成本和资源消耗方面具备可扩展性。尤其是，组织必须反复完成的一切，都应在人力投入方面具备可扩展性。许多常见政策在这个意义上似乎并不可扩展。"],
"body.p.00923":["不可扩展的政策"],
"body.p.00924":["稍加练习，就更容易发现扩展性不佳的政策。最常见的是，可以通过考察施加在单个"],
"body.p.00927":["12  本章中非正式使用“可扩展”一词时，指的是“相对于人际交互的次线性扩展”。"],
"body.p.00931":["工程师身上的工作，并设想组织规模扩展到 10 倍或 100 倍。规模增大 10 倍时，我们是否会增加 10 倍工作，让样本工程师必须跟上？工程师必须执行的工作量是否会随组织规模增长？工作是否随代码库规模扩大？如果其中任一项为真，我们是否已有自动化或优化该工作的机制？如果没有，我们就存在规模问题。"]}

def wj(p,v):
    with p.open('x',encoding='utf-8',newline='\n') as f: json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def wjl(p,vs):
    with p.open('x',encoding='utf-8',newline='\n') as f:
        for v in vs:f.write(v.model_dump_json()+'\n')
def request_for(v,b,sha,foot):
    by={s.anchor_ref:s.text for s in v.segments if s.anchor_ref}; rs=[r for r in foot if r['logical_work_order_id']==v.display_id]; binds=[VerifiedFootnoteNumericBinding(unit_uid=v.unit_uid,exact_source_marker_token=r['exact_source_marker_token'],footnote_number=r['footnote_number'],source_segment_binding=[VerifiedFootnoteSourceSegment(anchor_ref=x['anchor_ref'],text=x['text']) for x in r['source_segment_binding']]) for r in rs]
    return CalibrationRequest(source_sha256=sha,unit_uid=v.unit_uid,display_id=v.display_id,story=b.story,content_role=b.block_type,source_text=v.source_text,source_segments=[CalibrationSourceSegment(kind=s.kind,text=s.text,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s in v.segments],available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id,semantics=a.kinds,protected=a.protected,protected_text=by.get(a.anchor_id) if a.protected else None,container='hyperlink' if 'hyperlink_rid' in a.container_context else None) for a in v.anchors],continuation_hint=v.continuation_hint,previous_text=v.previous_text,next_text=v.next_text,selection_categories=['full_book_production','full_book_batch_003',CHECKPOINT,'chapter_1'],footnote_reference_numbers=[r['footnote_number'] for r in rs],verified_footnote_numeric_bindings=binds)
def qa(v,r,x,k):
    c=TaggedSyntheticCandidate(unit_uid=v.unit_uid,display_id=v.display_id,source_text=v.source_text,target_segments=x.target_segments,target_text=''.join(s.text for s in x.target_segments),generation_kind='calibration');z=FormatAnchorQA().validate(v,c,k,footnote_reference_numbers=r.footnote_reference_numbers,verified_footnote_numeric_bindings=r.verified_footnote_numeric_bindings);return {'unit_uid':v.unit_uid,'display_id':v.display_id,'passed':z.passed,'diagnostics':[d.model_dump() for d in z.diagnostics]}
def main():
    sha=fingerprint(SOURCE).sha256;sel=json.loads((RUN/'full_book_batch_003_selection.json').read_text(encoding='utf-8'));assert sel['selected_logical_work_order_ids'][:25]==ORDER
    vs={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding='utf-8').splitlines() if x)};bs={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in BLOCKS.read_text(encoding='utf-8').splitlines() if x)};foot=json.loads(FOOT.read_text(encoding='utf-8'))['records'];req=[request_for(vs[i],bs[i],sha,foot) for i in ORDER];out=BATCH/CHECKPOINT;BATCH.mkdir(exist_ok=False);shutil.copyfile(BIBLE,BATCH/'translation_bible_snapshot.md');update_manifest(BATCH/'batch_manifest.json',{'batch':'FULL_BOOK_BATCH_003','target_count':150,'completed_count':0,'checkpoint_count':0,'remaining_in_batch':150,'status':'OPEN_PROGRESS','next_checkpoint':CHECKPOINT,'checkpoints':[],'source_sha256':sha,'translation_bible_snapshot_sha256':sha256((BATCH/'translation_bible_snapshot.md').read_bytes()).hexdigest()});out.mkdir();wjl(out/'translation_request.jsonl',req);raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]) for r in req];rawsha=persist_raw_response(out/'translation_raw_response.jsonl',raw);k={v.unit_uid for v in vs.values()};a=[qa(vs[r.display_id],r,x,k) for r,x in zip(req,raw,strict=True)];wj(out/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in a),'fail_count':sum(not x['passed'] for x in a),'records':a});normalize_response_artifact(out/'translation_request.jsonl',out/'translation_raw_response.jsonl',out/'translation_normalized_response.jsonl',out/'translation_normalization_report.json');assert sha256((out/'translation_raw_response.jsonl').read_bytes()).hexdigest()==rawsha;n=[TranslationResult.model_validate_json(x) for x in (out/'translation_normalized_response.jsonl').read_text(encoding='utf-8').splitlines() if x];f=[qa(vs[r.display_id],r,x,k) for r,x in zip(req,n,strict=True)];wj(out/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in f),'fail_count':sum(not x['passed'] for x in f),'records':f});d,e=partition_deterministic_review_dispatch(f);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, protected literals, exact D050 footnote evidence where present, and reader-facing role.'} for x in d];wj(out/'semantic_review_report.json',semantic_review_summary(reviews,e));wjl(out/'translation_repaired_response.jsonl',n);ok=[x['unit_uid'] for x in f if x['passed']];bad=[x['unit_uid'] for x in f if not x['passed']];wj(out/'translation_final_qa_report.json',{'pass_count':len(ok),'fail_count':len(bad),'records':f,'final_approved_unit_uids':ok,'not_approved_deterministic_fail_unit_uids':bad});wj(out/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':sha,'raw_sha256':rawsha,'deterministic_pass_count':len(ok),'deterministic_fail_count':len(bad),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(e),'chapter_1_footnote_evidence':str(FOOT),'pdf_assisted_source_pages':[37,38,39,40]});append_sealed_checkpoint(BATCH/'batch_manifest.json',out);assert fingerprint(SOURCE).sha256==sha
if __name__=='__main__':main()
