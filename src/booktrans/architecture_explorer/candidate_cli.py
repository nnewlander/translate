from __future__ import annotations

import argparse
from pathlib import Path

from .candidate_scanner import scan_supervisor_worker_candidate, write_candidate_outputs
from .generator import validate_cli_output_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the Architecture Explorer T2 Supervisor-to-Worker candidate report"
    )
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--scanned-at", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = validate_cli_output_path(
        args.repository,
        args.output_directory,
        directory=True,
        output_files=("candidate.json", "candidate-review.md"),
    )
    candidate = scan_supervisor_worker_candidate(args.repository, scanned_at=args.scanned_at)
    machine_path, report_path = write_candidate_outputs(candidate, output)
    print(f"candidate={machine_path}")
    print(f"report={report_path}")
    print(f"source_revision={candidate['source_snapshot']['revision']}")
    print(f"candidate_report_sha256={candidate['candidate_report_sha256']}")
    print(f"scan_outcome={candidate['candidate_relation']['scan_outcome']}")
    return 0
