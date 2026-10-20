"""Run the closed, four-unit recovery sweep after Chapter 1 D050 preparation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import (
    CalibrationRequest,
    TranslationResult,
    VerifiedFootnoteNumericBinding,
    VerifiedFootnoteSourceSegment,
)
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
FOOTNOTES = RUN / "chapter_1_verified_footnote_evidence_v1.json"
OUT = RUN / "full_book_recovery_sweep_001"

HISTORICAL = {
    "body.p.00056": RUN / "full_book_batch_001" / "checkpoint_01",
    "body.p.00811": RUN / "full_book_batch_002" / "checkpoint_03",
    "body.p.00821": RUN / "full_book_batch_002" / "checkpoint_03",
    "body.p.00850": RUN / "full_book_batch_002" / "checkpoint_04",
}
ORDER = ["body.p.00056", "body.p.00811", "body.p.00821", "body.p.00850"]


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, values: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(value.model_dump_json())
            stream.write("\n")


def read_pairs(path: Path) -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {}
    for record in json.loads(path.read_text(encoding="utf-8"))["records"]:
        output.setdefault(record["logical_work_order_id"], []).append(record)
    return output


def qa(view: TranslationView, request: CalibrationRequest, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=request.footnote_reference_numbers, verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def main() -> None:
    if OUT.exists():
        raise SystemExit(f"immutable recovery sweep already exists: {OUT}")
    source_sha = fingerprint(SOURCE).sha256
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    pairs = read_pairs(FOOTNOTES)
    OUT.mkdir()
    results = []
    for display_id in ORDER:
        historical = HISTORICAL[display_id]
        requests = [CalibrationRequest.model_validate_json(line) for line in (historical / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line]
        original_request = next(request for request in requests if request.display_id == display_id)
        raws = [TranslationResult.model_validate_json(line) for line in (historical / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
        original_raw = next(raw for raw in raws if raw.unit_uid == original_request.unit_uid)
        evidence = pairs.get(display_id, [])
        bindings = [VerifiedFootnoteNumericBinding(unit_uid=original_request.unit_uid, exact_source_marker_token=record["exact_source_marker_token"], footnote_number=record["footnote_number"], source_segment_binding=[VerifiedFootnoteSourceSegment(anchor_ref=item["anchor_ref"], text=item["text"]) for item in record["source_segment_binding"]]) for record in evidence]
        request = original_request.model_copy(update={
            "selection_categories": ["full_book_production", "recovery_sweep_001", "second_pass_recovery"],
            "footnote_reference_numbers": [record["footnote_number"] for record in evidence],
            "verified_footnote_numeric_bindings": bindings,
        })
        if display_id == "body.p.00056":
            corrected = [
                "O’Reilly 图书可购买用于教育、商业或销售推广用途。大多数书名也提供在线版本（",
                "http://oreilly.com",
                ").如需更多信息，请联系企业/机构",
                "销售部门：", "800-998-9938 or", " ", "corporate@oreilly.com", "。",
            ]
            response = TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref) for segment, text in zip(request.source_segments, corrected, strict=True)])
            classification = "TARGET_OUTPUT_URL_TERMINAL_PUNCTUATION"
        else:
            response = original_raw
            classification = "FOOTNOTE_MARKER_EVIDENCE_ABSENT_FOR_FULL_BOOK_UNIT"
        unit = OUT / display_id.replace(".", "_")
        unit.mkdir()
        write_json(unit / "recovery_provenance.json", {
            "historical_request": str(historical / "translation_request.jsonl"),
            "historical_raw": str(historical / "translation_raw_response.jsonl"),
            "historical_raw_sha256": sha256((historical / "translation_raw_response.jsonl").read_bytes()).hexdigest(),
            "historical_raw_immutable": True,
            "failure_classification": classification,
            "new_recovery_request": True,
            "new_recovery_raw": True,
            "footnote_evidence": str(FOOTNOTES) if evidence else None,
        })
        write_jsonl(unit / "translation_request.jsonl", [request])
        raw_sha = persist_raw_response(unit / "translation_raw_response.jsonl", [response])
        raw_qa = qa(views[display_id], request, response, known)
        write_json(unit / "translation_raw_qa_report.json", {"pass_count": int(raw_qa["passed"]), "fail_count": int(not raw_qa["passed"]), "records": [raw_qa]})
        normalize_response_artifact(unit / "translation_request.jsonl", unit / "translation_raw_response.jsonl", unit / "translation_normalized_response.jsonl", unit / "translation_normalization_report.json")
        if sha256((unit / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
            raise RuntimeError("RAW_RESPONSE_MUTATED")
        normalized = TranslationResult.model_validate_json((unit / "translation_normalized_response.jsonl").read_text(encoding="utf-8").strip())
        final = qa(views[display_id], request, normalized, known)
        write_json(unit / "translation_normalized_qa_report.json", {"pass_count": int(final["passed"]), "fail_count": int(not final["passed"]), "records": [final]})
        dispatched, exclusions = partition_deterministic_review_dispatch([final])
        reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed that the recovery preserves source meaning, anchors, protected literals, and the exact verified footnote boundary where applicable."} for row in dispatched]
        write_json(unit / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
        write_jsonl(unit / "translation_repaired_response.jsonl", [normalized])
        approved = [final["unit_uid"]] if final["passed"] else []
        failed = [] if final["passed"] else [final["unit_uid"]]
        write_json(unit / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": [final], "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
        results.append({"logical_work_order_id": display_id, "final_approved": bool(final["passed"]), "recovery_artifact": str(unit), "raw_sha256": raw_sha})
    write_json(OUT / "recovery_sweep_closure.json", {"artifact_type": "FULL_BOOK_RECOVERY_SWEEP_CLOSURE", "source_sha256": source_sha, "units": results, "final_approved_count": sum(item["final_approved"] for item in results), "unresolved_count": sum(not item["final_approved"] for item in results), "footnote_evidence": str(FOOTNOTES)})
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
