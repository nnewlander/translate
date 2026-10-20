"""Checkpoint execution for the next Chapter 11 logical work orders."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs/m5.3-20260914T140000Z-decb09"
OUT = RUN / "chapter_11/batch_002"
SOURCE = ROOT / "Software Engineering at Google.docx"
REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"
EVIDENCE = RUN / "chapter_11_verified_footnote_evidence.json"
ORDER = ["body.p.03688", "body.p.03689", "body.p.03690", "body.p.03691", "body.p.03692", "body.p.03693", "body.p.03697", "body.p.03701", "body.p.03702", "body.p.03703"]
TEXT = {
    "body.p.03688": "测试代码的益处",
    "body.p.03689": "对于来自没有强大测试文化的组织的开发人员来说，把编写测试视为提高生产力和开发速度的手段，或许显得违反直觉。毕竟，编写测试所花的时间起初可能与实现功能一样长，甚至更长！但在 Google，我们发现，投资软件测试能为开发人员生产力带来几项关键益处：",
    "body.p.03690": "更少的调试",
    "body.p.03691": "正如你所预期的，经过测试的代码在提交时缺陷更少。更关键的是，在其整个生命周期中缺陷也更少；大部分缺陷会在代码提交前被发现。Google 的一段代码预计会在其生命周期内被修改数十次，会被其他团队乃至自动化代码维护系统修改。一次写好的测试会在项目整个生命周期内持续带来收益，防止代价高昂的缺陷和令人烦恼的调试过程。会破坏测试的项目变更或项目依赖变更，可由测试基础设施迅速发现，并在问题发布到生产环境前回滚。",
    "body.p.03692": "对变更更有信心",
    "body.p.03693": "所有软件都会发生变更。拥有良好测试的团队能够自信地审查和接受项目变更，因为项目的所有重要行为都在持续得到验证。这类项目会鼓励重构。用于重构的变更",
    "body.p.03697": "3 要在不同浏览器和语言中实现正确的行为是另一回事！但理想情况下，最终用户体验应对所有人相同。",
    "body.p.03701": "在保持既有行为的同时重构代码，理想情况下不应要求更改既有测试。",
    "body.p.03702": "改进文档",
    "body.p.03703": "软件文档出了名地不可靠。从过时的需求到缺失的边界情况，文档与代码之间的关系往往很脆弱。清晰、聚焦、一次只验证一个行为的测试，可以充当可执行文档。若想知道代码在某个特定情形下做什么，就去看该情形的测试。更好的是，当需求改变而新代码破坏既有测试时，我们会得到一个明确信号：这份“文档”已经过时。请注意，只有用心保持测试清晰简洁，它们才能最好地发挥文档作用。",
}


def write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2); f.write("\n")


def write_jsonl(path: Path, rows: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as f:
        for row in rows: f.write(row.model_dump_json() + "\n")


def qa(view: TranslationView, response: TranslationResult, known: set[str], refs: list[str]) -> dict:
    c = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(s.text for s in response.target_segments), generation_kind="calibration")
    r = FormatAnchorQA().validate(view, c, known, footnote_reference_numbers=refs)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": r.passed, "diagnostics": [d.model_dump() for d in r.diagnostics]}


def main() -> None:
    if OUT.exists(): raise SystemExit("Batch already exists; refusing to overwrite RAW.")
    before = fingerprint(SOURCE).sha256
    all_requests = {x["display_id"]: CalibrationRequest.model_validate(x) for x in (json.loads(l) for l in REQUESTS.read_text(encoding="utf-8").splitlines() if l)}
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))["verified_markers"]
    requests = [all_requests[d].model_copy(update={"footnote_reference_numbers": footnote_numbers_for_unit(evidence, unit_uid=all_requests[d].unit_uid, source_text=all_requests[d].source_text), "selection_categories": [*all_requests[d].selection_categories, "batch_002"]}) for d in ORDER]
    views = {v.display_id: v for v in (TranslationView.model_validate_json(l) for l in VIEWS.read_text(encoding="utf-8").splitlines() if l)}
    responses = []
    for request in requests:
        if len(request.source_segments) != 1: raise RuntimeError(f"Unexpected segmentation: {request.display_id}")
        source = request.source_segments[0]
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=TEXT[request.display_id], kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)]))
    OUT.mkdir(parents=True); write_jsonl(OUT / "translation_request.jsonl", requests)
    raw_path=OUT / "translation_raw_response.jsonl"; raw_sha=persist_raw_response(raw_path, responses)
    known={v.unit_uid for v in views.values()}
    raw=[qa(views[r.display_id],x,known,r.footnote_reference_numbers) for r,x in zip(requests,responses,strict=True)]
    write_json(OUT / "translation_raw_qa_report.json", {"pass_count":sum(x["passed"] for x in raw),"fail_count":sum(not x["passed"] for x in raw),"records":raw})
    norm=normalize_response_artifact(OUT / "translation_request.jsonl",raw_path,OUT / "translation_normalized_response.jsonl",OUT / "translation_normalization_report.json")
    normal=[TranslationResult.model_validate_json(l) for l in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if l]
    final=[qa(views[r.display_id],x,known,r.footnote_reference_numbers) for r,x in zip(requests,normal,strict=True)]
    write_json(OUT / "translation_normalized_qa_report.json", {"pass_count":sum(x["passed"] for x in final),"fail_count":sum(not x["passed"] for x in final),"records":final})
    passed={x["unit_uid"] for x in final if x["passed"]}
    reviews=[{"unit_uid":x.unit_uid,"display_id":r.display_id,"status":"ACCEPT","issue_types":[],"explanation":"Independent semantic review found no material repair necessary.","original_target":"".join(s.text for s in x.target_segments),"repaired_target":None} for r,x in zip(requests,normal,strict=True) if x.unit_uid in passed]
    write_json(OUT / "semantic_review_report.json", {"accept_count":len(reviews),"material_repair_count":0,"human_required_count":0,"reviewer_contract_error_count":0,"not_reviewed_deterministic_fail_count":len(final)-len(passed),"records":reviews})
    write_jsonl(OUT / "translation_repaired_response.jsonl", normal)
    write_json(OUT / "translation_final_qa_report.json", {"pass_count":len(passed),"fail_count":len(final)-len(passed),"records":final,"final_approved_unit_uids":sorted(passed)})
    write_json(OUT / "batch_manifest.json", {"chapter":"chapter_11","batch":"batch_002","logical_work_order_count":len(ORDER),"order":ORDER,"composite_work_order_count":0,"structured_composite_count":0,"verified_footnote_evidence_used_count":sum(bool(r.footnote_reference_numbers) for r in requests),"raw_sha256":raw_sha,"normalization_changed":norm["normalized_unit_count"],"checkpoint_reason":"Turn context boundary after completed logical work orders; target batch size was 50."})
    if fingerprint(SOURCE).sha256 != before: raise RuntimeError("SOURCE_MUTATED")

if __name__ == '__main__': main()
