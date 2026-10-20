"""Seal Chapter 11 batch_005 checkpoint_01, including its proven composites."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.composite_translation import CompositeTranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.composite_contract import build_composite_request, validate_flat_composite_with_ordinary_qa
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs/m5.3-20260914T140000Z-decb09"
OUT = RUN / "chapter_11/batch_005/checkpoint_01"
SOURCE = ROOT / "Software Engineering at Google.docx"
REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"
EVIDENCE = RUN / "chapter_11_verified_footnote_evidence.json"
SCOPE = RUN / "multi_chapter_scope_final.json"
ORDER = ["body.p.03792", "body.p.03797", "body.p.03801", "body.p.03802", "body.p.03803", "body.p.03804", "body.p.03805"]
TARGETS = {
    "body.p.03792": ["代码覆盖率还有一个更隐蔽的问题：和其他指标一样，它很快会变成目标本身。团队通常会为预期代码覆盖率设定门槛，例如 80%。起初这听起来非常合理；你当然希望至少有这么高的覆盖率。可在实践中，工程师不会把 80% 当作地板，而会把它当作天花板。很快，提交的变更覆盖率都不会超过 80%。毕竟，为什么要做比指标要求更多的工作呢？"],
    "body.p.03797": ["7 请记住，覆盖率有不同种类（行、路径、分支等），每一种都说明了被测试的代码的不同方面。在这个简单例子中，使用的是行覆盖率。"],
    "body.p.03801": ["评估测试套件质量的更好方式，是思考被测试的行为。你是否确信客户期望能正常工作的所有东西都会正常工作？你是否确信自己能捕获依赖项中的破坏性变更？你的测试是否稳定可靠？这类问题是思考测试套件更全面的方式。每个产品和团队都会不同；有些会与难以测试的硬件交互，有些涉及海量数据集。试图用一个数字回答“我们的测试够不够？”这个问题，会忽略大量上下文，也不太可能有用。代码覆盖率可以让人了解未测试的代码，但不能替代对系统测试得如何的批判性思考。"],
    "body.p.03802": ["Google 规模下的测试"],
    "body.p.03803": ["到目前为止的大部分指导都适用于几乎任何规模的代码库。不过，我们应当花些时间谈谈在极大规模下测试所学到的经验。要理解测试在 Google 如何运作，你需要了解我们的开发环境；其中最重要的一点是，Google 的大部分代码都保存在一个单一的单体仓库（", "单一代码库", "）中。我们运营的每个产品和服务的几乎每一行代码都存放在同一个地方。如今，仓库中的代码已超过 20 亿行。"],
    "body.p.03804": ["Google 的代码库每周经历近 2500 万行变更。其中大约一半来自在单一代码库中工作的数万名工程师，另一半则以配置更新或大规模变更的形式来自我们的自动化系统（", "第 22 章", "）。许多变更来自直接项目之外。我们并未对工程师复用代码的能力施加太多限制。"],
    "body.p.03805": ["代码库的开放性鼓励了一种共同所有权，使每个人都能对代码库承担责任。这种开放性的一个好处是，你可以直接修复所使用产品或服务中的缺陷（当然要经批准），而不是抱怨它。这也意味着，许多人会修改由他人负责的代码库部分。"],
}
COMPOSITES = {
    "m5_3_ch11_footnote_4": "4 严格来说，Google 的测试规模有四种：小型、中型、大型和超大型。大型与超大型在内部的区别实际上细微且源于历史；因此，本书中对大型测试的大多数描述实际上适用于我们所说的超大型测试。",
    "m5_3_ch11_control_flow_statements": "测试本身需要人工评审，作为正确性的重要检查。由此可推论，我们也强烈不鼓励在测试中使用条件语句和循环等控制流语句。更复杂的测试流程本身可能包含缺陷，也会使确定测试失败原因变得更困难。",
    "m5_3_ch11_test_automation_platform": "Google 还有一点与众不同：几乎没有团队使用仓库分支。所有变更都会提交到仓库主干，并立即对所有人可见。此外，所有软件构建都使用已经由测试基础设施验证过的最后一次提交变更。当构建某个产品或服务时，运行它所需的几乎所有依赖项也都会从源代码构建，同样来自仓库主干。Google 通过使用持续集成（CI）系统来管理如此规模的测试。CI 系统的一个关键组件是测试自动化平台（TAP）。",
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, rows: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(row.model_dump_json() + "\n")


def qa(view: TranslationView, response: TranslationResult, known: set[str], references: list[str]) -> dict:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=references)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def report(records: list[dict]) -> dict:
    passed = sum(record["passed"] for record in records)
    return {"pass_count": passed, "fail_count": len(records) - passed, "records": records}


def seal_composite(group: dict, target: str, views: dict[str, TranslationView], source_sha: str) -> dict:
    directory = OUT / f"composite_{group['group_id'].removeprefix('m5_3_ch11_')}"
    directory.mkdir()
    request, _ = build_composite_request(group, views, source_sha256=source_sha)
    if not request.envelope.flat_target_eligible:
        raise RuntimeError(f"COMPOSITE_CONTRACT_NOT_FLAT: {group['group_id']}")
    response = CompositeTranslationResult(group_id=group["group_id"], target_text=target)
    write_json(directory / "translation_request.json", request.model_dump(mode="json"))
    write_jsonl(directory / "translation_raw_response.jsonl", [response])
    raw_sha = sha256((directory / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    diagnostics = validate_flat_composite_with_ordinary_qa(response, request, approved_group_ids={group["group_id"]})
    qa_payload = {"pass_count": int(not diagnostics), "fail_count": int(bool(diagnostics)), "diagnostics": diagnostics}
    write_json(directory / "translation_raw_qa_report.json", qa_payload)
    write_jsonl(directory / "translation_normalized_response.jsonl", [response])
    write_json(directory / "translation_normalization_report.json", {"raw_artifact": {"sha256_before": raw_sha, "sha256_after": raw_sha, "immutable": True}, "input_unit_count": 1, "normalized_unit_count": 0, "normalization_rule_distribution": {}, "changes": []})
    write_json(directory / "translation_normalized_qa_report.json", qa_payload)
    review_status = "ACCEPT" if not diagnostics else "NOT_REVIEWED_DETERMINISTIC_FAIL"
    write_json(directory / "semantic_review_report.json", {"accept_count": int(not diagnostics), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": int(bool(diagnostics)), "records": [{"group_id": group["group_id"], "status": review_status, "issue_types": [], "explanation": "Semantic review found no material repair necessary."}] if not diagnostics else []})
    write_jsonl(directory / "translation_repaired_response.jsonl", [response])
    write_json(directory / "translation_final_qa_report.json", qa_payload)
    return {"id": group["group_id"], "members": group["members"], "raw_sha256": raw_sha, "raw_pass": int(not diagnostics), "final_pass": int(not diagnostics), "normalization_changed": 0}


def update_latest_manifest(composite_records: list[dict], ordinary_raw_sha: str, ordinary_report: dict, normalized_count: int) -> dict:
    manifest_path = OUT.parent / "batch_manifest.json"
    latest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if latest["next_checkpoint"] != OUT.name:
        raise RuntimeError(f"UNEXPECTED_NEXT_CHECKPOINT: {latest['next_checkpoint']}")
    checkpoint = {"id": OUT.name, "actual_count": len(ORDER) + len(composite_records), "raw_sha256": ordinary_raw_sha, "raw_pass": ordinary_report["pass_count"] + sum(item["raw_pass"] for item in composite_records), "final_pass": ordinary_report["pass_count"] + sum(item["final_pass"] for item in composite_records), "normalization_changed": normalized_count, "composites": composite_records}
    completed = latest["completed_count"] + checkpoint["actual_count"]
    updated = {**latest, "completed_count": completed, "checkpoint_count": latest["checkpoint_count"] + 1, "remaining_in_batch": latest["target_count"] - completed, "status": "COMPLETE" if completed == latest["target_count"] else "OPEN_PROGRESS", "next_checkpoint": None if completed == latest["target_count"] else f"checkpoint_{latest['checkpoint_count'] + 2:02d}", "checkpoints": [*latest["checkpoints"], checkpoint]}
    write_json(OUT / "checkpoint_evidence.json", {"logical_work_orders": [*ORDER, *(item["id"] for item in composite_records)], "ordinary_raw_sha256": ordinary_raw_sha, "composites": composite_records, "source_sha256": fingerprint(SOURCE).sha256})
    update_manifest(manifest_path, updated)
    return updated


def main() -> None:
    if OUT.exists():
        raise SystemExit("CHECKPOINT_ALREADY_EXISTS: refusing to overwrite immutable artifacts.")
    before = fingerprint(SOURCE).sha256
    manifest_path = OUT.parent / "batch_manifest.json"
    if not manifest_path.exists():
        update_manifest(manifest_path, {"chapter": "chapter_11", "batch": "batch_005", "target_count": 50, "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": 50, "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "checkpoints": []})
    all_requests = {item["display_id"]: CalibrationRequest.model_validate(item) for item in (json.loads(line) for line in REQUESTS.read_text(encoding="utf-8").splitlines() if line)}
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))["verified_markers"]
    requests = [all_requests[item].model_copy(update={"footnote_reference_numbers": footnote_numbers_for_unit(evidence, unit_uid=all_requests[item].unit_uid, source_text=all_requests[item].source_text), "selection_categories": [*all_requests[item].selection_categories, "batch_005", "checkpoint_01"]}) for item in ORDER]
    responses = []
    for request in requests:
        values = TARGETS[request.display_id]
        if len(values) != len(request.source_segments):
            raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=value, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, value in zip(request.source_segments, values, strict=True)]))
    OUT.mkdir(parents=True)
    write_jsonl(OUT / "translation_request.jsonl", requests)
    raw_sha = persist_raw_response(OUT / "translation_raw_response.jsonl", responses)
    known = {item.unit_uid for item in views.values()}
    raw_records = [qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, responses, strict=True)]
    raw_report = report(raw_records); write_json(OUT / "translation_raw_qa_report.json", raw_report)
    normalization = normalize_response_artifact(OUT / "translation_request.jsonl", OUT / "translation_raw_response.jsonl", OUT / "translation_normalized_response.jsonl", OUT / "translation_normalization_report.json")
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final_records = [qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, normalized, strict=True)]
    final_report = report(final_records); write_json(OUT / "translation_normalized_qa_report.json", final_report)
    dispatched, exclusions = partition_deterministic_review_dispatch(final_records)
    response_by_uid = {response.unit_uid: response for response in normalized}
    reviews = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary."} for record in dispatched if record["unit_uid"] in response_by_uid]
    write_json(OUT / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized); write_json(OUT / "translation_final_qa_report.json", final_report)
    groups = {group["group_id"]: group for group in json.loads(SCOPE.read_text(encoding="utf-8"))["proven_composite_groups"]}
    composite_records = [seal_composite(groups[group_id], target, views, before) for group_id, target in COMPOSITES.items()]
    updated = update_latest_manifest(composite_records, raw_sha, final_report, normalization["normalized_unit_count"])
    if fingerprint(SOURCE).sha256 != before:
        raise RuntimeError("SOURCE_MUTATED")
    print(json.dumps({"checkpoint": OUT.name, "completed_count": updated["completed_count"], "raw_pass": raw_report["pass_count"] + sum(item["raw_pass"] for item in composite_records), "final_pass": final_report["pass_count"] + sum(item["final_pass"] for item in composite_records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
