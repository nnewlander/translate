from __future__ import annotations

import json
from pathlib import Path

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment
from booktrans.providers.qwen_mt import QwenMTProvider, relevant_aids
from booktrans.providers.provenance import persist_provider_provenance
from booktrans.providers.translation_provider import (
    ApprovedTranslationExample,
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationProviderRequest,
    project_plain_candidate_to_raw,
)


def request(*, segments: int = 1) -> CalibrationRequest:
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-1",
        display_id="body.p.00001",
        story="body",
        content_role="body_paragraph",
        source_text="The unit test improves Readability for owner teams.",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text=f"segment {index}", anchor_ref=f"anchor-{index}", island_ref="island.001")
            for index in range(segments)
        ],
    )


def response(text: str = "中文译文", *, usage: dict | None = None) -> dict:
    return {"id": "resp-1", "choices": [{"message": {"content": text}}], "usage": usage or {"total_tokens": 9}}


class QueueTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict[str, str], dict, float]] = []

    def post(self, url: str, *, headers, payload, timeout_seconds):
        self.calls.append((url, dict(headers), dict(payload), timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def provider(transport: QueueTransport, sleeps: list[float]) -> QwenMTProvider:
    return QwenMTProvider(
        api_key="test-secret-never-log",
        base_url="https://workspace.example/compatible-mode/v1/",
        transport=transport,
        sleep=sleeps.append,
    )


def test_qwen_request_has_only_one_user_message_and_bounded_options(tmp_path: Path) -> None:
    bible = tmp_path / "bible.md"
    bible.write_text(
        "| Source term | Chinese | Level | Evidence / note |\n| Readability | 可读性 | STRICT | x |\n| owner | 所有者 | PREFERRED | x |\n",
        encoding="utf-8",
    )
    base = TranslationProviderRequest(request=request())
    enriched = relevant_aids(
        base,
        translation_bible=bible,
        approved_examples=(ApprovedTranslationExample(source="A unit test improves quality.", target="单元测试提升质量。"),),
    )
    transport = QueueTransport([(200, response())])
    candidate = provider(transport, []).translate(enriched)
    url, headers, payload, timeout = transport.calls[0]
    assert url == "https://workspace.example/compatible-mode/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-secret-never-log"
    assert timeout == 45.0
    assert payload["model"] == "qwen-mt-plus"
    assert payload["messages"] == [{"role": "user", "content": base.request.source_text}]
    assert payload["translation_options"]["source_lang"] == "English"
    assert payload["translation_options"]["target_lang"] == "Chinese"
    assert payload["translation_options"]["terms"] == [{"source": "Readability", "target": "可读性"}, {"source": "owner", "target": "所有者"}]
    assert payload["translation_options"]["tm_list"] == [{"source": "A unit test improves quality.", "target": "单元测试提升质量。"}]
    assert payload["translation_options"]["domains"].startswith("This is a software-engineering book.")
    assert candidate.response_text == "中文译文"


@pytest.mark.parametrize("outcomes", [
    [ProviderRuntimeError("QWEN_TRANSPORT_TIMEOUTERROR", retryable=True), (200, response())],
    [(429, {}), (200, response())],
    [(503, {}), (200, response())],
])
def test_retryable_provider_faults_are_bounded(outcomes: list[object]) -> None:
    sleeps: list[float] = []
    transport = QueueTransport(outcomes)
    candidate = provider(transport, sleeps).translate(TranslationProviderRequest(request=request()))
    assert candidate.attempt_count == 2
    assert len(transport.calls) == 2
    assert sleeps == [1.0]


def test_authentication_failure_is_fail_closed_and_secret_free() -> None:
    transport = QueueTransport([(401, {"error": {"message": "bad key"}})])
    with pytest.raises(ProviderRuntimeError, match="PROVIDER_AUTHENTICATION_FAILED") as raised:
        provider(transport, []).translate(TranslationProviderRequest(request=request()))
    assert raised.value.retryable is False
    assert len(transport.calls) == 1
    assert "test-secret-never-log" not in str(raised.value)


def test_missing_environment_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKTRANS_QWEN_BASE_URL", raising=False)
    with pytest.raises(ProviderConfigurationError, match="PROVIDER_CONFIGURATION_MISSING"):
        QwenMTProvider.from_environment()


def test_candidate_provenance_never_contains_api_key(tmp_path: Path) -> None:
    transport = QueueTransport([(200, response())])
    candidate = provider(transport, []).translate(TranslationProviderRequest(request=request()))
    assert "test-secret-never-log" not in json.dumps(candidate.provenance_record(), ensure_ascii=False)
    assert candidate.provenance_record()["usage"] == {"total_tokens": 9}
    path = tmp_path / "translation_provider_provenance.jsonl"
    persist_provider_provenance(path, [candidate])
    assert "test-secret-never-log" not in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="PROVIDER_PROVENANCE_ALREADY_EXISTS"):
        persist_provider_provenance(path, [candidate])


def test_projection_keeps_exact_anchor_metadata_for_one_segment() -> None:
    provider_request = TranslationProviderRequest(request=request())
    candidate = provider(QueueTransport([(200, response("译文"))]), []).translate(provider_request)
    raw = project_plain_candidate_to_raw(provider_request, candidate)
    assert raw.target_segments[0].text == "译文"
    assert raw.target_segments[0].anchor_ref == "anchor-0"
    assert raw.target_segments[0].island_ref == "island.001"


def test_multi_segment_plain_text_is_a_provider_contract_gap() -> None:
    provider_request = TranslationProviderRequest(request=request(segments=2))
    candidate = provider(QueueTransport([(200, response())]), []).translate(provider_request)
    with pytest.raises(ProviderContractGap, match="MULTI_SEGMENT_PLAIN_TEXT"):
        project_plain_candidate_to_raw(provider_request, candidate)
