"""Derived event telemetry is append-tolerant and cannot become production truth."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from booktrans.production_control.event_telemetry import derive_event_metrics


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "production" / "derive_production_campaign_event_metrics.py"


def test_event_metrics_are_read_only_and_tolerate_interrupted_append(tmp_path: Path) -> None:
    rows = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "event_type": "SUPERVISOR_STARTED", "operational_state": "RUNNING"},
        {"timestamp": "2026-01-01T00:00:02+00:00", "event_type": "WORKER_STARTED", "operational_state": "RUNNING", "worker_id": "w"},
        {"timestamp": "2026-01-01T00:01:02+00:00", "event_type": "CHECKPOINT_SEALED", "operational_state": "RUNNING", "worker_id": "w", "completed_count": 25},
        {"timestamp": "2026-01-01T00:01:04+00:00", "event_type": "AUTO_RECOVERY_START", "operational_state": "AUTO_RECOVERING", "policy_id": "NUMERIC_REPRESENTATION"},
        {"timestamp": "2026-01-01T00:01:09+00:00", "event_type": "AUTO_RECOVERY_PASS", "operational_state": "AUTO_RECOVERING", "policy_id": "NUMERIC_REPRESENTATION"},
        {"timestamp": "2026-01-01T00:01:12+00:00", "event_type": "WORKER_EXITED", "operational_state": "RUNNING", "worker_id": "w"},
    ]
    events = tmp_path / "production_campaign_events.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in rows) + '{"partial":', encoding="utf-8")
    before = events.read_bytes()
    metrics = derive_event_metrics(rows)
    assert metrics["event_count"] == 6
    assert metrics["units_completed"] == 25 and metrics["units_per_hour"] is not None
    assert metrics["recovery_success_rate"] == 1.0
    assert metrics["campaign_productive_seconds"] > 0 and metrics["campaign_recovery_seconds"] > 0
    result = subprocess.run([sys.executable, str(SCRIPT), "--events", str(events)], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["event_count"] == 6
    assert events.read_bytes() == before, "derivation must not append or rewrite telemetry"
