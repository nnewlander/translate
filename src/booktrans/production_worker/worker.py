"""Deterministic production worker entrypoints.  Codex is not on this path."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence

from hashlib import sha256

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.ir import DocumentIR, DocumentStoryIR
from booktrans.domain.models import SourceSnapshot
from booktrans.domain.translation_view import TranslationView
from booktrans.ooxml.translation_view import TranslationViewBuilder
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import normalize_response_artifact
from booktrans.providers.qwen_max import QwenMaxProvider, QwenMaxRepairProvider, QwenMaxReviewerProvider
from booktrans.providers.qwen_mt import QwenMTProvider
from booktrans.production_worker.artifacts import (
    candidate_provenance,
    write_json_once,
    write_json_replace,
    write_jsonl_once,
)
from booktrans.production_worker.canary import (
    PRODUCTION_BATCH,
    PRODUCTION_CHECKPOINT,
    SEMANTIC_PASS_TERMINALS,
    run_isolated_canary,
    semantic_terminal_of_outcome,
)
from booktrans.production_worker.continuation import continue_canary_from_raw, recalculate_canary_from_persisted_evidence
from booktrans.production_worker.semantic_review_run import (
    review_canary_semantic_group_repair,
    review_canary_units_with_semantic_context,
    review_corrected_second_review_only,
)
from booktrans.production_worker.diagnostics import DEFAULT_WATCHDOG_SECONDS, CanaryDiagnostics
from booktrans.production_worker.handoff import write_worker_handoff
from booktrans.production_worker.pipeline import WorkerProviders, process_unit, translate_unit, enrich_request
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.unit_store import (
    append_jsonl_fsync,
    append_raw_response_line,
    ensure_requests_artifact,
    load_raw_by_uid,
    load_unit_results,
    persist_unit_result,
    unit_path,
    resolve_effective_translation_request,
)
from booktrans.production_worker.selection import (
    DEFAULT_BLOCKS,
    DEFAULT_VIEWS,
    EXPECTED_SOURCE_SHA,
    FrozenSelectionError,
    frozen_checkpoint_order,
    load_checkpoint_requests,
    load_json,
    load_jsonl_models,
    load_selected_jsonl_by_display_id,
    load_unit_uids_from_jsonl,
    production_checkpoint_path,
    selection_sha256,
    d050_paths_for_checkpoint,
    load_checkpoint_d050_registry,
)


ROOT = Path(r"D:\translate")
DEFAULT_RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"


class ResumeForbiddenProviders:
    """Fail closed when a resume path is not allowed to call providers."""

    def translate(self, request):
        raise RuntimeError("TRANSLATION_API_FORBIDDEN_ON_RESUME")

    def translate_structured(self, request):
        raise RuntimeError("TRANSLATION_API_FORBIDDEN_ON_RESUME")

    def review_with_provenance(self, *args, **kwargs):
        raise RuntimeError("PROVIDER_API_FORBIDDEN_ON_RESUME")

    def repair(self, *args, **kwargs):
        raise RuntimeError("PROVIDER_API_FORBIDDEN_ON_RESUME")


def providers_from_environment() -> WorkerProviders:
    mt = QwenMTProvider.from_environment()
    structured = QwenMaxProvider.from_environment()
    return WorkerProviders(
        mt=mt,
        structured=structured,
        reviewer=QwenMaxReviewerProvider.from_environment(),
        repair=QwenMaxRepairProvider.from_environment(),
    )


def load_views_and_blocks(*, views_path: Path = DEFAULT_VIEWS, blocks_path: Path = DEFAULT_BLOCKS) -> tuple[dict[str, TranslationView], dict[str, BlockIR]]:
    views = {item.display_id: item for item in load_jsonl_models(views_path, TranslationView)}
    blocks = {item.display_id: item for item in load_jsonl_models(blocks_path, BlockIR)}
    return views, blocks


def frozen_b022_checkpoint03_inputs(run: Path, *, source_path: Path = SOURCE) -> dict[str, Any]:
    """Compatibility entry point for the isolated checkpoint_03 canary."""
    return frozen_b022_checkpoint_inputs(run, checkpoint=PRODUCTION_CHECKPOINT, source_path=source_path)


def frozen_b022_checkpoint_inputs(
    run: Path, *, checkpoint: str, source_path: Path = SOURCE
) -> dict[str, Any]:
    """Compatibility wrapper for the historical B022 frozen-selection loader."""
    return frozen_batch_checkpoint_inputs(
        run, batch_label=PRODUCTION_BATCH, checkpoint=checkpoint, source_path=source_path
    )


def frozen_batch_checkpoint_inputs(
    run: Path, *, batch_label: str, checkpoint: str, source_path: Path = SOURCE
) -> dict[str, Any]:
    """Load one exact active-batch checkpoint slice without deriving its cursor.

    The production checkpoint writer supplies the authoritative checkpoint from
    the rebuilt campaign state.  This loader only maps that fixed 25-item
    selection slice to the immutable DOCX/IR inputs and exact D050 registry.
    """
    source_sha = fingerprint(source_path).sha256
    if source_sha != EXPECTED_SOURCE_SHA:
        raise FrozenSelectionError("SOURCE_MUTATED")
    match = re.fullmatch(r"FULL_BOOK_BATCH_(\d{3})", batch_label)
    if not match:
        raise FrozenSelectionError("ACTIVE_BATCH_ID_INVALID")
    batch_suffix = match.group(1)
    batch_dir = run / f"full_book_batch_{batch_suffix}"
    selection_path = run / f"full_book_batch_{batch_suffix}_selection.json"
    selection = load_json(selection_path)
    if selection.get("batch") != batch_label:
        raise FrozenSelectionError("FROZEN_SELECTION_BATCH_MISMATCH")
    views, blocks = load_views_and_blocks()
    order, requests = load_checkpoint_requests(
        selection=selection,
        checkpoint=checkpoint,
        views=views,
        blocks=blocks,
        source_sha256=source_sha,
        d050_paths=d050_paths_for_checkpoint(run, checkpoint, batch_label=batch_label),
        selection_categories=["full_book_production", f"full_book_batch_{batch_suffix}", checkpoint],
    )
    if not order:
        raise FrozenSelectionError("FROZEN_SELECTION_CURSOR_MISMATCH")
    return {
        "batch_label": batch_label,
        "selection": selection,
        "selection_path": selection_path,
        "selection_sha256": selection_sha256(selection_path),
        "source_sha256": source_sha,
        "order": order,
        "requests": requests,
        "views": views,
        "blocks": blocks,
        "known_unit_ids": {view.unit_uid for view in views.values()},
        "bible": batch_dir / "translation_bible_snapshot.md",
    }


def fresh_checkpoint_requests_from_current_view(
    run: Path, *, batch_label: str, checkpoint: str, source_path: Path = SOURCE,
    frozen_inputs: dict[str, Any] | None = None,
) -> tuple[dict[str, TranslationView], list[CalibrationRequest]]:
    """Derive a never-before-persisted request from the current OOXML view.

    Frozen selection, block identity, D050 routing, and request schema remain
    the authority.  Only the derived TranslationView is refreshed before the
    first create-once request artifact is born, so a known structural mapper
    correction is present from the start rather than requiring a later
    supersession.
    """
    loaded = frozen_inputs or frozen_batch_checkpoint_inputs(
        run, batch_label=batch_label, checkpoint=checkpoint, source_path=source_path,
    )
    snapshot = fingerprint(source_path)
    stories: dict[tuple[str, str], list[BlockIR]] = {}
    # Neighbour context is document-scoped.  Rebuild every immutable block,
    # then select this checkpoint's views, so a fresh request does not acquire
    # an artificial boundary change merely because its checkpoint starts or
    # ends between two physical paragraphs.
    for block in loaded["blocks"].values():
        stories.setdefault((block.story, block.part_name), []).append(block)
    document = DocumentIR(
        document_sha256=snapshot.sha256,
        source=SourceSnapshot(
            path=str(source_path), sha256=snapshot.sha256,
            size_bytes=snapshot.size_bytes, mtime_utc=snapshot.mtime_utc,
        ),
        extracted_at_utc=datetime.now(timezone.utc),
        stories=[
            DocumentStoryIR(story=story, part_name=part, blocks=blocks)
            for (story, part), blocks in stories.items()
        ],
    )
    rebuilt_views = {
        view.display_id: view
        for view in TranslationViewBuilder().build(source_path, document)
    }
    views = {display_id: rebuilt_views[display_id] for display_id in loaded["order"]}
    suffix = batch_label.removeprefix("FULL_BOOK_BATCH_")
    _, requests = load_checkpoint_requests(
        selection=loaded["selection"], checkpoint=checkpoint, views=views,
        blocks=loaded["blocks"], source_sha256=str(loaded["source_sha256"]),
        d050_paths=d050_paths_for_checkpoint(run, checkpoint, batch_label=batch_label),
        selection_categories=["full_book_production", f"full_book_batch_{suffix}", checkpoint],
    )
    return views, requests


def current_batch_checkpoint_inputs(
    run: Path, *, batch_label: str, checkpoint: str, source_path: Path = SOURCE
) -> dict[str, Any]:
    """Compose frozen evidence with the sole effective-request resolver.

    ``frozen_batch_checkpoint_inputs`` remains an evidence loader.  This
    entry point is for current-state projection only and introduces no new
    request authority.
    """
    loaded = frozen_batch_checkpoint_inputs(run, batch_label=batch_label, checkpoint=checkpoint, source_path=source_path)
    checkpoint_dir = production_checkpoint_path(run, batch_label, checkpoint)
    manifest_path = checkpoint_dir.parent / "batch_manifest.json"
    effective_path, requests = resolve_effective_translation_request(
        checkpoint_dir=checkpoint_dir, source_sha256=str(loaded["source_sha256"]), batch_id=batch_label,
        checkpoint_id=checkpoint, selection_sha256=str(loaded["selection_sha256"]),
        manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(), expected_order=list(loaded["order"]),
    )
    loaded["requests"] = requests
    loaded["effective_translation_request_path"] = effective_path
    # Views are derived execution input.  Rebuild only the selected slice
    # where the frozen historical mapper no longer binds the effective request.
    def structural_binding(request: CalibrationRequest) -> tuple[object, ...]:
        """Existing request fields that bind a view to current execution.

        This deliberately compares only exporter-established fields.  D050
        evidence and selection labels belong to the checkpoint overlay, not
        to the paragraph's structural view.
        """
        return (
            request.source_sha256,
            request.unit_uid,
            request.display_id,
            request.source_text,
            tuple((segment.kind, segment.text, segment.anchor_ref, segment.island_ref)
                  for segment in request.source_segments),
            tuple((anchor.anchor_id, tuple(anchor.semantics), anchor.protected,
                   anchor.protected_text, anchor.container)
                  for anchor in request.available_anchors),
        )
    rebuilt = 0
    selected_blocks = [loaded["blocks"][display_id] for display_id in loaded["order"]]
    snapshot = fingerprint(source_path)
    stories: dict[tuple[str, str], list[BlockIR]] = {}
    for block in selected_blocks:
        stories.setdefault((block.story, block.part_name), []).append(block)
    document = DocumentIR(
        document_sha256=snapshot.sha256,
        source=SourceSnapshot(path=str(source_path), sha256=snapshot.sha256, size_bytes=snapshot.size_bytes, mtime_utc=snapshot.mtime_utc),
        extracted_at_utc=datetime.now(timezone.utc),
        stories=[DocumentStoryIR(story=story, part_name=part, blocks=blocks) for (story, part), blocks in stories.items()],
    )
    rebuilt_views = {view.display_id: view for view in TranslationViewBuilder().build(source_path, document)}
    from booktrans.production_worker.selection import build_calibration_request
    for request in requests:
        frozen_request = build_calibration_request(loaded["views"][request.display_id], loaded["blocks"][request.display_id], source_sha256=str(loaded["source_sha256"]))
        if structural_binding(frozen_request) == structural_binding(request):
            continue
        view = rebuilt_views.get(request.display_id)
        if view is None:
            raise FrozenSelectionError("CURRENT_VIEW_REBUILD_MISSING")
        rebuilt_request = build_calibration_request(view, loaded["blocks"][request.display_id], source_sha256=str(loaded["source_sha256"]))
        if structural_binding(rebuilt_request) != structural_binding(request):
            raise FrozenSelectionError("CURRENT_VIEW_EFFECTIVE_REQUEST_MISMATCH")
        loaded["views"][request.display_id] = view
        rebuilt += 1
    loaded["current_view_rebuilt_count"] = rebuilt
    loaded["current_view_frozen_reuse_count"] = len(requests) - rebuilt
    return loaded


def continuation_inputs(run: Path, isolated_output: Path, *, source_path: Path = SOURCE) -> dict[str, Any]:
    """Load frozen identity plus persisted canary requests.  Does not load blocks.jsonl."""
    source_sha = fingerprint(source_path).sha256
    if source_sha != EXPECTED_SOURCE_SHA:
        raise FrozenSelectionError("SOURCE_MUTATED")
    selection_path = run / "full_book_batch_022_selection.json"
    selection = load_json(selection_path)
    order = frozen_checkpoint_order(selection, PRODUCTION_CHECKPOINT)
    if order[0] != "body.p.06939":
        raise FrozenSelectionError("FROZEN_SELECTION_CURSOR_MISMATCH")
    request_path = isolated_output / "translation_request.jsonl"
    if not request_path.is_file():
        raise FrozenSelectionError(f"CANARY_REQUEST_MISSING:{request_path}")
    requests = load_jsonl_models(request_path, CalibrationRequest)
    if [item.display_id for item in requests] != order:
        raise FrozenSelectionError("CANARY_REQUEST_ORDER_MISMATCH")
    views = load_selected_jsonl_by_display_id(DEFAULT_VIEWS, TranslationView, order)
    if any(display_id not in views for display_id in order):
        raise FrozenSelectionError("FROZEN_SOURCE_UNITS_MISSING")
    return {
        "selection": selection,
        "selection_path": selection_path,
        "selection_sha256": selection_sha256(selection_path),
        "source_sha256": source_sha,
        "order": order,
        "requests": requests,
        "views": views,
        "known_unit_ids": load_unit_uids_from_jsonl(DEFAULT_VIEWS),
        "bible": run / "full_book_batch_022" / "translation_bible_snapshot.md",
    }


def run_canary_command(
    *,
    root: Path,
    run: Path,
    isolated_output: Path,
    providers: WorkerProviders | None = None,
    source_path: Path = SOURCE,
    watchdog_seconds: float = DEFAULT_WATCHDOG_SECONDS,
    require_provider_env: bool = True,
    continue_from_raw: bool = False,
    resume: bool = False,
    recalculate_from_evidence: bool = False,
    semantic_review: bool = False,
    semantic_review_ids: Sequence[str] | None = None,
    corrected_second_review: bool = False,
    group_repair: bool = False,
    human_adjudicate: bool = False,
    human_adjudication_path: Path | None = None,
) -> dict[str, Any]:
    continue_from_raw = continue_from_raw or resume
    isolated_output.mkdir(parents=True, exist_ok=True)
    diagnostics = CanaryDiagnostics(isolated_output, watchdog_seconds=watchdog_seconds)
    diagnostics.arm_watchdog()
    try:
        mode = (
            "human-adjudicate"
            if human_adjudicate
            else (
            "group-repair"
            if group_repair
            else (
            "corrected-second-review"
            if corrected_second_review
            else ("semantic-review" if semantic_review else ("continue-from-raw" if continue_from_raw else ("recalculate" if recalculate_from_evidence else "fresh")))))
        )
        diagnostics.emit("CANARY_START", mode=mode)
        d050_paths = d050_paths_for_checkpoint(run, PRODUCTION_CHECKPOINT)
        d050_registry = load_checkpoint_d050_registry(run, PRODUCTION_CHECKPOINT)
        diagnostics.emit("D050_PREFLIGHT_PASS", registries=len(d050_paths), records=len(d050_registry))
        production_checkpoint = production_checkpoint_path(run, PRODUCTION_BATCH, PRODUCTION_CHECKPOINT)
        if production_checkpoint.exists():
            raise FrozenSelectionError(f"PRODUCTION_CHECKPOINT_ALREADY_EXISTS:{production_checkpoint}")
        if human_adjudicate:
            from booktrans.production_worker.human_adjudication import (
                adjudication_from_mapping,
                apply_human_adjudication_to_canary,
                prepare_canary_group_members,
            )

            payload = continuation_inputs(run, isolated_output, source_path=source_path)
            if human_adjudication_path is None or not Path(human_adjudication_path).is_file():
                raise FrozenSelectionError("HUMAN_ADJUDICATION_PATH_REQUIRED")
            raw_payload = json.loads(Path(human_adjudication_path).read_text(encoding="utf-8"))
            adjudication = adjudication_from_mapping(raw_payload)
            current_id = next(
                (
                    item
                    for item in adjudication.member_work_order_ids
                    if (isolated_output / "group_reviews" / f"{item}.json").is_file()
                ),
                adjudication.member_work_order_ids[-1],
            )
            diagnostics.emit("HUMAN_ADJUDICATION", id=current_id, decision=adjudication.decision)
            group, members, _raws = prepare_canary_group_members(
                output_dir=isolated_output,
                production_run=run,
                requests=payload["requests"],
                frozen_order=payload["order"],
                current_display_id=current_id,
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            apply_report = apply_human_adjudication_to_canary(
                output_dir=isolated_output,
                production_run=run,
                requests=payload["requests"],
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                adjudication=adjudication,
                group=group,
                members=members,
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            diagnostics.emit("RECALCULATE_FROM_EVIDENCE", units=len(payload["requests"]))
            report = recalculate_canary_from_persisted_evidence(
                output_dir=isolated_output,
                requests=payload["requests"],
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            report["human_adjudication"] = apply_report
            report["translation_api_calls"] = 0
            report["reviewer_api_calls"] = 0
            report["repair_api_calls"] = 0
            write_json_replace(isolated_output / "canary_report.json", report)
            write_json_replace(isolated_output / "canary_continuation_report.json", report)
            diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
            return report
        if group_repair:
            payload = continuation_inputs(run, isolated_output, source_path=source_path)
            active = providers
            if active is None:
                if not require_provider_env:
                    raise FrozenSelectionError("PROVIDER_ENV_REQUIRED")
                env = providers_from_environment()
                from booktrans.production_worker.continuation import ForbiddenTranslationProvider

                active = WorkerProviders(
                    mt=ForbiddenTranslationProvider(),
                    structured=ForbiddenTranslationProvider(),
                    reviewer=env.reviewer,
                    repair=env.repair,
                )
            targets = list(semantic_review_ids or ("body.p.06968",))
            diagnostics.emit("GROUP_REPAIR", units=len(targets), ids=",".join(targets))
            review_report = review_canary_semantic_group_repair(
                output_dir=isolated_output,
                requests=payload["requests"],
                providers=active,
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                display_ids=targets,
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
                diagnostics=diagnostics,
            )
            write_json_replace(isolated_output / "canary_group_repair_report.json", review_report)
            diagnostics.emit("RECALCULATE_FROM_EVIDENCE", units=len(payload["requests"]))
            report = recalculate_canary_from_persisted_evidence(
                output_dir=isolated_output,
                requests=payload["requests"],
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            report["group_repair"] = review_report
            report["translation_api_calls"] = 0
            report["reviewer_api_calls"] = int(review_report.get("reviewer_api_calls") or 0)
            report["repair_api_calls"] = int(review_report.get("repair_api_calls") or 0)
            write_json_replace(isolated_output / "canary_report.json", report)
            write_json_replace(isolated_output / "canary_continuation_report.json", report)
            diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
            return report
        if corrected_second_review:
            payload = continuation_inputs(run, isolated_output, source_path=source_path)
            active = providers
            if active is None:
                if not require_provider_env:
                    raise FrozenSelectionError("PROVIDER_ENV_REQUIRED")
                env = providers_from_environment()
                from booktrans.production_worker.continuation import ForbiddenTranslationProvider

                active = WorkerProviders(
                    mt=ForbiddenTranslationProvider(),
                    structured=ForbiddenTranslationProvider(),
                    reviewer=env.reviewer,
                    repair=env.repair,
                )
            targets = list(semantic_review_ids or ("body.p.06968",))
            diagnostics.emit("CORRECTED_SECOND_REVIEW", units=len(targets), ids=",".join(targets))
            review_report = review_corrected_second_review_only(
                output_dir=isolated_output,
                requests=payload["requests"],
                providers=active,
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                display_ids=targets,
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
                diagnostics=diagnostics,
            )
            write_json_replace(isolated_output / "canary_corrected_second_review_report.json", review_report)
            diagnostics.emit("RECALCULATE_FROM_EVIDENCE", units=len(payload["requests"]))
            report = recalculate_canary_from_persisted_evidence(
                output_dir=isolated_output,
                requests=payload["requests"],
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            report["corrected_second_review"] = review_report
            report["translation_api_calls"] = 0
            report["reviewer_api_calls"] = int(review_report.get("reviewer_api_calls") or 0)
            report["repair_api_calls"] = 0
            write_json_replace(isolated_output / "canary_report.json", report)
            write_json_replace(isolated_output / "canary_continuation_report.json", report)
            diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
            return report
        if semantic_review:
            payload = continuation_inputs(run, isolated_output, source_path=source_path)
            active = providers
            if active is None:
                if not require_provider_env:
                    raise FrozenSelectionError("PROVIDER_ENV_REQUIRED")
                env = providers_from_environment()
                from booktrans.production_worker.continuation import ForbiddenTranslationProvider

                active = WorkerProviders(
                    mt=ForbiddenTranslationProvider(),
                    structured=ForbiddenTranslationProvider(),
                    reviewer=env.reviewer,
                    repair=env.repair,
                )
            targets = list(semantic_review_ids or ("body.p.06968",))
            diagnostics.emit("SEMANTIC_REVIEW_GROUP", units=len(targets), ids=",".join(targets))
            review_report = review_canary_units_with_semantic_context(
                output_dir=isolated_output,
                requests=payload["requests"],
                providers=active,
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                display_ids=targets,
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
                diagnostics=diagnostics,
            )
            write_json_replace(isolated_output / "canary_semantic_review_group_report.json", review_report)
            write_json_replace(isolated_output / "canary_semantic_review_report.json", review_report)
            diagnostics.emit("RECALCULATE_FROM_EVIDENCE", units=len(payload["requests"]))
            report = recalculate_canary_from_persisted_evidence(
                output_dir=isolated_output,
                requests=payload["requests"],
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            report["semantic_review"] = review_report
            report["translation_api_calls"] = 0
            report["reviewer_api_calls"] = int(review_report.get("reviewer_api_calls") or 0)
            report["repair_api_calls"] = int(review_report.get("repair_api_calls") or 0)
            write_json_replace(isolated_output / "canary_report.json", report)
            write_json_replace(isolated_output / "canary_continuation_report.json", report)
            diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
            return report
        if recalculate_from_evidence:
            payload = continuation_inputs(run, isolated_output, source_path=source_path)
            diagnostics.emit("RECALCULATE_FROM_EVIDENCE", units=len(payload["requests"]))
            report = recalculate_canary_from_persisted_evidence(
                output_dir=isolated_output,
                requests=payload["requests"],
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                d050_registry=d050_registry,
            )
            diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
            return report
        if continue_from_raw:
            diagnostics.emit("LOAD_FROZEN_IDENTITY")
            payload = continuation_inputs(run, isolated_output, source_path=source_path)
            active = providers
            if active is None:
                if not require_provider_env:
                    raise FrozenSelectionError("PROVIDER_ENV_REQUIRED")
                env = providers_from_environment()
                from booktrans.production_worker.continuation import ForbiddenTranslationProvider

                active = WorkerProviders(
                    mt=ForbiddenTranslationProvider(),
                    structured=ForbiddenTranslationProvider(),
                    reviewer=env.reviewer,
                    repair=env.repair,
                )
            diagnostics.emit("CONTINUE_FROM_RAW", units=len(payload["requests"]))
            report = continue_canary_from_raw(
                output_dir=isolated_output,
                requests=payload["requests"],
                providers=active,
                production_run=run,
                views=payload["views"],
                known_unit_ids=payload["known_unit_ids"],
                qa_fn=harness_qa_record,
                translation_bible=payload["bible"] if payload["bible"].is_file() else None,
                frozen_order=payload["order"],
                selection_sha256=payload["selection_sha256"],
                source_sha256=payload["source_sha256"],
                selection_ids=payload["order"],
                diagnostics=diagnostics,
                d050_registry=d050_registry,
            )
            diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
            return report
        payload = frozen_b022_checkpoint03_inputs(run, source_path=source_path)
        report = run_isolated_canary(
            output_dir=isolated_output,
            requests=payload["requests"],
            providers=providers or providers_from_environment(),
            production_run=run,
            views=payload["views"],
            known_unit_ids=payload["known_unit_ids"],
            qa_fn=harness_qa_record,
            translation_bible=payload["bible"] if payload["bible"].is_file() else None,
            frozen_order=payload["order"],
            selection_sha256=payload["selection_sha256"],
            source_sha256=payload["source_sha256"],
            diagnostics=diagnostics,
        )
        diagnostics.complete(status="GATE_PASS" if report.get("gate", {}).get("passed") else "GATE_FAIL")
        return report
    except Exception as error:
        diagnostics.fail("CANARY_FAILED", str(error))
        raise
    finally:
        diagnostics.cancel_watchdog()


def run_production_checkpoint(
    *,
    root: Path,
    run: Path,
    worker_id: str,
    handoff_path: Path,
    authorize: bool,
    providers: WorkerProviders | None = None,
    payload: dict[str, Any] | None = None,
    semantic_rescue: bool = False,
    human_adjudication_path: Path | None = None,
    d051_approval_path: Path | None = None,
    d051_reuse_approval_path: Path | None = None,
    d051_reuse_display_id: str | None = None,
    review_policy: str = "legacy",
    batch_label: str | None = None,
    checkpoint: str | None = None,
) -> int:
    """Seal the current cursor checkpoint.  Requires an explicit authorization flag."""
    from booktrans.production_worker.production_checkpoint import run_production_checkpoint as _run

    return _run(
        root=root,
        run=run,
        worker_id=worker_id,
        handoff_path=handoff_path,
        authorize=authorize,
        providers=providers,
        payload=payload,
        semantic_rescue=semantic_rescue,
        human_adjudication_path=human_adjudication_path,
        d051_approval_path=d051_approval_path,
        d051_reuse_approval_path=d051_reuse_approval_path,
        d051_reuse_display_id=d051_reuse_display_id,
        review_policy=review_policy,
        launch_batch_label=batch_label,
        launch_checkpoint=checkpoint,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Qwen production worker. Codex is not invoked.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-path", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--worker-id", default=os.environ.get("BOOKTRANS_PRODUCTION_WORKER_ID", ""))
    parser.add_argument("--handoff-path", type=Path, default=None)
    parser.add_argument(
        "--batch-label",
        default=os.environ.get("BOOKTRANS_PRODUCTION_BATCH"),
        help="Explicit supervisor-authorized production batch; never inferred from handoff history.",
    )
    parser.add_argument(
        "--checkpoint",
        default=os.environ.get("BOOKTRANS_PRODUCTION_CHECKPOINT"),
        help="Explicit supervisor-authorized production checkpoint paired with --batch-label.",
    )
    parser.add_argument("--mode", choices=("canary", "production"), default="canary")
    parser.add_argument("--review-policy", choices=("legacy", "v2_fastpath"), default="v2_fastpath")
    parser.add_argument("--isolated-output", type=Path, default=None)
    parser.add_argument("--authorize-production-checkpoint-write", action="store_true")
    parser.add_argument("--semantic-rescue", action="store_true", help="One bounded RAW-only semantic rescue for current production HUMAN_REQUIRED units.")
    parser.add_argument("--d051-approval-path", type=Path, default=None, help="Approved exact D051 evidence; never calls Translation.")
    parser.add_argument("--d051-reuse-approval-path", type=Path, default=None, help="Prior exact D051 approval to rebind for one identical current candidate.")
    parser.add_argument("--d051-reuse-display-id", default=None, help="Current work order receiving the exact D051 reuse.")
    parser.add_argument("--watchdog-seconds", type=float, default=DEFAULT_WATCHDOG_SECONDS)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume isolated canary from persisted artifacts without translation API calls.",
    )
    parser.add_argument(
        "--continue-from-raw",
        action="store_true",
        help="Continue isolated canary from persisted RAW. Never calls translation providers.",
    )
    parser.add_argument(
        "--recalculate-from-evidence",
        action="store_true",
        help="Recompute Canary from persisted RAW/review/repair with D050-aware QA. No provider calls.",
    )
    parser.add_argument(
        "--semantic-review",
        action="store_true",
        help="Review named units from RAW with SemanticReviewGroup context. Never calls translation providers.",
    )
    parser.add_argument(
        "--semantic-review-ids",
        help="Comma-separated logical work order IDs for group-aware semantic review.",
    )
    parser.add_argument(
        "--corrected-second-review",
        action="store_true",
        help="Re-run only the second review using the repaired SemanticReviewGroup span. Never translates or repairs.",
    )
    parser.add_argument(
        "--group-repair",
        action="store_true",
        help="One Semantic Group Repair for unsealed continuation members. Never calls translation providers.",
    )
    parser.add_argument(
        "--human-adjudicate",
        action="store_true",
        help="Apply a HumanSemanticAdjudication contract. Never calls translation, reviewer, or repair providers.",
    )
    parser.add_argument(
        "--human-adjudication-path",
        type=Path,
        default=None,
        help="Path to a HumanSemanticAdjudication JSON document.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handoff = args.handoff_path or Path(os.environ.get("BOOKTRANS_PRODUCTION_WORKER_HANDOFF_PATH", args.run_path / "production_campaign_worker_handoff.json"))
    if args.mode == "canary":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = args.isolated_output or (args.root / "runs" / f"qwen-production-canary-{stamp}")
        report = run_canary_command(
            root=args.root,
            run=args.run_path,
            isolated_output=output,
            continue_from_raw=args.continue_from_raw,
            resume=args.resume,
            recalculate_from_evidence=args.recalculate_from_evidence,
            semantic_review=args.semantic_review,
            semantic_review_ids=[item.strip() for item in str(args.semantic_review_ids or "").split(",") if item.strip()],
            corrected_second_review=args.corrected_second_review,
            group_repair=args.group_repair,
            human_adjudicate=args.human_adjudicate,
            human_adjudication_path=args.human_adjudication_path,
            watchdog_seconds=args.watchdog_seconds,
        )
        print(json.dumps({"output": str(output), "gate": report.get("gate")}, ensure_ascii=False), flush=True)
        return 0 if report.get("gate", {}).get("passed") else 2
    if not args.worker_id:
        raise SystemExit("WORKER_ID_REQUIRED")
    if bool(args.batch_label) != bool(args.checkpoint):
        raise SystemExit("WORKER_LAUNCH_BINDING_INCOMPLETE")
    return run_production_checkpoint(
        root=args.root,
        run=args.run_path,
        worker_id=args.worker_id,
        handoff_path=handoff,
        authorize=args.authorize_production_checkpoint_write,
        semantic_rescue=args.semantic_rescue,
        human_adjudication_path=args.human_adjudication_path,
        d051_approval_path=args.d051_approval_path,
        d051_reuse_approval_path=args.d051_reuse_approval_path,
        d051_reuse_display_id=args.d051_reuse_display_id,
        review_policy=args.review_policy,
        batch_label=args.batch_label,
        checkpoint=args.checkpoint,
    )
