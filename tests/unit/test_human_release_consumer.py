from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import TranslationResult
from booktrans.harness.human_release_consumer import (
    HumanReleaseConsumerError,
    consume_human_approval_for_release,
)
from booktrans.production_worker.candidate_authority import ResolvedCandidate, candidate_sha
from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs


BATCH = "FULL_BOOK_BATCH_023"
DISPLAY_ID = "body.p.07189"
UNIT_UID = "unit-07189"
SOURCE_SHA = "a" * 64
REQUEST_BINDING = "b" * 64
PARENT_SHA = "c" * 64


def _candidate(text: str) -> TranslationResult:
    return TranslationResult.model_validate({
        "schema_version": "m5.0",
        "unit_uid": UNIT_UID,
        "target_segments": [{"text": text, "kind": "anchored", "anchor_ref": "anchor", "island_ref": "island.001"}],
        "translator_notes": None,
    })


def _debt(*, parent_sha: str = PARENT_SHA, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "artifact_type": "B023_RELEASE_BOUNDARY_ADJUDICATION",
        "logical_work_order_id": DISPLAY_ID,
        "unit_uid": UNIT_UID,
        "source_sha256": SOURCE_SHA,
        "request_binding_sha256": REQUEST_BINDING,
        "current_authoritative_candidate_sha256": parent_sha,
        "release_status": "TRUE_HUMAN_REQUIRED",
        "release_classification": "FLASH_RESOLVABLE",
    }
    result.update(overrides)
    return result


def _adjudication(*, mode: str = "MUTATION", **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "artifact_type": "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION",
        "decision": "HUMAN_APPROVED",
        "triage_classification": "TRUE_SEMANTIC_HUMAN",
        "batch_label": BATCH,
        "logical_work_order_id": DISPLAY_ID,
        "unit_uid": UNIT_UID,
        "source_sha256": SOURCE_SHA,
        "request_binding_sha256": REQUEST_BINDING,
        "approval_mode": mode,
        "member_work_order_ids": [DISPLAY_ID],
        "member_unit_uids": [UNIT_UID],
        "source_hashes": {DISPLAY_ID: REQUEST_BINDING},
    }
    result.update(overrides)
    return result


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path, *, mutation: bool = True) -> tuple[Path, dict[str, object], ResolvedCandidate]:
    checkpoint = tmp_path / "full_book_batch_023" / "checkpoint_01"
    if mutation:
        approved = _candidate("approved")
        approved_sha = candidate_sha(approved)
        _write(checkpoint / "human_adjudications" / f"{DISPLAY_ID}.json", _adjudication())
        _write(checkpoint / "human_approved_candidates" / f"{DISPLAY_ID}.json", {
            "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE",
            "schema_version": 2,
            "current_display_id": DISPLAY_ID,
            "decision": "HUMAN_APPROVED",
            "semantic_terminal": "HUMAN_APPROVED",
            "parent_candidate_sha256": PARENT_SHA,
            "candidate_binding_sha256": approved_sha,
            "source_sha256": SOURCE_SHA,
            "request_binding_sha256": REQUEST_BINDING,
            "working": approved.model_dump(),
            "qa": {"display_id": DISPLAY_ID, "unit_uid": UNIT_UID, "passed": True},
        })
        return checkpoint, _debt(), ResolvedCandidate(approved, approved_sha, ())
    current = _candidate("preserved")
    current_sha = candidate_sha(current)
    _write(checkpoint / "human_adjudications" / f"{DISPLAY_ID}.json", _adjudication(
        mode="PRESERVE_CURRENT", approved_candidate_sha256=current_sha,
    ))
    return checkpoint, _debt(parent_sha=current_sha), ResolvedCandidate(current, current_sha, ())


def _consume(checkpoint: Path, debt: dict[str, object], head: ResolvedCandidate):
    return consume_human_approval_for_release(
        checkpoint_dir=checkpoint,
        batch_label=BATCH,
        logical_work_order_id=DISPLAY_ID,
        unit_uid=UNIT_UID,
        source_sha256=SOURCE_SHA,
        request_binding_sha256=REQUEST_BINDING,
        release_debt=debt,
        current_head=head,
    )


def test_historical_b022_human_approval_is_legacy_compatible() -> None:
    run = Path("runs/full-book-20260916T000200Z-decb09").resolve()
    checkpoint = run / "full_book_batch_022" / "checkpoint_03"
    display_id = "body.p.06968"
    adjudication = json.loads((checkpoint / "human_adjudications" / f"{display_id}.json").read_text(encoding="utf-8"))
    candidate_record = json.loads((checkpoint / "human_approved_candidates" / f"{display_id}.json").read_text(encoding="utf-8"))
    loaded = frozen_batch_checkpoint_inputs(run, batch_label="FULL_BOOK_BATCH_022", checkpoint="checkpoint_03")
    request = next(item for item in loaded["requests"] if item.display_id == display_id)
    approved = TranslationResult.model_validate(candidate_record["working"])
    head = ResolvedCandidate(approved, candidate_sha(approved), ())
    result = consume_human_approval_for_release(
        checkpoint_dir=checkpoint,
        batch_label="FULL_BOOK_BATCH_022",
        logical_work_order_id=display_id,
        unit_uid=request.unit_uid,
        source_sha256=request.source_sha256,
        request_binding_sha256=adjudication["source_hashes"][display_id],
        release_debt={
            "logical_work_order_id": display_id,
            "unit_uid": request.unit_uid,
            "source_sha256": request.source_sha256,
            "request_binding_sha256": adjudication["source_hashes"][display_id],
            "current_authoritative_candidate_sha256": head.candidate_sha256,
            "release_status": "TRUE_HUMAN_REQUIRED",
        },
        current_head=head,
        allow_legacy_batch_evidence=True,
    )
    assert result is not None
    assert result.status == "RESOLVED_FOR_RELEASE"


def test_valid_b023_human_approval_resolves_exact_release_debt(tmp_path: Path) -> None:
    checkpoint, debt, head = _fixture(tmp_path)
    result = _consume(checkpoint, debt, head)
    assert result is not None
    assert result.status == "RESOLVED_FOR_RELEASE"
    assert result.approval_mode == "MUTATION"


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("batch_label", "FULL_BOOK_BATCH_022"),
        ("logical_work_order_id", "body.p.99999"),
        ("unit_uid", "wrong-unit"),
        ("source_sha256", "d" * 64),
    ],
)
def test_human_release_rejects_identity_mismatches(tmp_path: Path, target: str, value: str) -> None:
    checkpoint, debt, head = _fixture(tmp_path)
    path = checkpoint / "human_adjudications" / f"{DISPLAY_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record[target] = value
    if target == "logical_work_order_id":
        record["member_work_order_ids"] = [value]
    if target == "unit_uid":
        record["member_unit_uids"] = [value]
    _write(path, record)
    with pytest.raises(HumanReleaseConsumerError):
        _consume(checkpoint, debt, head)


def test_b022_evidence_cannot_close_b023_debt(tmp_path: Path) -> None:
    checkpoint, debt, head = _fixture(tmp_path)
    record_path = checkpoint / "human_adjudications" / f"{DISPLAY_ID}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["batch_label"] = "FULL_BOOK_BATCH_022"
    _write(record_path, record)
    with pytest.raises(HumanReleaseConsumerError, match="BATCH_MISMATCH"):
        _consume(checkpoint, debt, head)


def test_wrong_parent_and_wrong_current_head_are_rejected(tmp_path: Path) -> None:
    checkpoint, debt, head = _fixture(tmp_path)
    candidate_path = checkpoint / "human_approved_candidates" / f"{DISPLAY_ID}.json"
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate["parent_candidate_sha256"] = "e" * 64
    _write(candidate_path, candidate)
    with pytest.raises(HumanReleaseConsumerError, match="CANDIDATE_BINDING_INVALID"):
        _consume(checkpoint, debt, head)
    candidate["parent_candidate_sha256"] = PARENT_SHA
    _write(candidate_path, candidate)
    wrong = _candidate("different")
    with pytest.raises(HumanReleaseConsumerError, match="CANDIDATE_BINDING_INVALID"):
        _consume(checkpoint, debt, ResolvedCandidate(wrong, candidate_sha(wrong), ()))


def test_create_once_conflict_is_rejected(tmp_path: Path) -> None:
    checkpoint, debt, head = _fixture(tmp_path)
    duplicate = json.loads((checkpoint / "human_approved_candidates" / f"{DISPLAY_ID}.json").read_text(encoding="utf-8"))
    _write(checkpoint / "human_approved_candidates" / "duplicate.json", duplicate)
    with pytest.raises(HumanReleaseConsumerError, match="CREATE_ONCE_CONFLICT"):
        _consume(checkpoint, debt, head)


def test_preserve_current_needs_no_candidate_and_resolves(tmp_path: Path) -> None:
    checkpoint, debt, head = _fixture(tmp_path, mutation=False)
    result = _consume(checkpoint, debt, head)
    assert result is not None
    assert result.approval_mode == "PRESERVE_CURRENT"
    assert not (checkpoint / "human_approved_candidates" / f"{DISPLAY_ID}.json").exists()


def test_no_human_approval_leaves_true_human_required_unresolved(tmp_path: Path) -> None:
    checkpoint = tmp_path / "full_book_batch_023" / "checkpoint_01"
    head_candidate = _candidate("current")
    head = ResolvedCandidate(head_candidate, candidate_sha(head_candidate), ())
    assert _consume(checkpoint, _debt(), head) is None


def test_b023_runner_uses_consumer_for_resolved_human_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint, debt, head = _fixture(tmp_path)
    script = Path("scripts/production/reconcile_b023_release_boundary.py").resolve()
    spec = importlib.util.spec_from_file_location("b023_release_runner_under_test", script)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.BATCH_DIR = checkpoint.parent
    runner.BATCH_LABEL = BATCH
    monkeypatch.setattr(runner, "providers_from_environment", lambda: None)
    monkeypatch.setattr(runner, "_collect_debts", lambda: {DISPLAY_ID: {"checkpoint": "checkpoint_01", "evidence": []}})
    request = SimpleNamespace(unit_uid=UNIT_UID, source_sha256=SOURCE_SHA)
    facts = {"checkpoint_dir": checkpoint, "request": request, "head": head, "bible": None}
    monkeypatch.setattr(runner, "_current_facts", lambda checkpoint_name, display_id: facts)
    monkeypatch.setattr(runner, "_effective_adjudication", lambda checkpoint_dir, display_id: debt)
    monkeypatch.setattr(runner, "load_stage", lambda checkpoint_dir, stage, display_id: debt if stage == "release_boundary_adjudications" else None)
    monkeypatch.setattr(runner, "enrich_request", lambda request, translation_bible=None: SimpleNamespace(request_binding_sha256=REQUEST_BINDING))
    monkeypatch.setattr(runner, "_read", lambda path: {"source_sha256": SOURCE_SHA})
    result = runner.run(apply=True)
    assert result["gate"]["verdict"] == "PASS"
    assert result["manifest"]["entries"][0]["release_status"] == "RESOLVED_FOR_RELEASE"
