from __future__ import annotations

import pytest

from booktrans.domain.calibration import (
    CalibrationAnchor,
    CalibrationRequest,
    CalibrationSourceSegment,
    TranslationResult,
)
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.production_worker.production_checkpoint import _repair_clause_coverage


def _request(*, source: tuple[str, ...], bold: bool = True, continuation: bool = False) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="toc-label-unit",
        display_id="body.p.00001",
        story="body",
        content_role="body_paragraph",
        source_text=" ".join(source),
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text=text, anchor_ref=f"a{index}", island_ref="island.001")
            for index, text in enumerate(source)
        ],
        available_anchors=[
            CalibrationAnchor(anchor_id=f"a{index}", semantics=["bold"] if bold else [])
            for index in range(len(source))
        ],
        continuation_hint=continuation,
    )


def _candidate(request: CalibrationRequest, texts: tuple[str, ...]) -> TranslationResult:
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment, text in zip(request.source_segments, texts, strict=True)
        ],
    )


@pytest.mark.parametrize(
    ("source", "base", "repaired"),
    [
        (("Part I.    ", "Thesis"), ("第一部分。", "论点"), ("第一部分", "论点")),
        (("17.  ", "Code Search"), ("17。", "代码搜索"), ("17", "代码搜索")),
    ],
)
def test_toc_ordinal_label_does_not_create_a_source_sentence_clause(source, base, repaired) -> None:
    request = _request(source=source)
    evidence = _repair_clause_coverage(request, _candidate(request, base), _candidate(request, repaired))
    assert evidence["rows"][0]["source_clause_count"] == 0
    assert evidence["rows"][0]["candidate_clause_count"] == 0
    assert evidence["passed"] is True


@pytest.mark.parametrize("source", ["This is a sentence.", "What is this?", "Important!"])
def test_prose_and_question_punctuation_keep_existing_clause_counts(source: str) -> None:
    request = _request(source=(source, "Title"))
    evidence = _repair_clause_coverage(request, _candidate(request, ("这是一句。", "标题")), _candidate(request, ("标题", "标题")))
    assert evidence["rows"][0]["source_clause_count"] == 1
    assert evidence["rows"][0]["candidate_clause_count"] == 0
    assert evidence["passed"] is False


def test_text_only_ordinal_without_structural_evidence_keeps_existing_clause_count() -> None:
    request = _request(source=("1.", "Title"), bold=False)
    evidence = _repair_clause_coverage(request, _candidate(request, ("1。", "标题")), _candidate(request, ("1", "标题")))
    assert evidence["rows"][0]["source_clause_count"] == 1
    assert evidence["passed"] is False
