"""T5-Lite continuity assessment and explicit review-state updates."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .candidate_scanner import scan_supervisor_worker_candidate
from .generator import ExplorerInputError
from .review_mapping import import_review_mapping
from .review_source import verify_trusted_review_source


REVIEW_STATE_SCHEMA = "architecture-explorer.review-state.v1"
RELATIONSHIP_ID = "relationship:production-control:launches:translation-execution"
_STATE_FIELDS = {
    "schema",
    "relationship_id",
    "review_decision_id",
    "status",
    "continuity_status",
    "assessed_at",
    "reasons",
}
_STATE_STATUSES = {"current-reviewed", "needs-review", "unknown"}
_CONTINUITY_STATUSES = {
    "exact-match",
    "material-change",
    "missing",
    "ambiguous",
    "parse-failure",
}


def _valid_state(state: object) -> bool:
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        return False
    valid = (
        state.get("schema") == REVIEW_STATE_SCHEMA
        and state.get("relationship_id") == RELATIONSHIP_ID
        and isinstance(state.get("review_decision_id"), str)
        and bool(state["review_decision_id"])
        and state.get("status") in _STATE_STATUSES
        and state.get("continuity_status") in _CONTINUITY_STATUSES
        and isinstance(state.get("assessed_at"), str)
        and bool(state["assessed_at"])
        and isinstance(state.get("reasons"), list)
        and all(isinstance(reason, str) and reason for reason in state["reasons"])
    )
    return bool(
        valid
        and (
            state["status"] != "current-reviewed"
            or state["continuity_status"] == "exact-match"
        )
    )


def _trusted_decision(
    candidate: dict[str, Any],
    review_mapping: dict[str, Any],
    review_source: dict[str, Any],
) -> dict[str, Any]:
    decision = review_mapping.get("decision")
    if verify_trusted_review_source(review_source) or not isinstance(decision, dict):
        raise ExplorerInputError("T5-Lite requires the trusted T3 review source")
    expected = import_review_mapping(
        candidate,
        review_source.get("comment"),
        review_source.get("permission"),
    )
    if expected != review_mapping or expected.get("validation", {}).get("status") != "valid":
        raise ExplorerInputError("T5-Lite review mapping does not match the trusted T3 source")
    approved_ids = candidate.get("candidate_relation", {}).get("production_evidence_ids")
    if (
        decision.get("result") != "APPROVED"
        or not isinstance(approved_ids, list)
        or len(approved_ids) != 13
        or decision.get("approved_evidence_ids") != approved_ids
    ):
        raise ExplorerInputError("T5-Lite requires one approved 13-evidence T3 decision")
    return decision


def _assessment(repository: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    try:
        current = scan_supervisor_worker_candidate(repository)
    except (ExplorerInputError, SyntaxError) as error:
        status = "missing" if "missing" in str(error).lower() else "parse-failure"
        return {
            "continuity_status": status,
            "review_status": "needs-review" if status == "missing" else "unknown",
            "reasons": [f"evidence_scan:{error}"],
            "source_snapshot": None,
        }
    approved_ids = candidate["candidate_relation"]["production_evidence_ids"]
    current_ids = current.get("candidate_relation", {}).get("production_evidence_ids", [])
    findings = current.get("required_findings", {})
    if any(item.get("status") == "ambiguous" for item in findings.values() if isinstance(item, dict)):
        status, review_status = "ambiguous", "unknown"
    elif current.get("candidate_relation", {}).get("scan_outcome") != "complete":
        status, review_status = "missing", "needs-review"
    elif current_ids != approved_ids:
        status, review_status = "material-change", "needs-review"
    else:
        status, review_status = "exact-match", "current-reviewed"
    return {
        "continuity_status": status,
        "review_status": review_status,
        "reasons": [] if review_status == "current-reviewed" else [f"evidence_continuity:{status}"],
        "source_snapshot": current["source_snapshot"],
    }


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def evaluate_review_state(
    repository: Path,
    candidate: dict[str, Any],
    review_mapping: dict[str, Any],
    review_source: dict[str, Any],
    review_state_path: Path | None,
) -> dict[str, Any]:
    """Read and evaluate current continuity without changing the review state."""

    decision = _trusted_decision(candidate, review_mapping, review_source)
    live = _assessment(repository, candidate)
    if review_state_path is None or not review_state_path.is_file():
        return {
            **live,
            "review_status": "historical-reviewed",
            "reasons": ["review_state:missing"],
            "state_status": "missing",
        }
    try:
        state = json.loads(review_state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            **live,
            "review_status": "historical-reviewed",
            "reasons": ["review_state:corrupt"],
            "state_status": "corrupt",
        }
    valid = _valid_state(state) and state["review_decision_id"] == decision.get("decision_id")
    if not valid:
        return {
            **live,
            "review_status": "historical-reviewed",
            "reasons": ["review_state:inconsistent"],
            "state_status": "inconsistent",
        }
    if state["status"] in {"needs-review", "unknown"}:
        return {
            **live,
            "review_status": state["status"],
            "reasons": list(state.get("reasons") or ["review_state:invalidated"]),
            "state_status": "valid",
        }
    return {**live, "state_status": "valid"}


def update_review_state(
    repository: Path,
    candidate: dict[str, Any],
    review_mapping: dict[str, Any],
    review_source: dict[str, Any],
    review_state_path: Path,
    *,
    assessed_at: str,
) -> dict[str, Any]:
    """Explicitly assess and atomically update the one Git review-state file."""

    decision = _trusted_decision(candidate, review_mapping, review_source)
    existing: dict[str, Any] | None = None
    if review_state_path.exists():
        try:
            loaded = json.loads(review_state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExplorerInputError("Existing review state is corrupt; refusing overwrite") from error
        if not _valid_state(loaded):
            raise ExplorerInputError("Existing review state is inconsistent; refusing overwrite")
        existing = loaded
    assessment = _assessment(repository, candidate)
    if (
        existing is not None
        and existing["review_decision_id"] == decision["decision_id"]
        and existing["status"] in {"needs-review", "unknown"}
    ):
        previous_status = existing["status"]
        if previous_status == "unknown" or assessment["review_status"] == "unknown":
            assessment["review_status"] = "unknown"
        else:
            assessment["review_status"] = "needs-review"
        if assessment["continuity_status"] == "exact-match":
            assessment["reasons"] = list(existing.get("reasons") or ["review_state:invalidated"])
    state = {
        "schema": REVIEW_STATE_SCHEMA,
        "relationship_id": RELATIONSHIP_ID,
        "review_decision_id": decision["decision_id"],
        "status": assessment["review_status"],
        "continuity_status": assessment["continuity_status"],
        "assessed_at": assessed_at,
        "reasons": assessment["reasons"],
    }
    _atomic_write(review_state_path, state)
    return state


__all__ = [
    "RELATIONSHIP_ID",
    "REVIEW_STATE_SCHEMA",
    "evaluate_review_state",
    "update_review_state",
]
