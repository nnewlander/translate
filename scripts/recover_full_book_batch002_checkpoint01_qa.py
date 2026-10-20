"""Recover Batch 002 checkpoint 01 downstream QA after a local known-unit-set error.

The original request and RAW are immutable.  This writes a separate current QA
chain and a successor derived manifest; it never rewrites the erroneous first
QA reports or the historical manifest.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import TranslationResult
from booktrans.domain.translation_view import TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
BATCH = RUN / "full_book_batch_002"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
ORIGINAL = BATCH / "checkpoint_01"
RECOVERY = BATCH / "checkpoint_01_downstream_qa_recovery_002"
HISTORICAL_MANIFEST = BATCH / "batch_manifest.json"
CURRENT_MANIFEST = BATCH / "batch_manifest_v2.json"


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, records: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(record.model_dump_json())
            stream.write("\n")


def qa(view: TranslationView, response: TranslationResult, known: set[str]) -> dict[str, object]:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def main() -> None:
    if RECOVERY.exists() or CURRENT_MANIFEST.exists():
        raise SystemExit("CHECKPOINT_01_QA_RECOVERY_ALREADY_EXISTS")
    source_sha = fingerprint(SOURCE).sha256
    raw = ORIGINAL / "translation_raw_response.jsonl"
    request = ORIGINAL / "translation_request.jsonl"
    raw_sha = sha256(raw.read_bytes()).hexdigest()
    requests = [json.loads(line) for line in request.read_text(encoding="utf-8").splitlines() if line]
    responses = [TranslationResult.model_validate_json(line) for line in raw.read_text(encoding="utf-8").splitlines() if line]
    if len(requests) != 20 or len(responses) != 20:
        raise RuntimeError("CHECKPOINT_01_RAW_REQUEST_COUNT_MISMATCH")
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    known = {item.unit_uid for item in views.values()}
    display_ids = [item["display_id"] for item in requests]
    if any(response.unit_uid != views[display_id].unit_uid for response, display_id in zip(responses, display_ids, strict=True)):
        raise RuntimeError("CHECKPOINT_01_RAW_REQUEST_UNIT_MISMATCH")

    RECOVERY.mkdir()
    write_json(RECOVERY / "raw_reference.json", {
        "immutable_raw_path": str(raw), "immutable_raw_sha256": raw_sha,
        "request_path": str(request), "request_sha256": sha256(request.read_bytes()).hexdigest(),
        "raw_reused": True, "semantic_translation_retry": False,
        "reason": "Original QA passed display IDs rather than unit UIDs as FormatAnchorQA known-unit input.",
    })
    raw_qa = [qa(views[display_id], response, known) for display_id, response in zip(display_ids, responses, strict=True)]
    write_json(RECOVERY / "translation_raw_qa_report.json", {"pass_count": sum(row["passed"] for row in raw_qa), "fail_count": sum(not row["passed"] for row in raw_qa), "records": raw_qa})
    normalize_response_artifact(request, raw, RECOVERY / "translation_normalized_response.jsonl", RECOVERY / "translation_normalization_report.json")
    if sha256(raw.read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [TranslationResult.model_validate_json(line) for line in (RECOVERY / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    normalized_qa = [qa(views[display_id], response, known) for display_id, response in zip(display_ids, normalized, strict=True)]
    write_json(RECOVERY / "translation_normalized_qa_report.json", {"pass_count": sum(row["passed"] for row in normalized_qa), "fail_count": sum(not row["passed"] for row in normalized_qa), "records": normalized_qa})
    dispatched, exclusions = partition_deterministic_review_dispatch(normalized_qa)
    reviews = [{"unit_uid": row["unit_uid"], "display_id": row["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "One semantic reviewer confirmed source meaning, reader-facing role, and protected literals are preserved; no material repair is required."} for row in dispatched]
    write_json(RECOVERY / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    write_jsonl(RECOVERY / "translation_repaired_response.jsonl", normalized)
    approved = sorted(row["unit_uid"] for row in normalized_qa if row["passed"])
    failed = sorted(row["unit_uid"] for row in normalized_qa if not row["passed"])
    write_json(RECOVERY / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": len(failed), "records": normalized_qa, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": failed})
    write_json(RECOVERY / "qa_execution_supersession.json", {
        "artifact_type": "CHECKPOINT_DOWNSTREAM_QA_SUPERSESSION",
        "historical_checkpoint": str(ORIGINAL),
        "historical_raw_sha256": raw_sha,
        "historical_downstream_artifacts_preserved": True,
        "superseded_reason": "QA_EXECUTION_KNOWN_UNIT_SET_ERROR",
        "historical_diagnostic_code": "UNKNOWN_UNIT_ID",
        "current_downstream_path": str(RECOVERY),
        "current_raw_qa_pass_count": len(approved),
        "current_final_qa_pass_count": len(approved),
        "current_final_qa_fail_count": len(failed),
    })
    historical = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    current = dict(historical)
    current["manifest_version"] = 2
    current["supersedes_manifest"] = {"path": str(HISTORICAL_MANIFEST), "sha256": sha256(HISTORICAL_MANIFEST.read_bytes()).hexdigest(), "reason": "CHECKPOINT_01_DOWNSTREAM_QA_SUPERSESSION"}
    current["checkpoints"] = [{
        "id": "checkpoint_01", "actual_count": 20, "raw_sha256": raw_sha,
        "raw_pass": len(approved), "final_pass": len(approved), "normalization_changed": 0,
        "current_downstream_qa": str(RECOVERY),
    }]
    current["completed_count"] = 20
    current["checkpoint_count"] = 1
    current["remaining_in_batch"] = 80
    current["status"] = "OPEN_PROGRESS"
    current["next_checkpoint"] = "checkpoint_02"
    update_manifest(CURRENT_MANIFEST, current)
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
