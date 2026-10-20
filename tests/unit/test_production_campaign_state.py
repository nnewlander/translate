from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from scripts.production.rebuild_production_campaign_state import derive_campaign_state, write_state_atomic


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def checkpoint(batch: Path, name: str, ids: list[str]) -> dict[str, object]:
    directory = batch / name
    directory.mkdir(parents=True)
    raw = directory / "translation_raw_response.jsonl"
    raw.write_text("{}\n" * len(ids), encoding="utf-8")
    write_json(directory / "checkpoint_evidence.json", {"logical_work_orders": ids, "d050_evidence_artifacts": []})
    write_json(directory / "translation_final_qa_report.json", {"pass_count": len(ids), "fail_count": 0})
    return {"id": name, "actual_count": len(ids), "final_pass": len(ids), "raw_sha256": sha256(raw.read_bytes()).hexdigest()}


def test_derives_active_state_from_sealed_prefix(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    source_sha = sha256(source.read_bytes()).hexdigest()
    run = tmp_path / "run"
    write_json(run / "full_book_progress_v10.json", {"artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "source_sha256": source_sha, "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_008", "unresolved_deterministic_fail": 0, "human_required": 0, "recovery_queue": []})
    write_json(run / "full_book_batch_009_selection_reconciled_v1.json", {"batch": "FULL_BOOK_BATCH_009", "selected_logical_work_order_ids": ["one", "two", "three", "four"]})
    batch = run / "full_book_batch_009"
    sealed = checkpoint(batch, "checkpoint_01", ["one", "two"])
    write_json(batch / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_009", "target_count": 4, "completed_count": 2, "checkpoint_count": 1, "next_checkpoint": "checkpoint_02", "status": "OPEN_PROGRESS", "checkpoints": [sealed]})

    state = derive_campaign_state(run, source)

    assert state["campaign_status"] == "CONTINUE"
    assert state["completed_count"] == 2
    assert state["next_checkpoint"] == "checkpoint_02"
    assert state["next_logical_work_order_id"] == "three"
    assert state["active_selection"]["path"].endswith("reconciled_v1.json")


def test_rejects_sealed_prefix_that_diverges_from_selection(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    source_sha = sha256(source.read_bytes()).hexdigest()
    run = tmp_path / "run"
    write_json(run / "full_book_progress_v1.json", {"artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "source_sha256": source_sha, "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_000", "unresolved_deterministic_fail": 0, "human_required": 0, "recovery_queue": []})
    write_json(run / "full_book_batch_001_selection.json", {"batch": "FULL_BOOK_BATCH_001", "selected_logical_work_order_ids": ["one", "two"]})
    batch = run / "full_book_batch_001"
    sealed = checkpoint(batch, "checkpoint_01", ["two"])
    write_json(batch / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_001", "target_count": 2, "completed_count": 1, "checkpoint_count": 1, "next_checkpoint": "checkpoint_02", "status": "OPEN_PROGRESS", "checkpoints": [sealed]})

    with pytest.raises(RuntimeError, match="SEALED_CHECKPOINT_SELECTION_PREFIX_MISMATCH"):
        derive_campaign_state(run, source)


def test_reconciled_selection_uses_numeric_version_not_filesystem_or_lexical_order(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    source_sha = sha256(source.read_bytes()).hexdigest()
    run = tmp_path / "run"
    write_json(run / "full_book_progress_v1.json", {"artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "source_sha256": source_sha, "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_000", "unresolved_deterministic_fail": 0, "human_required": 0, "recovery_queue": []})
    # v10 is intentionally written before v9: neither mtime nor lexical order
    # is allowed to choose the authoritative frozen selector.
    write_json(run / "full_book_batch_001_selection_reconciled_v10.json", {"batch": "FULL_BOOK_BATCH_001", "selected_logical_work_order_ids": ["one", "two"]})
    write_json(run / "full_book_batch_001_selection_reconciled_v9.json", {"batch": "FULL_BOOK_BATCH_001", "selected_logical_work_order_ids": ["bad", "selector"]})
    batch = run / "full_book_batch_001"
    sealed = checkpoint(batch, "checkpoint_01", ["one"])
    write_json(batch / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_001", "target_count": 2, "completed_count": 1, "checkpoint_count": 1, "next_checkpoint": "checkpoint_02", "status": "OPEN_PROGRESS", "checkpoints": [sealed]})

    state = derive_campaign_state(run, source)

    assert state["active_selection"]["path"].endswith("reconciled_v10.json")
    assert state["next_logical_work_order_id"] == "two"


def test_derives_batch_boundary_ready_for_one_complete_next_batch(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    source_sha = sha256(source.read_bytes()).hexdigest()
    run = tmp_path / "run"
    write_json(run / "full_book_progress_v1.json", {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "source_sha256": source_sha,
        "status": "FULL_BOOK_PRODUCTION_IN_PROGRESS", "latest_completed_batch": "FULL_BOOK_BATCH_000",
        "unresolved_deterministic_fail": 0, "human_required": 0, "recovery_queue": [],
    })
    write_json(run / "full_book_batch_001_selection.json", {
        "batch": "FULL_BOOK_BATCH_001", "selected_logical_work_order_ids": ["one", "two"],
    })
    batch = run / "full_book_batch_001"
    sealed = checkpoint(batch, "checkpoint_01", ["one", "two"])
    write_json(batch / "batch_manifest.json", {
        "batch": "FULL_BOOK_BATCH_001", "target_count": 2, "completed_count": 2,
        "checkpoint_count": 1, "next_checkpoint": None, "status": "COMPLETE", "checkpoints": [sealed],
    })

    state = derive_campaign_state(run, source)

    assert state["campaign_status"] == "BATCH_BOUNDARY_READY"
    assert state["stop_reason"] == "LATEST_BATCH_COMPLETE"
    assert state["active_batch"] == "FULL_BOOK_BATCH_001"
    assert state["next_checkpoint"] is None


def test_atomic_writer_refuses_existing_temp_file(tmp_path: Path) -> None:
    state = tmp_path / "production_campaign_state.json"
    write_state_atomic(state, {"schema_version": 1})
    assert json.loads(state.read_text(encoding="utf-8"))["schema_version"] == 1
    state.with_suffix(".json.tmp").write_text("unfinished", encoding="utf-8")
    with pytest.raises(RuntimeError, match="CAMPAIGN_STATE_TEMP_ALREADY_EXISTS"):
        write_state_atomic(state, {"schema_version": 2})
