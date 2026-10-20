from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.production_worker.canary import evaluate_canary_gate
from booktrans.production_worker.continuation import recalculate_canary_from_persisted_evidence
from booktrans.production_worker.human_adjudication import (
    HUMAN_ADJUDICATION_SCHEMA_VERSION,
    HumanAdjudicationError,
    HumanAllowedRepair,
    HumanApprovedPatch,
    apply_human_adjudication_to_canary,
    apply_human_adjudication_to_production_checkpoint,
    apply_human_patches,
    auto_repair_exhausted,
    build_human_adjudication,
    candidate_sha,
    semantic_terminal_of,
    validate_human_adjudication,
)
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.semantic_review_run import review_canary_semantic_group_repair
from booktrans.production_worker.unit_store import persist_stage_once
from booktrans.providers.translation_provider import TranslationProviderRequest
from tests.unit.test_semantic_group_repair import (
    SHA,
    SELECTION,
    CountingMax,
    CountingMT,
    ScriptedGroupRepair,
    ScriptedGroupReviewer,
    _binding,
    _pair,
    _pair_requests,
    _qa_fn,
    _raw,
    _request,
    _seed,
    _unit,
)


def _allowed(request: CalibrationRequest) -> HumanAllowedRepair:
    binding = _binding(request)
    return HumanAllowedRepair(
        logical_work_order_id=request.display_id,
        unit_uid=request.unit_uid,
        allowed_segment_indices=[0],
        source_fingerprint=binding.request_binding_sha256,
    )


def _hashes(previous: CalibrationRequest, current: CalibrationRequest, raws: dict[str, TranslationResult]) -> tuple[dict[str, str], dict[str, str]]:
    return (
        {previous.display_id: _binding(previous).request_binding_sha256, current.display_id: _binding(current).request_binding_sha256},
        {previous.display_id: candidate_sha(raws[previous.unit_uid]), current.display_id: candidate_sha(raws[current.unit_uid])},
    )


def test_auto_repair_exhausted_stays_human_required() -> None:
    assert auto_repair_exhausted("REPAIR", repair_applied=True) is True
    assert auto_repair_exhausted("ACCEPT", repair_applied=True) is False
    assert semantic_terminal_of(final_status="HUMAN_REQUIRED", human_required=True) == "HUMAN_REQUIRED"


def test_valid_human_approval_maps_natural_chinese_without_raw_mutation() -> None:
    previous, current = _pair_requests()
    raws = {previous.unit_uid: _raw(previous, "但请记住，libbase 实际上可能是"), current.unit_uid: _raw(current, "绝对任意软件")}
    group = _pair(previous, current)
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raws[previous.unit_uid]), (_binding(current), raws[current.unit_uid])]
    snapshot = raws[current.unit_uid].model_dump()
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason="Avoid mechanical 绝对任意; keep segment ownership.",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
        approved_patches=[
            HumanApprovedPatch(
                logical_work_order_id=current.display_id,
                unit_uid=current.unit_uid,
                index=0,
                target_text="任何参与构建你的依赖网络中两个或多个节点的软件。",
            )
        ],
        approved_group_text="但请记住，libbase 实际上可能是任何参与构建你的依赖网络中两个或多个节点的软件。",
    )
    assert adjudication.schema_version == HUMAN_ADJUDICATION_SCHEMA_VERSION
    validate_human_adjudication(adjudication, group, members, raws=raws)
    repaired = apply_human_patches(members, adjudication)
    assert raws[current.unit_uid].model_dump() == snapshot
    assert repaired[current.display_id].target_segments[0].text == "任何参与构建你的依赖网络中两个或多个节点的软件。"
    assert "绝对" not in repaired[current.display_id].target_segments[0].text


def test_human_approval_cannot_modify_out_of_scope_work_order() -> None:
    previous, current = _pair_requests()
    third = _request(uid="uid-099", display="body.p.00099", text="unrelated.")
    raws = {previous.unit_uid: _raw(previous), current.unit_uid: _raw(current), third.unit_uid: _raw(third)}
    group = _pair(previous, current)
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raws[previous.unit_uid]), (_binding(current), raws[current.unit_uid])]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason="third work order",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
        approved_patches=[
            HumanApprovedPatch(
                logical_work_order_id=third.display_id,
                unit_uid=third.unit_uid,
                index=0,
                target_text="越权",
            )
        ],
    )
    with pytest.raises(HumanAdjudicationError) as raised:
        validate_human_adjudication(adjudication, group, members, raws=raws)
    assert raised.value.code == "REPAIR_CONTRACT_GAP_GROUP_MEMBER_UNKNOWN"


def test_wrong_group_hash_is_rejected() -> None:
    previous, current = _pair_requests()
    raws = {previous.unit_uid: _raw(previous), current.unit_uid: _raw(current)}
    group = _pair(previous, current)
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raws[previous.unit_uid]), (_binding(current), raws[current.unit_uid])]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason="hash",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
        approved_patches=[
            HumanApprovedPatch(logical_work_order_id=current.display_id, unit_uid=current.unit_uid, index=0, target_text="自然中文。")
        ],
    )
    broken = adjudication.model_copy(update={"group_hash": "0" * 64})
    with pytest.raises(HumanAdjudicationError) as raised:
        validate_human_adjudication(broken, group, members, raws=raws)
    assert raised.value.code == "HUMAN_ADJUDICATION_GROUP_HASH_MISMATCH"


def test_human_approval_requires_deterministic_qa(tmp_path: Path) -> None:
    previous, current = _pair_requests()
    raw_previous = _raw(previous, "但请记住，libbase 实际上可能是")
    raw_current = _raw(current, "绝对任意软件")
    output = _seed(tmp_path, [previous, current], [raw_previous, raw_current])
    group = _pair(previous, current)
    raws = {previous.unit_uid: raw_previous, current.unit_uid: raw_current}
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raw_previous), (_binding(current), raw_current)]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason="qa required",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
        approved_patches=[
            HumanApprovedPatch(logical_work_order_id=current.display_id, unit_uid=current.unit_uid, index=0, target_text="任何参与构建你的依赖网络中两个或多个节点的软件。")
        ],
    )

    def fail_qa(view, request, response, known):
        return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": False, "diagnostics": [{"code": "NUMBER_PRESERVATION"}]}

    with pytest.raises(HumanAdjudicationError) as raised:
        apply_human_adjudication_to_canary(
            output_dir=output,
            production_run=tmp_path / "full-book-20260916T000200Z-decb09",
            requests=[previous, current],
            views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
            known_unit_ids={previous.unit_uid, current.unit_uid},
            qa_fn=fail_qa,
            frozen_order=[previous.display_id, current.display_id],
            selection_sha256=SELECTION,
            source_sha256=SHA,
            adjudication=adjudication,
            group=group,
            members=members,
        )
    assert raised.value.code == "HUMAN_ADJUDICATION_QA_FAILED"


def test_production_preserve_current_binds_head_without_creating_candidate(tmp_path: Path) -> None:
    previous, current = _pair_requests()
    raw_previous = _raw(previous)
    raw_current = _raw(current)
    run = tmp_path / "run"
    checkpoint = run / "full_book_batch_023" / "checkpoint_01"
    checkpoint.mkdir(parents=True)
    (checkpoint / "translation_raw_response.jsonl").write_text(
        raw_previous.model_dump_json() + "\n" + raw_current.model_dump_json() + "\n", encoding="utf-8"
    )
    group = _pair(previous, current)
    raws = {previous.unit_uid: raw_previous, current.unit_uid: raw_current}
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raw_previous), (_binding(current), raw_current)]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason="The current authoritative wording is explicitly approved.",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
        approved_patches=[
            HumanApprovedPatch(
                logical_work_order_id=current.display_id,
                unit_uid=current.unit_uid,
                index=0,
                target_text=raw_current.target_segments[0].text,
            )
        ],
    )
    result = apply_human_adjudication_to_production_checkpoint(
        checkpoint_dir=checkpoint,
        production_run=run,
        requests=[previous, current],
        views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, current.unit_uid},
        qa_fn=_qa_fn,
        adjudication=adjudication,
        group=group,
        members=members,
        d050_registry={},
        triage_classification="TRUE_SEMANTIC_HUMAN",
        batch_label="FULL_BOOK_BATCH_023",
        checkpoint="checkpoint_01",
    )
    evidence = json.loads((checkpoint / "human_adjudications" / f"{current.display_id}.json").read_text(encoding="utf-8"))
    assert result["patched"] == []
    assert evidence["approval_mode"] == "PRESERVE_CURRENT"
    assert evidence["approved_candidate_sha256"] == candidate_sha(raw_current)
    assert not (checkpoint / "human_approved_candidates" / f"{current.display_id}.json").exists()


def test_human_approved_overrides_model_second_review_repair(tmp_path: Path) -> None:
    previous, current = _pair_requests()
    raw_previous = _raw(previous, "但请记住，libbase 实际上可能是")
    raw_current = _raw(current, "绝对任意软件")
    output = _seed(tmp_path, [previous, current], [raw_previous, raw_current])
    raw_before = sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    persist_stage_once(
        output,
        "reviews_v3",
        previous.display_id,
        {"artifact_type": "CANARY_REVIEW_RESULT_V3", "schema_version": 1, "verdict": "ACCEPT", "unit_uid": previous.unit_uid, "issues": []},
    )
    reviewer = ScriptedGroupReviewer(["REPAIR", "REPAIR"])
    repair = ScriptedGroupRepair({current.display_id: "绝对任意软件组件"})
    review_canary_semantic_group_repair(
        output_dir=output,
        requests=[previous, current],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=repair),
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, current.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, current.display_id],
        selection_sha256=SELECTION,
        source_sha256=SHA,
        display_ids=[current.display_id],
        d050_registry={},
        context_units=[_unit(previous), _unit(current)],
    )
    group = _pair(previous, current)
    raws = {previous.unit_uid: raw_previous, current.unit_uid: raw_current}
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raw_previous), (_binding(current), raw_current)]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_APPROVED",
        reason="Human terminal after exhausted auto repair.",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
        approved_patches=[
            HumanApprovedPatch(
                logical_work_order_id=current.display_id,
                unit_uid=current.unit_uid,
                index=0,
                target_text="任何参与构建你的依赖网络中两个或多个节点的软件。",
            )
        ],
    )
    apply_human_adjudication_to_canary(
        output_dir=output,
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        requests=[previous, current],
        views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, current.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, current.display_id],
        selection_sha256=SELECTION,
        source_sha256=SHA,
        adjudication=adjudication,
        group=group,
        members=members,
    )
    report = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[previous, current],
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, current.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, current.display_id],
        selection_sha256=SELECTION,
        source_sha256=SHA,
        d050_registry={},
    )
    by_id = {row["display_id"]: row for row in report["unit_results"]}
    assert by_id[current.display_id]["semantic_terminal"] == "HUMAN_APPROVED"
    assert by_id[current.display_id]["human_required"] is False
    assert report["human_approved_count"] == 1
    assert report["unresolved_human_required"] == 0
    assert report["human_escalation_count"] == 1
    assert sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest() == raw_before
    gate = evaluate_canary_gate(report)
    assert gate["checks"]["semantic_terminals_resolved"] is True
    assert "human_required_zero" not in gate["checks"]


def test_human_rejected_remains_blocked(tmp_path: Path) -> None:
    previous, current = _pair_requests()
    raw_previous = _raw(previous)
    raw_current = _raw(current)
    output = _seed(tmp_path, [previous, current], [raw_previous, raw_current])
    persist_stage_once(
        output,
        "reviews_v3",
        previous.display_id,
        {"artifact_type": "CANARY_REVIEW_RESULT_V3", "schema_version": 1, "verdict": "ACCEPT", "unit_uid": previous.unit_uid, "issues": []},
    )
    persist_stage_once(
        output,
        "reviews_v3",
        current.display_id,
        {"artifact_type": "CANARY_REVIEW_RESULT_V3", "schema_version": 1, "verdict": "REPAIR", "unit_uid": current.unit_uid, "issues": []},
    )
    persist_stage_once(
        output,
        "re_reviews_v4",
        current.display_id,
        {"artifact_type": "CANARY_RE_REVIEW_RESULT_V4", "schema_version": 1, "verdict": "REPAIR", "unit_uid": current.unit_uid, "issues": []},
    )
    group = _pair(previous, current)
    raws = {previous.unit_uid: raw_previous, current.unit_uid: raw_current}
    source_hashes, candidate_hashes = _hashes(previous, current, raws)
    members = [(_binding(previous), raw_previous), (_binding(current), raw_current)]
    adjudication = build_human_adjudication(
        group,
        decision="HUMAN_REJECTED",
        reason="Meaning cannot be repaired inside allowed segments.",
        allowed_repairs=[_allowed(previous), _allowed(current)],
        source_hashes=source_hashes,
        original_candidate_hashes=candidate_hashes,
    )
    apply_human_adjudication_to_canary(
        output_dir=output,
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        requests=[previous, current],
        views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, current.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, current.display_id],
        selection_sha256=SELECTION,
        source_sha256=SHA,
        adjudication=adjudication,
        group=group,
        members=members,
    )
    report = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[previous, current],
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={previous.display_id: SimpleNamespace(), current.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, current.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, current.display_id],
        selection_sha256=SELECTION,
        source_sha256=SHA,
        d050_registry={},
    )
    by_id = {row["display_id"]: row for row in report["unit_results"]}
    assert by_id[current.display_id]["semantic_terminal"] == "HUMAN_REJECTED"
    assert by_id[current.display_id]["human_required"] is True
    assert evaluate_canary_gate(report)["passed"] is False
    assert evaluate_canary_gate(report)["checks"]["human_rejected_zero"] is False
