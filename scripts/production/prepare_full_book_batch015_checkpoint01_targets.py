"""Author the staged, closed-world target map for Batch 015 checkpoint 01."""

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
TARGET_MAP = Path(r"D:\translate\data\full-book-20260916T000200Z-decb09") / "full_book_batch_015_checkpoint_01_authored_targets.json"

# Each list is aligned one-for-one with the immutable TranslationView segments.
REPLACEMENTS: dict[str, list[str]] = {
    "body.p.04535": ["在 Google，我们已看到无数实例，表明测试替身能为代码库的生产率和软件质量带来益处，也看到它们可能造成的负面影响"],
    "body.p.04536": ["——若使用不当。Google 遵循的实践随时间推移并基于这些经验不断演变。过去，我们几乎没有关于如何"],
    "body.p.04539": ["258      |      第 13 章：测试替身"],
    "body.p.04543": ["有效使用测试替身的准则；但随着我们看到许多团队的代码库中反复出现常见模式和反模式，最佳实践逐渐形成。"],
    "body.p.04544": ["我们通过惨痛教训得到的一点是：过度使用模拟框架很危险。模拟框架让你可以轻松创建测试替身（本章稍后会更详细地讨论模拟框架）。模拟框架刚在 Google 使用时，仿佛是一把适用于所有钉子的锤子——无需考虑如何构造代码依赖项，就能非常容易地为孤立的代码片段编写高度聚焦的测试。直到数年过去、积累了无数测试后，我们才开始意识到这类测试的代价：尽管它们易于编写，却很少发现缺陷，同时又需要持续投入大量精力维护。如今 Google 的钟摆已开始向另一方向摆动，许多工程师不再使用模拟框架，转而编写更贴近真实情况的测试。"],
    "body.p.04545": ["尽管本章讨论的实践在 Google 内部普遍获得认同，但不同团队的实际应用差异很大。这种差异源自工程师对这些实践了解不一、现有代码库不符合这些实践而形成的惯性，或团队只图短期省事、没有思考长期影响。"],
    "body.p.04546": ["基本概念"],
    "body.p.04547": ["在深入讨论如何有效使用测试替身之前，先介绍与之相关的一些基本概念。它们构成了本章稍后将讨论的最佳实践的基础。"],
    "body.p.04548": ["一个测试替身示例"],
    "body.p.04549": ["设想一个需要处理信用卡付款的电商网站。其核心可能包含如", "示例 13-1", "所示的代码。"],
    "body.p.04551": ["示例 13-1：信用卡服务"],
    "body.p.04552": ["class", " ", "PaymentProcessor", " {"],
    "body.p.04553": ["private", " ", "CreditCardService", " ", "creditCardService", ";"],
    "body.p.04554": ["..."],
    "body.p.04555": ["boolean", " ", "makePayment", "(CreditCard creditCard, Money amount) {"],
    "body.p.04556": ["if", " (creditCard.isExpired()) { ", "return", " ", "false", "; }"],
    "body.p.04557": ["boolean", " success ="],
    "body.p.04558": ["creditCardService", ".chargeCreditCard(creditCard, amount);"],
    "body.p.04559": ["return", " success;"],
    "body.p.04560": ["}"],
    "body.p.04561": ["}"],
    "body.p.04565": ["在测试中使用真实的信用卡服务并不可行（想想运行测试产生的所有交易手续费！），但可以使用测试替身来", "模拟", "真实系统的行为。", "示例 13-2", "展示了一个极其简单的测试替身。"],
    "body.p.04567": ["示例 13-2：一个简单的测试替身"],
    "body.p.04568": ["class", " ", "TestDoubleCreditCardService", " ", "implements", " CreditCardService {"],
    "body.p.04569": ["@Override"],
}


def main() -> None:
    if TARGET_MAP.exists():
        raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    order = selection["selected_logical_work_order_ids"][:25]
    if len(order) != 25 or order != list(REPLACEMENTS):
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    views = {
        value.display_id: value
        for value in (
            TranslationView.model_validate_json(line)
            for line in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    if any(item not in views for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
    for item in order:
        if len(REPLACEMENTS[item]) != len(views[item].segments):
            raise RuntimeError(f"TARGET_SEGMENT_COUNT_MISMATCH:{item}")
    TARGET_MAP.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_type": "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP",
        "batch": "FULL_BOOK_BATCH_015",
        "checkpoint": "checkpoint_01",
        "logical_work_orders": order,
        "targets": REPLACEMENTS,
    }
    with TARGET_MAP.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"PASS: authored target map created at {TARGET_MAP}")


if __name__ == "__main__":
    main()
