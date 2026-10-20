from __future__ import annotations

from booktrans.providers.failures import (
    PROVIDER_AUTH_FAILURE,
    PROVIDER_CONFIGURATION_MISSING,
    PROVIDER_CONTRACT_FAILURE,
    PROVIDER_TRANSIENT,
    classify_provider_exception,
)
from booktrans.providers.translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
)
from booktrans.production_control.recovery_policy_registry import decide_recovery_policy


def test_timeout_and_connection_reset_are_transient() -> None:
    timeout = classify_provider_exception(ProviderRuntimeError("QWEN_TRANSPORT_TIMEOUTERROR", retryable=True))
    reset = classify_provider_exception(ProviderRuntimeError("QWEN_TRANSPORT_CONNECTIONRESETERROR", retryable=True))
    assert timeout.family == PROVIDER_TRANSIENT
    assert reset.family == PROVIDER_TRANSIENT
    assert timeout.disposition == "BOUNDED_RETRY"


def test_429_and_5xx_are_transient() -> None:
    too_many = classify_provider_exception(ProviderRuntimeError("QWEN_HTTP_429", retryable=True, status_code=429))
    server = classify_provider_exception(ProviderRuntimeError("QWEN_HTTP_503", retryable=True, status_code=503))
    assert too_many.family == server.family == PROVIDER_TRANSIENT


def test_auth_failure_is_blocked() -> None:
    auth = classify_provider_exception(ProviderRuntimeError("PROVIDER_AUTHENTICATION_FAILED", retryable=False, status_code=401))
    forbidden = classify_provider_exception(ProviderRuntimeError("QWEN_HTTP_403", retryable=False, status_code=403))
    assert auth.family == forbidden.family == PROVIDER_AUTH_FAILURE
    assert auth.disposition == "BLOCKED"


def test_structured_contract_failure_is_fail_closed_not_qa() -> None:
    gap = classify_provider_exception(ProviderContractGap("PROVIDER_CONTRACT_GAP_SEGMENT_INDEX_SET_MISMATCH"))
    assert gap.family == PROVIDER_CONTRACT_FAILURE
    assert gap.disposition == "FAIL_CLOSED"
    policy = decide_recovery_policy({"failure_classification": PROVIDER_CONTRACT_FAILURE, "source_sha_valid": True})
    assert policy["eligible"] is False
    assert policy["denial_reason"] == "PROVIDER_CONTRACT_FAILURE"


def test_configuration_missing_is_blocked() -> None:
    missing = classify_provider_exception(ProviderConfigurationError("PROVIDER_CONFIGURATION_MISSING"))
    assert missing.family == PROVIDER_CONFIGURATION_MISSING
    policy = decide_recovery_policy({"failure_classification": PROVIDER_CONFIGURATION_MISSING, "source_sha_valid": True})
    assert policy["eligible"] is False


def test_provider_transient_registry_retries_only_before_raw() -> None:
    eligible = decide_recovery_policy({
        "failure_classification": PROVIDER_TRANSIENT,
        "source_sha_valid": True,
        "safe_unchanged_cursor": True,
        "raw_required": False,
        "attempt_budget_override": 3,
        "retry_backoff_seconds": [0, 0, 0],
    })
    assert eligible["eligible"] is True
    assert eligible["handler_kind"] == "SUPERVISOR_RETRY"
    after_raw = decide_recovery_policy({
        "failure_classification": PROVIDER_TRANSIENT,
        "source_sha_valid": True,
        "safe_unchanged_cursor": True,
        "raw_required": True,
        "raw_sha_valid": True,
        "attempt_budget_override": 3,
        "retry_backoff_seconds": [0, 0, 0],
    })
    assert after_raw["eligible"] is False
    assert after_raw["denial_reason"] == "PROVIDER_TRANSIENT_AFTER_RAW"
