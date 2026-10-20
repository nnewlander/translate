"""Current source authority for verified extractor supersessions."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from booktrans.domain.calibration import CalibrationRequest


class EffectiveSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class EffectiveSource:
    request: CalibrationRequest
    effective_source_text: str
    effective_source_binding_sha256: str
    effective_context_binding_sha256: str | None
    source_authority_provenance: Mapping[str, Any]


def resolve_effective_source(*, checkpoint_dir: Path, request: CalibrationRequest) -> EffectiveSource:
    folder = checkpoint_dir / "source_extraction_exact_evidence"
    records: list[tuple[Path, Mapping[str, Any]]] = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json"), key=lambda item: item.name.casefold()):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("artifact_type") == "AUTHORITATIVE_SOURCE_SUPERSESSION" and value.get("logical_work_order_id") == request.display_id:
                records.append((path, value))
    if not records:
        return EffectiveSource(request, request.source_text, request.source_sha256, None, {"kind": "BASE_EXTRACTION"})
    values: set[tuple[str, str, str]] = set()
    for _, record in records:
        if record.get("unit_uid") not in {None, request.unit_uid}:
            raise EffectiveSourceError("EFFECTIVE_SOURCE_UNIT_UID_MISMATCH")
        text, binding, context = record.get("authoritative_source_span"), record.get("authoritative_source_binding_sha256"), record.get("authoritative_context_binding_sha256")
        if not all(isinstance(x, str) and x for x in (text, binding, context)) or len(binding) != 64 or len(context) != 64:
            raise EffectiveSourceError("EFFECTIVE_SOURCE_SUPERSESSION_INVALID")
        values.add((text, binding, context))
    if len(values) != 1:
        raise EffectiveSourceError("EFFECTIVE_SOURCE_SUPERSESSION_CONFLICT")
    text, binding, context = next(iter(values))
    return EffectiveSource(request.model_copy(update={"source_text": text}), text, binding, context, {
        "kind": "AUTHORITATIVE_SOURCE_SUPERSESSION", "paths": [str(path) for path, _ in records],
        "classification": records[0][1].get("classification"),
    })
