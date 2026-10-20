"""Write the immutable M5.3b multi-chapter translation-closure evidence.

This is a closed-world aggregation of the already-completed Chapter 2, 11,
and 18 release evidence.  It deliberately does not create a candidate DOCX
or modify the source package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


RUN_ID = "m5.3-20260914T140000Z-decb09"
SOURCE_NAME = "Software Engineering at Google.docx"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def final_report_ids(path: Path) -> tuple[list[str], set[str]]:
    report = read_json(path)
    records = report.get("records", [])
    identifiers = [record["display_id"] for record in records]
    failed = {record["display_id"] for record in records if not record.get("passed", False)}
    if report.get("fail_count", 0) != len(failed):
        raise RuntimeError(f"final-QA failure count disagrees with records: {path}")
    return identifiers, failed


def assert_unique(label: str, identifiers: list[str]) -> None:
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    if duplicates:
        raise RuntimeError(f"duplicate logical work orders in {label}: {duplicates}")


def require_final_qa_pass(path: Path) -> None:
    report = read_json(path)
    if report.get("fail_count", 0) != 0 or report.get("pass_count", 0) < 1:
        raise RuntimeError(f"recovery/composite final QA is not PASS: {path}")
    for record in report.get("records", []):
        if not record.get("passed", False):
            raise RuntimeError(f"recovery/composite final QA contains failed record: {path}")


def source_pair_is_canonical(value: Any, source_sha: str) -> bool:
    if isinstance(value, dict):
        return value.get("before") == source_sha and value.get("after") == source_sha
    if isinstance(value, list) and len(value) == 2:
        return value == [source_sha, source_sha]
    return False


def historical_raw_candidates(chapter_dir: Path, expected_sha: str) -> list[Path]:
    candidates: list[Path] = []
    for path in chapter_dir.rglob("translation_raw_response.jsonl"):
        if "second_pass_recovery" in path.parts:
            continue
        if sha256_file(path) == expected_sha:
            candidates.append(path)
    return candidates


def chapter_2_expected(run_root: Path, scope_groups: dict[str, dict[str, Any]]) -> tuple[list[str], list[str], set[str]]:
    chapter = run_root / "chapter_2"
    physical_ids: list[str] = []
    for batch_number in range(1, 9):
        path = chapter / f"batch_{batch_number:03d}" / "translation_request.jsonl"
        physical_ids.extend(row["display_id"] for row in jsonl_rows(path))
    assert_unique("chapter_2 physical first-pass requests", physical_ids)
    group = scope_groups["m5_3_ch2_project_hosting"]
    members = set(group["members"])
    expected: list[str] = []
    added_group = False
    for display_id in physical_ids:
        if display_id in members:
            if not added_group:
                expected.append(group["group_id"])
                added_group = True
            continue
        expected.append(display_id)
    assert added_group

    ordinary_paths = [chapter / "batch_001" / "downstream_v3" / "translation_final_qa_report.json"]
    ordinary_paths.extend(chapter / f"batch_{batch_number:03d}" / "translation_final_qa_report.json" for batch_number in range(2, 9))
    current_ids: list[str] = []
    failed: set[str] = set()
    for path in ordinary_paths:
        identifiers, path_failed = final_report_ids(path)
        current_ids.extend(identifiers)
        failed.update(path_failed)
    status = read_json(chapter / "composite_project_hosting_v3" / "final_status.json")
    if status.get("status") != "FINAL_APPROVED" or status.get("group_id") != group["group_id"]:
        raise RuntimeError("Chapter 2 Composite is not FINAL_APPROVED")
    current_ids.append(group["group_id"])
    return expected, current_ids, failed


def chapter_11_expected(run_root: Path, scope_groups: dict[str, dict[str, Any]]) -> tuple[list[str], list[str], set[str]]:
    chapter = run_root / "chapter_11"
    expected: list[str] = []
    for batch_number in (1, 2, 3):
        expected.extend(read_json(chapter / f"batch_{batch_number:03d}" / "batch_manifest.json")["order"])
    for checkpoint_number in range(1, 6):
        identifiers, _ = final_report_ids(chapter / "batch_004" / f"checkpoint_{checkpoint_number:02d}" / "translation_final_qa_report.json")
        expected.extend(identifiers)
    for batch_number, checkpoint_count in ((5, 5), (6, 4)):
        for checkpoint_number in range(1, checkpoint_count + 1):
            evidence = read_json(chapter / f"batch_{batch_number:03d}" / f"checkpoint_{checkpoint_number:02d}" / "checkpoint_evidence.json")
            expected.extend(
                item["work_order_id"] if isinstance(item, dict) else item
                for item in evidence["logical_work_orders"]
            )

    current_ids: list[str] = []
    failed: set[str] = set()
    for batch_number in (1, 2, 3):
        identifiers, path_failed = final_report_ids(chapter / f"batch_{batch_number:03d}" / "translation_final_qa_report.json")
        current_ids.extend(identifiers)
        failed.update(path_failed)
    for checkpoint_number in range(1, 6):
        identifiers, path_failed = final_report_ids(chapter / "batch_004" / f"checkpoint_{checkpoint_number:02d}" / "translation_final_qa_report.json")
        current_ids.extend(identifiers)
        failed.update(path_failed)
    for batch_number, checkpoint_count in ((5, 5), (6, 4)):
        for checkpoint_number in range(1, checkpoint_count + 1):
            identifiers, path_failed = final_report_ids(chapter / f"batch_{batch_number:03d}" / f"checkpoint_{checkpoint_number:02d}" / "translation_final_qa_report.json")
            current_ids.extend(identifiers)
            failed.update(path_failed)

    composite_paths = {
        "m5_3_ch11_system_scale_tests": chapter / "batch_003" / "composite_system_scale_tests" / "translation_final_qa_report.json",
        "m5_3_ch11_control_flow_statements": chapter / "batch_005" / "checkpoint_01" / "composite_control_flow_statements" / "translation_final_qa_report.json",
        "m5_3_ch11_footnote_4": chapter / "batch_005" / "checkpoint_01" / "composite_footnote_4" / "translation_final_qa_report.json",
        "m5_3_ch11_test_automation_platform": chapter / "batch_005" / "checkpoint_01" / "composite_test_automation_platform" / "translation_final_qa_report.json",
        "m5_3_ch11_system_grows": chapter / "batch_005" / "checkpoint_02" / "composite_system_grows" / "translation_final_qa_report.json",
        "m5_3_ch11_content_bullet": chapter / "batch_006" / "checkpoint_04" / "composite_content_bullet" / "translation_final_qa_report.json",
    }
    if set(composite_paths) != {group_id for group_id, group in scope_groups.items() if group["chapter_id"] == "chapter_11"}:
        raise RuntimeError("Chapter 11 Composite map does not match authoritative final scope")
    for group_id, path in composite_paths.items():
        require_final_qa_pass(path)
        current_ids.append(group_id)
    return expected, current_ids, failed


def chapter_18_expected(run_root: Path, scope_groups: dict[str, dict[str, Any]]) -> tuple[list[str], list[str], set[str]]:
    chapter = run_root / "chapter_18"
    selector = read_json(run_root / "chapter_18_final_logical_work_order_selector.json")
    if selector.get("physical_scope_count") != 245 or selector.get("logical_work_order_count") != 244:
        raise RuntimeError("Chapter 18 final logical selector has an invalid denominator")
    expected = [item["id"] for item in selector["work_orders"]]
    current_ids: list[str] = []
    failed: set[str] = set()
    for batch_number in range(1, 6):
        checkpoint_count = 5
        for checkpoint_number in range(1, checkpoint_count + 1):
            identifiers, path_failed = final_report_ids(chapter / f"batch_{batch_number:03d}" / f"checkpoint_{checkpoint_number:02d}" / "translation_final_qa_report.json")
            current_ids.extend(identifiers)
            failed.update(path_failed)
    group_id = "m5_3_ch18_modules_dependencies"
    selector_composite_ids = [
        item["id"] for item in selector["work_orders"] if item.get("composite_group_id") == group_id
    ]
    if selector_composite_ids != [f"composite.{group_id}"]:
        raise RuntimeError("Chapter 18 selector Composite identifier is invalid")
    require_final_qa_pass(chapter / "batch_004" / "checkpoint_05" / "composite_modules_dependencies" / "translation_final_qa_report.json")
    current_ids.append(selector_composite_ids[0])
    if group_id not in scope_groups:
        raise RuntimeError("Chapter 18 Composite missing from authoritative final scope")
    return expected, current_ids, failed


def collect_recoveries(run_root: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    reports = {
        "chapter_2": read_json(run_root / "chapter_2_recovery_report.json"),
        "chapter_11": read_json(run_root / "chapter_11_recovery_report.json"),
        "chapter_18": read_json(run_root / "chapter_18_translation_closure_report.json"),
    }
    selected: dict[str, list[dict[str, Any]]] = {chapter: report["recoveries"] for chapter, report in reports.items()}
    audit: list[dict[str, Any]] = []
    for chapter_id, recoveries in selected.items():
        chapter_dir = run_root / chapter_id
        for recovery in recoveries:
            display_id = recovery["display_id"]
            if chapter_id == "chapter_11":
                suffix = display_id.replace(".", "_")
                recovery_dir = chapter_dir / "second_pass_recovery" / (f"{suffix}_v2" if display_id == "body.p.03804" else suffix)
                raw_path = recovery_dir / "translation_raw_response.jsonl"
                historical_sha = recovery["historical_raw_sha256"]
            else:
                raw_path = Path(recovery["recovery_raw_path"])
                historical_sha = recovery.get("historical_raw_sha256", recovery.get("old_raw_sha256"))
            if sha256_file(raw_path) != recovery["recovery_raw_sha256"]:
                raise RuntimeError(f"recovery RAW hash mismatch: {raw_path}")
            require_final_qa_pass(raw_path.parent / "translation_final_qa_report.json")
            if chapter_id == "chapter_18":
                historical_path = Path(recovery["historical_raw_path"])
                candidates = [historical_path] if sha256_file(historical_path) == historical_sha else []
            else:
                candidates = historical_raw_candidates(chapter_dir, historical_sha)
            if not candidates:
                raise RuntimeError(f"historical immutable RAW cannot be located for {chapter_id}/{display_id}")
            audit.append(
                {
                    "chapter_id": chapter_id,
                    "logical_work_order_id": display_id,
                    "selected_recovery_raw_path": str(raw_path),
                    "selected_recovery_raw_sha256": recovery["recovery_raw_sha256"],
                    "historical_first_pass_raw_sha256": historical_sha,
                    "historical_first_pass_raw_paths_matching_frozen_sha256": [str(path) for path in candidates],
                    "status": "RECOVERY_SELECTED_HISTORICAL_RAW_IMMUTABLE",
                }
            )
    superseded = [
        {
            "chapter_id": "chapter_11",
            "path": reports["chapter_11"]["superseded_recovery_attempt"]["path"],
            "status": reports["chapter_11"]["superseded_recovery_attempt"]["status"],
            "excluded_from_current_release": True,
        }
    ]
    return selected, audit, superseded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, default=Path("runs") / RUN_ID)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    project_root = run_root.parents[1]
    output_path = run_root / "m5_3b_multi_chapter_translation_closure_report.json"
    if output_path.exists():
        raise RuntimeError(f"immutable closure report already exists: {output_path}")

    # This repository keeps the user-provided immutable DOCX at its project
    # root (rather than under a source/ directory).
    source_path = project_root / SOURCE_NAME
    source_sha = sha256_file(source_path)
    scope = read_json(run_root / "multi_chapter_scope_final.json")
    if not source_pair_is_canonical(scope.get("source_sha256_before_after"), source_sha):
        raise RuntimeError("authoritative final scope source SHA does not match the immutable source")
    groups = {group["group_id"]: group for group in scope["proven_composite_groups"]}

    expected_and_current = {
        "chapter_2": chapter_2_expected(run_root, groups),
        "chapter_11": chapter_11_expected(run_root, groups),
        "chapter_18": chapter_18_expected(run_root, groups),
    }
    selected_recoveries, historical_raw_audit, superseded = collect_recoveries(run_root)
    closure_artifacts = {
        "chapter_2": run_root / "chapter_2_recovery_report.json",
        "chapter_11": run_root / "chapter_11_recovery_report.json",
        "chapter_18": run_root / "chapter_18_translation_closure_report.json",
    }
    closure_reports = {chapter: read_json(path) for chapter, path in closure_artifacts.items()}
    expected_counts = {"chapter_2": 176, "chapter_11": 207, "chapter_18": 244}
    physical_counts = {chapter["chapter_id"]: chapter["final_scope_count"] for chapter in scope["chapters"]}
    chapter_entries: list[dict[str, Any]] = []
    all_logical_ids: list[str] = []
    all_current_ids: list[str] = []
    for chapter_id in ("chapter_2", "chapter_11", "chapter_18"):
        expected, current, failed = expected_and_current[chapter_id]
        assert_unique(f"{chapter_id} authoritative logical sequence", expected)
        assert_unique(f"{chapter_id} current first-pass/composite release sequence", current)
        recovery_ids = {item["display_id"] for item in selected_recoveries[chapter_id]}
        if failed != recovery_ids:
            raise RuntimeError(f"{chapter_id} first-pass failures do not exactly match selected recoveries: {failed} != {recovery_ids}")
        if set(expected) != set(current):
            missing = sorted(set(expected) - set(current))
            unexpected = sorted(set(current) - set(expected))
            raise RuntimeError(f"{chapter_id} current release set mismatch; missing={missing}, unexpected={unexpected}")
        if len(expected) != expected_counts[chapter_id]:
            raise RuntimeError(f"{chapter_id} logical count mismatch: {len(expected)}")
        report = closure_reports[chapter_id]
        approved = report.get("current_release_approved")
        if approved is None:
            approved = report["CURRENT_RELEASE_APPROVED"]["approved"]
        if approved != len(expected):
            raise RuntimeError(f"{chapter_id} closure report release count mismatch")
        source_pair = report.get("source_sha256_before_after")
        if not source_pair_is_canonical(source_pair, source_sha):
            raise RuntimeError(f"{chapter_id} closure report source SHA mismatch")
        chapter_groups = [group_id for group_id, group in groups.items() if group["chapter_id"] == chapter_id]
        chapter_entries.append(
            {
                "chapter_id": chapter_id,
                "physical_scope_count": physical_counts[chapter_id],
                "authoritative_logical_work_order_count": len(expected),
                "current_release_approved_count": len(expected),
                "exactly_one_current_release_target_per_logical_work_order": True,
                "logical_work_order_id_set_sha256": canonical_sha256(sorted(expected)),
                "current_release_target_id_set_sha256": canonical_sha256(sorted(current)),
                "logical_id_duplicate_count": 0,
                "missing_current_release_targets": [],
                "unexpected_current_release_targets": [],
                "first_pass_deterministic_failures_replaced_by_recovery_count": len(recovery_ids),
                "recovery_selected_work_order_ids": sorted(recovery_ids),
                "unresolved_deterministic_fail": 0,
                "human_required": 0,
                "composite_work_order_count": len(chapter_groups),
                "composite_work_order_ids": sorted(chapter_groups),
                "composite_status": "FINAL_APPROVED_LOGICAL_ONCE",
                "closure_artifact": str(closure_artifacts[chapter_id]),
            }
        )
        all_logical_ids.extend(f"{chapter_id}:{item}" for item in expected)
        all_current_ids.extend(f"{chapter_id}:{item}" for item in current)

    assert_unique("multi-chapter authoritative logical sequence", all_logical_ids)
    assert_unique("multi-chapter current release target sequence", all_current_ids)
    if scope.get("physical_scope_count") != 635 or scope.get("logical_translation_work_order_count") != 627:
        raise RuntimeError("authoritative multi-chapter scope count is not 635 physical / 627 logical")
    if len(all_logical_ids) != 627 or len(all_current_ids) != 627:
        raise RuntimeError("multi-chapter release count is not 627")

    report = {
        "stage": "M5.3B_MULTI_CHAPTER_RELEASE_CLOSURE",
        "scope_authority": str(run_root / "multi_chapter_scope_final.json"),
        "authoritative_source_docx": str(source_path),
        "authoritative_source_sha256": source_sha,
        "source_sha256_unchanged": True,
        "physical_scope_count": 635,
        "total_logical_work_orders": 627,
        "current_release_approved_count": 627,
        "exactly_one_current_release_target_per_logical_work_order": True,
        "logical_work_order_id_set_sha256": canonical_sha256(sorted(all_logical_ids)),
        "current_release_target_id_set_sha256": canonical_sha256(sorted(all_current_ids)),
        "chapter_counts": chapter_entries,
        "unresolved_deterministic_fail": 0,
        "human_required": 0,
        "composites": {
            "physical_member_count": 16,
            "logical_work_order_count": 8,
            "all_status": "FINAL_APPROVED_LOGICAL_ONCE",
        },
        "recovery_selected_units": historical_raw_audit,
        "historical_first_pass_raw_immutable": True,
        "superseded_evidence_excluded_from_current_release": superseded,
        "M5_3B_MULTI_CHAPTER_TRANSLATION_CLOSURE": "PASS",
        "M5_3B_TRANSLATION_CLOSURE": "PASS",
        "next_state": "READY_FOR_CANDIDATE_DOCX",
        "candidate_docx_created": False,
    }
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(output_path)


if __name__ == "__main__":
    main()
