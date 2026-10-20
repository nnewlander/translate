"""Create-once worker artifacts.  RAW remains the existing Harness contract."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.harness.translation_artifacts import persist_raw_response
from booktrans.providers.provenance import FORBIDDEN_PROVENANCE_KEYS, persist_provenance_records, provenance_contains_secret
from booktrans.providers.router import ROUTE_QWEN_MAX_STRUCTURED, ROUTE_QWEN_MT_PLUS, RouteDecision
from booktrans.providers.translation_provider import TranslationCandidate


ROUTE_PROVIDER_NAMES = {
    ROUTE_QWEN_MT_PLUS: "ALIYUN_DASHSCOPE_QWEN_MT",
    ROUTE_QWEN_MAX_STRUCTURED: "ALIYUN_DASHSCOPE_QWEN_MAX",
}

CANDIDATE_PROVENANCE_ALLOWLIST = frozenset(
    {
        "artifact_type",
        "schema_version",
        "provider",
        "model",
        "request_binding_sha256",
        "translation_options_sha256",
        "source_sha256",
        "unit_uid",
        "display_id",
        "requested_at",
        "responded_at",
        "response_id",
        "usage",
        "attempt_count",
        "route",
        "route_reason",
        "route_is_authoritative",
        "latency_seconds",
        "config_profile",
        "reconstructed",
        "reconstruction_source",
    }
)

_ENV_SECRET_NAMES = (
    "DASHSCOPE_API_KEY", "BOOKTRANS_QWEN_API_KEY",
    "BOOKTRANS_QWEN_MT_API_KEY", "BOOKTRANS_QWEN_MAX_API_KEY",
)
_BEARER_MARKERS = ("bearer ", "authorization:")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_secret_values() -> tuple[str, ...]:
    values: list[str] = []
    for name in _ENV_SECRET_NAMES:
        value = os.environ.get(name, "").strip()
        if len(value) >= 8:
            values.append(value)
    return tuple(values)


def inspect_secret_leak(record: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return leak hits without including secret values in the hit payload."""
    hits: list[dict[str, str]] = []
    env_values = _env_secret_values()

    def walk(value: Any, field_name: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_name = str(key)
                normalized = key_name.casefold().replace("-", "_")
                if normalized in FORBIDDEN_PROVENANCE_KEYS:
                    hits.append({"field_name": key_name, "matched_rule": "FORBIDDEN_PROVENANCE_KEY"})
                    continue
                walk(item, key_name)
            return
        if isinstance(value, list):
            for item in value:
                walk(item, field_name)
            return
        if not isinstance(value, str):
            return
        lowered = value.casefold()
        if any(marker in lowered for marker in _BEARER_MARKERS):
            hits.append({"field_name": field_name or "value", "matched_rule": "BEARER_TOKEN"})
            return
        for secret in env_values:
            if secret and secret in value:
                hits.append({"field_name": field_name or "value", "matched_rule": "ENV_CREDENTIAL_VALUE"})
                return

    walk(record, "")
    return hits


def assert_record_safe(record: Mapping[str, Any], *, extra_forbidden: tuple[str, ...] = ()) -> None:
    if inspect_secret_leak(record) or provenance_contains_secret(record):
        raise ValueError("WORKER_SECRET_LEAK_BLOCKED")
    if extra_forbidden:
        payload = json.dumps(record, ensure_ascii=False).casefold()
        for fragment in extra_forbidden:
            if fragment and fragment.casefold() in payload:
                raise ValueError("WORKER_SECRET_LEAK_BLOCKED")


def write_json_once(path: Path, payload: Mapping[str, Any]) -> None:
    assert_record_safe(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"ARTIFACT_ALREADY_EXISTS:{path}") from exc


def write_json_once_or_validate(path: Path, payload: Mapping[str, Any]) -> None:
    """Create JSON evidence once, or reuse only byte-identical evidence."""
    assert_record_safe(payload)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError(f"PREPARED_JSON_CONTENT_MISMATCH:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(encoded)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError(f"PREPARED_JSON_CONTENT_MISMATCH:{path}")


def write_json_replace(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace a derived report.  Immutable RAW/request files must not use this."""
    assert_record_safe(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def reconstructed_provenance(
    *,
    request: CalibrationRequest,
    decision: RouteDecision,
    request_binding_sha256: str,
    translation_options_sha256: str,
    reconstruction_source: str,
) -> dict[str, Any]:
    """In-memory route facts reconstructed from RAW.  Never invents usage/latency."""
    return {
        "artifact_type": "TRANSLATION_PROVIDER_PROVENANCE",
        "schema_version": 1,
        "provider": ROUTE_PROVIDER_NAMES.get(decision.route, decision.route),
        "model": decision.model,
        "route": decision.route,
        "route_reason": decision.reason,
        "route_is_authoritative": False,
        "unit_uid": request.unit_uid,
        "display_id": request.display_id,
        "request_binding_sha256": request_binding_sha256,
        "translation_options_sha256": translation_options_sha256,
        "source_sha256": request.source_sha256,
        "reconstructed": True,
        "reconstruction_source": reconstruction_source,
    }


def write_jsonl_once(path: Path, values: Sequence[CalibrationRequest] | Sequence[TranslationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for value in values:
                stream.write(value.model_dump_json())
                stream.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"ARTIFACT_ALREADY_EXISTS:{path}") from exc


def write_jsonl_once_or_validate(path: Path, values: Sequence[CalibrationRequest] | Sequence[TranslationResult]) -> None:
    """Create one prepared JSONL snapshot, or reuse only byte-identical evidence."""
    payload = "".join(f"{value.model_dump_json()}\n" for value in values).encode("utf-8")
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"PREPARED_OUTPUT_CONTENT_MISMATCH:{path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError(f"PREPARED_OUTPUT_CONTENT_MISMATCH:{path}")


def persist_checkpoint_raw(path: Path, responses: Sequence[TranslationResult]) -> str:
    return persist_raw_response(path, list(responses))


def candidate_provenance(
    candidate: TranslationCandidate, decision: RouteDecision, *, latency_seconds: float | None = None
) -> dict[str, Any]:
    record = candidate.provenance_record()
    record.pop("response_text", None)
    record["route"] = decision.route
    record["route_reason"] = decision.reason
    record["route_is_authoritative"] = False
    if latency_seconds is not None:
        record["latency_seconds"] = latency_seconds
    extra = set(record) - CANDIDATE_PROVENANCE_ALLOWLIST
    for key in extra:
        record.pop(key, None)
    assert_record_safe(record)
    return record


def persist_route_provenance(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    persist_provenance_records(path, records)
