from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError


CANDIDATE_FRAGMENT_SCHEMA = "architecture-explorer.candidate-fragment.v1"


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorerInputError(f"Candidate fragment {field} must be a non-empty string")
    return value


def _source_evidence_ids(explorer: dict[str, Any]) -> set[str]:
    source_facts = explorer.get("source_facts")
    modules = source_facts.get("modules") if isinstance(source_facts, dict) else None
    if not isinstance(modules, list):
        raise ExplorerInputError("Candidate fragments require T6-1 source facts")
    evidence_ids: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            continue
        for fact in [*module.get("direct_imports", []), *module.get("entries", [])]:
            evidence = fact.get("evidence") if isinstance(fact, dict) else None
            evidence_id = evidence.get("evidence_id") if isinstance(evidence, dict) else None
            if isinstance(evidence_id, str):
                evidence_ids.add(evidence_id)
    return evidence_ids


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
    parent_ids = {
        item.get("parent_module_id")
        for item in navigation["drilldowns"]
        if isinstance(item, dict)
    }
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
    known_evidence_ids = _source_evidence_ids(result)
    validated_fragments: list[dict[str, Any]] = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise ExplorerInputError("Candidate fragment must be an object")
        if fragment.get("schema") != CANDIDATE_FRAGMENT_SCHEMA:
            raise ExplorerInputError("Unsupported candidate fragment schema")
        parent_module_id = _text(fragment.get("parent_module_id"), "parent module id")
        if parent_module_id not in module_ids:
            raise ExplorerInputError(
                f"Candidate fragment references unknown parent module: {parent_module_id}"
            )
        if parent_module_id in parent_ids:
            raise ExplorerInputError(
                f"Candidate fragment parent module must be unique: {parent_module_id}"
            )
        parent_ids.add(parent_module_id)
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
            if node_id in global_node_ids:
                raise ExplorerInputError(
                    f"Candidate fragment node id must be globally unique: {node_id}"
                )
            node_ids.add(node_id)
            global_node_ids.add(node_id)
            node_type = _text(raw_node.get("node_type"), "node type")
            if node_type not in {"responsibility", "component"}:
                raise ExplorerInputError(f"Unsupported candidate fragment node type: {node_type}")
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
                    "source_assignment": {
                        "status": "unverified",
                        "note": "静态源码事实只支持这项候选解释，不证明文件已经归属该架构模块。",
                        "relationship_ids": [],
                        "evidence_ids": list(evidence_ids),
                    },
                }
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
        navigation["drilldowns"].append(validated)
        validated_fragments.append(validated)

    canonical = json.dumps(
        validated_fragments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["candidate_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


__all__ = ["CANDIDATE_FRAGMENT_SCHEMA", "apply_candidate_fragments"]
