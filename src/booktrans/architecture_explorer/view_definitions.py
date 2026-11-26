from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .generator import ExplorerInputError


VIEW_DEFINITIONS_SCHEMA = "architecture-explorer.view-definitions.v1"
VIEW_DEFINITION_DIRECTORY = PurePosixPath("architecture-explorer/views")


def authoritative_view_definition_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Select the repository's recursive View-definition namespace."""

    selected: set[str] = set()
    prefix = VIEW_DEFINITION_DIRECTORY.parts
    for raw_path in paths:
        path = PurePosixPath(raw_path.replace("\\", "/"))
        if path.parts[: len(prefix)] != prefix or path.suffix.lower() != ".json":
            continue
        normalized = path.as_posix()
        if normalized in selected:
            raise ExplorerInputError(f"Duplicate View Definition path: {normalized}")
        selected.add(normalized)
    return tuple(sorted(selected))


def discover_view_definition_paths(repository: Path) -> tuple[Path, ...]:
    """Discover all authoritative View-definition files in deterministic order."""

    root = repository / Path(VIEW_DEFINITION_DIRECTORY.as_posix())
    if not root.exists():
        return ()
    relative_paths = authoritative_view_definition_paths(
        path.relative_to(repository).as_posix() for path in root.rglob("*.json")
    )
    return tuple(repository / Path(path) for path in relative_paths)


def apply_view_definitions(
    explorer: dict[str, Any], definitions: dict[str, Any]
) -> dict[str, Any]:
    """Attach presentation-only recursive View definitions for projection."""

    if not isinstance(definitions, dict):
        raise ExplorerInputError("View definitions must be an object")
    if definitions.get("schema") != VIEW_DEFINITIONS_SCHEMA:
        raise ExplorerInputError("Unsupported View definitions schema")
    views = definitions.get("views")
    entry_actions = definitions.get("entry_actions")
    if not isinstance(views, list):
        raise ExplorerInputError("View definitions Views must be a list")
    if not isinstance(entry_actions, list):
        raise ExplorerInputError("View definitions entry actions must be a list")
    if not views and not entry_actions:
        raise ExplorerInputError("View definitions need a View or entry action")
    input_canonical = json.dumps(
        definitions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result = deepcopy(explorer)
    existing = result.get("view_definitions")
    if existing is None:
        combined = {
            "schema": VIEW_DEFINITIONS_SCHEMA,
            "views": [],
            "entry_actions": [],
        }
    elif (
        not isinstance(existing, dict)
        or existing.get("schema") != VIEW_DEFINITIONS_SCHEMA
        or not isinstance(existing.get("views"), list)
        or not isinstance(existing.get("entry_actions"), list)
    ):
        raise ExplorerInputError("Existing View definitions are malformed")
    else:
        combined = deepcopy(existing)
    combined["views"].extend(deepcopy(views))
    combined["entry_actions"].extend(deepcopy(entry_actions))
    result["view_definitions"] = combined
    result.setdefault("view_definition_inputs", []).append(
        {"sha256": hashlib.sha256(input_canonical).hexdigest()}
    )
    canonical = json.dumps(
        combined, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["view_definitions_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def apply_view_definition_documents(
    explorer: dict[str, Any], definitions: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Compose independently stored View-definition documents in order."""

    result = explorer
    for document in definitions:
        result = apply_view_definitions(result, document)
    return result


__all__ = [
    "VIEW_DEFINITION_DIRECTORY",
    "VIEW_DEFINITIONS_SCHEMA",
    "apply_view_definition_documents",
    "apply_view_definitions",
    "authoritative_view_definition_paths",
    "discover_view_definition_paths",
]
