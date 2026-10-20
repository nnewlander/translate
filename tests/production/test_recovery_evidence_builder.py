"""Production-shaped recovery evidence integration tests."""

from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from booktrans.production_control.recovery_evidence_builder import build_recovery_evidence
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy

SPEC = importlib.util.spec_from_file_location("build_recovery_evidence", ROOT / "scripts" / "production" / "build_recovery_evidence.py")
assert SPEC and SPEC.loader
BUILDER_CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER_CLI)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _qa(ids: list[str], code: str) -> dict:
    records = []
    for index in range(25):
        display_id = f"body.p.{index + 1:05d}"
        records.append({"display_id": display_id, "unit_uid": f"u{index}", "passed": display_id not in ids,
                        "diagnostics": [] if display_id not in ids else [{"code": code}]})
    return {"records": records, "pass_count": 25 - len(ids), "fail_count": len(ids)}


def _production_shape(tmp_path: Path, *, code: str = "NUMBER_PRESERVATION", classification: str | None = None, failures: list[str] | None = None, raw_sha: str | None = None, stage: str = "FINAL_DETERMINISTIC_QA") -> tuple[dict, dict, Path]:
    failures = failures or ["body.p.00001"]
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.docx"
    source.write_bytes(b"immutable-source")
    batch = tmp_path / "full_book_batch_020"
    cp = batch / "checkpoint_04"
    cp.mkdir(parents=True)
    selection = tmp_path / "selection.json"
    ids = [f"body.p.{index + 1:05d}" for index in range(25)]
    _write_json(selection, {"selected_logical_work_order_ids": ids})
    raw = cp / "translation_raw_response.jsonl"
    raw.write_text('{"unit_uid":"u0","target_segments":[{"text":"target"}]}\n', encoding="utf-8")
    raw_hash = sha256(raw.read_bytes()).hexdigest()
    (cp / "translation_request.jsonl").write_text('{"display_id":"body.p.00001","source_segments":[{"kind":"anchored","text":"source"}]}\n', encoding="utf-8")
    (cp / "translation_normalized_response.jsonl").write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
    report = _qa(failures, code)
    _write_json(cp / "translation_raw_qa_report.json", report)
    _write_json(cp / "translation_normalized_qa_report.json", report)
    cursor = {"source_sha256": sha256(source.read_bytes()).hexdigest(), "active_batch": "FULL_BOOK_BATCH_020", "next_checkpoint": "checkpoint_04", "active_selection": {"path": str(selection)}}
    handoff = {"status": "RECOVERY_REQUIRED", "active_batch": "FULL_BOOK_BATCH_020", "checkpoint": "checkpoint_04", "partial_checkpoint_path": str(cp), "stage_reached": stage, "failure_classification": classification or code, "failing_work_order_ids": failures, "raw_exists": True, "raw_sha256": raw_sha or raw_hash}
    return handoff, cursor, source


def _evidence(tmp_path: Path, **kwargs: object) -> dict:
    handoff, cursor, source = _production_shape(tmp_path, **kwargs)
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    return build_recovery_evidence(handoff, cursor, facts)


def test_number_handoff_to_registry_is_eligible(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path)
    decision = decide_recovery_policy(evidence)
    assert evidence["qa_codes"] == ["NUMBER_PRESERVATION"]
    assert evidence["failure_classification"] == "LOCAL_DETERMINISTIC_QA_FAILURE"
    assert (decision["policy_id"], decision["handler_kind"], decision["eligible"]) == ("NUMERIC_REPRESENTATION", "QWEN_NARROW_REPAIR", True)


def test_exact_index_locator_number_routes_to_existing_deterministic_resume(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path, stage="SEMANTIC_TERMINAL")
    cp = Path(handoff["partial_checkpoint_path"])
    (cp / "reviewer_payloads").mkdir()
    _write_json(cp / "reviewer_payloads" / "body.p.00001.json", {
        "source_segments": [{"kind": "anchored", "text": "entry, "}, {"kind": "anchored", "text": "60"}],
        "target_segments": [{"kind": "anchored", "text": "条目，"}, {"kind": "anchored", "text": "]"}],
        "unit_context": {"continuation_relation": "INDEX_ENTRY", "source_structural_identity": {"content_role": "index_entry"}, "continuation_review_rules": {"numeric_locator_owner": "HARNESS_DETERMINISTIC"}},
        "semantic_review_group": {"member_work_order_ids": ["body.p.00001"]},
    })
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    evidence = build_recovery_evidence(handoff, cursor, facts)
    decision = decide_recovery_policy(evidence)
    assert evidence["deterministic_index_locator_recovery"] is True
    assert (decision["policy_id"], decision["handler_kind"]) == ("INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION", "CURRENT_CHECKPOINT_RESUME")


def test_semantic_terminal_numeric_anchor_ownership_uses_current_qa_for_resume(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path, stage="SEMANTIC_TERMINAL", code="NUMERIC_ANCHOR_DUPLICATION")
    cp = Path(handoff["partial_checkpoint_path"])
    report = _qa(["body.p.00001"], "NUMBER_PRESERVATION")
    report["records"][0]["diagnostics"].append({"code": "NUMERIC_ANCHOR_DUPLICATION"})
    _write_json(cp / "translation_raw_qa_report.json", report)
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    evidence = build_recovery_evidence(handoff, cursor, facts)
    decision = decide_recovery_policy(evidence)
    assert evidence["qa_codes"] == ["NUMBER_PRESERVATION", "NUMERIC_ANCHOR_DUPLICATION"]
    assert (decision["policy_id"], decision["handler_kind"], decision["eligible"]) == ("NUMERIC_ANCHOR_OWNERSHIP_CORRECTION", "CURRENT_CHECKPOINT_RESUME", True)


def test_single_numeric_anchor_duplication_uses_existing_ownership_resume(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path, stage="RAW_DETERMINISTIC_QA", code="NUMERIC_ANCHOR_DUPLICATION")
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    evidence = build_recovery_evidence(handoff, cursor, facts)
    decision = decide_recovery_policy(evidence)
    assert evidence["qa_codes"] == ["NUMERIC_ANCHOR_DUPLICATION"]
    assert (decision["policy_id"], decision["handler_kind"], decision["eligible"]) == ("NUMERIC_ANCHOR_OWNERSHIP_CORRECTION", "CURRENT_CHECKPOINT_RESUME", True)


@pytest.mark.parametrize("stage", ["RAW_DETERMINISTIC_QA_FAILED", "NORMALIZED_DETERMINISTIC_QA_FAILED", "FINAL_DETERMINISTIC_QA"])
def test_number_stage_matrix_has_one_canonical_policy(tmp_path: Path, stage: str) -> None:
    evidence = _evidence(tmp_path, stage=stage)
    canonical = evidence["canonical_qa_failure"]
    decision = decide_recovery_policy(evidence)
    assert canonical["valid"] is True
    assert canonical["qa_codes"] == ["NUMBER_PRESERVATION"]
    assert decision["policy_id"] == "NUMERIC_REPRESENTATION"


def test_final_qa_broad_label_has_same_number_result(tmp_path: Path) -> None:
    evidence = _evidence(tmp_path, classification="FINAL_DETERMINISTIC_QA_FAILED")
    # The handoff label may be broad; immutable QA is the authoritative code.
    assert decide_recovery_policy(evidence)["policy_id"] == "NUMERIC_REPRESENTATION"


@pytest.mark.parametrize("stage", ["NORMALIZED_DETERMINISTIC_QA_FAILED", "FINAL_DETERMINISTIC_QA"])
def test_doi_production_shape_reaches_doi_policy(tmp_path: Path, stage: str) -> None:
    handoff, cursor, source = _production_shape(tmp_path, code="DOI_PRESERVATION", stage=stage)
    cp = Path(handoff["partial_checkpoint_path"])
    doi = "https://doi.org/10.1000/example"
    request = {"display_id": "body.p.00001", "source_text": f"{doi}.", "source_segments": [
        {"kind": "protected", "text": doi}, {"kind": "anchored", "text": "."},
    ]}
    response = {"unit_uid": "u0", "target_segments": [{"text": doi}, {"text": "。"}]}
    (cp / "translation_request.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
    raw = cp / "translation_raw_response.jsonl"
    raw.write_text(json.dumps(response) + "\n", encoding="utf-8")
    (cp / "translation_normalized_response.jsonl").write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
    handoff["raw_sha256"] = sha256(raw.read_bytes()).hexdigest()
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    decision = decide_recovery_policy(build_recovery_evidence(handoff, cursor, facts))
    assert (decision["policy_id"], decision["eligible"]) == ("DOI_PRESERVATION", True)


def test_unknown_and_dense_qa_are_closed_world_denials(tmp_path: Path) -> None:
    unknown = _evidence(tmp_path / "unknown", code="UNSEEN_QA_CODE")
    dense = _evidence(tmp_path / "dense", failures=[f"body.p.{index + 1:05d}" for index in range(4)])
    assert decide_recovery_policy(unknown)["eligible"] is False
    assert decide_recovery_policy(dense)["eligible"] is False


def test_raw_drift_and_missing_artifacts_are_closed_world_denials(tmp_path: Path) -> None:
    raw_drift = _evidence(tmp_path / "drift", raw_sha="0" * 64)
    handoff, cursor, source = _production_shape(tmp_path / "missing")
    Path(handoff["partial_checkpoint_path"]).joinpath("translation_raw_qa_report.json").unlink()
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    missing = build_recovery_evidence(handoff, cursor, facts)
    assert decide_recovery_policy(raw_drift)["denial_reason"] == "IMMUTABLE_RAW_SHA_MISMATCH"
    assert decide_recovery_policy(missing)["eligible"] is False


@pytest.mark.parametrize("stage", ["RAW_DETERMINISTIC_QA_FAILED", "NORMALIZED_DETERMINISTIC_QA_FAILED", "FINAL_DETERMINISTIC_QA"])
def test_unknown_stage_matrix_blocks(tmp_path: Path, stage: str) -> None:
    evidence = _evidence(tmp_path, code="UNSEEN_QA_CODE", stage=stage)
    assert evidence["canonical_qa_failure"]["qa_codes"] == ["UNSEEN_QA_CODE"]
    assert decide_recovery_policy(evidence)["eligible"] is False


def test_source_drift_missing_and_conflicting_qa_are_closed_world(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path / "source")
    source.write_bytes(b"changed")
    source_drift = build_recovery_evidence(handoff, cursor, BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source))
    handoff2, cursor2, source2 = _production_shape(tmp_path / "conflict")
    cp = Path(handoff2["partial_checkpoint_path"])
    _write_json(cp / "translation_raw_qa_report.json", _qa([], "NUMBER_PRESERVATION"))
    conflict = build_recovery_evidence(handoff2, cursor2, BUILDER_CLI.collect_immutable_recovery_facts(handoff2, cursor2, source2))
    handoff3, cursor3, source3 = _production_shape(tmp_path / "missing-normalized")
    (Path(handoff3["partial_checkpoint_path"]) / "translation_normalized_qa_report.json").unlink()
    missing = build_recovery_evidence(handoff3, cursor3, BUILDER_CLI.collect_immutable_recovery_facts(handoff3, cursor3, source3))
    assert decide_recovery_policy(source_drift)["eligible"] is False
    assert conflict["canonical_qa_failure"]["denial_reason"] == "QA_REPORT_CONFLICT"
    assert decide_recovery_policy(conflict)["eligible"] is False
    assert decide_recovery_policy(missing)["eligible"] is False


def test_d050_production_shape_preserves_exact_bounded_contract(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path, stage="D050_PREFLIGHT", code="D050_PREFLIGHT_REQUIRED")
    handoff["raw_exists"] = False
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    decision = decide_recovery_policy(build_recovery_evidence(handoff, cursor, facts))
    assert (decision["policy_id"], decision["eligible"]) == ("D050_EXACT_EVIDENCE", True)


def test_precheckpoint_provider_failure_builds_only_exact_local_requirement(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path, stage="D050_PDF_CAPABILITY", code="EVIDENCE_PROVIDER_UNAVAILABLE")
    handoff.update({"raw_exists": False, "raw_sha256": "", "partial_checkpoint_path": "", "failing_work_order_ids": ["body.p.00001"]})
    pdf = source.with_suffix(".pdf"); pdf.write_bytes(b"immutable-pdf")
    handoff["evidence_requirement"] = {"pdf_sha256": sha256(pdf.read_bytes()).hexdigest(), "page_numbers": [458], "logical_work_order_ids": ["body.p.00001"], "evidence_requirement": "D050_PDF_CORROBORATION"}
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    decision = decide_recovery_policy(build_recovery_evidence(handoff, cursor, facts))
    assert (decision["family"], decision["policy_id"], decision["handler_kind"], decision["attempt_budget"]) == ("EVIDENCE_INFRASTRUCTURE", "PDF_EVIDENCE_PROVIDER_RECOVERY", "SUPERVISOR_RETRY", 2)


def test_precheckpoint_provider_requirement_sha_drift_is_denied(tmp_path: Path) -> None:
    handoff, cursor, source = _production_shape(tmp_path, stage="D050_PDF_CAPABILITY", code="EVIDENCE_PROVIDER_UNAVAILABLE")
    handoff.update({"raw_exists": False, "raw_sha256": "", "partial_checkpoint_path": "", "failing_work_order_ids": ["body.p.00001"], "evidence_requirement": {"pdf_sha256": "0" * 64, "page_numbers": [458], "logical_work_order_ids": ["body.p.00001"], "evidence_requirement": "D050_PDF_CORROBORATION"}})
    source.with_suffix(".pdf").write_bytes(b"immutable-pdf")
    facts = BUILDER_CLI.collect_immutable_recovery_facts(handoff, cursor, source)
    assert decide_recovery_policy(build_recovery_evidence(handoff, cursor, facts))["eligible"] is False
