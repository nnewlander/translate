"""Small deterministic reviewer-dispatch policy for production hot paths."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping

from booktrans.domain.calibration import CalibrationRequest
from booktrans.providers.router import ROUTE_QWEN_MAX_STRUCTURED, RouteDecision


SAMPLE_DENOMINATOR = 5  # Stable 20% audit of simple clean MT units.
REVIEW_POLICY_V2_FASTPATH = "v2_fastpath"


def is_stable_quality_sample(unit_uid: str) -> bool:
    return int(sha256(unit_uid.encode("utf-8")).hexdigest()[:8], 16) % SAMPLE_DENOMINATOR == 0


def reviewer_dispatch_reason(
    request: CalibrationRequest,
    decision: RouteDecision,
    *,
    semantic_context: Mapping[str, Any] | None,
    repaired: bool,
) -> str:
    """Return a local, resumable reviewer decision; never use randomness."""
    context = semantic_context or {}
    if decision.route == ROUTE_QWEN_MAX_STRUCTURED:
        return "MANDATORY_STRUCTURED"
    if repaired:
        return "MANDATORY_REPAIRED"
    if context.get("continuation_relation") == "BODY_CONTINUATION" or len(context.get("member_work_order_ids") or []) > 1:
        return "MANDATORY_SEMANTIC_GROUP"
    if request.content_role not in {"body_paragraph", "paragraph"}:
        return "MANDATORY_STRUCTURAL"
    if is_stable_quality_sample(request.unit_uid):
        return "SAMPLED_QUALITY_AUDIT"
    return "DIRECT_CORE_PASS"


def reviewer_is_mandatory(reason: str) -> bool:
    return reason.startswith("MANDATORY_")
