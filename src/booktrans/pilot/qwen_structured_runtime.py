"""Isolated Qwen structured-provider pilot with per-unit failure isolation and resume.

This module never writes under a production run, never selects a checkpoint,
and never mutates Translation Harness contracts.  A single candidate failure is
pilot evidence, not a process abort.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

from booktrans.domain.calibration import CalibrationRequest, CalibrationResponseStatus, TranslationResult
from booktrans.harness.calibration import CalibrationImporter
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import persist_raw_response
from booktrans.providers.provenance import persist_provider_provenance
from booktrans.providers.qwen_max import (
    QWEN_MAX,
    QwenMaxProvider,
    QwenMaxReviewerProvider,
    SEGMENT_MODE_POLICY_VERSION,
    STRUCTURED_REVIEW_SCHEMA_VERSION,
    STRUCTURED_TRANSLATION_SCHEMA_VERSION,
    structured_candidate_to_raw,
)
from booktrans.providers.qwen_mt import QWEN_MT_PLUS, QwenMTProvider, relevant_aids
from booktrans.providers.translation_provider import (
    ProviderConfigurationError,
    ProviderContractGap,
    ProviderRuntimeError,
    TranslationCandidate,
    TranslationProviderRequest,
    project_plain_candidate_to_raw,
)


PILOT_RUNTIME_VERSION = "booktrans.qwen-structured-pilot.v2"
IDENTITY_NAME = "pilot_identity.json"
PROGRESS_NAME = "pilot_progress.jsonl"
REPORT_NAME = "pilot_comparison_report.json"
SIMPLE_ARM = "simple_qwen_mt"
ARM_A = "multi_arm_a_direct_structured"
ARM_B = "multi_arm_b_holistic_projection"
ARM_PROGRESS_LABEL = {
    SIMPLE_ARM: "simple_qwen_mt",
    ARM_A: "multi A",
    ARM_B: "multi B",
}
TERMINAL_STATUSES = {
    "PASS",
    "CONTRACT_GAP",
    "PROVIDER_RUNTIME",
    "PROVIDER_AUTHENTICATION",
    "STRUCTURED_VALIDATION",
    "HARNESS_FAIL",
    "UNEXPECTED_UNIT_ERROR",
    "ADOPTED_PASS",
}
FORBIDDEN_RECORD_KEYS = {"api_key", "authorization", "dashscope_api_key", "secret", "bearer"}


class PilotGlobalError(RuntimeError):
    """A condition that must abort the whole isolated pilot."""


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def _contains_forbidden_keys(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold().replace("-", "_") in FORBIDDEN_RECORD_KEYS:
                return True
            if _contains_forbidden_keys(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_keys(item) for item in value)
    return False


def _assert_record_safe(record: Mapping[str, Any], *, forbidden_substrings: tuple[str, ...] = ()) -> None:
    if _contains_forbidden_keys(record):
        raise PilotGlobalError("PILOT_SECRET_LEAK_BLOCKED")
    payload = json.dumps(record, ensure_ascii=False)
    lowered = payload.casefold()
    for fragment in forbidden_substrings:
        if fragment and fragment.casefold() in lowered:
            raise PilotGlobalError("PILOT_SECRET_LEAK_BLOCKED")


def _append_jsonl(path: Path, record: Mapping[str, Any], *, forbidden_substrings: tuple[str, ...] = ()) -> None:
    _assert_record_safe(record, forbidden_substrings=forbidden_substrings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_once(path: Path, payload: Mapping[str, Any]) -> None:
    _assert_record_safe(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise PilotGlobalError(f"QWEN_STRUCTURED_PILOT_ARTIFACT_EXISTS:{path}") from exc


def _write_json_derived(path: Path, payload: Mapping[str, Any]) -> None:
    _assert_record_safe(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_requests(path: Path, requests: list[CalibrationRequest]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for request in requests:
            stream.write(request.model_dump_json())
            stream.write("\n")


def identity_payload(*, plan_sha256: str, source_sha256: str, translation_bible_sha256: str) -> dict[str, Any]:
    return {
        "artifact_type": "QWEN_STRUCTURED_PILOT_IDENTITY",
        "schema_version": 1,
        "plan_sha256": plan_sha256,
        "source_sha256": source_sha256,
        "translation_bible_sha256": translation_bible_sha256,
        "models": {"qwen_mt": QWEN_MT_PLUS, "qwen_max": QWEN_MAX},
        "schema_versions": {
            "structured_translation": STRUCTURED_TRANSLATION_SCHEMA_VERSION,
            "structured_review": STRUCTURED_REVIEW_SCHEMA_VERSION,
            "segment_mode_policy": SEGMENT_MODE_POLICY_VERSION,
            "pilot_runtime": PILOT_RUNTIME_VERSION,
        },
        "enable_thinking": False,
    }


def load_progress(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PilotGlobalError(f"QWEN_STRUCTURED_PILOT_PROGRESS_CORRUPT:line={line_number}") from exc
        if not isinstance(record, dict):
            raise PilotGlobalError(f"QWEN_STRUCTURED_PILOT_PROGRESS_CORRUPT:line={line_number}")
        records.append(record)
    return records


def latest_progress_by_unit(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        arm = record.get("arm")
        unit_uid = record.get("unit_uid")
        if isinstance(arm, str) and isinstance(unit_uid, str):
            latest[(arm, unit_uid)] = record
    return latest


def unit_is_terminal(record: dict[str, Any] | None) -> bool:
    return bool(record) and record.get("status") in TERMINAL_STATUSES


def _latency_seconds(candidate: TranslationCandidate | None) -> float | None:
    if candidate is None:
        return None
    try:
        return (datetime.fromisoformat(candidate.responded_at) - datetime.fromisoformat(candidate.requested_at)).total_seconds()
    except (TypeError, ValueError):
        return None


def _latency_summary(candidates: list[TranslationCandidate]) -> dict[str, Any]:
    durations = [value for value in (_latency_seconds(candidate) for candidate in candidates) if value is not None]
    return {
        "sample_count": len(durations),
        "total_seconds": round(sum(durations), 6),
        "mean_seconds": round(sum(durations) / len(durations), 6) if durations else None,
    }


def _estimated_cost(usage: list[dict[str, Any]], *, input_price_per_million: float | None, output_price_per_million: float | None) -> dict[str, Any]:
    if input_price_per_million is None or output_price_per_million is None:
        return {"currency": "USD", "estimated_cost": None, "reason": "RATE_NOT_SUPPLIED"}
    input_tokens = sum(int(item.get("prompt_tokens", item.get("input_tokens", 0)) or 0) for item in usage)
    output_tokens = sum(int(item.get("completion_tokens", item.get("output_tokens", 0)) or 0) for item in usage)
    return {
        "currency": "USD",
        "estimated_cost": round(input_tokens * input_price_per_million / 1_000_000 + output_tokens * output_price_per_million / 1_000_000, 8),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_price_per_million": input_price_per_million,
        "output_price_per_million": output_price_per_million,
    }


def _progress_line(arm: str, index: int, total: int, status: str, display_id: str, failure_class: str | None = None) -> str:
    label = ARM_PROGRESS_LABEL[arm]
    if arm == SIMPLE_ARM:
        prefix = f"[{index}/{total}] {label}"
    else:
        prefix = f"[{label} {index}/{total}]"
    extra = f" {failure_class}" if failure_class and status != "PASS" else ""
    return f"{prefix} {status}{extra} {display_id}"


def _emit(message: str) -> None:
    print(message, flush=True)


def _load_plan_requests(plan: dict[str, Any]) -> list[CalibrationRequest]:
    values: list[CalibrationRequest] = []
    seen: set[str] = set()
    for entry in plan.get("requests", []):
        path = Path(entry["request_path"])
        line_number = entry["line_number"]
        lines = path.read_text(encoding="utf-8").splitlines()
        request = CalibrationRequest.model_validate_json(lines[line_number - 1])
        if request.unit_uid != entry["unit_uid"] or request.display_id != entry["logical_work_order_id"] or request.unit_uid in seen:
            raise PilotGlobalError("QWEN_STRUCTURED_PILOT_PLAN_BINDING_MISMATCH")
        seen.add(request.unit_uid)
        values.append(request)
    return values


def _classify_failure(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, ProviderContractGap):
        code = exc.code if getattr(exc, "code", None) else str(exc)
        if "STRUCTURED_RESPONSE_INVALID" in code:
            return "STRUCTURED_VALIDATION", code
        return "CONTRACT_GAP", code
    if isinstance(exc, ProviderRuntimeError):
        if exc.code == "PROVIDER_AUTHENTICATION_FAILED":
            return "PROVIDER_AUTHENTICATION", exc.code
        return "PROVIDER_RUNTIME", exc.code
    return "UNEXPECTED_UNIT_ERROR", type(exc).__name__


@dataclass
class UnitCallResult:
    status: str
    failure_class: str | None = None
    candidates: list[TranslationCandidate] = field(default_factory=list)
    raw: TranslationResult | None = None
    details: dict[str, Any] = field(default_factory=dict)
    artifact_paths: dict[str, str] = field(default_factory=dict)


def _unit_dir(arm_out: Path, unit_uid: str) -> Path:
    return arm_out / "units" / unit_uid


def _persist_unit_success(arm_out: Path, request: CalibrationRequest, candidates: list[TranslationCandidate], raw: TranslationResult) -> dict[str, str]:
    destination = _unit_dir(arm_out, request.unit_uid)
    destination.mkdir(parents=True, exist_ok=True)
    request_path = destination / "translation_request.jsonl"
    raw_path = destination / "translation_raw_response.jsonl"
    provenance_path = destination / "translation_provider_provenance.jsonl"
    if raw_path.is_file() or request_path.is_file() or provenance_path.is_file():
        return {"request": str(request_path), "raw": str(raw_path), "provenance": str(provenance_path)}
    _write_requests(request_path, [request])
    persist_raw_response(raw_path, [raw])
    persist_provider_provenance(provenance_path, candidates)
    return {
        "request": str(request_path),
        "raw": str(raw_path),
        "provenance": str(provenance_path),
    }


def _persist_unit_failure(arm_out: Path, request: CalibrationRequest, result: UnitCallResult) -> dict[str, str]:
    destination = _unit_dir(arm_out, request.unit_uid)
    destination.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    evidence_path = destination / "contract_gap_evidence.json"
    evidence = {
        "logical_work_order_id": request.display_id,
        "unit_uid": request.unit_uid,
        "status": result.status,
        "failure_class": result.failure_class,
        "details": result.details,
        "usage": [dict(candidate.usage) for candidate in result.candidates],
        "response_text_present": any(bool(candidate.response_text) for candidate in result.candidates),
    }
    if not evidence_path.is_file():
        _write_json_once(evidence_path, evidence)
    paths["evidence"] = str(evidence_path)
    if result.candidates:
        provenance_path = destination / "translation_provider_provenance.jsonl"
        if not provenance_path.is_file():
            persist_provider_provenance(provenance_path, result.candidates)
        paths["provenance"] = str(provenance_path)
    return paths


def _call_simple(provider_request: TranslationProviderRequest, qwen_mt: QwenMTProvider) -> UnitCallResult:
    candidate = qwen_mt.translate(provider_request)
    raw = project_plain_candidate_to_raw(provider_request, candidate)
    return UnitCallResult(status="CONTRACT_PASS", candidates=[candidate], raw=raw)


def _call_arm_a(provider_request: TranslationProviderRequest, qwen_max: QwenMaxProvider) -> UnitCallResult:
    result = qwen_max.translate_structured(provider_request)
    return UnitCallResult(status="CONTRACT_PASS", candidates=[result.candidate], raw=result.raw)


def _call_arm_b(
    provider_request: TranslationProviderRequest, qwen_mt: QwenMTProvider, qwen_max: QwenMaxProvider
) -> UnitCallResult:
    holistic = qwen_mt.translate(provider_request)
    result = qwen_max.project_holistic(provider_request, holistic_candidate=holistic)
    return UnitCallResult(status="CONTRACT_PASS", candidates=[holistic, result.candidate], raw=result.raw)


def _execute_unit_arm(
    *,
    name: str,
    provider_request: TranslationProviderRequest,
    qwen_mt: QwenMTProvider,
    qwen_max: QwenMaxProvider,
) -> UnitCallResult:
    try:
        if name == SIMPLE_ARM:
            return _call_simple(provider_request, qwen_mt)
        if name == ARM_A:
            return _call_arm_a(provider_request, qwen_max)
        if name == ARM_B:
            return _call_arm_b(provider_request, qwen_mt, qwen_max)
        raise PilotGlobalError("QWEN_STRUCTURED_PILOT_UNKNOWN_ARM")
    except (PilotGlobalError, KeyboardInterrupt, SystemExit):
        raise
    except ProviderConfigurationError as exc:
        if str(exc) == "PROVIDER_CONFIGURATION_MISSING":
            raise PilotGlobalError("PROVIDER_CONFIGURATION_MISSING") from exc
        status, code = "UNEXPECTED_UNIT_ERROR", str(exc)
        return UnitCallResult(status=status, failure_class=code)
    except (ProviderContractGap, ProviderRuntimeError) as exc:
        status, code = _classify_failure(exc)
        details = dict(getattr(exc, "details", {}) or {})
        candidates: list[TranslationCandidate] = []
        attached = getattr(exc, "candidate", None)
        if isinstance(attached, TranslationCandidate):
            candidates.append(attached)
        return UnitCallResult(status=status, failure_class=code, candidates=candidates, details=details)
    except Exception as exc:
        status, code = _classify_failure(exc)
        return UnitCallResult(status=status, failure_class=code)


def _legacy_arm_records(arm_out: Path, model: type, filename: str) -> list[Any]:
    path = arm_out / filename
    if not path.is_file():
        return []
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _legacy_arm_unit_uids(arm_out: Path) -> set[str]:
    path = arm_out / "translation_provider_provenance.jsonl"
    if not path.is_file():
        return set()
    return {json.loads(line)["unit_uid"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _assemble_arm_inputs(arm_out: Path, requests: list[CalibrationRequest]) -> tuple[list[CalibrationRequest], list[TranslationResult]] | None:
    passing_requests: list[CalibrationRequest] = []
    passing_raw: list[TranslationResult] = []
    for request in requests:
        raw_path = _unit_dir(arm_out, request.unit_uid) / "translation_raw_response.jsonl"
        if not raw_path.is_file():
            continue
        lines = [line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) != 1:
            raise PilotGlobalError(f"QWEN_STRUCTURED_PILOT_UNIT_RAW_CORRUPT:{request.unit_uid}")
        raw = TranslationResult.model_validate_json(lines[0])
        if raw.unit_uid != request.unit_uid:
            raise PilotGlobalError(f"QWEN_STRUCTURED_PILOT_UNIT_RAW_BINDING_MISMATCH:{request.unit_uid}")
        passing_requests.append(request)
        passing_raw.append(raw)
    if passing_requests:
        return passing_requests, passing_raw
    legacy_requests = _legacy_arm_records(arm_out, CalibrationRequest, "translation_request.jsonl")
    legacy_raw = _legacy_arm_records(arm_out, TranslationResult, "translation_raw_response.jsonl")
    if not legacy_requests or len(legacy_requests) != len(legacy_raw):
        return None
    by_uid = {item.unit_uid: item for item in legacy_raw}
    selected_requests = [request for request in requests if request.unit_uid in by_uid]
    if not selected_requests:
        return None
    return selected_requests, [by_uid[request.unit_uid] for request in selected_requests]


def _write_derived_arm_jsonl(arm_out: Path, requests: list[CalibrationRequest], raw: list[TranslationResult]) -> tuple[Path, Path]:
    request_path = arm_out / "harness_translation_request.jsonl"
    raw_path = arm_out / "harness_translation_raw_response.jsonl"
    request_path.write_text("".join(item.model_dump_json() + "\n" for item in requests), encoding="utf-8", newline="\n")
    raw_path.write_text("".join(item.model_dump_json() + "\n" for item in raw), encoding="utf-8", newline="\n")
    return request_path, raw_path


def _adopt_simple_arm_if_complete(
    *,
    out: Path,
    requests: list[CalibrationRequest],
    plan_sha256: str,
    source_sha256: str,
    translation_bible: Path,
    progress_path: Path,
    forbidden_substrings: tuple[str, ...],
) -> int:
    arm_out = out / SIMPLE_ARM
    required = [
        arm_out / "translation_request.jsonl",
        arm_out / "translation_raw_response.jsonl",
        arm_out / "translation_provider_provenance.jsonl",
        arm_out / "review_results.json",
        arm_out / "side_by_side.json",
        arm_out / "candidate.docx",
    ]
    if not all(path.is_file() for path in required):
        return 0
    reqs = [CalibrationRequest.model_validate_json(line) for line in required[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    raws = [TranslationResult.model_validate_json(line) for line in required[1].read_text(encoding="utf-8").splitlines() if line.strip()]
    provs = [json.loads(line) for line in required[2].read_text(encoding="utf-8").splitlines() if line.strip()]
    if [item.unit_uid for item in reqs] != [item.unit_uid for item in requests]:
        return 0
    if len(reqs) != len(raws) or len(reqs) != len(provs):
        return 0
    for request, raw, prov in zip(reqs, raws, provs, strict=True):
        if raw.unit_uid != request.unit_uid or prov.get("unit_uid") != request.unit_uid:
            return 0
        if request.source_sha256 != source_sha256 or prov.get("source_sha256") != source_sha256:
            return 0
        if len(request.source_segments) != len(raw.target_segments):
            return 0
        enriched = relevant_aids(TranslationProviderRequest(request=request), translation_bible=translation_bible)
        if prov.get("request_binding_sha256") != enriched.request_binding_sha256:
            return 0
        if prov.get("translation_options_sha256") != enriched.translation_options_sha256:
            return 0
        if prov.get("model") != QWEN_MT_PLUS:
            return 0
    adopted = 0
    for request, raw, prov in zip(reqs, raws, provs, strict=True):
        record = {
            "artifact_type": "QWEN_STRUCTURED_PILOT_PROGRESS",
            "schema_version": 1,
            "plan_sha256": plan_sha256,
            "source_sha256": source_sha256,
            "provider": prov.get("provider"),
            "model": prov.get("model"),
            "arm": SIMPLE_ARM,
            "unit_uid": request.unit_uid,
            "logical_work_order_id": request.display_id,
            "request_binding_sha256": prov.get("request_binding_sha256"),
            "translation_options_sha256": prov.get("translation_options_sha256"),
            "status": "ADOPTED_PASS",
            "failure_class": None,
            "attempts": prov.get("attempt_count"),
            "latency_seconds": None,
            "usage": prov.get("usage") or {},
            "artifact_paths": {
                "request": str(required[0]),
                "raw": str(required[1]),
                "provenance": str(required[2]),
            },
            "adopted_from_legacy_arm": True,
            "recorded_at": _utc_now(),
        }
        _append_jsonl(progress_path, record, forbidden_substrings=forbidden_substrings)
        adopted += 1
    return adopted


def _run_reviewer(
    *,
    arm_out: Path,
    provider_requests: list[TranslationProviderRequest],
    raw_by_uid: dict[str, TranslationResult],
    statuses: list[CalibrationResponseStatus],
    reviewer: QwenMaxReviewerProvider,
) -> tuple[list[dict[str, Any]], list[TranslationCandidate]]:
    review_path = arm_out / "review_results.json"
    if review_path.is_file():
        existing = json.loads(review_path.read_text(encoding="utf-8"))
        return existing, []
    status_by_uid = {item.unit_uid: item for item in statuses}
    review_records: list[dict[str, Any]] = []
    reviewer_candidates: list[TranslationCandidate] = []
    for provider_request in provider_requests:
        raw = raw_by_uid.get(provider_request.request.unit_uid)
        status = status_by_uid.get(provider_request.request.unit_uid)
        if raw is None or status is None or status.status != "PASS":
            continue
        reviewed = reviewer.review_with_provenance(
            provider_request,
            raw,
            deterministic_qa_summary={"status": status.status, "diagnostics": status.diagnostics},
        )
        review_records.append(reviewed.review.model_dump())
        reviewer_candidates.append(reviewed.candidate)
    _write_json_once(review_path, review_records)
    if reviewer_candidates:
        persist_provider_provenance(arm_out / "reviewer_provider_provenance.jsonl", reviewer_candidates)
    return review_records, reviewer_candidates


def _write_side_by_side(arm_out: Path, requests: list[CalibrationRequest], raw_by_uid: dict[str, TranslationResult]) -> None:
    path = arm_out / "side_by_side.json"
    if path.is_file():
        return
    rows = []
    for request in requests:
        raw = raw_by_uid.get(request.unit_uid)
        if raw is None:
            continue
        rows.append(
            {
                "logical_work_order_id": request.display_id,
                "unit_uid": request.unit_uid,
                "source_text": request.source_text,
                "target_segments": [segment.text for segment in raw.target_segments],
            }
        )
    _write_json_once(path, rows)


def _finalize_arm(
    *,
    name: str,
    arm_out: Path,
    requests: list[CalibrationRequest],
    provider_requests: list[TranslationProviderRequest],
    source: Path,
    reviewer: QwenMaxReviewerProvider,
    importer_factory: Callable[[], Any],
    progress_path: Path,
    plan_sha256: str,
    source_sha256: str,
    forbidden_substrings: tuple[str, ...],
    latest: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    assembled = _assemble_arm_inputs(arm_out, requests)
    raw_by_uid: dict[str, TranslationResult] = {}
    statuses: list[CalibrationResponseStatus] = []
    harness_pass = 0
    harness_fail = 0
    if assembled is not None:
        passing_requests, passing_raw = assembled
        raw_by_uid = {item.unit_uid: item for item in passing_raw}
        request_path, raw_path = _write_derived_arm_jsonl(arm_out, passing_requests, passing_raw)
        candidate_path = arm_out / "candidate.docx"
        if candidate_path.is_file() and (arm_out / "review_results.json").is_file():
            statuses = [
                CalibrationResponseStatus(unit_uid=item.unit_uid, display_id=item.display_id, status="PASS", diagnostics=[])
                for item in passing_requests
            ]
        else:
            try:
                imported = importer_factory().run(source, request_path, raw_path, candidate_path)
                statuses = list(imported.response_statuses)
            except (KeyboardInterrupt, SystemExit, PilotGlobalError):
                raise
            except Exception:
                statuses = [
                    CalibrationResponseStatus(
                        unit_uid=item.unit_uid,
                        display_id=item.display_id,
                        status="FAIL",
                        diagnostics=["HARNESS_EXCEPTION"],
                    )
                    for item in passing_requests
                ]
        for status in statuses:
            if status.status == "PASS":
                harness_pass += 1
                record_status = "PASS"
                failure_class = None
            else:
                harness_fail += 1
                record_status = "HARNESS_FAIL"
                failure_class = (status.diagnostics[0].split(":", 1)[0] if status.diagnostics else "HARNESS_FAIL")
            previous = latest.get((name, status.unit_uid))
            if previous and previous.get("status") in {"PASS", "ADOPTED_PASS", "HARNESS_FAIL"}:
                continue
            provider_request = next(item for item in provider_requests if item.request.unit_uid == status.unit_uid)
            _append_jsonl(
                progress_path,
                {
                    "artifact_type": "QWEN_STRUCTURED_PILOT_PROGRESS",
                    "schema_version": 1,
                    "plan_sha256": plan_sha256,
                    "source_sha256": source_sha256,
                    "provider": None,
                    "model": None,
                    "arm": name,
                    "unit_uid": status.unit_uid,
                    "logical_work_order_id": status.display_id,
                    "request_binding_sha256": provider_request.request_binding_sha256,
                    "translation_options_sha256": provider_request.translation_options_sha256,
                    "status": record_status,
                    "failure_class": failure_class,
                    "attempts": previous.get("attempts") if previous else None,
                    "latency_seconds": previous.get("latency_seconds") if previous else None,
                    "usage": previous.get("usage") if previous else {},
                    "artifact_paths": previous.get("artifact_paths") if previous else {},
                    "recorded_at": _utc_now(),
                },
                forbidden_substrings=forbidden_substrings,
            )
            latest[(name, status.unit_uid)] = {**dict(previous or {}), "status": record_status, "failure_class": failure_class}
    try:
        review_records, reviewer_candidates = _run_reviewer(
            arm_out=arm_out,
            provider_requests=provider_requests,
            raw_by_uid=raw_by_uid,
            statuses=statuses,
            reviewer=reviewer,
        )
    except (ProviderContractGap, ProviderRuntimeError, Exception) as exc:
        if isinstance(exc, (PilotGlobalError, KeyboardInterrupt, SystemExit)):
            raise
        review_records, reviewer_candidates = [], []
        _write_json_derived(
            arm_out / "reviewer_arm_failure.json",
            {"failure_class": _classify_failure(exc)[1], "status": "REVIEWER_ARM_FAILURE"},
        )
    _write_side_by_side(arm_out, requests, raw_by_uid)
    return {
        "harness_pass": harness_pass,
        "harness_fail": harness_fail,
        "review_records": review_records,
        "reviewer_candidates": reviewer_candidates,
        "statuses": statuses,
    }


def _run_arm(
    *,
    name: str,
    requests: list[CalibrationRequest],
    qwen_mt: QwenMTProvider,
    qwen_max: QwenMaxProvider,
    reviewer: QwenMaxReviewerProvider,
    translation_bible: Path,
    source: Path,
    out: Path,
    plan_sha256: str,
    source_sha256: str,
    progress_path: Path,
    latest: dict[tuple[str, str], dict[str, Any]],
    forbidden_substrings: tuple[str, ...],
    importer_factory: Callable[[], Any],
    input_price_per_million: float | None,
    output_price_per_million: float | None,
) -> dict[str, Any]:
    arm_out = out / name
    arm_out.mkdir(exist_ok=True)
    provider_requests = [relevant_aids(TranslationProviderRequest(request=request), translation_bible=translation_bible) for request in requests]
    for index, (request, provider_request) in enumerate(zip(requests, provider_requests, strict=True), start=1):
        existing = latest.get((name, request.unit_uid))
        unit_dir = _unit_dir(arm_out, request.unit_uid)
        if (
            unit_is_terminal(existing)
            or (existing and existing.get("status") == "CONTRACT_PASS")
            or request.unit_uid in _legacy_arm_unit_uids(arm_out)
            or (unit_dir / "translation_raw_response.jsonl").is_file()
            or (unit_dir / "contract_gap_evidence.json").is_file()
        ):
            _emit(_progress_line(name, index, len(requests), "SKIP_RESUME", request.display_id))
            continue
        started = datetime.now().astimezone()
        result = _execute_unit_arm(name=name, provider_request=provider_request, qwen_mt=qwen_mt, qwen_max=qwen_max)
        if result.status == "CONTRACT_PASS" and result.raw is not None:
            result.artifact_paths = _persist_unit_success(arm_out, request, result.candidates, result.raw)
        else:
            result.artifact_paths = _persist_unit_failure(arm_out, request, result)
        latency = _latency_seconds(result.candidates[-1]) if result.candidates else (datetime.now().astimezone() - started).total_seconds()
        record = {
            "artifact_type": "QWEN_STRUCTURED_PILOT_PROGRESS",
            "schema_version": 1,
            "plan_sha256": plan_sha256,
            "source_sha256": source_sha256,
            "provider": result.candidates[-1].provider if result.candidates else None,
            "model": result.candidates[-1].model if result.candidates else None,
            "arm": name,
            "unit_uid": request.unit_uid,
            "logical_work_order_id": request.display_id,
            "request_binding_sha256": provider_request.request_binding_sha256,
            "translation_options_sha256": provider_request.translation_options_sha256,
            "status": result.status,
            "failure_class": result.failure_class,
            "attempts": sum(candidate.attempt_count for candidate in result.candidates) if result.candidates else 1,
            "latency_seconds": latency,
            "usage": dict(result.candidates[-1].usage) if result.candidates else {},
            "artifact_paths": result.artifact_paths,
            "segment_index": result.details.get("segment_index"),
            "segment_mode": result.details.get("segment_mode"),
            "recorded_at": _utc_now(),
        }
        _append_jsonl(progress_path, record, forbidden_substrings=forbidden_substrings)
        latest[(name, request.unit_uid)] = record
        display_status = "PASS" if result.status == "CONTRACT_PASS" else result.status
        _emit(_progress_line(name, index, len(requests), display_status, request.display_id, result.failure_class))
    finalized = _finalize_arm(
        name=name,
        arm_out=arm_out,
        requests=requests,
        provider_requests=provider_requests,
        source=source,
        reviewer=reviewer,
        importer_factory=importer_factory,
        progress_path=progress_path,
        plan_sha256=plan_sha256,
        source_sha256=source_sha256,
        forbidden_substrings=forbidden_substrings,
        latest=latest,
    )
    latest = latest_progress_by_unit(load_progress(progress_path))
    unit_records = [latest[(name, request.unit_uid)] for request in requests if (name, request.unit_uid) in latest]
    candidates = []
    for request in requests:
        provenance = _unit_dir(arm_out, request.unit_uid) / "translation_provider_provenance.jsonl"
        if provenance.is_file():
            for line in provenance.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    payload = json.loads(line)
                    candidates.append(
                        TranslationCandidate(
                            provider=payload.get("provider") or "UNKNOWN",
                            model=payload.get("model") or "UNKNOWN",
                            request_binding_sha256=payload.get("request_binding_sha256") or "",
                            translation_options_sha256=payload.get("translation_options_sha256") or "",
                            source_sha256=payload.get("source_sha256") or source_sha256,
                            unit_uid=payload.get("unit_uid") or request.unit_uid,
                            display_id=payload.get("display_id") or request.display_id,
                            response_text=payload.get("response_text") or "",
                            requested_at=payload.get("requested_at") or _utc_now(),
                            responded_at=payload.get("responded_at") or _utc_now(),
                            response_id=payload.get("response_id"),
                            usage=payload.get("usage") or {},
                            attempt_count=int(payload.get("attempt_count") or 1),
                        )
                    )
    provider_usage = [dict(record.get("usage") or {}) for record in unit_records]
    review_records = finalized["review_records"]
    reviewer_candidates = finalized["reviewer_candidates"]
    failure_classes = Counter(record.get("failure_class") for record in unit_records if record.get("failure_class"))
    contract_pass = sum(record.get("status") in {"CONTRACT_PASS", "PASS", "ADOPTED_PASS", "HARNESS_FAIL"} for record in unit_records)
    contract_fail = sum(record.get("status") in {"CONTRACT_GAP", "STRUCTURED_VALIDATION"} for record in unit_records)
    return {
        "arm": name,
        "attempted": len(requests),
        "unit_count": len(requests),
        "contract_pass_count": contract_pass,
        "contract_fail_count": contract_fail,
        "deterministic_anchor_pass_count": finalized["harness_pass"],
        "deterministic_anchor_fail_count": finalized["harness_fail"],
        "failure_class_distribution": dict(failure_classes),
        "failure_types": dict(failure_classes),
        "provider_attempt_count": sum(int(record.get("attempts") or 0) for record in unit_records),
        "usage": provider_usage,
        "latency": _latency_summary(candidates),
        "estimated_cost": _estimated_cost(provider_usage, input_price_per_million=input_price_per_million, output_price_per_million=output_price_per_million),
        "reviewer": {
            "reviewed_count": len(review_records),
            "accept_count": sum(record.get("verdict") == "ACCEPT" for record in review_records),
            "repair_count": sum(record.get("verdict") == "REPAIR" for record in review_records),
            "human_required_count": sum(record.get("verdict") == "HUMAN_REQUIRED" for record in review_records),
            "provider_attempt_count": sum(candidate.attempt_count for candidate in reviewer_candidates),
            "usage": [dict(candidate.usage) for candidate in reviewer_candidates],
            "latency": _latency_summary(reviewer_candidates),
            "estimated_cost": _estimated_cost([dict(candidate.usage) for candidate in reviewer_candidates], input_price_per_million=input_price_per_million, output_price_per_million=output_price_per_million),
        },
        "unit_results": [
            {
                "logical_work_order_id": record.get("logical_work_order_id"),
                "unit_uid": record.get("unit_uid"),
                "status": record.get("status"),
                "failure_class": record.get("failure_class"),
                "attempts": record.get("attempts"),
                "latency_seconds": record.get("latency_seconds"),
            }
            for record in unit_records
        ],
    }


def run_execute(
    *,
    plan: dict[str, Any],
    plan_path: Path,
    source: Path,
    translation_bible: Path,
    out: Path,
    resume: bool,
    requests: list[CalibrationRequest],
    qwen_mt: QwenMTProvider | None = None,
    qwen_max: QwenMaxProvider | None = None,
    reviewer: QwenMaxReviewerProvider | None = None,
    importer_factory: Callable[[], Any] | None = None,
    input_price_per_million: float | None = None,
    output_price_per_million: float | None = None,
    api_key_for_redaction: str = "",
) -> dict[str, Any]:
    source_sha256 = fingerprint(source).sha256
    if source_sha256 != plan.get("source_sha256"):
        raise PilotGlobalError("QWEN_STRUCTURED_PILOT_SOURCE_SHA_MISMATCH")
    plan_sha256 = _sha256_path(plan_path)
    bible_sha256 = _sha256_path(translation_bible)
    expected_identity = identity_payload(plan_sha256=plan_sha256, source_sha256=source_sha256, translation_bible_sha256=bible_sha256)
    if resume:
        if not out.exists():
            raise PilotGlobalError("QWEN_STRUCTURED_PILOT_RESUME_DIRECTORY_MISSING")
        identity_path = out / IDENTITY_NAME
        if identity_path.is_file():
            actual = json.loads(identity_path.read_text(encoding="utf-8"))
            if actual != expected_identity:
                raise PilotGlobalError("QWEN_STRUCTURED_PILOT_RESUME_IDENTITY_MISMATCH")
        else:
            _write_json_once(identity_path, expected_identity)
    else:
        if out.exists():
            raise PilotGlobalError("QWEN_STRUCTURED_PILOT_OUTPUT_MUST_BE_NEW")
        if qwen_mt is None:
            qwen_mt = QwenMTProvider.from_environment()
        if qwen_max is None:
            qwen_max = QwenMaxProvider.from_environment()
        if reviewer is None:
            reviewer = QwenMaxReviewerProvider.from_environment()
        out.mkdir(parents=True)
        _write_json_once(out / IDENTITY_NAME, expected_identity)
    if qwen_mt is None:
        qwen_mt = QwenMTProvider.from_environment()
    if qwen_max is None:
        qwen_max = QwenMaxProvider.from_environment()
    if reviewer is None:
        reviewer = QwenMaxReviewerProvider.from_environment()
    if importer_factory is None:
        importer_factory = CalibrationImporter
    forbidden = tuple(fragment for fragment in (api_key_for_redaction, qwen_mt.api_key, qwen_max.api_key, reviewer.api_key) if fragment)
    progress_path = out / PROGRESS_NAME
    latest = latest_progress_by_unit(load_progress(progress_path))
    simple = [request for request, entry in zip(requests, plan["requests"], strict=True) if "simple_single_segment" in entry["categories"]]
    multi = [request for request, entry in zip(requests, plan["requests"], strict=True) if "multi_segment" in entry["categories"]]
    if resume and simple and not any(arm == SIMPLE_ARM for arm, _ in latest):
        _adopt_simple_arm_if_complete(
            out=out,
            requests=simple,
            plan_sha256=plan_sha256,
            source_sha256=source_sha256,
            translation_bible=translation_bible,
            progress_path=progress_path,
            forbidden_substrings=forbidden,
        )
        latest = latest_progress_by_unit(load_progress(progress_path))
    results = [
        _run_arm(
            name=name,
            requests=arm_requests,
            qwen_mt=qwen_mt,
            qwen_max=qwen_max,
            reviewer=reviewer,
            translation_bible=translation_bible,
            source=source,
            out=out,
            plan_sha256=plan_sha256,
            source_sha256=source_sha256,
            progress_path=progress_path,
            latest=latest,
            forbidden_substrings=forbidden,
            importer_factory=importer_factory,
            input_price_per_million=input_price_per_million,
            output_price_per_million=output_price_per_million,
        )
        for name, arm_requests in ((SIMPLE_ARM, simple), (ARM_A, multi), (ARM_B, multi))
        if arm_requests
    ]
    comparison = {
        "status": "COMPLETED",
        "plan_sha256": plan_sha256,
        "source_sha256": source_sha256,
        "identity": expected_identity,
        "arms": results,
    }
    _write_json_derived(out / REPORT_NAME, comparison)
    _emit(json.dumps({"status": "COMPLETED", "out": str(out), "arms": [{"arm": item["arm"], "attempted": item["attempted"], "contract_pass_count": item["contract_pass_count"], "contract_fail_count": item["contract_fail_count"], "failure_class_distribution": item["failure_class_distribution"]} for item in results]}, ensure_ascii=False))
    return comparison


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--translation-bible", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--input-price-per-million", type=float, help="Optional USD rate for usage-based estimate; never stored as a secret.")
    parser.add_argument("--output-price-per-million", type=float, help="Optional USD rate for usage-based estimate; never stored as a secret.")
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if plan.get("artifact_type") != "QWEN_STRUCTURED_PROVIDER_PILOT_PLAN" or plan.get("coverage_gaps"):
            raise PilotGlobalError("QWEN_STRUCTURED_PILOT_PLAN_NOT_APPROVED")
        requests = _load_plan_requests(plan)
        if fingerprint(args.source).sha256 != plan.get("source_sha256"):
            raise PilotGlobalError("QWEN_STRUCTURED_PILOT_SOURCE_SHA_MISMATCH")
        simple = [request for request, entry in zip(requests, plan["requests"], strict=True) if "simple_single_segment" in entry["categories"]]
        multi = [request for request, entry in zip(requests, plan["requests"], strict=True) if "multi_segment" in entry["categories"]]
        summary = {"status": "READY", "simple_count": len(simple), "multi_count": len(multi), "arms": [SIMPLE_ARM, ARM_A, ARM_B]}
        if not args.execute:
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
            return 0
        if args.out is None:
            raise PilotGlobalError("QWEN_STRUCTURED_PILOT_OUTPUT_MUST_BE_NEW")
        run_execute(
            plan=plan,
            plan_path=args.plan,
            source=args.source,
            translation_bible=args.translation_bible,
            out=args.out,
            resume=args.resume,
            requests=requests,
            input_price_per_million=args.input_price_per_million,
            output_price_per_million=args.output_price_per_million,
        )
        return 0
    except ProviderConfigurationError as exc:
        raise PilotGlobalError(str(exc)) from exc
    except PilotGlobalError:
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PilotGlobalError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2) from exc
