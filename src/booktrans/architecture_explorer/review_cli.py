from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .generator import ExplorerInputError, validate_cli_output_path, write_json, write_site
from .review_mapping import import_review_mapping, publish_reviewed_explorer


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid JSON input: {path}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Expected a JSON object: {path}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import and publish Architecture Explorer T3 review data")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("import-review", help="convert authenticated GitHub API records to a review map")
    review.add_argument("--repository", type=Path, required=True)
    review.add_argument("--candidate", type=Path, required=True)
    review.add_argument("--comment", type=Path, required=True)
    review.add_argument("--permission", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    build = commands.add_parser("build", help="build the offline reviewed relationship site")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--snapshot", type=Path, required=True)
    build.add_argument("--mapping", type=Path, required=True)
    build.add_argument("--candidate", type=Path, required=True)
    build.add_argument("--review-mapping", type=Path, required=True)
    build.add_argument("--web-assets", type=Path, required=True)
    build.add_argument("--output-directory", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-review":
        output = validate_cli_output_path(
            args.repository, args.output, directory=False,
            inputs=(args.candidate, args.comment, args.permission),
        )
        mapping = import_review_mapping(_object(args.candidate), _object(args.comment), _object(args.permission))
        write_json(output, mapping)
        print(f"review_mapping={output}")
        print(f"validation={mapping['validation']['status']}")
        return 0 if mapping["validation"]["status"] == "valid" else 2
    output = validate_cli_output_path(
        args.repository, args.output_directory, directory=True,
        inputs=(args.snapshot, args.mapping, args.candidate, args.review_mapping, args.web_assets),
        output_files=("explorer-data.json", "explorer-data.js", "index.html", "app.js", "styles.css"),
    )
    data = publish_reviewed_explorer(
        args.repository, args.snapshot, args.mapping, _object(args.candidate), _object(args.review_mapping)
    )
    write_site(data, output, args.web_assets)
    relation = data["architecture"]["relationships"][0]
    print(f"site={output / 'index.html'}")
    print(f"review_status={relation['review_status']}")
    print(f"source_continuity={relation['source_continuity']['status']}")
    print(f"implementation_revision={data['implementation']['revision']}")
    return 0


__all__ = ["main"]
