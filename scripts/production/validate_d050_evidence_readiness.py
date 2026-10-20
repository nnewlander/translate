"""Read-only validation of one bounded D050 evidence registry before a checkpoint.

This validates only supplied exact evidence; it deliberately does not discover
footnotes or infer missing records.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from booktrans.domain.translation_view import TranslationView  # noqa: E402
from booktrans.ooxml.verified_footnotes import load_exact_verified_evidence_registry  # noqa: E402


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--registry", type=Path, action="append", required=True)
    args = parser.parse_args()
    selection = _json(args.selection)
    order = selection.get("selected_logical_work_order_ids")
    if not isinstance(order, list):
        raise ValueError("FROZEN_SELECTION_INVALID")
    try:
        checkpoint_number = int(args.checkpoint.rsplit("_", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError("CHECKPOINT_IDENTIFIER_INVALID") from exc
    expected = [str(value) for value in order[(checkpoint_number - 1) * 25 : checkpoint_number * 25]]
    if len(expected) != 25:
        raise ValueError("CHECKPOINT_SELECTION_NOT_25")
    views = {item.display_id: item for item in (TranslationView.model_validate_json(line) for line in args.views.read_text(encoding="utf-8").splitlines() if line)}
    source_sha = sha256(args.source.read_bytes()).hexdigest()
    pdf_sha = sha256(args.pdf.read_bytes()).hexdigest()
    records: list[dict] = []
    for path in args.registry:
        value = _json(path)
        if value.get("schema") != "D050_EXACT_CLOSED_WORLD" or value.get("source_sha256") != source_sha or value.get("source_pdf_sha256") != pdf_sha:
            raise ValueError(f"D050_REGISTRY_SOURCE_BINDING_INVALID:{path.name}")
        raw = value.get("records")
        if not isinstance(raw, list):
            raise ValueError(f"D050_REGISTRY_RECORDS_INVALID:{path.name}")
        records.extend(raw)
    # Loader also rejects conflicting duplicate exact records.
    load_exact_verified_evidence_registry(args.registry)
    for record in records:
        if record.get("verification_status") != "VERIFIED":
            continue
        work_order = record.get("logical_work_order_id")
        view = views.get(work_order)
        if work_order not in expected or view is None or record.get("unit_uid") != view.unit_uid:
            raise ValueError(f"D050_EXACT_SCOPE_INVALID:{work_order}")
        marker = record.get("exact_source_marker_token")
        bindings = record.get("source_segment_binding")
        if not isinstance(marker, str) or not isinstance(bindings, list) or not bindings:
            raise ValueError(f"D050_EXACT_BINDING_INVALID:{work_order}")
        if not all(isinstance(binding, dict) and any(segment.anchor_ref == binding.get("anchor_ref") and segment.text == binding.get("text") for segment in view.segments) for binding in bindings):
            raise ValueError(f"D050_EXACT_BINDING_MISMATCH:{work_order}")
    print(json.dumps({"status": "PASS", "checkpoint": args.checkpoint, "verified_record_count": sum(item.get("verification_status") == "VERIFIED" for item in records), "routed_work_order_ids": sorted({item["logical_work_order_id"] for item in records if item.get("verification_status") == "VERIFIED"})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
