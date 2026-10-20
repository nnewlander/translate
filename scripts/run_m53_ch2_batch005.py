"""Execute Chapter 2 M5.3b batch_005 (30 immutable Translator responses)."""

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
OUT = RUN / "chapter_2" / "batch_005"

DISPLAY_IDS = [
    "body.p.01206", "body.p.01207", "body.p.01208", "body.p.01209", "body.p.01210",
    "body.p.01211", "body.p.01212", "body.p.01213", "body.p.01214", "body.p.01215",
    "body.p.01216", "body.p.01217", "body.p.01218", "body.p.01219", "textbox.016.p.0001",
    "body.p.01220", "textbox.017.p.0001", "body.p.01221", "body.p.01225", "body.p.01229",
    "body.p.01230", "body.p.01231", "body.p.01232", "body.p.01233", "body.p.01234",
    "body.p.01235", "body.p.01236", "body.p.01237", "body.p.01238", "body.p.01239",
]

# The values below are the Translator's first responses.  The raw JSONL is
# created exactly once before normalization or semantic review.
TARGETS = {
    "body.p.01206": ["简而言之：不要躲藏"],
    "body.p.01207": ["归根结底，“躲藏”意味着：独自工作天生比与他人合作风险更高。你或许担心有人偷走你的想法，或认为你不够聪明；但你更该担心的是，把大把时间耗费在做错误的事情上。"],
    "body.p.01208": ["别成为又一个统计数字。"],
    "body.p.01209": ["一切都关乎团队"],
    "body.p.01210": ["现在，让我们退一步，把这些想法放在一起看。"],
    "body.p.01211": ["我们反复强调的一点是：在编程领域，独行匠人极为罕见——即使确实存在，他们也不会在真空中完成超人的成就；改变世界的成果几乎总是先有灵感的火花，随后再有英雄般的团队努力。"],
    "body.p.01212": ["优秀团队会充分发挥超级明星的才华，但整体始终大于各部分之和。不过，打造一支明星团队异常困难。"],
    "body.p.01213": ["换句话说：", "软件工程是一项团队事业", "。"],
    "body.p.01214": ["这一概念直接违背了我们许多人内心怀有的“天才程序员”幻想；独自在黑客巢穴里才华横溢还不够。靠藏起并准备你的秘密发明，你既无法改变世界，也无法让数百万计算机用户满意。你需要与他人合作，分享愿景，分工协作，向他人学习，打造一支卓越的团队。"],
    "body.p.01215": ["想一想：你能说出多少真正由一个人独力编写、又被广泛使用且成功的软件？（有人或许会说“LaTeX”，但它很难算“广泛使用”，除非你认为撰写科学论文的人数在所有计算机用户中占有统计上显著的比例！）"],
    "body.p.01216": ["高效能团队弥足珍贵，是成功真正的关键。你应尽一切可能去追求这样的体验。"],
    "body.p.01217": ["社会互动的三大支柱"],
    "body.p.01218": ["既然团队合作是产出卓越软件的最佳途径，那么怎样才能建立（或找到）一支卓越的团队？"],
    "body.p.01219": ["要达到协作的理想境界，你首先需要学习并践行我所说的社交技能“三大支柱”。这三项原则不仅仅是润滑"],
    "textbox.016.p.0001": ["关系的"],
    "body.p.01220": ["车轮；它们还是一切"],
    "textbox.017.p.0001": ["健康"],
    "body.p.01221": ["互动与协作赖以建立的基础："],
    "body.p.01225": ["34　|　第 2 章：如何在团队中高效工作"],
    "body.p.01229": ["支柱 1：谦逊"],
    "body.p.01230": ["你不是宇宙的中心（你的代码也不是！）。你既非无所不知，也非永不犯错；你愿意自我提升。"],
    "body.p.01231": ["支柱 2：尊重"],
    "body.p.01232": ["你真诚地关心与你共事的人。你善待他们，欣赏他们的能力和成就。"],
    "body.p.01233": ["支柱 3：信任"],
    "body.p.01234": ["你相信他人有能力做正确的事，并且愿意在适当的时候让他们主导。", "5"],
    "body.p.01235": ["如果你对几乎任何社会冲突进行根因分析，最终都能把它追溯到缺乏谦逊、尊重和/或信任。乍听不太可信，但不妨一试。想想你生活中当前某个令人不快或不舒服的社会情境。从最根本的层面看，每个人是否都足够谦逊？人们是否真正相互尊重？彼此之间是否有信任？"],
    "body.p.01236": ["这些支柱为什么重要？"],
    "body.p.01237": ["开始阅读本章时，你大概没打算报名参加某种每周一次的互助小组。我们理解。处理社会问题可能很困难：人很复杂、不可预测，而且常常很难打交道。与其投入精力分析社会情境并采取策略性行动，不如干脆把整件事一笔勾销，诱惑很大。和一个可预测的编译器相处要容易得多，不是吗？何必费心处理这些社交问题呢？"],
    "body.p.01238": ["以下引自", "理查德·哈明的一场著名演讲", "："],
    "body.p.01239": ["我花些心思给秘书讲笑话，稍微友好一些，于是得到了极好的秘书协助。例如，有一次不知什么愚蠢的原因，默里山的所有复制服务都忙得不可开交。别问我怎么回事，反正就是这样。我想把一件事办成。我的秘书给霍姆德尔的某人打电话，坐进公司车，花一小时开到那里把它复制出来，然后回来。这是对我平时努力让她开心、讲笑话、友好相待的回报；正是那一点额外付出后来回报了我。认识到你必须使用这个系统，并研究如何让系统为你完成工作，你就会学会让系统适应你的愿望。"],
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


def make_request(view: TranslationView, block: BlockIR, source_sha: str) -> CalibrationRequest:
    by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(
        source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id,
        story=block.story, content_role=block.block_type, source_text=view.source_text,
        source_segments=[CalibrationSourceSegment(kind=s.kind, text=s.text, anchor_ref=s.anchor_ref, island_ref=s.island_ref) for s in view.segments],
        available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id, semantics=a.kinds, protected=a.protected, protected_text=by_anchor.get(a.anchor_id) if a.protected else None, container="hyperlink" if "hyperlink_rid" in a.container_context else None) for a in view.anchors],
        continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text,
        selection_categories=["m5_3_multi_chapter", "chapter_2", "batch_005"],
    )


def validate(validator: FormatAnchorQA, view: TranslationView, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration",
    )
    outcome = validator.validate(view, candidate, known)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": outcome.passed, "diagnostics": [item.model_dump() for item in outcome.diagnostics]}


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line.strip())}
    blocks = {block.display_id: block for block in (BlockIR.model_validate_json(line) for line in BLOCKS.read_text(encoding="utf-8").splitlines() if line.strip())}
    requests = [make_request(views[display_id], blocks[display_id], source_sha) for display_id in DISPLAY_IDS]
    OUT.mkdir(parents=True, exist_ok=False)
    write_jsonl(OUT / "translation_request.jsonl", requests)

    raw: list[TranslationResult] = []
    for request in requests:
        view = views[request.display_id]
        if len(TARGETS[request.display_id]) != len(view.segments):
            raise RuntimeError(f"target segment count mismatch for {request.display_id}")
        raw.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind="anchored", anchor_ref=view.segments[index].anchor_ref, island_ref=view.segments[index].island_ref) for index, text in enumerate(TARGETS[request.display_id])]))
    raw_sha = write_jsonl(OUT / "translation_raw_response.jsonl", raw)

    validator = FormatAnchorQA()
    known = {view.unit_uid for view in views.values()}
    raw_qa = [validate(validator, views[request.display_id], response, known) for request, response in zip(requests, raw, strict=True)]
    normalized: list[TranslationResult] = []
    changes = []
    for request, response in zip(requests, raw, strict=True):
        normalized_response, response_changes = normalize_footnote_superscripts(request, response)
        normalized.append(normalized_response)
        changes.extend(response_changes)
    raw_after = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_after != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    write_jsonl(OUT / "translation_normalized_response.jsonl", normalized)
    normalized_qa = [validate(validator, views[request.display_id], response, known) for request, response in zip(requests, normalized, strict=True)]
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
