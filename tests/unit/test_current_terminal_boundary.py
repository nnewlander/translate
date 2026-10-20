from types import SimpleNamespace

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.production_worker import production_checkpoint as checkpoint
from booktrans.production_worker.pipeline import UnitOutcome, route_semantic_unit


def _outcome():
    request = CalibrationRequest(
        source_sha256="a" * 64, unit_uid="unit-1", display_id="body.p.00001",
        story="body", content_role="paragraph", source_text="source",
        source_segments=[CalibrationSourceSegment(kind="plain", text="source", island_ref="island.001")],
    )
    raw = TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[StructuredTargetSegment(text="译文", kind="plain", island_ref="island.001")],
    )
    return UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw, working=raw,
        qa={"passed": True}, final_status="HUMAN_REQUIRED", semantic_terminal="HUMAN_REQUIRED",
        human_required=True,
    )


def _wire(monkeypatch, *, current_recovery_terminal=False, rejected_rescue=False):
    monkeypatch.setattr(checkpoint, "_resolve_checkpoint_authoritative_candidate", lambda directory, display_id, raw: SimpleNamespace(candidate=raw, candidate_sha256=checkpoint._candidate_binding(raw)))
    monkeypatch.setattr(checkpoint, "_current_head_rereview", lambda *args, **kwargs: None)
    monkeypatch.setattr(checkpoint, "_current_recovery_terminal_exists", lambda *args, **kwargs: current_recovery_terminal)
    monkeypatch.setattr(checkpoint, "_has_rejected_semantic_rescue_candidate", lambda *args, **kwargs: rejected_rescue)
    monkeypatch.setattr(checkpoint, "load_stage", lambda *args, **kwargs: None)


def test_historical_human_terminal_does_not_veto_current_core_clean_head(monkeypatch, tmp_path):
    _wire(monkeypatch)

    decision = checkpoint.canonical_production_disposition(_outcome(), checkpoint_dir=tmp_path)

    assert decision["disposition"] == "CONTINUE"
    assert decision["reason"] == "CURRENT_HEAD_CORE_QA_SUPERSEDES_HISTORICAL_TERMINAL"


def test_current_recovery_terminal_is_not_suppressed(monkeypatch, tmp_path):
    _wire(monkeypatch, current_recovery_terminal=True)
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: False)

    decision = checkpoint.canonical_production_disposition(_outcome(), checkpoint_dir=tmp_path)

    assert decision["disposition"] == "HARD_STOP"
    assert decision["reason"] == "CURRENT_HEAD_MATERIAL_DEFECT_RECOVERY_EXHAUSTED"


def test_rejected_rescue_remains_existing_current_recovery(monkeypatch, tmp_path):
    _wire(monkeypatch, rejected_rescue=True)

    decision = checkpoint.canonical_production_disposition(_outcome(), checkpoint_dir=tmp_path)

    assert decision["disposition"] == "RECOVERY_REQUIRED"
    assert decision["reason"] == "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR"


def test_later_exact_current_accept_precedes_historical_debt(monkeypatch, tmp_path):
    _wire(monkeypatch)
    monkeypatch.setattr(checkpoint, "_current_exact_terminal_accept", lambda *args, **kwargs: True)

    decision = checkpoint.canonical_production_disposition(_outcome(), checkpoint_dir=tmp_path)

    assert decision["disposition"] == "CONTINUE"
    assert decision["reason"] == "CURRENT_HEAD_ACCEPTED"


def test_old_request_debt_cannot_become_current_debt(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    monkeypatch.setattr(checkpoint, "_accepted_candidate_mutation_records", lambda *args: [{
        "display_id": outcome.request.display_id,
        "unit_uid": outcome.request.unit_uid,
        "source_sha256": outcome.request.source_sha256,
        "request_binding_sha256": "stale-request",
        "candidate_binding_sha256": checkpoint._candidate_binding(outcome.raw),
        "production_candidate_disposition": "DEFERRED_SEMANTIC_DEBT",
        "accepted": True,
    }])
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: False)

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] != "DEFERRED_SEMANTIC_DEBT"


def test_exact_current_debt_without_later_accept_remains_debt(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    expected = checkpoint.enrich_request(outcome.request, translation_bible=None).request_binding_sha256
    monkeypatch.setattr(checkpoint, "_accepted_candidate_mutation_records", lambda *args: [{
        "display_id": outcome.request.display_id,
        "unit_uid": outcome.request.unit_uid,
        "source_sha256": outcome.request.source_sha256,
        "request_binding_sha256": expected,
        "candidate_binding_sha256": checkpoint._candidate_binding(outcome.raw),
        "production_candidate_disposition": "DEFERRED_SEMANTIC_DEBT",
        "accepted": True,
    }])

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"


def test_current_integrity_failure_precedes_any_debt(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    outcome.qa = {"passed": False, "diagnostics": [{"code": "INTEGRITY"}]}
    monkeypatch.setattr(checkpoint, "_current_exact_terminal_accept", lambda *args, **kwargs: False)
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: True)

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "HARD_STOP"


def test_old_head_debt_is_historical_after_head_changes(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    monkeypatch.setattr(checkpoint, "_accepted_candidate_mutation_records", lambda *args: [{
        "display_id": outcome.request.display_id,
        "unit_uid": outcome.request.unit_uid,
        "source_sha256": outcome.request.source_sha256,
        "request_binding_sha256": checkpoint.enrich_request(outcome.request, translation_bible=None).request_binding_sha256,
        "candidate_binding_sha256": "0" * 64,
        "production_candidate_disposition": "DEFERRED_SEMANTIC_DEBT",
        "accepted": True,
    }])
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: False)

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] != "DEFERRED_SEMANTIC_DEBT"


def test_existing_current_semantic_debt_path_remains_available(monkeypatch, tmp_path):
    _wire(monkeypatch)
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: True)
    outcome = _outcome()
    outcome.semantic_terminal = None
    outcome.final_status = "PENDING"
    outcome.human_required = False

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"


def test_fastpath_accept_is_not_a_semantic_rescue_trigger(monkeypatch, tmp_path):
    outcome = _outcome()
    accepted = SimpleNamespace(verdict="ACCEPT", issues=[])
    monkeypatch.setattr(checkpoint, "_current_head_rereview", lambda *args, **kwargs: accepted)

    assert checkpoint._current_semantic_rescue_repair_trigger(
        outcome, checkpoint_dir=tmp_path, current=outcome.raw,
    ) is None


def test_fastpath_missing_review_is_not_a_semantic_rescue_trigger(monkeypatch, tmp_path):
    outcome = _outcome()
    monkeypatch.setattr(checkpoint, "_current_head_rereview", lambda *args, **kwargs: None)

    assert checkpoint._current_semantic_rescue_repair_trigger(
        outcome, checkpoint_dir=tmp_path, current=outcome.raw,
    ) is None


def test_exact_current_repair_is_a_semantic_rescue_trigger(monkeypatch, tmp_path):
    outcome = _outcome()
    repair = SimpleNamespace(
        verdict="REPAIR",
        issues=[SimpleNamespace(code="SEMANTIC_FIDELITY", affected_segment_indices=[0], repair_instruction="repair")],
    )
    monkeypatch.setattr(checkpoint, "_current_head_rereview", lambda *args, **kwargs: repair)
    monkeypatch.setattr(checkpoint, "review_issue_severities", lambda codes: {checkpoint.BLOCKING_SEMANTIC})

    assert checkpoint._current_semantic_rescue_repair_trigger(
        outcome, checkpoint_dir=tmp_path, current=outcome.raw,
    ) is repair


def test_synthetic_rescue_without_current_repair_is_historical_only(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    rescue = {
        "candidate_binding_sha256": checkpoint._candidate_binding(outcome.raw),
        "repair_applied": False,
        "review": None,
        "second_review": None,
    }
    monkeypatch.setattr(
        checkpoint,
        "load_stage",
        lambda _dir, stage, _id: rescue if stage == "semantic_rescue" else None,
    )

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "CONTINUE"
    assert decision["reason"] == "CURRENT_HEAD_SYNTHETIC_RESCUE_HISTORICAL_ONLY"


def test_real_current_recovery_terminal_remains_fail_closed(monkeypatch, tmp_path):
    _wire(monkeypatch, current_recovery_terminal=True)
    monkeypatch.setattr(checkpoint, "_synthetic_semantic_rescue_is_historical_only", lambda *args, **kwargs: False)
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: False)

    decision = checkpoint.canonical_production_disposition(_outcome(), checkpoint_dir=tmp_path)

    assert decision["disposition"] == "HARD_STOP"


def test_current_direct_reviewer_accept_is_a_success_terminal(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    outcome.semantic_terminal = None
    outcome.final_status = "PENDING"
    outcome.review = SimpleNamespace(verdict="ACCEPT", issues=[])

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "CONTINUE"
    assert decision["reason"] == "CURRENT_HEAD_ACCEPTED"


def test_direct_core_pass_without_review_is_a_success_terminal(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    outcome.semantic_terminal = None
    outcome.final_status = "PENDING"
    outcome.human_required = False
    outcome.review = None
    outcome.reviewer_dispatch = "DIRECT_CORE_PASS"

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "CONTINUE"


def test_missing_review_in_non_direct_dispatch_is_not_accepted(monkeypatch, tmp_path):
    _wire(monkeypatch)
    outcome = _outcome()
    outcome.semantic_terminal = None
    outcome.final_status = "PENDING"
    outcome.human_required = False
    outcome.review = None
    outcome.reviewer_dispatch = "SAMPLED_QUALITY_AUDIT"
    monkeypatch.setattr(checkpoint, "_safe_current_head_debt", lambda *args, **kwargs: False)

    decision = checkpoint.canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "HARD_STOP"
