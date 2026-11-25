"""Trusted per-object review for Candidate Architecture relationships.

The module owns review material and its read-only projection.  It deliberately
does not participate in View assembly or rendering.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from .continuity import _atomic_write
from .generator import ExplorerInputError
from .node_review import _canonical_sha256, _source_evidence
from .node_review_packet import (
    ALLOWED_RESULTS,
    EVIDENCE_RECORD_FIELDS,
    _validated_static_evidence_record,
)
from .node_review_definitions import rejected_node_review_claim_hashes
from .review_source import review_source_sha256
from .review_trust import (
    ReviewApprovalAnchor,
    ReviewTrustPolicy,
    match_trusted_review_source,
)


RELATIONSHIP_REVIEW_DEFINITIONS_SCHEMA = (
    "architecture-explorer.relationship-review-definitions.v1"
)
RELATIONSHIP_REVIEW_PACKET_SCHEMA = "architecture-explorer.relationship-review-packet.v1"
RELATIONSHIP_REVIEW_PROJECTION_SCHEMA = (
    "architecture-explorer.review-projection.v1"
)
RELATIONSHIP_REVIEW_DECISION_SCHEMA = (
    "architecture-explorer.relationship-review-decision.v1"
)
RELATIONSHIP_REVIEW_STATE_SCHEMA = "architecture-explorer.relationship-review-state.v1"
RELATIONSHIP_REVIEW_IMPORT_NOTE = (
    "Imported from a pinned GitHub Owner/admin review source."
)

_RELATIONSHIP_KINDS = {
    "candidate-collaboration": "collaboration",
    "candidate-decomposition": "decomposition",
}
_CLAIM_KINDS = (
    "IDENTITY",
    "ENDPOINT_RESPONSIBILITIES",
    "SEMANTICS_AND_DIRECTION",
    "RELATIONSHIP_DESCRIPTION",
    "CANDIDATE_EVIDENCE",
    "DEPENDENCIES_AND_LIMITS",
)
_DEFINITION_FIELDS = {
    "object_key",
    "reviewed_source_snapshot",
    "endpoint_bindings",
    "semantics",
    "claim_kinds",
    "candidate_evidence_ids",
    "dependencies_and_limits",
}
_INPUT_FIELDS = {
    "definition_sha256",
    "candidate_fact_sha256",
    "endpoint_bindings_sha256",
    "claims_sha256",
    "candidate_evidence_sha256",
}
_STATE_FIELDS = {
    "schema",
    "object_key",
    "decision_id",
    "state",
    "continuity",
    "evaluated_source_snapshot",
    "assessed_at",
    "reasons",
    "current_evidence_locations",
}


RelationshipReviewApprovalAnchor = ReviewApprovalAnchor
RelationshipReviewTrustPolicy = ReviewTrustPolicy


def stable_review_object_key(object_key: dict[str, Any]) -> str:
    """Return the v4 `(kind, object_id)` key, adapting historical Node keys."""

    if not isinstance(object_key, dict):
        raise ExplorerInputError("Review object key must be an object")
    kind = object_key.get("kind")
    if kind == "candidate-node" and set(object_key) == {"kind", "node_id"}:
        object_id = object_key.get("node_id")
    elif set(object_key) == {"kind", "object_id"}:
        object_id = object_key.get("object_id")
    else:
        raise ExplorerInputError("Review object key must use the closed v4 schema")
    if kind not in {"candidate-node", *_RELATIONSHIP_KINDS}:
        raise ExplorerInputError("Unsupported Candidate review object kind")
    if not isinstance(object_id, str) or not object_id:
        raise ExplorerInputError("Review object_id is required")
    return f"{kind}::{object_id}"


def _validate_endpoint_binding(value: object, *, label: str) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"node_id", "responsibility"}
        or not isinstance(value.get("node_id"), str)
        or not value["node_id"]
        or not isinstance(value.get("responsibility"), str)
        or not value["responsibility"]
    ):
        raise ExplorerInputError(f"Relationship Review {label} endpoint is invalid")


def validate_relationship_review_definitions(
    value: dict[str, Any],
) -> list[dict[str, Any]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "definitions"}
        or value.get("schema") != RELATIONSHIP_REVIEW_DEFINITIONS_SCHEMA
    ):
        raise ExplorerInputError("Unsupported Relationship Review Definitions schema")
    definitions = value.get("definitions")
    if not isinstance(definitions, list) or not definitions:
        raise ExplorerInputError("Relationship Review Definitions must be non-empty")
    indexed: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) != _DEFINITION_FIELDS:
            raise ExplorerInputError("Relationship Review Definition must use the closed schema")
        object_key = definition.get("object_key")
        key = stable_review_object_key(object_key)
        kind = object_key["kind"]
        if kind not in _RELATIONSHIP_KINDS:
            raise ExplorerInputError("Relationship Review Definition requires a relationship kind")
        if key in indexed:
            raise ExplorerInputError(f"Duplicate Relationship Review Definition: {key}")
        snapshot = definition.get("reviewed_source_snapshot")
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"revision", "worktree_state"}
            or not isinstance(snapshot.get("revision"), str)
            or re.fullmatch(r"[0-9a-f]{40}", snapshot["revision"]) is None
            or snapshot.get("worktree_state") != "clean"
        ):
            raise ExplorerInputError(f"Invalid reviewed source snapshot: {key}")
        endpoints = definition.get("endpoint_bindings")
        if not isinstance(endpoints, dict) or set(endpoints) != {"source", "target"}:
            raise ExplorerInputError(f"Relationship Review endpoints are invalid: {key}")
        _validate_endpoint_binding(endpoints["source"], label="source")
        _validate_endpoint_binding(endpoints["target"], label="target")
        semantics = definition.get("semantics")
        direction = semantics.get("direction") if isinstance(semantics, dict) else None
        if (
            not isinstance(semantics, dict)
            or set(semantics) != {"family", "direction"}
            or semantics.get("family") != _RELATIONSHIP_KINDS[kind]
            or not isinstance(direction, dict)
            or set(direction) != {"source_node_id", "target_node_id"}
            or direction.get("source_node_id") != endpoints["source"]["node_id"]
            or direction.get("target_node_id") != endpoints["target"]["node_id"]
        ):
            raise ExplorerInputError(f"Relationship Review semantics are invalid: {key}")
        if tuple(definition.get("claim_kinds", ())) != _CLAIM_KINDS:
            raise ExplorerInputError(f"Relationship Review Claims are invalid: {key}")
        evidence_ids = definition.get("candidate_evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or len(evidence_ids) != len(set(evidence_ids))
            or not all(isinstance(item, str) and item for item in evidence_ids)
        ):
            raise ExplorerInputError(f"Relationship Review Evidence identities are invalid: {key}")
        limits = definition.get("dependencies_and_limits")
        if not isinstance(limits, dict) or set(limits) != {
            "dependencies",
            "zero_dependency_reason",
            "retained_unknowns",
        }:
            raise ExplorerInputError(f"Relationship Review dependencies are invalid: {key}")
        dependencies = limits.get("dependencies")
        unknowns = limits.get("retained_unknowns")
        zero_reason = limits.get("zero_dependency_reason")
        if not isinstance(dependencies, list):
            raise ExplorerInputError(f"Relationship Review dependencies are invalid: {key}")
        if dependencies:
            if zero_reason is not None:
                raise ExplorerInputError(f"Relationship dependencies cannot have a zero reason: {key}")
            dependency_keys: set[str] = set()
            for dependency in dependencies:
                if not isinstance(dependency, dict) or set(dependency) != {
                    "object_key",
                    "basis",
                    "evidence_ids",
                }:
                    raise ExplorerInputError(
                        f"Relationship Review Dependency must use the closed schema: {key}"
                    )
                dependency_key = dependency.get("object_key")
                if (
                    not isinstance(dependency_key, dict)
                    or set(dependency_key) != {"kind", "object_id"}
                ):
                    raise ExplorerInputError(
                        f"Relationship Review Dependency must use the closed schema: {key}"
                    )
                stable_dependency_key = stable_review_object_key(dependency_key)
                if stable_dependency_key in dependency_keys:
                    raise ExplorerInputError(
                        f"Duplicate Relationship Review Dependency: {key}"
                    )
                dependency_keys.add(stable_dependency_key)
                dependency_evidence = dependency.get("evidence_ids")
                if (
                    not isinstance(dependency.get("basis"), str)
                    or not dependency["basis"]
                    or not isinstance(dependency_evidence, list)
                    or not dependency_evidence
                    or not all(
                        isinstance(item, str) and item in evidence_ids
                        for item in dependency_evidence
                    )
                ):
                    raise ExplorerInputError(
                        f"Relationship Review Dependency binding is invalid: {key}"
                    )
        elif not isinstance(zero_reason, str) or not zero_reason:
            raise ExplorerInputError(f"Zero dependencies require a reason: {key}")
        if (
            not isinstance(unknowns, list)
            or not unknowns
            or not all(isinstance(item, str) and item for item in unknowns)
        ):
            raise ExplorerInputError(f"Relationship retained unknowns are invalid: {key}")
        indexed[key] = deepcopy(definition)
    return [indexed[key] for key in sorted(indexed)]


def _relationship_records(explorer: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for drilldown in explorer.get("navigation", {}).get("drilldowns", []):
        for collaboration in drilldown.get("collaborations", []):
            if isinstance(collaboration, dict):
                adapted = deepcopy(collaboration)
                # v3 Collaboration records predate the explicit semantic field.
                # The authoritative typed container supplies compatibility
                # semantics; the object ID is deliberately ignored.
                adapted["semantic_family"] = "candidate-collaboration"
                records.append(adapted)
    for fragment in explorer.get("recursive_candidate_fragments", []):
        records.extend(fragment.get("relationships", []))
    return [record for record in records if isinstance(record, dict)]


def _target_relationship(
    explorer: dict[str, Any], object_key: dict[str, Any]
) -> dict[str, Any]:
    matches = [
        record
        for record in _relationship_records(explorer)
        if record.get("id") == object_key["object_id"]
    ]
    if len(matches) != 1:
        raise ExplorerInputError(
            f"Relationship Review target must resolve exactly once: {object_key['object_id']}"
        )
    return matches[0]


def _node_records(explorer: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        module
        for module in explorer.get("architecture", {}).get("modules", [])
        if isinstance(module, dict)
    ]
    for drilldown in explorer.get("navigation", {}).get("drilldowns", []):
        records.extend(
            node for node in drilldown.get("children", []) if isinstance(node, dict)
        )
    for fragment in explorer.get("recursive_candidate_fragments", []):
        records.extend(node for node in fragment.get("nodes", []) if isinstance(node, dict))
    return records


def _endpoint(explorer: dict[str, Any], expected: dict[str, Any]) -> dict[str, str]:
    matches = [node for node in _node_records(explorer) if node.get("id") == expected["node_id"]]
    if len(matches) != 1:
        raise ExplorerInputError(
            f"Relationship Review endpoint must resolve exactly once: {expected['node_id']}"
        )
    responsibility = matches[0].get("responsibility")
    if responsibility is None:
        responsibility = matches[0].get("professional_description")
    actual = {"node_id": matches[0].get("id"), "responsibility": responsibility}
    if actual != expected:
        raise ExplorerInputError(
            f"Relationship Review endpoint responsibility mismatch: {expected['node_id']}"
        )
    return actual


def _candidate_fact(record: dict[str, Any]) -> dict[str, Any]:
    semantic_family = record.get("semantic_family")
    semantics = _RELATIONSHIP_KINDS.get(semantic_family)
    if semantics is None:
        raise ExplorerInputError("Relationship Review target has unsupported semantics")
    return {
        "object_id": record.get("id"),
        "semantic_family": semantics,
        "source_node_id": record.get("source_node_id"),
        "target_node_id": record.get("target_node_id"),
        "name": record.get("name"),
        "professional_description": record.get("professional_description"),
        "plain_explanation": record.get("plain_explanation"),
        "provenance": "candidate",
        "evidence_ids": deepcopy(record.get("evidence_ids")),
    }


def _candidate_evidence(
    explorer: dict[str, Any], evidence_ids: list[str]
) -> list[dict[str, Any]]:
    indexed = _source_evidence(explorer, evidence_ids)
    records = []
    for evidence_id in evidence_ids:
        raw = indexed.get(evidence_id)
        if raw is None:
            raise ExplorerInputError(
                f"Relationship Review Candidate Evidence is missing: {evidence_id}"
            )
        validated = _validated_static_evidence_record(
            raw, label="Relationship Review Candidate Evidence"
        )
        records.append(
            {field: deepcopy(validated[field]) for field in EVIDENCE_RECORD_FIELDS}
        )
    return records


def _claims(
    definition: dict[str, Any],
    candidate: dict[str, Any],
    endpoints: dict[str, dict[str, str]],
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    limits = definition["dependencies_and_limits"]
    by_kind = {
        "IDENTITY": {
            "kind": "IDENTITY",
            "value": deepcopy(definition["object_key"]),
            "source": "relationship-review-definition.object-key",
        },
        "ENDPOINT_RESPONSIBILITIES": {
            "kind": "ENDPOINT_RESPONSIBILITIES",
            "value": deepcopy(endpoints),
            "source": "relationship-review-definition.endpoint-bindings",
        },
        "SEMANTICS_AND_DIRECTION": {
            "kind": "SEMANTICS_AND_DIRECTION",
            "value": deepcopy(definition["semantics"]),
            "source": "relationship-review-definition.semantics",
        },
        "RELATIONSHIP_DESCRIPTION": {
            "kind": "RELATIONSHIP_DESCRIPTION",
            "value": {
                field: candidate[field]
                for field in ("name", "professional_description", "plain_explanation")
            },
            "source": "candidate-relationship",
        },
        "CANDIDATE_EVIDENCE": {
            "kind": "CANDIDATE_EVIDENCE",
            "value": [
                {"evidence_id": item["evidence_id"], "role": item["evidence_kind"]}
                for item in evidence
            ],
            "source": "relationship-review-definition.candidate-evidence-ids",
        },
        "DEPENDENCIES_AND_LIMITS": {
            "kind": "DEPENDENCIES_AND_LIMITS",
            "value": deepcopy(limits),
            "source": "relationship-review-definition.dependencies-and-limits",
        },
    }
    return [by_kind[kind] for kind in definition["claim_kinds"]]


def build_relationship_review_packets(
    explorer: dict[str, Any], definitions: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        key = stable_review_object_key(definition["object_key"])
        if key in packets:
            raise ExplorerInputError(f"Duplicate Relationship Review Definition: {key}")
        source_snapshot = explorer.get("source_facts", {}).get("source_snapshot", {})
        current_snapshot = {
            "revision": source_snapshot.get("revision"),
            "worktree_state": source_snapshot.get("worktree", {}).get("state"),
        }
        if current_snapshot != definition["reviewed_source_snapshot"]:
            raise ExplorerInputError(
                f"Relationship Review source snapshot mismatch: {key}"
            )
        record = _target_relationship(explorer, definition["object_key"])
        candidate = _candidate_fact(record)
        expected_semantics = definition["semantics"]
        if (
            candidate["semantic_family"] != expected_semantics["family"]
            or candidate["source_node_id"]
            != expected_semantics["direction"]["source_node_id"]
            or candidate["target_node_id"]
            != expected_semantics["direction"]["target_node_id"]
        ):
            raise ExplorerInputError(f"Relationship Review semantic binding mismatch: {key}")
        if candidate["evidence_ids"] != definition["candidate_evidence_ids"]:
            raise ExplorerInputError(f"Relationship Review Evidence binding mismatch: {key}")
        endpoints = {
            role: _endpoint(explorer, definition["endpoint_bindings"][role])
            for role in ("source", "target")
        }
        evidence = _candidate_evidence(explorer, definition["candidate_evidence_ids"])
        claims = _claims(definition, candidate, endpoints, evidence)
        packet = {
            "schema": RELATIONSHIP_REVIEW_PACKET_SCHEMA,
            "object_key": deepcopy(definition["object_key"]),
            "reviewed_source_snapshot": deepcopy(definition["reviewed_source_snapshot"]),
            "allowed_results": list(ALLOWED_RESULTS),
            "candidate_fact": candidate,
            "endpoint_bindings": endpoints,
            "claims": claims,
            "candidate_evidence": evidence,
            "inputs": {
                "definition_sha256": _canonical_sha256(definition),
                "candidate_fact_sha256": _canonical_sha256(candidate),
                "endpoint_bindings_sha256": _canonical_sha256(endpoints),
                "claims_sha256": _canonical_sha256(claims),
                "candidate_evidence_sha256": _canonical_sha256(evidence),
            },
        }
        packet["packet_sha256"] = _canonical_sha256(packet)
        packets[key] = packet
    return {key: packets[key] for key in sorted(packets)}


def write_relationship_review_packets(
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
            "Relationship Review Packet output collision: "
            + ", ".join(str(path) for path in collisions)
        )
    written: dict[str, tuple[Path, Path]] = {}
    for key, packet in packets.items():
        packet_path, report_path = targets[key]
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        report_path.write_text(
            "\n".join(
                (
                    "# Candidate Relationship Review Packet",
                    "",
                    f"- Object: `{key}`",
                    f"- Packet SHA256: `{packet['packet_sha256']}`",
                    f"- Allowed results: {', '.join(packet['allowed_results'])}",
                    "",
                    "This neutral packet does not create or publish a Decision.",
                    "",
                )
            ),
            encoding="utf-8",
            newline="\n",
        )
        written[key] = (packet_path, report_path)
    return written


def render_relationship_review_decision_body(decision: dict[str, Any]) -> str:
    object_key = decision.get("object_key", {})
    inputs = decision.get("inputs", {})
    return "\n".join(
        (
            "ARCHITECTURE_EXPLORER_RELATIONSHIP_REVIEW_V1",
            f"Decision-ID: {decision.get('decision_id')}",
            f"Result: {decision.get('result')}",
            f"Object-Key: {object_key.get('kind')} + {object_key.get('object_id')}",
            f"Claims-SHA256: {inputs.get('claims_sha256')}",
            f"Candidate-Fact-SHA256: {inputs.get('candidate_fact_sha256')}",
            f"Reviewed-Revision: {decision.get('reviewed_source_snapshot', {}).get('revision')}",
        )
    )


def _matching_anchor(
    source: dict[str, Any] | None,
    trust_policy: RelationshipReviewTrustPolicy,
) -> RelationshipReviewApprovalAnchor:
    return match_trusted_review_source(
        source,
        trust_policy,
        label="Relationship Review",
    )


def verify_relationship_review_integrity(
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    *,
    trust_policy: RelationshipReviewTrustPolicy,
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
    if (
        not isinstance(decision, dict)
        or set(decision) != expected_fields
        or decision.get("schema") != RELATIONSHIP_REVIEW_DECISION_SCHEMA
    ):
        raise ExplorerInputError("Relationship Review Decision must use the closed schema")
    key = stable_review_object_key(definition["object_key"])
    if decision.get("object_key") != definition["object_key"]:
        raise ExplorerInputError(f"Relationship Review Decision object mismatch: {key}")
    if decision.get("result") not in ALLOWED_RESULTS:
        raise ExplorerInputError(f"Unsupported Relationship Review result: {key}")
    if decision.get("review_notes") != RELATIONSHIP_REVIEW_IMPORT_NOTE:
        raise ExplorerInputError(f"Relationship Review import note mismatch: {key}")
    inputs = decision.get("inputs")
    if (
        not isinstance(inputs, dict)
        or set(inputs) != _INPUT_FIELDS
        or not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in inputs.values()
        )
    ):
        raise ExplorerInputError(f"Relationship Review Decision inputs are invalid: {key}")
    claims = decision.get("claims")
    if (
        not isinstance(claims, list)
        or [claim.get("kind") for claim in claims if isinstance(claim, dict)]
        != definition["claim_kinds"]
        or any(
            not isinstance(claim, dict)
            or set(claim) != {"kind", "value", "source"}
            for claim in claims
        )
        or _canonical_sha256(claims) != inputs["claims_sha256"]
    ):
        raise ExplorerInputError(f"Relationship Review Claims are invalid: {key}")
    _matching_anchor(review_source, trust_policy)
    comment = review_source.get("comment", {})
    if decision.get("decision_id") != f"github-comment:{comment.get('id')}":
        raise ExplorerInputError(f"Relationship Review source comment mismatch: {key}")
    if decision.get("source_receipt_sha256") != review_source_sha256(review_source):
        raise ExplorerInputError(f"Relationship Review source receipt mismatch: {key}")
    if comment.get("body") != render_relationship_review_decision_body(decision):
        raise ExplorerInputError(f"Relationship Review source body mismatch: {key}")
    return deepcopy(decision), deepcopy(review_source)


def verify_relationship_review_materials(
    explorer: dict[str, Any],
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    *,
    trust_policy: RelationshipReviewTrustPolicy,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verified = verify_relationship_review_integrity(
        definition, decision, review_source, trust_policy=trust_policy
    )
    key = stable_review_object_key(definition["object_key"])
    packet = build_relationship_review_packets(explorer, [definition])[key]
    if decision.get("reviewed_source_snapshot") != packet["reviewed_source_snapshot"]:
        raise ExplorerInputError(f"Relationship Review snapshot mismatch: {key}")
    if decision.get("inputs") != packet["inputs"] or decision.get("claims") != packet["claims"]:
        raise ExplorerInputError(f"Relationship Review material binding mismatch: {key}")
    return verified


def _evaluated_source_snapshot(explorer: dict[str, Any]) -> dict[str, Any]:
    source_facts = explorer.get("source_facts", {})
    snapshot = source_facts.get("source_snapshot", {})
    return {
        "revision": snapshot.get("revision", "unknown"),
        "worktree_state": snapshot.get("worktree", {}).get("state", "unknown"),
        "source_facts_sha256": source_facts.get("deterministic_facts_sha256", "unknown"),
    }


def _current_evidence_locations(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": evidence["evidence_id"],
            "path": evidence["path"],
            "line_start": evidence["current_location"]["line_start"],
            "line_end": evidence["current_location"]["line_end"],
        }
        for evidence in packet["candidate_evidence"]
    ]


def _endpoint_rejection_assessment(
    explorer: dict[str, Any], definition: dict[str, Any]
) -> tuple[str, str, list[str]] | None:
    projection = explorer.get("node_review_projection", {})
    if projection.get("schema") != "architecture-explorer.node-review-projection.v1":
        return None
    objects = projection.get("objects")
    if not isinstance(objects, dict):
        return None
    invalidated: list[str] = []
    ambiguous: list[str] = []
    for endpoint in definition["endpoint_bindings"].values():
        node_id = endpoint["node_id"]
        review = objects.get(f"candidate-node::{node_id}")
        if not isinstance(review, dict):
            continue
        state = review.get("state")
        decision = review.get("decision")
        if (
            not isinstance(state, dict)
            or state.get("status") != "current"
            or state.get("continuity") != "exact-match"
            or not isinstance(decision, dict)
            or decision.get("result") != "REJECTED"
        ):
            continue
        try:
            rejected_hashes = rejected_node_review_claim_hashes(decision)
        except ExplorerInputError:
            rejected_hashes = None
        if rejected_hashes is None:
            ambiguous.append(
                f"endpoint-rejection:{node_id}:rejection-basis-ambiguous"
            )
            continue
        responsibility_claims = [
            claim
            for claim in decision.get("claims", [])
            if isinstance(claim, dict)
            and claim.get("kind") == "RESPONSIBILITY"
            and claim.get("value") == endpoint["responsibility"]
        ]
        if len(responsibility_claims) != 1:
            ambiguous.append(
                f"endpoint-rejection:{node_id}:responsibility-binding-ambiguous"
            )
        elif _canonical_sha256(responsibility_claims[0]) in rejected_hashes:
            invalidated.append(
                f"endpoint-rejection:{node_id}:responsibility-invalidated"
            )
    if ambiguous:
        return "unknown", "ambiguous", ambiguous
    if invalidated:
        return "needs-review", "material-change", invalidated
    return None


def assess_relationship_review_continuity(
    explorer: dict[str, Any],
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    *,
    trust_policy: RelationshipReviewTrustPolicy,
    prior_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess current inputs without writing State or creating a Decision."""

    verify_relationship_review_integrity(
        definition, decision, review_source, trust_policy=trust_policy
    )
    scan_error = explorer.get("_relationship_review_scan_error")
    if scan_error:
        if scan_error == "missing":
            status, continuity, reasons = (
                "needs-review",
                "missing",
                ["source-scan:missing"],
            )
        else:
            status, continuity, reasons = (
                "unknown",
                "parse-failure",
                ["source-scan:parse-failure"],
            )
        locations: list[dict[str, Any]] = []
    else:
        try:
            verify_relationship_review_materials(
                explorer,
                definition,
                decision,
                review_source,
                trust_policy=trust_policy,
            )
            packet = build_relationship_review_packets(explorer, [definition])[
                stable_review_object_key(definition["object_key"])
            ]
        except ExplorerInputError:
            status, continuity, reasons = (
                "needs-review",
                "material-change",
                ["relationship-review-material:material-change"],
            )
            locations = []
        else:
            endpoint_assessment = _endpoint_rejection_assessment(explorer, definition)
            if endpoint_assessment is None:
                status, continuity, reasons = "current", "exact-match", []
                locations = _current_evidence_locations(packet)
            else:
                status, continuity, reasons = endpoint_assessment
                locations = []
    if prior_state is not None:
        prior_status = prior_state.get("status", prior_state.get("state"))
        if (
            prior_state.get("decision_id") == decision["decision_id"]
            and prior_status in {"needs-review", "unknown"}
            and (status == "current" or prior_status == "unknown")
        ):
            status = prior_status
            continuity = prior_state["continuity"]
            reasons = list(prior_state["reasons"])
            locations = list(prior_state.get("current_evidence_locations", []))
    return {
        "object_key": deepcopy(decision["object_key"]),
        "status": status,
        "continuity": continuity,
        "decision_id": decision["decision_id"],
        "reasons": reasons,
        "evaluated_source_snapshot": _evaluated_source_snapshot(explorer),
        "current_evidence_locations": locations,
    }


def _valid_state(state: object, decision: dict[str, Any]) -> bool:
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        return False
    snapshot = state.get("evaluated_source_snapshot")
    locations = state.get("current_evidence_locations")
    return (
        state.get("schema") == RELATIONSHIP_REVIEW_STATE_SCHEMA
        and state.get("object_key") == decision.get("object_key")
        and state.get("decision_id") == decision.get("decision_id")
        and state.get("state") in {"current", "needs-review", "unknown"}
        and state.get("continuity")
        in {"exact-match", "material-change", "missing", "ambiguous", "parse-failure"}
        and isinstance(snapshot, dict)
        and set(snapshot) == {"revision", "worktree_state", "source_facts_sha256"}
        and all(isinstance(value, str) and value for value in snapshot.values())
        and isinstance(state.get("assessed_at"), str)
        and bool(state["assessed_at"])
        and isinstance(state.get("reasons"), list)
        and all(isinstance(reason, str) and reason for reason in state["reasons"])
        and isinstance(locations, list)
        and all(
            isinstance(location, dict)
            and set(location) == {"evidence_id", "path", "line_start", "line_end"}
            for location in locations
        )
        and (
            (state["state"] == "current" and state["continuity"] == "exact-match" and not state["reasons"])
            or (state["state"] != "current" and bool(state["reasons"]))
        )
    )


def evaluate_relationship_review_state(
    decision: dict[str, Any], state_path: Path | None
) -> dict[str, Any]:
    if state_path is None or not state_path.is_file():
        return {
            "object_key": deepcopy(decision.get("object_key")),
            "status": "unknown",
            "continuity": "missing",
            "decision_id": decision.get("decision_id"),
            "reasons": ["relationship-review-state:missing"],
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        state = None
    if not _valid_state(state, decision):
        return {
            "object_key": deepcopy(decision.get("object_key")),
            "status": "unknown",
            "continuity": "missing",
            "decision_id": decision.get("decision_id"),
            "reasons": ["relationship-review-state:inconsistent"],
        }
    return {
        "object_key": deepcopy(state["object_key"]),
        "status": state["state"],
        "continuity": state["continuity"],
        "decision_id": state["decision_id"],
        "reasons": deepcopy(state["reasons"]),
        "evaluated_source_snapshot": deepcopy(state["evaluated_source_snapshot"]),
        "current_evidence_locations": deepcopy(state["current_evidence_locations"]),
    }


def update_relationship_review_state(
    explorer: dict[str, Any],
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    state_path: Path,
    *,
    assessed_at: str,
    trust_policy: RelationshipReviewTrustPolicy,
) -> dict[str, Any]:
    existing: dict[str, Any] | None = None
    if state_path.exists():
        try:
            existing = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExplorerInputError(
                "Existing Relationship Review State is corrupt; refusing overwrite"
            ) from error
        if not _valid_state(existing, decision):
            raise ExplorerInputError(
                "Existing Relationship Review State is inconsistent; refusing overwrite"
            )
    assessment = assess_relationship_review_continuity(
        explorer,
        definition,
        decision,
        review_source,
        trust_policy=trust_policy,
        prior_state=existing,
    )
    state = {
        "schema": RELATIONSHIP_REVIEW_STATE_SCHEMA,
        "object_key": deepcopy(decision["object_key"]),
        "decision_id": decision["decision_id"],
        "state": assessment["status"],
        "continuity": assessment["continuity"],
        "evaluated_source_snapshot": assessment["evaluated_source_snapshot"],
        "assessed_at": assessed_at,
        "reasons": assessment["reasons"],
        "current_evidence_locations": assessment["current_evidence_locations"],
    }
    _atomic_write(state_path, state)
    return state


def project_relationship_reviews(
    explorer: dict[str, Any],
    definitions: list[dict[str, Any]],
    materials_by_key: dict[str, dict[str, Any]],
    *,
    trust_policy: RelationshipReviewTrustPolicy | None = None,
) -> dict[str, Any]:
    """Attach explicit read-only review records without changing relationships."""

    if "review_projection" in explorer:
        raise ExplorerInputError("Review Projection is already attached")
    definition_by_key = {
        stable_review_object_key(definition["object_key"]): definition
        for definition in definitions
    }
    if len(definition_by_key) != len(definitions):
        raise ExplorerInputError("Duplicate Relationship Review Definition")
    if set(materials_by_key) - set(definition_by_key):
        raise ExplorerInputError("Relationship Review materials contain an undefined object")
    objects: dict[str, dict[str, Any]] = {}
    for key in sorted(definition_by_key):
        definition = definition_by_key[key]
        material = materials_by_key.get(key)
        if material is None:
            build_relationship_review_packets(explorer, [definition])
            objects[key] = {
                "object_key": deepcopy(definition["object_key"]),
                "semantics": deepcopy(definition["semantics"]),
                "result": "Pending",
                "state": "not-reviewed",
                "approved_scope": [],
                "reasons": ["trusted-relationship-review-decision:missing"],
            }
            continue
        if (
            not isinstance(material, dict)
            or set(material) != {"decision", "source", "state"}
            or trust_policy is None
        ):
            raise ExplorerInputError(f"Relationship Review material bundle is invalid: {key}")
        decision = material["decision"]
        source = material["source"]
        state = material["state"]
        if not isinstance(state, dict) or state.get("object_key") != decision.get("object_key"):
            raise ExplorerInputError(f"Relationship Review Continuity object mismatch: {key}")
        if (
            state.get("decision_id") != decision.get("decision_id")
            or state.get("status") not in {"current", "needs-review", "unknown"}
            or state.get("continuity")
            not in {"exact-match", "material-change", "missing", "ambiguous", "parse-failure"}
            or not isinstance(state.get("reasons"), list)
        ):
            raise ExplorerInputError(f"Relationship Review Continuity object mismatch: {key}")
        assessment = assess_relationship_review_continuity(
            explorer,
            definition,
            decision,
            source,
            trust_policy=trust_policy,
            prior_state=state,
        )
        evidence_claim = next(
            (
                claim
                for claim in decision["claims"]
                if claim.get("kind") == "CANDIDATE_EVIDENCE"
            ),
            None,
        )
        evidence_scope = (
            [item.get("evidence_id") for item in evidence_claim.get("value", [])]
            if isinstance(evidence_claim, dict)
            and isinstance(evidence_claim.get("value"), list)
            else []
        )
        if not evidence_scope or not all(
            isinstance(evidence_id, str) and evidence_id
            for evidence_id in evidence_scope
        ):
            raise ExplorerInputError(f"Relationship Review approval scope is invalid: {key}")
        objects[key] = {
            "object_key": deepcopy(definition["object_key"]),
            "semantics": deepcopy(definition["semantics"]),
            "result": decision["result"],
            "state": assessment["status"],
            "approved_scope": [{
                "object_key": deepcopy(decision["object_key"]),
                "reviewed_source_snapshot": deepcopy(decision["reviewed_source_snapshot"]),
                "claims_sha256": decision["inputs"]["claims_sha256"],
                "evidence_ids": evidence_scope,
                "decision_id": decision["decision_id"],
            }] if decision["result"] == "APPROVED" else [],
            "reasons": deepcopy(assessment["reasons"]),
            "source": {
                "status": "trusted-owner-admin",
                "sha256": review_source_sha256(source),
                "repository": source.get("repository", {}).get("full_name"),
                "comment_url": source.get("comment", {}).get("html_url"),
                "author": source.get("comment", {}).get("user", {}).get("login"),
                "permission": source.get("permission", {}).get("permission"),
            },
        }
    result = deepcopy(explorer)
    result["review_projection"] = {
        "schema": RELATIONSHIP_REVIEW_PROJECTION_SCHEMA,
        "objects": objects,
    }
    return result


__all__ = [
    "RELATIONSHIP_REVIEW_DEFINITIONS_SCHEMA",
    "RELATIONSHIP_REVIEW_DECISION_SCHEMA",
    "RELATIONSHIP_REVIEW_PACKET_SCHEMA",
    "RELATIONSHIP_REVIEW_PROJECTION_SCHEMA",
    "RELATIONSHIP_REVIEW_STATE_SCHEMA",
    "RelationshipReviewApprovalAnchor",
    "RelationshipReviewTrustPolicy",
    "assess_relationship_review_continuity",
    "build_relationship_review_packets",
    "evaluate_relationship_review_state",
    "project_relationship_reviews",
    "render_relationship_review_decision_body",
    "stable_review_object_key",
    "update_relationship_review_state",
    "validate_relationship_review_definitions",
    "verify_relationship_review_integrity",
    "verify_relationship_review_materials",
    "write_relationship_review_packets",
]
