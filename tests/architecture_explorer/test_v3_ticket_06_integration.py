from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import subprocess

from booktrans.architecture_explorer.candidate_fragments import apply_candidate_fragments
from booktrans.architecture_explorer.content_delta import inspect_content_delta
from booktrans.architecture_explorer.node_review_definitions import validate_node_review_definitions
from booktrans.architecture_explorer.view_definitions import apply_view_definitions
from booktrans.architecture_explorer.view_projection import apply_view_projection
from tests.architecture_explorer.test_t9_review_packet import _published_explorer


REPOSITORY = Path(__file__).resolve().parents[2]
FRAGMENTS = REPOSITORY / "architecture-explorer/recursive-candidates"
VIEW_DEFINITIONS = REPOSITORY / "architecture-explorer/views/recursive-views.v1.json"
REVIEW_DEFINITIONS = REPOSITORY / "architecture-explorer/review-definitions/node-reviews.v1.json"
FIRST_BATCH_FRAGMENTS = (
    "translation-routing-and-acquisition.v2.json",
    "qwen-provider-adapters.v2.json",
    "quality-semantic-review-and-repair.v2.json",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _combined_projection() -> tuple[dict, dict]:
    fragments = [_json(path) for path in sorted(FRAGMENTS.glob("*.json"))]
    explorer = apply_candidate_fragments(_published_explorer(), fragments)
    explorer = apply_view_definitions(explorer, _json(VIEW_DEFINITIONS))
    return explorer, apply_view_projection(explorer)["view_projection"]


def test_ticket_06_combines_exact_first_batch_objects_without_duplicates() -> None:
    fragments = [_json(FRAGMENTS / name) for name in FIRST_BATCH_FRAGMENTS]
    nodes = [node for fragment in fragments for node in fragment["nodes"]]
    relationships = [item for fragment in fragments for item in fragment["relationships"]]
    families = Counter(item["semantic_family"] for item in relationships)

    assert len(nodes) == len({node["id"] for node in nodes}) == 7
    assert families == {"candidate-decomposition": 7, "candidate-collaboration": 3}
    assert len(relationships) == len({item["id"] for item in relationships}) == 10
    assert all(node["review_status"] == "candidate" for node in nodes)
    assert all(item["review_status"] == "candidate" for item in relationships)

    definitions = _json(VIEW_DEFINITIONS)
    first_batch_scope_ids = {fragment["scope_node_id"] for fragment in fragments}
    first_batch_views = [
        view for view in definitions["views"] if view["scope_node_id"] in first_batch_scope_ids
    ]
    assert len(first_batch_views) == 3
    assert len({view["id"] for view in first_batch_views}) == 3


def test_ticket_06_dynamic_inventory_and_review_boundaries_are_exact() -> None:
    explorer, projection = _combined_projection()
    candidate_nodes = [
        reference
        for reference in projection["node_index"].values()
        if reference["record_source"] != "architecture.modules"
    ]
    families = Counter(
        item["semantic_family"] for item in projection["relationship_index"].values()
    )

    assert len(candidate_nodes) == 27
    assert families["candidate-collaboration"] == 12
    assert families["candidate-decomposition"] == 9
    assert len(projection["view_index"]) == 12

    definitions = validate_node_review_definitions(_json(REVIEW_DEFINITIONS))
    assert {item["object_key"]["node_id"] for item in definitions} == {
        "worker-launch-boundary",
        "translation-routing-and-acquisition",
    }
    first_batch_node_ids = {
        node["id"]
        for name in FIRST_BATCH_FRAGMENTS
        for node in _json(FRAGMENTS / name)["nodes"]
    }
    assert first_batch_node_ids.isdisjoint(
        item["object_key"]["node_id"] for item in definitions
    )
    assert {
        node_id: reference["record_source"]
        for node_id, reference in projection["node_index"].items()
        if node_id in first_batch_node_ids
    } == {
        node_id: "recursive_candidate_fragments.nodes"
        for node_id in first_batch_node_ids
    }


def test_ticket_06_real_git_delta_indexes_both_v2_review_definitions() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    delta = inspect_content_delta(
        REPOSITORY,
        base_revision="6a239f6919cbb791e69e56383b91dbb634aeaf0f",
        head_revision=head,
    )
    assert [
        change.object_id
        for change in delta.changes
        if change.object_type == "node-review-definition"
    ] == [
        "candidate-node::translation-routing-and-acquisition",
        "candidate-node::worker-launch-boundary",
    ]
