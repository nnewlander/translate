"""Project a frozen physical chapter scope into logical translation work orders."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LogicalWorkOrder:
    """One ordinary block or one source-proven composite translator target."""

    work_order_id: str
    member_display_ids: tuple[str, ...]
    composite_group_id: str | None = None


def select_final_logical_work_orders(
    body_display_ids: Sequence[str],
    included_chapter_content: Sequence[Mapping[str, object]],
    composite_groups: Sequence[Mapping[str, object]],
) -> list[LogicalWorkOrder]:
    """Apply only the frozen final-scope additions and composite replacement.

    Body scope is already in stable body order.  Added chapter content has the
    final-scope artifact's stable order.  A composite replaces all of its
    physical members, so its two members can never inflate the denominator.
    """
    groups_by_member: dict[str, Mapping[str, object]] = {}
    for group in composite_groups:
        members = tuple(str(member) for member in group["members"])
        if len(members) != 2:
            raise ValueError("FINAL_SCOPE_COMPOSITE_MEMBER_COUNT")
        for member in members:
            if member in groups_by_member:
                raise ValueError("FINAL_SCOPE_COMPOSITE_MEMBER_OVERLAP")
            groups_by_member[member] = group

    ordered_physical = [*map(str, body_display_ids), *(str(item["display_id"]) for item in included_chapter_content)]
    seen_physical: set[str] = set()
    emitted_groups: set[str] = set()
    work_orders: list[LogicalWorkOrder] = []
    for display_id in ordered_physical:
        if display_id in seen_physical:
            continue
        seen_physical.add(display_id)
        group = groups_by_member.get(display_id)
        if group is None:
            work_orders.append(LogicalWorkOrder(display_id, (display_id,)))
            continue
        group_id = str(group["group_id"])
        if group_id in emitted_groups:
            continue
        members = tuple(str(member) for member in group["members"])
        if not all(member in ordered_physical for member in members):
            raise ValueError("FINAL_SCOPE_COMPOSITE_MEMBER_MISSING")
        work_orders.append(LogicalWorkOrder(f"composite.{group_id}", members, group_id))
        emitted_groups.add(group_id)
    return work_orders
