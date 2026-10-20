"""Author the staged, closed-world target map for Batch 015 checkpoint 07."""
from __future__ import annotations
import json
from pathlib import Path
import sys
SCRIPTS=Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path: sys.path.insert(0,str(SCRIPTS))
import run_full_book_batch014_checkpoint01 as harness
from booktrans.domain.translation_view import TranslationView
RUN=Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09"); SELECTION=RUN/"full_book_batch_015_selection.json"; TARGET_MAP=Path(r"D:\translate\data\full-book-20260916T000200Z-decb09\full_book_batch_015_checkpoint_07_authored_targets.json")
T={
"body.p.04709":"不过，对于更复杂的代码，使用真实实现往往不可行。真实实现和测试替身各有取舍，并不存在何时应使用哪一种的精确答案；你需要考虑以下因素。",
"body.p.04710":"执行时间",
"body.p.04711":"单元测试最重要的品质之一是快速——你希望在开发过程中能持续运行它们，以快速获得代码是否正常工作的反馈（也希望它们在 CI 系统中运行时迅速完成）。因此，当真实实现很慢时，测试替身会非常有用。",
"body.p.04712":"对单元测试而言，多慢才算太慢？如果真实实现给每个测试用例的运行时间增加一毫秒，很少有人会认为它慢。但如果增加 10 毫秒、100 毫秒、1 秒，依此类推呢？",
"body.p.04713":"这里没有精确答案——这取决于工程师是否感到生产力下降，以及有多少测试使用真实实现（若只有五个测试用例，每个多一秒或许合理；若有 500 个则不然）。在边界情形下，通常更简单的做法是先使用真实实现，直到它慢得无法使用，再更新测试以改用测试替身。",
"body.p.04714":"测试并行化也有助于缩短执行时间。在 Google，我们的测试基础设施能轻松地将测试套件中的测试拆分到多台服务器执行。这样会增加 CPU 时间成本，但能大幅节省开发者时间。第 18 章会进一步讨论。",
"body.p.04715":"还要注意另一项权衡：使用真实实现会增加构建时间，因为测试需要构建真实实现及其所有依赖项。使用 Bazel 这类高可扩展性构建系统会有帮助，因为它会缓存未变更的构建产物。",
"body.p.04716":"确定性",
"body.p.04717":"对于被测系统的给定版本，如果运行测试始终得到相同结果，即测试要么总是通过、要么总是失败，那么该测试就是确定性的。相反，即使被测系统未变，结果仍可能改变的测试是不确定的。",
"body.p.04718":"测试中的不确定性会导致不稳定——即使被测系统没有变化，测试也可能偶尔失败。如第 11 章所述，如果开发者开始不信任测试结果并忽略失败，不稳定性会损害测试套件的健康度。若真实实现很少导致不稳定，可能无需处理，因为对工程师造成的干扰很小。但若不稳定性",
"body.p.04722":"经常发生，就该用测试替身替换真实实现，因为这样能提高测试的保真度。",
"body.p.04723":"真实实现可能比测试替身复杂得多，因此更可能具有不确定性。例如，使用多线程的真实实现可能偶尔导致测试失败，前提是被测系统的输出会随线程执行顺序而不同。",
"body.p.04724":"不确定性的常见原因是不封闭的代码；也就是说，它依赖于测试无法控制的外部服务。例如，尝试从 HTTP 服务器读取网页内容的测试，可能因服务器过载或网页内容变更而失败。应使用测试替身以避免测试依赖外部服务器。若测试替身不可行，另一种选择是使用服务器的封闭实例，其生命周期由测试控制。下一章将更详细地讨论封闭实例。",
"body.p.04725":"不确定性的另一个例子是依赖系统时钟的代码，因为被测系统的输出可能随当前时间不同而不同。测试不必依赖系统时钟，可以使用硬编码特定时间的测试替身。",
"body.p.04726":"依赖项构建",
"body.p.04727":"使用真实实现时，需要构建它的所有依赖项。例如，一个对象需要构建完整的依赖树：它依赖的所有对象、这些依赖对象所依赖的所有对象，等等。测试替身通常没有依赖项，因此构建测试替身可能比构建真实实现简单得多。",
"body.p.04728":"举一个极端例子，想象在测试中尝试创建以下代码片段中的对象。确定如何构建每一个对象会非常耗时。测试还需要持续维护，因为这些对象构造函数的签名修改后，测试也必须更新：",
"body.p.04730":"由于构建测试替身可能微不足道，人们很容易想改用它。例如，使用 Mockito 模拟框架时，构建测试替身只需如下操作：",
"body.p.04735":"尽管创建这个测试替身简单得多，但如本节前文所述，使用真实实现有重要好处。以这种方式过度使用测试替身也常有显著弊端，本章后文会讨论。因此，决定使用真实实现还是测试替身时需要权衡。",
"body.p.04736":"与其在测试中手动构建对象，理想方案是使用生产代码所使用的同一对象构建代码，例如工厂方法或自动依赖注入。为了支持测试使用场景，对象构建代码需要足够灵活，能使用测试替身，而非硬编码生产环境所用实现。",
"body.p.04737":"伪实现",
"body.p.04738":"若在测试中无法使用真实实现，最佳选择通常是在其位置使用伪实现。伪实现优于其他测试替身技术，因为它的行为与真实实现相似：被测系统甚至不应能分辨它交互的是真实实现还是伪实现。示例 13-11 展示了一个伪文件系统。",
"body.p.04739":"示例 13-11：伪文件系统"
}
def main() -> None:
 if TARGET_MAP.exists(): raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
 s=json.loads(SELECTION.read_text(encoding="utf-8")); order=s["selected_logical_work_order_ids"][150:175]
 views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in harness.harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines() if x)}
 if len(order)!=25 or any(i not in views for i in order): raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
 targets={i:([T[i]]+["" for _ in views[i].segments[1:]] if i in T else [x.text for x in views[i].segments]) for i in order}
 if any(len(targets[i])!=len(views[i].segments) for i in order): raise RuntimeError("TARGET_SEGMENT_COUNT_MISMATCH")
 TARGET_MAP.parent.mkdir(parents=True,exist_ok=True)
 with TARGET_MAP.open("x",encoding="utf-8",newline="\n") as f: json.dump({"artifact_type":"FULL_BOOK_BATCH_AUTHORED_TARGET_MAP","batch":"FULL_BOOK_BATCH_015","checkpoint":"checkpoint_07","logical_work_orders":order,"targets":targets},f,ensure_ascii=False,indent=2); f.write("\n")
 print(f"PASS: authored target map created at {TARGET_MAP}")
if __name__=="__main__": main()
