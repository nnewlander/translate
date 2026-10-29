from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
from typing import Any


SCANNER_VERSION = "architecture-explorer-t1/1.0"
SNAPSHOT_SCHEMA = "architecture-explorer.snapshot.v1"
MAPPING_SCHEMA = "architecture-explorer.module-map.v1"
EXPLORER_SCHEMA = "architecture-explorer.data.v1"
COVERAGE_STATUSES = {"unverified", "partial", "verified"}
APPROVAL_STATUS = "approved"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REVISION_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_PORCELAIN_STATUS_COMBINATIONS = frozenset(
    {
        " M",
        " T",
        " A",
        " D",
        " R",
        " C",
        "M ",
        "MM",
        "MT",
        "MD",
        "T ",
        "TM",
        "TT",
        "TD",
        "A ",
        "AM",
        "AT",
        "AD",
        "D ",
        "R ",
        "RM",
        "RT",
        "RD",
        "C ",
        "CM",
        "CT",
        "CD",
        "DD",
        "AU",
        "UD",
        "UA",
        "DU",
        "AA",
        "UU",
        "??",
    }
)


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
    return completed.stdout.rstrip("\r\n")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    """Return whether an existing Windows path redirects through a reparse point."""

    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _validate_porcelain_v1_record(line: str) -> None:
    if len(line) < 4 or line[2] != " " or "\n" in line or "\r" in line:
        raise ExplorerInputError("Snapshot contains an invalid porcelain record")
    status = line[:2]
    if status not in _PORCELAIN_STATUS_COMBINATIONS:
        raise ExplorerInputError("Snapshot contains an invalid porcelain status combination")
    payload = line[3:]
    if not payload:
        raise ExplorerInputError("Snapshot porcelain record must identify a path")
    if "R" in status or "C" in status:
        original, separator, destination = payload.rpartition(" -> ")
        if not separator or not original or not destination:
            raise ExplorerInputError("Snapshot rename or copy record must identify both paths")


def validate_cli_output_path(
    repository: Path,
    requested_path: Path,
    *,
    directory: bool,
    inputs: tuple[Path, ...] = (),
    output_files: tuple[str, ...] = (),
) -> Path:
    """Resolve one CLI output below the repository's non-escaping output root."""

    repository = repository.resolve()
    if requested_path.is_absolute():
        raise ExplorerInputError("CLI output paths must be repository-relative")
    if not requested_path.parts or ".." in requested_path.parts:
        raise ExplorerInputError("CLI output paths cannot contain '..'")

    artifact_root_path = repository / "output"
    if (
        artifact_root_path.is_symlink()
        or artifact_root_path.is_junction()
        or _is_reparse_point(artifact_root_path)
    ):
        raise ExplorerInputError("Repository output root cannot be a link, junction, or reparse point")
    artifact_root = artifact_root_path.resolve()
    if not _is_within(artifact_root, repository):
        raise ExplorerInputError("Repository output root escapes through a link or junction")
    candidate = (repository / requested_path).resolve()
    if not _is_within(candidate, artifact_root):
        raise ExplorerInputError("CLI output must be inside the repository output directory")

    for input_path in inputs:
        resolved_input = input_path.resolve()
        if candidate == resolved_input:
            raise ExplorerInputError("CLI output overlaps an input path")
        if directory and (
            _is_within(resolved_input, candidate) or _is_within(candidate, resolved_input)
        ):
            raise ExplorerInputError("CLI output directory overlaps an input path")

    if directory and candidate.exists() and not candidate.is_dir():
        raise ExplorerInputError("CLI output directory target is an existing file")
    if not directory and candidate.exists() and not candidate.is_file():
        raise ExplorerInputError("CLI output file target is an existing directory")
    for filename in output_files:
        child = (candidate / filename).resolve()
        if not _is_within(child, candidate):
            raise ExplorerInputError("CLI output file escapes through a link or junction")
        if child.exists() and not child.is_file():
            raise ExplorerInputError("CLI output file target is an existing directory")
    return candidate


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
    resolved_inputs: set[str] = set()
    for relative_path in normalized:
        source = _explicit_file(repository, relative_path)
        source_identity = str(source).casefold()
        if source_identity in resolved_inputs:
            raise ExplorerInputError("Source inputs must resolve to unique files")
        resolved_inputs.add(source_identity)
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
    required = {
        "schema",
        "revision",
        "worktree",
        "scanner_version",
        "scanned_at",
        "input_files",
        "facts_sha256",
    }
    missing = sorted(required - snapshot.keys())
    if missing:
        raise ExplorerInputError(f"Snapshot is missing fields: {', '.join(missing)}")
    unexpected = sorted(snapshot.keys() - required)
    if unexpected:
        raise ExplorerInputError(f"Snapshot has unexpected fields: {', '.join(unexpected)}")

    revision = snapshot["revision"]
    if not isinstance(revision, str) or _REVISION_PATTERN.fullmatch(revision) is None:
        raise ExplorerInputError("Snapshot revision must be a full hexadecimal Git object id")

    scanner_version = snapshot["scanner_version"]
    if scanner_version != SCANNER_VERSION:
        raise ExplorerInputError(f"Unsupported snapshot scanner version: {scanner_version!r}")

    scanned_at = snapshot["scanned_at"]
    if not isinstance(scanned_at, str) or "T" not in scanned_at or not scanned_at.endswith("Z"):
        raise ExplorerInputError("Snapshot scan time must be an ISO-8601 UTC string")
    try:
        parsed_scan_time = datetime.fromisoformat(scanned_at[:-1] + "+00:00")
    except ValueError as error:
        raise ExplorerInputError("Snapshot scan time must be an ISO-8601 UTC string") from error
    if parsed_scan_time.utcoffset() != timezone.utc.utcoffset(parsed_scan_time):
        raise ExplorerInputError("Snapshot scan time must use UTC")

    worktree = snapshot["worktree"]
    if not isinstance(worktree, dict) or set(worktree) != {"state", "porcelain"}:
        raise ExplorerInputError("Snapshot worktree must contain only state and porcelain")
    state = worktree["state"]
    porcelain = worktree["porcelain"]
    if state not in {"clean", "dirty"}:
        raise ExplorerInputError("Snapshot worktree state must be clean or dirty")
    if not isinstance(porcelain, list) or not all(isinstance(line, str) for line in porcelain):
        raise ExplorerInputError("Snapshot porcelain must be a list of strings")
    for line in porcelain:
        _validate_porcelain_v1_record(line)
    if (state == "clean") != (not porcelain):
        raise ExplorerInputError("Snapshot worktree state contradicts its porcelain records")

    input_files = snapshot["input_files"]
    if not isinstance(input_files, list) or not input_files:
        raise ExplorerInputError("Snapshot input files must be a non-empty list")
    input_paths: list[str] = []
    for position, item in enumerate(input_files, start=1):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise ExplorerInputError(f"Snapshot input file {position} has an invalid structure")
        path = item["path"]
        if not isinstance(path, str) or not path or "\\" in path:
            raise ExplorerInputError(f"Snapshot input file {position} has an invalid path")
        pure_path = PurePosixPath(path)
        if (
            pure_path.is_absolute()
            or bool(PureWindowsPath(path).drive)
            or any(part in {"", ".", ".."} for part in pure_path.parts)
            or pure_path.as_posix() != path
        ):
            raise ExplorerInputError(f"Snapshot input file {position} must be normalized and relative")
        sha256 = item["sha256"]
        if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
            raise ExplorerInputError(f"Snapshot input file {position} has an invalid SHA-256")
        size_bytes = item["size_bytes"]
        if type(size_bytes) is not int or size_bytes < 0:
            raise ExplorerInputError(f"Snapshot input file {position} has an invalid size")
        input_paths.append(path)
    if len({path.casefold() for path in input_paths}) != len(input_paths):
        raise ExplorerInputError("Snapshot input file paths must be unique")
    if input_paths != sorted(input_paths):
        raise ExplorerInputError("Snapshot input file paths must be sorted")

    facts_sha256 = snapshot["facts_sha256"]
    if not isinstance(facts_sha256, str) or _SHA256_PATTERN.fullmatch(facts_sha256) is None:
        raise ExplorerInputError("Snapshot facts hash must be a lowercase SHA-256")
    deterministic_facts = {key: snapshot[key] for key in ("revision", "worktree", "scanner_version", "input_files")}
    expected = _sha256_bytes(_canonical_bytes(deterministic_facts))
    if facts_sha256 != expected:
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
