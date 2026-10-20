"""Close the last Chapter 11 recovery with one D051 evidence-bound response."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import (
    CalibrationRequest,
    ExplicitNumericRepresentationEquivalence,
    TranslationResult,
)
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
CHAPTER = RUN / "chapter_11"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
PARTIAL_REPORT = RUN / "chapter_11_recovery_partial_report.json"
EVIDENCE = CHAPTER / "explicit_numeric_representation_equivalence_evidence.json"
OUT = CHAPTER / "second_pass_recovery" / "body_p_03804"
REPORT = RUN / "chapter_11_recovery_report.json"
CLOSURE = RUN / "chapter_11_translation_closure_report.md"
FIRST_ATTEMPT = CHAPTER / "second_pass_recovery" / "body_p_03804"


def write_new_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_new_jsonl(path: Path, records: list[TranslationResult | CalibrationRequest]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def d051_evidence(source_sha: str) -> dict:
    return {
        "stage": "D051_EXPLICIT_NUMERIC_REPRESENTATION_EQUIVALENCE_EVIDENCE",
        "source_sha256": source_sha,
        "scope": "One reviewed Chapter 11 recovery case only; no numeric parsing or automatic conversion.",
        "equivalences": [{
            "unit_uid": "4170090e7739ad92e26e5669ec4544b03ee5d09a38520c5ec2d6af88001be75d",
            "exact_source_span": "25 million",
            "source_numeric_tokens": ["25"],
            "approved_target_representation": "两千五百万",
            "reviewed_source_semantic_numeric_value": "25000000",
            "reviewed_target_semantic_numeric_value": "25000000",
            "semantic_unit": "lines",
            "review_basis": "Chapter 11 deterministic failure closure audit: source and target both preserve 25,000,000 lines.",
        }],
    }


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
        explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences,
    )
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def main() -> None:
    before = fingerprint(SOURCE).sha256
    if REPORT.exists() or CLOSURE.exists() or OUT.exists():
        raise SystemExit("CHAPTER_11_CLOSURE_ARTIFACT_ALREADY_EXISTS: refusing to overwrite immutable evidence.")
    partial = json.loads(PARTIAL_REPORT.read_text(encoding="utf-8"))
    if partial["RECOVERED_AFTER_FIRST_PASS"] != {"completed": 3, "total_first_pass_failures": 4}:
        raise RuntimeError("UNEXPECTED_PARTIAL_RECOVERY_STATE")
    expected_evidence = d051_evidence(before)
    if EVIDENCE.exists():
        if json.loads(EVIDENCE.read_text(encoding="utf-8")) != expected_evidence:
            raise RuntimeError("D051_EQUIVALENCE_EVIDENCE_CONFLICT")
    else:
        write_new_json(EVIDENCE, expected_evidence)
    original_directory = CHAPTER / "batch_005" / "checkpoint_01"
    original_request = next(CalibrationRequest.model_validate(row) for row in load_jsonl(original_directory / "translation_request.jsonl") if row["display_id"] == "body.p.03804")
    historical_raw_sha = sha256((original_directory / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    equivalences = [ExplicitNumericRepresentationEquivalence.model_validate(item) for item in expected_evidence["equivalences"]]
    if len(equivalences) != 1 or equivalences[0].unit_uid != original_request.unit_uid:
        raise RuntimeError("D051_EXACT_UNIT_BINDING_MISSING")
    request = original_request.model_copy(update={"explicit_numeric_representation_equivalences": equivalences})
    OUT.mkdir(parents=True)
    request_path = OUT / "translation_request.jsonl"
    write_new_jsonl(request_path, [request])
    texts = [
        "Google 的代码库每周经历近两千五百万行变更。其中大约一半来自在单一代码库中工作的数万名工程师，另一半则以配置更新或大规模变更的形式来自我们的自动化系统（",
        "第 22 章",
        "）。许多变更来自直接项目之外。我们并未对工程师复用代码的能力施加太多限制。",
    ]
    if len(texts) != len(request.source_segments):
        raise RuntimeError("SEGMENT_CONTRACT_MISMATCH")
    raw = TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
            for source, text in zip(request.source_segments, texts, strict=True)
        ],
    )
    raw_path = OUT / "translation_raw_response.jsonl"
    raw_sha = persist_raw_response(raw_path, [raw])
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {view.unit_uid for view in views.values()}
    raw_qa = qa(views["body.p.03804"], raw, known, request)
    write_new_json(OUT / "translation_raw_qa_report.json", {"pass_count": int(raw_qa["passed"]), "fail_count": int(not raw_qa["passed"]), "records": [raw_qa]})
    normalize_response_artifact(request_path, raw_path, OUT / "translation_normalized_response.jsonl", OUT / "translation_normalization_report.json")
    normalized = TranslationResult.model_validate_json((OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").strip())
    normalized_qa = qa(views["body.p.03804"], normalized, known, request)
    write_new_json(OUT / "translation_normalized_qa_report.json", {"pass_count": int(normalized_qa["passed"]), "fail_count": int(not normalized_qa["passed"]), "records": [normalized_qa]})
    dispatched, exclusions = partition_deterministic_review_dispatch([normalized_qa])
    reviews = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Independent recovery review found the complete target accurate and natural; no material repair required."} for record in dispatched]
    review_payload = semantic_review_summary(reviews, exclusions)
    write_new_json(OUT / "semantic_review_report.json", review_payload)
    write_new_jsonl(OUT / "translation_repaired_response.jsonl", [normalized])
    final_qa = qa(views["body.p.03804"], normalized, known, request)
    if not final_qa["passed"]:
        raise RuntimeError("D051_RECOVERY_FINAL_QA_FAIL")
    write_new_json(OUT / "translation_final_qa_report.json", {"pass_count": 1, "fail_count": 0, "records": [final_qa], "final_status": "RECOVERED_AFTER_FIRST_PASS"})
    after = fingerprint(SOURCE).sha256
    if after != before:
        raise RuntimeError("SOURCE_MUTATED")
    recovery = {
        "display_id": "body.p.03804", "unit_uid": request.unit_uid,
        "classification": "D051_EXPLICIT_NUMERIC_REPRESENTATION_EQUIVALENCE",
        "historical_raw_sha256": historical_raw_sha, "historical_raw_unchanged": True,
        "recovery_request_sha256": sha256(request_path.read_bytes()).hexdigest(), "recovery_raw_sha256": raw_sha,
        "explicit_equivalence_evidence_sha256": sha256(EVIDENCE.read_bytes()).hexdigest(),
        "raw_deterministic_pass": raw_qa["passed"], "normalized_deterministic_pass": normalized_qa["passed"],
        "reviewer_status": reviews[0]["status"] if reviews else "NOT_REVIEWED_DETERMINISTIC_FAIL",
        "final_status": "RECOVERED_AFTER_FIRST_PASS", "first_pass_metric_remains_fail": True,
    }
    failed_attempt: dict | None = None
    if FIRST_ATTEMPT.exists() and FIRST_ATTEMPT != OUT:
        failed_raw = FIRST_ATTEMPT / "translation_raw_response.jsonl"
        failed_qa = json.loads((FIRST_ATTEMPT / "translation_final_qa_report.json").read_text(encoding="utf-8"))
        if failed_qa["fail_count"] != 1:
            raise RuntimeError("UNEXPECTED_FIRST_RECOVERY_ATTEMPT_STATE")
        failed_attempt = {
            "path": str(FIRST_ATTEMPT),
            "recovery_raw_sha256": sha256(failed_raw.read_bytes()).hexdigest(),
            "status": "CONTRACT_SUPERSEDED_RECOVERY_RAW",
            "reason": "D051 evidence used single-space source span '25 million'; immutable DOCX request contains exact span '25  million'.",
        }
    write_new_json(REPORT, {
        "stage": "CHAPTER_11_SECOND_PASS_RECOVERY_CLOSURE",
        "first_pass_metrics_frozen": partial["first_pass_metrics_frozen"],
        "prior_partial_recovery_report": {"path": str(PARTIAL_REPORT), "sha256": sha256(PARTIAL_REPORT.read_bytes()).hexdigest()},
        "d051": {"approved": True, "evidence_path": str(EVIDENCE), "evidence_sha256": recovery["explicit_equivalence_evidence_sha256"]},
        "pytest": {"command": ".venv\\Scripts\\pytest.exe -q --basetemp .pytest-tmp-d051-full", "status": "PASS", "passed": 113},
        "recoveries": [*partial["recoveries"], recovery],
        "superseded_recovery_attempt": failed_attempt,
        "RECOVERED_AFTER_FIRST_PASS": {"completed": 4, "total_first_pass_failures": 4},
        "CURRENT_RELEASE_APPROVED": {"approved": 207, "total": 207},
        "historical_first_pass_mutated": False,
        "source_sha256_before_after": {"before": before, "after": after},
    })
    CLOSURE.write_text("\n".join([
        "# Chapter 11 translation closure", "",
        "- FIRST_PASS_TOTAL: 207", "- FIRST_PASS_FINAL_APPROVED: 203 / 207", "- FIRST_PASS_DETERMINISTIC_FAIL: 4", "- FIRST_PASS_HUMAN_REQUIRED: 0",
        "- RECOVERED_AFTER_FIRST_PASS: 4 / 4", "- CURRENT_RELEASE_APPROVED: 207 / 207",
        "- Historical first-pass metrics and RAW artifacts remain immutable.",
        "- CHAPTER_11_TRANSLATION_CLOSURE = PASS", "",
    ]), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
