from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


SCANNER_VERSION = "architecture-explorer-t1/1.0"
SNAPSHOT_SCHEMA = "architecture-explorer.snapshot.v1"
MAPPING_SCHEMA = "architecture-explorer.module-map.v1"
EXPLORER_SCHEMA = "architecture-explorer.data.v1"
COVERAGE_STATUSES = {"unverified", "partial", "verified"}
APPROVAL_STATUS = "approved"


class ExplorerInputError(ValueError):
    """Raised when an explicit Explorer input violates the T1 contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError(f"Invalid UTF-8 JSON input: {path}") from error
    if not isinstance(value, dict):
        raise ExplorerInputError(f"Expected a JSON object: {path}")
    return value, _sha256_bytes(raw)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ExplorerInputError(f"Git metadata unavailable: {detail}")
    return completed.stdout.strip()


def _explicit_file(repository: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ExplorerInputError(f"Source input must be repository-relative: {relative_path!r}")
    repository = repository.resolve()
    candidate = (repository / relative_path).resolve()
    try:
        candidate.relative_to(repository)
    except ValueError as error:
        raise ExplorerInputError(f"Source input escapes repository: {relative_path}") from error
    if not candidate.is_file():
        raise ExplorerInputError(f"Source input is not a file: {relative_path}")
    return candidate


def scan_snapshot(
    repository: Path,
    source_files: list[str],
    *,
    scanned_at: str | None = None,
) -> dict[str, Any]:
    """Read only the requested files and Git metadata for one source snapshot."""

    repository = repository.resolve()
    if not source_files:
        raise ExplorerInputError("At least one explicit source input is required")
    normalized = sorted(set(Path(item).as_posix() for item in source_files))
    if len(normalized) != len(source_files):
        raise ExplorerInputError("Source inputs must be unique")

    inputs = []
    for relative_path in normalized:
        source = _explicit_file(repository, relative_path)
        raw = source.read_bytes()
        inputs.append(
            {
                "path": relative_path,
                "sha256": _sha256_bytes(raw),
                "size_bytes": len(raw),
            }
        )

    # "normal" reports untracked directory roots without enumerating their contents.
    # This keeps workspace-state reporting accurate without walking large ignored or
    # unrelated artifact trees.
    porcelain = _git(repository, "status", "--porcelain=v1", "--untracked-files=normal")
    worktree = {
        "state": "dirty" if porcelain else "clean",
        "porcelain": porcelain.splitlines(),
    }
    deterministic_facts = {
        "revision": _git(repository, "rev-parse", "HEAD"),
        "worktree": worktree,
        "scanner_version": SCANNER_VERSION,
        "input_files": inputs,
    }
    return {
        "schema": SNAPSHOT_SCHEMA,
        **deterministic_facts,
        "scanned_at": scanned_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "facts_sha256": _sha256_bytes(_canonical_bytes(deterministic_facts)),
    }


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ExplorerInputError(f"Unsupported snapshot schema: {snapshot.get('schema')!r}")
    required = {"revision", "worktree", "scanner_version", "scanned_at", "input_files", "facts_sha256"}
    missing = sorted(required - snapshot.keys())
    if missing:
        raise ExplorerInputError(f"Snapshot is missing fields: {', '.join(missing)}")
    deterministic_facts = {key: snapshot[key] for key in ("revision", "worktree", "scanner_version", "input_files")}
    expected = _sha256_bytes(_canonical_bytes(deterministic_facts))
    if snapshot["facts_sha256"] != expected:
        raise ExplorerInputError("Snapshot facts hash does not match its contents")


def _validate_mapping(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    if mapping.get("schema") != MAPPING_SCHEMA:
        raise ExplorerInputError(f"Unsupported mapping schema: {mapping.get('schema')!r}")
    modules = mapping.get("modules")
    if not isinstance(modules, list) or len(modules) != 7:
        raise ExplorerInputError("T1 mapping must define exactly seven modules")
    ids: set[str] = set()
    names: set[str] = set()
    validated = []
    for position, module in enumerate(modules, start=1):
        if not isinstance(module, dict):
            raise ExplorerInputError(f"Module {position} must be an object")
        module_id = module.get("id")
        name = module.get("name")
        responsibility = module.get("responsibility")
        approval = module.get("logical_definition")
        coverage = module.get("source_coverage")
        if not all(isinstance(value, str) and value.strip() for value in (module_id, name, responsibility)):
            raise ExplorerInputError(f"Module {position} needs non-empty id, name, and responsibility")
        if module_id in ids or name in names:
            raise ExplorerInputError(f"Module ids and names must be unique: {module_id!r}")
        ids.add(module_id)
        names.add(name)
        if not isinstance(approval, dict) or approval.get("status") != APPROVAL_STATUS:
            raise ExplorerInputError(f"Module {module_id} must have an approved logical definition")
        if not isinstance(approval.get("approved_in"), str) or not approval["approved_in"].strip():
            raise ExplorerInputError(f"Module {module_id} must identify its approval source")
        if not isinstance(coverage, dict) or coverage.get("status") not in COVERAGE_STATUSES:
            raise ExplorerInputError(f"Module {module_id} has an invalid source coverage status")
        evidence = coverage.get("evidence")
        if not isinstance(evidence, list):
            raise ExplorerInputError(f"Module {module_id} source coverage evidence must be a list")
        if coverage["status"] == "unverified" and evidence:
            raise ExplorerInputError(f"Unverified module {module_id} cannot claim source evidence")
        if coverage["status"] in {"partial", "verified"} and not evidence:
            raise ExplorerInputError(f"{coverage['status'].title()} module {module_id} needs reviewed evidence")
        validated.append(
            {
                "id": module_id,
                "name": name,
                "responsibility": responsibility,
                "logical_definition": {
                    "status": APPROVAL_STATUS,
                    "approved_in": approval["approved_in"],
                },
                "source_coverage": {
                    "status": coverage["status"],
                    "evidence": evidence,
                },
            }
        )
    return validated


def build_explorer(snapshot_path: Path, mapping_path: Path) -> dict[str, Any]:
    """Combine independently maintained snapshot facts and reviewed mapping."""

    snapshot, snapshot_sha256 = _read_json(snapshot_path)
    mapping, mapping_sha256 = _read_json(mapping_path)
    _validate_snapshot(snapshot)
    modules = _validate_mapping(mapping)
    architecture = {"modules": modules}
    deterministic_content = {
        "snapshot_facts_sha256": snapshot["facts_sha256"],
        "mapping_sha256": mapping_sha256,
        "architecture": architecture,
    }
    return {
        "schema": EXPLORER_SCHEMA,
        "snapshot": snapshot,
        "architecture": architecture,
        "generation_inputs": {
            "snapshot": {"path": snapshot_path.name, "sha256": snapshot_sha256},
            "mapping": {"path": mapping_path.name, "sha256": mapping_sha256},
        },
        "deterministic_content_sha256": _sha256_bytes(_canonical_bytes(deterministic_content)),
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_site(data: dict[str, Any], output_directory: Path, web_assets: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    write_json(output_directory / "explorer-data.json", data)
    javascript_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (output_directory / "explorer-data.js").write_text(
        f"window.ARCHITECTURE_EXPLORER_DATA = {javascript_data};\n",
        encoding="utf-8",
    )
    for filename in ("index.html", "app.js", "styles.css"):
        shutil.copyfile(web_assets / filename, output_directory / filename)
