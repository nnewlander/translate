"""Phase-4 crash-point and invariant tests; all artifacts live under tmp_path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from booktrans.production_control.fault_injection import CRASH_POINTS, FakeCampaignRun, restart_from_disk
from booktrans.production_control.handoff_decision import decide_handoff
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy


def _fixture(tmp_path: Path, *, checkpoint: str = "checkpoint_01") -> FakeCampaignRun:
    return FakeCampaignRun(tmp_path / "isolated-fake-run", checkpoint=checkpoint).create()


def _assert_invariants(run: FakeCampaignRun, before: dict[str, str]) -> None:
    """Restart inspection is read-only and cannot weaken any Phase-1 invariant."""
    after = run.immutable_snapshot()
    assert after == before, "restart inspection modified immutable-like evidence"
    state = json.loads(run.state.read_text(encoding="utf-8"))
    assert state["source_sha256"] == __import__("hashlib").sha256(run.source.read_bytes()).hexdigest()
    assert json.loads(run.selection.read_text(encoding="utf-8"))["batch"] == run.batch
    assert not (run.root / "production_campaign_worker.lock").exists()
    assert all("checkpoint_evidence" not in name or name in before for name in after)


@pytest.mark.parametrize(
    ("point", "prepare", "expected_state", "expected_action"),
    [
        ("AFTER_REQUEST_WRITE", lambda r: r.partial("REQUEST"), "TRIAGING", "PRESERVE_CURRENT_PARTIAL"),
        ("AFTER_RAW_WRITE", lambda r: r.partial("RAW"), "TRIAGING", "PRESERVE_CURRENT_PARTIAL"),
        ("AFTER_RAW_QA", lambda r: r.partial("RAW_QA"), "TRIAGING", "PRESERVE_CURRENT_PARTIAL"),
        ("AFTER_NORMALIZED_QA", lambda r: r.partial("NORMALIZED_QA"), "TRIAGING", "PRESERVE_CURRENT_PARTIAL"),
        ("AFTER_FINAL_QA_BEFORE_SEAL", lambda r: r.partial("FINAL_QA"), "RECONCILING", "COMPLETE_SEAL_FROM_FINAL_QA"),
        ("AFTER_CHECKPOINT_SEAL_BEFORE_STATE_REBUILD", lambda r: r.seal(), "RECONCILING", "REBUILD_STATE_FROM_SEALED_CHECKPOINT"),
        ("AFTER_WORKER_EXIT_BEFORE_HANDOFF_CONSUME", lambda r: r.seal(), "RECONCILING", "REBUILD_STATE_FROM_SEALED_CHECKPOINT"),
        ("DURING_HANDOFF_NORMALIZATION", lambda r: r.partial("RAW"), "TRIAGING", "PRESERVE_CURRENT_PARTIAL"),
        ("DURING_AUTO_RECOVERY_ATTEMPT_1", lambda r: (r.partial("RAW"), _pending_recovery(r)), "AUTO_RECOVERING", "RESUME_BOUNDED_RECOVERY"),
        ("AFTER_RECOVERY_PASS_BEFORE_STATE_REBUILD", lambda r: r.seal(), "RECONCILING", "REBUILD_STATE_FROM_SEALED_CHECKPOINT"),
        ("DURING_BATCH_CLOSURE_AFTER_CP08_SEAL", lambda r: r.terminal_seal(), "BLOCKED", "SUCCESSOR_SELECTION_MISSING"),
        ("AFTER_SUCCESSOR_SELECTION_BEFORE_MANIFEST", lambda r: (r.terminal_seal(), r.successor_selection()), "RECONCILING", "RECOVER_SUCCESSOR_MANIFEST"),
        ("AFTER_SUCCESSOR_MANIFEST_BEFORE_PROGRESS", lambda r: (r.terminal_seal(), r.successor_manifest()), "RECONCILING", "WRITE_SUCCESSOR_PROGRESS"),
        ("AFTER_PROGRESS_BEFORE_STATE_REBUILD", lambda r: r.seal(), "RECONCILING", "REBUILD_STATE_FROM_SEALED_CHECKPOINT"),
    ],
    ids=CRASH_POINTS,
)
def test_restart_from_each_named_crash_point_uses_disk_facts_only(point, prepare, expected_state, expected_action, tmp_path: Path) -> None:
    run = _fixture(tmp_path, checkpoint="checkpoint_08" if "BATCH_CLOSURE" in point or "SUCCESSOR" in point else "checkpoint_01")
    prepare(run)
    before = run.immutable_snapshot()
    first = restart_from_disk(run.root)
    second = restart_from_disk(run.root)
    assert first == second, f"{point}: restart must not depend on process memory"
    assert first == {"operational_state": expected_state, "action": expected_action}
    _assert_invariants(run, before)


def _pending_recovery(run: FakeCampaignRun, attempt: int = 1) -> None:
    (run.root / "recovery_attempt.json").write_text(json.dumps({"policy_id": "NUMERIC_REPRESENTATION", "attempt": attempt, "attempt_budget": 2}), encoding="utf-8")


def test_recovery_faults_are_closed_world_and_bounded(tmp_path: Path) -> None:
    base = {"source_sha_valid": True, "raw_required": True, "raw_sha_valid": True, "sparse_local": True}
    numeric = decide_recovery_policy({**base, "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED", "qa_codes": ["NUMBER_PRESERVATION"], "repair_contract": {"ids": ["one"]}})
    doi = decide_recovery_policy({**base, "failure_classification": "DOI_PRESERVATION", "qa_codes": ["DOI_PRESERVATION"], "repair_contract": {"doi": "10.1/x", "ids": ["one"]}})
    d050 = decide_recovery_policy({"failure_classification": "D050_PREFLIGHT_REQUIRED", "source_sha_valid": True, "exact_d050_binding": True, "repair_contract": {"logical_work_order_id": "one", "unit_uid": "u"}})
    assert [row["policy_id"] for row in (numeric, doi, d050)] == ["NUMERIC_REPRESENTATION", "DOI_PRESERVATION", "D050_EXACT_EVIDENCE"]
    assert all(row["eligible"] and row["attempt_budget"] == 2 for row in (numeric, doi, d050))
    # A crash during attempt 1 resumes bounded recovery; after a validated
    # recovery seal/rebuild, the next restart becomes a normal fresh-worker run.
    run = _fixture(tmp_path)
    run.partial("RAW"); _pending_recovery(run, attempt=1)
    assert restart_from_disk(run.root)["operational_state"] == "AUTO_RECOVERING"
    (run.root / "recovery_attempt.json").unlink()
    run.seal(); run.rebuild_state()
    assert restart_from_disk(run.root) == {"operational_state": "RUNNING", "action": "FRESH_WORKER_ALLOWED"}
    exhausted = _fixture(tmp_path / "exhausted")
    exhausted.partial("RAW"); _pending_recovery(exhausted, attempt=2)
    assert restart_from_disk(exhausted.root) == {"operational_state": "BLOCKED", "action": "AUTO_RECOVERY_EXHAUSTED"}
    assert numeric["handler_kind"] == "QWEN_NARROW_REPAIR" and numeric["attempt_budget"] == 2
    unknown = decide_recovery_policy({**base, "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED", "qa_codes": ["UNSEEN_CODE"], "repair_contract": {"ids": ["one"]}})
    dense = decide_recovery_policy({**base, "failure_classification": "FINAL_DETERMINISTIC_QA_FAILED", "sparse_local": False, "qa_codes": ["NUMBER_PRESERVATION"], "repair_contract": {"ids": ["one", "two"]}})
    raw_drift = decide_recovery_policy({**base, "raw_sha_valid": False, "failure_classification": "DOI_PRESERVATION", "qa_codes": ["DOI_PRESERVATION"], "repair_contract": {"doi": "10.1/x"}})
    source_drift = decide_recovery_policy({**base, "source_sha_valid": False, "failure_classification": "DOI_PRESERVATION", "qa_codes": ["DOI_PRESERVATION"], "repair_contract": {"doi": "10.1/x"}})
    assert all(not row["eligible"] and row["escalation_code"] == "BLOCK" for row in (unknown, dense, raw_drift, source_drift))


def test_control_metadata_reconciles_only_when_unique(tmp_path: Path) -> None:
    run = _fixture(tmp_path)
    state = json.loads(run.state.read_text(encoding="utf-8")); state["active_manifest"]["sha256"] = "stale"; run.state.write_text(json.dumps(state), encoding="utf-8")
    assert restart_from_disk(run.root) == {"operational_state": "RECONCILING", "action": "STALE_STATE_MANIFEST_BINDING"}
    run = _fixture(tmp_path / "competing")
    run.successor_manifest()
    (run.root / "full_book_batch_002" / "batch_manifest.v2.json").write_text("{}", encoding="utf-8")
    assert restart_from_disk(run.root)["operational_state"] == "BLOCKED"
    derivable = decide_handoff(current_worker_id="w", handoff={"schema_version": 1, "worker_id": "w", "status": "RECOVERY_REQUIRED"}, post_cursor={"active_batch": run.batch, "next_checkpoint": run.checkpoint}, facts={"source_sha_unchanged": True, "recovery_fields_complete": False})
    missing_identity = decide_handoff(current_worker_id="w", handoff={"schema_version": 1, "status": "RECOVERY_REQUIRED"}, post_cursor={"active_batch": run.batch, "next_checkpoint": run.checkpoint}, facts={"source_sha_unchanged": True, "recovery_fields_complete": False})
    assert derivable["decision"] == "NORMALIZE"
    assert missing_identity["decision"] == "BLOCK"
