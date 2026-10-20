"""Small, test-only B028 lifecycle reference model.

It deliberately models legal relationships, not production artifacts or QA.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class Model:
    candidate: str = "RAW"
    semantic: str = "NONE"
    recovery: str = "R0"
    checkpoint: str = "PARTIAL"
    lifecycle: str = "RUNNING"
    debt: str = "NONE"
    raw_root: str = "raw:0"
    current_head: str = "raw:0"
    accepted_head: str | None = None
    dispatches: int = 0
    dispatch_identity: str | None = None
    dispatch_counts: dict[str, int] = field(default_factory=dict)
    automatic_obligations_complete: bool = False
    cursor_from_seal: bool = False

    def event(self, name: str) -> None:
        if self.lifecycle == "CRASHED" and name not in {"resume", "rebuild"}:
            raise AssertionError("CRASHED_REQUIRES_RESUME")
        if name == "repair":
            if self.recovery == "EXHAUSTED":
                raise AssertionError("RECOVERY_EXHAUSTED")
            self.candidate, self.semantic = "PREPARED", "REPAIR"
            self.recovery = "R1" if self.recovery == "R0" else "R2"
        elif name == "normalize":
            if self.candidate != "PREPARED":
                raise AssertionError("NORMALIZE_REQUIRES_PREPARED")
        elif name in {"review_accept", "review_repair", "invalid_review"}:
            identity = f"{self.current_head}:{self.recovery}:{name}"
            if identity not in self.dispatch_counts:
                self.dispatch_identity, self.dispatches = identity, self.dispatches + 1
                self.dispatch_counts[identity] = 1
            if name == "invalid_review":
                return
            self.semantic = "ACCEPT" if name == "review_accept" else "REPAIR"
        elif name == "publish":
            if not (self.candidate == "PREPARED" and self.semantic == "ACCEPT"):
                raise AssertionError("PUBLISH_REQUIRES_CURRENT_ACCEPTANCE")
            self.current_head = f"published:{self.recovery}"
            self.accepted_head, self.candidate = self.current_head, "PUBLISHED"
            self.automatic_obligations_complete = True
        elif name == "crash":
            self.lifecycle = "CRASHED"
        elif name == "resume":
            self.lifecycle = "RESUMED"
        elif name == "rebuild":
            if self.current_head != self.accepted_head and self.candidate == "PUBLISHED":
                raise AssertionError("STALE_EVIDENCE_OVERRIDES_CURRENT")
        elif name == "defer":
            if self.recovery != "R2" or self.semantic != "REPAIR":
                raise AssertionError("DEFER_REQUIRES_EXHAUSTED_SECOND_REPAIR")
            self.recovery, self.debt, self.automatic_obligations_complete = "EXHAUSTED", "DEFERRED", True
        elif name == "seal":
            if not self.automatic_obligations_complete:
                raise AssertionError("SEAL_BEFORE_AUTOMATIC_OBLIGATIONS")
            if self.debt != "DEFERRED" and self.accepted_head != self.current_head:
                raise AssertionError("SEAL_WITHOUT_CURRENT_ACCEPTANCE")
            self.checkpoint, self.cursor_from_seal = "SEALED", True
        else:
            raise AssertionError(f"UNKNOWN_EVENT:{name}")
        self.assert_invariants()

    def assert_invariants(self) -> None:
        assert self.raw_root == "raw:0"                         # immutable RAW root
        assert self.current_head in {"raw:0", "published:R1", "published:R2"}  # unique head
        assert not (self.candidate == "PREPARED" and self.current_head.startswith("published"))
        if self.accepted_head is not None:
            assert self.accepted_head == self.current_head       # exact current-head acceptance
        if self.checkpoint == "SEALED":
            assert self.automatic_obligations_complete and self.cursor_from_seal
        if self.debt == "DEFERRED":
            assert self.checkpoint in {"PARTIAL", "SEALED"}    # production can continue
        assert all(count == 1 for count in self.dispatch_counts.values())  # identity exactly-once


SEQUENCES: dict[str, tuple[str, ...]] = {
    "repair_crash_resume": ("repair", "crash", "resume", "review_accept", "publish", "seal"),
    "prepared_rebuild_publish": ("repair", "normalize", "rebuild", "review_accept", "publish", "seal"),
    "repair_then_accept": ("review_repair", "repair", "review_accept", "publish", "seal"),
    "invalid_retry_crash_resume": ("invalid_review", "crash", "resume", "review_accept"),
    "rounds_exhaust_defer": ("repair", "review_repair", "repair", "review_repair", "defer", "seal"),
    "publish_stale_rebuild": ("repair", "review_accept", "publish", "rebuild", "seal"),
    "dispatch_crash_resume": ("review_accept", "crash", "resume", "review_accept"),
    "normalize_then_repair": ("repair", "normalize", "review_repair", "repair", "review_accept", "publish"),
    "crash_before_publish": ("repair", "review_accept", "crash", "resume", "publish", "seal"),
    "debt_resume_rebuild": ("repair", "review_repair", "repair", "review_repair", "defer", "crash", "resume", "rebuild"),
    "raw_review_accept": ("review_accept",),
    "prepared_accept_rebuild": ("repair", "review_accept", "rebuild", "publish", "seal"),
}


@pytest.mark.parametrize("_name,sequence", SEQUENCES.items())
def test_b028_reference_model_critical_sequences_are_legal(_name: str, sequence: tuple[str, ...]) -> None:
    model = Model()
    for event in sequence:
        model.event(event)
    model.assert_invariants()
    if model.debt == "DEFERRED":
        assert model.checkpoint == "SEALED" or model.lifecycle == "RESUMED"


def test_b028_reference_model_true_deterministic_failure_is_fail_closed_predicate() -> None:
    model = Model()
    model.event("repair")
    model.automatic_obligations_complete = False
    with pytest.raises(AssertionError, match="SEAL_BEFORE_AUTOMATIC_OBLIGATIONS"):
        model.event("seal")
