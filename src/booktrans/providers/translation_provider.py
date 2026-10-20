"""Small, side-effect-free provider contracts for semantic-unit translation."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment


class ProviderConfigurationError(RuntimeError):
    """A provider cannot be used safely because required configuration is absent."""


class ProviderRuntimeError(RuntimeError):
    """A provider call failed; ``retryable`` is explicit and bounded by caller policy."""

    def __init__(self, code: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class ProviderContractGap(RuntimeError):
    """A plain provider response cannot be proven safe for the existing RAW contract."""

    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})
        self.candidate = None
        self.structured = None


@dataclass(frozen=True)
class ApprovedTranslationExample:
    """Previously approved bilingual example supplied by a bounded caller selection."""

    source: str
    target: str


@dataclass(frozen=True)
class TranslationProviderRequest:
    """One complete existing semantic unit; never a Word run or arbitrary split."""

    request: CalibrationRequest
    terms: tuple[Mapping[str, str], ...] = ()
    tm_list: tuple[ApprovedTranslationExample, ...] = ()
    domains: str = "This is a software-engineering book. Preserve technical terminology, code-adjacent literals, logical relations, and professional explanatory prose."
    # Route-local output contracts participate in request identity.  A provider
    # contract upgrade is a new bounded request, never a third try of an older
    # exhausted request.
    output_contract_version: str = "booktrans.provider-output.v1"
    # Ephemeral, deterministic feedback for the sole bounded protocol retry.
    # It is deliberately excluded from request identity: this is a correction
    # of the same request, not a third request under a different contract.
    protocol_retry_feedback: Mapping[str, Any] | None = None

    @property
    def request_binding_sha256(self) -> str:
        payload = {"request": self.request.model_dump(mode="json"), "output_contract_version": self.output_contract_version}
        return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @property
    def translation_options(self) -> dict[str, Any]:
        return {
            "source_lang": "English",
            "target_lang": "Chinese",
            "terms": [dict(value) for value in self.terms],
            "tm_list": [{"source": value.source, "target": value.target} for value in self.tm_list],
            "domains": self.domains,
        }

    @property
    def translation_options_sha256(self) -> str:
        payload = json.dumps(self.translation_options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TranslationCandidate:
    """Untrusted provider candidate and non-secret immutable-provenance facts."""

    provider: str
    model: str
    request_binding_sha256: str
    translation_options_sha256: str
    source_sha256: str
    unit_uid: str
    display_id: str
    response_text: str
    requested_at: str
    responded_at: str
    response_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    attempt_count: int = 1
    config_profile: str | None = None

    def provenance_record(self) -> dict[str, Any]:
        """Safe sidecar record; callers must never add credentials to it."""
        return {
            "artifact_type": "TRANSLATION_PROVIDER_PROVENANCE",
            "schema_version": 1,
            "provider": self.provider,
            "model": self.model,
            "request_binding_sha256": self.request_binding_sha256,
            "translation_options_sha256": self.translation_options_sha256,
            "source_sha256": self.source_sha256,
            "unit_uid": self.unit_uid,
            "display_id": self.display_id,
            "response_text": self.response_text,
            "requested_at": self.requested_at,
            "responded_at": self.responded_at,
            "response_id": self.response_id,
            "usage": dict(self.usage),
            "attempt_count": self.attempt_count,
            "config_profile": self.config_profile,
        }


class TranslationProvider(Protocol):
    """A candidate generator only; it never owns checkpoints, QA, or sealing."""

    provider_name: str

    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate: ...


def project_plain_candidate_to_raw(
    provider_request: TranslationProviderRequest, candidate: TranslationCandidate
) -> TranslationResult:
    """Allow only a provably one-to-one plain response into the existing RAW model.

    A full semantic unit is always sent to the provider.  For multi-segment
    views, a plain text response has no deterministic boundary/anchor mapping,
    so this deliberately raises instead of splitting, merging, or padding.
    """
    request = provider_request.request
    def fail(code: str) -> None:
        # The response is immutable diagnostic evidence even when it cannot
        # become RAW.  Keep it on the exception so the sole production
        # boundary can persist it without manufacturing a candidate.
        error = ProviderContractGap(code)
        error.candidate = candidate
        raise error

    if candidate.unit_uid != request.unit_uid or candidate.request_binding_sha256 != provider_request.request_binding_sha256:
        fail("PROVIDER_CANDIDATE_REQUEST_BINDING_MISMATCH")
    if len(request.source_segments) != 1:
        fail("PROVIDER_CONTRACT_GAP_MULTI_SEGMENT_PLAIN_TEXT")
    source_segment = request.source_segments[0]
    if source_segment.kind == "protected":
        fail("PROVIDER_CONTRACT_GAP_PROTECTED_ONLY_UNIT")
    if not candidate.response_text.strip():
        fail("PROVIDER_CONTRACT_GAP_EMPTY_TARGET_TEXT")
    return TranslationResult(
        unit_uid=request.unit_uid,
        target_segments=[
            StructuredTargetSegment(
                text=candidate.response_text,
                kind=source_segment.kind,
                anchor_ref=source_segment.anchor_ref,
                island_ref=source_segment.island_ref,
            )
        ],
    )
