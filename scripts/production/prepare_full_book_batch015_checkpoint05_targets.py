"""Author the staged, closed-world target map for Batch 015 checkpoint 05."""
from __future__ import annotations
import json
from pathlib import Path
import sys
SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))
import run_full_book_batch014_checkpoint01 as harness
from booktrans.domain.translation_view import TranslationView
RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
SELECTION = RUN / "full_book_batch_015_selection.json"
TARGET_MAP = Path(r"D:\translate\data\full-book-20260916T000200Z-decb09\full_book_batch_015_checkpoint_05_authored_targets.json")
TRANSLATIONS = {
 "body.p.04665":"桩实现通常通过模拟框架完成，以减少原本需要手动创建硬编码返回值的新类所产生的样板代码。",
 "body.p.04666":"尽管桩实现是一种快速、简单的技术，但它也有局限性，本章稍后会讨论。",
 "body.p.04667":"交互测试",
 "body.p.04668":"交互测试是在不实际调用函数实现的情况下，验证函数如何被调用的方法。如果函数未以正确方式调用，测试就应失败——例如函数根本没有被调用、调用次数过多，或传入了错误参数。",
 "body.p.04669":"示例 13-9 展示了一个交互测试实例。Mockito 模拟框架中的 verify(...) 方法用于验证 lookupUser() 是否按预期被调用。",
 "body.p.04671":"示例 13-9：交互测试",
 "body.p.04677":"与桩实现类似，交互测试通常通过模拟框架完成。这比手动创建新类来记录函数被调用次数及传入参数更能减少样板代码。",
 "body.p.04678":"交互测试有时也称为模拟。我们在本章避免使用这一术语，因为它容易与既可用于桩实现又可用于交互测试的模拟框架混淆。",
 "body.p.04679":"如本章后文所述，交互测试在某些情形下有用，但应尽可能避免，因为过度使用很容易导致脆弱的测试。",
 "body.p.04680":"真实实现",
 "body.p.04681":"尽管测试替身是极有价值的测试工具，我们在测试中的首选仍是使用被测系统依赖项的真实实现，也就是生产代码中使用的同一实现。测试在以下情况下具有更高保真度：",
 "body.p.04685":"它们以生产环境中实际执行的方式执行代码，使用真实实现有助于做到这一点。",
 "body.p.04686":"在 Google，随着我们发现过度使用模拟框架会让测试充斥重复代码、与真实实现脱节并使重构困难，对真实实现的偏好逐渐形成。本章后文将更详细讨论这一主题。",
 "body.p.04687":"在测试中优先使用真实实现，被称为经典测试。还有一种称为模拟主义测试的风格，其偏好是使用模拟框架而非真实实现。尽管软件行业中有些人实践模拟主义测试（包括最早模拟框架的创建者），但在 Google，我们发现这种风格很难规模化。它要求工程师在设计被测系统时遵守严格准则，而 Google 大多数工程师的默认做法，是以更适合经典测试风格的方式编写代码。",
 "body.p.04688":"优先真实，而非隔离"
}
def main() -> None:
 if TARGET_MAP.exists(): raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
 selection=json.loads(SELECTION.read_text(encoding="utf-8")); order=selection["selected_logical_work_order_ids"][100:125]
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if x)}
 if len(order)!=25 or any(i not in views for i in order): raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
 targets={i: ([TRANSLATIONS[i]] + ["" for _ in views[i].segments[1:]] if i in TRANSLATIONS else [s.text for s in views[i].segments]) for i in order}
 if any(len(targets[i])!=len(views[i].segments) for i in order): raise RuntimeError("TARGET_SEGMENT_COUNT_MISMATCH")
 TARGET_MAP.parent.mkdir(parents=True,exist_ok=True)
 with TARGET_MAP.open("x",encoding="utf-8",newline="\n") as f: json.dump({"artifact_type":"FULL_BOOK_BATCH_AUTHORED_TARGET_MAP","batch":"FULL_BOOK_BATCH_015","checkpoint":"checkpoint_05","logical_work_orders":order,"targets":targets},f,ensure_ascii=False,indent=2); f.write("\n")
 print(f"PASS: authored target map created at {TARGET_MAP}")
if __name__=="__main__": main()
