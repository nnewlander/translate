from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import tempfile

import pytest

from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.node_review import (
    _NodeReviewApprovalAnchor,
    _NodeReviewTrustPolicy,
)
from booktrans.architecture_explorer.node_review_definitions import (
    build_node_review_packets,
    project_node_reviews,
    render_node_review_decision_body,
    validate_node_review_definitions,
    verify_node_review_v2_integrity,
)
from booktrans.architecture_explorer.node_review_v2_continuity import (
    evaluate_node_review_v2_state,
    update_node_review_v2_state,
)
from booktrans.architecture_explorer.relationship_review import (
    RelationshipReviewApprovalAnchor,
    RelationshipReviewTrustPolicy,
    build_relationship_review_packets,
    evaluate_relationship_review_state,
    project_relationship_reviews,
    render_relationship_review_decision_body,
    stable_review_object_key,
    update_relationship_review_state,
    validate_relationship_review_definitions,
    verify_relationship_review_integrity,
)
from booktrans.architecture_explorer.source_primitives import build_static_evidence
from booktrans.architecture_explorer.review_source import (
    build_review_source_receipt,
    review_source_sha256,
)


review_cli = importlib.import_module("booktrans.architecture_explorer.review_cli")
review_trust = importlib.import_module("booktrans.architecture_explorer.review_trust")


REVISION = "09aaf4a033123c7eca8d280fa8982e5b0e711b86"


def _evidence() -> dict:
    return build_static_evidence(
        path="src/example.py",
        source_role="automatic_source_fact",
        evidence_kind="python_function_entry",
        symbol="handoff",
        structural_anchor="python:function:handoff",
        syntax_position="module/function:handoff",
        snippet="def handoff():\n    return worker()",
        line_start=10,
        line_end=11,
        file_sha256="1" * 64,
    )


def _explorer(*, semantic_family: str = "candidate-collaboration") -> dict:
    evidence = _evidence()
    return {
        "source_facts": {
            "source_snapshot": {
                "revision": REVISION,
                "worktree": {"state": "clean"},
            },
            "deterministic_facts_sha256": "2" * 64,
            "fixture": {"evidence": evidence},
        },
        "navigation": {
            "drilldowns": [
                {
                    "parent_module_id": "production-control",
                    "children": [
                        {
                            "id": "supervisor-boundary",
                            "node_type": "responsibility",
                            "name": "Supervisor boundary",
                            "responsibility": "Prepare the bounded handoff.",
                            "plain_explanation": "Prepares a bounded handoff.",
                            "review_status": "candidate",
                            "source_assignment": {
                                "status": "unverified",
                                "note": "Candidate Evidence only.",
                                "relationship_ids": [],
                                "evidence_ids": [evidence["evidence_id"]],
                            },
                        },
                        {
                            "id": "worker-entry",
                            "node_type": "responsibility",
                            "name": "Worker entry",
                            "responsibility": "Validate the bounded handoff.",
                            "plain_explanation": "Validates a bounded handoff.",
                            "review_status": "candidate",
                            "source_assignment": {
                                "status": "unverified",
                                "note": "Candidate Evidence only.",
                                "relationship_ids": [],
                                "evidence_ids": [evidence["evidence_id"]],
                            },
                        }
                    ],
                    "collaborations": [],
                }
            ]
        },
        "recursive_candidate_fragments": [
            {
                "scope_node_id": "supervisor-boundary",
                "nodes": [],
                "relationships": [
                    {
                        "id": "relationship-under-review",
                        "semantic_family": semantic_family,
                        "source_node_id": "supervisor-boundary",
                        "target_node_id": "worker-entry",
                        "name": "Bounded handoff",
                        "professional_description": "Supervisor hands control to Worker.",
                        "plain_explanation": "One responsibility hands work to another.",
                        "review_status": "candidate",
                        "evidence_ids": [evidence["evidence_id"]],
                    }
                ],
            }
        ],
    }


def _definition(
    *,
    kind: str = "candidate-collaboration",
    object_id: str = "relationship-under-review",
) -> dict:
    return {
        "schema": "architecture-explorer.relationship-review-definitions.v1",
        "definitions": [
            {
                "object_key": {
                    "kind": kind,
                    "object_id": object_id,
                },
                "reviewed_source_snapshot": {
                    "revision": REVISION,
                    "worktree_state": "clean",
                },
                "endpoint_bindings": {
                    "source": {
                        "node_id": "supervisor-boundary",
                        "responsibility": "Prepare the bounded handoff.",
                    },
                    "target": {
                        "node_id": "worker-entry",
                        "responsibility": "Validate the bounded handoff.",
                    },
                },
                "semantics": {
                    "family": "collaboration" if kind == "candidate-collaboration" else "decomposition",
                    "direction": {
                        "source_node_id": "supervisor-boundary",
                        "target_node_id": "worker-entry",
                    },
                },
                "claim_kinds": [
                    "IDENTITY",
                    "ENDPOINT_RESPONSIBILITIES",
                    "SEMANTICS_AND_DIRECTION",
                    "RELATIONSHIP_DESCRIPTION",
                    "CANDIDATE_EVIDENCE",
                    "DEPENDENCIES_AND_LIMITS",
                ],
                "candidate_evidence_ids": [_evidence()["evidence_id"]],
                "dependencies_and_limits": {
                    "dependencies": [],
                    "zero_dependency_reason": "The candidate relationship is reviewed from its own evidence.",
                    "retained_unknowns": ["Runtime reachability is not established."],
                },
            }
        ],
    }


def _node_definition(node_id: str = "worker-entry") -> dict:
    return {
        "schema": "architecture-explorer.node-review-definitions.v1",
        "definitions": [
            {
                "object_key": {"kind": "candidate-node", "node_id": node_id},
                "reviewed_source_snapshot": {
                    "revision": REVISION,
                    "worktree_state": "clean",
                },
                "classification_rationale": "The source supports this bounded responsibility.",
                "claim_kinds": [
                    "IDENTITY",
                    "NAME",
                    "CLASSIFICATION",
                    "RESPONSIBILITY",
                    "PLAIN_EXPLANATION",
                    "SOURCE_ASSIGNMENT",
                    "CANDIDATE_EVIDENCE",
                    "DEPENDENCIES_AND_LIMITS",
                ],
                "candidate_evidence_ids": [_evidence()["evidence_id"]],
                "dependencies_and_limits": {
                    "dependencies": [],
                    "zero_dependency_reason": "No formal architecture dependency is required.",
                    "retained_unknowns": ["Runtime reachability remains unknown."],
                },
                "legacy_compatibility": None,
            }
        ],
    }


def _trusted_material(
    packet: dict, *, result: str = "APPROVED", comment_id: int = 77003
) -> tuple[dict, dict, RelationshipReviewTrustPolicy]:
    issue_number = 77
    created_at = "2026-11-25T08:00:00Z"
    decision = {
        "schema": "architecture-explorer.relationship-review-decision.v1",
        "decision_id": f"github-comment:{comment_id}",
        "object_key": deepcopy(packet["object_key"]),
        "result": result,
        "reviewed_source_snapshot": deepcopy(packet["reviewed_source_snapshot"]),
        "review_notes": "Imported from a pinned GitHub Owner/admin review source.",
        "source_receipt_sha256": "pending",
        "inputs": deepcopy(packet["inputs"]),
        "claims": deepcopy(packet["claims"]),
    }
    body = render_relationship_review_decision_body(decision)
    source = build_review_source_receipt(
        {
            "id": 1425303246,
            "node_id": "R_fixture",
            "full_name": "nnewlander/translate",
            "html_url": "https://github.com/nnewlander/translate",
            "private": True,
            "owner": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "U_fixture",
                "type": "User",
            },
        },
        {
            "id": 6599419502,
            "node_id": "I_fixture",
            "number": issue_number,
            "html_url": f"https://github.com/nnewlander/translate/issues/{issue_number}",
            "repository_url": "https://api.github.com/repos/nnewlander/translate",
            "state": "open",
        },
        {
            "id": comment_id,
            "node_id": "IC_fixture",
            "html_url": f"https://github.com/nnewlander/translate/issues/{issue_number}#issuecomment-{comment_id}",
            "issue_url": f"https://api.github.com/repos/nnewlander/translate/issues/{issue_number}",
            "author_association": "OWNER",
            "created_at": created_at,
            "updated_at": created_at,
            "body": body,
            "user": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "U_fixture",
                "type": "User",
            },
        },
        {
            "permission": "admin",
            "role_name": "admin",
            "user": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "U_fixture",
                "type": "User",
            },
        },
        fixed_t3_identity=False,
    )
    decision["source_receipt_sha256"] = review_source_sha256(source)
    policy = RelationshipReviewTrustPolicy(
        anchors=(
            RelationshipReviewApprovalAnchor(
                issue_id=6599419502,
                issue_number=issue_number,
                comment_id=comment_id,
                comment_created_at=created_at,
                comment_updated_at=created_at,
                source_receipt_sha256=review_source_sha256(source),
            ),
        )
    )
    return decision, source, policy


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _trusted_rejected_node_projection(
    explorer: dict,
    tmp_path: Path,
    *,
    rejected_claim_kind: str | None,
    materials_directory: Path | None = None,
    node_id: str = "worker-entry",
    comment_id: int = 77004,
) -> dict:
    definitions = validate_node_review_definitions(_node_definition(node_id))
    key = f"candidate-node::{node_id}"
    packet = build_node_review_packets(explorer, definitions)[key]
    rejected_claims = []
    if rejected_claim_kind is not None:
        claim = next(
            item for item in packet["claims"] if item["kind"] == rejected_claim_kind
        )
        rejected_claims.append(
            {
                "claim_sha256": _canonical_sha256(claim),
                "reason_code": "UNSUPPORTED_CLAIM",
                "explanation": "Owner explicitly rejected this bound Claim.",
            }
        )
    decision = {
        "schema": "architecture-explorer.node-review-decision.v2",
        "decision_id": f"github-comment:{comment_id}",
        "object_key": deepcopy(packet["object_key"]),
        "result": "REJECTED",
        "reviewed_source_snapshot": deepcopy(packet["reviewed_source_snapshot"]),
        "review_notes": "Imported from a pinned GitHub Owner/admin review source.",
        "source_receipt_sha256": "pending",
        "inputs": deepcopy(packet["inputs"]),
        "claims": deepcopy(packet["claims"]),
    }
    if rejected_claim_kind is not None:
        decision["rejection_basis"] = {
            "schema": "architecture-explorer.rejection-basis.v1",
            "rejected_claims": rejected_claims,
        }
    body = render_node_review_decision_body(decision)
    created_at = "2026-11-25T08:10:00Z"
    source = build_review_source_receipt(
        {
            "id": 1425303246,
            "node_id": "R_fixture",
            "full_name": "nnewlander/translate",
            "html_url": "https://github.com/nnewlander/translate",
            "private": True,
            "owner": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "U_fixture",
                "type": "User",
            },
        },
        {
            "id": 6599419502,
            "node_id": "I_fixture",
            "number": 77,
            "html_url": "https://github.com/nnewlander/translate/issues/77",
            "repository_url": "https://api.github.com/repos/nnewlander/translate",
            "state": "open",
        },
        {
            "id": comment_id,
            "node_id": "IC_fixture_node",
            "html_url": f"https://github.com/nnewlander/translate/issues/77#issuecomment-{comment_id}",
            "issue_url": "https://api.github.com/repos/nnewlander/translate/issues/77",
            "author_association": "OWNER",
            "created_at": created_at,
            "updated_at": created_at,
            "body": body,
            "user": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "U_fixture",
                "type": "User",
            },
        },
        {
            "permission": "admin",
            "role_name": "admin",
            "user": {
                "login": "nnewlander",
                "id": 256049772,
                "node_id": "U_fixture",
                "type": "User",
            },
        },
        fixed_t3_identity=False,
    )
    decision["source_receipt_sha256"] = review_source_sha256(source)
    policy = _NodeReviewTrustPolicy(
        anchors=(
            _NodeReviewApprovalAnchor(
                issue_id=6599419502,
                issue_number=77,
                comment_id=comment_id,
                comment_created_at=created_at,
                comment_updated_at=created_at,
                source_receipt_sha256=review_source_sha256(source),
            ),
        )
    )
    state_path = tmp_path / "node-state.json"
    update_node_review_v2_state(
        explorer,
        definitions[0],
        decision,
        source,
        state_path,
        assessed_at="2026-11-25T08:11:00Z",
        trust_policy=policy,
    )
    if materials_directory is not None:
        materials_directory.mkdir(parents=True)
        for filename, value in (
            ("decision.json", decision),
            ("source.json", source),
        ):
            (materials_directory / filename).write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
        (materials_directory / "state.json").write_text(
            state_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return project_node_reviews(
        explorer,
        definitions,
        {
            key: {
                "decision": decision,
                "source": source,
                "state": evaluate_node_review_v2_state(decision, state_path),
            }
        },
        trust_policy=policy,
    )


def test_pending_endpoints_do_not_block_relationship_packet() -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())

    packets = build_relationship_review_packets(explorer, definitions)
    key = "candidate-collaboration::relationship-under-review"
    packet = packets[key]

    assert stable_review_object_key(packet["object_key"]) == key
    assert packet["candidate_fact"]["semantic_family"] == "collaboration"
    assert packet["endpoint_bindings"] == {
        "source": {
            "node_id": "supervisor-boundary",
            "responsibility": "Prepare the bounded handoff.",
        },
        "target": {
            "node_id": "worker-entry",
            "responsibility": "Validate the bounded handoff.",
        },
    }
    assert all(
        "review_status" not in endpoint
        for endpoint in packet["endpoint_bindings"].values()
    )
    assert packet["allowed_results"] == ["APPROVED", "REJECTED"]
    assert packet == deepcopy(packet)


def test_v3_collaboration_container_adapts_semantics_without_id_inference() -> None:
    explorer = _explorer()
    relationship = explorer["recursive_candidate_fragments"][0]["relationships"].pop()
    relationship.pop("semantic_family")
    explorer["navigation"]["drilldowns"][0]["collaborations"].append(relationship)
    definitions = validate_relationship_review_definitions(_definition())

    packet = build_relationship_review_packets(explorer, definitions)[
        "candidate-collaboration::relationship-under-review"
    ]

    assert packet["candidate_fact"]["semantic_family"] == "collaboration"
    assert packet["candidate_fact"]["object_id"] == "relationship-under-review"


def test_missing_decision_is_an_explicit_pending_relationship_projection() -> None:
    explorer = _explorer(semantic_family="candidate-decomposition")
    explorer["node_review_projection"] = {
        "schema": "architecture-explorer.node-review-projection.v1",
        "objects": {
            "candidate-node::supervisor-boundary": {
                "object_key": {
                    "kind": "candidate-node",
                    "node_id": "supervisor-boundary",
                },
                "decision": {"result": "APPROVED"},
                "state": {"status": "current", "continuity": "exact-match"},
                "projection": "Current · Approved",
            },
            "candidate-node::worker-entry": {
                "object_key": {"kind": "candidate-node", "node_id": "worker-entry"},
                "decision": {"result": "APPROVED"},
                "state": {"status": "current", "continuity": "exact-match"},
                "projection": "Current · Approved",
            },
        },
    }
    definitions = validate_relationship_review_definitions(
        _definition(kind="candidate-decomposition")
    )

    projected = project_relationship_reviews(explorer, definitions, {})
    review = projected["review_projection"]["objects"][
        "candidate-decomposition::relationship-under-review"
    ]

    assert review == {
        "object_key": {
            "kind": "candidate-decomposition",
            "object_id": "relationship-under-review",
        },
        "semantics": {
            "family": "decomposition",
            "direction": {
                "source_node_id": "supervisor-boundary",
                "target_node_id": "worker-entry",
            },
        },
        "result": "Pending",
        "state": "not-reviewed",
        "approved_scope": [],
        "reasons": ["trusted-relationship-review-decision:missing"],
    }


def test_trusted_approved_relationship_projects_current_without_endpoint_approval(
    tmp_path: Path,
) -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)
    state_path = tmp_path / "relationship-review-state.json"

    update_relationship_review_state(
        explorer,
        definitions[0],
        decision,
        source,
        state_path,
        assessed_at="2026-11-25T08:01:00Z",
        trust_policy=policy,
    )
    state = evaluate_relationship_review_state(decision, state_path)
    projected = project_relationship_reviews(
        explorer,
        definitions,
        {key: {"decision": decision, "source": source, "state": state}},
        trust_policy=policy,
    )
    review = projected["review_projection"]["objects"][key]

    assert review["result"] == "APPROVED"
    assert review["state"] == "current"
    assert review["approved_scope"] == [{
        "object_key": decision["object_key"],
        "reviewed_source_snapshot": decision["reviewed_source_snapshot"],
        "claims_sha256": decision["inputs"]["claims_sha256"],
        "evidence_ids": packet["candidate_fact"]["evidence_ids"],
        "decision_id": decision["decision_id"],
    }]
    assert "node_review_projection" not in explorer


def test_continuity_ignores_endpoint_state_but_detects_responsibility_change(
    tmp_path: Path,
) -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)
    endpoint_state_only = deepcopy(explorer)
    endpoint_state_only["node_review_projection"] = {
        "schema": "architecture-explorer.node-review-projection.v1",
        "objects": {
            "candidate-node::worker-entry": {
                "object_key": {"kind": "candidate-node", "node_id": "worker-entry"},
                "decision": None,
                "state": {"status": "not-reviewed"},
                "projection": "Pending Review",
            }
        },
    }

    unchanged = update_relationship_review_state(
        endpoint_state_only,
        definitions[0],
        decision,
        source,
        tmp_path / "state-only.json",
        assessed_at="2026-11-25T08:02:00Z",
        trust_policy=policy,
    )
    changed_explorer = deepcopy(endpoint_state_only)
    changed_explorer["navigation"]["drilldowns"][0]["children"][1][
        "responsibility"
    ] = "The endpoint now owns a materially different responsibility."
    changed = update_relationship_review_state(
        changed_explorer,
        definitions[0],
        decision,
        source,
        tmp_path / "responsibility-change.json",
        assessed_at="2026-11-25T08:03:00Z",
        trust_policy=policy,
    )

    assert (unchanged["state"], unchanged["continuity"]) == (
        "current",
        "exact-match",
    )
    assert (changed["state"], changed["continuity"]) == (
        "needs-review",
        "material-change",
    )
    assert changed["reasons"] == ["relationship-review-material:material-change"]
    historical = project_relationship_reviews(
        changed_explorer,
        definitions,
        {
            key: {
                "decision": decision,
                "source": source,
                "state": evaluate_relationship_review_state(
                    decision, tmp_path / "responsibility-change.json"
                ),
            }
        },
        trust_policy=policy,
    )["review_projection"]["objects"][key]
    assert (historical["result"], historical["state"]) == (
        "APPROVED",
        "needs-review",
    )


def test_explicit_bound_responsibility_rejection_invalidates_stale_current(
    tmp_path: Path,
) -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)

    state_path = tmp_path / "relationship-state.json"
    stale = update_relationship_review_state(
        explorer,
        definitions[0],
        decision,
        source,
        state_path,
        assessed_at="2026-11-25T08:04:00Z",
        trust_policy=policy,
    )
    assert stale["state"] == "current"

    with_rejection = _trusted_rejected_node_projection(
        explorer,
        tmp_path / "node",
        rejected_claim_kind="RESPONSIBILITY",
    )
    persisted_before_projection = state_path.read_bytes()
    projected = project_relationship_reviews(
        with_rejection,
        definitions,
        {
            key: {
                "decision": decision,
                "source": source,
                "state": evaluate_relationship_review_state(decision, state_path),
            }
        },
        trust_policy=policy,
    )["review_projection"]["objects"][key]
    assert state_path.read_bytes() == persisted_before_projection
    invalidated = update_relationship_review_state(
        with_rejection,
        definitions[0],
        decision,
        source,
        state_path,
        assessed_at="2026-11-25T08:05:00Z",
        trust_policy=policy,
    )

    assert (projected["state"], projected["reasons"]) == (
        "needs-review",
        ["endpoint-rejection:worker-entry:responsibility-invalidated"],
    )
    assert (invalidated["state"], invalidated["continuity"]) == (
        "needs-review",
        "material-change",
    )
    assert invalidated["reasons"] == [
        "endpoint-rejection:worker-entry:responsibility-invalidated"
    ]


def test_rejection_of_other_endpoint_claim_does_not_invalidate_relationship(
    tmp_path: Path,
) -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)
    with_rejection = _trusted_rejected_node_projection(
        explorer,
        tmp_path / "node",
        rejected_claim_kind="NAME",
    )

    state = update_relationship_review_state(
        with_rejection,
        definitions[0],
        decision,
        source,
        tmp_path / "relationship-state.json",
        assessed_at="2026-11-25T08:05:00Z",
        trust_policy=policy,
    )

    assert (state["state"], state["continuity"], state["reasons"]) == (
        "current",
        "exact-match",
        [],
    )


def test_historical_rejection_without_explicit_basis_is_ambiguous(
    tmp_path: Path,
) -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)
    with_historical_rejection = _trusted_rejected_node_projection(
        explorer,
        tmp_path / "node",
        rejected_claim_kind=None,
    )

    state = update_relationship_review_state(
        with_historical_rejection,
        definitions[0],
        decision,
        source,
        tmp_path / "relationship-state.json",
        assessed_at="2026-11-25T08:05:00Z",
        trust_policy=policy,
    )

    assert (state["state"], state["continuity"]) == ("unknown", "ambiguous")
    assert state["reasons"] == [
        "endpoint-rejection:worker-entry:rejection-basis-ambiguous"
    ]


def test_semantics_direction_identity_and_evidence_changes_require_review(
    tmp_path: Path,
) -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)
    changed: dict[str, dict] = {}

    changed["semantics"] = deepcopy(explorer)
    changed["semantics"]["recursive_candidate_fragments"][0]["relationships"][0][
        "semantic_family"
    ] = "candidate-decomposition"

    changed["direction"] = deepcopy(explorer)
    changed["direction"]["recursive_candidate_fragments"][0]["relationships"][0][
        "target_node_id"
    ] = "supervisor-boundary"

    changed["endpoint-identity"] = deepcopy(explorer)
    changed["endpoint-identity"]["navigation"]["drilldowns"][0]["children"][1][
        "id"
    ] = "replacement-worker-entry"

    changed["evidence"] = deepcopy(explorer)
    changed["evidence"]["source_facts"]["fixture"]["evidence"][
        "snippet"
    ] = "def handoff():\n    return replacement()"

    changed["source-snapshot"] = deepcopy(explorer)
    changed["source-snapshot"]["source_facts"]["source_snapshot"][
        "revision"
    ] = "3" * 40

    for label, changed_explorer in changed.items():
        state = update_relationship_review_state(
            changed_explorer,
            definitions[0],
            decision,
            source,
            tmp_path / f"{label}.json",
            assessed_at="2026-11-25T08:06:00Z",
            trust_policy=policy,
        )
        assert (state["state"], state["continuity"]) == (
            "needs-review",
            "material-change",
        )


def test_missing_ambiguous_or_forged_sources_fail_closed() -> None:
    explorer = _explorer()
    definitions = validate_relationship_review_definitions(_definition())
    key = "candidate-collaboration::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet)

    missing = deepcopy(explorer)
    missing["source_facts"].pop("fixture")
    with pytest.raises(ExplorerInputError, match="Evidence is missing"):
        build_relationship_review_packets(missing, definitions)

    ambiguous = deepcopy(explorer)
    ambiguous["source_facts"]["duplicate"] = {
        "evidence": deepcopy(ambiguous["source_facts"]["fixture"]["evidence"])
    }
    with pytest.raises(ExplorerInputError, match="duplicate or ambiguous"):
        build_relationship_review_packets(ambiguous, definitions)

    forged = deepcopy(source)
    forged["comment"]["body"] += "\nforged"
    with pytest.raises(ExplorerInputError, match="untrusted"):
        verify_relationship_review_integrity(
            definitions[0], decision, forged, trust_policy=policy
        )


def test_historical_node_key_is_normalized_without_rewriting_it() -> None:
    legacy = {"kind": "candidate-node", "node_id": "worker-launch-boundary"}

    assert stable_review_object_key(legacy) == (
        "candidate-node::worker-launch-boundary"
    )
    assert legacy == {
        "kind": "candidate-node",
        "node_id": "worker-launch-boundary",
    }


def test_relationship_dependencies_use_closed_canonical_object_bindings() -> None:
    value = _definition()
    limits = value["definitions"][0]["dependencies_and_limits"]
    limits["zero_dependency_reason"] = None
    limits["dependencies"] = [{"kind": "candidate-node", "node_id": "worker-entry"}]

    with pytest.raises(ExplorerInputError, match="Dependency must use the closed schema"):
        validate_relationship_review_definitions(value)


def test_trusted_decomposition_rejection_is_an_independent_current_result(
    tmp_path: Path,
) -> None:
    explorer = _explorer(semantic_family="candidate-decomposition")
    definitions = validate_relationship_review_definitions(
        _definition(kind="candidate-decomposition")
    )
    key = "candidate-decomposition::relationship-under-review"
    packet = build_relationship_review_packets(explorer, definitions)[key]
    decision, source, policy = _trusted_material(packet, result="REJECTED")
    state_path = tmp_path / "rejected-decomposition.json"

    update_relationship_review_state(
        explorer,
        definitions[0],
        decision,
        source,
        state_path,
        assessed_at="2026-11-25T08:07:00Z",
        trust_policy=policy,
    )
    projected = project_relationship_reviews(
        explorer,
        definitions,
        {
            key: {
                "decision": decision,
                "source": source,
                "state": evaluate_relationship_review_state(decision, state_path),
            }
        },
        trust_policy=policy,
    )
    review = projected["review_projection"]["objects"][key]

    assert review["semantics"]["family"] == "decomposition"
    assert review["result"] == "REJECTED"
    assert review["state"] == "current"


def test_review_cli_reads_authoritative_materials_and_projects_all_object_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    short_temp = tempfile.TemporaryDirectory(prefix=".ae77-", dir=Path.cwd())
    request.addfinalizer(short_temp.cleanup)
    tmp_path = Path(short_temp.name)
    repository = tmp_path / "repository"
    repository.mkdir()
    explorer = _explorer()
    decomposition = deepcopy(
        explorer["recursive_candidate_fragments"][0]["relationships"][0]
    )
    decomposition["id"] = "decomposition-under-review"
    decomposition["semantic_family"] = "candidate-decomposition"
    explorer["recursive_candidate_fragments"][0]["relationships"].append(
        decomposition
    )
    explorer["architecture"] = {
        "relationships": [
            {
                "review_status": "candidate",
                "source_continuity": {"status": "current"},
            }
        ]
    }
    explorer["implementation"] = {"revision": REVISION}
    explorer["generation_inputs"] = {}
    explorer_path = repository / "explorer.json"
    node_definitions_path = repository / "node-definitions.json"
    relationship_definitions_path = repository / "relationship-definitions.json"
    explorer_path.write_text(json.dumps(explorer), encoding="utf-8")
    node_definitions_value = _node_definition()
    node_definitions_value["definitions"].extend(
        _node_definition("supervisor-boundary")["definitions"]
    )
    node_definitions_path.write_text(
        json.dumps(node_definitions_value), encoding="utf-8"
    )
    relationship_definitions_value = _definition()
    relationship_definitions_value["definitions"].extend(
        _definition(
            kind="candidate-decomposition",
            object_id="decomposition-under-review",
        )["definitions"]
    )
    relationship_definitions_path.write_text(
        json.dumps(relationship_definitions_value), encoding="utf-8"
    )

    assert review_cli._main(
        [
            "prepare-relationship-reviews",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--review-definitions",
            str(relationship_definitions_path),
            "--output-directory",
            "output/packets",
        ],
        node_review_trust_policy=_NodeReviewTrustPolicy(),
    ) == 0
    assert (
        repository
        / "output/packets/candidate-collaboration__relationship-under-review/review-packet.json"
    ).is_file()

    relationship_key = "candidate-collaboration::relationship-under-review"
    relationship_packet = build_relationship_review_packets(
        explorer,
        validate_relationship_review_definitions(relationship_definitions_value),
    )[relationship_key]
    relationship_decision, relationship_source, relationship_policy = (
        _trusted_material(relationship_packet)
    )
    relationship_materials = repository / "output/relationship-materials"
    relationship_materials.mkdir(parents=True)
    (relationship_materials / "decision.json").write_text(
        json.dumps(relationship_decision), encoding="utf-8"
    )
    (relationship_materials / "source.json").write_text(
        json.dumps(relationship_source), encoding="utf-8"
    )
    monkeypatch.setattr(
        review_cli,
        "scan_python_source_facts",
        lambda _repository: deepcopy(explorer["source_facts"]),
    )
    assert review_cli._main(
        [
            "update-relationship-review-state",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--review-definition",
            str(relationship_definitions_path),
            "--review-decision",
            str(relationship_materials / "decision.json"),
            "--review-source",
            str(relationship_materials / "source.json"),
            "--review-state",
            "output/relationship-materials/state.json",
            "--node-review-definitions",
            str(node_definitions_path),
            "--assessed-at",
            "2026-11-25T08:12:00Z",
        ],
        node_review_trust_policy=_NodeReviewTrustPolicy(),
        relationship_review_trust_policy=relationship_policy,
    ) == 0

    decomposition_key = "candidate-decomposition::decomposition-under-review"
    decomposition_packet = build_relationship_review_packets(
        explorer,
        validate_relationship_review_definitions(relationship_definitions_value),
    )[decomposition_key]
    decomposition_decision, decomposition_source, decomposition_policy = (
        _trusted_material(decomposition_packet, result="REJECTED", comment_id=77005)
    )
    decomposition_materials = repository / "output/decomposition-materials"
    decomposition_materials.mkdir()
    (decomposition_materials / "decision.json").write_text(
        json.dumps(decomposition_decision), encoding="utf-8"
    )
    (decomposition_materials / "source.json").write_text(
        json.dumps(decomposition_source), encoding="utf-8"
    )
    update_relationship_review_state(
        explorer,
        validate_relationship_review_definitions(relationship_definitions_value)[1],
        decomposition_decision,
        decomposition_source,
        decomposition_materials / "state.json",
        assessed_at="2026-11-25T08:13:00Z",
        trust_policy=decomposition_policy,
    )
    combined_relationship_policy = RelationshipReviewTrustPolicy(
        anchors=relationship_policy.anchors + decomposition_policy.anchors
    )

    node_materials = repository / "node-materials"
    _trusted_rejected_node_projection(
        explorer,
        tmp_path / "node-state",
        rejected_claim_kind="RESPONSIBILITY",
        materials_directory=node_materials,
    )
    node_source = json.loads(
        (node_materials / "source.json").read_text(encoding="utf-8")
    )
    node_policy = _NodeReviewTrustPolicy(
        anchors=(
            _NodeReviewApprovalAnchor(
                issue_id=node_source["issue"]["id"],
                issue_number=node_source["issue"]["number"],
                comment_id=node_source["comment"]["id"],
                comment_created_at=node_source["comment"]["created_at"],
                comment_updated_at=node_source["comment"]["updated_at"],
                source_receipt_sha256=review_source_sha256(node_source),
            ),
        )
    )
    missing_endpoint_definitions_path = repository / "missing-endpoint-definitions.json"
    missing_endpoint_definitions_path.write_text(
        json.dumps(_node_definition()), encoding="utf-8"
    )
    assert review_cli._main(
        [
            "update-relationship-review-state",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--review-definition",
            str(relationship_definitions_path),
            "--review-decision",
            str(relationship_materials / "decision.json"),
            "--review-source",
            str(relationship_materials / "source.json"),
            "--review-state",
            "output/pending-endpoint-state.json",
            "--node-review-definitions",
            str(missing_endpoint_definitions_path),
            "--assessed-at",
            "2026-11-25T08:13:30Z",
        ],
        node_review_trust_policy=_NodeReviewTrustPolicy(),
        relationship_review_trust_policy=relationship_policy,
    ) == 0
    pending_endpoint_state = json.loads(
        (repository / "output/pending-endpoint-state.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        pending_endpoint_state["state"],
        pending_endpoint_state["continuity"],
    ) == ("current", "exact-match")

    incomplete_node_materials = repository / "incomplete-node-materials"
    incomplete_node_materials.mkdir()
    (incomplete_node_materials / "decision.json").write_bytes(
        (node_materials / "decision.json").read_bytes()
    )
    with pytest.raises(ExplorerInputError, match="material directory is incomplete"):
        review_cli._main(
            [
                "update-relationship-review-state",
                "--repository",
                str(repository),
                "--explorer-data",
                str(explorer_path),
                "--review-definition",
                str(relationship_definitions_path),
                "--review-decision",
                str(relationship_materials / "decision.json"),
                "--review-source",
                str(relationship_materials / "source.json"),
                "--review-state",
                "output/incomplete-endpoint-state.json",
                "--node-review-definitions",
                str(node_definitions_path),
                "--node-review-material-directory",
                str(incomplete_node_materials),
                "--assessed-at",
                "2026-11-25T08:13:40Z",
            ],
            node_review_trust_policy=node_policy,
            relationship_review_trust_policy=relationship_policy,
        )
    assert not (repository / "output/incomplete-endpoint-state.json").exists()

    forged_node_materials = repository / "forged-node-materials"
    forged_node_materials.mkdir()
    (forged_node_materials / "decision.json").write_bytes(
        (node_materials / "decision.json").read_bytes()
    )
    forged_node_source = deepcopy(node_source)
    forged_node_source["comment"]["body"] += "\nforged"
    (forged_node_materials / "source.json").write_text(
        json.dumps(forged_node_source), encoding="utf-8"
    )
    (forged_node_materials / "state.json").write_bytes(
        (node_materials / "state.json").read_bytes()
    )
    with pytest.raises(ExplorerInputError, match="source is untrusted"):
        review_cli._main(
            [
                "update-relationship-review-state",
                "--repository",
                str(repository),
                "--explorer-data",
                str(explorer_path),
                "--review-definition",
                str(relationship_definitions_path),
                "--review-decision",
                str(relationship_materials / "decision.json"),
                "--review-source",
                str(relationship_materials / "source.json"),
                "--review-state",
                "output/forged-endpoint-state.json",
                "--node-review-definitions",
                str(node_definitions_path),
                "--node-review-material-directory",
                str(forged_node_materials),
                "--assessed-at",
                "2026-11-25T08:13:45Z",
            ],
            node_review_trust_policy=node_policy,
            relationship_review_trust_policy=relationship_policy,
        )
    assert not (repository / "output/forged-endpoint-state.json").exists()

    relationship_decision_before_update = (
        relationship_materials / "decision.json"
    ).read_bytes()
    assert review_cli._main(
        [
            "update-relationship-review-state",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--review-definition",
            str(relationship_definitions_path),
            "--review-decision",
            str(relationship_materials / "decision.json"),
            "--review-source",
            str(relationship_materials / "source.json"),
            "--review-state",
            "output/relationship-materials/state.json",
            "--node-review-definitions",
            str(node_definitions_path),
            "--node-review-material-directory",
            str(node_materials),
            "--assessed-at",
            "2026-11-25T08:14:00Z",
        ],
        node_review_trust_policy=node_policy,
        relationship_review_trust_policy=relationship_policy,
    ) == 0
    updated_relationship_state = json.loads(
        (relationship_materials / "state.json").read_text(encoding="utf-8")
    )
    assert (
        updated_relationship_state["state"],
        updated_relationship_state["continuity"],
        updated_relationship_state["reasons"],
    ) == (
        "needs-review",
        "material-change",
        ["endpoint-rejection:worker-entry:responsibility-invalidated"],
    )
    assert (
        relationship_materials / "decision.json"
    ).read_bytes() == relationship_decision_before_update
    dummy_paths = {}
    for name in ("snapshot", "mapping", "candidate", "review-mapping"):
        path = repository / f"{name}.json"
        path.write_text("{}", encoding="utf-8")
        dummy_paths[name] = path
    web_assets = repository / "web"
    web_assets.mkdir()
    captured: dict[str, dict] = {}
    monkeypatch.setattr(
        review_cli,
        "publish_reviewed_explorer",
        lambda *_args, **_kwargs: deepcopy(explorer),
    )
    monkeypatch.setattr(
        review_cli,
        "attach_python_source_facts",
        lambda data, _facts: data,
    )
    monkeypatch.setattr(
        review_cli,
        "write_site",
        lambda data, _output, _assets: captured.setdefault("data", deepcopy(data)),
    )

    assert review_cli._main(
        [
            "build",
            "--repository",
            str(repository),
            "--snapshot",
            str(dummy_paths["snapshot"]),
            "--mapping",
            str(dummy_paths["mapping"]),
            "--candidate",
            str(dummy_paths["candidate"]),
            "--review-mapping",
            str(dummy_paths["review-mapping"]),
            "--node-review-definitions",
            str(node_definitions_path),
            "--node-review-material-directory",
            str(node_materials),
            "--relationship-review-definitions",
            str(relationship_definitions_path),
            "--relationship-review-material-directory",
            str(relationship_materials),
            "--relationship-review-material-directory",
            str(decomposition_materials),
            "--web-assets",
            str(web_assets),
            "--output-directory",
            "output/site",
        ],
        node_review_trust_policy=node_policy,
        relationship_review_trust_policy=combined_relationship_policy,
    ) == 0

    projected = captured["data"]
    assert projected["node_review_projection"]["objects"][
        "candidate-node::worker-entry"
    ]["projection"] == "Current · Rejected"
    relationship = projected["review_projection"]["objects"][
        relationship_key
    ]
    assert (relationship["result"], relationship["state"]) == (
        "APPROVED",
        "needs-review",
    )
    assert relationship["reasons"] == [
        "endpoint-rejection:worker-entry:responsibility-invalidated"
    ]
    decomposition_review = projected["review_projection"]["objects"][
        decomposition_key
    ]
    assert (decomposition_review["result"], decomposition_review["state"]) == (
        "REJECTED",
        "needs-review",
    )
    handoff = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "docs/architecture-explorer/v4-review-projection-handoff.json"
        ).read_text(encoding="utf-8")
    )
    assert projected["review_projection"]["schema"] == handoff["schema"]
    for key in (relationship_key, decomposition_key):
        actual = projected["review_projection"]["objects"][key]
        example = handoff["objects"][key]
        assert actual["object_key"] == example["object_key"]
        assert actual["semantics"] == example["semantics"]
        assert actual["result"] == example["result"]
        assert isinstance(actual["state"], str)
        assert isinstance(actual["approved_scope"], list)
        assert isinstance(actual["reasons"], list)


def test_formal_main_reads_host_owned_relationship_approval_anchors(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    short_temp = tempfile.TemporaryDirectory(prefix=".ae77-main-", dir=Path.cwd())
    request.addfinalizer(short_temp.cleanup)
    repository = Path(short_temp.name) / "repository"
    repository.mkdir()
    explorer = _explorer()
    explorer_path = repository / "explorer.json"
    relationship_definitions_path = repository / "relationship-definitions.json"
    node_definitions_path = repository / "node-definitions.json"
    explorer_path.write_text(json.dumps(explorer), encoding="utf-8")
    relationship_definitions_path.write_text(json.dumps(_definition()), encoding="utf-8")
    node_definitions = _node_definition()
    node_definitions["definitions"].extend(
        _node_definition("supervisor-boundary")["definitions"]
    )
    node_definitions_path.write_text(json.dumps(node_definitions), encoding="utf-8")
    packet = build_relationship_review_packets(
        explorer, validate_relationship_review_definitions(_definition())
    )["candidate-collaboration::relationship-under-review"]
    decision, source, policy = _trusted_material(packet)
    materials = repository / "materials"
    materials.mkdir()
    (materials / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
    (materials / "source.json").write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setattr(
        review_cli,
        "scan_python_source_facts",
        lambda _repository: deepcopy(explorer["source_facts"]),
    )
    monkeypatch.setattr(
        review_trust,
        "PRODUCTION_RELATIONSHIP_REVIEW_APPROVAL_ANCHORS",
        policy.anchors,
        raising=False,
    )

    assert review_cli.main(
        [
            "update-relationship-review-state",
            "--repository",
            str(repository),
            "--explorer-data",
            str(explorer_path),
            "--review-definition",
            str(relationship_definitions_path),
            "--review-decision",
            str(materials / "decision.json"),
            "--review-source",
            str(materials / "source.json"),
            "--review-state",
            "output/state.json",
            "--node-review-definitions",
            str(node_definitions_path),
            "--assessed-at",
            "2026-11-25T09:00:00Z",
        ]
    ) == 0
    state = json.loads(
        (repository / "output/state.json").read_text(encoding="utf-8")
    )
    assert (state["state"], state["continuity"]) == ("current", "exact-match")
    (materials / "state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    build_explorer = deepcopy(explorer)
    build_explorer["architecture"] = {
        "relationships": [
            {
                "review_status": "candidate",
                "source_continuity": {"status": "current"},
            }
        ]
    }
    build_explorer["implementation"] = {"revision": REVISION}
    build_explorer["generation_inputs"] = {}
    dummy_paths = {}
    for name in ("snapshot", "mapping", "candidate", "review-mapping"):
        path = repository / f"{name}.json"
        path.write_text("{}", encoding="utf-8")
        dummy_paths[name] = path
    web_assets = repository / "web"
    web_assets.mkdir()
    captured: dict[str, dict] = {}
    monkeypatch.setattr(
        review_cli,
        "publish_reviewed_explorer",
        lambda *_args, **_kwargs: deepcopy(build_explorer),
    )
    monkeypatch.setattr(
        review_cli,
        "attach_python_source_facts",
        lambda data, _facts: data,
    )
    monkeypatch.setattr(
        review_cli,
        "write_site",
        lambda data, _output, _assets: captured.setdefault("data", deepcopy(data)),
    )
    assert review_cli.main(
        [
            "build",
            "--repository",
            str(repository),
            "--snapshot",
            str(dummy_paths["snapshot"]),
            "--mapping",
            str(dummy_paths["mapping"]),
            "--candidate",
            str(dummy_paths["candidate"]),
            "--review-mapping",
            str(dummy_paths["review-mapping"]),
            "--node-review-definitions",
            str(node_definitions_path),
            "--relationship-review-definitions",
            str(relationship_definitions_path),
            "--relationship-review-material-directory",
            str(materials),
            "--web-assets",
            str(web_assets),
            "--output-directory",
            "output/site",
        ]
    ) == 0
    formal_projection = captured["data"]["review_projection"]["objects"][
        "candidate-collaboration::relationship-under-review"
    ]
    assert (formal_projection["result"], formal_projection["state"]) == (
        "APPROVED",
        "current",
    )

    anchor = policy.anchors[0]
    failure_cases = (
        ((), "no independently trusted approval anchor", "missing-anchor.json"),
        (
            (replace(anchor, comment_id=anchor.comment_id + 1),),
            "no independently trusted approval anchor",
            "unauthorized-anchor.json",
        ),
        (
            (replace(anchor, source_receipt_sha256="0" * 64),),
            "source is untrusted",
            "tampered-anchor.json",
        ),
    )
    for anchors, message, output_name in failure_cases:
        monkeypatch.setattr(
            review_trust,
            "PRODUCTION_RELATIONSHIP_REVIEW_APPROVAL_ANCHORS",
            anchors,
        )
        with pytest.raises(ExplorerInputError, match=message):
            review_cli.main(
                [
                    "update-relationship-review-state",
                    "--repository",
                    str(repository),
                    "--explorer-data",
                    str(explorer_path),
                    "--review-definition",
                    str(relationship_definitions_path),
                    "--review-decision",
                    str(materials / "decision.json"),
                    "--review-source",
                    str(materials / "source.json"),
                    "--review-state",
                    f"output/{output_name}",
                    "--node-review-definitions",
                    str(node_definitions_path),
                    "--assessed-at",
                    "2026-11-25T09:01:00Z",
                ]
            )
        assert not (repository / "output" / output_name).exists()


def test_rejection_basis_cannot_be_tampered_replaced_or_reused_across_objects(
    request: pytest.FixtureRequest,
) -> None:
    short_temp = tempfile.TemporaryDirectory(prefix=".ae77-basis-", dir=Path.cwd())
    request.addfinalizer(short_temp.cleanup)
    root = Path(short_temp.name)
    explorer = _explorer()
    worker_materials = root / "worker-materials"
    _trusted_rejected_node_projection(
        explorer,
        root,
        rejected_claim_kind="RESPONSIBILITY",
        materials_directory=worker_materials,
    )
    worker_decision = json.loads(
        (worker_materials / "decision.json").read_text(encoding="utf-8")
    )
    worker_source = json.loads(
        (worker_materials / "source.json").read_text(encoding="utf-8")
    )
    worker_policy = _NodeReviewTrustPolicy(
        anchors=(
            _NodeReviewApprovalAnchor(
                issue_id=worker_source["issue"]["id"],
                issue_number=worker_source["issue"]["number"],
                comment_id=worker_source["comment"]["id"],
                comment_created_at=worker_source["comment"]["created_at"],
                comment_updated_at=worker_source["comment"]["updated_at"],
                source_receipt_sha256=review_source_sha256(worker_source),
            ),
        )
    )
    worker_definition = validate_node_review_definitions(_node_definition())[0]

    tampered = deepcopy(worker_decision)
    tampered["rejection_basis"]["rejected_claims"][0][
        "explanation"
    ] = "Tampered after the trusted comment was captured."
    with pytest.raises(ExplorerInputError, match="source body mismatch"):
        verify_node_review_v2_integrity(
            worker_definition,
            tampered,
            worker_source,
            trust_policy=worker_policy,
        )

    replaced = deepcopy(worker_decision)
    name_claim = next(
        claim for claim in replaced["claims"] if claim["kind"] == "NAME"
    )
    replaced["rejection_basis"]["rejected_claims"][0][
        "claim_sha256"
    ] = _canonical_sha256(name_claim)
    replaced_source = deepcopy(worker_source)
    replaced_source["comment"]["body"] = render_node_review_decision_body(replaced)
    replaced["source_receipt_sha256"] = review_source_sha256(replaced_source)
    with pytest.raises(ExplorerInputError, match="source is untrusted"):
        verify_node_review_v2_integrity(
            worker_definition,
            replaced,
            replaced_source,
            trust_policy=worker_policy,
        )

    supervisor_root = root / "supervisor"
    supervisor_root.mkdir()
    supervisor_materials = root / "supervisor-materials"
    _trusted_rejected_node_projection(
        explorer,
        supervisor_root,
        rejected_claim_kind="RESPONSIBILITY",
        materials_directory=supervisor_materials,
        node_id="supervisor-boundary",
        comment_id=77006,
    )
    supervisor_decision = json.loads(
        (supervisor_materials / "decision.json").read_text(encoding="utf-8")
    )
    supervisor_source = json.loads(
        (supervisor_materials / "source.json").read_text(encoding="utf-8")
    )
    supervisor_policy = _NodeReviewTrustPolicy(
        anchors=(
            _NodeReviewApprovalAnchor(
                issue_id=supervisor_source["issue"]["id"],
                issue_number=supervisor_source["issue"]["number"],
                comment_id=supervisor_source["comment"]["id"],
                comment_created_at=supervisor_source["comment"]["created_at"],
                comment_updated_at=supervisor_source["comment"]["updated_at"],
                source_receipt_sha256=review_source_sha256(supervisor_source),
            ),
        )
    )
    supervisor_decision["rejection_basis"] = deepcopy(
        worker_decision["rejection_basis"]
    )
    with pytest.raises(ExplorerInputError, match="rejection basis is invalid"):
        verify_node_review_v2_integrity(
            validate_node_review_definitions(
                _node_definition("supervisor-boundary")
            )[0],
            supervisor_decision,
            supervisor_source,
            trust_policy=supervisor_policy,
        )
