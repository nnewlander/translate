from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .generator import ExplorerInputError
from .source_evidence import build_source_evidence_index


VIEW_PROJECTION_SCHEMA = "architecture-explorer.view-projection.v2"
REVIEW_PROJECTION_SCHEMA = "architecture-explorer.review-projection.v1"
REVIEW_RESULTS = {"APPROVED", "REJECTED", "Pending"}
REVIEW_STATES = {"current", "needs-review", "unknown", "not-reviewed"}
SOURCE_CATALOG_SCHEMA = "architecture-explorer.source-catalog.v1"


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorerInputError(f"View Projection {field} must be a non-empty string")
    return value


def _pending_review() -> dict[str, Any]:
    return {
        "result": "Pending",
        "state": "not-reviewed",
        "approved_scope": [],
        "reasons": ["trusted-review-decision:missing"],
    }


def _canonical_key(kind: str, object_id: str) -> str:
    return f"{kind}::{object_id}"


def _explicit_reviews(explorer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    projection = explorer.get("review_projection")
    if projection is None:
        return {}
    if (
        not isinstance(projection, dict)
        or projection.get("schema") != REVIEW_PROJECTION_SCHEMA
    ):
        raise ExplorerInputError(
            "View Projection review projection schema is invalid"
        )
    objects = projection.get("objects")
    if not isinstance(objects, dict):
        raise ExplorerInputError("View Projection review projection objects must be an object")
    validated: dict[str, dict[str, Any]] = {}
    for key, review in objects.items():
        if not isinstance(review, dict):
            raise ExplorerInputError("View Projection review must be an object")
        object_key = review.get("object_key")
        if not isinstance(object_key, dict):
            raise ExplorerInputError("View Projection review object key is invalid")
        kind = _text(object_key.get("kind"), "review object kind")
        object_id = _text(object_key.get("object_id"), "review object id")
        if key != _canonical_key(kind, object_id):
            raise ExplorerInputError(f"View Projection review object mismatch: {key}")
        result = review.get("result")
        state = review.get("state")
        if kind not in {"candidate-collaboration", "candidate-decomposition"}:
            raise ExplorerInputError(
                f"View Projection Relationship Review kind is invalid: {kind}"
            )
        semantics = review.get("semantics")
        if not isinstance(semantics, dict) or set(semantics) != {
            "family",
            "direction",
        }:
            raise ExplorerInputError(
                f"View Projection Relationship Review semantics are invalid: {key}"
            )
        family = semantics.get("family")
        expected_family = {
            "candidate-collaboration": "collaboration",
            "candidate-decomposition": "decomposition",
        }[kind]
        direction = semantics.get("direction")
        if (
            family != expected_family
            or not isinstance(direction, dict)
            or set(direction) != {"source_node_id", "target_node_id"}
        ):
            raise ExplorerInputError(
                f"View Projection Relationship Review semantics are invalid: {key}"
            )
        source_node_id = _text(
            direction.get("source_node_id"), "review source Node id"
        )
        target_node_id = _text(
            direction.get("target_node_id"), "review target Node id"
        )
        approved_scope = review.get("approved_scope")
        reasons = review.get("reasons")
        if result not in REVIEW_RESULTS:
            raise ExplorerInputError(f"View Projection review result is invalid: {result}")
        if state not in REVIEW_STATES:
            raise ExplorerInputError(f"View Projection review state is invalid: {state}")
        if not isinstance(reasons, list) or not all(
            isinstance(reason, str) and reason for reason in reasons
        ):
            raise ExplorerInputError("View Projection review reasons must be strings")
        if result == "Pending":
            if state != "not-reviewed" or approved_scope:
                raise ExplorerInputError(
                    "View Projection Pending review must be not-reviewed without approval scope"
                )
        elif state == "not-reviewed":
            raise ExplorerInputError(
                "View Projection decided review cannot be not-reviewed"
            )
        if not isinstance(approved_scope, list):
            raise ExplorerInputError(
                "View Projection approved scope must be a list"
            )
        if result == "APPROVED" and not approved_scope:
            raise ExplorerInputError(
                "View Projection approved review requires an approved scope"
            )
        if result != "APPROVED" and approved_scope:
            raise ExplorerInputError(
                "View Projection non-approved review cannot carry an approved scope"
            )
        for scope in approved_scope:
            if not isinstance(scope, dict) or set(scope) != {
                "object_key", "reviewed_source_snapshot", "claims_sha256",
                "evidence_ids", "decision_id",
            }:
                raise ExplorerInputError(
                    "View Projection Relationship Review approval scope is invalid"
                )
            if scope.get("object_key") != object_key:
                raise ExplorerInputError(
                    f"View Projection review approval scope object mismatch: {key}"
                )
            snapshot = scope.get("reviewed_source_snapshot")
            evidence_ids = scope.get("evidence_ids")
            if (
                not isinstance(snapshot, dict)
                or set(snapshot) != {"revision", "worktree_state"}
                or not all(isinstance(value, str) and value for value in snapshot.values())
                or not isinstance(scope.get("claims_sha256"), str)
                or not scope["claims_sha256"]
                or not isinstance(evidence_ids, list)
                or not evidence_ids
                or not all(isinstance(evidence_id, str) and evidence_id for evidence_id in evidence_ids)
                or not isinstance(scope.get("decision_id"), str)
                or not scope["decision_id"]
            ):
                raise ExplorerInputError(
                    "View Projection Relationship Review approval scope is invalid"
                )
        validated[key] = {
            "result": result,
            "state": state,
            "approved_scope": deepcopy(approved_scope),
            "reasons": deepcopy(reasons),
            "semantics": {
                "family": family,
                "direction": {
                    "source_node_id": source_node_id,
                    "target_node_id": target_node_id,
                },
            },
        }
        if "source" in review:
            if not isinstance(review["source"], dict):
                raise ExplorerInputError(
                    "View Projection Relationship Review source is invalid"
                )
            validated[key]["source"] = deepcopy(review["source"])
    return validated


def _legacy_node_reviews(explorer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    projection = explorer.get("node_review_projection")
    if projection is None:
        return {}
    if (
        not isinstance(projection, dict)
        or projection.get("schema")
        != "architecture-explorer.node-review-projection.v1"
        or not isinstance(projection.get("objects"), dict)
    ):
        raise ExplorerInputError("View Projection legacy Node review projection is invalid")
    adapted: dict[str, dict[str, Any]] = {}
    for legacy_key, review in projection["objects"].items():
        if not isinstance(review, dict) or not isinstance(review.get("object_key"), dict):
            raise ExplorerInputError("View Projection legacy Node review is invalid")
        object_key = review["object_key"]
        if object_key.get("kind") != "candidate-node":
            raise ExplorerInputError("View Projection legacy Node review kind is invalid")
        object_id = _text(object_key.get("node_id"), "legacy review Node id")
        expected_legacy_key = _canonical_key("candidate-node", object_id)
        if legacy_key != expected_legacy_key:
            raise ExplorerInputError(
                f"View Projection legacy Node review object mismatch: {legacy_key}"
            )
        decision = review.get("decision")
        result = decision.get("result") if isinstance(decision, dict) else "Pending"
        state_record = review.get("state")
        state = state_record.get("status") if isinstance(state_record, dict) else "unknown"
        if result not in REVIEW_RESULTS:
            result = "Pending"
        if state not in REVIEW_STATES:
            state = "unknown"
        claim_kinds: list[str] = []
        if result == "APPROVED" and isinstance(decision, dict):
            claims = decision.get("claims")
            if isinstance(claims, list):
                claim_kinds = [
                    claim["kind"]
                    for claim in claims
                    if isinstance(claim, dict)
                    and isinstance(claim.get("kind"), str)
                    and claim["kind"]
                ]
        adapted[expected_legacy_key] = {
            "result": result,
            "state": state,
            "approved_scope": [
                {"legacy_claim_kind": claim_kind} for claim_kind in claim_kinds
            ],
            "reasons": deepcopy(state_record.get("reasons", []))
            if isinstance(state_record, dict)
            else [],
        }
    return adapted


def _review_for(
    reviews: dict[str, dict[str, Any]], *, kind: str, object_id: str
) -> dict[str, Any]:
    return deepcopy(reviews.get(_canonical_key(kind, object_id), _pending_review()))


def _candidate_node_record(
    node: dict[str, Any],
    reviews: dict[str, dict[str, Any]],
    legacy_details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    node_id = _text(node.get("id"), "Node id")
    display_node = deepcopy(node)
    display_node.pop("review_status", None)
    record = {
        **display_node,
        "object_key": {"kind": "candidate-node", "object_id": node_id},
        "review": _review_for(reviews, kind="candidate-node", object_id=node_id),
    }
    detail = legacy_details.get(_canonical_key("candidate-node", node_id))
    if detail is not None:
        record["review_detail"] = deepcopy(detail)
    return record


def _candidate_relationship_record(
    relationship: dict[str, Any], reviews: dict[str, dict[str, Any]], *, kind: str
) -> dict[str, Any]:
    relationship_id = _text(relationship.get("id"), "Relationship id")
    semantic_by_kind = {
        "candidate-collaboration": "collaboration",
        "candidate-decomposition": "decomposition",
    }
    if kind not in semantic_by_kind:
        raise ExplorerInputError(
            f"View Projection Relationship kind is invalid: {kind}"
        )
    display_relationship = deepcopy(relationship)
    display_relationship.pop("semantic_family", None)
    display_relationship.pop("review_status", None)
    review = _review_for(reviews, kind=kind, object_id=relationship_id)
    review_semantics = review.pop("semantics", None)
    expected_semantics = {
        "family": semantic_by_kind[kind],
        "direction": {
            "source_node_id": _text(
                relationship.get("source_node_id"), "Relationship source Node id"
            ),
            "target_node_id": _text(
                relationship.get("target_node_id"), "Relationship target Node id"
            ),
        },
    }
    if review_semantics is not None and review_semantics != expected_semantics:
        raise ExplorerInputError(
            f"View Projection Relationship Review semantics mismatch: {relationship_id}"
        )
    return {
        **display_relationship,
        "semantic": semantic_by_kind[kind],
        "provenance": {"kind": "candidate-fragment"},
        "object_key": {"kind": kind, "object_id": relationship_id},
        "review": review,
    }


def _formal_relationship_record(relationship: dict[str, Any]) -> dict[str, Any]:
    decision = relationship.get("review_decision")
    result = decision.get("result") if isinstance(decision, dict) else "Pending"
    if result not in REVIEW_RESULTS:
        result = "Pending"
    status = relationship.get("review_status")
    continuity = relationship.get("source_continuity")
    continuity_status = continuity.get("status") if isinstance(continuity, dict) else None
    if status == "current-reviewed" and continuity_status == "exact-match":
        state = "current"
    elif status in {"historical-reviewed", "needs-review"}:
        state = "needs-review"
    else:
        state = "unknown"
    scope = relationship.get("review_scope")
    display_relationship = deepcopy(relationship)
    display_relationship.pop("review_status", None)
    return {
        **display_relationship,
        "semantic": "formal",
        "provenance": {"kind": "formal-relationship"},
        "source_node_id": _text(
            relationship.get("source_module"), "Relationship source Node id"
        ),
        "target_node_id": _text(
            relationship.get("target_module"), "Relationship target Node id"
        ),
        "review": {
            "result": result,
            "state": state,
            "approved_scope": (
                [{"legacy_review_scope": scope}]
                if isinstance(scope, str) and scope
                else []
            ),
            "reasons": deepcopy(relationship.get("review_reasons", [])),
        },
    }


def _complete_evidence_projection(
    source_facts: dict[str, Any] | None,
    references: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if source_facts is None:
        return {}
    completed: dict[str, dict[str, Any]] = {}
    for evidence_id, reference in references.items():
        source = reference.get("record_source")
        record: dict[str, Any]
        if isinstance(source, str) and source.startswith("source_facts.modules."):
            collection = source.removeprefix("source_facts.modules.")
            source_module = source_facts["modules"][reference["module_index"]]
            record = {
                "fact": deepcopy(source_module[collection][reference["record_index"]]),
                "file_path": _text(source_module.get("path"), "Evidence file path"),
            }
        elif isinstance(source, str) and source.startswith(
            "source_facts.powershell_scripts."
        ):
            collection = source.removeprefix("source_facts.powershell_scripts.")
            source_module = source_facts["powershell_scripts"][reference["script_index"]]
            record = {
                "fact": deepcopy(source_module[collection][reference["record_index"]]),
                "file_path": _text(source_module.get("path"), "Evidence file path"),
                "language": "powershell",
            }
        elif source == "source_facts.python_definition_index.files.definitions":
            source_file = source_facts["python_definition_index"]["files"][reference["file_index"]]
            record = {
                "definition": deepcopy(
                    source_file["definitions"][reference["record_index"]]
                ),
                "file_path": _text(source_file.get("path"), "Evidence file path"),
            }
        elif source == "source_facts.python_call_facts.files.calls":
            source_file = source_facts["python_call_facts"]["files"][reference["file_index"]]
            record = {
                "call": deepcopy(source_file["calls"][reference["record_index"]]),
                "file_path": _text(source_file.get("path"), "Evidence file path"),
            }
        else:
            raise ExplorerInputError(
                f"View Projection Evidence source is invalid: {evidence_id}"
            )
        if "source_trail_kind" in reference:
            record["source_trail_kind"] = reference["source_trail_kind"]
        if "enclosing_definition_evidence_id" in reference:
            record["enclosing_definition_evidence_id"] = reference[
                "enclosing_definition_evidence_id"
            ]
        completed[evidence_id] = record
    return completed


def _source_projection(
    source_facts: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if source_facts is None:
        return {}, {
            "schema": SOURCE_CATALOG_SCHEMA,
            "file_paths": [],
            "python_module_paths": [],
            "powershell_script_paths": [],
            "python_files_scanned": 0,
            "powershell_files_scanned": 0,
        }
    if not isinstance(source_facts, dict):
        raise ExplorerInputError("View Projection Source Facts must be an object")

    modules = source_facts.get("modules")
    powershell_scripts = source_facts.get("powershell_scripts")
    definition_files = source_facts.get("python_definition_index", {}).get(
        "files"
    )
    call_files = source_facts.get("python_call_facts", {}).get("files")
    groups = {
        "Python module": (
            modules,
            ("module_id", "module_kind", "file_sha256", "size_bytes"),
            {
                "fact_evidence_ids": ("direct_imports", "entries"),
            },
        ),
        "PowerShell script": (
            powershell_scripts,
            ("file_sha256", "size_bytes", "parse_status", "parse_errors"),
            {
                "powershell_evidence_ids": (
                    "entries",
                    "script_bindings",
                    "dot_sources",
                ),
            },
        ),
        "Definition file": (
            definition_files,
            ("module_id", "file_sha256"),
            {"definition_evidence_ids": ("definitions",)},
        ),
        "Call Fact file": (
            call_files,
            ("module_id", "file_sha256"),
            {"call_evidence_ids": ("calls",)},
        ),
    }
    for label, (records, _, _) in groups.items():
        if not isinstance(records, list):
            raise ExplorerInputError(
                f"View Projection Source Catalog {label} records must be a list"
            )

    source_file_index: dict[str, dict[str, Any]] = {}
    for label, (records, metadata_fields, reference_groups) in groups.items():
        seen_paths: set[str] = set()
        for source_file in records:
            if not isinstance(source_file, dict):
                raise ExplorerInputError(
                    f"View Projection Source Catalog {label} must be an object"
                )
            path = _text(source_file.get("path"), "Source file path")
            if path in seen_paths:
                raise ExplorerInputError(
                    f"View Projection duplicate Source file identity: {label}:{path}"
                )
            seen_paths.add(path)
            combined = source_file_index.setdefault(path, {"path": path})
            for field in metadata_fields:
                if field not in source_file:
                    continue
                value = source_file[field]
                if field in combined and combined[field] != value:
                    raise ExplorerInputError(
                        "View Projection conflicting Source file record: "
                        f"{path}:{field}"
                    )
                combined[field] = deepcopy(value)
            for output_field, collections in reference_groups.items():
                identities = combined.setdefault(output_field, [])
                for collection in collections:
                    records_in_collection = source_file.get(collection)
                    if not isinstance(records_in_collection, list):
                        raise ExplorerInputError(
                            "View Projection Source Catalog collection must be a list: "
                            f"{label}:{path}:{collection}"
                        )
                    for record in records_in_collection:
                        evidence = record.get("evidence") if isinstance(record, dict) else None
                        evidence_id = (
                            evidence.get("evidence_id")
                            if isinstance(evidence, dict)
                            else None
                        )
                        if not isinstance(evidence_id, str) or not evidence_id:
                            if label == "PowerShell script" and isinstance(record, dict) and record.get("resolution") != "known":
                                continue
                            raise ExplorerInputError(
                                "View Projection Source Catalog record needs Evidence identity: "
                                f"{label}:{path}:{collection}"
                            )
                        if evidence_id in identities:
                            raise ExplorerInputError(
                                "View Projection duplicate Evidence reference in Source file: "
                                f"{path}:{evidence_id}"
                            )
                        identities.append(evidence_id)

    python_scope = source_facts.get("scope", {})
    powershell_scope = source_facts.get("powershell_scope", {})
    catalog = {
        "schema": SOURCE_CATALOG_SCHEMA,
        "scanner_version": source_facts.get("scanner_version"),
        "limitations": deepcopy(source_facts.get("limitations", [])),
        "file_paths": sorted(source_file_index),
        "python_module_paths": [source_file["path"] for source_file in modules],
        "powershell_script_paths": [
            source_file["path"] for source_file in powershell_scripts
        ],
        "python_files_scanned": (
            python_scope.get("files_scanned")
            if isinstance(python_scope, dict)
            else 0
        ),
        "powershell_files_scanned": (
            powershell_scope.get("files_scanned")
            if isinstance(powershell_scope, dict)
            else 0
        ),
    }
    return source_file_index, catalog


def _validate_candidate_evidence_references(
    records: list[dict[str, Any]],
    evidence_index: dict[str, dict[str, Any]],
) -> None:
    """Allow shared references while rejecting ambiguity within one object."""

    for record in records:
        object_key = record.get("object_key")
        if not isinstance(object_key, dict):
            continue
        kind = object_key.get("kind")
        object_id = object_key.get("object_id")
        if kind == "candidate-node":
            assignment = record.get("source_assignment")
            evidence_ids = (
                assignment.get("evidence_ids")
                if isinstance(assignment, dict)
                else None
            )
        elif kind in {"candidate-collaboration", "candidate-decomposition"}:
            evidence_ids = record.get("evidence_ids")
        else:
            continue
        if not isinstance(evidence_ids, list) or not all(
            isinstance(evidence_id, str) and evidence_id for evidence_id in evidence_ids
        ):
            raise ExplorerInputError(
                f"View Projection Evidence references must be a string list: {kind}::{object_id}"
            )
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ExplorerInputError(
                f"View Projection object repeats Evidence reference: {kind}::{object_id}"
            )
        unknown = next(
            (evidence_id for evidence_id in evidence_ids if evidence_id not in evidence_index),
            None,
        )
        if unknown is not None:
            raise ExplorerInputError(
                f"View Projection object has unknown Evidence reference: {kind}::{object_id}:{unknown}"
            )


def apply_view_projection(explorer: dict[str, Any]) -> dict[str, Any]:
    """Adapt the current authoritative inputs into a read-only View Projection."""

    result = deepcopy(explorer)
    explicit_reviews = _explicit_reviews(result)
    legacy_reviews = _legacy_node_reviews(result)
    legacy_projection = result.get("node_review_projection", {})
    legacy_details = (
        legacy_projection.get("objects", {})
        if isinstance(legacy_projection, dict)
        and isinstance(legacy_projection.get("objects", {}), dict)
        else {}
    )
    duplicates = sorted(set(explicit_reviews) & set(legacy_reviews))
    if duplicates:
        raise ExplorerInputError(
            f"View Projection review object is supplied twice: {duplicates[0]}"
        )
    explicit_reviews.update(legacy_reviews)
    source_facts = result.get("source_facts")
    source_evidence_index = (
        build_source_evidence_index(source_facts)
        if source_facts is not None
        else {}
    )
    evidence_projection = _complete_evidence_projection(source_facts, source_evidence_index)
    source_file_index, source_catalog = _source_projection(source_facts)
    result["source_evidence_index"] = source_evidence_index
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
    recursive_fragments = result.get("recursive_candidate_fragments", [])
    view_definitions = result.get("view_definitions")
    if not isinstance(modules, list) or not isinstance(relationships, list):
        raise ExplorerInputError("View Projection requires architecture indexes")
    if not isinstance(drilldowns, list) or not isinstance(root, dict):
        raise ExplorerInputError("View Projection requires navigation Views")
    if not isinstance(recursive_fragments, list):
        raise ExplorerInputError("View Projection recursive candidates must be a list")
    if view_definitions is not None and not isinstance(view_definitions, dict):
        raise ExplorerInputError("View Projection View definitions must be an object")

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
    for module in modules:
        if not isinstance(module, dict):
            raise ExplorerInputError("View Projection Node must be an object")
        node_id = _text(module.get("id"), "Node id")
        if node_id in node_index:
            raise ExplorerInputError(f"View Projection Node id must be unique: {node_id}")
        node_index[node_id] = deepcopy(module)

    for owner_view_id, drilldown in drilldown_by_parent.items():
        children = drilldown.get("children")
        if not isinstance(children, list):
            raise ExplorerInputError(f"View Projection View needs Appearances: {owner_view_id}")
        for child in children:
            if not isinstance(child, dict):
                raise ExplorerInputError("View Projection Node must be an object")
            node_id = _text(child.get("id"), "Node id")
            if node_id in node_index:
                raise ExplorerInputError(
                    f"View Projection Node id must be globally unique: {node_id}"
                )
            node_index[node_id] = _candidate_node_record(
                child, explicit_reviews, legacy_details
            )

    for fragment_index, fragment in enumerate(recursive_fragments):
        if not isinstance(fragment, dict):
            raise ExplorerInputError("View Projection recursive candidate must be an object")
        nodes = fragment.get("nodes")
        if not isinstance(nodes, list):
            raise ExplorerInputError("View Projection recursive candidate needs Nodes")
        for node in nodes:
            if not isinstance(node, dict):
                raise ExplorerInputError("View Projection Node must be an object")
            node_id = _text(node.get("id"), "Node id")
            if node_id in node_index:
                raise ExplorerInputError(
                    f"View Projection Node id must be globally unique: {node_id}"
                )
            node_index[node_id] = _candidate_node_record(
                node, explicit_reviews, legacy_details
            )

    relationship_index: dict[str, dict[str, Any]] = {}
    for relationship in relationships:
        if not isinstance(relationship, dict):
            raise ExplorerInputError("View Projection Relationship must be an object")
        relationship_id = _text(relationship.get("id"), "Relationship id")
        if relationship_id in relationship_index:
            raise ExplorerInputError(
                f"View Projection Relationship id must be unique: {relationship_id}"
            )
        relationship_index[relationship_id] = _formal_relationship_record(relationship)

    for owner_view_id, drilldown in drilldown_by_parent.items():
        collaborations = drilldown.get("collaborations", [])
        if not isinstance(collaborations, list):
            raise ExplorerInputError(
                f"View Projection View relationships must be a list: {owner_view_id}"
            )
        for collaboration in collaborations:
            if not isinstance(collaboration, dict):
                raise ExplorerInputError("View Projection Relationship must be an object")
            relationship_id = _text(collaboration.get("id"), "Relationship id")
            if relationship_id in relationship_index:
                raise ExplorerInputError(
                    "View Projection Relationship id must be globally unique: "
                    f"{relationship_id}"
                )
            relationship_index[relationship_id] = _candidate_relationship_record(
                collaboration, explicit_reviews, kind="candidate-collaboration"
            )

    for fragment_index, fragment in enumerate(recursive_fragments):
        recursive_relationships = fragment.get("relationships")
        if not isinstance(recursive_relationships, list):
            raise ExplorerInputError(
                "View Projection recursive candidate needs Relationships"
            )
        for relationship in recursive_relationships:
            if not isinstance(relationship, dict):
                raise ExplorerInputError("View Projection Relationship must be an object")
            relationship_id = _text(relationship.get("id"), "Relationship id")
            if relationship_id in relationship_index:
                raise ExplorerInputError(
                    f"View Projection Relationship id must be globally unique: {relationship_id}"
                )
            relationship_index[relationship_id] = _candidate_relationship_record(
                relationship,
                explicit_reviews,
                kind=_text(
                    relationship.get("semantic_family"),
                    "Relationship semantic family",
                ),
            )

    projected_object_keys = {
        _canonical_key(record["object_key"]["kind"], record["object_key"]["object_id"])
        for record in [*node_index.values(), *relationship_index.values()]
        if isinstance(record.get("object_key"), dict)
    }
    unknown_review_keys = sorted(set(explicit_reviews) - projected_object_keys)
    if unknown_review_keys:
        raise ExplorerInputError(
            "View Projection review references unknown object: "
            f"{unknown_review_keys[0]}"
        )
    _validate_candidate_evidence_references(
        [*node_index.values(), *relationship_index.values()], evidence_projection
    )

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
                        "transition_kind": "decomposition",
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

    entry_actions: list[dict[str, Any]] = []
    if view_definitions is not None:
        raw_views = view_definitions.get("views")
        entry_actions = view_definitions.get("entry_actions")
        if not isinstance(raw_views, list) or not isinstance(entry_actions, list):
            raise ExplorerInputError("View Projection View definitions are malformed")
        for raw_view in raw_views:
            if not isinstance(raw_view, dict):
                raise ExplorerInputError("View Projection View must be an object")
            view_id = _text(raw_view.get("id"), "View id")
            if view_id in view_index:
                raise ExplorerInputError(f"View Projection View id must be unique: {view_id}")
            appearances = raw_view.get("appearances")
            relationship_ids = raw_view.get("relationship_ids")
            if not isinstance(appearances, list) or not appearances:
                raise ExplorerInputError(f"View Projection View needs Appearances: {view_id}")
            if not isinstance(relationship_ids, list):
                raise ExplorerInputError(
                    f"View Projection View relationships must be a list: {view_id}"
                )
            view_index[view_id] = {
                "id": view_id,
                "display_name": _text(raw_view.get("display_name"), "View display name"),
                "perspective": _text(raw_view.get("perspective"), "View Perspective"),
                "scope_node_id": _text(raw_view.get("scope_node_id"), "scope Node id"),
                "coverage_note": _text(raw_view.get("coverage_note"), "coverage note"),
                "appearances": deepcopy(appearances),
                "relationship_ids": list(relationship_ids),
            }

    for entry_action in entry_actions:
        if not isinstance(entry_action, dict):
            raise ExplorerInputError("View Projection entry action must be an object")
        owner_view_id = _text(entry_action.get("view_id"), "entry action View id")
        node_id = _text(entry_action.get("node_id"), "entry action Node id")
        owner_view = view_index.get(owner_view_id)
        if owner_view is None:
            raise ExplorerInputError(
                f"View Projection entry action references unknown View: {owner_view_id}"
            )
        matches = [
            appearance
            for appearance in owner_view["appearances"]
            if appearance.get("node_id") == node_id
        ]
        if len(matches) != 1:
            raise ExplorerInputError(
                f"View Projection entry action references unknown Appearance: {owner_view_id}:{node_id}"
            )
        if "primary_action" in matches[0]:
            raise ExplorerInputError(
                f"View Projection entry action duplicates a Primary Navigation Action: {owner_view_id}:{node_id}"
            )
        action = entry_action.get("primary_action")
        if not isinstance(action, dict):
            raise ExplorerInputError("View Projection Primary Navigation Action must be an object")
        matches[0]["primary_action"] = deepcopy(action)

    for view_id, view in view_index.items():
        scope_node_id = view.get("scope_node_id")
        if view_id == root_view_id:
            if scope_node_id is not None:
                raise ExplorerInputError("View Projection root View cannot have a scope Node")
        elif scope_node_id not in node_index:
            raise ExplorerInputError(
                f"View Projection references unknown scope Node: {view_id}:{scope_node_id}"
            )
        seen_nodes: set[str] = set()
        visible_nodes = {
            appearance["node_id"] for appearance in view["appearances"]
        }
        for appearance in view["appearances"]:
            if not isinstance(appearance, dict):
                raise ExplorerInputError("View Projection Appearance must be an object")
            node_id = _text(appearance.get("node_id"), "Appearance Node id")
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
            if action is not None:
                if not isinstance(action, dict):
                    raise ExplorerInputError(
                        "View Projection Primary Navigation Action must be an object"
                    )
                kind = _text(action.get("kind"), "Primary Navigation Action kind")
                if kind not in {"open-view", "open-detail", "open-source-trail"}:
                    raise ExplorerInputError(
                        f"View Projection Primary Navigation Action kind is invalid: {kind}"
                    )
                _text(action.get("destination_label"), "destination label")
                if kind == "open-view":
                    target_view_id = _text(action.get("target_view_id"), "target View id")
                    if target_view_id not in view_index:
                        raise ExplorerInputError(
                            "View Projection references unknown target View: "
                            f"{target_view_id}"
                        )
                    transition_kind = _text(
                        action.get("transition_kind"), "View Transition kind"
                    )
                    if transition_kind not in {
                        "decomposition",
                        "cross-node",
                        "perspective",
                    }:
                        raise ExplorerInputError(
                            f"View Projection View Transition kind is invalid: {transition_kind}"
                        )
                    if (
                        transition_kind == "decomposition"
                        and view_index[target_view_id].get("scope_node_id") != node_id
                    ):
                        raise ExplorerInputError(
                            "View Projection decomposition target scope must match its Appearance: "
                            f"{view_id}:{node_id}->{target_view_id}"
                        )
                if kind == "open-source-trail":
                    evidence_id = _text(
                        action.get("target_evidence_id"), "Source Trail Evidence id"
                    )
                    evidence_reference = source_evidence_index.get(evidence_id)
                    if evidence_reference is None:
                        raise ExplorerInputError(
                            f"View Projection references unknown Source Trail Evidence: {evidence_id}"
                        )
                    if evidence_reference.get("source_trail_kind") not in {
                        "definition",
                        "call",
                    }:
                        raise ExplorerInputError(
                            "View Projection Source Trail target is not navigable: "
                            f"{evidence_id}"
                        )
        if len(set(view["relationship_ids"])) != len(view["relationship_ids"]):
            raise ExplorerInputError(
                f"View Projection repeats one Relationship in a View: {view_id}"
            )
        for relationship_id in view["relationship_ids"]:
            reference = relationship_index.get(relationship_id)
            if reference is None:
                raise ExplorerInputError(
                    f"View Projection references unknown Relationship: {relationship_id}"
                )
            endpoints = (
                reference.get("source_node_id"),
                reference.get("target_node_id"),
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
        "evidence_index": evidence_projection,
        "source_file_index": source_file_index,
        "source_catalog": source_catalog,
        "view_index": view_index,
    }
    canonical = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    result["view_projection"] = projection
    result["view_projection_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


__all__ = [
    "REVIEW_PROJECTION_SCHEMA",
    "VIEW_PROJECTION_SCHEMA",
    "apply_view_projection",
]
