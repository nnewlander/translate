"""Author the staged, closed-world target map for Batch 015 checkpoint 02."""

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
TARGET_MAP = Path(r"D:\translate\data\full-book-20260916T000200Z-decb09") / "full_book_batch_015_checkpoint_02_authored_targets.json"

REPLACEMENTS: dict[str, list[str]] = {
    "body.p.04570": ["public", " ", "boolean", " chargeCreditCard(CreditCard creditCard, Money amount) {"],
    "body.p.04571": ["return", " ", "true", ";"],
    "body.p.04572": ["}"], "body.p.04573": ["}"],
    "body.p.04574": ["虽然这个测试替身看起来并不十分有用，但在测试中使用它仍可让我们测试 makePayment() 方法中的一部分逻辑。例如，在", "示例 13-3", "中，我们可以验证信用卡过期时该方法是否正确工作，因为测试所走的代码路径并不依赖信用卡服务的行为。"],
    "body.p.04576": ["示例 13-3：使用测试替身"],
    "body.p.04577": ["@Test ", "public", " ", "void", " cardIsExpired_returnFalse() {"],
    "body.p.04578": ["boolean", " success = ", "paymentProcessor", ".makePayment(EXPIRED_CARD, AMOUNT);"],
    "body.p.04579": ["assertThat(success).isFalse(); }"],
    "body.p.04580": ["本章接下来的部分将讨论如何在比此处更复杂的情形中使用测试替身。"],
    "body.p.04581": ["接缝"],
    "body.p.04582": ["如果代码以便于为其编写单元测试的方式编写，就称其具有", "可测试性", "。", "接缝", "是一种通过允许使用测试替身来提高代码可测试性的方式——它使被测系统可以使用与生产环境不同的依赖项。"],
    "body.p.04583": ["依赖注入", "是引入接缝的一种常用技术。简言之，当一个类使用依赖注入时，它需要使用的任何类（即该类的", "依赖项", "）都会被传入，而不是直接实例化；这样就可以在测试中替换这些依赖项。"],
    "body.p.04584": ["示例 13-4", "展示了依赖注入的一个例子。构造函数并不创建 CreditCardService 实例，而是将其实例作为参数接受。"],
    "body.p.04588": ["示例 13-4：依赖注入"],
    "body.p.04589": ["class", " ", "PaymentProcessor", " {"],
    "body.p.04590": ["private", " CreditCardService creditCardService;"],
    "body.p.04591": ["PaymentProcessor(", "CreditCardService", " ", "creditCardService", ") { ", "this", ".creditCardService = creditCardService;"],
    "body.p.04592": ["}"], "body.p.04593": ["..."], "body.p.04594": ["}"],
    "body.p.04595": ["调用这个构造函数的代码负责创建合适的 CreditCardService 实例。生产代码可以传入一个与外部服务器通信的 CreditCardService 实现，而测试可以传入测试替身，如", "示例 13-5", "所示。"],
    "body.p.04597": ["示例 13-5：传入测试替身"],
    "body.p.04598": ["PaymentProcessor paymentProcessor ="],
    "body.p.04599": ["new", " PaymentProcessor(", "new", " ", "TestDoubleCreditCardService", "());"],
}


def main() -> None:
    if TARGET_MAP.exists():
        raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    order = selection["selected_logical_work_order_ids"][25:50]
    if len(order) != 25 or order != list(REPLACEMENTS):
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    views = {value.display_id: value for value in (TranslationView.model_validate_json(line) for line in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    if any(item not in views for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
    for item in order:
        if len(REPLACEMENTS[item]) != len(views[item].segments):
            raise RuntimeError(f"TARGET_SEGMENT_COUNT_MISMATCH:{item}")
    TARGET_MAP.parent.mkdir(parents=True, exist_ok=True)
    with TARGET_MAP.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump({"artifact_type": "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP", "batch": "FULL_BOOK_BATCH_015", "checkpoint": "checkpoint_02", "logical_work_orders": order, "targets": REPLACEMENTS}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"PASS: authored target map created at {TARGET_MAP}")


if __name__ == "__main__":
    main()
