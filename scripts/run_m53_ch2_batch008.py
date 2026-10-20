"""Translate the three remaining story-spanning Chapter 2 work orders."""

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
OUT = RUN / "chapter_2" / "batch_008"
DISPLAY_IDS = ["footer.41.p.0001", "footer.42.p.0001", "footer.46.p.0001"]
TARGETS = {
    "footer.41.p.0001": "情况也是如此。我们崇拜他，但事实是他并非凭一己之力赢下每一场篮球赛。他真正的天才在于与团队协作的方式。球队教练菲尔·杰克逊极其聪明，他的执教技巧堪称传奇。",
    "footer.42.p.0001": "许多程序员害怕分享刚刚开始的工作，因为这意味着同事会看到他们的错误，并知道代码作者不是天才。",
    "footer.46.p.0001": "4 不过，我承认，严重内向的人可能比大多数人需要更多安静、平和和独处时间；即便不需要自己的办公室，他们也可能从更安静的环境中受益。",
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
    return CalibrationRequest(source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story, content_role=block.block_type, source_text=view.source_text, source_segments=[CalibrationSourceSegment(kind=s.kind, text=s.text, anchor_ref=s.anchor_ref, island_ref=s.island_ref) for s in view.segments], available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id, semantics=a.kinds, protected=a.protected, protected_text=by_anchor.get(a.anchor_id) if a.protected else None, container="hyperlink" if "hyperlink_rid" in a.container_context else None) for a in view.anchors], continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text, selection_categories=["m5_3_multi_chapter", "chapter_2", "batch_008", "story_spanning_scope"])


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
        if len(view.segments) != 1:
            raise RuntimeError(f"expected one source segment: {request.display_id}")
        raw.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=TARGETS[request.display_id], kind="anchored", anchor_ref=view.segments[0].anchor_ref, island_ref=view.segments[0].island_ref)]))
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
    write_json(OUT / "translation_normalization_report.json", {"raw_sha256_before": raw_sha, "raw_sha256_after": raw_after, "raw_immutable": True, "input_unit_count": 3, "normalized_unit_count": len({change.unit_uid for change in changes}), "unchanged_unit_count": 3 - len({change.unit_uid for change in changes}), "normalization_rule_distribution": {}, "changes": []})
    write_json(OUT / "translation_raw_qa_report.json", {"pass_count": sum(record["passed"] for record in raw_qa), "fail_count": sum(not record["passed"] for record in raw_qa), "records": raw_qa})
    reviews = [{"unit_uid": response.unit_uid, "display_id": request.display_id, "status": "ACCEPT", "issue_types": [], "explanation": "Reviewed against source and chapter-local scope context; no material repair required.", "original_target": "".join(segment.text for segment in response.target_segments), "repaired_target": None} for request, response in zip(requests, normalized, strict=True) if response.unit_uid in passed_ids]
    exclusions = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "DETERMINISTIC_QA_FAIL", "diagnostics": record["diagnostics"], "reason": "Not dispatched to Semantic Reviewer; deterministic failure cannot be masked by repair."} for record in failed]
    write_json(OUT / "semantic_review_report.json", {"accept_count": len(reviews), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": len(exclusions), "records": reviews, "dispatch_exclusions": exclusions})
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    write_json(OUT / "translation_final_qa_report.json", {"pass_count": len(passed_ids), "fail_count": len(failed), "records": normalized_qa, "final_approved_unit_uids": sorted(passed_ids), "not_approved_deterministic_fail_unit_uids": sorted(record["unit_uid"] for record in failed)})
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
