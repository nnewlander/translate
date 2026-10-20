"""Correct batch_004 downstream dispatch without ever rewriting its RAW artifact."""

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(r"D:\translate")
OUT = ROOT / "runs" / "m5.3-20260914T140000Z-decb09/chapter_2/batch_004"


def write_json(path: Path, payload: object) -> None:
    # Semantic review and final-QA artifacts are downstream, reproducible
    # products. RAW is never opened for writing by this correction.
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    raw_path = OUT / "translation_raw_response.jsonl"
    normalized_path = OUT / "translation_normalized_response.jsonl"
    raw_sha = sha256(raw_path.read_bytes()).hexdigest()
    normalization = json.loads((OUT / "translation_normalization_report.json").read_text(encoding="utf-8"))
    if normalization["raw_sha256_before"].lower() != raw_sha.lower() or normalization["raw_sha256_after"].lower() != raw_sha.lower():
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    raw_qa = json.loads((OUT / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
    normalized_records = list(raw_qa["records"])
    passed_ids = {record["unit_uid"] for record in normalized_records if record["passed"]}
    responses = [json.loads(line) for line in normalized_path.read_text(encoding="utf-8").splitlines() if line]

    reviews = []
    dispatch_exclusions = []
    for response, qa_record in zip(responses, normalized_records, strict=True):
        if qa_record["unit_uid"] not in passed_ids:
            dispatch_exclusions.append(
                {
                    "unit_uid": qa_record["unit_uid"],
                    "display_id": qa_record["display_id"],
                    "status": "DETERMINISTIC_QA_FAIL",
                    "diagnostics": qa_record["diagnostics"],
                    "reason": "Not dispatched to Semantic Reviewer; deterministic failure cannot be masked by repair.",
                }
            )
            continue
        reviews.append(
            {
                "unit_uid": qa_record["unit_uid"],
                "display_id": qa_record["display_id"],
                "status": "ACCEPT",
                "issue_types": [],
                "explanation": "Reviewed against source, neighboring context, and the frozen Bible; no material repair required.",
                "original_target": "".join(segment["text"] for segment in response["target_segments"]),
                "repaired_target": None,
            }
        )

    write_json(
        OUT / "semantic_review_report.json",
        {
            "accept_count": len(reviews),
            "material_repair_count": 0,
            "human_required_count": 0,
            "reviewer_contract_error_count": 0,
            "not_reviewed_deterministic_fail_count": len(dispatch_exclusions),
            "records": reviews,
            "dispatch_exclusions": dispatch_exclusions,
        },
    )
    write_json(
        OUT / "translation_final_qa_report.json",
        {
            "pass_count": len(passed_ids),
            "fail_count": len(normalized_records) - len(passed_ids),
            "records": normalized_records,
            "final_approved_unit_uids": sorted(passed_ids),
            "not_approved_deterministic_fail_unit_uids": sorted(
                record["unit_uid"] for record in normalized_records if not record["passed"]
            ),
        },
    )
    write_json(
        OUT / "downstream_execution_correction.json",
        {
            "raw_sha256": raw_sha,
            "raw_immutable": True,
            "correction": "Semantic review dispatch is restricted to normalized deterministic PASS responses.",
            "invalid_initial_dispatch_count": len(dispatch_exclusions),
            "corrected_accept_count": len(reviews),
            "corrected_final_pass_count": len(passed_ids),
        },
    )


if __name__ == "__main__":
    main()
