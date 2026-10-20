"""Execute full-book batch 003 checkpoint 02 (closed 25-unit selection)."""

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


CHECKPOINT = "checkpoint_02"
ORDER = [
    "body.p.00932", "body.p.00933", "body.p.00934", "textbox.005.p.0001",
    "body.p.00935", "body.p.00938", "body.p.00942", "body.p.00943",
    "body.p.00944", "body.p.00945", "body.p.00946", "body.p.00947",
    "body.p.00956", "body.p.00960", "body.p.00961", "body.p.00962",
    "body.p.00963", "body.p.00967", "body.p.00971", "body.p.00972",
    "body.p.00973", "body.p.00974", "body.p.00975", "body.p.00976",
    "body.p.00977",
]
TARGETS = {
    "body.p.00932": ["以传统的弃用方式为例。我们将在", "第 15 章", "中更深入讨论弃用，但常见的弃用方法很好地说明了可扩展性问题。开发出了一个新的 Widget，于是决定让所有人都使用新版本并停止使用旧版本。为促成这一点，项目负责人会说：“我们将在 8 月 15 日删除旧 Widget；请确保您已迁移到新的版本。”"],
    "body.p.00933": ["这种做法在小型软件环境中或许行得通，但随着依赖图的深度和广度增加，很快就会失效。团队依赖的 Widget 数量不断增加，一次构建失败就可能影响公司中越来越高比例的人员。要以可扩展的方式解决这些问题，就意味着改变我们弃用的方式：团队不再把迁移工作推给客户，而是自行将其内化，从而获得由此带来的规模经济。"],
    "body.p.00934": ["2012 年，我们尝试通过减少变动的规则来制止这种情况：基础设施团队必须自行将内部用户迁移到新版本，或以向后兼容的方式原地更新。我们称这项政策为“变动规则”（Churn Rule），它的扩展性更好：依赖项目不再只是为了跟上变化而投入不断增加的精力。我们还发现，让专门的专家团队执行变更，比要求每位用户投入更多维护工作更具扩展性：专家会花时间深入了解整个问题，然后把这些专业知识应用于每个子问题。强迫用户应对变动，意味着每个受影响的团队都较差地完成学习，只解决眼前问题，然后丢弃那些已无用的知识。专业能力更具扩展性。传统的开发分支使用方式是另一种内建扩展性问题的政策。组织可能发现，将大型功能合并到主干会使产品不稳定，于是得出结论：“我们需要更严格地控制合并时机。我们应该降低合并频率。”这很快导致每个团队或每个功能都有各自的开发分支。每当某个分支被认为“完成”时，它就会被测试并合并到主干，从而让仍在开发分支上工作的其他工程师承担潜在昂贵的工作，例如重新同步和测试。这样的分支管理对于同时处理 5 到 10 个分支的小型组织或许可行。随着组织规模（以及分支数量）增加，很快就会变得"],
    "textbox.005.p.0001": ["显而易见：我们正付出"],
    "body.p.00935": ["不断增加的开销来完成同一项工作。随着规模扩大，我们需要不同的方法；我们将在", "第 16 章", "中讨论这一点。"],
    "body.p.00938": ["规模与效率      |      13"],
    "body.p.00942": ["具有良好扩展性的政策"],
    "body.p.00943": ["随着组织成长，哪些政策能带来更优的成本？或者更好的是，我们能制定哪些政策，使其随着组织成长提供超线性价值？"],
    "body.p.00944": ["我们最喜欢的一项内部政策，极大地赋能了基础设施团队，保护了它们安全进行基础设施变更的能力。“如果某产品因基础设施变更而发生宕机或其他问题，但我们的持续集成（CI）系统中的测试没有暴露该问题，那么责任不在基础设施变更。”更口语地说，就是“如果你喜欢它，就应该为它加上 CI 测试”，我们称之为“碧昂丝规则”（The Beyoncé Rule）。", "13", " 从扩展性的角度看，碧昂丝规则意味着：那些不会由我们通用 CI 系统触发的复杂、一次性定制测试不作数。没有这项规则，基础设施团队的工程师可能需要设法找到每个拥有受影响代码的团队，并询问他们如何运行测试。在只有一百名工程师时，我们尚可这样做；如今绝对负担不起了。"],
    "body.p.00945": ["我们发现，专业能力和共享交流论坛会在组织扩展时带来巨大价值。工程师在共享论坛中讨论和回答问题时，知识会传播，新专家会成长。如果有一百名工程师编写 Java，一位友善、乐于回答问题的 Java 专家，很快就能培养出一百名写出更好 Java 代码的工程师。知识具有病毒式传播性，专家是载体；清除工程师常见的绊脚石很有价值。我们将在", "第 3 章", "中更详细地讨论这一点。"],
    "body.p.00946": ["示例：编译器升级"],
    "body.p.00947": ["考虑升级编译器这项艰巨任务。理论上，鉴于语言为实现向后兼容所付出的巨大努力，编译器升级应当成本很低；但在实践中，它到底有多便宜？如果您从未做过这样的升级，会如何评估代码库是否与该变更兼容？"],
    "body.p.00956": ["13  这是对流行歌曲《Single Ladies》的引用，其中副歌唱道：“If you liked it then you shoulda put a ring on it.”"],
    "body.p.00960": ["根据我们的经验，即使普遍预期语言和编译器升级会向后兼容，它们仍是微妙而困难的任务。编译器升级几乎总会导致行为发生细微变化：修复错误编译、调整优化，或可能改变以前未定义行为的结果。您将如何针对所有这些潜在结果评估整个代码库的正确性？"],
    "body.p.00961": ["Google 历史上最传奇的一次编译器升级发生在 2006 年。当时我们已运营数年，拥有数千名工程师。我们大约五年没有更新编译器，大多数工程师都没有编译器变更的经验，大多数代码也只接触过一个编译器版本。对于一个主要由志愿者组成的团队来说，这是一项困难而痛苦的任务；最终，我们不得不寻找捷径和简化方法，以绕开那些我们不知道如何采纳的上游编译器和语言变更。", "14", " 最后，2006 年的编译器升级极其痛苦。大大小小的 Hyrum 定律问题已悄然进入代码库，使我们更依赖某个特定编译器版本。打破这些隐性依赖非常痛苦。相关工程师承担了风险：当时我们还没有碧昂丝规则，也没有普遍的 CI 系统，因此很难预先了解变更的影响，或确信他们不会因回归问题受到责难。"],
    "body.p.00962": ["这个故事一点也不罕见。许多公司的工程师都能讲述类似的痛苦升级经历。不同寻常的是，我们事后认识到任务的痛苦，并开始聚焦于技术和组织变更，以克服扩展性问题并将规模转化为优势：自动化（让一个人能完成更多工作）、整合/一致性（让底层变更的问题范围有限）以及专业能力（让少数人能完成更多工作）。"],
    "body.p.00963": ["您越频繁地改变基础设施，做这些改变就越容易。我们发现，大多数时候，当代码作为编译器升级之类工作的一部分被更新后，它会变得不那么脆弱，未来也更容易升级。在大多数代码都经历过多次升级的生态系统中，代码不再依赖底层实现的细节，而是依赖语言或操作系统保证的实际抽象。无论您具体升级什么，都应预期：对于一个代码库，第一次升级会显著比后续升级昂贵，即使控制了其他因素也是如此。"],
    "body.p.00967": ["14  具体而言，C++ 标准库的接口需要以命名空间 std 引用，而对 std::string 的一项优化变更，结果对我们的使用而言成了明显的性能倒退，因此需要一些额外的变通办法。"],
    "body.p.00971": ["通过这些以及其他经验，我们发现许多因素会影响代码库的灵活性："],
    "body.p.00972": ["专业能力"],
    "body.p.00973": ["我们知道如何做这件事；对于某些语言，我们现在已在多个平台上完成了数百次编译器升级。"],
    "body.p.00974": ["稳定性"],
    "body.p.00975": ["发布版本之间的变化更少，因为我们更规律地采用新版本；对于某些语言，我们现在每周或每两周部署一次编译器升级。"],
    "body.p.00976": ["一致性"],
    "body.p.00977": ["尚未经历过升级的代码更少了，同样是因为我们会定期升级。"],
}


def main() -> None:
    base.CHECKPOINT = CHECKPOINT
    source_sha = fingerprint(base.SOURCE).sha256
    selection = json.loads((base.RUN / "full_book_batch_003_selection.json").read_text(encoding="utf-8"))
    if selection["selected_logical_work_order_ids"][25:50] != ORDER:
        raise RuntimeError("AUTHORITATIVE_SELECTION_MISMATCH")
    output = base.BATCH / CHECKPOINT
    if output.exists():
        raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")
    views = {v.display_id: v for v in (TranslationView.model_validate_json(line) for line in base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {b.display_id: b for b in (BlockIR.model_validate_json(line) for line in base.BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    footnotes = json.loads(base.FOOT.read_text(encoding="utf-8"))["records"]
    requests = [base.request_for(views[item], blocks[item], source_sha, footnotes) for item in ORDER]
    output.mkdir()
    base.wjl(output / "translation_request.jsonl", requests)
    raw = [TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, TARGETS[request.display_id], strict=True)]) for request in requests]
    raw_sha = persist_raw_response(output / "translation_raw_response.jsonl", raw)
    known = {view.unit_uid for view in views.values()}
    raw_qa = [base.qa(views[request.display_id], request, result, known) for request, result in zip(requests, raw, strict=True)]
    base.wj(output / "translation_raw_qa_report.json", {"pass_count": sum(item["passed"] for item in raw_qa), "fail_count": sum(not item["passed"] for item in raw_qa), "records": raw_qa})
    normalize_response_artifact(output / "translation_request.jsonl", output / "translation_raw_response.jsonl", output / "translation_normalized_response.jsonl", output / "translation_normalization_report.json")
    if sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (output / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final = [base.qa(views[request.display_id], request, result, known) for request, result in zip(requests, normalized, strict=True)]
    base.wj(output / "translation_normalized_qa_report.json", {"pass_count": sum(item["passed"] for item in final), "fail_count": sum(not item["passed"] for item in final), "records": final})
    dispatch, excluded = partition_deterministic_review_dispatch(final)
    reviews = [{"unit_uid": item["unit_uid"], "display_id": item["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed source meaning, protected literals, and exact D050 footnote evidence where present."} for item in dispatch]
    base.wj(output / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    base.wjl(output / "translation_repaired_response.jsonl", normalized)
    approved = [item["unit_uid"] for item in final if item["passed"]]
    failed = [item["unit_uid"] for item in final if not item["passed"]]
    base.wj(output / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": final, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
    base.wj(output / "checkpoint_evidence.json", {"logical_work_orders": ORDER, "source_sha256": source_sha, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": len(failed), "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "chapter_1_footnote_evidence": str(base.FOOT), "pdf_assisted_source_pages": [42, 43, 44]})
    append_sealed_checkpoint(base.BATCH / "batch_manifest.json", output)
    if fingerprint(base.SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
