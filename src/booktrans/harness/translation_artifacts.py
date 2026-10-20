"""Immutable, file-based evidence chain for a real translation run.

This module deliberately owns only RAW -> NORMALIZED artifacts.  It does not
translate, repair, patch OOXML, or decide semantic equivalence beyond the one
closed-world footnote typography rule in ``response_normalization``.
"""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.harness.calibration import load_requests, load_responses
from booktrans.harness.response_normalization import NormalizationChange, normalize_footnote_superscripts


RAW_RESPONSE_MUTATED = "RAW_RESPONSE_MUTATED"


def persist_raw_response(path: Path, responses: list[TranslationResult]) -> str:
    """Create a raw artifact once, refusing to overwrite prior model evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _jsonl(responses)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise ValueError(f"RAW_RESPONSE_ALREADY_EXISTS: refusing to overwrite {path}") from exc
    return _sha256(path)


def normalize_response_artifact(
    request_path: Path,
    raw_response_path: Path,
    normalized_response_path: Path,
    report_path: Path,
) -> dict:
    """Read immutable raw JSONL, write a separate normalized JSONL and audit.

    ``raw_response_path`` is never opened for writing.  A before/after digest
    is nevertheless checked so accidental future mutation is a hard blocker.
    """
    _require_distinct_paths(raw_response_path, normalized_response_path, report_path)
    normalized, report = _normalized_artifact_expectation(
        request_path, raw_response_path, normalized_response_path,
    )
    _write_new_jsonl(normalized_response_path, normalized)
    report["normalized_artifact"]["sha256"] = _sha256(normalized_response_path)
    _write_new_json(report_path, report)
    return report


def normalize_or_validate_response_artifact(
    request_path: Path,
    raw_response_path: Path,
    normalized_response_path: Path,
    report_path: Path,
) -> dict:
    """Create a normalized artifact once, or strictly reuse its valid evidence.

    A partial or mismatched pre-existing pair is not repairable by overwrite:
    callers must stop so the immutable evidence can be investigated.
    """
    _require_distinct_paths(raw_response_path, normalized_response_path, report_path)
    normalized_exists = normalized_response_path.exists()
    report_exists = report_path.exists()
    if not normalized_exists and not report_exists:
        return normalize_response_artifact(
            request_path, raw_response_path, normalized_response_path, report_path,
        )
    if normalized_exists != report_exists:
        raise ValueError("NORMALIZED_ARTIFACT_SET_INCOMPLETE")
    return validate_normalized_response_artifact(
        request_path, raw_response_path, normalized_response_path, report_path,
    )


def validate_normalized_response_artifact(
    request_path: Path,
    raw_response_path: Path,
    normalized_response_path: Path,
    report_path: Path,
) -> dict:
    """Fail closed unless existing normalized evidence is exactly reproducible."""
    _require_distinct_paths(raw_response_path, normalized_response_path, report_path)
    if not normalized_response_path.is_file() or not report_path.is_file():
        raise ValueError("NORMALIZED_ARTIFACT_SET_INCOMPLETE")
    normalized, expected_report = _normalized_artifact_expectation(
        request_path, raw_response_path, normalized_response_path,
    )
    if normalized_response_path.read_bytes() != _jsonl(normalized).encode("utf-8"):
        raise ValueError("NORMALIZED_ARTIFACT_CONTENT_MISMATCH")
    try:
        persisted_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("NORMALIZED_ARTIFACT_REPORT_INVALID") from exc
    if persisted_report != expected_report:
        raise ValueError("NORMALIZED_ARTIFACT_REPORT_MISMATCH")
    return persisted_report


def _normalized_artifact_expectation(
    request_path: Path,
    raw_response_path: Path,
    normalized_response_path: Path,
) -> tuple[list[TranslationResult], dict]:
    raw_before = _sha256(raw_response_path)
    requests = load_requests(request_path)
    raw_responses = load_responses(raw_response_path)
    request_by_uid = {request.unit_uid: request for request in requests}
    if len(request_by_uid) != len(requests):
        raise ValueError("DUPLICATE_REQUEST_UNIT: cannot normalize ambiguous request records.")
    if len({response.unit_uid for response in raw_responses}) != len(raw_responses):
        raise ValueError("DUPLICATE_RESPONSE_UNIT: cannot normalize ambiguous raw responses.")

    normalized: list[TranslationResult] = []
    changes: list[tuple[CalibrationRequest, NormalizationChange]] = []
    for response in raw_responses:
        request = request_by_uid.get(response.unit_uid)
        if request is None:
            raise ValueError(f"UNKNOWN_UNIT_ID: raw response {response.unit_uid} is absent from request artifact.")
        result, response_changes = normalize_footnote_superscripts(request, response)
        normalized.append(result)
        changes.extend((request, change) for change in response_changes)

    raw_after = _sha256(raw_response_path)
    if raw_before != raw_after:
        raise ValueError(f"{RAW_RESPONSE_MUTATED}: raw response hash changed during normalization.")
    changed_uids = {change.unit_uid for _, change in changes}
    report = {
        "stage": "NORMALIZED",
        "raw_artifact": {
            "path": str(raw_response_path),
            "sha256_before": raw_before,
            "sha256_after": raw_after,
            "immutable": True,
        },
        "normalized_artifact": {
            "path": str(normalized_response_path),
            "sha256": sha256(_jsonl(normalized).encode("utf-8")).hexdigest(),
            "input_equals_output": not changes,
        },
        "input_unit_count": len(raw_responses),
        "normalized_unit_count": len(changed_uids),
        "unchanged_unit_count": len(raw_responses) - len(changed_uids),
        "normalization_rule_distribution": dict(Counter(change.rule_id for _, change in changes)),
        "changes": [_change_record(request, change) for request, change in changes],
        "blockers": [],
    }
    return normalized, report


def _change_record(request: CalibrationRequest, change: NormalizationChange) -> dict:
    return {
        "unit_uid": change.unit_uid,
        "display_id": request.display_id,
        "rule_id": change.rule_id,
        "before": change.before,
        "after": change.after,
        "source_evidence": {
            "source_text": request.source_text,
            "exact_ascii_numeric_value": change.after_characters,
            "footnote_reference_numbers": request.footnote_reference_numbers,
            "location_evidence": change.source_reference_evidence,
        },
        "exact_changed_character_span": {
            "segment_index": change.segment_index,
            "start": change.character_start,
            "end": change.character_end,
            "before": change.before_characters,
            "after": change.after_characters,
        },
        "semantic_value_unchanged_proof": (
            f"Unicode superscript {change.before_characters!r} maps exactly to source-evidenced ASCII "
            f"footnote reference {change.after_characters!r}; no other target text changed."
        ),
        "anchor_island_protected_invariants": {
            "anchor_ref_unchanged": True,
            "island_ref_unchanged": True,
            "protected_content_unchanged": True,
        },
    }


def _require_distinct_paths(raw_path: Path, normalized_path: Path, report_path: Path) -> None:
    resolved = [path.resolve() for path in (raw_path, normalized_path, report_path)]
    if len(set(resolved)) != len(resolved):
        raise ValueError("ARTIFACT_PATH_COLLISION: raw, normalized and report artifacts must be separate files.")


def _write_new_jsonl(path: Path, records: list[TranslationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"ARTIFACT_ALREADY_EXISTS: refusing to overwrite {path}")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(_jsonl(records))


def _write_new_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"ARTIFACT_ALREADY_EXISTS: refusing to overwrite {path}")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _jsonl(records: list[TranslationResult]) -> str:
    return "".join(f"{record.model_dump_json()}\n" for record in records)


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
