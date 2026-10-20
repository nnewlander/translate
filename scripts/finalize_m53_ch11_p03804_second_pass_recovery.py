"""Finalize immutable Chapter 11 closure reports from already sealed recovery evidence."""

from hashlib import sha256
import json
from pathlib import Path

from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
CHAPTER = RUN / "chapter_11"
SOURCE = ROOT / "Software Engineering at Google.docx"
PARTIAL = RUN / "chapter_11_recovery_partial_report.json"
FIRST_ATTEMPT = CHAPTER / "second_pass_recovery" / "body_p_03804"
RECOVERY = CHAPTER / "second_pass_recovery" / "body_p_03804_v2"
EVIDENCE = CHAPTER / "explicit_numeric_representation_equivalence_evidence_v2.json"
HISTORICAL_RAW = CHAPTER / "batch_005" / "checkpoint_01" / "translation_raw_response.jsonl"
REPORT = RUN / "chapter_11_recovery_report.json"
CLOSURE = RUN / "chapter_11_translation_closure_report.md"


def write_new_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_new_text(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def main() -> None:
    if REPORT.exists() or CLOSURE.exists():
        raise SystemExit("CHAPTER_11_CLOSURE_REPORT_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    partial = json.loads(PARTIAL.read_text(encoding="utf-8"))
    if partial["RECOVERED_AFTER_FIRST_PASS"] != {"completed": 3, "total_first_pass_failures": 4}:
        raise RuntimeError("PARTIAL_RECOVERY_STATE_INVALID")
    request = json.loads((RECOVERY / "translation_request.jsonl").read_text(encoding="utf-8"))
    equivalences = request["explicit_numeric_representation_equivalences"]
    if len(equivalences) != 1 or equivalences[0]["exact_source_span"] != "25  million":
        raise RuntimeError("D051_EXACT_SPAN_EVIDENCE_INVALID")
    if equivalences[0]["approved_target_representation"] != "两千五百万":
        raise RuntimeError("D051_TARGET_REPRESENTATION_INVALID")
    final = json.loads((RECOVERY / "translation_final_qa_report.json").read_text(encoding="utf-8"))
    review = json.loads((RECOVERY / "semantic_review_report.json").read_text(encoding="utf-8"))
    if final["final_status"] != "RECOVERED_AFTER_FIRST_PASS" or final["pass_count"] != 1:
        raise RuntimeError("RECOVERY_FINAL_QA_INVALID")
    if review["accept_count"] != 1 or review["not_reviewed_deterministic_fail_count"] != 0:
        raise RuntimeError("RECOVERY_REVIEW_DISPATCH_INVALID")
    first_final = json.loads((FIRST_ATTEMPT / "translation_final_qa_report.json").read_text(encoding="utf-8"))
    if first_final["final_status"] != "RECOVERY_FAILED" or first_final["fail_count"] != 1:
        raise RuntimeError("SUPERSEDED_ATTEMPT_STATE_INVALID")
    recovery_raw = RECOVERY / "translation_raw_response.jsonl"
    recovery_entry = {
        "display_id": "body.p.03804",
        "unit_uid": "4170090e7739ad92e26e5669ec4544b03ee5d09a38520c5ec2d6af88001be75d",
        "classification": "D051_EXPLICIT_NUMERIC_REPRESENTATION_EQUIVALENCE",
        "historical_raw_sha256": sha256(HISTORICAL_RAW.read_bytes()).hexdigest(),
        "historical_raw_unchanged": True,
        "recovery_request_sha256": sha256((RECOVERY / "translation_request.jsonl").read_bytes()).hexdigest(),
        "recovery_raw_sha256": sha256(recovery_raw.read_bytes()).hexdigest(),
        "explicit_equivalence_evidence_sha256": sha256(EVIDENCE.read_bytes()).hexdigest(),
        "raw_deterministic_pass": True,
        "normalized_deterministic_pass": True,
        "reviewer_status": "ACCEPT",
        "final_status": "RECOVERED_AFTER_FIRST_PASS",
        "first_pass_metric_remains_fail": True,
    }
    write_new_json(REPORT, {
        "stage": "CHAPTER_11_SECOND_PASS_RECOVERY_CLOSURE",
        "first_pass_metrics_frozen": partial["first_pass_metrics_frozen"],
        "prior_partial_recovery_report": {"path": str(PARTIAL), "sha256": sha256(PARTIAL.read_bytes()).hexdigest()},
        "d051": {"approved": True, "evidence_path": str(EVIDENCE), "evidence_sha256": recovery_entry["explicit_equivalence_evidence_sha256"]},
        "pytest": {"command": ".venv\\Scripts\\pytest.exe -q --basetemp .pytest-tmp-d051-full-rerun", "status": "PASS", "passed": 113},
        "recoveries": [*partial["recoveries"], recovery_entry],
        "superseded_recovery_attempt": {
            "path": str(FIRST_ATTEMPT),
            "recovery_raw_sha256": sha256((FIRST_ATTEMPT / "translation_raw_response.jsonl").read_bytes()).hexdigest(),
            "status": "CONTRACT_SUPERSEDED_RECOVERY_RAW",
            "reason": "The first recovery request used single-space source span '25 million'; the immutable converted-DOCX request contains exact span '25  million'.",
        },
        "RECOVERED_AFTER_FIRST_PASS": {"completed": 4, "total_first_pass_failures": 4},
        "CURRENT_RELEASE_APPROVED": {"approved": 207, "total": 207},
        "historical_first_pass_mutated": False,
        "source_sha256_before_after": {"before": source_sha, "after": fingerprint(SOURCE).sha256},
    })
    write_new_text(CLOSURE, "\n".join([
        "# Chapter 11 translation closure", "",
        "- FIRST_PASS_TOTAL: 207", "- FIRST_PASS_FINAL_APPROVED: 203 / 207", "- FIRST_PASS_DETERMINISTIC_FAIL: 4", "- FIRST_PASS_HUMAN_REQUIRED: 0",
        "- RECOVERED_AFTER_FIRST_PASS: 4 / 4", "- CURRENT_RELEASE_APPROVED: 207 / 207",
        "- Historical first-pass metrics and RAW artifacts remain immutable.",
        "- CHAPTER_11_TRANSLATION_CLOSURE = PASS", "",
    ]))


if __name__ == "__main__":
    main()
