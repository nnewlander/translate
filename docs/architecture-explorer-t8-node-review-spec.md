# Architecture Explorer T8：单节点审核投影

状态：本地正式 Spec 草案，待 Owner 验收；未授权实施或发布为 GitHub Issue

固定基线：`95e219a926023aff0022dbe0174e9a361e20c8b1`

集成分支：`experiment/matt-skills`

## Contract / Inherits / Delta / Owner Overrides

- **Contract:** `architecture-explorer/v1`
- **Inherits:** all current invariants，包括 7 个顶层模块、1 个正式 Relationship、13 条批准 Evidence、18 个候选节点、8 条候选协作、T7 卡片/内联详情/右键 Evidence 详情、离线只读边界、可信门禁和浏览器职责。
- **Delta:** 为且仅为 `candidate-node + production-control + worker-launch-boundary` 增加独立的 Node Review Decision、Node Review State 与只读 UI Projection；Candidate Fact、正式 Relationship 和 Evidence 权威对象均不改写。
- **Owner Overrides:** 本轮 Owner 已确定直接综合 T8 Q1–Q8；不重新运行 `$grill-with-docs`，不制作新原型，不重复访谈；源码快照变化只触发连续性复评，纯行号移动不得单独导致失效。若更早材料与本 Spec 冲突，以本节和 Owner 本轮明确指令为准。

**Owner security boundary override：** T8 保护正式 GitHub Owner/admin 导入、版本化 Decision/receipt 的绑定、正式离线 build 和明确受支持的 Python API；能够任意修改、导入或执行本机 Python 代码的主体不属于本阶段威胁模型。内部纯投影函数不承担身份认证职责，也不是受支持的可信入口。生产只使用版本化、host-owned approval anchor（当前为空，因此默认 Pending）；隔离测试可使用预先固定且不得由 mocked GitHub 响应现场生成的 test-only anchor，该锚点不得进入生产策略。本澄清不修改 `architecture-explorer/v1`。

**Owner UI presentation override：** `worker-launch-boundary` 卡片只保留“候选职责”身份及“源码归属”“人工审核”两行辅助状态；人工审核以简洁中文组合表达 pending、current approved/rejected、historical approved/rejected 和 unknown，不在卡片展示英文 Projection、Decision 或 Node Review State 内部值。此覆盖仅精简展示层；Decision 与 Node Review State 在数据模型中仍独立，完整值、八类 Claims、Classification Rationale 与 Evidence 继续在既有右键详情中只读展示，不修改 `architecture-explorer/v1`。

## Problem Statement

Architecture Explorer 目前能展示有真实源码 Evidence 支持的候选节点，但候选事实与正式 Relationship 的审核状态并不能回答“这个候选节点本身是否已被 Owner 接受，以及该审核对当前源码是否仍然有效”。如果直接改写候选节点、复用 Relationship 审核状态或把源码变更一概视为失效，会混淆事实、决定和连续性，并产生相互矛盾的可信表达。

## Solution

在现有候选事实之上增加一个窄范围、独立、可追溯的节点审核层。可信 Owner/admin 来源可对唯一目标节点作 `APPROVED` 或 `REJECTED` 决定；没有可信决定时为 `Pending Review`。独立的 Node Review State 根据当前源码和精确依赖绑定评估决定是否仍适用于当前事实。最终 UI 只投影三者的组合，不修改 Candidate Fact，也不提升任何 Candidate Collaboration 或顶层源码覆盖。

## User Stories

1. 作为 Owner，我希望只审核 `worker-launch-boundary`，从而不把其余候选对象意外纳入批准范围。
2. 作为架构浏览者，我希望区分候选事实、审核决定和当前有效性，从而知道“存在”“被接受”和“现在仍有效”是三件不同的事。
3. 作为 Reviewer，我希望决定精确绑定目标节点、三条候选 Evidence、正式 Relationship、T3 Decision 和十三条批准 Evidence，从而防止相似对象或部分证据被误用。
4. 作为架构浏览者，我希望清楚看到 `APPROVED`、`REJECTED` 和 `Pending Review`，从而不会把未审核误读为拒绝或批准。
5. 作为架构浏览者，我希望单独看到 Node Review State，从而识别历史决定是否仍适用于当前源码。
6. 作为维护者，我希望纯行号移动保持连续性，从而避免无语义变化的噪声复审。
7. 作为维护者，我希望材料变化、缺失、歧义或解析失败保守降级，从而不会由源码自动制造或恢复批准。
8. 作为 T7 用户，我希望继续使用同一张卡片、同一右键详情和同一 Evidence renderer，从而不学习第二套交互。
9. 作为离线用户，我希望浏览和构建不依赖网络，从而保持当前只读可信边界。

## Scope and Object Identity

本阶段唯一可审核对象的封闭键为：

- object kind：`candidate-node`
- parent module：`production-control`
- node id：`worker-launch-boundary`

该键不等于模块、Relationship、Candidate Collaboration、源码文件或运行实例。决定不得使用名称模糊匹配，也不得扩展到 `deterministic-worker-wrapper` 或 `candidate-collaboration:supervisor-to-worker-wrapper`。

## Data and Review Model

### 1. Candidate Fact

Candidate Fact 继续由现有候选片段和源码事实生成，保留稳定 ID、节点类型、专业职责、通俗解释、候选状态、源码归属状态及 Evidence 引用。它是审核输入，不是审核结果；审核导入、状态更新和 UI 投影均不得就地修改、重写或复制出第二份 Candidate Fact。

### 2. Node Review Decision

Node Review Decision 是独立、版本化、可追溯的审核记录：

- decision result 仅允许 `APPROVED` 或 `REJECTED`；
- `Pending Review` 表示当前目标没有可用的可信决定，不伪造一条“待审核决定”；
- 记录稳定 decision id、目标对象键、被审核源码快照、Candidate Fact 摘要、精确依赖绑定、审核说明和可信来源收据；
- 只有经验证的仓库 Owner 身份且仓库权限为 admin 的 GitHub 来源可成为可信决定来源；本地文本、自报身份、被修改的收据或不完整权限信息均不能发布有效决定；
- 离线构建只读取已经验证并版本化的来源收据，不在普通浏览或构建时访问 GitHub。

`APPROVED` 只批准该节点在绑定事实与依赖下的架构解释；`REJECTED` 只拒绝该绑定版本的节点解释。二者都不修改源码、不证明真实运行、不批准 Candidate Collaboration，也不改变正式 Relationship 的 T3 决定。

### 3. Node Review State

Node Review State 独立于 Decision result，评估某一可信决定对当前源码是否仍有效：

- state：`current`、`needs-review`、`unknown` 或 `not-reviewed`；
- continuity：`exact-match`、`material-change`、`missing`、`ambiguous`、`parse-failure` 或 `not-assessed`；
- 记录目标对象键、decision id、评估源码快照、评估时间和封闭原因列表；
- `APPROVED/REJECTED + current + exact-match` 才是当前有效决定；
- 没有可信决定时显示 `Pending Review + not-reviewed + not-assessed`；
- 状态文件若缺失、损坏或与决定不一致，不得推断当前有效，最多保留可追溯的历史决定并保守降级；
- 一旦显式记录为 `needs-review` 或 `unknown`，源码恢复不得自动清除失效；只有新的可信决定才能恢复当前有效状态。

### 4. Node Review Projection

Projection 是 Candidate Fact、Node Review Decision 和 Node Review State 的只读组合。它可增加展示字段，但不得回写 Candidate Fact、覆盖其 `candidate` 身份、复制 Evidence 内容或改变正式 Relationship 数据。相同输入必须产生确定性相同的投影。

### 5. Eight Review Claims

Node Review Decision 必须逐项包含并校验以下八类 Review Claims；这是封闭集合，不能用一条笼统的“批准节点”替代，也不能只批准其中一部分后投影为节点已批准。每项都绑定 claim kind、规范值及其来源；缺失、重复、未知类型、值不一致或来源绑定不一致均拒绝导入。

| Claim | 本阶段规范值 | 精确绑定与校验 |
| --- | --- | --- |
| `IDENTITY` | `candidate-node + production-control + worker-launch-boundary` | 三段对象键逐字段相等；名称或近似 ID 不能替代。 |
| `NAME` | `Worker 启动边界` | 精确绑定 Candidate Fact 的当前名称；改名属于材料变化。 |
| `CLASSIFICATION` | `responsibility` | 精确绑定 `node_type`，并携带下述 Classification Rationale；不得改投影为 `component`。 |
| `RESPONSIBILITY` | `候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。` | 精确绑定专业职责文本的规范化摘要与哈希；语义或边界改变必须复审。 |
| `PLAIN_EXPLANATION` | 现有“受控发车闸门”通俗解释 | 精确绑定完整通俗解释的规范化摘要与哈希，不能只匹配关键词。 |
| `SOURCE_ASSIGNMENT` | `partial`，且只由唯一正式 Relationship 支撑 | 精确绑定 assignment status、既有说明和唯一 Relationship ID；不得推导为 `verified` 或排他归属。 |
| `CANDIDATE_EVIDENCE` | 本 Spec 列出的三条 Candidate Evidence | 数量、角色、身份和目标节点归属逐项校验；不得以相同路径、相似 snippet 或 Evidence 子集替代。 |
| `FORMAL_DEPENDENCY_AND_LIMITS` | 唯一正式 Relationship、T3 Decision、13 条批准 Evidence 及保留未知项 | 对依赖 ID、决定结果、Evidence 封闭列表和 unknowns 逐字段校验；不复制或扩大 T3 批准范围。 |

**Classification Rationale：** `worker-launch-boundary` 描述的是生产控制把受参数和守卫约束的启动请求跨越进程边界交给翻译执行这一稳定架构职责，而不是某一个文件、函数、脚本包装器或可部署组件。三条 Candidate Evidence 跨越控制侧启动准备与 Worker 入口定位，只支持“该职责边界值得作为候选节点表达”；它们不足以证明一个物理组件、完整源码边界或排他所有权。因此分类固定为 `responsibility`，相邻的 `deterministic-worker-wrapper` 仍是独立、未审核的 `component` Candidate Fact。

Node Review 明确保留以下未知项，`APPROVED` 也不得消除：是否在某次实际运行中到达或成功跨越该边界；运行时前置条件、凭据和外部依赖是否可用；该职责的完整源码覆盖；任何文件、symbol 或代码区域是否排他归属该节点；观察到的名称、字面值和直接绑定以外的参数业务含义；相邻组件是否获批；任何 Candidate Collaboration 是否获批；顶层模块源码覆盖是否完成。未知项只能由后续独立审核改变，不能由本节点投影推断。

## Exact Source, Evidence, and Dependency Binding

目标 Candidate Fact 必须精确绑定以下三条候选 Evidence，数量、身份和节点归属均不得漂移：

1. `evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a`
2. `evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429`
3. `evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8`

审核依赖还必须精确绑定：

- Relationship：`relationship:production-control:launches:translation-execution`
- T3 Decision：`github-comment:6516894339`
- T3 result：`APPROVED`
- T3 reviewed snapshot：`857a4b85cf4763b74ecff0d02e660b005a0c8bae`
- 十三条批准 Evidence（保持权威 Review Map 的封闭顺序）：
  1. `evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13`
  2. `evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711`
  3. `evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3`
  4. `evidence:sha256:3929e17745639510a1a3c6a39b5be55b5a6afc2070c3f18cb300fa0107983308`
  5. `evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a`
  6. `evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc`
  7. `evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2`
  8. `evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa`
  9. `evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2`
  10. `evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d`
  11. `evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5`
  12. `evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914`
  13. `evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d`

绑定引用权威对象，不复制其语义或 Evidence payload。任一依赖不再通过自身既有可信门禁时，节点决定不得保持 `current`。

### Reviewed Source Snapshot, Hashes, and Scanner Versions

本阶段 Decision 的受审核源码快照固定从基线 `95e219a926023aff0022dbe0174e9a361e20c8b1` 建立，并记录 worktree state。导入时必须绑定并验证：

- Candidate/source facts scanner：`architecture-explorer-t6-4/1.5`；继承的 T3 Relationship candidate scanner 继续为 `architecture-explorer-t2/1.3`，不得用其中一个冒充另一个；
- `production-control` Candidate Fragment SHA-256：`201c4283ccbf2850395c5a326dee0a5a8462fb8194e05df755e743bee731e669`；
- Navigation SHA-256：`e4589d88bf091a318561bf525a9e24657bb5f55ab21fa9e8e50eae243fd14f70`；
- T3 Review Map SHA-256：`03ad7e0b78c454326dbeab311d6133ed141c4bc1b54de0c6bf537ae9e4c707f1`；
- T3 Review Source SHA-256：`62c296838c8d76c6e621ebb473e38c3b45b67d970c88a41b59cf73ed2de6ad2b`；
- 目标 Candidate Fact 的规范化摘要、八类 Claims 的规范化摘要，以及当前扫描记录中每个相关输入文件和三条 Evidence 的既有内容/结构哈希。

上述四个版本化输入哈希和 Claims 摘要是 Decision 精确输入；不匹配即为材料变化。源码输入文件哈希、revision 和 locator 是审计及重新扫描触发器，不是单独的失效结论：它们变化后必须以稳定对象键、Evidence ID、结构锚点、symbol、规范化 snippet、角色绑定及正式依赖重新评估。若这些语义身份均唯一匹配，只发生行号或等价位置移动，则保持 `current + exact-match` 并只更新 locator；不得同时要求“源码文件哈希完全相等”和“纯行号移动有效”这两个互相矛盾的条件。

## State and Invalidation Rules

| Decision | Node Review State | UI 含义 | 是否可当作当前有效决定 |
| --- | --- | --- | --- |
| APPROVED | current / exact-match | 已批准 | 是 |
| REJECTED | current / exact-match | 已拒绝 | 是 |
| 无可信决定 | not-reviewed / not-assessed | Pending Review | 否 |
| APPROVED 或 REJECTED | needs-review | 保留历史决定；当前需复核 | 否 |
| APPROVED 或 REJECTED | unknown | 保留历史决定；当前有效性未知 | 否 |

Projection 使用下列封闭、用户可见名称覆盖既定语义，而不增加第三个权威状态源：`Pending Review`（无可信 Decision）、`Current · Approved`、`Current · Rejected`、`Historical · Approved`、`Historical · Rejected`、`Unknown`。前五项分别由 Decision 与 Node Review State 的组合确定；Decision/State 缺失、损坏、不一致或连续性不可判定时使用 `Unknown`，不得降成 Pending 或伪装成 Historical。

连续性规则按以下顺序应用：

1. **源码快照变化不是失效结论。** revision、整文件哈希或扫描时间变化只要求重新评估；不得仅因提交 SHA 不同就把决定降级。
2. **纯行号移动保持连续。** 当目标对象、三条候选 Evidence 的稳定身份、结构锚点、symbol、规范化片段和角色绑定均能唯一匹配，且正式依赖仍通过既有门禁时，只更新当前 locator；即使文件哈希或行号变化，也仍为 `current + exact-match`。
3. **与绑定对象无关的源码变化不失效。** 目标之外的编辑不能仅凭共享源码快照变化传播为节点失效。
4. **材料变化必须失效。** Candidate Fact 的语义字段、对象键、任一候选 Evidence 身份/数量/角色绑定、正式 Relationship、T3 Decision 或十三条批准 Evidence 发生变化，均为 `needs-review + material-change`。
5. **缺失保守降级。** 必需对象或 Evidence 无法找到、正式依赖不再当前有效时为 `needs-review + missing`。
6. **歧义或不可解析不得猜测。** 多重定位为 `unknown + ambiguous`；扫描/解析失败为 `unknown + parse-failure`。
7. **失效具有记忆。** 已记录失效即使随后源码恢复为相同形状，也不能自动恢复旧决定；必须导入新的可信 Owner/admin 决定。
8. **Candidate Fact 不随投影改变。** 所有状态变化只发生在 Decision、State 或 Projection 层。

## Object Isolation

- 其余 17 个候选节点保持 `candidate/unreviewed`，没有 Node Review Decision。
- 全部 8 条 Candidate Collaboration 保持 `candidate/unreviewed` 和既有虚线表达。
- 唯一正式 Relationship、T3 Decision、13 条批准 Evidence、7 个顶层模块及其源码覆盖状态保持原样。
- 批准目标节点不创建新边、不批准相邻包装组件、不把 `partial` 提升为完整源码归属，也不改变运行状态 `NOT_COLLECTED`。
- 不创建第二份 Candidate、Relationship、Evidence 或审核来源权威。

## UI Projection

- 复用 T7 现有候选卡片、展开/收起、右键菜单、详情 dialog 和 Evidence renderer；不新增页面、图层或审核编辑器。
- Decision 与 Node Review State 在数据层保持独立；卡片按 Owner UI presentation override 合并为一行中文“人工审核”语义，既有右键详情仍分别展示完整字段，颜色不得成为唯一信号。
- 折叠卡片继续只显示名称、节点类型和必要可信状态；批准或拒绝不得删除“候选事实”来源边界。
- 内联详情继续只解释职责、通俗说明和必要可信状态，不显示 Evidence 数量、ID 或 Evidence 入口。
- 既有右键“查看关系与源码证据”详情展示三条候选 Evidence，并增加只读审核摘要：对象键、Decision、Node Review State、可信来源及正式依赖。Evidence 仍由同一个 renderer 解析，不复制卡片专用渲染逻辑。
- 同一右键详情增加只读“Review Claims”区，按固定顺序展示八类 Claim 的名称、审核值、当前绑定校验结果；`CLASSIFICATION` 同处展示 Classification Rationale，`FORMAL_DEPENDENCY_AND_LIMITS` 同处展示保留未知项。该区不得提供批准、拒绝、编辑或刷新来源的控件。
- `APPROVED + needs-review/unknown` 不得显示为当前“已批准”；`REJECTED + needs-review/unknown` 不得暗示当前 Candidate Fact 已被重新拒绝。两者显示历史决定和当前失效原因。
- 正式实线与 Candidate Collaboration 虚线规则完全继承 T7；节点批准不改变边样式。

## Implementation Decisions

1. 复用现有候选片段导入、可信 GitHub Review Source 验证、显式 Review State 更新和 Explorer build seam；只增加节点级 schema 与组合逻辑，不建立平行框架。
2. 最高层测试 seam 固定为：`Candidate Fact + trusted Node Review Decision source + current source facts + existing formal dependencies -> immutable Candidate Fact + Node Review Projection`。
3. 普通 build/read 路径保持只读；显式状态更新采用原子写入并拒绝覆盖损坏或不一致状态。
4. Decision 导入必须 closed-world 校验所有字段、目标键、精确 Evidence 和依赖；未知字段、重复 ID、顺序漂移或部分匹配均拒绝。
5. Pending Review 是派生展示状态，不是有权限含义的伪决定。
6. 本阶段不改变 `architecture-explorer/v1`；T8 是该契约的显式 Delta。若实现需要改变继承不变量，必须先回到 Owner 决策，不能静默升级契约。

## Testing Decisions

采用 TDD，并优先在上述单一最高层 seam 验证外部可观察行为：

1. **Red — 模型与可信来源：** 无决定、可信 APPROVED、可信 REJECTED、伪造身份、非 admin、篡改收据、错误目标、错误三 Evidence、错误 Relationship/T3 Decision/13 Evidence 均有失败测试。
2. **Red — 连续性矩阵：** 相同快照、仅插入空行、函数/片段唯一重定位、无关源码变化、材料变化、Evidence 缺失、重复/歧义、解析失败及“失效后源码恢复”均先写失败测试。
3. **Green — 对象隔离：** 构建前后 Candidate Fact 深度相等；仅目标节点获得投影；仍为 18 个候选节点、8 条候选协作、1 个正式 Relationship 和 13 条正式 Evidence；其余 17/8 状态不变。
4. **Green — UI 行为：** 复用现有 DOM/真实浏览器 harness 验证三种 Decision、独立 State、历史失效表达、右键详情、同一 Evidence renderer、键盘/焦点语义，以及正式/候选边样式不变。
5. **Chrome CI：** 继续由统一 Architecture Explorer Windows/Chrome 入口执行完整专项回归；不得 deselect 或 skip 必需测试。当前基线证据为 253 项 Explorer 测试、31 项必需 Chrome 测试，T8 只能在其上增加覆盖，不能以固定数字替代完整收集/执行检查。
6. **真实 viewport：** 继承 1440×900、1280×900、960×900 和同页 1440→1280→960→1440；新增状态不得造成溢出、遮挡、边端点脱离或 reload。
7. **受控负向：** 使用 fixture 造成错误绑定、伪造来源或材料变化，验证命令非零失败/明确降级；不得修改真实生产源码来制造负向场景。
8. **最终验收：** 类型/静态检查、相关单测、完整 Architecture Explorer suite、必需 Chrome suite 和统一 CI 入口全部通过；Firefox 仍由 Owner 人工验收，Edge 仅补充且阻塞不得记为 PASS。

八类 Claims 的新增反例集中在同一最高层 seam：逐类删除、重复、替换类型、改值、交换来源绑定，以及只批准七类，均不得产生 `Current · Approved/Rejected`。Classification 专项反例包括把职责改为 `component`、删除 Rationale、把相邻包装组件或 Candidate Collaboration 的 Evidence 当作本节点批准范围。快照专项反例区分两类行为：仅 revision/文件哈希/行号变化但三条 Evidence 可唯一等价重定位时仍为 current；Candidate Fragment、Claims 摘要、扫描器版本、Evidence 身份/角色、正式依赖或 unknowns 改变时必须 `needs-review`，歧义或解析失败必须 `unknown`。这些是对既有连续性矩阵的精确补充，不另建第二套测试 seam。

好测试只断言数据契约、状态投影、可信门禁和用户可观察行为，不绑定内部函数调用或脆弱像素快照。

## Acceptance Criteria

- [ ] 只有 `worker-launch-boundary` 可出现 Node Review Projection；其余 17 个候选节点和 8 条候选协作保持未审核。
- [ ] Candidate Fact、正式 Relationship、T3 Decision、13 条批准 Evidence 均未被节点审核写回或复制成第二权威。
- [ ] APPROVED、REJECTED、Pending Review 与 Node Review State 在数据和 UI 中分别表达。
- [ ] 可信来源同时验证 Owner 身份、admin 权限、仓库/Issue/comment 身份及内容完整性；普通 build 离线。
- [ ] 三条候选 Evidence 与正式依赖按本 Spec 精确绑定；任何部分匹配均不能产生当前有效决定。
- [ ] 八类 Review Claims 以封闭集合逐项存在、精确绑定和校验；任一缺失、重复、未知、错值、错来源或只批准子集均不能产生当前有效决定。
- [ ] `CLASSIFICATION=responsibility` 带明确 Rationale；实际运行、完整源码覆盖、排他归属、相邻组件和 Candidate Collaboration 批准等 unknowns 在数据与右键详情中保留。
- [ ] Decision 精确绑定受审核基线、版本化输入哈希、Candidate/Claims 摘要及对应扫描器版本；源码 revision、文件哈希或 locator 变化只触发复评，不单独判定失效。
- [ ] 源码快照变化触发复评但不自动失效；唯一可重定位的纯行号移动保持 `current + exact-match`。
- [ ] 材料变化、缺失、歧义、解析失败和已记录失效按封闭规则保守处理，源码恢复不自动重新批准。
- [ ] T7 卡片、内联详情、右键详情、Evidence renderer、导航、响应式与可信边样式全部复用且回归通过。
- [ ] UI 以封闭 Projection 名称区分 pending、current approved、current rejected、historical approved/rejected 和 unknown；八类 Claims 仅在既有右键详情只读展示，内联卡片不显示 Evidence 数量、ID、入口或 Claims 清单。
- [ ] 完整 Architecture Explorer 与必需 Chrome CI 通过，负向 fixture 能被可靠拒绝或降级。

## Out of Scope

- 不实施本 Spec，不创建 T8 GitHub Issue，不 Commit、Push、开 PR 或 Merge。
- 不提供审核写入 UI，不在 Explorer 中登录 GitHub、提交评论或改变权限。
- 不审核其他 17 个候选节点、任何 Candidate Collaboration、顶层模块源码覆盖或新 Relationship。
- 不修改 T3 Decision、正式 Relationship、13 条批准 Evidence 或其既有连续性门禁。
- 不新增原型、页面、第三层导航、图布局系统、数据库、网络依赖、AI 审核或生产监控。
- 不修改生产翻译系统、分支保护或 Required Status Check。
- 不重新讨论或推翻已完成的 T8 Q1–Q8。

## Further Notes

- 本 Spec 只定义 T8 第一阶段。后续若扩展到其他节点或 Candidate Collaboration，必须另行定义对象范围、批量冲突和跨对象依赖，不能由本阶段自动推导。
- Owner 验收本草案后，仍需另行明确授权 `$implement`、T8 Issue、Commit、Push、PR 与 Merge；这些动作不由本 Spec 推定。
