"""Existing Harness QA used as-is.  Qwen routing cannot relax these checks."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import TaggedSyntheticCandidate, TranslationView
from booktrans.harness.tagged_roundtrip import FormatAnchorQA


QaFn = Callable[[TranslationView, CalibrationRequest, TranslationResult, set[str]], dict[str, Any]]


def harness_qa_record(
    view: TranslationView,
    request: CalibrationRequest,
    response: TranslationResult,
    known_unit_ids: set[str],
) -> dict[str, Any]:
    """Same FormatAnchorQA path used by sealed production checkpoints."""
    candidate = TaggedSyntheticCandidate(
        unit_uid=view.unit_uid,
        display_id=view.display_id,
        source_text=view.source_text,
        target_segments=list(response.target_segments),
        target_text="".join(segment.text for segment in response.target_segments),
        generation_kind="calibration",
    )
    result = FormatAnchorQA().validate(
        view,
        candidate,
        known_unit_ids,
        footnote_reference_numbers=request.footnote_reference_numbers,
        verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings,
        explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences,
    )
    return {
        "unit_uid": view.unit_uid,
        "display_id": view.display_id,
        "passed": result.passed,
        "diagnostics": [item.model_dump() for item in result.diagnostics],
    }


def qa_codes(record: Mapping[str, Any]) -> list[str]:
    return [str(item.get("code")) for item in record.get("diagnostics") or [] if item.get("code")]
