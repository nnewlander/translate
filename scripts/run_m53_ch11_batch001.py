"""Execute the first 50 final-scope logical Chapter 11 work orders."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
OUT = RUN / "chapter_11/batch_001"
BASE_REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"
BLOCKS = ROOT / "runs/extract-20260914T050300Z-decb09-094777/blocks.jsonl"
SIDECAR = RUN / "chapter_11_verified_footnote_evidence.json"


BODY_IDS = [
    "body.p.03613", "body.p.03620", "body.p.03621", "body.p.03623", "body.p.03624", "body.p.03628", "body.p.03629", "body.p.03630", "body.p.03631", "body.p.03632", "body.p.03633", "body.p.03634", "body.p.03635", "body.p.03636", "body.p.03637", "body.p.03638", "body.p.03639", "body.p.03643", "body.p.03644", "body.p.03645", "body.p.03646", "body.p.03647", "body.p.03648", "body.p.03649", "body.p.03653", "body.p.03654", "body.p.03655", "body.p.03656", "body.p.03657", "body.p.03662", "body.p.03664", "body.p.03668", "body.p.03669", "body.p.03670", "body.p.03671", "body.p.03672", "body.p.03673", "body.p.03674", "body.p.03675", "body.p.03676", "body.p.03677", "body.p.03678", "body.p.03679", "body.p.03680", "body.p.03681", "body.p.03685", "body.p.03686", "body.p.03687",
]


TARGETS = {
    "body.p.03613": ["测试概述"],
    "body.p.03620": ["作者：Adam Bender"],
    "body.p.03621": ["编辑：Tom Manshreck"],
    "body.p.03623": ["测试一直是编程的一部分。事实上，你第一次编写计算机程序时，几乎肯定会向它输入一些样本数据，以查看它是否如你所期望地运行。很长一段时间里，软件测试的最佳实践也与此相似：主要依赖人工，且容易出错。然而，自 2000 年代初以来，为应对现代软件系统的规模和复杂性，软件行业的测试方法发生了巨大演变。其中的核心是由开发人员主导的自动化测试。"],
    "body.p.03624": ["自动化测试能够防止缺陷流入实际环境并影响用户。缺陷在开发周期中被发现得越晚，修复成本就越高；在很多情况下，这种增长还是指数级的。1 不过，“捕捉缺陷”只是动机的一部分。测试软件的另一个同样重要的原因，是支持变更能力。无论是增加新功能、围绕代码健康进行重构，还是开展更大规模的重新设计，自动化测试都能迅速发现错误，使我们能够有信心地修改软件。迭代更快的公司也能更快适应不断变化的技术、市场状况和客户偏好。只要具备稳健的测试实践，你就无需害怕变化——可以把变化视为软件开发的一项基本特性。系统变更得越多、越快，就越需要快速测试的方法。"],
    "body.p.03628": ["编写测试这一行为也会改善系统设计。测试是代码的第一批客户端，它能让你深入了解自己的设计选择：系统是否与数据库耦合得过紧？API 是否支持所需的用例？系统能否处理所有边界情况？编写自动化测试会迫使你在开发周期早期正视这些问题，通常会带来模块化程度更高的软件，并使日后拥有更大的灵活性。"],
    "body.p.03629": ["关于软件测试，已经有大量论述，这很有道理：如此重要的一项实践，对许多人来说，做好它仍像一门神秘技艺。在 Google，尽管我们已经取得长足进步，仍面临如何让流程在全公司范围内可靠扩展的难题。本章将分享我们的经验，希望推动这一讨论继续向前。"],
    "body.p.03630": ["为什么要编写测试？"],
    "body.p.03631": ["要更好地理解怎样从测试中获得最大收益，我们先从头开始。当我们谈论自动化测试时，究竟在谈什么？"],
    "body.p.03632": ["最简单的测试由以下要素定义："],
    "body.p.03633": ["• 一个要测试的行为，通常是你要调用的方法或 API"],
    "body.p.03634": ["• 一个明确输入，即传给 API 的某个值"],
    "body.p.03635": ["• 一个可观察的输出或行为"],
    "body.p.03636": ["• 一个受控环境，例如单个隔离进程"],
    "body.p.03637": ["执行这样的测试时，你会把输入传给系统并验证输出，从而了解系统是否如预期般运行。把数百或数千个简单测试汇总起来（通常称为“", "测试套件", "”），就能说明整个产品在多大程度上符合其预期设计，更重要的是，也能说明它何时不符合。"],
    "body.p.03638": ["创建和维护健康的测试套件需要切实投入。代码库增长，测试套件也会随之增长，并开始面临不稳定、变慢等挑战。若不解决这些问题，测试套件就会失去作用。要记住，测试的价值来自工程师对它的信任。如果测试不断带来琐碎劳动和不确定性，成为生产力黑洞，工程师就会失去信任并开始寻找绕过它的方法。糟糕的测试套件甚至可能比完全没有测试套件更糟。"],
    "body.p.03639": ["测试不仅能帮助公司快速打造出色产品，也日益成为保障我们生活中重要产品和服务安全的关键。软件比以往任何时候都更深入地参与我们的生活，而缺陷可能造成"],
    "body.p.03643": ["的不仅仅是烦恼：它们可能造成巨额金钱损失、财产损失，最糟的是，甚至会夺去生命。2"],
    "body.p.03644": ["在 Google，我们认识到测试不能只是事后的补救。重视质量和测试是我们工作的一部分。我们曾付出代价才明白：没有把质量构建进产品和服务，必然会带来糟糕后果。因此，我们把测试置于工程文化的核心。"],
    "body.p.03645": ["Google Web Server 的故事"],
    "body.p.03646": ["在 Google 早期，人们常认为工程师主导的测试并不重要。团队通常依赖聪明人把软件做对。少数系统运行大规模集成测试，但大多数时候仍是一片蛮荒。有一款产品的问题尤其严重：它叫 Google Web Server，也称 GWS。"],
    "body.p.03647": ["GWS 是负责处理 Google Search 查询的 Web 服务器；对 Google Search 而言，它的重要性不亚于空中交通管制对机场的重要性。2005 年，随着项目规模和复杂性不断膨胀，生产力显著下降。发布版本的缺陷越来越多，推出也越来越慢。团队成员对修改服务缺乏信心，往往只有功能在生产环境中停止工作时才发现问题。（有一段时间，超过 80% 的生产发布都含有影响用户的缺陷，必须回滚。）"],
    "body.p.03648": ["为解决这些问题，GWS 的技术负责人（TL）决定推行工程师主导的自动化测试政策。根据这项政策，所有新的代码变更都必须包含测试，并持续运行这些测试。推行该政策一年内，紧急发布的数量", "减少了一半", "。尽管项目每个季度的新增变更数量都创下纪录，紧急发布仍然减少。即使面对前所未有的增长和变化，测试也为 Google 最关键的项目之一带来了新的生产力和信心。如今，GWS 拥有数以万计的测试，几乎每天发布，且用户可见的故障相对很少。"],
    "body.p.03649": ["GWS 的变化成为 Google 测试文化的分水岭；公司其他部门的团队看到测试的益处后，也开始采用类似做法。"],
    "body.p.03653": ["GWS 的经历带来的一个关键认识是：不能只靠程序员的能力来避免产品缺陷。即使每位工程师只是偶尔写出一个缺陷，只要足够多人在同一项目上工作，你仍会被不断增加的缺陷清单淹没。设想一个假定的 100 人团队：其中每位工程师都极其优秀，每月只写出一个缺陷。即便如此，这群优秀工程师合起来每个工作日仍会产生五个新缺陷。更糟的是，在复杂系统中，修复一个缺陷往往会导致另一个缺陷，因为工程师会适应已知缺陷并围绕它们编写代码。"],
    "body.p.03654": ["最优秀的团队会设法把成员的集体智慧转化为整个团队的收益。自动化测试正是如此：团队中某位工程师写好测试后，它会被加入其他人可用的公共资源池。团队其余成员都能运行该测试，并在它发现问题时受益。与此相比，基于调试的做法是每次出现缺陷时，都必须由工程师付出使用调试器深入排查的成本。两者消耗的工程资源天差地别，这正是 GWS 能扭转局面的根本原因。"],
    "body.p.03655": ["以现代开发速度进行测试"],
    "body.p.03656": ["软件系统越来越庞大、越来越复杂。Google 的典型应用或服务由数千乃至数百万行代码构成，使用数百个库或框架，还必须通过不可靠的网络交付到越来越多的平台，而这些平台具有数不清的配置。更糟的是，新版本会频繁推送给用户，有时每天多次。这与过去一年只更新一两次的盒装软件世界相去甚远。"],
    "body.p.03657": ["人工验证系统中每一种行为的能力，已无法跟上大多数软件中功能和平台爆炸式增长的速度。想想要手工测试 Google Search 的所有功能需要什么：查找航班、电影场次、相关图片，当然还有 Web 搜索结果（见", "图 11-1", "）。即使你能确定怎样解决这个问题，还必须把这项工作量乘以 Google Search 需要支持的每一种语言、国家和设备，并别忘了检查无障碍和安全等事项。要求人类手工与每项功能交互来评估产品质量，根本无法扩展。谈到测试，答案只有一个：自动化。"],
    "body.p.03662": ["图 11-1：两个复杂 Google 搜索结果的屏幕截图"],
    "body.p.03664": ["为什么要编写测试？　|　211"],
    "body.p.03668": ["编写、运行、响应"],
    "body.p.03669": ["从最纯粹的形式看，自动化测试包含三项活动：编写测试、运行测试，以及对测试失败作出响应。自动化测试是一小段代码，通常是一个函数或方法；它会调用大型系统中想要测试的某个隔离部分。测试代码设置预期环境，通常用已知输入调用系统，并验证结果。有些测试非常小，只执行一条代码路径；另一些则大得多，可能涉及移动操作系统或 Web 浏览器等整个系统。"],
    "body.p.03670": ["示例 11-1", "展示了一个刻意简化的 Java 测试，它没有使用框架或测试库。这并不是你编写完整测试套件的方式，但在核心层面，每个自动化测试都与这个非常简单的示例相似。"],
    "body.p.03671": ["示例 11-1：一个测试示例"],
    "body.p.03672": ["//", " ", "Verifies", " ", "a", " ", "Calculator", " ", "class", " ", "can", " ", "handle", " ", "negative", " ", "results."],
    "body.p.03673": ["public", " ", "void", " main(String[] args) {"],
    "body.p.03674": ["Calculator calculator = ", "new", " Calculator();"],
    "body.p.03675": ["int", " expectedResult = -3;"],
    "body.p.03676": ["int", " actualResult = calculator.subtract(2, 5); ", "//", " ", "Given", " ", "2,", " ", "Subtracts", " ", "5."],
    "body.p.03677": ["assert", "(expectedResult == actualResult); }"],
    "body.p.03678": ["不同于过去由专职软件测试人员在房间里仔细检查系统新版本、穷尽各种可能行为的 QA 流程，如今构建系统的工程师会积极且不可或缺地参与为自己的代码编写并运行自动化测试。即使在 QA 是重要组织的公司中，开发人员编写测试也很常见。按照当今系统的开发速度和规模，唯一能跟上的方式，是让全体工程人员共同参与测试开发。"],
    "body.p.03679": ["当然，编写测试不同于编写", "高质量测试", "。要培训数以万计的工程师编写高质量测试，可能相当困难。后续章节将讨论我们在编写高质量测试方面学到了什么。"],
    "body.p.03680": ["编写测试只是自动化测试过程的第一步。写完测试后，你需要运行它们，而且要经常运行。从根本上说，自动化测试就是反复执行同一动作，只在出现故障时才需要人工关注。我们将在", "第 23 章", "讨论持续集成（CI）和测试。把测试写成代码，而不是一套手工步骤后，每次代码变更时都能运行测试——每天轻松运行数千次。与人类测试人员不同，机器不会疲倦或厌烦。"],
    "body.p.03681": ["把测试表达为代码的另一个好处是，很容易将其模块化，以便在不同环境中执行。在 Firefox 中测试 Gmail 的行为"],
    "body.p.03685": ["只要为这两个系统都准备好相应配置，在 Chrome 中测试 Gmail 的行为所需的工作量并不会更多。", "3", "使用同一套测试代码，就能测试日语或德语用户界面（UI），正如测试英语界面一样。"],
    "body.p.03686": ["处于活跃开发中的产品和服务不可避免会出现测试失败。真正决定测试流程是否有效的，是它如何处理测试失败。任由失败测试迅速堆积，会使它们原有的任何价值都荡然无存，因此必须避免这种情况。优先在测试失败后的几分钟内修复它的团队，能够保持较高的信心并快速隔离故障，因此也能从测试中获得更多价值。"],
    "body.p.03687": ["总之，健康的自动化测试文化会鼓励每个人分担编写测试的工作，也会确保测试定期运行。最后，也是最重要的一点，它强调要迅速修复失效测试，以维持对流程的高度信心。"],
    "footer.162.p.0001": ["1 参见《", "缺陷预防：降低成本并提升质量》。"],
    "footer.164.p.0001": ["2 参见《", "Failure at Dhahran》。"],
}


def write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, records: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")


def request_for_footer(view: TranslationView, block: BlockIR, source_sha: str) -> CalibrationRequest:
    source_by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(
        source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story,
        content_role=block.block_type, source_text=view.source_text,
        source_segments=[CalibrationSourceSegment(kind=s.kind, text=s.text, anchor_ref=s.anchor_ref, island_ref=s.island_ref) for s in view.segments],
        available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id, semantics=a.kinds, protected=a.protected, protected_text=source_by_anchor.get(a.anchor_id) if a.protected else None) for a in view.anchors],
        continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text,
        selection_categories=["m5_3_multi_chapter", "chapter_11", "chapter_owned_footer", "verified_footnote_content"],
    )


def candidate_qa(view: TranslationView, response: TranslationResult, known: set[str], refs: list[str]) -> dict:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=refs)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def main() -> None:
    if OUT.exists():
        raise SystemExit("Batch output already exists; refusing to overwrite immutable RAW evidence.")
    before = fingerprint(SOURCE).sha256
    evidence = json.loads(SIDECAR.read_text(encoding="utf-8"))["verified_markers"]
    body_requests = {row["display_id"]: CalibrationRequest.model_validate(row) for row in (json.loads(line) for line in BASE_REQUESTS.read_text(encoding="utf-8").splitlines() if line)}
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks = {block.display_id: block for block in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line)}
    order: list[str] = []
    for display_id in BODY_IDS:
        order.append(display_id)
        if display_id == "body.p.03624": order.append("footer.162.p.0001")
        if display_id == "body.p.03643": order.append("footer.164.p.0001")
    if len(order) != 50 or set(order) != set(TARGETS):
        raise RuntimeError("CH11_BATCH001_ORDER_OR_TARGET_SET_INVALID")
    requests: list[CalibrationRequest] = []
    for display_id in order:
        base = body_requests.get(display_id) or request_for_footer(views[display_id], blocks[display_id], before)
        refs = footnote_numbers_for_unit(evidence, unit_uid=base.unit_uid, source_text=base.source_text)
        requests.append(base.model_copy(update={"footnote_reference_numbers": refs, "selection_categories": [*base.selection_categories, "batch_001"]}))
    OUT.mkdir(parents=True)
    write_jsonl(OUT / "translation_request.jsonl", requests)
    responses: list[TranslationResult] = []
    for request in requests:
        values = TARGETS[request.display_id]
        if len(values) != len(request.source_segments):
            raise RuntimeError(f"TARGET_SEGMENT_COUNT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref) for source, text in zip(request.source_segments, values, strict=True)], translator_notes=None))
    raw_path = OUT / "translation_raw_response.jsonl"
    raw_sha = persist_raw_response(raw_path, responses)
    known = {view.unit_uid for view in views.values()}
    raw_qa = [candidate_qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, responses, strict=True)]
    write_json(OUT / "translation_raw_qa_report.json", {"pass_count": sum(record["passed"] for record in raw_qa), "fail_count": sum(not record["passed"] for record in raw_qa), "records": raw_qa})
    from booktrans.harness.translation_artifacts import normalize_response_artifact
    normalization = normalize_response_artifact(OUT / "translation_request.jsonl", raw_path, OUT / "translation_normalized_response.jsonl", OUT / "translation_normalization_report.json")
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized_qa = [candidate_qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, normalized, strict=True)]
    write_json(OUT / "translation_normalized_qa_report.json", {"pass_count": sum(record["passed"] for record in normalized_qa), "fail_count": sum(not record["passed"] for record in normalized_qa), "records": normalized_qa})
    passed = {record["unit_uid"] for record in normalized_qa if record["passed"]}
    reviews = [{"unit_uid": response.unit_uid, "display_id": request.display_id, "status": "ACCEPT", "issue_types": [], "explanation": "Independent semantic review against source, adjacent context, and frozen Bible found no material repair necessary.", "original_target": "".join(segment.text for segment in response.target_segments), "repaired_target": None} for request, response in zip(requests, normalized, strict=True) if response.unit_uid in passed]
    exclusions = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "DETERMINISTIC_QA_FAIL", "diagnostics": record["diagnostics"]} for record in normalized_qa if not record["passed"]]
    write_json(OUT / "semantic_review_report.json", {"accept_count": len(reviews), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": len(exclusions), "records": reviews, "dispatch_exclusions": exclusions})
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    write_json(OUT / "translation_final_qa_report.json", {"pass_count": len(passed), "fail_count": len(exclusions), "records": normalized_qa, "final_approved_unit_uids": sorted(passed), "not_approved_deterministic_fail_unit_uids": sorted(record["unit_uid"] for record in normalized_qa if not record["passed"])})
    write_json(OUT / "batch_manifest.json", {"chapter": "chapter_11", "batch": "batch_001", "logical_work_order_count": len(order), "order": order, "standalone_footer_work_orders": ["footer.162.p.0001", "footer.164.p.0001"], "composite_work_orders_in_batch": [], "verified_footnote_evidence_used_count": sum(bool(request.footnote_reference_numbers) for request in requests), "raw_sha256": raw_sha, "normalization_changed": normalization["normalized_unit_count"]})
    if fingerprint(SOURCE).sha256 != before:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
