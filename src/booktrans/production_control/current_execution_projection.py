"""Pure orchestration projection for the current production execution step.

This module owns no evidence and computes no candidate, QA, disposition, or
recovery policy.  It only combines already-authoritative facts supplied by the
campaign adapters so the supervisor has one current-state decision point.
"""

from __future__ import annotations

from typing import Any, Mapping


def project_current_execution(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Return the next existing orchestration action from read-only facts."""
    cursor = str(facts.get("cursor_decision") or "")
    partial = str(facts.get("partial_kind") or "NONE")
    campaign = str(facts.get("campaign_status") or "")
    ready = facts.get("batch_ready") is True
    terminal = facts.get("terminal_checkpoint") is True
    recovery = facts.get("canonical_recovery") is True
    semantic_rescue = facts.get("semantic_rescue") is True
    reviewer_resume = facts.get("reviewer_resume") is True

    if campaign != "CONTINUE":
        return {"action": "BLOCK_CAMPAIGN_STATE", "reason": campaign}
    if cursor == "INTEGRITY_FAILURE":
        return {"action": "BLOCK_CURSOR", "reason": cursor}
    if cursor == "SEALED_STATE_DRIFT":
        return {
            "action": "TERMINAL_CLOSURE" if terminal else "BLOCK_CURSOR",
            "reason": cursor,
        }
    if not ready and not any((reviewer_resume, semantic_rescue, recovery)):
        return {"action": "BLOCK_BATCH_READY", "reason": "BATCH_READY_FAILED"}
    if cursor == "CURRENT_PARTIAL" and not (ready or recovery):
        return {"action": "BLOCK_CURSOR", "reason": cursor}
    if reviewer_resume:
        return {"action": "RUN_REVIEWER_RESUME", "reason": "CURRENT_PARTIAL"}
    if semantic_rescue:
        return {"action": "RUN_SEMANTIC_RESCUE", "reason": "CURRENT_PARTIAL"}
    if recovery:
        return {"action": "RUN_CANONICAL_RECOVERY", "reason": "CURRENT_PARTIAL"}
    if partial == "PARTIAL" and ready:
        return {"action": "RUN_CURRENT_PARTIAL", "reason": "CURRENT_PARTIAL"}
    return {"action": "RUN_WORKER", "reason": cursor or "NO_PROGRESS"}


def project_handoff_consumption(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Classify consumption of an already validated handoff without policy work."""
    kind = str(facts.get("handoff_kind") or "MISSING")
    status = str(facts.get("handoff_status") or "")
    disposition = str(facts.get("production_disposition") or "")
    if kind == "STALE":
        return {"action": "ROTATE_STALE_HANDOFF", "reason": str(facts.get("handoff_reason") or "STALE")}
    if kind == "CURRENT" and status == "RECOVERY_REQUIRED" and disposition == "RECOVERY_REQUIRED":
        return {"action": "CONSUME_CANONICAL_RECOVERY", "reason": disposition}
    if kind == "CURRENT" and status == "BLOCKED" and disposition == "HARD_STOP":
        return {"action": "CONSUME_HARD_STOP", "reason": disposition}
    if kind == "CURRENT" and status == "BLOCKED":
        return {"action": "REPROJECT_CURRENT_HEAD", "reason": "LOCAL_TERMINAL"}
    if kind == "CURRENT" and status == "RECOVERY_REQUIRED":
        return {"action": "TRIAGE_EXISTING_RECOVERY", "reason": "RECOVERY_REQUIRED"}
    return {"action": "CONTINUE", "reason": kind}
