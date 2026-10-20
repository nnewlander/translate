from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.production_worker.group_repair import (
    decide_group_repair_eligibility,
    decide_semantic_group_repair_contract,
)
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.reviewer_context import ReviewerContextUnit
from booktrans.production_worker.semantic_review_group import (
    ROLE_PREVIOUS_SEALED,
    ROLE_PREVIOUS_UNSEALED,
    build_semantic_review_group,
)
from booktrans.production_worker.semantic_review_run import review_canary_semantic_group_repair
from booktrans.production_worker.unit_store import persist_stage_once
from booktrans.providers.qwen_max import (
    GROUP_REPAIR_CONTRACT_SCHEMA_VERSION,
    GROUP_REVIEW_SCHEMA_VERSION,
    STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
    GroupRepairProviderResult,
    GroupReviewIssue,
    GroupReviewResult,
    GroupReviewerProviderResult,
    SemanticGroupAllowedRepair,
    SemanticGroupRepairContract,
    StructuredGroupRepairPatch,
    StructuredGroupRepairResult,
    apply_group_repair,
)
from booktrans.providers.translation_provider import (
    ProviderContractGap,
    TranslationCandidate,
    TranslationProviderRequest,
)


PREVIOUS_TEXT = "keep in mind that libbase might actually be"
CURRENT_TEXT = "absolutely any piece of software involved in the construction of two or more nodes in your dependency network."
SHA = "a" * 64
SELECTION = "b" * 64


def _request(*, uid: str, display: str, text: str, protected: bool = False) -> CalibrationRequest:
    kind = "protected" if protected else "anchored"
    return CalibrationRequest(
        source_sha256=SHA,
        unit_uid=uid,
        display_id=display,
        story="body",
        content_role="body_paragraph",
        source_text=text,
        source_segments=[
            CalibrationSourceSegment(kind=kind, text=text, anchor_ref="a0", island_ref="island.001"),
        ],
    )


def _protected_request(*, uid: str, display: str, plain: str, protected: str) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256=SHA,
        unit_uid=uid,
        display_id=display,
        story="body",
        content_role="body_paragraph",
        source_text=plain + protected,
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text=plain, anchor_ref="a0", island_ref="island.001"),
            CalibrationSourceSegment(kind="protected", text=protected, anchor_ref="a1", island_ref="island.002"),
        ],
    )


def _raw(request: CalibrationRequest, *texts: str) -> TranslationResult:
    values = list(texts) if texts else ["原文候选"]
    while len(values) < len(request.source_segments):
        values.append("原文候选")
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(
                text=values[index],
                kind=segment.kind,
                anchor_ref=segment.anchor_ref,
                island_ref=segment.island_ref,
            )
            for index, segment in enumerate(request.source_segments)
        ],
    )


def _unit(request: CalibrationRequest) -> ReviewerContextUnit:
    return ReviewerContextUnit(
        display_id=request.display_id,
        unit_uid=request.unit_uid,
        story="body",
        part_name="word/document.xml",
        content_role="paragraph",
        block_type="body_paragraph",
        source_text=request.source_text,
        chapter_id="chapter_21",
    )


def _pair(previous: CalibrationRequest, current: CalibrationRequest):
    footnote = ReviewerContextUnit(
        "body.p.06964",
        "uid-064",
        "body",
        "word/document.xml",
        "paragraph",
        "body_paragraph",
        "2  For instance, security bugs, deprecations.",
        "chapter_21",
    )
    return build_semantic_review_group(
        _unit(current),
        [_unit(previous), footnote, _unit(current)],
        footnote_bodies=frozenset({"body.p.06964", "uid-064"}),
        segment_counts={
            previous.display_id: len(previous.source_segments),
            current.display_id: len(current.source_segments),
        },
        unsealed_work_order_ids=frozenset({previous.display_id, current.display_id}),
    )


def _binding(request: CalibrationRequest) -> TranslationProviderRequest:
    return TranslationProviderRequest(request=request)


def _allowed(request: CalibrationRequest, indices: list[int] | None = None) -> SemanticGroupAllowedRepair:
    binding = _binding(request)
    return SemanticGroupAllowedRepair(
        logical_work_order_id=request.display_id,
        unit_uid=request.unit_uid,
        allowed_segment_indices=indices or [index for index, segment in enumerate(request.source_segments) if segment.kind != "protected"],
        source_fingerprint=binding.request_binding_sha256,
    )


def _contract(previous: CalibrationRequest, current: CalibrationRequest, group, *, extra: SemanticGroupAllowedRepair | None = None) -> SemanticGroupRepairContract:
    allowed = [_allowed(previous), _allowed(current)]
    if extra is not None:
        allowed.append(extra)
    return SemanticGroupRepairContract(
        review_group_id=group.semantic_review_group_id,
        member_work_order_ids=list(group.member_work_order_ids),
        member_unit_uids=[member.unit_uid for member in group.members],
        group_hash=group.group_hash,
        source_span_hash=sha256(group.full_source_span.encode("utf-8")).hexdigest(),
        allowed_repairs=allowed,
        immutable_raw_shas={previous.display_id: "raw-prev", current.display_id: "raw-cur"},
        repair_instruction="Redistribute natural Chinese across the unsealed group span.",
    )


def _issue(request: CalibrationRequest, *, indices: list[int] | None = None, instruction: str = "Rewrite the full sentence naturally.") -> GroupReviewIssue:
    return GroupReviewIssue(
        code="SEMANTIC_DRIFT",
        logical_work_order_id=request.display_id,
        unit_uid=request.unit_uid,
        affected_segment_indices=indices if indices is not None else [0],
        reason="Fragment-forced Chinese is mechanical.",
        repair_instruction=instruction,
    )


def _review(group, *issues: GroupReviewIssue, verdict: str = "REPAIR") -> GroupReviewResult:
    return GroupReviewResult(
        schema_version=GROUP_REVIEW_SCHEMA_VERSION,
        review_group_id=group.semantic_review_group_id,
        verdict=verdict,
        issues=list(issues),
    )


def _candidate(request: CalibrationRequest) -> TranslationCandidate:
    binding = _binding(request)
    return TranslationCandidate(
        provider="ALIYUN_DASHSCOPE_QWEN_MAX_REVIEWER",
        model="qwen3.8-max",
        request_binding_sha256=binding.request_binding_sha256,
        translation_options_sha256=binding.translation_options_sha256,
        source_sha256=request.source_sha256,
        unit_uid=request.unit_uid,
        display_id=request.display_id,
        response_text="group-json",
        requested_at="2026-09-17T00:00:00+00:00",
        responded_at="2026-09-17T00:00:02+00:00",
        usage={"total_tokens": 3},
    )


class CountingMT:
    calls = 0

    def translate(self, request):
        self.calls += 1
        raise AssertionError("translation API must not be called")


class CountingMax:
    calls = 0

    def translate_structured(self, request):
        self.calls += 1
        raise AssertionError("structured translation API must not be called")


class ScriptedGroupReviewer:
    def __init__(self, verdicts: list[str], *, issues_fn=None) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0
        self.payloads: list[dict] = []
        self.issues_fn = issues_fn

    def review_group_with_provenance(self, members, *, group_payload, deterministic_qa_summary):
        self.calls += 1
        self.payloads.append(dict(group_payload))
        verdict = self.verdicts.pop(0)
        current = members[-1][0].request
        issues = []
        if verdict == "REPAIR":
            if self.issues_fn is not None:
                issues = self.issues_fn(members, group_payload)
            else:
                issues = [_issue(current)]
        review = GroupReviewResult(
            schema_version=GROUP_REVIEW_SCHEMA_VERSION,
            review_group_id=str(group_payload["semantic_review_group_id"]),
            verdict=verdict,
            issues=issues,
        )
        return GroupReviewerProviderResult(review=review, candidate=_candidate(current))


class ScriptedGroupRepair:
    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = dict(texts)
        self.calls = 0
        self.contracts: list[SemanticGroupRepairContract] = []

    def repair_group_with_provenance(self, members, contract):
        self.calls += 1
        self.contracts.append(contract)
        patches = []
        for provider_request, _current in members:
            request = provider_request.request
            if request.display_id not in self.texts:
                continue
            patches.append(
                StructuredGroupRepairPatch(
                    logical_work_order_id=request.display_id,
                    unit_uid=request.unit_uid,
                    index=0,
                    target_text=self.texts[request.display_id],
                )
            )
        result = StructuredGroupRepairResult(
            schema_version=STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
            review_group_id=contract.review_group_id,
            patches=patches,
        )
        repaired = apply_group_repair(members, contract, result)
        return GroupRepairProviderResult(repaired=repaired, candidate=_candidate(members[-1][0].request))


def _write_jsonl(path: Path, rows: list) -> None:
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def _seed(tmp_path: Path, requests: list[CalibrationRequest], raws: list[TranslationResult]) -> Path:
    (tmp_path / "full-book-20260916T000200Z-decb09" / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "canary"
    output.mkdir()
    _write_jsonl(output / "translation_request.jsonl", requests)
    _write_jsonl(output / "translation_raw_response.jsonl", raws)
    (output / "translation_provider_provenance.jsonl").write_text("{}\n", encoding="utf-8")
    records = [{"unit_uid": item.unit_uid, "display_id": item.display_id, "passed": True, "diagnostics": []} for item in requests]
    while len(records) < 25:
        records.append({"unit_uid": f"pad-{len(records)}", "display_id": f"body.p.{len(records)+1:05d}", "passed": True, "diagnostics": []})
    (output / "translation_raw_qa_report.json").write_text(
        json.dumps({"pass_count": 25, "fail_count": 0, "records": records}),
        encoding="utf-8",
    )
    return output


def _history(output: Path, display_id: str, unit_uid: str) -> dict[str, str]:
    persist_stage_once(output, "reviews", display_id, {"artifact_type": "CANARY_REVIEW_RESULT", "schema_version": 1, "verdict": "REPAIR", "unit_uid": unit_uid, "issues": []})
    persist_stage_once(output, "reviews_v2", display_id, {"artifact_type": "CANARY_REVIEW_RESULT_V2", "schema_version": 1, "verdict": "REPAIR", "unit_uid": unit_uid, "issues": []})
    persist_stage_once(output, "reviews_v3", display_id, {"artifact_type": "CANARY_REVIEW_RESULT_V3", "schema_version": 1, "verdict": "REPAIR", "unit_uid": unit_uid, "issues": []})
    persist_stage_once(output, "repair_candidates_v3", display_id, {"artifact_type": "CANARY_REPAIR_CANDIDATE_V3", "schema_version": 1, "working": {"unit_uid": unit_uid, "target_segments": []}})
    persist_stage_once(output, "re_reviews_v3", display_id, {"artifact_type": "CANARY_RE_REVIEW_RESULT_V3", "schema_version": 1, "verdict": "REPAIR", "unit_uid": unit_uid, "issues": []})
    persist_stage_once(output, "re_reviews_v4", display_id, {"artifact_type": "CANARY_RE_REVIEW_RESULT_V4", "schema_version": 1, "verdict": "REPAIR", "unit_uid": unit_uid, "issues": []})
    return {
        name: sha256((output / name / f"{display_id}.json").read_bytes()).hexdigest()
        for name in ("reviews", "reviews_v2", "reviews_v3", "repair_candidates_v3", "re_reviews_v3", "re_reviews_v4")
    }


def _qa_fn(view, request, response, known):
    return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": True, "diagnostics": []}


def _pair_requests() -> tuple[CalibrationRequest, CalibrationRequest]:
    previous = _request(uid="uid-061", display="body.p.06961", text=PREVIOUS_TEXT)
    current = _request(uid="uid-068", display="body.p.06968", text=CURRENT_TEXT)
    return previous, current


def test_unsealed_continuation_pair_group_repair_allowed() -> None:
    previous, current = _pair_requests()
    group = _pair(previous, current)
    assert group.member_work_order_ids == ("body.p.06961", "body.p.06968")
    assert group.members[0].role == ROLE_PREVIOUS_UNSEALED
    raws = {previous.unit_uid: _raw(previous, "请记住 libbase 实际上可能是"), current.unit_uid: _raw(current, "绝对任何软件组件")}
    eligibility = decide_group_repair_eligibility(
        group,
        frozen_selection_ids={"body.p.06961", "body.p.06968"},
        unsealed_checkpoint_ids={"body.p.06961", "body.p.06968"},
        production_sealed_ids=set(),
        requests={previous.display_id: previous, current.display_id: current},
        raws=raws,
        stored_raw_shas={},
        raw_line_shas={},
    )
    assert eligibility.allowed is True
    assert eligibility.repairable_work_order_ids == ("body.p.06961", "body.p.06968")
    gate = decide_semantic_group_repair_contract(
        group,
        _review(group, _issue(current)),
        members=[(_binding(previous), raws[previous.unit_uid]), (_binding(current), raws[current.unit_uid])],
        raw_shas={"body.p.06961": "raw-prev", "body.p.06968": "raw-cur"},
    )
    assert gate.action == "REPAIR"
    assert gate.contract is not None
    assert gate.contract.schema_version == GROUP_REPAIR_CONTRACT_SCHEMA_VERSION
    assert [item.logical_work_order_id for item in gate.contract.allowed_repairs] == ["body.p.06961", "body.p.06968"]
    assert gate.contract.allowed_repairs[0].allowed_segment_indices == [0]
    assert gate.contract.allowed_repairs[1].allowed_segment_indices == [0]


def test_previous_unsealed_member_permits_continuation_tail_only() -> None:
    previous = CalibrationRequest(
        source_sha256=SHA,
        unit_uid="uid-061",
        display_id="body.p.06961",
        story="body",
        content_role="body_paragraph",
        source_text="Lead sentence. diamond dependency keep in mind that libbase might actually be",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text="Lead sentence. ", anchor_ref="a0", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text="diamond dependency ", anchor_ref="a1", island_ref="island.002"),
            CalibrationSourceSegment(kind="anchored", text="keep in mind that libbase might actually be", anchor_ref="a2", island_ref="island.003"),
        ],
    )
    current = _request(uid="uid-068", display="body.p.06968", text=CURRENT_TEXT)
    group = _pair(previous, current)
    members = [(_binding(previous), _raw(previous, "前", "中", "libbase 实际上可能是")), (_binding(current), _raw(current, "绝对任何软件组件"))]
    gate = decide_semantic_group_repair_contract(
        group,
        _review(group, _issue(current)),
        members=members,
        raw_shas={},
    )
    assert gate.action == "REPAIR"
    by_id = {item.logical_work_order_id: item.allowed_segment_indices for item in gate.contract.allowed_repairs}
    assert by_id[previous.display_id] == [2]
    assert by_id[current.display_id] == [0]
    with pytest.raises(ProviderContractGap) as raised:
        apply_group_repair(
            members,
            gate.contract,
            StructuredGroupRepairResult(
                schema_version=STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
                review_group_id=gate.contract.review_group_id,
                patches=[
                    StructuredGroupRepairPatch(
                        logical_work_order_id=previous.display_id,
                        unit_uid=previous.unit_uid,
                        index=0,
                        target_text="不得改前段",
                    )
                ],
            ),
        )
    assert raised.value.code == "REPAIR_CONTRACT_GAP_SCOPE_EXPANDED"


def test_previous_member_already_sealed_forbids_group_repair() -> None:
    previous, current = _pair_requests()
    group = build_semantic_review_group(
        _unit(current),
        [_unit(previous), _unit(current)],
        footnote_bodies=frozenset(),
        segment_counts={previous.display_id: 1, current.display_id: 1},
    )
    assert group.members[0].role == ROLE_PREVIOUS_SEALED
    eligibility = decide_group_repair_eligibility(
        group,
        frozen_selection_ids={"body.p.06961", "body.p.06968"},
        unsealed_checkpoint_ids={"body.p.06961", "body.p.06968"},
        production_sealed_ids=set(),
        requests={previous.display_id: previous, current.display_id: current},
        raws={previous.unit_uid: _raw(previous), current.unit_uid: _raw(current)},
        stored_raw_shas={},
        raw_line_shas={},
    )
    assert eligibility.allowed is False
    assert eligibility.reason == "GROUP_REPAIR_SEALED_MEMBER"
    production = decide_group_repair_eligibility(
        _pair(previous, current),
        frozen_selection_ids={"body.p.06961", "body.p.06968"},
        unsealed_checkpoint_ids={"body.p.06961", "body.p.06968"},
        production_sealed_ids={"body.p.06961"},
        requests={previous.display_id: previous, current.display_id: current},
        raws={previous.unit_uid: _raw(previous), current.unit_uid: _raw(current)},
        stored_raw_shas={},
        raw_line_shas={},
    )
    assert production.allowed is False
    assert production.reason == "GROUP_REPAIR_PRODUCTION_SEALED"


def test_protected_segment_outside_permitted_scope_is_rejected() -> None:
    previous = _request(uid="uid-061", display="body.p.06961", text=PREVIOUS_TEXT)
    current = _protected_request(uid="uid-068", display="body.p.06968", plain=CURRENT_TEXT, protected="https://example.test")
    group = _pair(previous, current)
    members = [(_binding(previous), _raw(previous)), (_binding(current), _raw(current, "后半", "https://example.test"))]
    gate = decide_semantic_group_repair_contract(
        group,
        _review(group, _issue(current, indices=[1])),
        members=members,
        raw_shas={},
    )
    assert gate.action == "HUMAN_REQUIRED"
    assert gate.reason == "GROUP_REPAIR_PROTECTED_SCOPE"
    contract = _contract(previous, current, group)
    with pytest.raises(ProviderContractGap) as raised:
        apply_group_repair(
            members,
            contract,
            StructuredGroupRepairResult(
                schema_version=STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
                review_group_id=contract.review_group_id,
                patches=[
                    StructuredGroupRepairPatch(
                        logical_work_order_id=current.display_id,
                        unit_uid=current.unit_uid,
                        index=1,
                        target_text="不得改保护段",
                    )
                ],
            ),
        )
    assert raised.value.code == "REPAIR_CONTRACT_GAP_SCOPE_EXPANDED"


def test_repair_attempting_third_work_order_is_rejected() -> None:
    previous, current = _pair_requests()
    third = _request(uid="uid-099", display="body.p.00099", text="unrelated sentence.")
    group = _pair(previous, current)
    members = [(_binding(previous), _raw(previous)), (_binding(current), _raw(current))]
    foreign = GroupReviewIssue(
        code="SEMANTIC_DRIFT",
        logical_work_order_id=third.display_id,
        unit_uid=third.unit_uid,
        affected_segment_indices=[0],
        reason="out of group",
        repair_instruction="Do not touch a third work order.",
    )
    gate = decide_semantic_group_repair_contract(group, _review(group, foreign), members=members, raw_shas={})
    assert gate.action == "HUMAN_REQUIRED"
    assert gate.reason == "GROUP_REPAIR_THIRD_WORK_ORDER"
    contract = _contract(previous, current, group)
    with pytest.raises(ProviderContractGap) as raised:
        apply_group_repair(
            members,
            contract,
            StructuredGroupRepairResult(
                schema_version=STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
                review_group_id=contract.review_group_id,
                patches=[
                    StructuredGroupRepairPatch(
                        logical_work_order_id=third.display_id,
                        unit_uid=third.unit_uid,
                        index=0,
                        target_text="第三工单",
                    )
                ],
            ),
        )
    assert raised.value.code == "REPAIR_CONTRACT_GAP_GROUP_MEMBER_UNKNOWN"


def test_wrong_unit_uid_is_rejected() -> None:
    previous, current = _pair_requests()
    group = _pair(previous, current)
    members = [(_binding(previous), _raw(previous)), (_binding(current), _raw(current))]
    wrong = GroupReviewIssue(
        code="SEMANTIC_DRIFT",
        logical_work_order_id=current.display_id,
        unit_uid="uid-wrong",
        affected_segment_indices=[0],
        reason="uid mismatch",
        repair_instruction="Must bind the exact unit_uid.",
    )
    gate = decide_semantic_group_repair_contract(group, _review(group, wrong), members=members, raw_shas={})
    assert gate.action == "HUMAN_REQUIRED"
    assert gate.reason == "GROUP_REPAIR_UNIT_UID_MISMATCH"
    contract = _contract(previous, current, group)
    with pytest.raises(ProviderContractGap) as raised:
        apply_group_repair(
            members,
            contract,
            StructuredGroupRepairResult(
                schema_version=STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
                review_group_id=contract.review_group_id,
                patches=[
                    StructuredGroupRepairPatch(
                        logical_work_order_id=current.display_id,
                        unit_uid="uid-wrong",
                        index=0,
                        target_text="错误绑定",
                    )
                ],
            ),
        )
    assert raised.value.code == "REPAIR_CONTRACT_GAP_UNIT_UID_MISMATCH"


def test_group_repair_then_qa_pass_then_second_review_accept(tmp_path: Path) -> None:
    previous, current = _pair_requests()
    raw_previous = _raw(previous, "请记住 libbase 实际上可能是")
    raw_current = _raw(current, "绝对任何软件组件")
    output = _seed(tmp_path, [previous, current], [raw_previous, raw_current])
    before_history = _history(output, current.display_id, current.unit_uid)
    raw_before = sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    reviewer = ScriptedGroupReviewer(["REPAIR", "ACCEPT"])
    repair = ScriptedGroupRepair(
        {
            previous.display_id: "请记住，libbase 实际上可能是",
            current.display_id: "依赖网络中参与两个或更多节点构建的任何软件。",
        }
    )
    mt = CountingMT()
    report = review_canary_semantic_group_repair(
        output_dir=output,
        requests=[previous, current],
        providers=WorkerProviders(mt=mt, structured=CountingMax(), reviewer=reviewer, repair=repair),
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
    assert mt.calls == 0
    assert report["translation_api_calls"] == 0
    assert report["reviewer_api_calls"] == 2
    assert report["repair_api_calls"] == 1
    assert report["outcomes"][0]["final_status"] == "ACCEPT"
    assert report["outcomes"][0]["group_second_review"] == "ACCEPT"
    assert (output / "group_reviews" / f"{current.display_id}.json").is_file()
    assert (output / "group_repair_contracts" / f"{current.display_id}.json").is_file()
    assert (output / "group_repair_candidates" / f"{previous.display_id}.json").is_file()
    assert (output / "group_repair_candidates" / f"{current.display_id}.json").is_file()
    assert (output / "group_re_reviews" / f"{current.display_id}.json").is_file()
    after_history = {
        name: sha256((output / name / f"{current.display_id}.json").read_bytes()).hexdigest() for name in before_history
    }
    assert after_history == before_history
    assert sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest() == raw_before
    assert json.loads((output / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines()[1])["target_segments"][0]["text"] == "绝对任何软件组件"


def test_second_review_repair_stays_human_required(tmp_path: Path) -> None:
    previous, current = _pair_requests()
    output = _seed(tmp_path, [previous, current], [_raw(previous), _raw(current)])
    reviewer = ScriptedGroupReviewer(["REPAIR", "REPAIR"])
    repair = ScriptedGroupRepair({previous.display_id: "前半自然中文", current.display_id: "后半自然中文"})
    report = review_canary_semantic_group_repair(
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
    assert repair.calls == 1
    assert reviewer.calls == 2
    assert report["outcomes"][0]["final_status"] == "HUMAN_REQUIRED"
    assert report["outcomes"][0]["group_second_review"] == "REPAIR"


def test_group_repair_leaves_raw_byte_identical() -> None:
    previous, current = _pair_requests()
    original_previous = _raw(previous, "请记住 libbase 实际上可能是")
    original_current = _raw(current, "绝对任何软件组件")
    snapshot_previous = original_previous.model_dump()
    snapshot_current = original_current.model_dump()
    group = _pair(previous, current)
    contract = _contract(previous, current, group)
    repaired = apply_group_repair(
        [(_binding(previous), original_previous), (_binding(current), original_current)],
        contract,
        StructuredGroupRepairResult(
            schema_version=STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION,
            review_group_id=contract.review_group_id,
            patches=[
                StructuredGroupRepairPatch(
                    logical_work_order_id=previous.display_id,
                    unit_uid=previous.unit_uid,
                    index=0,
                    target_text="请记住，libbase 实际上可能是",
                ),
                StructuredGroupRepairPatch(
                    logical_work_order_id=current.display_id,
                    unit_uid=current.unit_uid,
                    index=0,
                    target_text="依赖网络中参与两个或更多节点构建的任何软件。",
                ),
            ],
        ),
    )
    assert original_previous.model_dump() == snapshot_previous
    assert original_current.model_dump() == snapshot_current
    assert repaired[previous.display_id].target_segments[0].text == "请记住，libbase 实际上可能是"
    assert repaired[current.display_id].target_segments[0].text == "依赖网络中参与两个或更多节点构建的任何软件。"
    assert repaired[previous.display_id].unit_uid == previous.unit_uid
    assert repaired[current.display_id].unit_uid == current.unit_uid


def test_historical_canary_06961_06968_group_repair_is_eligible() -> None:
    """Keep this canary fixture independent of later production sealing.

    The fixture proves the eligibility decision from the canary's frozen
    unsealed selection.  Looking up the live campaign's sealed set made this
    historical assertion flip merely because B023 later completed.
    """
    from booktrans.domain.calibration import CalibrationRequest as LiveRequest
    from booktrans.production_worker.semantic_review_run import (
        build_unit_group,
        candidate_text_map,
        context_units_for_order,
        segment_count_map,
    )
    from booktrans.production_worker.selection import load_checkpoint_d050_registry
    from booktrans.production_worker.unit_store import load_raw_by_uid, load_unit_result, unit_path

    canary = Path(r"D:\translate\runs\qwen-production-canary-20260917T085341Z")
    historical_b022 = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
    requests = [LiveRequest.model_validate_json(line) for line in (canary / "translation_request.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {item.display_id: item for item in requests}
    previous = by_id["body.p.06961"]
    current = by_id["body.p.06968"]
    frozen = {item.display_id for item in requests}
    sealed = frozenset()
    raws = load_raw_by_uid(canary / "translation_raw_response.jsonl")
    units = context_units_for_order([item.display_id for item in requests])
    group = build_unit_group(
        current,
        units,
        load_checkpoint_d050_registry(
            historical_b022, "checkpoint_03", batch_label="FULL_BOOK_BATCH_022"
        ),
        candidate_texts=candidate_text_map(requests, raws),
        segment_counts=segment_count_map(requests),
        unsealed_work_order_ids=frozenset(frozen),
        sealed_work_order_ids=sealed,
    )
    assert group.member_work_order_ids == ("body.p.06961", "body.p.06968")
    assert set(group.repairable_work_order_ids) == {"body.p.06961", "body.p.06968"}
    stored = {}
    for member in group.members:
        payload = load_unit_result(unit_path(canary, member.display_id)) or {}
        stored[member.display_id] = str(payload.get("raw_sha256") or "")
    eligibility = decide_group_repair_eligibility(
        group,
        frozen_selection_ids=frozen,
        unsealed_checkpoint_ids=frozen,
        production_sealed_ids=set(sealed),
        requests=by_id,
        raws=raws,
        stored_raw_shas=stored,
        raw_line_shas={},
    )
    assert eligibility.allowed is True, eligibility.reason
    assert (canary / "reviews_v3" / "body.p.06968.json").is_file()
    assert (canary / "re_reviews_v4" / "body.p.06968.json").is_file()


def test_textbox_context_loader_uses_frozen_textbox_family(tmp_path: Path) -> None:
    """Textbox-local ordinals must not be treated as body paragraph numbers."""
    from booktrans.domain.ir import BlockIR, SourceLocator
    from booktrans.production_worker.reviewer_context import build_reviewer_semantic_context
    from booktrans.production_worker.semantic_review_run import context_units_for_order

    blocks = []
    for number, text in (("0001", "The train starts"), ("0002", "at the same"), ("0003", "time and continues.")):
        blocks.append(BlockIR(
            unit_uid=f"uid-{number}", display_id=f"textbox.152.p.{number}",
            story="body", part_name="word/document.xml", block_type="textbox_paragraph",
            content_role="textbox_paragraph",
            locator=SourceLocator(part_name="word/document.xml", story="body", xml_path=f"/p[{number}]"),
            source_plain_text=text, source_hash=f"source-{number}", structure_fingerprint=f"structure-{number}",
        ).model_dump_json())
    path = tmp_path / "blocks.jsonl"
    path.write_text("\n".join(blocks) + "\n", encoding="utf-8")

    units = context_units_for_order(["textbox.152.p.0002"], blocks_path=path)
    current = next(item for item in units if item.display_id == "textbox.152.p.0002")
    context = build_reviewer_semantic_context(current, units)
    assert context.next_semantic_display_id == "textbox.152.p.0003"
    assert context.next_text == "time and continues."
