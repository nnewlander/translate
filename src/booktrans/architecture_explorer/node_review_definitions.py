from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from .generator import ExplorerInputError
from .node_review import (
    NODE_REVIEW_DECISION_SCHEMA,
    _NodeReviewTrustPolicy,
    _canonical_sha256,
    _candidate_fact,
    _matching_anchor,
    _source_evidence,
    _verify_node_review_material_integrity,
    _verify_node_review_materials,
)
from .node_review_continuity import _valid_current_projection_state
from .node_review_packet import (
    ALLOWED_RESULTS,
    EVIDENCE_RECORD_FIELDS,
    build_node_review_packet as build_legacy_worker_node_review_v1_packet,
    _validated_static_evidence_record,
)
from .node_review_projection import attach_node_review_projection
from .review_source import review_source_sha256


NODE_REVIEW_DEFINITIONS_SCHEMA = "architecture-explorer.node-review-definitions.v1"
NODE_REVIEW_PACKET_V2_SCHEMA = "architecture-explorer.node-review-packet.v2"
NODE_REVIEW_DECISION_V2_SCHEMA = "architecture-explorer.node-review-decision.v2"
REJECTION_BASIS_SCHEMA = "architecture-explorer.rejection-basis.v1"
_DEFINITION_FIELDS = {
    "object_key",
    "reviewed_source_snapshot",
    "classification_rationale",
    "claim_kinds",
    "candidate_evidence_ids",
    "dependencies_and_limits",
    "legacy_compatibility",
}
_DEPENDENCY_FIELDS = {
    "kind",
    "object_id",
    "decision_id",
    "result",
    "reviewed_revision",
    "claims_sha256",
    "evidence_ids",
}
_V2_CLAIM_KINDS = (
    "IDENTITY",
    "NAME",
    "CLASSIFICATION",
    "RESPONSIBILITY",
    "PLAIN_EXPLANATION",
    "SOURCE_ASSIGNMENT",
    "CANDIDATE_EVIDENCE",
    "DEPENDENCIES_AND_LIMITS",
)
_V2_INPUT_FIELDS = {
    "definition_sha256",
    "candidate_fact_sha256",
    "claims_sha256",
    "candidate_evidence_sha256",
    "dependency_evidence_sha256",
}


def stable_node_review_key(object_key: dict[str, Any]) -> str:
    if set(object_key) != {"kind", "node_id"}:
        raise ExplorerInputError("Node Review v2 object key must use the closed schema")
    if object_key.get("kind") != "candidate-node":
        raise ExplorerInputError("Node Review Definition supports candidate-node only")
    node_id = object_key.get("node_id")
    if not isinstance(node_id, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", node_id) is None:
        raise ExplorerInputError("Node Review Definition node_id is required")
    return f"candidate-node::{node_id}"


def validate_node_review_definitions(value: dict[str, Any]) -> list[dict[str, Any]]:
    if set(value) != {"schema", "definitions"} or value.get("schema") != NODE_REVIEW_DEFINITIONS_SCHEMA:
        raise ExplorerInputError("Unsupported Node Review Definitions schema")
    raw_definitions = value.get("definitions")
    if not isinstance(raw_definitions, list) or not raw_definitions:
        raise ExplorerInputError("Node Review Definitions must be a non-empty list")
    definitions: dict[str, dict[str, Any]] = {}
    for raw in raw_definitions:
        if not isinstance(raw, dict) or set(raw) != _DEFINITION_FIELDS:
            raise ExplorerInputError("Node Review Definition must use the closed schema")
        key = stable_node_review_key(raw.get("object_key", {}))
        if key in definitions:
            raise ExplorerInputError(f"Duplicate Node Review Definition: {key}")
        snapshot = raw.get("reviewed_source_snapshot")
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"revision", "worktree_state"}
            or not isinstance(snapshot.get("revision"), str)
            or len(snapshot["revision"]) != 40
            or snapshot.get("worktree_state") != "clean"
        ):
            raise ExplorerInputError(f"Invalid reviewed source snapshot: {key}")
        rationale = raw.get("classification_rationale")
        claim_kinds = raw.get("claim_kinds")
        evidence_ids = raw.get("candidate_evidence_ids")
        if not isinstance(rationale, str) or not rationale:
            raise ExplorerInputError(f"Classification rationale is required: {key}")
        if not isinstance(claim_kinds, list) or tuple(claim_kinds) != _V2_CLAIM_KINDS:
            raise ExplorerInputError(f"Node Review Claims are invalid: {key}")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or len(evidence_ids) != len(set(evidence_ids))
            or not all(isinstance(item, str) and item for item in evidence_ids)
        ):
            raise ExplorerInputError(f"Candidate Evidence identities are invalid: {key}")
        limits = raw.get("dependencies_and_limits")
        if not isinstance(limits, dict) or set(limits) != {
            "dependencies", "zero_dependency_reason", "retained_unknowns"
        }:
            raise ExplorerInputError(f"Dependencies and limits are invalid: {key}")
        dependencies = limits.get("dependencies")
        unknowns = limits.get("retained_unknowns")
        zero_reason = limits.get("zero_dependency_reason")
        if not isinstance(dependencies, list) or not isinstance(unknowns, list) or not unknowns:
            raise ExplorerInputError(f"Dependencies or retained unknowns are invalid: {key}")
        if not all(isinstance(item, str) and item for item in unknowns):
            raise ExplorerInputError(f"Retained unknowns are invalid: {key}")
        if dependencies:
            if zero_reason is not None:
                raise ExplorerInputError(f"Non-empty dependencies cannot have a zero-dependency reason: {key}")
        elif not isinstance(zero_reason, str) or not zero_reason:
            raise ExplorerInputError(f"Zero dependencies require a structured reason: {key}")
        dependency_keys: set[tuple[str, str]] = set()
        for dependency in dependencies:
            if not isinstance(dependency, dict) or set(dependency) != _DEPENDENCY_FIELDS:
                raise ExplorerInputError(f"Dependency must use the closed schema: {key}")
            if dependency.get("kind") != "reviewed-relationship":
                raise ExplorerInputError(f"Unsupported dependency kind: {key}")
            dependency_key = (dependency["kind"], dependency.get("object_id"))
            if dependency_key in dependency_keys:
                raise ExplorerInputError(f"Duplicate dependency: {key}")
            dependency_keys.add(dependency_key)
            if (
                not all(isinstance(dependency.get(field), str) and dependency[field] for field in (
                    "object_id", "decision_id", "result", "reviewed_revision",
                    "claims_sha256",
                ))
                or dependency.get("result") not in ALLOWED_RESULTS
                or not isinstance(dependency.get("evidence_ids"), list)
                or not dependency["evidence_ids"]
            ):
                raise ExplorerInputError(f"Dependency binding is incomplete: {key}")
        legacy = raw.get("legacy_compatibility")
        if legacy is not None:
            if (
                not isinstance(legacy, dict)
                or set(legacy) != {"schema", "object_key"}
                or legacy.get("schema") != NODE_REVIEW_DECISION_SCHEMA
                or not isinstance(legacy.get("object_key"), dict)
                or set(legacy["object_key"]) != {
                    "kind", "parent_module_id", "node_id"
                }
                or legacy["object_key"].get("kind") != "candidate-node"
                or legacy["object_key"].get("node_id")
                != raw["object_key"]["node_id"]
            ):
                raise ExplorerInputError(
                    f"Legacy compatibility identity mismatch: {key}"
                )
        definitions[key] = deepcopy(raw)
    return [definitions[key] for key in sorted(definitions)]


def _target_node(explorer: dict[str, Any], object_key: dict[str, Any]) -> dict[str, Any]:
    node_id = object_key["node_id"]
    matches = [
        node
        for drilldown in explorer.get("navigation", {}).get("drilldowns", [])
        for node in drilldown.get("children", [])
        if node.get("id") == node_id
    ]
    if len(matches) != 1:
        raise ExplorerInputError(f"Node Review target must resolve exactly once: {node_id}")
    return matches[0]


def _candidate_evidence(
    explorer: dict[str, Any], evidence_ids: list[str]
) -> list[dict[str, Any]]:
    evidence_by_id = _source_evidence(explorer, evidence_ids)
    result = []
    for evidence_id in evidence_ids:
        raw = evidence_by_id.get(evidence_id)
        if raw is None:
            raise ExplorerInputError(f"Node Review Candidate Evidence is missing: {evidence_id}")
        validated = _validated_static_evidence_record(raw, label="Node Review Candidate Evidence")
        result.append({field: deepcopy(validated[field]) for field in EVIDENCE_RECORD_FIELDS})
    return result


def _dependencies(
    explorer: dict[str, Any], definition: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    projected = []
    evidence_records = []
    relationships = explorer.get("architecture", {}).get("relationships", [])
    for expected in definition["dependencies_and_limits"]["dependencies"]:
        matches = [item for item in relationships if item.get("id") == expected["object_id"]]
        if len(matches) != 1:
            raise ExplorerInputError(f"Node Review dependency must resolve exactly once: {expected['object_id']}")
        relationship = matches[0]
        decision = relationship.get("review_decision", {})
        dependency_claims = {
            "relationship": decision.get("relationship"),
            "retained_unknowns": decision.get("retained_unknowns"),
            "review_notes": decision.get("review_notes"),
        }
        actual = {
            "kind": "reviewed-relationship",
            "object_id": relationship.get("id"),
            "decision_id": decision.get("decision_id"),
            "result": decision.get("result"),
            "reviewed_revision": decision.get("reviewed_source_snapshot", {}).get("revision"),
            "claims_sha256": _canonical_sha256(dependency_claims),
            "evidence_ids": relationship.get("evidence_ids"),
        }
        if actual != expected:
            raise ExplorerInputError(f"Node Review dependency binding mismatch: {expected['object_id']}")
        if relationship.get("review_status") != "current-reviewed" or relationship.get("source_continuity", {}).get("status") != "exact-match":
            raise ExplorerInputError(f"Node Review dependency is not current: {expected['object_id']}")
        raw_evidence = relationship.get("evidence")
        if not isinstance(raw_evidence, list) or len(raw_evidence) != len(expected["evidence_ids"]):
            raise ExplorerInputError(f"Node Review dependency Evidence mismatch: {expected['object_id']}")
        validated = [
            _validated_static_evidence_record(item, label="Node Review dependency Evidence")
            for item in raw_evidence
        ]
        if [item["evidence_id"] for item in validated] != expected["evidence_ids"]:
            raise ExplorerInputError(f"Node Review dependency Evidence identity mismatch: {expected['object_id']}")
        projected.append(deepcopy(expected))
        evidence_records.extend(validated)
    return projected, evidence_records


def _claims(
    target: dict[str, Any],
    definition: dict[str, Any],
    candidate_evidence: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    object_key = definition["object_key"]
    limits = definition["dependencies_and_limits"]
    claims_by_kind = {
        "IDENTITY": {"kind": "IDENTITY", "value": deepcopy(object_key), "source": "candidate-fact.object-key"},
        "NAME": {"kind": "NAME", "value": target.get("name"), "source": "candidate-fact.name"},
        "CLASSIFICATION": {"kind": "CLASSIFICATION", "value": {"node_type": target.get("node_type"), "rationale": definition["classification_rationale"]}, "source": "node-review-definition.classification"},
        "RESPONSIBILITY": {"kind": "RESPONSIBILITY", "value": target.get("responsibility"), "source": "candidate-fact.responsibility"},
        "PLAIN_EXPLANATION": {"kind": "PLAIN_EXPLANATION", "value": target.get("plain_explanation"), "source": "candidate-fact.plain-explanation"},
        "SOURCE_ASSIGNMENT": {"kind": "SOURCE_ASSIGNMENT", "value": deepcopy(target.get("source_assignment")), "source": "candidate-fact.source-assignment"},
        "CANDIDATE_EVIDENCE": {"kind": "CANDIDATE_EVIDENCE", "value": [{"evidence_id": item["evidence_id"], "role": item["evidence_kind"]} for item in candidate_evidence], "source": "node-review-definition.candidate-evidence-ids"},
        "DEPENDENCIES_AND_LIMITS": {"kind": "DEPENDENCIES_AND_LIMITS", "value": {"dependencies": dependencies, "zero_dependency_reason": limits["zero_dependency_reason"], "retained_unknowns": deepcopy(limits["retained_unknowns"])}, "source": "node-review-definition.dependencies-and-limits"},
    }
    return [claims_by_kind[kind] for kind in definition["claim_kinds"]]


def build_node_review_packets(
    explorer: dict[str, Any], definitions: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        key = stable_node_review_key(definition["object_key"])
        if key in indexed:
            raise ExplorerInputError(f"Duplicate Node Review Definition: {key}")
        target = _target_node(explorer, definition["object_key"])
        candidate_fact = _candidate_fact(target)
        if candidate_fact.get("source_assignment", {}).get("evidence_ids") != definition["candidate_evidence_ids"]:
            raise ExplorerInputError(f"Node Review Definition Evidence mismatch: {key}")
        candidate_evidence = _candidate_evidence(explorer, definition["candidate_evidence_ids"])
        dependencies, dependency_evidence = _dependencies(explorer, definition)
        claims = _claims(target, definition, candidate_evidence, dependencies)
        packet = {
            "schema": NODE_REVIEW_PACKET_V2_SCHEMA,
            "object_key": deepcopy(definition["object_key"]),
            "reviewed_source_snapshot": deepcopy(definition["reviewed_source_snapshot"]),
            "allowed_results": list(ALLOWED_RESULTS),
            "candidate_fact": candidate_fact,
            "claims": claims,
            "candidate_evidence": candidate_evidence,
            "dependency_evidence": dependency_evidence,
            "inputs": {
                "definition_sha256": _canonical_sha256(definition),
                "candidate_fact_sha256": _canonical_sha256(candidate_fact),
                "claims_sha256": _canonical_sha256(claims),
                "candidate_evidence_sha256": _canonical_sha256(candidate_evidence),
                "dependency_evidence_sha256": _canonical_sha256(dependency_evidence),
            },
        }
        if definition["legacy_compatibility"] is not None:
            legacy = build_legacy_worker_node_review_v1_packet(explorer)
            if legacy["object_key"] != definition["legacy_compatibility"]["object_key"]:
                raise ExplorerInputError(
                    f"Legacy compatibility object mismatch: {key}"
                )
            packet["legacy_v1_compatibility"] = {
                "schema": legacy["schema"],
                "object_key": legacy["object_key"],
                "candidate_fact_sha256": legacy["inputs"]["candidate_fact_sha256"],
                "claims_sha256": legacy["inputs"]["claims_sha256"],
                "candidate_evidence_fingerprint_sha256": legacy["inputs"]["candidate_evidence_fingerprint_sha256"],
                "formal_evidence_sha256": legacy["inputs"]["formal_evidence_sha256"],
                "packet_sha256": legacy["packet_sha256"],
            }
        packet["packet_sha256"] = _canonical_sha256(packet)
        indexed[key] = packet
    return {key: indexed[key] for key in sorted(indexed)}


def _evaluated_source_snapshot(explorer: dict[str, Any]) -> dict[str, Any]:
    source_facts = explorer.get("source_facts", {})
    snapshot = source_facts.get("source_snapshot", {})
    return {
        "revision": snapshot.get("revision", "unknown"),
        "worktree_state": snapshot.get("worktree", {}).get("state", "unknown"),
        "source_facts_sha256": source_facts.get(
            "deterministic_facts_sha256", "unknown"
        ),
    }


def _current_v2_continuity_evidence(
    explorer: dict[str, Any], definition: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    packet = build_node_review_packets(explorer, [definition])[
        stable_node_review_key(definition["object_key"])
    ]
    locations = [
        {
            "evidence_id": evidence["evidence_id"],
            "path": evidence["path"],
            "line_start": evidence["current_location"]["line_start"],
            "line_end": evidence["current_location"]["line_end"],
        }
        for evidence in packet["candidate_evidence"]
    ]
    return _evaluated_source_snapshot(explorer), locations


def render_node_review_packet_report(packet: dict[str, Any]) -> str:
    candidate = packet["candidate_fact"]
    limits = next(
        claim["value"]
        for claim in packet["claims"]
        if claim["kind"] == "DEPENDENCIES_AND_LIMITS"
    )
    lines = [
        "# Architecture Explorer：Candidate Node 审阅材料",
        "",
        "> 本材料是中立 Packet，不是 Decision，也不包含默认批准、批量批准或状态传播。",
        "",
        f"- 对象：`{packet['object_key']['kind']}::{packet['object_key']['node_id']}`",
        f"- 名称：{candidate['name']}",
        f"- Packet SHA-256：`{packet['packet_sha256']}`",
        f"- Candidate Evidence：{len(packet['candidate_evidence'])} 条",
        f"- 正式依赖：{len(limits['dependencies'])} 个",
        "",
        "## 依赖与保留未知项",
        "",
    ]
    if limits["dependencies"]:
        lines.extend(
            f"- 依赖 `{item['kind']}::{item['object_id']}`，Decision `{item['decision_id']}`。"
            for item in limits["dependencies"]
        )
    else:
        lines.append(f"- 零正式架构依赖理由：{limits['zero_dependency_reason']}")
    lines.extend(["", "### Retained unknowns", ""])
    lines.extend(f"- {item}" for item in limits["retained_unknowns"])
    lines.extend(
        [
            "",
            "## 独立审核边界",
            "",
            "- 本 Packet 不安装 Decision、可信 receipt、Production Anchor 或 Continuity。",
            "- 对本对象的未来 Decision 不覆盖父节点、子节点、Collaboration、Decomposition、View 或 Appearance。",
            "",
        ]
    )
    return "\n".join(lines)


def render_node_review_decision_body(decision: dict[str, Any]) -> str:
    object_key = decision.get("object_key", {})
    inputs = decision.get("inputs", {})
    lines = [
            "ARCHITECTURE_EXPLORER_NODE_REVIEW_V2",
            f"Decision-ID: {decision.get('decision_id')}",
            f"Result: {decision.get('result')}",
            f"Object-Key: {object_key.get('kind')} + {object_key.get('node_id')}",
            f"Claims-SHA256: {inputs.get('claims_sha256')}",
            f"Candidate-Fact-SHA256: {inputs.get('candidate_fact_sha256')}",
            f"Reviewed-Revision: {decision.get('reviewed_source_snapshot', {}).get('revision')}",
    ]
    if "rejection_basis" in decision:
        lines.append(
            f"Rejection-Basis-SHA256: {_canonical_sha256(decision['rejection_basis'])}"
        )
    if "batch_publication_sha256" in decision:
        lines.append(f"Publication-Key: {decision['batch_publication_sha256']}")
    return "\n".join(lines)


def rejected_node_review_claim_hashes(decision: dict[str, Any]) -> set[str] | None:
    """Return explicitly rejected Claim hashes, or ``None`` for legacy ambiguity."""

    basis = decision.get("rejection_basis")
    if basis is None:
        return None
    if (
        decision.get("result") != "REJECTED"
        or not isinstance(basis, dict)
        or set(basis) != {"schema", "rejected_claims"}
        or basis.get("schema") != REJECTION_BASIS_SCHEMA
        or not isinstance(basis.get("rejected_claims"), list)
        or not basis["rejected_claims"]
    ):
        raise ExplorerInputError("Node Review rejection basis is invalid")
    claim_hashes = {
        _canonical_sha256(claim)
        for claim in decision.get("claims", [])
        if isinstance(claim, dict)
    }
    rejected: set[str] = set()
    for item in basis["rejected_claims"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"claim_sha256", "reason_code", "explanation"}
            or not isinstance(item.get("claim_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", item["claim_sha256"]) is None
            or item["claim_sha256"] not in claim_hashes
            or not isinstance(item.get("reason_code"), str)
            or re.fullmatch(r"[A-Z][A-Z0-9_]*", item["reason_code"]) is None
            or not isinstance(item.get("explanation"), str)
            or not item["explanation"]
            or item["claim_sha256"] in rejected
        ):
            raise ExplorerInputError("Node Review rejection basis is invalid")
        rejected.add(item["claim_sha256"])
    return rejected


def verify_node_review_v2_materials(
    explorer: dict[str, Any],
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    *,
    trust_policy: _NodeReviewTrustPolicy,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verified_decision, verified_source = verify_node_review_v2_integrity(
        definition,
        decision,
        review_source,
        trust_policy=trust_policy,
    )
    key = stable_node_review_key(definition["object_key"])
    packet = build_node_review_packets(explorer, [definition])[key]
    if verified_decision.get("reviewed_source_snapshot") != packet["reviewed_source_snapshot"]:
        raise ExplorerInputError(f"Node Review Decision v2 snapshot mismatch: {key}")
    if verified_decision.get("inputs") != packet["inputs"] or verified_decision.get("claims") != packet["claims"]:
        raise ExplorerInputError(f"Node Review Decision v2 Claim binding mismatch: {key}")
    return verified_decision, verified_source


def verify_node_review_v2_integrity(
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    *,
    trust_policy: _NodeReviewTrustPolicy,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_fields = {
        "schema",
        "decision_id",
        "object_key",
        "result",
        "reviewed_source_snapshot",
        "review_notes",
        "source_receipt_sha256",
        "inputs",
        "claims",
    }
    actual_fields = set(decision) - {"batch_publication_sha256"}
    publication = decision.get("batch_publication_sha256")
    if "batch_publication_sha256" in decision and (
        not isinstance(publication, str) or not re.fullmatch(r"[0-9a-f]{64}", publication)
    ):
        raise ExplorerInputError("Invalid Node Review batch publication binding")
    if (
        frozenset(actual_fields)
        not in {
            frozenset(expected_fields),
            frozenset(expected_fields | {"rejection_basis"}),
        }
        or decision.get("schema") != NODE_REVIEW_DECISION_V2_SCHEMA
    ):
        raise ExplorerInputError("Node Review Decision v2 must use the closed schema")
    key = stable_node_review_key(definition["object_key"])
    if decision.get("object_key") != definition["object_key"]:
        raise ExplorerInputError(f"Node Review Decision v2 object mismatch: {key}")
    if decision.get("result") not in ALLOWED_RESULTS:
        raise ExplorerInputError(f"Unsupported Node Review Decision v2 result: {key}")
    if decision.get("review_notes") != "Imported from a pinned GitHub Owner/admin review source.":
        raise ExplorerInputError(f"Node Review Decision v2 import note mismatch: {key}")
    inputs = decision.get("inputs")
    if (
        not isinstance(inputs, dict)
        or set(inputs) != _V2_INPUT_FIELDS
        or not all(
            isinstance(value, str)
            and re.fullmatch(r"[0-9a-f]{64}", value) is not None
            for value in inputs.values()
        )
    ):
        raise ExplorerInputError(f"Node Review Decision v2 inputs are invalid: {key}")
    claims = decision.get("claims")
    if (
        not isinstance(claims, list)
        or [claim.get("kind") for claim in claims if isinstance(claim, dict)]
        != definition["claim_kinds"]
        or any(
            not isinstance(claim, dict)
            or set(claim) != {"kind", "value", "source"}
            or not isinstance(claim.get("source"), str)
            or not claim["source"]
            for claim in claims
        )
    ):
        raise ExplorerInputError(f"Node Review Decision v2 Claims are invalid: {key}")
    if _canonical_sha256(claims) != inputs["claims_sha256"]:
        raise ExplorerInputError(f"Node Review Decision v2 Claims hash mismatch: {key}")
    rejected_node_review_claim_hashes(decision)
    _matching_anchor(review_source, trust_policy)
    comment = review_source.get("comment", {})
    if decision.get("decision_id") != f"github-comment:{comment.get('id')}":
        raise ExplorerInputError(f"Node Review Decision v2 source comment mismatch: {key}")
    if decision.get("source_receipt_sha256") != review_source_sha256(review_source):
        raise ExplorerInputError(f"Node Review Decision v2 source receipt mismatch: {key}")
    if comment.get("body") != render_node_review_decision_body(decision):
        raise ExplorerInputError(f"Node Review Decision v2 source body mismatch: {key}")
    return deepcopy(decision), deepcopy(review_source)


def write_node_review_packets(
    packets: dict[str, dict[str, Any]], output_directory: Path
) -> dict[str, tuple[Path, Path]]:
    targets = {
        key: (
            output_directory / key.replace("::", "__") / "review-packet.json",
            output_directory / key.replace("::", "__") / "review-packet.md",
        )
        for key in packets
    }
    collisions = [path for paths in targets.values() for path in paths if path.exists()]
    if collisions:
        raise ExplorerInputError(
            "Node Review Packet output collision: "
            + ", ".join(str(path) for path in collisions)
        )
    written: dict[str, tuple[Path, Path]] = {}
    for key, packet in packets.items():
        directory = output_directory / key.replace("::", "__")
        directory.mkdir(parents=True, exist_ok=True)
        packet_path, report_path = targets[key]
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        report_path.write_text(
            render_node_review_packet_report(packet),
            encoding="utf-8",
            newline="\n",
        )
        written[key] = (packet_path, report_path)
    return written


def project_node_reviews(
    explorer: dict[str, Any],
    definitions: list[dict[str, Any]],
    materials_by_key: dict[str, dict[str, Any]],
    *,
    trust_policy: _NodeReviewTrustPolicy,
) -> dict[str, Any]:
    definition_by_key = {
        stable_node_review_key(definition["object_key"]): definition
        for definition in definitions
    }
    if len(definition_by_key) != len(definitions):
        raise ExplorerInputError("Duplicate Node Review Definition")
    if set(materials_by_key) - set(definition_by_key):
        raise ExplorerInputError("Node Review materials contain an undefined object")
    reviews_by_key: dict[str, dict[str, Any]] = {}
    for key in sorted(definition_by_key):
        definition = definition_by_key[key]
        _target_node(explorer, definition["object_key"])
        material = materials_by_key.get(key)
        if material is None:
            reviews_by_key[key] = {
                "object_key": deepcopy(definition["object_key"]),
                "decision": None,
                "state": {
                    "status": "not-reviewed",
                    "continuity": "not-assessed",
                    "reasons": ["trusted-node-review-decision:missing"],
                },
                "projection": "Pending Review",
            }
            continue
        if not isinstance(material, dict) or set(material) != {"decision", "source", "state"}:
            raise ExplorerInputError(f"Node Review material bundle is invalid: {key}")
        decision = material["decision"]
        source = material["source"]
        state = material["state"]
        if not isinstance(decision, dict) or decision.get("schema") not in {
            NODE_REVIEW_DECISION_SCHEMA, NODE_REVIEW_DECISION_V2_SCHEMA
        }:
            raise ExplorerInputError(f"Unsupported Node Review Decision schema: {key}")
        decision_key = decision.get("object_key", {})
        if decision_key.get("kind") != "candidate-node" or decision_key.get("node_id") != definition["object_key"]["node_id"]:
            raise ExplorerInputError(f"Node Review Decision object mismatch: {key}")
        if decision["schema"] == NODE_REVIEW_DECISION_SCHEMA:
            legacy = definition.get("legacy_compatibility")
            if (
                not isinstance(legacy, dict)
                or legacy.get("schema") != decision["schema"]
                or legacy.get("object_key") != decision_key
            ):
                raise ExplorerInputError(
                    f"Decision object requires exact v1 legacy compatibility: {key}"
                )
            if state.get("status") == "current" and state.get("continuity") == "exact-match":
                verified_decision, verified_source = _verify_node_review_materials(
                    explorer,
                    decision,
                    source,
                    trust_policy=trust_policy,
                )
            else:
                verified_decision, verified_source = _verify_node_review_material_integrity(
                    decision,
                    source,
                    trust_policy=trust_policy,
                )
        else:
            if state.get("status") == "current" and state.get("continuity") == "exact-match":
                verified_decision, verified_source = verify_node_review_v2_materials(
                    explorer,
                    definition,
                    decision,
                    source,
                    trust_policy=trust_policy,
                )
            else:
                verified_decision, verified_source = verify_node_review_v2_integrity(
                    definition,
                    decision,
                    source,
                    trust_policy=trust_policy,
                )
        if (
            not isinstance(state, dict)
            or state.get("object_key") != verified_decision["object_key"]
            or state.get("decision_id") != verified_decision["decision_id"]
            or state.get("status") not in {"current", "needs-review", "unknown"}
            or state.get("continuity") not in {
                "exact-match", "material-change", "missing", "ambiguous", "parse-failure"
            }
            or not isinstance(state.get("reasons"), list)
        ):
            raise ExplorerInputError(f"Node Review Continuity object mismatch: {key}")
        snapshot = state.get("evaluated_source_snapshot")
        locations = state.get("current_evidence_locations")
        if (
            (
                "evaluated_source_snapshot" in state
                and snapshot is not None
                and (
                    not isinstance(snapshot, dict)
                    or set(snapshot)
                    != {"revision", "worktree_state", "source_facts_sha256"}
                    or not all(
                        isinstance(value, str) and value
                        for value in snapshot.values()
                    )
                )
            )
            or (
                "current_evidence_locations" in state
                and (
                    not isinstance(locations, list)
                    or not all(
                        isinstance(location, dict)
                        and set(location)
                        == {"evidence_id", "path", "line_start", "line_end"}
                        and isinstance(location["evidence_id"], str)
                        and bool(location["evidence_id"])
                        and isinstance(location["path"], str)
                        and bool(location["path"])
                        and isinstance(location["line_start"], int)
                        and isinstance(location["line_end"], int)
                        and location["line_start"] > 0
                        and location["line_end"] >= location["line_start"]
                        for location in locations
                    )
                )
            )
        ):
            raise ExplorerInputError(
                f"Node Review Continuity display evidence is invalid: {key}"
            )
        if state["status"] == "current" and state["continuity"] == "exact-match":
            if (
                decision["schema"] == NODE_REVIEW_DECISION_SCHEMA
                and not _valid_current_projection_state(explorer, state)
            ):
                raise ExplorerInputError(
                    f"Node Review current Continuity evidence mismatch: {key}"
                )
            if decision["schema"] == NODE_REVIEW_DECISION_V2_SCHEMA:
                expected_snapshot, expected_locations = (
                    _current_v2_continuity_evidence(explorer, definition)
                )
                if (
                    state.get("evaluated_source_snapshot") != expected_snapshot
                    or state.get("current_evidence_locations")
                    != expected_locations
                ):
                    raise ExplorerInputError(
                        f"Node Review current Continuity evidence mismatch: {key}"
                    )
            projection = (
                "Current · Approved"
                if verified_decision["result"] == "APPROVED"
                else "Current · Rejected"
            )
        elif state["status"] == "needs-review":
            projection = (
                "Historical · Approved"
                if verified_decision["result"] == "APPROVED"
                else "Historical · Rejected"
            )
        else:
            projection = "Unknown"
        repository = verified_source.get("repository", {})
        comment = verified_source.get("comment", {})
        permission = verified_source.get("permission", {})
        reviews_by_key[key] = {
            "object_key": deepcopy(definition["object_key"]),
            "decision": verified_decision,
            "source": {
                "status": "trusted-owner-admin",
                "sha256": review_source_sha256(verified_source),
                "repository": repository.get("full_name"),
                "comment_url": comment.get("html_url"),
                "author": comment.get("user", {}).get("login"),
                "author_association": comment.get("author_association"),
                "permission": permission.get("permission"),
            },
            "state": {
                field: deepcopy(state[field])
                for field in (
                    "status",
                    "continuity",
                    "decision_id",
                    "reasons",
                    "evaluated_source_snapshot",
                    "current_evidence_locations",
                )
                if field in state
            },
            "projection": projection,
        }
    return attach_node_review_projection(explorer, reviews_by_key)


__all__ = [
    "NODE_REVIEW_DEFINITIONS_SCHEMA",
    "NODE_REVIEW_DECISION_V2_SCHEMA",
    "NODE_REVIEW_PACKET_V2_SCHEMA",
    "REJECTION_BASIS_SCHEMA",
    "build_node_review_packets",
    "rejected_node_review_claim_hashes",
    "render_node_review_decision_body",
    "stable_node_review_key",
    "validate_node_review_definitions",
    "verify_node_review_v2_materials",
    "verify_node_review_v2_integrity",
    "write_node_review_packets",
]
