"""Fail fast on authored target-map structure before checkpoint creation.

This deliberately validates only the frozen-selection/TranslationView
structure contract.  Translation quality and the existing Harness QA chain
remain the authority after a RAW response is created.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from booktrans.domain.translation_view import TranslationView


class TargetMapValidationError(RuntimeError):
    """A precise, user-actionable authored target-map contract failure."""


def expected_checkpoint_order(selection: Mapping[str, object], checkpoint: str) -> list[str]:
    try:
        checkpoint_number = int(checkpoint.rsplit("_", 1)[1])
    except (IndexError, ValueError) as error:
        raise TargetMapValidationError(f"CHECKPOINT_IDENTIFIER_INVALID: {checkpoint}") from error
    selected = selection.get("selected_logical_work_order_ids")
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise TargetMapValidationError("FROZEN_SELECTION_INVALID")
    start = (checkpoint_number - 1) * 25
    order = selected[start:start + 25]
    if len(order) != 25 or len(set(order)) != 25:
        raise TargetMapValidationError(f"FROZEN_SELECTION_CHECKPOINT_INVALID: {checkpoint}")
    return order


def validate_authored_target_map(
    *,
    selection: Mapping[str, object],
    checkpoint: str,
    target_artifact: Mapping[str, object],
    views: Mapping[str, TranslationView],
) -> list[str]:
    """Validate a closed-world authored map against one frozen checkpoint."""
    if target_artifact.get("artifact_type") != "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP":
        raise TargetMapValidationError("TARGET_MAP_ARTIFACT_TYPE_INVALID")

    order = expected_checkpoint_order(selection, checkpoint)
    declared_order = target_artifact.get("logical_work_orders")
    if not isinstance(declared_order, list) or not all(isinstance(item, str) for item in declared_order):
        raise TargetMapValidationError("TARGET_MAP_LOGICAL_WORK_ORDERS_INVALID")
    if len(set(declared_order)) != len(declared_order):
        raise TargetMapValidationError("TARGET_MAP_LOGICAL_WORK_ORDERS_DUPLICATE")
    if declared_order != order:
        raise TargetMapValidationError("AUTHORITATIVE_SELECTION_MISMATCH")

    targets = target_artifact.get("targets")
    if not isinstance(targets, dict):
        raise TargetMapValidationError("TARGET_MAP_TARGETS_INVALID")
    expected_ids = set(order)
    actual_ids = set(targets)
    if actual_ids != expected_ids:
        missing = ",".join(sorted(expected_ids - actual_ids))
        extra = ",".join(sorted(actual_ids - expected_ids))
        raise TargetMapValidationError(f"TARGET_MAP_CLOSED_WORLD_MISMATCH: missing={missing or '-'} extra={extra or '-'}")

    for logical_work_order_id in order:
        view = views.get(logical_work_order_id)
        if view is None:
            raise TargetMapValidationError(f"FROZEN_SOURCE_UNITS_MISSING: {logical_work_order_id}")
        values = targets[logical_work_order_id]
        if not isinstance(values, list):
            raise TargetMapValidationError(f"TARGET_SEGMENTS_NOT_LIST: {logical_work_order_id}")
        if len(values) != len(view.segments):
            raise TargetMapValidationError(
                f"TARGET_SEGMENT_COUNT_MISMATCH: {logical_work_order_id} expected={len(view.segments)} actual={len(values)}"
            )
        for index, value in enumerate(values):
            if not isinstance(value, str):
                raise TargetMapValidationError(f"TARGET_SEGMENT_NOT_STRING: {logical_work_order_id} index={index}")
            source_segment = view.segments[index]
            if source_segment.kind == "protected" and value != source_segment.text:
                raise TargetMapValidationError(
                    f"PROTECTED_SEGMENT_CORRESPONDENCE_MISMATCH: {logical_work_order_id} index={index}"
                )
    return order


def _load_views(path: Path) -> dict[str, TranslationView]:
    return {
        view.display_id: view
        for view in (
            TranslationView.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--views", type=Path, required=True)
    args = parser.parse_args()
    try:
        selection = json.loads(args.selection.read_text(encoding="utf-8"))
        target_artifact = json.loads(args.targets.read_text(encoding="utf-8"))
        if not isinstance(selection, dict) or not isinstance(target_artifact, dict):
            raise TargetMapValidationError("JSON_OBJECT_REQUIRED")
        validate_authored_target_map(
            selection=selection,
            checkpoint=args.checkpoint,
            target_artifact=target_artifact,
            views=_load_views(args.views),
        )
    except TargetMapValidationError as error:
        print(str(error))
        raise SystemExit(1)
    print("PASS")


if __name__ == "__main__":
    main()
