"""Provider-specific, secret-safe Qwen configuration resolution."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping
from urllib.parse import urlparse

from .translation_provider import ProviderConfigurationError


MT_CONFIG = "MT_CONFIG"
MAX_CONFIG = "MAX_CONFIG"
_SPECS = {
    MT_CONFIG: ("BOOKTRANS_QWEN_MT_API_KEY", "BOOKTRANS_QWEN_MT_BASE_URL"),
    MAX_CONFIG: ("BOOKTRANS_QWEN_MAX_API_KEY", "BOOKTRANS_QWEN_MAX_BASE_URL"),
}
_LEGACY = ("DASHSCOPE_API_KEY", "BOOKTRANS_QWEN_BASE_URL")


@dataclass(frozen=True)
class QwenProviderConfig:
    """Resolved credentials, with safe metadata kept separate from their values."""

    api_key: str
    base_url: str
    config_profile: str
    api_key_env_key: str
    base_url_env_key: str
    legacy_fallback: bool


def _valid_base_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc)


def resolve_qwen_provider_config(profile: str, *, environ: Mapping[str, str] | None = None) -> QwenProviderConfig:
    """Resolve one provider only; never mix MT and MAX credential fields."""
    if profile not in _SPECS:
        raise ValueError(f"QWEN_CONFIG_PROFILE_INVALID:{profile}")
    values = os.environ if environ is None else environ
    key_name, url_name = _SPECS[profile]
    key = str(values.get(key_name, "")).strip()
    base_url = str(values.get(url_name, "")).strip()
    legacy_fallback = False
    if not key:
        key = str(values.get(_LEGACY[0], "")).strip()
        legacy_fallback = bool(key)
        key_name = _LEGACY[0] if key else key_name
    if not base_url:
        base_url = str(values.get(_LEGACY[1], "")).strip()
        legacy_fallback = legacy_fallback or bool(base_url)
        url_name = _LEGACY[1] if base_url else url_name
    if not key or not base_url:
        raise ProviderConfigurationError("PROVIDER_CONFIGURATION_MISSING")
    if not _valid_base_url(base_url):
        raise ProviderConfigurationError("PROVIDER_BASE_URL_INVALID")
    return QwenProviderConfig(
        api_key=key,
        base_url=base_url,
        config_profile=profile,
        api_key_env_key=key_name,
        base_url_env_key=url_name,
        legacy_fallback=legacy_fallback,
    )


def probe_qwen_provider_config(profile: str, *, environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Offline readiness check.  It never exposes secret values or full URLs."""
    values = os.environ if environ is None else environ
    key_name, url_name = _SPECS.get(profile, ("", ""))
    try:
        config = resolve_qwen_provider_config(profile, environ=values)
    except (ProviderConfigurationError, ValueError) as error:
        return {
            "config_profile": profile,
            "ready": False,
            "api_key_env_key": key_name,
            "base_url_env_key": url_name,
            "error_class": type(error).__name__,
            "error_code": str(error),
        }
    return {
        "config_profile": profile,
        "ready": True,
        "api_key_env_key": config.api_key_env_key,
        "base_url_env_key": config.base_url_env_key,
        "legacy_fallback": config.legacy_fallback,
        "base_url_host": urlparse(config.base_url).netloc,
    }
