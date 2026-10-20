"""Pure post-worker handoff classification."""

from __future__ import annotations

from typing import Any, Mapping


def _checkpoint(handoff: Mapping[str, Any]) -> str | None:
    value = handoff.get("checkpoint")
    if not value and handoff.get("status") == "CONTINUE":
        value = handoff.get("next_checkpoint")
    return value if isinstance(value, str) and value else None


def decide_handoff(
    *, current_worker_id: str, handoff: Mapping[str, Any] | None,
    post_cursor: Mapping[str, Any], facts: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify control metadata after the immutable cursor has been rebuilt."""
    if handoff is None:
        return {"decision": "ACCEPT_CONTINUE", "reason": "HANDOFF_ABSENT", "evidence": {}}
    if handoff.get("schema_version") != 1:
        return {"decision": "BLOCK", "reason": "HANDOFF_SCHEMA_UNSUPPORTED", "evidence": {}}
    worker_id = handoff.get("worker_id")
    if not isinstance(worker_id, str) or not worker_id:
        return {"decision": "BLOCK", "reason": "HANDOFF_WORKER_ID_MISSING", "evidence": {}}
    if worker_id != current_worker_id:
        return {"decision": "STALE_ROTATE", "reason": "WORKER_ID_MISMATCH", "evidence": {}}
    if facts.get("source_sha_unchanged") is False:
        return {"decision": "BLOCK", "reason": "SOURCE_SHA_CHANGED", "evidence": {}}
    status = handoff.get("status")
    if status not in {"CONTINUE", "RECOVERY_REQUIRED", "BLOCKED"}:
        return {"decision": "BLOCK", "reason": "HANDOFF_STATUS_INVALID", "evidence": {"status": status}}
    checkpoint = _checkpoint(handoff)
    if status == "RECOVERY_REQUIRED" and facts.get("recovery_fields_complete") is False:
        return {"decision": "NORMALIZE", "reason": "RECOVERY_FIELDS_DERIVABLE", "evidence": {}}
    if checkpoint is None:
        if status == "CONTINUE" and facts.get("worker_made_progress") and facts.get("current_partial") == "NONE":
            return {"decision": "STALE_ROTATE", "reason": "SUPERSEDED_BY_AUTHORITATIVE_PROGRESS", "evidence": {}}
        return {"decision": "BLOCK", "reason": "HANDOFF_CHECKPOINT_INVALID", "evidence": {}}
    if facts.get("checkpoint_format_valid") is False:
        if status == "CONTINUE" and facts.get("worker_made_progress") and facts.get("current_partial") == "NONE":
            return {"decision": "STALE_ROTATE", "reason": "SUPERSEDED_BY_AUTHORITATIVE_PROGRESS", "evidence": {}}
        return {"decision": "BLOCK", "reason": "HANDOFF_CHECKPOINT_INVALID", "evidence": {}}
    if facts.get("checkpoint_is_sealed"):
        reason = "RECOVERY_CHECKPOINT_ALREADY_SEALED" if status == "RECOVERY_REQUIRED" else "HANDOFF_CHECKPOINT_ALREADY_SEALED"
        return {"decision": "STALE_ROTATE", "reason": reason, "evidence": {"checkpoint": checkpoint}}
    current_batch = post_cursor.get("active_batch")
    current_checkpoint = post_cursor.get("next_checkpoint")
    is_current = handoff.get("active_batch") == current_batch and checkpoint == current_checkpoint
    if status == "RECOVERY_REQUIRED":
        if not facts.get("handoff_written_during_worker", True):
            return {"decision": "BLOCK", "reason": "RECOVERY_HANDOFF_TIMESTAMP_OUT_OF_RANGE", "evidence": {}}
        if is_current and facts.get("precheckpoint_evidence_recovery") and facts.get("current_partial") == "NONE":
            return {"decision": "TRIAGE", "reason": "CURRENT_PRECHECKPOINT_EVIDENCE_RECOVERY", "evidence": {"checkpoint": checkpoint}}
        if is_current and facts.get("current_partial") == "PARTIAL" and facts.get("partial_path_matches"):
            return {"decision": "TRIAGE", "reason": "CURRENT_RECOVERY_REQUIRED", "evidence": {"checkpoint": checkpoint}}
        if is_current and facts.get("current_partial") == "PARTIAL":
            return {"decision": "BLOCK", "reason": "RECOVERY_HANDOFF_PARTIAL_PATH_MISMATCH", "evidence": {"checkpoint": checkpoint}}
        return {"decision": "BLOCK", "reason": "RECOVERY_HANDOFF_POST_STATE_MISMATCH", "evidence": {"checkpoint": checkpoint}}
    if status == "BLOCKED":
        if is_current:
            return {"decision": "BLOCK", "reason": "CURRENT_WORKER_BLOCKED", "evidence": {"checkpoint": checkpoint}}
        return {"decision": "BLOCK", "reason": "HANDOFF_POST_STATE_MISMATCH", "evidence": {"checkpoint": checkpoint}}
    if facts.get("worker_made_progress"):
        return {"decision": "STALE_ROTATE", "reason": "AUTHORITATIVE_PROGRESS_SUPERSEDES_CONTINUE", "evidence": {"checkpoint": checkpoint}}
    if handoff.get("active_batch") not in {facts.get("launch_batch"), current_batch}:
        return {"decision": "BLOCK", "reason": "CONTINUE_HANDOFF_UNRELATED_BATCH", "evidence": {}}
    if facts.get("handoff_checkpoint_is_future") and handoff.get("active_batch") == current_batch:
        return {"decision": "BLOCK", "reason": "CONTINUE_HANDOFF_FUTURE_CHECKPOINT", "evidence": {"checkpoint": checkpoint}}
    return {"decision": "ACCEPT_CONTINUE", "reason": "CURRENT_CONTINUE", "evidence": {"checkpoint": checkpoint}}
