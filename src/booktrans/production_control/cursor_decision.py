"""Pure cursor classification for the production control plane."""

from __future__ import annotations

from typing import Any, Mapping


_PROGRESS_FIELDS = (
    "active_batch", "completed_count", "next_checkpoint",
    "next_logical_work_order_id", "completed_checkpoints",
)


def decide_cursor(
    *, before: Mapping[str, Any], after: Mapping[str, Any], evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a serializable cursor decision without reading or writing files.

    ``evidence`` is an already-validated summary supplied by the orchestrator:
    ``integrity_failure`` and ``current_partial`` are facts, not paths to scan.
    """
    if evidence.get("integrity_failure"):
        return {"decision": "INTEGRITY_FAILURE", "reason": str(evidence.get("integrity_failure")), "evidence": {"integrity_failure": True}}
    if evidence.get("source_sha_unchanged") is False:
        return {"decision": "INTEGRITY_FAILURE", "reason": "SOURCE_SHA_CHANGED", "evidence": {"source_sha_unchanged": False}}
    partial = evidence.get("current_partial", "NONE")
    if partial not in {"NONE", "PARTIAL", "SEALED"}:
        return {"decision": "INTEGRITY_FAILURE", "reason": "PARTIAL_FACT_INVALID", "evidence": {"current_partial": partial}}
    if after.get("completed_count") == after.get("target_count") and after.get("target_count") is not None:
        return {"decision": "BATCH_COMPLETE", "reason": "TARGET_COUNT_REACHED", "evidence": {"completed_count": after.get("completed_count")}}
    changed = [field for field in _PROGRESS_FIELDS if before.get(field) != after.get(field)]
    before_progress = before.get("latest_authoritative_progress") or {}
    after_progress = after.get("latest_authoritative_progress") or {}
    if before_progress != after_progress:
        changed.append("latest_authoritative_progress")
    if changed:
        return {"decision": "PROGRESS", "reason": "AUTHORITATIVE_CURSOR_ADVANCED", "evidence": {"changed_fields": changed}}
    # A newly advanced cursor can legitimately point to the next partial
    # checkpoint.  The handoff adapter then owns triage; cursor classification
    # must preserve the legacy supervisor's progress-first semantics.
    if partial == "SEALED":
        return {"decision": "SEALED_STATE_DRIFT", "reason": "NEXT_CHECKPOINT_ALREADY_SEALED", "evidence": {"current_partial": partial}}
    if partial == "PARTIAL":
        return {"decision": "CURRENT_PARTIAL", "reason": "CURRENT_CHECKPOINT_UNSEALED", "evidence": {"current_partial": partial}}
    return {"decision": "NO_PROGRESS", "reason": "AUTHORITATIVE_CURSOR_UNCHANGED", "evidence": {"changed_fields": []}}
