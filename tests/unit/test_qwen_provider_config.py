from __future__ import annotations

import json

import pytest

from booktrans.providers.qwen_config import MAX_CONFIG, MT_CONFIG, probe_qwen_provider_config, resolve_qwen_provider_config
from booktrans.providers.qwen_max import QwenMaxProvider, QwenMaxRepairProvider, QwenMaxReviewerProvider
from booktrans.providers.qwen_mt import QwenMTProvider
from booktrans.providers.translation_provider import ProviderConfigurationError


def _clear_qwen_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "DASHSCOPE_API_KEY", "BOOKTRANS_QWEN_BASE_URL",
        "BOOKTRANS_QWEN_MT_API_KEY", "BOOKTRANS_QWEN_MT_BASE_URL",
        "BOOKTRANS_QWEN_MAX_API_KEY", "BOOKTRANS_QWEN_MAX_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_mt_and_max_resolve_distinct_provider_specific_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_qwen_environment(monkeypatch)
    monkeypatch.setenv("BOOKTRANS_QWEN_MT_API_KEY", "mt-secret")
    monkeypatch.setenv("BOOKTRANS_QWEN_MT_BASE_URL", "https://mt.example/v1")
    monkeypatch.setenv("BOOKTRANS_QWEN_MAX_API_KEY", "max-secret")
    monkeypatch.setenv("BOOKTRANS_QWEN_MAX_BASE_URL", "https://max.example/v1")
    mt = QwenMTProvider.from_environment()
    maximum = QwenMaxProvider.from_environment()
    assert (mt.api_key, mt.base_url, mt.config_profile) == ("mt-secret", "https://mt.example/v1", MT_CONFIG)
    assert (maximum.api_key, maximum.base_url, maximum.config_profile) == ("max-secret", "https://max.example/v1", MAX_CONFIG)
    assert QwenMaxReviewerProvider.from_environment().config_profile == MAX_CONFIG
    assert QwenMaxRepairProvider.from_environment().config_profile == MAX_CONFIG


def test_provider_specific_settings_win_over_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_qwen_environment(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "legacy-secret")
    monkeypatch.setenv("BOOKTRANS_QWEN_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("BOOKTRANS_QWEN_MT_API_KEY", "mt-secret")
    monkeypatch.setenv("BOOKTRANS_QWEN_MT_BASE_URL", "https://mt.example/v1")
    config = resolve_qwen_provider_config(MT_CONFIG)
    assert config.api_key == "mt-secret"
    assert config.base_url == "https://mt.example/v1"
    assert config.legacy_fallback is False
    maximum = resolve_qwen_provider_config(MAX_CONFIG)
    assert maximum.api_key == "legacy-secret"
    assert maximum.base_url == "https://legacy.example/v1"
    assert maximum.legacy_fallback is True


def test_missing_one_provider_configuration_does_not_affect_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_qwen_environment(monkeypatch)
    monkeypatch.setenv("BOOKTRANS_QWEN_MAX_API_KEY", "max-secret")
    monkeypatch.setenv("BOOKTRANS_QWEN_MAX_BASE_URL", "https://max.example/v1")
    assert QwenMaxProvider.from_environment().base_url == "https://max.example/v1"
    with pytest.raises(ProviderConfigurationError, match="PROVIDER_CONFIGURATION_MISSING"):
        QwenMTProvider.from_environment()


def test_missing_max_configuration_does_not_affect_mt(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_qwen_environment(monkeypatch)
    monkeypatch.setenv("BOOKTRANS_QWEN_MT_API_KEY", "mt-secret")
    monkeypatch.setenv("BOOKTRANS_QWEN_MT_BASE_URL", "https://mt.example/v1")
    assert QwenMTProvider.from_environment().base_url == "https://mt.example/v1"
    with pytest.raises(ProviderConfigurationError, match="PROVIDER_CONFIGURATION_MISSING"):
        QwenMaxProvider.from_environment()


def test_offline_probe_never_serializes_secret_or_full_base_url() -> None:
    probe = probe_qwen_provider_config(MT_CONFIG, environ={
        "BOOKTRANS_QWEN_MT_API_KEY": "mt-secret-never-log",
        "BOOKTRANS_QWEN_MT_BASE_URL": "https://mt.example/v1/private-path",
    })
    encoded = json.dumps(probe)
    assert probe == {
        "config_profile": MT_CONFIG,
        "ready": True,
        "api_key_env_key": "BOOKTRANS_QWEN_MT_API_KEY",
        "base_url_env_key": "BOOKTRANS_QWEN_MT_BASE_URL",
        "legacy_fallback": False,
        "base_url_host": "mt.example",
    }
    assert "mt-secret-never-log" not in encoded
    assert "/private-path" not in encoded


@pytest.mark.parametrize("profile", (MT_CONFIG, MAX_CONFIG))
def test_probe_rejects_missing_or_invalid_url_without_network(profile: str) -> None:
    missing = probe_qwen_provider_config(profile, environ={})
    assert missing["ready"] is False and missing["error_code"] == "PROVIDER_CONFIGURATION_MISSING"
    invalid = probe_qwen_provider_config(profile, environ={
        "BOOKTRANS_QWEN_MT_API_KEY" if profile == MT_CONFIG else "BOOKTRANS_QWEN_MAX_API_KEY": "secret",
        "BOOKTRANS_QWEN_MT_BASE_URL" if profile == MT_CONFIG else "BOOKTRANS_QWEN_MAX_BASE_URL": "not-a-url",
    })
    assert invalid["ready"] is False and invalid["error_code"] == "PROVIDER_BASE_URL_INVALID"
