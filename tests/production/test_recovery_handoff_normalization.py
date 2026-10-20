from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalizer = _module("recovery_handoff_normalizer", ROOT / "scripts" / "production" / "normalize_recovery_handoff.py")
triage = _module("final_qa_triage_for_normalizer", ROOT / "scripts" / "production" / "triage_final_deterministic_qa.py")


def _fixture(tmp_path: Path, *, code: str = "NUMBER_PRESERVATION") -> tuple[Path, dict, dict, str]:
    run = tmp_path / "run"
    batch = run / "full_book_batch_001"
    cp = batch / "checkpoint_04"
    cp.mkdir(parents=True)
    (batch / "batch_manifest.json").write_text(json.dumps({"checkpoints": [{"id": f"checkpoint_{i:02d}"} for i in range(1, 4)]}), encoding="utf-8")
    (cp / "translation_request.jsonl").write_text("{}\n", encoding="utf-8")
    raw = b"immutable raw\n"
    (cp / "translation_raw_response.jsonl").write_bytes(raw)
    (cp / "translation_normalized_response.jsonl").write_bytes(raw)
    (cp / "translation_normalization_report.json").write_text("{}", encoding="utf-8")
    records = [{"display_id": "body.p.00001", "passed": False, "diagnostics": [{"code": code}]}]
    records.extend({"display_id": f"pass.{i}", "passed": True, "diagnostics": []} for i in range(24))
    report = {"records": records}
    for name in ("translation_raw_qa_report.json", "translation_normalized_qa_report.json"):
        (cp / name).write_text(json.dumps(report), encoding="utf-8")
    state = {"source_sha256": "source", "active_batch": "FULL_BOOK_BATCH_001", "next_checkpoint": "checkpoint_04", "next_logical_work_order_id": "body.p.00001"}
    handoff = {"schema_version": 1, "worker_id": "worker", "status": "RECOVERY_REQUIRED", "failure_classification": "DETERMINISTIC_QA_FAILURE:NUMBER_PRESERVATION", "timestamp": "2026-09-17T00:00:00Z"}
    return run, state, handoff, sha256(raw).hexdigest()


def test_missing_derivable_fields_are_normalized_from_unique_partial(tmp_path: Path) -> None:
    run, state, handoff, raw_sha = _fixture(tmp_path)
    result = normalizer.normalize_recovery_handoff(handoff=handoff, state=state, source_sha256="source", run=run)
    assert result["normalized"] is True, result
    assert {"partial_checkpoint_path", "stage_reached", "failing_work_order_ids", "raw_exists", "raw_sha256", "created_at_from_timestamp"} <= set(result["derived_fields"])
    assert result["handoff"]["raw_sha256"] == raw_sha
    assert result["handoff"]["failing_work_order_ids"] == ["body.p.00001"]


def test_normalized_known_numeric_evidence_is_triage_eligible(tmp_path: Path) -> None:
    run, state, handoff, raw_sha = _fixture(tmp_path)
    normalized = normalizer.normalize_recovery_handoff(handoff=handoff, state=state, source_sha256="source", run=run)["handoff"]
    result = triage.classify_final_deterministic_qa(
        checkpoint=Path(normalized["partial_checkpoint_path"]), expected_raw_sha256=raw_sha,
        expected_source_sha256="source", actual_source_sha256="source",
        handoff_failing_ids=normalized["failing_work_order_ids"],
    )
    assert result["eligible"] is True
    assert result["canonical_class"] == "LOCAL_DETERMINISTIC_QA_FAILURE"


def test_competing_partial_fails_closed(tmp_path: Path) -> None:
    run, state, handoff, _ = _fixture(tmp_path)
    (run / "full_book_batch_001" / "checkpoint_05").mkdir()
    result = normalizer.normalize_recovery_handoff(handoff=handoff, state=state, source_sha256="source", run=run)
    assert result["normalized"] is False
    assert result["reason"] == "PARTIAL_CHECKPOINT_NOT_UNIQUE"


def test_supplied_raw_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    run, state, handoff, _ = _fixture(tmp_path)
    handoff["raw_sha256"] = "0" * 64
    result = normalizer.normalize_recovery_handoff(handoff=handoff, state=state, source_sha256="source", run=run)
    assert result["normalized"] is False
    assert result["reason"] == "IMMUTABLE_RAW_SHA_MISMATCH"


def test_missing_qa_evidence_is_not_guessed(tmp_path: Path) -> None:
    run, state, handoff, _ = _fixture(tmp_path)
    (run / "full_book_batch_001" / "checkpoint_04" / "translation_raw_qa_report.json").unlink()
    result = normalizer.normalize_recovery_handoff(handoff=handoff, state=state, source_sha256="source", run=run)
    assert result["normalized"] is False
    assert result["reason"] == "QA_EVIDENCE_MISSING_FOR_DERIVATION"


def test_complete_valid_handoff_stays_valid(tmp_path: Path) -> None:
    run, state, handoff, raw_sha = _fixture(tmp_path)
    cp = run / "full_book_batch_001" / "checkpoint_04"
    handoff.update({"active_batch": state["active_batch"], "checkpoint": state["next_checkpoint"], "partial_checkpoint_path": str(cp), "stage_reached": "FINAL_DETERMINISTIC_QA", "failing_work_order_ids": ["body.p.00001"], "relevant_qa_artifact_paths": [str(cp / "translation_raw_qa_report.json"), str(cp / "translation_normalized_qa_report.json"), str(cp / "translation_normalization_report.json")], "raw_exists": True, "raw_sha256": raw_sha, "created_at": "2026-09-17T00:00:00Z"})
    result = normalizer.normalize_recovery_handoff(handoff=handoff, state=state, source_sha256="source", run=run)
    assert result["normalized"] is True, result
    assert result["derived_fields"] == ["next_logical_work_order_id"]
