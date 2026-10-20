"""Freeze the existing all-book TranslationViews into the first scale-out scope."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import TranslationView
from booktrans.harness.logical_work_order_selector import select_final_logical_work_orders
from booktrans.harness.source_integrity import fingerprint


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
BLOCKS = ROOT / "runs" / "extract-20260914T050300Z-decb09-094777" / "blocks.jsonl"
PILOT_SCOPE = ROOT / "runs" / "m5.3-20260914T140000Z-decb09" / "multi_chapter_scope_final.json"
PILOT_MANIFEST = ROOT / "runs" / "m5.3-20260914T140000Z-decb09" / "multi_chapter_manifest.json"
PILOT_CLOSURE = ROOT / "runs" / "m5.3-20260914T140000Z-decb09" / "m5_3b_multi_chapter_translation_closure_report.json"

CHAPTER_STARTS = {
    "chapter_1": 791, "chapter_2": 1126, "chapter_3": 1370,
    "chapter_4": 1824, "chapter_5": 1981, "chapter_6": 2373,
    "chapter_7": 2594, "chapter_8": 2988, "chapter_9": 3334,
    "chapter_10": 3588, "chapter_11": 3936, "chapter_12": 4247,
    "chapter_13": 4848, "chapter_14": 5341, "chapter_15": 5826,
    "chapter_16": 6012, "chapter_17": 6308, "chapter_18": 6589,
    "chapter_19": 6950, "chapter_20": 7202, "chapter_21": 7361,
    "chapter_22": 7727, "chapter_23": 8025, "chapter_24": 8106,
    "chapter_25": 8556,
}


def read_jsonl(path: Path, model: type[BlockIR] | type[TranslationView]) -> dict[str, object]:
    return {
        item.display_id: item
        for item in (model.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    }


def phase_for(block: BlockIR) -> str:
    if block.story != "body":
        return "reader_facing_running_structural_content"
    ordinal = block.locator.paragraph_ordinal
    if ordinal < 93:
        return "front_matter"
    if ordinal < 612:
        return "toc_late_stage"
    if ordinal < 641:
        return "foreword"
    if ordinal < 791:
        return "preface"
    starts = list(CHAPTER_STARTS.items())
    for index, (chapter, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else 8971
        if start <= ordinal < end:
            return chapter
    return "back_matter"


def stable_physical_order(block: BlockIR) -> tuple[int, int, str]:
    story_order = {"body": 0, "headers": 1, "footers": 2}
    return (story_order.get(block.story, 9), block.locator.paragraph_ordinal or 0, block.display_id)


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    views = read_jsonl(VIEWS, TranslationView)
    blocks = read_jsonl(BLOCKS, BlockIR)
    # A TranslationView can carry protected segments (for example a URL inside
    # otherwise translatable prose), so its enclosing BlockIR is not always
    # labelled solely TRANSLATABLE.  The frozen view set is the production
    # work-order authority; every view must still resolve to exactly one IR
    # block.
    if not set(views) <= set(blocks):
        raise RuntimeError("FULL_BOOK_VIEW_BLOCK_SCOPE_MISMATCH")

    pilot_scope = json.loads(PILOT_SCOPE.read_text(encoding="utf-8"))
    pilot_manifest = json.loads(PILOT_MANIFEST.read_text(encoding="utf-8"))
    closure = json.loads(PILOT_CLOSURE.read_text(encoding="utf-8"))
    if closure["M5_3B_TRANSLATION_CLOSURE"] != "PASS" or closure["current_release_approved_count"] != 627:
        raise RuntimeError("PILOT_RELEASE_CLOSURE_NOT_PASS")

    approved_physical: set[str] = set()
    pilot_phase_by_member: dict[str, str] = {}
    composite_groups: list[dict[str, object]] = []
    for chapter in pilot_scope["chapters"]:
        chapter_id = chapter["chapter_id"]
        manifest = next(item for item in pilot_manifest["chapters"] if item["chapter_id"] == chapter_id)
        for unit in manifest["units"]:
            approved_physical.add(unit["display_id"])
            pilot_phase_by_member[unit["display_id"]] = chapter_id
        for unit in chapter["included"]:
            approved_physical.add(unit["display_id"])
            pilot_phase_by_member[unit["display_id"]] = chapter_id
        composite_groups.extend(chapter["composite_groups"])
    if len(approved_physical) != 635:
        raise RuntimeError("PILOT_PHYSICAL_RELEASE_SET_MISMATCH")

    ordered_blocks = sorted((blocks[display_id] for display_id in views), key=stable_physical_order)
    body_ids = [block.display_id for block in ordered_blocks if block.story == "body"]
    structural = [block for block in ordered_blocks if block.story != "body"]
    # Keep the frozen view ordering for ordinary work.  Passing the whole
    # closed-world set together is essential for the known body/footer
    # composites: their two physical members may live in different stories.
    logical = select_final_logical_work_orders(
        [*body_ids, *(block.display_id for block in structural)], [], composite_groups,
    )
    if len(logical) != 8096:
        raise RuntimeError("FULL_BOOK_LOGICAL_DENOMINATOR_MISMATCH")

    composite_phase_by_id = {group["group_id"]: group["chapter_id"] for group in composite_groups}
    orders = []
    for order in logical:
        members = list(order.member_display_ids)
        member_blocks = [blocks[member] for member in members]
        member_phases = {pilot_phase_by_member[member] for member in members if member in pilot_phase_by_member}
        if order.composite_group_id:
            phase = composite_phase_by_id[order.composite_group_id]
        elif member_phases:
            if len(member_phases) != 1:
                raise RuntimeError("PILOT_RELEASE_PHASE_AMBIGUITY")
            phase = member_phases.pop()
        else:
            phase = phase_for(member_blocks[0])
        release_status = "ALREADY_APPROVED" if set(members) <= approved_physical else "PENDING_FIRST_PASS"
        if release_status == "ALREADY_APPROVED" and not set(members) <= approved_physical:
            raise RuntimeError("PILOT_RELEASE_MEMBER_MISMATCH")
        orders.append({
            "logical_work_order_id": order.work_order_id,
            "physical_member_display_ids": members,
            "composite_group_id": order.composite_group_id,
            "phase": phase,
            "release_status": release_status,
        })
    approved = [order for order in orders if order["release_status"] == "ALREADY_APPROVED"]
    pending = [order for order in orders if order["release_status"] == "PENDING_FIRST_PASS"]
    if len(approved) != 627 or len(pending) != 7469:
        raise RuntimeError("FULL_BOOK_RELEASE_ACCOUNTING_MISMATCH")

    phase_counts: dict[str, dict[str, int]] = {}
    for order in orders:
        counts = phase_counts.setdefault(order["phase"], {"logical_total": 0, "already_approved": 0, "pending": 0})
        counts["logical_total"] += 1
        if order["release_status"] == "ALREADY_APPROVED":
            counts["already_approved"] += 1
        else:
            counts["pending"] += 1
    batch_order = [
        order for order in pending
        if order["phase"] in {"front_matter", "foreword", "preface", "chapter_1"}
    ][:100]
    if len(batch_order) != 100:
        raise RuntimeError("FULL_BOOK_BATCH_001_SELECTION_SHORT")
    selected_ids = [order["logical_work_order_id"] for order in batch_order]
    digest = sha256("\n".join(selected_ids).encode("utf-8")).hexdigest()

    RUN.mkdir(parents=True, exist_ok=False)
    write_json(RUN / "full_book_translation_scope.json", {
        "artifact_type": "FULL_BOOK_TRANSLATION_SCOPE",
        "scope_status": "PASS",
        "source_docx": str(SOURCE),
        "source_sha256": source_sha,
        "coverage_authority": str(BLOCKS.parent / "coverage.json"),
        "physical_translatable_work_order_count": len(views),
        "logical_translation_work_order_count": len(orders),
        "known_composite_group_count": len(composite_groups),
        "known_composite_physical_member_count": sum(len(group["members"]) for group in composite_groups),
        "already_approved_logical_count": len(approved),
        "remaining_first_pass_logical_count": len(pending),
        "pilot_release_authority": str(PILOT_CLOSURE),
        "late_stage_content": {
            "toc_page_number_finalization": "PLANNED_LATE_STAGE_NOT_OMITTED",
            "index_localization_page_reference_finalization": "PLANNED_LATE_STAGE_NOT_OMITTED",
            "cover_back_cover_image_baked_text_localization": "PLANNED_LATE_STAGE_NOT_OMITTED",
        },
        "progress_by_phase": phase_counts,
        "logical_work_orders": orders,
    })
    write_json(RUN / "full_book_progress.json", {
        "artifact_type": "FULL_BOOK_TRANSLATION_PROGRESS",
        "total_logical_work_orders": len(orders),
        "already_approved": len(approved),
        "final_approved": len(approved),
        "remaining": len(pending),
        "deterministic_fail": 0,
        "human_required": 0,
        "progress_by_phase": phase_counts,
        "status": "FULL_BOOK_PRODUCTION_READY",
    })
    write_json(RUN / "full_book_batch_001_selection.json", {
        "artifact_type": "FULL_BOOK_BATCH_SELECTION",
        "batch": "FULL_BOOK_BATCH_001",
        "macro_target": 100,
        "micro_checkpoint_size": 20,
        "selection_order": "front_matter -> foreword -> preface -> chapter_1; TOC is explicit late-stage content",
        "selected_logical_work_order_ids": selected_ids,
        "selected_id_set_sha256": digest,
        "selected_orders": batch_order,
    })
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
