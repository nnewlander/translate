"""Small offline command line entry point for first batch confirmation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .batch_review import (
    confirm_batch_review,
    decided_objects,
    prepare_batch_review,
    render_batch_checklist,
    render_final_decision_list,
)
from .generator import ExplorerInputError


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid batch review input: {path}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Expected JSON object: {path}")
    return value


def _write_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
    except FileExistsError as error:
        raise ExplorerInputError(f"Refusing to overwrite batch review artifact: {path}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Architecture Explorer batch review")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--explorer-data", type=Path, required=True)
    prepare.add_argument("--node-definitions", type=Path)
    prepare.add_argument("--relationship-definitions", type=Path)
    prepare.add_argument("--ai-analysis", type=Path, required=True)
    prepare.add_argument("--output-directory", type=Path, required=True)
    final_list = commands.add_parser("prepare-decisions")
    final_list.add_argument("--batch", type=Path, required=True)
    final_list.add_argument("--decisions", type=Path, required=True)
    final_list.add_argument("--output", type=Path, required=True)
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--batch", type=Path, required=True)
    confirm.add_argument("--decisions", type=Path, required=True)
    confirm.add_argument("--final-list", type=Path, required=True)
    confirm.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        if args.node_definitions is None and args.relationship_definitions is None:
            parser.error("At least one definitions file is required")
        output = args.output_directory
        batch_path, checklist_path = output / "batch-review.json", output / "checklist.md"
        if batch_path.exists() or checklist_path.exists():
            raise ExplorerInputError("Refusing to overwrite batch review artifacts")
        batch = prepare_batch_review(
            _read(args.explorer_data),
            _read(args.node_definitions) if args.node_definitions else None,
            _read(args.relationship_definitions) if args.relationship_definitions else None,
            _read(args.ai_analysis),
        )
        _write_new(batch_path, json.dumps(batch, ensure_ascii=False, indent=2) + "\n")
        _write_new(checklist_path, render_batch_checklist(batch) + "\n")
        print(f"batch={batch_path}")
        print(f"checklist={checklist_path}")
        return 0
    batch = _read(args.batch)
    checklist_path = args.batch.with_name("checklist.md")
    try:
        displayed_checklist = checklist_path.read_bytes()
    except OSError as error:
        raise ExplorerInputError(f"First confirmation needs the displayed checklist: {checklist_path}") from error
    if displayed_checklist != (render_batch_checklist(batch) + "\n").encode("utf-8"):
        raise ExplorerInputError("Displayed checklist differs from the bound batch review")
    decisions = _read(args.decisions)
    expected_list = render_final_decision_list(batch, decisions)
    if args.command == "prepare-decisions":
        _write_new(args.output, expected_list)
        print(f"final_decision_list={args.output}")
        return 0
    try:
        displayed_final_list = args.final_list.read_bytes()
    except OSError as error:
        raise ExplorerInputError(f"First confirmation needs the displayed final decision list: {args.final_list}") from error
    if displayed_final_list != expected_list.encode("utf-8"):
        raise ExplorerInputError("Displayed final decision list differs from the bound decisions")
    record = confirm_batch_review(batch, decisions, final_decision_list=expected_list)
    decided_objects(record, batch)
    _write_new(args.output, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"first_confirmation={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
