from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
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


def _current_explorer() -> dict:
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
    assert projection["schema"] == "architecture-explorer.view-projection.v1"
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


def test_projection_indexes_reference_authoritative_records_without_copying_them() -> None:
    projected = apply_view_projection(_current_explorer())
    projection = projected["view_projection"]

    assert projection["node_index"]["production-control"] == {
        "record_source": "architecture.modules",
        "record_index": 5,
    }
    assert projection["node_index"]["worker-launch-boundary"] == {
        "record_source": "navigation.drilldowns.children",
        "owner_view_id": "production-control",
        "record_index": 1,
    }
    formal_id = "relationship:production-control:launches:translation-execution"
    assert projection["relationship_index"][formal_id] == {
        "record_source": "architecture.relationships",
        "record_index": 0,
        "semantic_family": "formal-relationship",
    }
    candidate_id = "candidate-collaboration:supervisor-to-worker-wrapper"
    assert projection["relationship_index"][candidate_id] == {
        "record_source": "navigation.drilldowns.collaborations",
        "owner_view_id": "production-control",
        "record_index": 1,
        "semantic_family": "candidate-collaboration",
    }
