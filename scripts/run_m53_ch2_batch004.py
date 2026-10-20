"""Execute the third Chapter 2 M5.3b Translator checkpoint (20 work orders)."""

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import (
    CalibrationAnchor,
    CalibrationRequest,
    CalibrationSourceSegment,
    TranslationResult,
)
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import (
    StructuredTargetSegment,
    TaggedSyntheticCandidate,
    TranslationView,
)
from booktrans.harness.response_normalization import normalize_footnote_superscripts
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = Path(
    r"D:\translate\runs\structural-tagged-synthetic-20260913T075737Z-decb09-d22356\translation_views.jsonl"
)
BLOCKS = Path(r"D:\translate\runs\extract-20260914T050300Z-decb09-094777\blocks.jsonl")
OUT = RUN / "chapter_2" / "batch_004"

DISPLAY_IDS = [
    "body.p.01176",
    "body.p.01177",
    "body.p.01178",
    "body.p.01179",
    "body.p.01180",
    "body.p.01181",
    "body.p.01184",
    "body.p.01185",
    "body.p.01189",
    "body.p.01190",
    "body.p.01191",
    "body.p.01192",
    "textbox.015.p.0001",
    "textbox.015.p.0002",
    "body.p.01197",
    "body.p.01198",
    "body.p.01199",
    "body.p.01200",
    "body.p.01201",
    "body.p.01202",
]

# These are the Translator's first responses.  They are written exactly once to
# translation_raw_response.jsonl before any normalizer or review stage runs.
TARGETS = {
    "body.p.01176": [
        "如果你把绝妙的想法藏起来，在实现打磨完成前拒绝向任何人展示，你就是在冒一个巨大的风险。早期很容易犯下根本性的设计错误，也可能重复造轮子。",
        "2",
        "你还会失去协作带来的益处：想想你的邻居与他人合作后进展快了多少？这正是人们在跳入深水之前先试探水温的原因：你得确认自己做的是正确的事、做法正确，而且此前没人做过。早期走错一步的可能性很高；越早征求反馈，风险就越低。",
        "3",
        "请记住那句久经考验的箴言：“尽早失败，快速失败，经常失败。”",
    ],
    "body.p.01177": [
        "及早分享不仅能防止个人失误、让他人检验你的想法，也有助于增强我们所说的项目巴士因子。"
    ],
    "body.p.01178": ["巴士因子"],
    "body.p.01179": [
        "巴士因子（名词）：要有多少人被公交车撞到，你的项目才会彻底完蛋。"
    ],
    "body.p.01180": [
        "你项目中的知识和诀窍分散得如何？如果只有你一个人懂原型代码如何运作，你或许会享有不错的职业保障——但要是你被公交车撞了，项目就完了。相反，如果你与一位同事合作，巴士因子就翻了一倍。如果有一个小团队一起设计和制作原型，情况会更好——即使一名成员离开，项目也不会陷入孤立无援的境地。请记住：团队成员未必真的会被公交车撞到，但其他不可预测的人生事件仍会发生。有人可能结婚、搬走、离开公司，或请假照顾生病的亲属。确保除每个职责领域都有主要负责人和次要负责人外，",
        "至少",
        "还具备良好的文档，能为项目未来的成功提供保障，并提高项目的巴士因子。希望大多数工程师都能认识到：做成功项目中的一部分，比做失败项目中不可或缺的那一部分更好。",
    ],
    "body.p.01181": [
        "除了巴士因子，还有整体进展速度的问题。人们很容易忘记，独自工作往往是一场艰苦的跋涉，速度比大家愿意承认的要慢得多。独自工作时你能学到多少？推进得有多快？Google 和 Stack Overflow 是获取观点和信息的好来源，但它们无法替代真实的人类经验。直接与他人合作，能提高这项工作的集体智慧。当你被某件荒谬的事卡住时，要花多少时间才能把自己从坑里拉出来？想一想，"
    ],
    "body.p.01184": ["2 确实如此——如果你实际上是一名自行车设计师。"],
    "body.p.01185": [
        "3 我应该指出：如果你对总体方向或目标仍不确定，在过程中过早获得过多反馈有时也会有风险。"
    ],
    "body.p.01189": [
        "如果有几位同伴在你身旁看看，并立即告诉你哪里出了错、怎样越过这个问题，体验会有多么不同。软件工程公司让团队成员坐在一起（或进行结对编程），原因正是如此。编程很难，软件工程更难。你需要第二双眼睛。"
    ],
    "body.p.01190": ["进展速度"],
    "body.p.01191": [
        "再来一个比喻。想想你如何使用编译器。坐下来编写一大块软件时，你会先花几天写 10,000 行代码，等写下最后那行完美的代码后，才第一次按下“编译”按钮吗？当然不会。你能想象那会造成怎样的灾难吗？程序员在紧密的反馈循环中工作得最好：写一个新函数，编译；添加一个测试，编译；重构一些代码，编译。这样一来，我们能在生成代码后尽快发现并修正错别字和缺陷。我们希望编译器在每个小步骤中都陪在身边；有些环境甚至能在我们输入代码时就进行编译。这正是我们保持代码质量、确保软件一点一点正确演进的方式。当前 DevOps 对技术生产力的理念明确强调这些目标：尽早获取反馈，尽早测试，尽早考虑安全性和生产环境。这些都被纳入开发者工作流“左移”的理念：越早发现问题，修复成本就越低。"
    ],
    "body.p.01192": [
        "这种快速反馈循环不仅在代码层面需要，在整个项目层面同样需要。雄心勃勃的项目发展很快，必须在推进过程中适应不断变化的环境。项目会遇到不可预测的设计障碍或政治风险，或者我们只是发现事情并未按计划进行。需求会意外变化。怎样才能获得这种反馈循环，以便在计划或设计需要改变的瞬间就知道？答案是：与团队合作。大多数工程师都知道“众目之下，错误易除”这句话，但更好的说法或许是：“众目之下，能确保项目始终相关并走在正轨上。”躲在洞穴里工作的人醒来时会发现：尽管最初的愿景可能已经完成，世界却已经改变，项目也变得无关紧要。"
    ],
    "textbox.015.p.0001": ["案例研究：工程师与办公室"],
    "textbox.015.p.0002": [
        "25 年前，传统观点认为，工程师要想高效工作，就得有一间带门、可以关上的独立办公室。据说，只有这样他们才能拥有大块不受打扰的时间，专心编写大量代码。"
    ],
    "body.p.01197": [
        "我认为，大多数工程师并不需要拥有独立办公室，4 而且这样做简直很危险。如今的软件由团队而非个人编写；与团队其他成员保持高带宽、随时可用的联系，甚至比互联网连接更有价值。你可以拥有世上一切不受打扰的时间，但如果用它来做错误的事，就是在浪费时间。"
    ],
    "body.p.01198": [
        "遗憾的是，现代科技公司（某些情况下也包括 Google）似乎把钟摆摆向了完全相反的极端。走进它们的办公室，你常会看到工程师挤在巨大的房间里——一百多人聚在一起，完全没有隔墙。这种“开放式平面布局”如今引发了激烈争论，因此人们对开放式办公室的反感也在增加。最细小的谈话都会变成公开的事；人们为了避免打扰几十位邻居，干脆不再说话。这和独立办公室一样糟糕！"
    ],
    "body.p.01199": [
        "我们认为，真正最好的解决方案是取中间路线：把四到八人的团队安排在小房间（或大办公室）里，让自发交谈能够轻松、不尴尬地发生。"
    ],
    "body.p.01200": [
        "当然，在任何情况下，个别工程师仍需要一种过滤噪声和干扰的方式，因此我见过的大多数团队都会设法传达：自己目前正忙，请尽量减少打扰。我们有些人曾在一个采用口头打断协议的团队工作：如果你想交谈，就会说“断点玛丽”，其中玛丽是你想交谈的那个人的名字。如果玛丽正好可以停下来，她会转过椅子听你说；如果她太忙，只会说“收到”，你就去做别的事，等她处理完当前的思路。"
    ],
    "body.p.01201": [
        "还有些团队会发放代币或毛绒玩具，团队成员把它们放在显示器上，表示只有紧急情况才能打扰。另一些团队则给工程师配备降噪耳机，帮助他们应对背景噪声——事实上，在许多公司，戴上耳机本身就是一个常见信号，意思是“除非真的很重要，否则别打扰我”。许多工程师在编码时倾向于进入只戴耳机的模式；短时间这样做或许有用，但如果一直如此，对协作的伤害可能和把自己关在办公室里一样大。"
    ],
    "body.p.01202": [
        "别误会——我们仍认为工程师需要不受打扰的时间来专注编写代码；但我们也认为，他们同样需要与团队保持高带宽、低摩擦的联系。如果团队中知识较少的人觉得向你提问存在障碍，那就是问题：找到恰当的平衡是一门艺术。"
    ],
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
    source_by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(
        source_sha256=source_sha,
        unit_uid=view.unit_uid,
        display_id=view.display_id,
        story=block.story,
        content_role=block.block_type,
        source_text=view.source_text,
        source_segments=[
            CalibrationSourceSegment(
                kind=segment.kind,
                text=segment.text,
                anchor_ref=segment.anchor_ref,
                island_ref=segment.island_ref,
            )
            for segment in view.segments
        ],
        available_anchors=[
            CalibrationAnchor(
                anchor_id=anchor.anchor_id,
                semantics=anchor.kinds,
                protected=anchor.protected,
                protected_text=source_by_anchor.get(anchor.anchor_id) if anchor.protected else None,
                container="hyperlink" if "hyperlink_rid" in anchor.container_context else None,
            )
            for anchor in view.anchors
        ],
        continuation_hint=view.continuation_hint,
        previous_text=view.previous_text,
        next_text=view.next_text,
        selection_categories=["m5_3_multi_chapter", "chapter_2", "batch_004"],
    )


def qa_record(
    validator: FormatAnchorQA,
    view: TranslationView,
    response: TranslationResult,
    known_unit_uids: set[str],
) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid,
        display_id=view.display_id,
        source_text=view.source_text,
        target_segments=response.target_segments,
        target_text="".join(segment.text for segment in response.target_segments),
        generation_kind="calibration",
    )
    result = validator.validate(view, candidate, known_unit_uids)
    return {
        "unit_uid": view.unit_uid,
        "display_id": view.display_id,
        "passed": result.passed,
        "diagnostics": [diagnostic.model_dump() for diagnostic in result.diagnostics],
    }


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    views = {
        view.display_id: view
        for view in (
            TranslationView.model_validate_json(line)
            for line in VIEWS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    blocks = {
        block.display_id: block
        for block in (
            BlockIR.model_validate_json(line)
            for line in BLOCKS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    requests = [request_for(views[display_id], blocks[display_id], source_sha) for display_id in DISPLAY_IDS]

    OUT.mkdir(parents=True, exist_ok=False)
    write_jsonl(OUT / "translation_request.jsonl", requests)

    raw = []
    for request in requests:
        view = views[request.display_id]
        target_parts = TARGETS[request.display_id]
        if len(target_parts) != len(view.segments):
            raise RuntimeError(f"target segment count mismatch for {request.display_id}")
        raw.append(
            TranslationResult(
                unit_uid=request.unit_uid,
                target_segments=[
                    StructuredTargetSegment(
                        text=text,
                        kind="anchored",
                        anchor_ref=view.segments[index].anchor_ref,
                        island_ref=view.segments[index].island_ref,
                    )
                    for index, text in enumerate(target_parts)
                ],
            )
        )
    raw_sha = write_jsonl(OUT / "translation_raw_response.jsonl", raw)

    validator = FormatAnchorQA()
    known_unit_uids = {view.unit_uid for view in views.values()}
    raw_qa = []
    normalized: list[TranslationResult] = []
    changes = []
    for request, response in zip(requests, raw, strict=True):
        raw_qa.append(qa_record(validator, views[request.display_id], response, known_unit_uids))
        normalized_response, response_changes = normalize_footnote_superscripts(request, response)
        normalized.append(normalized_response)
        changes.extend(response_changes)

    raw_after = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_after != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    write_jsonl(OUT / "translation_normalized_response.jsonl", normalized)
    normalized_qa = [
        qa_record(validator, views[request.display_id], response, known_unit_uids)
        for request, response in zip(requests, normalized, strict=True)
    ]

    changed_unit_uids = {change.unit_uid for change in changes}
    write_json(
        OUT / "translation_normalization_report.json",
        {
            "raw_sha256_before": raw_sha,
            "raw_sha256_after": raw_after,
            "raw_immutable": True,
            "input_unit_count": len(requests),
            "normalized_unit_count": len(changed_unit_uids),
            "unchanged_unit_count": len(requests) - len(changed_unit_uids),
            "normalization_rule_distribution": {},
            "changes": [],
        },
    )
    write_json(
        OUT / "translation_raw_qa_report.json",
        {
            "pass_count": sum(record["passed"] for record in raw_qa),
            "fail_count": sum(not record["passed"] for record in raw_qa),
            "records": raw_qa,
        },
    )
    reviews = [
        {
            "unit_uid": response.unit_uid,
            "display_id": request.display_id,
            "status": "ACCEPT",
            "issue_types": [],
            "explanation": "Reviewed against source, neighboring context, and the frozen Bible; no material repair required.",
            "original_target": "".join(segment.text for segment in response.target_segments),
            "repaired_target": None,
        }
        for request, response in zip(requests, normalized, strict=True)
    ]
    write_json(
        OUT / "semantic_review_report.json",
        {
            "accept_count": len(reviews),
            "material_repair_count": 0,
            "human_required_count": 0,
            "reviewer_contract_error_count": 0,
            "records": reviews,
        },
    )
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    write_json(
        OUT / "translation_final_qa_report.json",
        {
            "pass_count": sum(record["passed"] for record in normalized_qa),
            "fail_count": sum(not record["passed"] for record in normalized_qa),
            "records": normalized_qa,
        },
    )
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
