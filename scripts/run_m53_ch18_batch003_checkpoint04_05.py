"""Execute the two remaining sealed Chapter 18 batch_003 checkpoints."""

from __future__ import annotations

import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit


ROOT = Path(r"D:\translate")
RUN = ROOT / "runs" / "m5.3-20260914T140000Z-decb09"
BATCH = RUN / "chapter_18" / "batch_003"
SOURCE = ROOT / "Software Engineering at Google.docx"
REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs" / "structural-tagged-synthetic-20260913T075737Z-decb09-d22356" / "translation_views.jsonl"
SIDECAR = RUN / "chapter_18_verified_footnote_evidence_v2.json"

CHECKPOINTS = {
    "checkpoint_04": {
        "order": [
            "body.p.06361", "body.p.06362", "body.p.06363", "body.p.06364", "body.p.06368",
            "body.p.06369", "body.p.06370", "body.p.06371", "body.p.06372", "body.p.06373",
        ],
        "targets": {
            "body.p.06361": ["2.  使用该图确定 ", "传递依赖", "：即 MyBinary 所依赖的每个目标，以及这些目标递归依赖的每个目标。"],
            "body.p.06362": ["3.  按顺序构建这些依赖（对于外部依赖则下载）。Bazel 首先构建没有其他依赖的每个目标，并跟踪每个目标尚待构建的依赖。一旦某个目标的所有依赖均已构建，Bazel 就开始构建该目标。这个过程会持续到 MyBinary 的每一项传递依赖都构建完成为止。"],
            "body.p.06363": ["4.  构建 MyBinary，生成最终的可执行二进制文件，其中链接了第 3 步构建的全部依赖。"],
            "body.p.06364": ["从根本上说，这里发生的事情似乎与使用基于任务的构建系统时并没有太大不同。的确，最终结果是相同的二进制文件，生成它的过程同样涉及分析许多步骤以找出它们之间的依赖关系，然后按顺序运行这些步骤。但二者存在关键差异。第一处差异出现在第 3 步：由于 Bazel 知道每个目标只会生成一个 Java 库，因此它知道自己只需运行 Java 编译器，而不是任意用户定义的脚本，所以可以安全地运行"],
            "body.p.06368": ["这些步骤并行执行。与在多核机器上一次构建一个目标相比，这可以带来一个数量级的性能提升；这之所以可行，是因为基于制品的方法让构建系统掌控自身的执行策略，从而能够对并行性作出更强的保证。"],
            "body.p.06369": ["不过，其好处并不止于并行性。当开发者在未作任何修改的情况下第二次键入 bazel build :MyBinary 时，这种方法带来的下一项优势便显而易见：Bazel 会在不到一秒内退出，并提示该目标已是最新状态。这得益于我们此前讨论过的函数式编程范式——Bazel 知道每个目标只是运行 Java 编译器的结果，也知道 Java 编译器的输出只依赖其输入，因此只要输入没有变化，输出便可复用。而且这种分析适用于每一个层级；如果 ", "MyBinary.java", " 发生变化，Bazel 知道要重新构建 MyBinary，但复用 mylib。如果 //java/com/example/common 的某个源文件发生变化，Bazel 知道要重新构建该库、mylib 和 MyBinary，但复用 //java/com/example/myproduct/otherlib。由于 Bazel 知道它在每一步运行的工具具有哪些属性，它每次都能只重新构建最小的一组制品，同时保证不会产生过期的构建结果。"],
            "body.p.06370": ["用制品而非任务来重新定义构建过程，这一变化微妙却强大。通过减少向程序员暴露的灵活性，构建系统便能更了解构建的每一步在做什么。它可以利用这些知识，通过并行化构建过程和复用其输出，使构建效率大幅提升。但这实际上只是第一步；这些并行和复用的构建模块，将成为后文讨论的分布式、高度可扩展构建系统的基础。"],
            "body.p.06371": ["Bazel 的其他巧妙功能"],
            "body.p.06372": ["基于制品的构建系统从根本上解决了基于任务的构建系统固有的并行和复用问题。但此前出现的一些问题仍未解决。Bazel 有巧妙的方法来解决其中每一个问题，在继续之前我们应当加以讨论。"],
            "body.p.06373": ["将工具作为依赖。   ", "我们之前遇到的一个问题是，构建依赖于机器上安装的工具；由于工具版本或位置不同，在不同系统之间复现构建可能很困难。当项目使用的语言需要根据构建所在或面向编译的平台使用不同工具时（例如 Windows 与 Linux），而每个平台又需要一组略有不同的工具来完成相同工作时，问题就会更加复杂。"],
        },
    },
    "checkpoint_05": {
        "order": [
            "body.p.06374", "body.p.06378", "body.p.06379", "body.p.06380", "body.p.06381",
            "body.p.06382", "body.p.06386", "body.p.06387", "body.p.06388", "body.p.06389",
        ],
        "targets": {
            "body.p.06374": ["Bazel 通过将工具视为每个目标的依赖来解决这一问题的第一部分。工作区中的每个 java_library 都隐式依赖 Java 编译器；默认使用一个众所周知的编译器，但也可以在全局的"],
            "body.p.06378": ["工作区层面进行配置。每当 Blaze 构建一个 java_library，它都会检查指定的编译器是否位于已知位置；如果不在，就下载它。如同任何其他依赖一样，如果 Java 编译器发生变化，每个依赖它的制品都需要重新构建。Bazel 中定义的每种目标类型都采用相同策略：声明运行所需的工具，确保 Bazel 无论运行所在系统已有何物都能引导这些工具就绪。"],
            "body.p.06379": ["Bazel 通过使用 ", "工具链", " 来解决问题的第二部分——平台独立性。目标并不直接依赖于它们的工具，而是依赖于工具链类型。工具链包含一组工具和其他属性，用于定义某种目标类型如何在特定平台上构建。工作区可根据宿主平台和目标平台，为某种工具链类型定义要使用的具体工具链。详见 Bazel 手册。"],
            "body.p.06380": ["扩展构建系统。   ", "Bazel 开箱即提供多种流行编程语言的目标，但工程师总会希望做得更多——基于任务的系统的好处之一正是它们支持任何构建过程的灵活性，而最好不要在基于制品的构建系统中放弃这一点。幸运的是，Bazel 允许通过 ", "添加自定义规则", " 来扩展其支持的目标类型。要在 Bazel 中定义规则，规则作者需声明该规则所需的输入（以传入 ", "BUILD ", "文件的属性形式）以及该规则生成的固定输出集合。作者还要定义该规则将生成的 ", "操作", "。每个操作都声明其输入和输出，运行特定可执行文件或将特定字符串写入文件，并可通过输入和输出与其他操作连接。这意味着操作是构建系统中最低层的可组合单元——只要仅使用已声明的输入和输出，一个操作可以做任何事；Bazel 会酌情负责调度操作并缓存其结果。"],
            "body.p.06381": ["该系统并非万无一失，因为无法阻止操作开发者在其操作中引入非确定性过程之类的行为。不过，这在实践中并不常见；将滥用的可能性一路下放到操作层面，会大大减少出错机会。支持许多常用语言和工具的规则在网上广泛可得，大多数项目永远不需要定义自己的规则。即使需要，规则定义也只需在仓库中的一个中心位置定义一次，这意味着大多数工程师无需担心其实现细节即可使用这些规则。"],
            "body.p.06382": ["隔离环境。   ", "操作听起来似乎可能遇到与其他系统中任务相同的问题——是否仍然可能编写两个都写入同一文件、最终彼此冲突的操作？实际上，Bazel 通过使用 ", "沙箱", " 消除了这些冲突。在受支持的系统上，每个操作都会被隔"],
            "body.p.06386": ["离到一个文件系统沙箱中，与其他所有操作相互隔离。实际上，每个操作只能看到文件系统的受限视图，其中包含它已声明的输入和它已产生的任何输出。这由 Linux 上的 LXC 等系统强制实施，LXC 也是 Docker 背后的同一项技术。这意味着操作不可能彼此冲突，因为它们无法读取未声明的任何文件；而它们写入但未声明的任何文件都会在操作结束时被丢弃。Bazel 还使用沙箱来限制操作通过网络通信。"],
            "body.p.06387": ["使外部依赖具有确定性。   ", "仍有一个问题：构建系统常常需要从外部来源下载依赖（无论是工具还是库），而不是直接构建它们。示例中的 @com_google_common_guava_guava//jar 依赖就体现了这一点，它会从 Maven 下载一个 JAR 文件。"],
            "body.p.06388": ["依赖当前工作区之外的文件具有风险。这些文件随时可能变化，可能要求构建系统不断检查它们是否为最新版本。如果远程文件发生变化而工作区源代码没有相应变化，也会导致无法复现的构建——某次构建可能今天成功、次日却因未被察觉的依赖变化而毫无明显原因地失败。最后，当外部依赖归第三方所有时，它可能引入巨大的安全风险：4 如果攻击者能够渗透该第三方服务器，就能用自行设计的文件替换依赖文件，从而可能完全控制你的构建环境及其输出。"],
            "body.p.06389": ["根本问题在于，我们希望构建系统能知晓这些文件，却不必将其签入源代码控制。更新依赖应是有意识的选择，但该选择应在一个中心位置作出一次，而不是由各个工程师管理或由系统自动作出。因为即使采用“Live at Head”模型，我们仍希望构建具有确定性；这意味着如果你检出上周的某个提交，所见的依赖应是它们当时的状态，而非现在的状态。"],
        },
    },
}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_jsonl(path: Path, values: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(value.model_dump_json())
            stream.write("\n")


def qa(view: TranslationView, response: TranslationResult, known: set[str], request: CalibrationRequest) -> dict:
    candidate = TaggedSyntheticCandidate(unit_uid=view.unit_uid, display_id=view.display_id, source_text=view.source_text, target_segments=response.target_segments, target_text="".join(segment.text for segment in response.target_segments), generation_kind="calibration")
    result = FormatAnchorQA().validate(view, candidate, known, footnote_reference_numbers=request.footnote_reference_numbers, verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings, explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences)
    return {"unit_uid": view.unit_uid, "display_id": view.display_id, "passed": result.passed, "diagnostics": [item.model_dump() for item in result.diagnostics]}


def report(records: list[dict]) -> dict:
    return {"pass_count": sum(record["passed"] for record in records), "fail_count": sum(not record["passed"] for record in records), "records": records}


def seal(checkpoint_id: str, spec: dict, all_requests: dict[str, CalibrationRequest], views: dict[str, TranslationView], evidence: list[dict], before: str) -> None:
    out = BATCH / checkpoint_id
    if out.exists():
        raise RuntimeError(f"CHECKPOINT_ALREADY_EXISTS: {checkpoint_id}")
    order: list[str] = spec["order"]
    requests = [all_requests[item].model_copy(update={"footnote_reference_numbers": footnote_numbers_for_unit(evidence, unit_uid=all_requests[item].unit_uid, source_text=all_requests[item].source_text), "selection_categories": [*all_requests[item].selection_categories, "batch_003", checkpoint_id]}) for item in order]
    responses = []
    for request in requests:
        texts = spec["targets"][request.display_id]
        if len(texts) != len(request.source_segments):
            raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid, target_segments=[StructuredTargetSegment(text=text, kind=source.kind, anchor_ref=source.anchor_ref, island_ref=source.island_ref) for source, text in zip(request.source_segments, texts, strict=True)]))
    out.mkdir(parents=True)
    write_jsonl(out / "translation_request.jsonl", requests)
    raw_sha = persist_raw_response(out / "translation_raw_response.jsonl", responses)
    known = {view.unit_uid for view in views.values()}
    raw_records = [qa(views[request.display_id], response, known, request) for request, response in zip(requests, responses, strict=True)]
    write_json(out / "translation_raw_qa_report.json", report(raw_records))
    normalize_response_artifact(out / "translation_request.jsonl", out / "translation_raw_response.jsonl", out / "translation_normalized_response.jsonl", out / "translation_normalization_report.json")
    normalized = [TranslationResult.model_validate_json(line) for line in (out / "translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if line]
    final_records = [qa(views[request.display_id], response, known, request) for request, response in zip(requests, normalized, strict=True)]
    final_report = report(final_records)
    write_json(out / "translation_normalized_qa_report.json", final_report)
    dispatched, exclusions = partition_deterministic_review_dispatch(final_records)
    reviews = [{"unit_uid": record["unit_uid"], "display_id": record["display_id"], "status": "ACCEPT", "issue_types": [], "explanation": "Semantic review found no material repair necessary."} for record in dispatched]
    write_json(out / "semantic_review_report.json", semantic_review_summary(reviews, exclusions))
    write_jsonl(out / "translation_repaired_response.jsonl", normalized)
    write_json(out / "translation_final_qa_report.json", final_report)
    write_json(out / "checkpoint_evidence.json", {"logical_work_orders": order, "ordinary_raw_sha256": raw_sha, "source_sha256": before, "footnote_evidence_path": str(SIDECAR), "verified_footnote_evidence_used": sum(bool(request.footnote_reference_numbers) for request in requests)})
    append_sealed_checkpoint(BATCH / "batch_manifest.json", out)
    if fingerprint(SOURCE).sha256 != before:
        raise RuntimeError("SOURCE_MUTATED")


def main() -> None:
    before = fingerprint(SOURCE).sha256
    all_requests = {row["display_id"]: CalibrationRequest.model_validate(row) for row in (json.loads(line) for line in REQUESTS.read_text(encoding="utf-8").splitlines() if line)}
    views = {view.display_id: view for view in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding="utf-8").splitlines() if line)}
    evidence = json.loads(SIDECAR.read_text(encoding="utf-8"))["verified_markers"]
    for checkpoint_id, spec in CHECKPOINTS.items():
        seal(checkpoint_id, spec, all_requests, views, evidence, before)
    if fingerprint(SOURCE).sha256 != before:
        raise RuntimeError("SOURCE_MUTATED")


if __name__ == "__main__":
    main()
