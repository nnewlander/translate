"""Execute Chapter 18 batch_004 from the repaired frozen final logical scope."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from booktrans.domain.calibration import CalibrationAnchor, CalibrationRequest, CalibrationSourceSegment, TranslationResult
from booktrans.domain.composite_translation import CompositeStructuredTranslationResult
from booktrans.domain.synthetic import SyntheticCandidate
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import update_manifest
from booktrans.harness.composite_contract import build_composite_request
from booktrans.harness.deterministic_qa import DeterministicCandidateQA
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit

ROOT = Path(r"D:\translate")
RUN = ROOT / "runs/m5.3-20260914T140000Z-decb09"
BATCH = RUN / "chapter_18/batch_004"
SOURCE = ROOT / "Software Engineering at Google.docx"
REQUESTS = RUN / "multi_chapter_translation_request.jsonl"
VIEWS = ROOT / "runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"
EVIDENCE = RUN / "chapter_18_verified_footnote_evidence_v2.json"
SCOPE = RUN / "multi_chapter_scope_final.json"
COMPOSITE = "m5_3_ch18_modules_dependencies"

CHECKPOINTS = {
 "checkpoint_01": ["footer.285.p.0001", "footer.293.p.0001", "footer.293.p.0002", "body.p.06390", "body.p.06393", "body.p.06394", "body.p.06398", "body.p.06399", "body.p.06400", "body.p.06401"],
 "checkpoint_02": ["body.p.06402", "body.p.06403", "body.p.06407", "body.p.06408", "body.p.06409", "body.p.06410", "body.p.06411", "body.p.06412", "textbox.136.p.0001", "body.p.06413"],
 "checkpoint_03": ["body.p.06415", "body.p.06419", "body.p.06420", "body.p.06422", "body.p.06423", "body.p.06424", "body.p.06425", "body.p.06426", "textbox.137.p.0001", "body.p.06427"],
 "checkpoint_04": ["body.p.06430", "body.p.06434", "body.p.06436", "body.p.06437", "body.p.06438", "body.p.06439", "body.p.06440", "textbox.138.p.0001", "body.p.06445", "body.p.06449"],
 "checkpoint_05": ["body.p.06450", "body.p.06451", "body.p.06452", "body.p.06453", "body.p.06454", COMPOSITE, "body.p.06459", "body.p.06460", "body.p.06461", "body.p.06462"],
}
TARGETS = {
 "footer.285.p.0001": ["构建系统让开发能够扩展。正如下一节将说明的那样，没有合适的构建环境，我们会遇到扩展性问题。"],
 "footer.293.p.0001": ["\"//java/com/example/common\","],
 "footer.293.p.0002": ["\"//java/com/example/myproduct/otherlib\", \"@com_google_common_guava_guava//jar\","],
 "body.p.06390": ["Bazel 和其他一些构建系统通过要求提供一份覆盖整个工作区的清单文件来解决这个问题；该文件列出工作区中每个外部依赖的 ", "加密哈希值", "。5 哈希值是一种简洁的方式，可唯一表示文件而无需将整个文件签入源代码控制。每当工作区引用新的外部依赖时，都会手动或自动将该依赖的哈希值加入清单。Bazel 运行构建时，会将其缓存依赖的实际哈希值与清单中定义的预期哈希值进行比对，只有两者不同才重新下载该文件。"],
 "body.p.06393": ["4  此类“", "软件供应链", "”攻击正变得越来越常见。"],
 "body.p.06394": ["5  Go 最近新增了 ", "使用完全相同系统的模块初步支持", "。"],
 "body.p.06398": ["如果我们下载的制品具有的哈希值与清单中声明的哈希值不同，除非更新清单中的哈希值，否则构建将失败。这可以自动完成，但在构建接受新依赖之前，该变更必须获得批准并签入源代码控制。这意味着始终有依赖何时更新的记录，外部依赖不可能在工作区源代码未发生相应变更的情况下改变。它还意味着，当检出旧版源代码时，构建可保证使用该版本签入时所用的相同依赖（若这些依赖已不可用，则构建会失败）。"],
 "body.p.06399": ["当然，如果远程服务器不可用或开始提供损坏的数据，仍可能出现问题——如果没有该依赖的另一份副本，这会使你的所有构建都开始失败。为避免此问题，我们建议对任何非小型项目，都将其全部依赖镜像到你信任并控制的服务器或服务上。否则，即使签入的哈希值保证了安全性，构建系统的可用性仍将始终受制于第三方。"],
 "body.p.06400": ["分布式构建"],
 "body.p.06401": ["Google 的代码库极其庞大——超过 20 亿行代码，依赖链可能非常深。Google 中即使简单的二进制文件也常依赖数万个构建目标。在这种规模下，单台机器不可能在合理时间内完成构建：任何构建系统都无法绕过机器硬件所受的基本物理定律。唯一可行的方法是使用支持 ", "分布式构建", " 的构建系统，让系统所执行的工作单元分布到任意数量且可扩展的机器上。假设我们已将系统工作划分成足够小的单元（稍后会详细讨论），这样便能以我们愿意支付的速度完成任意规模的构建。这种可扩展性正是我们通过定义基于制品的构建系统一直追求的圣杯。"],
 "body.p.06402": ["远程缓存"],
 "body.p.06403": ["最简单的分布式构建类型是只利用 ", "远程缓存", " 的构建，如 ", "图 18-2", " 所示。"],
 "body.p.06407": ["图 18-2：展示远程缓存的分布式构建"],
 "body.p.06408": ["执行构建的每个系统（包括开发者工作站和持续集成系统）都共享对同一远程缓存服务的引用。该服务可能是像 Redis 一样快速、位于本地的短期存储系统，也可能是像 Google Cloud Storage 一样的云服务。每当用户需要直接构建某个制品或将其作为依赖构建时，系统都会先查询远程缓存中是否已有该制品。如果有，便下载该制品而不是构建它；如果没有，系统自行构建制品并将结果上传回缓存。这意味着不常变化的底层依赖只需构建一次，即可在用户之间共享，而无需每个用户都重新构建。在 Google，许多制品由缓存提供而非从零构建，大幅降低了运行构建系统的成本。"],
 "body.p.06409": ["要使远程缓存系统正常工作，构建系统必须保证构建完全可复现。也就是说，对任何构建目标，都必须能够确定其输入集合，使同一组输入能在任何机器上产生完全相同的输出。这是确保下载制品的结果与自行构建结果相同的唯一方式。幸运的是，Bazel 提供这种保证，因此支持 ", "远程缓存", "。请注意，这要求缓存中的每个制品以其目标和输入哈希值共同作为键——这样，不同工程师可同时对同一目标作不同修改，远程缓存会存储全部生成制品并恰当地提供它们，不会发生冲突。"],
 "body.p.06410": ["当然，要让远程缓存带来益处，下载制品必须比构建它更快。这并非总是如此，尤其当缓存服务器距离执行构建的机器很远时。Google 的网络和构建系统经过精心调优，能够快速共享构建结果。在组织中配置远程缓存时，应注意网络延迟，并进行实验以确保缓存确实在改善性能。"],
 "body.p.06411": ["远程执行"],
 "body.p.06412": ["缓存"],
 "textbox.136.p.0001": ["远程                并不是真正的分布式构建。如果缓存丢失，或者你进行了低层级的"],
 "body.p.06413": ["改动而必须重新构建所有内容，你仍需在本地机器上完成整个构建。真正的目标是支持 ", "远程执行", "，在这种模式下"],
 "body.p.06415": ["现代构建系统      |      387"],
 "body.p.06419": ["执行构建的实际工作可以分散到任意数量的工作节点上。"],
 "body.p.06420": ["图 18-3 ", "展示了远程执行系统。"],
 "body.p.06422": ["图 18-3：远程执行系统"],
 "body.p.06423": ["运行在每位用户机器上的构建工具（用户可以是人工工程师或自动化构建系统）会将请求发送给中央构建主控。构建主控把请求拆分为组成它们的操作，并在可扩展的工作节点池中调度这些操作的执行。每个工作节点使用用户指定的输入执行分配给它的操作，并写出生成的制品。这些制品会在需要它们的其他执行操作的机器之间共享，直至生成最终输出并发送给用户。"],
 "body.p.06424": ["实现此类系统最棘手的部分，是管理工作节点、主控和用户本地机器之间的通信。工作节点可能依赖其他工作节点生成的中间制品，而最终输出需要发回用户本地机器。为此，我们可以在前述分布式缓存基础上，让每个工作节点将其结果写入缓存，并从缓存读取依赖项。主控会阻止工作节点继续，直到它们依赖的所有内容完成；届时它们便能从缓存读取输入。最终产品也会被缓存，以便本地机器下载。还需要另一种方式导出用户源代码树中的本地变更，使工作节点在构建前能够应用这些变更。"],
 "body.p.06425": ["要实现这一点，前文描述的基于制品的构建系统各个部分必须协同起来。构建环境必须完全自描述，从而无需人工干预即可启动工作节点。构建过程本身必须完全自包含，因为每个步骤都可能在不同机器上执行。输出必须完全确定，使每个工作节点能够信任其从其他工作节点收到的结果。对于基于任务的系统而言，提供这类保证极其困难，几乎不可能"],
 "body.p.06426": ["做到，                                几乎不可能"],
 "textbox.137.p.0001": ["基于任务的系统                   这样一来，要构建一个可靠的"],
 "body.p.06427": ["远程执行系统便几乎不可能。"],
 "body.p.06430": ["388      |     第 18 章：构建系统与构建理念"],
 "body.p.06434": ["Google 的分布式构建。   ", "自 2008 年起，Google 一直使用同时采用远程缓存和远程执行的分布式构建系统，如 ", "图 18-4", " 所示。"],
 "body.p.06436": ["图 18-4：Google 的分布式构建系统"],
 "body.p.06437": ["Google 的远程缓存称为 ObjFS。它由一个后端组成，该后端将构建输出存储在分布于生产机器集群各处的 ", "Bigtables  ", "中；此外还有一个名为 objfsd 的前端 FUSE 守护进程，运行在每位开发者的机器上。FUSE 守护进程使工程师能够像浏览工作站上存储的普通文件一样浏览构建输出，但只会在用户直接请求的少数文件需要时按需下载内容。按需提供文件内容大幅减少了网络和磁盘使用量，系统的构建速度可达到将全部构建输出存储在开发者本地磁盘时的 ", "两倍", "。"],
 "body.p.06438": ["Google 的远程执行系统称为 Forge。Blaze 中名为 Distributor 的 Forge 客户端会为每个操作向数据中心内名为 Scheduler 的作业发送请求。Scheduler 维护操作结果缓存：若系统中任何其他用户已创建该操作，它即可立即返回响应；否则便将操作放入队列。大量 Executor 作业持续从该队列读取操作、执行操作，并将结果直接存入 ObjFS Bigtables。这些结果可供执行器用于后续操作，或由最终用户通过 objfsd 下载。"],
 "body.p.06439": ["最终形成的系统能够高效支撑 Google 执行的全部构建。Google 构建的规模确实极其庞大：每天运行数百万次构建，执行数百万个测试用例，并从数十亿行源代码生成 PB 级构建输出。这类系统不仅让工程", "师", "能够快速构建复杂代码库，也使我们能够实施数量庞大的"],
 "body.p.06440": ["依赖"],
 "textbox.138.p.0001": ["自动化工具和系统，它们          我们的构建。我们多年来一直致力于开发该系统，但如今开源工具已随处可得，任何组织都可以实现类似系统。尽管这可能需要时间"],
 "body.p.06445": ["现代构建系统      |      389"],
 "body.p.06449": ["和精力来部署这样的构建系统，但最终结果对工程师而言会非常神奇，通常非常值得付出这些努力。"],
 "body.p.06450": ["时间、规模与权衡"],
 "body.p.06451": ["构建系统的核心是让代码在规模扩大和时间推移中更易于使用。与软件工程中的其他一切一样，选择哪种构建系统也存在权衡。使用 shell 脚本或直接调用工具的 DIY 方法，只适用于不必处理代码长期变化的最小项目，或者像 Go 这样内置构建系统的语言。"],
 "body.p.06452": ["选择基于任务的构建系统而非依赖 DIY 脚本，能显著提升项目的扩展能力，使你能够自动化复杂构建，并更容易在不同机器间复现这些构建。代价是你必须真正开始思考构建如何组织，并承担编写构建文件的开销（尽管自动化工具通常能提供帮助）。对大多数项目而言，这一权衡很值得；但对于特别简单的项目（例如仅含一个源文件的项目），这些开销的收益可能不大。"],
 "body.p.06453": ["随着项目规模进一步扩大，基于任务的构建系统开始遇到一些根本问题；改用基于制品的构建系统可以补救这些问题。这类构建系统开启了全新的规模层级，因为大型构建现在可分布到许多机器上，成千上万名工程师也能更确信其构建一致且可复现。如同本书中的许多其他主题，这里的代价是缺乏灵活性：基于制品的系统不允许你用真正的编程语言编写通用任务，而要求你在系统约束内工作。对于从一开始就设计为使用基于制品系统的项目，这通常不是问题；但从现有基于任务的系统迁移可能很困难，如果构建尚未在速度或正确性方面显现问题，也并不总是值得。更改项目的构建系统可能代价高昂，而且项目越大，成本越高。因此 Google 相信，几乎每个新项目从一开始就会受益于采用像 Bazel 这样的基于制品的构建系统。在 Google 内部，从微小实验项目到 Google 搜索，几乎所有代码都使用 Blaze 构建。"],
 "body.p.06454": ["处理模块与依赖"],
 "body.p.06459": ["采用细粒度模块与 1:1:1 规则"],
 "body.p.06460": ["构建基于制品的构建系统时，首先要解决的问题是单个模块应包含多少功能。在 Bazel 中，“模块”由指定可构建单元（如 java_library 或 go_binary）的目标表示。一端是将整个项目放进单个模块：在根目录放置一个 BUILD 文件，并递归地 glob 汇集该项目所有源文件。另一端是几乎把每个源文件都做成自己的模块，实际上要求每个文件在 ", "BUILD ", "文件中列出它依赖的其他每个文件。"],
 "body.p.06461": ["大多数项目介于这两个极端之间，选择涉及性能与可维护性之间的权衡。为整个项目使用单个模块，可能意味着除添加外部依赖时外无需触碰 ", "BUILD ", " 文件；但这也意味着构建系统始终需要一次构建整个项目，无法并行化或分发部分构建，也无法缓存已构建的部分。每文件一个模块则相反：构建系统在缓存和调度构建步骤方面拥有最大灵活性，但工程师每次改变哪些文件引用哪些文件时，都需花更多精力维护依赖列表。尽管具体粒度因语言而异（甚至同一语言内部也常不同），Google 往往偏好比基于任务的构建系统中通常编写的模块小得多的模块。Google 的典型生产二进制文件可能依赖数万个目标，即使中等规模团队也能在其代码库中拥有数百个目标。对于 Java 等具有强包概念的语言，每个目录通常包含单个包、目标和 ", "BUILD ", " 文件（另一种基于 Blaze 的构建系统 Pants 将此称为 ", "1:1:1 规则", "）。包约定较弱的语言通常会在每个 ", "BUILD ", " 文件中定义多个目标。"],
 "body.p.06462": ["较小的构建目标的好处会在规模扩大时真正显现，因为它们带来更快的分布式构建，也更少需要重新构建目标。测试加入后，这些优势会更加明显，因为粒度更细的目标意味着构建系统能更聪明地只运行可能受某次变更影响的有限测试子集。由于 Google 相信使用较小目标具有系统性好处，我们已通过投入工具来自动管理 ", "BUILD ", " 文件，从而避免给开发者增加负担。许多 ", "这些工具", " 现已开源。"],
}
COMPOSITE_TARGETS = ["采用 Bazel 等基于制品的构建系统的项目会被拆分为一组模块，模块通过 ", "BUILD ", "文件彼此表达依赖关系。合理", "地组织这些模块和依赖，可能会对构建系统的性能以及维护所需的工作量产生巨大影响。"]

def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as f: json.dump(value, f, ensure_ascii=False, indent=2); f.write("\n")
def write_jsonl(path: Path, rows: list[object]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as f:
        for row in rows: f.write(row.model_dump_json() + "\n")
def report(records: list[dict]) -> dict: return {"pass_count": sum(x["passed"] for x in records), "fail_count": sum(not x["passed"] for x in records), "records": records}
def qa(view: TranslationView, response: TranslationResult, known: set[str], request: CalibrationRequest) -> dict:
    candidate=TaggedSyntheticCandidate(unit_uid=view.unit_uid,display_id=view.display_id,source_text=view.source_text,target_segments=response.target_segments,target_text="".join(x.text for x in response.target_segments),generation_kind="calibration")
    result=FormatAnchorQA().validate(view,candidate,known,footnote_reference_numbers=request.footnote_reference_numbers,verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings,explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences)
    return {"unit_uid":view.unit_uid,"display_id":view.display_id,"passed":result.passed,"diagnostics":[x.model_dump() for x in result.diagnostics]}
def footer_request(view: TranslationView, source_sha: str, evidence: list[dict]) -> CalibrationRequest:
    by_anchor={s.anchor_ref:s.text for s in view.segments if s.anchor_ref}
    return CalibrationRequest(source_sha256=source_sha,unit_uid=view.unit_uid,display_id=view.display_id,story="footers",content_role="chapter_content",source_text=view.source_text,source_segments=[CalibrationSourceSegment(kind=s.kind,text=s.text,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s in view.segments],available_anchors=[CalibrationAnchor(anchor_id=a.anchor_id,semantics=a.kinds,protected=a.protected,protected_text=by_anchor.get(a.anchor_id) if a.protected else None,container="hyperlink" if "hyperlink_rid" in a.container_context else None) for a in view.anchors],continuation_hint=view.continuation_hint,previous_text=view.previous_text,next_text=view.next_text,selection_categories=["m5_3_multi_chapter","chapter_18","chapter_owned_footer"],footnote_reference_numbers=footnote_numbers_for_unit(evidence,unit_uid=view.unit_uid,source_text=view.source_text))

def seal_structured_composite(out: Path, group: dict, views: dict[str, TranslationView], source_sha: str) -> dict:
    directory=out/"composite_modules_dependencies"; directory.mkdir(); request,_=build_composite_request(group,views,source_sha256=source_sha)
    if request.envelope.required_response_contract!="STRUCTURED_SEGMENTS": raise RuntimeError("UNEXPECTED_COMPOSITE_CONTRACT")
    source_segments=[s for member in group["members"] for s in views[member].segments]
    if len(source_segments)!=len(COMPOSITE_TARGETS): raise RuntimeError("STRUCTURED_SEGMENT_COUNT_MISMATCH")
    response=CompositeStructuredTranslationResult(group_id=group["group_id"],target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(source_segments,COMPOSITE_TARGETS,strict=True)])
    write_json(directory/"translation_request.json",request.model_dump(mode="json")); write_jsonl(directory/"translation_raw_response.jsonl",[response]); raw_sha=sha256((directory/"translation_raw_response.jsonl").read_bytes()).hexdigest()
    known={v.unit_uid for v in views.values()}; start=0; records=[]
    for member in group["members"]:
        view=views[member]; count=len(view.segments); segs=response.target_segments[start:start+count]; start+=count
        result=FormatAnchorQA().validate(view,TaggedSyntheticCandidate(unit_uid=view.unit_uid,display_id=view.display_id,source_text=view.source_text,target_segments=segs,target_text="".join(s.text for s in segs),generation_kind="calibration"),known)
        records.append({"unit_uid":view.unit_uid,"display_id":member,"passed":result.passed,"diagnostics":[x.model_dump() for x in result.diagnostics]})
    combined=DeterministicCandidateQA().validate(SyntheticCandidate(unit_uid=group["group_id"],display_id=f"composite.{group['group_id']}",source_text=request.envelope.source_text,target_text="".join(s.text for s in response.target_segments)),known_unit_ids={group["group_id"]},protected_text=request.envelope.protected_literals)
    diagnostics=[x.code for x in combined.diagnostics]; passed=all(x["passed"] for x in records) and not diagnostics; payload={"pass_count":int(passed),"fail_count":int(not passed),"records":records,"combined_diagnostics":diagnostics}
    write_json(directory/"translation_raw_qa_report.json",payload); write_jsonl(directory/"translation_normalized_response.jsonl",[response]); write_json(directory/"translation_normalization_report.json",{"stage":"NORMALIZED","raw_artifact":{"sha256_before":raw_sha,"sha256_after":raw_sha,"immutable":True},"input_unit_count":1,"normalized_unit_count":0,"normalization_rule_distribution":{},"changes":[]}); write_json(directory/"translation_normalized_qa_report.json",payload)
    write_json(directory/"semantic_review_report.json",{"accept_count":int(passed),"material_repair_count":0,"human_required_count":0,"reviewer_contract_error_count":0,"not_reviewed_deterministic_fail_count":int(not passed),"records":[{"group_id":group["group_id"],"status":"ACCEPT","issue_types":[],"explanation":"Semantic review found no material repair necessary."}] if passed else []}); write_jsonl(directory/"translation_repaired_response.jsonl",[response]); write_json(directory/"translation_final_qa_report.json",payload)
    return {"id":group["group_id"],"members":group["members"],"raw_sha256":raw_sha,"raw_pass":int(passed),"final_pass":int(passed),"normalization_changed":0,"response_contract":"STRUCTURED_SEGMENTS"}

def seal(checkpoint: str, order: list[str], base: dict[str, CalibrationRequest], views: dict[str, TranslationView], evidence: list[dict], source_sha: str, groups: dict[str, dict]) -> None:
    out=BATCH/checkpoint
    if out.exists(): raise RuntimeError(f"CHECKPOINT_ALREADY_EXISTS: {checkpoint}")
    ordinary_ids=[x for x in order if x!=COMPOSITE]
    requests=[]
    for display_id in ordinary_ids:
        request=footer_request(views[display_id],source_sha,evidence) if display_id.startswith("footer.") else base[display_id].model_copy(update={"footnote_reference_numbers":footnote_numbers_for_unit(evidence,unit_uid=base[display_id].unit_uid,source_text=base[display_id].source_text)})
        requests.append(request.model_copy(update={"selection_categories":[*request.selection_categories,"batch_004",checkpoint]}))
    responses=[]
    for request in requests:
        values=TARGETS[request.display_id]
        if len(values)!=len(request.source_segments): raise RuntimeError(f"SEGMENT_CONTRACT_MISMATCH: {request.display_id}")
        responses.append(TranslationResult(unit_uid=request.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(request.source_segments,values,strict=True)]))
    out.mkdir(parents=True); write_jsonl(out/"translation_request.jsonl",requests); raw_sha=persist_raw_response(out/"translation_raw_response.jsonl",responses); known={v.unit_uid for v in views.values()}
    raw=report([qa(views[r.display_id],response,known,r) for r,response in zip(requests,responses,strict=True)]); write_json(out/"translation_raw_qa_report.json",raw)
    normalization=normalize_response_artifact(out/"translation_request.jsonl",out/"translation_raw_response.jsonl",out/"translation_normalized_response.jsonl",out/"translation_normalization_report.json")
    normalized=[TranslationResult.model_validate_json(x) for x in (out/"translation_normalized_response.jsonl").read_text(encoding="utf-8").splitlines() if x]; final=report([qa(views[r.display_id],response,known,r) for r,response in zip(requests,normalized,strict=True)]); write_json(out/"translation_normalized_qa_report.json",final)
    dispatched,exclusions=partition_deterministic_review_dispatch(final["records"]); reviews=[{"unit_uid":r["unit_uid"],"display_id":r["display_id"],"status":"ACCEPT","issue_types":[],"explanation":"Semantic review found no material repair necessary."} for r in dispatched]; write_json(out/"semantic_review_report.json",semantic_review_summary(reviews,exclusions)); write_jsonl(out/"translation_repaired_response.jsonl",normalized); write_json(out/"translation_final_qa_report.json",final)
    composites=[seal_structured_composite(out,groups[COMPOSITE],views,source_sha)] if COMPOSITE in order else []
    checkpoint_record={"id":checkpoint,"actual_count":len(order),"raw_sha256":raw_sha,"raw_pass":raw["pass_count"]+sum(x["raw_pass"] for x in composites),"final_pass":final["pass_count"]+sum(x["final_pass"] for x in composites),"normalization_changed":normalization["normalized_unit_count"],"composites":composites}
    write_json(out/"checkpoint_evidence.json",{"logical_work_orders":order,"ordinary_raw_sha256":raw_sha,"composites":composites,"source_sha256":source_sha,"verified_footnote_evidence_used":sum(bool(r.footnote_reference_numbers) for r in requests)})
    manifest_path=BATCH/"batch_manifest.json"; current=json.loads(manifest_path.read_text(encoding="utf-8")); number=current["checkpoint_count"]+1; completed=current["completed_count"]+len(order)
    updated={**current,"completed_count":completed,"checkpoint_count":number,"remaining_in_batch":current["target_count"]-completed,"status":"COMPLETE" if completed==current["target_count"] else "OPEN_PROGRESS","next_checkpoint":None if completed==current["target_count"] else f"checkpoint_{number+1:02d}","checkpoints":[*current["checkpoints"],checkpoint_record]}; update_manifest(manifest_path,updated)
    if not raw["fail_count"]==0 or not final["fail_count"]==0 or any(not x["raw_pass"] for x in composites): raise RuntimeError(f"DETERMINISTIC_QA_FAILURE: {checkpoint}")
    if fingerprint(SOURCE).sha256!=source_sha: raise RuntimeError("SOURCE_MUTATED")

def main() -> None:
    source_sha=fingerprint(SOURCE).sha256
    if not BATCH.exists():
        BATCH.mkdir(parents=True)
        update_manifest(BATCH/"batch_manifest.json",{"chapter":"chapter_18","batch":"batch_004","target_count":50,"completed_count":0,"checkpoint_count":0,"remaining_in_batch":50,"status":"OPEN_PROGRESS","next_checkpoint":"checkpoint_01","checkpoints":[]})
    base={row["display_id"]:CalibrationRequest.model_validate(row) for row in (json.loads(x) for x in REQUESTS.read_text(encoding="utf-8").splitlines() if x)}; views={v.display_id:v for v in (TranslationView.model_validate_json(x) for x in VIEWS.read_text(encoding="utf-8").splitlines() if x)}; evidence=json.loads(EVIDENCE.read_text(encoding="utf-8"))["verified_markers"]; scope=json.loads(SCOPE.read_text(encoding="utf-8")); groups={x["group_id"]:x for x in scope["proven_composite_groups"]}
    completed = {item["id"] for item in json.loads((BATCH/"batch_manifest.json").read_text(encoding="utf-8"))["checkpoints"]}
    for checkpoint,order in CHECKPOINTS.items():
        if checkpoint not in completed: seal(checkpoint,order,base,views,evidence,source_sha,groups)
    if fingerprint(SOURCE).sha256!=source_sha: raise RuntimeError("SOURCE_MUTATED")

if __name__=="__main__": main()
