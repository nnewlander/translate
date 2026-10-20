from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from booktrans.harness import release_membership
from booktrans.harness.release_membership import derive_release_membership, pending_in_source_order


def _write_final(root: Path, name: str, rows: list[dict[str, object]]) -> None:
    path = root / name / "translation_final_qa_report.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"records": rows}), encoding="utf-8")


def test_release_membership_excludes_initial_and_final_approved_units(tmp_path: Path) -> None:
    scope = {"logical_work_orders": [
        {"logical_work_order_id": "preface.1", "phase": "preface", "release_status": "ALREADY_APPROVED"},
        {"logical_work_order_id": "chapter_1.1", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
        {"logical_work_order_id": "chapter_7.1", "phase": "chapter_7", "release_status": "PENDING_FIRST_PASS"},
    ]}
    _write_final(tmp_path, "batch/checkpoint", [{"display_id": "chapter_1.1", "passed": True}])
    released = derive_release_membership(tmp_path, scope)
    assert released == {"preface.1", "chapter_1.1"}
    assert [item["logical_work_order_id"] for item in pending_in_source_order(scope, released)] == ["chapter_7.1"]


def test_release_membership_uses_only_passing_final_records_and_keeps_source_order(tmp_path: Path) -> None:
    scope = {"logical_work_orders": [
        {"logical_work_order_id": "a", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
        {"logical_work_order_id": "b", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
        {"logical_work_order_id": "toc", "phase": "toc_late_stage", "release_status": "PENDING_FIRST_PASS"},
    ]}
    _write_final(tmp_path, "batch/checkpoint", [{"display_id": "a", "passed": False}, {"display_id": "b", "passed": True}])
    released = derive_release_membership(tmp_path, scope)
    assert released == {"b"}
    assert [item["logical_work_order_id"] for item in pending_in_source_order(scope, released)] == ["a"]


def test_release_membership_excludes_fully_bound_deferred_semantic_debt(tmp_path: Path) -> None:
    scope = {"logical_work_orders": [
        {"logical_work_order_id": "a", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
        {"logical_work_order_id": "b", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
    ]}
    path = tmp_path / "batch/checkpoint/translation_final_qa_report.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "records": [
            {"display_id": "a", "unit_uid": "uid-a", "passed": True},
            {"display_id": "b", "unit_uid": "uid-b", "passed": True},
        ],
        "final_approved_unit_uids": ["uid-a"],
        "deferred_semantic_debt_unit_uids": ["uid-b"],
    }), encoding="utf-8")
    assert derive_release_membership(tmp_path, scope) == {"a"}


def test_release_membership_admits_only_bound_production_human_approval(tmp_path: Path) -> None:
    scope = {"logical_work_orders": [
        {"logical_work_order_id": "a", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
    ]}
    checkpoint = tmp_path / "batch/checkpoint"
    _write_final(tmp_path, "batch/checkpoint", [{"display_id": "a", "unit_uid": "uid-a", "passed": True}])
    (checkpoint / "human_adjudications").mkdir()
    (checkpoint / "human_approved_candidates").mkdir()
    (checkpoint / "human_adjudications" / "a.json").write_text(json.dumps({
        "artifact_type": "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION", "decision": "HUMAN_APPROVED",
        "member_work_order_ids": ["a"], "member_unit_uids": ["uid-a"],
    }), encoding="utf-8")
    (checkpoint / "human_approved_candidates" / "a.json").write_text(json.dumps({
        "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE", "current_display_id": "a",
        "decision": "HUMAN_APPROVED", "semantic_terminal": "HUMAN_APPROVED",
        "working": {"unit_uid": "uid-a"}, "qa": {"display_id": "a", "unit_uid": "uid-a", "passed": True},
    }), encoding="utf-8")
    assert derive_release_membership(tmp_path, scope) == {"a"}


def test_pending_phase_counts_and_resume_point_follow_release_membership(tmp_path: Path) -> None:
    scope = {"logical_work_orders": [
        {"logical_work_order_id": "preface.done", "phase": "preface", "release_status": "ALREADY_APPROVED"},
        {"logical_work_order_id": "chapter_1.done", "phase": "chapter_1", "release_status": "PENDING_FIRST_PASS"},
        {"logical_work_order_id": "chapter_7.pending", "phase": "chapter_7", "release_status": "PENDING_FIRST_PASS"},
        {"logical_work_order_id": "chapter_8.pending", "phase": "chapter_8", "release_status": "PENDING_FIRST_PASS"},
    ]}
    _write_final(tmp_path, "batch/checkpoint", [{"display_id": "chapter_1.done", "passed": True}])
    pending = pending_in_source_order(scope, derive_release_membership(tmp_path, scope))
    assert pending[0]["logical_work_order_id"] == "chapter_7.pending"
    assert {phase: sum(item["phase"] == phase for item in pending) for phase in {item["phase"] for item in pending}} == {"chapter_7": 1, "chapter_8": 1}


def _generic_gate(tmp_path: Path, *, batch: str = "024", candidate: str = "c" * 64, status: str = "RESOLVED_FOR_RELEASE", resolution_type: str = "REVIEW_ACCEPT", verdict: str = "BLOCKED_CURRENT_RELEASE_DEBT") -> tuple[Path, dict[str, object]]:
    batch_dir = tmp_path / f"full_book_batch_{batch}"
    checkpoint = batch_dir / "checkpoint_01"
    resolution = {
        "artifact_type": "BATCH_RELEASE_CURRENT_HEAD_RESOLUTION", "schema_version": 1,
        "batch": f"FULL_BOOK_BATCH_{batch}", "checkpoint": "checkpoint_01",
        "logical_work_order_id": "a", "unit_uid": "uid-a", "source_sha256": "s" * 64,
        "request_binding_sha256": "r" * 64, "current_authoritative_candidate_sha256": candidate,
        "candidate_authority_pass": True, "core_integrity_pass": True, "resolution_type": resolution_type,
    }
    resolution_path = checkpoint / "release_boundary_adjudications" / "a.json"
    resolution_path.parent.mkdir(parents=True)
    resolution_path.write_text(json.dumps(resolution), encoding="utf-8")
    entry = {
        "logical_work_order_id": "a", "checkpoint": "checkpoint_01", "unit_uid": "uid-a",
        "source_sha256": "s" * 64, "request_binding_sha256": "r" * 64,
        "current_authoritative_candidate_sha256": candidate, "release_status": status,
        "resolution_path": str(resolution_path), "resolution_sha256": sha256(resolution_path.read_bytes()).hexdigest(),
    }
    manifest = {
        "artifact_type": "BATCH_RELEASE_DEBT_MANIFEST", "schema_version": 1,
        "batch": f"FULL_BOOK_BATCH_{batch}", "batch_dir": batch_dir.name, "entries": [entry],
    }
    manifest_path = batch_dir / "release_debt_manifest_v1.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    gate = {
        "artifact_type": "BATCH_RELEASE_GATE", "schema_version": 1,
        "batch": f"FULL_BOOK_BATCH_{batch}", "batch_dir": batch_dir.name,
        "verdict": verdict, "release_debt_manifest": str(manifest_path),
        "release_debt_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
    }
    (batch_dir / "release_gate_v1.json").write_text(json.dumps(gate), encoding="utf-8")
    return batch_dir, entry


def test_generic_release_gate_accepts_only_current_head_bound_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _generic_gate(tmp_path)
    monkeypatch.setattr(release_membership, "_current_head_binding", lambda *_args: "c" * 64)
    scope = {"logical_work_orders": [{"logical_work_order_id": "a", "phase": "chapter", "release_status": "PENDING_FIRST_PASS"}]}
    assert derive_release_membership(tmp_path, scope) == {"a"}


def test_generic_release_gate_consumes_published_current_head_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _generic_gate(tmp_path)
    scope = {"logical_work_orders": [{"logical_work_order_id": "a", "phase": "chapter", "release_status": "PENDING_FIRST_PASS"}]}
    monkeypatch.setattr(release_membership, "_current_head_binding", lambda *_args: "d" * 64)
    # A PASS gate is the authoritative current-head projection; membership
    # consumers validate its immutable bindings rather than casting a second
    # current-head vote during every selection scan.
    assert derive_release_membership(tmp_path, scope) == {"a"}
    batch_dir, _ = _generic_gate(tmp_path / "other", batch="024")
    gate_path = batch_dir / "release_gate_v1.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")); gate["batch"] = "FULL_BOOK_BATCH_023"; gate_path.write_text(json.dumps(gate), encoding="utf-8")
    monkeypatch.setattr(release_membership, "_current_head_binding", lambda *_args: "c" * 64)
    with pytest.raises(RuntimeError, match="BATCH_RELEASE_GATE_BINDING_INVALID"):
        derive_release_membership(tmp_path / "other", scope)


@pytest.mark.parametrize("resolution_type", ["REPAIR_ACCEPT", "HUMAN_APPROVED"])
def test_generic_release_gate_accepts_each_allowed_current_head_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution_type: str) -> None:
    _generic_gate(tmp_path, resolution_type=resolution_type, verdict="PASS")
    monkeypatch.setattr(release_membership, "_current_head_binding", lambda *_args: "c" * 64)
    scope = {"logical_work_orders": [{"logical_work_order_id": "a", "phase": "chapter", "release_status": "PENDING_FIRST_PASS"}]}
    # Replaying the same immutable gate is idempotent.
    assert derive_release_membership(tmp_path, scope) == {"a"}
    assert derive_release_membership(tmp_path, scope) == {"a"}


def test_generic_release_gate_rejects_wrong_context_and_keeps_unresolved_debt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    batch_dir, entry = _generic_gate(tmp_path, status="UNRESOLVED")
    monkeypatch.setattr(release_membership, "_current_head_binding", lambda *_args: "c" * 64)
    scope = {"logical_work_orders": [{"logical_work_order_id": "a", "phase": "chapter", "release_status": "PENDING_FIRST_PASS"}]}
    assert derive_release_membership(tmp_path, scope) == set()
    entry["release_status"] = "RESOLVED_FOR_RELEASE"
    resolution_path = Path(str(entry["resolution_path"]))
    resolution = json.loads(resolution_path.read_text(encoding="utf-8")); resolution["request_binding_sha256"] = "x" * 64; resolution_path.write_text(json.dumps(resolution), encoding="utf-8")
    entry["resolution_sha256"] = sha256(resolution_path.read_bytes()).hexdigest()
    manifest_path = batch_dir / "release_debt_manifest_v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")); manifest["entries"] = [entry]; manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    gate_path = batch_dir / "release_gate_v1.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")); gate["release_debt_manifest_sha256"] = sha256(manifest_path.read_bytes()).hexdigest(); gate_path.write_text(json.dumps(gate), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BATCH_RELEASE_RESOLUTION_BINDING_INVALID"):
        derive_release_membership(tmp_path, scope)
