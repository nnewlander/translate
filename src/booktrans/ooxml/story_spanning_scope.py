"""Narrow, evidence-led story-spanning scope closure for approved source scope.

Body markers establish a chapter interval, but conversion-derived OOXML can put
chapter prose and footnotes in footer parts.  This module deliberately does
not infer general page order: callers must supply reviewed evidence records.
"""

from dataclasses import dataclass
from typing import Literal


ScopeDisposition = Literal[
    "INCLUDE_CHAPTER_CONTENT",
    "EXCLUDE_RUNNING_DECORATION",
    "REVIEW_REQUIRED",
]


@dataclass(frozen=True)
class StoryScopeEvidence:
    chapter_id: str
    unit_uid: str
    display_id: str
    part_name: str
    disposition: ScopeDisposition
    reason: str
    composite_group_id: str | None = None


def close_story_spanning_scope(
    body_unit_uids: set[str], evidence: list[StoryScopeEvidence],
) -> dict:
    """Return an auditable closed-world ledger without silently adding content.

    Evidence cannot replace a body member and each non-body unit can have only
    one disposition.  This keeps a content-bearing footer distinct from a
    page number or running title, while unresolved ownership remains visible.
    """
    seen: set[str] = set()
    for item in evidence:
        if item.unit_uid in body_unit_uids:
            raise ValueError(f"STORY_SCOPE_BODY_DUPLICATE: {item.display_id}")
        if item.unit_uid in seen:
            raise ValueError(f"STORY_SCOPE_DUPLICATE_EVIDENCE: {item.display_id}")
        seen.add(item.unit_uid)
    included = [item for item in evidence if item.disposition == "INCLUDE_CHAPTER_CONTENT"]
    excluded = [item for item in evidence if item.disposition == "EXCLUDE_RUNNING_DECORATION"]
    review = [item for item in evidence if item.disposition == "REVIEW_REQUIRED"]
    return {
        "body_baseline_count": len(body_unit_uids),
        "added_chapter_content_count": len(included),
        "excluded_running_decoration_count": len(excluded),
        "review_required_count": len(review),
        "final_scope_count": len(body_unit_uids) + len(included),
        "included": [item.__dict__ for item in included],
        "excluded": [item.__dict__ for item in excluded],
        "review_required": [item.__dict__ for item in review],
    }
