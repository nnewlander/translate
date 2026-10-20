from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest


MODULE = Path(__file__).parents[2] / "scripts" / "advance_full_book_batch013.py"
SCRIPTS = MODULE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("terminal_close_policy", MODULE)
assert SPEC and SPEC.loader
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def configured_close_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, gate_pass: bool = True, target_count: int = 200, exhausted: bool = False) -> tuple[Path, Path]:
    run = tmp_path / "run"; source = tmp_path / "source.docx"; source.write_bytes(b"source")
    source_sha = sha256(source.read_bytes()).hexdigest(); batch = run / "full_book_batch_028"
    selected = [f"body.p.{index:05d}" for index in range(target_count)]
    scope_ids = selected if exhausted else selected + [f"body.p.{index:05d}" for index in range(target_count, target_count * 2)]
    selection = run / "full_book_batch_028_selection.json"
    write(selection, {"batch": "FULL_BOOK_BATCH_028", "selected_logical_work_order_ids": selected})
    checkpoints = []
    for offset in range((target_count + 24) // 25):
        checkpoint = f"checkpoint_{offset + 1:02d}"; ids = selected[offset * 25:(offset + 1) * 25]
        directory = batch / checkpoint
        write(directory / "checkpoint_evidence.json", {"logical_work_orders": ids})
        write(directory / "translation_raw_qa_report.json", {"pass_count": len(ids), "fail_count": 0})
        write(directory / "translation_final_qa_report.json", {"pass_count": len(ids), "fail_count": 0})
        write(directory / "semantic_review_report.json", {"human_required_count": 0})
        checkpoints.append({"id": checkpoint})
    write(batch / "batch_manifest.json", {"batch": "FULL_BOOK_BATCH_028", "status": "COMPLETE", "completed_count": target_count, "target_count": target_count, "checkpoint_count": len(checkpoints), "checkpoints": checkpoints})
    scope = run / "scope.json"; write(scope, {"logical_work_orders": [{"logical_work_order_id": item, "phase": "body"} for item in scope_ids]})
    prior = run / "full_book_progress_v29.json"; write(prior, {"current_release_set_count": 0, "total_physical_work_orders": 400})
    debt = batch / "release_debt_manifest_v1.json"; write(debt, {"artifact_type": "BATCH_RELEASE_DEBT_MANIFEST", "batch": "FULL_BOOK_BATCH_028", "current_release_debt_total": 0, "entries": [], "current_authoritative_state_digest": "digest"})
    gate = batch / "release_gate_v1.json"; write(gate, {"artifact_type": "BATCH_RELEASE_GATE", "batch": "FULL_BOOK_BATCH_028", "verdict": "PASS" if gate_pass else "BLOCKED_HUMAN_ADJUDICATION_REQUIRED", "unresolved_ids": [], "integrity_pass": True, "candidate_authority_pass": True, "finalize_only": True, "model_calls": 0, "provider_dispatches": 0, "release_debt_manifest": str(debt), "release_debt_manifest_sha256": sha256(debt.read_bytes()).hexdigest(), "current_authoritative_state_digest": "digest"})
    monkeypatch.setattr(policy, "RUN", run); monkeypatch.setattr(policy, "SOURCE", source); monkeypatch.setattr(policy, "SHA", source_sha)
    monkeypatch.setattr(policy, "SCOPE", scope); monkeypatch.setattr(policy, "PRIOR", prior); monkeypatch.setattr(policy, "BATCH", batch); monkeypatch.setattr(policy, "SELECTION", selection)
    monkeypatch.setattr(policy, "NEXT_BATCH", run / "full_book_batch_029"); monkeypatch.setattr(policy, "NEXT_SELECTION", run / "full_book_batch_029_selection.json")
    monkeypatch.setattr(policy, "COMPLETION", run / "full_book_batch_028_completion_report.json"); monkeypatch.setattr(policy, "PROGRESS", run / "full_book_progress_v30.json")
    monkeypatch.setattr(policy, "BATCH_LABEL", "FULL_BOOK_BATCH_028"); monkeypatch.setattr(policy, "NEXT_BATCH_LABEL", "FULL_BOOK_BATCH_029")
    monkeypatch.setattr(policy, "derive_release_membership", lambda _run, _scope: set(selected))
    return run, batch


def test_close_only_closes_without_creating_b029(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _batch = configured_close_only(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["close", "--close-only"])
    policy.main()
    completion = json.loads((run / "full_book_batch_028_completion_report.json").read_text(encoding="utf-8"))
    assert completion["canonical_close"] is True and completion["canonical_close_only"] is True
    assert not (run / "full_book_batch_029").exists()


def test_repeat_close_only_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _batch = configured_close_only(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["close", "--close-only"])
    policy.main(); first = (run / "full_book_batch_028_completion_report.json").read_bytes()
    policy.main()
    assert (run / "full_book_batch_028_completion_report.json").read_bytes() == first


def test_close_only_allows_an_exhausted_authoritative_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _batch = configured_close_only(monkeypatch, tmp_path, exhausted=True)
    monkeypatch.setattr(sys, "argv", ["close", "--close-only"])
    policy.main()
    completion = json.loads((run / "full_book_batch_028_completion_report.json").read_text(encoding="utf-8"))
    progress = json.loads((run / "full_book_progress_v30.json").read_text(encoding="utf-8"))
    assert completion["next_resume_point_derived_from_authoritative_scope"] is None
    assert progress["next_resume_point_derived_from_authoritative_scope"] is None
    assert not (run / "full_book_batch_029").exists()


def test_close_only_accepts_1000_units_and_forty_checkpoints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _batch = configured_close_only(monkeypatch, tmp_path, target_count=1000)
    monkeypatch.setattr(sys, "argv", ["close", "--close-only"])
    policy.main()
    completion = json.loads((run / "full_book_batch_028_completion_report.json").read_text(encoding="utf-8"))
    assert completion["completed_logical_work_orders"] == 1000
    assert len(completion["checkpoint_stats"]) == 40


def test_close_only_rejects_blocked_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configured_close_only(monkeypatch, tmp_path, gate_pass=False)
    monkeypatch.setattr(sys, "argv", ["close", "--close-only"])
    with pytest.raises(RuntimeError, match="CANONICAL_CLOSE_RELEASE_GATE_INVALID"):
        policy.main()


def test_legacy_combined_policy_keeps_pre_gate_compatibility(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _run, batch = configured_close_only(monkeypatch, tmp_path)
    monkeypatch.setattr(policy, "BATCH", batch / "legacy"); (batch / "legacy").mkdir()
    monkeypatch.setattr(policy, "BATCH_LABEL", "FULL_BOOK_BATCH_027")
    monkeypatch.setattr(sys, "argv", ["close"])
    assert policy._release_gate_for_close() is None


def test_historical_b013_rollover_identity_is_derived(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write(run / "full_book_progress_v14.json", {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS",
        "latest_completed_batch": "FULL_BOOK_BATCH_013",
    })
    context = policy._derive_rollover_context(run)
    assert context["BATCH_LABEL"] == "FULL_BOOK_BATCH_013"
    assert context["NEXT_BATCH_LABEL"] == "FULL_BOOK_BATCH_014"
    assert context["PROGRESS"] == run / "full_book_progress_v15.json"


def test_arbitrary_rollover_identity_and_latest_generation_are_derived(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write(run / "full_book_progress_v41.json", {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS",
        "latest_completed_batch": "FULL_BOOK_BATCH_034",
    })
    write(run / "full_book_progress_v43.json", {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS",
        "latest_completed_batch": "FULL_BOOK_BATCH_035",
    })
    context = policy._derive_rollover_context(run)
    assert context["BATCH_LABEL"] == "FULL_BOOK_BATCH_035"
    assert context["NEXT_BATCH_LABEL"] == "FULL_BOOK_BATCH_036"
    assert context["PROGRESS"] == run / "full_book_progress_v44.json"


def test_rollover_requires_a_canonical_close_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configured_close_only(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["rollover", "--rollover-only"])
    with pytest.raises(RuntimeError, match="ROLLOVER_CANONICAL_CLOSE_MISSING"):
        policy.main()


def test_rollover_rejects_a_wrong_close_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _batch = configured_close_only(monkeypatch, tmp_path)
    write(run / "full_book_batch_028_completion_report.json", {
        "batch": "FULL_BOOK_BATCH_027", "canonical_close": True, "canonical_close_only": True,
    })
    monkeypatch.setattr(sys, "argv", ["rollover", "--rollover-only"])
    with pytest.raises(RuntimeError, match="ROLLOVER_CANONICAL_CLOSE_INVALID"):
        policy.main()


def test_rollover_only_publishes_successor_progress_and_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, batch = configured_close_only(monkeypatch, tmp_path)
    bible = tmp_path / "translation_bible.md"; bible.write_text("snapshot", encoding="utf-8")
    shutil.copyfile(bible, batch / "translation_bible_snapshot.md")
    monkeypatch.setattr(sys, "argv", ["close", "--close-only"])
    policy.main()
    close_progress = policy.PROGRESS
    successor = run / "full_book_progress_v31.json"
    monkeypatch.setattr(policy, "PRIOR", close_progress)
    monkeypatch.setattr(policy, "PROGRESS", successor)
    monkeypatch.setattr(sys, "argv", ["close", "--rollover-only"])
    policy.main()
    first = successor.read_bytes()
    policy.main()
    progress = json.loads(first)
    assert progress["status"] == "FULL_BOOK_PRODUCTION_IN_PROGRESS"
    assert progress["supersedes"] == close_progress.name
    assert (run / "full_book_batch_029").is_dir()
    assert successor.read_bytes() == first
