"""Classify a failed final deterministic-QA handoff from immutable evidence.

This is deliberately a closed-world control-plane classifier.  It does not
repair a translation and it never writes a checkpoint artifact.  A positive
result means only that a fresh recovery worker may attempt the named,
already-supported targeted repair policy.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable


# A checkpoint has 25 records.  Both limits are required: this is a stated
# sparse-local policy, rather than an inference that every small failure set is
# safe to repair.
MAX_SPARSE_LOCAL_FAILURES = 3
MAX_SPARSE_FAILURE_DENSITY = 0.12
KNOWN_LOCAL_CODES = {"NUMBER_PRESERVATION", "DOI_PRESERVATION", "URL_PRESERVATION", "EMPTY_TARGET", "PROTECTED_TOKEN"}
DOI_URL_RE = re.compile(r"https://doi\.org/10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _failed_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError("QA_RECORDS_INVALID")
    result = [record for record in records if isinstance(record, dict) and not record.get("passed")]
    if len(result) != sum(1 for record in records if isinstance(record, dict) and not record.get("passed")):
        raise ValueError("QA_RECORD_INVALID")
    return result


def _codes(records: Iterable[dict[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for record in records:
        diagnostics = record.get("diagnostics")
        if not isinstance(diagnostics, list) or not diagnostics:
            raise ValueError("QA_DIAGNOSTICS_INVALID")
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict) or not isinstance(diagnostic.get("code"), str):
                raise ValueError("QA_DIAGNOSTIC_INVALID")
            codes.add(diagnostic["code"])
    return codes


def _jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"JSONL_RECORD_INVALID:{path.name}")
    return values


def _triage_exact_doi_boundary(checkpoint: Path, failing_ids: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    """Accept only an exact protected DOI plus adjacent punctuation repair.

    This intentionally excludes generic DOI rewriting.  The URL must be a
    unique source protected segment and the only changed target segment must
    be the immediately following punctuation boundary.
    """
    request_path = checkpoint / "translation_request.jsonl"
    raw_path = checkpoint / "translation_raw_response.jsonl"
    normalized_path = checkpoint / "translation_normalized_response.jsonl"
    if not all(path.is_file() for path in (request_path, raw_path, normalized_path)):
        return None, "DOI_TRIAGE_ARTIFACT_MISSING"
    try:
        requests, raw, normalized = (_jsonl(path) for path in (request_path, raw_path, normalized_path))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return None, f"DOI_TRIAGE_ARTIFACT_INVALID:{error}"
    if len(requests) != len(raw) or raw != normalized:
        return None, "DOI_TRIAGE_RAW_NORMALIZED_CHAIN_MISMATCH"
    repairs: list[dict[str, Any]] = []
    for request, raw_response in zip(requests, raw, strict=True):
        if request.get("display_id") not in failing_ids:
            continue
        source_segments, target_segments = request.get("source_segments"), raw_response.get("target_segments")
        if not isinstance(source_segments, list) or not isinstance(target_segments, list) or len(source_segments) != len(target_segments):
            return None, "DOI_TRIAGE_SEGMENT_CHAIN_INVALID"
        matches = [(index, DOI_URL_RE.fullmatch(str(segment.get("text", "")))) for index, segment in enumerate(source_segments) if isinstance(segment, dict)]
        doi_segments = [(index, match.group(0)) for index, match in matches if match]
        if len(doi_segments) != 1:
            return None, "DOI_SOURCE_NOT_UNIQUE"
        index, doi_url = doi_segments[0]
        if source_segments[index].get("kind") != "protected" or index + 1 >= len(source_segments):
            return None, "DOI_SOURCE_BINDING_AMBIGUOUS"
        source_suffix = source_segments[index + 1]
        target_url, target_suffix = target_segments[index], target_segments[index + 1]
        if not all(isinstance(value, dict) for value in (source_suffix, target_url, target_suffix)):
            return None, "DOI_TARGET_SEGMENT_INVALID"
        if source_suffix.get("text") != "." or target_url.get("text") != doi_url:
            return None, "DOI_SOURCE_TARGET_BINDING_MISMATCH"
        # The only first-stage DOI auto repair is a full-width punctuation
        # boundary.  Anything embedded, split, altered, or plural is escalated.
        if target_suffix.get("text") != "。":
            return None, "DOI_PUNCTUATION_BOUNDARY_NOT_UNIQUE"
        source_text = str(request.get("source_text", ""))
        if source_text.count(doi_url) != 1 or f"{doi_url}." not in source_text:
            return None, "DOI_SOURCE_TEXT_BINDING_AMBIGUOUS"
        repairs.append({
            "logical_work_order_id": request["display_id"], "source_doi_url": doi_url,
            "expected_doi_token": f"{doi_url}.", "target_segment_index": index + 1,
            "target_before": "。", "target_after": ".",
        })
    if len(repairs) != len(failing_ids):
        return None, "DOI_FAILURE_SCOPE_MISMATCH"
    return {"repairs": repairs}, None


def _triage_exact_source_url_restoration(checkpoint: Path, failing_ids: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    """Prove only whole-segment, source-owned URL restoration eligibility."""
    request_path = checkpoint / "translation_request.jsonl"
    raw_path = checkpoint / "translation_raw_response.jsonl"
    if not all(path.is_file() for path in (request_path, raw_path)):
        return None, "URL_TRIAGE_ARTIFACT_MISSING"
    try:
        requests, raw = (_jsonl(path) for path in (request_path, raw_path))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return None, f"URL_TRIAGE_ARTIFACT_INVALID:{error}"
    if len(requests) != len(raw):
        return None, "URL_TRIAGE_CHAIN_MISMATCH"
    # Import the established executor primitive only for its pure eligibility
    # proof; it never writes, normalizes, or contacts a provider.
    from booktrans.domain.calibration import CalibrationRequest, TranslationResult
    from booktrans.production_worker.deterministic_recovery import restore_exact_source_urls
    repairs: list[dict[str, Any]] = []
    for request_payload, raw_payload in zip(requests, raw, strict=True):
        if request_payload.get("display_id") not in failing_ids:
            continue
        try:
            request = CalibrationRequest.model_validate(request_payload)
            candidate = TranslationResult.model_validate(raw_payload)
        except Exception:
            return None, "URL_TRIAGE_SEGMENT_CHAIN_INVALID"
        repaired = restore_exact_source_urls(request, candidate)
        if repaired is None or repaired.model_dump() == candidate.model_dump():
            return None, "URL_SOURCE_OR_BINDING_AMBIGUOUS"
        urls = [match.group(0).rstrip(".,;:!?。！？）】》」』") for match in re.finditer(r"(?:https?://|www\.)[^\s<>]+", request.source_text, re.I)]
        if not urls or any("".join(segment.text for segment in repaired.target_segments).count(url) != 1 for url in urls):
            return None, "URL_RESTORATION_NOT_EXACT"
        repairs.append({"logical_work_order_id": request.display_id, "unit_uid": request.unit_uid, "source_urls": urls})
    if len(repairs) != len(failing_ids):
        return None, "URL_FAILURE_SCOPE_MISMATCH"
    return {"repairs": repairs}, None


def classify_final_deterministic_qa(
    *,
    checkpoint: Path,
    expected_raw_sha256: str,
    expected_source_sha256: str,
    actual_source_sha256: str,
    handoff_failing_ids: Iterable[str],
) -> dict[str, Any]:
    """Return the only canonical local class currently eligible for recovery.

    Any missing evidence, SHA drift, unknown code, or non-sparse failure is a
    fail-closed result.  The caller must still perform worker ownership and
    campaign identity checks.
    """
    denied: dict[str, Any] = {
        "eligible": False,
        "canonical_class": None,
        "repair_policy": None,
        "reason": None,
    }
    if actual_source_sha256 != expected_source_sha256:
        denied["reason"] = "SOURCE_SHA_MISMATCH"
        return denied
    raw = checkpoint / "translation_raw_response.jsonl"
    raw_qa = checkpoint / "translation_raw_qa_report.json"
    normalized_qa = checkpoint / "translation_normalized_qa_report.json"
    required = (checkpoint / "translation_request.jsonl", raw, raw_qa)
    if not all(path.is_file() for path in required):
        denied["reason"] = "QA_TRIAGE_EVIDENCE_MISSING"
        return denied
    if any((checkpoint / name).exists() for name in (
        "translation_final_qa_report.json", "checkpoint_evidence.json",
    )):
        denied["reason"] = "CHECKPOINT_ALREADY_FINALIZED"
        return denied
    raw_sha = sha256(raw.read_bytes()).hexdigest()
    if not expected_raw_sha256 or raw_sha.lower() != expected_raw_sha256.lower():
        denied["reason"] = "IMMUTABLE_RAW_SHA_MISMATCH"
        return denied
    try:
        raw_failed = _failed_records(_load_json(raw_qa))
        # URL exact-source restoration runs before normalization: the RAW
        # candidate is the current authority and the raw report is therefore
        # the only immutable QA evidence available.  All older policies keep
        # their existing RAW/normalized agreement requirement below.
        normalized_failed = _failed_records(_load_json(normalized_qa)) if normalized_qa.is_file() else raw_failed
        raw_ids = [str(record.get("display_id")) for record in raw_failed]
        normalized_ids = [str(record.get("display_id")) for record in normalized_failed]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        denied["reason"] = f"QA_EVIDENCE_INVALID:{error}"
        return denied
    requested_ids = [str(item) for item in handoff_failing_ids]
    if not requested_ids or len(set(requested_ids)) != len(requested_ids):
        denied["reason"] = "HANDOFF_FAILURE_SCOPE_INVALID"
        return denied
    if raw_ids != requested_ids or normalized_ids != requested_ids:
        denied["reason"] = "QA_FAILURE_SCOPE_MISMATCH"
        return denied
    total = len(_load_json(normalized_qa if normalized_qa.is_file() else raw_qa).get("records", []))
    density = len(requested_ids) / total if total else 1.0
    if len(requested_ids) > MAX_SPARSE_LOCAL_FAILURES or density > MAX_SPARSE_FAILURE_DENSITY:
        denied["reason"] = "QA_FAILURE_NOT_SPARSE"
        denied["failure_count"] = len(requested_ids)
        denied["failure_density"] = density
        return denied
    try:
        codes = _codes(raw_failed) | _codes(normalized_failed)
    except ValueError as error:
        denied["reason"] = str(error)
        return denied
    if not codes <= KNOWN_LOCAL_CODES:
        denied["reason"] = "QA_FAILURE_CODE_NOT_ALLOWLISTED"
        denied["codes"] = sorted(codes)
        return denied
    if not normalized_qa.is_file() and codes != {"URL_PRESERVATION"}:
        denied["reason"] = "QA_TRIAGE_EVIDENCE_MISSING"
        denied["codes"] = sorted(codes)
        return denied
    if codes == {"NUMBER_PRESERVATION"}:
        return {
            "eligible": True,
            "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
            "repair_policy": "NUMERIC_REPRESENTATION",
            "reason": None,
            "failure_count": len(requested_ids),
            "failure_density": density,
            "failing_work_order_ids": requested_ids,
            "raw_sha256": raw_sha,
            "codes": sorted(codes),
        }
    if codes == {"DOI_PRESERVATION"}:
        doi, reason = _triage_exact_doi_boundary(checkpoint, requested_ids)
        if doi is None:
            denied["reason"] = reason
            denied["codes"] = sorted(codes)
            return denied
        return {
            "eligible": True,
            "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
            "repair_policy": "DOI_PRESERVATION",
            "reason": None,
            "failure_count": len(requested_ids),
            "failure_density": density,
            "failing_work_order_ids": requested_ids,
            "raw_sha256": raw_sha,
            "codes": sorted(codes),
            "doi": doi,
        }
    if codes == {"URL_PRESERVATION"}:
        url, reason = _triage_exact_source_url_restoration(checkpoint, requested_ids)
        if url is None:
            denied["reason"] = reason
            denied["codes"] = sorted(codes)
            return denied
        return {
            "eligible": True,
            "canonical_class": "LOCAL_DETERMINISTIC_QA_FAILURE",
            "repair_policy": "EXACT_SOURCE_URL_RESTORATION",
            "reason": None,
            "failure_count": len(requested_ids),
            "failure_density": density,
            "failing_work_order_ids": requested_ids,
            "raw_sha256": raw_sha,
            "codes": sorted(codes),
            "url": url,
        }
    # Other known codes retain dedicated recovery paths; they are not silently
    # reclassified from a broad final-QA worker label.
    if codes != {"NUMBER_PRESERVATION"}:
        denied["reason"] = "QA_CODE_REQUIRES_DEDICATED_POLICY"
        denied["codes"] = sorted(codes)
        return denied
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--expected-raw-sha256", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--actual-source-sha256", required=True)
    parser.add_argument("--failing-work-order-id", action="append", default=[])
    args = parser.parse_args()
    result = classify_final_deterministic_qa(
        checkpoint=args.checkpoint,
        expected_raw_sha256=args.expected_raw_sha256,
        expected_source_sha256=args.expected_source_sha256,
        actual_source_sha256=args.actual_source_sha256,
        handoff_failing_ids=args.failing_work_order_id,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["eligible"] else 1)


if __name__ == "__main__":
    main()
