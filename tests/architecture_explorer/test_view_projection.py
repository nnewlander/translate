from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

import pytest

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.generator import ExplorerInputError
from booktrans.architecture_explorer.navigation import apply_navigation
from booktrans.architecture_explorer.review_mapping import publish_reviewed_explorer
from booktrans.architecture_explorer.source_scanner import (
    attach_python_source_facts,
    scan_python_source_facts,
)
from booktrans.architecture_explorer.view_projection import apply_view_projection


REPOSITORY = Path(__file__).resolve().parents[2]


def _json(relative_path: str) -> dict:
    return json.loads((REPOSITORY / relative_path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _current_explorer_baseline() -> dict:
    reviewed = publish_reviewed_explorer(
        REPOSITORY,
        REPOSITORY / "output/architecture-explorer/t1-delivery-4be1c19/snapshot.json",
        REPOSITORY / "architecture-explorer/modules.json",
        _json("architecture-explorer/review-inputs/supervisor-worker-857a4b8.json"),
        _json("architecture-explorer/reviews/supervisor-worker.v1.json"),
        _json("architecture-explorer/reviews/supervisor-worker.source.v1.json"),
        review_state_path=(
            REPOSITORY / "output/architecture-explorer/t5-lite/review-state.v1.json"
        ),
    )
    with_facts = attach_python_source_facts(
        reviewed,
        scan_python_source_facts(REPOSITORY),
    )
    navigable = apply_navigation(
        with_facts,
        _json("architecture-explorer/navigation.v1.json"),
    )
    fragments = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (REPOSITORY / "architecture-explorer/candidates").glob("*.json")
        )
    ]
    return apply_candidate_fragments(navigable, fragments)


def _current_explorer() -> dict:
    return deepcopy(_current_explorer_baseline())


def test_current_explorer_reuses_an_isolated_read_only_baseline() -> None:
    first = _current_explorer()
    first["architecture"]["modules"].clear()
    second = _current_explorer()

    assert second["architecture"]["modules"]
    assert _current_explorer_baseline.cache_info().misses == 1


def test_current_eight_views_are_projected_without_changing_authorities() -> None:
    explorer = _current_explorer()
    authoritative = deepcopy(
        {
            "architecture": explorer["architecture"],
            "navigation": explorer["navigation"],
            "source_facts": explorer["source_facts"],
        }
    )

    projected = apply_view_projection(explorer)

    assert {
        "architecture": projected["architecture"],
        "navigation": projected["navigation"],
        "source_facts": projected["source_facts"],
    } == authoritative
    projection = projected["view_projection"]
    assert projection["schema"] == "architecture-explorer.view-projection.v2"
    assert projection["root_view_id"] == "top-level"
    assert list(projection["view_index"]) == [
        "top-level",
        "document-understanding",
        "scope-and-request",
        "translation-execution",
        "quality-and-repair",
        "production-evidence",
        "production-control",
        "candidate-and-release",
    ]
    assert len(projection["node_index"]) == 25
    assert len(projection["relationship_index"]) == 9

    root = projection["view_index"]["top-level"]
    assert [item["node_id"] for item in root["appearances"]] == [
        module["id"] for module in authoritative["architecture"]["modules"]
    ]
    assert all(
        item["primary_action"] == {
            "kind": "open-view",
            "target_view_id": item["node_id"],
            "transition_kind": "decomposition",
            "destination_label": projection["view_index"][item["node_id"]][
                "display_name"
            ],
        }
        for item in root["appearances"]
    )

    for drilldown in authoritative["navigation"]["drilldowns"]:
        view = projection["view_index"][drilldown["parent_module_id"]]
        assert view["scope_node_id"] == drilldown["parent_module_id"]
        assert [item["node_id"] for item in view["appearances"]] == [
            child["id"] for child in drilldown["children"]
        ]
        assert all("primary_action" not in item for item in view["appearances"])
        assert view["relationship_ids"] == [
            item["id"] for item in drilldown["collaborations"]
        ]


def test_projection_indexes_contain_complete_display_records_without_physical_locations() -> None:
    projected = apply_view_projection(_current_explorer())
    projection = projected["view_projection"]

    assert projection["node_index"]["production-control"]["id"] == "production-control"
    worker = projection["node_index"]["worker-launch-boundary"]
    assert worker["name"] == "Worker 启动边界"
    assert worker["object_key"] == {
        "kind": "candidate-node",
        "object_id": "worker-launch-boundary",
    }
    assert worker["review"] == {
        "result": "Pending",
        "state": "not-reviewed",
        "approved_scope": [],
        "reasons": ["trusted-review-decision:missing"],
    }
    formal_id = "relationship:production-control:launches:translation-execution"
    formal = projection["relationship_index"][formal_id]
    assert formal["semantic"] == "formal"
    assert formal["provenance"] == {"kind": "formal-relationship"}
    assert formal["source_node_id"] == "production-control"
    assert formal["target_node_id"] == "translation-execution"
    assert formal["review"] == {
        "result": "APPROVED",
        "state": "current",
        "approved_scope": [
            {"legacy_review_scope": "current-source-snapshot"}
        ],
        "reasons": [],
    }
    candidate_id = "candidate-collaboration:supervisor-to-worker-wrapper"
    candidate = projection["relationship_index"][candidate_id]
    assert candidate["semantic"] == "collaboration"
    assert candidate["provenance"] == {"kind": "candidate-fragment"}
    assert candidate["object_key"] == {
        "kind": "candidate-collaboration",
        "object_id": candidate_id,
    }
    assert candidate["review"] == {
        "result": "Pending",
        "state": "not-reviewed",
        "approved_scope": [],
        "reasons": ["trusted-review-decision:missing"],
    }
    assert "semantic_family" not in candidate
    assert "review_status" not in candidate
    assert "record_source" not in json.dumps(projection)
    assert "record_index" not in json.dumps(projection)


def test_relationship_semantic_and_explicit_review_projection_are_orthogonal() -> None:
    explorer = apply_candidate_fragments(
        _current_explorer(),
        [_json("architecture-explorer/recursive-candidates/worker-launch-boundary.v2.json")],
    )
    first_collaboration = "candidate-collaboration:supervisor-to-worker-wrapper"
    second_collaboration = (
        "candidate-collaboration:worker-wrapper-to-production-entry-guard"
    )
    decomposition = (
        "candidate-decomposition:worker-launch-boundary-to-worker-production-entry-guard"
    )
    approved_scope = {
        "object_key": {
            "kind": "candidate-collaboration",
            "object_id": first_collaboration,
        },
        "reviewed_source_snapshot": {
            "revision": "09aaf4a033123c7eca8d280fa8982e5b0e711b86",
            "worktree_state": "clean",
        },
        "claims_sha256": "1" * 64,
        "evidence_ids": ["evidence:approved-collaboration"],
        "decision_id": "github-comment:7601",
    }
    explorer["review_projection"] = {
        "schema": "architecture-explorer.review-projection.v1",
        "objects": {
            f"candidate-collaboration::{first_collaboration}": {
                "object_key": {
                    "kind": "candidate-collaboration",
                    "object_id": first_collaboration,
                },
                "semantics": {
                    "family": "collaboration",
                    "direction": {
                        "source_node_id": "worker-launch-boundary",
                        "target_node_id": "deterministic-worker-wrapper",
                    },
                },
                "result": "APPROVED",
                "state": "current",
                "approved_scope": [approved_scope],
                "reasons": [],
            },
            f"candidate-collaboration::{second_collaboration}": {
                "object_key": {
                    "kind": "candidate-collaboration",
                    "object_id": second_collaboration,
                },
                "semantics": {
                    "family": "collaboration",
                    "direction": {
                        "source_node_id": "deterministic-worker-wrapper",
                        "target_node_id": "worker-production-entry-guard",
                    },
                },
                "result": "REJECTED",
                "state": "needs-review",
                "approved_scope": [],
                "reasons": ["candidate-evidence:material-change"],
            },
            f"candidate-decomposition::{decomposition}": {
                "object_key": {
                    "kind": "candidate-decomposition",
                    "object_id": decomposition,
                },
                "semantics": {
                    "family": "decomposition",
                    "direction": {
                        "source_node_id": "worker-launch-boundary",
                        "target_node_id": "worker-production-entry-guard",
                    },
                },
                "result": "APPROVED",
                "state": "current",
                "approved_scope": [{
                    **approved_scope,
                    "object_key": {
                        "kind": "candidate-decomposition",
                        "object_id": decomposition,
                    },
                }],
                "reasons": [],
            },
        },
    }

    relationships = apply_view_projection(explorer)["view_projection"][
        "relationship_index"
    ]

    assert relationships[first_collaboration]["semantic"] == "collaboration"
    assert relationships[first_collaboration]["review"]["state"] == "current"
    assert relationships[first_collaboration]["review"]["approved_scope"] == [approved_scope]
    assert relationships[second_collaboration]["semantic"] == "collaboration"
    assert relationships[second_collaboration]["review"]["state"] == "needs-review"
    assert relationships[decomposition]["semantic"] == "decomposition"
    assert relationships[decomposition]["review"]["result"] == "APPROVED"
    assert relationships[decomposition]["review"]["state"] == "current"


def test_legacy_node_review_projection_adapts_node_id_without_rewriting_authority() -> None:
    explorer = _current_explorer()
    explorer["node_review_projection"] = {
        "schema": "architecture-explorer.node-review-projection.v1",
        "objects": {
            "candidate-node::worker-launch-boundary": {
                "object_key": {
                    "kind": "candidate-node",
                    "node_id": "worker-launch-boundary",
                },
                "decision": {
                    "result": "APPROVED",
                    "claims": [{"kind": "RESPONSIBILITY"}],
                },
                "state": {"status": "current"},
                "projection": "Current · Approved",
            }
        },
    }
    authoritative = deepcopy(explorer["node_review_projection"])

    projected = apply_view_projection(explorer)

    assert projected["node_review_projection"] == authoritative
    worker = projected["view_projection"]["node_index"]["worker-launch-boundary"]
    assert worker["object_key"] == {
        "kind": "candidate-node",
        "object_id": "worker-launch-boundary",
    }
    assert worker["review"] == {
        "result": "APPROVED",
        "state": "current",
        "approved_scope": [{"legacy_claim_kind": "RESPONSIBILITY"}],
        "reasons": [],
    }


def test_review_projection_rejects_an_unknown_canonical_object_key() -> None:
    explorer = _current_explorer()
    explorer["review_projection"] = {
        "schema": "architecture-explorer.review-projection.v1",
        "objects": {
            "candidate-collaboration::missing": {
                "object_key": {
                    "kind": "candidate-collaboration",
                    "object_id": "missing",
                },
                "semantics": {
                    "family": "collaboration",
                    "direction": {
                        "source_node_id": "missing-source",
                        "target_node_id": "missing-target",
                    },
                },
                "result": "Pending",
                "state": "not-reviewed",
                "approved_scope": [],
                "reasons": [],
            }
        },
    }

    with pytest.raises(ExplorerInputError, match="unknown object"):
        apply_view_projection(explorer)


def test_projection_contains_complete_source_trail_records() -> None:
    projection = apply_view_projection(_current_explorer())["view_projection"]
    evidence_id, record = next(
        (evidence_id, record)
        for evidence_id, record in projection["evidence_index"].items()
        if record.get("source_trail_kind") == "definition"
    )

    assert record["definition"]["evidence"]["evidence_id"] == evidence_id
    assert record["file_path"] == record["definition"]["evidence"]["path"]
    assert projection["source_file_index"][record["file_path"]]["path"] == record[
        "file_path"
    ]
    assert "record_source" not in json.dumps(record)
    assert "record_index" not in json.dumps(record)


def test_projection_exposes_one_validated_source_catalog_including_call_facts() -> None:
    projection = apply_view_projection(_current_explorer())["view_projection"]
    call_evidence_id, call_record = next(
        (evidence_id, record)
        for evidence_id, record in projection["evidence_index"].items()
        if "call" in record
    )
    source_file = projection["source_file_index"][call_record["file_path"]]

    assert projection["source_catalog"]["schema"] == (
        "architecture-explorer.source-catalog.v1"
    )
    assert projection["source_catalog"]["file_paths"] == sorted(
        projection["source_file_index"]
    )
    assert call_evidence_id in source_file["call_evidence_ids"]
    assert "calls" not in source_file
    assert "definitions" not in source_file
    assert "direct_imports" not in source_file
    app = (REPOSITORY / "architecture-explorer/web/app.js").read_text(
        encoding="utf-8"
    )
    assert "data.source_facts" not in app


def test_projection_rejects_conflicting_records_for_one_source_file_identity() -> None:
    explorer = _current_explorer()
    source_facts = explorer["source_facts"]
    conflicting = deepcopy(source_facts["python_call_facts"]["files"][0])
    conflicting["calls"] = []
    conflicting["managed_content_sha256"] = "conflict"
    source_facts["python_call_facts"]["files"].append(conflicting)

    with pytest.raises(ExplorerInputError, match="duplicate Source file identity"):
        apply_view_projection(explorer)


def test_projection_allows_shared_evidence_reference_without_copying_identity() -> None:
    explorer = _current_explorer()
    children = explorer["navigation"]["drilldowns"][0]["children"]
    shared_id = children[0]["source_assignment"]["evidence_ids"][0]
    target = next(
        child
        for child in children[1:]
        if shared_id not in child["source_assignment"]["evidence_ids"]
    )
    target["source_assignment"]["evidence_ids"].append(shared_id)

    projection = apply_view_projection(explorer)["view_projection"]

    assert shared_id in projection["node_index"][children[0]["id"]][
        "source_assignment"
    ]["evidence_ids"]
    assert shared_id in projection["node_index"][target["id"]]["source_assignment"][
        "evidence_ids"
    ]
    assert list(projection["evidence_index"]).count(shared_id) == 1


def test_projection_accepts_v3_candidates_without_evidence_references() -> None:
    explorer = _current_explorer()
    for drilldown in explorer["navigation"]["drilldowns"]:
        for child in drilldown["children"]:
            child["source_assignment"].pop("evidence_ids")
        for relationship in drilldown.get("collaborations", []):
            relationship.pop("evidence_ids", None)

    projection = apply_view_projection(explorer)["view_projection"]

    candidate = projection["node_index"][
        explorer["navigation"]["drilldowns"][0]["children"][0]["id"]
    ]
    assert candidate["review"] == {
        "result": "Pending",
        "state": "not-reviewed",
        "approved_scope": [],
        "reasons": ["trusted-review-decision:missing"],
    }
    assert projection["evidence_index"]


def test_projection_rejects_missing_or_repeated_evidence_references() -> None:
    missing = _current_explorer()
    missing["navigation"]["drilldowns"][0]["children"][0]["source_assignment"][
        "evidence_ids"
    ] = ["evidence:missing"]
    with pytest.raises(ExplorerInputError, match="unknown Evidence reference"):
        apply_view_projection(missing)

    repeated = _current_explorer()
    evidence_ids = repeated["navigation"]["drilldowns"][0]["children"][0][
        "source_assignment"
    ]["evidence_ids"]
    evidence_ids.append(evidence_ids[0])
    with pytest.raises(ExplorerInputError, match="repeats Evidence reference"):
        apply_view_projection(repeated)
