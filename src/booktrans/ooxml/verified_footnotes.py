"""Closed-world, run-scoped footnote-marker evidence for request export.

This is deliberately not a footnote detector.  Callers supply only records
already proven by OOXML plus read-only source-PDF evidence.
"""

from collections.abc import Mapping, Sequence
import json
from pathlib import Path

from booktrans.domain.calibration import (
    CalibrationRequest,
    VerifiedFootnoteNumericBinding,
    VerifiedFootnoteSourceSegment,
)


def load_exact_verified_evidence_registry(paths: Sequence[Path]) -> dict[tuple[str, str], tuple[dict[str, object], ...]]:
    """Load only exact VERIFIED D050 records, keyed by work-order identity.

    This is a read-only routing registry, not a detector or a numeric exemption.
    A conflicting duplicate key is rejected rather than guessed across chapters.
    """
    routed: dict[tuple[str, str], list[dict[str, object]]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        records = value.get("records")
        if not isinstance(records, list):
            raise ValueError(f"D050_EVIDENCE_RECORDS_INVALID: {path}")
        for record in records:
            if not isinstance(record, dict) or record.get("verification_status") != "VERIFIED":
                continue
            logical_id, unit_uid = record.get("logical_work_order_id"), record.get("unit_uid")
            marker, number = record.get("exact_source_marker_token"), record.get("footnote_number")
            if not all(isinstance(item, str) for item in (logical_id, unit_uid, marker, number)):
                raise ValueError(f"D050_EVIDENCE_IDENTITY_INVALID: {path}")
            exact_key = (logical_id, unit_uid, marker, number)
            if exact_key in seen:
                continue
            seen.add(exact_key)
            routed.setdefault((logical_id, unit_uid), []).append(record)
    return {key: tuple(records) for key, records in routed.items()}


def exact_evidence_for_work_order(
    registry: Mapping[tuple[str, str], Sequence[Mapping[str, object]]], *, logical_work_order_id: str, unit_uid: str,
) -> tuple[Mapping[str, object], ...]:
    """Return evidence solely for this exact logical-id/unit-id pair."""
    return tuple(registry.get((logical_work_order_id, unit_uid), ()))


def footnote_numbers_for_unit(
    evidence: Sequence[Mapping[str, object]] | None,
    *,
    unit_uid: str,
    source_text: str,
) -> list[str]:
    """Return exact verified markers for this source unit, in stable order."""
    values: list[str] = []
    for record in evidence or []:
        if record.get("verification_status") != "VERIFIED":
            continue
        if record.get("unit_uid") != unit_uid:
            continue
        marker = record.get("exact_source_marker_token")
        number = record.get("footnote_number")
        if not isinstance(marker, str) or not isinstance(number, str):
            continue
        # Both the marker and the number must be present in the current
        # request source.  A stale sidecar therefore cannot supply evidence.
        if marker not in source_text or number not in marker:
            continue
        if number not in values:
            values.append(number)
    return values


def verified_numeric_bindings_for_request(
    evidence: Sequence[Mapping[str, object]] | None,
    request: CalibrationRequest,
) -> list[VerifiedFootnoteNumericBinding]:
    """Export only D050 records whose exact source-segment binding still matches.

    This is intentionally a narrow exporter, not a parser. A record is ignored
    unless all of its already-proven fields exactly match the current request.
    """
    bindings: list[VerifiedFootnoteNumericBinding] = []
    for record in evidence or []:
        if record.get("verification_status") != "VERIFIED":
            continue
        if record.get("unit_uid") != request.unit_uid:
            continue
        marker = record.get("exact_source_marker_token")
        number = record.get("footnote_number")
        raw_segments = record.get("source_segment_binding")
        raw_tokens = record.get("pre_footnote_numeric_tokens")
        if not isinstance(marker, str) or not isinstance(number, str):
            continue
        if not isinstance(raw_segments, list) or not isinstance(raw_tokens, list):
            continue
        if marker not in request.source_text or number not in marker:
            continue
        try:
            binding_segments = [
                VerifiedFootnoteSourceSegment.model_validate(item)
                for item in raw_segments
            ]
        except (TypeError, ValueError):
            continue
        if not all(isinstance(item, str) for item in raw_tokens):
            continue
        positions: list[int] = []
        for segment in binding_segments:
            position = next(
                (
                    index
                    for index, request_segment in enumerate(request.source_segments)
                    if request_segment.anchor_ref == segment.anchor_ref
                    and request_segment.text == segment.text
                ),
                None,
            )
            if position is None:
                break
            positions.append(position)
        else:
            if positions == sorted(positions) and len(set(positions)) == len(positions):
                bindings.append(
                    VerifiedFootnoteNumericBinding(
                        unit_uid=request.unit_uid,
                        exact_source_marker_token=marker,
                        footnote_number=number,
                        source_segment_binding=binding_segments,
                        pre_footnote_numeric_tokens=list(raw_tokens),
                    )
                )
    return bindings
