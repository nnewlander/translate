"""Read-only CLI projection of the current canonical production recovery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from booktrans.production_worker.continuation import project_current_canonical_recovery
from booktrans.production_worker.qa import harness_qa_record
from booktrans.production_worker.selection import load_checkpoint_d050_registry, production_checkpoint_path
from booktrans.production_worker.worker import SOURCE, current_batch_checkpoint_inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    checkpoint_dir = production_checkpoint_path(args.run, args.batch, args.checkpoint)
    if not checkpoint_dir.is_dir():
        raise SystemExit("CURRENT_CANONICAL_RECOVERY_PARTIAL_MISSING")
    inputs = current_batch_checkpoint_inputs(
        args.run, batch_label=args.batch, checkpoint=args.checkpoint, source_path=args.source,
    )
    result = project_current_canonical_recovery(
        output_dir=checkpoint_dir,
        requests=inputs["requests"],
        production_run=args.run,
        views=inputs["views"],
        known_unit_ids=inputs["known_unit_ids"],
        qa_fn=harness_qa_record,
        frozen_order=inputs["order"],
        selection_sha256=inputs["selection_sha256"],
        source_sha256=inputs["source_sha256"],
        active_batch=args.batch,
        checkpoint=args.checkpoint,
        translation_bible=inputs["bible"] if inputs["bible"].is_file() else None,
        d050_registry=load_checkpoint_d050_registry(args.run, args.checkpoint, batch_label=args.batch),
    )
    print(json.dumps({"recovery": result}, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
