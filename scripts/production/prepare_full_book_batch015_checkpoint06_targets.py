"""Author the staged, closed-world target map for Batch 015 checkpoint 06."""
from __future__ import annotations
import json
from pathlib import Path
import sys
SCRIPTS=Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path: sys.path.insert(0,str(SCRIPTS))
import run_full_book_batch014_checkpoint01 as harness
from booktrans.domain.translation_view import TranslationView
RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09"); SELECTION=RUN/"full_book_batch_015_selection.json"; TARGET_MAP=Path(r"D:\translate\data\full-book-20260916T000200Z-decb09\full_book_batch_015_checkpoint_06_authored_targets.json")
TRANSLATIONS={
"body.p.04689":"使用依赖项的真实实现，会使被测系统更加真实，因为这些真实实现中的所有代码都会在测试中执行。相反，使用测试替身的测试会将被测系统与其依赖项隔离，使测试不执行被测系统依赖项中的代码。",
"body.p.04690":"我们偏好真实的测试，因为它们能让我们更有信心被测系统正常工作。如果单元测试过于依赖测试替身，工程师可能需要运行集成测试，或手动验证功能是否按预期工作，才能获得同等信心。执行这些额外任务会拖慢开发；如果工程师因其耗时而完全跳过它们，甚至可能让缺陷漏过。",
"body.p.04691":"将一个类的所有依赖项都替换为测试替身，会随意地把被测系统限制在作者恰好直接放入该类的实现上，并排除恰好位于其他类中的实现。不过，好的测试应独立于实现——它应根据所测试的 API 编写，而不是根据实现的组织方式编写。",
"body.p.04692":"如果真实实现中存在缺陷，使用真实实现可能导致测试失败。这是好事！你希望测试在这种情况下失败，因为这表明你的代码在生产环境中无法正常工作。有时，",
"body.p.04693":"真实实现",
"textbox.100.p.0001":"一连串的", "textbox.101.p.0001":"失败，因为其他", "textbox.102.p.0001":"的", "textbox.103.p.0001":"测试", "textbox.104.p.0001":"测试", "textbox.105.p.0001":"可能造成", "textbox.106.p.0001":"使用",
"body.p.04694":"真实实现也可能失败。但借助良好的开发工具，例如一个",
"body.p.04698":"真实实现      |      265",
"body.p.04702":"持续集成（CI）系统，通常很容易追溯导致失败的变更。",
"table.018.r.001.c.001.p.001":"案例研究：@DoNotMock",
"table.018.r.001.c.001.p.002":"在 Google，我们见过太多过度依赖模拟框架的测试，因此促成了 Java 中 @DoNotMock 注解的创建；它是 ErrorProne 静态分析工具的一部分。该注解让 API 所有者能够声明：“此类型不应被模拟，因为存在更好的替代方案。”",
"table.018.r.001.c.001.p.003":"如果工程师尝试用模拟框架为带有 @DoNotMock 注解的类或接口创建实例（如示例 13-10 所示），就会看到错误提示，引导其采用更合适的测试策略，例如真实实现或伪实现。该注解最常用于可直接使用的简单值对象，以及具备设计良好的伪实现的 API。",
"table.018.r.001.c.001.p.004":"示例 13-10：@DoNotMock 注解",
"table.018.r.001.c.001.p.007":"API 所有者为什么要关心？简言之，这会严重限制 API 所有者随时间更改其实现的能力。正如本章后文将探讨的，每当模拟框架用于桩实现或交互测试时，都会复制 API 提供的行为。",
"table.018.r.001.c.001.p.008":"当 API 所有者想更改其 API 时，可能发现它已经在 Google 代码库中被模拟数千次甚至数万次！这些测试替身很可能表现出违反被模拟类型 API 契约的行为，例如为绝不应返回 null 的方法返回 null。如果测试使用真实实现或伪实现，API 所有者就能在无需先修复数千个有缺陷测试的情况下更改实现。",
"body.p.04704":"如何决定何时使用真实实现",
"body.p.04705":"如果真实实现运行快速、具有确定性且依赖关系简单，就应优先使用它。例如，值对象应使用真实实现，如金额、日期、地理地址，或列表、映射等集合类。"
}
def main() -> None:
 if TARGET_MAP.exists(): raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
 s=json.loads(SELECTION.read_text(encoding="utf-8")); order=s["selected_logical_work_order_ids"][125:150]
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if x)}
 if len(order)!=25 or any(i not in views for i in order): raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
 targets={i:([TRANSLATIONS[i]]+["" for _ in views[i].segments[1:]] if i in TRANSLATIONS else [x.text for x in views[i].segments]) for i in order}
 if any(len(targets[i])!=len(views[i].segments) for i in order): raise RuntimeError("TARGET_SEGMENT_COUNT_MISMATCH")
 TARGET_MAP.parent.mkdir(parents=True,exist_ok=True)
 with TARGET_MAP.open("x",encoding="utf-8",newline="\n") as f: json.dump({"artifact_type":"FULL_BOOK_BATCH_AUTHORED_TARGET_MAP","batch":"FULL_BOOK_BATCH_015","checkpoint":"checkpoint_06","logical_work_orders":order,"targets":targets},f,ensure_ascii=False,indent=2); f.write("\n")
 print(f"PASS: authored target map created at {TARGET_MAP}")
if __name__=="__main__": main()
