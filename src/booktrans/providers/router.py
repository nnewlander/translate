"""Pure Provider Router.  Routing is provenance, not an authoritative production fact."""

from __future__ import annotations

from dataclasses import dataclass

from booktrans.domain.calibration import CalibrationRequest
from booktrans.providers.qwen_max import QWEN_MAX, classify_source_segment_mode
from booktrans.providers.qwen_mt import QWEN_MT_PLUS


ROUTE_QWEN_MT_PLUS = "QWEN_MT_PLUS"
ROUTE_QWEN_MAX_STRUCTURED = "QWEN_MAX_STRUCTURED"
REJECTED_HOLISTIC_PROJECTION = "multi_arm_b_holistic_projection"
REJECTED_ROUTES = frozenset(
    {
        REJECTED_HOLISTIC_PROJECTION,
        "HOLISTIC_PROJECTION",
        "multi_arm_b",
        "ARM_B",
        "QWEN_MT_HOLISTIC_PROJECTION",
    }
)


@dataclass(frozen=True)
class RouteDecision:
    """Non-authoritative routing record written only into provider provenance."""

    route: str
    model: str
    reason: str
    translatable_segment_count: int
    source_segment_count: int
    qwen_mt_projection_supported: bool


def non_protected_translatable_indices(request: CalibrationRequest) -> tuple[int, ...]:
    """TEXT-mode segments the Qwen-MT one-segment projection could actually own."""
    return tuple(
        index
        for index, segment in enumerate(request.source_segments)
        if segment.kind != "protected" and classify_source_segment_mode(segment) == "TEXT"
    )


def qwen_mt_projection_supported(request: CalibrationRequest) -> bool:
    """Mirror ``project_plain_candidate_to_raw`` preconditions; never split/merge/pad."""
    if len(request.source_segments) != 1:
        return False
    segment = request.source_segments[0]
    if segment.kind == "protected":
        return False
    return bool(segment.text.strip())


def route_semantic_unit(request: CalibrationRequest) -> RouteDecision:
    """Select Qwen-MT Plus only for a one-segment projection-safe semantic unit.

    ARM B / holistic projection is rejected and is never a fallback.
    """
    translatable = non_protected_translatable_indices(request)
    mt_ok = qwen_mt_projection_supported(request)
    if len(translatable) == 1 and mt_ok:
        return RouteDecision(
            route=ROUTE_QWEN_MT_PLUS,
            model=QWEN_MT_PLUS,
            reason="EXACTLY_ONE_NON_PROTECTED_TRANSLATABLE_SEGMENT",
            translatable_segment_count=1,
            source_segment_count=len(request.source_segments),
            qwen_mt_projection_supported=True,
        )
    return RouteDecision(
        route=ROUTE_QWEN_MAX_STRUCTURED,
        model=QWEN_MAX,
        reason="STRUCTURED_REQUIRED",
        translatable_segment_count=len(translatable),
        source_segment_count=len(request.source_segments),
        qwen_mt_projection_supported=mt_ok,
    )


def assert_route_not_rejected(decision: RouteDecision) -> None:
    if decision.route in REJECTED_ROUTES:
        raise RuntimeError(f"REJECTED_PROVIDER_ROUTE:{decision.route}")
