from __future__ import annotations

import argparse
from pathlib import Path

from .generator import (
    build_explorer,
    scan_snapshot,
    validate_cli_output_path,
    write_json,
    write_site,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the offline Architecture Explorer T1 view")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="record a narrow, read-only source snapshot")
    scan.add_argument("--repository", type=Path, required=True)
    scan.add_argument("--source-file", action="append", required=True)
    scan.add_argument("--output", type=Path, required=True)

    build = subparsers.add_parser("build", help="combine snapshot facts and the human module map")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--snapshot", type=Path, required=True)
    build.add_argument("--mapping", type=Path, required=True)
    build.add_argument("--output-directory", type=Path, required=True)
    build.add_argument("--web-assets", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "scan":
        source_inputs = tuple(args.repository / source_file for source_file in args.source_file)
        output = validate_cli_output_path(
            args.repository,
            args.output,
            directory=False,
            inputs=source_inputs,
        )
        snapshot = scan_snapshot(args.repository, args.source_file)
        write_json(output, snapshot)
        print(f"snapshot={output}")
        print(f"facts_sha256={snapshot['facts_sha256']}")
        return 0

    output_directory = validate_cli_output_path(
        args.repository,
        args.output_directory,
        directory=True,
        inputs=(args.snapshot, args.mapping, args.web_assets),
        output_files=("explorer-data.json", "explorer-data.js", "index.html", "app.js", "styles.css"),
    )
    data = build_explorer(args.snapshot, args.mapping)
    write_site(data, output_directory, args.web_assets)
    print(f"site={output_directory / 'index.html'}")
    print(f"data={output_directory / 'explorer-data.json'}")
    print(f"deterministic_content_sha256={data['deterministic_content_sha256']}")
    return 0
