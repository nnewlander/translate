"""Atomic updates for derived, resumable macro-batch indexes."""
from __future__ import annotations
from hashlib import sha256
import json, os, re
from pathlib import Path
from typing import Any


MANIFEST_VERSION_RE = re.compile(r"batch_manifest_v(\d+)\.json$")


def manifest_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def resolve_authoritative_manifest(batch_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Resolve the single append-only manifest authority for a batch.

    A legacy batch has only ``batch_manifest.json``.  A reconciled batch may
    publish versioned manifests; each successor must cryptographically bind the
    immediately previous authoritative manifest.  This deliberately makes the
    pointer chain explicit instead of allowing filesystem time ordering to
    select production authority.
    """
    root = batch_dir / "batch_manifest.json"
    current_path = root
    current = _load_json(root, "BATCH_MANIFEST_INVALID")
    versions = sorted(
        (int(match.group(1)), path)
        for path in batch_dir.glob("batch_manifest_v*.json")
        if (match := MANIFEST_VERSION_RE.fullmatch(path.name))
    )
    for _, path in versions:
        successor = _load_json(path, "CURRENT_BATCH_MANIFEST_INVALID")
        supersedes = successor.get("supersedes_manifest")
        direct_root = (
            isinstance(supersedes, dict)
            and supersedes.get("path") == str(root)
            and supersedes.get("sha256") == manifest_sha256(root)
        )
        chained = (
            isinstance(supersedes, dict)
            and supersedes.get("path") == str(current_path)
            and supersedes.get("sha256") == manifest_sha256(current_path)
        )
        # Historical batches used sparse version numbers where every version
        # directly named the root.  Preserve that convention while allowing
        # new compensating generations to name their immediate predecessor.
        if not (direct_root or chained):
            raise ValueError("CURRENT_MANIFEST_SUPERSESSION_INVALID")
        current_path, current = path, successor
    return current_path, current


def publish_manifest_successor(
    batch_dir: Path, manifest: dict[str, Any], *, allow_compensating_rollback: bool = False,
) -> Path:
    """Publish one immutable manifest successor after its evidence is complete.

    The caller supplies a fully validated derived projection.  Publication is
    create-once and the cryptographic predecessor pointer is filled here, so a
    partial checkpoint generation can never become authority by directory
    discovery alone.
    """
    previous_path, previous = resolve_authoritative_manifest(batch_dir)
    next_number = 1
    for path in batch_dir.glob("batch_manifest_v*.json"):
        match = MANIFEST_VERSION_RE.fullmatch(path.name)
        if match:
            next_number = max(next_number, int(match.group(1)) + 1)
    target = batch_dir / f"batch_manifest_v{next_number}.json"
    if target.exists():
        raise ValueError(f"MANIFEST_SUCCESSOR_ALREADY_EXISTS:{target}")
    if manifest.get("completed_count", -1) < previous.get("completed_count", -1):
        if not allow_compensating_rollback or manifest.get("authority_action") != "COMPENSATING_SUPERSESSION":
            raise ValueError("MANIFEST_COMPLETED_COUNT_REGRESSION")
    successor = dict(manifest)
    successor["supersedes_manifest"] = {
        "path": str(previous_path), "sha256": manifest_sha256(previous_path),
    }
    # Create-once is intentionally distinct from update_manifest: historical
    # successor projections remain immutable evidence after a later correction.
    temporary = target.with_suffix(target.suffix + ".tmp")
    if temporary.exists():
        raise ValueError(f"MANIFEST_TEMP_ALREADY_EXISTS: {temporary}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(successor, handle, ensure_ascii=False, indent=2)
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


def resolve_checkpoint_seal_artifacts(
    batch_dir: Path, checkpoint: dict[str, Any],
) -> tuple[Path, Path, Path, int]:
    """Return ``(checkpoint_dir, evidence, final_qa, generation)``.

    Legacy entries intentionally keep generation zero at their fixed paths.
    A corrected generation is selected solely by the authoritative manifest
    entry, never by a directory scan.
    """
    checkpoint_id = checkpoint.get("id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise ValueError("CHECKPOINT_ID_INVALID")
    checkpoint_dir = batch_dir / checkpoint_id
    seal = checkpoint.get("seal_generation")
    if seal is None:
        return checkpoint_dir, checkpoint_dir / "checkpoint_evidence.json", checkpoint_dir / "translation_final_qa_report.json", 0
    if not isinstance(seal, dict):
        raise ValueError("CHECKPOINT_SEAL_GENERATION_INVALID")
    generation = seal.get("generation")
    if not isinstance(generation, int) or generation < 1:
        raise ValueError("CHECKPOINT_SEAL_GENERATION_INVALID")
    seal_dir = _checkpoint_seal_dir(checkpoint_dir, seal)
    evidence = seal_dir / "checkpoint_evidence.json"
    final = seal_dir / "translation_final_qa_report.json"
    if not evidence.is_file() or not final.is_file():
        raise ValueError("CHECKPOINT_SEAL_GENERATION_ARTIFACT_MISSING")
    if seal.get("checkpoint_evidence_sha256") != manifest_sha256(evidence):
        raise ValueError("CHECKPOINT_SEAL_GENERATION_EVIDENCE_SHA_MISMATCH")
    if seal.get("final_qa_sha256") != manifest_sha256(final):
        raise ValueError("CHECKPOINT_SEAL_GENERATION_FINAL_QA_SHA_MISMATCH")
    return checkpoint_dir, evidence, final, generation

def update_manifest(path: Path, manifest: dict[str, Any]) -> None:
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if previous is not None:
        if manifest["completed_count"] < previous["completed_count"]: raise ValueError("MANIFEST_COMPLETED_COUNT_REGRESSION")
        old={x["id"]:x for x in previous.get("checkpoints",[])}; new={x["id"]:x for x in manifest.get("checkpoints",[])}
        if len(new) != len(manifest.get("checkpoints",[])): raise ValueError("MANIFEST_DUPLICATE_CHECKPOINT")
        if any(new.get(k)!=v for k,v in old.items()): raise ValueError("MANIFEST_EXISTING_CHECKPOINT_DRIFT")
    path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix(path.suffix+".tmp")
    if temp.exists(): raise ValueError(f"MANIFEST_TEMP_ALREADY_EXISTS: {temp}")
    with temp.open("x",encoding="utf-8",newline="\n") as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2);f.write("\n");f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def append_sealed_checkpoint(path: Path, checkpoint_dir: Path, *, seal_generation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append one evidence-backed checkpoint to the latest derived manifest.

    The manifest is intentionally not an evidence source.  This function reads the
    immutable checkpoint artifacts first, then derives the next manifest from the
    current on-disk manifest so an old runner cannot regress completed_count.
    """
    if not path.exists():
        raise ValueError(f"MANIFEST_MISSING: {path}")
    latest = _load_json(path, "MANIFEST_INVALID_JSON")
    # Commit retry is deliberately idempotent.  A process may die after the
    # atomic manifest publication but before it refreshes its derived cursor;
    # the same immutable seal must then be a no-op, never a duplicate append.
    existing = next((item for item in latest.get("checkpoints", []) if item.get("id") == checkpoint_dir.name), None)
    if existing is not None:
        observed = _checkpoint_summary(checkpoint_dir, seal_generation=seal_generation)
        if existing != observed:
            raise ValueError(f"MANIFEST_EXISTING_CHECKPOINT_DRIFT: {checkpoint_dir.name}")
        return latest
    updated = sealed_checkpoint_manifest_projection(latest, checkpoint_dir, seal_generation=seal_generation)
    update_manifest(path, updated)
    return updated


def commit_prepared_checkpoint(
    batch_dir: Path, checkpoint_dir: Path, *, seal_generation: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    """Commit one fully prepared checkpoint through the manifest authority.

    This is the recovery-safe prepare→validate→publish boundary.  It never
    recreates translation evidence: the summary validation reads what is
    already present, then either publishes the derived manifest projection or
    returns the already-published identical checkpoint unchanged.
    """
    current_path, current = resolve_authoritative_manifest(batch_dir)
    existing = next((item for item in current.get("checkpoints", []) if item.get("id") == checkpoint_dir.name), None)
    observed = _checkpoint_summary(checkpoint_dir, seal_generation=seal_generation)
    if existing is not None:
        if existing != observed:
            raise ValueError(f"MANIFEST_EXISTING_CHECKPOINT_DRIFT: {checkpoint_dir.name}")
        return current_path, current, False
    projection = sealed_checkpoint_manifest_projection(current, checkpoint_dir, seal_generation=seal_generation)
    # Historical root manifests are mutable derived indexes.  Once a
    # successor has made the current pointer immutable, publish the next
    # successor instead of overwriting it.
    if current_path.name == "batch_manifest.json":
        update_manifest(current_path, projection)
        return current_path, projection, True
    published = publish_manifest_successor(batch_dir, projection)
    return published, projection, True


def sealed_checkpoint_manifest_projection(
    latest: dict[str, Any], checkpoint_dir: Path, *, seal_generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build, but do not publish, the next manifest projection from evidence."""
    checkpoint = _checkpoint_summary(checkpoint_dir, seal_generation=seal_generation)
    expected = latest.get("next_checkpoint")
    if checkpoint["id"] != expected:
        raise ValueError(
            f"MANIFEST_UNEXPECTED_CHECKPOINT: expected {expected!r}, got {checkpoint['id']!r}"
        )
    if any(item.get("id") == checkpoint["id"] for item in latest.get("checkpoints", [])):
        raise ValueError(f"MANIFEST_DUPLICATE_CHECKPOINT: {checkpoint['id']}")

    completed = latest["completed_count"] + checkpoint["actual_count"]
    target = latest["target_count"]
    if completed > target:
        raise ValueError(f"MANIFEST_TARGET_EXCEEDED: {completed}>{target}")
    updated = dict(latest)
    updated["completed_count"] = completed
    updated["checkpoint_count"] = len(latest.get("checkpoints", [])) + 1
    updated["remaining_in_batch"] = target - completed
    updated["checkpoints"] = [*latest.get("checkpoints", []), checkpoint]
    updated["status"] = "COMPLETE" if completed == target else "OPEN_PROGRESS"
    updated["next_checkpoint"] = None if completed == target else _next_checkpoint_id(checkpoint["id"])
    return updated


def _checkpoint_summary(checkpoint_dir: Path, *, seal_generation: dict[str, Any] | None = None) -> dict[str, Any]:
    raw_path = checkpoint_dir / "translation_raw_response.jsonl"
    request_records = _jsonl_records(checkpoint_dir / "translation_request.jsonl")
    raw_records = _jsonl_records(raw_path)
    normalized_records = _jsonl_records(checkpoint_dir / "translation_normalized_response.jsonl")
    # A selected generation is one immutable seal boundary.  Every output
    # derived while preparing that seal must be read from that same directory;
    # falling back to the checkpoint root would mix historical and current
    # prepared state.  RAW/request/normalization artifacts predate the seal
    # and intentionally remain rooted at the checkpoint.
    seal_dir = _checkpoint_seal_dir(checkpoint_dir, seal_generation)
    repaired_records = _jsonl_records(seal_dir / "translation_repaired_response.jsonl")
    request_rows, raw_rows = len(request_records), len(raw_records)
    normalized_rows, repaired_rows = len(normalized_records), len(repaired_records)
    # A bounded Human initial candidate is authoritative membership but is
    # deliberately never provider RAW.  Count only its versioned, QA-clean
    # artifact; every other row remains subject to the normal RAW invariant.
    human_dir = checkpoint_dir / "human_approved_candidates"
    human_initial = []
    if human_dir.is_dir():
        for path in sorted(human_dir.glob("*.json")):
            record = _load_json(path, "CHECKPOINT_HUMAN_INITIAL_INVALID")
            if (
                record.get("origin") == "HUMAN_INITIAL_CANDIDATE"
                and record.get("artifact_type") == "PRODUCTION_HUMAN_APPROVED_CANDIDATE"
                and record.get("semantic_terminal") == "HUMAN_APPROVED"
                and isinstance(record.get("working"), dict)
                and bool((record.get("qa") or {}).get("passed"))
            ):
                human_initial.append(record)
    request_uids = _unique_unit_uids(request_records, "REQUEST")
    raw_uids = _unique_unit_uids(raw_records, "RAW")
    human_uids = _unique_human_initial_uids(human_initial)
    candidate_uids = raw_uids | human_uids
    # Completeness is frozen-plan membership, not provider provenance count.
    # Human initial candidates are valid membership only at their own exact
    # unit UID, and may never collide with a provider RAW candidate.
    if raw_uids & human_uids or candidate_uids != request_uids:
        raise ValueError("CHECKPOINT_CANDIDATE_MEMBERSHIP_MISMATCH")
    candidate_rows = len(candidate_uids)
    if not request_rows or candidate_rows != request_rows or normalized_rows + len(human_initial) != request_rows or repaired_rows != request_rows:
        raise ValueError("CHECKPOINT_ARTIFACT_COUNT_MISMATCH")

    raw_qa = _load_json(checkpoint_dir / "translation_raw_qa_report.json", "CHECKPOINT_RAW_QA_MISSING")
    normalized_qa = _load_json(seal_dir / "translation_normalized_qa_report.json", "CHECKPOINT_NORMALIZED_QA_MISSING")
    final_qa = _load_json(seal_dir / "translation_final_qa_report.json", "CHECKPOINT_FINAL_QA_MISSING")
    normalization = _load_json(checkpoint_dir / "translation_normalization_report.json", "CHECKPOINT_NORMALIZATION_MISSING")
    review = _load_json(seal_dir / "semantic_review_report.json", "CHECKPOINT_REVIEW_MISSING")
    actual_count = request_rows
    _validate_qa_report(raw_qa, actual_count, "RAW")
    _validate_qa_report(normalized_qa, actual_count, "NORMALIZED")
    _validate_qa_report(final_qa, actual_count, "FINAL")
    raw_sha = sha256(raw_path.read_bytes()).hexdigest()
    raw_artifact = normalization.get("raw_artifact", {})
    if raw_artifact.get("sha256_before") != raw_sha or raw_artifact.get("sha256_after") != raw_sha:
        raise ValueError("CHECKPOINT_RAW_SHA_MISMATCH")
    if normalization.get("input_unit_count") + len(human_initial) != actual_count:
        raise ValueError("CHECKPOINT_NORMALIZATION_COUNT_MISMATCH")
    reviewed = sum(int(review.get(key, 0)) for key in (
        "accept_count", "material_repair_count", "human_required_count",
        "reviewer_contract_error_count", "not_reviewed_deterministic_fail_count",
    ))
    if reviewed != actual_count:
        raise ValueError(f"CHECKPOINT_REVIEW_COUNT_MISMATCH: {reviewed}!={actual_count}")
    summary = {
        "id": checkpoint_dir.name,
        "actual_count": actual_count,
        "raw_sha256": raw_sha,
        "raw_pass": raw_qa["pass_count"],
        "final_pass": final_qa["pass_count"],
        "normalization_changed": normalization["normalized_unit_count"],
    }
    if seal_generation is not None:
        evidence = seal_dir / "checkpoint_evidence.json"
        if not evidence.is_file():
            raise ValueError("CHECKPOINT_SEAL_GENERATION_EVIDENCE_MISSING")
        if "finalization_input_fingerprint" in seal_generation:
            prepared_state = _load_json(seal_dir / "prepared_state.json", "CHECKPOINT_SEAL_GENERATION_PREPARED_STATE_MISSING")
            if prepared_state.get("finalization_input_fingerprint") != seal_generation["finalization_input_fingerprint"]:
                raise ValueError("CHECKPOINT_SEAL_GENERATION_INPUT_FINGERPRINT_MISMATCH")
        summary["seal_generation"] = {
            **seal_generation,
            "checkpoint_evidence_sha256": manifest_sha256(evidence),
            "final_qa_sha256": manifest_sha256(seal_dir / "translation_final_qa_report.json"),
        }
    return summary


def _checkpoint_seal_dir(checkpoint_dir: Path, seal_generation: dict[str, Any] | None) -> Path:
    if seal_generation is None:
        return checkpoint_dir
    relative = seal_generation.get("path")
    if not isinstance(relative, str):
        raise ValueError("CHECKPOINT_SEAL_GENERATION_INVALID")
    seal_dir = (checkpoint_dir / relative).resolve()
    if checkpoint_dir.resolve() not in seal_dir.parents:
        raise ValueError("CHECKPOINT_SEAL_GENERATION_PATH_ESCAPE")
    return seal_dir


def _load_json(path: Path, error: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{error}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{error}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{error}: expected JSON object at {path}")
    return value


def _jsonl_count(path: Path) -> int:
    return len(_jsonl_records(path))


def _jsonl_records(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    except FileNotFoundError as exc:
        raise ValueError(f"CHECKPOINT_ARTIFACT_MISSING: {path}") from exc
    records: list[dict[str, Any]] = []
    for line in rows:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CHECKPOINT_INVALID_JSONL: {path}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"CHECKPOINT_INVALID_JSONL: {path}")
        records.append(record)
    return records


def _unique_unit_uids(records: list[dict[str, Any]], stage: str) -> set[str]:
    uids = [record.get("unit_uid") for record in records]
    if any(not isinstance(uid, str) or not uid for uid in uids) or len(set(uids)) != len(uids):
        raise ValueError(f"CHECKPOINT_{stage}_UNIT_UIDS_INVALID")
    return set(uids)


def _unique_human_initial_uids(records: list[dict[str, Any]]) -> set[str]:
    uids = [record.get("unit_uid") for record in records]
    if any(not isinstance(uid, str) or not uid for uid in uids) or len(set(uids)) != len(uids):
        raise ValueError("CHECKPOINT_HUMAN_INITIAL_UIDS_INVALID")
    return set(uids)


def _validate_qa_report(report: dict[str, Any], actual_count: int, stage: str) -> None:
    records = report.get("records")
    if not isinstance(records, list) or len(records) != actual_count:
        raise ValueError(f"CHECKPOINT_{stage}_QA_RECORD_COUNT_MISMATCH")
    passed = sum(bool(record.get("passed")) for record in records)
    if report.get("pass_count") != passed or report.get("fail_count") != actual_count - passed:
        raise ValueError(f"CHECKPOINT_{stage}_QA_SUMMARY_MISMATCH")


def _next_checkpoint_id(checkpoint_id: str) -> str:
    prefix, separator, suffix = checkpoint_id.rpartition("_")
    if not prefix or not separator or not suffix.isdigit():
        raise ValueError(f"CHECKPOINT_ID_INVALID: {checkpoint_id}")
    return f"{prefix}_{int(suffix) + 1:0{len(suffix)}d}"
