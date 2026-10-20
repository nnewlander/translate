"""Immutable sidecar provenance for provider candidates, separate from RAW schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .translation_provider import TranslationCandidate

FORBIDDEN_PROVENANCE_KEYS = frozenset(
    {"api_key", "authorization", "dashscope_api_key", "secret", "bearer", "token", "password"}
)


def provenance_contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold().replace("-", "_") in FORBIDDEN_PROVENANCE_KEYS:
                return True
            if provenance_contains_secret(item):
                return True
        return False
    if isinstance(value, list):
        return any(provenance_contains_secret(item) for item in value)
    return False


def persist_provider_provenance(path: Path, candidates: Iterable[TranslationCandidate]) -> None:
    """Create a provenance JSONL once; credentials are absent by construction."""
    persist_provenance_records(path, (candidate.provenance_record() for candidate in candidates))


def persist_provenance_records(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Create-once sidecar JSONL.  API keys are rejected before any write."""
    rows = [dict(record) for record in records]
    for record in rows:
        if provenance_contains_secret(record):
            raise ValueError("PROVIDER_PROVENANCE_SECRET_BLOCKED")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for record in rows:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                stream.write("\n")
    except FileExistsError as exc:
        raise ValueError(f"PROVIDER_PROVENANCE_ALREADY_EXISTS:{path}") from exc
