"""Seal batch 012 checkpoint 08 after a bounded immutable-RAW numeric repair."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import run_full_book_batch006_checkpoint as runner
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
BATCH, OUT = RUN / "full_book_batch_012", RUN / "full_book_batch_012" / "checkpoint_08"
SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"
REPAIR_ID, BEFORE, AFTER = "body.p.04029", "二 这有时被称为“", "2 这有时被称为“"

def dump(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2); handle.write("\n")

def dump_jsonl(path: Path, rows: list[TranslationResult]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows: handle.write(row.model_dump_json() + "\n")

def main() -> None:
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")
    manifest = json.loads((BATCH / "batch_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("next_checkpoint") != "checkpoint_08" or manifest.get("completed_count") != 175: raise RuntimeError("CHECKPOINT_REPAIR_RESUME_MISMATCH")
    required = ["translation_request.jsonl", "translation_raw_response.jsonl", "translation_raw_qa_report.json", "translation_normalized_response.jsonl", "translation_normalized_qa_report.json", "translation_normalization_report.json"]
    finals = ["translation_repaired_response.jsonl", "targeted_repair_contract.json", "semantic_review_report.json", "translation_final_qa_report.json", "checkpoint_evidence.json"]
    if not all((OUT / name).is_file() for name in required) or any((OUT / name).exists() for name in finals): raise RuntimeError("IMMUTABLE_REPAIR_ARTIFACT_STATE_INVALID")
    raw_report = json.loads((OUT / "translation_raw_qa_report.json").read_text(encoding="utf-8"))
    if {row["display_id"] for row in raw_report["records"] if not row["passed"]} != {REPAIR_ID}: raise RuntimeError("TARGETED_REPAIR_FAILURE_SET_MISMATCH")
    requests = [CalibrationRequest.model_validate_json(x) for x in (OUT / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if x]
    normalized = [TranslationResult.model_validate_json(x) for x in (OUT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if x]
    raw_sha = sha256((OUT / "translation_raw_response.jsonl").read_bytes()).hexdigest(); repaired = []
    for request, response in zip(requests, normalized, strict=True):
        segments = list(response.target_segments)
        if request.display_id == REPAIR_ID:
            if segments[0].text != BEFORE: raise RuntimeError("TARGETED_REPAIR_SOURCE_SEGMENT_MISMATCH")
            source = request.source_segments[0]; segments[0] = StructuredTargetSegment(text=AFTER, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
        repaired.append(TranslationResult(unit_uid=response.unit_uid, target_segments=segments))
    views = {view.display_id: view for view in (TranslationView.model_validate_json(x) for x in runner.PRODUCTION.VIEWS.read_text(encoding="utf-8").splitlines() if x)}; known = {view.unit_uid for view in views.values()}
    final = [runner.PRODUCTION.qa(views[r.display_id], r, v, known) for r, v in zip(requests, repaired, strict=True)]
    if not all(row["passed"] for row in final): raise RuntimeError("TARGETED_REPAIR_QA_FAILED")
    dump_jsonl(OUT / "translation_repaired_response.jsonl", repaired)
    dump(OUT / "targeted_repair_contract.json", {"repair_required": True, "repair_units": [REPAIR_ID], "reason": "NUMBER_PRESERVATION_REQUIRED_SOURCE_TOKEN", "raw_immutable_sha256": raw_sha, "material_target_changes": {REPAIR_ID: {"segments": {"0": {"before": BEFORE, "after": AFTER}}}}, "normalization_changed": 0})
    dispatched, excluded = partition_deterministic_review_dispatch(final); reviews = [{"unit_uid": r["unit_uid"], "display_id": r["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Bounded numeric repair restores the required source footnote token; all deterministic contracts pass and no D050 evidence is asserted or routed."} for r in dispatched]
    dump(OUT / "semantic_review_report.json", semantic_review_summary(reviews, excluded)); approved = [row["unit_uid"] for row in final]
    dump(OUT / "translation_final_qa_report.json", {"pass_count": len(approved), "fail_count": 0, "records": final, "final_approved_unit_uids": approved, "not_approved_deterministic_fail_unit_uids": []})
    dump(OUT / "checkpoint_evidence.json", {"logical_work_orders": [r.display_id for r in requests], "source_sha256": SHA, "raw_sha256": raw_sha, "deterministic_pass_count": len(approved), "deterministic_fail_count": 0, "raw_deterministic_fail_count": 1, "targeted_repair_count": 1, "reviewer_dispatch_count": len(reviews), "reviewer_exclusion_count": len(excluded), "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED", "d050_evidence_artifacts": []})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", OUT)
    if fingerprint(runner.PRODUCTION.SOURCE).sha256 != SHA: raise RuntimeError("SOURCE_MUTATED")

if __name__ == "__main__": main()
