from __future__ import annotations

import json

import pytest

from booktrans.domain.calibration import CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment
from booktrans.providers.qwen_max import (
    ApprovedRepairContract,
    QwenMaxProvider,
    QwenMaxRepairProvider,
    QwenMaxReviewerProvider,
    StructuredRepairResult,
    StructuredTranslationCandidate,
    StructuredTranslationTextArray,
    apply_narrow_repair,
    reviewer_request_payload,
    structured_candidate_to_raw,
    text_array_candidate_to_raw,
)
from booktrans.providers.translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationCandidate,
    TranslationProviderRequest,
)


def request(*, protected: bool = False) -> CalibrationRequest:
    segments = [
        CalibrationSourceSegment(kind="anchored", text="First source segment.", anchor_ref="anchor-0", island_ref="island.001"),
        CalibrationSourceSegment(
            kind="protected" if protected else "anchored",
            text="https://example.test/api" if protected else "Second source segment.",
            anchor_ref="anchor-1",
            island_ref="island.002",
        ),
    ]
    return CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-structured-1",
        display_id="body.p.00002",
        story="body",
        content_role="body_paragraph",
        source_text="First source segment. Second source segment.",
        source_segments=segments,
    )


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


def response(content: str, *, usage: dict | None = None) -> dict:
    return {"id": "max-resp-1", "choices": [{"message": {"content": content}}], "usage": usage or {"total_tokens": 12}}


def provider(transport: QueueTransport, sleeps: list[float]) -> QwenMaxProvider:
    return QwenMaxProvider(
        api_key="test-secret-never-log",
        base_url="https://workspace.example/compatible-mode/v1/",
        transport=transport,
        sleep=sleeps.append,
    )


def structured(provider_request: TranslationProviderRequest, *, segments: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "schema_version": "booktrans.structured-translation.v1",
            "unit_uid": provider_request.request.unit_uid,
            "source_fingerprint": provider_request.request_binding_sha256,
            "segments": segments or [{"index": 0, "target_text": "第一段。"}, {"index": 1, "target_text": "第二段。"}],
        },
        ensure_ascii=False,
    )


def text_array(*texts: str) -> str:
    return json.dumps({"target_texts": list(texts or ("第一段。", "第二段。"))}, ensure_ascii=False)


def test_direct_structured_payload_and_exact_identity_projection() -> None:
    provider_request = TranslationProviderRequest(request=request())
    transport = QueueTransport([(200, response(text_array()))])
    result = provider(transport, []).translate_structured(provider_request)
    _, headers, payload, timeout = transport.calls[0]
    assert headers["Authorization"] == "Bearer test-secret-never-log"
    assert timeout == 45.0
    assert payload["model"] == "qwen3.8-flash"
    assert [message["role"] for message in payload["messages"]] == ["user"]
    assert payload["enable_thinking"] is False
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["target_texts"]["minItems"] == 2
    assert schema["properties"]["target_texts"]["maxItems"] == 2
    assert "index" not in json.dumps(schema)
    raw = result.raw
    assert [segment.text for segment in raw.target_segments] == ["第一段。", "第二段。"]
    assert [(segment.anchor_ref, segment.island_ref) for segment in raw.target_segments] == [("anchor-0", "island.001"), ("anchor-1", "island.002")]
    assert "test-secret-never-log" not in json.dumps(result.candidate.provenance_record(), ensure_ascii=False)


@pytest.mark.parametrize("texts", [[], ["一", "二", "三"]])
def test_text_array_rejects_missing_or_extra_model_text(texts: list[str]) -> None:
    provider_request = TranslationProviderRequest(request=request())
    with pytest.raises(ProviderContractGap, match="TARGET_TEXT_COUNT_MISMATCH"):
        text_array_candidate_to_raw(provider_request, StructuredTranslationTextArray(target_texts=texts))


def test_text_array_keeps_machine_owned_preserve_identity_out_of_model_payload() -> None:
    provider_request = TranslationProviderRequest(request=request(protected=True))
    raw = text_array_candidate_to_raw(provider_request, StructuredTranslationTextArray(target_texts=["第一段。 "]))
    assert [segment.text for segment in raw.target_segments] == ["第一段。 ", "https://example.test/api"]


@pytest.mark.parametrize("text", ["", "   "])
def test_text_array_rejects_empty_or_whitespace_text(text: str) -> None:
    provider_request = TranslationProviderRequest(request=request())
    with pytest.raises(ProviderContractGap, match="EMPTY_TARGET_TEXT"):
        text_array_candidate_to_raw(provider_request, StructuredTranslationTextArray(target_texts=["第一段。", text]))


@pytest.mark.parametrize(
    ("segments", "code"),
    [
        ([{"index": 0, "target_text": "一"}], "SEGMENT_INDEX_SET_MISMATCH"),
        ([{"index": 0, "target_text": "一"}, {"index": 1, "target_text": "二"}, {"index": 2, "target_text": "三"}], "SEGMENT_INDEX_SET_MISMATCH"),
        ([{"index": 0, "target_text": "一"}, {"index": 0, "target_text": "二"}], "DUPLICATE_SEGMENT_INDEX"),
        ([{"index": 0, "target_text": "一"}, {"index": 3, "target_text": "二"}], "SEGMENT_INDEX_SET_MISMATCH"),
    ],
)
def test_closed_world_segment_set_rejects_missing_extra_duplicate_and_wrong_index(segments: list[dict], code: str) -> None:
    provider_request = TranslationProviderRequest(request=request())
    candidate = StructuredTranslationCandidate.model_validate_json(structured(provider_request, segments=segments))
    with pytest.raises(ProviderContractGap, match=code):
        structured_candidate_to_raw(provider_request, candidate)


def test_protected_segment_mutation_is_rejected() -> None:
    provider_request = TranslationProviderRequest(request=request(protected=True))
    candidate = StructuredTranslationCandidate.model_validate_json(
        structured(provider_request, segments=[{"index": 0, "target_text": "第一段"}, {"index": 1, "target_text": "https://changed.example"}])
    )
    with pytest.raises(ProviderContractGap, match="PROTECTED_SEGMENT_MUTATED"):
        structured_candidate_to_raw(provider_request, candidate)


def test_malformed_json_is_a_contract_gap() -> None:
    transport = QueueTransport([(200, response("not-json"))])
    with pytest.raises(ProviderContractGap, match="STRUCTURED_RESPONSE_INVALID"):
        provider(transport, []).translate_structured(TranslationProviderRequest(request=request()))


@pytest.mark.parametrize("outcomes", [
    [ProviderRuntimeError("QWEN_TRANSPORT_TIMEOUTERROR", retryable=True), (200, response("unused"))],
    [(429, {}), (200, response("unused"))],
    [(503, {}), (200, response("unused"))],
])
def test_qwen_max_retries_only_runtime_transients(outcomes: list[object]) -> None:
    provider_request = TranslationProviderRequest(request=request())
    outcomes[-1] = (200, response(text_array()))
    sleeps: list[float] = []
    result = provider(QueueTransport(outcomes), sleeps).translate_structured(provider_request)
    assert result.candidate.attempt_count == 2
    assert sleeps == [1.0]


def test_authentication_fails_closed_without_secret() -> None:
    with pytest.raises(ProviderRuntimeError, match="PROVIDER_AUTHENTICATION_FAILED") as raised:
        provider(QueueTransport([(401, {})]), []).translate_structured(TranslationProviderRequest(request=request()))
    assert raised.value.retryable is False
    assert "test-secret-never-log" not in str(raised.value)


def test_missing_environment_configuration_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("BOOKTRANS_QWEN_BASE_URL", raising=False)
    monkeypatch.delenv("BOOKTRANS_QWEN_MAX_API_KEY", raising=False)
    monkeypatch.delenv("BOOKTRANS_QWEN_MAX_BASE_URL", raising=False)
    with pytest.raises(ProviderConfigurationError, match="PROVIDER_CONFIGURATION_MISSING"):
        QwenMaxProvider.from_environment()


def test_holistic_arm_uses_constrained_projection_not_plain_segment_split() -> None:
    provider_request = TranslationProviderRequest(request=request())
    holistic = TranslationCandidate(
        provider="ALIYUN_DASHSCOPE_QWEN_MT",
        model="qwen-mt-plus",
        request_binding_sha256=provider_request.request_binding_sha256,
        translation_options_sha256=provider_request.translation_options_sha256,
        source_sha256=provider_request.request.source_sha256,
        unit_uid=provider_request.request.unit_uid,
        display_id=provider_request.request.display_id,
        response_text="完整中文译文。",
        requested_at="now",
        responded_at="now",
    )
    transport = QueueTransport([(200, response(text_array()))])
    result = provider(transport, []).project_holistic(provider_request, holistic_candidate=holistic)
    user_body = json.loads(transport.calls[0][2]["messages"][0]["content"])
    assert user_body["task"] == "CONSTRAINED_HOLISTIC_PROJECTION_ONLY"
    assert "Do not retranslate" in user_body["instruction"]
    assert user_body["holistic_translation"] == "完整中文译文。"
    assert result.strategy == "HOLISTIC_PROJECTION"


def test_reviewer_payload_includes_complete_semantic_unit_context() -> None:
    rec = request().model_copy(
        update={
            "previous_text": "libbase might actually be",
            "next_text": "The diamond dependency problem requires three layers.",
            "continuation_hint": True,
        }
    )
    provider_request = TranslationProviderRequest(request=rec)
    candidate = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="第一段", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="第二段", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    payload = reviewer_request_payload(
        provider_request,
        candidate,
        deterministic_qa_summary={"passed": True},
        semantic_context={
            "review_context_version": "booktrans.review-context.v2",
            "previous_text": "keep in mind that libbase might actually be",
            "next_text": "The diamond dependency problem requires three layers.",
            "continuation_relation": "BODY_CONTINUATION",
            "current_unit_uid": rec.unit_uid,
            "previous_semantic_unit_uid": "uid-previous",
            "next_semantic_unit_uid": "uid-next",
            "current_lane": "MAIN_BODY",
            "context_hash": "abc",
        },
    )
    assert payload["source_text"] == rec.source_text
    assert payload["unit_context"]["display_id"] == rec.display_id
    assert payload["unit_context"]["previous_text"] == "keep in mind that libbase might actually be"
    assert payload["unit_context"]["previous_semantic_unit_uid"] == "uid-previous"
    assert payload["unit_context"]["continuation_relation"] == "BODY_CONTINUATION"
    assert payload["unit_context"]["context_hash"] == "abc"
    assert payload["unit_context"]["review_context_version"] == "booktrans.review-context.v2"
    assert "fragment" in payload["instruction"].casefold() or "semantic unit" in payload["instruction"].casefold()
    assert "canonical technical proper name" in payload["instruction"].casefold()


def test_reviewer_payload_uses_group_span_not_fragment_sentence() -> None:
    rec = request().model_copy(
        update={
            "previous_text": "2  For instance, security bugs.",
            "continuation_hint": False,
        }
    )
    provider_request = TranslationProviderRequest(request=rec)
    candidate = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="当前片段", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="第二段", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    payload = reviewer_request_payload(
        provider_request,
        candidate,
        deterministic_qa_summary={"passed": True},
        semantic_context={
            "review_context_version": "booktrans.review-context.v2",
            "review_contract_version": "booktrans.review-group.v1",
            "previous_text": "keep in mind that libbase might actually be",
            "continuation_relation": "BODY_CONTINUATION",
            "current_unit_uid": rec.unit_uid,
            "semantic_review_group_id": "srg:body.p.00030:abc",
            "member_work_order_ids": ["body.p.00010", rec.display_id],
            "repairable_work_order_ids": [rec.display_id],
            "group_hash": "g" * 64,
            "full_source_span": "keep in mind that libbase might actually be absolutely any piece of software.",
            "full_candidate_span": "请记住 libbase 实际上可能是 当前片段",
            "continuation_review_rules": {"punctuation_completeness_scope": "GROUP"},
            "semantic_review_group": {
                "review_contract_version": "booktrans.review-group.v1",
                "repairable_work_order_ids": [rec.display_id],
            },
        },
    )
    assert payload["semantic_review_group"]["repairable_work_order_ids"] == [rec.display_id]
    assert payload["unit_context"]["full_source_span"].startswith("keep in mind")
    assert "group" in payload["instruction"].casefold()
    assert "terminal punctuation" in payload["instruction"].casefold()
    assert "read-only" in payload["instruction"].casefold()


def test_reviewer_schema_is_strict_and_never_patches_candidate() -> None:
    provider_request = TranslationProviderRequest(request=request())
    candidate = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="第一段", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="第二段", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    review_json = json.dumps({"schema_version": "booktrans.structured-review.v1", "unit_uid": "unit-structured-1", "verdict": "REPAIR", "issues": [{"code": "NUMBER", "affected_segment_indices": [1], "reason": "number", "repair_instruction": "preserve 2"}]})
    reviewer = QwenMaxReviewerProvider(api_key="secret", base_url="https://example/v1", transport=QueueTransport([(200, response(review_json))]), sleep=lambda _: None)
    result = reviewer.review(provider_request, candidate, deterministic_qa_summary={"pass": True})
    assert result.verdict == "REPAIR"
    assert candidate.target_segments[1].text == "第二段"


def test_reviewer_model_echo_uid_is_diagnostic_while_local_request_owns_identity() -> None:
    provider_request = TranslationProviderRequest(request=request())
    candidate = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="第一段", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="第二段", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    payload = json.dumps({"schema_version": "booktrans.structured-review.v1", "unit_uid": "wrong-model-echo", "verdict": "ACCEPT", "issues": []})
    reviewer = QwenMaxReviewerProvider(api_key="secret", base_url="https://example/v1", transport=QueueTransport([(200, response(payload))]), sleep=lambda _: None)
    result = reviewer.review_with_provenance(provider_request, candidate, deterministic_qa_summary={"passed": True})
    assert result.review.unit_uid == provider_request.request.unit_uid
    assert result.model_echo_unit_uid == "wrong-model-echo"


def test_repair_contract_scope_cannot_expand_or_touch_protected() -> None:
    provider_request = TranslationProviderRequest(request=request())
    current = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="旧一", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="旧二", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    contract = ApprovedRepairContract(policy_id="NUMERIC_REPRESENTATION", unit_uid="unit-structured-1", source_fingerprint=provider_request.request_binding_sha256, permitted_segment_indices=[1], repair_instruction="only segment 1")
    bad = StructuredRepairResult.model_validate({"schema_version": "booktrans.structured-repair.v1", "unit_uid": "unit-structured-1", "source_fingerprint": provider_request.request_binding_sha256, "patches": [{"index": 0, "target_text": "越界"}]})
    with pytest.raises(ProviderContractGap, match="SCOPE_EXPANDED"):
        apply_narrow_repair(provider_request, current, contract, bad)
    good = StructuredRepairResult.model_validate({"schema_version": "booktrans.structured-repair.v1", "unit_uid": "unit-structured-1", "source_fingerprint": provider_request.request_binding_sha256, "patches": [{"index": 1, "target_text": "修复二"}]})
    repaired = apply_narrow_repair(provider_request, current, contract, good)
    assert [segment.text for segment in repaired.target_segments] == ["旧一", "修复二"]


def test_repair_rejects_a_repair_generated_clause_copied_from_immutable_neighbor() -> None:
    provider_request = TranslationProviderRequest(request=request())
    current = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="前段已经完成并且保持不变。", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="原有后段", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    contract = ApprovedRepairContract(policy_id="SEMANTIC_NARROW_SEGMENT", unit_uid=current.unit_uid, source_fingerprint=provider_request.request_binding_sha256, permitted_segment_indices=[1], repair_instruction="repair only segment 1")
    duplicated = StructuredRepairResult.model_validate({"schema_version": "booktrans.structured-repair.v1", "unit_uid": current.unit_uid, "source_fingerprint": provider_request.request_binding_sha256, "patches": [{"index": 1, "target_text": "前段已经完成并且保持不变。新的后段"}]})
    with pytest.raises(ProviderContractGap, match="SEGMENT_DUPLICATION"):
        apply_narrow_repair(provider_request, current, contract, duplicated)


def test_repair_provider_receives_only_policy_approved_scope() -> None:
    provider_request = TranslationProviderRequest(request=request())
    current = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="旧一", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="旧二", kind="anchored", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    contract = ApprovedRepairContract(policy_id="NUMERIC_REPRESENTATION", unit_uid="unit-structured-1", source_fingerprint=provider_request.request_binding_sha256, permitted_segment_indices=[1], repair_instruction="restore 2")
    repair_json = json.dumps({"schema_version": "booktrans.structured-repair.v1", "unit_uid": "unit-structured-1", "source_fingerprint": provider_request.request_binding_sha256, "patches": [{"index": 1, "target_text": "修复二"}]})
    transport = QueueTransport([(200, response(repair_json))])
    repair_provider = QwenMaxRepairProvider(api_key="secret", base_url="https://example/v1", transport=transport, sleep=lambda _: None)
    repaired = repair_provider.repair(provider_request, current, contract)
    body = json.loads(transport.calls[0][2]["messages"][0]["content"])
    assert body["permitted_segment_indices"] == [1]
    assert body["permitted_segment_responsibilities"] == [{
        "segment_id": 1,
        "source_span": "Second source segment.",
        "current_target_text": "旧二",
    }]
    assert [segment.text for segment in repaired.target_segments] == ["旧一", "修复二"]


def test_whitespace_only_source_is_preserved_not_treated_as_empty() -> None:
    segments = [
        CalibrationSourceSegment(kind="anchored", text="See ", anchor_ref="anchor-0", island_ref="island.001"),
        CalibrationSourceSegment(kind="protected", text="https://example.test/api", anchor_ref="anchor-1", island_ref="island.001"),
        CalibrationSourceSegment(kind="anchored", text=" ", anchor_ref="anchor-2", island_ref="island.001"),
        CalibrationSourceSegment(kind="anchored", text="for", anchor_ref="anchor-3", island_ref="island.001"),
        CalibrationSourceSegment(kind="anchored", text=" details.", anchor_ref="anchor-4", island_ref="island.001"),
    ]
    source = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-whitespace-1",
        display_id="body.p.00075",
        story="body",
        content_role="body_paragraph",
        source_text="See https://example.test/api for details.",
        source_segments=segments,
    )
    provider_request = TranslationProviderRequest(request=source)
    candidate = StructuredTranslationCandidate.model_validate(
        {
            "schema_version": "booktrans.structured-translation.v1",
            "unit_uid": source.unit_uid,
            "source_fingerprint": provider_request.request_binding_sha256,
            "segments": [
                {"index": 0, "target_text": "参见 "},
                {"index": 1, "target_text": "https://example.test/api"},
                {"index": 2, "target_text": " "},
                {"index": 3, "target_text": "以了解"},
                {"index": 4, "target_text": "详情。"},
            ],
        }
    )
    raw = structured_candidate_to_raw(provider_request, candidate)
    assert [segment.text for segment in raw.target_segments] == ["参见 ", "https://example.test/api", " ", "以了解", "详情。"]


def test_empty_target_for_translatable_text_is_still_rejected() -> None:
    provider_request = TranslationProviderRequest(request=request())
    candidate = StructuredTranslationCandidate.model_validate_json(
        structured(provider_request, segments=[{"index": 0, "target_text": "第一段。"}, {"index": 1, "target_text": ""}])
    )
    with pytest.raises(ProviderContractGap, match="EMPTY_TARGET_TEXT") as raised:
        structured_candidate_to_raw(provider_request, candidate)
    assert raised.value.details["segment_index"] == 1
    assert raised.value.details["segment_mode"] == "TEXT"


def test_empty_target_for_whitespace_source_is_not_intentional_empty() -> None:
    source = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-whitespace-empty",
        display_id="body.p.00075",
        story="body",
        content_role="body_paragraph",
        source_text="See x for",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text="See ", anchor_ref="a0", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text=" ", anchor_ref="a1", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text="for", anchor_ref="a2", island_ref="island.001"),
        ],
    )
    provider_request = TranslationProviderRequest(request=source)
    candidate = StructuredTranslationCandidate.model_validate(
        {
            "schema_version": "booktrans.structured-translation.v1",
            "unit_uid": source.unit_uid,
            "source_fingerprint": provider_request.request_binding_sha256,
            "segments": [
                {"index": 0, "target_text": "参见 "},
                {"index": 1, "target_text": ""},
                {"index": 2, "target_text": "以便"},
            ],
        }
    )
    with pytest.raises(ProviderContractGap, match="EMPTY_TARGET_TEXT") as raised:
        structured_candidate_to_raw(provider_request, candidate)
    assert raised.value.details["segment_index"] == 1
    assert raised.value.details["segment_mode"] == "PRESERVE"


def test_intentional_empty_is_only_for_empty_unprotected_source() -> None:
    source = CalibrationRequest(
        source_sha256="a" * 64,
        unit_uid="unit-empty-source",
        display_id="body.p.00000",
        story="body",
        content_role="body_paragraph",
        source_text="Keep",
        source_segments=[
            CalibrationSourceSegment(kind="anchored", text="Keep", anchor_ref="a0", island_ref="island.001"),
            CalibrationSourceSegment(kind="anchored", text="", anchor_ref="a1", island_ref="island.001"),
        ],
    )
    provider_request = TranslationProviderRequest(request=source)
    candidate = StructuredTranslationCandidate.model_validate(
        {
            "schema_version": "booktrans.structured-translation.v1",
            "unit_uid": source.unit_uid,
            "source_fingerprint": provider_request.request_binding_sha256,
            "segments": [{"index": 0, "target_text": "保留"}, {"index": 1, "target_text": ""}],
        }
    )
    raw = structured_candidate_to_raw(provider_request, candidate)
    assert [segment.text for segment in raw.target_segments] == ["保留", ""]


def test_repair_contract_rejects_protected_segment_scope() -> None:
    provider_request = TranslationProviderRequest(request=request(protected=True))
    current = TranslationResult(
        unit_uid=provider_request.request.unit_uid,
        target_segments=[
            StructuredTargetSegment(text="一", kind="anchored", anchor_ref="anchor-0", island_ref="island.001"),
            StructuredTargetSegment(text="https://example.test/api", kind="protected", anchor_ref="anchor-1", island_ref="island.002"),
        ],
    )
    contract = ApprovedRepairContract(policy_id="NUMERIC_REPRESENTATION", unit_uid="unit-structured-1", source_fingerprint=provider_request.request_binding_sha256, permitted_segment_indices=[1], repair_instruction="forbidden")
    result = StructuredRepairResult.model_validate({"schema_version": "booktrans.structured-repair.v1", "unit_uid": "unit-structured-1", "source_fingerprint": provider_request.request_binding_sha256, "patches": []})
    with pytest.raises(ProviderContractGap, match="PROTECTED_SCOPE_FORBIDDEN"):
        apply_narrow_repair(provider_request, current, contract, result)
