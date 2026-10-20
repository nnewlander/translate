from __future__ import annotations

import json
from hashlib import sha256
import importlib.util
from pathlib import Path

from booktrans.harness.release_membership import derive_release_membership
from booktrans.production_worker.selection import (
    CHECKPOINT_SIZE,
    checkpoint_count_for_target,
    frozen_checkpoint_order,
    validate_frozen_checkpoint_members,
)


_RELEASE_BOUNDARY = Path(__file__).parents[2] / "scripts" / "production" / "reconcile_release_boundary.py"
_SPEC = importlib.util.spec_from_file_location("release_boundary_scale", _RELEASE_BOUNDARY)
assert _SPEC and _SPEC.loader
release_boundary = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_boundary)


def _selection(count: int) -> dict[str, object]:
    return {"selected_logical_work_order_ids": [f"body.p.{index:05d}" for index in range(count)]}


def test_200_shape_remains_eight_full_checkpoints() -> None:
    selection = _selection(200)
    assert checkpoint_count_for_target(200) == 8
    assert len(frozen_checkpoint_order(selection, "checkpoint_08")) == CHECKPOINT_SIZE


def test_1000_shape_accepts_forty_full_checkpoints_with_25_unit_blast_radius() -> None:
    selection = _selection(1000)
    assert checkpoint_count_for_target(1000) == 40
    order = frozen_checkpoint_order(selection, "checkpoint_40")
    assert len(order) == CHECKPOINT_SIZE
    assert order == [f"body.p.{index:05d}" for index in range(975, 1000)]


def test_terminal_checkpoint_is_derived_from_actual_target_not_checkpoint_08() -> None:
    selection = _selection(976)
    assert checkpoint_count_for_target(976) == 40
    assert frozen_checkpoint_order(selection, "checkpoint_40") == ["body.p.00975"]


def test_final_partial_checkpoint_contract_accepts_generic_298_and_26_shapes() -> None:
    assert checkpoint_count_for_target(298) == 12
    assert validate_frozen_checkpoint_members(target_count=298, selection_count=298, checkpoint="checkpoint_12", member_count=23)
    assert checkpoint_count_for_target(26) == 2
    assert validate_frozen_checkpoint_members(target_count=26, selection_count=26, checkpoint="checkpoint_02", member_count=1)


def test_full_checkpoint_shapes_remain_exactly_25() -> None:
    assert validate_frozen_checkpoint_members(target_count=200, selection_count=200, checkpoint="checkpoint_08", member_count=25)
    assert validate_frozen_checkpoint_members(target_count=1000, selection_count=1000, checkpoint="checkpoint_40", member_count=25)
    assert not validate_frozen_checkpoint_members(target_count=298, selection_count=298, checkpoint="checkpoint_11", member_count=24)


def test_final_partial_checkpoint_rejects_empty_oversized_or_selection_mismatch() -> None:
    assert not validate_frozen_checkpoint_members(target_count=298, selection_count=298, checkpoint="checkpoint_12", member_count=0)
    assert not validate_frozen_checkpoint_members(target_count=298, selection_count=298, checkpoint="checkpoint_12", member_count=26)
    assert not validate_frozen_checkpoint_members(target_count=298, selection_count=297, checkpoint="checkpoint_12", member_count=23)
    assert not validate_frozen_checkpoint_members(target_count=298, selection_count=299, checkpoint="checkpoint_12", member_count=23)


def test_finalized_release_gate_uses_actual_selection_membership(tmp_path: Path) -> None:
    count = 1000
    ids = [f"body.p.{index:05d}" for index in range(count)]
    run = tmp_path / "run"; batch = run / "full_book_batch_036"; batch.mkdir(parents=True)
    selection = run / "full_book_batch_036_selection.json"
    selection.write_text(json.dumps({"selected_logical_work_order_ids": ids}), encoding="utf-8")
    debt = batch / "release_debt_manifest_v1.json"
    debt.write_text(json.dumps({
        "artifact_type": "BATCH_RELEASE_DEBT_MANIFEST", "schema_version": 2,
        "batch": "FULL_BOOK_BATCH_036", "batch_dir": batch.name,
        "current_release_debt_total": 0, "entries": [], "current_authoritative_state_digest": "state",
    }), encoding="utf-8")
    gate = batch / "release_gate_v1.json"
    gate.write_text(json.dumps({
        "artifact_type": "BATCH_RELEASE_GATE", "schema_version": 2,
        "batch": "FULL_BOOK_BATCH_036", "batch_dir": batch.name, "verdict": "PASS", "unresolved_ids": [],
        "integrity_pass": True, "candidate_authority_pass": True, "finalize_only": True, "model_calls": 0,
        "provider_dispatches": 0, "release_debt_manifest": str(debt),
        "release_debt_manifest_sha256": sha256(debt.read_bytes()).hexdigest(), "current_authoritative_state_digest": "state",
    }), encoding="utf-8")
    scope = {"logical_work_orders": [{"logical_work_order_id": item, "phase": "body", "release_status": "PENDING_FIRST_PASS"} for item in ids]}
    assert derive_release_membership(run, scope) == set(ids)


def test_release_boundary_opens_only_after_actual_1000_target_and_40_seals(tmp_path: Path) -> None:
    batch = tmp_path / "full_book_batch_036"; batch.mkdir()
    checkpoints = []
    for ordinal in range(1, 41):
        checkpoint = f"checkpoint_{ordinal:02d}"
        (batch / checkpoint).mkdir()
        (batch / checkpoint / "checkpoint_evidence.json").write_text("{}", encoding="utf-8")
        checkpoints.append({"id": checkpoint, "actual_count": 25})
    manifest = {
        "batch": "FULL_BOOK_BATCH_036", "target_count": 1000, "completed_count": 1000,
        "checkpoint_count": 40, "status": "COMPLETE", "next_checkpoint": None, "checkpoints": checkpoints,
    }
    path = batch / "batch_manifest.json"; path.write_text(json.dumps(manifest), encoding="utf-8")
    assert release_boundary._manifest_path(batch) == path
    assert len(release_boundary._checkpoints(batch, manifest)) == 40
    manifest["completed_count"] = 999
    path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        release_boundary._manifest_path(batch)
    except RuntimeError as error:
        assert str(error) == "RELEASE_BATCH_NOT_COMPLETE"
    else:
        raise AssertionError("release must not open before the actual target is complete")
