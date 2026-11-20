from __future__ import annotations

from copy import deepcopy
from typing import Any

from .generator import ExplorerInputError


NODE_REVIEW_PROJECTION_SCHEMA = "architecture-explorer.node-review-projection.v1"


def attach_node_review_projection(
    explorer: dict[str, Any], reviews_by_key: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Join derived review display data without changing authoritative Node facts."""

    if "node_review_projection" in explorer:
        raise ExplorerInputError("Node Review Projection is already attached")
    for key, review in reviews_by_key.items():
        object_key = review.get("object_key", {})
        expected = f"{object_key.get('kind')}::{object_key.get('node_id')}"
        if key != expected:
            raise ExplorerInputError(f"Node Review Projection object mismatch: {key}")
    result = deepcopy(explorer)
    result["node_review_projection"] = {
        "schema": NODE_REVIEW_PROJECTION_SCHEMA,
        "objects": {
            key: deepcopy(reviews_by_key[key]) for key in sorted(reviews_by_key)
        },
    }
    return result


def projected_node_review(
    explorer: dict[str, Any], object_key: dict[str, Any]
) -> dict[str, Any] | None:
    """Read one derived review by its stable object key."""

    key = f"{object_key.get('kind')}::{object_key.get('node_id')}"
    projection = explorer.get("node_review_projection", {})
    if projection.get("schema") != NODE_REVIEW_PROJECTION_SCHEMA:
        return None
    review = projection.get("objects", {}).get(key)
    return deepcopy(review) if isinstance(review, dict) else None


__all__ = [
    "NODE_REVIEW_PROJECTION_SCHEMA",
    "attach_node_review_projection",
    "projected_node_review",
]
