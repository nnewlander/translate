"""Canonical provider-failure taxonomy.  These are not content QA failures."""

from __future__ import annotations

from dataclasses import dataclass

from booktrans.providers.translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
)


PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
PROVIDER_AUTH_FAILURE = "PROVIDER_AUTH_FAILURE"
PROVIDER_CONTRACT_FAILURE = "PROVIDER_CONTRACT_FAILURE"
PROVIDER_CONFIGURATION_MISSING = "PROVIDER_CONFIGURATION_MISSING"

DISPOSITION_BOUNDED_RETRY = "BOUNDED_RETRY"
DISPOSITION_BLOCKED = "BLOCKED"
DISPOSITION_FAIL_CLOSED = "FAIL_CLOSED"

_TRANSIENT_CODE_MARKERS = (
    "TIMEOUT",
    "CONNECTION",
    "URLERROR",
    "RESET",
    "UNAVAILABLE",
)


@dataclass(frozen=True)
class ProviderFailureClassification:
    family: str
    disposition: str
    retryable: bool
    code: str
    status_code: int | None = None


def classify_provider_exception(error: BaseException) -> ProviderFailureClassification:
    """Map a provider exception to the closed-world production taxonomy."""
    if isinstance(error, ProviderConfigurationError):
        return ProviderFailureClassification(
            family=PROVIDER_CONFIGURATION_MISSING,
            disposition=DISPOSITION_BLOCKED,
            retryable=False,
            code=str(error) or "PROVIDER_CONFIGURATION_MISSING",
        )
    if isinstance(error, ProviderContractGap):
        return ProviderFailureClassification(
            family=PROVIDER_CONTRACT_FAILURE,
            disposition=DISPOSITION_FAIL_CLOSED,
            retryable=False,
            code=error.code,
        )
    if isinstance(error, ProviderRuntimeError):
        if error.status_code in {401, 403} or error.code == "PROVIDER_AUTHENTICATION_FAILED":
            return ProviderFailureClassification(
                family=PROVIDER_AUTH_FAILURE,
                disposition=DISPOSITION_BLOCKED,
                retryable=False,
                code=error.code,
                status_code=error.status_code,
            )
        if _is_transient(error):
            return ProviderFailureClassification(
                family=PROVIDER_TRANSIENT,
                disposition=DISPOSITION_BOUNDED_RETRY,
                retryable=True,
                code=error.code,
                status_code=error.status_code,
            )
        return ProviderFailureClassification(
            family=PROVIDER_CONTRACT_FAILURE,
            disposition=DISPOSITION_FAIL_CLOSED,
            retryable=False,
            code=error.code,
            status_code=error.status_code,
        )
    return ProviderFailureClassification(
        family=PROVIDER_CONTRACT_FAILURE,
        disposition=DISPOSITION_FAIL_CLOSED,
        retryable=False,
        code=type(error).__name__,
    )


def _is_transient(error: ProviderRuntimeError) -> bool:
    if error.retryable:
        return True
    if error.status_code == 429 or (error.status_code is not None and 500 <= error.status_code <= 599):
        return True
    code = error.code.upper()
    return any(marker in code for marker in _TRANSIENT_CODE_MARKERS)
