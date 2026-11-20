"""Acceptance-control inputs for Architecture Explorer contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

from .content_delta import (
    FIXED_BASE_REVISION,
    ManifestValidationResult,
    read_git,
    validate_content_delta_manifest,
)
from .generator import ExplorerInputError


CONTRACT_FAMILY = "architecture-explorer-acceptance"
APPROVED_PROPOSED_V3_METADATA = {
    "contract_family": CONTRACT_FAMILY,
    "contract_version": "3",
    "contract_ref": "architecture-explorer/v3",
    "status": "proposed",
    "runtime_authority": "false",
    "authority_issue": "https://github.com/nnewlander/translate/issues/58",
    "implementation_issue": "https://github.com/nnewlander/translate/issues/59",
    "fixed_starting_baseline": FIXED_BASE_REVISION,
    "approved_spec_sha256": "0eb5f5650897fc0ba15b129a2782c09eeceda551e087cb3ff7e51ca5afbf1283",
    "inherits": "architecture-explorer/v2",
    "activation": "candidate-head-acceptance-then-contract-status-only-switch",
    "content_delta_manifest_schema": "architecture-explorer.content-delta-manifest.v1",
}


@dataclass(frozen=True)
class ContractSelection:
    current_contract: str
    target_contract: str
    target_status: str
    target_metadata: dict[str, str]


@dataclass(frozen=True)
class CandidateAcceptance:
    contracts: ContractSelection
    manifest: ManifestValidationResult


def _frontmatter_text(text: str, source: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return metadata
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ExplorerInputError(f"Invalid contract metadata in {source}: {line}")
        normalized_key = key.strip()
        if normalized_key in metadata:
            raise ExplorerInputError(
                f"Duplicate contract metadata key in {source}: {normalized_key}"
            )
        metadata[normalized_key] = value.strip()
    raise ExplorerInputError(f"Unterminated contract metadata in {source}")


def _frontmatter(path: Path) -> dict[str, str]:
    return _frontmatter_text(path.read_text(encoding="utf-8"), str(path))


def _validate_proposed_v3_status_surface(text: str) -> None:
    lines = text.splitlines()
    h1 = [line[2:].strip() for line in lines if line.startswith("# ")]
    expected_title = "Architecture Explorer v3 proposed acceptance contract"
    if h1 != [expected_title]:
        raise ExplorerInputError("Proposed v3 has an invalid structured status surface")

    if lines.count("## Contract status") != 1:
        raise ExplorerInputError("Proposed v3 has an invalid structured status surface")
    try:
        frontmatter_end = lines.index("---", 1)
        normative_start = lines.index("## Inheritance and Delta", frontmatter_end + 1)
    except ValueError as error:
        raise ExplorerInputError("Proposed v3 has an invalid structured status surface") from error
    status_region = [
        line
        for line in lines[frontmatter_end + 1 : normative_start]
        if line.strip()
    ]
    if status_region[:2] != [f"# {expected_title}", "## Contract status"]:
        raise ExplorerInputError("Proposed v3 has an invalid structured status surface")
    status: dict[str, str] = {}
    for line in status_region[2:]:
        if not line.startswith("- "):
            raise ExplorerInputError("Proposed v3 has an invalid structured status surface")
        key, separator, value = line[2:].partition(":")
        if not separator or not key.strip() or not value.strip() or key.strip() in status:
            raise ExplorerInputError("Proposed v3 has an invalid structured status surface")
        status[key.strip()] = value.strip()
    if status != {
        "current_contract": "architecture-explorer/v2",
        "target_contract": "architecture-explorer/v3",
        "target_status": "proposed",
        "effective_baseline": "absent",
    }:
        raise ExplorerInputError("Proposed v3 has an invalid structured status surface")


def _contract_metadata(repository: Path, revision: str | None) -> dict[str, dict[str, str]]:
    records: list[tuple[str, dict[str, str]]] = []
    if revision is None:
        records = [
            (str(path), _frontmatter(path))
            for path in (repository / "docs").glob("architecture-explorer*.md")
        ]
    else:
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ExplorerInputError("Acceptance Head must be a full lowercase commit SHA")
        paths = str(
            read_git(repository, "ls-tree", "-r", "--name-only", revision, "--", "docs")
        ).splitlines()
        for path in paths:
            if not Path(path).name.startswith("architecture-explorer") or not path.endswith(".md"):
                continue
            raw = read_git(repository, "show", f"{revision}:{path}", text=False)
            try:
                text = bytes(raw).decode("utf-8")
            except UnicodeDecodeError as error:
                raise ExplorerInputError(f"Contract is not UTF-8 at {revision}:{path}") from error
            records.append((f"{revision}:{path}", _frontmatter_text(text, f"{revision}:{path}")))

    contracts: dict[str, dict[str, str]] = {}
    for source, metadata in records:
        if metadata.get("contract_family") != CONTRACT_FAMILY:
            continue
        contract_ref = metadata.get("contract_ref")
        if not contract_ref or contract_ref in contracts:
            raise ExplorerInputError(f"Architecture Explorer contract refs must be unique: {source}")
        contracts[contract_ref] = metadata
    return contracts


def select_acceptance_contract(
    repository: Path,
    *,
    target_contract: str | None = None,
    revision: str | None = None,
) -> ContractSelection:
    """Select an explicit validation target without changing runtime authority."""

    contracts = _contract_metadata(repository.resolve(), revision)

    current = [
        contract_ref
        for contract_ref, metadata in contracts.items()
        if metadata.get("status") == "current"
    ]
    if len(current) != 1:
        raise ExplorerInputError("Exactly one Architecture Explorer contract must be current")

    selected_ref = target_contract or current[0]
    try:
        selected = contracts[selected_ref]
    except KeyError as error:
        raise ExplorerInputError(f"Unknown Architecture Explorer target contract: {selected_ref}") from error
    if selected.get("status") not in {"current", "proposed"}:
        raise ExplorerInputError(
            f"Architecture Explorer target contract is not eligible: {selected_ref}"
        )
    return ContractSelection(
        current_contract=current[0],
        target_contract=selected_ref,
        target_status=selected["status"],
        target_metadata=dict(selected),
    )


def validate_acceptance_target(
    repository: Path,
    *,
    head_revision: str,
    target_contract: str,
    manifest_path: str,
) -> CandidateAcceptance:
    """Validate an explicit proposed-v3 target entirely from candidate Head inputs."""

    repository = repository.resolve()
    selection = select_acceptance_contract(
        repository,
        target_contract=target_contract,
        revision=head_revision,
    )
    if selection.current_contract != "architecture-explorer/v2":
        raise ExplorerInputError("Candidate acceptance requires v2 to remain the sole current contract")
    if selection.target_contract != "architecture-explorer/v3" or selection.target_status != "proposed":
        raise ExplorerInputError("Candidate acceptance requires proposed architecture-explorer/v3")
    forbidden_proposed_fields = {"effective_baseline", "superseded_by"}
    conflicting_fields = sorted(forbidden_proposed_fields & selection.target_metadata.keys())
    if conflicting_fields:
        raise ExplorerInputError(
            "Proposed architecture-explorer/v3 cannot have status field: "
            + ", ".join(conflicting_fields)
        )
    if selection.target_metadata != APPROVED_PROPOSED_V3_METADATA:
        raise ExplorerInputError(
            "Proposed architecture-explorer/v3 does not match its approved trusted metadata"
        )
    v2_path = "docs/architecture-explorer-acceptance-v2.md"
    if read_git(repository, "show", f"{head_revision}:{v2_path}", text=False) != read_git(
        repository,
        "show",
        f"{FIXED_BASE_REVISION}:{v2_path}",
        text=False,
    ):
        raise ExplorerInputError(
            "Candidate Head must preserve the complete current v2 contract from the fixed Base"
        )
    v3_text = bytes(
        read_git(
            repository,
            "show",
            f"{head_revision}:docs/architecture-explorer-acceptance-v3.md",
            text=False,
        )
    ).decode("utf-8")
    _validate_proposed_v3_status_surface(v3_text)
    path = Path(manifest_path)
    if (
        path.is_absolute()
        or path.suffix != ".json"
        or path.as_posix() != manifest_path
        or path.parts[:2] != ("architecture-explorer", "manifests")
        or ".." in path.parts
    ):
        raise ExplorerInputError("Manifest path must be a normalized file below architecture-explorer/manifests")
    raw = read_git(repository, "show", f"{head_revision}:{manifest_path}", text=False)
    try:
        manifest: Any = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplorerInputError("Candidate Head Manifest is not valid UTF-8 JSON") from error
    result = validate_content_delta_manifest(
        repository,
        head_revision=head_revision,
        manifest=manifest,
    )
    return CandidateAcceptance(selection, result)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate one Architecture Explorer contract target")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--head-revision", required=True)
    parser.add_argument("--target-contract", required=True)
    parser.add_argument("--manifest-path", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_acceptance_target(
            args.repository,
            head_revision=args.head_revision,
            target_contract=args.target_contract,
            manifest_path=args.manifest_path,
        )
    except ExplorerInputError as error:
        print(f"ARCHITECTURE_EXPLORER_ACCEPTANCE: FAIL - {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "CONTROL_INPUTS_PASS",
                "complete_validation": "REQUIRED_SEPARATELY",
                "required_chrome": "REQUIRED_SEPARATELY",
                "current_contract": result.contracts.current_contract,
                "target_contract": result.contracts.target_contract,
                "base_revision": result.manifest.base_revision,
                "head_revision": result.manifest.head_revision,
                "manifest_sha256": result.manifest.manifest_sha256,
                "base_content_sha256": result.manifest.base_content_sha256,
                "head_content_sha256": result.manifest.head_content_sha256,
                "object_counts": result.manifest.object_counts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
