"""Persistent continuity state for the one T8 node-review object."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .continuity import _atomic_write
from .generator import ExplorerInputError
from .node_review import (
    CANDIDATE_EVIDENCE_FINGERPRINTS,
    EXPECTED_CANDIDATE_FACT,
    FORMAL_EVIDENCE_IDS,
    NODE_REVIEW_OBJECT_KEY,
    T3_DECISION_ID,
    T3_REVIEWED_SNAPSHOT,
    VERSIONED_INPUTS,
    _NodeReviewTrustPolicy,
    _PRODUCTION_NODE_REVIEW_TRUST_POLICY,
    _candidate_fact,
    _relationship,
    _target_node,
    _validate_decision_integrity,
)


NODE_REVIEW_STATE_SCHEMA = "architecture-explorer.node-review-state.v1"
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
_REASONS = {
    "candidate-fact:missing",
    "candidate-fact:material-change",
    "formal-dependency:missing",
    "formal-dependency:material-change",
    "candidate-evidence:missing",
    "candidate-evidence:material-change",
    "candidate-evidence:ambiguous",
    "source-scan:parse-failure",
    *(f"versioned-input:{field}" for field in VERSIONED_INPUTS if field != "candidate_evidence_fingerprint_sha256"),
}


def _valid_state(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        return False
    snapshot = value.get("evaluated_source_snapshot")
    locations = value.get("current_evidence_locations")
    valid = (
        value.get("schema") == NODE_REVIEW_STATE_SCHEMA
        and value.get("object_key") == NODE_REVIEW_OBJECT_KEY
        and isinstance(value.get("decision_id"), str)
        and bool(value["decision_id"])
        and value.get("state") in {"current", "needs-review", "unknown"}
        and value.get("continuity")
        in {"exact-match", "material-change", "missing", "ambiguous", "parse-failure"}
        and isinstance(snapshot, dict)
        and set(snapshot) == {"revision", "worktree_state", "source_facts_sha256"}
        and all(isinstance(item, str) and item for item in snapshot.values())
        and snapshot["worktree_state"] in {"clean", "dirty"}
        and isinstance(value.get("assessed_at"), str)
        and bool(value["assessed_at"])
        and isinstance(value.get("reasons"), list)
        and all(reason in _REASONS for reason in value["reasons"])
        and isinstance(locations, list)
        and all(
            isinstance(location, dict)
            and set(location) == {"evidence_id", "path", "line_start", "line_end"}
            and isinstance(location["evidence_id"], str)
            and isinstance(location["path"], str)
            and isinstance(location["line_start"], int)
            and isinstance(location["line_end"], int)
            for location in locations
        )
    )
    if not valid:
        return False
    if value["state"] == "current":
        return (
            value["continuity"] == "exact-match"
            and value["reasons"] == []
            and len(locations) == 3
        )
    if value["state"] == "needs-review":
        return value["continuity"] in {"material-change", "missing"} and bool(
            value["reasons"]
        )
    return value["continuity"] in {"ambiguous", "parse-failure"} and bool(
        value["reasons"]
    )


def _candidate_evidence_records(explorer: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_ids = _target_node(explorer)["source_assignment"]["evidence_ids"]
    matches: dict[str, list[dict[str, Any]]] = {evidence_id: [] for evidence_id in evidence_ids}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            evidence = value.get("evidence")
            evidence_id = evidence.get("evidence_id") if isinstance(evidence, dict) else None
            if evidence_id in matches:
                matches[evidence_id].append(evidence)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(explorer.get("source_facts", {}))
    return [matches[evidence_id][0] for evidence_id in evidence_ids]


def _all_evidence(explorer: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            evidence = value.get("evidence")
            if isinstance(evidence, dict):
                found.append(evidence)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(explorer.get("source_facts", {}))
    return found


def _assessment(explorer: dict[str, Any]) -> dict[str, Any]:
    scan_error = explorer.get("_node_review_scan_error")
    if scan_error:
        if scan_error == "missing":
            return _outcome("needs-review", "missing", "candidate-evidence:missing")
        return _outcome("unknown", "parse-failure", "source-scan:parse-failure")
    parse_failed = any(
        item.get("parse_status") == "parse-error"
        for item in explorer.get("source_facts", {}).get("powershell_scripts", [])
        if isinstance(item, dict)
    )
    if parse_failed:
        return _outcome("unknown", "parse-failure", "source-scan:parse-failure")
    try:
        target = _target_node(explorer)
    except ExplorerInputError:
        return _outcome("needs-review", "missing", "candidate-fact:missing")
    if _candidate_fact(target) != EXPECTED_CANDIDATE_FACT:
        return _outcome("needs-review", "material-change", "candidate-fact:material-change")

    try:
        relationship = _relationship(explorer)
    except ExplorerInputError:
        return _outcome("needs-review", "missing", "formal-dependency:missing")
    decision = relationship.get("review_decision", {})
    formal_identity = (
        relationship.get("id")
        == "relationship:production-control:launches:translation-execution"
        and decision.get("decision_id") == T3_DECISION_ID
        and decision.get("result") == "APPROVED"
        and decision.get("reviewed_source_snapshot", {}).get("revision")
        == T3_REVIEWED_SNAPSHOT
        and relationship.get("evidence_ids") == list(FORMAL_EVIDENCE_IDS)
    )
    if not formal_identity:
        return _outcome("needs-review", "material-change", "formal-dependency:material-change")
    if (
        relationship.get("review_status") != "current-reviewed"
        or relationship.get("source_continuity", {}).get("status") != "exact-match"
    ):
        return _outcome("needs-review", "missing", "formal-dependency:missing")

    generation = explorer.get("generation_inputs", {})
    fragments = generation.get("candidate_fragments", [])
    production_fragment = next(
        (
            item
            for item in fragments
            if isinstance(item, dict) and item.get("path") == "production-control.v1.json"
        ),
        {},
    )
    current_versioned = {
        "candidate_fragment_sha256": production_fragment.get("sha256"),
        "navigation_sha256": generation.get("navigation", {}).get("sha256"),
        "t3_review_map_sha256": generation.get("review_mapping", {}).get("sha256"),
        "t3_review_source_sha256": generation.get("review_source", {}).get("sha256"),
        "candidate_scanner_version": explorer.get("source_facts", {}).get("scanner_version"),
        "relationship_candidate_scanner_version": decision.get(
            "reviewed_source_snapshot", {}
        ).get("scanner_version"),
    }
    for field, value in current_versioned.items():
        if value != VERSIONED_INPUTS[field]:
            return _outcome("needs-review", "material-change", f"versioned-input:{field}")

    evidence = _all_evidence(explorer)
    current_locations = []
    for fingerprint in CANDIDATE_EVIDENCE_FINGERPRINTS:
        evidence_id = fingerprint["evidence_id"]
        matches = [item for item in evidence if item.get("evidence_id") == evidence_id]
        if len(matches) > 1:
            return _outcome("unknown", "ambiguous", "candidate-evidence:ambiguous")
        if not matches:
            semantic_matches = [
                item
                for item in evidence
                if item.get("path") == fingerprint["path"]
                and item.get("symbol") == fingerprint["symbol"]
                and item.get("structural_anchor") == fingerprint["structural_anchor"]
                and item.get("evidence_kind") == fingerprint["evidence_kind"]
            ]
            if len(semantic_matches) > 1:
                return _outcome("unknown", "ambiguous", "candidate-evidence:ambiguous")
            if semantic_matches:
                return _outcome(
                    "needs-review", "material-change", "candidate-evidence:material-change"
                )
            return _outcome("needs-review", "missing", "candidate-evidence:missing")
        record = matches[0]
        for field, expected in fingerprint.items():
            if record.get(field) != expected:
                return _outcome(
                    "needs-review", "material-change", "candidate-evidence:material-change"
                )
        location = record.get("current_location", {})
        if not isinstance(location.get("line_start"), int) or not isinstance(
            location.get("line_end"), int
        ):
            return _outcome("unknown", "parse-failure", "source-scan:parse-failure")
        current_locations.append(
            {
                "evidence_id": evidence_id,
                "path": record["path"],
                "line_start": location["line_start"],
                "line_end": location["line_end"],
            }
        )
    return {
        "state": "current",
        "continuity": "exact-match",
        "reasons": [],
        "current_evidence_locations": current_locations,
    }


def _outcome(state: str, continuity: str, reason: str) -> dict[str, Any]:
    return {
        "state": state,
        "continuity": continuity,
        "reasons": [reason],
        "current_evidence_locations": [],
    }


def _update_node_review_state(
    explorer: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    state_path: Path,
    *,
    assessed_at: str,
    trust_policy: _NodeReviewTrustPolicy,
) -> dict[str, Any]:
    """Explicitly assess and atomically persist the T8 node-review state."""

    _validate_decision_integrity(decision, review_source, trust_policy)
    existing: dict[str, Any] | None = None
    if state_path.exists():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExplorerInputError(
                "Existing Node Review State is corrupt; refusing overwrite"
            ) from error
        if not _valid_state(loaded):
            raise ExplorerInputError(
                "Existing Node Review State is inconsistent; refusing overwrite"
            )
        existing = loaded
    source_facts = explorer["source_facts"]
    source_snapshot = source_facts["source_snapshot"]
    assessment = _assessment(explorer)
    if (
        existing is not None
        and existing["decision_id"] == decision["decision_id"]
        and existing["state"] in {"needs-review", "unknown"}
    ):
        if assessment["state"] == "current" or existing["state"] == "unknown":
            assessment = {
                "state": existing["state"],
                "continuity": existing["continuity"],
                "reasons": list(existing["reasons"]),
                "current_evidence_locations": assessment["current_evidence_locations"],
            }
    state = {
        "schema": NODE_REVIEW_STATE_SCHEMA,
        "object_key": deepcopy(NODE_REVIEW_OBJECT_KEY),
        "decision_id": decision["decision_id"],
        "state": assessment["state"],
        "continuity": assessment["continuity"],
        "evaluated_source_snapshot": {
            "revision": source_snapshot["revision"],
            "worktree_state": source_snapshot["worktree"]["state"],
            "source_facts_sha256": source_facts["deterministic_facts_sha256"],
        },
        "assessed_at": assessed_at,
        "reasons": assessment["reasons"],
        "current_evidence_locations": assessment["current_evidence_locations"],
    }
    _atomic_write(state_path, state)
    return state


def _evaluate_node_review_state(
    decision: dict[str, Any],
    state_path: Path | None,
) -> dict[str, Any]:
    """Read a node-review state without changing it; fail closed to Unknown."""

    if state_path is None or not state_path.is_file():
        return _unknown_state(decision, "node-review-state:missing")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _unknown_state(decision, "node-review-state:corrupt")
    if not _valid_state(state) or state["decision_id"] != decision.get("decision_id"):
        return _unknown_state(decision, "node-review-state:inconsistent")
    return {
        "status": state["state"],
        "continuity": state["continuity"],
        "decision_id": state["decision_id"],
        "reasons": deepcopy(state["reasons"]),
        "evaluated_source_snapshot": deepcopy(state["evaluated_source_snapshot"]),
        "current_evidence_locations": deepcopy(state["current_evidence_locations"]),
    }


def _unknown_state(decision: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "continuity": "missing",
        "decision_id": decision.get("decision_id"),
        "reasons": [reason],
        "evaluated_source_snapshot": None,
        "current_evidence_locations": [],
    }


def update_node_review_state(
    explorer: dict[str, Any],
    decision: dict[str, Any],
    review_source: dict[str, Any],
    state_path: Path,
    *,
    assessed_at: str,
) -> dict[str, Any]:
    """Update through the production host-owned trust policy."""

    return _update_node_review_state(
        explorer,
        decision,
        review_source,
        state_path,
        assessed_at=assessed_at,
        trust_policy=_PRODUCTION_NODE_REVIEW_TRUST_POLICY,
    )


def evaluate_node_review_state(
    decision: dict[str, Any],
    state_path: Path | None,
) -> dict[str, Any]:
    """Read the persisted T8 state and fail closed when it is unusable."""

    return _evaluate_node_review_state(decision, state_path)


__all__ = [
    "NODE_REVIEW_STATE_SCHEMA",
    "evaluate_node_review_state",
    "update_node_review_state",
]
