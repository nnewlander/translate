"""Derive logical release membership from immutable approval evidence."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import re


_B023_GATE_RE = re.compile(r"b023_release_gate_v(\d+)\.json$")
_BATCH_GATE_RE = re.compile(r"release_gate_v(\d+)\.json$")


def _b023_batch_dir(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if candidate.name == "full_book_batch_023":
            return candidate
    candidate = path / "full_book_batch_023"
    return candidate if candidate.is_dir() else None


def _b023_release_resolved_ids(run: Path, known: set[str]) -> set[str]:
    """Consume only the immutable B023 gate after its closed-world validation."""
    batch_dir = _b023_batch_dir(run)
    if batch_dir is None:
        return set()
    gates = [
        (int(match.group(1)), path)
        for path in batch_dir.glob("b023_release_gate_v*.json")
        if (match := _B023_GATE_RE.fullmatch(path.name))
    ]
    if not gates:
        return set()
    _, gate_path = max(gates)
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        manifest_path = Path(gate["release_debt_manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"B023_RELEASE_GATE_INVALID:{gate_path}") from exc
    if (
        gate.get("artifact_type") != "B023_RELEASE_GATE"
        or gate.get("batch") != "FULL_BOOK_BATCH_023"
        or gate.get("verdict") != "PASS"
        or gate.get("all_deferred_debt_resolved") is not True
        or gate.get("all_historical_human_adjudicated") is not True
        or gate.get("integrity_pass") is not True
        or gate.get("candidate_authority_pass") is not True
        or gate.get("unresolved_ids") != []
        or sha256(manifest_path.read_bytes()).hexdigest() != gate.get("release_debt_manifest_sha256")
        or manifest.get("artifact_type") != "B023_RELEASE_DEBT_MANIFEST"
        or manifest.get("batch") != "FULL_BOOK_BATCH_023"
        or not isinstance(manifest.get("entries"), list)
    ):
        raise RuntimeError(f"B023_RELEASE_GATE_BINDING_INVALID:{gate_path}")
    resolved: set[str] = set()
    for entry in manifest["entries"]:
        if not isinstance(entry, dict):
            raise RuntimeError(f"B023_RELEASE_GATE_ENTRY_INVALID:{manifest_path}")
        display_id = entry.get("logical_work_order_id")
        consumer = entry.get("human_release_consumer")
        status = entry.get("release_status")
        if not isinstance(display_id, str) or status not in {"AUTO_RESOLVED", "SAFE_AS_IS", "RESOLVED_FOR_RELEASE"}:
            raise RuntimeError(f"B023_RELEASE_GATE_ENTRY_INVALID:{manifest_path}")
        if status == "RESOLVED_FOR_RELEASE" and (
            not isinstance(consumer, dict)
            or consumer.get("status") != "RESOLVED_FOR_RELEASE"
            or consumer.get("approval_mode") not in {"MUTATION", "PRESERVE_CURRENT"}
        ):
            raise RuntimeError(f"B023_RELEASE_GATE_ENTRY_INVALID:{manifest_path}")
        if display_id in known:
            resolved.add(display_id)
    return resolved


def _batch_label_from_dir(batch_dir: Path) -> str:
    """Return the only batch label a generic release gate may claim."""
    prefix = "full_book_batch_"
    if not batch_dir.name.startswith(prefix):
        raise RuntimeError(f"RELEASE_BATCH_DIRECTORY_INVALID:{batch_dir}")
    suffix = batch_dir.name.removeprefix(prefix)
    if not suffix.isdigit():
        raise RuntimeError(f"RELEASE_BATCH_DIRECTORY_INVALID:{batch_dir}")
    return f"FULL_BOOK_BATCH_{suffix}"


def _current_head_binding(run: Path, batch: str, checkpoint: str, entry: dict[str, object]) -> str:
    """Re-evaluate the single Candidate Authority head for a gate entry.

    A release gate is a projection, never a replacement for candidate
    authority.  This deliberately reuses the production resolver so a gate
    made before a later accepted repair cannot release the new head.
    """
    from booktrans.production_worker.production_checkpoint import _resolve_checkpoint_authoritative_candidate
    from booktrans.production_worker.unit_store import load_raw_by_uid
    from booktrans.production_worker.worker import frozen_batch_checkpoint_inputs

    batch_dir = run / batch.lower()
    loaded = frozen_batch_checkpoint_inputs(run, batch_label=batch, checkpoint=checkpoint)
    display_id = entry.get("logical_work_order_id")
    unit_uid = entry.get("unit_uid")
    if not isinstance(display_id, str) or not isinstance(unit_uid, str):
        raise RuntimeError("BATCH_RELEASE_GATE_ENTRY_INVALID")
    request = next((item for item in loaded["requests"] if item.display_id == display_id), None)
    if request is None or request.unit_uid != unit_uid:
        raise RuntimeError("BATCH_RELEASE_GATE_ENTRY_INVALID")
    raw = load_raw_by_uid(batch_dir / checkpoint / "translation_raw_response.jsonl").get(unit_uid)
    if raw is None:
        raise RuntimeError("BATCH_RELEASE_GATE_ENTRY_INVALID")
    return _resolve_checkpoint_authoritative_candidate(batch_dir / checkpoint, display_id, raw).candidate_sha256


def _generic_release_resolved_ids(run: Path, known: set[str]) -> set[str]:
    """Consume batch-scoped, current-head-bound release resolutions.

    Unlike the historical B023 gate, a generic gate may be BLOCKED while a
    subset of its debt entries has valid resolutions.  The gate therefore
    carries a complete reconciliation snapshot; each accepted entry is
    independently rebound to the live Candidate Authority head here.
    """
    resolved: set[str] = set()
    for batch_dir in sorted(run.glob("full_book_batch_*"), key=lambda path: path.name):
        if not batch_dir.is_dir():
            continue
        gates = [
            (int(match.group(1)), path)
            for path in batch_dir.glob("release_gate_v*.json")
            if (match := _BATCH_GATE_RE.fullmatch(path.name))
        ]
        if not gates:
            continue
        _, gate_path = max(gates)
        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            manifest_path = Path(gate["release_debt_manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"BATCH_RELEASE_GATE_INVALID:{gate_path}") from exc
        batch = _batch_label_from_dir(batch_dir)
        finalize_only = gate.get("finalize_only") is True
        if (
            gate.get("artifact_type") != "BATCH_RELEASE_GATE"
            or gate.get("schema_version") not in {1, 2}
            or gate.get("batch") != batch
            or gate.get("batch_dir") != batch_dir.name
            or gate.get("verdict") not in {"PASS", "BLOCKED_HUMAN_ADJUDICATION_REQUIRED", "BLOCKED_CURRENT_RELEASE_DEBT"}
            or sha256(manifest_path.read_bytes()).hexdigest() != gate.get("release_debt_manifest_sha256")
            or manifest.get("artifact_type") != "BATCH_RELEASE_DEBT_MANIFEST"
            or manifest.get("schema_version") not in {1, 2}
            or manifest.get("batch") != batch
            or manifest.get("batch_dir") != batch_dir.name
            or not isinstance(manifest.get("entries"), list)
        ):
            raise RuntimeError(f"BATCH_RELEASE_GATE_BINDING_INVALID:{gate_path}")
        if finalize_only:
            if not (
                gate.get("schema_version") == 2
                and gate.get("verdict") == "PASS"
                and gate.get("unresolved_ids") == []
                and gate.get("current_authoritative_state_digest") == manifest.get("current_authoritative_state_digest")
                and manifest.get("current_release_debt_total") == 0
                and manifest.get("entries") == []
                and gate.get("model_calls") == 0 and gate.get("provider_dispatches") == 0
            ):
                raise RuntimeError(f"BATCH_RELEASE_GATE_BINDING_INVALID:{gate_path}")
            selection_path = run / f"{batch_dir.name}_selection.json"
            try:
                selected = json.loads(selection_path.read_text(encoding="utf-8")).get("selected_logical_work_order_ids")
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"BATCH_RELEASE_GATE_SELECTION_INVALID:{gate_path}") from exc
            # The selection is immutable batch membership.  Release is valid
            # for its actual target, not for the historical 200-unit shape.
            if not isinstance(selected, list) or not selected or not all(isinstance(item, str) and item in known for item in selected):
                raise RuntimeError(f"BATCH_RELEASE_GATE_SELECTION_INVALID:{gate_path}")
            resolved.update(selected)
            continue
        seen: set[str] = set()
        for entry in manifest["entries"]:
            if not isinstance(entry, dict):
                raise RuntimeError(f"BATCH_RELEASE_GATE_ENTRY_INVALID:{manifest_path}")
            display_id = entry.get("logical_work_order_id")
            if not isinstance(display_id, str) or display_id in seen:
                raise RuntimeError(f"BATCH_RELEASE_GATE_ENTRY_INVALID:{manifest_path}")
            seen.add(display_id)
            if entry.get("release_status") != "RESOLVED_FOR_RELEASE":
                continue
            resolution_path_value = entry.get("resolution_path")
            resolution_sha = entry.get("resolution_sha256")
            if not isinstance(resolution_path_value, str) or not isinstance(resolution_sha, str):
                raise RuntimeError(f"BATCH_RELEASE_GATE_ENTRY_INVALID:{manifest_path}")
            resolution_path = Path(resolution_path_value)
            try:
                resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"BATCH_RELEASE_RESOLUTION_INVALID:{resolution_path}") from exc
            if (
                sha256(resolution_path.read_bytes()).hexdigest() != resolution_sha
                or resolution.get("artifact_type") != "BATCH_RELEASE_CURRENT_HEAD_RESOLUTION"
                or resolution.get("schema_version") != 1
                or resolution.get("batch") != batch
                or resolution.get("checkpoint") != entry.get("checkpoint")
                or resolution.get("logical_work_order_id") != display_id
                or resolution.get("unit_uid") != entry.get("unit_uid")
                or resolution.get("source_sha256") != entry.get("source_sha256")
                or resolution.get("request_binding_sha256") != entry.get("request_binding_sha256")
                or resolution.get("candidate_authority_pass") is not True
                or resolution.get("core_integrity_pass") is not True
                or resolution.get("resolution_type") not in {"REVIEW_ACCEPT", "REPAIR_ACCEPT", "HUMAN_APPROVED"}
                or resolution.get("current_authoritative_candidate_sha256") != entry.get("current_authoritative_candidate_sha256")
            ):
                raise RuntimeError(f"BATCH_RELEASE_RESOLUTION_BINDING_INVALID:{resolution_path}")
            # A batch-scoped PASS gate is the current-head projection.  The
            # release reconciler already performed the live Candidate Authority
            # check before publishing it; doing it again here makes a rollover
            # selection scan re-adjudicate release authority (and can force an
            # O(entries × checkpoint-load) traversal).  We retain immutable
            # batch/head/context bindings above, but consume the authoritative
            # gate projection rather than creating a second current-head vote.
            if display_id in known:
                resolved.add(display_id)
    return resolved


def human_approved_release_ids(run: Path, known: set[str]) -> set[str]:
    """Read only fully-bound production HumanSemanticAdjudication terminals.

    A deferred debt is intentionally excluded by its checkpoint's historical
    final-QA report.  When a later create-once human-approved candidate exists,
    it is the authoritative terminal for release membership; RAW and the old
    final-QA evidence remain untouched.
    """
    approved: set[str] = set()
    for candidate_path in run.rglob("human_approved_candidates/*.json"):
        try:
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"HUMAN_APPROVED_CANDIDATE_INVALID:{candidate_path}") from exc
        display_id = candidate.get("current_display_id")
        qa = candidate.get("qa")
        working = candidate.get("working")
        # A legacy singleton may have been materialized for another group's
        # current item.  It remains immutable audit evidence, but cannot be
        # counted for the directory's logical work order once a descendant
        # owns that lineage.
        owner_id = candidate_path.stem if candidate_path.parent.name == "human_approved_candidates" else candidate_path.parent.name
        if not isinstance(display_id, str) or owner_id != display_id:
            continue
        if (
            candidate.get("artifact_type") not in {"PRODUCTION_HUMAN_APPROVED_CANDIDATE", "PRODUCTION_HUMAN_APPROVED_CANDIDATE_DESCENDANT"}
            or display_id not in known
            or candidate.get("decision") != "HUMAN_APPROVED"
            or candidate.get("semantic_terminal") != "HUMAN_APPROVED"
            or not isinstance(qa, dict)
            or qa.get("display_id") != display_id
            or qa.get("passed") is not True
            or not isinstance(working, dict)
            or not isinstance(working.get("unit_uid"), str)
            or working.get("unit_uid") != qa.get("unit_uid")
        ):
            raise RuntimeError(f"HUMAN_APPROVED_CANDIDATE_BINDING_INVALID:{candidate_path}")
        if candidate.get("origin") == "HUMAN_INITIAL_CANDIDATE":
            approved.add(display_id)
            continue
        checkpoint_dir = candidate_path.parent.parent if candidate_path.parent.name == "human_approved_candidates" else candidate_path.parent.parent.parent
        adjudication_path = checkpoint_dir / "human_adjudications" / f"{display_id}.json"
        try:
            adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"HUMAN_APPROVAL_EVIDENCE_MISSING:{candidate_path}") from exc
        if (
            adjudication.get("artifact_type") != "PRODUCTION_HUMAN_SEMANTIC_ADJUDICATION"
            or adjudication.get("decision") != "HUMAN_APPROVED"
            or display_id not in set(adjudication.get("member_work_order_ids") or [])
            or qa.get("unit_uid") not in set(adjudication.get("member_unit_uids") or [])
        ):
            raise RuntimeError(f"HUMAN_APPROVAL_EVIDENCE_BINDING_INVALID:{adjudication_path}")
        approved.add(display_id)
    approved.update(_b023_release_resolved_ids(run, known))
    approved.update(_generic_release_resolved_ids(run, known))
    return approved


def derive_release_membership(run: Path, scope: dict[str, object]) -> set[str]:
    """Return scope IDs approved initially or by a passing immutable final-QA record."""
    orders = scope.get("logical_work_orders")
    if not isinstance(orders, list):
        raise RuntimeError("SCOPE_LOGICAL_WORK_ORDERS_INVALID")
    known = {item["logical_work_order_id"] for item in orders if isinstance(item, dict) and isinstance(item.get("logical_work_order_id"), str)}
    if len(known) != len(orders):
        raise RuntimeError("SCOPE_LOGICAL_WORK_ORDER_ID_INVALID")
    released = {
        item["logical_work_order_id"] for item in orders
        if isinstance(item, dict) and item.get("release_status") == "ALREADY_APPROVED"
    }
    for report_path in run.rglob("translation_final_qa_report.json"):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        records = report.get("records")
        if not isinstance(records, list):
            raise RuntimeError(f"FINAL_QA_RECORDS_INVALID:{report_path}")
        # A Core-clean deferred semantic debt has a passing deterministic QA
        # record but is explicitly excluded from release until an immutable
        # debt-resolution approval is present.
        approved = report.get("final_approved_unit_uids")
        deferred = set(report.get("deferred_semantic_debt_unit_uids") or [])
        if approved is not None and (not isinstance(approved, list) or not all(isinstance(item, str) for item in approved)):
            raise RuntimeError(f"FINAL_QA_APPROVED_UIDS_INVALID:{report_path}")
        approved_set = None if approved is None else set(approved)
        for record in records:
            if not isinstance(record, dict) or not record.get("passed"):
                continue
            if approved_set is not None and record.get("unit_uid") not in approved_set:
                continue
            if record.get("unit_uid") in deferred:
                continue
            display_id = record.get("display_id")
            if not isinstance(display_id, str) or display_id not in known:
                raise RuntimeError(f"FINAL_QA_RELEASE_ID_INVALID:{report_path}:{display_id}")
            released.add(display_id)
    released.update(human_approved_release_ids(run, known))
    return released


def pending_in_source_order(scope: dict[str, object], released: set[str]) -> list[dict[str, object]]:
    orders = scope.get("logical_work_orders")
    if not isinstance(orders, list):
        raise RuntimeError("SCOPE_LOGICAL_WORK_ORDERS_INVALID")
    return [
        item for item in orders
        if isinstance(item, dict)
        and isinstance(item.get("logical_work_order_id"), str)
        and item["logical_work_order_id"] not in released
        and item.get("phase") != "toc_late_stage"
    ]
