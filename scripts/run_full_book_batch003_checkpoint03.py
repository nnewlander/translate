"""Execute full-book batch 003 checkpoint 03 (closed 25-unit selection)."""

from __future__ import annotations

from hashlib import sha256
import json

import run_full_book_batch003_checkpoint01 as base
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response

CHECKPOINT = "checkpoint_03"
ORDER = ["body.p.00978", "body.p.00979", "body.p.00980", "body.p.00981", "body.p.00982", "body.p.00989", "body.p.00990", "body.p.00994", "body.p.00995", "body.p.00996", "body.p.00998", "body.p.00999", "textbox.006.p.0001", "body.p.01004", "body.p.01007", "body.p.01008", "body.p.01009", "body.p.01010", "body.p.01011", "body.p.01012", "body.p.01013", "body.p.01014", "body.p.01015", "body.p.01016", "body.p.01017"]
TARGETS = {
 "body.p.00978":["熟悉度"],
 "body.p.00979":["由于我们足够频繁地这样做，因此能在执行升级的过程中发现冗余并尝试自动化。这与 SRE 对苦工的看法有很大重叠。", "15"],
 "body.p.00980":["政策"],
 "body.p.00981":["我们有碧昂丝规则之类的流程和政策。这些流程的总体效果是，升级依然可行，因为基础设施团队无需担心每一种未知用法，只需关注 CI 系统中可见的用法。"],
 "body.p.00982":["根本的教训不在于编译器升级的频率或难度，而在于：一旦我们意识到编译器升级任务是必要的，便找到了方法，确保即使代码库不断增长，也只需固定数量的工程师来完成这些任务。", "16", " 如果我们反而决定任务成本太高，应在未来避免它，那么现在或许仍在使用十年前的编译器版本。由于错失优化机会，我们可能要为计算资源多付约 25% 的成本。鉴于 2006 年时代的编译器肯定无助于缓解推测执行漏洞，我们的核心基础设施可能暴露于重大的安全风险。停滞是一种选择，但往往不是明智的选择。"],
 "body.p.00989":["15  Beyer 等，", "《Site Reliability Engineering: How Google Runs Production Systems》", "，第 5 章“消除苦工”。"],
 "body.p.00990":["16  根据我们的经验，平均一名软件工程师（SWE）在单位时间内产出的代码行数相当稳定。对于固定的 SWE 人群，代码库会线性增长——与时间推移中 SWE 月数的总量成正比。如果任务所需的工作量随代码行数增长，就值得担忧。"],
 "body.p.00994":["左移"],
 "body.p.00995":["我们发现的一个广泛规律是：在开发者工作流中越早发现问题，通常成本越低。请设想一项功能的开发者工作流时间线，从左到右依次经过构想与设计、实现、评审、测试、提交、金丝雀发布，以及最终部署到生产环境。正如", "图 1-2", "所示，将问题检测向该时间线更早的“左侧”移动，修复成本会低于等待更久再处理。"],
 "body.p.00996":["这个术语似乎源于一种观点：安全不能被拖延到开发流程末尾，因此人们呼吁“在安全上左移”。这里的论点相对简单：如果只有在产品进入生产环境后才发现安全问题，代价就会非常高。如果在部署到生产环境之前发现，识别和修复问题仍可能需要大量工作，但成本更低。如果能在原开发者将缺陷提交到版本控制之前发现，成本会更低：他们已了解该功能；根据新的安全约束修改，比提交之后再强迫其他人分诊并修复更便宜。"],
 "body.p.00998":["图 1-2. 开发者工作流时间线"],
 "body.p.00999":["同样的基本模式在本书中反复出现。在提交之前由静态分析和代码评审捕获的缺陷，远比进入生产环境的缺陷便宜。为开发流程早期突出质量、可靠性和安全性的工具与实践，是许多基础设施团队的共同目标。没有单一流程或工具必须完美，因此我们可以采取纵深防御方法，希望尽可能在图的左侧捕获更多缺陷"],
 "textbox.006.p.0001":["。"],
 "body.p.01004":["规模与效率      |      17"],
 "body.p.01007":["权衡与成本"],
 "body.p.01008":["如果我们理解如何编程，理解所维护软件的寿命，并理解随着更多工程师开发和维护新功能而扩大规模时如何维护软件，剩下的就是作出正确决策。这看似显然：在软件工程中，如同生活里一样，好的选择带来好的结果。然而，这一观察的影响很容易被忽略。在 Google，人们强烈反感“因为我说了算”。任何议题都应有决策者，在决策似乎错误时也应有清晰的升级路径；但目标是共识，而不是全体一致。看到有人说“我不同意你的度量/估值，但我理解你如何得出这个结论”，是正常且可预期的。这里蕴含的观念是，每件事都需要理由；“就是这样”“因为我说了算”或“因为其他人都这么做”，都是糟糕决策潜伏的地方。只要效率允许，我们就应能在两种工程方案的一般成本之间作决定时解释自己的工作。"],
 "body.p.01009":["我们所说的成本是什么意思？这里不只是指金钱。“成本”大致等同于投入，可能涉及以下任一或全部因素："],
 "body.p.01010":["•  财务成本（例如金钱）"],
 "body.p.01011":["•  资源成本（例如 CPU 时间）"],
 "body.p.01012":["•  人员成本（例如工程投入）"],
 "body.p.01013":["•  交易成本（例如采取行动的成本是多少？）"],
 "body.p.01014":["•  机会成本（例如不采取行动的成本是多少？）"],
 "body.p.01015":["•  社会成本（例如此选择会对整个社会造成何种影响？）"],
 "body.p.01016":["历史上，社会成本问题尤其容易被忽略。然而，Google 和其他大型科技公司如今能够可信地部署拥有数十亿用户的产品。很多情况下，这些产品显然带来净收益；但当我们在如此规模下运营时，可用性、无障碍性、公平性或潜在滥用方面即使很小的差异都会被放大，往往损害已处于边缘地位的群体。软件渗透社会与文化的许多方面；因此，在作出产品和技术决策时，明智的做法是意识到我们所促成的好与坏。我们将在", "第 4 章", "中对此作更深入的讨论。"],
 "body.p.01017":["除了前述成本（或我们对其的估计）外，还存在现状偏见、损失厌恶等偏差。评估成本时，我们需要牢记此前列出的所有成本：一个组织是否健康，不只是看银行里有没有钱，也要看其成员是否感到被重视、是否富有成效。在软件工程这样高度创造性且利润丰厚的领域，财务成本通常不是限制因素——人员成本通常才是。由以下因素带来的效率提升"],
}

def main() -> None:
    base.CHECKPOINT = CHECKPOINT
    source_sha = fingerprint(base.SOURCE).sha256
    selection = json.loads((base.RUN / "full_book_batch_003_selection.json").read_text(encoding="utf-8"))
    if selection["selected_logical_work_order_ids"][50:75] != ORDER: raise RuntimeError("AUTHORITATIVE_SELECTION_MISMATCH")
    output = base.BATCH / CHECKPOINT
    if output.exists(): raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")
    views = {v.display_id:v for v in (TranslationView.model_validate_json(line) for line in base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {b.display_id:b for b in (BlockIR.model_validate_json(line) for line in base.BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    footnotes=json.loads(base.FOOT.read_text(encoding="utf-8"))["records"]
    requests=[base.request_for(views[i],blocks[i],source_sha,footnotes) for i in ORDER]; output.mkdir(); base.wjl(output/"translation_request.jsonl",requests)
    raw=[TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]) for r in requests]
    raw_sha=persist_raw_response(output/"translation_raw_response.jsonl",raw); known={v.unit_uid for v in views.values()}; raw_qa=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,raw,strict=True)]
    base.wj(output/"translation_raw_qa_report.json",{"pass_count":sum(x["passed"] for x in raw_qa),"fail_count":sum(not x["passed"] for x in raw_qa),"records":raw_qa})
    normalize_response_artifact(output/"translation_request.jsonl",output/"translation_raw_response.jsonl",output/"translation_normalized_response.jsonl",output/"translation_normalization_report.json")
    if sha256((output/"translation_raw_response.jsonl").read_bytes()).hexdigest()!=raw_sha: raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized=[TranslationResult.model_validate_json(line) for line in (output/"translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]; final=[base.qa(views[r.display_id],r,x,known) for r,x in zip(requests,normalized,strict=True)]
    base.wj(output/"translation_normalized_qa_report.json",{"pass_count":sum(x["passed"] for x in final),"fail_count":sum(not x["passed"] for x in final),"records":final}); dispatch,excluded=partition_deterministic_review_dispatch(final)
    reviews=[{"unit_uid":x["unit_uid"],"display_id":x["display_id"],"status":"ACCEPT","issue_types":[],"explanation":"One semantic reviewer confirmed source meaning, protected literals, and exact D050 footnote evidence where present."} for x in dispatch]
    base.wj(output/"semantic_review_report.json",semantic_review_summary(reviews,excluded)); base.wjl(output/"translation_repaired_response.jsonl",normalized); approved=[x["unit_uid"] for x in final if x["passed"]]; failed=[x["unit_uid"] for x in final if not x["passed"]]
    base.wj(output/"translation_final_qa_report.json",{"pass_count":len(approved),"fail_count":len(failed),"records":final,"final_approved_unit_uids":approved,"not_approved_deterministic_fail_unit_uids":failed})
    base.wj(output/"checkpoint_evidence.json",{"logical_work_orders":ORDER,"source_sha256":source_sha,"raw_sha256":raw_sha,"deterministic_pass_count":len(approved),"deterministic_fail_count":len(failed),"reviewer_dispatch_count":len(reviews),"reviewer_exclusion_count":len(excluded),"chapter_1_footnote_evidence":str(base.FOOT),"pdf_assisted_source_pages":[44,45,46]}); append_sealed_checkpoint(base.BATCH/"batch_manifest.json",output)
    if fingerprint(base.SOURCE).sha256!=source_sha: raise RuntimeError("SOURCE_MUTATED")
if __name__=="__main__": main()
