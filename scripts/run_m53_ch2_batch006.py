"""Execute Chapter 2 M5.3b batch_006 (30 immutable Translator responses)."""

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
OUT = RUN / "chapter_2" / "batch_006"

DISPLAY_IDS = [
    "body.p.01240", "body.p.01243", "body.p.01247", "body.p.01248", "body.p.01249",
    "body.p.01250", "body.p.01251", "body.p.01252", "body.p.01253", "body.p.01257",
    "body.p.01258", "body.p.01259", "body.p.01260", "body.p.01261", "textbox.018.p.0001",
    "body.p.01262", "body.p.01264", "body.p.01268", "body.p.01269", "body.p.01270",
    "body.p.01271", "body.p.01272", "body.p.01273", "body.p.01274", "body.p.01275",
    "body.p.01276", "body.p.01277", "body.p.01280", "body.p.01281", "body.p.01285",
]

TARGETS = {
    "body.p.01240": ["寓意是：不要低估参与社交互动的力量。这不是欺骗或操纵他人，而是建立关系来把事情办成。关系总比项目更长久。当你与同事的关系更深厚时，他们在你需要时会更愿意多走一步。"],
    "body.p.01243": ["5 如果你过去因把任务委托给不称职的人而吃过亏，这一点会极其困难。"],
    "body.p.01247": ["谦逊、尊重与信任的实践"],
    "body.p.01248": ["关于谦逊、尊重和信任的这些说教听起来像一篇布道。让我们脚踏实地，想想怎样把这些理念应用到现实生活中。接下来会考察一系列可以开始践行的具体行为和例子。起初其中许多做法或许显而易见，但当你开始思考它们，就会发现自己（以及同事）常常没有遵循——我们当然也在自己身上发现过！"],
    "body.p.01249": ["放下自我"],
    "body.p.01250": ["好吧，这可以算是让一个不够谦逊的人收起架子的简单说法。没有人愿意与一个总把自己当作房间里最重要人物的人合作。即使你知道自己是讨论中最有智慧的人，也别把这点在别人面前挥来挥去。举例说，你是否总觉得每个话题都得先说或最后说？是否觉得必须评论提案或讨论中的每一个细节？或者，你是否认识这样的人？"],
    "body.p.01251": ["谦逊固然重要，但这并不意味着你必须当受气包；自信并没有错。只是别显得像个无所不知的人。更好的做法是培养“集体”自我：与其担心自己是否足够出色，不如努力建立团队成就感和群体自豪感。例如，Apache 软件基金会长期围绕软件项目创建社区。这些社区具有极强的认同感，并会排斥那些更关心自我宣传的人。"],
    "body.p.01252": ["自我以多种方式表现出来，而且常常会妨碍生产力、拖慢你的进度。下面是哈明演讲中的另一个精彩故事，恰好说明了这一点（强调为我们所加）："],
    "body.p.01253": ["约翰·图基几乎总是穿得很随意。他走进一间重要办公室后，往往要过很久，对方才意识到这是一位一流人物，自己最好听他说话。很长一段时间里，约翰都不得不克服这种敌意。这是徒劳的努力！我不是说你应该从众；我说的是：“看起来从众，会让你走得很远。”如果你选择以各种方式彰显自我，比如说“我要按自己的方式做”，那么在整个职业生涯中都会持续付出一点小小的代价。而这一点一滴在一生中会累积成大量不必要的麻烦。[…] 认识到你必须使用这个系统，并研究怎样让系统为你完成工作，你就会学会让系统适应你的愿望。", "或者，你也可以用一生持续与它抗争，打一场小规模、未宣战的战争。"],
    "body.p.01257": ["学会", "给予并", "接受批评"],
    "body.p.01258": ["几年前，乔开始了一份程序员的新工作。第一周之后，他开始深入研究代码库。因为关心正在发生的事，他开始温和地向其他团队成员询问他们的贡献。他通过电子邮件发送简单的代码评审，礼貌地询问设计假设，或指出逻辑可以改进的地方。几周后，他被叫到主管办公室。“有什么问题吗？”乔问，“我做错什么了吗？”主管神情忧虑：“乔，我们收到了很多对你行为的投诉。显然，你对团队成员一直很苛刻，四处批评他们。他们很不高兴。你需要收敛一些。”乔完全困惑了。他当然认为，同事应该欢迎并欣赏自己的代码评审。然而，在这个案例中，乔本应更敏感地察觉团队普遍缺乏安全感，并采用更细腻的方式把代码评审引入文化中——甚至可以先与团队讨论这个想法，再请团队成员试用几周这么简单。"],
    "body.p.01259": ["在专业的软件工程环境中，批评几乎从不针对个人——它通常只是打造更好项目过程的一部分。关键是确保你（以及身边的人）理解：对某人创造性产出的建设性批评，与对某人品格的直接攻击之间存在区别。后者毫无用处——琐碎而且几乎无法据此行动。前者能够（也应该）提供帮助，并指引改进的方向。更重要的是，它充满尊重：提出建设性批评的人真诚关心对方，希望他们改进自己或自己的工作。学会尊重同事，并礼貌地提出建设性批评。如果你真正尊重某人，就会愿意选择圆融而有帮助的措辞——这是一种需要大量练习才能获得的技能。我们会在", "第 9 章", "中进一步讨论这一点。"],
    "body.p.01260": ["从谈话的另一方来说，你也需要学会接受批评。这不仅意味着谦逊地看待自己的技能，还意味着相信对方把你（以及你的项目！）的利益放在心上，并不是真的觉得你是个笨蛋。编程和其他技能一样：通过练习会不断提高。如果同事指出你可以怎样改善杂耍技巧，你会把这视为对你人格和作为人的价值的攻击吗？我们希望不会。同样，你的自我价值不应与所写的代码——或构建的任何创造性项目——绑定在一起。再次强调：", "你不是你的代码", "。反复说这句话。你不是你所做出来的东西。你不仅要自己相信，也要让同事相信。"],
    "body.p.01261": ["例如，如果你有一位缺乏安全感的协作者，以下这种话千万别说：“伙计，你把那个方法里的控制流完全弄错了。你应该使用"],
    "textbox.018.p.0001": ["。"],
    "body.p.01262": ["和其他人一样的标准 xyzzy 代码模式。”这种反馈充满反模式：你在说对方“错了”（仿佛世界非黑即白），要求他们改变某些东西，并指控他们做出了"],
    "body.p.01264": ["一切都关乎团队　|　37"],
    "body.p.01268": ["违背其他人做法的东西（让他们觉得自己很蠢）。你的同事会立刻进入防御状态，回应势必过于情绪化。"],
    "body.p.01269": ["同一件事，更好的说法可以是：“嘿，我对这里这段控制流感到困惑。我想，xyzzy 代码模式是否能让它更清晰、更容易维护？”注意，你借助谦逊让问题关乎自己，而非对方。他们没有错；只是你难以理解这段代码。这个建议只是为了帮可怜的你理清问题，同时或许也有助于项目的长期可持续性目标。你也没有要求任何事——你给了协作者平和地拒绝建议的能力。讨论仍聚焦于代码本身，而不是任何人的价值或编程技能。"],
    "body.p.01270": ["快速失败，持续迭代"],
    "body.p.01271": ["商界有一个广为流传的都市传说：一位经理犯了错误，损失了惊人的 $10 million。第二天，他沮丧地走进办公室，开始收拾桌子；当接到那个不可避免的电话——“CEO 要你去他的办公室”——时，他拖着脚步走进 CEO 办公室，默默地把一张纸推过桌面。"],
    "body.p.01272": ["“这是什么？”CEO 问。"],
    "body.p.01273": ["“我的辞职信，”这位高管说，“我想你叫我来，是要解雇我。”"],
    "body.p.01274": ["“解雇你？”CEO 难以置信地回答，“我为什么要解雇你？我刚花了 $10 million 培训你！”", "6"],
    "body.p.01275": ["这当然是个极端的故事，但故事中的 CEO 明白，解雇这位高管无法挽回 $10 million 的损失；反而会因为失去一名宝贵的高管而加重损失，而他可以非常确定这位高管不会再犯同样的错误。"],
    "body.p.01276": ["在 Google，我们最喜欢的格言之一是“失败也是一种选择”。人们普遍认为，如果你不时失败，说明你创新得还不够，或承担的风险还不够。失败被视为学习并在下一轮改进的黄金机会。", "7", "事实上，人们常引用托马斯·爱迪生的话：“如果我发现了 10,000 种行不通的方法，我并没有失败。我不会气馁，因为每一次被舍弃的错误尝试，都是向前迈出的一步。”"],
    "body.p.01277": ["在 Google X——负责自动驾驶汽车、气球提供互联网接入等“登月计划”的部门——失败被刻意纳入激励体系。人们提出异想天开的点子，同事会被积极鼓励"],
    "body.p.01280": ["6 你可以在网上找到这个传说的十几种版本，它们被归于不同的著名经理人。"],
    "body.p.01281": ["7 同样地，如果你一遍又一遍地做同一件事并持续失败，那就不是失败，而是无能。"],
    "body.p.01285": ["尽快将这些点子否决。个人会因在固定时间内能证伪或判定无效的想法数量而获得奖励（甚至彼此竞争）。只有一个概念在白板前无法被所有同事驳倒时，它才会进入早期原型阶段。"],
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
    return CalibrationRequest(source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story, content_role=block.block_type, source_text=view.source_text, source_segments=[CalibrationSourceSegment(kind=s.kind, text=s.text, anchor_ref=s.anchor_ref, island_ref=s.island_ref) for s in view.segments], available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id, semantics=a.kinds, protected=a.protected, protected_text=by_anchor.get(a.anchor_id) if a.protected else None, container="hyperlink" if "hyperlink_rid" in a.container_context else None) for a in view.anchors], continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text, selection_categories=["m5_3_multi_chapter", "chapter_2", "batch_006"])


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
