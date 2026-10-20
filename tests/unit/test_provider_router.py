from __future__ import annotations

from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment
from booktrans.production_worker.selection import frozen_checkpoint_order, load_json
from booktrans.providers.router import (
    REJECTED_HOLISTIC_PROJECTION,
    REJECTED_ROUTES,
    ROUTE_QWEN_MAX_STRUCTURED,
    ROUTE_QWEN_MT_PLUS,
    route_semantic_unit,
)
from booktrans.production_worker import pipeline as pipeline_mod


def _request(*texts: str, protected: tuple[int, ...] = ()) -> CalibrationRequest:
    segments = [
        CalibrationSourceSegment(
            kind="protected" if index in protected else "anchored",
            text=text,
            anchor_ref=f"anchor-{index}",
            island_ref="island.001",
        )
        for index, text in enumerate(texts)
    ]
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-router",
        display_id="body.p.00001",
        story="body",
        content_role="body_paragraph",
        source_text=" ".join(texts),
        source_segments=segments,
    )


def test_router_simple_one_translatable_segment_selects_qwen_mt() -> None:
    decision = route_semantic_unit(_request("A single software-engineering sentence."))
    assert decision.route == ROUTE_QWEN_MT_PLUS
    assert decision.model == "qwen-mt-plus"
    assert decision.qwen_mt_projection_supported is True


def test_router_multi_segment_selects_qwen_max_structured() -> None:
    decision = route_semantic_unit(_request("First clause.", "Second clause."))
    assert decision.route == ROUTE_QWEN_MAX_STRUCTURED
    assert decision.model == "qwen3.8-flash"


def test_router_protected_or_whitespace_is_structured() -> None:
    assert route_semantic_unit(_request("https://example.test", protected=(0,))).route == ROUTE_QWEN_MAX_STRUCTURED
    assert route_semantic_unit(_request(" ")).route == ROUTE_QWEN_MAX_STRUCTURED
    mixed = route_semantic_unit(_request("Keep this.", "https://example.test", protected=(1,)))
    assert mixed.route == ROUTE_QWEN_MAX_STRUCTURED


def test_arm_b_holistic_projection_is_never_selected() -> None:
    for request in (
        _request("Simple."),
        _request("One.", "Two."),
        _request("Keep.", "https://x.test", protected=(1,)),
    ):
        decision = route_semantic_unit(request)
        assert decision.route not in REJECTED_ROUTES
        assert decision.route != REJECTED_HOLISTIC_PROJECTION
        assert decision.route != "HOLISTIC_PROJECTION"
    source = Path(pipeline_mod.__file__).read_text(encoding="utf-8")
    assert "project_holistic" not in source
    assert "multi_arm_b" not in source
    assert "HOLISTIC_PROJECTION" not in source


def test_frozen_b022_checkpoint_03_order_starts_at_body_p_06939() -> None:
    selection = load_json(Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09\full_book_batch_022_selection.json"))
    order = frozen_checkpoint_order(selection, "checkpoint_03")
    assert len(order) == 25
    assert order[0] == "body.p.06939"
