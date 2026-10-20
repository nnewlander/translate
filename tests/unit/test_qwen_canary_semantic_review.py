from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import ReviewIssue, ReviewResult, ReviewerProviderResult
from booktrans.providers.translation_provider import TranslationCandidate, TranslationProviderRequest
from booktrans.production_worker.continuation import recalculate_canary_from_persisted_evidence
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.reviewer_context import ReviewerContextUnit
from booktrans.production_worker.semantic_review_run import review_canary_units_with_semantic_context
from booktrans.production_worker.unit_store import persist_stage_once


def _request(*, uid: str = "uid-068", display: str = "body.p.06968", sha: str = "a" * 64) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256=sha,
        unit_uid=uid,
        display_id=display,
        story="body",
        content_role="body_paragraph",
        source_text="absolutely any piece of software involved in the construction of two or more nodes.",
        source_segments=[
            CalibrationSourceSegment(
                kind="anchored",
                text="absolutely any piece of software involved in the construction of two or more nodes.",
                anchor_ref="a0",
                island_ref="island.001",
            )
        ],
        previous_text="2  For instance, security bugs, deprecations.",
        next_text="The diamond dependency problem requires three layers.",
        continuation_hint=False,
    )


def _raw(request: CalibrationRequest, text: str = "原文候选") -> TranslationResult:
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment in request.source_segments
        ],
    )


def _candidate(request: CalibrationRequest) -> TranslationCandidate:
    binding = TranslationProviderRequest(request=request)
    return TranslationCandidate(
        provider="ALIYUN_DASHSCOPE_QWEN_MAX_REVIEWER",
        model="qwen3.8-max",
        request_binding_sha256=binding.request_binding_sha256,
        translation_options_sha256=binding.translation_options_sha256,
        source_sha256=request.source_sha256,
        unit_uid=request.unit_uid,
        display_id=request.display_id,
        response_text="review-json",
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


class ScriptedReviewer:
    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0
        self.contexts: list[dict] = []

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.calls += 1
        self.contexts.append(dict(semantic_context or {}))
        verdict = self.verdicts.pop(0)
        issues = []
        if verdict == "REPAIR":
            issues = [ReviewIssue(code="SENSE", affected_segment_indices=[0], reason="narrow", repair_instruction="Restore tone.")]
        review = ReviewResult(
            schema_version="booktrans.structured-review.v1",
            unit_uid=request.request.unit_uid,
            verdict=verdict,
            issues=issues,
        )
        return ReviewerProviderResult(review=review, candidate=_candidate(request.request))


class CountingRepair:
    def repair(self, request, current, contract):
        raise AssertionError("unexpected repair")


def _write_jsonl(path: Path, rows: list) -> None:
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def _seed(tmp_path: Path, request: CalibrationRequest, raw: TranslationResult) -> Path:
    (tmp_path / "full-book-20260916T000200Z-decb09" / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "canary"
    output.mkdir()
    _write_jsonl(output / "translation_request.jsonl", [request])
    _write_jsonl(output / "translation_raw_response.jsonl", [raw])
    (output / "translation_provider_provenance.jsonl").write_text("{}\n", encoding="utf-8")
    records = [{"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": True, "diagnostics": []}]
    while len(records) < 25:
        records.append({"unit_uid": f"pad-{len(records)}", "display_id": f"body.p.{len(records)+1:05d}", "passed": True, "diagnostics": []})
    (output / "translation_raw_qa_report.json").write_text(
        json.dumps({"pass_count": 25, "fail_count": 0, "records": records}),
        encoding="utf-8",
    )
    return output


def _qa_fn(view, request, response, known):
    return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": True, "diagnostics": []}


def _units() -> list[ReviewerContextUnit]:
    return [
        ReviewerContextUnit("body.p.06961", "uid-061", "body", "word/document.xml", "paragraph", "body_paragraph", "libbase might actually be", "chapter_21"),
        ReviewerContextUnit("body.p.06964", "uid-064", "body", "word/document.xml", "paragraph", "body_paragraph", "2  For instance, security bugs, deprecations.", "chapter_21"),
        ReviewerContextUnit("body.p.06968", "uid-068", "body", "word/document.xml", "paragraph", "body_paragraph", "absolutely any piece of software involved in the construction of two or more nodes.", "chapter_21"),
    ]


def _registry():
    return {
        ("body.p.06959", "uid-059"): (
            {
                "verification_status": "VERIFIED",
                "corresponding_footnote_body": {"display_id": "body.p.06964", "unit_uid": "uid-064"},
            },
        )
    }


def test_old_invalid_context_review_is_not_overwritten(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha)
    raw = _raw(request)
    output = _seed(tmp_path, request, raw)
    persist_stage_once(
        output,
        "reviews",
        request.display_id,
        {
            "artifact_type": "CANARY_REVIEW_RESULT",
            "schema_version": 1,
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "verdict": "REPAIR",
            "issues": [],
        },
    )
    persist_stage_once(
        output,
        "reviews_v2",
        request.display_id,
        {
            "artifact_type": "CANARY_REVIEW_RESULT_V2",
            "schema_version": 1,
            "review_context_version": "booktrans.review-context.v2",
            "display_id": request.display_id,
            "unit_uid": request.unit_uid,
            "verdict": "REPAIR",
            "issues": [],
        },
    )
    before = sha256((output / "reviews" / f"{request.display_id}.json").read_bytes()).hexdigest()
    before_v2 = sha256((output / "reviews_v2" / f"{request.display_id}.json").read_bytes()).hexdigest()
    reviewer = ScriptedReviewer(["ACCEPT"])
    review_canary_units_with_semantic_context(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        display_ids=[request.display_id],
        d050_registry=_registry(),
        context_units=_units(),
    )
    after = sha256((output / "reviews" / f"{request.display_id}.json").read_bytes()).hexdigest()
    after_v2 = sha256((output / "reviews_v2" / f"{request.display_id}.json").read_bytes()).hexdigest()
    assert after == before
    assert after_v2 == before_v2
    v2 = json.loads((output / "reviews_v2" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert v2["verdict"] == "REPAIR"
    v3 = json.loads((output / "reviews_v3" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert v3["verdict"] == "ACCEPT"
    assert v3["review_contract_version"] == "booktrans.review-group.v1"
    assert v3["member_work_order_ids"][-1] == request.display_id


def test_reviewer_v2_uses_original_candidate_and_semantic_previous(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha)
    raw = _raw(request, "原文候选")
    output = _seed(tmp_path, request, raw)
    persist_stage_once(
        output,
        "repair_candidates",
        request.display_id,
        {"working": json.loads(_raw(request, "错误上下文修复").model_dump_json())},
    )
    reviewer = ScriptedReviewer(["ACCEPT"])
    mt = CountingMT()
    review_canary_units_with_semantic_context(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=mt, structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        display_ids=[request.display_id],
        d050_registry=_registry(),
        context_units=_units(),
    )
    assert mt.calls == 0
    assert reviewer.calls == 1
    context = reviewer.contexts[0]
    assert context["previous_semantic_unit_uid"] == "uid-061"
    assert context["previous_text"] == "libbase might actually be"
    assert "security bugs" not in (context.get("previous_text") or "")
    assert context["member_work_order_ids"] == ["body.p.06961", "body.p.06968"]
    assert context["repairable_work_order_ids"] == ["body.p.06968"]
    assert context["review_contract_version"] == "booktrans.review-group.v1"
    assert "security bugs" not in (context.get("full_source_span") or "")
    v3 = json.loads((output / "reviews_v3" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert v3["verdict"] == "ACCEPT"
    report = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[request],
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        d050_registry={},
    )
    assert report["translation_api_calls"] == 0
    assert report["unit_results"][0]["final_status"] == "ACCEPT"
    assert report["unit_results"][0]["repair_applied"] is False
    assert json.loads((output / "repair_candidates" / f"{request.display_id}.json").read_text(encoding="utf-8"))["working"]["target_segments"][0]["text"] == "错误上下文修复"


class RecordingRepair:
    def __init__(self) -> None:
        self.calls = 0
        self.unit_uids: list[str] = []

    def repair(self, request, current, contract):
        self.calls += 1
        self.unit_uids.append(contract.unit_uid)
        assert contract.unit_uid == request.request.unit_uid
        assert contract.unit_uid != "uid-061"
        patched = []
        for index, segment in enumerate(current.target_segments):
            text = "仅修改当前单元" if index in contract.permitted_segment_indices else segment.text
            patched.append(segment.model_copy(update={"text": text}))
        return current.model_copy(update={"target_segments": patched})


def _previous_request(*, sha: str) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256=sha,
        unit_uid="uid-061",
        display_id="body.p.06961",
        story="body",
        content_role="body_paragraph",
        source_text="libbase might actually be",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text="libbase might actually be", anchor_ref="a0", island_ref="island.001")
        ],
        previous_text=None,
        next_text="absolutely any piece of software involved in the construction of two or more nodes.",
        continuation_hint=False,
    )


def test_repair_cannot_modify_previous_sealed_unit(tmp_path: Path) -> None:
    sha = "a" * 64
    previous = _previous_request(sha=sha)
    request = _request(sha=sha)
    previous_raw = _raw(previous, "已批准前文")
    raw = _raw(request, "原文候选")
    output = _seed(tmp_path, request, raw)
    with (output / "translation_raw_response.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(previous_raw.model_dump_json() + "\n")
    persist_stage_once(
        output,
        "repair_candidates_v2",
        request.display_id,
        {"working": json.loads(_raw(request, "旧v2修复").model_dump_json())},
    )
    reviewer = ScriptedReviewer(["REPAIR", "ACCEPT"])
    repair = RecordingRepair()
    review_canary_units_with_semantic_context(
        output_dir=output,
        requests=[previous, request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=repair),
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={previous.display_id: SimpleNamespace(), request.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        display_ids=[request.display_id],
        d050_registry=_registry(),
        context_units=_units(),
    )
    assert repair.calls == 1
    assert repair.unit_uids == ["uid-068"]
    assert reviewer.calls == 2
    assert "仅修改当前单元" in (reviewer.contexts[1].get("full_candidate_span") or "")
    assert "原文候选" not in (reviewer.contexts[1].get("full_candidate_span") or "")
    raws = (output / "translation_raw_response.jsonl").read_text(encoding="utf-8")
    assert "已批准前文" in raws
    v2 = json.loads((output / "repair_candidates_v2" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert v2["working"]["target_segments"][0]["text"] == "旧v2修复"
    v3 = json.loads((output / "repair_candidates_v3" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert v3["working"]["target_segments"][0]["text"] == "仅修改当前单元"
    assert v3["repairable_work_order_ids"] == ["body.p.06968"]
    report = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[request],
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        d050_registry={},
    )
    assert report["translation_api_calls"] == 0
    assert report["unit_results"][0]["final_status"] == "ACCEPT"
    assert report["unit_results"][0]["repair_applied"] is True


def test_recalc_prefers_group_v3_accept_over_v2_human_required(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha)
    raw = _raw(request, "原文候选")
    output = _seed(tmp_path, request, raw)
    persist_stage_once(
        output,
        "reviews_v2",
        request.display_id,
        {
            "artifact_type": "CANARY_REVIEW_RESULT_V2",
            "schema_version": 1,
            "verdict": "REPAIR",
            "issues": [{"code": "SENSE", "affected_segment_indices": [0], "reason": "old", "repair_instruction": "old"}],
            "unit_uid": request.unit_uid,
        },
    )
    persist_stage_once(
        output,
        "re_reviews_v2",
        request.display_id,
        {"artifact_type": "CANARY_RE_REVIEW_RESULT_V2", "schema_version": 1, "verdict": "REPAIR", "issues": [], "unit_uid": request.unit_uid},
    )
    persist_stage_once(
        output,
        "reviews_v3",
        request.display_id,
        {
            "artifact_type": "CANARY_REVIEW_RESULT_V3",
            "schema_version": 1,
            "review_contract_version": "booktrans.review-group.v1",
            "verdict": "ACCEPT",
            "issues": [],
            "unit_uid": request.unit_uid,
        },
    )
    report = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[request],
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        d050_registry={},
    )
    assert report["unit_results"][0]["final_status"] == "ACCEPT"
    assert report["unit_results"][0]["human_required"] is False
    assert report["translation_api_calls"] == 0


def test_live_corrected_second_review_payload_uses_repaired_slice() -> None:
    from booktrans.domain.calibration import TranslationResult
    from booktrans.production_worker.semantic_review_run import (
        assert_repaired_second_review_payload,
        build_repaired_second_review_group,
        candidate_text_map,
        context_units_for_order,
        repaired_candidate_text,
        segment_count_map,
    )
    from booktrans.production_worker.selection import load_checkpoint_d050_registry
    from booktrans.production_worker.unit_store import load_jsonl_models, load_raw_by_uid, load_stage

    canary = Path(r"D:\translate\runs\qwen-production-canary-20260917T085341Z")
    run = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
    requests = load_jsonl_models(canary / "translation_request.jsonl", CalibrationRequest)
    request = next(item for item in requests if item.display_id == "body.p.06968")
    repaired = TranslationResult.model_validate(load_stage(canary, "repair_candidates_v3", request.display_id)["working"])
    raw_by_uid = load_raw_by_uid(canary / "translation_raw_response.jsonl")
    original = repaired_candidate_text(raw_by_uid[request.unit_uid])
    repaired_text = repaired_candidate_text(repaired)
    assert repaired_text != original
    assert "绝对" in repaired_text
    group = build_repaired_second_review_group(
        request,
        context_units_for_order([item.display_id for item in requests]),
        load_checkpoint_d050_registry(run, "checkpoint_03"),
        candidate_texts=candidate_text_map(requests, raw_by_uid),
        segment_counts=segment_count_map(requests),
        repaired=repaired,
    )
    assert_repaired_second_review_payload(
        group,
        request=request,
        repaired=repaired,
        previous_display_id="body.p.06961",
    )
    assert group.member_work_order_ids == ("body.p.06961", "body.p.06968")
    assert group.repairable_work_order_ids == ("body.p.06968",)
    assert group.members[0].sealed is True
    assert group.members[0].candidate_text != repaired_text
    assert group.members[1].candidate_text == repaired_text
    assert repaired_text in (group.full_candidate_span or "")
    assert original not in (group.full_candidate_span or "").split(group.members[0].candidate_text)[-1]
    invalid = load_stage(canary, "re_reviews_v3", request.display_id)
    assert invalid["verdict"] == "REPAIR"


def test_corrected_second_review_does_not_overwrite_invalid_v3(tmp_path: Path) -> None:
    from booktrans.production_worker.semantic_review_run import review_corrected_second_review_only

    sha = "a" * 64
    previous = _previous_request(sha=sha)
    request = _request(sha=sha)
    previous_raw = _raw(previous, "已批准前文")
    raw = _raw(request, "原文候选")
    output = _seed(tmp_path, request, raw)
    with (output / "translation_raw_response.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(previous_raw.model_dump_json() + "\n")
    persist_stage_once(output, "reviews_v3", request.display_id, {"verdict": "REPAIR", "issues": [], "unit_uid": request.unit_uid})
    persist_stage_once(
        output,
        "re_reviews_v3",
        request.display_id,
        {"verdict": "REPAIR", "issues": [], "unit_uid": request.unit_uid, "artifact_type": "CANARY_RE_REVIEW_RESULT_V3"},
    )
    persist_stage_once(
        output,
        "repair_candidates_v3",
        request.display_id,
        {"working": json.loads(_raw(request, "在你的依赖关系网络中，参与构建两个或多个节点的绝对任何软件组件。").model_dump_json())},
    )
    before_v3 = sha256((output / "re_reviews_v3" / f"{request.display_id}.json").read_bytes()).hexdigest()
    reviewer = ScriptedReviewer(["ACCEPT"])
    report = review_corrected_second_review_only(
        output_dir=output,
        requests=[previous, request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={previous.display_id: SimpleNamespace(), request.display_id: SimpleNamespace()},
        known_unit_ids={previous.unit_uid, request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[previous.display_id, request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        display_ids=[request.display_id],
        d050_registry=_registry(),
        context_units=_units(),
    )
    assert report["translation_api_calls"] == 0
    assert report["repair_api_calls"] == 0
    assert report["reviewer_api_calls"] == 1
    assert reviewer.calls == 1
    assert "绝对任何软件组件" in (reviewer.contexts[0].get("full_candidate_span") or "")
    assert "原文候选" not in (reviewer.contexts[0].get("full_candidate_span") or "")
    after_v3 = sha256((output / "re_reviews_v3" / f"{request.display_id}.json").read_bytes()).hexdigest()
    assert after_v3 == before_v3
    supersession = json.loads((output / "re_reviews_v3_supersession" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert supersession["reason"] == "invalid-input-contract"
    v4 = json.loads((output / "re_reviews_v4" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert v4["verdict"] == "ACCEPT"
    recalc = recalculate_canary_from_persisted_evidence(
        output_dir=output,
        requests=[request],
        production_run=tmp_path / "full-book-20260916T000200Z-decb09",
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_fn,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        d050_registry={},
    )
    assert recalc["unit_results"][0]["final_status"] == "ACCEPT"
    assert recalc["unit_results"][0]["repair_applied"] is True
