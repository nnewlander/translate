"""Canonical, side-effect-free facts for deterministic QA failures."""

from __future__ import annotations

from typing import Any, Mapping


SCHEMA_VERSION = 1
_STAGE_REPORTS = {
    "RAW_DETERMINISTIC_QA": ("raw",),
    "NORMALIZED_DETERMINISTIC_QA": ("normalized", "raw"),
    # A final-stage handoff can occur before a final report is persisted; the
    # normalized deterministic report is then the immutable stage evidence.
    "FINAL_DETERMINISTIC_QA": ("final", "normalized", "raw"),
}


def _stage_key(stage: str) -> str | None:
    for key in _STAGE_REPORTS:
        if stage == key or stage.startswith(f"{key}_"):
            return key
    return None


def _failed(report: Mapping[str, Any]) -> tuple[list[str], set[str], int] | None:
    records = report.get("records")
    if not isinstance(records, list) or not records:
        return None
    ids: list[str] = []
    codes: set[str] = set()
    for row in records:
        if not isinstance(row, Mapping) or not isinstance(row.get("display_id"), str):
            return None
        if row.get("passed"):
            continue
        diagnostics = row.get("diagnostics")
        if not isinstance(diagnostics, list) or not diagnostics:
            return None
        ids.append(row["display_id"])
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, Mapping) or not isinstance(diagnostic.get("code"), str):
                return None
            codes.add(diagnostic["code"])
    return ids, codes, len(records)


def canonicalize_qa_failure(
    *,
    stage: str,
    handoff: Mapping[str, Any],
    reports: Mapping[str, Mapping[str, Any]],
    report_paths: Mapping[str, str],
    source_sha_valid: bool,
    raw_required: bool,
    raw_sha_valid: bool,
) -> dict[str, Any]:
    """Produce a canonical QA envelope from already-read immutable reports.

    This never selects a recovery policy and never reads or writes disk.
    """
    requested = [str(item) for item in handoff.get("failing_work_order_ids") or []]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "failing_work_order_ids": requested,
        "qa_codes": [],
        "failure_count": len(requested),
        "total_count": None,
        "density": None,
        "source_sha_valid": bool(source_sha_valid),
        "raw_required": bool(raw_required),
        "raw_sha_valid": bool(raw_sha_valid),
        "qa_report_paths": dict(report_paths),
        "evidence_summary": "",
        "valid": False,
        "denial_reason": None,
    }
    stage_key = _stage_key(stage)
    if stage_key is None:
        result["denial_reason"] = "QA_STAGE_UNSUPPORTED"
        result["evidence_summary"] = "unsupported deterministic QA stage"
        return result
    if not source_sha_valid:
        result["denial_reason"] = "SOURCE_SHA_MISMATCH"
        result["evidence_summary"] = "source SHA changed"
        return result
    if raw_required and not raw_sha_valid:
        result["denial_reason"] = "IMMUTABLE_RAW_SHA_MISMATCH"
        result["evidence_summary"] = "immutable RAW SHA mismatch"
        return result
    if not requested or len(set(requested)) != len(requested):
        result["denial_reason"] = "HANDOFF_FAILURE_SCOPE_INVALID"
        result["evidence_summary"] = "handoff failure scope invalid"
        return result
    primary = "raw" if stage_key == "RAW_DETERMINISTIC_QA" else (
        "normalized" if stage_key == "NORMALIZED_DETERMINISTIC_QA" else ("final" if "final" in reports else "normalized")
    )
    if primary not in reports:
        result["denial_reason"] = "QA_STAGE_REPORT_MISSING"
        result["evidence_summary"] = "no immutable QA report for stage"
        return result
    if stage_key != "RAW_DETERMINISTIC_QA" and "raw" not in reports:
        result["denial_reason"] = "QA_STAGE_REPORT_MISSING"
        result["evidence_summary"] = "immutable raw QA report missing"
        return result
    candidates = [(primary, reports[primary])]
    for name in _STAGE_REPORTS[stage_key]:
        if name != primary and name in reports:
            candidates.append((name, reports[name]))
    parsed = []
    for name, report in candidates:
        facts = _failed(report)
        if facts is None:
            result["denial_reason"] = "QA_REPORT_INVALID"
            result["evidence_summary"] = f"invalid {name} QA report"
            return result
        parsed.append((name, *facts))
    primary_name, failed_ids, codes, total = parsed[0]
    if failed_ids != requested:
        result["denial_reason"] = "QA_FAILURE_SCOPE_MISMATCH"
        result["evidence_summary"] = f"{primary_name} failures differ from handoff"
        return result
    # If multiple reports are supplied for one stage, their failure facts must
    # agree; otherwise no unique repair scope can be claimed.
    for name, other_ids, other_codes, other_total in parsed[1:]:
        if (other_ids, other_codes, other_total) != (failed_ids, codes, total):
            result["denial_reason"] = "QA_REPORT_CONFLICT"
            result["evidence_summary"] = f"{primary_name} and {name} QA evidence conflicts"
            return result
    result.update({
        "qa_codes": sorted(codes),
        "failure_count": len(failed_ids),
        "total_count": total,
        "density": (len(failed_ids) / total) if total else None,
        "valid": True,
        "evidence_summary": f"{stage_key}:{primary_name}:{len(failed_ids)}/{total}",
    })
    return result
