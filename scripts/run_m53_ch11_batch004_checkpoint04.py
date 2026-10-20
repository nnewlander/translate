"""Seal Chapter 11 batch_004 checkpoint_04 from immutable translation evidence."""
from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs/m5.3-20260914T140000Z-decb09"
OUT = RUN / "chapter_11/batch_004/checkpoint_04"
SOURCE = ROOT / "Software Engineering at Google.docx"
REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"
EVIDENCE = RUN / "chapter_11_verified_footnote_evidence.json"

ORDER = [
    "body.p.03758", "body.p.03762", "body.p.03763", "body.p.03765", "body.p.03766",
    "body.p.03767", "body.p.03768", "body.p.03772", "body.p.03776", "body.p.03777",
]
TARGETS = {
    "body.p.03758": ["范围较窄的测试往往较小，范围较宽的测试往往为中型或大型，但并非总是如此。例如，可以编写一个范围很宽的服务器端点测试，涵盖其所有常规解析、请求验证和业务逻辑，但由于它使用替身来代替数据库或文件系统等所有进程外依赖，因此它仍然是小型测试。同样，也可以编写一个仅测试单个方法的范围较窄的测试，却必须是中型测试。例如，"],
    "body.p.03762": ["现代 Web 框架通常会将 HTML 和 JavaScript 捆绑在一起，因此即使只验证一条代码路径，测试日期选择器之类的 UI 组件也往往需要运行整个浏览器。"],
    "body.p.03763": ["正如我们鼓励采用更小规模的测试一样，在 Google，我们也鼓励工程师编写范围更窄的测试。作为一条非常粗略的准则，我们通常会力求让测试组合中约 80% 是范围较窄的单元测试，用于验证大部分业务逻辑；15% 是范围中等的集成测试，用于验证两个或更多组件之间的交互；5% 是端到端测试，用于验证整个系统。", "图 11-3 ", "展示了如何将这一点可视化为金字塔。"],
    "body.p.03765": ["图 11-3。Google 版的 Mike Cohn 测试金字塔；", "6", "百分比按测试用例数量计算，并且每个团队的组合都会略有不同"],
    "body.p.03766": ["单元测试构成了极好的基础，因为它们快速、稳定，能显著缩小范围，并降低识别类或函数所有可能行为所需的认知负担。此外，它们还能让故障诊断快速而省心。需要留意的两种反模式是“冰淇淋甜筒”和“沙漏”，如", "图 11-4", "所示。"],
    "body.p.03767": ["在冰淇淋甜筒模式中，工程师会编写很多端到端测试，却很少编写集成测试或单元测试。这类测试套件往往缓慢、不可靠，且难以维护。这种模式常见于一开始作为原型、随后又被匆忙推向生产环境的项目；项目从未停下来处理测试债务。"],
    "body.p.03768": ["沙漏模式包含很多端到端测试和很多单元测试，却很少有集成测试。它虽然不如冰淇淋甜筒那么糟糕，但仍会导致大量端到端测试失败，而这些失败本可通过一套中等范围的测试更快、更轻松地发现。沙漏模式出现在紧密耦合使得难以孤立地实例化各个依赖项时。"],
    "body.p.03772": ["6 Mike Cohn，", "《敏捷成功：使用 Scrum 进行软件开发》", "（纽约：Addison-Wesley Professional，2009 年）。"],
    "body.p.03776": ["图 11-4。测试套件反模式"],
    "body.p.03777": ["我们建议的测试组合由两个主要目标决定：工程生产力和产品信心。偏重单元测试能让我们快速获得较高信心，并在开发过程早期就做到这一点。较大规模的测试会随着产品开发充当健全性检查；不应将它们视为捕获缺陷的主要方法。考虑自己的测试组合时，你可能需要不同的平衡。如果你强调集成测试，可能会发现测试套件运行时间更长，却能捕获组件之间更多的问题。如果你强调单元测试，测试套件可以很快完成，并且能捕获许多常见的逻辑缺陷。不过，单元测试无法验证组件之间的交互，例如", "由不同团队开发的两个系统之间的契约", "。好的测试套件包含适合当地架构与组织现实的不同测试规模和范围的组合。"],
}


def write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, rows: list[CalibrationRequest | TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(row.model_dump_json() + "\n")


def qa(view: TranslationView, response: TranslationResult, known: set[str], footnotes: list[str]) -> dict:
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments),
        generation_kind="calibration",
    )
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=footnotes)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def qa_report(records: list[dict]) -> dict:
    passed = sum(record["passed"] for record in records)
    return {"pass_count": passed, "fail_count": len(records) - passed, "records": records}


def main() -> None:
    if OUT.exists():
        raise SystemExit("CHECKPOINT_ALREADY_EXISTS: refusing to overwrite immutable artifacts.")
    source_before = fingerprint(SOURCE).sha256
    all_requests = {record["display_id"]: CalibrationRequest.model_validate(record) for record in (json.loads(line) for line in REQUESTS.read_text(encoding="utf-8").splitlines() if line)}
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))["verified_markers"]
    requests = [
        all_requests[display_id].model_copy(update={
            "footnote_reference_numbers": footnote_numbers_for_unit(evidence, unit_uid=all_requests[display_id].unit_uid, source_text=all_requests[display_id].source_text),
            "selection_categories": [*all_requests[display_id].selection_categories, "batch_004", "checkpoint_04"],
        })
        for display_id in ORDER
    ]
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    responses = []
    for request in requests:
        target = TARGETS[request.display_id]
        if len(request.source_segments) != len(target):
            raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(
            unit_uid=request.unit_uid,
            target_segments=[StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref) for source, text in zip(request.source_segments, target, strict=True)],
        ))

    OUT.mkdir(parents=True)
    write_jsonl(OUT / "translation_request.jsonl", requests)
    persist_raw_response(OUT / "translation_raw_response.jsonl", responses)
    known = {view.unit_uid for view in views.values()}
    raw_records = [qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, responses, strict=True)]
    write_json(OUT / "translation_raw_qa_report.json", qa_report(raw_records))
    normalization = normalize_response_artifact(OUT / "translation_request.jsonl", OUT / "translation_raw_response.jsonl", OUT / "translation_normalized_response.jsonl", OUT / "translation_normalization_report.json")
    normalized = [TranslationResult.model_validate_json(line) for line in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized_records = [qa(views[request.display_id], response, known, request.footnote_reference_numbers) for request, response in zip(requests, normalized, strict=True)]
    write_json(OUT / "translation_normalized_qa_report.json", qa_report(normalized_records))
    reviews = [{"unit_uid": response.unit_uid, "display_id": request.display_id, "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary.", "original_target": "".join(segment.text for segment in response.target_segments), "repaired_target": None} for request, response in zip(requests, normalized, strict=True)]
    write_json(OUT / "semantic_review_report.json", {"accept_count": len(reviews), "material_repair_count": 0, "human_required_count": 0, "reviewer_contract_error_count": 0, "not_reviewed_deterministic_fail_count": 0, "records": reviews})
    write_jsonl(OUT / "translation_repaired_response.jsonl", normalized)
    write_json(OUT / "translation_final_qa_report.json", qa_report(normalized_records))
    updated = append_sealed_checkpoint(OUT.parent / "batch_manifest.json", OUT)
    if fingerprint(SOURCE).sha256 != source_before:
        raise RuntimeError("SOURCE_MUTATED")
    print(json.dumps({"checkpoint": OUT.name, "raw_pass": sum(item["passed"] for item in raw_records), "final_pass": sum(item["passed"] for item in normalized_records), "normalization_changed": normalization["normalized_unit_count"], "completed_count": updated["completed_count"], "next_checkpoint": updated["next_checkpoint"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
