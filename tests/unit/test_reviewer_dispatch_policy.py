from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment
from booktrans.providers.router import route_semantic_unit
from booktrans.production_worker.reviewer_dispatch_policy import is_stable_quality_sample, reviewer_dispatch_reason


def _request(*, unit_uid: str = "a" * 64) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="b" * 64, unit_uid=unit_uid, display_id="body.p.00001", story="body",
        content_role="body_paragraph", source_text="One sentence.",
        source_segments=[CalibrationSourceSegment(text="One sentence.", kind="anchored", anchor_ref="anchor", island_ref="island.001")],
    )


def test_simple_clean_mt_is_direct_or_stably_sampled() -> None:
    request = _request()
    reason = reviewer_dispatch_reason(request, route_semantic_unit(request), semantic_context=None, repaired=False)
    assert reason in {"DIRECT_CORE_PASS", "SAMPLED_QUALITY_AUDIT"}
    assert is_stable_quality_sample(request.unit_uid) == is_stable_quality_sample(request.unit_uid)


def test_structured_continuation_and_repair_remain_mandatory() -> None:
    request = _request()
    decision = route_semantic_unit(request)
    assert reviewer_dispatch_reason(request, decision, semantic_context={"continuation_relation": "BODY_CONTINUATION"}, repaired=False) == "MANDATORY_SEMANTIC_GROUP"
    assert reviewer_dispatch_reason(request, decision, semantic_context=None, repaired=True) == "MANDATORY_REPAIRED"
