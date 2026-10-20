"""Deterministic current-candidate authority for production checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Sequence

from booktrans.domain.calibration import TranslationResult


class CandidateAuthorityError(RuntimeError):
    """Evidence cannot prove one unique current candidate."""


def candidate_sha(candidate: TranslationResult) -> str:
    return sha256(candidate.model_dump_json().encode("utf-8")).hexdigest()


def require_parent_is_current(*, artifact: str, parent_candidate_sha256: str | None, current_candidate_sha256: str) -> None:
    """Fail closed before a derived candidate can fork the durable lineage."""
    if parent_candidate_sha256 != current_candidate_sha256:
        raise CandidateAuthorityError(
            f"CANDIDATE_LINEAGE_PARENT_MISMATCH:{artifact}:{parent_candidate_sha256}:{current_candidate_sha256}"
        )


@dataclass(frozen=True)
class CandidateMutation:
    artifact: str
    candidate: TranslationResult
    parent_candidate_sha256: str | None
    accepted: bool
    human_approved: bool = False
    legacy_unbound: bool = False
    legacy_compatible: bool = False


@dataclass(frozen=True)
class ResolvedCandidate:
    candidate: TranslationResult
    candidate_sha256: str
    lineage: tuple[dict[str, str], ...]


def resolve_authoritative_candidate(
    raw: TranslationResult, mutations: Sequence[CandidateMutation], *, root_artifact: str = "RAW"
) -> ResolvedCandidate:
    """Resolve a single head; rejected or stale branches cannot become current.

    RAW is always the root.  Every automatic accepted mutation must name the
    current head as its parent.  Human approval is an explicitly-bound terminal
    authority and therefore may replace the current accepted head.
    """
    head = raw
    head_sha = candidate_sha(raw)
    lineage: list[dict[str, str]] = [{"artifact": root_artifact, "candidate_sha256": head_sha, "status": "ROOT"}]
    for mutation in mutations:
        candidate_sha256 = candidate_sha(mutation.candidate)
        if mutation.legacy_unbound:
            lineage.append({"artifact": mutation.artifact, "candidate_sha256": candidate_sha256, "status": "LEGACY_UNBOUND_DERIVED"})
            continue
        if not mutation.accepted:
            lineage.append({"artifact": mutation.artifact, "candidate_sha256": candidate_sha256, "status": "REJECTED"})
            continue
        if mutation.human_approved:
            # Versioned production human evidence is also a mutation: it may
            # be terminal authority, but cannot silently fork the candidate
            # that the adjudication actually reviewed.  Pre-authority legacy
            # evidence has no parent and remains readable for sealed history.
            if mutation.parent_candidate_sha256 is not None:
                require_parent_is_current(
                    artifact=mutation.artifact,
                    parent_candidate_sha256=mutation.parent_candidate_sha256,
                    current_candidate_sha256=head_sha,
                )
            head, head_sha = mutation.candidate, candidate_sha256
            lineage.append({"artifact": mutation.artifact, "candidate_sha256": head_sha, "status": "HUMAN_APPROVED_HEAD"})
            continue
        if mutation.parent_candidate_sha256 != head_sha and mutation.legacy_compatible:
            lineage.append({"artifact": mutation.artifact, "candidate_sha256": candidate_sha256, "status": "LEGACY_UNBOUND_DERIVED"})
            continue
        require_parent_is_current(
            artifact=mutation.artifact,
            parent_candidate_sha256=mutation.parent_candidate_sha256,
            current_candidate_sha256=head_sha,
        )
        head, head_sha = mutation.candidate, candidate_sha256
        lineage.append({"artifact": mutation.artifact, "candidate_sha256": head_sha, "status": "ACCEPTED_HEAD"})
    return ResolvedCandidate(head, head_sha, tuple(lineage))
