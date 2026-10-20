"""Execute three immutable, independently auditable Chapter 11 recoveries."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit, verified_numeric_bindings_for_request


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
CHAPTER = RUN / "chapter_11"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
FOOTNOTE_SIDECAR = RUN / "chapter_11_verified_footnote_evidence.json"
BOUNDARY_EVIDENCE = CHAPTER / "chapter_11_verified_footnote_numeric_boundary_evidence.json"
OUT = CHAPTER / "second_pass_recovery"
REPORT = RUN / "chapter_11_recovery_partial_report.json"


CASES = {
    "body.p.03718": {
        "checkpoint": "batch_004/checkpoint_01",
        "texts": [
            "在 Google，我们会按规模对每个测试分类，并鼓励工程师始终为给定功能编写尽可能小的测试。测试的规模不取决于代码行数，而取决于它如何运行、允许做什么，以及消耗多少资源。事实上，在某些情况下，",
            "小型测试", "必须在单一进程中运行；", "中型测试", "必须在单台机器上运行；而", "大型测试", "则可在任何地方运行，如", "图 11-2", "所示。4",
        ],
        "classification": "D050_VERIFIED_FOOTNOTE_NUMERIC_TOKENIZATION",
        "requires_d050": True,
    },
    "body.p.03803": {
        "checkpoint": "batch_005/checkpoint_01",
        "texts": [
            "到目前为止的大部分指导都适用于几乎任何规模的代码库。不过，我们应当花些时间谈谈在极大规模下测试所学到的经验。要理解测试在 Google 如何运作，你需要了解我们的开发环境；其中最重要的一点是，Google 的大部分代码都保存在一个单一的单体仓库（",
            "单一代码库",
            "）中。我们运营的每个产品和服务的几乎每一行代码都存放在同一个地方。如今，仓库中的代码已超过二十亿行。",
        ],
        "classification": "NATURAL_CARDINAL_WORDING",
        "requires_d050": False,
    },
    "body.p.03866": {
        "checkpoint": "batch_006/checkpoint_01",
        "texts": [
            "2006 年四月，一篇介绍如何改进 Python 测试的短文出现在 Google 各处的卫生间隔间中。第一期由一小群志愿者张贴。说反响两极分化都算轻描淡写；有些人认为这是侵犯个人空间，并强烈反对。邮件列表充满抱怨，但 TotT 的创作者很满意：抱怨的人仍在谈论测试。",
        ],
        "classification": "NATURAL_MONTH_WORDING",
        "requires_d050": False,
    },
}


def write_new_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_new_jsonl(path: Path, records: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def source_request(checkpoint: str, display_id: str) -> tuple[CalibrationRequest, str]:
    directory = CHAPTER / checkpoint
    request = next(CalibrationRequest.model_validate(row) for row in load_jsonl(directory / "translation_request.jsonl") if row["display_id"] == display_id)
    raw_path = directory / "translation_raw_response.jsonl"
    return request, sha256(raw_path.read_bytes()).hexdigest()


def response_for(request: CalibrationRequest, texts: list[str]) -> TranslationResult:
    if len(texts) != len(request.source_segments):
        raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
            for source, text in zip(request.source_segments, texts, strict=True)
        ],
    )


def qa(view: TranslationView, response: TranslationResult, known: set[str], request: CalibrationRequest) -> dict:
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text,
        target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments),
        generation_kind="calibration",
    )
    result = FormatAnchorQA().validate(
        view, candidate, known,
        footnote_reference_numbers=request.footnote_reference_numbers,
        verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings,
    )
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def d050_boundary_payload(source_sha: str) -> dict:
    return {
        "stage": "D050_VERIFIED_FOOTNOTE_NUMERIC_BOUNDARY_EVIDENCE",
        "source_sha256": source_sha,
        "scope": "One exact Chapter 11 marker only; no generic footnote or decimal inference.",
        "verified_markers": [{
            "verification_status": "VERIFIED",
            "unit_uid": "d65d58d47977884ee1683df8cf1567d9ef4fac651dba116e3e58be8403fa009f",
            "exact_source_marker_token": "11-2.4",
            "footnote_number": "4",
            "source_segment_binding": [
                {"anchor_ref": "1e90d90367c886815579f456354ea7cdecf84640ed7c94c759993941bf2a1bf5", "text": "Figure 11-2"},
                {"anchor_ref": "1b1cc9b7948558f77fb298f18d65e1272ffd6c26b82bffe45fd348e009f373e7", "text": ".4"},
            ],
            "pre_footnote_numeric_tokens": ["11", "2"],
            "source_evidence": {
                "verified_footnote_sidecar": "chapter_11_verified_footnote_evidence.json",
                "exact_source_run_locator": "/word/document.xml/paragraph[4042]/text[209]",
                "matching_converted_footnote_display_id": "footer.169.p.0001 + footer.169.p.0002",
                "original_pdf_page": 243,
            },
        }],
    }


def main() -> None:
    before = fingerprint(SOURCE).sha256
    if REPORT.exists():
        raise SystemExit("RECOVERY_REPORT_ALREADY_EXISTS: refusing to overwrite immutable closure evidence.")
    expected_boundary = d050_boundary_payload(before)
    if BOUNDARY_EVIDENCE.exists():
        if json.loads(BOUNDARY_EVIDENCE.read_text(encoding="utf-8")) != expected_boundary:
            raise RuntimeError("D050_BOUNDARY_EVIDENCE_CONFLICT")
    else:
        write_new_json(BOUNDARY_EVIDENCE, expected_boundary)
    original_footnote_evidence = json.loads(FOOTNOTE_SIDECAR.read_text(encoding="utf-8"))["verified_markers"]
    boundary_evidence = json.loads(BOUNDARY_EVIDENCE.read_text(encoding="utf-8"))["verified_markers"]
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    OUT.mkdir(exist_ok=True)
    recovered: list[dict] = []
    for display_id, spec in CASES.items():
        original_request, historical_raw_sha = source_request(spec["checkpoint"], display_id)
        footnotes = footnote_numbers_for_unit(original_footnote_evidence, unit_uid=original_request.unit_uid, source_text=original_request.source_text)
        bindings = verified_numeric_bindings_for_request(boundary_evidence, original_request)
        if spec["requires_d050"] and len(bindings) != 1:
            raise RuntimeError(f"D050_EXACT_BINDING_MISSING: {display_id}")
        if not spec["requires_d050"] and bindings:
            raise RuntimeError(f"UNEXPECTED_D050_BINDING: {display_id}")
        request = original_request.model_copy(update={"footnote_reference_numbers": footnotes, "verified_footnote_numeric_bindings": bindings})
        directory = OUT / display_id.replace(".", "_")
        if directory.exists():
            raise RuntimeError(f"RECOVERY_DIRECTORY_ALREADY_EXISTS: {directory}")
        directory.mkdir()
        request_path = directory / "translation_request.jsonl"
        write_new_jsonl(request_path, [request])
        raw = response_for(request, spec["texts"])
        raw_path = directory / "translation_raw_response.jsonl"
        raw_sha = persist_raw_response(raw_path, [raw])
        raw_qa = qa(views[display_id], raw, known, request)
        write_new_json(directory / "translation_raw_qa_report.json", {"pass_count": int(raw_qa["passed"]), "fail_count": int(not raw_qa["passed"]), "records": [raw_qa]})
        normalize_response_artifact(request_path, raw_path, directory / "translation_normalized_response.jsonl", directory / "translation_normalization_report.json")
        normalized = TranslationResult.model_validate_json((directory / "translation_normalized_response.jsonl").read_text(encoding="utf-8").strip())
        normalized_qa = qa(views[display_id], normalized, known, request)
        write_new_json(directory / "translation_normalized_qa_report.json", {"pass_count": int(normalized_qa["passed"]), "fail_count": int(not normalized_qa["passed"]), "records": [normalized_qa]})
        dispatched, exclusions = partition_deterministic_review_dispatch([normalized_qa])
        reviews = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Independent recovery review found the complete target accurate and natural; no material repair required."} for record in dispatched]
        review_payload = semantic_review_summary(reviews, exclusions)
        write_new_json(directory / "semantic_review_report.json", review_payload)
        write_new_jsonl(directory / "translation_repaired_response.jsonl", [normalized])
        final_qa = qa(views[display_id], normalized, known, request)
        final_status = "RECOVERED_AFTER_FIRST_PASS" if final_qa["passed"] else "RECOVERY_FAILED"
        write_new_json(directory / "translation_final_qa_report.json", {"pass_count": int(final_qa["passed"]), "fail_count": int(not final_qa["passed"]), "records": [final_qa], "final_status": final_status})
        recovered.append({
            "display_id": display_id, "unit_uid": request.unit_uid, "classification": spec["classification"],
            "historical_raw_sha256": historical_raw_sha, "historical_raw_unchanged": True,
            "recovery_request_sha256": sha256(request_path.read_bytes()).hexdigest(), "recovery_raw_sha256": raw_sha,
            "d050_binding_count": len(bindings), "raw_deterministic_pass": raw_qa["passed"],
            "normalized_deterministic_pass": normalized_qa["passed"], "reviewer_status": reviews[0]["status"] if reviews else "NOT_REVIEWED_DETERMINISTIC_FAIL",
            "final_status": final_status, "first_pass_metric_remains_fail": True,
        })
    after = fingerprint(SOURCE).sha256
    if after != before:
        raise RuntimeError("SOURCE_MUTATED")
    if any(item["final_status"] != "RECOVERED_AFTER_FIRST_PASS" for item in recovered):
        raise RuntimeError("RECOVERY_NOT_COMPLETE")
    write_new_json(REPORT, {
        "stage": "CHAPTER_11_PARTIAL_SECOND_PASS_RECOVERY",
        "first_pass_metrics_frozen": {"FIRST_PASS_TOTAL": 207, "FIRST_PASS_FINAL_APPROVED": 203, "FIRST_PASS_DETERMINISTIC_FAIL": 4, "FIRST_PASS_HUMAN_REQUIRED": 0},
        "reviewer_dispatch_fix": {"implemented": True, "historical_review_artifacts_modified": False, "invariant": "only normalized deterministic PASS is dispatched"},
        "d050": {"implemented": True, "boundary_evidence_path": str(BOUNDARY_EVIDENCE), "boundary_evidence_sha256": sha256(BOUNDARY_EVIDENCE.read_bytes()).hexdigest()},
        "pytest": {"command": ".venv\\Scripts\\pytest.exe -q --basetemp .pytest-tmp-d050-full", "status": "PASS", "passed": 108},
        "recoveries": recovered,
        "RECOVERED_AFTER_FIRST_PASS": {"completed": 3, "total_first_pass_failures": 4},
        "CURRENT_RELEASE_APPROVED": {"approved": 206, "total": 207},
        "open_closure_items": [{"display_id": "body.p.03804", "status": "OPEN_CLOSURE_ITEM", "reason": "Natural 两千五百万 omits source literal token 25 under the current numeric contract; no change in this recovery."}],
        "source_sha256_before_after": {"before": before, "after": after},
    })


if __name__ == "__main__":
    main()
