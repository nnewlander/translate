"""Isolated, disk-backed crash fixtures for control-plane fault injection.

This module is deliberately test-only.  It creates minimal immutable-like
facts under a caller-provided temporary directory and makes a restart decision
from those files alone.  It neither imports the Translation Harness nor knows
about a real production run.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


CRASH_POINTS = (
    "AFTER_REQUEST_WRITE", "AFTER_RAW_WRITE", "AFTER_RAW_QA",
    "AFTER_NORMALIZED_QA", "AFTER_FINAL_QA_BEFORE_SEAL",
    "AFTER_CHECKPOINT_SEAL_BEFORE_STATE_REBUILD",
    "AFTER_WORKER_EXIT_BEFORE_HANDOFF_CONSUME",
    "DURING_HANDOFF_NORMALIZATION", "DURING_AUTO_RECOVERY_ATTEMPT_1",
    "AFTER_RECOVERY_PASS_BEFORE_STATE_REBUILD",
    "DURING_BATCH_CLOSURE_AFTER_CP08_SEAL",
    "AFTER_SUCCESSOR_SELECTION_BEFORE_MANIFEST",
    "AFTER_SUCCESSOR_MANIFEST_BEFORE_PROGRESS",
    "AFTER_PROGRESS_BEFORE_STATE_REBUILD",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class FakeCampaignRun:
    """A minimal on-disk campaign with enough facts for restart reconciliation."""

    root: Path
    batch: str = "FULL_BOOK_BATCH_001"
    checkpoint: str = "checkpoint_01"

    @property
    def source(self) -> Path:
        return self.root / "source.docx"

    @property
    def selection(self) -> Path:
        return self.root / "full_book_batch_001_selection.json"

    @property
    def batch_dir(self) -> Path:
        return self.root / "full_book_batch_001"

    @property
    def manifest(self) -> Path:
        return self.batch_dir / "batch_manifest.json"

    @property
    def state(self) -> Path:
        return self.root / "production_campaign_state.json"

    @property
    def checkpoint_dir(self) -> Path:
        return self.batch_dir / self.checkpoint

    def create(self) -> "FakeCampaignRun":
        self.root.mkdir(parents=True, exist_ok=True)
        self.source.write_bytes(b"phase-4-fake-source-v1")
        source_sha = _sha(self.source)
        _write(self.selection, {"artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": self.batch,
                                "source_sha256": source_sha,
                                "selected_logical_work_order_ids": ["one"]})
        _write(self.manifest, {"artifact_type": "FULL_BOOK_BATCH_MANIFEST", "batch": self.batch,
                               "source_sha256": source_sha, "selection_path": str(self.selection),
                               "selection_sha256": _sha(self.selection), "target_count": 200,
                               "completed_count": 0, "next_checkpoint": self.checkpoint,
                               "checkpoints": []})
        self.rebuild_state()
        return self

    def rebuild_state(self) -> None:
        manifest = _read(self.manifest)
        _write(self.state, {"artifact_type": "PRODUCTION_CAMPAIGN_STATE", "source_sha256": _sha(self.source),
                            "active_batch": self.batch, "active_selection": {"path": str(self.selection), "sha256": _sha(self.selection)},
                            "active_manifest": {"path": str(self.manifest), "sha256": _sha(self.manifest)},
                            "completed_count": manifest["completed_count"], "target_count": manifest["target_count"],
                            "next_checkpoint": manifest["next_checkpoint"], "next_logical_work_order_id": "one",
                            "campaign_status": "CONTINUE", "recovery_queue": []})

    def partial(self, stage: str) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        _write(self.checkpoint_dir / "translation_request.jsonl", {"logical_work_orders": ["one"]})
        if stage in {"RAW", "RAW_QA", "NORMALIZED_QA", "FINAL_QA"}:
            _write(self.checkpoint_dir / "translation_raw_response.jsonl", {"target": "raw"})
        if stage in {"RAW_QA", "NORMALIZED_QA", "FINAL_QA"}:
            _write(self.checkpoint_dir / "translation_raw_qa_report.json", {"pass_count": 1, "fail_count": 0})
        if stage in {"NORMALIZED_QA", "FINAL_QA"}:
            _write(self.checkpoint_dir / "translation_normalized_response.jsonl", {"target": "normalized"})
            _write(self.checkpoint_dir / "translation_normalized_qa_report.json", {"pass_count": 1, "fail_count": 0})
        if stage == "FINAL_QA":
            _write(self.checkpoint_dir / "translation_final_qa_report.json", {"pass_count": 1, "fail_count": 0})

    def seal(self) -> None:
        self.partial("FINAL_QA")
        raw = self.checkpoint_dir / "translation_raw_response.jsonl"
        _write(self.checkpoint_dir / "checkpoint_evidence.json", {"logical_work_orders": ["one"], "raw_sha256": _sha(raw), "final_qa_pass": True})
        manifest = _read(self.manifest)
        manifest["completed_count"] = 25
        manifest["next_checkpoint"] = "checkpoint_02"
        manifest["checkpoints"] = [{"id": self.checkpoint, "actual_count": 25, "raw_sha256": _sha(raw), "final_pass": 25}]
        _write(self.manifest, manifest)

    def terminal_seal(self) -> None:
        """Create a valid terminal-like sealed fact without advancing state."""
        self.seal()
        manifest = _read(self.manifest)
        manifest["completed_count"] = 200
        manifest["target_count"] = 200
        manifest["next_checkpoint"] = None
        _write(self.manifest, manifest)

    def successor_selection(self) -> Path:
        result = self.root / "full_book_batch_002_selection.json"
        _write(result, {"artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": "FULL_BOOK_BATCH_002",
                        "source_sha256": _sha(self.source), "selected_logical_work_order_ids": ["two"]})
        return result

    def successor_manifest(self) -> Path:
        selection = self.successor_selection()
        result = self.root / "full_book_batch_002" / "batch_manifest.json"
        _write(result, {"artifact_type": "FULL_BOOK_BATCH_MANIFEST", "batch": "FULL_BOOK_BATCH_002",
                        "source_sha256": _sha(self.source), "selection_path": str(selection),
                        "selection_sha256": _sha(selection), "target_count": 200, "completed_count": 0,
                        "next_checkpoint": "checkpoint_01", "checkpoints": []})
        return result

    def immutable_snapshot(self) -> dict[str, str]:
        return {str(path.relative_to(self.root)): _sha(path) for path in (self.source, self.selection, self.manifest,
                *self.checkpoint_dir.glob("translation_*.json*"), *self.checkpoint_dir.glob("checkpoint_evidence.json")) if path.exists()}


def restart_from_disk(root: Path) -> dict[str, Any]:
    """Classify a crash restart entirely from fixture disk facts.

    It models the control-plane restart boundary: it does not repair or mutate
    any fact.  A caller can therefore assert that repeated restart inspection
    preserves immutable-like files byte-for-byte.
    """
    run = FakeCampaignRun(root)
    state, manifest, selection = _read(run.state), _read(run.manifest), _read(run.selection)
    if _sha(run.source) != state["source_sha256"] or _sha(run.source) != selection["source_sha256"] or _sha(run.source) != manifest["source_sha256"]:
        return {"operational_state": "BLOCKED", "action": "SOURCE_SHA_MISMATCH"}
    if _sha(run.selection) != manifest["selection_sha256"]:
        return {"operational_state": "BLOCKED", "action": "SELECTION_BINDING_MISMATCH"}
    if len(list(root.glob("full_book_batch_002*/batch_manifest*.json"))) > 1 or len(list(root.glob("full_book_batch_002*_selection*.json"))) > 1:
        return {"operational_state": "BLOCKED", "action": "COMPETING_SUCCESSOR_AUTHORITY"}
    current_checkpoint = run.batch_dir / str(state.get("next_checkpoint") or "")
    if not current_checkpoint.exists() and state.get("active_manifest", {}).get("sha256") == _sha(run.manifest) and state.get("completed_count") == manifest.get("completed_count"):
        return {"operational_state": "RUNNING", "action": "FRESH_WORKER_ALLOWED"}
    checkpoint_candidates = sorted(path for path in run.batch_dir.glob("checkpoint_*") if path.is_dir())
    checkpoint = current_checkpoint if current_checkpoint.exists() else (checkpoint_candidates[-1] if checkpoint_candidates else run.checkpoint_dir)
    raw, evidence, final_qa = (checkpoint / "translation_raw_response.jsonl", checkpoint / "checkpoint_evidence.json", checkpoint / "translation_final_qa_report.json")
    pending_recovery = root / "recovery_attempt.json"
    if pending_recovery.exists():
        pending = _read(pending_recovery)
        if pending.get("policy_id") not in {"NUMERIC_REPRESENTATION", "DOI_PRESERVATION", "D050_EXACT_EVIDENCE"} or not raw.exists():
            return {"operational_state": "BLOCKED", "action": "RECOVERY_ATTEMPT_UNPROVEN"}
        if pending.get("attempt", 0) >= pending.get("attempt_budget", 0):
            return {"operational_state": "BLOCKED", "action": "AUTO_RECOVERY_EXHAUSTED"}
        return {"operational_state": "AUTO_RECOVERING", "action": "RESUME_BOUNDED_RECOVERY"}
    if evidence.exists():
        recorded = _read(evidence).get("raw_sha256")
        if not raw.exists() or recorded != _sha(raw):
            return {"operational_state": "BLOCKED", "action": "IMMUTABLE_RAW_SHA_MISMATCH"}
        if manifest["completed_count"] == manifest["target_count"]:
            successor_selection = root / "full_book_batch_002_selection.json"
            successor_manifest = root / "full_book_batch_002" / "batch_manifest.json"
            if not successor_selection.exists():
                return {"operational_state": "BLOCKED", "action": "SUCCESSOR_SELECTION_MISSING"}
            if not successor_manifest.exists():
                return {"operational_state": "RECONCILING", "action": "RECOVER_SUCCESSOR_MANIFEST"}
            successor = _read(successor_manifest)
            if successor.get("source_sha256") != _sha(run.source) or successor.get("selection_sha256") != _sha(successor_selection):
                return {"operational_state": "BLOCKED", "action": "SUCCESSOR_MANIFEST_BINDING_MISMATCH"}
            return {"operational_state": "RECONCILING", "action": "WRITE_SUCCESSOR_PROGRESS"}
        if state["next_checkpoint"] == checkpoint.name:
            return {"operational_state": "RECONCILING", "action": "REBUILD_STATE_FROM_SEALED_CHECKPOINT"}
    if state.get("active_manifest", {}).get("sha256") != _sha(run.manifest):
        return {"operational_state": "RECONCILING", "action": "STALE_STATE_MANIFEST_BINDING"}
    if final_qa.exists() and _read(final_qa).get("fail_count") == 0 and not evidence.exists():
        return {"operational_state": "RECONCILING", "action": "COMPLETE_SEAL_FROM_FINAL_QA"}
    if raw.exists() or (checkpoint / "translation_request.jsonl").exists():
        return {"operational_state": "TRIAGING", "action": "PRESERVE_CURRENT_PARTIAL"}
    return {"operational_state": "RUNNING", "action": "FRESH_WORKER_ALLOWED"}
