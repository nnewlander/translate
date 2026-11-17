from __future__ import annotations

from typing import Any, Callable

from .generator import ExplorerInputError
from .node_review import TRUSTED_REPOSITORY
from .node_review_packet import (
    ALLOWED_RESULTS,
    build_node_review_packet,
    render_node_review_comment,
)
from .review_source import build_review_source_receipt, review_source_sha256


NODE_REVIEW_ANCHOR_CANDIDATE_SCHEMA = (
    "architecture-explorer.node-review-anchor-candidate.v1"
)


def preflight_node_review_anchor_candidate(
    explorer: dict[str, Any],
    *,
    issue_number: int,
    comment_id: int,
    api: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an untrusted candidate from one canonical live receipt."""

    repository = api(f"repos/{TRUSTED_REPOSITORY}")
    issue = api(f"repos/{TRUSTED_REPOSITORY}/issues/{issue_number}")
    comment = api(f"repos/{TRUSTED_REPOSITORY}/issues/comments/{comment_id}")
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    login = user.get("login")
    if not isinstance(login, str) or not login:
        raise ExplorerInputError("GitHub node review comment has no actual author login")
    permission = api(
        f"repos/{TRUSTED_REPOSITORY}/collaborators/{login}/permission"
    )
    source = build_review_source_receipt(
        repository,
        issue,
        comment,
        permission,
        fixed_t3_identity=False,
    )
    if (
        source["issue"].get("number") != issue_number
        or source["comment"].get("id") != comment_id
    ):
        raise ExplorerInputError(
            "GitHub node review receipt does not match the requested identity"
        )

    packet = build_node_review_packet(explorer)
    matching_results = [
        result
        for result in ALLOWED_RESULTS
        if comment.get("body")
        == render_node_review_comment(packet, result=result, comment_id=comment_id)
    ]
    if len(matching_results) != 1:
        raise ExplorerInputError(
            "GitHub node review comment does not match one exact final body"
        )

    candidate = {
        "schema": NODE_REVIEW_ANCHOR_CANDIDATE_SCHEMA,
        "trust_status": "untrusted",
        "installation_status": "uninstalled",
        "repository": TRUSTED_REPOSITORY,
        "issue_id": source["issue"]["id"],
        "issue_number": source["issue"]["number"],
        "comment_id": source["comment"]["id"],
        "comment_created_at": source["comment"]["created_at"],
        "comment_updated_at": source["comment"]["updated_at"],
        "source_receipt_sha256": review_source_sha256(source),
    }
    return source, candidate


__all__ = [
    "NODE_REVIEW_ANCHOR_CANDIDATE_SCHEMA",
    "preflight_node_review_anchor_candidate",
]
