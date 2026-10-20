"""Per-unit create-once / append-only canary persistence."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.production_worker.artifacts import assert_record_safe, write_json_once, write_jsonl_once, write_jsonl_once_or_validate


REQUEST_SUPERSESSION_DIR = "translation_request_supersessions"
REQUEST_SUPERSESSION_REASON = "HARNESS_STRUCTURAL_ISLAND_MAPPING_CORRECTION"
PROVIDER_REACQUISITION_DIR = "provider_reacquisitions"
PROVIDER_REACQUISITION_REASON = "REQUEST_SUPERSESSION_REACQUISITION"


class RequestSupersessionError(RuntimeError):
    """A pre-seal request lineage is not safe to use as execution input."""


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _physical_request_identity(row: CalibrationRequest) -> dict[str, Any]:
    """Physical source facts represented by a request, never editable here."""
    value = row.model_dump()
    # These are a deterministic Harness projection of physical nodes, rather
    # than physical source facts themselves.  They may be regenerated when a
    # corrected island map splits a text run around ``w:tab``.
    value.pop("source_segments", None)
    value.pop("available_anchors", None)
    return value


def _same_physical_segment_text_and_order(parent: CalibrationRequest, child: CalibrationRequest) -> bool:
    """Permit topology changes, never a change to visible source content/order."""
    parent_text = "".join(segment.text for segment in parent.source_segments)
    child_text = "".join(segment.text for segment in child.source_segments)
    return parent_text == child_text == parent.source_text == child.source_text


def _same_physical_request(parent: CalibrationRequest, child: CalibrationRequest) -> bool:
    return _physical_request_identity(parent) == _physical_request_identity(child) and _same_physical_segment_text_and_order(parent, child)


def _read_request_rows(path: Path) -> list[CalibrationRequest]:
    if not path.is_file():
        raise RequestSupersessionError(f"REQUEST_SUPERSESSION_REQUEST_MISSING:{path}")
    return load_jsonl_models(path, CalibrationRequest)


def _checkpoint_is_sealed(checkpoint_dir: Path) -> bool:
    return (checkpoint_dir / "checkpoint_evidence.json").is_file()


def _sealed_legacy_baseline_acquisitions(
    *, checkpoint_dir: Path, requests: Sequence[CalibrationRequest], source_sha256: str,
    excluded_uids: set[str],
) -> dict[str, TranslationResult]:
    """Read a pre-provenance RAW baseline only from a complete sealed lineage.

    This is deliberately not a general RAW fallback.  It is available only to
    old sealed checkpoints whose immutable checkpoint evidence binds the whole
    RAW packet, whose original request is still effective, and whose final-QA
    evidence names exactly one RAW row for the requested unit.
    """
    if not _checkpoint_is_sealed(checkpoint_dir):
        return {}
    if _supersession_records(checkpoint_dir):
        return {}
    raw_path = checkpoint_dir / "translation_raw_response.jsonl"
    final_path = checkpoint_dir / "translation_final_qa_report.json"
    try:
        seal = json.loads((checkpoint_dir / "checkpoint_evidence.json").read_text(encoding="utf-8"))
        final = json.loads(final_path.read_text(encoding="utf-8"))
        rows = [TranslationResult.model_validate(json.loads(line)) for line in raw_path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RequestSupersessionError("LEGACY_SEALED_BASELINE_EVIDENCE_INVALID") from exc
    if (
        seal.get("source_sha256") != source_sha256
        or seal.get("raw_sha256") != _sha256_file(raw_path)
        or not isinstance(seal.get("logical_work_orders"), list)
        or not isinstance(final.get("records"), list)
    ):
        raise RequestSupersessionError("LEGACY_SEALED_BASELINE_EVIDENCE_INVALID")
    by_uid: dict[str, list[TranslationResult]] = {}
    for row in rows:
        by_uid.setdefault(row.unit_uid, []).append(row)
    final_by_uid: dict[str, list[Mapping[str, Any]]] = {}
    for record in final["records"]:
        if isinstance(record, Mapping) and isinstance(record.get("unit_uid"), str):
            final_by_uid.setdefault(record["unit_uid"], []).append(record)
    resolved: dict[str, TranslationResult] = {}
    for request in requests:
        if request.unit_uid in excluded_uids:
            continue
        human_path = checkpoint_dir / "human_approved_candidates" / f"{request.display_id}.json"
        if human_path.is_file():
            try:
                human = json.loads(human_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RequestSupersessionError("LEGACY_SEALED_BASELINE_EVIDENCE_INVALID") from exc
            if (
                human.get("origin") == "HUMAN_INITIAL_CANDIDATE"
                and human.get("current_display_id") == request.display_id
                and human.get("unit_uid") == request.unit_uid
            ):
                continue
        candidates = by_uid.get(request.unit_uid, [])
        final_records = final_by_uid.get(request.unit_uid, [])
        if (
            request.display_id not in seal["logical_work_orders"]
            or len(candidates) != 1
            or len(final_records) != 1
            or final_records[0].get("display_id") != request.display_id
        ):
            raise RequestSupersessionError("LEGACY_SEALED_BASELINE_UNPROVEN")
        resolved[request.unit_uid] = candidates[0]
    return resolved


def _supersession_records(checkpoint_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    folder = checkpoint_dir / REQUEST_SUPERSESSION_DIR
    if not folder.is_dir():
        return []
    found: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(folder.glob("*.json"), key=lambda item: item.name):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestSupersessionError(f"REQUEST_SUPERSESSION_RECORD_INVALID:{path}") from exc
        if not isinstance(record, dict):
            raise RequestSupersessionError(f"REQUEST_SUPERSESSION_RECORD_INVALID:{path}")
        found.append((path, record))
    return found


def _validate_context(record: Mapping[str, Any], *, source_sha256: str, batch_id: str,
                      checkpoint_id: str, selection_sha256: str, manifest_sha256: str) -> None:
    expected = {"source_sha256": source_sha256, "batch_id": batch_id,
                "checkpoint_id": checkpoint_id, "selection_sha256": selection_sha256,
                "manifest_sha256": manifest_sha256}
    if any(record.get(key) != value for key, value in expected.items()):
        raise RequestSupersessionError("REQUEST_SUPERSESSION_STALE_BINDING")
    if record.get("artifact_type") != "PRE_SEAL_DERIVED_REQUEST_SUPERSESSION":
        raise RequestSupersessionError("REQUEST_SUPERSESSION_TYPE_INVALID")
    if record.get("reason_code") != REQUEST_SUPERSESSION_REASON:
        raise RequestSupersessionError("REQUEST_SUPERSESSION_REASON_INVALID")


def resolve_effective_translation_request(*, checkpoint_dir: Path, source_sha256: str,
                                          batch_id: str, checkpoint_id: str,
                                          selection_sha256: str, manifest_sha256: str,
                                          expected_order: Sequence[str]) -> tuple[Path, list[CalibrationRequest]]:
    """Resolve the one immutable pre-seal request chain, or fail closed.

    This is deliberately local to request artifacts: it is not candidate or
    source authority and it has no mutable ``current`` marker.
    """
    if _checkpoint_is_sealed(checkpoint_dir):
        if _supersession_records(checkpoint_dir):
            raise RequestSupersessionError("REQUEST_SUPERSESSION_AFTER_SEAL")
    original = checkpoint_dir / "translation_request.jsonl"
    original_rows = _read_request_rows(original)
    if [row.display_id for row in original_rows] != list(expected_order):
        raise RequestSupersessionError("REQUEST_SUPERSESSION_ORIGINAL_MEMBERSHIP_MISMATCH")
    records = _supersession_records(checkpoint_dir)
    if not records:
        return original, original_rows
    if _checkpoint_is_sealed(checkpoint_dir):
        raise RequestSupersessionError("REQUEST_SUPERSESSION_AFTER_SEAL")
    known: dict[str, tuple[Path, list[CalibrationRequest]]] = {_sha256_file(original): (original, original_rows)}
    children: dict[str, tuple[str, Path, list[CalibrationRequest]]] = {}
    for evidence_path, record in records:
        _validate_context(record, source_sha256=source_sha256, batch_id=batch_id,
                          checkpoint_id=checkpoint_id, selection_sha256=selection_sha256,
                          manifest_sha256=manifest_sha256)
        parent_sha, child_sha = record.get("parent_request_sha256"), record.get("child_request_sha256")
        child_name = record.get("child_request_artifact")
        if not all(isinstance(item, str) and len(item) == 64 for item in (parent_sha, child_sha)) or not isinstance(child_name, str):
            raise RequestSupersessionError("REQUEST_SUPERSESSION_BINDING_INVALID")
        child_path = checkpoint_dir / child_name
        rows = _read_request_rows(child_path)
        if _sha256_file(child_path) != child_sha:
            raise RequestSupersessionError("REQUEST_SUPERSESSION_CHILD_SHA_MISMATCH")
        if [row.display_id for row in rows] != list(expected_order):
            raise RequestSupersessionError("REQUEST_SUPERSESSION_MEMBERSHIP_MISMATCH")
        if parent_sha in children:
            raise RequestSupersessionError("REQUEST_SUPERSESSION_FORK")
        children[parent_sha] = (child_sha, child_path, rows)
    current_sha = _sha256_file(original)
    current_rows = original_rows
    seen: set[str] = set()
    while current_sha in children:
        if current_sha in seen:
            raise RequestSupersessionError("REQUEST_SUPERSESSION_CYCLE")
        seen.add(current_sha)
        child_sha, child_path, child_rows = children[current_sha]
        if len(current_rows) != len(child_rows) or any(not _same_physical_request(left, right) for left, right in zip(current_rows, child_rows)):
            raise RequestSupersessionError("REQUEST_SUPERSESSION_SOURCE_IDENTITY_CHANGED")
        current_sha, current_rows, current_path = child_sha, child_rows, child_path
    # Every submitted record must be a reachable member of this one lineage.
    if len(seen) != len(records):
        raise RequestSupersessionError("REQUEST_SUPERSESSION_MISSING_PARENT_OR_CYCLE")
    return current_path if seen else original, current_rows


def create_preseal_request_supersession(*, checkpoint_dir: Path, parent_path: Path,
                                        corrected_requests: Sequence[CalibrationRequest], source_sha256: str,
                                        batch_id: str, checkpoint_id: str, selection_sha256: str,
                                        manifest_sha256: str, affected_unit_uids: Sequence[str],
                                        code_evidence: Mapping[str, Any] | None = None) -> Path:
    """Append one Harness-proven corrected request snapshot and its binding."""
    if _checkpoint_is_sealed(checkpoint_dir):
        raise RequestSupersessionError("REQUEST_SUPERSESSION_AFTER_SEAL")
    parent_rows = _read_request_rows(parent_path)
    child_rows = list(corrected_requests)
    if [row.display_id for row in parent_rows] != [row.display_id for row in child_rows]:
        raise RequestSupersessionError("REQUEST_SUPERSESSION_MEMBERSHIP_MISMATCH")
    if any(not _same_physical_request(left, right) for left, right in zip(parent_rows, child_rows)):
        raise RequestSupersessionError("REQUEST_SUPERSESSION_SOURCE_IDENTITY_CHANGED")
    actual_affected = [row.unit_uid for left, row in zip(parent_rows, child_rows) if left.model_dump() != row.model_dump()]
    if sorted(actual_affected) != sorted(affected_unit_uids) or not actual_affected:
        raise RequestSupersessionError("REQUEST_SUPERSESSION_AFFECTED_UNITS_MISMATCH")
    payload = "".join(f"{row.model_dump_json()}\n" for row in child_rows).encode("utf-8")
    child_sha = sha256(payload).hexdigest()
    child_name = f"translation_request.derived.{child_sha}.jsonl"
    child_path = checkpoint_dir / child_name
    write_jsonl_once_or_validate(child_path, child_rows)
    evidence = {"artifact_type": "PRE_SEAL_DERIVED_REQUEST_SUPERSESSION", "schema_version": 1,
                "parent_request_sha256": _sha256_file(parent_path), "child_request_sha256": child_sha,
                "child_request_artifact": child_name, "source_sha256": source_sha256,
                "batch_id": batch_id, "checkpoint_id": checkpoint_id,
                "selection_sha256": selection_sha256, "manifest_sha256": manifest_sha256,
                "reason_code": REQUEST_SUPERSESSION_REASON,
                "affected_unit_uids": sorted(affected_unit_uids), "code_evidence": dict(code_evidence or {})}
    evidence_path = checkpoint_dir / REQUEST_SUPERSESSION_DIR / f"{child_sha}.json"
    if evidence_path.exists():
        if json.loads(evidence_path.read_text(encoding="utf-8")) != evidence:
            raise RequestSupersessionError("REQUEST_SUPERSESSION_EVIDENCE_CONFLICT")
    else:
        write_json_once(evidence_path, evidence)
    return child_path


def unit_dir(output_dir: Path) -> Path:
    return output_dir / "units"


def unit_path(output_dir: Path, display_id: str) -> Path:
    return unit_dir(output_dir) / f"{display_id}.json"


def persist_unit_result(path: Path, record: Mapping[str, Any]) -> None:
    """Create-once unit evidence with fsync.  Never overwrites a prior unit file."""
    write_json_once(path, record)


def load_unit_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"UNIT_RESULT_NOT_OBJECT:{path}")
    return payload


def load_unit_results(output_dir: Path) -> dict[str, dict[str, Any]]:
    directory = unit_dir(output_dir)
    if not directory.is_dir():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        record = load_unit_result(path)
        if record is None:
            continue
        display_id = str(record.get("display_id") or path.stem)
        found[display_id] = record
    return found


def append_jsonl_fsync(path: Path, record: Mapping[str, Any]) -> None:
    assert_record_safe(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_jsonl_models(path: Path, model):
    if not path.is_file():
        return []
    return [model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_raw_by_uid(path: Path) -> dict[str, TranslationResult]:
    rows = load_jsonl_models(path, TranslationResult)
    found: dict[str, TranslationResult] = {}
    for row in rows:
        if row.unit_uid in found:
            raise ValueError(f"DUPLICATE_RAW_UNIT:{row.unit_uid}")
        found[row.unit_uid] = row
    return found


def _provenance_by_uid(path: Path, *, sealed_duplicate_uids: set[str] | None = None) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        uid = record.get("unit_uid") if isinstance(record, Mapping) else None
        if not isinstance(uid, str):
            raise RequestSupersessionError("PROVIDER_ACQUISITION_BASE_PROVENANCE_INVALID")
        if uid in found:
            # Schema-v1 has no candidate binding.  A duplicate can therefore
            # never select a response by file order; only a separately proven
            # sealed baseline permits collapsing identical historical facts.
            first = found[uid]
            identity = ("schema_version", "unit_uid", "display_id", "source_sha256", "request_binding_sha256", "provider", "model", "route", "config_profile", "translation_options_sha256")
            if (
                uid not in (sealed_duplicate_uids or set())
                or any(record.get(key) != first.get(key) for key in identity)
            ):
                raise RequestSupersessionError("PROVIDER_ACQUISITION_BASE_PROVENANCE_INVALID")
            continue
        found[uid] = record
    return found


def _validate_sealed_reacquisition_projection(*, checkpoint_dir: Path,
                                               records: Sequence[tuple[Path, Mapping[str, Any]]],
                                               requests: Sequence[CalibrationRequest], source_sha256: str,
                                               batch_id: str, checkpoint_id: str, selection_sha256: str,
                                               manifest_sha256: str,
                                               effective_request_artifact_sha256: str) -> None:
    """Prove that sealed reacquisitions are an immutable execution projection.

    A directory's presence is not proof of a post-seal mutation.  This reader
    accepts it only when the complete derived-request lineage, its affected
    rows, and every reacquisition are bound to the exact sealed execution
    context.  Filesystem time is a supplementary last check for a genuinely
    new post-seal write; it is never the sole admission criterion.
    """
    seal = checkpoint_dir / "checkpoint_evidence.json"
    if not seal.is_file():
        raise RequestSupersessionError("PROVIDER_REACQUISITION_SEAL_MISSING")
    supersessions = _supersession_records(checkpoint_dir)
    if not supersessions:
        raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
    original = checkpoint_dir / "translation_request.jsonl"
    original_rows = _read_request_rows(original)
    expected_order = [request.display_id for request in requests]
    if [row.display_id for row in original_rows] != expected_order:
        raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_MEMBERSHIP_INVALID")
    children: dict[str, tuple[Path, list[CalibrationRequest], Mapping[str, Any]]] = {}
    for evidence_path, record in supersessions:
        _validate_context(record, source_sha256=source_sha256, batch_id=batch_id,
                          checkpoint_id=checkpoint_id, selection_sha256=selection_sha256,
                          manifest_sha256=manifest_sha256)
        parent_sha, child_sha, child_name = (
            record.get("parent_request_sha256"), record.get("child_request_sha256"),
            record.get("child_request_artifact"),
        )
        if not all(isinstance(value, str) and len(value) == 64 for value in (parent_sha, child_sha)) or not isinstance(child_name, str):
            raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
        child_path = checkpoint_dir / child_name
        child_rows = _read_request_rows(child_path)
        if (
            _sha256_file(child_path) != child_sha
            or [row.display_id for row in child_rows] != expected_order
            or parent_sha in children
        ):
            raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
        if evidence_path.stat().st_mtime_ns > seal.stat().st_mtime_ns or child_path.stat().st_mtime_ns > seal.stat().st_mtime_ns:
            raise RequestSupersessionError("PROVIDER_REACQUISITION_AFTER_SEAL")
        children[parent_sha] = (child_path, child_rows, record)
    current_path, current_rows = original, original_rows
    seen: set[str] = set()
    affected: set[str] = set()
    while (parent_sha := _sha256_file(current_path)) in children:
        if parent_sha in seen:
            raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
        seen.add(parent_sha)
        child_path, child_rows, record = children[parent_sha]
        if len(current_rows) != len(child_rows) or any(
            not _same_physical_request(left, right) for left, right in zip(current_rows, child_rows, strict=True)
        ):
            raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
        affected.update(uid for uid in record.get("affected_unit_uids") or [] if isinstance(uid, str))
        current_path, current_rows = child_path, child_rows
    if len(seen) != len(supersessions) or _sha256_file(current_path) != effective_request_artifact_sha256:
        raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
    reacquired: set[str] = set()
    for path, record in records:
        if path.stat().st_mtime_ns > seal.stat().st_mtime_ns:
            raise RequestSupersessionError("PROVIDER_REACQUISITION_AFTER_SEAL")
        uid = record.get("unit_uid")
        provenance = record.get("provider_provenance")
        if (
            not isinstance(uid, str)
            or uid not in affected
            or record.get("effective_request_artifact_sha256") != effective_request_artifact_sha256
            or not isinstance(provenance, Mapping)
            or provenance.get("unit_uid") != uid
            or provenance.get("request_binding_sha256") != record.get("effective_request_row_fingerprint")
            or provenance.get("source_sha256") != source_sha256
        ):
            raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")
        reacquired.add(uid)
    if not affected <= reacquired:
        raise RequestSupersessionError("PROVIDER_REACQUISITION_SEALED_BINDING_INVALID")


def resolve_effective_provider_acquisition(*, checkpoint_dir: Path, requests: Sequence[CalibrationRequest],
                                           request_binding_by_uid: Mapping[str, str], source_sha256: str,
                                           batch_id: str, checkpoint_id: str, selection_sha256: str,
                                           manifest_sha256: str, effective_request_artifact_sha256: str) -> tuple[dict[str, TranslationResult], set[str]]:
    """Return only acquisitions exactly bound to the current effective request."""
    base_raw = load_raw_by_uid(checkpoint_dir / "translation_raw_response.jsonl")
    # Establish the sealed baseline before parsing schema-v1 provenance so a
    # duplicate record is admitted only when it cannot choose or alter RAW.
    sealed_baselines = _sealed_legacy_baseline_acquisitions(
        checkpoint_dir=checkpoint_dir,
        requests=requests,
        source_sha256=source_sha256,
        excluded_uids=set(),
    )
    base_provenance = _provenance_by_uid(
        checkpoint_dir / "translation_provider_provenance.jsonl",
        sealed_duplicate_uids=set(sealed_baselines),
    )
    effective: dict[str, TranslationResult] = {}
    historical: set[str] = set()
    for request in requests:
        expected = request_binding_by_uid.get(request.unit_uid)
        raw, provenance = base_raw.get(request.unit_uid), base_provenance.get(request.unit_uid)
        if raw is not None and provenance is not None and provenance.get("request_binding_sha256") == expected:
            effective[request.unit_uid] = raw
        elif raw is not None:
            historical.add(request.unit_uid)
    folder = checkpoint_dir / PROVIDER_REACQUISITION_DIR
    if folder.is_dir():
        records: list[tuple[Path, Mapping[str, Any]]] = []
        for path in sorted(folder.glob("*.json"), key=lambda item: item.name):
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("artifact_type") != "REQUEST_BOUND_PROVIDER_REACQUISITION":
                raise RequestSupersessionError("PROVIDER_REACQUISITION_RECORD_INVALID")
            records.append((path, record))
        if _checkpoint_is_sealed(checkpoint_dir):
            seal = checkpoint_dir / "checkpoint_evidence.json"
            # A file demonstrably created after the seal is always a true
            # mutation, even if it is otherwise malformed or unbound.
            if any(path.stat().st_mtime_ns > seal.stat().st_mtime_ns for path, _record in records):
                raise RequestSupersessionError("PROVIDER_REACQUISITION_AFTER_SEAL")
            _validate_sealed_reacquisition_projection(
                checkpoint_dir=checkpoint_dir, records=records, requests=requests,
                source_sha256=source_sha256, batch_id=batch_id, checkpoint_id=checkpoint_id,
                selection_sha256=selection_sha256, manifest_sha256=manifest_sha256,
                effective_request_artifact_sha256=effective_request_artifact_sha256,
            )
        for path, record in records:
            expected_context = {"source_sha256": source_sha256, "batch_id": batch_id, "checkpoint_id": checkpoint_id,
                                "selection_sha256": selection_sha256, "manifest_sha256": manifest_sha256,
                                "effective_request_artifact_sha256": effective_request_artifact_sha256,
                                "reason_code": PROVIDER_REACQUISITION_REASON}
            if any(record.get(key) != value for key, value in expected_context.items()):
                raise RequestSupersessionError("PROVIDER_REACQUISITION_STALE_BINDING")
            uid = record.get("unit_uid"); binding = record.get("effective_request_row_fingerprint")
            if not isinstance(uid, str) or request_binding_by_uid.get(uid) != binding:
                raise RequestSupersessionError("PROVIDER_REACQUISITION_REQUEST_BINDING_INVALID")
            raw = TranslationResult.model_validate(record.get("raw_response"))
            if raw.unit_uid != uid or sha256(raw.model_dump_json().encode("utf-8")).hexdigest() != record.get("raw_response_sha256"):
                raise RequestSupersessionError("PROVIDER_REACQUISITION_RAW_INVALID")
            if uid in effective:
                raise RequestSupersessionError("PROVIDER_REACQUISITION_FORK")
            effective[uid] = raw
    # Pre-provenance checkpoint generations have no per-call provider record.
    # They may be consumed only through the sealed packet proof above; modern
    # provenance and any request-bound reacquisition always retain precedence.
    legacy = _sealed_legacy_baseline_acquisitions(
        checkpoint_dir=checkpoint_dir,
        requests=requests,
        source_sha256=source_sha256,
        excluded_uids=set(effective),
    )
    for uid, raw in legacy.items():
        if uid in effective:
            raise RequestSupersessionError("LEGACY_SEALED_BASELINE_FORK")
        effective[uid] = raw
    return effective, historical


def persist_request_bound_provider_reacquisition(*, checkpoint_dir: Path, request: CalibrationRequest,
                                                  request_binding_sha256: str, effective_request_artifact_sha256: str,
                                                  source_sha256: str, batch_id: str, checkpoint_id: str,
                                                  selection_sha256: str, manifest_sha256: str,
                                                  raw: TranslationResult, provenance: Mapping[str, Any]) -> Path:
    if _checkpoint_is_sealed(checkpoint_dir):
        raise RequestSupersessionError("PROVIDER_REACQUISITION_AFTER_SEAL")
    if raw.unit_uid != request.unit_uid or provenance.get("request_binding_sha256") != request_binding_sha256:
        raise RequestSupersessionError("PROVIDER_REACQUISITION_REQUEST_BINDING_INVALID")
    raw_sha = sha256(raw.model_dump_json().encode("utf-8")).hexdigest()
    record = {"artifact_type": "REQUEST_BOUND_PROVIDER_REACQUISITION", "schema_version": 1,
              "reason_code": PROVIDER_REACQUISITION_REASON, "unit_uid": request.unit_uid,
              "display_id": request.display_id, "effective_request_artifact_sha256": effective_request_artifact_sha256,
              "effective_request_row_fingerprint": request_binding_sha256, "source_sha256": source_sha256,
              "batch_id": batch_id, "checkpoint_id": checkpoint_id, "selection_sha256": selection_sha256,
              "manifest_sha256": manifest_sha256, "raw_response": raw.model_dump(), "raw_response_sha256": raw_sha,
              "provider_provenance": dict(provenance)}
    path = checkpoint_dir / PROVIDER_REACQUISITION_DIR / f"{request.unit_uid}.{request_binding_sha256}.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != record:
            raise RequestSupersessionError("PROVIDER_REACQUISITION_EVIDENCE_CONFLICT")
    else:
        write_json_once(path, record)
    return path


def raw_uids_on_disk(path: Path) -> set[str]:
    return set(load_raw_by_uid(path))


def append_raw_response_line(path: Path, response: TranslationResult) -> None:
    existing = raw_uids_on_disk(path) if path.is_file() else set()
    if response.unit_uid in existing:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(response.model_dump_json())
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def ensure_requests_artifact(path: Path, requests: list[CalibrationRequest]) -> None:
    if not path.exists():
        write_jsonl_once(path, requests)
        return
    existing = load_jsonl_models(path, CalibrationRequest)
    if [row.model_dump() for row in existing] == [row.model_dump() for row in requests]:
        return
    # D050 is a verified runtime overlay.  A checkpoint that translated before
    # an exact marker registry existed must retain its immutable request file;
    # the current registry is applied only in memory for downstream Core QA.
    runtime_d050_fields = {"footnote_reference_numbers", "verified_footnote_numeric_bindings"}
    existing_core = [row.model_dump(exclude=runtime_d050_fields) for row in existing]
    requested_core = [row.model_dump(exclude=runtime_d050_fields) for row in requests]
    if existing_core != requested_core:
        raise RuntimeError(f"CANARY_REQUEST_ARTIFACT_MISMATCH:{path}")


CONTINUATION_STAGES = (
    "reviews",
    "repair_contracts",
    "repair_candidates",
    "semantic_repair_contracts",
    "semantic_repair_candidates",
    "re_reviews",
    "continuation",
    "review_contexts",
    "reviews_v2",
    "re_reviews_v2",
    "repair_contracts_v2",
    "repair_candidates_v2",
    "review_groups",
    "reviews_v3",
    "re_reviews_v3",
    "repair_contracts_v3",
    "repair_candidates_v3",
    "re_reviews_v3_supersession",
    "re_reviews_v4",
    "group_repair_eligibility",
    "group_reviews",
    "group_repair_contracts",
    "group_repair_candidates",
    "group_re_reviews",
    "human_adjudications",
    "human_approved_candidates",
    "human_group_commits",
    "reviewer_payloads",
    "reviewer_envelopes",
    "production_reprocess",
    "d050_preflight_reprocess",
    # D050 exact-marker recovery is a distinct, one-shot recovery family.
    # Its attempt record is the family-scoped consumed marker; it must never
    # reuse or overwrite the older generic semantic-rescue evidence.
    "d050_exact_marker_recovery_attempts",
    "d050_exact_marker_recovery_eligibility",
    "d050_exact_marker_recovery_repairs",
    "d050_exact_marker_recovery_completeness",
    "d050_exact_marker_recovery_final_review_requests",
    "d050_exact_marker_recovery_final_reviews",
    "d050_exact_marker_recovery_final_review_failures",
    "d050_exact_marker_recovery_final_review_contract_failures",
    "d050_exact_marker_recovery_final_review_retries",
    "human_required_triage",
    # Create-once assessment proving why a terminal semantic escalation is
    # actually necessary; it never rewrites a historical triage record.
    "human_escalation_assessments",
    "human_escalation_packets",
    "reviewer_contract_failures",
    "reviewer_contract_debts",
    "repair_contract_failures",
    "repair_contract_debts",
    # Invalid candidate-generating provider responses are durable diagnostic
    # evidence only. They never form RAW or candidate authority.
    "provider_contract_failures",
    # A terminal no-candidate disposition retains the exact upstream failure
    # that exhausted acquisition; it never creates or promotes a candidate.
    "candidate_acquisition_terminals",
    "semantic_rescue_eligibility",
    "semantic_rescue_reviews",
    "semantic_rescue_repairs",
    "semantic_rescue_final_review_requests",
    "semantic_rescue_final_reviews",
    "semantic_rescue_final_review_failures",
    "semantic_rescue_completeness",
    "semantic_rescue_corrective_eligibility",
    "semantic_rescue_corrective_repairs",
    "semantic_rescue_corrective_completeness",
    "semantic_rescue_corrective_final_review_requests",
    "semantic_rescue_corrective_final_reviews",
    "semantic_rescue_corrective_final_review_failures",
    "semantic_rescue_corrective",
    # A corrective child is keyed to the authoritative parent/head review.
    # It cannot share a create-once namespace with an earlier head's rescue.
    "semantic_rescue_current_head_corrective_eligibility",
    "semantic_rescue_current_head_corrective_repairs",
    "semantic_rescue_current_head_corrective_completeness",
    "semantic_rescue_current_head_corrective_final_review_requests",
    "semantic_rescue_current_head_corrective_final_reviews",
    "semantic_rescue_current_head_corrective_final_review_failures",
    "semantic_rescue_current_head_corrective_final_review_contract_failures",
    "semantic_rescue_current_head_corrective_final_review_retries",
    "semantic_rescue_current_head_corrective",
    # Round two remains a prepared workspace under the same authoritative
    # parent.  These immutable records are not a Candidate Authority lane.
    "semantic_rescue_current_head_corrective_round_2_eligibility",
    "semantic_rescue_current_head_corrective_round_2_repairs",
    "semantic_rescue_current_head_corrective_round_2_completeness",
    "semantic_rescue_current_head_corrective_round_2_final_review_requests",
    "semantic_rescue_current_head_corrective_round_2_final_reviews",
    "semantic_rescue_current_head_corrective_round_2_final_review_failures",
    "semantic_rescue_current_head_corrective_round_2_final_review_contract_failures",
    "semantic_rescue_current_head_corrective_round_2_final_review_retries",
    "semantic_rescue_current_head_corrective_round_2",
    "semantic_rescue",
    "semantic_rescue_binding_audits",
    "semantic_rescue_rereview_requests",
    "semantic_rescue_rereviews",
    "resolution_queue_auto_repairs",
    "resolution_queue_auto_final_reviews",
    "resolution_queue_auto_resolutions",
    "resolution_queue_auto_dispositions",
    "resolution_queue_rejected_repair_candidates",
    "d051_reviewed_equivalences",
    "d051_qa",
    "semantic_rescue_polish",
    "semantic_rescue_polish_reviews",
    "auto_polish_resolutions",
    # Derived compatibility evidence: it records the candidate selected by
    # the single production authority and never replaces immutable RAW.
    "authoritative_candidate_states",
    # Append-only effective-QA reconciliation. It binds a historical RAW QA
    # failure to the single current candidate head without rewriting history.
    "current_candidate_qa_reconciliations",
    # Immutable DOCX/PDF exact-reading evidence may correct a derived source
    # extraction gap.  It is source proof only and never source or candidate
    # authority by itself.
    "source_extraction_exact_evidence",
    # Release-boundary evidence reuses the normal checkpoint authority.  These
    # records are create-once audits/reviews of an already sealed batch; they
    # do not form a second candidate or campaign subsystem.
    "release_boundary_repairs",
    "release_boundary_final_reviews",
    "release_boundary_adjudications",
    "release_boundary_adjudication_supersessions",
    # A production seal may carry a localized, fully-bound semantic debt.  The
    # debt is create-once evidence, never a replacement for the unit result or
    # an implicit model approval.
    "resolution_queue_deferred_debts",
    # The sole durable commit record for a normal production mutation.  It is
    # intentionally separate from the compatibility ``units`` record: the
    # latter describes an outcome but is never candidate authority.
    "accepted_candidate_mutations",
)


def stage_path(output_dir: Path, stage: str, display_id: str) -> Path:
    if stage not in CONTINUATION_STAGES:
        raise ValueError(f"CANARY_STAGE_UNKNOWN:{stage}")
    return output_dir / stage / f"{display_id}.json"


def persist_stage_once(output_dir: Path, stage: str, display_id: str, record: Mapping[str, Any]) -> None:
    persist_unit_result(stage_path(output_dir, stage, display_id), record)


def load_stage(output_dir: Path, stage: str, display_id: str) -> dict[str, Any] | None:
    return load_unit_result(stage_path(output_dir, stage, display_id))


def raw_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()
