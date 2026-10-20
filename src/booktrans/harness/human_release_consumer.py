"""Fail-closed consumption of production human approval for release debt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from booktrans.domain.calibration import TranslationResult
from booktrans.production_worker.candidate_authority import ResolvedCandidate, candidate_sha


class HumanReleaseConsumerError(RuntimeError):
    """Human evidence cannot close this exact release debt."""


@dataclass(frozen=True)
class HumanReleaseConsumption:
    logical_work_order_id: str
    status: str = "RESOLVED_FOR_RELEASE"
    approval_mode: str = "PRESERVE_CURRENT"


def _fail(code: str) -> None:
    raise HumanReleaseConsumerError(code)


def _read(path: Path, *, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HumanReleaseConsumerError(f"{code}:{path}") from exc
    if not isinstance(value, Mapping):
        _fail(f"{code}:{path}")
    return value


def _batch_for_checkpoint(checkpoint_dir: Path) -> str | None:
    name = checkpoint_dir.parent.name
    if not name.startswith("full_book_batch_"):
        return None
    return f"FULL_BOOK_BATCH_{name.removeprefix('full_book_batch_')}"


def _same_member(adjudication: Mapping[str, Any], *, display_id: str, unit_uid: str) -> bool:
    ids = adjudication.get("member_work_order_ids")
    uids = adjudication.get("member_unit_uids")
    if not isinstance(ids, list) or not isinstance(uids, list) or len(ids) != len(uids):
        return False
    return sum(item == display_id and uid == unit_uid for item, uid in zip(ids, uids, strict=True)) == 1


def _candidate_paths_for_debt(folder: Path, *, display_id: str, unit_uid: str) -> list[Path]:
    if not folder.is_dir():
        return []
    paths: list[Path] = []
    for path in sorted(folder.glob("*.json"), key=lambda item: item.name.casefold()):
        candidate = _read(path, code="HUMAN_RELEASE_CANDIDATE_INVALID")
        working = candidate.get("working")
        if candidate.get("current_display_id") == display_id and isinstance(working, Mapping) and working.get("unit_uid") == unit_uid:
            paths.append(path)
    return paths


def _require_debt_context(
    release_debt: Mapping[str, Any], *, display_id: str, unit_uid: str,
    source_sha256: str, request_binding_sha256: str,
) -> str:
    if (
        release_debt.get("logical_work_order_id") != display_id
        or release_debt.get("unit_uid") != unit_uid
        or release_debt.get("source_sha256") != source_sha256
        or release_debt.get("request_binding_sha256") != request_binding_sha256
        or release_debt.get("release_status") != "TRUE_HUMAN_REQUIRED"
    ):
        _fail("HUMAN_RELEASE_DEBT_CONTEXT_MISMATCH")
    parent = release_debt.get("current_authoritative_candidate_sha256")
    if not isinstance(parent, str) or len(parent) != 64:
        _fail("HUMAN_RELEASE_DEBT_PARENT_MISSING")
    return parent


def consume_human_approval_for_release(
    *,
    checkpoint_dir: Path,
    batch_label: str,
    logical_work_order_id: str,
    unit_uid: str,
    source_sha256: str,
    request_binding_sha256: str,
    release_debt: Mapping[str, Any],
    current_head: ResolvedCandidate,
    allow_legacy_batch_evidence: bool = False,
) -> HumanReleaseConsumption | None:
    """Return a release terminal only for one completely bound approval.

    Absence of an approval is intentionally not an error: its debt remains
    ``TRUE_HUMAN_REQUIRED``.  Any present-but-invalid evidence raises instead
    of permitting a fallback or a cross-batch interpretation.
    """
    adjudication_path = checkpoint_dir / "human_adjudications" / f"{logical_work_order_id}.json"
    candidate_folder = checkpoint_dir / "human_approved_candidates"
    candidate_paths = _candidate_paths_for_debt(
        candidate_folder, display_id=logical_work_order_id, unit_uid=unit_uid
    )
    if not adjudication_path.exists():
        if candidate_paths:
            _fail("HUMAN_RELEASE_ADJUDICATION_MISSING")
        return None
    adjudication = _read(adjudication_path, code="HUMAN_RELEASE_ADJUDICATION_INVALID")
    parent_sha = _require_debt_context(
        release_debt,
        display_id=logical_work_order_id,
        unit_uid=unit_uid,
        source_sha256=source_sha256,
        request_binding_sha256=request_binding_sha256,
    )
    if (
        adjudication.get("artifact_type") != "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION"
        or adjudication.get("decision") != "HUMAN_APPROVED"
        or adjudication.get("triage_classification") != "TRUE_SEMANTIC_HUMAN"
        or not _same_member(adjudication, display_id=logical_work_order_id, unit_uid=unit_uid)
    ):
        _fail("HUMAN_RELEASE_ADJUDICATION_BINDING_INVALID")
    evidence_batch = adjudication.get("batch_label")
    if evidence_batch != batch_label:
        if not (
            allow_legacy_batch_evidence
            and evidence_batch is None
            and _batch_for_checkpoint(checkpoint_dir) == batch_label
        ):
            _fail("HUMAN_RELEASE_BATCH_MISMATCH")
    evidence_source = adjudication.get("source_sha256")
    if evidence_source is not None and evidence_source != source_sha256:
        _fail("HUMAN_RELEASE_SOURCE_SHA_MISMATCH")
    source_hashes = adjudication.get("source_hashes")
    if not isinstance(source_hashes, Mapping) or source_hashes.get(logical_work_order_id) != request_binding_sha256:
        _fail("HUMAN_RELEASE_REQUEST_BINDING_MISMATCH")
    if len(candidate_paths) > 1:
        _fail("HUMAN_RELEASE_CREATE_ONCE_CONFLICT")
    candidate_path = candidate_folder / f"{logical_work_order_id}.json"
    if candidate_paths and candidate_paths[0] != candidate_path:
        _fail("HUMAN_RELEASE_CREATE_ONCE_CONFLICT")

    mode = adjudication.get("approval_mode")
    if candidate_path.exists():
        candidate_record = _read(candidate_path, code="HUMAN_RELEASE_CANDIDATE_INVALID")
        if mode not in {None, "MUTATION"}:
            _fail("HUMAN_RELEASE_APPROVAL_MODE_INVALID")
        working = candidate_record.get("working")
        qa = candidate_record.get("qa")
        try:
            approved = TranslationResult.model_validate(working)
        except Exception as exc:
            raise HumanReleaseConsumerError("HUMAN_RELEASE_CANDIDATE_INVALID") from exc
        approved_sha = candidate_sha(approved)
        legacy_candidate = allow_legacy_batch_evidence and int(candidate_record.get("schema_version") or 1) < 2
        if (
            candidate_record.get("artifact_type") != "PRODUCTION_HUMAN_APPROVED_CANDIDATE"
            or candidate_record.get("current_display_id") != logical_work_order_id
            or candidate_record.get("decision") != "HUMAN_APPROVED"
            or candidate_record.get("semantic_terminal") != "HUMAN_APPROVED"
            or approved.unit_uid != unit_uid
            or not isinstance(qa, Mapping)
            or qa.get("display_id") != logical_work_order_id
            or qa.get("unit_uid") != unit_uid
            or qa.get("passed") is not True
            or current_head.candidate_sha256 != approved_sha
        ):
            _fail("HUMAN_RELEASE_CANDIDATE_BINDING_INVALID")
        if not legacy_candidate and (
            candidate_record.get("candidate_binding_sha256") != approved_sha
            or candidate_record.get("parent_candidate_sha256") != parent_sha
            or candidate_record.get("source_sha256") != source_sha256
            or candidate_record.get("request_binding_sha256") != request_binding_sha256
        ):
            _fail("HUMAN_RELEASE_CANDIDATE_BINDING_INVALID")
        return HumanReleaseConsumption(logical_work_order_id, approval_mode="MUTATION")

    if mode != "PRESERVE_CURRENT":
        _fail("HUMAN_RELEASE_APPROVED_CANDIDATE_MISSING")
    if (
        adjudication.get("source_sha256") != source_sha256
        or adjudication.get("approved_candidate_sha256") != current_head.candidate_sha256
        or current_head.candidate_sha256 != parent_sha
    ):
        _fail("HUMAN_RELEASE_PRESERVE_CURRENT_BINDING_INVALID")
    return HumanReleaseConsumption(logical_work_order_id)
