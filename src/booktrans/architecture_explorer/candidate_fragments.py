from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError
from .source_evidence import build_source_evidence_index


CANDIDATE_FRAGMENT_SCHEMA = "architecture-explorer.candidate-fragment.v1"
RECURSIVE_CANDIDATE_FRAGMENT_SCHEMA = "architecture-explorer.candidate-fragment.v2"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorerInputError(f"Candidate fragment {field} must be a non-empty string")
    return value


def _reject_decomposition_cycles(relationships: list[dict[str, Any]]) -> None:
    adjacency: dict[str, list[str]] = {}
    for relationship in relationships:
        if relationship["semantic_family"] != "candidate-decomposition":
            continue
        source = relationship["source_node_id"]
        target = relationship["target_node_id"]
        adjacency.setdefault(source, []).append(target)

    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            start = visiting.index(node_id)
            cycle = visiting[start:] + [node_id]
            raise ExplorerInputError(
                "Candidate decomposition cycle is not allowed: " + " -> ".join(cycle)
            )
        if node_id in visited:
            return
        visiting.append(node_id)
        for target_id in sorted(adjacency.get(node_id, [])):
            visit(target_id)
        visiting.pop()
        visited.add(node_id)

    for source_id in sorted(adjacency):
        visit(source_id)


def _apply_recursive_candidate_fragments(
    result: dict[str, Any],
    fragments: list[dict[str, Any]],
    known_evidence_ids: set[str],
    global_node_ids: set[object],
    global_relationship_ids: set[object],
) -> list[dict[str, Any]]:
    validated_fragments: list[dict[str, Any]] = []
    all_relationships: list[dict[str, Any]] = []
    scope_ids: set[str] = set()
    for fragment in fragments:
        scope_node_id = _text(fragment.get("scope_node_id"), "scope node id")
        if scope_node_id not in global_node_ids:
            raise ExplorerInputError(
                f"Recursive candidate fragment references unknown scope Node: {scope_node_id}"
            )
        if scope_node_id in scope_ids:
            raise ExplorerInputError(
                f"Recursive candidate fragment scope Node must be unique: {scope_node_id}"
            )
        scope_ids.add(scope_node_id)
        raw_nodes = fragment.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ExplorerInputError("Recursive candidate fragment needs nodes")
        nodes: list[dict[str, Any]] = []
        fragment_node_ids: set[str] = set()
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                raise ExplorerInputError("Recursive candidate Node must be an object")
            if raw_node.get("review_status") != "candidate":
                raise ExplorerInputError("Recursive candidate Nodes must remain candidate")
            node_id = _text(raw_node.get("id"), "node id")
            if node_id in global_node_ids or node_id in fragment_node_ids:
                raise ExplorerInputError(
                    f"Recursive candidate Node id must be globally unique: {node_id}"
                )
            evidence_ids = raw_node.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ExplorerInputError("Recursive candidate Node needs source evidence ids")
            unknown = [item for item in evidence_ids if item not in known_evidence_ids]
            if unknown:
                raise ExplorerInputError(
                    f"Recursive candidate fragment references unknown source evidence: {unknown[0]}"
                )
            node_type = _text(raw_node.get("node_type"), "node type")
            if node_type not in {"responsibility", "component"}:
                raise ExplorerInputError(
                    f"Unsupported recursive candidate Node type: {node_type}"
                )
            fragment_node_ids.add(node_id)
            global_node_ids.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "node_type": node_type,
                    "name": _text(raw_node.get("name"), "node name"),
                    "responsibility": _text(
                        raw_node.get("professional_description"),
                        "professional description",
                    ),
                    "plain_explanation": _text(
                        raw_node.get("plain_explanation"), "plain explanation"
                    ),
                    "review_status": "candidate",
                    "source_assignment": {
                        "status": "unverified",
                        "note": "静态源码事实只支持这项候选解释，不证明完整源码归属、运行可达或审核批准。",
                        "relationship_ids": [],
                        "evidence_ids": list(evidence_ids),
                    },
                }
            )

        raw_relationships = fragment.get("relationships")
        if not isinstance(raw_relationships, list) or not raw_relationships:
            raise ExplorerInputError("Recursive candidate fragment needs relationships")
        relationships: list[dict[str, Any]] = []
        for raw_relationship in raw_relationships:
            if not isinstance(raw_relationship, dict):
                raise ExplorerInputError("Recursive candidate Relationship must be an object")
            if raw_relationship.get("review_status") != "candidate":
                raise ExplorerInputError("Recursive candidate Relationships must remain candidate")
            relationship_id = _text(raw_relationship.get("id"), "relationship id")
            if relationship_id in global_relationship_ids:
                raise ExplorerInputError(
                    f"Recursive candidate Relationship id must be globally unique: {relationship_id}"
                )
            semantic_family = _text(
                raw_relationship.get("semantic_family"), "relationship semantic family"
            )
            if semantic_family not in {
                "candidate-collaboration",
                "candidate-decomposition",
            }:
                raise ExplorerInputError(
                    f"Unsupported recursive candidate Relationship semantic family: {semantic_family}"
                )
            source_node_id = _text(
                raw_relationship.get("source_node_id"), "relationship source Node id"
            )
            target_node_id = _text(
                raw_relationship.get("target_node_id"), "relationship target Node id"
            )
            for endpoint in (source_node_id, target_node_id):
                if endpoint not in global_node_ids:
                    raise ExplorerInputError(
                        f"Recursive candidate Relationship references unknown Node: {endpoint}"
                    )
            evidence_ids = raw_relationship.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ExplorerInputError(
                    "Recursive candidate Relationship needs source evidence ids"
                )
            unknown = [item for item in evidence_ids if item not in known_evidence_ids]
            if unknown:
                raise ExplorerInputError(
                    f"Recursive candidate fragment references unknown source evidence: {unknown[0]}"
                )
            relationship = {
                "id": relationship_id,
                "semantic_family": semantic_family,
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
                "name": _text(raw_relationship.get("name"), "relationship name"),
                "professional_description": _text(
                    raw_relationship.get("professional_description"),
                    "relationship professional description",
                ),
                "plain_explanation": _text(
                    raw_relationship.get("plain_explanation"),
                    "relationship plain explanation",
                ),
                "review_status": "candidate",
                "evidence_ids": list(evidence_ids),
            }
            global_relationship_ids.add(relationship_id)
            relationships.append(relationship)
            all_relationships.append(relationship)
        validated_fragments.append(
            {
                "scope_node_id": scope_node_id,
                "nodes": nodes,
                "relationships": relationships,
            }
        )
    _reject_decomposition_cycles(all_relationships)
    if validated_fragments:
        result["recursive_candidate_fragments"] = validated_fragments
    return validated_fragments


def apply_candidate_fragments(
    explorer: dict[str, Any], fragments: list[dict[str, Any]]
) -> dict[str, Any]:
    """Attach evidence-backed, explicitly unreviewed candidate drilldowns."""

    result = deepcopy(explorer)
    navigation = result.get("navigation")
    if not isinstance(navigation, dict) or not isinstance(navigation.get("drilldowns"), list):
        raise ExplorerInputError("Candidate fragments require Architecture Explorer navigation")
    module_ids = {
        item.get("id")
        for item in result.get("architecture", {}).get("modules", [])
        if isinstance(item, dict)
    }
    drilldown_by_parent = {
        item.get("parent_module_id"): item
        for item in navigation["drilldowns"]
        if isinstance(item, dict)
    }
    fragment_parent_ids: set[str] = set()
    global_node_ids = {
        child.get("id")
        for drilldown in navigation["drilldowns"]
        if isinstance(drilldown, dict)
        for child in drilldown.get("children", [])
        if isinstance(child, dict)
    }
    global_collaboration_ids = {
        collaboration.get("id")
        for drilldown in navigation["drilldowns"]
        if isinstance(drilldown, dict)
        for collaboration in drilldown.get("collaborations", [])
        if isinstance(collaboration, dict)
    }
    source_evidence_index = build_source_evidence_index(result.get("source_facts"))
    result["source_evidence_index"] = source_evidence_index
    known_evidence_ids = set(source_evidence_index)
    validated_fragments: list[dict[str, Any]] = []
    recursive_fragments = [
        fragment
        for fragment in fragments
        if isinstance(fragment, dict)
        and fragment.get("schema") == RECURSIVE_CANDIDATE_FRAGMENT_SCHEMA
    ]
    legacy_fragments = [fragment for fragment in fragments if fragment not in recursive_fragments]
    for fragment in legacy_fragments:
        if not isinstance(fragment, dict):
            raise ExplorerInputError("Candidate fragment must be an object")
        if fragment.get("schema") != CANDIDATE_FRAGMENT_SCHEMA:
            raise ExplorerInputError("Unsupported candidate fragment schema")
        parent_module_id = _text(fragment.get("parent_module_id"), "parent module id")
        if parent_module_id not in module_ids:
            raise ExplorerInputError(
                f"Candidate fragment references unknown parent module: {parent_module_id}"
            )
        if parent_module_id in fragment_parent_ids:
            raise ExplorerInputError(
                f"Candidate fragment parent module must be unique: {parent_module_id}"
            )
        fragment_parent_ids.add(parent_module_id)
        existing_drilldown = drilldown_by_parent.get(parent_module_id)
        if existing_drilldown is not None and parent_module_id != "production-control":
            raise ExplorerInputError(
                f"Candidate fragment parent module must be unique: {parent_module_id}"
            )
        existing_children = {
            child.get("id"): child
            for child in existing_drilldown.get("children", [])
            if isinstance(child, dict) and isinstance(child.get("id"), str)
        } if isinstance(existing_drilldown, dict) else {}
        raw_nodes = fragment.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ExplorerInputError(f"Candidate fragment {parent_module_id} needs nodes")
        children = []
        node_ids: set[str] = set()
        for raw_node in raw_nodes:
            if not isinstance(raw_node, dict):
                raise ExplorerInputError("Candidate fragment node must be an object")
            if raw_node.get("review_status") != "candidate":
                raise ExplorerInputError("Candidate fragment nodes must remain candidate")
            evidence_ids = raw_node.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ExplorerInputError("Candidate fragment node needs source evidence ids")
            unknown = [item for item in evidence_ids if item not in known_evidence_ids]
            if unknown:
                raise ExplorerInputError(
                    f"Candidate fragment references unknown source evidence: {unknown[0]}"
                )
            node_id = _text(raw_node.get("id"), "node id")
            if node_id in node_ids:
                raise ExplorerInputError(f"Candidate fragment node id must be unique: {node_id}")
            if node_id in global_node_ids and node_id not in existing_children:
                raise ExplorerInputError(
                    f"Candidate fragment node id must be globally unique: {node_id}"
                )
            node_ids.add(node_id)
            global_node_ids.add(node_id)
            node_type = _text(raw_node.get("node_type"), "node type")
            if node_type not in {"responsibility", "component"}:
                raise ExplorerInputError(f"Unsupported candidate fragment node type: {node_type}")
            source_assignment = {
                "status": "unverified",
                "note": "静态源码事实只支持这项候选解释，不证明文件已经归属该架构模块。",
                "relationship_ids": [],
                "evidence_ids": list(evidence_ids),
            }
            if node_id in existing_children:
                existing_assignment = existing_children[node_id].get("source_assignment")
                if not isinstance(existing_assignment, dict):
                    raise ExplorerInputError(
                        f"Existing candidate node needs source assignment: {node_id}"
                    )
                source_assignment = deepcopy(existing_assignment)
                source_assignment["evidence_ids"] = list(evidence_ids)
            children.append(
                {
                    "id": node_id,
                    "node_type": node_type,
                    "name": _text(raw_node.get("name"), "node name"),
                    "responsibility": _text(
                        raw_node.get("professional_description"), "professional description"
                    ),
                    "plain_explanation": _text(
                        raw_node.get("plain_explanation"), "plain explanation"
                    ),
                    "review_status": "candidate",
                    "source_assignment": source_assignment,
                }
            )
        missing_existing = sorted(set(existing_children) - node_ids)
        if missing_existing:
            raise ExplorerInputError(
                "Candidate fragment must preserve existing candidate node: "
                f"{missing_existing[0]}"
            )
        raw_collaborations = fragment.get("collaborations")
        if not isinstance(raw_collaborations, list):
            raise ExplorerInputError("Candidate fragment collaborations must be a list")
        collaborations = []
        collaboration_ids: set[str] = set()
        for raw_collaboration in raw_collaborations:
            if not isinstance(raw_collaboration, dict):
                raise ExplorerInputError("Candidate collaboration must be an object")
            if raw_collaboration.get("review_status") != "candidate":
                raise ExplorerInputError("Candidate collaborations must remain candidate")
            collaboration_id = _text(raw_collaboration.get("id"), "collaboration id")
            if collaboration_id in collaboration_ids:
                raise ExplorerInputError(
                    f"Candidate collaboration id must be unique: {collaboration_id}"
                )
            if collaboration_id in global_collaboration_ids:
                raise ExplorerInputError(
                    "Candidate collaboration id must be globally unique: "
                    f"{collaboration_id}"
                )
            collaboration_ids.add(collaboration_id)
            global_collaboration_ids.add(collaboration_id)
            source_node_id = _text(
                raw_collaboration.get("source_node_id"), "collaboration source node id"
            )
            target_node_id = _text(
                raw_collaboration.get("target_node_id"), "collaboration target node id"
            )
            for endpoint in (source_node_id, target_node_id):
                if endpoint not in node_ids:
                    raise ExplorerInputError(
                        f"Candidate collaboration references unknown node: {endpoint}"
                    )
            evidence_ids = raw_collaboration.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ExplorerInputError("Candidate collaboration needs source evidence ids")
            unknown = [item for item in evidence_ids if item not in known_evidence_ids]
            if unknown:
                raise ExplorerInputError(
                    f"Candidate fragment references unknown source evidence: {unknown[0]}"
                )
            collaborations.append(
                {
                    "id": collaboration_id,
                    "source_node_id": source_node_id,
                    "target_node_id": target_node_id,
                    "name": _text(raw_collaboration.get("name"), "collaboration name"),
                    "professional_description": _text(
                        raw_collaboration.get("professional_description"),
                        "collaboration professional description",
                    ),
                    "plain_explanation": _text(
                        raw_collaboration.get("plain_explanation"),
                        "collaboration plain explanation",
                    ),
                    "review_status": "candidate",
                    "evidence_ids": list(evidence_ids),
                }
            )
        validated = {
            "parent_module_id": parent_module_id,
            "name": _text(fragment.get("name"), "drilldown name"),
            "coverage_note": _text(fragment.get("coverage_note"), "coverage note"),
            "children": children,
            "collaborations": collaborations,
        }
        if existing_drilldown is None:
            navigation["drilldowns"].append(validated)
        else:
            index = navigation["drilldowns"].index(existing_drilldown)
            navigation["drilldowns"][index] = validated
        validated_fragments.append(validated)

    validated_recursive = _apply_recursive_candidate_fragments(
        result,
        recursive_fragments,
        known_evidence_ids,
        global_node_ids,
        global_collaboration_ids,
    )
    canonical = json.dumps(
        [*validated_fragments, *validated_recursive],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result["candidate_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


__all__ = [
    "CANDIDATE_FRAGMENT_SCHEMA",
    "RECURSIVE_CANDIDATE_FRAGMENT_SCHEMA",
    "apply_candidate_fragments",
]
