from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "production" / "validate_batch_rollover_readiness.py"
SPEC = importlib.util.spec_from_file_location("batch_ready", MODULE_PATH)
assert SPEC and SPEC.loader
batch_ready = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch_ready)


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path: Path, *, target_count: int = 200) -> tuple[Path, Path, Path]:
    source = tmp_path / "source.docx"; source.write_bytes(b"source")
    sha = sha256(source.read_bytes()).hexdigest()
    run = tmp_path / "run"; batch = run / "full_book_batch_015"; batch.mkdir(parents=True)
    selection_path = run / "full_book_batch_015_selection.json"; ids = [f"body.p.{i:05d}" for i in range(target_count)]
    write(selection_path, {"artifact_type": "FULL_BOOK_BATCH_SELECTION", "batch": "FULL_BOOK_BATCH_015", "source_sha256": sha, "selected_logical_work_order_ids": ids})
    selection_sha = sha256(selection_path.read_bytes()).hexdigest()
    manifest_path = batch / "batch_manifest.json"
    write(manifest_path, {"batch": "FULL_BOOK_BATCH_015", "source_sha256": sha, "target_count": target_count, "completed_count": 0, "checkpoint_count": 0, "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "frozen_selection": str(selection_path), "frozen_selection_sha256": selection_sha})
    progress_path = run / "full_book_progress_v16.json"
    write(progress_path, {"artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS", "source_sha256": sha, "latest_completed_batch": "FULL_BOOK_BATCH_014", "next_resume_point_derived_from_authoritative_scope": ids[0], "recovery_queue": []})
    state_path = run / "production_campaign_state.json"
    write(state_path, {"artifact_type": "PRODUCTION_CAMPAIGN_STATE", "derived_from_immutable_artifacts": True, "source_sha256": sha, "active_batch": "FULL_BOOK_BATCH_015", "latest_authoritative_progress": {"path": str(progress_path), "sha256": sha256(progress_path.read_bytes()).hexdigest()}, "active_manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path.read_bytes()).hexdigest()}, "active_selection": {"path": str(selection_path), "sha256": selection_sha}, "target_count": target_count, "completed_count": 0, "next_checkpoint": "checkpoint_01", "next_logical_work_order_id": ids[0], "recovery_queue": []})
    return run, source, state_path


def test_batch_ready_passes_for_consistent_new_batch(tmp_path: Path) -> None:
    run, source, state = fixture(tmp_path)
    assert batch_ready.validate(run, source, state) == "FULL_BOOK_BATCH_015"


def test_batch_boundary_complete_projection_uses_null_cursor(tmp_path: Path) -> None:
    run, source, state = fixture(tmp_path)
    manifest_path = run / "full_book_batch_015" / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"completed_count": 200, "checkpoint_count": 8, "status": "COMPLETE", "next_checkpoint": None})
    write(manifest_path, manifest)
    state_record = json.loads(state.read_text(encoding="utf-8"))
    state_record["active_manifest"]["sha256"] = sha256(manifest_path.read_bytes()).hexdigest()
    state_record.update({"completed_count": 200, "next_checkpoint": None, "next_logical_work_order_id": None, "campaign_status": "BATCH_BOUNDARY_READY"})
    write(state, state_record)
    assert batch_ready.validate(run, source, state) == "FULL_BOOK_BATCH_015"


def test_batch_ready_accepts_1000_shape_and_derives_checkpoint_40_terminal(tmp_path: Path) -> None:
    run, source, state = fixture(tmp_path, target_count=1000)
    assert batch_ready.validate(run, source, state) == "FULL_BOOK_BATCH_015"
    manifest_path = run / "full_book_batch_015" / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"completed_count": 1000, "checkpoint_count": 40, "status": "COMPLETE", "next_checkpoint": None})
    write(manifest_path, manifest)
    state_record = json.loads(state.read_text(encoding="utf-8"))
    state_record["active_manifest"]["sha256"] = sha256(manifest_path.read_bytes()).hexdigest()
    state_record.update({"target_count": 1000, "completed_count": 1000, "next_checkpoint": None, "next_logical_work_order_id": None, "campaign_status": "BATCH_BOUNDARY_READY"})
    write(state, state_record)
    assert batch_ready.validate(run, source, state) == "FULL_BOOK_BATCH_015"


@pytest.mark.parametrize(("target", "sizes"), [(200, [25] * 8), (298, [25] * 11 + [23]), (1000, [25] * 40), (26, [25, 1])])
def test_d050_scopes_cover_the_manifest_derived_final_checkpoint(tmp_path: Path, target: int, sizes: list[int]) -> None:
    run, _source, _state = fixture(tmp_path, target_count=target)
    selection = json.loads((run / "full_book_batch_015_selection.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "full_book_batch_015" / "batch_manifest.json").read_text(encoding="utf-8"))
    scopes = batch_ready._d050_checkpoint_scopes(selection, manifest)
    assert [len(items) for _checkpoint, items in scopes] == sizes
    assert [item for _checkpoint, items in scopes for item in items] == selection["selected_logical_work_order_ids"]


def test_d050_scope_rejects_manifest_selection_mismatch(tmp_path: Path) -> None:
    run, _source, _state = fixture(tmp_path, target_count=26)
    selection = json.loads((run / "full_book_batch_015_selection.json").read_text(encoding="utf-8"))
    manifest = json.loads((run / "full_book_batch_015" / "batch_manifest.json").read_text(encoding="utf-8"))
    manifest["target_count"] = 27
    with pytest.raises(RuntimeError, match="BATCH_READY_D050_SELECTION_INVALID"):
        batch_ready._d050_checkpoint_scopes(selection, manifest)


@pytest.mark.parametrize(("path_part", "field", "value", "code"), [
    ("full_book_batch_015/batch_manifest.json", "batch", "FULL_BOOK_BATCH_014", "BATCH_READY_MANIFEST_BATCH_ID_MISMATCH"),
    ("full_book_batch_015_selection.json", "batch", "FULL_BOOK_BATCH_014", "BATCH_READY_SELECTION_BATCH_ID_MISMATCH"),
    ("full_book_batch_015/batch_manifest.json", "target_count", 199, "BATCH_READY_MANIFEST_TARGET_COUNT_MISMATCH"),
    ("full_book_batch_015/batch_manifest.json", "next_checkpoint", "checkpoint_02", "BATCH_READY_MANIFEST_NEXT_CHECKPOINT_MISMATCH"),
])
def test_batch_ready_rejects_immutable_transition_contradictions(tmp_path: Path, path_part: str, field: str, value: object, code: str) -> None:
    run, source, state = fixture(tmp_path)
    path = run / path_part; record = json.loads(path.read_text(encoding="utf-8")); record[field] = value; write(path, record)
    state_record = json.loads(state.read_text(encoding="utf-8"))
    key = "active_manifest" if path.name.startswith("batch_manifest") else "active_selection"
    state_record[key]["sha256"] = sha256(path.read_bytes()).hexdigest(); write(state, state_record)
    with pytest.raises(RuntimeError, match=code): batch_ready.validate(run, source, state)


def test_batch_ready_rejects_source_and_partial_checkpoint(tmp_path: Path) -> None:
    run, source, state = fixture(tmp_path)
    source.write_bytes(b"mutated")
    with pytest.raises(RuntimeError, match="BATCH_READY_SOURCE_SHA_MISMATCH"): batch_ready.validate(run, source, state)
    source.write_bytes(b"source")
    (run / "full_book_batch_015" / "checkpoint_01").mkdir()
    with pytest.raises(RuntimeError, match="BATCH_READY_UNEXPECTED_PARTIAL_CHECKPOINT_01"): batch_ready.validate(run, source, state)


def test_supervised_resume_allows_only_current_unsealed_partial(tmp_path: Path) -> None:
    run, source, state = fixture(tmp_path)
    partial = run / "full_book_batch_015" / "checkpoint_01"; partial.mkdir()
    assert batch_ready.validate(run, source, state, allow_current_partial=True) == "FULL_BOOK_BATCH_015"
    (partial / "checkpoint_evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="BATCH_READY_UNEXPECTED_PARTIAL_CHECKPOINT_01"):
        batch_ready.validate(run, source, state, allow_current_partial=True)


def test_batch_d050_coverage_requires_every_frozen_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial D050 traversal cannot make a new batch ready."""
    run, source, state = fixture(tmp_path)
    monkeypatch.setattr(
        batch_ready,
        "prepare_batch_d050_readiness",
        lambda *_: {"risk_records": 2, "selected_units_checked": 175},
    )
    with pytest.raises(RuntimeError, match="BATCH_READY_D050_COVERAGE_INCOMPLETE"):
        batch_ready.validate(run, source, state, prepare_d050_readiness=True)


def test_batch_d050_coverage_accepts_complete_exact_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, source, state = fixture(tmp_path)
    monkeypatch.setattr(
        batch_ready,
        "prepare_batch_d050_readiness",
        lambda *_: {"risk_records": 3, "selected_units_checked": 200},
    )
    assert batch_ready.validate(run, source, state, prepare_d050_readiness=True) == "FULL_BOOK_BATCH_015"
