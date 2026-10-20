"""Emit read-only source/candidate visual-differential facts.

The PDFs must already have been rendered with the same renderer configuration.
This utility neither invokes LibreOffice nor modifies a DOCX or production state.
It consumes bounded renderer observations and immutable-evidence-derived anchor
locations; it intentionally never scans a whole book PDF to guess alignment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from booktrans.ooxml.rendered_differential import rendered_differential_facts


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--source-docx", required=True, type=Path)
    result.add_argument("--candidate-docx", required=True, type=Path)
    result.add_argument("--source-rendered-pdf", required=True, type=Path)
    result.add_argument("--candidate-rendered-pdf", required=True, type=Path)
    result.add_argument("--official-source-pdf", type=Path)
    result.add_argument("--anchors", required=True, type=Path, help="JSON list of immutable-evidence-derived content-anchor locations")
    result.add_argument("--source-render-observed", required=True, type=Path, help="Bounded source-LO renderer observation JSON")
    result.add_argument("--candidate-render-observed", required=True, type=Path, help="Bounded candidate-LO renderer observation JSON")
    result.add_argument("--official-render-observed", type=Path, help="Bounded official-source renderer observation JSON")
    result.add_argument("--output", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    anchors = json.loads(args.anchors.read_text(encoding="utf-8"))
    if not isinstance(anchors, list) or not all(isinstance(item, dict) for item in anchors):
        raise ValueError("ANCHOR_SCHEMA_INVALID: expected a JSON list of anchor objects.")
    facts = rendered_differential_facts(
        source_docx=args.source_docx,
        candidate_docx=args.candidate_docx,
        source_rendered_pdf=args.source_rendered_pdf,
        candidate_rendered_pdf=args.candidate_rendered_pdf,
        official_source_pdf=args.official_source_pdf,
        anchors=anchors,
        source_render_observed=json.loads(args.source_render_observed.read_text(encoding="utf-8")),
        candidate_render_observed=json.loads(args.candidate_render_observed.read_text(encoding="utf-8")),
        official_render_observed=json.loads(args.official_render_observed.read_text(encoding="utf-8")) if args.official_render_observed else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
