"""Read-only derivation for append-only production control-plane events."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def derive_event_metrics(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Derive campaign/session timing without inspecting a book or artifacts."""
    rows = [dict(row) for row in events if _time(row.get("timestamp"))]
    rows.sort(key=lambda row: _time(row["timestamp"]))
    durations = {"RUNNING": 0.0, "AUTO_RECOVERING": 0.0, "BLOCKED": 0.0}
    for current, following in zip(rows, rows[1:]):
        state = current.get("operational_state")
        if state in durations:
            durations[state] += max(0.0, (_time(following["timestamp"]) - _time(current["timestamp"])).total_seconds())
    workers: dict[str, datetime] = {}
    uninterrupted: list[float] = []
    worker_count = recovery_count = recovery_passes = 0
    first_worker = last_terminal = None
    completed_units = 0
    for row in rows:
        timestamp = _time(row["timestamp"])
        event_type, worker_id = row.get("event_type"), row.get("worker_id")
        if event_type == "WORKER_STARTED":
            worker_count += 1
            if first_worker is None: first_worker = timestamp
            if isinstance(worker_id, str): workers[worker_id] = timestamp
        elif event_type == "WORKER_EXITED" and isinstance(worker_id, str) and worker_id in workers:
            uninterrupted.append(max(0.0, (timestamp - workers.pop(worker_id)).total_seconds()))
            last_terminal = timestamp
        elif event_type == "AUTO_RECOVERY_START":
            recovery_count += 1
        elif event_type == "AUTO_RECOVERY_PASS":
            recovery_passes += 1
        if event_type == "CHECKPOINT_SEALED":
            completed_units = max(completed_units, int(row.get("completed_count") or 0))
            last_terminal = timestamp
    first = _time(rows[0]["timestamp"]) if rows else None
    elapsed_hours = ((last_terminal - first).total_seconds() / 3600) if first and last_terminal and last_terminal > first else None
    campaign_seconds = sum(durations.values())
    return {
        "event_count": len(rows),
        "units_completed": completed_units,
        "units_per_hour": round(completed_units / elapsed_hours, 3) if elapsed_hours else None,
        "campaign_productive_seconds": round(durations["RUNNING"], 3),
        "campaign_recovery_seconds": round(durations["AUTO_RECOVERING"], 3),
        "campaign_blocked_seconds": round(durations["BLOCKED"], 3),
        "campaign_productive_ratio": round(durations["RUNNING"] / campaign_seconds, 4) if campaign_seconds else None,
        "recovery_count": recovery_count,
        "recovery_success_rate": round(recovery_passes / recovery_count, 4) if recovery_count else None,
        "worker_count": worker_count,
        "mean_uninterrupted_run_seconds": round(sum(uninterrupted) / len(uninterrupted), 3) if uninterrupted else None,
        "worker_startup_overhead_seconds": round((first_worker - first).total_seconds(), 3) if first_worker and first else None,
    }
