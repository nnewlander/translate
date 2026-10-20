"""Logical-work-order resume selection from immutable checkpoint evidence."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ResumeCoverage:
    completed_logical_ids: frozenset[str]
    completed_physical_ids: frozenset[str]

def coverage(ordinary_ids: set[str], completed_composites: dict[str, tuple[str, ...]]) -> ResumeCoverage:
    logical=set(ordinary_ids)|set(completed_composites)
    physical=set(ordinary_ids)
    for members in completed_composites.values(): physical.update(members)
    return ResumeCoverage(frozenset(logical),frozenset(physical))

def next_logical(sequence: list[tuple[str, tuple[str, ...]]], done: ResumeCoverage) -> str | None:
    for logical_id,members in sequence:
        if logical_id not in done.completed_logical_ids and not any(x in done.completed_physical_ids for x in members): return logical_id
    return None
