"""Qwen-MT Plus OpenAI-compatible candidate provider.

This module intentionally uses only stdlib HTTP.  It records no secrets and
returns no project artifacts; Harness integration remains a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .translation_provider import (
    ApprovedTranslationExample,
    ProviderConfigurationError,
    ProviderRuntimeError,
    TranslationCandidate,
    TranslationProviderRequest,
)
from .qwen_config import MT_CONFIG, resolve_qwen_provider_config


QWEN_MT_PLUS = "qwen-mt-plus"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 45.0


class HttpTransport(Protocol):
    def post(self, url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> tuple[int, Mapping[str, Any]]: ...


class UrllibJsonTransport:
    """Minimal OpenAI-compatible JSON transport with no request logging."""

    def post(self, url: str, *, headers: Mapping[str, str], payload: Mapping[str, Any], timeout_seconds: float) -> tuple[int, Mapping[str, Any]]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - URL comes only from explicit config.
                raw = response.read().decode("utf-8")
                return int(response.status), _json_object(raw)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return int(exc.code), _json_object_or_empty(raw)
        except (TimeoutError, URLError, ConnectionError, OSError) as exc:
            raise ProviderRuntimeError(f"QWEN_TRANSPORT_{type(exc).__name__.upper()}", retryable=True) from exc


def _json_object(raw: str) -> Mapping[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ProviderRuntimeError("QWEN_RESPONSE_NOT_OBJECT", retryable=False)
    return value


def _json_object_or_empty(raw: str) -> Mapping[str, Any]:
    try:
        return _json_object(raw)
    except (ValueError, ProviderRuntimeError):
        return {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usage(value: Mapping[str, Any]) -> dict[str, Any]:
    usage = value.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    return {str(key): item for key, item in usage.items() if isinstance(item, (str, int, float, bool)) or item is None}


@dataclass
class QwenMTProvider:
    """Bounded-retry Qwen-MT candidate provider, with direct provider semantics."""

    api_key: str
    base_url: str
    transport: HttpTransport
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    sleep: Callable[[float], None] = time.sleep
    provider_name: str = "ALIYUN_DASHSCOPE_QWEN_MT"
    model: str = QWEN_MT_PLUS
    config_profile: str = MT_CONFIG

    @classmethod
    def from_environment(cls, *, transport: HttpTransport | None = None) -> "QwenMTProvider":
        config = resolve_qwen_provider_config(MT_CONFIG)
        return cls(api_key=config.api_key, base_url=config.base_url, transport=transport or UrllibJsonTransport(), config_profile=config.config_profile)

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def _payload(self, request: TranslationProviderRequest) -> dict[str, Any]:
        # Qwen-MT's compatible-mode contract permits one user message only.
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": request.request.source_text}],
            "translation_options": request.translation_options,
        }

    def translate(self, request: TranslationProviderRequest) -> TranslationCandidate:
        if self.max_attempts < 1:
            raise ProviderConfigurationError("PROVIDER_RETRY_CONFIGURATION_INVALID")
        payload = self._payload(request)
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
            text = _response_text(response)
            return TranslationCandidate(
                provider=self.provider_name,
                model=self.model,
                request_binding_sha256=request.request_binding_sha256,
                translation_options_sha256=request.translation_options_sha256,
                source_sha256=request.request.source_sha256,
                unit_uid=request.request.unit_uid,
                display_id=request.request.display_id,
                response_text=text,
                requested_at=requested_at,
                responded_at=_utc_now(),
                response_id=str(response["id"]) if response.get("id") is not None else None,
                usage=_usage(response),
                attempt_count=attempt,
                config_profile=self.config_profile,
            )
        raise last_error or ProviderRuntimeError("QWEN_UNEXPECTED_NO_RESPONSE", retryable=False)


def _backoff_seconds(attempt: int) -> float:
    return (1.0, 3.0, 8.0)[min(attempt - 1, 2)]


def _response_text(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise ProviderRuntimeError("QWEN_RESPONSE_CHOICES_INVALID", retryable=False)
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise ProviderRuntimeError("QWEN_RESPONSE_CONTENT_INVALID", retryable=False)
    return message["content"]


def relevant_aids(
    request: TranslationProviderRequest,
    *,
    translation_bible: Path,
    approved_examples: tuple[ApprovedTranslationExample, ...] = (),
    max_terms: int = 8,
    max_tm: int = 3,
) -> TranslationProviderRequest:
    """Bounded lexical selection from the Bible and caller-supplied approved TM."""
    terms = tuple(_relevant_terms(request.request.source_text, translation_bible, max_terms=max_terms))
    memories = tuple(_relevant_memories(request.request.source_text, approved_examples, max_tm=max_tm))
    return TranslationProviderRequest(request=request.request, terms=terms, tm_list=memories, domains=request.domains)


def _relevant_terms(source_text: str, bible: Path, *, max_terms: int) -> list[dict[str, str]]:
    if max_terms < 1 or not bible.is_file():
        return []
    source_lower = source_text.casefold()
    selected: list[dict[str, str]] = []
    for line in bible.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] == "Source term":
            continue
        term, target = cells[0], cells[1]
        if term and target and term.casefold() in source_lower:
            selected.append({"source": term, "target": target})
        if len(selected) >= max_terms:
            break
    return selected


def _relevant_memories(source_text: str, examples: tuple[ApprovedTranslationExample, ...], *, max_tm: int) -> list[ApprovedTranslationExample]:
    if max_tm < 1:
        return []
    tokens = {token for token in source_text.casefold().replace("/", " ").split() if len(token) > 3}
    ranked = [
        (len(tokens.intersection({token for token in example.source.casefold().replace("/", " ").split() if len(token) > 3})), index, example)
        for index, example in enumerate(examples)
    ]
    return [example for score, _, example in sorted(ranked, key=lambda row: (-row[0], row[1])) if score > 0][:max_tm]
