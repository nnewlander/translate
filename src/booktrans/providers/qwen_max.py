"""Strict structured Qwen-Max candidate, review, and narrow-repair providers.

These providers produce *untrusted in-memory candidates only*.  They do not
write RAW, decide recovery policy, own checkpoint lifecycle, or change Harness
contracts.  The local closed-world validators are intentionally stricter than
the remote JSON schema: a model may return only indexed target text, while
segment/anchor/island identity is always copied from the frozen request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from difflib import SequenceMatcher
import json
import time
from typing import Any, Callable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from booktrans.domain.calibration import CalibrationSourceSegment, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment

from .qwen_mt import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    HttpTransport,
    UrllibJsonTransport,
    _backoff_seconds,
    _usage,
)
from .translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationCandidate,
    TranslationProviderRequest,
)
from .qwen_config import MAX_CONFIG, resolve_qwen_provider_config


# ``QWEN_MAX`` is retained as the compatibility symbol for the existing
# provider, route, and credential-profile names. It is the one runtime
# authority for every general-model call (structured translation, review, and
# bounded repair); those stable names do not imply the active model id.
QWEN_GENERAL_MODEL = "qwen3.8-flash"
QWEN_MAX = QWEN_GENERAL_MODEL
STRUCTURED_TRANSLATION_SCHEMA_VERSION = "booktrans.structured-translation.v2"
STRUCTURED_TRANSLATION_LEGACY_SCHEMA_VERSION = "booktrans.structured-translation.v1"
STRUCTURED_REVIEW_SCHEMA_VERSION = "booktrans.structured-review.v1"
STRUCTURED_REPAIR_SCHEMA_VERSION = "booktrans.structured-repair.v1"
REPAIR_CONTRACT_SCHEMA_VERSION = "booktrans.repair-contract.v1"
GROUP_REVIEW_SCHEMA_VERSION = "booktrans.structured-group-review.v1"
GROUP_REPAIR_CONTRACT_SCHEMA_VERSION = "booktrans.semantic-group-repair-contract.v1"
STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION = "booktrans.structured-group-repair.v1"
SEMANTIC_GROUP_POLICY = "SEMANTIC_GROUP_SPAN"
SEGMENT_MODE_POLICY_VERSION = "booktrans.segment-mode.v1"
SegmentMode = Literal["TEXT", "PRESERVE", "INTENTIONAL_EMPTY"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StructuredTranslationSegment(_StrictModel):
    """The only model-controlled translation field is the exact segment text."""

    index: int
    target_text: str


class StructuredTranslationCandidate(_StrictModel):
    """Legacy parser retained for immutable historical v1 response tests only."""

    schema_version: Literal[STRUCTURED_TRANSLATION_LEGACY_SCHEMA_VERSION]
    unit_uid: str
    source_fingerprint: str
    segments: list[StructuredTranslationSegment]


class StructuredTranslationTextArray(_StrictModel):
    """Translation wire shape: order is harness-owned, text is model-owned."""

    target_texts: list[str]


@dataclass(frozen=True)
class StructuredProviderResult:
    candidate: TranslationCandidate
    structured: StructuredTranslationTextArray
    strategy: Literal["DIRECT_STRUCTURED", "HOLISTIC_PROJECTION"]
    raw: TranslationResult


class ReviewIssue(_StrictModel):
    code: str
    affected_segment_indices: list[int]
    reason: str
    repair_instruction: str | None


class ReviewResult(_StrictModel):
    """Locally bound production review; its unit identity is never model authority."""

    schema_version: Literal[STRUCTURED_REVIEW_SCHEMA_VERSION]
    unit_uid: str
    verdict: Literal["ACCEPT", "REPAIR", "HUMAN_REQUIRED"]
    issues: list[ReviewIssue]


class ReviewerSemanticResponse(_StrictModel):
    """Wire response: the optional legacy UID is diagnostic model echo only."""

    schema_version: Literal[STRUCTURED_REVIEW_SCHEMA_VERSION]
    unit_uid: str | None = None
    verdict: Literal["ACCEPT", "REPAIR", "HUMAN_REQUIRED"]
    issues: list[ReviewIssue]


@dataclass(frozen=True)
class ReviewerProviderResult:
    """Non-authoritative review plus safe response provenance/usage facts."""

    review: ReviewResult
    candidate: TranslationCandidate
    model_echo_unit_uid: str | None = None


@dataclass(frozen=True)
class RepairProviderResult:
    """Narrow repair result plus safe response provenance/usage facts."""

    repaired: TranslationResult
    candidate: TranslationCandidate


class ApprovedRepairContract(_StrictModel):
    """A narrow policy-approved contract; the provider cannot extend it."""

    schema_version: Literal[REPAIR_CONTRACT_SCHEMA_VERSION] = REPAIR_CONTRACT_SCHEMA_VERSION
    policy_id: str
    unit_uid: str
    source_fingerprint: str
    permitted_segment_indices: list[int] = Field(min_length=1)
    repair_instruction: str
    # This is authored by the local repair gate, never inferred from provider
    # output.  It is deliberately optional so historical v1 contracts retain
    # their exact non-empty behaviour.
    intentional_empty_authorizations: list["IntentionalEmptyAuthorization"] = Field(default_factory=list)


class IntentionalEmptyAuthorization(_StrictModel):
    """Harness authorization to project a frozen TEXT segment as empty.

    ``carrier_segment_indices`` name the already-existing in-scope semantic
    carriers.  The provider may select only this pre-authorized operation; it
    never owns an anchor, index, source binding, or carrier identity.
    """

    empty_index: int
    carrier_segment_indices: list[int] = Field(min_length=1)
    evidence_codes: list[str] = Field(min_length=1)


class StructuredRepairPatch(_StrictModel):
    index: int
    operation: Literal["KEEP", "REPLACE", "INTENTIONAL_EMPTY"] = "REPLACE"
    target_text: str | None = None


class StructuredRepairResult(_StrictModel):
    schema_version: Literal[STRUCTURED_REPAIR_SCHEMA_VERSION]
    unit_uid: str
    source_fingerprint: str
    patches: list[StructuredRepairPatch]


class GroupReviewIssue(_StrictModel):
    code: str
    logical_work_order_id: str
    unit_uid: str
    affected_segment_indices: list[int]
    reason: str
    repair_instruction: str | None


class GroupReviewResult(_StrictModel):
    schema_version: Literal[GROUP_REVIEW_SCHEMA_VERSION]
    review_group_id: str
    verdict: Literal["ACCEPT", "REPAIR", "HUMAN_REQUIRED"]
    issues: list[GroupReviewIssue]


@dataclass(frozen=True)
class GroupReviewerProviderResult:
    review: GroupReviewResult
    candidate: TranslationCandidate


class SemanticGroupAllowedRepair(_StrictModel):
    logical_work_order_id: str
    unit_uid: str
    allowed_segment_indices: list[int] = Field(min_length=1)
    source_fingerprint: str
    intentional_empty_authorizations: list[IntentionalEmptyAuthorization] = Field(default_factory=list)


class SemanticGroupRepairContract(_StrictModel):
    schema_version: Literal[GROUP_REPAIR_CONTRACT_SCHEMA_VERSION] = GROUP_REPAIR_CONTRACT_SCHEMA_VERSION
    policy_id: str = SEMANTIC_GROUP_POLICY
    review_group_id: str
    member_work_order_ids: list[str] = Field(min_length=1)
    member_unit_uids: list[str] = Field(min_length=1)
    group_hash: str
    source_span_hash: str
    allowed_repairs: list[SemanticGroupAllowedRepair] = Field(min_length=1)
    immutable_raw_shas: dict[str, str]
    repair_instruction: str


class StructuredGroupRepairPatch(_StrictModel):
    logical_work_order_id: str
    unit_uid: str
    index: int
    operation: Literal["KEEP", "REPLACE", "INTENTIONAL_EMPTY"] = "REPLACE"
    target_text: str | None = None


class StructuredGroupRepairResult(_StrictModel):
    schema_version: Literal[STRUCTURED_GROUP_REPAIR_SCHEMA_VERSION]
    review_group_id: str
    patches: list[StructuredGroupRepairPatch] = Field(min_length=1)


@dataclass(frozen=True)
class GroupRepairProviderResult:
    repaired: dict[str, TranslationResult]
    candidate: TranslationCandidate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise ProviderRuntimeError("QWEN_RESPONSE_CHOICES_INVALID", retryable=False)
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise ProviderRuntimeError("QWEN_RESPONSE_CONTENT_INVALID", retryable=False)
    return message["content"]


def _schema_payload(name: str, model: type[BaseModel] | Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": model.model_json_schema() if isinstance(model, type) else dict(model)},
    }


@dataclass
class QwenMaxProvider:
    """OpenAI-compatible Qwen Max client for strict structured candidates."""

    api_key: str
    base_url: str
    transport: HttpTransport
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    sleep: Callable[[float], None] = time.sleep
    provider_name: str = "ALIYUN_DASHSCOPE_QWEN_MAX"
    model: str = QWEN_MAX
    config_profile: str = MAX_CONFIG

    @classmethod
    def from_environment(cls, *, transport: HttpTransport | None = None) -> "QwenMaxProvider":
        config = resolve_qwen_provider_config(MAX_CONFIG)
        return cls(api_key=config.api_key, base_url=config.base_url, transport=transport or UrllibJsonTransport(), config_profile=config.config_profile)

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def _call_structured(
        self,
        *,
        payload_body: Mapping[str, Any],
        schema_name: str,
        schema_model: type[BaseModel] | Mapping[str, Any],
    ) -> tuple[str, Mapping[str, Any], str, str, int]:
        if self.max_attempts < 1:
            raise ProviderConfigurationError("PROVIDER_RETRY_CONFIGURATION_INVALID")
        payload = {
            "model": self.model,
            # Deliberately only one user message.  All constraints are in the
            # supplied structured task, not an untracked system instruction.
            "messages": [{"role": "user", "content": json.dumps(payload_body, ensure_ascii=False, separators=(",", ":"))}],
            "response_format": _schema_payload(schema_name, schema_model),
            # Qwen hybrid models otherwise default to thinking mode.  These
            # bounded mechanical calls use the non-thinking path by policy.
            "enable_thinking": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: ProviderRuntimeError | None = None
        for attempt in range(1, self.max_attempts + 1):
            requested_at = _utc_now()
            try:
                status, response = self.transport.post(self.endpoint, headers=headers, payload=payload, timeout_seconds=self.timeout_seconds)
            except ProviderRuntimeError as error:
                last_error = error
                if not error.retryable or attempt == self.max_attempts:
                    raise
                self.sleep(_backoff_seconds(attempt))
                continue
            if status in {401, 403}:
                raise ProviderRuntimeError("PROVIDER_AUTHENTICATION_FAILED", retryable=False, status_code=status)
            if status == 429 or 500 <= status <= 599:
                last_error = ProviderRuntimeError(f"QWEN_HTTP_{status}", retryable=True, status_code=status)
                if attempt == self.max_attempts:
                    raise last_error
                self.sleep(_backoff_seconds(attempt))
                continue
            if status < 200 or status >= 300:
                raise ProviderRuntimeError(f"QWEN_HTTP_{status}", retryable=False, status_code=status)
            return _response_text(response), response, requested_at, _utc_now(), attempt
        raise last_error or ProviderRuntimeError("QWEN_UNEXPECTED_NO_RESPONSE", retryable=False)

    def _candidate(
        self,
        request: TranslationProviderRequest,
        *,
        response_text: str,
        response: Mapping[str, Any],
        requested_at: str,
        responded_at: str,
        attempt_count: int,
    ) -> TranslationCandidate:
        return TranslationCandidate(
            provider=self.provider_name,
            model=self.model,
            request_binding_sha256=request.request_binding_sha256,
            translation_options_sha256=request.translation_options_sha256,
            source_sha256=request.request.source_sha256,
            unit_uid=request.request.unit_uid,
            display_id=request.request.display_id,
            response_text=response_text,
            requested_at=requested_at,
            responded_at=responded_at,
            response_id=str(response["id"]) if response.get("id") is not None else None,
            usage=_usage(response),
            attempt_count=attempt_count,
            config_profile=self.config_profile,
        )

    def translate_structured(self, request: TranslationProviderRequest) -> StructuredProviderResult:
        body = _structured_translation_input(request, task="DIRECT_STRUCTURED_TRANSLATION")
        text, response, requested_at, responded_at, attempts = self._call_structured(
            payload_body=body,
            schema_name="booktrans_structured_translation_text_array_v2",
            schema_model=_translation_text_array_schema(request),
        )
        candidate = self._candidate(request, response_text=text, response=response, requested_at=requested_at, responded_at=responded_at, attempt_count=attempts)
        structured, raw = _validated_text_array_candidate(request, text, candidate)
        return StructuredProviderResult(candidate=candidate, structured=structured, strategy="DIRECT_STRUCTURED", raw=raw)

    def project_holistic(
        self, request: TranslationProviderRequest, *, holistic_candidate: TranslationCandidate
    ) -> StructuredProviderResult:
        if holistic_candidate.unit_uid != request.request.unit_uid or holistic_candidate.request_binding_sha256 != request.request_binding_sha256:
            raise ProviderContractGap("PROVIDER_CANDIDATE_REQUEST_BINDING_MISMATCH")
        if not holistic_candidate.response_text.strip():
            raise ProviderContractGap("PROVIDER_CONTRACT_GAP_EMPTY_HOLISTIC_CANDIDATE")
        body = _structured_translation_input(
            request,
            task="CONSTRAINED_HOLISTIC_PROJECTION_ONLY",
            holistic_translation=holistic_candidate.response_text,
        )
        text, response, requested_at, responded_at, attempts = self._call_structured(
            payload_body=body,
            schema_name="booktrans_structured_translation_projection_text_array_v2",
            schema_model=_translation_text_array_schema(request),
        )
        candidate = self._candidate(request, response_text=text, response=response, requested_at=requested_at, responded_at=responded_at, attempt_count=attempts)
        structured, raw = _validated_text_array_candidate(request, text, candidate)
        return StructuredProviderResult(candidate=candidate, structured=structured, strategy="HOLISTIC_PROJECTION", raw=raw)


def _structured_translation_input(
    request: TranslationProviderRequest,
    *,
    task: str,
    holistic_translation: str | None = None,
) -> dict[str, Any]:
    source = request.request
    body: dict[str, Any] = {
        "task": task,
        "instruction": (
            "Translate the complete semantic unit from English to Chinese and return only the strict response schema. "
            "Return target_texts in exactly the supplied translatable-segment order. Do not return segment IDs, indices, "
            "unit IDs, source fingerprints, preserved segments, or empty-source segments."
            if task == "DIRECT_STRUCTURED_TRANSLATION"
            else "Project the supplied complete Chinese translation into the exact source segment structure. Do not retranslate, rewrite, or improve it. Return only the strict response schema. Preserve protected segments verbatim. Copy whitespace-only source segments unchanged; do not omit them."
        ),
        "output_contract_version": STRUCTURED_TRANSLATION_SCHEMA_VERSION,
        "source_text": source.source_text,
        "source_segments": [
            {
                "text": segment.text,
                "mode": classify_source_segment_mode(segment),
            }
            for index, segment in enumerate(source.source_segments)
        ],
        "reference_aids": request.translation_options,
    }
    if holistic_translation is not None:
        body["holistic_translation"] = holistic_translation
    if request.protocol_retry_feedback is not None:
        body["protocol_retry_feedback"] = dict(request.protocol_retry_feedback)
    return body


def classify_source_segment_mode(segment: CalibrationSourceSegment) -> SegmentMode:
    """Local closed-world mode.  The model never supplies or overrides this."""
    if segment.kind == "protected":
        return "PRESERVE"
    if segment.text == "":
        return "INTENTIONAL_EMPTY"
    if segment.text.strip() == "":
        return "PRESERVE"
    return "TEXT"


def _segment_gap(code: str, *, request, index: int, source_segment, target_text: str, mode: SegmentMode) -> ProviderContractGap:
    return ProviderContractGap(
        code,
        details={
            "unit_uid": request.unit_uid,
            "display_id": request.display_id,
            "segment_index": index,
            "segment_mode": mode,
            "source_kind": source_segment.kind,
            "source_text": source_segment.text,
            "target_text": target_text,
        },
    )


def _accept_target_text(source_segment: CalibrationSourceSegment, target_text: str, *, request, index: int) -> None:
    mode = classify_source_segment_mode(source_segment)
    if mode == "PRESERVE":
        if target_text == source_segment.text:
            return
        if source_segment.kind == "protected":
            raise _segment_gap("PROVIDER_CONTRACT_GAP_PROTECTED_SEGMENT_MUTATED", request=request, index=index, source_segment=source_segment, target_text=target_text, mode=mode)
        if not target_text.strip():
            raise _segment_gap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT", request=request, index=index, source_segment=source_segment, target_text=target_text, mode=mode)
        raise _segment_gap("PROVIDER_CONTRACT_GAP_WHITESPACE_NOT_PRESERVED", request=request, index=index, source_segment=source_segment, target_text=target_text, mode=mode)
    if mode == "INTENTIONAL_EMPTY":
        if target_text != "":
            raise _segment_gap("PROVIDER_CONTRACT_GAP_NONEMPTY_FOR_EMPTY_SOURCE", request=request, index=index, source_segment=source_segment, target_text=target_text, mode=mode)
        return
    if not target_text.strip():
        raise _segment_gap("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT", request=request, index=index, source_segment=source_segment, target_text=target_text, mode=mode)


def _parse_structured_candidate(text: str) -> StructuredTranslationCandidate:
    try:
        return StructuredTranslationCandidate.model_validate_json(text)
    except Exception as exc:  # Pydantic emits several precise ValidationError variants.
        raise ProviderContractGap("PROVIDER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID") from exc


def _translation_output_indices(request: TranslationProviderRequest) -> list[int]:
    return [index for index, segment in enumerate(request.request.source_segments) if classify_source_segment_mode(segment) == "TEXT"]


def _translation_text_array_schema(request: TranslationProviderRequest) -> dict[str, Any]:
    count = len(_translation_output_indices(request))
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"target_texts": {"type": "array", "minItems": count, "maxItems": count, "items": {"type": "string", "minLength": 1}}},
        "required": ["target_texts"],
    }


def _parse_text_array_candidate(text: str) -> StructuredTranslationTextArray:
    try:
        return StructuredTranslationTextArray.model_validate_json(text)
    except Exception as exc:
        raise ProviderContractGap("PROVIDER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID") from exc


def _validated_text_array_candidate(
    request: TranslationProviderRequest, text: str, candidate: TranslationCandidate,
) -> tuple[StructuredTranslationTextArray, TranslationResult]:
    try:
        structured = _parse_text_array_candidate(text)
        raw = text_array_candidate_to_raw(request, structured)
    except ProviderContractGap as exc:
        exc.candidate = candidate
        raise
    return structured, raw


def text_array_candidate_to_raw(
    provider_request: TranslationProviderRequest, candidate: StructuredTranslationTextArray,
) -> TranslationResult:
    """Deterministically project ordered model text into harness-owned segments."""
    request = provider_request.request
    output_indices = _translation_output_indices(provider_request)
    if len(candidate.target_texts) != len(output_indices):
        raise ProviderContractGap("PROVIDER_CONTRACT_GAP_TARGET_TEXT_COUNT_MISMATCH")
    targets = dict(zip(output_indices, candidate.target_texts, strict=True))
    projected: list[StructuredTargetSegment] = []
    for index, source_segment in enumerate(request.source_segments):
        mode = classify_source_segment_mode(source_segment)
        target_text = targets[index] if mode == "TEXT" else ("" if mode == "INTENTIONAL_EMPTY" else source_segment.text)
        _accept_target_text(source_segment, target_text, request=request, index=index)
        projected.append(StructuredTargetSegment(text=target_text, kind=source_segment.kind, anchor_ref=source_segment.anchor_ref, island_ref=source_segment.island_ref))
    return TranslationResult(unit_uid=request.unit_uid, target_segments=projected)


def _validated_structured_candidate(
    request: TranslationProviderRequest, text: str, candidate: TranslationCandidate
) -> tuple[StructuredTranslationCandidate, TranslationResult]:
    try:
        structured = _parse_structured_candidate(text)
        raw = structured_candidate_to_raw(request, structured)
    except ProviderContractGap as exc:
        exc.candidate = candidate
        raise
    return structured, raw


def structured_candidate_to_raw(
    provider_request: TranslationProviderRequest, candidate: StructuredTranslationCandidate
) -> TranslationResult:
    """Project only a complete closed-world candidate into existing RAW schema."""
    request = provider_request.request
    if candidate.unit_uid != request.unit_uid or candidate.source_fingerprint != provider_request.request_binding_sha256:
        raise ProviderContractGap("PROVIDER_CANDIDATE_REQUEST_BINDING_MISMATCH")
    expected = set(range(len(request.source_segments)))
    indices = [segment.index for segment in candidate.segments]
    if len(indices) != len(set(indices)):
        raise ProviderContractGap("PROVIDER_CONTRACT_GAP_DUPLICATE_SEGMENT_INDEX")
    if set(indices) != expected:
        raise ProviderContractGap("PROVIDER_CONTRACT_GAP_SEGMENT_INDEX_SET_MISMATCH")
    targets = {segment.index: segment.target_text for segment in candidate.segments}
    projected: list[StructuredTargetSegment] = []
    for index, source_segment in enumerate(request.source_segments):
        target_text = targets[index]
        _accept_target_text(source_segment, target_text, request=request, index=index)
        projected.append(
            StructuredTargetSegment(
                text=target_text,
                kind=source_segment.kind,
                anchor_ref=source_segment.anchor_ref,
                island_ref=source_segment.island_ref,
            )
        )
    return TranslationResult(unit_uid=request.unit_uid, target_segments=projected)


def reviewer_request_payload(
    request: TranslationProviderRequest,
    candidate: TranslationResult,
    *,
    deterministic_qa_summary: Mapping[str, Any],
    semantic_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reviewer input contract: complete semantic-unit context, never a fragment-only view."""
    context = dict(semantic_context or {})
    previous_text = context["previous_text"] if "previous_text" in context else request.request.previous_text
    next_text = context["next_text"] if "next_text" in context else request.request.next_text
    continuation_hint = bool(context.get("continuation_relation") == "BODY_CONTINUATION") or request.request.continuation_hint
    group = context.get("semantic_review_group") if isinstance(context.get("semantic_review_group"), Mapping) else None
    instruction = _reviewer_instruction(context, group)
    unit_context = {
        "display_id": request.request.display_id,
        "previous_text": previous_text,
        "next_text": next_text,
        "continuation_hint": continuation_hint,
        "review_context_version": context.get("review_context_version"),
        "review_contract_version": context.get("review_contract_version"),
        "current_unit_uid": context.get("current_unit_uid") or request.request.unit_uid,
        "previous_semantic_unit_uid": context.get("previous_semantic_unit_uid"),
        "next_semantic_unit_uid": context.get("next_semantic_unit_uid"),
        "continuation_relation": context.get("continuation_relation"),
        "current_lane": context.get("current_lane"),
        "context_hash": context.get("context_hash"),
        "semantic_review_group_id": context.get("semantic_review_group_id"),
        "member_work_order_ids": list(context.get("member_work_order_ids") or []),
        "repairable_work_order_ids": list(context.get("repairable_work_order_ids") or []),
        "group_hash": context.get("group_hash"),
        "full_source_span": context.get("full_source_span"),
        "full_candidate_span": context.get("full_candidate_span"),
        "segment_ownership": list(context.get("segment_ownership") or []),
        "continuation_review_rules": dict(context.get("continuation_review_rules") or {}),
        "source_structural_identity": dict(context.get("source_structural_identity") or {}),
    }
    payload = {
        "task": "REVIEW_ONLY",
        "instruction": instruction,
        "unit_uid": request.request.unit_uid,
        "source_fingerprint": request.request_binding_sha256,
        "source_text": request.request.source_text,
        "source_segments": [segment.model_dump() for segment in request.request.source_segments],
        "target_segments": [segment.model_dump() for segment in candidate.target_segments],
        "unit_context": unit_context,
        "reference_aids": request.translation_options,
        "deterministic_qa_summary": dict(deterministic_qa_summary),
    }
    if group is not None:
        payload["semantic_review_group"] = dict(group)
    return payload


def _reviewer_instruction(context: Mapping[str, Any], group: Mapping[str, Any] | None) -> str:
    identity = context.get("source_structural_identity") if isinstance(context.get("source_structural_identity"), Mapping) else {}
    structural = ""
    technical_name_guidance = (
        " For a standalone source unit that is solely a canonical technical proper name "
        "(for example a programming language, library, or project), retaining that canonical "
        "Latin-script name is not untranslated content and does not require a generic Chinese suffix. "
        "A noncanonical transliteration of such a name may still require a terminology repair."
    )
    if context.get("current_lane") == "FOOTNOTE":
        structural += (
            " The current_lane is FOOTNOTE: this unit is a footnote body, not main-body prose. "
            "A leading footnote marker digit is footnote identity; do not treat it as unexplained body numbering or a layout artifact. "
        )
    if identity.get("review_disposition") == "PRESERVE_NUMERIC_LITERAL":
        structural += (
            " source_structural_identity.review_disposition is PRESERVE_NUMERIC_LITERAL: this selected unit's source is a numeric literal. "
            "Preserve the source numerals. Do not emit REPAIR to delete it as a header, footer, or page-number extraction artifact. "
        )
    if group is not None or context.get("review_contract_version") == "booktrans.review-group.v1":
        return (
            "Review the current-unit candidate as its owned slice of the SemanticReviewGroup. "
            "Evaluate sense, emphasis, copula/subject/predicate completeness, and punctuation on the full "
            "source and candidate semantic spans, not on the current fragment in isolation. "
            "If continuation_relation is BODY_CONTINUATION, the current fragment is not a standalone sentence; "
            "do not require it to contain its own subject, copula, or terminal punctuation. "
            "Do not emit REPAIR solely because the current fragment lacks a subject, copula, or terminal punctuation "
            "when the group span is complete. Source emphasis such as 'absolutely' is judged by group semantic fidelity. "
            "Use MISALIGNMENT only for a structural segment-distribution debt when the full group meaning is complete; "
            "use OMISSION, UNSUPPORTED_ADDITION, SEMANTIC_MISMATCH, INTEGRITY, or SEGMENT_MISALIGNMENT_DUPLICATION "
            "when there is material meaning loss, addition, relationship break, or duplicate meaning in the full group. "
            "Sealed or already approved previous members are read-only; never request edits to them. "
            "affected_segment_indices must index only the current repairable unit's target_segments. "
            "Do not treat a footnote, endnote, sidebar, or textbox as preceding body prose. "
            + structural
            + technical_name_guidance
            + "Do not translate or modify the candidate. Return only the strict review schema."
        )
    return (
        "Review the supplied candidate as one complete semantic unit. "
        "Use previous_text and next_text as discourse context from the semantic reviewer context. "
        "If continuation_relation is BODY_CONTINUATION, the source continues the previous semantic unit. "
        "Do not treat a footnote, endnote, sidebar, or textbox as preceding body prose. "
        "Do not treat a fragment as a standalone document. "
        + structural
        + technical_name_guidance
        + "Do not translate or modify the candidate. Return only the strict review schema."
    )


@dataclass
class QwenMaxReviewerProvider(QwenMaxProvider):
    """Structured reviewer.  It can recommend, never mutate, a candidate."""

    provider_name: str = "ALIYUN_DASHSCOPE_QWEN_MAX_REVIEWER"

    def review(
        self,
        request: TranslationProviderRequest,
        candidate: TranslationResult,
        *,
        deterministic_qa_summary: Mapping[str, Any],
    ) -> ReviewResult:
        return self.review_with_provenance(request, candidate, deterministic_qa_summary=deterministic_qa_summary).review

    def review_with_provenance(
        self,
        request: TranslationProviderRequest,
        candidate: TranslationResult,
        *,
        deterministic_qa_summary: Mapping[str, Any],
        semantic_context: Mapping[str, Any] | None = None,
        protocol_retry: bool = False,
    ) -> ReviewerProviderResult:
        if candidate.unit_uid != request.request.unit_uid or len(candidate.target_segments) != len(request.request.source_segments):
            raise ProviderContractGap("REVIEWER_CANDIDATE_REQUEST_MISMATCH")
        body = reviewer_request_payload(
            request,
            candidate,
            deterministic_qa_summary=deterministic_qa_summary,
            semantic_context=semantic_context,
        )
        if protocol_retry:
            # This is a protocol-only retry.  The request, candidate, and
            # semantic context above are unchanged; the model is told only to
            # return the complete schema for that exact task.
            body = {
                **body,
                "protocol_retry": {
                    "reason": "PREVIOUS_REVIEW_RESPONSE_CONTRACT_INVALID",
                    "instruction": "Return the complete required JSON schema for this same review task. Do not change scope, candidate, or task identity.",
                    "contract_diagnostic": dict(request.protocol_retry_feedback or {}),
                },
            }
        text, response, requested_at, responded_at, attempts = self._call_structured(
            payload_body=body,
            schema_name="booktrans_structured_review_result",
            schema_model=ReviewerSemanticResponse,
        )
        review_candidate = self._candidate(
            request,
            response_text=text,
            response=response,
            requested_at=requested_at,
            responded_at=responded_at,
            attempt_count=attempts,
        )
        try:
            response_result = ReviewerSemanticResponse.model_validate_json(text)
            result = ReviewResult(
                schema_version=response_result.schema_version,
                unit_uid=request.request.unit_uid,
                verdict=response_result.verdict,
                issues=response_result.issues,
            )
            _validate_review_result(request, result)
        except ProviderContractGap as exc:
            _attach_reviewer_contract_diagnostic(exc, body=body, candidate=review_candidate)
            raise
        except Exception as exc:
            error = ProviderContractGap("REVIEWER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID")
            _attach_reviewer_contract_diagnostic(error, body=body, candidate=review_candidate)
            raise error from exc
        return ReviewerProviderResult(
            review=result,
            candidate=review_candidate,
            model_echo_unit_uid=response_result.unit_uid,
        )

    def review_retry_with_provenance(
        self,
        request: TranslationProviderRequest,
        candidate: TranslationResult,
        *,
        deterministic_qa_summary: Mapping[str, Any],
        semantic_context: Mapping[str, Any] | None = None,
    ) -> ReviewerProviderResult:
        """Repeat one identical reviewer task after an invalid wire response."""
        return self.review_with_provenance(
            request,
            candidate,
            deterministic_qa_summary=deterministic_qa_summary,
            semantic_context=semantic_context,
            protocol_retry=True,
        )

    def review_group_with_provenance(
        self,
        members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
        *,
        group_payload: Mapping[str, Any],
        deterministic_qa_summary: Mapping[str, Any],
    ) -> GroupReviewerProviderResult:
        if not members:
            raise ProviderContractGap("GROUP_REVIEW_MEMBERS_MISSING")
        current_request, current_candidate = members[-1]
        member_payloads = []
        for provider_request, candidate in members:
            request = provider_request.request
            if candidate.unit_uid != request.unit_uid or len(candidate.target_segments) != len(request.source_segments):
                raise ProviderContractGap("REVIEWER_CANDIDATE_REQUEST_MISMATCH")
            member_payloads.append(
                {
                    "logical_work_order_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "source_fingerprint": provider_request.request_binding_sha256,
                    "source_text": request.source_text,
                    "source_segments": [segment.model_dump() for segment in request.source_segments],
                    "target_segments": [segment.model_dump() for segment in candidate.target_segments],
                }
            )
        body = {
            "task": "GROUP_REVIEW_ONLY",
            "instruction": _group_reviewer_instruction(group_payload),
            "review_group_id": group_payload.get("semantic_review_group_id"),
            "group_hash": group_payload.get("group_hash"),
            "full_source_span": group_payload.get("full_source_span"),
            "full_candidate_span": group_payload.get("full_candidate_span"),
            "member_work_order_ids": list(group_payload.get("member_work_order_ids") or []),
            "repairable_work_order_ids": list(group_payload.get("repairable_work_order_ids") or []),
            "segment_ownership": list(group_payload.get("segment_ownership") or []),
            "continuation_review_rules": dict(group_payload.get("continuation_review_rules") or {}),
            "members": member_payloads,
            "deterministic_qa_summary": dict(deterministic_qa_summary),
        }
        text, response, requested_at, responded_at, attempts = self._call_structured(
            payload_body=body,
            schema_name="booktrans_structured_group_review_result",
            schema_model=GroupReviewResult,
        )
        try:
            result = GroupReviewResult.model_validate_json(text)
        except Exception as exc:
            raise ProviderContractGap("REVIEWER_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID") from exc
        _validate_group_review_result(members, result, group_payload)
        return GroupReviewerProviderResult(
            review=result,
            candidate=self._candidate(
                current_request,
                response_text=text,
                response=response,
                requested_at=requested_at,
                responded_at=responded_at,
                attempt_count=attempts,
            ),
        )


def _validate_review_result(request: TranslationProviderRequest, result: ReviewResult) -> None:
    if result.unit_uid != request.request.unit_uid:
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_UNIT_UID_MISMATCH")
    valid = set(range(len(request.request.source_segments)))
    all_indices = [index for issue in result.issues for index in issue.affected_segment_indices]
    if any(index not in valid for index in all_indices):
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_SEGMENT_INDEX_INVALID")
    if any(
        not str(issue.code).strip()
        or not str(issue.reason).strip()
        or not issue.affected_segment_indices
        for issue in result.issues
    ):
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_ISSUE_INVALID")
    if result.verdict == "ACCEPT" and result.issues:
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_ACCEPT_WITH_ISSUES")
    if result.verdict == "REPAIR" and (
        not result.issues
        or any(
            not isinstance(issue.repair_instruction, str)
            or not issue.repair_instruction.strip()
            or issue.repair_instruction.strip().casefold() in {"none", "null"}
            for issue in result.issues
        )
    ):
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING")


def _attach_reviewer_contract_diagnostic(
    error: ProviderContractGap,
    *,
    body: Mapping[str, Any],
    candidate: TranslationCandidate,
) -> None:
    """Attach hash-only reviewer facts for a durable production diagnostic.

    The response itself remains untrusted and is never made into a review
    artifact.  The checkpoint writer persists only this binding metadata when
    its bounded reviewer-contract retry is exhausted.
    """
    request_bytes = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    error.reviewer_contract_diagnostic = {
        "provider": candidate.provider,
        "model": candidate.model,
        "request_binding_sha256": candidate.request_binding_sha256,
        "review_request_sha256": sha256(request_bytes).hexdigest(),
        "response_sha256": sha256(candidate.response_text.encode("utf-8")).hexdigest(),
        "response_id": candidate.response_id,
        "requested_at": candidate.requested_at,
        "responded_at": candidate.responded_at,
        "provider_attempt_count": candidate.attempt_count,
    }


def _attach_repair_contract_diagnostic(
    error: ProviderContractGap,
    *,
    body: Mapping[str, Any],
    response_text: str,
    candidate: TranslationCandidate,
) -> None:
    """Preserve the rejected repair payload as bound, append-only evidence.

    The checkpoint writer owns persistence.  This adapter deliberately keeps
    the payload out of the candidate authority path while exposing the exact
    request/response facts required to record a rejected proposal.
    """
    request_bytes = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    error.repair_contract_diagnostic = {
        "provider": candidate.provider,
        "model": candidate.model,
        "request_binding_sha256": candidate.request_binding_sha256,
        "repair_request_sha256": sha256(request_bytes).hexdigest(),
        "response_sha256": sha256(response_text.encode("utf-8")).hexdigest(),
        "response_text": response_text,
        "response_id": candidate.response_id,
        "requested_at": candidate.requested_at,
        "responded_at": candidate.responded_at,
        "provider_attempt_count": candidate.attempt_count,
    }


@dataclass
class QwenMaxRepairProvider(QwenMaxProvider):
    """Policy-constrained narrow repair provider; it never persists its output."""

    provider_name: str = "ALIYUN_DASHSCOPE_QWEN_MAX_REPAIR"

    def repair(
        self,
        request: TranslationProviderRequest,
        current: TranslationResult,
        contract: ApprovedRepairContract,
    ) -> TranslationResult:
        return self.repair_with_provenance(request, current, contract).repaired

    def repair_with_provenance(
        self,
        request: TranslationProviderRequest,
        current: TranslationResult,
        contract: ApprovedRepairContract,
        *,
        protocol_retry: bool = False,
    ) -> RepairProviderResult:
        _validate_repair_contract(request, current, contract)
        permitted_segment_responsibilities = [
            {
                "segment_id": index,
                "source_span": request.request.source_segments[index].text,
                "current_target_text": current.target_segments[index].text,
            }
            for index in contract.permitted_segment_indices
        ]
        body = {
            "task": "NARROW_APPROVED_REPAIR_ONLY",
            "instruction": (
                "The previous repair proposal violated the allowed repair scope. "
                "For this same repair task, return a complete schema-valid proposal that changes only the exact permitted "
                "segment indices. Do not add members, segments, or unsupported text."
                if protocol_retry else
                "Apply only the approved repair contract. Do not retranslate the unit, modify other segments, or alter protected content. "
                "For every permitted segment, preserve its own source-span responsibility: do not copy a clause from an immutable neighbor, repeat a translated clause in another segment, or swap segment responsibilities. "
                "Return only the strict patch schema. An empty target is forbidden unless the contract explicitly "
                "authorizes INTENTIONAL_EMPTY for that exact index; ordinary REPLACE must contain text."
            ),
            "protocol_retry": protocol_retry,
            "policy_id": contract.policy_id,
            "unit_uid": request.request.unit_uid,
            "source_fingerprint": request.request_binding_sha256,
            "permitted_segment_indices": contract.permitted_segment_indices,
            "repair_instruction": contract.repair_instruction,
            "permitted_segment_responsibilities": permitted_segment_responsibilities,
            "source_segments": [segment.model_dump() for segment in request.request.source_segments],
            "current_target_segments": [segment.model_dump() for segment in current.target_segments],
        }
        if protocol_retry:
            body["protocol_retry_feedback"] = dict(request.protocol_retry_feedback or {})
        text, response, requested_at, responded_at, attempts = self._call_structured(
            payload_body=body,
            schema_name="booktrans_structured_repair_result",
            schema_model=StructuredRepairResult,
        )
        try:
            result = StructuredRepairResult.model_validate_json(text)
            repaired = apply_narrow_repair(request, current, contract, result)
        except ProviderContractGap as exc:
            _attach_repair_contract_diagnostic(exc, body=body, response_text=text, candidate=self._candidate(
                request, response_text=text, response=response, requested_at=requested_at,
                responded_at=responded_at, attempt_count=attempts,
            ))
            raise
        except Exception as exc:
            error = ProviderContractGap("REPAIR_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID")
            _attach_repair_contract_diagnostic(error, body=body, response_text=text, candidate=self._candidate(
                request, response_text=text, response=response, requested_at=requested_at,
                responded_at=responded_at, attempt_count=attempts,
            ))
            raise error from exc
        return RepairProviderResult(
            repaired=repaired,
            candidate=self._candidate(
                request,
                response_text=text,
                response=response,
                requested_at=requested_at,
                responded_at=responded_at,
                attempt_count=attempts,
            ),
        )

    def repair_retry_with_provenance(
        self,
        request: TranslationProviderRequest,
        current: TranslationResult,
        contract: ApprovedRepairContract,
    ) -> RepairProviderResult:
        """One caller-owned retry for the exact same approved repair scope."""
        return self.repair_with_provenance(request, current, contract, protocol_retry=True)

    def repair_group(
        self,
        members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
        contract: SemanticGroupRepairContract,
    ) -> dict[str, TranslationResult]:
        return self.repair_group_with_provenance(members, contract).repaired

    def repair_group_with_provenance(
        self,
        members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
        contract: SemanticGroupRepairContract,
    ) -> GroupRepairProviderResult:
        _validate_group_repair_inputs(members, contract)
        member_payloads = []
        for provider_request, current in members:
            request = provider_request.request
            allowed = next(
                (
                    item
                    for item in contract.allowed_repairs
                    if item.logical_work_order_id == request.display_id and item.unit_uid == request.unit_uid
                ),
                None,
            )
            member_payloads.append(
                {
                    "logical_work_order_id": request.display_id,
                    "unit_uid": request.unit_uid,
                    "source_fingerprint": provider_request.request_binding_sha256,
                    "allowed_segment_indices": list(allowed.allowed_segment_indices) if allowed else [],
                    "source_segments": [segment.model_dump() for segment in request.source_segments],
                    "current_target_segments": [segment.model_dump() for segment in current.target_segments],
                }
            )
        body = {
            "task": "GROUP_APPROVED_REPAIR_ONLY",
            "instruction": (
                "Apply only the SemanticGroupRepairContract. Redistribute natural Chinese across allowed "
                "member segments so the full group span is accurate. Do not retranslate unlisted work orders, "
                "alter protected content, or change segment ownership. Use INTENTIONAL_EMPTY only where the "
                "contract explicitly authorizes it; ordinary REPLACE must contain text. Return only the strict group patch schema."
            ),
            "contract": contract.model_dump(),
            "members": member_payloads,
        }
        text, response, requested_at, responded_at, attempts = self._call_structured(
            payload_body=body,
            schema_name="booktrans_structured_group_repair_result",
            schema_model=StructuredGroupRepairResult,
        )
        try:
            result = StructuredGroupRepairResult.model_validate_json(text)
        except Exception as exc:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_STRUCTURED_RESPONSE_INVALID") from exc
        repaired = apply_group_repair(members, contract, result)
        current_request = members[-1][0]
        return GroupRepairProviderResult(
            repaired=repaired,
            candidate=self._candidate(
                current_request,
                response_text=text,
                response=response,
                requested_at=requested_at,
                responded_at=responded_at,
                attempt_count=attempts,
            ),
        )


def _validate_repair_contract(
    request: TranslationProviderRequest, current: TranslationResult, contract: ApprovedRepairContract
) -> None:
    if current.unit_uid != request.request.unit_uid or len(current.target_segments) != len(request.request.source_segments):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_CURRENT_CANDIDATE_MISMATCH")
    if contract.unit_uid != request.request.unit_uid or contract.source_fingerprint != request.request_binding_sha256:
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_REQUEST_BINDING_MISMATCH")
    permitted = contract.permitted_segment_indices
    if len(permitted) != len(set(permitted)) or not set(permitted).issubset(set(range(len(current.target_segments)))):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_PERMITTED_SCOPE_INVALID")
    if any(request.request.source_segments[index].kind == "protected" for index in permitted):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_PROTECTED_SCOPE_FORBIDDEN")
    _validate_intentional_empty_authorizations(
        request.request.source_segments,
        permitted,
        contract.intentional_empty_authorizations,
    )


_STRUCTURAL_REDISTRIBUTION_EVIDENCE = frozenset({
    "segment_misalignment_duplication",
    "semantic_redundancy",
    "duplication",
})


def _validate_intentional_empty_authorizations(
    source_segments: Sequence[CalibrationSourceSegment],
    permitted_indices: Sequence[int],
    authorizations: Sequence[IntentionalEmptyAuthorization],
) -> None:
    """Validate harness-owned redistribution authorization before projection."""
    permitted = set(permitted_indices)
    seen: set[int] = set()
    valid = set(range(len(source_segments)))
    for authorization in authorizations:
        empty = authorization.empty_index
        if empty in seen or empty not in permitted or empty not in valid:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_SCOPE_INVALID")
        seen.add(empty)
        if source_segments[empty].kind == "protected":
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_PROTECTED_SCOPE_FORBIDDEN")
        carriers = authorization.carrier_segment_indices
        if len(carriers) != len(set(carriers)) or not set(carriers).issubset(valid) or empty in carriers:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_CARRIER_INVALID")
        if any(source_segments[index].kind == "protected" for index in carriers):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_PROTECTED_SCOPE_FORBIDDEN")
        if not {str(code).casefold() for code in authorization.evidence_codes} & _STRUCTURAL_REDISTRIBUTION_EVIDENCE:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_EVIDENCE_MISSING")


def _project_repair_patch(
    *,
    source: CalibrationSourceSegment,
    current_text: str,
    patch: StructuredRepairPatch | StructuredGroupRepairPatch,
    request,
    index: int,
    authorizations: Mapping[int, IntentionalEmptyAuthorization],
    carrier_texts: Sequence[str],
) -> str:
    """Project a provider intent without allowing it to own structural identity."""
    if patch.operation == "KEEP":
        if patch.target_text not in (None, current_text):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_KEEP_TEXT_MISMATCH")
        return current_text
    if patch.operation == "INTENTIONAL_EMPTY":
        if patch.target_text not in (None, ""):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_PAYLOAD_INVALID")
        authorization = authorizations.get(index)
        if authorization is None:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_UNAUTHORIZED")
        if any(not carrier_texts[carrier].strip() for carrier in authorization.carrier_segment_indices):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_INTENTIONAL_EMPTY_CARRIER_MISSING")
        return ""
    if patch.target_text is None:
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_REPLACE_TEXT_MISSING")
    try:
        _accept_target_text(source, patch.target_text, request=request, index=index)
    except ProviderContractGap as exc:
        if exc.code == "PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT":
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_EMPTY_TARGET_TEXT", details=exc.details) from exc
        raise
    return patch.target_text


def apply_narrow_repair(
    request: TranslationProviderRequest,
    current: TranslationResult,
    contract: ApprovedRepairContract,
    repair: StructuredRepairResult,
) -> TranslationResult:
    """Return an in-memory patch candidate only after closed-world scope checks."""
    _validate_repair_contract(request, current, contract)
    if repair.unit_uid != request.request.unit_uid or repair.source_fingerprint != request.request_binding_sha256:
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_RESULT_BINDING_MISMATCH")
    indices = [patch.index for patch in repair.patches]
    if len(indices) != len(set(indices)) or not set(indices).issubset(set(contract.permitted_segment_indices)):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_SCOPE_EXPANDED")
    patched = list(current.target_segments)
    authorizations = {item.empty_index: item for item in contract.intentional_empty_authorizations}
    for patch in repair.patches:
        source = request.request.source_segments[patch.index]
        text = _project_repair_patch(
            source=source, current_text=current.target_segments[patch.index].text, patch=patch,
            request=request.request, index=patch.index, authorizations=authorizations,
            carrier_texts=[segment.text for segment in current.target_segments],
        )
        patched[patch.index] = StructuredTargetSegment(
            text=text,
            kind=source.kind,
            anchor_ref=source.anchor_ref,
            island_ref=source.island_ref,
        )
    _reject_repair_generated_neighbor_duplication(current, patched, set(indices))
    return TranslationResult(unit_uid=request.request.unit_uid, target_segments=patched)


def _reject_repair_generated_neighbor_duplication(
    current: TranslationResult,
    patched: Sequence[StructuredTargetSegment],
    changed_indices: set[int],
) -> None:
    """Reject a repair-created copied clause across an immutable neighbor.

    This is deliberately structural and conservative: it only compares a
    changed segment with an immediately adjacent *unchanged* segment and only
    rejects a long, sentence-like exact overlap.  It does not judge ordinary
    translation semantics; the final Reviewer remains that authority.
    """
    for index in changed_indices:
        candidate = "".join(patched[index].text.split())
        if len(candidate) < 12:
            continue
        for neighbor in (index - 1, index + 1):
            if neighbor < 0 or neighbor >= len(patched) or neighbor in changed_indices:
                continue
            original_neighbor = "".join(current.target_segments[neighbor].text.split())
            if len(original_neighbor) < 12:
                continue
            longest = max(SequenceMatcher(None, candidate, original_neighbor).get_matching_blocks(), key=lambda block: block.size)
            overlap = candidate[longest.a:longest.a + longest.size]
            if longest.size >= 12 and any(mark in overlap for mark in "。！？；"):
                raise ProviderContractGap("REPAIR_CONTRACT_GAP_SEGMENT_DUPLICATION")


def _group_reviewer_instruction(group_payload: Mapping[str, Any]) -> str:
    return (
        "Review the SemanticReviewGroup as one semantic sentence. "
        "Judge sense, emphasis, and natural Chinese on the full source and candidate spans. "
        "Do not require each fragment to be a complete clause or to mirror English fragment syntax one-to-one. "
        "Preserve segment ownership: each issue must name an exact repairable logical_work_order_id, unit_uid, "
        "and that member's local affected_segment_indices. "
        "You may recommend repairs only for repairable_work_order_ids. Sealed members are read-only. "
        "Do not translate here. Return only the strict group review schema."
    )


def _validate_group_review_result(
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    result: GroupReviewResult,
    group_payload: Mapping[str, Any],
) -> None:
    expected_group = str(group_payload.get("semantic_review_group_id") or "")
    if expected_group and result.review_group_id != expected_group:
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_GROUP_ID_MISMATCH")
    by_id = {item[0].request.display_id: item[0] for item in members}
    repairable = set(group_payload.get("repairable_work_order_ids") or by_id)
    if result.verdict == "ACCEPT" and result.issues:
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_ACCEPT_WITH_ISSUES")
    if result.verdict == "REPAIR" and (not result.issues or any(not issue.repair_instruction for issue in result.issues)):
        raise ProviderContractGap("REVIEWER_CONTRACT_GAP_REPAIR_INSTRUCTION_MISSING")
    for issue in result.issues:
        provider_request = by_id.get(issue.logical_work_order_id)
        if provider_request is None:
            raise ProviderContractGap("REVIEWER_CONTRACT_GAP_GROUP_MEMBER_UNKNOWN")
        if issue.logical_work_order_id not in repairable:
            raise ProviderContractGap("REVIEWER_CONTRACT_GAP_SEALED_MEMBER")
        if issue.unit_uid != provider_request.request.unit_uid:
            raise ProviderContractGap("REVIEWER_CONTRACT_GAP_UNIT_UID_MISMATCH")
        valid = set(range(len(provider_request.request.source_segments)))
        if any(index not in valid for index in issue.affected_segment_indices):
            raise ProviderContractGap("REVIEWER_CONTRACT_GAP_SEGMENT_INDEX_INVALID")


def _validate_group_repair_inputs(
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    contract: SemanticGroupRepairContract,
) -> dict[str, tuple[TranslationProviderRequest, TranslationResult]]:
    by_id: dict[str, tuple[TranslationProviderRequest, TranslationResult]] = {}
    for provider_request, current in members:
        request = provider_request.request
        if current.unit_uid != request.unit_uid or len(current.target_segments) != len(request.source_segments):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_CURRENT_CANDIDATE_MISMATCH")
        by_id[request.display_id] = (provider_request, current)
    if set(by_id) - set(contract.member_work_order_ids):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_GROUP_MEMBER_UNKNOWN")
    allowed_ids = {item.logical_work_order_id for item in contract.allowed_repairs}
    if not allowed_ids.issubset(set(by_id)):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_GROUP_MEMBER_MISSING")
    if not allowed_ids.issubset(set(contract.member_work_order_ids)):
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_GROUP_SCOPE_EXPANDED")
    for allowed in contract.allowed_repairs:
        pair = by_id.get(allowed.logical_work_order_id)
        if pair is None:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_GROUP_MEMBER_UNKNOWN")
        provider_request, current = pair
        request = provider_request.request
        if allowed.unit_uid != request.unit_uid:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_UNIT_UID_MISMATCH")
        if allowed.source_fingerprint != provider_request.request_binding_sha256:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_REQUEST_BINDING_MISMATCH")
        valid = set(range(len(current.target_segments)))
        if len(allowed.allowed_segment_indices) != len(set(allowed.allowed_segment_indices)):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_PERMITTED_SCOPE_INVALID")
        if any(index not in valid for index in allowed.allowed_segment_indices):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_PERMITTED_SCOPE_INVALID")
        if any(request.source_segments[index].kind == "protected" for index in allowed.allowed_segment_indices):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_PROTECTED_SCOPE_FORBIDDEN")
        _validate_intentional_empty_authorizations(
            request.source_segments,
            allowed.allowed_segment_indices,
            allowed.intentional_empty_authorizations,
        )
    return by_id


def apply_group_repair(
    members: Sequence[tuple[TranslationProviderRequest, TranslationResult]],
    contract: SemanticGroupRepairContract,
    repair: StructuredGroupRepairResult,
) -> dict[str, TranslationResult]:
    """Return in-memory per-member patches.  Never mutates RAW inputs."""
    by_id = _validate_group_repair_inputs(members, contract)
    if repair.review_group_id != contract.review_group_id:
        raise ProviderContractGap("REPAIR_CONTRACT_GAP_GROUP_ID_MISMATCH")
    allowed_map = {
        (item.logical_work_order_id, item.unit_uid): item for item in contract.allowed_repairs
    }
    seen: set[tuple[str, int]] = set()
    patched_texts: dict[str, list] = {
        display_id: list(current.target_segments) for display_id, (_request, current) in by_id.items()
    }
    for patch in repair.patches:
        key = (patch.logical_work_order_id, patch.index)
        if key in seen:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_DUPLICATE_SEGMENT_INDEX")
        seen.add(key)
        pair = by_id.get(patch.logical_work_order_id)
        if pair is None:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_GROUP_MEMBER_UNKNOWN")
        provider_request, current = pair
        request = provider_request.request
        if patch.unit_uid != request.unit_uid:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_UNIT_UID_MISMATCH")
        allowed = allowed_map.get((patch.logical_work_order_id, patch.unit_uid))
        if allowed is None or patch.index not in set(allowed.allowed_segment_indices):
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_SCOPE_EXPANDED")
        source = request.source_segments[patch.index]
        before = list(current.target_segments)
        authorizations = {item.empty_index: item for item in allowed.intentional_empty_authorizations}
        text = _project_repair_patch(
            source=source, current_text=current.target_segments[patch.index].text, patch=patch,
            request=request, index=patch.index, authorizations=authorizations,
            carrier_texts=[segment.text for segment in current.target_segments],
        )
        if current.target_segments != before:
            raise ProviderContractGap("REPAIR_CONTRACT_GAP_RAW_MUTATED")
        patched_texts[patch.logical_work_order_id][patch.index] = StructuredTargetSegment(
            text=text,
            kind=source.kind,
            anchor_ref=source.anchor_ref,
            island_ref=source.island_ref,
        )
    out: dict[str, TranslationResult] = {}
    for display_id, (provider_request, _current) in by_id.items():
        out[display_id] = TranslationResult(
            unit_uid=provider_request.request.unit_uid,
            target_segments=patched_texts[display_id],
        )
    return out
