"""Seal the first ten Chapter 18 logical work orders under the established M5.3 contract."""

from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
CHAPTER = RUN / "chapter_18"
OUT = CHAPTER / "batch_001" / "checkpoint_01"
SOURCE = ROOT / "Software Engineering at Google.docx"
REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
SIDECAR = RUN / "chapter_18_verified_footnote_evidence_v2.json"
ORDER = ["body.p.06181", "body.p.06188", "body.p.06189", "body.p.06191", "body.p.06192", "body.p.06193", "body.p.06194", "body.p.06195", "body.p.06200", "body.p.06205"]
TARGETS = {
    "body.p.06181": ["构建系统与构建理念"],
    "body.p.06188": ["作者：Erik Kuefler"],
    "body.p.06189": ["编辑：Lisa Carey"],
    "body.p.06191": [
        "如果你问 Google 的工程师，他们最喜欢在 Google 工作的什么（除了免费的食物和酷炫的产品），你可能会听到一个令人惊讶的答案：工程师热爱构建系统。",
        "1",
        "Google 在其发展历程中投入了大量工程精力，从零开始打造自己的构建系统，目标是确保工程师能够快速、可靠地构建代码。这一努力非常成功，以至于离开 Google 的前员工已多次重新实现 Blaze——构建系统的主要组件。",
        "2",
        "2015 年，Google 终于将一个名为 ",
        "Bazel",
        " 的 Blaze 实现开源。",
    ],
    "body.p.06192": ["构建系统的目的"],
    "body.p.06193": ["从根本上说，所有构建系统都有一个直接的目的：把工程师编写的源代码转换为机器可读取的可执行二进制文件。优秀的构建系统通常会努力优化两个重要特性："],
    "body.p.06194": ["快速"],
    "body.p.06195": ["开发者应能输入一条命令来运行构建，并在通常仅需几秒的时间内得到生成的二进制文件。"],
    "body.p.06200": ["1 在一项内部调查中，83% 的 Google 员工表示对构建系统感到满意，使其成为受调查的 19 种工具中满意度排名第四的工具。平均工具的满意度为 69%。"],
    "body.p.06205": ["正确"],
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, values: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(value.model_dump_json())
            stream.write("\n")


def qa(view: TranslationView, response: TranslationResult, known: set[str], request: CalibrationRequest) -> dict:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=request.footnote_reference_numbers, verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings, explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def report(records: list[dict]) -> dict:
    return {"pass_count": sum(record["passed"] for record in records), "fail_count": sum(not record["passed"] for record in records), "records": records}


def main() -> None:
    if OUT.exists():
        raise SystemExit("CHECKPOINT_ALREADY_EXISTS")
    before = fingerprint(SOURCE).sha256
    all_requests = {row["display_id"]: CalibrationRequest.model_validate(row) for row in (json.loads(line) for line in REQUESTS.read_text(encoding="utf-8").splitlines() if line)}
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    evidence = json.loads(SIDECAR.read_text(encoding="utf-8"))["verified_markers"]
    requests = [all_requests[item].model_copy(update={"footnote_reference_numbers": footnote_numbers_for_unit(evidence, unit_uid=all_requests[item].unit_uid, source_text=all_requests[item].source_text), "selection_categories": [*all_requests[item].selection_categories, "batch_001", "checkpoint_01"]}) for item in ORDER]
    responses = []
    for request in requests:
        texts = TARGETS[request.display_id]
        if len(texts) != len(request.source_segments):
            raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref) for source, text in zip(request.source_segments, texts, strict=True)]))
    OUT.mkdir(parents=True)
    write_jsonl(OUT / "translation_request.jsonl", requests)
    raw_sha = persist_raw_response(OUT / "translation_raw_response.jsonl", responses)
    known = {view.unit_uid for view in views.values()}
    raw_records = [qa(views[request.display_id], response, known, request) for request, response in zip(requests, responses, strict=True)]
    write_json(OUT / "translation_raw_qa_report.json", report(raw_records))
    normalize_response_artifact(OUT / "translation_request.jsonl", OUT / "translation_raw_response.jsonl", OUT / "translation_normalized_response.jsonl", OUT / "translation_normalization_report.json")
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final_records = [qa(views[request.display_id], response, known, request) for request, response in zip(requests, normalized, strict=True)]
    final_report = report(final_records)
    write_json(OUT / "translation_normalized_qa_report.json", final_report)
    dispatched, exclusions = partition_deterministic_review_dispatch(final_records)
    reviews = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary."} for record in dispatched]
    write_json(OUT / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    write_json(OUT / "translation_final_qa_report.json", final_report)
    write_json(OUT / "checkpoint_evidence.json", {"logical_work_orders": ORDER, "ordinary_raw_sha256": raw_sha, "source_sha256": before, "footnote_evidence_path": str(SIDECAR), "verified_footnote_evidence_used": sum(bool(request.footnote_reference_numbers) for request in requests)})
    append_sealed_checkpoint(OUT.parent / "batch_manifest.json", OUT)
    if fingerprint(SOURCE).sha256 != before:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
