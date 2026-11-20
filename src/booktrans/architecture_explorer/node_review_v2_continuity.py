from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .continuity import _atomic_write
from .generator import ExplorerInputError
from .node_review import _NodeReviewTrustPolicy
from .node_review_definitions import (
    _current_v2_continuity_evidence,
    _evaluated_source_snapshot,
    verify_node_review_v2_integrity,
    verify_node_review_v2_materials,
)


NODE_REVIEW_STATE_V2_SCHEMA = "architecture-explorer.node-review-state.v2"
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


def _valid_state(state: object, decision: dict[str, Any]) -> bool:
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        return False
    snapshot = state.get("evaluated_source_snapshot")
    locations = state.get("current_evidence_locations")
    return (
        state.get("schema") == NODE_REVIEW_STATE_V2_SCHEMA
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


def evaluate_node_review_v2_state(
    decision: dict[str, Any], state_path: Path | None
) -> dict[str, Any]:
    if state_path is None or not state_path.is_file():
        return {
            "object_key": deepcopy(decision.get("object_key")),
            "status": "unknown",
            "continuity": "missing",
            "decision_id": decision.get("decision_id"),
            "reasons": ["node-review-state:missing"],
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
            "reasons": ["node-review-state:inconsistent"],
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


def update_node_review_v2_state(
    explorer: dict[str, Any],
    definition: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    state_path: Path,
    *,
    assessed_at: str,
    trust_policy: _NodeReviewTrustPolicy,
) -> dict[str, Any]:
    verify_node_review_v2_integrity(
        definition, decision, review_source, trust_policy=trust_policy
    )
    existing: dict[str, Any] | None = None
    if state_path.exists():
        try:
            existing = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExplorerInputError(
                "Existing Node Review State v2 is corrupt; refusing overwrite"
            ) from error
        if not _valid_state(existing, decision):
            raise ExplorerInputError(
                "Existing Node Review State v2 is inconsistent; refusing overwrite"
            )
    scan_error = explorer.get("_node_review_scan_error")
    if scan_error:
        if scan_error == "missing":
            status = "needs-review"
            continuity = "missing"
            reasons = ["source-scan:missing"]
        else:
            status = "unknown"
            continuity = "parse-failure"
            reasons = ["source-scan:parse-failure"]
        locations = []
    else:
        try:
            verify_node_review_v2_materials(
                explorer,
                definition,
                decision,
                review_source,
                trust_policy=trust_policy,
            )
            status = "current"
            continuity = "exact-match"
            reasons = []
            _, locations = _current_v2_continuity_evidence(explorer, definition)
        except ExplorerInputError:
            status = "needs-review"
            continuity = "material-change"
            reasons = ["node-review-material:material-change"]
            locations = []
    if (
        existing is not None
        and existing["decision_id"] == decision["decision_id"]
        and existing["state"] in {"needs-review", "unknown"}
        and (status == "current" or existing["state"] == "unknown")
    ):
        status = existing["state"]
        continuity = existing["continuity"]
        reasons = list(existing["reasons"])
    state = {
        "schema": NODE_REVIEW_STATE_V2_SCHEMA,
        "object_key": deepcopy(decision["object_key"]),
        "decision_id": decision["decision_id"],
        "state": status,
        "continuity": continuity,
        "evaluated_source_snapshot": _evaluated_source_snapshot(explorer),
        "assessed_at": assessed_at,
        "reasons": reasons,
        "current_evidence_locations": locations,
    }
    _atomic_write(state_path, state)
    return state


__all__ = [
    "NODE_REVIEW_STATE_V2_SCHEMA",
    "evaluate_node_review_v2_state",
    "update_node_review_v2_state",
]
