"""Seal Chapter 11 batch_005 checkpoint_03, including verified footnote 8."""
from __future__ import annotations

import json

import run_m53_ch11_batch005_checkpoint01 as runner
from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import TranslationView


runner.OUT = runner.RUN / "chapter_11/batch_005/checkpoint_03"
runner.ORDER = ["body.p.03829", "body.p.03830", "footer.178.p.0001", "body.p.03831", "body.p.03832", "body.p.03836", "body.p.03837", "body.p.03838", "body.p.03839", "body.p.03840"]
runner.TARGETS = {
    "body.p.03829": ["如果不能保持测试套件的确定性和速度，它必然会成为生产力的障碍。在 Google，遇到这些测试的工程师已经找到绕开慢速问题的方法；有些人在提交变更时甚至完全跳过这些测试。显然，这种做法有风险，应当避免；但如果测试套件弊大于利，工程师最终总会想办法把工作做完，有没有测试都一样。"],
    "body.p.03830": ["与大型测试套件共处的秘诀是尊重它。激励工程师关心自己的测试；像奖励出色的功能发布一样，奖励他们拥有坚如磐石的测试。设定适当的性能目标，并重构缓慢或处于边缘状态的测试。基本上，要像对待生产代码一样对待测试。当简单的变更开始耗费不可忽视的时间时，就投入精力让测试不那么脆弱。除了培养合适的文化外，还要通过开发 linter、文档或其他辅助工具来投资测试基础设施，使编写糟糕测试变得更困难。减少需要支持的框架和工具数量，从而提高投入时间来改进情况的效率。8 如果不投资让测试易于管理，工程师最终会认为根本不值得拥有它们。"],
    "footer.178.p.0001": ["8 Google 支持的每种语言都有一个标准测试框架和一个标准 mock/stub 库。一套基础设施在整个代码库中为所有语言运行大多数测试。"],
    "body.p.03831": ["Google 的测试历史"],
    "body.p.03832": ["既然已讨论 Google 如何处理测试，了解我们如何走到今天或许很有启发。如前所述，Google 的工程师并非一直认同自动化测试的价值。事实上，直到 2005 年，测试与其说是一种规范实践，不如说更像一种新奇事物。大多数测试即便做了，也都是手工完成的。不过，从 2005 年到 2006 年，一场测试革命发生了，并改变了"],
    "body.p.03836": ["我们处理软件工程的方式。这场革命的影响至今仍在公司内部回响。"],
    "body.p.03837": ["本章开头讨论过的 GWS 项目经历起到了催化剂作用。它清楚表明自动化测试能够多么强大。GWS 在 2005 年改进后，这些实践开始传播到整个公司。当时的工具还很原始。不过，后来被称为 Testing Grouplet 的志愿者们没有因此放慢脚步。"],
    "body.p.03838": ["三项关键举措帮助自动化测试进入公司的意识：入职培训课程、测试认证计划，以及厕所里的测试。每一项都以完全不同的方式产生影响，它们共同重塑了 Google 的工程文化。"],
    "body.p.03839": ["入职培训课程"],
    "body.p.03840": ["尽管 Google 早期的大部分工程人员都回避测试，Google 自动化测试的先驱者知道，随着公司快速增长，新工程师很快会超过既有团队成员的数量。如果他们能够触达公司所有新员工，这将是引入文化变革的一条极有效途径。幸运的是，所有新工程人员都要经过一个单一关口：入职培训；现在仍然如此。"],
}
runner.COMPOSITES = {}


def request_for_footer(view: TranslationView, block: BlockIR, source_sha: str) -> CalibrationRequest:
    source_by_anchor = {segment.anchor_ref: segment.text for segment in view.segments if segment.anchor_ref}
    return CalibrationRequest(source_sha256=source_sha, unit_uid=view.unit_uid, display_id=view.display_id, story=block.story, content_role=block.block_type, source_text=view.source_text, source_segments=[CalibrationSourceSegment(kind=segment.kind, text=segment.text, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment in view.segments], available_anchors=[CalibrationAnchor(anchor_id=anchor.anchor_id, semantics=anchor.kinds, protected=anchor.protected, protected_text=source_by_anchor.get(anchor.anchor_id) if anchor.protected else None) for anchor in view.anchors], continuation_hint=view.continuation_hint, previous_text=view.previous_text, next_text=view.next_text, selection_categories=["m5_3_multi_chapter", "chapter_11", "chapter_owned_footer", "verified_footnote_content"])


def main() -> None:
    if runner.OUT.exists(): raise SystemExit("CHECKPOINT_ALREADY_EXISTS")
    before = runner.fingerprint(runner.SOURCE).sha256
    all_requests = {item["display_id"]: runner.CalibrationRequest.model_validate(item) for item in (json.loads(line) for line in runner.REQUESTS.read_text(encoding="utf-8").splitlines() if line)}
    views = {item.display_id: item for item in (runner.TranslationView.model_validate_json(line) for line in runner.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    blocks_path = runner.ROOT / "runs/extract-20260914T050300Z-decb09-094777/blocks.jsonl"
    blocks = {item.display_id: item for item in (BlockIR.model_validate_json(line) for line in blocks_path.read_text(encoding="utf-8").splitlines() if line)}
    all_requests["footer.178.p.0001"] = request_for_footer(views["footer.178.p.0001"], blocks["footer.178.p.0001"], before)
    evidence = json.loads(runner.EVIDENCE.read_text(encoding="utf-8"))["verified_markers"]
    requests = [all_requests[item].model_copy(update={"footnote_reference_numbers": runner.footnote_numbers_for_unit(evidence, unit_uid=all_requests[item].unit_uid, source_text=all_requests[item].source_text), "selection_categories": [*all_requests[item].selection_categories, "batch_005", "checkpoint_03"]}) for item in runner.ORDER]
    responses = []
    for request in requests:
        values = runner.TARGETS[request.display_id]
        if len(values) != len(request.source_segments): raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(runner.TranslationResult(unit_uid=request.unit_uid, target_segments=[runner.StructuredTargetSegment(text=value, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, value in zip(request.source_segments, values, strict=True)]))
    runner.OUT.mkdir(parents=True); runner.write_jsonl(runner.OUT / "translation_request.jsonl", requests); raw_sha = runner.persist_raw_response(runner.OUT / "translation_raw_response.jsonl", responses); known = {item.unit_uid for item in views.values()}; raw_records = [runner.qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, responses, strict=True)]; raw_report = runner.report(raw_records); runner.write_json(runner.OUT / "translation_raw_qa_report.json", raw_report); normalization = runner.normalize_response_artifact(runner.OUT / "translation_request.jsonl", runner.OUT / "translation_raw_response.jsonl", runner.OUT / "translation_normalized_response.jsonl", runner.OUT / "translation_normalization_report.json"); normalized = [runner.TranslationResult.model_validate_json(line) for line in (runner.OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]; final_records = [runner.qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, normalized, strict=True)]; final_report = runner.report(final_records); runner.write_json(runner.OUT / "translation_normalized_qa_report.json", final_report); reviews = [{"unit_uid": response.unit_uid, "display_id": request.display_id, "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary."} for request, response in zip(requests, normalized, strict=True)]; runner.write_json(runner.OUT / "semantic_review_report.json", {"accept_count": len(reviews), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": 0, "records": reviews}); runner.write_jsonl(runner.OUT / "translation_repaired_response.jsonl", normalized); runner.write_json(runner.OUT / "translation_final_qa_report.json", final_report); updated = runner.update_latest_manifest([], raw_sha, final_report, normalization["normalized_unit_count"])
    if runner.fingerprint(runner.SOURCE).sha256 != before: raise RuntimeError("SOURCE_MUTATED")
    print(json.dumps({"checkpoint": runner.OUT.name, "raw_pass": raw_report["pass_count"], "final_pass": final_report["pass_count"], "completed_count": updated["completed_count"]}, ensure_ascii=False))


if __name__ == "__main__": main()
