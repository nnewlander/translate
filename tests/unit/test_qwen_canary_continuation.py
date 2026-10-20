from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import ApprovedRepairContract, ReviewIssue, ReviewResult, ReviewerProviderResult, StructuredRepairPatch, StructuredRepairResult, apply_narrow_repair
from booktrans.providers.translation_provider import ProviderContractGap, TranslationCandidate, TranslationProviderRequest
from booktrans.production_worker.continuation import continue_canary_from_raw, verify_raw_unit
from booktrans.production_worker.numeric_recovery import decide_numeric_recovery_policy, failing_number_segment_indices
from booktrans.production_worker.pipeline import WorkerProviders


def _request(*, unit: str = "u1", display: str = "body.p.00001", texts: tuple[str, ...] = ("Alpha 2 quality.",), sha: str = "a" * 64) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256=sha,
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


def _raw(request: CalibrationRequest, texts: tuple[str, ...] | None = None) -> TranslationResult:
    values = texts or tuple("译" + str(index) for index in range(len(request.source_segments)))
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment, text in zip(request.source_segments, values, strict=True)
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
    def __init__(self) -> None:
        self.calls = 0

    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate:
        self.calls += 1
        raise AssertionError("translation API must not be called")


class CountingMax:
    def __init__(self) -> None:
        self.calls = 0

    def translate_structured(self, request: TranslationProviderRequest):
        self.calls += 1
        raise AssertionError("structured translation API must not be called")


class ScriptedReviewer:
    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.calls += 1
        verdict = self.verdicts.pop(0)
        issues = []
        if verdict == "REPAIR":
            issues = [ReviewIssue(code="SENSE", affected_segment_indices=[0], reason="narrow", repair_instruction="Restore 2.")]
        review = ReviewResult(schema_version="booktrans.structured-review.v1", unit_uid=request.request.unit_uid, verdict=verdict, issues=issues)
        return ReviewerProviderResult(review=review, candidate=_candidate(request.request))


class CountingRepair:
    def __init__(self, text: str = "译0含2") -> None:
        self.calls = 0
        self.text = text

    def repair(self, request, current, contract):
        self.calls += 1
        if set(contract.permitted_segment_indices) - set(range(len(current.target_segments))):
            raise AssertionError("repair scope expanded")
        patched = list(current.target_segments)
        for index in contract.permitted_segment_indices:
            source = request.request.source_segments[index]
            patched[index] = StructuredTargetSegment(text=self.text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
        return TranslationResult(unit_uid=current.unit_uid, target_segments=patched)


def _qa_from_raw(view, request, response, known):
    passed = "2" in "".join(segment.text for segment in response.target_segments) or request.display_id.endswith("00002")
    diagnostics = [] if passed else [{"code": "NUMBER_PRESERVATION", "message": "missing 2"}]
    return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": passed, "diagnostics": diagnostics}


def _write_jsonl(path: Path, rows: list) -> None:
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")


def _seed_canary(tmp_path: Path, requests: list[CalibrationRequest], raws: list[TranslationResult], *, fail_ids: list[str] | None = None) -> Path:
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "qwen-production-canary-test"
    output.mkdir()
    _write_jsonl(output / "translation_request.jsonl", requests)
    _write_jsonl(output / "translation_raw_response.jsonl", raws)
    fail_ids = fail_ids or []
    records = []
    for request in requests:
        passed = request.display_id not in fail_ids
        records.append({"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": passed, "diagnostics": [] if passed else [{"code": "NUMBER_PRESERVATION"}]})
    while len(records) < 25:
        display = f"body.p.{len(records) + 1:05d}"
        records.append({"unit_uid": f"pad-{len(records)}", "display_id": display, "passed": True, "diagnostics": []})
    report = {"pass_count": sum(row["passed"] for row in records), "fail_count": sum(not row["passed"] for row in records), "records": records}
    (output / "translation_raw_qa_report.json").write_text(json.dumps(report), encoding="utf-8")
    return output


def test_existing_raw_continuation_makes_zero_translation_api_calls(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("译文含2",))
    output = _seed_canary(tmp_path, [request], [raw])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    mt = CountingMT()
    structured = CountingMax()
    reviewer = ScriptedReviewer(["ACCEPT"])
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=mt, structured=structured, reviewer=reviewer, repair=CountingRepair()),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id, *[f"body.p.{index:05d}" for index in range(2, 26)]],
    )
    assert mt.calls == 0
    assert structured.calls == 0
    assert report["translation_api_calls"] == 0
    assert report["raw_reused_count"] == 1
    assert reviewer.calls == 1
    assert (output / "reviews" / f"{request.display_id}.json").is_file()
    assert json.loads((output / "reviews" / f"{request.display_id}.json").read_text(encoding="utf-8"))["verdict"] == "ACCEPT"


def test_numeric_failure_routes_through_registry(tmp_path: Path) -> None:
    output = tmp_path / "canary"
    output.mkdir()
    requests = [_request(unit=f"u{index}", display=f"body.p.{index + 1:05d}", texts=(f"Token {index} value.",)) for index in range(25)]
    raws = [_raw(request, ("译",)) for request in requests]
    _write_jsonl(output / "translation_request.jsonl", requests)
    _write_jsonl(output / "translation_raw_response.jsonl", raws)
    records = []
    for index, request in enumerate(requests):
        passed = index not in {0, 1}
        records.append({"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": passed, "diagnostics": [] if passed else [{"code": "NUMBER_PRESERVATION"}]})
    (output / "translation_raw_qa_report.json").write_text(json.dumps({"pass_count": 23, "fail_count": 2, "records": records}), encoding="utf-8")
    from hashlib import sha256

    raw_sha = sha256((output / "translation_raw_response.jsonl").read_bytes()).hexdigest()
    result = decide_numeric_recovery_policy(
        output_dir=output,
        source_sha256="a" * 64,
        selection_ids=[request.display_id for request in requests],
        raw_sha256=raw_sha,
        active_batch="FULL_BOOK_BATCH_022",
        checkpoint="checkpoint_03",
    )
    assert result["decision"]["eligible"] is True
    assert result["decision"]["policy_id"] == "NUMERIC_REPRESENTATION"
    assert result["failing_work_order_ids"] == ["body.p.00001", "body.p.00002"]


def test_failing_number_segment_indices_are_exact() -> None:
    request = _request(texts=("Keep 2 here.", "No number."))
    raw = _raw(request, ("缺数字", "仍无"))
    assert failing_number_segment_indices(request, raw) == [0]


def test_reviewer_results_persisted_incrementally(tmp_path: Path) -> None:
    sha = "a" * 64
    first = _request(unit="u1", display="body.p.00001", texts=("Alpha 2 quality.",), sha=sha)
    second = _request(unit="u2", display="body.p.00002", texts=("Beta 2 quality.",), sha=sha)
    output = _seed_canary(tmp_path, [first, second], [_raw(first, ("译文含2",)), _raw(second, ("第二含2",))])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"

    class BoomReviewer(ScriptedReviewer):
        def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
            result = super().review_with_provenance(request, candidate, deterministic_qa_summary=deterministic_qa_summary)
            if request.request.display_id == "body.p.00002":
                raise RuntimeError("injected-review-crash")
            return result

    reviewer = BoomReviewer(["ACCEPT", "ACCEPT"])
    with pytest.raises(RuntimeError, match="injected-review-crash"):
        continue_canary_from_raw(
            output_dir=output,
            requests=[first, second],
            providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
            production_run=production_run,
            views={first.display_id: SimpleNamespace(), second.display_id: SimpleNamespace()},
            known_unit_ids={first.unit_uid, second.unit_uid},
            qa_fn=_qa_from_raw,
            translation_bible=None,
            frozen_order=[first.display_id, second.display_id],
            selection_sha256="b" * 64,
            source_sha256=sha,
            selection_ids=[first.display_id, second.display_id],
        )
    assert (output / "reviews" / "body.p.00001.json").is_file()
    assert not (output / "reviews" / "body.p.00002.json").exists()
    assert (output / "continuation" / "body.p.00001.json").is_file()


def test_crash_during_review_can_resume_without_retranslation(tmp_path: Path) -> None:
    sha = "a" * 64
    first = _request(unit="u1", display="body.p.00001", texts=("Alpha 2 quality.",), sha=sha)
    second = _request(unit="u2", display="body.p.00002", texts=("Beta 2 quality.",), sha=sha)
    output = _seed_canary(tmp_path, [first, second], [_raw(first, ("译文含2",)), _raw(second, ("第二含2",))])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    reviewer = ScriptedReviewer(["ACCEPT", "ACCEPT"])
    continue_canary_from_raw(
        output_dir=output,
        requests=[first],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
        production_run=production_run,
        views={first.display_id: SimpleNamespace()},
        known_unit_ids={first.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[first.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[first.display_id, second.display_id],
    )
    resume_reviewer = ScriptedReviewer(["ACCEPT"])
    mt = CountingMT()
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[first, second],
        providers=WorkerProviders(mt=mt, structured=CountingMax(), reviewer=resume_reviewer, repair=CountingRepair()),
        production_run=production_run,
        views={first.display_id: SimpleNamespace(), second.display_id: SimpleNamespace()},
        known_unit_ids={first.unit_uid, second.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[first.display_id, second.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[first.display_id, second.display_id],
    )
    assert mt.calls == 0
    assert resume_reviewer.calls == 1
    assert report["unit_count"] == 2
    assert report["translation_api_calls"] == 0


def test_crash_during_repair_can_resume(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("缺数字",))
    output = _seed_canary(tmp_path, [request], [raw], fail_ids=[request.display_id])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"

    class BoomRepair(CountingRepair):
        def repair(self, request, current, contract):
            self.calls += 1
            raise RuntimeError("injected-repair-crash")

    with pytest.raises(RuntimeError, match="injected-repair-crash"):
        continue_canary_from_raw(
            output_dir=output,
            requests=[request],
            providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=ScriptedReviewer(["ACCEPT"]), repair=BoomRepair()),
            production_run=production_run,
            views={request.display_id: SimpleNamespace()},
            known_unit_ids={request.unit_uid},
            qa_fn=_qa_from_raw,
            translation_bible=None,
            frozen_order=[request.display_id],
            selection_sha256="b" * 64,
            source_sha256=sha,
            selection_ids=[request.display_id, *[f"body.p.{index:05d}" for index in range(2, 26)]],
        )
    assert (output / "repair_contracts" / f"{request.display_id}.json").is_file()
    assert not (output / "continuation" / f"{request.display_id}.json").exists()
    mt = CountingMT()
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=mt, structured=CountingMax(), reviewer=ScriptedReviewer(["ACCEPT"]), repair=CountingRepair("译文含2")),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id, *[f"body.p.{index:05d}" for index in range(2, 26)]],
    )
    assert mt.calls == 0
    assert report["translation_api_calls"] == 0


def test_second_review_repair_stops_without_loop(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("译文含2",))
    output = _seed_canary(tmp_path, [request], [raw])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    reviewer = ScriptedReviewer(["REPAIR", "REPAIR"])
    repair = CountingRepair()
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=repair),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id],
    )
    assert reviewer.calls == 2
    assert repair.calls == 1
    assert report["human_required"] == 1
    assert report["gate"]["passed"] is False


def test_human_required_stops_without_repair(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("译文含2",))
    output = _seed_canary(tmp_path, [request], [raw])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    repair = CountingRepair()
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=ScriptedReviewer(["HUMAN_REQUIRED"]), repair=repair),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id],
    )
    assert repair.calls == 0
    assert report["human_required"] == 1
    assert report["gate"]["passed"] is False


def test_repair_provider_scope_enforced(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.", "Second."))
    raw = _raw(request, ("译文含2", "第二"))
    output = _seed_canary(tmp_path, [request], [raw])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"

    class ExpandingRepair:
        calls = 0

        def repair(self, request, current, contract):
            self.calls += 1
            patched = list(current.target_segments)
            for index in range(len(patched)):
                source = request.request.source_segments[index]
                patched[index] = StructuredTargetSegment(text="全翻", kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref)
            return TranslationResult(unit_uid=current.unit_uid, target_segments=patched)

    expanding = ExpandingRepair()
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=ScriptedReviewer(["REPAIR", "ACCEPT"]), repair=expanding),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id],
    )
    # Fake expanding repair is in-memory; QwenMaxRepairProvider would raise SCOPE_EXPANDED.
    # Continuation still requires second ACCEPT and QA pass. Expanding text drops "2" -> QA fail -> HUMAN_REQUIRED.
    assert expanding.calls == 1
    assert report["human_required"] == 1


def test_repair_provider_scope_cannot_expand_contract() -> None:
    request = _request(texts=("Alpha 2 quality.", "Second."))
    provider_request = TranslationProviderRequest(request=request)
    current = _raw(request, ("译文含2", "第二"))
    contract = ApprovedRepairContract(
        policy_id="NUMERIC_REPRESENTATION",
        unit_uid=request.unit_uid,
        source_fingerprint=provider_request.request_binding_sha256,
        permitted_segment_indices=[0],
        repair_instruction="Restore only the exact source numeric representation in the permitted segments.",
    )
    expanded = StructuredRepairResult(
        schema_version="booktrans.structured-repair.v1",
        unit_uid=request.unit_uid,
        source_fingerprint=provider_request.request_binding_sha256,
        patches=[
            StructuredRepairPatch(index=0, target_text="译文含2"),
            StructuredRepairPatch(index=1, target_text="被扩大"),
        ],
    )
    with pytest.raises(ProviderContractGap, match="SCOPE_EXPANDED"):
        apply_narrow_repair(provider_request, current, contract, expanded)


def test_incomplete_numeric_terminal_is_reprocessed_through_registry(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("缺数字",))
    output = _seed_canary(tmp_path, [request], [raw], fail_ids=[request.display_id])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    (output / "continuation").mkdir()
    (output / "continuation" / f"{request.display_id}.json").write_text(
        json.dumps(
            {
                "artifact_type": "CANARY_CONTINUATION_UNIT",
                "schema_version": 1,
                "display_id": request.display_id,
                "unit_uid": request.unit_uid,
                "final_status": "HUMAN_REQUIRED",
                "repair_applied": False,
                "repair_policy_id": None,
                "human_required": True,
                "blocked": False,
                "review": "NOT_PERSISTED",
                "qa_passed": False,
            }
        ),
        encoding="utf-8",
    )
    repair = CountingRepair("译文含2")
    reviewer = ScriptedReviewer(["ACCEPT"])
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=repair),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id, *[f"body.p.{index:05d}" for index in range(2, 26)]],
    )
    assert repair.calls == 1
    assert reviewer.calls == 1
    assert report["translation_api_calls"] == 0
    assert report["numeric_recovery"]["eligible"] is True
    assert report["numeric_recovery"]["success"] == 1
    terminal = json.loads((output / "continuation" / f"{request.display_id}.json").read_text(encoding="utf-8"))
    assert terminal["final_status"] == "ACCEPT"
    assert terminal["repair_policy_id"] == "NUMERIC_REPRESENTATION"


def test_incomplete_contract_failure_without_review_is_retried(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("译文含2",))
    output = _seed_canary(tmp_path, [request], [raw])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    (output / "continuation").mkdir()
    (output / "continuation" / f"{request.display_id}.json").write_text(
        json.dumps(
            {
                "artifact_type": "CANARY_CONTINUATION_UNIT",
                "schema_version": 1,
                "display_id": request.display_id,
                "unit_uid": request.unit_uid,
                "final_status": "PROVIDER_CONTRACT_FAILURE",
                "repair_applied": False,
                "human_required": True,
                "blocked": False,
                "review": "NOT_PERSISTED",
                "qa_passed": True,
            }
        ),
        encoding="utf-8",
    )
    reviewer = ScriptedReviewer(["ACCEPT"])
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=CountingRepair()),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id],
    )
    assert reviewer.calls == 1
    assert report["translation_api_calls"] == 0
    assert json.loads((output / "reviews" / f"{request.display_id}.json").read_text(encoding="utf-8"))["verdict"] == "ACCEPT"


def test_complete_human_required_with_review_is_not_retried(tmp_path: Path) -> None:
    sha = "a" * 64
    request = _request(sha=sha, texts=("Alpha 2 quality.",))
    raw = _raw(request, ("译文含2",))
    output = _seed_canary(tmp_path, [request], [raw])
    production_run = tmp_path / "full-book-20260916T000200Z-decb09"
    continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=ScriptedReviewer(["HUMAN_REQUIRED"]), repair=CountingRepair()),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id],
    )
    reviewer = ScriptedReviewer(["ACCEPT"])
    repair = CountingRepair()
    report = continue_canary_from_raw(
        output_dir=output,
        requests=[request],
        providers=WorkerProviders(mt=CountingMT(), structured=CountingMax(), reviewer=reviewer, repair=repair),
        production_run=production_run,
        views={request.display_id: SimpleNamespace()},
        known_unit_ids={request.unit_uid},
        qa_fn=_qa_from_raw,
        translation_bible=None,
        frozen_order=[request.display_id],
        selection_sha256="b" * 64,
        source_sha256=sha,
        selection_ids=[request.display_id],
    )
    assert reviewer.calls == 0
    assert repair.calls == 0
    assert report["human_required"] == 1
    assert report["translation_api_calls"] == 0


def test_verify_raw_mismatch_is_blocked() -> None:
    request = _request()
    other = _raw(_request(unit="other"))
    reasons = verify_raw_unit(request, other, None, source_sha256="a" * 64, selection_sha256="b" * 64, translation_bible=None)
    assert "UNIT_UID_MISMATCH" in reasons
