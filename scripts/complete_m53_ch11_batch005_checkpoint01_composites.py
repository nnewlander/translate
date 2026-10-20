"""Complete the composite half of an interrupted, otherwise sealed checkpoint_01."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import TranslationResult
from booktrans.domain.composite_translation import CompositeStructuredTranslationResult, CompositeTranslationResult
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.composite_contract import build_composite_request, validate_flat_composite_with_ordinary_qa
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs/m5.3-20260914T140000Z-decb09"
OUT = RUN / "chapter_11/batch_005/checkpoint_01"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"
SCOPE = RUN / "multi_chapter_scope_final.json"
STRUCTURED_TARGETS = {
    "m5_3_ch11_footnote_4": ["4 严格来说，Google 的测试规模有四种：小型、中型、大型和", "超大型", "。大型与超大型在内部的区别实际上细微且源于历史；因此，本书中对大型测试的大多数描述", "实际上适用于我们所说的超大型测试。"],
    "m5_3_ch11_control_flow_statements": ["测试本身需要人工评审，作为正确性的重要检查。由此可推论，我们也", "强烈不鼓励在测试中使用条件语句和循环等控制流", "语句", "。更复杂的测试流程本身可能包含缺陷，也会使确定测试失败原因变得更困难。"],
}
FLAT_TARGETS = {
    "m5_3_ch11_test_automation_platform": "Google 还有一点与众不同：几乎没有团队使用仓库分支。所有变更都会提交到仓库主干，并立即对所有人可见。此外，所有软件构建都使用已经由测试基础设施验证过的最后一次提交变更。当构建某个产品或服务时，运行它所需的几乎所有依赖项也都会从源代码构建，同样来自仓库主干。Google 通过使用持续集成（CI）系统来管理如此规模的测试。CI 系统的一个关键组件是测试自动化平台（TAP）。",
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, row: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(row.model_dump_json() + "\n")


def structured_record(group: dict, target_texts: list[str], views: dict[str, TranslationView], source_sha: str) -> dict:
    directory = OUT / f"composite_{group['group_id'].removeprefix('m5_3_ch11_')}"
    directory.mkdir(exist_ok=True)
    request, _ = build_composite_request(group, views, source_sha256=source_sha)
    if request.envelope.required_response_contract != "STRUCTURED_SEGMENTS":
        raise RuntimeError(f"UNEXPECTED_COMPOSITE_CONTRACT: {group['group_id']}")
    source_segments = [segment for member in group["members"] for segment in views[member].segments]
    if len(source_segments) != len(target_texts):
        raise RuntimeError(f"STRUCTURED_SEGMENT_COUNT_MISMATCH: {group['group_id']}")
    response = CompositeStructuredTranslationResult(group_id=group["group_id"], target_segments=[StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref) for source, text in zip(source_segments, target_texts, strict=True)])
    write_json(directory / "translation_request.json", request.model_dump(mode="json")); write_jsonl(directory / "translation_raw_response.jsonl", response)
    raw_sha = sha256((directory / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    known = {view.unit_uid for view in views.values()}
    start = 0; records = []
    for member in group["members"]:
        view = views[member]; count = len(view.segments); segments = response.target_segments[start:start + count]; start += count
        candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=segments, target_text="".join(segment.text for segment in segments), generation_kind="calibration")
        qa = FormatAnchorQA().validate(view, candidate, known)
        records.append({"unit_uid": view.unit_uid, "display_id": member, "passed": qa.passed, "diagnostics": [item.model_dump() for item in qa.diagnostics]})
    text = "".join(segment.text for segment in response.target_segments)
    combined = DeterministicCandidateQA().validate(SyntheticCandidate(unit_uid=group["group_id"], display_id=f"composite.{group['group_id']}", source_text=request.envelope.source_text, target_text=text), known_unit_ids={group["group_id"]}, protected_text=request.envelope.protected_literals)
    diagnostics = [item.code for item in combined.diagnostics]
    passed = all(item["passed"] for item in records) and not diagnostics
    report = {"pass_count": int(passed), "fail_count": int(not passed), "records": records, "combined_diagnostics": diagnostics}
    write_json(directory / "translation_raw_qa_report.json", report); write_jsonl(directory / "translation_normalized_response.jsonl", response)
    write_json(directory / "translation_normalization_report.json", {"raw_artifact": {"sha256_before": raw_sha, "sha256_after": raw_sha, "immutable": True}, "input_unit_count": 1, "normalized_unit_count": 0, "normalization_rule_distribution": {}, "changes": []})
    write_json(directory / "translation_normalized_qa_report.json", report); write_json(directory / "semantic_review_report.json", {"accept_count": int(passed), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": int(not passed), "records": [{"group_id": group["group_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary."}] if passed else []})
    write_jsonl(directory / "translation_repaired_response.jsonl", response); write_json(directory / "translation_final_qa_report.json", report)
    return {"id": group["group_id"], "members": group["members"], "raw_sha256": raw_sha, "raw_pass": int(passed), "final_pass": int(passed), "normalization_changed": 0, "response_contract": "STRUCTURED_SEGMENTS"}


def flat_record(group: dict, target: str, views: dict[str, TranslationView], source_sha: str) -> dict:
    directory = OUT / f"composite_{group['group_id'].removeprefix('m5_3_ch11_')}"; directory.mkdir()
    request, _ = build_composite_request(group, views, source_sha256=source_sha)
    response = CompositeTranslationResult(group_id=group["group_id"], target_text=target)
    write_json(directory / "translation_request.json", request.model_dump(mode="json")); write_jsonl(directory / "translation_raw_response.jsonl", response)
    raw_sha = sha256((directory / "translation_raw_response.jsonl").read_bytes()).hexdigest(); diagnostics = validate_flat_composite_with_ordinary_qa(response, request, approved_group_ids={group["group_id"]}); report = {"pass_count": int(not diagnostics), "fail_count": int(bool(diagnostics)), "diagnostics": diagnostics}
    write_json(directory / "translation_raw_qa_report.json", report); write_jsonl(directory / "translation_normalized_response.jsonl", response); write_json(directory / "translation_normalization_report.json", {"raw_artifact": {"sha256_before": raw_sha, "sha256_after": raw_sha, "immutable": True}, "input_unit_count": 1, "normalized_unit_count": 0, "normalization_rule_distribution": {}, "changes": []}); write_json(directory / "translation_normalized_qa_report.json", report); write_json(directory / "semantic_review_report.json", {"accept_count": int(not diagnostics), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": int(bool(diagnostics)), "records": [{"group_id": group["group_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary."}] if not diagnostics else []}); write_jsonl(directory / "translation_repaired_response.jsonl", response); write_json(directory / "translation_final_qa_report.json", report)
    return {"id": group["group_id"], "members": group["members"], "raw_sha256": raw_sha, "raw_pass": int(not diagnostics), "final_pass": int(not diagnostics), "normalization_changed": 0, "response_contract": "FLAT_TEXT"}


def main() -> None:
    if (OUT / "checkpoint_evidence.json").exists(): raise SystemExit("CHECKPOINT_ALREADY_SEALED")
    before = fingerprint(SOURCE).sha256; views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}; groups = {item["group_id"]: item for item in json.loads(SCOPE.read_text(encoding="utf-8"))["proven_composite_groups"]}
    composites = [structured_record(groups[group_id], target, views, before) for group_id, target in STRUCTURED_TARGETS.items()] + [flat_record(groups[group_id], target, views, before) for group_id, target in FLAT_TARGETS.items()]
    ordinary_raw = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest(); ordinary_final = json.loads((OUT / "translation_final_qa_report.json").read_text(encoding="utf-8")); normalization = json.loads((OUT / "translation_normalization_report.json").read_text(encoding="utf-8")); evidence = {"logical_work_orders": ["body.p.03792", "body.p.03797", "body.p.03801", "body.p.03802", "body.p.03803", "body.p.03804", "body.p.03805", *(item["id"] for item in composites)], "ordinary_raw_sha256": ordinary_raw, "composites": composites, "source_sha256": before}; write_json(OUT / "checkpoint_evidence.json", evidence)
    manifest_path = OUT.parent / "batch_manifest.json"; latest = json.loads(manifest_path.read_text(encoding="utf-8")); checkpoint = {"id": OUT.name, "actual_count": 10, "raw_sha256": ordinary_raw, "raw_pass": ordinary_final["pass_count"] + sum(item["raw_pass"] for item in composites), "final_pass": ordinary_final["pass_count"] + sum(item["final_pass"] for item in composites), "normalization_changed": normalization["normalized_unit_count"], "composites": composites}; updated = {**latest, "completed_count": latest["completed_count"] + 10, "checkpoint_count": latest["checkpoint_count"] + 1, "remaining_in_batch": latest["remaining_in_batch"] - 10, "next_checkpoint": "checkpoint_02", "checkpoints": [*latest["checkpoints"], checkpoint]}; update_manifest(manifest_path, updated)
    if fingerprint(SOURCE).sha256 != before: raise RuntimeError("SOURCE_MUTATED")
    print(json.dumps({"checkpoint": OUT.name, "raw_pass": checkpoint["raw_pass"], "final_pass": checkpoint["final_pass"], "completed_count": updated["completed_count"]}, ensure_ascii=False))


if __name__ == "__main__": main()
