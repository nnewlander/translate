"""Isolated 25-unit production-shaped canary.  Never writes B022/checkpoint_03."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.translation_artifacts import normalize_response_artifact
from booktrans.production_worker.artifacts import (
    ROUTE_PROVIDER_NAMES,
    candidate_provenance,
    persist_checkpoint_raw,
    persist_route_provenance,
    reconstructed_provenance,
    write_json_once,
    write_json_replace,
    write_jsonl_once,
)
from booktrans.production_worker.diagnostics import TELEMETRY_FILENAMES, CanaryDiagnostics
from booktrans.production_worker.pipeline import UnitOutcome, WorkerProviders, enrich_request, process_unit
from booktrans.production_worker.qa import QaFn, qa_codes
from booktrans.production_worker.selection import (
    CHECKPOINT_SIZE,
    assert_not_production_checkpoint,
)
from booktrans.production_worker.unit_store import (
    append_jsonl_fsync,
    append_raw_response_line,
    ensure_requests_artifact,
    load_raw_by_uid,
    load_unit_result,
    load_unit_results,
    persist_unit_result,
    raw_sha256,
    unit_path,
)
from booktrans.providers.router import route_semantic_unit


CANARY_RUNTIME_VERSION = "booktrans.qwen-deterministic-canary.v1"
PRODUCTION_BATCH = "FULL_BOOK_BATCH_022"
PRODUCTION_CHECKPOINT = "checkpoint_03"
NOT_PERSISTED = "NOT_PERSISTED"
SEMANTIC_PASS_TERMINALS = frozenset({"MODEL_ACCEPTED", "HUMAN_APPROVED"})


def semantic_terminal_of_outcome(outcome: UnitOutcome) -> str:
    stored = getattr(outcome, "semantic_terminal", None)
    if stored in {"MODEL_ACCEPTED", "HUMAN_APPROVED", "HUMAN_REJECTED", "HUMAN_REQUIRED"}:
        return stored
    if outcome.final_status == "HUMAN_APPROVED":
        return "HUMAN_APPROVED"
    if outcome.final_status == "HUMAN_REJECTED":
        return "HUMAN_REJECTED"
    if outcome.final_status in {"ACCEPT", "MODEL_ACCEPTED"}:
        return "MODEL_ACCEPTED"
    if outcome.human_required or outcome.final_status == "HUMAN_REQUIRED":
        return "HUMAN_REQUIRED"
    return "UNRESOLVED"


def evaluate_canary_gate(report: Mapping[str, Any]) -> dict[str, Any]:
    unit_count = int(report.get("unit_count") or 0)
    unresolved_human = int(report.get("unresolved_human_required", report.get("human_required", 0)) or 0)
    rejected = int(report.get("human_rejected_count") or 0)
    model_accepted = int(report.get("semantic_terminal_model_accepted") or 0)
    human_approved = int(report.get("human_approved_count") or 0)
    semantic_resolved = model_accepted + human_approved
    if "semantic_terminal_model_accepted" not in report and "human_approved_count" not in report:
        semantic_resolved = unit_count if unresolved_human == 0 and report.get("final_qa_pass") == unit_count else -1
    checks = {
        "provider_contract_complete": report.get("provider_contract_pass") == unit_count and unit_count > 0,
        "no_source_protected_integrity_violation": report.get("source_integrity_violations", 0) == 0
        and report.get("protected_integrity_violations", 0) == 0,
        "final_qa_complete": report.get("final_qa_pass") == unit_count and unit_count > 0,
        "repairs_resolved_or_human_required": report.get("unresolved_repair", 0) == 0,
        "no_silent_unresolved_semantic": report.get("silent_unresolved_semantic", 0) == 0,
        "no_immutable_provenance_violation": report.get("immutable_provenance_violations", 0) == 0,
        "reviewer_persisted": report.get("review_not_persisted", 0) == 0,
        "no_reexecution_required": report.get("reexecution_required", 0) == 0,
        "semantic_terminals_resolved": semantic_resolved == unit_count and unit_count > 0,
        "unresolved_human_required_zero": unresolved_human == 0,
        "human_rejected_zero": rejected == 0,
    }
    if "translation_api_calls" in report:
        checks["translation_api_calls_zero"] = report.get("translation_api_calls") == 0
    return {"passed": all(checks.values()), "checks": checks}


def _integrity_codes(record: Mapping[str, Any]) -> tuple[int, int]:
    codes = qa_codes(record)
    source = sum(code in {"UNKNOWN_UNIT_ID", "ISLAND_ORDER_OR_SET_MISMATCH", "MISSING_ANCHOR"} for code in codes)
    protected = sum(code == "PROTECTED_TEXT" for code in codes)
    return source, protected


def _review_status(outcome: UnitOutcome) -> str:
    terminal = semantic_terminal_of_outcome(outcome)
    if terminal in SEMANTIC_PASS_TERMINALS:
        return "ACCEPT"
    if outcome.human_required or outcome.final_status in {"HUMAN_REQUIRED", "HUMAN_REJECTED"}:
        return "HUMAN_REQUIRED"
    if outcome.final_status == "DETERMINISTIC_QA_FAILED":
        return "NOT_REVIEWED_DETERMINISTIC_FAIL"
    return "HUMAN_REQUIRED"


def _unit_review_status(outcome: UnitOutcome) -> str:
    if outcome.review is not None:
        return outcome.review.verdict
    if outcome.blocked:
        return "BLOCKED"
    if outcome.qa and not outcome.qa.get("passed"):
        return "NOT_REVIEWED_DETERMINISTIC_FAIL"
    if outcome.raw is not None:
        return "NOT_PERSISTED"
    return "NOT_REVIEWED"


def _persist_live_unit(
    output_dir: Path,
    outcome: UnitOutcome,
    *,
    source_sha256: str | None,
    selection_sha256: str | None,
) -> None:
    if outcome.raw is None or outcome.candidate is None:
        return
    provenance = candidate_provenance(
        outcome.candidate, outcome.decision, latency_seconds=outcome.latency_seconds
    )
    line_sha = sha256(outcome.raw.model_dump_json().encode("utf-8")).hexdigest()
    persist_unit_result(
        unit_path(output_dir, outcome.request.display_id),
        {
            "artifact_type": "CANARY_UNIT_RESULT",
            "schema_version": 1,
            "display_id": outcome.request.display_id,
            "unit_uid": outcome.request.unit_uid,
            "route": outcome.decision.route,
            "route_reason": outcome.decision.reason,
            "provider": provenance.get("provider"),
            "model": outcome.decision.model,
            "source_sha256": source_sha256 or outcome.request.source_sha256,
            "selection_sha256": selection_sha256,
            "candidate_binding": {
                "unit_uid": outcome.request.unit_uid,
                "display_id": outcome.request.display_id,
                "request_binding_sha256": outcome.candidate.request_binding_sha256,
                "translation_options_sha256": outcome.candidate.translation_options_sha256,
                "source_sha256": outcome.request.source_sha256,
            },
            "provenance": provenance,
            "qa": outcome.qa,
            "review": outcome.review.verdict if outcome.review is not None else NOT_PERSISTED,
            "final_status": outcome.final_status,
            "terminal_status": outcome.final_status,
            "failure_family": outcome.failure_family,
            "failure_code": outcome.failure_code,
            "repair_applied": outcome.repair_applied,
            "human_required": outcome.human_required,
            "latency_seconds": outcome.latency_seconds if outcome.latency_seconds is not None else NOT_PERSISTED,
            "usage": outcome.usage or NOT_PERSISTED,
            "recovered_from": outcome.recovered_from,
            "reexecution_required": False,
            "raw_sha256": line_sha,
        },
    )
    append_raw_response_line(output_dir / "translation_raw_response.jsonl", outcome.raw)
    append_jsonl_fsync(output_dir / "translation_provider_provenance.jsonl", provenance)


def _resume_isolated_outcomes(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    views: Mapping[str, TranslationView] | None,
    known_unit_ids: set[str] | None,
    qa_fn: QaFn | None,
    translation_bible: Path | None,
    source_sha256: str | None,
    selection_sha256: str | None,
) -> tuple[list[UnitOutcome], int, int]:
    existing_raw = load_raw_by_uid(output_dir / "translation_raw_response.jsonl")
    stored_units = load_unit_results(output_dir)
    outcomes: list[UnitOutcome] = []
    recovered_from_raw = 0
    recovered_from_unit = 0
    provenance_records = []
    for request in requests:
        decision = route_semantic_unit(request)
        outcome = UnitOutcome(request=request, decision=decision)
        stored = stored_units.get(request.display_id)
        raw = existing_raw.get(request.unit_uid)
        outcome.raw = raw
        outcome.working = raw
        provider_request = enrich_request(request, translation_bible=translation_bible)
        if stored is not None:
            recovered_from_unit += 1
            outcome.recovered_from = "unit"
            if isinstance(stored.get("qa"), dict):
                outcome.qa = stored["qa"]
            elif qa_fn is not None and views is not None and known_unit_ids is not None and raw is not None:
                outcome.qa = qa_fn(views[request.display_id], request, raw, known_unit_ids)
            outcome.final_status = str(stored.get("final_status") or "ACCEPT")
            outcome.human_required = bool(stored.get("human_required"))
            outcome.repair_applied = bool(stored.get("repair_applied"))
            review = stored.get("review")
            if review in {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}:
                from booktrans.providers.qwen_max import ReviewResult

                outcome.review = ReviewResult(
                    schema_version="booktrans.structured-review.v1",
                    unit_uid=request.unit_uid,
                    verdict=review,
                    issues=[],
                )
            if isinstance(stored.get("provenance"), dict):
                provenance_records.append(stored["provenance"])
        elif raw is not None:
            recovered_from_raw += 1
            outcome.recovered_from = "raw"
            if qa_fn is not None and views is not None and known_unit_ids is not None:
                outcome.qa = qa_fn(views[request.display_id], request, raw, known_unit_ids)
            if outcome.qa and not outcome.qa.get("passed"):
                outcome.final_status = "DETERMINISTIC_QA_FAILED"
                outcome.human_required = True
            else:
                outcome.final_status = "QA_PASS_REVIEW_NOT_PERSISTED"
            provenance_records.append(
                reconstructed_provenance(
                    request=request,
                    decision=decision,
                    request_binding_sha256=provider_request.request_binding_sha256,
                    translation_options_sha256=provider_request.translation_options_sha256,
                    reconstruction_source="translation_raw_response.jsonl",
                )
            )
        else:
            outcome.blocked = True
            outcome.final_status = "BLOCKED"
            outcome.human_required = True
        outcomes.append(outcome)
    provenance_path = output_dir / "translation_provider_provenance.jsonl"
    if provenance_records and not provenance_path.exists():
        persist_route_provenance(provenance_path, provenance_records)
    return outcomes, recovered_from_raw, recovered_from_unit


def run_isolated_canary(
    *,
    output_dir: Path,
    requests: Sequence[CalibrationRequest],
    providers: WorkerProviders,
    production_run: Path,
    views: Mapping[str, TranslationView] | None = None,
    known_unit_ids: set[str] | None = None,
    qa_fn: QaFn | None = None,
    translation_bible: Path | None = None,
    frozen_order: Sequence[str] | None = None,
    selection_sha256: str | None = None,
    source_sha256: str | None = None,
    allow_semantic_narrow: bool = True,
    diagnostics: CanaryDiagnostics | None = None,
    resume: bool = False,
    allow_provider_calls: bool = True,
) -> dict[str, Any]:
    """Execute the full new chain into an isolated directory.

    This function refuses the authoritative production checkpoint path.
    """
    assert_not_production_checkpoint(
        output_dir, run=production_run, batch_label=PRODUCTION_BATCH, checkpoint=PRODUCTION_CHECKPOINT
    )
    if not resume:
        if output_dir.exists():
            leftover = [path.name for path in output_dir.iterdir() if path.name not in TELEMETRY_FILENAMES]
            if leftover:
                raise RuntimeError(f"CANARY_OUTPUT_NOT_EMPTY:{output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
    order = list(frozen_order or [request.display_id for request in requests])
    started = datetime.now(timezone.utc)
    if resume:
        outcomes, recovered_from_raw, recovered_from_unit = _resume_isolated_outcomes(
            output_dir=output_dir,
            requests=requests,
            views=views,
            known_unit_ids=known_unit_ids,
            qa_fn=qa_fn,
            translation_bible=translation_bible,
            source_sha256=source_sha256,
            selection_sha256=selection_sha256,
        )
        raw_path = output_dir / "translation_raw_response.jsonl"
        raw_sha = raw_sha256(raw_path)
        extra = {
            "immutable_provenance_violations": 0,
            "final_qa_pass": sum(bool(item.qa and item.qa.get("passed") and semantic_terminal_of_outcome(item) in SEMANTIC_PASS_TERMINALS) for item in outcomes),
            "source_integrity_violations": 0,
            "protected_integrity_violations": 0,
            "review_not_persisted": sum(item.review is None and item.raw is not None and not item.blocked for item in outcomes),
            "reexecution_required": 0,
            "resumed": True,
            "api_calls": 0 if not allow_provider_calls else None,
            "recovered_from_raw": recovered_from_raw,
            "recovered_from_unit": recovered_from_unit,
            "provider_latency": NOT_PERSISTED,
            "usage": {"prompt_tokens": NOT_PERSISTED, "completion_tokens": NOT_PERSISTED, "total_tokens": NOT_PERSISTED},
        }
        report = _report(outcomes, started, order, selection_sha256, source_sha256, raw_sha=raw_sha, gate=None, extra=extra)
        report["recovery"] = {
            "resumed": True,
            "api_calls": 0 if not allow_provider_calls else extra.get("api_calls") or 0,
            "recovered_from_raw": recovered_from_raw,
            "recovered_from_unit": recovered_from_unit,
        }
        report["latency"]["provider_latency"] = NOT_PERSISTED
        report["usage"] = extra["usage"]
        report["gate"] = evaluate_canary_gate(report)
        writer = write_json_replace if (output_dir / "canary_report.json").exists() else write_json_once
        writer(output_dir / "canary_report.json", report)
        writer = write_json_replace if (output_dir / "canary_gate.json").exists() else write_json_once
        writer(output_dir / "canary_gate.json", report["gate"])
        return report
    outcomes: list[UnitOutcome] = []
    started = datetime.now(timezone.utc)
    total = len(requests)
    for index, request in enumerate(requests, start=1):
        if diagnostics is not None:
            diagnostics.emit("UNIT_START", index=index, total=total, id=request.display_id)
            decision = route_semantic_unit(request)
            diagnostics.emit(f"ROUTE {decision.route}")
            diagnostics.emit("PROVIDER_CALL_START")
        view = None if views is None else views.get(request.display_id)
        outcomes.append(
            process_unit(
                request,
                providers,
                view=view,
                known_unit_ids=known_unit_ids,
                qa_fn=qa_fn,
                translation_bible=translation_bible,
                allow_semantic_narrow=allow_semantic_narrow,
            )
        )
        if diagnostics is not None:
            outcome = outcomes[-1]
            if outcome.raw is not None:
                diagnostics.emit("PROVIDER_CALL_PASS")
            else:
                diagnostics.emit("PROVIDER_CALL_FAIL", reason=outcome.failure_code)
        outcome = outcomes[-1]
        if outcome.raw is not None and outcome.candidate is not None:
            ensure_requests_artifact(output_dir / "translation_request.jsonl", list(requests))
            _persist_live_unit(
                output_dir,
                outcome,
                source_sha256=source_sha256,
                selection_sha256=selection_sha256,
            )
    contract_pass = [item for item in outcomes if item.raw is not None]
    provider_failures = [item for item in outcomes if item.raw is None]
    if provider_failures:
        report = _report(outcomes, started, order, selection_sha256, source_sha256, raw_sha=None, gate=None)
        write_json_once(output_dir / "canary_report.json", report)
        write_json_once(output_dir / "canary_gate.json", evaluate_canary_gate(report))
        return report

    write_jsonl_once(output_dir / "translation_request.jsonl", list(requests)) if not (output_dir / "translation_request.jsonl").exists() else None
    raw = [item.raw for item in contract_pass]
    if any(item is None for item in raw):
        raise RuntimeError("CANARY_RAW_INCOMPLETE")
    raw_path = output_dir / "translation_raw_response.jsonl"
    if raw_path.exists():
        raw_sha = sha256(raw_path.read_bytes()).hexdigest()
    else:
        raw_sha = persist_checkpoint_raw(raw_path, [item for item in raw if item is not None])
    provenance_path = output_dir / "translation_provider_provenance.jsonl"
    if not provenance_path.exists():
        persist_route_provenance(
            provenance_path,
            [
                candidate_provenance(item.candidate, item.decision, latency_seconds=item.latency_seconds)
                for item in outcomes
                if item.candidate is not None
            ],
        )
    try:
        persist_checkpoint_raw(output_dir / "translation_raw_response.jsonl", [item for item in raw if item is not None])
        immutable_violation = 1
    except ValueError:
        immutable_violation = 0

    raw_qa = [item.qa for item in outcomes]
    if qa_fn is not None and views is not None and known_unit_ids is not None:
        raw_qa = [item.qa or qa_fn(views[item.request.display_id], item.request, item.raw, known_unit_ids) for item in outcomes]
    write_json_once(
        output_dir / "translation_raw_qa_report.json",
        {
            "pass_count": sum(bool(row and row.get("passed")) for row in raw_qa),
            "fail_count": sum(not (row and row.get("passed")) for row in raw_qa),
            "records": raw_qa,
        },
    )
    normalize_response_artifact(
        output_dir / "translation_request.jsonl",
        output_dir / "translation_raw_response.jsonl",
        output_dir / "translation_normalized_response.jsonl",
        output_dir / "translation_normalization_report.json",
    )
    if sha256((output_dir / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [
        TranslationResult.model_validate_json(line)
        for line in (output_dir / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if qa_fn is not None and views is not None and known_unit_ids is not None:
        normalized_qa = [
            qa_fn(views[item.request.display_id], item.request, normalized[index], known_unit_ids)
            for index, item in enumerate(outcomes)
        ]
    else:
        normalized_qa = list(raw_qa)
    write_json_once(
        output_dir / "translation_normalized_qa_report.json",
        {
            "pass_count": sum(bool(row and row.get("passed")) for row in normalized_qa),
            "fail_count": sum(not (row and row.get("passed")) for row in normalized_qa),
            "records": normalized_qa,
        },
    )
    working = [item.working or item.raw for item in outcomes]
    write_jsonl_once(output_dir / "translation_repaired_response.jsonl", [item for item in working if item is not None])

    final_records = []
    if qa_fn is not None and views is not None and known_unit_ids is not None:
        final_records = [
            qa_fn(views[item.request.display_id], item.request, item.working or item.raw, known_unit_ids)
            for item in outcomes
        ]
    else:
        final_records = [
            item.qa
            or {
                "unit_uid": item.request.unit_uid,
                "display_id": item.request.display_id,
                "passed": item.raw is not None and item.final_status == "ACCEPT",
                "diagnostics": [],
            }
            for item in outcomes
        ]
    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = []
    for row in dispatched:
        outcome = next(item for item in outcomes if item.request.unit_uid == row["unit_uid"])
        reviews.append(
            {
                "unit_uid": row["unit_uid"],
                "display_id": row["display_id"],
                "status": _review_status(outcome),
                "issue_types": [issue.code for issue in (outcome.review.issues if outcome.review else [])],
                "explanation": outcome.final_status,
            }
        )
    write_json_once(output_dir / "semantic_review_report.json", semantic_review_summary(reviews, excluded))
    approved = [row["unit_uid"] for row in final_records if row.get("passed") and _review_status(next(item for item in outcomes if item.request.unit_uid == row["unit_uid"])) == "ACCEPT"]
    not_approved = [row["unit_uid"] for row in final_records if row["unit_uid"] not in approved]
    write_json_once(
        output_dir / "translation_final_qa_report.json",
        {
            "pass_count": sum(bool(row.get("passed")) for row in final_records),
            "fail_count": sum(not row.get("passed") for row in final_records),
            "records": final_records,
            "final_approved_unit_uids": approved,
            "not_approved_deterministic_fail_unit_uids": [
                row["unit_uid"] for row in final_records if not row.get("passed")
            ],
            "human_required_unit_uids": [
                item.request.unit_uid for item in outcomes if item.human_required
            ],
            "final_status": "CANARY",
        },
    )
    write_json_once(
        output_dir / "checkpoint_evidence.json",
        {
            "logical_work_orders": order,
            "source_sha256": source_sha256,
            "raw_sha256": raw_sha,
            "isolated": True,
            "production_batch": PRODUCTION_BATCH,
            "production_checkpoint_not_written": PRODUCTION_CHECKPOINT,
            "canary_runtime": CANARY_RUNTIME_VERSION,
        },
    )
    report = _report(
        outcomes,
        started,
        order,
        selection_sha256,
        source_sha256,
        raw_sha=raw_sha,
        gate=None,
        extra={
            "immutable_provenance_violations": immutable_violation,
            "final_qa_pass": sum(bool(row.get("passed")) for row in final_records),
            "source_integrity_violations": sum(_integrity_codes(row)[0] for row in final_records),
            "protected_integrity_violations": sum(_integrity_codes(row)[1] for row in final_records),
        },
    )
    gate = evaluate_canary_gate(report)
    report["gate"] = gate
    write_json_once(output_dir / "canary_report.json", report)
    write_json_once(output_dir / "canary_gate.json", gate)
    return report


def _report(
    outcomes: Sequence[UnitOutcome],
    started: datetime,
    order: Sequence[str],
    selection_sha256: str | None,
    source_sha256: str | None,
    *,
    raw_sha: str | None,
    gate: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    reviews = Counter(
        (item.review.verdict if item.review is not None else "NOT_REVIEWED") for item in outcomes
    )
    routes = Counter(item.decision.route for item in outcomes)
    usage_rows = [item.usage for item in outcomes if item.usage]
    latencies = [item.latency_seconds for item in outcomes if item.latency_seconds is not None]
    unresolved_repair = sum(
        1
        for item in outcomes
        if item.review is not None
        and item.review.verdict == "REPAIR"
        and not item.repair_applied
        and semantic_terminal_of_outcome(item) not in SEMANTIC_PASS_TERMINALS
    )
    silent = unresolved_repair
    terminals = Counter(semantic_terminal_of_outcome(item) for item in outcomes)
    human_approved = terminals.get("HUMAN_APPROVED", 0)
    human_rejected = terminals.get("HUMAN_REJECTED", 0)
    human_required_unresolved = terminals.get("HUMAN_REQUIRED", 0)
    model_accepted = terminals.get("MODEL_ACCEPTED", 0)
    human_escalation = human_approved + human_rejected + human_required_unresolved
    report: dict[str, Any] = {
        "artifact_type": "QWEN_DETERMINISTIC_CANARY_REPORT",
        "schema_version": 1,
        "runtime": CANARY_RUNTIME_VERSION,
        "isolated": True,
        "frozen_order": list(order),
        "unit_count": len(outcomes),
        "checkpoint_size": CHECKPOINT_SIZE,
        "selection_sha256": selection_sha256,
        "source_sha256": source_sha256,
        "raw_sha256": raw_sha,
        "route_distribution": dict(routes),
        "provider_contract_pass": sum(item.raw is not None for item in outcomes),
        "provider_contract_fail": sum(item.raw is None for item in outcomes),
        "deterministic_qa_pass": sum(bool(item.qa and item.qa.get("passed")) for item in outcomes),
        "final_qa_pass": extra.get("final_qa_pass", 0) if extra else 0,
        "review_accept": reviews.get("ACCEPT", 0),
        "review_repair": reviews.get("REPAIR", 0),
        "review_human_required": reviews.get("HUMAN_REQUIRED", 0) + sum(item.human_required for item in outcomes if item.review is None),
        "repair_success": sum(
            item.repair_applied and semantic_terminal_of_outcome(item) in SEMANTIC_PASS_TERMINALS for item in outcomes
        ),
        "unresolved_repair": unresolved_repair,
        "silent_unresolved_semantic": silent,
        "human_required": human_required_unresolved,
        "unresolved_human_required": human_required_unresolved,
        "semantic_terminal_model_accepted": model_accepted,
        "semantic_terminal_human_approved": human_approved,
        "semantic_terminal_human_rejected": human_rejected,
        "semantic_terminal_unresolved": human_required_unresolved + terminals.get("UNRESOLVED", 0),
        "human_approved_count": human_approved,
        "human_rejected_count": human_rejected,
        "human_escalation_count": human_escalation,
        "human_escalations_per_1000": round(human_escalation / len(outcomes) * 1000, 4) if outcomes else 0,
        "number_failures": _code_count(outcomes, "NUMBER_PRESERVATION"),
        "url_failures": _code_count(outcomes, "URL_PRESERVATION"),
        "doi_failures": _code_count(outcomes, "DOI_PRESERVATION"),
        "d050_failures": sum(
            1
            for item in outcomes
            if item.request.footnote_reference_numbers and item.qa and "NUMBER_PRESERVATION" in qa_codes(item.qa)
        ),
        "source_integrity_violations": extra.get("source_integrity_violations", 0) if extra else 0,
        "protected_integrity_violations": extra.get("protected_integrity_violations", 0) if extra else 0,
        "immutable_provenance_violations": extra.get("immutable_provenance_violations", 0) if extra else 0,
        "review_not_persisted": extra.get(
            "review_not_persisted",
            sum(item.review is None and item.raw is not None and not item.blocked for item in outcomes),
        )
        if extra
        else sum(item.review is None and item.raw is not None and not item.blocked for item in outcomes),
        "reexecution_required": extra.get("reexecution_required", 0) if extra else 0,
        "latency": {
            "sample_count": len(latencies),
            "total_seconds": round(sum(latencies), 6) if latencies else 0,
            "mean_seconds": round(sum(latencies) / len(latencies), 6) if latencies else None,
            "wall_seconds": round(elapsed, 6),
        },
        "usage": {
            "prompt_tokens": sum(int(row.get("prompt_tokens") or row.get("input_tokens") or 0) for row in usage_rows),
            "completion_tokens": sum(int(row.get("completion_tokens") or row.get("output_tokens") or 0) for row in usage_rows),
            "total_tokens": sum(int(row.get("total_tokens") or 0) for row in usage_rows),
        },
        "estimated_throughput_units_per_hour": round(len(outcomes) / elapsed * 3600, 2) if elapsed > 0 else None,
        "unit_results": [
            {
                "display_id": item.request.display_id,
                "unit_uid": item.request.unit_uid,
                "route": item.decision.route,
                "final_status": item.final_status,
                "failure_family": item.failure_family,
                "failure_code": item.failure_code,
                "repair_applied": item.repair_applied,
                "human_required": item.human_required,
                "semantic_terminal": semantic_terminal_of_outcome(item),
                "review_status": _unit_review_status(item),
                "recovered_from": item.recovered_from,
                "reexecution_required": False,
            }
            for item in outcomes
        ],
    }
    if extra:
        report["recovery"] = {
            "resumed": bool(extra.get("resumed")),
            "api_calls": extra.get("api_calls") or 0,
            "recovered_from_raw": extra.get("recovered_from_raw", 0),
            "recovered_from_unit": extra.get("recovered_from_unit", 0),
        }
        if extra.get("provider_latency") is not None:
            report["latency"]["provider_latency"] = extra["provider_latency"]
        if extra.get("usage") is not None:
            report["usage"] = extra["usage"]
    if gate is not None:
        report["gate"] = dict(gate)
    return report


def _code_count(outcomes: Sequence[UnitOutcome], code: str) -> int:
    return sum(1 for item in outcomes if item.qa and code in qa_codes(item.qa))
