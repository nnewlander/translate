from __future__ import annotations

import json
from hashlib import sha256
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult, VerifiedFootnoteNumericBinding, VerifiedFootnoteSourceSegment
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import ReviewIssue, ReviewResult, ReviewerProviderResult
from booktrans.providers.provenance import persist_provenance_records
from booktrans.providers.translation_provider import (
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationCandidate,
    TranslationProviderRequest,
)
from booktrans.production_worker.canary import evaluate_canary_gate, run_isolated_canary
from booktrans.production_worker.artifacts import write_json_once
from booktrans.production_worker.pipeline import WorkerProviders, process_unit
from booktrans.production_worker.selection import FrozenSelectionError, assert_not_production_checkpoint
from booktrans.production_worker.unit_store import persist_stage_once, persist_unit_result, unit_path, write_jsonl_once
from booktrans.harness.translation_artifacts import persist_raw_response
from booktrans.production_worker.production_checkpoint import (
    canonical_production_disposition,
    _candidate_binding,
    _corrective_semantic_repair_is_eligible,
    _current_recovery_terminal_exists,
    _only_missing_verified_d050_markers,
    _optional_review_result,
    _rejected_repair_final_is_not_current_head,
    _translate_with_bounded_provider_contract_retry,
)
from booktrans.production_worker.pipeline import enrich_request
from booktrans.providers.router import route_semantic_unit


def _request(*, unit: str = "unit-1", display: str = "body.p.00001", texts: tuple[str, ...] = ("A unit test improves quality.",)) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid=unit,
        display_id=display,
        story="body",
        content_role="body_paragraph",
        source_text=" ".join(texts),
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text=text, anchor_ref=f"a{index}", island_ref="island.001")
            for index, text in enumerate(texts)
        ],
    )


def _candidate(request: CalibrationRequest, text: str = "译文") -> TranslationCandidate:
    now = datetime.now(timezone.utc).isoformat()
    return TranslationCandidate(
        provider="FAKE",
        model="fake",
        request_binding_sha256=TranslationProviderRequest(request=request).request_binding_sha256,
        translation_options_sha256=TranslationProviderRequest(request=request).translation_options_sha256,
        source_sha256=request.source_sha256,
        unit_uid=request.unit_uid,
        display_id=request.display_id,
        response_text=text,
        requested_at=now,
        responded_at=now,
        usage={"total_tokens": 4},
        attempt_count=1,
    )


def _raw(request: CalibrationRequest, texts: tuple[str, ...] | None = None) -> TranslationResult:
    values = texts or tuple("译" + str(index) for index in range(len(request.source_segments)))
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment, text in zip(request.source_segments, values, strict=True)
        ],
    )


def _review(request: CalibrationRequest, verdict: str = "ACCEPT", issues: list[ReviewIssue] | None = None) -> ReviewResult:
    return ReviewResult(schema_version="booktrans.structured-review.v1", unit_uid=request.unit_uid, verdict=verdict, issues=issues or [])


def test_corrective_semantic_repair_requires_the_original_authoritative_scope() -> None:
    request = _request(texts=("first", "second", "third"))
    original = {"contract": {"permitted_segment_indices": [1, 2]}}
    inside = _review(request, "REPAIR", [ReviewIssue(code="SEGMENT_MISALIGNMENT_DUPLICATION", affected_segment_indices=[1, 2], reason="copied clause", repair_instruction="repair local ownership")])
    outside = _review(request, "REPAIR", [ReviewIssue(code="SEGMENT_MISALIGNMENT_DUPLICATION", affected_segment_indices=[0, 1], reason="would expand", repair_instruction="repair")])
    assert _corrective_semantic_repair_is_eligible(original, inside)
    assert not _corrective_semantic_repair_is_eligible(original, outside)


def test_current_rereview_scope_overrides_rejected_repair_history() -> None:
    """A current-head corrective repair uses only the current rereview indices."""
    from booktrans.production_worker.production_checkpoint import _current_rereview_corrective_scope

    request = _request(unit="rereview-scope", texts=("zero", "one", "two", "three", "four"))
    candidate = _raw(request)
    group = SimpleNamespace(
        member_work_order_ids=(request.display_id,),
        repairable_work_order_ids=(request.display_id,),
    )
    # The old rejected candidate had scope [2]; its scope must never leak.
    assert _current_rereview_corrective_scope(
        request=request, candidate=candidate,
        review=_review(request, "REPAIR", [ReviewIssue(
            code="OMISSION", affected_segment_indices=[4], reason="current", repair_instruction="repair 4",
        )]), group=group,
    ) == (4,)
    assert _current_rereview_corrective_scope(
        request=request, candidate=candidate,
        review=_review(request, "REPAIR", [ReviewIssue(
            code="OMISSION", affected_segment_indices=[0], reason="current", repair_instruction="repair 0",
        )]), group=group,
    ) == (0,)
    assert _current_rereview_corrective_scope(
        request=request, candidate=candidate,
        review=_review(request, "REPAIR", [ReviewIssue(
            code="OMISSION", affected_segment_indices=[1, 3], reason="current", repair_instruction="repair both",
        )]), group=group,
    ) == (1, 3)
    assert _current_rereview_corrective_scope(
        request=request, candidate=candidate,
        review=_review(request, "REPAIR", [ReviewIssue(
            code="OMISSION", affected_segment_indices=[5], reason="outside", repair_instruction="invalid",
        )]), group=group,
    ) is None


def test_current_head_corrective_review_reads_exact_bound_repair_scope(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _current_head_corrective_review
    from booktrans.production_worker.unit_store import persist_stage_once

    request = _request(unit="current-head-scope", display="body.p.08815", texts=("term", "64", "codelabs", "60"))
    current = _raw(request, ("术语", "64", "代码实验室", "60"))
    binding = enrich_request(request, translation_bible=None).request_binding_sha256
    review = _review(request, "REPAIR", [ReviewIssue(code="SEMANTIC_MISMATCH", affected_segment_indices=[2], reason="name", repair_instruction="Use Codelabs.")])
    persist_stage_once(tmp_path, "semantic_rescue_corrective_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(current), "request_binding_sha256": binding,
        "review": review.model_dump(),
    })
    resolved = _current_head_corrective_review(checkpoint_dir=tmp_path, request=request, candidate_sha256=_candidate_binding(current), bible=None)
    assert resolved is not None
    assert resolved.issues[0].affected_segment_indices == [2]


def test_current_head_rereview_projects_bound_corrective_review_scope(tmp_path: Path) -> None:
    """Canonical recovery must not erase a current corrective REPAIR scope."""
    from booktrans.production_worker.production_checkpoint import _current_candidate_rereview
    from booktrans.production_worker.unit_store import persist_stage_once

    request = _request(unit="current-projection", display="body.p.08815", texts=("term", "64", "codelabs", "60"))
    current = _raw(request, ("术语", "64", "代码实验室", "60"))
    candidate_sha = _candidate_binding(current)
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[2], reason="name", repair_instruction="Use Codelabs.",
    )])
    context = "c" * 64
    group = "g" * 64
    persist_stage_once(tmp_path, "semantic_rescue_corrective_final_review_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": candidate_sha, "context_sha256": context, "semantic_group_hash": group,
    })
    persist_stage_once(tmp_path, "semantic_rescue_corrective_final_reviews", request.display_id, {
        "candidate_binding_sha256": candidate_sha, "context_sha256": context, "semantic_group_hash": group,
        "review": review.model_dump(),
    })
    resolved = _current_candidate_rereview(checkpoint_dir=tmp_path, request=request, current=current)
    assert resolved is not None
    assert [index for issue in resolved.issues for index in issue.affected_segment_indices] == [2]


def test_selected_current_recovery_gateway_uses_exact_canonical_contract() -> None:
    """Legacy rescue artifacts are irrelevant once projection selects a head."""
    from booktrans.production_worker.production_checkpoint import (
        _canonical_sha256,
        _selected_current_recovery_execution_family,
    )

    request = _request(unit="gateway", display="body.p.08814", texts=("term", "64"))
    current = _raw(request, ("术语", "64"))
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0], reason="term", repair_instruction="Use the canonical term.",
    )])
    obligation = {
        "recovery_family": "SEMANTIC_CORRECTIVE",
        "failing_work_order_ids": [request.display_id],
        "recovery_fingerprint": {
            "work_order": request.display_id,
            "current_head_sha256": _candidate_binding(current),
            "recovery_family": "SEMANTIC_CORRECTIVE",
            "bound_review_result_sha256": _canonical_sha256(review.model_dump()),
            "affected_segment_indices": [0],
            "recovery_budget_used": 0,
        },
    }
    assert _selected_current_recovery_execution_family(
        obligation, request=request, candidate_sha256=_candidate_binding(current), review=review,
    ) == "SEMANTIC_CORRECTIVE"
    # A changed head is stale: no legacy fallback can make it executable.
    assert _selected_current_recovery_execution_family(
        obligation, request=request, candidate_sha256="f" * 64, review=review,
    ) is None
    rereview = {
        "recovery_family": "SEMANTIC_REREVIEW",
        "failing_work_order_ids": [request.display_id],
        "recovery_fingerprint": {
            "work_order": request.display_id,
            "current_head_sha256": _candidate_binding(current),
            "recovery_family": "SEMANTIC_REREVIEW",
            "bound_review_result_sha256": None,
            "affected_segment_indices": [],
            "recovery_budget_used": 0,
        },
    }
    assert _selected_current_recovery_execution_family(
        rereview, request=request, candidate_sha256=_candidate_binding(current), review=None,
    ) == "SEMANTIC_REREVIEW"


def test_current_corrective_reuses_repair_and_spends_one_final_review_protocol_retry(tmp_path: Path) -> None:
    """A failed final review retries the reviewer, never the valid Repair."""
    from booktrans.production_worker.production_checkpoint import _run_semantic_rescue

    request = _request(unit="final-review-retry", display="body.p.08813", texts=("term",))
    raw = _raw(request, ("术语",))
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0], reason="term", repair_instruction="Use the canonical term.",
    )])
    repair = FakeRepair(text="规范术语")
    reviewer = ContractGapReviewer([
        "GAP:REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING", "ACCEPT",
    ])
    kwargs = dict(
        checkpoint_dir=tmp_path, request=request, base=raw,
        active=_providers(reviewer=reviewer, repair=repair), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None,
        semantic_context=None, group=None, prior={"review": review.model_dump()},
        stage_prefix="semantic_rescue_current_head_corrective", authority_raw=raw,
    )
    first = _run_semantic_rescue(**kwargs)
    assert first.failure_code == "REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING"
    assert repair.calls == 1 and reviewer.calls == 1
    second = _run_semantic_rescue(**kwargs)
    assert second.final_status == "ACCEPT"
    assert repair.calls == 1 and reviewer.calls == 2
    assert (tmp_path / "semantic_rescue_current_head_corrective_final_reviews" / f"{request.display_id}.json").is_file()


def test_reviewer_prose_cannot_expand_structured_semantic_scope() -> None:
    from booktrans.production_worker.production_checkpoint import _review_scope_contract_error

    request = _request(unit="scope-contract", texts=("term", "64", "codelabs", "60"))
    invalid = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[2], reason="link",
        repair_instruction="Merge segments 1 and 2 so the number modifies the term.",
    )])
    error = _review_scope_contract_error(invalid)
    assert error is not None
    assert error.code == "REVIEWER_CONTRACT_GAP_SCOPE_INCONSISTENT"
    valid = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[2], reason="term",
        repair_instruction="Change segment 2 to the canonical term.",
    )])
    assert _review_scope_contract_error(valid) is None


def test_current_head_corrective_attempt_is_bound_to_parent_review_and_scope(tmp_path: Path) -> None:
    """Old-head rescue evidence cannot suppress a child repair for a new head."""
    from booktrans.production_worker.production_checkpoint import _current_head_corrective_attempt_matches

    request = _request(unit="current-child", display="body.p.08815", texts=("term", "64", "codelabs", "60"))
    current = _raw(request, ("术语", "64", "代码实验室", "60"))
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[2], reason="name", repair_instruction="Use Codelabs.",
    )])
    binding = enrich_request(request, translation_bible=None).request_binding_sha256
    # This represents the immutable historical rescue. It deliberately lives
    # in its legacy lane and therefore cannot be a child-attempt match.
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {"parent_candidate_sha256": "e" * 64})
    assert not _current_head_corrective_attempt_matches(
        checkpoint_dir=tmp_path, request=request, parent_candidate_sha256=_candidate_binding(current),
        review=review, scope=(2,), bible=None,
    )
    persist_stage_once(tmp_path, "semantic_rescue_current_head_corrective", request.display_id, {
        "parent_candidate_sha256": _candidate_binding(current),
        "request_binding_sha256": binding,
        "review_result_sha256": sha256(json.dumps(review.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "required_repair_scope": [2],
    })
    assert _current_head_corrective_attempt_matches(
        checkpoint_dir=tmp_path, request=request, parent_candidate_sha256=_candidate_binding(current),
        review=review, scope=(2,), bible=None,
    )
    assert not _current_head_corrective_attempt_matches(
        checkpoint_dir=tmp_path, request=request, parent_candidate_sha256=_candidate_binding(current),
        review=review, scope=(1,), bible=None,
    )


def test_current_head_corrective_child_final_accept_is_current_authority(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _current_candidate_reviewer_accept

    request = _request(unit="current-child-accept", display="body.p.08816")
    candidate = _raw(request)
    candidate_sha = _candidate_binding(candidate)
    persist_stage_once(tmp_path, "semantic_rescue_current_head_corrective_final_reviews", request.display_id, {
        "candidate_binding_sha256": candidate_sha,
        "request_binding_sha256": enrich_request(request, translation_bible=None).request_binding_sha256,
        "review": _review(request, "ACCEPT").model_dump(),
    })
    accepted = _current_candidate_reviewer_accept(
        checkpoint_dir=tmp_path, request=request, candidate_sha256=candidate_sha, bible=None,
    )
    assert accepted is not None
    assert accepted["stage"] == "semantic_rescue_current_head_corrective_final_reviews"


def test_current_head_corrective_protocol_retry_accept_is_current_authority(tmp_path: Path) -> None:
    """A bound ACCEPT from the one-shot contract retry certifies this child."""
    from booktrans.production_worker.production_checkpoint import _current_candidate_reviewer_accept

    request = _request(unit="current-child-retry-accept", display="body.p.08815")
    candidate = _raw(request)
    candidate_sha = _candidate_binding(candidate)
    persist_stage_once(tmp_path, "semantic_rescue_current_head_corrective_final_review_retries", request.display_id, {
        "candidate_binding_sha256": candidate_sha,
        "request_binding_sha256": enrich_request(request, translation_bible=None).request_binding_sha256,
        "review": _review(request, "ACCEPT").model_dump(),
    })
    accepted = _current_candidate_reviewer_accept(
        checkpoint_dir=tmp_path, request=request, candidate_sha256=candidate_sha, bible=None,
    )
    assert accepted is not None
    assert accepted["stage"] == "semantic_rescue_current_head_corrective_final_review_retries"


def test_follow_up_corrective_round_keeps_prepared_drafts_off_ca_and_publishes_only_final(tmp_path: Path) -> None:
    """Round two may repair a Core-clean draft, but only its ACCEPTed result commits."""
    from booktrans.production_worker.production_checkpoint import (
        CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX,
        _commit_accepted_candidate_mutation,
        _resolve_checkpoint_authoritative_candidate,
        _run_semantic_rescue,
    )

    request = _request(
        unit="round-two", display="body.p.08830",
        texts=("first semantic term, ", "373 ", "untranslated source term, ", "240"),
    )
    raw = _raw(request, ("原始一，", "373 ", "原始二，", "240"))
    current = _raw(request, ("第一项，", "373 ", "untranslated source term, ", "240"))
    prepared_round_one = _raw(request, ("已修第一项，", "373 ", "untranslated source term, ", "240"))
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(current),
        "source_sha256": request.source_sha256, "selection_sha256": "b" * 64,
        "request_binding_sha256": request_sha, "working": current.model_dump(),
        "qa": _qa_pass(None, request, current, {request.unit_uid}), "accepted": True,
    })
    follow_up = _review(request, "REPAIR", [ReviewIssue(
        code="UNTRANSLATED_CONTENT", affected_segment_indices=[2], reason="literal source remains",
        repair_instruction="Translate segment 2 only.",
    )])
    repair = FakeRepair(text="已翻译术语，")
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    kwargs = dict(
        checkpoint_dir=tmp_path, request=request, base=prepared_round_one,
        active=_providers(repair=repair, reviewer=reviewer), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None, semantic_context=None,
        group=None, prior={"review": follow_up.model_dump()},
        stage_prefix=CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX,
        corrective_diagnosis="literal source remains", required_repair_scope=(2,), authority_raw=raw,
    )
    outcome = _run_semantic_rescue(**kwargs)
    assert outcome.final_status == "ACCEPT"
    assert tuple(segment.text for segment in prepared_round_one.target_segments) == (
        "已修第一项，", "373 ", "untranslated source term, ", "240",
    )
    assert tuple(segment.text for segment in outcome.working.target_segments) == (
        "已修第一项，", "373 ", "已翻译术语，", "240",
    )
    assert _candidate_binding(outcome.raw) == _candidate_binding(raw)
    assert _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw).candidate_sha256 == _candidate_binding(current)
    _commit_accepted_candidate_mutation(
        checkpoint_dir=tmp_path, request=request, raw=raw, outcome=outcome,
        source_sha256=request.source_sha256, selection_sha256="b" * 64, bible=None,
    )
    resolved = _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw)
    assert resolved.candidate_sha256 == _candidate_binding(outcome.working)
    assert resolved.lineage[-1]["status"] == "ACCEPTED_HEAD"
    # Durable stage evidence makes the exact same second round idempotent.
    replay = _run_semantic_rescue(**kwargs)
    assert replay.final_status == "ACCEPT"
    assert repair.calls == 1 and len(reviewer.seen) == 1


def test_exhausted_second_corrective_round_defers_only_bound_fidelity_debt(tmp_path: Path) -> None:
    """A rejected prepared round two is debt, never a hidden CA publication."""
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.production_worker.production_checkpoint import (
        CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX, _persist_deferred_semantic_debt,
        canonical_production_disposition,
    )

    request = _request(unit="round-two-debt", display="body.p.08830", texts=("compilers",))
    raw = _raw(request, ("原始编译器",))
    current = _raw(request, ("编译器",))
    prepared = _raw(request, ("更快的编译器",))
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0],
        reason="unsupported local addition", repair_instruction="Remove the modifier.",
    )])
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    group_sha = "g" * 64
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(current),
        "working": current.model_dump(), "qa": {"passed": True}, "accepted": True,
    })
    persist_stage_once(tmp_path, CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX, request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "round": 2, "authoritative_parent_candidate_sha256": _candidate_binding(current),
        "request_binding_sha256": request_sha, "semantic_group_hash": group_sha,
        "working": prepared.model_dump(),
    })
    persist_stage_once(tmp_path, f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(prepared),
        "request_binding_sha256": request_sha, "semantic_group_hash": group_sha,
        "review": review.model_dump(),
    })
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=prepared, qa={"passed": True})
    outcome.second_review = review; outcome.human_required = True
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    assert decision["reason"] == "EXHAUSTED_CONTAINED_SEMANTIC_FIDELITY_DEBT"
    debt = _persist_deferred_semantic_debt(checkpoint_dir=tmp_path, item=outcome, bible=None)
    assert debt["release_gate"] == "BLOCKING_UNTIL_BATCH_DEBT_RESOLUTION"
    assert debt["candidate_binding_sha256"] == _candidate_binding(current)
    assert debt["prepared_candidate_binding_sha256"] == _candidate_binding(prepared)

    untranslated = _review(request, "REPAIR", [ReviewIssue(
        code="UNTRANSLATED_CONTENT", affected_segment_indices=[0],
        reason="source content is missing", repair_instruction="Translate it.",
    )])
    # Replace only the test fixture's final evidence, not a production artifact.
    second = tmp_path / f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews" / f"{request.display_id}.json"
    second.unlink()
    persist_stage_once(tmp_path, f"{CURRENT_HEAD_CORRECTIVE_ROUND_2_PREFIX}_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(prepared),
        "request_binding_sha256": request_sha, "semantic_group_hash": group_sha,
        "review": untranslated.model_dump(),
    })
    outcome.second_review = untranslated
    assert canonical_production_disposition(outcome, checkpoint_dir=tmp_path)["disposition"] == "HARD_STOP"


def test_bound_human_terminal_reconciles_historical_raw_qa_for_its_current_head(tmp_path: Path) -> None:
    """A valid human terminal is current semantic authority, not stale RAW QA."""
    from booktrans.production_worker.production_checkpoint import _current_candidate_reviewer_accept, resolve_effective_qa

    request = _request(unit="human-current", display="body.p.08011")
    candidate = _raw(request, ("2",))
    candidate_sha = _candidate_binding(candidate)
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    persist_stage_once(tmp_path, "human_approved_candidates", request.display_id, {
        "artifact_type": "PRODUCTION_HUMAN_APPROVED_CANDIDATE",
        "schema_version": 2,
        "current_display_id": request.display_id,
        "semantic_terminal": "HUMAN_APPROVED",
        "candidate_binding_sha256": candidate_sha,
        "parent_candidate_sha256": "f" * 64,
        "source_sha256": request.source_sha256,
        "request_binding_sha256": request_sha,
        "working": candidate.model_dump(),
    })
    accepted = _current_candidate_reviewer_accept(
        checkpoint_dir=tmp_path, request=request, candidate_sha256=candidate_sha, bible=None,
    )
    assert accepted is not None
    assert accepted["stage"] == "human_approved_candidates"
    assert accepted["semantic_terminal"] == "HUMAN_APPROVED"
    assert _current_candidate_reviewer_accept(
        checkpoint_dir=tmp_path, request=request, candidate_sha256="0" * 64, bible=None,
    ) is None
    original_qa = tmp_path / "translation_raw_qa_report.json"
    original_qa.write_text("{}", encoding="utf-8")
    effective = resolve_effective_qa(
        checkpoint_dir=tmp_path, request=request, raw=candidate,
        current_head=SimpleNamespace(candidate_sha256=candidate_sha, lineage=[]),
        historical_qa_record={"passed": False}, current_qa={"passed": True},
        original_qa_path=original_qa, bible=None,
    )
    assert effective["state"] == "PASS"
    assert effective["reason"] == "HUMAN_APPROVED_CURRENT_HEAD_CORE_QA_PASS"


def test_current_raw_head_uses_current_deterministic_qa_after_normalizer_hardening(tmp_path: Path) -> None:
    """An immutable old RAW diagnostic cannot outvote QA for the same RAW head."""
    from booktrans.production_worker.production_checkpoint import resolve_effective_qa

    request = _request(unit="raw-current-qa", display="body.p.080111")
    raw = _raw(request, ("两万次",))
    raw_qa_path = tmp_path / "translation_raw_qa_report.json"
    raw_qa_path.write_text("{}", encoding="utf-8")

    effective = resolve_effective_qa(
        checkpoint_dir=tmp_path,
        request=request,
        raw=raw,
        current_head=SimpleNamespace(candidate_sha256=_candidate_binding(raw), lineage=[]),
        historical_qa_record={"passed": False},
        current_qa={"passed": True},
        original_qa_path=raw_qa_path,
        bible=None,
    )

    assert effective["state"] == "PASS"
    assert effective["reason"] == "CURRENT_RAW_HEAD_EFFECTIVE_QA_PASS"


def test_corrective_final_accept_is_current_head_qa_reconciliation_authority(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _current_candidate_reviewer_accept

    request = _request(unit="corrective-current", display="body.p.08012")
    candidate = _raw(request, ("已修复",))
    candidate_sha = _candidate_binding(candidate)
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    persist_stage_once(tmp_path, "semantic_rescue_corrective_final_reviews", request.display_id, {
        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE_FINAL_REVIEW",
        "candidate_binding_sha256": candidate_sha,
        "request_binding_sha256": request_sha,
        "review": _review(request, "ACCEPT").model_dump(),
    })
    accepted = _current_candidate_reviewer_accept(
        checkpoint_dir=tmp_path, request=request, candidate_sha256=candidate_sha, bible=None,
    )
    assert accepted is not None
    assert accepted["stage"] == "semantic_rescue_corrective_final_reviews"


def test_blocked_handoff_echoes_explicit_current_batch(tmp_path: Path) -> None:
    """A resolved B025 context must never serialize the B022 fallback label."""
    from booktrans.production_worker.production_checkpoint import _blocked

    handoff = tmp_path / "handoff.json"
    assert _blocked(
        handoff, "worker-b025", checkpoint="checkpoint_07",
        classification="RAW_QA_IMMUTABLE_MISMATCH", active_batch="FULL_BOOK_BATCH_025",
    ) == 2
    payload = json.loads(handoff.read_text(encoding="utf-8"))
    assert payload["active_batch"] == "FULL_BOOK_BATCH_025"
    assert payload["checkpoint"] == "checkpoint_07"


def test_stale_rereview_cannot_supply_corrective_scope(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _current_candidate_rereview

    request = _request(unit="stale-rereview", display="body.p.000011")
    raw = _raw(request, ("旧候选",))
    current = _raw(request, ("当前候选",))
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(raw), "context_sha256": "context", "semantic_group_hash": "group",
    })
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "context_sha256": "context", "semantic_group_hash": "group",
        "review": _review(request, "REPAIR", [ReviewIssue(
            code="OMISSION", affected_segment_indices=[0], reason="stale", repair_instruction="repair",
        )]).model_dump(),
    })
    assert _current_candidate_rereview(checkpoint_dir=tmp_path, request=request, current=current) is None


def test_rejected_repair_final_review_is_not_semantic_authority_for_the_raw_head(tmp_path: Path) -> None:
    request = _request(unit="current", display="body.p.00002")
    raw = _raw(request, ("当前候选",))
    persist_stage_once(tmp_path, "semantic_rescue_final_reviews", request.display_id, {
        "candidate_binding_sha256": "f" * 64,
        "review": _review(request, "REPAIR", [ReviewIssue(code="SEMANTIC_OMISSION", affected_segment_indices=[0], reason="historical", repair_instruction="repair")]).model_dump(),
    })
    assert _rejected_repair_final_is_not_current_head(tmp_path, request.display_id, raw)


def test_rejected_repair_final_is_compared_to_the_resolved_authority_head(tmp_path: Path) -> None:
    """A later accepted mutation, not RAW, is the rereview subject on resume."""
    request = _request(unit="resolved-current", display="body.p.000021")
    raw = _raw(request, ("旧候选",))
    current = _raw(request, ("已验收的当前候选",))
    persist_stage_once(tmp_path, "semantic_rescue_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw),
        "review": _review(request, "REPAIR", [ReviewIssue(
            code="SEMANTIC_OMISSION", affected_segment_indices=[0],
            reason="historical candidate", repair_instruction="repair",
        )]).model_dump(),
    })
    assert not _rejected_repair_final_is_not_current_head(tmp_path, request.display_id, raw)
    assert _rejected_repair_final_is_not_current_head(tmp_path, request.display_id, current)


def test_rejected_rescue_candidate_requests_current_head_rereview_not_hard_stop(tmp_path: Path) -> None:
    """A rejected repair is history; RAW remains the authority to replay."""
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.production_worker.production_checkpoint import _candidate_binding

    request = _request(unit="current-rereview", display="body.p.00003")
    raw = _raw(request, ("原始候选",))
    rejected = _raw(request, ("被拒绝的修复候选",))
    issue = ReviewIssue(
        code="SEMANTIC_OMISSION", affected_segment_indices=[0],
        reason="current repair required", repair_instruction="restore the clause",
    )
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "semantic_terminal": "HUMAN_REQUIRED",
        "working": rejected.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_repairs", request.display_id, {
        "authority_parent_verified": True, "parent_candidate_sha256": _candidate_binding(raw),
        "working": rejected.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_eligibility", request.display_id, {
        "eligible": True,
    })
    persist_stage_once(tmp_path, "semantic_rescue_completeness", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(rejected), "passed": True,
    })
    persist_stage_once(tmp_path, "semantic_rescue_final_reviews", request.display_id, {
        "review": _review(request, "REPAIR", [issue]).model_dump(),
    })
    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw,
        working=rejected, qa={"passed": True},
    )
    outcome.review = _review(request, "REPAIR", [issue])
    outcome.second_review = _review(request, "REPAIR", [issue])
    outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True

    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    assert decision["disposition"] == "RECOVERY_REQUIRED"
    assert decision["reason"] == "CURRENT_HEAD_REREVIEW_REQUIRED_AFTER_REJECTED_REPAIR"


def test_current_head_accept_rereview_precedes_rejected_repair_history(tmp_path: Path) -> None:
    """A durable ACCEPT rereview prevents a resume-loop recovery request."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="accepted-rereview", display="body.p.000030")
    raw = _raw(request, ("历史 RAW",))
    current = _raw(request, ("当前 authority head",))
    issue = ReviewIssue(
        code="SEMANTIC_OMISSION", affected_segment_indices=[0],
        reason="rejected repair only", repair_instruction="repair",
    )
    binding = enrich_request(request, translation_bible=None).request_binding_sha256
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(current),
        "source_sha256": request.source_sha256, "selection_sha256": "b" * 64,
        "request_binding_sha256": binding, "working": current.model_dump(),
        "qa": {"passed": True}, "semantic_terminal": "MODEL_ACCEPTED", "accepted": True,
    })
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "working": raw.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_repairs", request.display_id, {
        "authority_parent_verified": True, "parent_candidate_sha256": _candidate_binding(raw),
        "working": raw.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_completeness", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(raw), "passed": True,
    })
    persist_stage_once(tmp_path, "semantic_rescue_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "review": _review(request, "REPAIR", [issue]).model_dump(),
    })
    rereview_request = {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(current),
        "context_sha256": "c" * 64, "semantic_group_hash": "d" * 64,
    }
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, rereview_request)
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {
        **rereview_request, "review": _review(request, "ACCEPT").model_dump(),
    })
    # Model the historical resume projection: its working candidate is RAW,
    # while Candidate Authority has already selected ``current``.
    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw,
        working=raw, qa={"passed": True},
    )
    outcome.final_status = "HUMAN_REQUIRED"; outcome.human_required = True
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    assert decision["disposition"] == "CONTINUE"
    assert decision["reason"] == "CURRENT_HEAD_ACCEPTED"


def test_accepted_mutation_descendant_keeps_one_authoritative_lineage(tmp_path: Path) -> None:
    """A corrective release candidate appends below, never overwrites, a head."""
    from booktrans.production_worker.production_checkpoint import _candidate_binding, _resolve_checkpoint_authoritative_candidate

    request = _request(unit="descendant", display="body.p.09991")
    raw, head_a, head_b = _raw(request, ("RAW",)), _raw(request, ("A",)), _raw(request, ("B",))
    binding = enrich_request(request, translation_bible=None).request_binding_sha256
    base = {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256, "selection_sha256": "b" * 64,
        "request_binding_sha256": binding, "qa": {"passed": True},
        "semantic_terminal": "MODEL_ACCEPTED", "accepted": True,
    }
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, {
        **base, "mutation_kind": "NUMERIC_REPRESENTATION",
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(head_a), "working": head_a.model_dump(),
    })
    descendant = tmp_path / "accepted_candidate_mutations" / request.display_id / f"{_candidate_binding(head_b)}.json"
    write_json_once(descendant, {
        **base, "mutation_kind": "RELEASE_BOUNDARY",
        "parent_candidate_sha256": _candidate_binding(head_a),
        "candidate_binding_sha256": _candidate_binding(head_b), "working": head_b.model_dump(),
    })
    resolved = _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw)
    assert resolved.candidate_sha256 == _candidate_binding(head_b)
    assert [item["status"] for item in resolved.lineage][-2:] == ["ACCEPTED_HEAD", "ACCEPTED_HEAD"]


def test_error_directed_repair_retry_contains_only_bound_contract_diagnostics() -> None:
    from booktrans.production_worker.pipeline import error_directed_protocol_retry_feedback

    request = _request(unit="retry-feedback", display="body.p.09992", texts=("exact source", "other source"))
    feedback = error_directed_protocol_retry_feedback(
        enrich_request(request, translation_bible=None),
        ProviderContractGap("REPAIR_CONTRACT_GAP_EMPTY_TARGET_TEXT", details={"invalid_index": 1}),
        lane="REPAIR", permitted_segment_indices=[1],
    )
    assert feedback["error_code"] == "REPAIR_CONTRACT_GAP_EMPTY_TARGET_TEXT"
    assert feedback["invalid_field"] == "patches[*].target_text"
    assert feedback["source_segment_bindings"] == [{"index": 1, "source_fragment": "other source"}]
    assert "target_text" not in feedback


def test_resume_rereviews_the_resolved_accepted_head_once_after_rejected_repair(tmp_path: Path) -> None:
    """Resume consumes stale final evidence against the authority head, not RAW."""
    request = _request(unit="livelock-current", display="body.p.000031")
    run, payload, runner = _production_run(tmp_path, [request])
    payload["blocks"] = [object()]
    checkpoint = _seed_partial_raw(run, request)
    raw = _raw(request, ("旧候选",))
    current = _raw(request, ("当前已验收候选",))
    raw_path = checkpoint / "translation_raw_response.jsonl"
    raw_path.unlink()
    persist_raw_response(raw_path, [raw])
    issue = ReviewIssue(
        code="SEMANTIC_OMISSION", affected_segment_indices=[0],
        reason="historical repair candidate", repair_instruction="repair",
    )
    review = _review(request, "REPAIR", [issue])
    persist_unit_result(unit_path(checkpoint, request.display_id), {
        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "working": raw.model_dump(), "qa": _qa_pass(None, request, raw, {request.unit_uid}),
        "review": review.model_dump(), "second_review": review.model_dump(),
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True, "translation_api_calls": 1,
    })
    binding = enrich_request(request, translation_bible=payload["bible"]).request_binding_sha256
    persist_stage_once(checkpoint, "accepted_candidate_mutations", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(current),
        "source_sha256": "a" * 64, "selection_sha256": "b" * 64,
        "request_binding_sha256": binding, "working": current.model_dump(),
        "qa": _qa_pass(None, request, current, {request.unit_uid}),
        "semantic_terminal": "MODEL_ACCEPTED", "accepted": True,
    })
    persist_stage_once(checkpoint, "semantic_rescue", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "working": raw.model_dump(),
        "semantic_terminal": "HUMAN_REQUIRED",
    })
    persist_stage_once(checkpoint, "semantic_rescue_repairs", request.display_id, {
        "authority_parent_verified": True, "parent_candidate_sha256": _candidate_binding(raw),
        "working": raw.model_dump(),
    })
    persist_stage_once(checkpoint, "semantic_rescue_completeness", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(raw), "passed": True,
    })
    persist_stage_once(checkpoint, "semantic_rescue_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "review": review.model_dump(),
    })
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    assert runner(
        root=tmp_path, run=run, worker_id="livelock-resume", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=FakeMT(), reviewer=reviewer), payload=payload, qa_fn=_qa_pass,
    ) == 0
    rereview_path = checkpoint / "semantic_rescue_rereviews" / f"{request.display_id}.json"
    # A current-head acceptance may now be written directly in the bound
    # corrective-final lane; both lanes are canonical current review evidence.
    if not rereview_path.exists():
        rereview_path = checkpoint / "semantic_rescue_corrective_final_reviews" / f"{request.display_id}.json"
    rereview = json.loads(rereview_path.read_text(encoding="utf-8"))
    assert rereview["candidate_binding_sha256"] == _candidate_binding(current)
    assert len(reviewer.seen) == 1
    # A second resume uses the durable rereview and never calls Translation or
    # the Reviewer again for the same recovery fingerprint.
    assert runner(
        root=tmp_path, run=run, worker_id="livelock-resume-repeat", handoff_path=run / "handoff-repeat.json",
        authorize=True, providers=_providers(mt=FakeMT(), reviewer=reviewer), payload=payload, qa_fn=_qa_pass,
    ) == 2  # the first run sealed the one-unit fixture checkpoint
    repeat_handoff = json.loads((run / "handoff-repeat.json").read_text(encoding="utf-8"))
    assert repeat_handoff["failure_classification"] == "CHECKPOINT_ALREADY_SEALED"
    assert len(reviewer.seen) == 1


def test_b028_current_locator_head_final_review_seals_before_raw_qa_reconciliation(
    tmp_path: Path,
) -> None:
    """The real checkpoint entry must review a Core-clean locator head before sealing.

    This is deliberately a one-unit isolated production fixture.  It seeds the
    same durable shape as B028/body.p.08697: immutable failing RAW QA, a
    deterministic locator descendant as Candidate Authority's current head,
    and an old-head review which must not certify that descendant.
    """
    from booktrans.production_worker.semantic_review_run import context_units_for_order

    # Use the immutable IR UID for this real work order: reviewer-group
    # binding intentionally rejects a synthetic UID even in an isolated run.
    request = _request(
        unit=context_units_for_order(["body.p.08697"])[0].unit_uid,
        display="body.p.08697", texts=("term, 29",)
    )
    run, payload, runner = _production_run(tmp_path, [request], checkpoint="checkpoint_01", completed_count=0, batch_label="FULL_BOOK_BATCH_028")
    # A real group is constructed by the checkpoint path; the fixture merely
    # supplies the frozen-IR presence gate required by group construction.
    payload["blocks"] = [object()]
    checkpoint = run / "full_book_batch_028" / "checkpoint_01"
    checkpoint.mkdir(parents=True, exist_ok=True)
    raw = _raw(request, ("术语，",))
    current = _raw(request, ("术语, 29",))
    persist_raw_response(checkpoint / "translation_raw_response.jsonl", [raw])
    raw_qa = {
        "unit_uid": request.unit_uid,
        "display_id": request.display_id,
        "passed": False,
        "diagnostics": [{"code": "NUMBER_PRESERVATION", "message": "historical locator miss"}],
    }
    _write_json(checkpoint / "translation_raw_qa_report.json", {"pass_count": 0, "fail_count": 1, "records": [raw_qa]})
    persist_unit_result(unit_path(checkpoint, request.display_id), {
        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "working": raw.model_dump(), "qa": raw_qa,
        "review": _review(request, "ACCEPT").model_dump(),
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True, "translation_api_calls": 0,
    })
    binding = enrich_request(request, translation_bible=payload["bible"]).request_binding_sha256
    persist_stage_once(checkpoint, "accepted_candidate_mutations", request.display_id, {
        "artifact_type": "PRODUCTION_ACCEPTED_CANDIDATE_MUTATION", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "mutation_kind": "INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION",
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(current),
        "source_sha256": request.source_sha256, "selection_sha256": payload["selection_sha256"],
        "request_binding_sha256": binding, "working": current.model_dump(),
        "qa": _qa_pass(None, request, current, {request.unit_uid}),
        "semantic_terminal": "MODEL_ACCEPTED",
        "production_candidate_disposition": "PENDING_SEMANTIC_REVIEW", "accepted": True,
        "translation_api_calls": 0,
    })
    # Historical acceptance is bound to RAW, deliberately not the current head.
    persist_stage_once(checkpoint, "semantic_rescue_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(raw), "request_binding_sha256": binding,
        "review": _review(request, "ACCEPT").model_dump(),
    })

    class RecordingReviewer(FakeReviewer):
        def review_with_provenance(self, *args, **kwargs):
            request_path = checkpoint / "semantic_rescue_corrective_final_review_requests" / f"{request.display_id}.json"
            # The orchestrator has committed the exact-current request before
            # provider dispatch, and has not prematurely terminalized RAW QA.
            assert request_path.is_file()
            assert not (checkpoint / "current_candidate_qa_reconciliations" / f"{request.display_id}.json").exists()
            assert not (run / "handoff.json").exists()
            assert not (checkpoint / "human_required_triage" / f"{request.display_id}.json").exists()
            return super().review_with_provenance(*args, **kwargs)

    reviewer = RecordingReviewer(verdicts=["ACCEPT"])
    assert runner(
        root=tmp_path, run=run, worker_id="b028-entry", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=FakeMT(), reviewer=reviewer), payload=payload,
        qa_fn=_qa_pass,
    ) == 0
    request_path = checkpoint / "semantic_rescue_corrective_final_review_requests" / f"{request.display_id}.json"
    review_path = checkpoint / "semantic_rescue_corrective_final_reviews" / f"{request.display_id}.json"
    request_record = json.loads(request_path.read_text(encoding="utf-8"))
    review_record = json.loads(review_path.read_text(encoding="utf-8"))
    assert request_record["candidate_binding_sha256"] == _candidate_binding(current)
    assert review_record["candidate_binding_sha256"] == _candidate_binding(current)
    assert review_record["review"]["verdict"] == "ACCEPT"
    assert len(reviewer.seen) == 1
    reconciliation = json.loads((checkpoint / "current_candidate_qa_reconciliations" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert reconciliation["current_candidate_sha256"] == _candidate_binding(current)
    assert (checkpoint / "checkpoint_evidence.json").is_file()

    # A sealed checkpoint is immutable; replay must not create another review
    # identity or spend another provider attempt.
    before = review_path.read_bytes()
    assert runner(
        root=tmp_path, run=run, worker_id="b028-entry-replay", handoff_path=run / "handoff-replay.json",
        authorize=True, providers=_providers(mt=FakeMT(), reviewer=reviewer), payload=payload,
        qa_fn=_qa_pass,
    ) == 2
    assert review_path.read_bytes() == before
    assert len(reviewer.seen) == 1
    assert json.loads((run / "handoff-replay.json").read_text(encoding="utf-8"))["failure_classification"] == "CHECKPOINT_ALREADY_SEALED"


def test_checkpoint_rebuild_reconciles_historical_raw_failure_before_disposition(tmp_path: Path) -> None:
    """A prior accepted descendant cannot be re-blocked by immutable RAW QA."""
    from booktrans.production_worker.semantic_review_run import context_units_for_order

    request = _request(
        unit=context_units_for_order(["body.p.08697"])[0].unit_uid,
        display="body.p.08697", texts=("term, 29",),
    )
    run, payload, runner = _production_run(
        tmp_path, [request], checkpoint="checkpoint_01", completed_count=0,
        batch_label="FULL_BOOK_BATCH_028",
    )
    payload["blocks"] = [object()]
    checkpoint = run / "full_book_batch_028" / "checkpoint_01"
    checkpoint.mkdir(parents=True, exist_ok=True)
    raw = _raw(request, ("术语，",))
    current = _raw(request, ("术语, 29",))
    persist_raw_response(checkpoint / "translation_raw_response.jsonl", [raw])
    raw_qa = {
        "unit_uid": request.unit_uid, "display_id": request.display_id,
        "passed": False, "diagnostics": [{"code": "NUMBER_PRESERVATION"}],
    }
    _write_json(checkpoint / "translation_raw_qa_report.json", {
        "pass_count": 0, "fail_count": 1, "records": [raw_qa],
    })
    persist_unit_result(unit_path(checkpoint, request.display_id), {
        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "working": raw.model_dump(), "qa": raw_qa,
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True,
    })
    binding = enrich_request(request, translation_bible=payload["bible"]).request_binding_sha256
    persist_stage_once(checkpoint, "accepted_candidate_mutations", request.display_id, {
        "artifact_type": "PRODUCTION_ACCEPTED_CANDIDATE_MUTATION", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": _candidate_binding(current),
        "source_sha256": request.source_sha256, "selection_sha256": payload["selection_sha256"],
        "request_binding_sha256": binding, "working": current.model_dump(),
        "qa": _qa_pass(None, request, current, {request.unit_uid}), "accepted": True,
    })
    persist_stage_once(checkpoint, "semantic_rescue_current_head_corrective_final_review_retries", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(current),
        "request_binding_sha256": binding, "review": _review(request, "ACCEPT").model_dump(),
    })
    from booktrans.production_worker.production_checkpoint import _project_current_effective_qa

    projected = _project_current_effective_qa(
        checkpoint_dir=checkpoint, request=request, raw=raw,
        resolved_candidate=SimpleNamespace(
            candidate=current, candidate_sha256=_candidate_binding(current),
            lineage=[
                {"candidate_sha256": _candidate_binding(raw)},
                {"candidate_sha256": _candidate_binding(current)},
            ],
        ),
        qa=_qa_pass, view=None, known_unit_ids={request.unit_uid},
        bible=payload["bible"],
    )
    assert projected["passed"] is True
    assert projected["effective_qa_reason"] == "CURRENT_HEAD_RECONCILIATION_PASS"
    reconciliation = json.loads((checkpoint / "current_candidate_qa_reconciliations" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert reconciliation["current_candidate_sha256"] == _candidate_binding(current)
    assert reconciliation["failed_qa_rules"] == ["NUMBER_PRESERVATION"]
    # The immutable failure and the first reconciliation are both replay-safe.
    before = (checkpoint / "current_candidate_qa_reconciliations" / f"{request.display_id}.json").read_bytes()
    replay = _project_current_effective_qa(
        checkpoint_dir=checkpoint, request=request, raw=raw,
        resolved_candidate=SimpleNamespace(
            candidate=current, candidate_sha256=_candidate_binding(current),
            lineage=[
                {"candidate_sha256": _candidate_binding(raw)},
                {"candidate_sha256": _candidate_binding(current)},
            ],
        ),
        qa=_qa_pass, view=None, known_unit_ids={request.unit_uid},
        bible=payload["bible"],
    )
    assert replay["passed"] is True
    assert (checkpoint / "current_candidate_qa_reconciliations" / f"{request.display_id}.json").read_bytes() == before


@pytest.mark.parametrize("failed_code", ["NUMBER_PRESERVATION", "D050"])
def test_b028_current_locator_head_deterministic_safety_fails_closed(
    tmp_path: Path, failed_code: str,
) -> None:
    """Seal-last cannot dispatch a Reviewer for an unsafe current locator head."""
    from booktrans.production_worker.semantic_review_run import context_units_for_order

    request = _request(unit=context_units_for_order(["body.p.08697"])[0].unit_uid, display="body.p.08697", texts=("term, 29",))
    run, payload, runner = _production_run(tmp_path, [request], checkpoint="checkpoint_01", completed_count=0, batch_label="FULL_BOOK_BATCH_028")
    payload["blocks"] = [object()]
    checkpoint = run / "full_book_batch_028" / "checkpoint_01"
    checkpoint.mkdir(parents=True, exist_ok=True)
    raw, current = _raw(request, ("术语，",)), _raw(request, ("术语, 29",))
    persist_raw_response(checkpoint / "translation_raw_response.jsonl", [raw])
    raw_qa = {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": False, "diagnostics": [{"code": "NUMBER_PRESERVATION"}]}
    _write_json(checkpoint / "translation_raw_qa_report.json", {"pass_count": 0, "fail_count": 1, "records": [raw_qa]})
    persist_unit_result(unit_path(checkpoint, request.display_id), {"artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1, "display_id": request.display_id, "unit_uid": request.unit_uid, "working": raw.model_dump(), "qa": raw_qa, "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True})
    binding = enrich_request(request, translation_bible=payload["bible"]).request_binding_sha256
    persist_stage_once(checkpoint, "accepted_candidate_mutations", request.display_id, {
        "artifact_type": "PRODUCTION_ACCEPTED_CANDIDATE_MUTATION", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "mutation_kind": "INDEX_LOCATOR_DETERMINISTIC_MATERIALIZATION",
        "parent_candidate_sha256": _candidate_binding(raw), "candidate_binding_sha256": _candidate_binding(current),
        "source_sha256": request.source_sha256, "selection_sha256": payload["selection_sha256"],
        "request_binding_sha256": binding, "working": current.model_dump(), "qa": {"passed": False},
        "semantic_terminal": "MODEL_ACCEPTED", "production_candidate_disposition": "PENDING_SEMANTIC_REVIEW", "accepted": True,
    })

    def unsafe_qa(view, req, candidate, known):
        return {"unit_uid": req.unit_uid, "display_id": req.display_id, "passed": False, "diagnostics": [{"code": failed_code}]}

    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    assert runner(root=tmp_path, run=run, worker_id="b028-unsafe", handoff_path=run / "handoff.json", authorize=True, providers=_providers(reviewer=reviewer), payload=payload, qa_fn=unsafe_qa) != 0
    assert reviewer.seen == []
    assert not (checkpoint / "semantic_rescue_corrective_final_review_requests" / f"{request.display_id}.json").exists()
    assert not (checkpoint / "semantic_rescue_corrective_final_reviews" / f"{request.display_id}.json").exists()
    assert not (checkpoint / "checkpoint_evidence.json").exists()


def _seed_l2_06_numeric_range_resume(
    tmp_path: Path, *, duplicate: str = "321-322",
) -> tuple[Path, dict, object, CalibrationRequest, Path]:
    """Build the persisted, post-rereview-shaped numeric resume boundary.

    This is deliberately a minimal test-only reconstruction of the B029
    incident family.  RAW is immutable and remains the authority root; the
    partial unit / RAW-QA records are historical compatibility evidence.  No
    prepared or rejected repair is installed as Candidate Authority.
    """
    from booktrans.production_worker.semantic_review_run import context_units_for_order

    request = _request(
        unit=context_units_for_order(["body.p.09005"])[0].unit_uid,
        display="body.p.09005",
        texts=("deprecation tooling, ", "321", "-", "322"),
    ).model_copy(update={"source_text": "deprecation tooling, 321-322"})
    run, payload, runner = _production_run(
        # This isolated fixture contains one selected work order, so only the
        # first frozen checkpoint is legal.  Its former checkpoint_04/cursor
        # combination correctly failed selection validation before reaching
        # the numeric current-head invariant under test.
        tmp_path, [request], checkpoint="checkpoint_01", completed_count=0,
        batch_label="FULL_BOOK_BATCH_029",
    )
    # This isolated production fixture has one current-view work order. Keep
    # its manifest, campaign state, and frozen selection internally exact;
    # synthetic selection members would fail fresh current-view resolution.
    selection_path = run / "full_book_batch_029_selection.json"
    _write_json(selection_path, {
        "batch": "FULL_BOOK_BATCH_029",
        "selected_logical_work_order_ids": [request.display_id],
    })
    _write_json(run / "production_campaign_state.json", {
        "schema_version": 1, "artifact_type": "PRODUCTION_CAMPAIGN_STATE",
        "derived_from_immutable_artifacts": True, "source_sha256": "a" * 64,
        "active_batch": "FULL_BOOK_BATCH_029", "completed_count": 0,
        "target_count": 1, "next_checkpoint": "checkpoint_01",
        "next_logical_work_order_id": request.display_id,
        "campaign_status": "CONTINUE", "recovery_queue": [],
    })
    _write_json(run / "full_book_batch_029" / "batch_manifest.json", {
        "batch": "FULL_BOOK_BATCH_029", "target_count": 1,
        "completed_count": 0, "checkpoint_count": 0, "remaining_in_batch": 1,
        "status": "OPEN_PROGRESS", "next_checkpoint": "checkpoint_01", "checkpoints": [],
    })
    # The real resume entry builds the production group.  Its frozen-IR
    # presence gate is all this isolated fixture needs.
    payload["blocks"] = [object()]
    checkpoint = run / "full_book_batch_029" / "checkpoint_01"
    checkpoint.mkdir(parents=True, exist_ok=True)
    # This test begins after request acquisition.  Persist its exact frozen
    # request so the runtime does not attempt to rebuild an unrelated DOCX
    # view from the deliberately minimal unit-test fixture.
    write_jsonl_once(checkpoint / "translation_request.jsonl", [request])
    raw = _raw(request, (f"弃用工具，{duplicate}", "321", "-", "322"))
    persist_raw_response(checkpoint / "translation_raw_response.jsonl", [raw])

    def numeric_ownership_qa(view, req, candidate, known):
        duplicated = any(token in candidate.target_segments[0].text for token in ("321", "322"))
        return {
            "unit_uid": req.unit_uid,
            "display_id": req.display_id,
            "passed": not duplicated,
            "diagnostics": [] if not duplicated else [
                {"code": "NUMERIC_ANCHOR_DUPLICATION", "message": "duplicate non-owner range"},
            ],
        }

    raw_qa = numeric_ownership_qa(None, request, raw, {request.unit_uid})
    _write_json(checkpoint / "translation_raw_qa_report.json", {
        "pass_count": 0, "fail_count": 1, "records": [raw_qa],
    })
    # A persisted unit result models the resume position after prior semantic
    # handling.  Its working value is RAW, so it cannot masquerade as a CA
    # descendant; the resolver still projects the immutable authority root.
    persist_unit_result(unit_path(checkpoint, request.display_id), {
        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "working": raw.model_dump(), "qa": raw_qa,
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True, "translation_api_calls": 0,
    })
    return run, payload, runner, request, checkpoint, numeric_ownership_qa


def test_l2_06_persisted_range_duplicate_resume_corrects_without_provider(tmp_path: Path) -> None:
    """L2-06 positive: the real checkpoint resume publishes only the legal descendant."""
    run, payload, runner, request, checkpoint, qa = _seed_l2_06_numeric_range_resume(tmp_path)
    mt = FakeMT()
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    assert runner(
        root=tmp_path, run=run, worker_id="l2-06-positive", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=mt, reviewer=reviewer), payload=payload, qa_fn=qa,
    ) == 0
    assert mt.calls == 0
    # Candidate-changing deterministic recovery still has to satisfy the
    # existing exact-current-head review obligation before sealing.  This is a
    # fake-only reviewer call; translation and repair dispatch remain zero.
    assert len(reviewer.seen) == 1
    mutation = json.loads((checkpoint / "accepted_candidate_mutations" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert mutation["mutation_kind"] == "NUMERIC_ANCHOR_OWNERSHIP_CORRECTION"
    assert [segment["text"] for segment in mutation["working"]["target_segments"]] == ["弃用工具，", "321", "-", "322"]
    assert mutation["qa"]["passed"] is True
    assert (checkpoint / "checkpoint_evidence.json").is_file()


def test_numeric_current_head_repair_is_scheduled_before_immutable_raw_qa_barrier(tmp_path: Path) -> None:
    """Current numeric REPAIR outranks stale RAW-QA at the canonical owner."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="numeric-current", display="body.p.09005", texts=("term 321",))
    current = _raw(request, ("术语 321",))
    current_sha = _candidate_binding(current)
    # This is immutable historical RAW-QA from before the resolver-validated
    # current acquisition.  It must not suppress the current effective QA.
    _write_json(tmp_path / "translation_raw_qa_report.json", {
        "pass_count": 0, "fail_count": 1,
        "records": [{"unit_uid": request.unit_uid, "display_id": request.display_id,
                     "passed": False, "diagnostics": [{"code": "NUMBER_PRESERVATION"}]}],
    })
    review = _review(request, "REPAIR", [ReviewIssue(
        code="NUMBER_PRESERVATION", affected_segment_indices=[0],
        reason="current numeric anchor requires correction", repair_instruction="Preserve 321 exactly.",
    )])
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": current_sha, "context_sha256": "current-context",
        "semantic_group_hash": "current-group",
    })
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {
        "candidate_binding_sha256": current_sha, "context_sha256": "current-context",
        "semantic_group_hash": "current-group", "review": review.model_dump(),
    })
    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=current, working=current,
        qa={"passed": True, "effective_qa_reason": "RESOLVER_VALIDATED_EFFECTIVE_ACQUISITION"},
    )
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    assert decision["disposition"] == "RECOVERY_REQUIRED"
    assert decision["recovery_family"] == "SEMANTIC_CORRECTIVE"


def test_numeric_anchor_current_recovery_precedes_machine_human_required_only(tmp_path: Path) -> None:
    """The B029 precedence rule accepts no substitute for a real Human decision."""
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.production_worker.production_checkpoint import canonical_production_disposition

    run, payload, _runner, request, checkpoint, qa = _seed_l2_06_numeric_range_resume(tmp_path)
    raw = TranslationResult.model_validate(json.loads((checkpoint / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines()[0]))
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa=qa(None, request, raw, {request.unit_uid}))
    outcome.human_required = True
    outcome.final_status = "HUMAN_REQUIRED"
    assert canonical_production_disposition(outcome, checkpoint_dir=checkpoint)["disposition"] == "RECOVERY_REQUIRED"

    persist_stage_once(checkpoint, "human_adjudications", request.display_id, {
        "artifact_type": "HUMAN_ADJUDICATION", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "decision": "HUMAN_REJECTED",
    })
    assert canonical_production_disposition(outcome, checkpoint_dir=checkpoint)["disposition"] == "HARD_STOP"


def test_numeric_anchor_current_recovery_rejects_ambiguous_and_already_published_heads(tmp_path: Path) -> None:
    """Unsafe ownership and an existing accepted mutation both fail closed/no-op."""
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.production_worker.production_checkpoint import canonical_production_disposition

    run, payload, runner, request, checkpoint, qa = _seed_l2_06_numeric_range_resume(tmp_path, duplicate="321 x 322")
    raw = TranslationResult.model_validate(json.loads((checkpoint / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines()[0]))
    ambiguous = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa=qa(None, request, raw, {request.unit_uid}))
    assert canonical_production_disposition(ambiguous, checkpoint_dir=checkpoint)["disposition"] == "HARD_STOP"

    # Publish the one legal descendant, then reconstruct the historical RAW
    # failure: canonical projection must not schedule the same correction.
    run, payload, runner, request, checkpoint, qa = _seed_l2_06_numeric_range_resume(tmp_path / "published")
    assert runner(root=tmp_path / "published", run=run, worker_id="numeric-once", handoff_path=run / "handoff.json", authorize=True, providers=_providers(reviewer=FakeReviewer(verdicts=["ACCEPT"])), payload=payload, qa_fn=qa) == 0
    raw = TranslationResult.model_validate(json.loads((checkpoint / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines()[0]))
    historical = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa=qa(None, request, raw, {request.unit_uid}))
    assert canonical_production_disposition(historical, checkpoint_dir=checkpoint)["disposition"] != "RECOVERY_REQUIRED"


@pytest.mark.parametrize("duplicate", ["321-322-323", "321 x 322", "321-322"])
def test_l2_06_persisted_ambiguous_range_resume_fails_closed(tmp_path: Path, duplicate: str) -> None:
    """L2-06 negative: only the exact unique contiguous range may be corrected."""
    run, payload, runner, request, checkpoint, qa = _seed_l2_06_numeric_range_resume(tmp_path, duplicate=duplicate)
    # Keep the last case structurally ambiguous by duplicating an owner slot.
    if duplicate == "321-322":
        raw_path = checkpoint / "translation_raw_response.jsonl"
        raw = _raw(request, ("弃用工具，321-322", "321", "-", "321"))
        raw_path.unlink()
        persist_raw_response(raw_path, [raw])
        unit = json.loads(unit_path(checkpoint, request.display_id).read_text(encoding="utf-8"))
        unit["working"] = raw.model_dump()
        unit_path(checkpoint, request.display_id).unlink()
        persist_unit_result(unit_path(checkpoint, request.display_id), unit)
    mt = FakeMT()
    assert runner(
        root=tmp_path, run=run, worker_id="l2-06-negative", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=mt), payload=payload, qa_fn=qa,
    ) == 2
    assert mt.calls == 0
    handoff = json.loads((run / "handoff.json").read_text(encoding="utf-8"))
    assert handoff["failure_classification"] == "NUMERIC_ANCHOR_OWNERSHIP_AMBIGUOUS"
    assert not (checkpoint / "accepted_candidate_mutations" / request.display_id).exists()
    assert not (checkpoint / "checkpoint_evidence.json").exists()


def test_current_rereview_corrective_is_not_suppressed_by_historical_queue_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A historical queue terminal cannot strand a current-bound corrective attempt."""
    request = _request(unit="corrective-after-queue", display="body.p.000032")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw = _raw(request, ("旧表述",))
    historical_repair = _raw(request, ("历史队列修复",))
    raw_path = checkpoint / "translation_raw_response.jsonl"
    raw_path.unlink()
    persist_raw_response(raw_path, [raw])
    issue = ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0],
        reason="current-head wording", repair_instruction="Use the corrected wording.",
    )
    review = _review(request, "REPAIR", [issue])
    qa_record = _qa_pass(None, request, raw, {request.unit_uid})
    persist_unit_result(unit_path(checkpoint, request.display_id), {
        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "working": historical_repair.model_dump(), "qa": qa_record,
        "review": review.model_dump(), "second_review": review.model_dump(),
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True, "translation_api_calls": 1,
    })
    binding = enrich_request(request, translation_bible=payload["bible"]).request_binding_sha256
    rereview_request = {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(raw),
        "context_sha256": "c" * 64, "semantic_group_hash": "d" * 64,
        "request_binding_sha256": binding,
    }
    persist_stage_once(checkpoint, "semantic_rescue", request.display_id, {
        "working": raw.model_dump(), "qa": qa_record,
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True,
    })
    persist_stage_once(checkpoint, "semantic_rescue_repairs", request.display_id, {
        "authority_parent_verified": True, "parent_candidate_sha256": _candidate_binding(raw),
        "contract": {"permitted_segment_indices": [0]}, "working": raw.model_dump(),
    })
    persist_stage_once(checkpoint, "semantic_rescue_rereview_requests", request.display_id, rereview_request)
    persist_stage_once(checkpoint, "semantic_rescue_rereviews", request.display_id, {
        **rereview_request, "review": review.model_dump(),
    })
    # This is immutable history from the queue path, not current repair authority.
    persist_stage_once(checkpoint, "resolution_queue_auto_resolutions", request.display_id, {
        "working": raw.model_dump(), "qa": qa_record,
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
        "human_required": True, "second_review": review.model_dump(),
    })
    from booktrans.production_worker import production_checkpoint as checkpoint_module
    def build_group(*args, **kwargs):
        candidate = kwargs.get("candidate_overrides", {}).get(request.display_id, raw)
        text = "".join(segment.text for segment in candidate.target_segments)
        member = SimpleNamespace(as_payload=lambda: {
            "display_id": request.display_id, "unit_uid": request.unit_uid, "candidate_text": text,
        })
        return SimpleNamespace(
            member_work_order_ids=(request.display_id,), repairable_work_order_ids=(request.display_id,),
            semantic_review_group_id="srg:test:current-group", group_hash="current-group",
            full_candidate_span=text, members=[member],
            continuation_relation=None, continuation_review_rules={},
            as_reviewer_context_payload=lambda: {"group_hash": "current-group", "full_candidate_span": text},
        )
    monkeypatch.setattr(checkpoint_module, "_production_review_group", build_group)
    mt = FakeMT()
    repair = FakeRepair(text="已修正表述")
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    assert runner(
        root=tmp_path, run=run, worker_id="current-rereview-corrective",
        handoff_path=run / "handoff.json", authorize=True,
        providers=_providers(mt=mt, repair=repair, reviewer=reviewer), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 0 and repair.calls == 1 and len(reviewer.seen) == 1
    assert (checkpoint / "semantic_rescue_corrective" / f"{request.display_id}.json").is_file()


def test_unexplained_candidate_head_mismatch_remains_hard_stop(tmp_path: Path) -> None:
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="unexplained-mismatch", display="body.p.00004")
    raw = _raw(request, ("原始候选",))
    different = _raw(request, ("无绑定候选",))
    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw,
        working=different, qa={"passed": True},
    )
    assert canonical_production_disposition(outcome, checkpoint_dir=tmp_path)["reason"] == "CURRENT_HEAD_BINDING_MISMATCH"


def test_exact_d050_marker_repair_gate_rejects_any_non_marker_numeric_failure() -> None:
    request = _request(texts=("Authors identify19 an API.",)).model_copy(update={
        "source_text": "Authors identify19 an API.",
        "verified_footnote_numeric_bindings": [
            VerifiedFootnoteNumericBinding(
                unit_uid="unit-1", exact_source_marker_token="19", footnote_number="19",
                source_segment_binding=[VerifiedFootnoteSourceSegment(anchor_ref="a0", text="Authors identify19 an API.")],
            )
        ],
    })
    candidate = _raw(request, ("作者识别出 API。",))
    assert _only_missing_verified_d050_markers(
        request, candidate,
        {"diagnostics": [{"code": "NUMBER_PRESERVATION", "message": "Required source token(s) are absent from target: ['19']"}]},
    )
    assert not _only_missing_verified_d050_markers(
        request, candidate,
        {"diagnostics": [{"code": "NUMBER_PRESERVATION", "message": "Required source token(s) are absent from target: ['19', '20']"}]},
    )


@pytest.mark.parametrize("instruction", [None, "", "   ", "None", "null", " NuLl "])
def test_reviewer_repair_instruction_null_like_values_are_contract_invalid(instruction: str | None) -> None:
    from booktrans.providers.qwen_max import _validate_review_result

    request = _request()
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0], reason="reported issue", repair_instruction=instruction,
    )])
    with pytest.raises(ProviderContractGap, match="REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING"):
        _validate_review_result(TranslationProviderRequest(request=request), review)
    valid = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0], reason="reported issue", repair_instruction="Correct the named source clause.",
    )])
    _validate_review_result(TranslationProviderRequest(request=request), valid)


@dataclass
class FakeMT:
    text: str = "中文译文"
    error: Exception | None = None
    calls: int = 0

    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate:
        self.calls += 1
        if self.error:
            raise self.error
        return _candidate(request.request, self.text)


@dataclass
class FakeMax:
    error: Exception | None = None
    calls: int = 0

    def translate_structured(self, request: TranslationProviderRequest) -> SimpleNamespace:
        self.calls += 1
        if self.error:
            raise self.error
        raw = _raw(request.request)
        return SimpleNamespace(candidate=_candidate(request.request, "structured"), raw=raw, structured=None, strategy="DIRECT_STRUCTURED")


@dataclass
class ScriptedMax:
    outcomes: list[object]
    calls: int = 0

    def translate_structured(self, request: TranslationProviderRequest) -> SimpleNamespace:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        raw = _raw(request.request, tuple(str(value) for value in outcome))
        return SimpleNamespace(candidate=_candidate(request.request, "structured"), raw=raw, structured=None, strategy="DIRECT_STRUCTURED")


@dataclass
class FakeReviewer:
    verdicts: list[str] = field(default_factory=lambda: ["ACCEPT"] * 8)
    mutate: bool = False
    seen: list[list[str]] = field(default_factory=list)
    contexts: list[dict | None] = field(default_factory=list)

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.seen.append([segment.text for segment in candidate.target_segments])
        self.contexts.append(None if semantic_context is None else dict(semantic_context))
        if self.mutate:
            candidate.target_segments[0] = StructuredTargetSegment(
                text="mutated-by-reviewer",
                kind=candidate.target_segments[0].kind,
                anchor_ref=candidate.target_segments[0].anchor_ref,
                island_ref=candidate.target_segments[0].island_ref,
            )
        verdict = self.verdicts.pop(0)
        issues = []
        if verdict == "REPAIR":
            issues = [ReviewIssue(code="SENSE", affected_segment_indices=[0], reason="kickback", repair_instruction="Use 分成.")]
        review = _review(request.request, verdict, issues)
        return ReviewerProviderResult(review=review, candidate=_candidate(request.request, "review"))


@dataclass
class ContractGapReviewer:
    outcomes: list[str]
    calls: int = 0

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if outcome.startswith("GAP:"):
            raise ProviderContractGap(outcome.removeprefix("GAP:"))
        if outcome == "INVALID_ACCEPT_WITH_ISSUES":
            raise ProviderContractGap("REVIEWER_CONTRACT_GAP_ACCEPT_WITH_ISSUES")
        review = _review(request.request, outcome)
        return ReviewerProviderResult(review=review, candidate=_candidate(request.request, "review"))


@dataclass
class FakeRepair:
    text: str = "分成"
    fail: Exception | None = None
    extra_index: int | None = None
    calls: int = 0

    def repair(self, request, current, contract):
        self.calls += 1
        if self.fail:
            raise self.fail
        if set(contract.permitted_segment_indices) - set(range(len(current.target_segments))):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_SCOPE_EXPANDED")
        patched = list(current.target_segments)
        for index in contract.permitted_segment_indices:
            source = request.request.source_segments[index]
            patched[index] = StructuredTargetSegment(text=self.text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
        if self.extra_index is not None:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_SCOPE_EXPANDED")
        return TranslationResult(unit_uid=current.unit_uid, target_segments=patched)


@dataclass
class ContractGapRepair:
    outcomes: list[str]
    calls: int = 0

    def repair(self, request, current, contract):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if outcome.startswith("GAP:"):
            raise ProviderContractGap(outcome.removeprefix("GAP:"))
        patched = list(current.target_segments)
        for index in contract.permitted_segment_indices:
            source = request.request.source_segments[index]
            patched[index] = StructuredTargetSegment(text=outcome, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
        return TranslationResult(unit_uid=current.unit_uid, target_segments=patched)


def _qa_pass(view, request, response, known):
    return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": True, "diagnostics": []}


def _qa_fail(view, request, response, known):
    return {
        "unit_uid": request.unit_uid,
        "display_id": request.display_id,
        "passed": False,
        "diagnostics": [{"code": "NUMBER_PRESERVATION", "message": "missing 2"}],
    }


def _qa_number_roundtrip(view, request, response, known):
    passed = response.target_segments[0].text == "250"
    return {
        "unit_uid": request.unit_uid,
        "display_id": request.display_id,
        "passed": passed,
        "diagnostics": [] if passed else [{"code": "NUMBER_PRESERVATION", "message": "250 changed"}],
    }


def _providers(**kwargs) -> WorkerProviders:
    return WorkerProviders(
        mt=kwargs.get("mt", FakeMT()),
        structured=kwargs.get("structured", FakeMax()),
        reviewer=kwargs.get("reviewer", FakeReviewer()),
        repair=kwargs.get("repair", FakeRepair()),
    )


def test_provider_timeout_does_not_create_raw() -> None:
    request = _request()
    outcome = process_unit(request, _providers(mt=FakeMT(error=ProviderRuntimeError("QWEN_TRANSPORT_TIMEOUTERROR", retryable=True))))
    assert outcome.raw is None
    assert outcome.failure_family == "PROVIDER_TRANSIENT"


def test_structured_contract_failure_fails_closed_before_raw() -> None:
    request = _request(texts=("One.", "Two."))
    outcome = process_unit(
        request,
        _providers(structured=FakeMax(error=ProviderContractGap("PROVIDER_CONTRACT_GAP_SEGMENT_INDEX_SET_MISMATCH"))),
        view=SimpleNamespace(),
        known_unit_ids={"unit-1"},
        qa_fn=_qa_pass,
    )
    assert outcome.raw is None
    assert outcome.failure_family == "PROVIDER_CONTRACT_FAILURE"


def test_provider_contract_invalid_retries_once_then_creates_raw(tmp_path: Path) -> None:
    request = _request(texts=("One.", "Two."))
    structured = ScriptedMax([ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT"), ("一", "二")])
    candidate, raw, evidence = _translate_with_bounded_provider_contract_retry(
        checkpoint_dir=tmp_path, run=tmp_path, request=request, bible=None,
        active=_providers(structured=structured), decision=route_semantic_unit(request),
    )
    assert candidate is not None and raw is not None and evidence is None
    assert structured.calls == 2
    failure = json.loads((tmp_path / "provider_contract_failures" / f"{request.display_id}.attempt_00.json").read_text(encoding="utf-8"))
    assert failure["contract_error_code"] == "PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT"
    assert failure["valid_raw_created"] is False


def test_provider_contract_two_invalid_responses_stop_without_third_call(tmp_path: Path) -> None:
    request = _request(texts=("One.", "Two."))
    structured = ScriptedMax([ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT"), ProviderContractGap("PROVIDER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID")])
    candidate, raw, evidence = _translate_with_bounded_provider_contract_retry(
        checkpoint_dir=tmp_path, run=tmp_path, request=request, bible=None,
        active=_providers(structured=structured), decision=route_semantic_unit(request),
    )
    assert candidate is None and raw is None and evidence is not None
    assert structured.calls == 2
    candidate, raw, evidence = _translate_with_bounded_provider_contract_retry(
        checkpoint_dir=tmp_path, run=tmp_path, request=request, bible=None,
        active=_providers(structured=structured), decision=route_semantic_unit(request),
    )
    assert candidate is None and raw is None and evidence is not None
    assert structured.calls == 2


def test_no_candidate_terminal_binds_the_exhausted_provider_evidence(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _persist_no_safe_authoritative_candidate_terminal

    request = _request(texts=("One.", "Two."))
    decision = route_semantic_unit(request)
    evidence = tmp_path / "provider_contract_failures" / f"{request.display_id}.attempt_01.json"
    evidence.parent.mkdir()
    evidence.write_text(json.dumps({"provider": "TEST_PROVIDER", "model": decision.model, "contract_error_code": "PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT"}), encoding="utf-8")
    terminal = _persist_no_safe_authoritative_candidate_terminal(
        tmp_path, request=request, bible=None, decision=decision, provider_contract_evidence=evidence,
    )
    payload = json.loads(terminal.read_text(encoding="utf-8"))
    assert payload["terminal_class"] == "NO_SAFE_AUTHORITATIVE_CANDIDATE"
    assert payload["root_cause_class"] == "PROVIDER_ATTEMPTS_EXHAUSTED"
    assert payload["root_cause_evidence"] == str(evidence)
    assert payload["valid_provider_result_exists"] is False


def test_provider_contract_resume_after_first_invalid_runs_only_remaining_retry(tmp_path: Path) -> None:
    request = _request(texts=("One.", "Two."))
    structured = ScriptedMax([("一", "二")])
    decision = route_semantic_unit(request)
    from booktrans.production_worker.production_checkpoint import _persist_provider_contract_failure
    _persist_provider_contract_failure(
        tmp_path, request=request, bible=None, decision=decision,
        error=ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT"), attempt=0,
    )
    candidate, raw, evidence = _translate_with_bounded_provider_contract_retry(
        checkpoint_dir=tmp_path, run=tmp_path, request=request, bible=None,
        active=_providers(structured=structured), decision=decision,
    )
    assert candidate is not None and raw is not None and evidence is None
    assert structured.calls == 1


def test_reviewer_cannot_mutate_candidate() -> None:
    request = _request()
    with pytest.raises(RuntimeError, match="REVIEWER_MUTATED_CANDIDATE"):
        process_unit(
            request,
            _providers(reviewer=FakeReviewer(mutate=True)),
            view=SimpleNamespace(),
            known_unit_ids={"unit-1"},
            qa_fn=_qa_pass,
        )


def test_repair_then_qa_then_rereview_keeps_original_on_second_repair() -> None:
    request = _request()
    outcome = process_unit(
        request,
        _providers(reviewer=FakeReviewer(verdicts=["REPAIR", "REPAIR"]), repair=FakeRepair(text="分成")),
        view=SimpleNamespace(),
        known_unit_ids={"unit-1"},
        qa_fn=_qa_pass,
    )
    assert outcome.human_required is True
    assert outcome.final_status == "HUMAN_REQUIRED"
    assert outcome.working is not None
    assert outcome.raw is not None
    assert [segment.text for segment in outcome.working.target_segments] == [segment.text for segment in outcome.raw.target_segments]


def test_repair_success_requires_second_accept() -> None:
    request = _request()
    outcome = process_unit(
        request,
        _providers(reviewer=FakeReviewer(verdicts=["REPAIR", "ACCEPT"]), repair=FakeRepair(text="分成")),
        view=SimpleNamespace(),
        known_unit_ids={"unit-1"},
        qa_fn=_qa_pass,
    )
    assert outcome.repair_applied is True
    assert outcome.final_status == "ACCEPT"
    assert outcome.working.target_segments[0].text == "分成"
    assert outcome.raw.target_segments[0].text != "分成"


def test_number_preservation_uses_exact_raw_segment_for_qwen_narrow_repair() -> None:
    request = _request(texts=("250",))
    repair = FakeRepair(text="250")
    outcome = process_unit(
        request,
        _providers(
            mt=FakeMT(text="2.5"),
            reviewer=FakeReviewer(verdicts=["ACCEPT"]),
            repair=repair,
        ),
        view=SimpleNamespace(),
        known_unit_ids={"unit-1"},
        qa_fn=_qa_number_roundtrip,
    )
    assert outcome.final_status == "ACCEPT"
    assert outcome.translation_api_calls == 1
    assert repair.calls == 1
    assert outcome.repair_applied is True
    assert outcome.repair_policy_id == "NUMERIC_REPRESENTATION"


def test_raw_immutability_create_once(tmp_path: Path) -> None:
    request = _request()
    raw = _raw(request)
    path = tmp_path / "translation_raw_response.jsonl"
    persist_raw_response(path, [raw])
    with pytest.raises(ValueError, match="RAW_RESPONSE_ALREADY_EXISTS"):
        persist_raw_response(path, [raw])


def test_provider_api_key_never_logged(tmp_path: Path) -> None:
    request = _request()
    candidate = _candidate(request)
    record = candidate.provenance_record()
    record["route"] = "QWEN_MT_PLUS"
    path = tmp_path / "translation_provider_provenance.jsonl"
    persist_provenance_records(path, [record])
    payload = path.read_text(encoding="utf-8")
    assert "api_key" not in payload
    assert "test-secret" not in payload
    with pytest.raises(ValueError, match="PROVIDER_PROVENANCE_SECRET_BLOCKED"):
        persist_provenance_records(tmp_path / "secret.jsonl", [{"provider": "x", "api_key": "sk-secret"}])


def test_isolated_canary_refuses_production_checkpoint(tmp_path: Path) -> None:
    run = tmp_path / "full-book-run"
    (run / "full_book_batch_022").mkdir(parents=True)
    with pytest.raises(FrozenSelectionError, match="ISOLATED_OUTPUT_COLLIDES_WITH_PRODUCTION"):
        assert_not_production_checkpoint(run / "full_book_batch_022" / "checkpoint_03", run=run, batch_label="FULL_BOOK_BATCH_022", checkpoint="checkpoint_03")


def test_isolated_canary_writes_outside_production_and_passes_gate(tmp_path: Path) -> None:
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "qwen-production-canary-test"
    request_a = _request(unit="u1", display="body.p.06939")
    request_b = _request(unit="u2", display="body.p.06940", texts=("First.", "Second."))
    views = {request_a.display_id: SimpleNamespace(), request_b.display_id: SimpleNamespace()}
    report = run_isolated_canary(
        output_dir=output,
        requests=[request_a, request_b],
        providers=_providers(reviewer=FakeReviewer(verdicts=["ACCEPT", "ACCEPT"])),
        production_run=production_run,
        views=views,
        known_unit_ids={"u1", "u2"},
        qa_fn=_qa_pass,
        frozen_order=[request_a.display_id, request_b.display_id],
        selection_sha256="b" * 64,
        source_sha256="a" * 64,
    )
    assert (output / "translation_raw_response.jsonl").is_file()
    assert not (production_run / "full_book_batch_022" / "checkpoint_03").exists()
    assert report["route_distribution"]["QWEN_MT_PLUS"] == 1
    assert report["route_distribution"]["QWEN_MAX_STRUCTURED"] == 1
    assert report["provider_contract_pass"] == 2
    assert evaluate_canary_gate(report)["passed"] is True
    with pytest.raises(ValueError, match="RAW_RESPONSE_ALREADY_EXISTS"):
        persist_raw_response(output / "translation_raw_response.jsonl", [_raw(request_a)])


def test_canary_contract_failure_does_not_write_raw(tmp_path: Path) -> None:
    production_run = tmp_path / "run"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "canary-fail"
    request = _request(texts=("One.", "Two."))
    report = run_isolated_canary(
        output_dir=output,
        requests=[request],
        providers=_providers(structured=FakeMax(error=ProviderContractGap("PROVIDER_CONTRACT_GAP_PROTECTED_SEGMENT_MUTATED"))),
        production_run=production_run,
        frozen_order=[request.display_id],
    )
    assert not (output / "translation_raw_response.jsonl").exists()
    assert report["provider_contract_pass"] == 0
    assert evaluate_canary_gate(report)["passed"] is False


def test_production_worker_package_does_not_call_codex() -> None:
    root = Path(r"D:\translate\src\booktrans\production_worker")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "codex exec" not in text
        assert "CodexExecutable" not in text
    text = Path(r"D:\translate\scripts\production\production_campaign_supervisor.ps1").read_text(encoding="utf-8")
    assert "run_qwen_deterministic_worker.py" in text
    assert "run_allowlisted_recovery.py" in text
    assert "workerKind=deterministic" in text
    assert 'if ($CodexExecutable -eq "codex")' in text
    assert "QWEN_NARROW_REPAIR" in text
    assert "DETERMINISTIC_EVIDENCE" in text
    assert "--forbid-production-write" in text
    assert "serialized_repair_contract_json" in text
    recovery_block = text.split("AUTO_RECOVERY_START fingerprint=")[1].split("AUTO_RECOVERY_EXHAUSTED")[0]
    assert "CodexExecutable exec" not in recovery_block
    assert "$allowlistedRecoveryScript" in recovery_block


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _production_run(
    tmp_path: Path,
    requests: list[CalibrationRequest],
    *,
    checkpoint: str = "checkpoint_03",
    completed_count: int = 50,
    batch_label: str = "FULL_BOOK_BATCH_022",
):
    from booktrans.production_worker.production_checkpoint import run_production_checkpoint

    run = tmp_path / "full-book-20260916T000200Z-decb09"
    suffix = batch_label.removeprefix("FULL_BOOK_BATCH_")
    batch = run / f"full_book_batch_{suffix}"
    batch.mkdir(parents=True)
    _write_json(
        run / "production_campaign_state.json",
        {
            "schema_version": 1,
            "artifact_type": "PRODUCTION_CAMPAIGN_STATE",
            "derived_from_immutable_artifacts": True,
            "source_sha256": "a" * 64,
            "active_batch": batch_label,
            "completed_count": completed_count,
            "target_count": 200,
            "next_checkpoint": checkpoint,
            "next_logical_work_order_id": requests[0].display_id,
            "campaign_status": "CONTINUE",
            "recovery_queue": [],
        },
    )
    _write_json(
        batch / "batch_manifest.json",
        {
            "batch": batch_label,
            "target_count": 200,
            "completed_count": completed_count,
            "checkpoint_count": completed_count // 25,
            "remaining_in_batch": 200 - completed_count,
            "status": "OPEN_PROGRESS",
            "next_checkpoint": checkpoint,
            "checkpoints": [
                {"id": f"checkpoint_{index:02d}", "actual_count": 25, "raw_sha256": str(index) * 64, "raw_pass": 25, "final_pass": 25, "normalization_changed": 0}
                for index in range(1, completed_count // 25 + 1)
            ],
        },
    )
    _write_json(
        run / f"full_book_batch_{suffix}_selection.json",
        {"batch": batch_label, "selected_logical_work_order_ids": [item.display_id for item in requests]},
    )
    payload = {
        "batch_label": batch_label,
        "requests": requests,
        "views": {item.display_id: SimpleNamespace() for item in requests},
        "order": [item.display_id for item in requests],
        "known_unit_ids": {item.unit_uid for item in requests},
        "bible": tmp_path / "missing-bible.md",
        "source_sha256": "a" * 64,
        "selection_sha256": "b" * 64,
    }
    return run, payload, run_production_checkpoint


def test_b023_cursor_uses_b023_checkpoint_path_and_not_b022(tmp_path: Path) -> None:
    request = _request(unit="b023-u1", display="body.p.07180")
    run, payload, runner = _production_run(
        tmp_path, [request], checkpoint="checkpoint_01", completed_count=0, batch_label="FULL_BOOK_BATCH_023"
    )
    mt = FakeMT()
    assert runner(
        root=tmp_path, run=run, worker_id="b023", handoff_path=run / "handoff.json", authorize=True,
        providers=_providers(mt=mt, reviewer=FakeReviewer(verdicts=["ACCEPT"])), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 1
    assert (run / "full_book_batch_023" / "checkpoint_01" / "checkpoint_evidence.json").is_file()
    assert not (run / "full_book_batch_022").exists()


def test_active_batch_selection_mismatch_fails_before_model_or_raw(tmp_path: Path) -> None:
    request = _request(unit="b023-u1", display="body.p.07180")
    run, payload, runner = _production_run(
        tmp_path, [request], checkpoint="checkpoint_01", completed_count=0, batch_label="FULL_BOOK_BATCH_023"
    )
    payload["batch_label"] = "FULL_BOOK_BATCH_022"
    mt = FakeMT()
    assert runner(
        root=tmp_path, run=run, worker_id="b023", handoff_path=run / "handoff.json", authorize=True,
        providers=_providers(mt=mt), payload=payload, qa_fn=_qa_pass,
    ) == 2
    assert mt.calls == 0
    assert not (run / "full_book_batch_023" / "checkpoint_01").exists()
    handoff = json.loads((run / "handoff.json").read_text(encoding="utf-8"))
    assert handoff["failure_classification"] == "ACTIVE_BATCH_SELECTION_MISMATCH"


def test_explicit_worker_batch_binding_fails_before_checkpoint_mutation(tmp_path: Path) -> None:
    """A supervisor/worker identity split is rejected before lock, RAW, or authority writes."""
    request = _request(unit="b025-u1", display="body.p.08002")
    run, payload, runner = _production_run(
        tmp_path, [request], checkpoint="checkpoint_07", completed_count=150, batch_label="FULL_BOOK_BATCH_025"
    )
    mt = FakeMT()
    assert runner(
        root=tmp_path, run=run, worker_id="b025", handoff_path=run / "handoff.json", authorize=True,
        providers=_providers(mt=mt), payload=payload, qa_fn=_qa_pass,
        launch_batch_label="FULL_BOOK_BATCH_022", launch_checkpoint="checkpoint_07",
    ) == 2
    assert mt.calls == 0
    assert not (run / "full_book_batch_025" / "checkpoint_07").exists()
    handoff = json.loads((run / "handoff.json").read_text(encoding="utf-8"))
    assert handoff["active_batch"] == "FULL_BOOK_BATCH_025"
    assert handoff["failure_classification"] == "WORKER_LAUNCH_BATCH_MISMATCH"


def test_production_checkpoint_uses_authoritative_current_cursor(tmp_path: Path) -> None:
    request = _request(unit="u4", display="body.p.06986")
    run, payload, runner = _production_run(
        tmp_path,
        [request],
        checkpoint="checkpoint_04",
        completed_count=75,
    )
    mt = FakeMT()
    assert runner(
        root=tmp_path,
        run=run,
        worker_id="w4",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(mt=mt, reviewer=FakeReviewer(verdicts=["ACCEPT"])),
        payload=payload,
        qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 1
    assert (run / "full_book_batch_022" / "checkpoint_04" / "checkpoint_evidence.json").is_file()
    manifest = json.loads((run / "full_book_batch_022" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_count"] == 76
    assert manifest["next_checkpoint"] == "checkpoint_05"


def test_current_execution_validates_effective_request_not_historical_base_against_frozen_input(monkeypatch, tmp_path: Path) -> None:
    """A current-view topology correction must not invalidate immutable base evidence."""
    from booktrans.production_worker import worker as worker_module

    effective = _request(unit="current-input-1", display="body.p.06986")
    frozen = effective.model_copy(update={
        "source_segments": [effective.source_segments[0].model_copy(update={"island_ref": "island.unsupported"})],
    })
    run, payload, runner = _production_run(tmp_path, [frozen], checkpoint="checkpoint_01", completed_count=0)
    checkpoint = run / "full_book_batch_022" / "checkpoint_01"
    checkpoint.mkdir(parents=True)
    manifest_path = run / "full_book_batch_022" / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["target_count"] = 1
    _write_json(manifest_path, manifest)
    base = checkpoint / "translation_request.jsonl"
    write_jsonl_once(base, [effective])

    current = dict(payload)
    current.update({"requests": [effective], "effective_translation_request_path": base})
    monkeypatch.setattr(worker_module, "current_batch_checkpoint_inputs", lambda *args, **kwargs: current)

    mt = FakeMT()
    assert runner(
        root=tmp_path, run=run, worker_id="current-input", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=mt, reviewer=FakeReviewer(verdicts=["ACCEPT"])),
        payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 1
    assert base.read_bytes()  # immutable base was read, not rewritten


def _seed_partial_raw(run: Path, request: CalibrationRequest) -> Path:
    checkpoint = run / "full_book_batch_022" / "checkpoint_03"
    checkpoint.mkdir(parents=True, exist_ok=True)
    persist_raw_response(checkpoint / "translation_raw_response.jsonl", [_raw(request)])
    return checkpoint


def test_reviewer_contract_gap_retries_reviewer_only_from_partial_raw(tmp_path: Path) -> None:
    request = _request(unit="review-gap-1", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw_before = (checkpoint / "translation_raw_response.jsonl").read_bytes()
    mt = FakeMT()
    reviewer = ContractGapReviewer(["INVALID_ACCEPT_WITH_ISSUES", "ACCEPT"])
    assert runner(
        root=tmp_path,
        run=run,
        worker_id="review-gap-worker",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(mt=mt, reviewer=reviewer),
        payload=payload,
        qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 0
    assert reviewer.calls == 2
    assert (checkpoint / "translation_raw_response.jsonl").read_bytes() == raw_before
    evidence = json.loads((checkpoint / "reviewer_contract_failures" / "body.p.06939.attempt_01.json").read_text(encoding="utf-8"))
    assert evidence["contract_error_code"] == "REVIEWER_CONTRACT_GAP_ACCEPT_WITH_ISSUES"
    assert evidence["invalid_response_persisted_as_review"] is False


def test_two_reviewer_contract_gaps_defer_to_release_and_reuse_raw(tmp_path: Path) -> None:
    request = _request(unit="review-gap-2", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw_before = (checkpoint / "translation_raw_response.jsonl").read_bytes()
    mt = FakeMT()
    reviewer = ContractGapReviewer(["INVALID_ACCEPT_WITH_ISSUES", "INVALID_ACCEPT_WITH_ISSUES"])
    assert runner(
        root=tmp_path,
        run=run,
        worker_id="review-gap-worker",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(mt=mt, reviewer=reviewer),
        payload=payload,
        qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 0
    assert reviewer.calls == 2
    assert (checkpoint / "translation_raw_response.jsonl").read_bytes() == raw_before
    assert len(list((checkpoint / "reviewer_contract_failures").glob("*.json"))) == 2
    debt = json.loads((checkpoint / "reviewer_contract_debts" / "body.p.06939.json").read_text(encoding="utf-8"))
    assert debt["resolution_class"] == "DEFER_TO_RELEASE"
    assert debt["reviewer_contract_attempt_count"] == 2


def test_scope_invalid_repair_retries_once_and_finalizes_without_translation(tmp_path: Path) -> None:
    request = _request(unit="repair-gap-retry", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw_before = (checkpoint / "translation_raw_response.jsonl").read_bytes()
    repair = ContractGapRepair(["GAP:REPAIR_CONTRACT_GAP_SCOPE_EXPANDED", "已修复"])
    reviewer = FakeReviewer(verdicts=["REPAIR", "ACCEPT"])
    assert runner(
        root=tmp_path, run=run, worker_id="repair-gap-worker", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=FakeMT(), reviewer=reviewer, repair=repair), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert repair.calls == 2 and (checkpoint / "translation_raw_response.jsonl").read_bytes() == raw_before
    evidence = json.loads((checkpoint / "repair_contract_failures" / "body.p.06939.attempt_01.json").read_text(encoding="utf-8"))
    assert evidence["contract_error_code"] == "REPAIR_CONTRACT_GAP_SCOPE_EXPANDED"
    assert not (checkpoint / "repair_contract_debts" / "body.p.06939.json").exists()


def test_two_scope_invalid_repairs_defer_local_core_clean_head_without_third_call(tmp_path: Path) -> None:
    request = _request(unit="repair-gap-debt", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw_before = (checkpoint / "translation_raw_response.jsonl").read_bytes()
    repair = ContractGapRepair(["GAP:REPAIR_CONTRACT_GAP_SCOPE_EXPANDED"] * 2)
    assert runner(
        root=tmp_path, run=run, worker_id="repair-gap-worker", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=FakeMT(), reviewer=FakeReviewer(verdicts=["REPAIR"]), repair=repair), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert repair.calls == 2 and (checkpoint / "translation_raw_response.jsonl").read_bytes() == raw_before
    debt = json.loads((checkpoint / "repair_contract_debts" / "body.p.06939.json").read_text(encoding="utf-8"))
    assert debt["resolution_class"] == "DEFER_TO_RELEASE" and debt["repair_contract_attempt_count"] == 2


def test_historical_reviewer_contract_failure_allows_only_one_resume_retry(tmp_path: Path) -> None:
    request = _request(unit="review-gap-3", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    persist_stage_once(
        checkpoint,
        "reviewer_contract_failures",
        "body.p.06939.attempt_01",
        {
            "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_FAILURE", "schema_version": 1,
            "reviewer_contract_attempt": 1, "unit_uid": request.unit_uid,
            "candidate_binding_sha256": sha256(_raw(request).model_dump_json().encode("utf-8")).hexdigest(),
            "request_binding_sha256": TranslationProviderRequest(request=request).request_binding_sha256,
            "semantic_context_sha256": sha256(b"{}").hexdigest(),
        },
    )
    mt = FakeMT()
    reviewer = ContractGapReviewer(["INVALID_ACCEPT_WITH_ISSUES"])
    assert runner(
        root=tmp_path,
        run=run,
        worker_id="review-gap-worker",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(mt=mt, reviewer=reviewer),
        payload=payload,
        qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 0
    assert reviewer.calls == 1
    assert len(list((checkpoint / "reviewer_contract_failures").glob("*.json"))) == 2


@pytest.mark.parametrize(
    "gap",
    [
        "REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING",
        "REVIEWER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID",
        "REVIEWER_CONTRACT_GAP_TRUNCATED_RESPONSE",
        "REVIEWER_CONTRACT_GAP_BINDING_MISMATCH",
    ],
)
def test_invalid_reviewer_categories_retry_once_then_defer_without_translation(tmp_path: Path, gap: str) -> None:
    """Malformed, truncated, missing-binding, and incomplete REPAIR share one path."""
    request = _request(unit=f"review-gap-{gap}", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw_before = (checkpoint / "translation_raw_response.jsonl").read_bytes()
    reviewer = ContractGapReviewer([f"GAP:{gap}", f"GAP:{gap}"])
    mt = FakeMT()
    assert runner(
        root=tmp_path, run=run, worker_id="review-gap-worker", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(mt=mt, reviewer=reviewer), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert mt.calls == 0 and reviewer.calls == 2
    assert (checkpoint / "translation_raw_response.jsonl").read_bytes() == raw_before
    assert (checkpoint / "reviewer_contract_debts" / "body.p.06939.json").is_file()


def test_two_bound_invalid_reviewer_attempts_resume_without_a_third_call(tmp_path: Path) -> None:
    request = _request(unit="review-gap-no-third", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    common = {
        "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_FAILURE", "schema_version": 1,
        "unit_uid": request.unit_uid,
        "candidate_binding_sha256": sha256(_raw(request).model_dump_json().encode("utf-8")).hexdigest(),
        "request_binding_sha256": TranslationProviderRequest(request=request).request_binding_sha256,
        "semantic_context_sha256": sha256(b"{}").hexdigest(),
    }
    for attempt in (1, 2):
        persist_stage_once(checkpoint, "reviewer_contract_failures", f"body.p.06939.attempt_{attempt:02d}", {**common, "reviewer_contract_attempt": attempt})
    reviewer = ContractGapReviewer([])
    assert runner(
        root=tmp_path, run=run, worker_id="review-gap-worker", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(reviewer=reviewer), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert reviewer.calls == 0
    assert (checkpoint / "reviewer_contract_debts" / "body.p.06939.json").is_file()


def test_two_invalid_reviewer_attempts_do_not_defer_an_independent_core_hard_failure(tmp_path: Path) -> None:
    request = _request(unit="review-gap-core-hard", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    common = {
        "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_FAILURE", "schema_version": 1,
        "unit_uid": request.unit_uid,
        "candidate_binding_sha256": sha256(_raw(request).model_dump_json().encode("utf-8")).hexdigest(),
        "request_binding_sha256": TranslationProviderRequest(request=request).request_binding_sha256,
        "semantic_context_sha256": sha256(b"{}").hexdigest(),
    }
    for attempt in (1, 2):
        persist_stage_once(checkpoint, "reviewer_contract_failures", f"body.p.06939.attempt_{attempt:02d}", {**common, "reviewer_contract_attempt": attempt})
    assert runner(
        root=tmp_path, run=run, worker_id="review-gap-worker", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(reviewer=ContractGapReviewer([])), payload=payload, qa_fn=_qa_fail,
    ) == 3
    assert not (checkpoint / "reviewer_contract_debts" / "body.p.06939.json").exists()


def test_old_candidate_bound_contract_failure_is_not_reused(tmp_path: Path) -> None:
    request = _request(unit="review-gap-new-head", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    persist_stage_once(
        checkpoint, "reviewer_contract_failures", "body.p.06939.attempt_01",
        {
            "artifact_type": "PRODUCTION_REVIEWER_CONTRACT_FAILURE", "schema_version": 1,
            "reviewer_contract_attempt": 1, "unit_uid": request.unit_uid,
            "candidate_binding_sha256": "f" * 64,
            "request_binding_sha256": TranslationProviderRequest(request=request).request_binding_sha256,
            "semantic_context_sha256": sha256(b"{}").hexdigest(),
        },
    )
    reviewer = ContractGapReviewer(["GAP:REVIEWER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID"] * 2)
    assert runner(
        root=tmp_path, run=run, worker_id="review-gap-worker", handoff_path=run / "handoff.json",
        authorize=True, providers=_providers(reviewer=reviewer), payload=payload, qa_fn=_qa_pass,
    ) == 0
    assert reviewer.calls == 2


def test_production_checkpoint_requires_authorization(tmp_path: Path) -> None:
    request = _request(unit="u1", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    with pytest.raises(FrozenSelectionError, match="PRODUCTION_CHECKPOINT_WRITE_NOT_AUTHORIZED"):
        runner(
            root=tmp_path,
            run=run,
            worker_id="w1",
            handoff_path=run / "handoff.json",
            authorize=False,
            providers=_providers(),
            payload=payload,
        )
    assert not (run / "full_book_batch_022" / "checkpoint_03").exists()


def test_production_persists_raw_per_unit_and_resume_skips_translate(tmp_path: Path) -> None:
    request_a = _request(unit="u1", display="body.p.06939")
    request_b = _request(unit="u2", display="body.p.06940")
    run, payload, runner = _production_run(tmp_path, [request_a, request_b])
    mt = FakeMT()
    first = runner(
        root=tmp_path,
        run=run,
        worker_id="w1",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(mt=mt, reviewer=FakeReviewer(verdicts=["ACCEPT", "ACCEPT"])),
        payload=payload,
        qa_fn=_qa_pass,
    )
    assert first == 0
    assert mt.calls == 2
    raw_path = run / "full_book_batch_022" / "checkpoint_03" / "translation_raw_response.jsonl"
    raw_before = raw_path.read_bytes()
    mt_resume = FakeMT()
    second = runner(
        root=tmp_path,
        run=run,
        worker_id="w2",
        handoff_path=run / "handoff2.json",
        authorize=True,
        providers=_providers(mt=mt_resume, reviewer=FakeReviewer(verdicts=["ACCEPT", "ACCEPT"])),
        payload=payload,
        qa_fn=_qa_pass,
    )
    assert second == 2
    assert mt_resume.calls == 0
    assert raw_path.read_bytes() == raw_before
    handoff = json.loads((run / "handoff2.json").read_text(encoding="utf-8"))
    assert handoff["failure_classification"] == "CHECKPOINT_ALREADY_SEALED"


def test_safe_human_required_is_release_debt_and_does_not_block_production_seal(tmp_path: Path) -> None:
    request = _request(unit="u1", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    code = runner(
        root=tmp_path,
        run=run,
        worker_id="w1",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(reviewer=FakeReviewer(verdicts=["REPAIR", "REPAIR"])),
        payload=payload,
        qa_fn=_qa_pass,
    )
    assert code == 0
    checkpoint = run / "full_book_batch_022" / "checkpoint_03"
    assert (checkpoint / "translation_raw_response.jsonl").is_file()
    manifest = json.loads((run / "full_book_batch_022" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["next_checkpoint"] == "checkpoint_04"
    assert manifest["completed_count"] == 51
    reviewer_payload = json.loads(
        (checkpoint / "reviewer_payloads" / "body.p.06939.json").read_text(encoding="utf-8")
    )
    assert reviewer_payload["artifact_type"] == "PRODUCTION_REVIEWER_PAYLOAD"
    assert reviewer_payload["unit_uid"] == request.unit_uid
    debt = json.loads((checkpoint / "resolution_queue_deferred_debts" / "body.p.06939.json").read_text(encoding="utf-8"))
    assert debt["resolution_class"] == "DEFERRED_SEMANTIC_DEBT"
    handoff = json.loads((run / "handoff.json").read_text(encoding="utf-8"))
    assert handoff["status"] == "CONTINUE"


def test_process_unit_forwards_semantic_context_to_reviewer() -> None:
    request = _request()
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    outcome = process_unit(
        request,
        _providers(reviewer=reviewer),
        view=SimpleNamespace(),
        known_unit_ids={"unit-1"},
        qa_fn=_qa_pass,
        semantic_context={"review_context_version": "booktrans.review-context.v2", "continuation_relation": "BODY_CONTINUATION"},
    )
    assert outcome.final_status == "ACCEPT"
    assert reviewer.contexts[0]["continuation_relation"] == "BODY_CONTINUATION"


def test_production_severe_repair_retries_existing_semantic_rescue_from_raw_and_can_seal(tmp_path: Path) -> None:
    request = _request(unit="u1", display="body.p.06939")
    run, payload, runner = _production_run(tmp_path, [request])
    mt = FakeMT()
    first = runner(
        root=tmp_path,
        run=run,
        worker_id="w1",
        handoff_path=run / "handoff.json",
        authorize=True,
        providers=_providers(mt=mt, reviewer=FakeReviewer(verdicts=["REPAIR", "REPAIR", "ACCEPT"])),
        payload=payload,
        qa_fn=_qa_pass,
    )
    # A material fresh-path REPAIR now spends the existing current-head
    # semantic rescue before it can reach the human terminal.
    assert first == 0
    raw_path = run / "full_book_batch_022" / "checkpoint_03" / "translation_raw_response.jsonl"
    raw_before = raw_path.read_bytes()
    unit_before = (run / "full_book_batch_022" / "checkpoint_03" / "units" / "body.p.06939.json").read_bytes()
    assert mt.calls == 1
    assert raw_path.read_bytes() == raw_before
    assert (run / "full_book_batch_022" / "checkpoint_03" / "units" / "body.p.06939.json").read_bytes() == unit_before
    rescue = json.loads((run / "full_book_batch_022" / "checkpoint_03" / "semantic_rescue" / "body.p.06939.json").read_text(encoding="utf-8"))
    assert rescue["semantic_terminal"] == "MODEL_ACCEPTED"
    assert rescue["translation_api_calls"] == 0
    manifest = json.loads((run / "full_book_batch_022" / "batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_count"] == 51
    assert manifest["next_checkpoint"] == "checkpoint_04"


def test_production_semantic_rescue_reuses_raw_once_and_enters_seal_aggregation(tmp_path: Path) -> None:
    request = _request(unit="rescue-1", display="body.p.07018")
    run, payload, runner = _production_run(tmp_path, [request])
    checkpoint = _seed_partial_raw(run, request)
    raw_before = (checkpoint / "translation_raw_response.jsonl").read_bytes()
    persist_stage_once(
        checkpoint,
        "production_reprocess",
        request.display_id,
        {
            "artifact_type": "PRODUCTION_REPROCESS_FROM_RAW", "schema_version": 1,
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            "working": _raw(request).model_dump(), "qa": _qa_pass(None, request, _raw(request), {request.unit_uid}),
            "review": _review(request, "REPAIR", [ReviewIssue(code="SENSE", affected_segment_indices=[0], reason="sense", repair_instruction="Fix sense.")]).model_dump(),
            "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True,
            "translation_api_calls": 0, "reviewer_api_calls": 2, "repair_api_calls": 1,
        },
    )
    persist_unit_result(
        unit_path(checkpoint, request.display_id),
        {
            "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            "working": _raw(request).model_dump(), "qa": _qa_pass(None, request, _raw(request), {request.unit_uid}),
            "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True,
            "translation_api_calls": 1,
        },
    )
    persist_stage_once(checkpoint, "human_required_triage", request.display_id, {"classification": "TRUE_SEMANTIC_HUMAN"})
    mt = FakeMT()
    repair = FakeRepair(text="修正语义")
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    assert runner(
        root=tmp_path, run=run, worker_id="rescue-worker", handoff_path=run / "handoff.json", authorize=True,
        providers=_providers(mt=mt, reviewer=reviewer, repair=repair), payload=payload, qa_fn=_qa_pass, semantic_rescue=True,
    ) == 0
    assert mt.calls == 0
    assert repair.calls == 1
    assert len(reviewer.seen) == 1
    assert (checkpoint / "translation_raw_response.jsonl").read_bytes() == raw_before
    rescue = json.loads((checkpoint / "semantic_rescue" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert rescue["semantic_terminal"] == "MODEL_ACCEPTED"
    final_qa = json.loads((checkpoint / "translation_final_qa_report.json").read_text(encoding="utf-8"))
    assert final_qa["pass_count"] == 1 and final_qa["fail_count"] == 0


def test_root_raw_resume_runs_typed_d050_repair_despite_legacy_noop_rescue(tmp_path: Path) -> None:
    """A ROOT head must not need legacy-unbound lineage to repair exact D050."""
    request = _request(unit="d050-root", display="body.p.99999", texts=("Exact marker 8 is visible.",)).model_copy(update={
        "source_text": "Exact marker 8 is visible.",
        "verified_footnote_numeric_bindings": [
            VerifiedFootnoteNumericBinding(
                unit_uid="d050-root", exact_source_marker_token="8", footnote_number="8",
                source_segment_binding=[VerifiedFootnoteSourceSegment(anchor_ref="a0", text="Exact marker 8 is visible.")],
            )
        ],
    })
    run, payload, runner = _production_run(tmp_path, [request])
    # Production review-group construction is intentionally gated on frozen
    # block presence; one placeholder reaches the same ROOT-head branch here.
    payload["blocks"] = [object()]
    checkpoint = _seed_partial_raw(run, request)
    raw = _raw(request, ("可见标记缺失。",))
    raw_path = checkpoint / "translation_raw_response.jsonl"
    raw_path.unlink()
    persist_raw_response(raw_path, [raw])
    raw_before = raw_path.read_bytes()
    persist_unit_result(unit_path(checkpoint, request.display_id), {
        "artifact_type": "PRODUCTION_UNIT_RESULT", "schema_version": 1,
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "working": raw.model_dump(), "qa": {"passed": False},
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True,
        "translation_api_calls": 1,
    })
    persist_stage_once(checkpoint, "semantic_rescue", request.display_id, {
        "mode": "SEMANTIC_REPAIR", "repair_applied": False, "working": raw.model_dump(),
    })
    legacy_path = checkpoint / "semantic_rescue" / f"{request.display_id}.json"
    legacy_before = legacy_path.read_bytes()

    def d050_qa(view, current, candidate, known):
        missing = "8" not in "".join(segment.text for segment in candidate.target_segments)
        return {
            "unit_uid": current.unit_uid, "display_id": current.display_id, "passed": not missing,
            "diagnostics": [] if not missing else [{
                "code": "NUMBER_PRESERVATION",
                "message": "Required source token(s) are absent from target: ['8']",
            }],
        }

    mt = FakeMT()
    repair = FakeRepair(text="已恢复标记8。")
    assert runner(
        root=tmp_path, run=run, worker_id="d050-root", handoff_path=run / "handoff.json", authorize=True,
        providers=_providers(mt=mt, reviewer=FakeReviewer(verdicts=["ACCEPT"]), repair=repair),
        payload=payload, qa_fn=d050_qa,
    ) == 0
    assert mt.calls == 0 and repair.calls == 1
    assert raw_path.read_bytes() == raw_before
    assert legacy_path.read_bytes() == legacy_before
    attempt = json.loads((checkpoint / "d050_exact_marker_recovery_attempts" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert attempt["recovery_family"] == "D050_EXACT_MARKER_REPLAY"
    assert attempt["accepted"] is True and attempt["qa"]["passed"] is True


def test_semantic_rescue_rejects_a_repair_that_drops_explicit_source_clauses_before_final_review(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _run_semantic_rescue

    request = _request(unit="rescue-completeness", display="body.p.07147", texts=("First sentence. Second sentence. Third sentence.",))
    base = _raw(request, ("第一句。第二句。第三句。",))
    review = _review(request, "REPAIR", [ReviewIssue(code="STRUCTURAL_MISMATCH", affected_segment_indices=[0], reason="format", repair_instruction="Fix only the format.")])
    mt = FakeMT()
    repair = FakeRepair(text="仅保留第一句。")
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    outcome = _run_semantic_rescue(
        checkpoint_dir=tmp_path, request=request, base=base,
        active=_providers(mt=mt, repair=repair, reviewer=reviewer), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None, semantic_context=None,
        group=None, prior={"review": review.model_dump()},
    )
    assert mt.calls == 0 and repair.calls == 1 and reviewer.seen == []
    assert outcome.human_required is True
    assert outcome.failure_code == "REPAIR_COMPLETENESS_CLAUSE_COVERAGE_FAILED"
    evidence = json.loads((tmp_path / "semantic_rescue_completeness" / "body.p.07147.json").read_text(encoding="utf-8"))
    assert evidence["passed"] is False
    assert evidence["rows"][0]["source_clause_count"] == 3
    assert evidence["rows"][0]["candidate_clause_count"] == 1


def test_stale_final_rereview_is_bound_to_durable_repaired_candidate_and_fails_closed(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _rereview_stale_final_candidate

    request = _request(unit="stale-review", display="body.p.07018")
    candidate = _raw(request, ("提出问题",))
    member = SimpleNamespace(
        as_payload=lambda: {"display_id": request.display_id, "unit_uid": request.unit_uid, "candidate_text": "提出问题"},
    )
    group = SimpleNamespace(
        group_hash="rebuilt-group", full_candidate_span="提出问题", members=[member],
        as_reviewer_context_payload=lambda: {"full_candidate_span": "提出问题", "group_hash": "rebuilt-group", "review_context_version": "booktrans.review-context.v2"},
    )
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    outcome = _rereview_stale_final_candidate(
        checkpoint_dir=tmp_path, request=request, candidate=candidate, active=_providers(reviewer=reviewer),
        view=SimpleNamespace(), known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None, group=group,
    )
    assert outcome.final_status == "ACCEPT"
    assert len(reviewer.seen) == 1
    request_evidence = json.loads((tmp_path / "semantic_rescue_rereview_requests" / "body.p.07018.json").read_text(encoding="utf-8"))
    assert request_evidence["target_segments"][0]["text"] == "提出问题"
    request_evidence["candidate_binding_sha256"] = "not-the-durable-candidate"
    (tmp_path / "semantic_rescue_rereview_requests" / "body.p.07018.json").unlink()
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, request_evidence)
    blocked_reviewer = FakeReviewer(verdicts=["ACCEPT"])
    blocked = _rereview_stale_final_candidate(
        checkpoint_dir=tmp_path, request=request, candidate=candidate, active=_providers(reviewer=blocked_reviewer),
        view=SimpleNamespace(), known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None, group=group,
    )
    assert blocked.human_required is True
    assert blocked.failure_code == "FINAL_REVIEW_CANDIDATE_BINDING_MISMATCH"
    assert blocked_reviewer.seen == []


def test_post_repair_final_review_rejects_a_stale_group_slice() -> None:
    from booktrans.production_worker.production_checkpoint import _post_repair_final_review_binding

    request = _request(unit="stale-slice", display="body.p.07019")
    candidate = _raw(request, ("当前修复候选",))
    member = SimpleNamespace(
        as_payload=lambda: {"display_id": request.display_id, "unit_uid": request.unit_uid, "candidate_text": "修复前候选"},
    )
    group = SimpleNamespace(
        group_hash="stale-group", full_candidate_span="修复前候选", members=[member],
        as_reviewer_context_payload=lambda: {"full_candidate_span": "修复前候选", "group_hash": "stale-group"},
    )
    with pytest.raises(RuntimeError, match="FINAL_REVIEW_CANDIDATE_BINDING_MISMATCH"):
        _post_repair_final_review_binding(request, candidate, group)


def test_resolution_queue_valid_rereview_gets_one_corrective_repair_without_translation(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _run_resolution_queue_auto_resolution

    request = _request(unit="queue-rereview", display="body.p.07053")
    candidate = _raw(request, ("对于上游项目而言，存在风险。",))
    review = _review(request, "REPAIR", [ReviewIssue(code="SYNTAX_ERROR", affected_segment_indices=[0], reason="subject", repair_instruction="Restore the subject.")])
    mt = FakeMT()
    repair = FakeRepair(text="上游项目本身就是一种风险。")
    reviewer = FakeReviewer(verdicts=["ACCEPT"])
    outcome = _run_resolution_queue_auto_resolution(
        checkpoint_dir=tmp_path, request=request, candidate=candidate, review=review,
        active=_providers(mt=mt, repair=repair, reviewer=reviewer), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None, rebuild_group=lambda repaired: None,
        mode="VALID_REREVIEW_CORRECTIVE",
    )
    assert outcome.final_status == "ACCEPT"
    assert mt.calls == 0 and repair.calls == 1 and len(reviewer.seen) == 1
    assert (tmp_path / "resolution_queue_auto_resolutions" / "body.p.07053.json").is_file()


def test_resolution_queue_canonical_name_fallback_preserves_exact_source(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _run_resolution_queue_auto_resolution

    request = _request(unit="queue-name", display="body.p.07049", texts=("5 Common Vulnerabilities and Exposures",))
    candidate = _raw(request, ("5 常见漏洞与披露",))
    review = _review(request, "REPAIR", [ReviewIssue(code="TERM_MISTRANSLATION", affected_segment_indices=[0], reason="name", repair_instruction="Use canonical name.")])
    source_name = "".join(segment.text for segment in request.source_segments)
    repair = FakeRepair(text=source_name)
    outcome = _run_resolution_queue_auto_resolution(
        checkpoint_dir=tmp_path, request=request, candidate=candidate, review=review,
        active=_providers(repair=repair, reviewer=FakeReviewer(verdicts=["ACCEPT"])), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=_qa_pass, bible=None, rebuild_group=lambda repaired: None,
        mode="CLOSED_WORLD_CANONICAL_NAME",
    )
    assert outcome.final_status == "ACCEPT"
    assert outcome.working is not None and outcome.working.target_segments[0].text == source_name


def test_resolution_queue_disposition_accepts_only_advisory_or_closed_world_reviewer_conflicts() -> None:
    from booktrans.production_worker.production_checkpoint import _resolution_queue_advisory_disposition

    request = _request(unit="queue-disposition", display="body.p.07053")
    punctuation = _review(request, "REPAIR", [ReviewIssue(code="PUNCTUATION_MISMATCH", affected_segment_indices=[0], reason="already present", repair_instruction="Add 。")])
    resolution = {"mode": "VALID_REREVIEW_CORRECTIVE", "working": _raw(request).model_dump(), "qa": _qa_pass(None, request, _raw(request), {request.unit_uid}), "second_review": punctuation.model_dump()}
    assert _resolution_queue_advisory_disposition(resolution)["semantic_terminal"] == "MODEL_ACCEPTED"
    term_conflict = _review(request, "REPAIR", [ReviewIssue(code="UNTRANSLATED_CONTENT", affected_segment_indices=[0], reason="preserve", repair_instruction="Translate.")])
    resolution["mode"] = "CLOSED_WORLD_CANONICAL_NAME"
    resolution["second_review"] = term_conflict.model_dump()
    assert _resolution_queue_advisory_disposition(resolution)["disposition"] == "CLOSED_WORLD_CANONICAL_NAME_AUTHORITY"


def test_only_local_core_clean_syntax_issue_is_eligible_for_deferred_semantic_debt() -> None:
    from booktrans.production_worker.production_checkpoint import _deferred_semantic_debt_eligible
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="debt", display="body.p.07112")
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=_raw(request), working=_raw(request), qa={"passed": True})
    outcome.human_required = True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="SYNTAX_ERROR", affected_segment_indices=[0], reason="order", repair_instruction="Reorder locally.")])
    assert _deferred_semantic_debt_eligible(outcome) is True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="OMISSION", affected_segment_indices=[0], reason="missing", repair_instruction="Restore.")])
    assert _deferred_semantic_debt_eligible(outcome) is False


def test_production_continuity_defers_safe_human_protocol_and_contained_material_debt(tmp_path: Path) -> None:
    """Production seal consumes one safe-head rule; release remains separate."""
    from booktrans.production_worker.production_checkpoint import _deferred_semantic_debt_eligible
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="continuity", display="body.p.07112")
    raw = _raw(request)
    safe_human = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    safe_human.human_required = True
    safe_human.failure_family = "REVIEWER_EXECUTION_FAILURE"
    safe_human.failure_code = "REVIEWER_CONTRACT_INVALID_AFTER_RETRY"
    # A protocol failure has no legal reviewer verdict to turn into a stop.
    assert _deferred_semantic_debt_eligible(safe_human, checkpoint_dir=tmp_path) is True

    safe_repair = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    safe_repair.human_required = True
    safe_repair.failure_family = "REPAIR_EXECUTION_FAILURE"
    safe_repair.failure_code = "REPAIR_CONTRACT_INVALID_AFTER_RETRY"
    safe_repair.review = _review(request, "REPAIR", [
        ReviewIssue(code="INTEGRITY", affected_segment_indices=[0], reason="orphan punctuation", repair_instruction="Remove punctuation."),
    ])
    # A failed proposal did not replace the Core-clean authoritative head.
    assert _deferred_semantic_debt_eligible(safe_repair, checkpoint_dir=tmp_path) is True

    material = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    material.human_required = True
    material.review = _review(request, "REPAIR", [
        ReviewIssue(code="OMISSION", affected_segment_indices=[0], reason="source meaning absent", repair_instruction="Restore it."),
    ])
    # A unique, Core-clean, exact-scope current head with exhausted bounded
    # recovery is release debt, including a localized omission.  It must not
    # let the raw HUMAN_REQUIRED label stop production.
    assert _deferred_semantic_debt_eligible(material, checkpoint_dir=tmp_path) is True

    no_candidate = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=None, working=None, qa={"passed": True})
    no_candidate.human_required = True
    assert _deferred_semantic_debt_eligible(no_candidate, checkpoint_dir=tmp_path) is False


def test_contained_semantic_debt_never_defers_source_ambiguity(tmp_path: Path) -> None:
    """The widened semantic lane does not weaken source-authority stops."""
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="source-ambiguity", display="body.p.071120")
    raw = _raw(request)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    outcome.human_required = True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(
        code="MEANING_AMBIGUITY", affected_segment_indices=[0],
        reason="source evidence is ambiguous", repair_instruction="Do not infer source intent.",
    )])
    assert canonical_production_disposition(outcome, checkpoint_dir=tmp_path)["disposition"] == "HARD_STOP"


def test_isolated_exhausted_material_semantic_debt_defers_production(tmp_path: Path) -> None:
    """Release-blocking local wording does not stop an otherwise safe batch."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="isolated-material", display="body.p.071121")
    raw = _raw(request, ("局部措辞",))
    persist_stage_once(tmp_path, "semantic_rescue_corrective", request.display_id, {
        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE", "repair_applied": True,
    })
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    outcome.human_required = True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0],
        reason="isolated local wording", repair_instruction="Correct wording locally.",
    )])
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    assert decision["reason"] == "SAFE_CURRENT_HEAD_RELEASE_DEBT"


def test_historical_machine_escalation_cannot_override_safe_current_ca_debt(tmp_path: Path) -> None:
    """R5: a stale machine terminal is evidence, never current authority."""
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.production_worker.production_checkpoint import _persist_deferred_semantic_debt

    request = _request(unit="b033-p09986", display="body.p.09986", texts=("style guides for code",))
    raw = _raw(request, ("历史原始候选",))
    historical_machine_candidate = _raw(request, ("历史 machine escalation 候选",))
    current = _raw(request, ("当前权威候选",))
    current_sha = _candidate_binding(current)
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    current_review = _review(request, "REPAIR", [ReviewIssue(
        code="OMISSION", affected_segment_indices=[0],
        reason="contained current semantic omission", repair_instruction="Restore the owned term.",
    )])

    # Candidate Authority has a unique, accepted, Core-clean current head.
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(raw),
        "candidate_binding_sha256": current_sha, "working": current.model_dump(),
        "qa": {"passed": True}, "accepted": True,
    })
    # Exact-head REPAIR plus an existing corrective attempt is safe exhausted
    # semantic debt.  The human packet below deliberately remains stale.
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": current_sha, "request_binding_sha256": request_sha,
        "context_sha256": "current-context", "semantic_group_hash": "current-group",
    })
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {
        "candidate_binding_sha256": current_sha, "context_sha256": "current-context",
        "semantic_group_hash": "current-group", "review": current_review.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_corrective", request.display_id, {
        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE", "repair_applied": True,
    })
    persist_stage_once(tmp_path, "human_escalation_packets", request.display_id, {
        "artifact_type": "PRODUCTION_TRUE_SEMANTIC_HUMAN_PACKET",
        "current_head_sha256": _candidate_binding(historical_machine_candidate),
    })
    persist_stage_once(tmp_path, "human_escalation_assessments", request.display_id, {
        "artifact_type": "PRODUCTION_HUMAN_ESCALATION_ASSESSMENT",
        "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(historical_machine_candidate),
        "recovery_attempted": True,
    })
    persist_stage_once(tmp_path, "human_required_triage", request.display_id, {
        "artifact_type": "HUMAN_REQUIRED_TRIAGE", "unit_uid": request.unit_uid,
        "auto_repair_exhausted": True,
    })

    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw,
        working=historical_machine_candidate, qa={"passed": True},
    )
    outcome.human_required = True
    outcome.final_status = "HUMAN_REQUIRED"
    outcome.semantic_terminal = "HUMAN_REQUIRED"
    outcome.second_review = current_review
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    assert decision["reason"] == "SAFE_CURRENT_HEAD_RELEASE_DEBT"
    assert decision["reason"] != "CURRENT_HEAD_BINDING_MISMATCH"

    # The runtime persistence boundary receives the reprojected current head;
    # RAW and the historical machine packet remain untouched.
    outcome.working = current
    debt = _persist_deferred_semantic_debt(checkpoint_dir=tmp_path, item=outcome, bible=None)
    assert debt["candidate_binding_sha256"] == current_sha
    assert (tmp_path / "resolution_queue_deferred_debts" / f"{request.display_id}.json").is_file()


def _persist_current_bound_corrective_contract_terminal(
    checkpoint: Path, request: CalibrationRequest, current: TranslationResult, *,
    terminal_head: str | None = None, terminal_group: str = "episode-a", include_failure: bool = True,
) -> TranslationResult:
    """Write the existing six-artifact terminal chain used by a corrective episode."""
    current_sha = _candidate_binding(current)
    bound_head = terminal_head or current_sha
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0],
        reason="contained wording", repair_instruction="Correct local wording.",
    )])
    draft = _raw(request, ("corrective draft",))
    persist_stage_once(checkpoint, "semantic_rescue_rereview_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": bound_head, "context_sha256": "ctx", "semantic_group_hash": terminal_group,
    })
    persist_stage_once(checkpoint, "semantic_rescue_rereviews", request.display_id, {
        "candidate_binding_sha256": bound_head, "context_sha256": "ctx", "semantic_group_hash": terminal_group,
        "review": review.model_dump(),
    })
    persist_stage_once(checkpoint, "semantic_rescue_corrective_repairs", request.display_id, {
        "parent_candidate_sha256": bound_head, "request_binding_sha256": request_sha,
        "semantic_group_hash": terminal_group, "working": draft.model_dump(),
    })
    persist_stage_once(checkpoint, "semantic_rescue_corrective_final_review_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": _candidate_binding(draft), "semantic_group_hash": terminal_group,
    })
    if include_failure:
        persist_stage_once(checkpoint, "semantic_rescue_corrective_final_review_failures", request.display_id, {
            "display_id": request.display_id, "unit_uid": request.unit_uid,
            "candidate_binding_sha256": _candidate_binding(draft), "request_binding_sha256": request_sha,
            "semantic_group_hash": terminal_group,
            "failure_code": "REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING",
        })
    persist_stage_once(checkpoint, "semantic_rescue_corrective", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": bound_head, "semantic_group_hash": terminal_group,
        "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED",
    })
    return draft


def test_r6_current_bound_contract_terminal_cannot_resurrect_rereview_trigger(tmp_path: Path) -> None:
    """R6: a validated terminal corrective episode is monotonic on resume."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="b034-terminal", display="body.p.10318")
    current = _raw(request, ("current authoritative",))
    historical = _persist_current_bound_corrective_contract_terminal(tmp_path, request, current)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=current, working=historical, qa={"passed": True})
    outcome.human_required = True
    outcome.failure_family = "REPAIR_EXECUTION_FAILURE"
    outcome.failure_code = "REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING"

    assert _current_recovery_terminal_exists(outcome, checkpoint_dir=tmp_path, current=current) is True
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    assert decision["reason"] == "SAFE_CURRENT_HEAD_RELEASE_DEBT"


def test_r6_stale_or_wrong_episode_contract_terminal_does_not_consume_current_recovery(tmp_path: Path) -> None:
    """R6 negatives: only the exact head and semantic group may become terminal."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="b034-terminal-negative", display="body.p.10318a")
    current = _raw(request, ("current authoritative",))
    stale = _persist_current_bound_corrective_contract_terminal(
        tmp_path, request, current, terminal_head="f" * 64,
    )
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=current, working=stale, qa={"passed": True})
    assert _current_recovery_terminal_exists(outcome, checkpoint_dir=tmp_path, current=current) is False

    # A distinct episode must not be consumed merely because its draft exists.
    wrong_root = tmp_path / "wrong-episode"; wrong_root.mkdir()
    wrong_draft = _persist_current_bound_corrective_contract_terminal(
        wrong_root, request, current, terminal_group="episode-a",
    )
    wrong_outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=current, working=wrong_draft, qa={"passed": True})
    wrong_episode = wrong_root / "semantic_rescue_corrective_final_review_failures" / f"{request.display_id}.json"
    payload = json.loads(wrong_episode.read_text(encoding="utf-8")); payload["semantic_group_hash"] = "episode-b"
    wrong_episode.write_text(json.dumps(payload), encoding="utf-8")
    assert _current_recovery_terminal_exists(wrong_outcome, checkpoint_dir=wrong_root, current=current) is False


def test_r6_executed_without_valid_terminal_keeps_existing_follow_on_transition(tmp_path: Path) -> None:
    """R6: EXECUTED alone cannot be mistaken for the terminal state."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="b034-executed", display="body.p.10318b")
    current = _raw(request, ("current authoritative",))
    historical = _persist_current_bound_corrective_contract_terminal(
        tmp_path, request, current, include_failure=False,
    )
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=current, working=historical, qa={"passed": True})
    assert _current_recovery_terminal_exists(outcome, checkpoint_dir=tmp_path, current=current) is False


def test_exhausted_second_corrective_round_projects_existing_final_review_without_replay(tmp_path: Path) -> None:
    """A prepared round-two draft is not Authority, but its bound final review is terminal evidence."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="b037-round-two", display="body.p.00221")
    current = _raw(request, ("current authoritative",))
    current_sha = _candidate_binding(current)
    request_sha = enrich_request(request, translation_bible=None).request_binding_sha256
    review = _review(request, "REPAIR", [ReviewIssue(
        code="SEMANTIC_MISMATCH", affected_segment_indices=[0],
        reason="contained fidelity issue", repair_instruction="Correct the local wording.",
    )])
    draft = _raw(request, ("prepared corrective",))
    group = "round-two-group"
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": current_sha, "context_sha256": "ctx", "semantic_group_hash": group,
    })
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {
        "candidate_binding_sha256": current_sha, "context_sha256": "ctx", "semantic_group_hash": group,
        "review": review.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_current_head_corrective_round_2", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid, "round": 2,
        "authoritative_parent_candidate_sha256": current_sha,
        "working": draft.model_dump(), "request_binding_sha256": request_sha,
        "semantic_group_hash": group,
    })
    persist_stage_once(tmp_path, "semantic_rescue_current_head_corrective_round_2_final_reviews", request.display_id, {
        "candidate_binding_sha256": _candidate_binding(draft),
        "request_binding_sha256": request_sha, "semantic_group_hash": group,
        "review": review.model_dump(),
    })

    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=current,
        working=current, qa={"passed": True},
    )
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)

    assert decision["disposition"] == "DEFERRED_SEMANTIC_DEBT"
    assert decision["reason"] == "EXHAUSTED_CONTAINED_SEMANTIC_FIDELITY_DEBT"


def test_current_head_rereview_precedes_historical_second_review_for_containment(tmp_path: Path) -> None:
    """A rejected repair's old review cannot turn a local current finding into a stop."""
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="rereview-precedence", display="body.p.071121a")
    raw = _raw(request, ("局部措辞",))
    binding = _candidate_binding(raw)
    current_review = _review(request, "REPAIR", [ReviewIssue(
        code="INTEGRITY", affected_segment_indices=[0],
        reason="stray local punctuation", repair_instruction="Remove the punctuation.",
    )])
    # These two artifacts are the binding-valid current-head rereview.
    persist_stage_once(tmp_path, "semantic_rescue_rereview_requests", request.display_id, {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "candidate_binding_sha256": binding, "context_sha256": "current-context",
        "semantic_group_hash": "current-group",
    })
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {
        "candidate_binding_sha256": binding, "context_sha256": "current-context",
        "semantic_group_hash": "current-group", "review": current_review.model_dump(),
    })
    persist_stage_once(tmp_path, "semantic_rescue_corrective", request.display_id, {
        "artifact_type": "PRODUCTION_SEMANTIC_RESCUE", "repair_applied": True,
    })
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    outcome.human_required = True
    # Historical repair evidence contains a broader terminology disagreement;
    # it is not current authority and must not drive containment.
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(
        code="TERMINOLOGY", affected_segment_indices=[0],
        reason="historical repair review", repair_instruction="Use a different term.",
    )])
    decision = canonical_production_disposition(outcome, checkpoint_dir=tmp_path)
    # The exact current REPAIR wins over stale terminology evidence.  The
    # approved canonical policy now performs its bounded correction before
    # considering release debt; it does not escalate the historical review.
    assert decision["disposition"] == "RECOVERY_REQUIRED"
    assert decision["recovery_family"] == "SEMANTIC_CORRECTIVE"


def test_absent_historical_second_review_is_optional_during_current_head_recovery() -> None:
    """A legacy `second_review: null` cannot crash a current-head replay."""
    assert _optional_review_result(None) is None


def test_untranslated_prose_never_defers_even_when_local(tmp_path: Path) -> None:
    """Actual missing source content remains a fail-closed production boundary."""
    from booktrans.production_worker.pipeline import UnitOutcome

    raw_request = _request(unit="untranslated-local", display="body.p.071122")
    raw = _raw(raw_request, ("untranslated prose",))
    issue = ReviewIssue(
        code="UNTRANSLATED_CONTENT", affected_segment_indices=[0],
        reason="local prose remains untranslated", repair_instruction="Translate this segment.",
    )
    persist_stage_once(tmp_path, "semantic_rescue_corrective", raw_request.display_id, {"artifact_type": "PRODUCTION_SEMANTIC_RESCUE"})
    local = UnitOutcome(request=raw_request, decision=route_semantic_unit(raw_request), raw=raw, working=raw, qa={"passed": True})
    local.human_required = True; local.second_review = _review(raw_request, "REPAIR", [issue])
    assert canonical_production_disposition(local, checkpoint_dir=tmp_path)["disposition"] == "HARD_STOP"

    continuation_request = raw_request.model_copy(update={"continuation_hint": True})
    continuation = UnitOutcome(request=continuation_request, decision=route_semantic_unit(continuation_request), raw=raw, working=raw, qa={"passed": True})
    continuation.human_required = True; continuation.second_review = _review(continuation_request, "REPAIR", [issue])
    assert canonical_production_disposition(continuation, checkpoint_dir=tmp_path)["disposition"] == "HARD_STOP"


def test_deterministic_qa_failure_never_uses_textual_debt_containment(tmp_path: Path) -> None:
    from booktrans.production_worker.pipeline import UnitOutcome

    request = _request(unit="deterministic-hard", display="body.p.071123")
    raw = _raw(request)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": False})
    outcome.human_required = True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(
        code="INTEGRITY", affected_segment_indices=[0], reason="text label only", repair_instruction="Fix.",
    )])
    assert canonical_production_disposition(outcome, checkpoint_dir=tmp_path)["reason"] == "CORE_QA_OR_PROTECTED_INTEGRITY_FAILED"


def test_core_clean_structural_misalignment_repair_is_release_debt_not_a_hard_stop() -> None:
    from booktrans.production_worker.production_checkpoint import _deferred_semantic_debt_eligible
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="segment-debt", display="body.p.07113", texts=("one", "two"))
    raw = _raw(request, ("完整含义", "仍完整。"))
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa={"passed": True})
    outcome.human_required = True
    outcome.second_review = _review(request, "REPAIR", [
        ReviewIssue(code="MISALIGNMENT", affected_segment_indices=[0, 1], reason="physical distribution only", repair_instruction="optional alignment"),
        ReviewIssue(code="PUNCTUATION_SPACE", affected_segment_indices=[1], reason="spacing", repair_instruction="optional spacing"),
    ])
    assert _deferred_semantic_debt_eligible(outcome) is True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="SEGMENT_MISALIGNMENT_DUPLICATION", affected_segment_indices=[0, 1], reason="meaning duplicated", repair_instruction="remove duplicate")])
    assert _deferred_semantic_debt_eligible(outcome) is False


def test_bounded_queue_localized_semantic_mismatch_is_deferred_not_accepted() -> None:
    from booktrans.production_worker.production_checkpoint import _deferred_semantic_debt_eligible
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="debt-queue", display="body.p.07189")
    raw = _raw(request)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa=_qa_pass(None, request, raw, set()))
    outcome.human_required = True
    outcome.repair_applied = True
    outcome.repair_policy_id = "RESOLUTION_QUEUE_AUTO_RESOLUTION"
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="SEMANTIC_MISMATCH", affected_segment_indices=[0], reason="local modality", repair_instruction="Clarify locally.")])
    assert _deferred_semantic_debt_eligible(outcome) is True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="OMISSION", affected_segment_indices=[0], reason="missing", repair_instruction="Restore.")])
    assert _deferred_semantic_debt_eligible(outcome) is False


def test_localized_fidelity_addition_is_release_debt_not_a_production_hard_block() -> None:
    from booktrans.production_worker.production_checkpoint import _deferred_semantic_debt_eligible
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="debt-fidelity", display="body.p.07381")
    raw = _raw(request)
    outcome = UnitOutcome(request=request, decision=route_semantic_unit(request), raw=raw, working=raw, qa=_qa_pass(None, request, raw, set()))
    outcome.human_required = True
    outcome.repair_applied = True
    outcome.repair_policy_id = "NUMERIC_REPRESENTATION"
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="FIDELITY_ADDED_MEANING", affected_segment_indices=[0], reason="local expansion", repair_instruction="Remove expansion.")])
    assert _deferred_semantic_debt_eligible(outcome) is True


def test_candidate_authority_rejects_stale_branch_and_keeps_raw_head() -> None:
    from booktrans.production_worker.candidate_authority import CandidateMutation, candidate_sha, resolve_authoritative_candidate

    request = _request(unit="lineage", display="body.p.07249")
    raw = _raw(request, ("2.5 亿",))
    stale = _raw(request, ("250 million",))
    resolved = resolve_authoritative_candidate(raw, [
        CandidateMutation("rejected_repair", stale, candidate_sha(stale), accepted=False),
    ])
    assert resolved.candidate_sha256 == candidate_sha(raw)


def test_candidate_authority_rejects_conflicting_accepted_parent() -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError, CandidateMutation, candidate_sha, resolve_authoritative_candidate

    request = _request(unit="lineage", display="body.p.07249")
    raw = _raw(request, ("root",))
    stale = _raw(request, ("stale",))
    with pytest.raises(CandidateAuthorityError, match="CANDIDATE_LINEAGE_PARENT_MISMATCH"):
        resolve_authoritative_candidate(raw, [CandidateMutation("stale", stale, candidate_sha(stale), accepted=True)])


def test_candidate_authority_accepts_only_a_bound_repair_as_the_unique_head() -> None:
    from booktrans.production_worker.candidate_authority import CandidateMutation, candidate_sha, resolve_authoritative_candidate

    request = _request(unit="lineage", display="body.p.07249")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("已修复",))
    resolved = resolve_authoritative_candidate(raw, [
        CandidateMutation("accepted_repair", repaired, candidate_sha(raw), accepted=True),
    ])
    assert resolved.candidate_sha256 == candidate_sha(repaired)
    assert resolved.lineage[-1]["status"] == "ACCEPTED_HEAD"


def test_candidate_authority_human_approval_precedes_model_candidates() -> None:
    from booktrans.production_worker.candidate_authority import CandidateMutation, candidate_sha, resolve_authoritative_candidate

    request = _request(unit="lineage", display="body.p.07249")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("模型修复",))
    approved = _raw(request, ("人工批准",))
    resolved = resolve_authoritative_candidate(raw, [
        CandidateMutation("accepted_repair", repaired, candidate_sha(raw), accepted=True),
        CandidateMutation("human_approved", approved, None, accepted=True, human_approved=True),
    ])
    assert resolved.candidate_sha256 == candidate_sha(approved)
    assert resolved.lineage[-1]["status"] == "HUMAN_APPROVED_HEAD"


def test_candidate_authority_rejects_a_bound_human_candidate_from_a_stale_parent() -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError, CandidateMutation, candidate_sha, resolve_authoritative_candidate

    request = _request(unit="lineage", display="body.p.07381")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("模型修复",))
    approved = _raw(request, ("人工批准",))
    with pytest.raises(CandidateAuthorityError, match="CANDIDATE_LINEAGE_PARENT_MISMATCH"):
        resolve_authoritative_candidate(raw, [
            CandidateMutation("accepted_repair", repaired, candidate_sha(raw), accepted=True),
            CandidateMutation("human_approved", approved, candidate_sha(raw), accepted=True, human_approved=True),
        ])


def test_checkpoint_authority_rejects_normal_mutation_without_parent_binding(tmp_path: Path) -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError
    from booktrans.production_worker.production_checkpoint import _resolve_checkpoint_authoritative_candidate
    from booktrans.production_worker.unit_store import persist_stage_once

    request = _request(unit="lineage", display="body.p.07381")
    raw = _raw(request, ("1万个",))
    derived = _raw(request, ("10,000",))
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, {
        "working": derived.model_dump(),
        "candidate_binding_sha256": "not-the-candidate-hash",
        "accepted": True,
        "qa": {"passed": True},
    })
    with pytest.raises(CandidateAuthorityError, match="ACCEPTED_CANDIDATE_MUTATION_BINDING_MISMATCH"):
        _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw)


def test_numeric_mutation_requires_materialized_localized_number() -> None:
    from booktrans.production_worker.production_checkpoint import _numeric_mutation_materialized

    request = _request(unit="numeric-materialization", display="body.p.07382")
    parent = _raw(request, ("超过 20 亿台设备",))
    phantom = _raw(request, ("超过 two billion 台设备",))
    assert _numeric_mutation_materialized(parent, phantom) is False
    assert _numeric_mutation_materialized(parent, _raw(request, ("超过 20 亿台设备",))) is True


@pytest.mark.parametrize(("source", "candidate", "expected"), [
    (("celebrity, ", "29"), ("名人， ", "]"), ("名人， ", "29")),
    (("term, 48 70",), ("术语，",), ("术语, 48 70",)),
    (("term, 336-339",), ("术语，",), ("术语, 336-339",)),
])
def test_index_locator_materialization_restores_exact_source_owned_locator(source, candidate, expected) -> None:
    from booktrans.production_worker.deterministic_recovery import materialize_index_locator

    request = _request(unit="index-locator", display="body.p.08697", texts=source)
    materialized = materialize_index_locator(request, _raw(request, candidate))
    assert materialized is not None
    assert tuple(segment.text for segment in materialized.target_segments) == expected


def test_index_locator_materialization_is_idempotent_and_rejects_conflicting_locator() -> None:
    from booktrans.production_worker.deterministic_recovery import materialize_index_locator

    request = _request(unit="index-locator", display="body.p.08697", texts=("term, 29",))
    exact = _raw(request, ("术语, 29",))
    assert materialize_index_locator(request, exact) == exact
    assert materialize_index_locator(request, _raw(request, ("术语, 30",))) is None


def test_index_locator_materialization_tolerates_exact_prior_locator_with_delimiter() -> None:
    from booktrans.production_worker.deterministic_recovery import materialize_index_locator

    request = _request(unit="index-locator-delimiter", display="body.p.08815", texts=("term, ", "64 ", "codelabs, ", "60"))
    candidate = _raw(request, ("术语，", "64，", "代码实验室，", "]"))
    materialized = materialize_index_locator(request, candidate)
    assert materialized is not None
    assert tuple(segment.text for segment in materialized.target_segments) == ("术语，", "64，", "代码实验室，", "60")


def test_index_locator_materialization_pipeline_excludes_non_index_entries() -> None:
    from booktrans.production_worker.production_checkpoint import _index_locator_materialization_eligible

    non_index = SimpleNamespace(
        continuation_relation="BODY_CONTINUATION",
        continuation_review_rules={"numeric_locator_owner": "HARNESS_DETERMINISTIC"},
    )
    index = SimpleNamespace(
        continuation_relation="INDEX_ENTRY",
        continuation_review_rules={"numeric_locator_owner": "HARNESS_DETERMINISTIC"},
    )
    qa = {"diagnostics": [{"code": "NUMBER_PRESERVATION"}]}
    assert _index_locator_materialization_eligible(non_index, qa) is False
    assert _index_locator_materialization_eligible(index, qa) is True


def _index_owned_finalization_group(request: CalibrationRequest, candidate: TranslationResult, *, index: bool = True):
    text = "".join(segment.text for segment in candidate.target_segments)
    member = SimpleNamespace(as_payload=lambda: {
        "display_id": request.display_id, "unit_uid": request.unit_uid, "candidate_text": text,
    })
    return SimpleNamespace(
        continuation_relation="INDEX_ENTRY" if index else "BODY_CONTINUATION",
        continuation_review_rules={"numeric_locator_owner": "HARNESS_DETERMINISTIC"},
        group_hash="index-owned-finalization",
        full_candidate_span=text,
        members=[member],
        as_reviewer_context_payload=lambda: {
            "group_hash": "index-owned-finalization", "full_candidate_span": text,
        },
    )


def _index_owned_qa(view, request, candidate, known):
    first, second = (segment.text for segment in candidate.target_segments)
    passed = "244" not in first and second.strip() == "244"
    return {
        "unit_uid": request.unit_uid, "display_id": request.display_id,
        "passed": passed,
        "diagnostics": [] if passed else [{"code": "NUMBER_PRESERVATION"}],
    }


def test_prepublication_index_owned_finalization_composes_without_publishing_intermediate() -> None:
    from booktrans.production_worker.production_checkpoint import finalize_recovery_candidate

    request = _request(unit="index-finalize", display="body.p.08731", texts=("naming tests, ", "244"))
    base = _raw(request, (", 244", ","))
    semantic_repair = _raw(request, ("根据被测行为命名测试，244", ","))
    final, qa, _context, binding = finalize_recovery_candidate(
        request=request, base=base, candidate=semantic_repair,
        parent_candidate_sha256=_candidate_binding(base), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=_index_owned_qa,
        group=_index_owned_finalization_group(request, semantic_repair),
        rebuild_group=lambda current: _index_owned_finalization_group(request, current),
        allow_index_owned_normalization=True,
    )
    assert tuple(segment.text for segment in final.target_segments) == ("根据被测行为命名测试，", "244")
    assert qa["passed"] is True
    assert binding["candidate_binding_sha256"] == _candidate_binding(final)
    assert _candidate_binding(final) != _candidate_binding(semantic_repair)


def test_prepublication_index_owned_finalization_short_circuits_after_locator_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PASS locator head never needs a synthetic duplication mutation."""
    from booktrans.production_worker import production_checkpoint as checkpoint_module

    request = _request(unit="index-short-circuit", display="body.p.08769", texts=("term, ", "181"))
    base = _raw(request, (", 181", "]("))
    semantic_repair = _raw(request, ("术语， ", "]("))

    def unexpected_duplication(*args, **kwargs):
        raise AssertionError("DUPLICATION_CORRECTION_MUST_NOT_RUN_AFTER_CORE_PASS")

    monkeypatch.setattr(checkpoint_module, "correct_numeric_anchor_duplication", unexpected_duplication)
    final, qa, _context, binding = checkpoint_module.finalize_recovery_candidate(
        request=request, base=base, candidate=semantic_repair,
        parent_candidate_sha256=_candidate_binding(base), view=SimpleNamespace(),
        known_unit_ids={request.unit_uid}, qa=lambda view, req, candidate, known: {
            "unit_uid": req.unit_uid, "display_id": req.display_id,
            "passed": candidate.target_segments[1].text.strip() == "181",
            "diagnostics": [] if candidate.target_segments[1].text.strip() == "181" else [{"code": "NUMBER_PRESERVATION"}],
        },
        group=_index_owned_finalization_group(request, semantic_repair),
        rebuild_group=lambda current: _index_owned_finalization_group(request, current),
        allow_index_owned_normalization=True,
    )
    assert tuple(segment.text for segment in final.target_segments) == ("术语， ", "181")
    assert qa["passed"] is True
    assert binding["candidate_binding_sha256"] == _candidate_binding(final)


def test_prepublication_index_owned_finalization_fails_closed_when_final_core_still_fails() -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError
    from booktrans.production_worker.production_checkpoint import finalize_recovery_candidate

    request = _request(unit="index-finalize-fail", display="body.p.08731", texts=("term, ", "244"))
    base = _raw(request, (", 244", ","))
    semantic_repair = _raw(request, ("术语，244", ","))
    with pytest.raises(CandidateAuthorityError, match="RECOVERY_CANDIDATE_CORE_QA_FAILED"):
        finalize_recovery_candidate(
            request=request, base=base, candidate=semantic_repair,
            parent_candidate_sha256=_candidate_binding(base), view=SimpleNamespace(),
            known_unit_ids={request.unit_uid}, qa=lambda *args: {"passed": False, "diagnostics": [{"code": "NUMBER_PRESERVATION"}]},
            group=_index_owned_finalization_group(request, semantic_repair),
            allow_index_owned_normalization=True,
        )


def test_prepublication_index_owned_finalization_is_idempotent_and_excludes_non_index() -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError
    from booktrans.production_worker.production_checkpoint import finalize_recovery_candidate

    request = _request(unit="index-finalize-replay", display="body.p.08731", texts=("term, ", "244"))
    base = _raw(request, (", ", "244"))
    group = _index_owned_finalization_group(request, base)
    first = finalize_recovery_candidate(
        request=request, base=base, candidate=base, parent_candidate_sha256=_candidate_binding(base),
        view=SimpleNamespace(), known_unit_ids={request.unit_uid}, qa=_index_owned_qa,
        group=group, rebuild_group=lambda current: _index_owned_finalization_group(request, current),
        allow_index_owned_normalization=True,
    )
    replay = finalize_recovery_candidate(
        request=request, base=base, candidate=base, parent_candidate_sha256=_candidate_binding(base),
        view=SimpleNamespace(), known_unit_ids={request.unit_uid}, qa=_index_owned_qa,
        group=group, rebuild_group=lambda current: _index_owned_finalization_group(request, current),
        allow_index_owned_normalization=True,
    )
    assert _candidate_binding(first[0]) == _candidate_binding(replay[0]) == _candidate_binding(base)
    unsafe = _raw(request, (", 244", ","))
    with pytest.raises(CandidateAuthorityError, match="RECOVERY_CANDIDATE_CORE_QA_FAILED"):
        finalize_recovery_candidate(
            request=request, base=base, candidate=unsafe, parent_candidate_sha256=_candidate_binding(base),
            view=SimpleNamespace(), known_unit_ids={request.unit_uid}, qa=_index_owned_qa,
            group=_index_owned_finalization_group(request, unsafe, index=False),
            allow_index_owned_normalization=True,
        )


def test_unmaterialized_numeric_mutation_fails_closed_but_cannot_block_bound_human_fix(tmp_path: Path) -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError, candidate_sha
    from booktrans.production_worker.production_checkpoint import _assert_accepted_candidate_mutation_binding

    request = _request(unit="numeric-human", display="body.p.07383")
    raw = _raw(request, ("超过 20 亿台设备",))
    phantom = _raw(request, ("超过 two billion 台设备",))
    provider = enrich_request(request, translation_bible=None)
    record = {
        "display_id": request.display_id, "unit_uid": request.unit_uid,
        "source_sha256": request.source_sha256, "selection_sha256": "selection",
        "request_binding_sha256": provider.request_binding_sha256,
        "parent_candidate_sha256": candidate_sha(raw),
        "candidate_binding_sha256": candidate_sha(phantom),
        "mutation_kind": "NUMERIC_REPRESENTATION", "working": phantom.model_dump(),
        "qa": {"passed": True}, "accepted": True,
    }
    persist_stage_once(tmp_path, "accepted_candidate_mutations", request.display_id, record)
    with pytest.raises(CandidateAuthorityError, match="MUTATION_MATERIALIZATION_MISMATCH"):
        _assert_accepted_candidate_mutation_binding(
            checkpoint_dir=tmp_path, request=request, raw=raw,
            source_sha256=request.source_sha256, selection_sha256="selection", bible=None,
        )
    human = _raw(request, ("超过 20 亿台设备",))
    persist_stage_once(tmp_path, "human_approved_candidates", request.display_id, {
        "semantic_terminal": "HUMAN_APPROVED",
        "parent_candidate_sha256": candidate_sha(phantom),
        "candidate_binding_sha256": candidate_sha(human),
    })
    _assert_accepted_candidate_mutation_binding(
        checkpoint_dir=tmp_path, request=request, raw=raw,
        source_sha256=request.source_sha256, selection_sha256="selection", bible=None,
    )


def _d050_recovery_attempt_record(request: CalibrationRequest, parent: TranslationResult, candidate: TranslationResult) -> dict[str, object]:
    from booktrans.production_worker.production_checkpoint import (
        D050_EXACT_MARKER_RECOVERY_FAMILY,
        _candidate_binding,
    )

    candidate_sha = _candidate_binding(candidate)
    return {
        "artifact_type": "PRODUCTION_D050_EXACT_MARKER_RECOVERY_ATTEMPT",
        "schema_version": 1,
        "recovery_family": D050_EXACT_MARKER_RECOVERY_FAMILY,
        "display_id": request.display_id,
        "unit_uid": request.unit_uid,
        "parent_candidate_sha256": _candidate_binding(parent),
        "candidate_binding_sha256": candidate_sha,
        "working": candidate.model_dump(),
        "qa": {"passed": True},
        "qa_candidate_binding_sha256": candidate_sha,
        "final_review": _review(request, "ACCEPT").model_dump(),
        "final_review_candidate_binding_sha256": candidate_sha,
        "request_binding_sha256": "bound-request",
        "final_review_request_binding_sha256": "bound-request",
        "final_review_accepted": True,
        "accepted": True,
    }


def test_d050_recovery_attempt_consumption_is_family_scoped(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import (
        D050_EXACT_MARKER_RECOVERY_STAGE,
        _d050_exact_marker_recovery_consumed,
    )

    request = _request(unit="d050-family", display="body.p.07390")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("含19标记",))
    # Historical generic rescue evidence is not a D050 replay consumption.
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {"mode": "SEMANTIC_REPAIR"})
    assert not _d050_exact_marker_recovery_consumed(tmp_path, request.display_id)
    persist_stage_once(tmp_path, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id, _d050_recovery_attempt_record(request, raw, repaired))
    assert _d050_exact_marker_recovery_consumed(tmp_path, request.display_id)


def test_checkpoint_authority_accepts_only_bound_qa_and_final_reviewed_d050_attempt(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import (
        D050_EXACT_MARKER_RECOVERY_STAGE,
        _candidate_binding,
        _resolve_checkpoint_authoritative_candidate,
    )

    request = _request(unit="d050-authority", display="body.p.07391")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("含19标记",))
    persist_stage_once(tmp_path, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id, _d050_recovery_attempt_record(request, raw, repaired))
    resolved = _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw)
    assert resolved.candidate_sha256 == _candidate_binding(repaired)
    assert resolved.lineage[-1]["artifact"] == "d050_exact_marker_recovery_attempt"


def test_checkpoint_authority_does_not_promote_d050_attempt_without_bound_qa_or_final_review(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import (
        D050_EXACT_MARKER_RECOVERY_STAGE,
        _candidate_binding,
        _resolve_checkpoint_authoritative_candidate,
    )

    request = _request(unit="d050-qa-final", display="body.p.07392")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("含19标记",))
    attempt = _d050_recovery_attempt_record(request, raw, repaired)
    attempt["qa"] = {"passed": False}
    attempt["accepted"] = False
    persist_stage_once(tmp_path, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id, attempt)
    assert _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw).candidate_sha256 == _candidate_binding(raw)


def test_checkpoint_authority_does_not_promote_d050_attempt_without_final_review(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import (
        D050_EXACT_MARKER_RECOVERY_STAGE,
        _candidate_binding,
        _resolve_checkpoint_authoritative_candidate,
    )

    request = _request(unit="d050-final", display="body.p.07394")
    raw = _raw(request, ("RAW",))
    repaired = _raw(request, ("含19标记",))
    attempt = _d050_recovery_attempt_record(request, raw, repaired)
    attempt["final_review"] = None
    attempt["final_review_candidate_binding_sha256"] = None
    attempt["final_review_accepted"] = False
    attempt["accepted"] = False
    persist_stage_once(tmp_path, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id, attempt)
    assert _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw).candidate_sha256 == _candidate_binding(raw)


def test_checkpoint_authority_rejects_d050_attempt_from_stale_parent(tmp_path: Path) -> None:
    from booktrans.production_worker.candidate_authority import CandidateAuthorityError
    from booktrans.production_worker.production_checkpoint import (
        D050_EXACT_MARKER_RECOVERY_STAGE,
        _candidate_binding,
        _resolve_checkpoint_authoritative_candidate,
    )

    request = _request(unit="d050-parent", display="body.p.07393")
    raw = _raw(request, ("RAW",))
    stale = _raw(request, ("STALE",))
    repaired = _raw(request, ("含19标记",))
    attempt = _d050_recovery_attempt_record(request, raw, repaired)
    attempt["parent_candidate_sha256"] = _candidate_binding(stale)
    persist_stage_once(tmp_path, D050_EXACT_MARKER_RECOVERY_STAGE, request.display_id, attempt)
    with pytest.raises(CandidateAuthorityError, match="CANDIDATE_LINEAGE_PARENT_MISMATCH"):
        _resolve_checkpoint_authoritative_candidate(tmp_path, request.display_id, raw)


def test_candidate_authority_excludes_legacy_unbound_candidate_without_forking_raw() -> None:
    from booktrans.production_worker.candidate_authority import CandidateMutation, candidate_sha, resolve_authoritative_candidate

    request = _request(unit="legacy", display="body.p.07248")
    raw = _raw(request, ("RAW",))
    old_working = _raw(request, ("旧派生候选",))
    resolved = resolve_authoritative_candidate(raw, [
        CandidateMutation(
            "legacy_queue_repair", old_working, candidate_sha(old_working), accepted=True, legacy_compatible=True,
        ),
    ])
    assert resolved.candidate_sha256 == candidate_sha(raw)
    assert resolved.lineage[-1]["status"] == "LEGACY_UNBOUND_DERIVED"


def test_current_acquisition_readiness_defers_raw_qa_until_all_current_rows_exist() -> None:
    """Fresh/partial CURRENT checkpoints acquire first; QA cannot run early."""
    from booktrans.production_worker.production_checkpoint import _missing_effective_acquisitions

    first = _request(unit="acq-first", display="body.p.072481")
    second = _request(unit="acq-second", display="body.p.072482")
    first_raw = _raw(first)
    second_raw = _raw(second)

    assert _missing_effective_acquisitions([first, second], {}) == [first, second]
    assert _missing_effective_acquisitions([first, second], {first.unit_uid: first_raw}) == [second]
    assert _missing_effective_acquisitions(
        [first, second], {first.unit_uid: first_raw, second.unit_uid: second_raw},
    ) == []


def test_current_acquisition_readiness_rejects_nonmember_or_incomplete_set() -> None:
    """Only the exact N/N effective acquisition set may reach RAW-QA."""
    from booktrans.production_worker.production_checkpoint import _missing_effective_acquisitions

    first = _request(unit="acq-bound-first", display="body.p.072483")
    second = _request(unit="acq-bound-second", display="body.p.072484")
    foreign = _request(unit="acq-foreign", display="body.p.072485")
    # A foreign row cannot satisfy either current request; N-1 remains missing.
    assert _missing_effective_acquisitions(
        [first, second], {first.unit_uid: _raw(first), foreign.unit_uid: _raw(foreign)},
    ) == [second]


def test_d051_reuse_rebinds_only_an_identical_preapproved_literal_pair(tmp_path: Path) -> None:
    from booktrans.domain.calibration import ExplicitNumericRepresentationEquivalence
    from booktrans.production_worker.production_checkpoint import _reuse_exact_d051_equivalence

    request = _request(unit="current", display="body.p.07249", texts=("250 million lines",))
    candidate = _raw(request, ("2.5 亿行",))
    approved_path = tmp_path / "approved.json"
    approved_path.write_text(json.dumps({
        "artifact_type": "D051_REVIEWED_EQUIVALENCE_APPROVAL", "display_id": "body.p.06997",
        "equivalence": ExplicitNumericRepresentationEquivalence(
            unit_uid="old", exact_source_span="250 million lines", source_numeric_tokens=["250"],
            approved_target_representation="2.5 亿行", reviewed_source_semantic_numeric_value="250000000",
            reviewed_target_semantic_numeric_value="250000000", semantic_unit="lines",
        ).model_dump(),
    }), encoding="utf-8")
    record = _reuse_exact_d051_equivalence(
        approved=json.loads(approved_path.read_text(encoding="utf-8")), approved_path=approved_path,
        request=request, candidate=candidate, bible=None,
    )
    assert record["display_id"] == request.display_id
    assert record["unit_uid"] == request.unit_uid
    assert record["equivalence"]["unit_uid"] == request.unit_uid
    assert record["candidate_binding_sha256"]


def test_rejected_repair_restores_a_complete_last_known_good_candidate_as_deferred_debt() -> None:
    from booktrans.production_worker.production_checkpoint import _deferred_semantic_debt_eligible
    from booktrans.production_worker.pipeline import UnitOutcome
    from booktrans.providers.router import route_semantic_unit

    request = _request(unit="last-known-good", display="body.p.07147")
    raw = _raw(request, ("完整候选。",))
    outcome = UnitOutcome(
        request=request, decision=route_semantic_unit(request), raw=raw, working=raw,
        qa={"passed": True}, recovered_from="last_known_good_rejected_repair",
    )
    outcome.human_required = True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="STRUCTURAL_MISMATCH", affected_segment_indices=[0], reason="local layout", repair_instruction="Integrate MVS.")])
    assert _deferred_semantic_debt_eligible(outcome) is True
    outcome.second_review = _review(request, "REPAIR", [ReviewIssue(code="OMISSION", affected_segment_indices=[0], reason="missing", repair_instruction="Restore.")])
    assert _deferred_semantic_debt_eligible(outcome) is False


def test_reprocess_accept_requires_current_group_envelope(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _reprocess_binding_matches_current_group

    request = _request(unit="group-binding", display="body.p.07104")
    candidate = _raw(request)
    group = SimpleNamespace(
        group_hash="rebuilt-group",
        as_reviewer_context_payload=lambda: {"group_hash": "rebuilt-group", "current_unit_uid": request.unit_uid},
    )
    assert not _reprocess_binding_matches_current_group(tmp_path, request, candidate, group)
    from booktrans.production_worker.production_checkpoint import _candidate_binding, _canonical_sha256

    persist_stage_once(
        tmp_path,
        "reviewer_envelopes",
        f"{request.display_id}.attempt_01",
        {
            "logical_work_order_id": request.display_id,
            "unit_uid": request.unit_uid,
            "candidate_sha256": _candidate_binding(candidate),
            "semantic_group_sha256": group.group_hash,
            "context_sha256": _canonical_sha256(group.as_reviewer_context_payload()),
        },
    )
    assert _reprocess_binding_matches_current_group(tmp_path, request, candidate, group)


def test_context_gap_rereview_supersedes_older_second_review_for_followup_repair(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="latest-rereview", display="body.p.07104")
    working = _raw(request).model_dump()
    old = _review(request, "REPAIR", [ReviewIssue(code="OLD_ISSUE", affected_segment_indices=[0], reason="old", repair_instruction="old")])
    current = _review(request, "REPAIR", [ReviewIssue(code="CURRENT_ISSUE", affected_segment_indices=[0], reason="current", repair_instruction="current")])
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {"review": current.model_dump()})
    stored = {"working": working, "qa": {"passed": True}, "review": old.model_dump(), "second_review": old.model_dump()}
    assert _merge_latest_unit_record(tmp_path, request.display_id, stored)["second_review"]["issues"][0]["code"] == "CURRENT_ISSUE"


def test_later_semantic_rescue_supersedes_older_queue_terminal(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="rescue-order", display="body.p.07114")
    working = _raw(request).model_dump()
    stored = {"working": working, "qa": {"passed": True}, "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True}
    persist_stage_once(tmp_path, "resolution_queue_auto_resolutions", request.display_id, dict(stored))
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {"working": working, "qa": stored["qa"], "final_status": "ACCEPT", "semantic_terminal": "MODEL_ACCEPTED", "human_required": False})
    assert _merge_latest_unit_record(tmp_path, request.display_id, stored)["semantic_terminal"] == "MODEL_ACCEPTED"


def test_accepted_queue_head_is_not_overwritten_by_rejected_semantic_rescue(tmp_path: Path) -> None:
    """Resume must project Candidate Authority's queue head, not stale rescue."""
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="queue-current-head", display="body.p.071141")
    raw = _raw(request, ("历史候选",)).model_dump()
    accepted = _raw(request, ("已验收队列候选",)).model_dump()
    stored = {"working": raw, "qa": {"passed": True}, "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True}
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {
        **stored, "working": raw,
    })
    persist_stage_once(tmp_path, "resolution_queue_auto_resolutions", request.display_id, {
        "working": accepted, "qa": {"passed": True}, "final_status": "ACCEPT",
        "semantic_terminal": "MODEL_ACCEPTED", "human_required": False,
    })
    merged = _merge_latest_unit_record(tmp_path, request.display_id, stored)
    assert merged["working"] == accepted
    assert merged["semantic_terminal"] == "MODEL_ACCEPTED"
    assert merged["recovered_from"] == "resolution_queue_auto_resolution"


def test_core_clean_d050_reprocess_supersedes_a_failed_older_semantic_rescue(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="d050-late-evidence", display="body.p.07349")
    working = _raw(request).model_dump()
    stored = {"working": working, "qa": {"passed": False}, "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True}
    persist_stage_once(tmp_path, "semantic_rescue", request.display_id, {"working": working, "qa": {"passed": False}, "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True})
    persist_stage_once(tmp_path, "d050_preflight_reprocess", request.display_id, {"working": working, "qa": {"passed": True}, "final_status": "ACCEPT", "semantic_terminal": "MODEL_ACCEPTED", "human_required": False})
    merged = _merge_latest_unit_record(tmp_path, request.display_id, stored)
    assert merged["semantic_terminal"] == "MODEL_ACCEPTED"
    assert merged["recovered_from"] == "d050_preflight_reprocess"


def test_rejected_queue_repair_restores_the_bound_last_known_good_candidate(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="queue-rejected-repair", display="body.p.07375")
    safe = _raw(request).model_dump()
    rejected = _raw(request, ("重复的候选。",)).model_dump()
    stored = {"working": safe, "qa": {"passed": True}, "final_status": "HUMAN_REQUIRED", "semantic_terminal": "HUMAN_REQUIRED", "human_required": True}
    persist_stage_once(tmp_path, "resolution_queue_auto_resolutions", request.display_id, {**stored, "working": rejected})
    persist_stage_once(tmp_path, "resolution_queue_rejected_repair_candidates", request.display_id, {**stored, "working": safe, "review": _review(request, "REPAIR", [ReviewIssue(code="SENSE_ERROR", affected_segment_indices=[0], reason="local wording", repair_instruction="polish")]).model_dump(), "second_review": None})
    merged = _merge_latest_unit_record(tmp_path, request.display_id, stored)
    assert merged["working"] == safe
    assert merged["recovered_from"] == "resolution_queue_rejected_repair"


def test_queue_disposition_supersedes_its_rereview_but_not_human_adjudication(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="queue-merge", display="body.p.07053")
    working = _raw(request).model_dump()
    stored = {"working": working, "qa": _qa_pass(None, request, _raw(request), {request.unit_uid}), "semantic_terminal": "HUMAN_REQUIRED", "final_status": "HUMAN_REQUIRED", "human_required": True}
    persist_stage_once(tmp_path, "semantic_rescue_rereviews", request.display_id, {"review": _review(request, "REPAIR").model_dump()})
    persist_stage_once(tmp_path, "resolution_queue_auto_dispositions", request.display_id, {"working": working, "qa": stored["qa"], "final_status": "ACCEPT", "semantic_terminal": "MODEL_ACCEPTED", "human_required": False})
    assert _merge_latest_unit_record(tmp_path, request.display_id, stored)["semantic_terminal"] == "MODEL_ACCEPTED"
    persist_stage_once(tmp_path, "human_approved_candidates", request.display_id, {"working": working, "qa": stored["qa"]})
    assert _merge_latest_unit_record(tmp_path, request.display_id, stored)["semantic_terminal"] == "HUMAN_APPROVED"


def test_human_approved_candidate_materializes_over_historical_authority_sidecar(tmp_path: Path) -> None:
    from booktrans.production_worker.production_checkpoint import _merge_latest_unit_record

    request = _request(unit="human-materialized", display="body.p.07054")
    raw = _raw(request, ("旧文本",))
    human = _raw(request, ("人工批准的新文本",))
    qa = _qa_pass(None, request, raw, {request.unit_uid})
    stored = {"working": raw.model_dump(), "qa": qa, "semantic_terminal": "HUMAN_REQUIRED", "final_status": "HUMAN_REQUIRED", "human_required": True}
    persist_stage_once(tmp_path, "authoritative_candidate_states", request.display_id, {"working": raw.model_dump(), "qa": qa, "candidate_binding_sha256": _candidate_binding(raw)})
    persist_stage_once(tmp_path, "human_approved_candidates", request.display_id, {"working": human.model_dump(), "qa": qa, "candidate_binding_sha256": _candidate_binding(human), "parent_candidate_sha256": _candidate_binding(raw)})
    merged = _merge_latest_unit_record(tmp_path, request.display_id, stored)
    assert merged["semantic_terminal"] == "HUMAN_APPROVED"
    assert merged["working"] == human.model_dump()
