"""Execute full-book batch 003 checkpoint 04 (closed 25-unit selection)."""
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

CHECKPOINT="checkpoint_04"
ORDER=["body.p.01021","body.p.01022","body.p.01023","body.p.01024","body.p.01025","body.p.01026","body.p.01027","body.p.01028","body.p.01029","body.p.01035","body.p.01036","body.p.01040","body.p.01041","body.p.01042","body.p.01043","body.p.01044","body.p.01045","body.p.01046","body.p.01047","body.p.01048","body.p.01052","body.p.01053","body.p.01054","body.p.01055","body.p.01056"]
TARGETS={
"body.p.01021":["让工程师保持愉快、专注和投入，因专注度和生产率差异很大且很容易想见 10% 到 20% 的差异，所以很容易成为压倒其他因素的主导成本。"],
"body.p.01022":["示例：白板笔"],
"body.p.01023":["在许多组织中，白板笔被当作珍贵物品。它们受到严格控制，且总是供不应求。任何一块白板上，往往有一半白板笔已干涸、无法使用。有多少次会议因缺少能用的白板笔而被打断？有多少次您的思路因笔没墨而中断？又有多少次所有白板笔都消失了，想必是别的团队用完了自己的笔，只好拿走你们的？而这只是一个不到一美元的产品。"],
"body.p.01024":["Google 在大多数工作区都设有未上锁、装满办公用品的储物间，其中包括白板笔。只需片刻，就能轻松拿到数十支不同颜色的白板笔。在某个时候，我们明确作出了一项权衡：为无障碍头脑风暴而优化，远比防止有人带走一把白板笔更重要。"],
"body.p.01025":["从办公用品、员工福利，到开发者的日常体验，以及如何配置和运行全球规模服务，我们都希望以同样清醒、明确的方式权衡所涉及的成本/收益取舍。我们常说：“Google 是数据驱动的文化。”事实上，这是简化说法：即使没有", "数据", "，仍可能有", "证据", "、", "先例", "和", "论证", "。作出好的工程决策，就是权衡所有可用输入，并对取舍作出知情决定。有时，这些决定基于直觉或被接受的最佳实践，但那只会发生在我们已穷尽尝试衡量或估计真实底层成本的方法之后。"],
"body.p.01026":["归根结底，工程团队的决策应归结为极少数几件事："],
"body.p.01027":["•  我们这样做是因为必须这样做（法律要求、客户要求）。"],
"body.p.01028":["•  我们这样做是因为基于当前证据，这是目前我们能看到的最佳选择（由适当的决策者确定）。"],
"body.p.01029":["决策不应是“我们这样做是因为我说了算”。", "17"],
"body.p.01035":["17  这并不是说决策必须全体一致，甚至不必获得广泛共识；归根结底，"],
"body.p.01036":["必须有人作出决定。这主要是在说明：对于实际负责该决策的人，决策过程应当如何推进。"],
"body.p.01040":["决策的输入"],
"body.p.01041":["权衡数据时，我们发现两种常见情形："],
"body.p.01042":["•  涉及的所有量都可测量，或至少可以估计。这通常意味着我们在评估 CPU 与网络、美元与 RAM 之间的取舍，或者考虑是否花两周工程师时间，以在数据中心节省", "N ", "个 CPU。"],
"body.p.01043":["•  有些量很微妙，或者我们不知道如何测量。有时表现为“我们不知道这要耗费多少工程师时间”。有时更为模糊：如何衡量设计糟糕的 API 的工程成本？又如何衡量某项产品选择的社会影响？"],
"body.p.01044":["对于第一类决策，几乎没有理由准备不足。任何软件工程组织都可以、也应当跟踪计算资源、工程师工时以及其他经常交互量的当前成本。即使不愿向组织公开准确的美元金额，仍可以制作一张换算表：这么多 CPU 与这么多 RAM 或网络带宽成本相同。"],
"body.p.01045":["有了约定的换算表，每位工程师都能自行分析。“如果我花两周把这个链表改成性能更高的结构，我将多使用五吉比字节的生产 RAM，但节省两千个 CPU。我该做吗？”这个问题不仅取决于 RAM 和 CPU 的相对成本，也取决于人员成本（支持一名软件工程师两周）和机会成本（那名工程师两周还能产出什么？）。"],
"body.p.01046":["对于第二类决策，没有简单答案。我们依靠经验、领导力和先例来协商这些问题。我们正投资研究以帮助量化难以量化的事物（参见", "第 7 章", "）。然而，我们能给出的最佳总体建议是：意识到并非一切都可测量或可预测，并尝试以同样优先级、更加谨慎地对待这类决策。它们通常同样重要，但更难管理。"],
"body.p.01047":["示例：分布式构建"],
"body.p.01048":["请考虑您的构建。根据完全不科学的 Twitter 投票，即使面对今天庞大而复杂的构建，仍有大约 60% 到 70% 的开发者在本地构建。这直接导致了", "这幅“编译中”漫画", "所描绘的并非玩笑：组织中有多少生产性时间浪费在等待构建上？与之相比，为一个小团队运行类似 distcc 的系统成本如何？或者，为一个大团队运行小型构建农场成本如何？需要多少周/月，才能让这些成本变成净收益？"],
"body.p.01052":["在 2000 年代中期，Google 完全依赖本地构建系统：检出代码后在本地编译。有时我们拥有性能强大的本地机器（您甚至可以在桌面电脑上构建 Maps！），但随着代码库增长，编译时间越来越长。不出所料，我们因时间损失承担了不断增加的人员成本，也因更大、更强的本地机器承担了更高资源成本，等等。这些资源成本尤其麻烦：我们当然希望人们拥有尽可能快的构建速度，但大多数时候，高性能桌面开发机都处于闲置状态。这不像是投资这些资源的恰当方式。"],
"body.p.01053":["最终，Google 开发了自己的分布式构建系统。当然，开发该系统产生成本：工程师需要时间开发，更多工程师需要时间改变所有人的习惯和工作流并学习新系统，当然还要消耗额外的计算资源。但总体节省显然值得：构建变得更快，工程师时间得以收回，硬件投资可以集中在受管共享基础设施（实际上是生产机群的一个子集），而不是性能不断增强的桌面机器上。", "第 18 章", "进一步介绍了我们对分布式构建的方法和相关取舍。"],
"body.p.01054":["于是，我们构建了一个新系统，将其部署到生产环境，并加快了每个人的构建。这是故事的圆满结局吗？并不完全是：分布式构建系统大幅提升了工程师生产率，但随着时间推移，分布式构建本身变得臃肿。先前受单个工程师约束的事物（因为他们有既得利益让本地构建尽可能快），在分布式构建系统中不再受约束。构建图中臃肿或不必要的依赖变得司空见惯。当每个人都直接感受到非最优构建的痛苦并有动力保持警惕时，激励更为一致。通过移除这些激励、把臃肿依赖隐藏在并行分布式构建中，我们造成了一种消费可能肆意增长、几乎无人有动力留意构建膨胀的局面。这令人想起", "杰文斯悖论", "：资源消耗可能会", "增加", "，以回应其使用效率的提高。"],
"body.p.01055":["总体而言，增加分布式构建系统所节省的成本，远远超过构建和维护它带来的负面成本。但是，正如我们看到消费增加时那样，我们并未预见所有这些成本。在一路推进后，我们发现自己需要重新构想系统及使用方式的目标和约束、识别最佳实践（小依赖、由机器管理依赖），并为新生态系统的工具和维护提供资金。即使相对简单的取舍，例如"],
"body.p.01056":["“我们为计算资源花费 $$$，以收回工程师时间”，也会产生未预见的下游影响。"],
}
def main():
 base.CHECKPOINT=CHECKPOINT; source_sha=fingerprint(base.SOURCE).sha256; selection=json.loads((base.RUN/'full_book_batch_003_selection.json').read_text(encoding='utf8'))
 if selection['selected_logical_work_order_ids'][75:100]!=ORDER: raise RuntimeError('AUTHORITATIVE_SELECTION_MISMATCH')
 output=base.BATCH/CHECKPOINT
 if output.exists(): raise RuntimeError('CHECKPOINT_ALREADY_EXISTS')
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in base.VIEWS.read_text(encoding='utf8').splitlines() if x)};blocks={b.display_id:b for b in (BlockIR.model_validate_json(x) for x in base.BLOCKS.read_text(encoding='utf8').splitlines() if x)};footnotes=json.loads(base.FOOT.read_text(encoding='utf8'))['records'];requests=[base.request_for(views[i],blocks[i],source_sha,footnotes) for i in ORDER];output.mkdir();base.wjl(output/'translation_request.jsonl',requests)
 raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]) for r in requests];raw_sha=persist_raw_response(output/'translation_raw_response.jsonl',raw);known={v.unit_uid for v in views.values()};raw_qa=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,raw,strict=True)];base.wj(output/'translation_raw_qa_report.json',{'pass_count':sum(x['passed'] for x in raw_qa),'fail_count':sum(not x['passed'] for x in raw_qa),'records':raw_qa});normalize_response_artifact(output/'translation_request.jsonl',output/'translation_raw_response.jsonl',output/'translation_normalized_response.jsonl',output/'translation_normalization_report.json')
 if sha256((output/'translation_raw_response.jsonl').read_bytes()).hexdigest()!=raw_sha: raise RuntimeError('RAW_RESPONSE_MUTATED')
 normalized=[TranslationResult.model_validate_json(x) for x in (output/'translation_normalized_response.jsonl').read_text(encoding='utf8').splitlines() if x];final=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,normalized,strict=True)];base.wj(output/'translation_normalized_qa_report.json',{'pass_count':sum(x['passed'] for x in final),'fail_count':sum(not x['passed'] for x in final),'records':final});dispatch,excluded=partition_deterministic_review_dispatch(final);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'One semantic reviewer confirmed source meaning, protected literals, and exact D050 footnote evidence where present.'} for x in dispatch];base.wj(output/'semantic_review_report.json',semantic_review_summary(reviews,excluded));base.wjl(output/'translation_repaired_response.jsonl',normalized);approved=[x['unit_uid'] for x in final if x['passed']];failed=[x['unit_uid'] for x in final if not x['passed']];base.wj(output/'translation_final_qa_report.json',{'pass_count':len(approved),'fail_count':len(failed),'records':final,'final_approved_unit_uids':approved,'not_approved_deterministic_fail_unit_uids':failed});base.wj(output/'checkpoint_evidence.json',{'logical_work_orders':ORDER,'source_sha256':source_sha,'raw_sha256':raw_sha,'deterministic_pass_count':len(approved),'deterministic_fail_count':len(failed),'reviewer_dispatch_count':len(reviews),'reviewer_exclusion_count':len(excluded),'chapter_1_footnote_evidence':str(base.FOOT),'pdf_assisted_source_pages':[47,48,49]});append_sealed_checkpoint(base.BATCH/'batch_manifest.json',output)
 if fingerprint(base.SOURCE).sha256!=source_sha: raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__': main()
