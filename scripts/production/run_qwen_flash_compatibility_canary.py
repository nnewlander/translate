"""Small, isolated live compatibility gate for the Flash general model.

This is intentionally not a production entry point: it reads frozen source/IR
facts, makes a handful of provider calls, and writes a single immutable report
outside the production run.  It never loads a campaign cursor, appends RAW, or
creates a checkpoint.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any


ROOT = Path(r"D:\translate")
sys.path.insert(0, str(ROOT / "src"))

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.source_integrity import fingerprint
from booktrans.providers.qwen_max import (
    ApprovedRepairContract,
    QWEN_MAX,
    QwenMaxProvider,
    QwenMaxRepairProvider,
    QwenMaxReviewerProvider,
)
from booktrans.providers.qwen_mt import QWEN_MT_PLUS, QwenMTProvider
from booktrans.providers.translation_provider import TranslationProviderRequest
from booktrans.production_worker.semantic_review_group import build_semantic_review_group
from booktrans.production_worker.reviewer_context import units_from_blocks
from booktrans.production_worker.selection import DEFAULT_BLOCKS, DEFAULT_VIEWS, EXPECTED_SOURCE_SHA, load_jsonl_models


CURRENT_PRODUCTION_RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"

# Frozen source representatives.  B023 supplies current multi/protected cases;
# B022 supplies a known continuation pair solely as historical read-only input.
MT_ID = "body.p.07180"
MULTI_ID = "body.p.07183"
PROTECTED_ID = "body.p.07455"
CONTINUATION_IDS = ("body.p.06961", "body.p.06968")


def _calibration_request(display_id: str, views: dict[str, TranslationView], blocks: dict[str, BlockIR], source_sha: str) -> CalibrationRequest:
    """Mirror the frozen request projection without persisting an artifact."""
    view = views[display_id]
    block = blocks[display_id]
    from booktrans.domain.calibration import CalibrationSourceSegment

    return CalibrationRequest(
        source_sha256=source_sha,
        unit_uid=view.unit_uid,
        display_id=view.display_id,
        story=block.story,
        content_role=block.block_type,
        source_text=view.source_text,
        source_segments=[
            CalibrationSourceSegment(
                kind=segment.kind,
                text=segment.text,
                anchor_ref=segment.anchor_ref,
                island_ref=segment.island_ref,
            )
            for segment in view.segments
        ],
        available_anchors=[],
        continuation_hint=view.continuation_hint,
        previous_text=view.previous_text,
        next_text=view.next_text,
        selection_categories=["flash_compatibility_canary"],
    )


def _latency(call):
    started = time.monotonic()
    value = call()
    return value, round(time.monotonic() - started, 3)


def _usage(candidate) -> dict[str, Any]:
    return {str(key): value for key, value in dict(candidate.usage).items()}


def _row(*, name: str, candidate, latency_seconds: float, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": "PASS",
        "model": candidate.model,
        "provider": candidate.provider,
        "latency_seconds": latency_seconds,
        "usage": _usage(candidate),
        **extra,
    }


def _assert_output_isolated(output: Path) -> None:
    resolved = output.resolve()
    try:
        resolved.relative_to(CURRENT_PRODUCTION_RUN.resolve())
    except ValueError:
        return
    raise RuntimeError("CANARY_OUTPUT_MUST_NOT_BE_INSIDE_CURRENT_PRODUCTION_RUN")


def run(output: Path) -> dict[str, Any]:
    _assert_output_isolated(output)
    if output.exists():
        raise RuntimeError(f"CANARY_OUTPUT_ALREADY_EXISTS:{output}")
    source_sha = fingerprint(SOURCE).sha256
    if source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    views = {item.display_id: item for item in load_jsonl_models(DEFAULT_VIEWS, TranslationView)}
    blocks = {item.display_id: item for item in load_jsonl_models(DEFAULT_BLOCKS, BlockIR)}
    requested_ids = (MT_ID, MULTI_ID, PROTECTED_ID, *CONTINUATION_IDS)
    if any(item not in views or item not in blocks for item in requested_ids):
        raise RuntimeError("CANARY_FROZEN_REPRESENTATIVE_MISSING")
    requests = {item: _calibration_request(item, views, blocks, source_sha) for item in requested_ids}

    mt = QwenMTProvider.from_environment()
    structured = QwenMaxProvider.from_environment()
    reviewer = QwenMaxReviewerProvider.from_environment()
    repair = QwenMaxRepairProvider.from_environment()
    records: list[dict[str, Any]] = []

    mt_result, elapsed = _latency(lambda: mt.translate(TranslationProviderRequest(request=requests[MT_ID])))
    if not mt_result.response_text.strip() or mt_result.model != QWEN_MT_PLUS:
        raise RuntimeError("MT_SMOKE_CONTRACT_FAILURE")
    records.append(_row(name="QWEN_MT_PLUS_SMOKE", candidate=mt_result, latency_seconds=elapsed, display_id=MT_ID))

    multi_request = TranslationProviderRequest(request=requests[MULTI_ID])
    multi_result, elapsed = _latency(lambda: structured.translate_structured(multi_request))
    if multi_result.candidate.model != QWEN_MAX or len(multi_result.raw.target_segments) != len(requests[MULTI_ID].source_segments):
        raise RuntimeError("FLASH_STRUCTURED_SMOKE_CONTRACT_FAILURE")
    records.append(_row(name="QWEN_FLASH_STRUCTURED_SMOKE", candidate=multi_result.candidate, latency_seconds=elapsed, display_id=MULTI_ID, segment_count=len(multi_result.raw.target_segments)))

    protected_request = TranslationProviderRequest(request=requests[PROTECTED_ID])
    protected_result, elapsed = _latency(lambda: structured.translate_structured(protected_request))
    protected_pairs = zip(requests[PROTECTED_ID].source_segments, protected_result.raw.target_segments)
    if any(source.kind == "protected" and source.text != target.text for source, target in protected_pairs):
        raise RuntimeError("FLASH_PROTECTED_ANCHOR_CONTRACT_FAILURE")
    records.append(_row(name="FLASH_PROTECTED_ANCHOR_CASE", candidate=protected_result.candidate, latency_seconds=elapsed, display_id=PROTECTED_ID))

    accept, elapsed = _latency(lambda: reviewer.review_with_provenance(
        multi_request,
        multi_result.raw,
        deterministic_qa_summary={"passed": True, "codes": []},
    ))
    accept_latency = elapsed
    resolution_path = "DIRECT_ACCEPT"
    accepted_candidate = multi_result.raw
    if accept.review.verdict == "REPAIR":
        indices = sorted({index for issue in accept.review.issues for index in issue.affected_segment_indices})
        instruction = " ".join(str(issue.repair_instruction or "") for issue in accept.review.issues).strip()
        if not indices or not instruction:
            raise RuntimeError("FLASH_REVIEWER_REPAIR_CONTRACT_INCOMPLETE")
        resolution_contract = ApprovedRepairContract(
            policy_id="FLASH_CANARY_BOUNDED_AUTO_RESOLUTION",
            unit_uid=multi_request.request.unit_uid,
            source_fingerprint=multi_request.request_binding_sha256,
            permitted_segment_indices=indices,
            repair_instruction=instruction,
        )
        resolved, repair_elapsed = _latency(lambda: repair.repair_with_provenance(multi_request, multi_result.raw, resolution_contract))
        accept, review_elapsed = _latency(lambda: reviewer.review_with_provenance(
            multi_request,
            resolved.repaired,
            deterministic_qa_summary={"passed": True, "codes": []},
        ))
        accept_latency += repair_elapsed + review_elapsed
        resolution_path = "BOUNDED_REPAIR_ACCEPT"
        accepted_candidate = resolved.repaired
    if accept.review.verdict != "ACCEPT":
        raise RuntimeError(f"FLASH_REVIEWER_ACCEPT_EXPECTED:{accept.review.verdict}")
    records.append(_row(name="QWEN_FLASH_REVIEWER_SMOKE", candidate=accept.candidate, latency_seconds=accept_latency, verdict=accept.review.verdict, resolution_path=resolution_path))

    incorrect_segments = list(accepted_candidate.target_segments)
    first = incorrect_segments[0]
    incorrect_segments[0] = StructuredTargetSegment(text="完全不相关的错误内容", kind=first.kind, anchor_ref=first.anchor_ref, island_ref=first.island_ref)
    incorrect = TranslationResult(unit_uid=multi_result.raw.unit_uid, target_segments=incorrect_segments)
    repair_review, elapsed = _latency(lambda: reviewer.review_with_provenance(
        multi_request,
        incorrect,
        deterministic_qa_summary={"passed": True, "codes": []},
    ))
    if repair_review.review.verdict != "REPAIR" or not repair_review.review.issues:
        raise RuntimeError(f"FLASH_REVIEWER_REPAIR_EXPECTED:{repair_review.review.verdict}")
    records.append(_row(name="FLASH_REVIEWER_REPAIR_CASE", candidate=repair_review.candidate, latency_seconds=elapsed, verdict=repair_review.review.verdict))

    contract = ApprovedRepairContract(
        policy_id="FLASH_COMPATIBILITY_EXACT_SEMANTIC_REPAIR",
        unit_uid=multi_request.request.unit_uid,
        source_fingerprint=multi_request.request_binding_sha256,
        permitted_segment_indices=[0],
        repair_instruction="Faithfully translate only source segment 0 into Chinese; do not alter any other segment.",
    )
    repaired, elapsed = _latency(lambda: repair.repair_with_provenance(multi_request, incorrect, contract))
    if repaired.repaired.target_segments[0].text == incorrect.target_segments[0].text or any(
        repaired.repaired.target_segments[index].text != incorrect.target_segments[index].text
        for index in range(1, len(incorrect.target_segments))
    ):
        raise RuntimeError("FLASH_EXACT_REPAIR_SCOPE_OR_CONTENT_FAILURE")
    records.append(_row(name="QWEN_FLASH_REPAIR_SMOKE", candidate=repaired.candidate, latency_seconds=elapsed, repaired_indices=[0]))

    previous_id, current_id = CONTINUATION_IDS
    previous_request = TranslationProviderRequest(request=requests[previous_id])
    current_request = TranslationProviderRequest(request=requests[current_id])
    previous_result, _ = _latency(lambda: structured.translate_structured(previous_request))
    current_result, _ = _latency(lambda: structured.translate_structured(current_request))
    units = units_from_blocks([blocks[previous_id], blocks[current_id]])
    current_unit = next(item for item in units if item.display_id == current_id)
    group = build_semantic_review_group(
        current_unit,
        units,
        candidate_texts={
            previous_id: "".join(segment.text for segment in previous_result.raw.target_segments),
            current_id: "".join(segment.text for segment in current_result.raw.target_segments),
        },
        segment_counts={previous_id: len(previous_result.raw.target_segments), current_id: len(current_result.raw.target_segments)},
        sealed_work_order_ids=frozenset({previous_id}),
    )
    if group.continuation_relation != "BODY_CONTINUATION" or len(group.member_work_order_ids) != 2:
        raise RuntimeError("CANARY_CONTINUATION_GROUP_INPUT_INVALID")
    group_review, elapsed = _latency(lambda: reviewer.review_group_with_provenance(
        [(previous_request, previous_result.raw), (current_request, current_result.raw)],
        group_payload=group.as_payload(),
        deterministic_qa_summary={"passed": True, "codes": []},
    ))
    if group_review.review.verdict not in {"ACCEPT", "REPAIR", "HUMAN_REQUIRED"}:
        raise RuntimeError("FLASH_GROUP_REVIEW_SCHEMA_FAILURE")
    records.append(_row(name="FLASH_CONTINUATION_SEMANTIC_REVIEW_GROUP", candidate=group_review.candidate, latency_seconds=elapsed, verdict=group_review.review.verdict, members=list(group.member_work_order_ids)))

    report = {
        "artifact_type": "QWEN_FLASH_COMPATIBILITY_CANARY",
        "schema_version": 1,
        "isolated_from_production": True,
        "production_run_read_only": str(CURRENT_PRODUCTION_RUN),
        "source_sha256": source_sha,
        "general_model": QWEN_MAX,
        "mt_model": QWEN_MT_PLUS,
        "records": records,
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    output.mkdir(parents=True)
    (output / "canary_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args.output)
    except Exception as exc:
        _assert_output_isolated(args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        failure = args.output / "canary_failure.json"
        if not failure.exists():
            failure.write_text(
                json.dumps(
                    {"artifact_type": "QWEN_FLASH_COMPATIBILITY_CANARY_FAILURE", "error_class": type(exc).__name__, "error_code": str(exc)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
        raise
    print(json.dumps({"status": report["status"], "general_model": report["general_model"], "records": report["records"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
