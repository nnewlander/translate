"""Strictly normalize documented, derivable RECOVERY_REQUIRED handoff fields.

This is a read-only control-plane helper: it derives an in-memory normalized
handoff from the current immutable partial checkpoint.  It never writes the
worker's original handoff or any translation artifact.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


REQUIRED_SCHEMA_VERSION = 1
CHECKPOINT_RE = re.compile(r"checkpoint_(\d{2})$")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _failed_ids(path: Path) -> list[str]:
    report = _json(path)
    records = report.get("records")
    if not isinstance(records, list):
        raise ValueError(f"QA_RECORDS_INVALID:{path.name}")
    failed: list[str] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("display_id"), str):
            raise ValueError(f"QA_RECORD_INVALID:{path.name}")
        if not record.get("passed"):
            failed.append(record["display_id"])
    return failed


def _stage(checkpoint: Path) -> str:
    names = {path.name for path in checkpoint.iterdir() if path.is_file()}
    if "translation_request.jsonl" not in names:
        raise ValueError("PARTIAL_REQUEST_MISSING")
    if "translation_raw_response.jsonl" not in names:
        return "FROZEN_REQUEST_WRITTEN_BEFORE_RAW"
    if "translation_raw_qa_report.json" not in names:
        return "RAW_WRITTEN"
    if not {"translation_normalized_response.jsonl", "translation_normalization_report.json", "translation_normalized_qa_report.json"} <= names:
        return "RAW_DETERMINISTIC_QA"
    if "translation_final_qa_report.json" not in names:
        return "FINAL_DETERMINISTIC_QA"
    if "checkpoint_evidence.json" not in names:
        return "FINAL_QA_BEFORE_CHECKPOINT_EVIDENCE"
    return "CHECKPOINT_EVIDENCE_PRESENT"


def normalize_recovery_handoff(*, handoff: dict[str, Any], state: dict[str, Any], source_sha256: str, run: Path) -> dict[str, Any]:
    """Return normalized handoff or a fail-closed reason.

    Only documented recovery fields are derived.  Existing supplied fields
    must agree with the unique current partial checkpoint; they are never
    silently overwritten when contradictory.
    """
    denied = {"normalized": False, "reason": None, "derived_fields": [], "handoff": None}
    if handoff.get("schema_version") != REQUIRED_SCHEMA_VERSION:
        denied["reason"] = "HANDOFF_SCHEMA_VERSION_UNSUPPORTED"
        return denied
    if handoff.get("status") != "RECOVERY_REQUIRED":
        denied["reason"] = "HANDOFF_STATUS_NOT_RECOVERY_REQUIRED"
        return denied
    if not isinstance(handoff.get("worker_id"), str) or not handoff["worker_id"]:
        denied["reason"] = "WORKER_ID_MISSING"
        return denied
    if state.get("source_sha256") != source_sha256:
        denied["reason"] = "SOURCE_SHA_MISMATCH"
        return denied
    batch = state.get("active_batch")
    checkpoint_name = state.get("next_checkpoint")
    if not isinstance(batch, str) or not isinstance(checkpoint_name, str) or not CHECKPOINT_RE.fullmatch(checkpoint_name):
        denied["reason"] = "CURRENT_CHECKPOINT_IDENTITY_INVALID"
        return denied
    if handoff.get("active_batch") not in (None, "", batch) or handoff.get("checkpoint") not in (None, "", checkpoint_name):
        denied["reason"] = "RECOVERY_HANDOFF_IDENTITY_MISMATCH"
        return denied
    batch_dir = run / f"full_book_batch_{batch.rsplit('_', 1)[1]}"
    manifest_path = batch_dir / "batch_manifest.json"
    if not manifest_path.is_file():
        denied["reason"] = "ACTIVE_MANIFEST_MISSING"
        return denied
    manifest = _json(manifest_path)
    sealed = {item.get("id") for item in manifest.get("checkpoints", []) if isinstance(item, dict)}
    candidates = [path for path in batch_dir.glob("checkpoint_*") if path.is_dir() and path.name not in sealed]
    current = batch_dir / checkpoint_name
    if len(candidates) != 1 or candidates[0].resolve() != current.resolve() or not current.is_dir():
        denied["reason"] = "PARTIAL_CHECKPOINT_NOT_UNIQUE"
        return denied
    if (current / "checkpoint_evidence.json").exists() or (current / "translation_final_qa_report.json").exists():
        denied["reason"] = "CURRENT_CHECKPOINT_ALREADY_FINALIZED"
        return denied
    derived: list[str] = []
    normalized = dict(handoff)
    for name, value in (("active_batch", batch), ("checkpoint", checkpoint_name), ("partial_checkpoint_path", str(current))):
        existing = normalized.get(name)
        if existing not in (None, ""):
            if name == "partial_checkpoint_path":
                if Path(str(existing)).resolve() != current.resolve():
                    denied["reason"] = "RECOVERY_HANDOFF_PARTIAL_PATH_MISMATCH"
                    return denied
            elif existing != value:
                denied["reason"] = f"RECOVERY_HANDOFF_{name.upper()}_MISMATCH"
                return denied
        else:
            normalized[name] = value
            derived.append(name)
    try:
        actual_stage = _stage(current)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        denied["reason"] = f"PARTIAL_ARTIFACT_CHAIN_INVALID:{error}"
        return denied
    existing_stage = normalized.get("stage_reached")
    if existing_stage not in (None, "") and actual_stage not in str(existing_stage):
        denied["reason"] = "RECOVERY_HANDOFF_STAGE_MISMATCH"
        return denied
    if existing_stage in (None, ""):
        derived.append("stage_reached")
    normalized["stage_reached"] = actual_stage
    raw = current / "translation_raw_response.jsonl"
    raw_exists = raw.is_file()
    existing_raw_exists = normalized.get("raw_exists")
    if existing_raw_exists is not None and bool(existing_raw_exists) != raw_exists:
        denied["reason"] = "RECOVERY_HANDOFF_RAW_EXISTS_MISMATCH"
        return denied
    if existing_raw_exists is None:
        derived.append("raw_exists")
    normalized["raw_exists"] = raw_exists
    if raw_exists:
        actual_raw_sha = sha256(raw.read_bytes()).hexdigest()
        existing_raw_sha = normalized.get("raw_sha256")
        if existing_raw_sha not in (None, "") and str(existing_raw_sha).lower() != actual_raw_sha:
            denied["reason"] = "IMMUTABLE_RAW_SHA_MISMATCH"
            return denied
        if existing_raw_sha in (None, ""):
            derived.append("raw_sha256")
        normalized["raw_sha256"] = actual_raw_sha
    elif normalized.get("raw_sha256") not in (None, ""):
        denied["reason"] = "RECOVERY_HANDOFF_RAW_SHA_WITHOUT_RAW"
        return denied
    qa_paths = [current / "translation_raw_qa_report.json", current / "translation_normalized_qa_report.json"]
    if all(path.is_file() for path in qa_paths):
        try:
            raw_failed, normalized_failed = (_failed_ids(path) for path in qa_paths)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            denied["reason"] = f"QA_EVIDENCE_INVALID:{error}"
            return denied
        if raw_failed != normalized_failed or not raw_failed:
            denied["reason"] = "QA_FAILURE_EVIDENCE_CONFLICT"
            return denied
        existing_ids = normalized.get("failing_work_order_ids")
        if existing_ids not in (None, []) and existing_ids != raw_failed:
            denied["reason"] = "RECOVERY_HANDOFF_FAILURE_SCOPE_MISMATCH"
            return denied
        if existing_ids in (None, []):
            derived.append("failing_work_order_ids")
        normalized["failing_work_order_ids"] = raw_failed
        expected_paths = [str(path) for path in (*qa_paths, current / "translation_normalization_report.json") if path.is_file()]
        existing_paths = normalized.get("relevant_qa_artifact_paths")
        if existing_paths not in (None, []) and set(existing_paths) != set(expected_paths):
            denied["reason"] = "RECOVERY_HANDOFF_QA_PATHS_MISMATCH"
            return denied
        if existing_paths in (None, []):
            derived.append("relevant_qa_artifact_paths")
        normalized["relevant_qa_artifact_paths"] = expected_paths
    elif normalized.get("failing_work_order_ids") in (None, []) or normalized.get("relevant_qa_artifact_paths") in (None, []):
        denied["reason"] = "QA_EVIDENCE_MISSING_FOR_DERIVATION"
        return denied
    if normalized.get("created_at") in (None, ""):
        timestamp = normalized.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            denied["reason"] = "RECOVERY_HANDOFF_TIMESTAMP_MISSING"
            return denied
        normalized["created_at"] = timestamp
        derived.append("created_at_from_timestamp")
    if normalized.get("next_logical_work_order_id") in (None, ""):
        normalized["next_logical_work_order_id"] = state.get("next_logical_work_order_id")
        derived.append("next_logical_work_order_id")
    return {"normalized": True, "reason": None, "derived_fields": derived, "handoff": normalized}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = normalize_recovery_handoff(handoff=_json(args.handoff), state=_json(args.state), source_sha256=args.source_sha256, run=args.run)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"normalized": False, "reason": f"NORMALIZATION_INPUT_INVALID:{error}", "derived_fields": [], "handoff": None}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["normalized"] else 1)


if __name__ == "__main__":
    main()
