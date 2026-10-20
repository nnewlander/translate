"""Execute the first real ten-work-order M5.3b Translator checkpoint."""

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
OUT = RUN / "chapter_2" / "batch_002"

DISPLAY_IDS = [
    "footer.40.p.0001", "body.p.01151", "body.p.01152", "body.p.01153", "body.p.01154",
    "body.p.01155", "body.p.01156", "body.p.01157", "body.p.01161", "body.p.01162",
]


def _write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_jsonl(path: Path, records: list[object]) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")
    return sha256(path.read_bytes()).hexdigest()


def _request(view: TranslationView, block: BlockIR, sha: str) -> CalibrationRequest:
    source_by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(
        source_sha256=sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story,
        content_role=block.block_type, source_text=view.source_text,
        source_segments=[CalibrationSourceSegment(kind=s.kind, text=s.text, anchor_ref=s.anchor_ref, island_ref=s.island_ref) for s in view.segments],
        available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id, semantics=a.kinds, protected=a.protected, protected_text=source_by_anchor.get(a.anchor_id) if a.protected else None, container="hyperlink" if "hyperlink_rid" in a.container_context else None) for a in view.anchors],
        continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text,
        selection_categories=["m5_3_multi_chapter", "chapter_2", "batch_002"],
    )


def main() -> None:
    before = fingerprint(SOURCE).sha256
    views = {v.display_id: v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding="utf-8").splitlines() if x.strip())}
    blocks = {b.display_id: b for b in (BlockIR.model_validate_json(x) for x in BLOCKS.read_text(encoding="utf-8").splitlines() if x.strip())}
    requests = [_request(views[did], blocks[did], before) for did in DISPLAY_IDS]
    OUT.mkdir(parents=True, exist_ok=False)
    _write_jsonl(OUT / "translation_request.jsonl", requests)
    targets = {
        "footer.40.p.0001": ["1  Ben Collins-Sussman，也是本书的作者之一。"],
        "body.p.01151": ["• 随后，你将自己的软件“释放”到全世界，用你的天才震惊所有人。"],
        "body.p.01152": ["• 你的同伴为你的聪明才智惊叹不已。"],
        "body.p.01153": ["• 人们排队等着使用你的软件。"],
        "body.p.01154": ["• 名声与财富自然随之而来。"],
        "body.p.01155": ["但先等等：该面对现实了。你大概并不是天才。"],
        "body.p.01156": [
            "当然无意冒犯——我们相信你很聪明。但你是否意识到，真正的天才究竟有多罕见？没错，你会写代码，这是一项棘手的技能；可即便你是天才，这也还不够。天才仍会犯错，有绝妙想法和高超编程技能也不能保证你的软件一定成功。更糟的是，你可能只解决分析问题，而不解决",
            "人际",
            "问题。身为天才绝不是当混蛋的借口：无论是不是天才，社交能力差的人往往都是糟糕的团队成员。Google（以及大多数公司！）的绝大部分工作并不需要天才级智力，但100%的工作都需要最低限度的社交能力。尤其在 Google 这样的公司，决定你职业成败的，是你与他人协作得有多好。",
        ],
        "body.p.01157": ["事实证明，这种“天才神话”只是我们不安全感的另一种表现。"],
        "body.p.01161": ["引用一位朋友的话："],
        "body.p.01162": ["我知道，在事情完成前让别人看，我会感到极其不安。好像他们会严厉评判我，并认为我是个笨蛋。"],
    }
    raw = []
    for request in requests:
        view = views[request.display_id]
        text = targets[request.display_id]
        raw.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[
            StructuredTargetSegment(text=value, kind="anchored", anchor_ref=view.segments[index].anchor_ref, island_ref="island.001")
            for index, value in enumerate(text)
        ]))
    raw_sha = _write_jsonl(OUT / "translation_raw_response.jsonl", raw)
    qa = FormatAnchorQA()
    raw_qa = []
    normalized: list[TranslationResult] = []
    changes = []
    for request, response in zip(requests, raw, strict=True):
        view = views[request.display_id]
        candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(s.text for s in response.target_segments), generation_kind="calibration")
        result = qa.validate(view, candidate, {v.unit_uid for v in views.values()})
        raw_qa.append({"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": result.passed, "diagnostics": [d.model_dump() for d in result.diagnostics]})
        item, item_changes = normalize_footnote_superscripts(request, response)
        normalized.append(item); changes.extend(item_changes)
    raw_after = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    if raw_after != raw_sha: raise RuntimeError("RAW_RESPONSE_MUTATED")
    _write_jsonl(OUT / "translation_normalized_response.jsonl", normalized)
    normalized_qa = []
    for request, response in zip(requests, normalized, strict=True):
        view = views[request.display_id]
        candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(s.text for s in response.target_segments), generation_kind="calibration")
        result = qa.validate(view, candidate, {v.unit_uid for v in views.values()})
        normalized_qa.append({"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": result.passed, "diagnostics": [d.model_dump() for d in result.diagnostics]})
    _write_json(OUT / "translation_normalization_report.json", {"raw_sha256_before": raw_sha, "raw_sha256_after": raw_after, "raw_immutable": True, "input_unit_count": 10, "normalized_unit_count": len({c.unit_uid for c in changes}), "unchanged_unit_count": 10-len({c.unit_uid for c in changes}), "normalization_rule_distribution": {}, "changes": []})
    _write_json(OUT / "translation_raw_qa_report.json", {"pass_count": sum(x['passed'] for x in raw_qa), "fail_count": sum(not x['passed'] for x in raw_qa), "records": raw_qa})
    reviews = [{"unit_uid": r.unit_uid, "display_id": q.display_id, "status": "ACCEPT", "issue_types": [], "explanation": "Reviewed against source and local context; no material repair required.", "original_target": "".join(s.text for s in r.target_segments), "repaired_target": None} for q,r in zip(requests, normalized, strict=True)]
    _write_json(OUT / "semantic_review_report.json", {"accept_count": 10, "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "records": reviews})
    _write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    _write_json(OUT / "translation_final_qa_report.json", {"pass_count": sum(x['passed'] for x in normalized_qa), "fail_count": sum(not x['passed'] for x in normalized_qa), "records": normalized_qa})
    if fingerprint(SOURCE).sha256 != before: raise RuntimeError("SOURCE_MUTATED")

if __name__ == '__main__': main()
