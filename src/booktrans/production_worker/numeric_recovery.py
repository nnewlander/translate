"""Production-shaped numeric recovery: canonical QA → evidence builder → registry."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.harness.deterministic_qa import _numeric_tokens
from booktrans.production_control.canonical_qa_failure import canonicalize_qa_failure
from booktrans.production_control.recovery_evidence_builder import build_recovery_evidence
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy
from booktrans.production_worker.qa import qa_codes
from booktrans.providers.repair_gate import decide_approved_repair_contract
from booktrans.providers.translation_provider import TranslationProviderRequest


def failing_number_segment_indices(request: CalibrationRequest, raw: TranslationResult) -> list[int] | None:
    """Map unit-level NUMBER_PRESERVATION onto exact non-protected segment indices."""
    if len(request.source_segments) != len(raw.target_segments):
        return None
    footnotes = list(request.footnote_reference_numbers or [])
    indices: list[int] = []
    for index, (source, target) in enumerate(zip(request.source_segments, raw.target_segments, strict=True)):
        if source.kind == "protected":
            continue
        source_tokens = list(_numeric_tokens(source.text))
        for value in footnotes:
            if value in source.text and value not in source_tokens:
                source_tokens.append(value)
        missing = [token for token in source_tokens if token not in target.text]
        extra = [token for token in _numeric_tokens(target.text) if token not in source_tokens]
        if missing or extra:
            indices.append(index)
    return indices or None


def load_qa_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def failing_display_ids(report: Mapping[str, Any] | None) -> list[str]:
    if not report:
        return []
    ids: list[str] = []
    for row in report.get("records") or []:
        if isinstance(row, Mapping) and not row.get("passed") and isinstance(row.get("display_id"), str):
            ids.append(row["display_id"])
    return ids


def decide_numeric_recovery_policy(
    *,
    output_dir: Path,
    source_sha256: str,
    selection_ids: Sequence[str],
    raw_sha256: str,
    active_batch: str,
    checkpoint: str,
) -> dict[str, Any]:
    """Canonical QA failure → recovery evidence builder → policy registry."""
    raw_report = load_qa_report(output_dir / "translation_raw_qa_report.json")
    normalized_report = load_qa_report(output_dir / "translation_normalized_qa_report.json")
    final_report = load_qa_report(output_dir / "translation_final_qa_report.json")
    failing = failing_display_ids(raw_report)
    reports: dict[str, Mapping[str, Any]] = {}
    paths: dict[str, str] = {}
    if raw_report is not None:
        reports["raw"] = raw_report
        paths["raw"] = str(output_dir / "translation_raw_qa_report.json")
    if normalized_report is not None:
        reports["normalized"] = normalized_report
        paths["normalized"] = str(output_dir / "translation_normalized_qa_report.json")
    if final_report is not None:
        reports["final"] = final_report
        paths["final"] = str(output_dir / "translation_final_qa_report.json")
    handoff = {
        "status": "RECOVERY_REQUIRED",
        "active_batch": active_batch,
        "checkpoint": checkpoint,
        "partial_checkpoint_path": str(output_dir),
        "stage_reached": "RAW_DETERMINISTIC_QA_FAILED",
        "failure_classification": "NUMBER_PRESERVATION",
        "failing_work_order_ids": failing,
        "raw_exists": True,
        "raw_sha256": raw_sha256,
    }
    canonical = canonicalize_qa_failure(
        stage="RAW_DETERMINISTIC_QA_FAILED",
        handoff=handoff,
        reports=reports,
        report_paths=paths,
        source_sha_valid=True,
        raw_required=True,
        raw_sha_valid=True,
    )
    sparse = (
        canonical.get("valid") is True
        and int(canonical.get("failure_count") or 0) <= 3
        and canonical.get("density") is not None
        and float(canonical["density"]) <= 0.12
    )
    codes = list(canonical.get("qa_codes") or [])
    if canonical.get("valid") and sparse and codes == ["NUMBER_PRESERVATION"]:
        triage = {
            "eligible": True,
            "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
            "repair_policy": "NUMERIC_REPRESENTATION",
            "reason": None,
            "failure_count": canonical["failure_count"],
            "failure_density": canonical["density"],
            "failing_work_order_ids": failing,
            "raw_sha256": raw_sha256,
            "codes": codes,
        }
    elif canonical.get("valid") and not sparse:
        triage = {
            "eligible": False,
            "reason": "QA_FAILURE_NOT_SPARSE",
            "codes": codes,
            "failure_count": canonical.get("failure_count"),
            "failure_density": canonical.get("density"),
        }
    elif canonical.get("valid"):
        triage = {
            "eligible": False,
            "reason": "QA_FAILURE_CODE_NOT_ALLOWLISTED",
            "codes": codes,
            "failure_count": canonical.get("failure_count"),
            "failure_density": canonical.get("density"),
        }
    else:
        triage = {
            "eligible": False,
            "reason": canonical.get("denial_reason") or "CANONICAL_QA_INVALID",
            "codes": codes,
            "failure_count": canonical.get("failure_count"),
            "failure_density": canonical.get("density"),
        }
    facts = {
        "source_sha_valid": True,
        "identity_valid": True,
        "partial_path_exists": output_dir.is_dir(),
        "scope_active_and_unique": bool(failing)
        and len(failing) == len(set(failing))
        and len(failing) <= 5
        and all(item in set(selection_ids) for item in failing),
        "raw_sha_valid": sha256((output_dir / "translation_raw_response.jsonl").read_bytes()).hexdigest() == raw_sha256,
        "canonical_qa_failure": canonical,
        "final_qa_triage": triage,
    }
    cursor = {
        "source_sha256": source_sha256,
        "active_batch": active_batch,
        "next_checkpoint": checkpoint,
    }
    evidence = build_recovery_evidence(handoff, cursor, facts)
    decision = decide_recovery_policy(evidence)
    return {"evidence": evidence, "canonical": canonical, "decision": decision, "failing_work_order_ids": failing}


def approved_numeric_contract(
    provider_request: TranslationProviderRequest,
    raw: TranslationResult,
    qa_record: Mapping[str, Any],
    registry: Mapping[str, Any],
):
    decision = registry.get("decision") or {}
    if decision.get("policy_id") != "NUMERIC_REPRESENTATION" or decision.get("eligible") is not True:
        return None
    indices = failing_number_segment_indices(provider_request.request, raw)
    if not indices:
        return decide_approved_repair_contract(
            provider_request,
            raw,
            qa_codes=qa_codes(qa_record),
            qa_segment_indices=(),
            registry_evidence=decision if "source_sha_valid" in decision else registry.get("evidence"),
            allow_semantic_narrow=False,
        )
    return decide_approved_repair_contract(
        provider_request,
        raw,
        qa_codes=qa_codes(qa_record),
        qa_segment_indices=indices,
        registry_evidence=registry.get("evidence"),
        allow_semantic_narrow=False,
    )
