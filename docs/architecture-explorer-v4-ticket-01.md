# Architecture Explorer v4 Ticket 01：七模块覆盖审计与 v3 基线清单

## 状态与边界

- Ticket：[#75](https://github.com/nnewlander/translate/issues/75)，`v4-01 七模块覆盖审计与 v3 基线清单`。
- proposed Spec：[#74](https://github.com/nnewlander/translate/issues/74)。现行合同仍为 `architecture-explorer/v3`。
- 固定设计 Base（B0）与本分支起点（Bstart）均为 `09aaf4a033123c7eca8d280fa8982e5b0e711b86`；目标集成分支为 `experiment/matt-skills`。
- 机器可读清单位于 `docs/architecture-explorer-v4-ticket-01-baseline.json`。
- 本文和 JSON 仅是规划与验收证据，不是运行时事实、Source Fact、Review Decision、Review State 或正式合同权威。
- 本票没有新增 Candidate 内容，没有修改 Projection、Review、正式 Relationship、正式 Evidence 或合同。

## 可重复计算方法

固定对象清单通过现有 `booktrans.architecture_explorer.content_delta.inspect_content_delta` 对 B0 自比较得到，不读取工作树新增文件：

```text
base_revision = head_revision = 09aaf4a033123c7eca8d280fa8982e5b0e711b86
content_sha256 = 518c26a6115d9ce5243b0b071f33a551deb50114c32c9e5ff5935f41c7c9e2cb
changes = []
```

对象投影统计复用已有装配链：正式 Relationship 发布映射、`scan_python_source_facts`、Navigation、Candidate Fragments、View Definitions 和 View Projection。扫描器为 `architecture-explorer-t6-4/1.5`，本次 Source Facts 摘要为 `4060a4d5079dee8623afe22210bf1d776850433709773198bc8e37e7ae3682b5`；Definition Index 覆盖 320 个 Python 模块、199 个 class、1,538 个 function/method，共 1,737 个 definition。没有为本票建设扫描器。

固定 Git 对象索引的权威输入包括 `modules.json`、`navigation.v1.json`、11 个 Candidate Fragment、`recursive-views.v1.json`、正式 Relationship/Review Source、Node Review Definitions 和 v3 current contract。`output/` 中的既有本地构建输入仅用于聚焦装配验证，不参与固定对象计数或内容摘要。

以下命令只调用已有 API。`project_authoritative_commit` 会从 B0 Git 对象物化隔离的 clean tree，再运行既有 Source Facts、Candidate、View 与 Projection 装配；它不读取当前工作树的 `output/`：

```powershell
& .\scripts\architecture_explorer\prepare_worktree.ps1
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
$code = @'
from collections import deque
from pathlib import Path
from booktrans.architecture_explorer.content_delta import (
    inspect_content_delta,
    project_authoritative_commit,
)

repo = Path.cwd()
b0 = "09aaf4a033123c7eca8d280fa8982e5b0e711b86"
delta = inspect_content_delta(repo, base_revision=b0, head_revision=b0)
projection = project_authoritative_commit(repo, b0)["view_projection"]
views = projection["view_index"]
depth = {projection["root_view_id"]: 0}
queue = deque([projection["root_view_id"]])
while queue:
    current = queue.popleft()
    for appearance in views[current]["appearances"]:
        action = appearance.get("primary_action")
        if action and action.get("kind") == "open-view":
            target = action["target_view_id"]
            if target not in depth:
                depth[target] = depth[current] + 1
                queue.append(target)
print(delta.head_content_sha256)
print(delta.object_counts)
print({
    "nodes": len(projection["node_index"]),
    "relationships": len(projection["relationship_index"]),
    "views": len(views),
    "appearances": sum(len(view["appearances"]) for view in views.values()),
    "max_depth": max(depth.values()),
    "perspectives": sorted({view["perspective"] for view in views.values()}),
})
'@
& $python -c $code
```

逐模块归属采用固定规则：v1 Fragment 的 `parent_module_id` 是直接 owner；recursive Fragment 从其 `scope_node_id` 已归属的模块递归继承 owner；Evidence 对该模块所有 Node/Relationship 的 `evidence_ids` 去重，再用既有 Source Evidence Index 精确解析；View 深度只沿显式 `primary_action.kind == open-view` 做 BFS。机器清单的 `module_traceability` 保存每模块的权威文件、完整 Node/Relationship/View/Evidence ID、Evidence path 和逐模块审核覆盖；`appearances_by_view` 保存 12 个 View 的完整 `node_id`、可选 `primary_action` 及其权威来源。因此 39 Appearance、重复 scope Node、root Appearance、Evidence 去重与 owner 分组都可从 B0 重算并逐项比对。

## v3 实际基线

| 维度 | B0 实际值 | 说明 |
| --- | ---: | --- |
| 顶层模块 | 7 | 全部逻辑定义已批准；全部 `source_coverage` 仍为 `unverified` |
| Candidate Fragment | 11 | 7 个顶层 fragment + 4 个 recursive fragment |
| Candidate Node | 27 | 加上 7 个顶层模块后，投影 Node 总数为 34 |
| Candidate Collaboration | 12 | 未经正式 Relationship 审核 |
| Candidate Decomposition | 9 | 未经正式 Relationship 审核；当前无环 |
| 正式 Relationship | 1 | Supervisor/production-control → Worker/translation-execution |
| 正式 Evidence | 13 | 只绑定上述正式 Relationship 的精确审核范围 |
| Relationship 总数 | 22 | 1 + 12 + 9 |
| View | 12 | root 1 + 模块 View 7 + recursive View 4 |
| View entry action | 4 | 全部进入 recursive View |
| Appearance | 39 | root 7 + 模块/recursive View 32；是出现次数，不是 Node 数 |
| 实际最大 View 跳转深度 | 2 | root 为 0，模块 View 为 1，recursive View 为 2 |
| Perspective | 3 | `logical-architecture`、`responsibility`、`component` |
| Node Review Definition | 2 | `worker-launch-boundary`、`translation-routing-and-acquisition` |

正式审核覆盖必须分开理解：7 个顶层模块只有逻辑边界获批，源码覆盖仍是 0/7；正式 Relationship 为 1/1 `current-reviewed + exact-match`，绑定 13 条正式 Evidence；固定 B0 中的生产 Candidate Node Decision 为 0/27，Candidate Relationship Decision 为 0/21。这个生产 Decision 统计不等同于历史 Owner Review 活动或 Review Definition；两个 Node Review Definition 是待审核材料定义，不是 Decision，也不提升投影可信状态。View 与 Appearance 不是独立审核对象。

实际导航路径为：

- 深度 1：`top-level` 分别进入七个模块 View。
- 深度 2：`production-control → worker-launch-boundary-internal`；`translation-execution → translation-routing-and-acquisition-internal`；`translation-execution → translation-provider-adapters-internal`；`quality-and-repair → quality-semantic-review-and-repair-internal`。
- 文档理解、范围与请求、生产证据、候选与发布仍只到模块 View；没有 recursive View。

## 七模块覆盖清单

表中 “Appearance” 是模块 View 与该模块 recursive View 内的出现次数，括号另列 root 中该模块的 1 次 Appearance。

| 模块 | Node | Collaboration / Decomposition | View / Appearance | Evidence | 深度 / Perspective | 扩展优先级 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| 文档理解与定位 | 2 | 1 / 0 | 1 / 2（+root 1） | 6/6 可解析；全为 `direct_import` | 1 / responsibility | P0 |
| 工作范围与请求构造 | 2 | 1 / 0 | 1 / 2（+root 1） | 9/9；全为 `direct_import` | 1 / responsibility | P0 |
| 翻译执行与模型接入 | 6 | 2 / 4 | 3 / 8（+root 1） | 24/24；import 6、definition 13、call 5 | 2 / responsibility、component | P2 |
| 质量判断与受限修复 | 5 | 3 / 3 | 2 / 6（+root 1） | 16/16；import 8、definition 4、call 4 | 2 / responsibility | P0 仅限确定性结构 QA；其余 P2 |
| 生产证据与 Checkpoint | 2 | 1 / 0 | 1 / 2（+root 1） | 2/2；全为 `direct_import` | 1 / responsibility | P0 |
| 生产调度与控制 | 7 | 3 / 2 | 2 / 9（+root 1） | 11/11；PowerShell/Wrapper 9、definition 2 | 2 / responsibility | P1 |
| 候选文档与发布验证 | 3 | 1 / 0 | 1 / 3（+root 1） | 4/4；全为 `direct_import` | 1 / responsibility | P0 |

### 逐模块对象追溯索引

完整 Evidence ID（各模块内去重后共 72 条，跨模块再次去重仍为 72 条）及源码 path 见 JSON 的 `module_traceability`；逐 View 的 39 条 Appearance、11 个 `open-view` Primary Navigation Action（其中 4 个进入 recursive View）及权威来源见 `appearances_by_view`。下表列出稳定对象 ID 和权威 Fragment，避免把数量当作清单。

| 模块 | 权威 Fragment | Candidate Node ID | Candidate Relationship ID | View ID | 逐模块正式审核覆盖 |
| --- | --- | --- | --- | --- | --- |
| 文档理解与定位 | `candidates/document-understanding.v1.json` | `document-package-extraction`; `document-translation-view` | `candidate-collaboration:document-extraction-to-view` | `document-understanding` | Node Decision 0/2；Candidate Relationship Decision 0/1；正式 Relationship 0 |
| 工作范围与请求构造 | `candidates/scope-and-request.v1.json` | `scope-logical-work-order-selection`; `scope-request-context-assembly` | `candidate-collaboration:scope-selection-to-request` | `scope-and-request` | Node Decision 0/2；Candidate Relationship Decision 0/1；正式 Relationship 0 |
| 翻译执行与模型接入 | `candidates/translation-execution.v1.json`; `recursive-candidates/qwen-provider-adapters.v2.json`; `recursive-candidates/translation-routing-and-acquisition.v2.json` | `translation-routing-and-acquisition`; `translation-provider-adapters`; `qwen-mt-single-text-adapter`; `qwen-max-structured-translation-adapter`; `semantic-unit-route-decision`; `translation-provider-dispatch-and-raw-projection` | 2 Collaboration + 4 Decomposition；完整 ID 见 JSON | `translation-execution`; `translation-routing-and-acquisition-internal`; `translation-provider-adapters-internal` | Review Definition 1/6（`translation-routing-and-acquisition`），Decision 0/6；Candidate Relationship Decision 0/6；共享正式 Relationship 1 |
| 质量判断与受限修复 | `candidates/quality-and-repair.v1.json`; `recursive-candidates/quality-semantic-review-and-repair.v2.json` | `quality-deterministic-qa`; `quality-semantic-review-and-repair`; `semantic-review-contract-boundary`; `repair-authorization-gate`; `constrained-repair-call-boundary` | 3 Collaboration + 3 Decomposition；完整 ID 见 JSON | `quality-and-repair`; `quality-semantic-review-and-repair-internal` | Node Decision 0/5；Candidate Relationship Decision 0/6；正式 Relationship 0 |
| 生产证据与 Checkpoint | `candidates/production-evidence.v1.json` | `checkpoint-evidence-persistence`; `checkpoint-manifest-seal` | `candidate-collaboration:checkpoint-evidence-to-manifest-seal` | `production-evidence` | Node Decision 0/2；Candidate Relationship Decision 0/1；正式 Relationship 0 |
| 生产调度与控制 | `candidates/production-control.v1.json`; `recursive-candidates/worker-launch-boundary.v2.json` | `supervisor-orchestration`; `worker-launch-boundary`; `state-and-control-decisions`; `production-control-adapters`; `deterministic-worker-wrapper`; `supervisor-process-launch-construction`; `worker-production-entry-guard` | 3 Collaboration + 2 Decomposition；完整 ID 见 JSON | `production-control`; `worker-launch-boundary-internal` | Review Definition 1/7（`worker-launch-boundary`），Decision 0/7；Candidate Relationship Decision 0/5；共享正式 Relationship 1 |
| 候选文档与发布验证 | `candidates/candidate-and-release.v1.json` | `candidate-document-projection`; `package-preservation-verifier`; `release-membership-derivation` | `candidate-collaboration:projection-to-package-verification` | `candidate-and-release` | Node Decision 0/3；Candidate Relationship Decision 0/1；正式 Relationship 0 |

### 文档理解与定位

- 已有内容：`document-package-extraction`、`document-translation-view`，以及“提取事实交给视图构造”Candidate Collaboration；只有模块 View，没有分解关系或内部 View。
- Evidence：当前 6 条引用全部是 `extractor.py` 与 `translation_view.py` 的 import 事实。Definition Index 另能精确定位 `DocumentExtractor.extract`、XML part/story/block/locator 构造、`TranslationViewBuilder.build`、`RootLocatorIndex` 和 `resolve_locator*`，但这些 definition 尚未被 Candidate 内容引用。
- 缺口：现有两个 Node 把包解析、IR/稳定定位、可翻译片段/anchor 构造压成两个平面职责，看不到从 DOCX part 到 `DocumentIR`、再到 `TranslationView` 的边界。
- 未知项：静态定义不能证明某份真实 DOCX 覆盖完整、所有 story 均成功解析、locator 在运行时可解析，或某个文件/definition 排他归属该职责。
- 优先级：P0。证据可用且当前深度仅 1，适合首批补足广度。

### 工作范围与请求构造

- 已有内容：`scope-logical-work-order-selection`、`scope-request-context-assembly` 与一条 Candidate Collaboration；没有 recursive View。
- Evidence：当前 9 条均为 import 事实。Definition Index 已揭示三个可分辨边界：`select_final_logical_work_orders` 的复合工作单元替换；`frozen_checkpoint_order`/`validate_frozen_checkpoint_members` 的 25 单元冻结切片；`ensure_checkpoint_d050_preflight`、`build_calibration_request`、`load_checkpoint_requests` 的精确证据和请求装配。
- 缺口：范围冻结、Checkpoint 切片、D050 closed-world evidence 与请求构造目前被压入两个 Node，无法解释 Selection authority 与 request materialization 的差别。
- 未知项：Definition/import 不证明某批选择实际被使用，不证明 PDF 页关联或 D050 候选在运行时唯一，也不证明请求已送达 Provider。
- 优先级：P0。它连接文档事实与生产执行，是跨模块理解的上游关键边界。

### 翻译执行与模型接入

- 已有内容：6 个 Candidate Node、2 条 Collaboration、4 条 Decomposition；`translation-routing-and-acquisition-internal` 与 `translation-provider-adapters-internal` 两个 recursive View。它是目前覆盖最丰富的模块之一，也是唯一使用 `component` Perspective 的模块。
- Evidence：24 条均可从 Source Facts 解析，且已经混合使用 definition、call 和 import；相比五处优先范围，证据形态更成熟。
- 缺口：已有 View 明确保留 Provider runtime receiver dispatch、运行可达性和外部服务可用性为未知；这些不应由本票补造内容。
- 未知项：真实请求是否选择某 route、凭据/网络是否可用、Provider 是否返回有效候选、完整运行调用链。
- 优先级：P2。先让其他模块获得相近广度，再考虑继续下钻。

### 质量判断与受限修复

- 已有内容：顶层 `quality-deterministic-qa` 与 `quality-semantic-review-and-repair`；后者已有三项内部职责、3 条 Decomposition、2 条内部 Collaboration 和 recursive View。确定性 QA 自身仍无内部 View。
- Evidence：模块 16 条均可解析；语义审核切片已使用 definition/call，确定性 QA 顶层对象目前只引用 3 条 import。
- Definition Index 调查：`FormatAnchorQA.validate` 先校验 unit、target/segments、island 顺序、anchor 引用与 protected text，再调用 `DeterministicCandidateQA.validate` 检查数字、URL、Email、DOI、ISBN 和 protected text；`verify_projections` 再比较真实 DOCX 的 structural anchor、文本节点、样式和 hyperlink；`harness_qa_record` 把这些结果投影为稳定 QA record；`process_unit` 依据精确 `qa_codes` 进入失败或受限 repair gate；`canonicalize_qa_failure` 只从已读 immutable reports 形成无副作用失败包络。
- 缺口：用户目前无法从确定性 QA Node 看见“结构/anchor closed-world 检查 → 规范 QA record → 精确失败范围 → 受限修复门禁”的边界。
- 未知项：静态证据不证明真实候选通过检查、repair 被允许或成功，也不证明所有生产 QA 路径只经过这一条调用链。
- 优先级：P0，但只能作为首批多模块内容之一，不能成为唯一首批内容。

### 生产证据与 Checkpoint

- 已有内容：`checkpoint-evidence-persistence`、`checkpoint-manifest-seal` 与一条 Candidate Collaboration；只有模块 View。
- Evidence：当前仅 2 条 import，来自 `production_checkpoint.py` 对 artifact 与 manifest 模块的引用。它们支持候选边界，但不能解释 8,380 行生产 Checkpoint 实现中的权威分层。
- Definition Index 调查：`artifacts.py` 明确区分 create-once、create-or-byte-validate 和 derived atomic replace；`batch_manifest.py` 提供 authoritative manifest chain、prepared seal 校验、idempotent commit 与 successor publication；`run_production_checkpoint` 在最终阶段写入 QA、review、checkpoint evidence，再从 immutable seal 派生/发布 manifest。
- 缺口：Evidence persistence、prepared seal generation、manifest projection/publication 与恢复安全当前没有分解；这是七模块中“实现规模大、候选证据最薄”的范围。
- 未知项：静态定义不证明某个生产 Checkpoint 已成功封存、磁盘写入原子性在故障环境中成立，或所有历史 manifest 均有效；manifest 是派生索引，不是 Evidence authority。
- 优先级：P0，且在内容风险上最高；建议先做窄的持久化/封存边界，不把整个 `production_checkpoint.py` 变成单个 Node。

### 生产调度与控制

- 已有内容：7 个 Candidate Node、3 条 Collaboration、2 条 Decomposition；`worker-launch-boundary-internal` 已提供深度 2。唯一正式 Relationship 从本模块指向翻译执行，处于 `current-reviewed + exact-match`。
- Evidence：11 条均可解析，涵盖 PowerShell function/binding、dot-source、deterministic wrapper 与 Python definition；正式 Relationship 另有独立 13 条批准 Evidence。
- 缺口：Supervisor orchestration 与 state/control decisions 仍是平面 Candidate；但本模块已有首个递归切片和唯一正式 Relationship，覆盖优先级低于五处指定范围。
- 未知项：静态启动证据不证明某次生产循环实际到达、外部条件可用或 Worker 成功完成；正式审核范围不能传播给相邻 Node/Relationship。
- 优先级：P1。在 P0 多模块切片后再调查控制循环与状态决策。

### 候选文档与发布验证

- 已有内容：`candidate-document-projection`、`package-preservation-verifier`、`release-membership-derivation` 与“候选投影交给包完整性校验”Candidate Collaboration；无 recursive View，release membership 在 View 中是孤立 Node。
- Evidence：当前 4 条均为 import。Definition Index 可定位 `apply_tagged_projections`、`TaggedSyntheticRoundTrip.run`、`verify_package_preservation`、`derive_release_membership` 与 `pending_in_source_order`。
- 缺口：当前 View 没有表达候选投影、结构/包验证、由 `ALREADY_APPROVED` 与 immutable passing final-QA records 派生 release membership 的差别，也没有显示 release membership 与包验证之间证据边界。
- 未知项：静态代码不证明真实候选视觉正确、完整书籍包已交付、某个运行目录中的 final-QA 记录都合法，或 release membership 等同于发布完成。视觉 QA 和整书候选交付仍未授权。
- 优先级：P0。可以独立形成发布边界切片，但必须保留视觉与真实交付未知项。

## 五处优先范围调查结论

| 优先范围 | 当前证据结论 | 建议扩展边界 | 不应推断 |
| --- | --- | --- | --- |
| 文档理解与定位 | 6 条 import 已解析；Definition Index 有 extractor、IR、locator、view builder 的稳定定义 | 包/Story 提取 → `DocumentIR` 与稳定定位 → `TranslationView`/anchor 构造 | 真实 DOCX 全覆盖、运行成功、排他源码归属 |
| 工作范围与请求构造 | 9 条 import；selector、冻结切片、D050 preflight、request builder 定义清楚 | logical work order → frozen checkpoint selection → exact evidence/context → calibration request | 实际选择已执行、PDF/DOCX 关联必然唯一、Provider 已接收 |
| 生产证据与 Checkpoint | 2 条 import，但 artifact/manifest/seal 定义边界强 | create-once evidence → prepared seal → derived manifest projection → atomic/successor publication | manifest 是 Evidence、Checkpoint 已真实封存、故障原子性已实测 |
| 候选文档与发布验证 | 4 条 import；projection/preservation/release membership 定义可定位 | tagged projection → structural/package verification → release membership derivation | 视觉 PASS、完整候选已交付、membership 即发布完成 |
| 确定性结构 QA | 顶层仅 3 条 import；底层 definition/call 证据可区分 anchor、token、record、repair gate | closed-world structure/token QA → QA record/failure envelope → bounded repair handoff | 候选已通过、repair 已执行或成功、覆盖全部 QA 路径 |

五处都具备继续调查的静态证据，不需要因证据不足而放弃；但前三个模块和候选发布范围的现有 Candidate Evidence 仍只有 import，不能直接把建议对象提升为 Candidate Architecture。正式内容开发时应先引用现有 definition/call Evidence，无法形成唯一静态绑定时保留未知，而不是按建议名称凑 Node。

## 建议的批量内容分组

以下是后续内容 Ticket 的建议，不是本票创建的 Candidate 对象或配额。

1. **源文档到冻结工作单元**：跨“文档理解与定位”和“工作范围与请求构造”，围绕 DOCX part/story/locator、`DocumentIR`/`TranslationView`、logical work order 与 frozen checkpoint slice。它补足两个深度 1 模块，并解释源码事实如何进入不可变选择边界。
2. **Checkpoint Evidence 与封存发布**：聚焦 `artifacts.py`、prepared seal generation 与 `batch_manifest.py` 的派生 authority chain。先做窄的 evidence→seal→manifest 纵向切片，避免把巨大 `run_production_checkpoint` 当成单一职责。
3. **确定性结构 QA 与受限修复交接**：围绕 `FormatAnchorQA`、`DeterministicCandidateQA`、稳定 QA record、canonical failure envelope 和按精确 `qa_codes` 的 repair gate。该组必须与其他模块组并行验收，不能独占首批。
4. **候选投影到发布成员资格**：区分 tagged DOCX projection、structural/package preservation 和 release membership derivation；可使用 `component` 或新的有证据 Perspective，但不应把 View 分类扩成独立生命周期。

组 1 优先补上游理解广度；组 2 风险最高，应尽早验证证据边界；组 3 提供清晰闭环但不能成为唯一内容；组 4 完成下游发布理解。每组后续都应以相关 Node、Collaboration/Decomposition、View、Appearance 与 Evidence 纵向交付，并继续使用稳定 Node 身份、View 局部 Appearance 和显式 navigation target。

## Evidence 限制与保留未知

- Source Facts 与 Definition Index 是指定快照的可重复静态观察，不证明运行时可达、顺序、成功、外部服务可用或完整源码归属。
- `direct_import` 只证明静态依赖出现，不单独证明架构协作、数据交接或分解关系。五处优先范围中四处当前 Candidate Evidence 只有 import。
- Definition Index 提供稳定 definition/Evidence 定位；部分文件没有唯一 call resolution，不能因此制造 call Relationship。
- 13 条正式 Evidence 只覆盖精确的 Supervisor → Worker 静态条件启动 Relationship，不批准端点 Node、Candidate Collaboration、模块源码覆盖或相邻职责。
- 两个 Node Review Definition 不等于 Decision；B0 没有生产 Candidate Node/Relationship Decision。不得用 Definition 数量冒充审核覆盖。
- Navigation depth 是从 `top-level` 沿显式 `open-view` action 计算的实际 View 跳转数，不是永久 Node level，也不是历史阶段数量。
- Appearance 39 是 View 内出现次数；同一稳定 Node 可在多个 View 出现，不能按 Appearance 数推断 Node 增长。
- 未执行视觉 QA，也没有授权生成完整书籍候选或发布包；本票不能声明视觉 PASS。

## 与并行 Ticket 的写入边界

本票只新增上述两个 `docs/` 清单文件。未修改 `architecture-explorer/` 权威输入、`src/booktrans/architecture_explorer/` Projection/Review/扫描实现、Web renderer、测试、合同或任何 Candidate Fragment，因此不触碰 #76、#77 的公共接口和写入范围。

## 聚焦验证记录

- JSON 解析与跨模块算术校验：PASS；模块合计为 27 Candidate Node、12 Candidate Collaboration、9 Candidate Decomposition、39 Appearance，与顶层统计一致。
- 固定 B0 `inspect_content_delta(B0, B0)`：PASS；`changes=[]`，Base/Head 内容摘要均为 `518c26a6115d9ce5243b0b071f33a551deb50114c32c9e5ff5935f41c7c9e2cb`，对象计数与 JSON 清单一致。
- 固定对象逐项追溯校验：PASS；27 个 Candidate Node、21 个 Candidate Relationship、12 个 View 与 72 条去重 Candidate Evidence ID 均与 B0 的既有 Projection/Source Evidence Index 精确一致。
- Appearance 逐项追溯校验：PASS；12 个 View 的 39 条 `{node_id, primary_action?}` 与 B0 View Projection 深度相等且顺序一致，其中 11 个 `open-view` action、4 个 recursive entry action。
- v3 组合投影与 View Projection：`6 passed in 42.10s`。
- Python Definition Index：`23 passed in 18.90s`。
- 一次包含 `test_v3_content_delta.py` 的较宽聚焦运行结果为 `36 passed, 29 failed`。独立 Reviewer 核实临时仓库中的 recursive Candidate Fragments 实际存在，原先将 29 项失败全部归因于 Fixture 缺少 Fragment 的判断不成立。默认较长临时路径下观察到 `Filename too long`；使用短 `--basetemp` 后，代表性测试结果为 `1 passed`。其余 28 项失败未逐一复现或归因，不能据此断定全部由路径长度造成或属于既有缺陷。[PR #73 的历史 Architecture Explorer CI](https://github.com/nnewlander/translate/actions/runs/45533872994) 结论为 `success`，仅作为历史辅助证据，不是本轮验证结果。
- 未运行全仓翻译测试、浏览器/视觉 QA、完整书籍候选或发布验证；本票是只读审计材料，且 AGENTS 明确禁止无可靠 renderer 证据声称视觉 PASS。
