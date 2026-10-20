"""Author the staged, closed-world target map for Batch 014 checkpoint 04."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_full_book_batch004_checkpoint02 as harness
from booktrans.domain.translation_view import TranslationView


RUN = Path(r"D:\translate\runs\full-book-20260916T000200Z-decb09")
SELECTION = RUN / "full_book_batch_014_selection.json"


REPLACEMENTS: dict[str, list[str]] = {
    "body.p.04380": [
        "//", " ", "数百", " ", "的", " ", "行", " ", "的", " ", "其他", " ", "测试……",
    ],
    "body.p.04388": [
        "这种策略可以使测试非常简洁，但随着测试套件规模增长，它也会带来问题。其一，可能很难理解为什么为某个测试选择了特定的值。在",
        "示例 12-21",
        "中，测试名称所幸说明了正在测试哪些场景，但你仍需向上滚动到定义处，以确认 ACCOUNT_1 和 ACCOUNT_2 是否适用于这些场景。更具描述性的常量名称（例如，",
    ],
    "body.p.04392": [
        "CLOSED_ACCOUNT 和 ACCOUNT_WITH_LOW_BALANCE）会有所帮助，但它们仍会让人更难看清正在测试的值的确切细节；而且这些值易于复用，可能会鼓励工程师即使名称并不完全描述测试所需内容时也去复用它们。",
    ],
    "body.p.04393": [
        "工程师通常倾向于使用共享常量，因为在每个测试中构造单独的值会很冗长。实现这一目标的更好方法是",
        "使用辅助方法构造数据 ",
        "（参见 ",
        "示例 12-22",
        "），这样测试作者只需指定自己关心的值，并为其他所有值",
        "7",
        "设置合理的默认值。对于支持命名参数的语言，这种构造方式很容易实现；而没有命名参数的语言可以使用诸如 ",
        "构建器 ",
        "模式来模拟它们（通常借助 ",
        "AutoValue",
        "）：",
    ],
    "body.p.04394": ["示例 12-22：使用辅助方法共享值"],
    "body.p.04395": [
        "#", " ", "一个", " ", "辅助", " ", "方法", " ", "封装", " ", "一个", " ", "构造函数", " ", "通过", " ", "定义", " ", "任意", " ", "默认值", " ", "用于",
    ],
    "body.p.04396": ["#", " ", "每个", " ", "参数", " ", "的", " ", "默认值。"],
    "body.p.04399": [
        "#", " ", "测试", " ", "调用", " ", "该", " ", "辅助方法，", " ", "仅", " ", "为", " ", "它们", " ", "关心", " ", "的", " ", "参数", " ", "指定", " ", "值",
    ],
    "body.p.04400": ["#", " ", "关心", " ", "的。"],
    "body.p.04404": [
        "// 像 Java 这样不支持命名参数的语言，可以通过返回一个表示正在构造的值的可变“builder”对象来模拟命名参数。",
    ],
}

REPLACEMENTS_BY_CHECKPOINT: dict[str, dict[str, list[str]]] = {
    "checkpoint_04": REPLACEMENTS,
    "checkpoint_05": {
        "body.p.04411": [
            "// 测试随后调用构建器上的方法，只覆盖它们关心的参数",
            "，",
            "然后调用 build",
            "() ",
            "以从构建器中获得一个真实的值。",
        ],
        "body.p.04417": [
            "7  在许多情况下，甚至可以对那些尚未",
        ],
        "body.p.04418": [
            "显式设置的字段所返回的默认值进行轻微随机化。这样有助于确保两个不同实例不会意外地比较为相等，也会让工程师更难将依赖关系硬编码到默认值上。",
        ],
        "body.p.04425": [
            "使用辅助方法来构造这些值，可以让每个测试创建其所需的精确值，而不必担心指定无关信息或与其他测试发生冲突。",
        ],
        "body.p.04426": ["共享设置"],
        "body.p.04427": [
            "测试共享代码的一种相关方式是通过设置/初始化逻辑。许多测试框架允许工程师定义在套件中每项测试运行前执行的方法。若使用得当，这些方法可以免去重复乏味且无关的初始化逻辑，使测试更清晰、更简洁；若使用不当，则可能通过把重要细节隐藏在单独的初始化方法中而损害测试的完整性。",
        ],
        "body.p.04428": [
            "设置方法最适合用于构造被测对象及其协作者。当大多数测试不关心用于构造这些对象的具体参数、可以让它们保持默认状态时，这种方式很有用。同样的想法也适用于为测试替身存根返回值；我们将在",
            "第 13 章",
            "更详细地探讨这一概念。",
        ],
        "body.p.04429": [
            "使用设置方法的一个风险是：如果测试开始依赖设置中使用的特定值，测试可能变得不清楚。例如，",
            "示例 12-23 ",
            "中的测试看起来并不完整，因为读者需要四处查找字符串“Donald Knuth”的来源。",
        ],
        "body.p.04431": ["示例 12-23：依赖设置方法中取值"],
    },
    "checkpoint_06": {
        "body.p.04440": [
            "//", " ", "[...", " ", "数百", " ", "多", " ", "行", " ", "的", " ", "测试", " ", "...]",
        ],
        "body.p.04449": [
            "像这样的测试如果明确关注特定值，就应直接声明这些值；必要时覆盖设置方法中定义的默认值。如",
            "示例 12-24",
            "所示，得到的测试虽然略有更多重复，但更具描述性和意义。",
        ],
        "body.p.04451": ["示例 12-24：覆盖设置方法中的值"],
        "body.p.04466": ["共享辅助方法与验证"],
    },
    "checkpoint_07": {
        "body.p.04467": [
            "测试之间共享代码的最后一种常见方式，是从测试方法体中调用“辅助方法”。我们已经讨论过辅助方法如何能简洁地构造测试值——这种用法是合理的，但其他类型的辅助方法可能很危险。",
        ],
        "body.p.04468": [
            "一种常见的辅助方法是在被测系统上执行一组共同断言的方法。极端的例子是在每个测试方法末尾调用一个 validate 方法，对被测系统执行一组固定检查。养成这种验证策略可能是坏习惯，因为采用此方法的测试较少以行为为驱动。在这类测试中，更难确定任何特定测试的意图，也更难推断作者编写它时心中设想的确切情形。当引入缺陷时，这种策略还可能使缺陷更难定位，因为它们经常会导致大量测试开始失败。",
        ],
        "body.p.04472": [
            "不过，更聚焦的验证方法仍可能有用。最佳验证辅助方法会断言一个",
            "单一的概念性事实",
            "，即关于其输入的事实；这不同于覆盖一系列条件的通用验证方法。当所验证的条件在概念上简单、但实现它需要循环或条件逻辑，若将其置于测试方法体中会降低清晰度时，这类方法尤其有用。例如，",
            "示例 12-25 ",
            "中的辅助方法可能适合用于覆盖账户访问相关若干不同情况的测试。",
        ],
        "body.p.04473": ["示例 12-25：概念上简单的测试"],
        "body.p.04482": ["定义测试基础设施"],
        "body.p.04483": [
            "我们目前讨论的技术涵盖了在一个测试类或套件内跨方法共享代码。有时，跨多个测试套件共享代码也很有价值。我们将此类代码称为",
            "测试基础设施",
            "。虽然它通常在集成测试或端到端测试中更有价值，但精心设计的测试基础设施在某些情况下可以让单元测试更易编写。",
        ],
        "body.p.04484": [
            "自定义测试基础设施必须比单个测试套件内发生的代码共享更审慎地对待。在许多方面，鉴于它可能有许多依赖它的调用者，且在不引入破坏性变更的情况下难以修改，测试基础设施代码与生产代码更相似，而非其他测试代码。大多数工程师在测试自己的功能时，不会被期望修改通用测试基础设施。测试基础设施需要被视为独立产品，因此，",
            "测试基础设施必须始终拥有自己的测试",
            "。",
        ],
        "body.p.04485": [
            "当然，大多数工程师使用的测试基础设施以众所周知的第三方库形式提供，例如",
            "JUnit",
            "。这类库数量众多，组织内应尽早且尽可能普遍地将其标准化。例如，多年前 Google 规定 Mockito 是新 Java 测试唯一可用的模拟框架，并禁止新测试使用其他模拟框架。这项规定当时让习惯其他框架的人有些抱怨，但今天人们普遍认为这是让测试更易理解、更易使用的明智举措。",
        ],
        "body.p.04489": ["结论"],
        "body.p.04490": [
            "单元测试是我们作为软件工程师所拥有的最强大工具之一，可帮助确保系统在面对未预料的变化时仍能长期正常工作。但能力越大，责任越大；粗心使用单元测试可能导致系统维护需要更多精力、变更也需要更多精力，却无法真正提升我们对该系统的信心。",
        ],
        "body.p.04491": [
            "Google 的单元测试远非完美，但我们发现，遵循本章所述实践的测试比不遵循的测试更有价值，幅度可达数量级。希望它们能帮助你提升自己测试的质量！",
        ],
        "body.p.04492": ["要点速览"],
        "body.p.04493": ["•  努力让测试保持不变。"],
        "body.p.04494": ["•  通过公共 API 进行测试。"],
        "body.p.04495": ["•  测试状态，而非交互。"],
        "body.p.04496": ["•  让测试完整且简洁。"],
        "body.p.04497": ["•  测试行为，而非方法。"],
    },
    "checkpoint_08": {
        "body.p.04498": ["•  构造测试以突出行为。"],
        "body.p.04499": ["•  按所测试的行为为测试命名。"],
        "body.p.04500": ["•  不要在测试中放入逻辑。"],
        "body.p.04501": ["•  编写清晰的失败消息。"],
        "body.p.04502": ["•  为测试共享代码时，遵循 DAMP 而非 DRY。"],
        "body.p.04504": ["测试替身"],
        "body.p.04511": ["作者：Andrew Trenk、Dillon Bly；编辑：Tom Manshreck"],
        "body.p.04513": [
            "单元测试是保持开发者高效并减少代码缺陷的关键工具。它们可能很容易为简单代码编写，但随着代码变得更复杂，编写单元测试也会变得困难。",
        ],
        "body.p.04514": [
            "例如，设想你要为一个向外部服务器发送请求、然后将响应存入数据库的函数编写测试。费些力气或许能写出少量测试；但若需要编写数百或数千个这样的测试，测试套件很可能需要数小时才能运行完，并且可能会因随机网络故障或测试相互覆盖数据等问题而变得不稳定。",
        ],
        "body.p.04515": [
            "测试替身在这种情况下很有用。一个 ",
            "测试替身 ",
            "是在测试中可替代真实实现的对象或函数，就像电影中的特技替身可以替代演员一样。使用测试替身通常被称为 ",
            "模拟",
            "，但本章避免使用该术语，因为如我们将看到的，它也用于指称测试替身更具体的方面。",
        ],
        "body.p.04516": [
            "也许最明显的一类测试替身，是行为与真实实现相似但更简单的对象实现，例如内存数据库。其他类型的测试替身可以帮助验证系统的特定细节，例如便于触发罕见错误条件，或确保调用重量级函数而无需实际执行该函数的实现。",
        ],
        "body.p.04517": [
            "前两章介绍了",
            "小型测试 ",
            "的概念并讨论了为什么它们应构成测试套件的大多数。然而，生产代码",
        ],
        "body.p.04518": [
            "往往因跨多个进程或机器通信而无法满足小型测试的约束。测试替身可以比真实",
        ],
        "body.p.04525": ["实现轻量得多，因此你可以编写大量执行迅速且不易不稳定的小型测试。"],
        "body.p.04526": ["测试替身对软件开发的影响"],
        "body.p.04527": [
            "使用测试替身会给软件开发带来一些复杂性，需要作出若干取舍。本章后续会更深入地讨论这里引入的概念：",
        ],
        "body.p.04528": ["可测试性"],
        "body.p.04529": [
            "要使用测试替身，代码库需要被设计为",
            "可测试的",
            "——测试应能以测试替身替换真实实现。例如，调用数据库的代码需要足够灵活，才能用测试替身取代真实数据库。如果代码库在设计时没有考虑测试，之后才决定需要测试，则重构代码以支持测试替身的使用可能需要投入大量精力。",
        ],
        "body.p.04530": ["适用性"],
        "body.p.04531": [
            "恰当地应用测试替身能够强力提升工程效率，但不当使用会导致测试脆弱、复杂且效果较差。当在大型代码库中不恰当地使用测试替身时，这些缺点会被放大，可能严重损害工程师的生产力。在许多情况下，测试替身并不适合，工程师应优先使用真实实现。",
        ],
        "body.p.04532": ["保真度"],
        "body.p.04533": [
            "保真度 ",
            "指测试替身的行为与其所替代的真实实现行为的接近程度。如果测试替身的行为与真实实现显著不同，使用该测试替身的测试很可能没有多少价值——例如，设想为一个忽略所有写入数据、始终返回空结果的数据库测试替身编写测试。但完美保真度未必可行；测试替身往往必须比真实实现简单得多，才适合在测试中使用。在许多情形中，即使没有完美保真度，使用测试替身也是恰当的。使用测试替身的单元测试通常还需要由覆盖真实实现的更大范围测试加以补充。",
        ],
        "body.p.04534": ["Google 的测试替身"],
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", default="checkpoint_04")
    args = parser.parse_args()
    checkpoint = args.checkpoint
    try:
        checkpoint_number = int(checkpoint.rsplit("_", 1)[1])
    except (IndexError, ValueError) as error:
        raise RuntimeError("CHECKPOINT_IDENTIFIER_INVALID") from error
    replacements = REPLACEMENTS_BY_CHECKPOINT.get(checkpoint)
    if replacements is None:
        raise RuntimeError("CHECKPOINT_TARGETS_NOT_AUTHORED")
    target_map = RUN / f"full_book_batch_014_{checkpoint}_authored_targets.json"
    if target_map.exists():
        raise RuntimeError("AUTHORED_TARGET_MAP_ALREADY_EXISTS")
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    start = (checkpoint_number - 1) * 25
    order = selection["selected_logical_work_order_ids"][start:start + 25]
    if len(order) != 25:
        raise RuntimeError("FROZEN_SELECTION_MISMATCH")
    views = {
        value.display_id: value
        for value in (
            TranslationView.model_validate_json(line)
            for line in harness.base.base.VIEWS.read_text(encoding="utf-8").splitlines()
            if line
        )
    }
    if any(item not in views for item in order):
        raise RuntimeError("FROZEN_SOURCE_UNITS_MISSING")
    targets = {item: [segment.text for segment in views[item].segments] for item in order}
    targets.update(replacements)
    for item in order:
        if len(targets[item]) != len(views[item].segments):
            raise RuntimeError(f"TARGET_SEGMENT_COUNT_MISMATCH:{item}")
    payload = {
        "artifact_type": "FULL_BOOK_BATCH_AUTHORED_TARGET_MAP",
        "batch": "FULL_BOOK_BATCH_014",
        "checkpoint": checkpoint,
        "logical_work_orders": order,
        "targets": targets,
    }
    with target_map.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"PASS: authored target map created at {target_map}")


if __name__ == "__main__":
    main()
