from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationResponseStatus, CalibrationSourceSegment
from booktrans.harness.source_integrity import fingerprint
from booktrans.pilot.qwen_structured_runtime import (
    ARM_A,
    ARM_B,
    SIMPLE_ARM,
    PilotGlobalError,
    main,
    run_execute,
)
from booktrans.providers.qwen_max import (
    QWEN_MAX,
    ReviewResult,
    ReviewerProviderResult,
    StructuredProviderResult,
    StructuredTranslationCandidate,
    structured_candidate_to_raw,
)
from booktrans.providers.qwen_mt import QWEN_MT_PLUS
from booktrans.providers.translation_provider import (
    ProviderContractGap,
    TranslationCandidate,
    TranslationProviderRequest,
)


SECRET = "test-secret-never-log"


def _segment(text: str, *, kind: str = "anchored", index: int = 0) -> CalibrationSourceSegment:
    return CalibrationSourceSegment(kind=kind, text=text, anchor_ref=f"anchor-{index}", island_ref="island.001")


def _request(*, unit_uid: str, display_id: str, source_sha256: str, segments: list[CalibrationSourceSegment]) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256=source_sha256,
        unit_uid=unit_uid,
        display_id=display_id,
        story="body",
        content_role="body_paragraph",
        source_text="".join(segment.text for segment in segments),
        source_segments=segments,
    )


def _candidate(provider_request: TranslationProviderRequest, text: str, *, provider: str = "ALIYUN_DASHSCOPE_QWEN_MT", model: str = QWEN_MT_PLUS) -> TranslationCandidate:
    return TranslationCandidate(
        provider=provider,
        model=model,
        request_binding_sha256=provider_request.request_binding_sha256,
        translation_options_sha256=provider_request.translation_options_sha256,
        source_sha256=provider_request.request.source_sha256,
        unit_uid=provider_request.request.unit_uid,
        display_id=provider_request.request.display_id,
        response_text=text,
        requested_at="2026-09-17T00:00:00+00:00",
        responded_at="2026-09-17T00:00:01+00:00",
        response_id="resp-test",
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        attempt_count=1,
    )


def _structured_result(provider_request: TranslationProviderRequest, targets: list[str], *, strategy: str = "DIRECT_STRUCTURED") -> StructuredProviderResult:
    structured = StructuredTranslationCandidate.model_validate(
        {
            "schema_version": "booktrans.structured-translation.v1",
            "unit_uid": provider_request.request.unit_uid,
            "source_fingerprint": provider_request.request_binding_sha256,
            "segments": [{"index": index, "target_text": text} for index, text in enumerate(targets)],
        }
    )
    raw = structured_candidate_to_raw(provider_request, structured)
    return StructuredProviderResult(
        candidate=_candidate(provider_request, json.dumps(structured.model_dump(), ensure_ascii=False), provider="ALIYUN_DASHSCOPE_QWEN_MAX", model=QWEN_MAX),
        structured=structured,
        strategy=strategy,  # type: ignore[arg-type]
        raw=raw,
    )


class FakeImporter:
    def __init__(self, fail_uids: set[str] | None = None) -> None:
        self.fail_uids = fail_uids or set()
        self.calls = 0

    def run(self, source, request_path, raw_path, candidate_path):
        self.calls += 1
        Path(candidate_path).write_bytes(b"fake-docx")
        raws = [json.loads(line) for line in Path(raw_path).read_text(encoding="utf-8").splitlines() if line.strip()]
        statuses = []
        for raw in raws:
            status = "FAIL" if raw["unit_uid"] in self.fail_uids else "PASS"
            diagnostics = ["HARNESS_FAIL"] if status == "FAIL" else []
            statuses.append(CalibrationResponseStatus(unit_uid=raw["unit_uid"], display_id=raw["unit_uid"], status=status, diagnostics=diagnostics))
        return type("Import", (), {"response_statuses": statuses})()


@dataclass
class FakeReviewer:
    api_key: str = SECRET
    calls: list[str] = field(default_factory=list)

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.calls.append(request.request.unit_uid)
        review = ReviewResult.model_validate(
            {"schema_version": "booktrans.structured-review.v1", "unit_uid": request.request.unit_uid, "verdict": "ACCEPT", "issues": []}
        )
        return ReviewerProviderResult(review=review, candidate=_candidate(request, "{}", provider="ALIYUN_DASHSCOPE_QWEN_MAX_REVIEWER", model=QWEN_MAX))


@dataclass
class FakeQwenMT:
    api_key: str = SECRET
    model: str = QWEN_MT_PLUS
    provider_name: str = "ALIYUN_DASHSCOPE_QWEN_MT"
    calls: list[str] = field(default_factory=list)
    text_by_uid: dict[str, str] = field(default_factory=dict)

    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate:
        self.calls.append(request.request.unit_uid)
        return _candidate(request, self.text_by_uid.get(request.request.unit_uid, "中文译文"))


@dataclass
class FakeQwenMax:
    api_key: str = SECRET
    model: str = QWEN_MAX
    provider_name: str = "ALIYUN_DASHSCOPE_QWEN_MAX"
    calls: list[tuple[str, str]] = field(default_factory=list)
    targets_by_uid: dict[str, list[str]] = field(default_factory=dict)
    errors_by_uid: dict[str, Exception] = field(default_factory=dict)
    interrupt_on: str | None = None

    def translate_structured(self, request: TranslationProviderRequest) -> StructuredProviderResult:
        uid = request.request.unit_uid
        self.calls.append(("A", uid))
        if self.interrupt_on == uid:
            raise KeyboardInterrupt()
        if uid in self.errors_by_uid:
            error = self.errors_by_uid[uid]
            if isinstance(error, ProviderContractGap):
                try:
                    _structured_result(request, [""] + ["占位"] * (len(request.request.source_segments) - 1))
                except ProviderContractGap as generated:
                    generated.candidate = _candidate(request, "{}", provider=self.provider_name, model=self.model)
                    raise generated
                raise error
            raise error
        targets = self.targets_by_uid.get(uid)
        if targets is None:
            targets = ["译文"] * len(request.request.source_segments)
            for index, segment in enumerate(request.request.source_segments):
                if segment.kind == "protected" or not segment.text.strip():
                    targets[index] = segment.text
        return _structured_result(request, targets)

    def project_holistic(self, request: TranslationProviderRequest, *, holistic_candidate: TranslationCandidate) -> StructuredProviderResult:
        uid = request.request.unit_uid
        self.calls.append(("B", uid))
        if uid in self.errors_by_uid:
            raise self.errors_by_uid[uid]
        targets = self.targets_by_uid.get(uid)
        if targets is None:
            targets = ["投影"] * len(request.request.source_segments)
            for index, segment in enumerate(request.request.source_segments):
                if segment.kind == "protected" or not segment.text.strip():
                    targets[index] = segment.text
        return _structured_result(request, targets, strategy="HOLISTIC_PROJECTION")


def _write_plan(tmp_path: Path, source: Path, entries: list[tuple[CalibrationRequest, list[str]]]) -> Path:
    request_path = tmp_path / "sealed_requests.jsonl"
    with request_path.open("w", encoding="utf-8", newline="\n") as stream:
        for request, _ in entries:
            stream.write(request.model_dump_json())
            stream.write("\n")
    plan = {
        "artifact_type": "QWEN_STRUCTURED_PROVIDER_PILOT_PLAN",
        "schema_version": 1,
        "source_sha256": fingerprint(source).sha256,
        "unit_count": len(entries),
        "coverage_gaps": [],
        "requests": [
            {
                "request_path": str(request_path),
                "line_number": index,
                "unit_uid": request.unit_uid,
                "logical_work_order_id": request.display_id,
                "categories": categories,
            }
            for index, (request, categories) in enumerate(entries, start=1)
        ],
    }
    path = tmp_path / "pilot_plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def _bible(tmp_path: Path) -> Path:
    path = tmp_path / "bible.md"
    path.write_text("| Source term | Chinese |\n| --- | --- |\n", encoding="utf-8")
    return path


def _requests(source_sha256: str) -> tuple[CalibrationRequest, CalibrationRequest, CalibrationRequest]:
    simple = _request(unit_uid="simple-1", display_id="body.p.00001", source_sha256=source_sha256, segments=[_segment("Hello world.")])
    multi_a = _request(
        unit_uid="multi-1",
        display_id="body.p.00002",
        source_sha256=source_sha256,
        segments=[_segment("First.", index=0), _segment("Second.", index=1)],
    )
    multi_b = _request(
        unit_uid="multi-2",
        display_id="body.p.00003",
        source_sha256=source_sha256,
        segments=[_segment("Third.", index=0), _segment("Fourth.", index=1)],
    )
    return simple, multi_a, multi_b


def _run(tmp_path: Path, source: Path, plan: Path, out: Path, *, resume: bool = False, qwen_mt=None, qwen_max=None, reviewer=None, importer=None, **kwargs):
    plan_value = json.loads(plan.read_text(encoding="utf-8"))
    from booktrans.pilot.qwen_structured_runtime import _load_plan_requests

    return run_execute(
        plan=plan_value,
        plan_path=plan,
        source=source,
        translation_bible=_bible(tmp_path),
        out=out,
        resume=resume,
        requests=_load_plan_requests(plan_value),
        qwen_mt=qwen_mt or FakeQwenMT(),
        qwen_max=qwen_max or FakeQwenMax(),
        reviewer=reviewer or FakeReviewer(),
        importer_factory=lambda: importer or FakeImporter(),
        api_key_for_redaction=SECRET,
        **kwargs,
    )


def test_empty_target_is_recorded_and_next_unit_continues(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    qwen_max = FakeQwenMax()
    qwen_max.errors_by_uid[multi_a.unit_uid] = ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT")
    out = tmp_path / "pilot-out"
    report = _run(tmp_path, minimal_docx, plan, out, qwen_max=qwen_max)
    arm_a = next(item for item in report["arms"] if item["arm"] == ARM_A)
    assert arm_a["contract_fail_count"] == 1
    assert arm_a["contract_pass_count"] == 1
    assert "PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT" in arm_a["failure_class_distribution"]
    progress = (out / "pilot_progress.jsonl").read_text(encoding="utf-8")
    assert "CONTRACT_GAP" in progress
    assert SECRET not in progress
    assert (out / ARM_A / "units" / multi_b.unit_uid / "translation_raw_response.jsonl").is_file()
    assert not (out / ARM_A / "units" / multi_a.unit_uid / "translation_raw_response.jsonl").is_file()


def test_arm_a_fail_still_executes_arm_b(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])

    class SplitMax(FakeQwenMax):
        def translate_structured(self, request):
            self.calls.append(("A", request.request.unit_uid))
            raise ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT")

    qwen_max = SplitMax()
    report = _run(tmp_path, minimal_docx, plan, tmp_path / "pilot-split", qwen_max=qwen_max)
    assert next(item for item in report["arms"] if item["arm"] == ARM_A)["contract_fail_count"] == 2
    assert next(item for item in report["arms"] if item["arm"] == ARM_B)["contract_pass_count"] == 2
    assert any(call[0] == "B" for call in qwen_max.calls)


def test_interrupt_then_resume_skips_completed_calls(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    out = tmp_path / "pilot-resume"
    qwen_max = FakeQwenMax(interrupt_on=multi_b.unit_uid)
    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, minimal_docx, plan, out, qwen_max=qwen_max)
    first_calls = list(qwen_max.calls)
    assert ("A", multi_a.unit_uid) in first_calls
    assert ("A", multi_b.unit_uid) in first_calls
    resumed = FakeQwenMax()
    report = _run(tmp_path, minimal_docx, plan, out, resume=True, qwen_max=resumed)
    assert ("A", multi_a.unit_uid) not in resumed.calls
    assert ("A", multi_b.unit_uid) in resumed.calls
    assert next(item for item in report["arms"] if item["arm"] == ARM_A)["attempted"] == 2


def test_resume_identity_mismatch_fails_closed(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    out = tmp_path / "pilot-id"
    _run(tmp_path, minimal_docx, plan, out)
    identity = json.loads((out / "pilot_identity.json").read_text(encoding="utf-8"))
    identity["models"]["qwen_max"] = "changed-model"
    (out / "pilot_identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(PilotGlobalError, match="RESUME_IDENTITY_MISMATCH"):
        _run(tmp_path, minimal_docx, plan, out, resume=True)


def test_duplicate_resume_does_not_repeat_api_or_provenance(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    out = tmp_path / "pilot-dup"
    qwen_max = FakeQwenMax()
    _run(tmp_path, minimal_docx, plan, out, qwen_max=qwen_max)
    first = list(qwen_max.calls)
    provenance = (out / ARM_A / "units" / multi_a.unit_uid / "translation_provider_provenance.jsonl").read_text(encoding="utf-8")
    qwen_max_again = FakeQwenMax()
    _run(tmp_path, minimal_docx, plan, out, resume=True, qwen_max=qwen_max_again)
    assert qwen_max_again.calls == []
    assert (out / ARM_A / "units" / multi_a.unit_uid / "translation_provider_provenance.jsonl").read_text(encoding="utf-8") == provenance
    assert first


def test_missing_credentials_stop_the_whole_pilot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKTRANS_QWEN_BASE_URL", raising=False)
    out = tmp_path / "pilot-creds"
    with pytest.raises(PilotGlobalError, match="PROVIDER_CONFIGURATION_MISSING"):
        main(
            [
                "--plan",
                str(plan),
                "--source",
                str(minimal_docx),
                "--translation-bible",
                str(_bible(tmp_path)),
                "--out",
                str(out),
                "--execute",
            ]
        )
    assert not out.exists()


def test_source_sha_mismatch_stops_the_whole_pilot(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    plan.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(PilotGlobalError, match="SOURCE_SHA_MISMATCH"):
        main(["--plan", str(plan), "--source", str(minimal_docx), "--translation-bible", str(_bible(tmp_path))])


def test_final_report_includes_partial_failures(tmp_path: Path, minimal_docx: Path) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    importer = FakeImporter(fail_uids={multi_b.unit_uid})
    qwen_max = FakeQwenMax()
    qwen_max.errors_by_uid[multi_a.unit_uid] = ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT")
    out = tmp_path / "pilot-report"
    report = _run(tmp_path, minimal_docx, plan, out, qwen_max=qwen_max, importer=importer)
    assert report["status"] == "COMPLETED"
    arm_a = next(item for item in report["arms"] if item["arm"] == ARM_A)
    assert arm_a["contract_fail_count"] == 1
    assert arm_a["deterministic_anchor_fail_count"] == 1
    assert (out / "pilot_comparison_report.json").is_file()


def test_api_key_is_never_logged(tmp_path: Path, minimal_docx: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sha = fingerprint(minimal_docx).sha256
    simple, multi_a, multi_b = _requests(sha)
    plan = _write_plan(tmp_path, minimal_docx, [(simple, ["simple_single_segment"]), (multi_a, ["multi_segment"]), (multi_b, ["multi_segment"])])
    out = tmp_path / "pilot-secret"
    _run(tmp_path, minimal_docx, plan, out)
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    for path in out.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(encoding="utf-8", errors="ignore")
