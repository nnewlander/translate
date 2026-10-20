"""Execute all remaining Chapter 18 final-selector work orders."""
from __future__ import annotations
import json
from pathlib import Path
from booktrans.domain.calibration import CalibrationRequest, TranslationResult
from booktrans.domain.translation_view import StructuredTargetSegment, TaggedSyntheticCandidate, TranslationView
from booktrans.harness.batch_manifest import append_sealed_checkpoint, update_manifest
from booktrans.harness.semantic_review import partition_deterministic_review_dispatch, semantic_review_summary
from booktrans.harness.source_integrity import fingerprint
from booktrans.harness.tagged_roundtrip import FormatAnchorQA
from booktrans.harness.translation_artifacts import normalize_response_artifact, persist_raw_response
from booktrans.ooxml.verified_footnotes import footnote_numbers_for_unit

ROOT=Path(r"D:\translate"); RUN=ROOT/"runs/m5.3-20260914T140000Z-decb09"; BATCH=RUN/"chapter_18/batch_005"; SOURCE=ROOT/"Software Engineering at Google.docx"; REQUESTS=RUN/"multi_chapter_translation_request.jsonl"; VIEWS=ROOT/"runs/structural-tagged-synthetic-20260913T075737Z-decb09-d22356/translation_views.jsonl"; EVIDENCE=RUN/"chapter_18_verified_footnote_evidence_v2.json"; SELECTOR=RUN/"chapter_18_final_logical_work_order_selector.json"
CHECKPOINTS={
 "checkpoint_01":["body.p.06466","body.p.06467","body.p.06468","body.p.06469","body.p.06470","body.p.06471","body.p.06472","body.p.06473","body.p.06475","body.p.06479"],
 "checkpoint_02":["body.p.06480","body.p.06481","body.p.06482","body.p.06483","body.p.06487","body.p.06488","body.p.06492","body.p.06493","body.p.06494","body.p.06495"],
 "checkpoint_03":["body.p.06496","body.p.06497","body.p.06501","body.p.06502","body.p.06503","body.p.06504","textbox.139.p.0001","body.p.06505","body.p.06507","body.p.06511"],
 "checkpoint_04":["body.p.06512","body.p.06513","body.p.06514","body.p.06515","body.p.06519","body.p.06520","body.p.06521","body.p.06522","body.p.06523","body.p.06524"],
 "checkpoint_05":["body.p.06525","body.p.06529","body.p.06530","body.p.06531"],
}
TARGETS={
"body.p.06466":["最小化模块可见性"],
"body.p.06467":["Bazel 和其他构建系统允许每个目标指定可见性：即指定哪些其他目标可以依赖它的属性。目标可以是公开的，此时工作区中的任何其他目标都可引用它；也可以是私有的，此时只能在同一个 ","BUILD ","文件内引用；还可以仅对明确列出的其他目标可见。可见性本质上与依赖相反：如果目标 A 想依赖目标 B，目标 B 必须让自己对目标 A 可见。"],
"body.p.06468":["和大多数编程语言一样，通常最好尽可能缩小可见性。一般而言，Google 的团队只会在目标代表 Google 中任意团队都可使用的广泛使用库时将其公开。要求其他人在使用其代码前与之协调的团队，会维护一份客户目标白名单作为其目标的可见性。每个团队的内部实现目标只会限制在该团队拥有的目录内，而大多数 ","BUILD ","文件中只有一个非私有目标。"],
"body.p.06469":["管理依赖"],
"body.p.06470":["模块需要能够彼此引用。将代码库拆分为细粒度模块的缺点是，你需要管理这些模块之间的依赖关系（尽管工具可以帮助自动化这一过程）。表达这些依赖通常会占据 ","BUILD ","文件内容的大部分。"],
"body.p.06471":["内部依赖"],
"body.p.06472":["在拆分为细粒度模块的大型项目中，大多数依赖很可能是内部依赖；即依赖同一源代码仓库中定义和构建的另一个目标。内部依赖与外部依赖的不同之处在于，它们在运行构建时从源代码构建，而不是下载预构建制品。这也意味着内部依赖没有“版本”的概念——一个目标及其全部内部依赖始终在仓库中的同一提交/修订版本构建。"],
"body.p.06473":["对于内部依赖，需要谨慎处理的一个问题是如何对待 ","传递依赖","（","图 18-5","）。假设目标 A 依赖目标 B，而目标 B 依赖一个公共库目标 C。目标 A 是否应能使用目标 C 中定义的类？"],
"body.p.06475":["图 18-5：传递依赖"],
"body.p.06479":["就底层工具而言，这没有问题；构建目标 A 时，B 和 C 都会链接到 A，因此 C 中定义的任何符号对 A 都是已知的。Blaze 多年来一直允许这样做，但随着 Google 规模增长，我们开始看到问题。假设 B 被重构，不再需要依赖 C。若随后移除 B 对 C 的依赖，A 以及任何通过依赖 B 使用 C 的其他目标都会中断。实际上，一个目标的依赖成了其公共契约的一部分，永远无法安全变更。这意味着依赖会随时间累积，Google 的构建开始变慢。"],
"body.p.06480":["Google 最终通过在 Blaze 中引入“严格传递依赖模式”解决了这个问题。在此模式中，Blaze 会检测目标是否试图引用某个符号却未直接依赖它；如果是，便以错误和一条可自动插入该依赖的 shell 命令失败。将这项变更推广到 Google 的整个代码库，并重构数百万个构建目标以显式列出依赖，是一项历时多年的工作，但非常值得。现在，由于目标拥有更少不必要的依赖，我们的构建快得多，","6","工程师也可以放心移除不需要的依赖，而不必担心破坏依赖它们的目标。"],
"body.p.06481":["和往常一样，实施严格传递依赖涉及权衡。它使构建文件更加冗长，因为常用库现在需要在许多地方显式列出，而非顺带引入；工程师也需要花更多精力向 ","BUILD ","文件添加依赖。此后我们开发了工具，可自动检测许多缺失依赖并将其加入 ","BUILD ","文件，无需开发者干预。但即使没有这些工具，随着代码库扩展，我们也发现这一权衡非常值得：向 ","BUILD  ","文件显式添加依赖是一次性成本，而处理隐式传递依赖会在构建目标存在的整个期间持续造成问题。","Bazel 默认对 Java 代码实施严格传递依赖","。"],
"body.p.06482":["外部依赖"],
"body.p.06483":["如果依赖不是内部的，它就一定是外部的。外部依赖是指在构建系统之外构建和存储的制品上的依赖。该依赖会直接从 ","制品仓库","（通常通过互联网访问）导入并按原样使用，而不是从源代码构建。外部依赖与内部依赖的最大区别之一是，外部依赖具有","版本","，且这些版本独立于项目的源代码而存在。"],
"body.p.06487":["6  当然，实际","移除","这些依赖是完全独立的过程。但要求每个目标"],
"body.p.06488":["显式声明它所使用的内容，是关键的第一步。有关 Google 如何进行此类大规模变更的更多信息，请参见","第 22 章","。"],
"body.p.06492":["自动与手动依赖管理。   ","构建系统可允许手动或自动管理外部依赖的版本。手动管理时，构建文件会显式列出希望从制品仓库下载的版本，通常使用","语义版本字符串","，例如“1.1.4”。自动管理时，源文件会指定可接受版本范围，构建系统始终下载最新版本。例如，Gradle 允许将依赖版本声明为“1.+”，表示只要主版本为 1，任何次版本或补丁版本都可接受。"],
"body.p.06493":["自动管理的依赖对小型项目可能很方便，但对于规模不小或有多名工程师参与的项目，通常是灾难的根源。自动管理依赖的问题在于，你无法控制版本何时更新。无法保证外部方不会进行破坏性更新（即便它们声称使用语义化版本），因此某天可工作的构建次日可能坏掉，却没有简便方式确定发生了什么或回滚到可工作状态。即使构建没有中断，也可能出现难以追查的微妙行为或性能变化。"],
"body.p.06494":["相比之下，由于手动管理的依赖需要修改源代码控制，因此可以轻松发现和回滚，也可以检出仓库旧版本以使用旧依赖进行构建。Bazel 要求手动指定所有依赖的版本。即使在中等规模下，手动版本管理所带来的稳定性也完全值得其开销。"],
"body.p.06495":["单版本规则。   ","库的不同版本通常由不同制品表示，因此理论上没有理由不能在构建系统中以不同名称同时声明同一外部依赖的不同版本。这样，每个目标都可以选择想使用的依赖版本。Google 发现这在实践中会造成很多问题，因此我们在内部代码库中对所有第三方依赖实施严格的 ","单版本规则","。"],
"body.p.06496":["允许多个版本的最大问题是","菱形依赖","问题。假设目标 A 依赖目标 B 和某个外部库的 v1 版本。如果之后重构目标 B，使其添加对同一外部库 v2 版本的依赖，目标 A 就会中断，因为它现在隐式依赖同一库的两个不同版本。实际上，向具有多个版本的任何第三方库添加新依赖从来都不安全，因为该目标的某个使用者可能已依赖不同版本。遵循单版本规则可使此冲突不可能发生——如果目标添加对第三方库的依赖，任何现有依赖已经处于同一版本，因此可以愉快共存。"],
"body.p.06497":["我们将在第 ","21 章","的一个大型单体仓库背景下进一步考察此问题。"],
"body.p.06501":["传递性外部依赖。   ","处理外部依赖的传递依赖可能特别困难。许多制品仓库（如 Maven Central）允许制品指定对仓库中其他制品特定版本的依赖。Maven 或 Gradle 等构建工具通常默认递归下载每个传递依赖，这意味着在项目中添加一个依赖，可能总共下载数十个制品。这非常方便：添加对新库的依赖时，若必须找出该库的每项传递依赖并全部手动添加，将会非常痛苦。但也有巨大缺点：不同库可能依赖同一第三方库的不同版本，这种策略必然违反单版本规则并导致菱形依赖问题。如果目标依赖两个使用同一依赖不同版本的外部库，便无法确定最终会得到哪一个。这也意味着，若新版本开始引入其某些依赖的冲突版本，更新外部依赖可能在整个代码库中造成看似无关的失败。"],
"body.p.06502":["因此，Bazel 不会自动下载传递依赖。遗憾的是，没有灵丹妙药——Bazel 的替代方案是要求一个全局文件列出仓库的每一个外部依赖，以及该依赖在整个仓库中使用的明确版本。幸运的是，","Bazel 提供工具","，能自动生成包含一组 Maven 制品传递依赖的此类文件。此工具可运行一次来生成项目初始的","WORKSPACE ","文件，随后可手动更新该文件以调整各依赖的版本。"],
"body.p.06503":["再一次，这里的选择是在便利性和可扩展性之间。小型项目可能更愿意不必自行管理传递依赖，也许能够使用自动传递依赖。这一策略随着组织和代码库成长而越来越不吸引人，冲突和意外结果会越来越频繁。在更大规模下，手动管理依赖的成本远低于处理自动依赖管理所致问题的成本。"],
"body.p.06504":["使用外部依赖缓存构建结果。    ","外部依赖最常由发布稳定库版本的第三方提供，也许并不提供源代码。一些组织也可能选择将自己的部分代码作为制品提供，使其他代码可将其作为第三方依赖而非内部依赖。若制品构建慢而下载快，这在理论上可加速构建"],
"textbox.139.p.0001":["。                                                                 。"],
"body.p.06505":["不过，这也带来大量开销和复杂性：必须有人负责构建每个此类制品并将其上传到制品"],
"body.p.06507":["处理模块与依赖      |      395"],
"body.p.06511":["仓库，而客户端需要确保自己始终使用最新版本。调试也会困难得多，因为系统的不同部分将在仓库的不同时间点构建，源代码树不再具有一致视图。"],
"body.p.06512":["解决制品构建耗时长问题的更好方式，是使用如前所述支持远程缓存的构建系统。这样的构建系统会将每次构建产生的制品保存到工程师之间共享的位置，因此若开发者依赖某个最近由他人构建的制品，构建系统会自动下载它而不是重新构建。这样既能获得直接依赖制品的全部性能优势，又能确保构建与始终从同一源代码构建时一样一致。这是 Google 内部采用的策略，Bazel 也可配置为使用远程缓存。"],
"body.p.06513":["外部依赖的安全性与可靠性。     ","依赖第三方来源的制品本身具有风险。如果第三方来源（例如制品仓库）宕机，会带来可用性风险，因为无法下载外部依赖时，整个构建可能完全停顿。还存在安全风险：如果攻击者攻破第三方系统，就可以用自己设计的制品替换所引用的制品，从而将任意代码注入你的构建。"],
"body.p.06514":["这两个问题都可通过将所依赖的任何制品镜像到你控制的服务器，并阻止构建系统访问 Maven Central 等第三方制品仓库来缓解。代价是维护这些镜像需要精力和资源，因此是否使用它们通常取决于项目规模。还可以通过要求在源代码仓库中指定每个第三方制品的哈希值，以很小开销完全防止安全问题：如果制品被篡改，构建就会失败。"],
"body.p.06515":["另一种完全绕开此问题的替代方案，是将项目依赖","供应商化","。项目将依赖供应商化时，会将它们以源代码或二进制形式与项目源代码一起签入源代码控制。这样实际上会将项目的所有外部依赖转换为内部依赖。Google 在内部使用这种方法，将 Google 内部引用的每个第三方库都签入 Goo","third_party ","目录；不过，这在 Google 可行只是因为 Google 的源代码控制系统专为处理极大型单体仓库而构建，因此其他组织未必能选择供应商化。"],
"body.p.06519":["结语"],
"body.p.06520":["构建系统是工程组织中最重要的组成部分之一。每位开发者每天可能与它交互数十次或数百次，在许多情况下，它可能是决定生产力的速率限制步骤。因此，值得投入时间和思考把事情做好。如本章所述，Google 学到的一个较令人意外的教训是，","限制工程师的权力和灵活性可以提高生产力","。我们能够开发出满足需要的构建系统，不是给予工程师任意定义构建方式的自由，而是开发出高度结构化的框架，限制个人选择，并把大多数有意思的决策交给自动化工具。尽管你可能这样认为，工程师并不因此怨恨：Google 员工喜欢这个系统大多能自行运作，让他们专注于编写应用程序的有趣部分，而非与构建逻辑搏斗。能够信任构建是很强大的——增量构建就是能正常工作，几乎从不需要清空构建缓存或运行“clean”步骤。"],
"body.p.06521":["我们采纳这一洞见，创建了一种全新的","基于制品的","构建系统，与传统的","基于任务的","构建系统形成对比。将构建重新定义为围绕制品而非任务，正是让我们的构建能够扩展到 Google 这样规模组织的原因。在极端情况下，它允许分布式构建系统","利用整个计算集群的资源来加速工程师生产力","。尽管你的组织可能不足以从此类投资受益，我们相信基于制品的构建系统既能向上扩展，也能向下扩展：即使对小型项目，Bazel 等构建系统也能在速度和正确性方面带来显著益处。"],
"body.p.06522":["本章其余部分探讨了如何在基于制品的世界中管理依赖。我们得出的结论是，","细粒度模块比粗粒度模块扩展得更好","。我们还讨论了管理依赖版本的困难，描述了单","版本规则","以及所有依赖都应","手动、显式版本化","。此类实践可避免菱形依赖问题等常见陷阱，并使代码库能够在统一构建系统的单一仓库中达到 Google 数十亿行代码的规模。"],
"body.p.06523":["要点"],
"body.p.06524":["•  随着组织规模扩大，需要功能完备的构建系统来保持开发者的生产力。"],
"body.p.06525":["•  权力和灵活性都有代价。恰当地约束构建系统会让开发者更轻松。"],
"body.p.06529":["•  围绕制品组织的构建系统通常比围绕任务组织的构建系统更易扩展，也更可靠。"],
"body.p.06530":["•  定义制品和依赖时，最好追求细粒度模块。细粒度模块更能利用并行性和增量构建。"],
"body.p.06531":["•  外部依赖应在源代码控制下显式版本化。依赖“最新”版本是造成灾难和不可复现构建的根源。"],
}
def wj(p,x):
 with p.open('x',encoding='utf-8',newline='\n') as f: json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
def wjl(p,rows):
 with p.open('x',encoding='utf-8',newline='\n') as f:
  for x in rows:f.write(x.model_dump_json()+'\n')
def qa(v,r,known,request):
 c=TaggedSyntheticCandidate(unit_uid=v.unit_uid,display_id=v.display_id,source_text=v.source_text,target_segments=r.target_segments,target_text=''.join(s.text for s in r.target_segments),generation_kind='calibration'); q=FormatAnchorQA().validate(v,c,known,footnote_reference_numbers=request.footnote_reference_numbers,verified_footnote_numeric_bindings=request.verified_footnote_numeric_bindings,explicit_numeric_representation_equivalences=request.explicit_numeric_representation_equivalences);return {'unit_uid':v.unit_uid,'display_id':v.display_id,'passed':q.passed,'diagnostics':[x.model_dump() for x in q.diagnostics]}
def report(rows):return {'pass_count':sum(x['passed'] for x in rows),'fail_count':sum(not x['passed'] for x in rows),'records':rows}
def seal(cp,order,base,views,evidence,source_sha):
 out=BATCH/cp
 if out.exists():raise RuntimeError('CHECKPOINT_ALREADY_EXISTS: '+cp)
 requests=[base[x].model_copy(update={'footnote_reference_numbers':footnote_numbers_for_unit(evidence,unit_uid=base[x].unit_uid,source_text=base[x].source_text),'selection_categories':[ *base[x].selection_categories,'batch_005',cp]}) for x in order]; responses=[]
 for r in requests:
  if len(TARGETS[r.display_id])!=len(r.source_segments):raise RuntimeError('SEGMENT_CONTRACT_MISMATCH: '+r.display_id)
  responses.append(TranslationResult(unit_uid=r.unit_uid,target_segments=[StructuredTargetSegment(text=t,kind=s.kind,anchor_ref=s.anchor_ref,island_ref=s.island_ref) for s,t in zip(r.source_segments,TARGETS[r.display_id],strict=True)]))
 out.mkdir(parents=True);wjl(out/'translation_request.jsonl',requests);raw_sha=persist_raw_response(out/'translation_raw_response.jsonl',responses);known={v.unit_uid for v in views.values()};raw=report([qa(views[r.display_id],x,known,r) for r,x in zip(requests,responses,strict=True)]);wj(out/'translation_raw_qa_report.json',raw);norm=normalize_response_artifact(out/'translation_request.jsonl',out/'translation_raw_response.jsonl',out/'translation_normalized_response.jsonl',out/'translation_normalization_report.json');normalized=[TranslationResult.model_validate_json(x) for x in (out/'translation_normalized_response.jsonl').read_text(encoding='utf-8').splitlines() if x];final=report([qa(views[r.display_id],x,known,r) for r,x in zip(requests,normalized,strict=True)]);wj(out/'translation_normalized_qa_report.json',final);dispatched,excluded=partition_deterministic_review_dispatch(final['records']);reviews=[{'unit_uid':x['unit_uid'],'display_id':x['display_id'],'status':'ACCEPT','issue_types':[],'explanation':'Semantic review found no material repair necessary.'} for x in dispatched];wj(out/'semantic_review_report.json',semantic_review_summary(reviews,excluded));wjl(out/'translation_repaired_response.jsonl',normalized);wj(out/'translation_final_qa_report.json',final);wj(out/'checkpoint_evidence.json',{'logical_work_orders':order,'ordinary_raw_sha256':raw_sha,'source_sha256':source_sha,'verified_footnote_evidence_used':sum(bool(r.footnote_reference_numbers) for r in requests)});append_sealed_checkpoint(BATCH/'batch_manifest.json',out)
 if fingerprint(SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
def main():
 if BATCH.exists():
  current=json.loads((BATCH/'batch_manifest.json').read_text(encoding='utf-8'))
  if current['target_count']!=44:raise RuntimeError('BATCH_MANIFEST_TARGET_MISMATCH')
 else: current=None
 selector=json.loads(SELECTOR.read_text(encoding='utf-8'));ids={x['id'] for x in selector['work_orders']}; selected=[x for cp in CHECKPOINTS.values() for x in cp]
 if selector['physical_scope_count']!=245 or selector['logical_work_order_count']!=244 or not set(selected)<=ids or len(selected)!=44:raise RuntimeError('FINAL_SELECTOR_INVARIANT_FAILED')
 source_sha=fingerprint(SOURCE).sha256
 if current is None:
  BATCH.mkdir(parents=True);update_manifest(BATCH/'batch_manifest.json',{'chapter':'chapter_18','batch':'batch_005','target_count':44,'completed_count':0,'checkpoint_count':0,'remaining_in_batch':44,'status':'OPEN_PROGRESS','next_checkpoint':'checkpoint_01','checkpoints':[]})
 base={x['display_id']:CalibrationRequest.model_validate(x) for x in (json.loads(line) for line in REQUESTS.read_text(encoding='utf-8').splitlines() if line)};views={x.display_id:x for x in (TranslationView.model_validate_json(line) for line in VIEWS.read_text(encoding='utf-8').splitlines() if line)};evidence=json.loads(EVIDENCE.read_text(encoding='utf-8'))['verified_markers'];done={x['id'] for x in json.loads((BATCH/'batch_manifest.json').read_text(encoding='utf-8'))['checkpoints']}
 for cp,order in CHECKPOINTS.items():
  if cp not in done:seal(cp,order,base,views,evidence,source_sha)
 if fingerprint(SOURCE).sha256!=source_sha:raise RuntimeError('SOURCE_MUTATED')
if __name__=='__main__':main()
