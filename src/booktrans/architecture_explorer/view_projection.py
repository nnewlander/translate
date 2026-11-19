from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError


VIEW_PROJECTION_SCHEMA = "architecture-explorer.view-projection.v1"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorerInputError(f"View Projection {field} must be a non-empty string")
    return value


def apply_view_projection(explorer: dict[str, Any]) -> dict[str, Any]:
    """Adapt the current authoritative inputs into a read-only View Projection."""

    result = deepcopy(explorer)
    architecture = result.get("architecture")
    navigation = result.get("navigation")
    if not isinstance(architecture, dict):
        raise ExplorerInputError("View Projection requires Architecture Explorer data")
    if navigation is None:
        navigation = {
            "root": {"id": "top-level", "name": "整体架构"},
            "drilldowns": [],
        }
    if not isinstance(navigation, dict):
        raise ExplorerInputError("View Projection navigation must be an object")

    modules = architecture.get("modules")
    relationships = architecture.get("relationships", [])
    drilldowns = navigation.get("drilldowns")
    root = navigation.get("root")
    if not isinstance(modules, list) or not isinstance(relationships, list):
        raise ExplorerInputError("View Projection requires architecture indexes")
    if not isinstance(drilldowns, list) or not isinstance(root, dict):
        raise ExplorerInputError("View Projection requires navigation Views")

    root_view_id = _text(root.get("id"), "root View id")
    root_name = _text(root.get("name"), "root View display name")
    drilldown_by_parent: dict[str, dict[str, Any]] = {}
    for drilldown in drilldowns:
        if not isinstance(drilldown, dict):
            raise ExplorerInputError("View Projection drilldown must be an object")
        parent_id = _text(drilldown.get("parent_module_id"), "scope Node id")
        if parent_id in drilldown_by_parent:
            raise ExplorerInputError(f"View Projection View id must be unique: {parent_id}")
        drilldown_by_parent[parent_id] = drilldown

    node_index: dict[str, dict[str, Any]] = {}
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise ExplorerInputError("View Projection Node must be an object")
        node_id = _text(module.get("id"), "Node id")
        if node_id in node_index:
            raise ExplorerInputError(f"View Projection Node id must be unique: {node_id}")
        node_index[node_id] = {
            "record_source": "architecture.modules",
            "record_index": index,
        }

    for owner_view_id, drilldown in drilldown_by_parent.items():
        children = drilldown.get("children")
        if not isinstance(children, list):
            raise ExplorerInputError(f"View Projection View needs Appearances: {owner_view_id}")
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                raise ExplorerInputError("View Projection Node must be an object")
            node_id = _text(child.get("id"), "Node id")
            if node_id in node_index:
                raise ExplorerInputError(
                    f"View Projection Node id must be globally unique: {node_id}"
                )
            node_index[node_id] = {
                "record_source": "navigation.drilldowns.children",
                "owner_view_id": owner_view_id,
                "record_index": index,
            }

    relationship_index: dict[str, dict[str, Any]] = {}
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            raise ExplorerInputError("View Projection Relationship must be an object")
        relationship_id = _text(relationship.get("id"), "Relationship id")
        if relationship_id in relationship_index:
            raise ExplorerInputError(
                f"View Projection Relationship id must be unique: {relationship_id}"
            )
        relationship_index[relationship_id] = {
            "record_source": "architecture.relationships",
            "record_index": index,
            "semantic_family": "formal-relationship",
        }

    for owner_view_id, drilldown in drilldown_by_parent.items():
        collaborations = drilldown.get("collaborations", [])
        if not isinstance(collaborations, list):
            raise ExplorerInputError(
                f"View Projection View relationships must be a list: {owner_view_id}"
            )
        for index, collaboration in enumerate(collaborations):
            if not isinstance(collaboration, dict):
                raise ExplorerInputError("View Projection Relationship must be an object")
            relationship_id = _text(collaboration.get("id"), "Relationship id")
            if relationship_id in relationship_index:
                raise ExplorerInputError(
                    "View Projection Relationship id must be globally unique: "
                    f"{relationship_id}"
                )
            relationship_index[relationship_id] = {
                "record_source": "navigation.drilldowns.collaborations",
                "owner_view_id": owner_view_id,
                "record_index": index,
                "semantic_family": "candidate-collaboration",
            }

    module_ids = [_text(module.get("id"), "Node id") for module in modules]
    module_id_set = set(module_ids)
    unknown = sorted(set(drilldown_by_parent) - module_id_set)
    if unknown:
        raise ExplorerInputError(
            f"View Projection View scope must be a top-level module: {unknown[0]}"
        )

    view_index: dict[str, dict[str, Any]] = {}
    view_index[root_view_id] = {
        "id": root_view_id,
        "display_name": root_name,
        "perspective": "logical-architecture",
        "coverage_note": "已批准的逻辑职责，与源码覆盖核验分别呈现。",
        "appearances": [
            (
                {
                    "node_id": module_id,
                    "primary_action": {
                        "kind": "open-view",
                        "target_view_id": module_id,
                        "destination_label": _text(
                            drilldown_by_parent[module_id].get("name"),
                            "View display name",
                        ),
                    },
                }
                if module_id in drilldown_by_parent
                else {"node_id": module_id}
            )
            for module_id in module_ids
        ],
        "relationship_ids": [
            _text(relationship.get("id"), "Relationship id")
            for relationship in relationships
        ],
    }

    for module_id in module_ids:
        if module_id not in drilldown_by_parent:
            continue
        drilldown = drilldown_by_parent[module_id]
        children = drilldown["children"]
        collaborations = drilldown.get("collaborations", [])
        view_index[module_id] = {
            "id": module_id,
            "display_name": _text(drilldown.get("name"), "View display name"),
            "perspective": "responsibility",
            "scope_node_id": module_id,
            "coverage_note": _text(drilldown.get("coverage_note"), "coverage note"),
            "appearances": [
                {"node_id": _text(child.get("id"), "Appearance Node id")}
                for child in children
            ],
            "relationship_ids": [
                _text(item.get("id"), "visible Relationship id")
                for item in collaborations
            ],
        }

    for view_id, view in view_index.items():
        seen_nodes: set[str] = set()
        visible_nodes = {
            appearance["node_id"] for appearance in view["appearances"]
        }
        for appearance in view["appearances"]:
            node_id = appearance["node_id"]
            if node_id not in node_index:
                raise ExplorerInputError(
                    f"View Projection references unknown Node: {view_id}:{node_id}"
                )
            if node_id in seen_nodes:
                raise ExplorerInputError(
                    f"View Projection repeats one Node in a View: {view_id}:{node_id}"
                )
            seen_nodes.add(node_id)
            action = appearance.get("primary_action")
            if action is not None and action["target_view_id"] not in view_index:
                raise ExplorerInputError(
                    "View Projection references unknown target View: "
                    f"{action['target_view_id']}"
                )
        for relationship_id in view["relationship_ids"]:
            reference = relationship_index.get(relationship_id)
            if reference is None:
                raise ExplorerInputError(
                    f"View Projection references unknown Relationship: {relationship_id}"
                )
            if reference["record_source"] == "architecture.relationships":
                record = relationships[reference["record_index"]]
                endpoints = (record.get("source_module"), record.get("target_module"))
            else:
                owner = drilldown_by_parent[reference["owner_view_id"]]
                record = owner["collaborations"][reference["record_index"]]
                endpoints = (
                    record.get("source_node_id"),
                    record.get("target_node_id"),
                )
            if any(endpoint not in visible_nodes for endpoint in endpoints):
                raise ExplorerInputError(
                    "View Projection Relationship endpoints must be visible in its View: "
                    f"{view_id}:{relationship_id}"
                )

    projection = {
        "schema": VIEW_PROJECTION_SCHEMA,
        "root_view_id": root_view_id,
        "node_index": node_index,
        "relationship_index": relationship_index,
        "view_index": view_index,
    }
    canonical = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["view_projection"] = projection
    result["view_projection_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


__all__ = ["VIEW_PROJECTION_SCHEMA", "apply_view_projection"]
