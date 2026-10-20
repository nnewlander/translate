"""Atomic worker handoff matching the Supervisor schema-v1 helper."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from booktrans.production_worker.artifacts import assert_record_safe


def write_worker_handoff(
    path: Path,
    *,
    status: str,
    worker_id: str,
    active_batch: str,
    checkpoint: str | None = None,
    next_checkpoint: str | None = None,
    next_logical_work_order_id: str | None = None,
    completed_checkpoint: str | None = None,
    partial_checkpoint_path: str | None = None,
    stage_reached: str | None = None,
    failure_classification: str | None = None,
    failing_work_order_ids: Sequence[str] = (),
    relevant_qa_artifact_paths: Sequence[str] = (),
    raw_exists: bool | None = None,
    raw_sha256: str | None = None,
    production_disposition: str | None = None,
    disposition_reason: str | None = None,
    disposition_evidence_references: Sequence[str] = (),
    recovery_family: str | None = None,
    recovery_fingerprint: dict[str, Any] | None = None,
) -> None:
    if status not in {"CONTINUE", "RECOVERY_REQUIRED", "BLOCKED"}:
        raise ValueError(f"HANDOFF_STATUS_INVALID:{status}")
    record: dict[str, Any] = {
        "schema_version": 1,
        "worker_id": worker_id,
        "status": status,
        "active_batch": active_batch,
        "checkpoint": checkpoint,
        "next_checkpoint": next_checkpoint,
        "next_logical_work_order_id": next_logical_work_order_id,
        "completed_checkpoint": completed_checkpoint,
        "partial_checkpoint_path": partial_checkpoint_path,
        "stage_reached": stage_reached,
        "failure_classification": failure_classification,
        "failing_work_order_ids": list(failing_work_order_ids),
        "relevant_qa_artifact_paths": list(relevant_qa_artifact_paths),
        "raw_exists": raw_exists,
        "raw_sha256": raw_sha256,
        "evidence_requirement": None,
        # Operational handoffs are not authority.  These fields carry the
        # result of the one current-head boundary so the Supervisor never has
        # to promote a local terminal label into a production stop.
        "production_disposition": production_disposition,
        "disposition_reason": disposition_reason,
        "disposition_evidence_references": list(disposition_evidence_references),
        "recovery_family": recovery_family,
        # A diagnostic-only, current-head-bound fingerprint.  The supervisor
        # uses it to detect a recovery loop with no authoritative transition;
        # it never supplies semantic authority.
        "recovery_fingerprint": recovery_fingerprint,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    assert_record_safe(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".production_campaign_worker_handoff.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    except Exception:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
        raise
