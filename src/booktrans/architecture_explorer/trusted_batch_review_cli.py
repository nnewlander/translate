"""Command line workflow for trusted batch comments and approval anchors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .generator import ExplorerInputError
from .github_review_publisher import GhCliReviewPublisher
from .review_source import TRUSTED_REPOSITORY
from .review_artifacts import write_review_artifact
from .trusted_batch_review import (
    confirm_anchor_batch,
    host_anchor_installation_path,
    install_anchor_batch,
    prepare_anchor_confirmation,
    prepare_anchor_installation,
    prepare_comment_publication,
    publish_review_comments,
    render_second_confirmation_checklist,
)


def _read(path: Path) -> dict[str, Any]:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ExplorerInputError(f"Duplicate JSON key {key!r}: {path}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid trusted batch review input: {path}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Expected JSON object: {path}")
    return value


def _write_new(path: Path, content: str) -> None:
    if not path.parent.is_dir():
        raise ExplorerInputError(f"Output directory does not exist: {path.parent}")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
    except FileExistsError as error:
        raise ExplorerInputError(f"Refusing to overwrite trusted batch artifact: {path}") from error


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _add_bound_inputs(command: argparse.ArgumentParser) -> None:
    command.add_argument("--batch", type=Path, required=True)
    command.add_argument("--first-confirmation", type=Path, required=True)


def main(argv: list[str] | None = None, *, github=None) -> int:
    parser = argparse.ArgumentParser(
        description="Architecture Explorer trusted batch review workflow"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    publication = commands.add_parser("prepare-publication")
    _add_bound_inputs(publication)
    publication.add_argument("--issue-number", type=int, required=True)
    publication.add_argument("--issue-id", type=int, required=True)
    publication.add_argument("--output", type=Path, required=True)

    publish = commands.add_parser("publish-comments")
    _add_bound_inputs(publish)
    publish.add_argument("--publication", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--confirmed-publication-sha256", required=True)

    anchor_candidate = commands.add_parser("prepare-anchor-confirmation")
    _add_bound_inputs(anchor_candidate)
    anchor_candidate.add_argument("--publication", type=Path, required=True)
    anchor_candidate.add_argument("--receipts", type=Path, required=True)
    anchor_candidate.add_argument("--output", type=Path, required=True)
    anchor_candidate.add_argument("--checklist-output", type=Path, required=True)

    confirm = commands.add_parser("confirm-anchors")
    confirm.add_argument("--candidate", type=Path, required=True)
    confirm.add_argument("--checklist", type=Path, required=True)
    confirm.add_argument("--output", type=Path, required=True)

    install = commands.add_parser("install-anchors")
    install.add_argument("--candidate", type=Path, required=True)
    install.add_argument("--second-confirmation", type=Path, required=True)
    install.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "prepare-publication":
        value = prepare_comment_publication(
            _read(args.batch),
            _read(args.first_confirmation),
            repository=TRUSTED_REPOSITORY,
            issue_number=args.issue_number,
            issue_id=args.issue_id,
        )
        _write_new(args.output, _json(value))
        print(f"publication={args.output}")
        return 0
    if args.command == "publish-comments":
        publication = _read(args.publication)
        if (
            publication.get("publication_sha256") != args.confirmed_publication_sha256
            or type(publication.get("issue_id")) is not int
            or type(publication.get("issue_number")) is not int
            or publication["issue_number"] <= 0
        ):
            raise ExplorerInputError("Publishing requires the exact confirmed publication and Issue ID")
        if not args.output.parent.is_dir():
            raise ExplorerInputError("Receipt output directory does not exist")
        adapter = github or GhCliReviewPublisher()
        batch, first = _read(args.batch), _read(args.first_confirmation)
        if args.output.exists():
            candidate = prepare_anchor_confirmation(
                batch, first, publication, _read(args.output), github=adapter
            )
            if candidate["status"] != "READY":
                raise ExplorerInputError(f"Existing Receipt output differs: {candidate['errors']}")
        else:
            value = publish_review_comments(batch, first, publication, adapter)
            write_review_artifact(args.output, value)
        print(f"receipts={args.output}")
        return 0
    if args.command == "prepare-anchor-confirmation":
        value = prepare_anchor_confirmation(
            _read(args.batch),
            _read(args.first_confirmation),
            _read(args.publication),
            _read(args.receipts),
            github=github or GhCliReviewPublisher(),
        )
        checklist = render_second_confirmation_checklist(value)
        _write_new(args.output, _json(value))
        _write_new(args.checklist_output, checklist)
        print(f"anchor_candidate={args.output}")
        print(f"second_confirmation_checklist={args.checklist_output}")
        return 0 if value["status"] == "READY" else 2
    if args.command == "confirm-anchors":
        try:
            checklist = args.checklist.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise ExplorerInputError(
                f"Second confirmation checklist is unavailable: {args.checklist}"
            ) from error
        candidate = _read(args.candidate)
        value = confirm_anchor_batch(candidate, checklist=checklist)
        installation = prepare_anchor_installation(candidate, value)
        _write_new(args.output, _json(value))
        print(f"host_authorization_digest={installation['installation_sha256']}")
        print(f"second_confirmation={args.output}")
        return 0

    value = install_anchor_batch(
        _read(args.candidate),
        _read(args.second_confirmation),
        args.output,
    )
    print(f"installed_anchor_batch={host_anchor_installation_path(value['installation_sha256'])}")
    print(f"installation_sha256={value['installation_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
