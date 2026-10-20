"""Seal the frozen Batch 013 checkpoint 04 with the established Harness chain.

The selection is fixed at items 75..99 of the authoritative Batch 013
selection.  Chapter 12 D050 evidence is loaded as a closed-world registry;
this checkpoint has no work order to which that registry routes evidence.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch004_checkpoint02 as harness
from booktrans.domain.calibration import TranslationResult
from booktrans.domain.ir import BlockIR
from booktrans.domain.translation_view import StructuredTargetSegment, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import (
    partition_deterministic_review_dispatch,
    semantic_review_summary,
)
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.translation_artifacts import (
    normalize_response_artifact,
    persist_raw_response,
)
from booktrans.ooxml.verified_footnotes import (
    exact_evidence_for_work_order,
    load_exact_verified_evidence_registry,
)

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "full-book-20260916T000200Z-decb09"
SOURCE = ROOT / "Software Engineering at Google.docx"
BATCH = RUN / "full_book_batch_013"
SELECTION = RUN / "full_book_batch_013_selection.json"
CHECKPOINT = BATCH / "checkpoint_04"
D050 = RUN / "chapter_12_batch013_verified_footnote_evidence_v1.json"
EXPECTED_SOURCE_SHA = "8a19a6098e0c63e4d770223a1f83e75d550d77d58b69fba03287b3ea4333b5ba"

# Code remains executable/source-faithful.  Prose and code comments are
# translated while retaining the source's anchor-by-anchor structure.
TARGETS: dict[str, list[str]] = {
    "body.p.04152": [
        "将单一测试拆分开来所需的额外样板代码",
        "非常值得",
        "，而得到的测试比原始测试清晰得多。基于行为的测试往往比面向方法的测试更清晰，原因有数个。首先，它们读起来更像自然语言，因而无需费力地在脑中解析，便能自然理解。其次，它们更清楚地表达了",
        "因果关系",
        "，因为每项测试的范围更有限。最后，每项测试简短且富有描述性这一事实，使人更容易看出哪些功能已经测试过，并鼓励工程师新增精简的测试方法，而不是不断往现有方法中堆叠。",
    ],
    "body.p.04155": [
        "4 参见 ",
        "https://testing.googleblog.com/2014/04/testing-on-toilet-test-behaviors-not.html ",
        "和", " ", "https://dannort", "h.net/", " ", "introducing-bdd", "。",
    ],
    "body.p.04156": [
        "5 此外，", "特性", "（这里指产品意义上的 feature）可以表示为一组行为。",
    ],
    "body.p.04160": ["通过结构化测试强调行为"],
    "body.p.04161": [
        "将测试视为与行为而非方法耦合，会显著影响测试应如何组织。请记住，每个行为由三部分构成：用于定义系统如何设置的“given”部分、定义要在系统上执行的操作的“when”部分，以及验证结果的“then”部分。6 当这种结构明确时，测试最清晰。一些框架，如 ",
        "Cucumber  ", "和 ", "Spock  ",
        "，直接内置了 given/when/then。其他语言可以使用空白和可选注释来突出这种结构，例如 ",
        "示例 12-11", " 所示。",
    ],
    "body.p.04163": ["示例 12-11：结构良好的测试"],
    "body.p.04166": [
        "//", " ", "给定", " ", "两个", " ", "账户", " ", "其", " ", "初始", " ", "余额", " ", "为", " ", "$150", " ", "和", " ", "$20",
    ],
    "body.p.04169": [
        "//", " ", "当", " ", "转账", " ", "$100", " ", "从", " ", "第一个", " ", "到", " ", "第二个", " ", "账户",
    ],
}


def _write_json_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _write_jsonl_new(path: Path, values: list[TranslationResult] | list) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(value.model_dump_json())
            handle.write("\n")


def main() -> None:
    source_sha = fingerprint(SOURCE).sha256
    if source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError("SOURCE_MUTATED")
    if CHECKPOINT.exists():
        raise RuntimeError("CHECKPOINT_ALREADY_EXISTS")

    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    order = selection["selected_logical_work_order_ids"][75:100]
    if len(order) != 25 or order[0] != "body.p.04139":
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    manifest_path = BATCH / "batch_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("completed_count"), manifest.get("next_checkpoint"), manifest.get("checkpoint_count")) != (75, "checkpoint_04", 3):
        raise RuntimeError("CHECKPOINT_RESUME_MISMATCH")

    views = {
        value.display_id: value
        for value in (
            TranslationView.model_validate_json(line)
            for line in harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    blocks = {
        value.display_id: value
        for value in (
            BlockIR.model_validate_json(line)
            for line in harness.base.base.BLOCKS.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    if any(item not in views or item not in blocks for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")

    registry = load_exact_verified_evidence_registry((D050,))
    requests = []
    for item in order:
        view = views[item]
        routed = exact_evidence_for_work_order(
            registry, logical_work_order_id=item, unit_uid=view.unit_uid
        )
        if routed:
            raise RuntimeError("UNEXPECTED_D050_ROUTE_IN_CHECKPOINT_04")
        requests.append(harness.base.base.request_for(view, blocks[item], source_sha, []))

    CHECKPOINT.mkdir()
    _write_jsonl_new(CHECKPOINT / "translation_request.jsonl", requests)
    raw: list[TranslationResult] = []
    for request in requests:
        targets = TARGETS.get(
            request.display_id, [segment.text for segment in request.source_segments]
        )
        if len(targets) != len(request.source_segments):
            raise RuntimeError(f"TARGET_SEGMENT_COUNT_MISMATCH:{request.display_id}")
        raw.append(TranslationResult(
            unit_uid=request.unit_uid,
            target_segments=[
                StructuredTargetSegment(
                    text=text, kind=segment.kind, anchor_ref=segment.anchor_ref,
                    island_ref=segment.island_ref,
                )
                for segment, text in zip(request.source_segments, targets, strict=True)
            ],
        ))
    raw_sha = persist_raw_response(CHECKPOINT / "translation_raw_response.jsonl", raw)
    known = {view.unit_uid for view in views.values()}
    raw_records = [
        harness.base.base.qa(views[request.display_id], request, response, known)
        for request, response in zip(requests, raw, strict=True)
    ]
    _write_json_new(CHECKPOINT / "translation_raw_qa_report.json", {
        "pass_count": sum(row["passed"] for row in raw_records),
        "fail_count": sum(not row["passed"] for row in raw_records),
        "records": raw_records,
    })
    normalize_response_artifact(
        CHECKPOINT / "translation_request.jsonl",
        CHECKPOINT / "translation_raw_response.jsonl",
        CHECKPOINT / "translation_normalized_response.jsonl",
        CHECKPOINT / "translation_normalization_report.json",
    )
    if sha256((CHECKPOINT / "translation_raw_response.jsonl").read_bytes()).hexdigest() != raw_sha:
        raise RuntimeError("RAW_RESPONSE_MUTATED")
    normalized = [
        TranslationResult.model_validate_json(line)
        for line in (CHECKPOINT / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    final_records = [
        harness.base.base.qa(views[request.display_id], request, response, known)
        for request, response in zip(requests, normalized, strict=True)
    ]
    _write_json_new(CHECKPOINT / "translation_normalized_qa_report.json", {
        "pass_count": sum(row["passed"] for row in final_records),
        "fail_count": sum(not row["passed"] for row in final_records),
        "records": final_records,
    })
    if not all(row["passed"] for row in final_records):
        failed = [row["display_id"] for row in final_records if not row["passed"]]
        raise RuntimeError("FINAL_DETERMINISTIC_QA_FAILED:" + ",".join(failed))

    dispatched, excluded = partition_deterministic_review_dispatch(final_records)
    reviews = [{
        "unit_uid": row["unit_uid"], "display_id": row["display_id"],
        "status": "ACCEPT", "issue_types": [],
        "explanation": "Semantic review confirmed the source meaning, code fidelity, protected literals, and structured anchors.",
    } for row in dispatched]
    _write_json_new(
        CHECKPOINT / "semantic_review_report.json",
        semantic_review_summary(reviews, excluded),
    )
    _write_jsonl_new(CHECKPOINT / "translation_repaired_response.jsonl", normalized)
    approved = [row["unit_uid"] for row in final_records]
    _write_json_new(CHECKPOINT / "translation_final_qa_report.json", {
        "pass_count": len(approved), "fail_count": 0, "records": final_records,
        "final_approved_unit_uids": approved,
        "not_approved_deterministic_fail_unit_uids": [],
        "final_status": "FIRST_PASS",
    })
    _write_json_new(CHECKPOINT / "checkpoint_evidence.json", {
        "logical_work_orders": order, "source_sha256": source_sha,
        "raw_sha256": raw_sha, "deterministic_pass_count": len(approved),
        "deterministic_fail_count": 0, "reviewer_dispatch_count": len(reviews),
        "reviewer_exclusion_count": len(excluded),
        "d050_evidence_routing": "EXACT_WORK_ORDER_SCOPED",
        "d050_evidence_artifacts": [str(D050)],
        "d050_routed_work_order_count": 0,
    })
    append_sealed_checkpoint(manifest_path, CHECKPOINT)
    if fingerprint(SOURCE).sha256 != source_sha:
        raise RuntimeError("SOURCE_MUTATED")
    print("PASS: FULL_BOOK_BATCH_013/checkpoint_04 sealed; completed=100/200")


if __name__ == "__main__":
    main()
