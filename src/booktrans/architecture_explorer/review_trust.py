"""Shared trust roots for Architecture Explorer review materials.

Object-specific modules validate their own Decisions and Claims.  This module
owns the one host-side rule for matching an immutable GitHub Owner/admin source
against an independently configured approval anchor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .generator import ExplorerInputError
from .review_source import _verify_owner_admin_review_source_against_anchor


@dataclass(frozen=True)
class ReviewApprovalAnchor:
    issue_id: int
    issue_number: int
    comment_id: int
    comment_created_at: str
    comment_updated_at: str
    source_receipt_sha256: str


@dataclass(frozen=True)
class ReviewTrustPolicy:
    """Host-owned policy; review input data can never populate this value."""

    anchors: tuple[ReviewApprovalAnchor, ...] = ()


# Installed only by a separately authorized Owner change.  CLI input, review
# material, environment variables, and the selected repository cannot extend
# this host-owned tuple.
PRODUCTION_RELATIONSHIP_REVIEW_APPROVAL_ANCHORS: tuple[
    ReviewApprovalAnchor, ...
] = ()

# Host configuration records the exact installation digest authorized by the
# Owner's second confirmation. A repository passed to a CLI cannot set this.
PRODUCTION_BATCH_INSTALLATION_DIGESTS: frozenset[str] = frozenset({
    # Issue #84 second confirmation. The exact installed batch is still required.
    "1041dab86073f3ea4aea04a9b492d27e4310566b203ac360f858275780a10a08",
})

# Per-owner host state shared by every local Worktree. Neither the selected
# repository nor its temporary directory controls this location.
HOST_REVIEW_STATE_DIRECTORY = Path.home() / ".booktrans" / "architecture-explorer"


def production_relationship_review_trust_policy() -> ReviewTrustPolicy:
    return ReviewTrustPolicy(PRODUCTION_RELATIONSHIP_REVIEW_APPROVAL_ANCHORS)


def match_trusted_review_source(
    source: dict[str, Any] | None,
    trust_policy: ReviewTrustPolicy,
    *,
    label: str,
) -> ReviewApprovalAnchor:
    if source is None:
        raise ExplorerInputError(f"{label} source is missing")
    issue = source.get("issue") if isinstance(source.get("issue"), dict) else {}
    comment = (
        source.get("comment") if isinstance(source.get("comment"), dict) else {}
    )
    for anchor in trust_policy.anchors:
        if (
            issue.get("id") == anchor.issue_id
            and issue.get("number") == anchor.issue_number
            and comment.get("id") == anchor.comment_id
            and comment.get("created_at") == anchor.comment_created_at
            and comment.get("updated_at") == anchor.comment_updated_at
        ):
            errors = _verify_owner_admin_review_source_against_anchor(
                source,
                expected_sha256=anchor.source_receipt_sha256,
            )
            if errors:
                raise ExplorerInputError(f"{label} source is untrusted: {errors}")
            return anchor
    raise ExplorerInputError(
        f"{label} source has no independently trusted approval anchor"
    )


__all__ = [
    "ReviewApprovalAnchor",
    "ReviewTrustPolicy",
    "match_trusted_review_source",
    "production_relationship_review_trust_policy",
]
