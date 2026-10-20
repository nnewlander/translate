"""Author the staged, closed-world target map for Batch 015 checkpoint 04."""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch014_checkpoint01 as harness
from booktrans.domain.translation_view import TranslationView

RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
SELECTION = RUN / "full_book_batch_015_selection.json"
TARGET_MAP = Path(r"D:\translate\data\full-book-20260916T000200Z-decb09") / "full_book_batch_015_checkpoint_04_authored_targets.json"

REPLACEMENTS = {
    "body.p.04629": ["大多数主流编程语言都有模拟框架。在 Google，我们针对 Java 使用 Mockito，", "Googletest 的 googlemock 组件", "用于 C++，而 ", "unittest.mock ", "用于 Python。"],
    "body.p.04630": ["尽管模拟框架能让测试替身更易于使用，但它们也有一些重要注意事项：过度使用往往会使代码库更难维护。本章稍后会介绍其中的一些问题。"],
    "body.p.04631": ["使用测试替身的技术"],
    "body.p.04632": ["使用测试替身主要有三种技术。本节简要介绍这些技术，帮助你快速了解它们是什么以及彼此有何不同。本章后续各节会更详细地讲解如何有效应用它们。"],
    "body.p.04633": ["了解这些技术区别的工程师，在需要使用测试替身时，更有可能知道应采用哪一种技术。"],
    "body.p.04637": ["伪实现"],
    "body.p.04638": ["", "伪实现  ", "是 API 的轻量级实现，其行为类似真实实现，但不适合用于生产环境；例如内存中的数据库。", "示例 13-7 ", "给出了一个伪实现的示例。"],
    "body.p.04639": ["示例 13-7：简单的伪实现"],
    "body.p.04648": ["当需要使用测试替身时，伪实现通常是理想技术；但测试所需的对象可能没有现成伪实现，而自行编写又可能很困难，因为你需要确保它现在和将来都与真实实现具有相似行为。"],
    "body.p.04649": ["桩实现"],
    "body.p.04650": ["桩实现 ", "是为原本自身没有行为的函数赋予行为的过程——你向函数准确指定应返回哪些值（也就是 ", "桩定 ", "返回值）。"],
    "body.p.04651": ["示例 13-8 ", "说明了桩实现。Mockito 模拟框架中的 when(...).thenReturn(...) 方法调用指定了 lookupUser() 方法的行为。"],
    "body.p.04653": ["示例 13-8：桩实现"],
}


def main() -> None:
    if TARGET_MAP.exists():
        raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    order = selection["selected_logical_work_order_ids"][75:100]
    views = {value.display_id: value for value in (TranslationView.model_validate_json(line) for line in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    if len(order) != 25 or any(item not in views for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
    targets = {item: REPLACEMENTS.get(item, [segment.text for segment in views[item].segments]) for item in order}
    if any(len(targets[item]) != len(views[item].segments) for item in order):
        raise RuntimeError("TARGET_SEGMENT_COUNT_MISMATCH")
    TARGET_MAP.parent.mkdir(parents=True, exist_ok=True)
    with TARGET_MAP.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump({"artifact_type": "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP", "batch": "FULL_BOOK_BATCH_015", "checkpoint": "checkpoint_04", "logical_work_orders": order, "targets": targets}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"PASS: authored target map created at {TARGET_MAP}")


if __name__ == "__main__":
    main()
