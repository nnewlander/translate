from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from .generator import ExplorerInputError, build_explorer
from .presentation_zh import build_relationship_presentation_zh


REVIEW_MAP_SCHEMA = "architecture-explorer.review-map.v1"
DATA_CONTRACT_VERSION = "architecture-explorer.t3-t5.v1"
REVIEW_IMPORTER_VERSION = "architecture-explorer-t3/1.0"
REVIEW_MARKER = "ARCHITECTURE_REVIEW_DECISION"
REVIEW_STATUSES = {"candidate", "current-reviewed", "needs-review", "unknown"}
CONTINUITY_STATUSES = {
    "exact-match", "equivalent-relocation", "material-change", "missing", "ambiguous", "parse-failure"
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EVIDENCE_ID = re.compile(r"evidence:sha256:[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExplorerInputError(f"Review decision needs a non-empty {key}")
    return value.strip()


def _parse_list(lines: list[str], start: int) -> tuple[list[str], int]:
    values: list[str] = []
    index = start
    while index < len(lines):
        match = re.fullmatch(r"\s*-\s+(.+?)\s*", lines[index])
        if not match:
            break
        values.append(match.group(1))
        index += 1
    return values, index


def parse_review_decision(body: str) -> dict[str, Any]:
    """Parse the deliberately narrow Issue #3 decision format."""

    if not isinstance(body, str):
        raise ExplorerInputError("GitHub review body must be text")
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != REVIEW_MARKER:
        raise ExplorerInputError("GitHub comment is not an architecture review decision")
    fields: dict[str, Any] = {}
    index = 1
    list_fields = {"approved_evidence_ids", "retained_unknowns"}
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip():
            continue
        logical_line = line[2:] if line.startswith("  ") else line
        match = re.fullmatch(r"([a-z0-9_]+):(?:\s*(.*))?", logical_line)
        if not match:
            if fields.get("review_notes") is not None:
                fields["review_notes"] += "\n" + line
                continue
            raise ExplorerInputError(f"Malformed review decision line: {line}")
        key, value = match.group(1), (match.group(2) or "").strip()
        if key in fields:
            raise ExplorerInputError(f"Duplicate review decision field: {key}")
        if key in list_fields:
            if value:
                raise ExplorerInputError(f"Review decision list {key} must use dash items")
            values, index = _parse_list(lines, index)
            fields[key] = values
        elif key == "review_notes" and value == "|":
            note_lines: list[str] = []
            while index < len(lines):
                note_lines.append(lines[index][2:] if lines[index].startswith("  ") else lines[index])
                index += 1
            fields[key] = "\n".join(note_lines).strip()
        else:
            fields[key] = value
    required = {
        "decision", "reviewed_source_commit", "reviewed_worktree_state",
        "candidate_report_sha256", "scanner_version", "approved_evidence_ids",
        "source_module", "target_module", "direction", "relationship_type",
        "approved_reachability", "runtime_state", "retained_unknowns", "review_notes",
    }
    if set(fields) != required:
        missing = sorted(required - set(fields))
        unexpected = sorted(set(fields) - required)
        raise ExplorerInputError(f"Review decision fields mismatch; missing={missing}, unexpected={unexpected}")
    if fields["decision"] not in {"APPROVED", "REJECTED"}:
        raise ExplorerInputError("Review decision must be APPROVED or REJECTED")
    if _COMMIT.fullmatch(fields["reviewed_source_commit"]) is None:
        raise ExplorerInputError("Reviewed source commit must be a full Git SHA")
    if fields["reviewed_worktree_state"] != "clean":
        raise ExplorerInputError("T3 first publication only supports a clean reviewed worktree")
    if _SHA256.fullmatch(fields["candidate_report_sha256"]) is None:
        raise ExplorerInputError("Candidate report digest must be a lowercase SHA-256")
    ids = fields["approved_evidence_ids"]
    if not ids or any(_EVIDENCE_ID.fullmatch(item) is None for item in ids) or len(set(ids)) != len(ids):
        raise ExplorerInputError("Approved evidence IDs must be a non-empty unique evidence ID list")
    if not fields["retained_unknowns"]:
        raise ExplorerInputError("Review decision must retain explicit unknowns")
    return fields


def _validate_github_author(comment: dict[str, Any], permission: dict[str, Any]) -> dict[str, Any]:
    user = comment.get("user")
    permission_user = permission.get("user")
    if not isinstance(user, dict) or not isinstance(permission_user, dict):
        raise ExplorerInputError("GitHub API author metadata is missing")
    login = user.get("login")
    if not isinstance(login, str) or login != permission_user.get("login"):
        raise ExplorerInputError("GitHub comment author and permission subject do not match")
    association = comment.get("author_association")
    repository_permission = permission.get("permission")
    if association != "OWNER" or repository_permission != "admin":
        raise ExplorerInputError("GitHub comment author is not authorized for architecture review")
    html_url = comment.get("html_url")
    if not isinstance(html_url, str) or not re.fullmatch(
        r"https://github\.com/nnewlander/translate/issues/3#issuecomment-\d+", html_url
    ):
        raise ExplorerInputError("Review comment URL is not an Issue #3 comment")
    created_at = comment.get("created_at")
    if not isinstance(created_at, str):
        raise ExplorerInputError("GitHub review timestamp is missing")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExplorerInputError("GitHub review timestamp is invalid") from error
    return {
        "login": login,
        "github_user_id": user.get("id"),
        "author_association": association,
        "repository_permission": repository_permission,
    }


def _candidate_binding_errors(candidate: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    relation = candidate.get("candidate_relation", {})
    snapshot = candidate.get("source_snapshot", {})
    expected = {
        "reviewed_source_commit": snapshot.get("revision"),
        "reviewed_worktree_state": snapshot.get("worktree", {}).get("state"),
        "candidate_report_sha256": candidate.get("candidate_report_sha256"),
        "scanner_version": candidate.get("scanner_version"),
        "source_module": relation.get("source_module"),
        "target_module": relation.get("target_module"),
        "direction": relation.get("direction"),
        "relationship_type": relation.get("proposed_relationship_type"),
        "approved_reachability": relation.get("reachability", {}).get("status"),
        "runtime_state": relation.get("runtime_state"),
    }
    errors = [f"{key}:mismatch" for key, value in expected.items() if decision.get(key) != value]
    production_ids = relation.get("production_evidence_ids")
    if not isinstance(production_ids, list) or set(decision["approved_evidence_ids"]) != set(production_ids):
        errors.append("approved_evidence_ids:mismatch")
    if decision["approved_evidence_ids"] != production_ids:
        errors.append("approved_evidence_ids:order-mismatch")
    if decision["retained_unknowns"] != relation.get("reachability", {}).get("retained_unknowns"):
        errors.append("retained_unknowns:mismatch")
    evidence_ids = {item.get("evidence_id") for item in candidate.get("evidence", []) if isinstance(item, dict)}
    if any(item not in evidence_ids for item in decision["approved_evidence_ids"]):
        errors.append("approved_evidence_ids:missing-from-candidate")
    if relation.get("scan_outcome") != "complete":
        errors.append("candidate_scan:not-complete")
    return errors


def import_review_mapping(
    candidate: dict[str, Any], comment: dict[str, Any] | None, permission: dict[str, Any] | None
) -> dict[str, Any]:
    """Turn authenticated GitHub API data into the only accepted versioned review mapping."""

    if comment is None:
        return {
            "schema": REVIEW_MAP_SCHEMA,
            "data_contract_version": DATA_CONTRACT_VERSION,
            "importer_version": REVIEW_IMPORTER_VERSION,
            "decision": None,
            "validation": {"status": "no-decision", "errors": ["github_review_decision:missing"]},
        }
    try:
        author = _validate_github_author(comment, permission or {})
        decision = parse_review_decision(comment.get("body"))
        errors = _candidate_binding_errors(candidate, decision)
    except ExplorerInputError as error:
        return {
            "schema": REVIEW_MAP_SCHEMA,
            "data_contract_version": DATA_CONTRACT_VERSION,
            "importer_version": REVIEW_IMPORTER_VERSION,
            "decision": None,
            "validation": {"status": "invalid", "errors": [str(error)]},
        }
    mapped = {
        "decision_id": f"github-comment:{comment.get('id')}",
        "result": decision["decision"],
        "reviewed_source_snapshot": {
            "revision": decision["reviewed_source_commit"],
            "worktree_state": decision["reviewed_worktree_state"],
            "input_files": deepcopy(candidate["source_snapshot"]["input_files"]),
            "scanner_version": decision["scanner_version"],
        },
        "candidate_report_sha256": decision["candidate_report_sha256"],
        "approved_evidence_ids": decision["approved_evidence_ids"],
        "relationship": {
            "source_module": decision["source_module"],
            "target_module": decision["target_module"],
            "direction": decision["direction"],
            "type": decision["relationship_type"],
            "reachability": decision["approved_reachability"],
            "runtime_state": decision["runtime_state"],
        },
        "retained_unknowns": decision["retained_unknowns"],
        "review_notes": decision["review_notes"],
        "github": {
            "comment_url": comment["html_url"],
            "comment_id": comment.get("id"),
            "actual_author": author,
            "reviewed_at": comment["created_at"],
        },
    }
    return {
        "schema": REVIEW_MAP_SCHEMA,
        "data_contract_version": DATA_CONTRACT_VERSION,
        "importer_version": REVIEW_IMPORTER_VERSION,
        "decision": mapped,
        "validation": {"status": "valid" if not errors else "invalid", "errors": errors},
    }


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if completed.returncode:
        raise ExplorerInputError(f"Git metadata unavailable: {(completed.stderr or completed.stdout).strip()}")
    return completed.stdout.rstrip("\r\n")


def _production_continuity(repository: Path, candidate: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    production_paths = set(candidate["scan_scope"]["production_files"])
    expected = {
        item["path"]: item["sha256"]
        for item in candidate["source_snapshot"]["input_files"]
        if item["path"] in production_paths
    }
    results: list[dict[str, Any]] = []
    overall = "exact-match"
    for relative in sorted(production_paths):
        path = repository / relative
        if relative not in expected or not path.is_file():
            status, actual = "missing", None
        else:
            actual = _sha256(path.read_bytes())
            status = "exact-match" if actual == expected[relative] else "material-change"
        if status != "exact-match":
            overall = status if overall == "exact-match" else overall
        results.append({"path": relative, "expected_sha256": expected.get(relative), "actual_sha256": actual, "status": status})
    return overall, results


def _review_map_binding_errors(candidate: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    relation = candidate.get("candidate_relation", {})
    reviewed = decision.get("reviewed_source_snapshot", {})
    mapped_relation = decision.get("relationship", {})
    comparisons = {
        "reviewed_source_snapshot.revision": (reviewed.get("revision"), candidate.get("source_snapshot", {}).get("revision")),
        "reviewed_source_snapshot.worktree_state": (reviewed.get("worktree_state"), candidate.get("source_snapshot", {}).get("worktree", {}).get("state")),
        "reviewed_source_snapshot.input_files": (reviewed.get("input_files"), candidate.get("source_snapshot", {}).get("input_files")),
        "reviewed_source_snapshot.scanner_version": (reviewed.get("scanner_version"), candidate.get("scanner_version")),
        "candidate_report_sha256": (decision.get("candidate_report_sha256"), candidate.get("candidate_report_sha256")),
        "approved_evidence_ids": (decision.get("approved_evidence_ids"), relation.get("production_evidence_ids")),
        "relationship.source_module": (mapped_relation.get("source_module"), relation.get("source_module")),
        "relationship.target_module": (mapped_relation.get("target_module"), relation.get("target_module")),
        "relationship.direction": (mapped_relation.get("direction"), relation.get("direction")),
        "relationship.type": (mapped_relation.get("type"), relation.get("proposed_relationship_type")),
        "relationship.reachability": (mapped_relation.get("reachability"), relation.get("reachability", {}).get("status")),
        "relationship.runtime_state": (mapped_relation.get("runtime_state"), relation.get("runtime_state")),
        "retained_unknowns": (decision.get("retained_unknowns"), relation.get("reachability", {}).get("retained_unknowns")),
    }
    errors = [f"review_mapping_binding:{key}" for key, values in comparisons.items() if values[0] != values[1]]
    github = decision.get("github", {})
    author = github.get("actual_author", {}) if isinstance(github, dict) else {}
    if author.get("author_association") != "OWNER" or author.get("repository_permission") != "admin":
        errors.append("review_mapping_binding:github_authorization")
    if not isinstance(github.get("comment_url"), str) or not re.fullmatch(
        r"https://github\.com/nnewlander/translate/issues/3#issuecomment-\d+", github.get("comment_url", "")
    ):
        errors.append("review_mapping_binding:github_comment_url")
    return errors


def publish_reviewed_explorer(
    repository: Path,
    snapshot_path: Path,
    module_mapping_path: Path,
    candidate: dict[str, Any],
    review_mapping: dict[str, Any],
) -> dict[str, Any]:
    base = build_explorer(snapshot_path, module_mapping_path)
    continuity, file_checks = _production_continuity(repository.resolve(strict=True), candidate)
    validation = review_mapping.get("validation", {})
    decision = review_mapping.get("decision")
    mapping_errors = []
    if review_mapping.get("schema") != REVIEW_MAP_SCHEMA:
        mapping_errors.append("review_mapping_schema:unsupported")
    if review_mapping.get("data_contract_version") != DATA_CONTRACT_VERSION:
        mapping_errors.append("review_mapping_contract:unsupported")
    if review_mapping.get("importer_version") != REVIEW_IMPORTER_VERSION:
        mapping_errors.append("review_mapping_importer:unsupported")
    if isinstance(decision, dict):
        mapping_errors.extend(_review_map_binding_errors(candidate, decision))
    if decision is None and validation.get("status") == "no-decision":
        status, reasons = "candidate", validation.get("errors", [])
    elif validation.get("status") != "valid" or not isinstance(decision, dict) or mapping_errors:
        status, reasons = "unknown", [*validation.get("errors", []), *mapping_errors] or ["review_mapping:invalid"]
    elif decision.get("result") == "REJECTED":
        status, reasons = "needs-review", ["review_decision:rejected"]
    elif decision.get("result") != "APPROVED":
        status, reasons = "unknown", ["review_decision:unsupported"]
    elif continuity != "exact-match":
        status, reasons = "needs-review", [f"production_source_continuity:{continuity}"]
    else:
        status, reasons = "current-reviewed", []
    if status not in REVIEW_STATUSES or continuity not in CONTINUITY_STATUSES:
        raise ExplorerInputError("Internal T3 review state is invalid")
    relation_candidate = candidate["candidate_relation"]
    evidence_by_id = {item["evidence_id"]: item for item in candidate["evidence"]}
    # The visible evidence chain always comes from the fixed candidate facts.
    # A malformed decision stays available for diagnostics but cannot select or
    # suppress the source facts shown by the Explorer.
    evidence_ids = relation_candidate["production_evidence_ids"]
    relationship = {
        "id": "relationship:production-control:launches:translation-execution",
        "source_module": relation_candidate["source_module"],
        "target_module": relation_candidate["target_module"],
        "direction": relation_candidate["direction"],
        "type": relation_candidate["proposed_relationship_type"],
        "review_status": status,
        "review_reasons": reasons,
        "code_reachability": relation_candidate["reachability"],
        "runtime_state": relation_candidate["runtime_state"],
        "evidence_ids": evidence_ids,
        "evidence": [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id],
        "architecture_explanation": (
            decision.get("review_notes", "") if isinstance(decision, dict) else "尚无有效人工架构审核决定。"
        ),
        "review_decision": decision,
        "source_continuity": {"status": continuity, "files": file_checks},
    }
    relationship["supporting_evidence"] = [
        item for item in candidate["evidence"] if item.get("source_role") == "auxiliary_test"
    ]
    relationship["presentation_zh"] = build_relationship_presentation_zh(
        base["architecture"]["modules"], relationship, candidate["evidence"]
    )
    base["schema"] = "architecture-explorer.data.v2"
    base["data_contract_version"] = DATA_CONTRACT_VERSION
    base["architecture"]["relationships"] = [relationship]
    base["implementation"] = {
        "revision": _git(repository, "rev-parse", "HEAD"),
        "worktree_state": "dirty" if _git(repository, "status", "--porcelain=v1", "--untracked-files=normal") else "clean",
        "separate_from_reviewed_source_snapshot": True,
    }
    digest_model = {
        "snapshot_facts_sha256": base["snapshot"]["facts_sha256"],
        "mapping_sha256": base["generation_inputs"]["mapping"]["sha256"],
        "architecture": base["architecture"],
        "data_contract_version": base["data_contract_version"],
        "implementation": base["implementation"],
    }
    base["deterministic_content_sha256"] = _sha256(_canonical_bytes(digest_model))
    return base


__all__ = [
    "DATA_CONTRACT_VERSION", "REVIEW_IMPORTER_VERSION", "REVIEW_MAP_SCHEMA",
    "import_review_mapping", "parse_review_decision", "publish_reviewed_explorer",
]
