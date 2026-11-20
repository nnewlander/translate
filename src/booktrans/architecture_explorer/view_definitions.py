from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError


VIEW_DEFINITIONS_SCHEMA = "architecture-explorer.view-definitions.v1"


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
    if not isinstance(views, list) or not views:
        raise ExplorerInputError("View definitions need at least one View")
    if not isinstance(entry_actions, list):
        raise ExplorerInputError("View definitions entry actions must be a list")
    result = deepcopy(explorer)
    result["view_definitions"] = deepcopy(definitions)
    canonical = json.dumps(
        definitions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["view_definitions_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


__all__ = ["VIEW_DEFINITIONS_SCHEMA", "apply_view_definitions"]
