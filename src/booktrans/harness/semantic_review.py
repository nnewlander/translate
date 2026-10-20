"""Minimal artifact invariants for a single semantic-review pass."""

from booktrans.domain.calibration import TranslationResult


def partition_deterministic_review_dispatch(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep deterministic failures outside the one semantic-review dispatch."""
    dispatched = [record for record in records if record.get("passed") is True]
    exclusions = [
        {
            "unit_uid": record["unit_uid"],
            "display_id": record["display_id"],
            "status": "NOT_REVIEWED_DETERMINISTIC_FAIL",
            "reason": "normalized_deterministic_qa_failed",
        }
        for record in records
        if record.get("passed") is not True
    ]
    return dispatched, exclusions


def semantic_review_summary(records: list[dict], dispatch_exclusions: list[dict]) -> dict:
    """Count only reviewer records actually dispatched after deterministic QA."""
    return {
        "accept_count": sum(record.get("status") == "ACCEPT" for record in records),
        "material_repair_count": sum(record.get("status") == "REPAIR_REQUIRED" for record in records),
        "human_required_count": sum(record.get("status") == "HUMAN_REQUIRED" for record in records),
        "reviewer_contract_error_count": sum(record.get("status") == "REVIEWER_CONTRACT_ERROR" for record in records),
        "not_reviewed_deterministic_fail_count": len(dispatch_exclusions),
        "records": records,
        "dispatch_exclusions": dispatch_exclusions,
    }


def has_material_target_change(original: TranslationResult, repaired: TranslationResult) -> bool:
    """Compare the complete structured target contract, not joined text alone."""
    return [
        (segment.text, segment.kind, segment.anchor_ref, segment.island_ref)
        for segment in original.target_segments
    ] != [
        (segment.text, segment.kind, segment.anchor_ref, segment.island_ref)
        for segment in repaired.target_segments
    ]


def review_status_for_repair(original: TranslationResult, repaired: TranslationResult) -> str:
    """Prevent an unchanged repair from inflating repair metrics."""
    return "REPAIR_REQUIRED" if has_material_target_change(original, repaired) else "REVIEWER_CONTRACT_ERROR"
