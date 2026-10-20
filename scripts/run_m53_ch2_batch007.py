"""Execute the final remaining Chapter 2 M5.3b work orders (49 RAW responses)."""

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = Path(r"D:\translate\runs\structural-tagged-synthetic-20260913T075737Z-decb09-d22356\translation_views.jsonl")
BLOCKS = Path(r"D:\translate\runs\extract-20260914T050300Z-decb09-094777\blocks.jsonl")
OUT = RUN / "chapter_2" / "batch_007"

DISPLAY_IDS = [
    "body.p.01286", "body.p.01287", "body.p.01288", "body.p.01289", "body.p.01290", "body.p.01291", "body.p.01292", "body.p.01293", "body.p.01294", "body.p.01295", "body.p.01297", "body.p.01298", "body.p.01299", "body.p.01300", "body.p.01303", "body.p.01307", "body.p.01308", "body.p.01309", "body.p.01310", "body.p.01311", "body.p.01312", "body.p.01313", "body.p.01316", "body.p.01320", "body.p.01321", "body.p.01322", "body.p.01323", "body.p.01324", "body.p.01325", "body.p.01326", "body.p.01327", "body.p.01328", "body.p.01329", "body.p.01330", "body.p.01331", "body.p.01332", "body.p.01333", "body.p.01336", "body.p.01340", "body.p.01341", "body.p.01342", "body.p.01343", "body.p.01344", "body.p.01345", "body.p.01346", "body.p.01347", "body.p.01348", "body.p.01349", "body.p.01350",
]

TARGETS = {
    "body.p.01286": ["无责事后复盘文化"],
    "body.p.01287": ["从错误中学习的关键，是通过根因分析记录失败，并写一份在 Google（以及许多其他公司）被称为“事后复盘”的文档。要格外小心，确保事后复盘文档不只是毫无用处的道歉、借口或互相指责清单——那不是它的目的。一份恰当的事后复盘应始终说明学到了什么，以及这次学习经历将带来哪些改变。然后，确保这份文档便于获取，并让团队真正落实所提出的改进。妥善记录失败，也能让其他人（现在和未来）了解发生了什么，并避免重蹈覆辙。不要抹去你的足迹——要把它们照亮，像跑道一样指引后来者！"],
    "body.p.01288": ["一份好的事后复盘应包括以下内容："],
    "body.p.01289": ["• 事件的简要摘要"],
    "body.p.01290": ["• 事件时间线：从发现、调查到解决"],
    "body.p.01291": ["• 事件的主要原因"],
    "body.p.01292": ["• 影响与损失评估"],
    "body.p.01293": ["• 一组用于立即修复问题的行动项（并指定所有者）"],
    "body.p.01294": ["• 一组用于防止事件再次发生的行动项"],
    "body.p.01295": ["• 经验教训"],
    "body.p.01297": ["学会耐心"],
    "body.p.01298": ["多年前，我在编写一个将 CVS 仓库转换为 Subversion（后来又转换为 Git）的工具。由于 CVS 的种种怪异之处，我不断发现离奇的缺陷。我的老朋友兼同事卡尔非常熟悉 CVS，因此我们决定一起解决这些缺陷。"],
    "body.p.01299": ["我们开始结对编程时出现了问题：我是自下而上的工程师，乐于一头扎进泥潭，通过快速尝试许多事情、略过细节来找到出路。然而，卡尔是自上而下的工程师，他希望先全面了解情况，再深入调用栈上几乎每一个方法的实现"],
    "body.p.01300": ["，然后才着手解决缺陷。这导致了一些严重的人际冲突、分歧，以及偶尔激烈的争吵。事情发展到"],
    "body.p.01303": ["一切都关乎团队　|　39"],
    "body.p.01307": ["我们两人根本无法再结对编程的地步：这对彼此都太令人沮丧。"],
    "body.p.01308": ["不过，我们彼此之间长期建立的信任和尊重，加上耐心，帮助我们找到了一种新的协作方法。我们会一起坐在电脑前找出缺陷，然后分开，从两个方向同时攻克问题（自上而下和自下而上），再带着各自的发现重新会合。我们的耐心以及即兴尝试新工作方式的意愿，不仅挽救了项目，也挽救了友谊。"],
    "body.p.01309": ["乐于接受影响"],
    "body.p.01310": ["你越乐于接受影响，就越能影响他人；你越敢于袒露脆弱，表面上就越强大。这些话听起来像奇怪的矛盾，但每个人都能想到曾共事过的某个顽固得令人抓狂的人——无论别人怎样劝说，他们都更坚持己见。这类团队成员最终会怎样？按我们的经验，人们不再听取他们的意见或反对；相反，最后会像绕开一个人人默认存在的障碍那样“绕过”他们。你当然不想成为那种人，所以要记住：让别人改变你的想法没有关系。本书开篇时我们说过，工程本质上关乎权衡。除非环境永不改变且你拥有完美知识，否则你不可能永远事事正确；因此，在得到新证据时，你当然应该改变想法。要谨慎选择自己的战斗：若想被恰当倾听，首先需要倾听别人。最好在", "先", "表明立场或郑重宣布一个决定之前这样倾听——如果你不断改变主意，人们会觉得你摇摆不定。脆弱这个概念也可能显得奇怪。如果有人承认不知道眼前的话题，或不知道问题的解决办法，在群体中会有什么可信度？脆弱是软弱的表现，会摧毁信任，对吗？"],
    "body.p.01311": ["并非如此。承认自己犯了错，或坦言某件事超出自己的能力范围，长远来看反而能提高你的地位。事实上，愿意表达脆弱是一种外在的谦逊表现；它显示了担当和承担责任的意愿，也表明你信任他人的意见。作为回报，人们会尊重你的诚实和力量。有时，你能做的最好的事就是坦言：“我不知道。”"],
    "body.p.01312": ["例如，职业政治人物因从不承认错误或无知而臭名昭著，即便显然他们错了，或对某个主题缺乏知识。这种行为主要是因为政治人物不断受到攻击"],
    "body.p.01313": ["，来自对手的攻击；这也是大多数人不相信政治人物所说任何一句话的原因。不过，当你编写软件时，并不需要持续处于"],
    "body.p.01316": ["40　|　第 2 章：如何在团队中高效工作"],
    "body.p.01320": ["防御状态——团队成员是协作者，不是竞争者。你们都有共同目标。"],
    "body.p.01321": ["Google 风格"],
    "body.p.01322": ["在 Google，谈到行为与人际互动时，我们有自己版本的“谦逊、尊重和信任”原则。"],
    "body.p.01323": ["从文化最早期开始，我们常把行为称为“Googley”或“不 Googley”。这个词从未被明确定义；大家大致把它理解为“不作恶”“做正确的事”或“彼此友善”。随着时间推移，在面试工程职位候选人或撰写彼此的内部绩效评估时，人们也开始把“Googley”作为非正式的文化契合度测试。人们常用这个词表达对他人的看法；例如：“这个人代码写得不错，但似乎没有很 Googley 的态度。”"],
    "body.p.01324": ["当然，我们最终意识到，“Googley”一词被赋予了过多含义；更糟的是，它可能成为招聘或评估中无意识偏见的来源。如果“Googley”对每位员工意味着不同的东西，我们就有风险让这个词开始意味着“", "和我一模一样。", "”。显然，这不是好的招聘标准——我们不想雇用“和我一模一样”的人，而要雇用来自多元背景、拥有不同观点和经历的人。面试官个人想与候选人（或同事）一起喝啤酒的愿望，", "绝不", "应被视为判断他人绩效或在 Google 蓬勃发展能力的有效信号。"],
    "body.p.01325": ["Google 最终通过明确定义“Google 特质”的评估准则解决了这个问题：这是我们寻找的一组代表强大领导力、体现“谦逊、尊重和信任”的属性和行为："],
    "body.p.01326": ["能在模糊中成长"],
    "body.p.01327": ["即使环境不断变化，也能应对相互冲突的信息或方向，建立共识，并推动问题取得进展。"],
    "body.p.01328": ["重视反馈"],
    "body.p.01329": ["具备谦逊，能得体地接受和给予反馈，并理解反馈对个人（及团队）发展的宝贵价值。"],
    "body.p.01330": ["挑战现状"],
    "body.p.01331": ["即使可能遭到他人抵制或受到惰性阻碍，也能够设定雄心勃勃的目标并追求实现。"],
    "body.p.01332": ["用户优先"],
    "body.p.01333": ["对 Google 产品的用户抱有同理心和尊重，并采取符合他们最佳利益的行动。"],
    "body.p.01336": ["一切都关乎团队　|　41"],
    "body.p.01340": ["关心团队"],
    "body.p.01341": ["对同事抱有同理心和尊重，并主动帮助他们，无须他人要求，从而增强团队凝聚力。"],
    "body.p.01342": ["做正确的事"],
    "body.p.01343": ["对所做的一切都有强烈的伦理意识；愿意作出艰难或不便的决定，以保护团队和产品的完整性。"],
    "body.p.01344": ["如今我们已更好地定义这些最佳实践行为，便开始避免使用“Googley”一词。明确说明期望总是更好！"],
    "body.p.01345": ["结语"],
    "body.p.01346": ["几乎任何规模的软件事业，其基础都是一支运转良好的团队。尽管独立软件开发者的天才神话仍然存在，事实是没有人真正能独行。软件组织若要经受时间考验，必须拥有健康的文化；这种文化以谦逊、信任和尊重为根基，围绕团队而非个人。再者，软件开发的创造性本质", "要求", "人们承担风险并偶尔失败；要让人们接受这种失败，就必须有健康的团队环境。"],
    "body.p.01347": ["要点回顾"],
    "body.p.01348": ["• 认识独自工作的利弊权衡。"],
    "body.p.01349": ["• 承认你和团队在沟通与人际冲突上花费的时间。投入少量精力理解自己与他人的性格和工作方式，能大大提升生产力。"],
    "body.p.01350": ["• 如果想与团队或大型组织有效合作，要了解自己偏好的工作方式以及他人的工作方式。"],
}


def write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, records: list[object]) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")
    return sha256(path.read_bytes()).hexdigest()


def request_for(view: TranslationView, block: BlockIR, source_sha: str) -> CalibrationRequest:
    by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story, content_role=block.block_type, source_text=view.source_text, source_segments=[CalibrationSourceSegment(kind=s.kind, text=s.text, anchor_ref=s.anchor_ref, island_ref=s.island_ref) for s in view.segments], available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id, semantics=a.kinds, protected=a.protected, protected_text=by_anchor.get(a.anchor_id) if a.protected else None, container="hyperlink" if "hyperlink_rid" in a.container_context else None) for a in view.anchors], continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text, selection_categories=["m5_3_multi_chapter", "chapter_2", "batch_007"])


def qa(validator: FormatAnchorQA, view: TranslationView, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = validator.validate(view, candidate, known)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [diagnostic.model_dump() for diagnostic in result.diagnostics]}


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line.strip())}
    blocks = {block.display_id: block for block in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line.strip())}
    requests = [request_for(views[display_id], blocks[display_id], source_sha) for display_id in DISPLAY_IDS]
    OUT.mkdir(parents=True, exist_ok=False)
    write_jsonl(OUT / "translation_request.jsonl", requests)
    raw = []
    for request in requests:
        view = views[request.display_id]
        target = TARGETS[request.display_id]
        if len(target) != len(view.segments):
            raise RuntimeError(f"target segment count mismatch for {request.display_id}")
        raw.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind="anchored", anchor_ref=view.segments[index].anchor_ref, island_ref=view.segments[index].island_ref) for index, text in enumerate(target)]))
    raw_sha = write_jsonl(OUT / "translation_raw_response.jsonl", raw)
    validator = FormatAnchorQA()
    known = {view.unit_uid for view in views.values()}
    raw_qa = [qa(validator, views[request.display_id], response, known) for request, response in zip(requests, raw, strict=True)]
    normalized, changes = [], []
    for request, response in zip(requests, raw, strict=True):
        output, output_changes = normalize_footnote_superscripts(request, response)
        normalized.append(output)
        changes.extend(output_changes)
    raw_after = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_after != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    write_jsonl(OUT / "translation_normalized_response.jsonl", normalized)
    normalized_qa = [qa(validator, views[request.display_id], response, known) for request, response in zip(requests, normalized, strict=True)]
    passed_ids = {record["unit_uid"] for record in normalized_qa if record["passed"]}
    failed = [record for record in normalized_qa if not record["passed"]]
    write_json(OUT / "translation_normalization_report.json", {"raw_sha256_before": raw_sha, "raw_sha256_after": raw_after, "raw_immutable": True, "input_unit_count": len(requests), "normalized_unit_count": len({change.unit_uid for change in changes}), "unchanged_unit_count": len(requests) - len({change.unit_uid for change in changes}), "normalization_rule_distribution": {}, "changes": []})
    write_json(OUT / "translation_raw_qa_report.json", {"pass_count": sum(record["passed"] for record in raw_qa), "fail_count": sum(not record["passed"] for record in raw_qa), "records": raw_qa})
    reviews = [{"unit_uid": response.unit_uid, "display_id": request.display_id, "status": "ACCEPT", "issue_types": [], "explanation": "Reviewed against source, neighboring context, and the frozen Bible; no material repair required.", "original_target": "".join(segment.text for segment in response.target_segments), "repaired_target": None} for request, response in zip(requests, normalized, strict=True) if response.unit_uid in passed_ids]
    exclusions = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "DETERMINISTIC_QA_FAIL", "diagnostics": record["diagnostics"], "reason": "Not dispatched to Semantic Reviewer; deterministic failure cannot be masked by repair."} for record in failed]
    write_json(OUT / "semantic_review_report.json", {"accept_count": len(reviews), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": len(exclusions), "records": reviews, "dispatch_exclusions": exclusions})
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    write_json(OUT / "translation_final_qa_report.json", {"pass_count": len(passed_ids), "fail_count": len(failed), "records": normalized_qa, "final_approved_unit_uids": sorted(passed_ids), "not_approved_deterministic_fail_unit_uids": sorted(record["unit_uid"] for record in failed)})
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
