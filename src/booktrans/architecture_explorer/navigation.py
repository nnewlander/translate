from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError


NAVIGATION_SCHEMA = "architecture-explorer.navigation.v1"
REVIEW_STATUSES = {"candidate"}
SOURCE_ASSIGNMENT_STATUSES = {"unverified", "partial"}


def _non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorerInputError(f"T4 navigation {field} must be a non-empty string")
    return value


def apply_navigation(explorer: dict[str, Any], navigation: dict[str, Any]) -> dict[str, Any]:
    """Validate and attach presentation-only T4 navigation to T3 explorer data.

    Relationship records remain owned by T3. T4 stores only stable references to
    those records and never copies their evidence, review state, or semantics.
    """

    if navigation.get("schema") != NAVIGATION_SCHEMA:
        raise ExplorerInputError("Unsupported T4 navigation schema")
    modules = explorer.get("architecture", {}).get("modules", [])
    relationships = explorer.get("architecture", {}).get("relationships", [])
    module_ids = {item.get("id") for item in modules if isinstance(item, dict)}
    relationship_ids = {item.get("id") for item in relationships if isinstance(item, dict)}

    root = navigation.get("root")
    if not isinstance(root, dict):
        raise ExplorerInputError("T4 navigation root must be an object")
    validated_root = {
        "id": _non_empty(root.get("id"), "root id"),
        "name": _non_empty(root.get("name"), "root name"),
    }
    if validated_root["id"] != "top-level":
        raise ExplorerInputError("T4 navigation root id must remain top-level")

    raw_drilldowns = navigation.get("drilldowns")
    if not isinstance(raw_drilldowns, list) or len(raw_drilldowns) != 1:
        raise ExplorerInputError("T4 navigation must define exactly one drilldown")
    parent_ids: set[str] = set()
    child_ids: set[str] = set()
    validated_drilldowns = []
    for raw in raw_drilldowns:
        if not isinstance(raw, dict):
            raise ExplorerInputError("T4 drilldown must be an object")
        parent_id = _non_empty(raw.get("parent_module_id"), "parent module id")
        if parent_id != "production-control":
            raise ExplorerInputError("T4 drilldown is limited to production-control")
        if parent_id not in module_ids:
            raise ExplorerInputError(f"T4 drilldown references unknown module: {parent_id}")
        if parent_id in parent_ids:
            raise ExplorerInputError(f"T4 drilldown parent must be unique: {parent_id}")
        parent_ids.add(parent_id)
        raw_children = raw.get("children")
        if not isinstance(raw_children, list) or not raw_children:
            raise ExplorerInputError(f"T4 drilldown {parent_id} needs children")
        children = []
        for raw_child in raw_children:
            if not isinstance(raw_child, dict):
                raise ExplorerInputError("T4 child responsibility must be an object")
            child_id = _non_empty(raw_child.get("id"), "child id")
            if child_id in child_ids or child_id in module_ids:
                raise ExplorerInputError(f"T4 child id must be globally unique: {child_id}")
            child_ids.add(child_id)
            review_status = raw_child.get("review_status")
            if review_status not in REVIEW_STATUSES:
                raise ExplorerInputError("T4 unreviewed responsibilities must remain candidate")
            assignment = raw_child.get("source_assignment")
            if not isinstance(assignment, dict):
                raise ExplorerInputError(f"T4 child {child_id} needs source assignment status")
            assignment_status = assignment.get("status")
            if assignment_status not in SOURCE_ASSIGNMENT_STATUSES:
                raise ExplorerInputError(f"T4 child {child_id} has invalid source assignment status")
            references = assignment.get("relationship_ids")
            if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
                raise ExplorerInputError(f"T4 child {child_id} relationship ids must be a list")
            if len(set(references)) != len(references):
                raise ExplorerInputError(f"T4 child {child_id} relationship ids must be unique")
            unknown = [item for item in references if item not in relationship_ids]
            if unknown:
                raise ExplorerInputError(f"T4 child {child_id} references unknown relationship: {unknown[0]}")
            if assignment_status == "unverified" and references:
                raise ExplorerInputError(f"Unverified T4 child {child_id} cannot claim relationship evidence")
            if assignment_status == "partial" and not references:
                raise ExplorerInputError(f"Partially verified T4 child {child_id} needs relationship evidence")
            children.append(
                {
                    "id": child_id,
                    "name": _non_empty(raw_child.get("name"), "child name"),
                    "responsibility": _non_empty(raw_child.get("responsibility"), "child responsibility"),
                    "review_status": "candidate",
                    "source_assignment": {
                        "status": assignment_status,
                        "note": _non_empty(assignment.get("note"), "source assignment note"),
                        "relationship_ids": list(references),
                    },
                }
            )
        validated_drilldowns.append(
            {
                "parent_module_id": parent_id,
                "name": _non_empty(raw.get("name"), "drilldown name"),
                "coverage_note": _non_empty(raw.get("coverage_note"), "coverage note"),
                "children": children,
            }
        )

    history = navigation.get("history_and_auxiliary")
    if not isinstance(history, dict):
        raise ExplorerInputError("T4 history and auxiliary section must be an object")
    raw_items = history.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ExplorerInputError("T4 history and auxiliary section needs at least one item")
    history_ids: set[str] = set()
    items = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ExplorerInputError("T4 history item must be an object")
        item_id = _non_empty(raw_item.get("id"), "history item id")
        if item_id in history_ids:
            raise ExplorerInputError(f"T4 history item id must be unique: {item_id}")
        history_ids.add(item_id)
        if raw_item.get("classification") != "unclassified":
            raise ExplorerInputError("Evidence-insufficient T4 history items must remain unclassified")
        items.append(
            {
                "id": item_id,
                "name": _non_empty(raw_item.get("name"), "history item name"),
                "classification": "unclassified",
                "note": _non_empty(raw_item.get("note"), "history item note"),
            }
        )

    result = deepcopy(explorer)
    result["navigation"] = {
        "schema": NAVIGATION_SCHEMA,
        "root": validated_root,
        "drilldowns": validated_drilldowns,
        "history_and_auxiliary": {
            "title": _non_empty(history.get("title"), "history title"),
            "description": _non_empty(history.get("description"), "history description"),
            "default_expanded": False,
            "items": items,
        },
    }
    navigation_bytes = json.dumps(
        result["navigation"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["navigation_content_sha256"] = hashlib.sha256(navigation_bytes).hexdigest()
    return result


__all__ = ["NAVIGATION_SCHEMA", "apply_navigation"]
