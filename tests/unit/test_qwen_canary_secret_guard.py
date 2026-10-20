from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import ReviewResult, ReviewerProviderResult
from booktrans.providers.router import route_semantic_unit
from booktrans.providers.translation_provider import TranslationCandidate, TranslationProviderRequest
from booktrans.production_worker.artifacts import (
    CANDIDATE_PROVENANCE_ALLOWLIST,
    assert_record_safe,
    candidate_provenance,
    inspect_secret_leak,
)
from booktrans.production_worker.canary import run_isolated_canary
from booktrans.production_worker.pipeline import WorkerProviders
from booktrans.production_worker.worker import ResumeForbiddenProviders


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


def _candidate(request: CalibrationRequest, *, provider: str = "ALIYUN_DASHSCOPE_QWEN_MT", text: str = "中文译文 https://example.com/docs") -> TranslationCandidate:
    binding = TranslationProviderRequest(request=request)
    return TranslationCandidate(
        provider=provider,
        model="qwen-mt-plus",
        request_binding_sha256=binding.request_binding_sha256,
        translation_options_sha256=binding.translation_options_sha256,
        source_sha256=request.source_sha256,
        unit_uid=request.unit_uid,
        display_id=request.display_id,
        response_text=text,
        requested_at="2026-09-17T00:00:00+00:00",
        responded_at="2026-09-17T00:00:10+00:00",
        usage={"total_tokens": 9, "prompt_tokens": 4, "completion_tokens": 5},
        attempt_count=1,
        response_id="resp-1",
    )


def _raw(request: CalibrationRequest, text: str = "中文译文") -> TranslationResult:
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text=text, kind=segment.kind, anchor_ref=segment.anchor_ref, island_ref=segment.island_ref)
            for segment in request.source_segments
        ],
    )


def _qa_pass(view, request, response, known):
    return {"unit_uid": request.unit_uid, "display_id": request.display_id, "passed": True, "diagnostics": []}


class FakeMT:
    def __init__(self) -> None:
        self.calls = 0

    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate:
        self.calls += 1
        return _candidate(request.request)


class FakeMax:
    def __init__(self) -> None:
        self.calls = 0

    def translate_structured(self, request: TranslationProviderRequest):
        self.calls += 1
        raw = _raw(request.request, "structured")
        return SimpleNamespace(candidate=_candidate(request.request, provider="ALIYUN_DASHSCOPE_QWEN_MAX", text="structured"), raw=raw)


class FakeReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def review_with_provenance(self, request, candidate, *, deterministic_qa_summary, semantic_context=None):
        self.calls += 1
        review = ReviewResult(
            schema_version="booktrans.structured-review.v1",
            unit_uid=request.request.unit_uid,
            verdict="ACCEPT",
            issues=[],
        )
        return ReviewerProviderResult(review=review, candidate=_candidate(request.request))


class FakeRepair:
    def repair(self, request, current, contract):
        raise AssertionError("repair not expected")


def _providers() -> WorkerProviders:
    return WorkerProviders(mt=FakeMT(), structured=FakeMax(), reviewer=FakeReviewer(), repair=FakeRepair())


def test_real_secret_in_provenance_is_blocked() -> None:
    record = {"provider": "x", "api_key": "sk-should-not-appear"}
    hits = inspect_secret_leak(record)
    assert hits
    assert hits[0]["field_name"] == "api_key"
    assert hits[0]["matched_rule"] == "FORBIDDEN_PROVENANCE_KEY"
    assert "sk-should-not-appear" not in json.dumps(hits)
    with pytest.raises(ValueError, match="WORKER_SECRET_LEAK_BLOCKED"):
        assert_record_safe(record)
    with pytest.raises(ValueError, match="WORKER_SECRET_LEAK_BLOCKED"):
        assert_record_safe({"note": "Bearer leaked-token-value"})
    with pytest.raises(ValueError, match="WORKER_SECRET_LEAK_BLOCKED"):
        assert_record_safe({"header": "Authorization: Bearer leaked-token-value"})


def test_env_api_key_value_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "canary-test-key-do-not-log-aaaa")
    with pytest.raises(ValueError, match="WORKER_SECRET_LEAK_BLOCKED"):
        assert_record_safe({"note": "prefix canary-test-key-do-not-log-aaaa suffix"})
    hits = inspect_secret_leak({"note": "prefix canary-test-key-do-not-log-aaaa suffix"})
    assert hits[0]["matched_rule"] == "ENV_CREDENTIAL_VALUE"
    assert "canary-test-key-do-not-log-aaaa" not in json.dumps(hits)


def test_ordinary_url_text_and_provider_identity_are_not_blocked() -> None:
    record = {
        "provider": "ALIYUN_DASHSCOPE_QWEN_MT",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "text": "译文见 https://example.com/path?q=1",
        "usage": {"total_tokens": 9},
    }
    assert inspect_secret_leak(record) == []
    assert_record_safe(record)


def test_candidate_provenance_allowlist_excludes_response_text_and_api_key() -> None:
    request = _request()
    candidate = _candidate(request)
    record = candidate_provenance(candidate, route_semantic_unit(request), latency_seconds=10.0)
    assert set(record) <= CANDIDATE_PROVENANCE_ALLOWLIST
    assert "response_text" not in record
    assert "api_key" not in record
    payload = json.dumps(record, ensure_ascii=False)
    assert "api_key" not in payload
    assert candidate.response_text not in payload
    assert record["provider"] == "ALIYUN_DASHSCOPE_QWEN_MT"
    assert_record_safe(record)


def test_one_unit_provenance_failure_does_not_lose_previous_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    production_run = tmp_path / "run"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "canary"
    request_a = _request(unit="u1", display="body.p.06939")
    request_b = _request(unit="u2", display="body.p.06940", texts=("First.", "Second."))
    real = candidate_provenance
    count = {"n": 0}

    def wrapped(*args, **kwargs):
        count["n"] += 1
        if count["n"] >= 2:
            raise ValueError("WORKER_SECRET_LEAK_BLOCKED")
        return real(*args, **kwargs)

    monkeypatch.setattr("booktrans.production_worker.canary.candidate_provenance", wrapped)
    with pytest.raises(ValueError, match="WORKER_SECRET_LEAK_BLOCKED"):
        run_isolated_canary(
            output_dir=output,
            requests=[request_a, request_b],
            providers=_providers(),
            production_run=production_run,
            views={request_a.display_id: SimpleNamespace(), request_b.display_id: SimpleNamespace()},
            known_unit_ids={"u1", "u2"},
            qa_fn=_qa_pass,
            frozen_order=[request_a.display_id, request_b.display_id],
            selection_sha256="b" * 64,
            source_sha256="a" * 64,
        )
    assert (output / "units" / "body.p.06939.json").is_file()
    assert not (output / "units" / "body.p.06940.json").exists()
    raw_lines = [line for line in (output / "translation_raw_response.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(raw_lines) == 1


def test_interrupted_canary_resumes_without_api_call(tmp_path: Path) -> None:
    production_run = tmp_path / "run"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "canary"
    request_a = _request(unit="u1", display="body.p.06939")
    request_b = _request(unit="u2", display="body.p.06940", texts=("First.", "Second."))
    first = _providers()
    run_isolated_canary(
        output_dir=output,
        requests=[request_a, request_b],
        providers=first,
        production_run=production_run,
        views={request_a.display_id: SimpleNamespace(), request_b.display_id: SimpleNamespace()},
        known_unit_ids={"u1", "u2"},
        qa_fn=_qa_pass,
        frozen_order=[request_a.display_id, request_b.display_id],
        selection_sha256="b" * 64,
        source_sha256="a" * 64,
    )
    (output / "canary_report.json").unlink()
    (output / "canary_gate.json").unlink()
    forbidden = WorkerProviders(
        mt=ResumeForbiddenProviders(),
        structured=ResumeForbiddenProviders(),
        reviewer=ResumeForbiddenProviders(),
        repair=ResumeForbiddenProviders(),
    )
    report = run_isolated_canary(
        output_dir=output,
        requests=[request_a, request_b],
        providers=forbidden,
        production_run=production_run,
        views={request_a.display_id: SimpleNamespace(), request_b.display_id: SimpleNamespace()},
        known_unit_ids={"u1", "u2"},
        qa_fn=_qa_pass,
        frozen_order=[request_a.display_id, request_b.display_id],
        selection_sha256="b" * 64,
        source_sha256="a" * 64,
        resume=True,
        allow_provider_calls=False,
    )
    assert report["recovery"]["api_calls"] == 0
    assert report["recovery"]["recovered_from_unit"] == 2
    assert report["unit_count"] == 2
    assert (output / "canary_report.json").is_file()


def test_report_regenerates_from_raw_without_api_call(tmp_path: Path) -> None:
    production_run = tmp_path / "run"
    (production_run / "full_book_batch_022").mkdir(parents=True)
    output = tmp_path / "crashed-canary"
    output.mkdir()
    request_a = _request(unit="u1", display="body.p.06939")
    request_b = _request(unit="u2", display="body.p.06940", texts=("First.", "Second."))
    (output / "translation_request.jsonl").write_text(
        request_a.model_dump_json() + "\n" + request_b.model_dump_json() + "\n",
        encoding="utf-8",
    )
    (output / "translation_raw_response.jsonl").write_text(
        _raw(request_a).model_dump_json() + "\n" + _raw(request_b, "第二段").model_dump_json() + "\n",
        encoding="utf-8",
    )
    forbidden = WorkerProviders(
        mt=ResumeForbiddenProviders(),
        structured=ResumeForbiddenProviders(),
        reviewer=ResumeForbiddenProviders(),
        repair=ResumeForbiddenProviders(),
    )
    report = run_isolated_canary(
        output_dir=output,
        requests=[request_a, request_b],
        providers=forbidden,
        production_run=production_run,
        views={request_a.display_id: SimpleNamespace(), request_b.display_id: SimpleNamespace()},
        known_unit_ids={"u1", "u2"},
        qa_fn=_qa_pass,
        frozen_order=[request_a.display_id, request_b.display_id],
        selection_sha256="b" * 64,
        source_sha256="a" * 64,
        resume=True,
        allow_provider_calls=False,
    )
    assert report["recovery"]["api_calls"] == 0
    assert report["recovery"]["recovered_from_raw"] == 2
    assert report["provider_contract_pass"] == 2
    assert report["review_not_persisted"] == 2
    assert report["unit_results"][0]["review_status"] == "NOT_PERSISTED"
    assert report["latency"]["provider_latency"] == "NOT_PERSISTED"
    assert report["usage"]["total_tokens"] == "NOT_PERSISTED"
    assert report["gate"]["passed"] is False
    provenance = (output / "translation_provider_provenance.jsonl").read_text(encoding="utf-8")
    assert "api_key" not in provenance
    assert "response_text" not in provenance
    assert "ALIYUN_DASHSCOPE_QWEN_MT" in provenance
