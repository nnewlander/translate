# Architecture Explorer：递归式多层架构探索

- **Status:** Revised local draft; design direction Owner-accepted, contract activation and tracker publication not authorized
- **Project:** `nnewlander/translate`
- **Fixed integration baseline:** `4c7f8be1f0be48189804a51ed42aaf8bdb64ab94`
- **Current product contract:** `architecture-explorer/v1`
- **Proposed successor contract:** `architecture-explorer/v2`
- **Authority:** 本地 Spec 草案，不是 GitHub Issue、实现授权、审核决定或生产可信来源
- **Glossary:** 使用同一 Worktree 中尚未合并的 Architecture Explorer `GLOSSARY.md`

## Problem Statement

Architecture Explorer 当前能够可信地展示七个顶层逻辑模块、七个模块候选图、18 个 Candidate Node、8 条 Candidate Collaboration、唯一正式 Supervisor → Worker Relationship，以及 Definition Index、Call Facts 和 Source Trail。现有 T7 renderer、右键 Evidence、渐进式详情和可信状态投影已经证明，离线页面可以在不执行生产代码的情况下解释架构候选及其源码依据。

然而，当前产品的数据和导航结构仍然固定为“系统总览 → 顶层模块候选图”两层。Navigation 只允许一个初始 drilldown，Candidate Fragment 的 parent 只能是七个顶层模块，前端面包屑只显示根与当前层，点击行为通过“是否存在孩子”推断。用户无法从 Worker 启动边界继续进入其内部结构，也无法让同一个 Node 在不同 View 中以不同导航行为出现。

这个限制不能通过增加“第三层专用”数据、renderer 分支或 CSS 特例解决，否则每增加一层都需要再次修改生成器、导航和 UI。它也不能通过把 Source Facts、静态调用或 View 位置自动提升为架构结论解决，否则会破坏 Candidate、Reviewed Architecture Claim、Continuity 和 UI Projection 的可信边界。

用户需要一个数据驱动、无固定最大深度的 View 导航机制。第一阶段必须以真实产品数据完成以下纵向路径：

`系统总览 → 生产调度与控制 → Worker 启动边界 → Worker 启动边界内部 Candidate View`

该能力必须兼容现有七模块、18 个 Candidate Node、8 条 Candidate Collaboration、唯一正式 Relationship、13 条批准 Evidence、现有 Node Review 和 Source Trail，不进行全量迁移，也不创建第二套事实或审核来源。

## Solution

在现有权威输入与 T7 renderer 之间增加一个规范化、只读的 View Projection。构建过程从现有 Module Map、Navigation、Candidate Fragments、正式 Relationship、Source Facts、Review 和 Continuity 数据生成稳定的 Node、Relationship 与 View 索引。现有数据由集中兼容适配器映射到这一投影，不重写原文件，也不改变其可信含义。

View 是具有稳定 ID 的可导航投影。它引用 Node Appearance 和需要展示的 Relationship，不复制 Node 描述、Evidence 或 Review。Appearance 通过 `node_id` 引用稳定 Node，并可声明一个明确的 Primary Navigation Action。没有主要操作时打开详情；进入内部 View、跨 Node 导航、Perspective 切换和进入 Source Trail 都必须使用显式、可验证且对用户可理解的目标。

架构分解、Candidate Collaboration、正式 Relationship、静态源码调用和 Evidence 关联保持不同语义。架构分解必须无循环；View Transition 可以有意往返。运行时路径遇到已存在于当前路径中的目标 View 时，回到该既有路径位置而不是继续压入重复项，从而允许往返而不会产生无限增长的导航历史。任何 View Transition 都不会改变 Node、Relationship、Review 或 Continuity 状态。

首个内部 Candidate View 以 Worker 启动边界为 scope，复用现有 `worker-launch-boundary` 和 `deterministic-worker-wrapper` Node，并新增两个由真实 Source Facts 支持的 Candidate Node：Supervisor 进程启动构造、Worker 生产入口守卫。证据只支持把启动构造和入口守卫解释为启动边界的候选子职责；`deterministic-worker-wrapper` 是相邻独立组件，继续通过既有 Candidate Collaboration 与边界连接，不得重分类为内部组成。内部 View 因此新增两条 Candidate Decomposition 和一条从包装器到入口守卫的 Candidate Collaboration，并复用既有“启动边界指向 Worker 包装入口”协作。所有新增对象保持 candidate/unreviewed；现有父 Node 的 Approved 投影、正式 Supervisor → Worker Relationship 和 13 条批准 Evidence 不向它们传播。

T7 继续是唯一架构图 renderer。它只接收当前 View 的 Appearance 和 Relationship，不读取导航深度，也不包含第三层、第四层或特定 View ID 的渲染分支。现有已验收布局保持不变；新递归 View 使用通用、确定性的响应式网格规则，不引入图布局引擎、拖拽、pan/zoom 或物理布局。

`architecture-explorer/v1` 明确禁止第三层并冻结 18/8 清单，因此本功能不能作为静默 Delta 留在 v1。Owner 接受本 Spec 时只批准 v2 设计；在功能尚未集成并通过完整验收前，v1 仍是唯一 current 合同。开发期间 v2 合同保持 proposed。实施完成后先冻结包含全部新行为、但 v2 仍为 proposed 的候选 Head H，并针对 H 通过完整 v2 验收；随后一个仅改变合同状态元数据的激活变更把 v2 切为 current、把 v1 标为 superseded，并把 H 记录为 v2 的 `effective_baseline`。激活 Head 随后复跑合同检查与完整验证。v1 规范正文保持不变。v2 继承所有未明确改变的 v1 可信、交互、离线、安全、浏览器和响应式不变量。

## User Stories

1. As an Architecture Explorer user, I want to continue from a top-level module into a Node's internal View, so that I can understand architecture beyond the current two levels.
2. As an Architecture Explorer user, I want Navigation Depth to follow meaningful architecture structure rather than a fixed program limit, so that different modules can have different useful depths.
3. As a non-programmer, I want every View and destination to have a clear Chinese name and explanation, so that I know what I will see before navigating.
4. As a developer, I want code-oriented Views and Source Trail to coexist, so that architecture exploration is not forced to stop at one fixed boundary.
5. As a user, I want the same Node to appear in multiple Views without becoming multiple conflicting objects, so that its identity and trust information remain consistent.
6. As a reviewer, I want Node identity reuse to preserve the exact scope of each review, so that approval in one context is not propagated to responsibilities, collaborations, source ownership or process positions that were not reviewed.
7. As a user, I want a Node's primary action to be explicit in its current Appearance, so that clicking it has a predictable result.
8. As a keyboard user, I want Enter and Space to invoke the same primary action as pointer activation, so that recursive navigation remains accessible.
9. As a user, I want an Appearance with no configured primary action to open Node details, so that missing optional navigation never causes a broken or surprising transition.
10. As a user, I want secondary destinations to have understandable labels such as “查看内部职责” or “查看源码”, so that I can choose among architecture, Perspective and source navigation.
11. As a user, I want a complete breadcrumb for the active View path, so that I can understand how the current context relates to the whole system.
12. As a user, I want BACK to return exactly one path entry and restore focus to the Appearance that opened the departed View, so that I can explore without losing my place.
13. As a user, I want HOME to return to the system overview from any Navigation Depth, so that I always have a reliable escape route.
14. As a user, I want an intentional View Transition back to an earlier View to reuse that existing path entry, so that Perspective switching cannot grow navigation history forever.
15. As a user, I want right-click Evidence behavior to remain available at every recursive level, so that deeper exploration preserves the existing trust workflow.
16. As a user, I want Source Trail to open and close without changing the architecture View path, so that source investigation does not corrupt macro navigation.
17. As a user, I want Source Trail close and Escape behavior to restore focus correctly, so that deeper architecture navigation does not regress existing accessibility.
18. As an Owner, I want the existing seven modules, 18 Candidate Nodes and 8 Candidate Collaborations to remain unchanged inputs, so that recursive exploration does not require a risky migration.
19. As an Owner, I want new Candidate Architecture to cite existing validated Source Fact Evidence IDs explicitly, so that static calls or file proximity cannot silently become architecture claims.
20. As an Owner, I want every new internal Node, Candidate Decomposition and Candidate Collaboration to remain visibly candidate/unreviewed, so that the approved Worker launch boundary does not lend them false authority.
21. As an Owner, I want the existing formal Supervisor → Worker Relationship and its 13 Evidence records to remain byte-for-byte and semantically unchanged, so that the new View cannot rewrite prior approval.
22. As a reviewer, I want formal Relationship, Candidate Collaboration, Candidate Decomposition, static call and Evidence linkage to remain distinguishable, so that edge semantics and trust are not conflated.
23. As a reviewer, I want semantic relationship type and trust state represented independently, so that dashed styling means candidate without pretending all candidate edges mean the same thing.
24. As a maintainer, I want existing inputs adapted into one generated View Projection, so that the renderer has one interface without introducing a second fact source.
25. As a maintainer, I want old Candidate Fragment v1 files to remain valid, so that the first recursive slice can land without migrating all seven modules.
26. As a maintainer, I want a narrowly scoped additive candidate format for the new internal View, so that new Candidate Nodes and relationships have one owner and can later be migrated independently.
27. As a maintainer, I want the renderer to consume only the current View's nodes and edges, so that adding another depth does not require renderer changes.
28. As a maintainer, I want View IDs and references validated before site generation, so that malformed navigation fails closed rather than producing a broken offline page.
29. As a maintainer, I want Architecture Decomposition cycles rejected while legitimate collaboration and call cycles remain valid, so that validation follows relationship semantics.
30. As a maintainer, I want View Transition cycles to be safe rather than globally forbidden, so that users can switch Perspectives or navigate across Nodes and return.
31. As a maintainer, I want the real product to demonstrate three architecture levels and controlled test data to demonstrate more than three, so that recursion is proven without fabricating production architecture.
32. As a maintainer, I want new Views to use a generic deterministic grid rather than per-View coordinates or depth branches, so that future Candidate additions remain easy to change.
33. As a reviewer, I want the current contract version and Delta recorded explicitly, so that implementation cannot silently override the v1 prohibition on a third layer.
34. As a reviewer, I want fixed Base and Head, Owner overrides and contract version recorded before review, so that Standards and Spec review use one coherent authority.
35. As an offline user, I want all recursive exploration to continue working through `file://` without network, database, AI or production dependencies, so that the product remains safe and reproducible.
36. As an Owner, I want the recursive feature removable without rewriting existing architecture facts, so that the change can be rolled back safely if the interaction or model proves unsuitable.

## Implementation Decisions

### 1. Contract and authority

- The fixed implementation baseline is `4c7f8be1f0be48189804a51ed42aaf8bdb64ab94`.
- The proposed implementation contract is `architecture-explorer/v2`. Owner acceptance of this Spec approves its design but does not make it current.
- During development, v1 remains the only current contract and v2 is stored as proposed without an effective baseline.
- After implementation, freeze candidate Head H containing the complete feature while v2 is still proposed, and run the complete v2 acceptance suite against H.
- After H passes, one metadata-only activation change atomically marks v2 current, records H as its effective baseline, and marks v1 superseded by v2. The v1 normative body remains unchanged. Contract checks and complete validation run again on the activation Head.
- Contract tests enforce exactly one current version before and after the switch. No registry, service or contract-management platform is introduced.
- v2 inherits v1 unless a Delta below explicitly replaces a clause.
- Runtime generation never reads the acceptance contract as architecture data.
- Module Map, existing Candidate Fragments, Source Facts, formal Relationship review data, Node Review data and Continuity remain authoritative for their existing domains.
- The normalized View Projection is generated output and cannot become a second review or Evidence authority.

### 2. v2 Delta from v1

- Replace the prohibition on a third layer with data-driven recursive View navigation having no fixed maximum depth.
- Replace “BACK returns one of two fixed levels” with “BACK pops one active View path entry and restores the originating Appearance focus”. HOME still returns to the system overview.
- Replace the two-item breadcrumb with a complete, read-only View path breadcrumb.
- Replace implicit `hasChildren` click routing with explicit Primary Navigation Action; absent action opens details.
- Permit explicit `open-view`, `open-source-trail` and `open-detail` actions while preserving existing left-click, Enter/Space, context menu, Shift+F10, Escape and text-selection semantics.
- Permit intentional reciprocal View Transitions; prohibit recursive Architecture Decomposition cycles and prevent duplicate active-path growth.
- Keep T7 as the only architecture graph renderer, but allow it to render any normalized View.
- Clarify that a generic deterministic responsive grid for new Views is allowed and is not a graph auto-layout engine. Existing approved View layouts remain unchanged.
- Add Candidate Decomposition as a separate candidate relationship semantic. It uses candidate/unreviewed trust presentation without being mislabeled as collaboration.
- Update the fixed product inventory from 18 to 20 Candidate Nodes, from 8 to 9 Candidate Collaborations, and add exactly 2 Candidate Decomposition relationships for this slice.
- Keep exactly 7 top-level modules, 1 formal Relationship and 13 approved formal Evidence records.
- Update the rendered View inventory from 8 to 9: one system overview, seven existing module Views and one Worker launch boundary internal View.

### 3. Normalized View Projection

- Introduce one versioned generated projection containing a root View ID, a Node index, a Relationship index and a View index.
- The Node index references the existing authoritative Node records. It does not duplicate Evidence, review decisions or continuity state into a new authority.
- The Relationship index preserves each relationship's semantic family and trust state separately. Formal Relationship, Candidate Collaboration, Candidate Decomposition, static call and Evidence linkage are not interchangeable.
- Each View has a globally unique View ID, display name, Perspective classification, optional scope Node ID, coverage note, ordered Appearance list and ordered visible Relationship ID list.
- The system overview is the only View allowed to omit a scope Node ID.
- An Appearance is identified by the pair of View ID and Node ID. A separate Appearance ID is unnecessary while one Node may appear at most once per View.
- Appearance-local data is limited to optional display text and explicit navigation actions. It cannot override Node description, Evidence, review status or continuity.
- Perspective values are a validated, extensible vocabulary based only on real shipped Views. The first recursive slice uses the existing responsibility-oriented Perspective and does not establish a permanent closed taxonomy.
- Navigation Depth is derived from the active View path and is never stored on Node.

### 4. Compatibility adapter

- A single generation-boundary adapter maps the existing top-level module data and existing Candidate Fragment v1 drilldowns into normalized Views.
- Existing module IDs, Candidate Node IDs, Candidate Collaboration IDs, ordering, descriptions, Evidence IDs and review projections remain unchanged.
- Existing navigation IDs should be preserved where possible so that top-level and module View behavior does not acquire a needless migration.
- The adapter may synthesize View-only metadata, but it cannot synthesize new architecture facts or relationships.
- The adapter is deliberately narrow and one-way. Renderer and navigation code consume only normalized Views; they do not retain parallel old/new rendering paths.
- No current input file is rewritten as part of the first slice.

### 5. Additive candidate input

- Add one versioned candidate format capable of declaring Candidate Nodes, Candidate Collaboration and Candidate Decomposition for a scope Node that is not limited to a top-level module.
- Candidate Node descriptions, plain explanations, node types, evidence references and candidate status remain owned by Candidate input, not View definitions.
- New Candidate Evidence may explicitly reference validated Definition and Call Fact Evidence IDs in addition to the existing Source Fact categories. Merely resolving an exact call target never appends Candidate Evidence automatically.
- Every new Candidate Node and relationship needs at least one Evidence ID from the current fixed-baseline Source Facts and must fail closed on unknown or malformed Evidence.
- Candidate input cannot declare an approved or reviewed status.
- Candidate Fragment v1 remains accepted unchanged. The new format is additive and used only where v1 cannot express the recursive scope and new relationship semantic.

### 6. First internal Candidate View

- Add one View scoped to `worker-launch-boundary`.
- Reuse `worker-launch-boundary` as the scope Appearance and `deterministic-worker-wrapper` as an existing cross-View Node Appearance.
- Add exactly two Candidate Nodes, named “Supervisor 进程启动构造” and “Worker 生产入口守卫”; their final stable IDs remain an implementation detail, but their Evidence bindings below are closed for this Spec.
- Add Candidate Decomposition from `worker-launch-boundary` to “Supervisor 进程启动构造”. Evidence is `evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a`, the current Source Fact for `Start-DeterministicWorkerProcess`. Candidate rationale: the function statically contains process argument quoting, process-start construction, launch environment preparation and child-process start handling, which supports interpreting that bounded launch construction as one sub-responsibility of the Worker launch boundary. It does not prove runtime execution or complete boundary ownership.
- Add Candidate Decomposition from `worker-launch-boundary` to “Worker 生产入口守卫”. Evidence is `evidence:sha256:6e36e3c009c8408b2fddd0188c1597ee8fa83511b38d60f09f734a1a4c722aad` for `build_parser` and `evidence:sha256:73ff577c855008cf0261991574392080b5d717caadbea0d82cbd75612fc7d43d` for Worker `main`. Candidate rationale: these current Definition Facts show the accepted launch parameters and the production-side worker, batch/checkpoint guards before checkpoint entry, supporting a bounded entry-guard sub-responsibility. They do not prove that a production run reaches or passes the guards.
- Do not create Candidate Decomposition from `worker-launch-boundary` to `deterministic-worker-wrapper`. T8 already classifies the boundary as a responsibility rather than a physical component and explicitly keeps the wrapper as an adjacent independent component. The existing `candidate-collaboration:supervisor-to-worker-wrapper` remains authoritative for their candidate handoff. Its existing Evidence IDs are `evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429`, the resolved Supervisor script binding, and `evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8`, the deterministic wrapper entry. Candidate rationale: the binding names the wrapper target and the wrapper fact identifies its guarded entry; this supports adjacency and handoff, not containment.
- Do not add a separate “启动构造 → 包装器” Candidate Collaboration. The current automatic Source Facts identify the process-launch function and the wrapper binding/entry independently but do not establish a distinct new collaboration beyond the existing boundary-to-wrapper candidate. Co-location in this View and the expected execution sequence are insufficient.
- Add exactly one new Candidate Collaboration from `deterministic-worker-wrapper` to “Worker 生产入口守卫”. Evidence is `evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8` for the wrapper import/main guard and `evidence:sha256:73ff577c855008cf0261991574392080b5d717caadbea0d82cbd75612fc7d43d` for Worker `main`. Candidate rationale: the wrapper Source Fact identifies the exact Worker main entry it hands control to, while the main Definition Fact contains the production guards; this supports a candidate wrapper-to-entry collaboration without approving runtime reachability.
- The internal View shows the scope Node and the three internal elements, the two Candidate Decomposition relationships, the reused existing boundary-to-wrapper Candidate Collaboration and the one new wrapper-to-entry Candidate Collaboration. The same Node can have a different primary action in a different View.
- The `worker-launch-boundary` Appearance in the production-control View opens the internal View. Its Appearance inside the internal View defaults to details and cannot recursively open the same View.
- All three new relationships and the two new Nodes remain candidate/unreviewed. The reused existing collaboration also remains candidate/unreviewed. The existing reviewed Node projection may still appear on the reused parent Node, but its text must explicitly state that it does not approve the new internal decomposition or collaboration.

### 7. View actions and transition validation

- Supported primary action kinds for this slice are detail, open View and open Source Trail. Detail is the default when no action is present.
- Every non-default action has a user-facing destination label and a validated target.
- An open-View action declares whether it is a decomposition transition, cross-Node transition or Perspective transition.
- A decomposition transition must originate from the Node being decomposed and target a View whose scope Node matches that Node.
- Cross-Node and Perspective transitions may target another valid View without scope matching. They do not imply an Architecture Decomposition Relationship.
- Architecture Decomposition relationships are validated as a directed acyclic graph across the complete Node set.
- The overall View Transition graph is not required to be acyclic.
- Navigation is always caused by a user action; no View automatically redirects to another View.
- If an open-View target is already present in the active path, navigation truncates the path to its most recent existing entry instead of pushing a duplicate. This permits intentional return transitions without an arbitrary maximum depth or unbounded cycle growth.
- Invalid targets, missing labels, unknown action kinds, invalid decomposition scope matches and malformed path frames fail closed.

### 8. Navigation state and interaction

- The navigation path stores View entries rather than assuming each path item is a module ID. Each non-root entry retains the originating Appearance needed for focus restoration.
- BACK removes one path entry. HOME restores the root path. Both clear inline card expansion and transient relationship/detail state according to existing behavior.
- Breadcrumb renders every active View path entry in order and remains read-only in this slice.
- Enter/Space and pointer activation dispatch the same explicit primary action.
- Context Menu/Shift+F10 continues to open existing Node operations and Evidence. Opening or closing Evidence does not change the View path.
- Source Trail retains its own detail-local stack. Its back operation does not pop the architecture View path.
- Closing details, Evidence or Source Trail restores focus under existing rules; BACK restores focus to the Appearance that created the departed View path frame.
- Existing misclick, selected-text, outside-click and Escape protections remain inherited from v1.

### 9. Renderer and layout

- T7 remains the only architecture graph renderer. It receives a normalized current View and resolves referenced Nodes and Relationships through the generated indexes.
- Renderer behavior cannot branch on Navigation Depth, “third level”, “fourth level”, the Worker launch View ID or a specific new Node ID.
- Existing top-level and seven module View layouts and responsive behavior remain unchanged.
- New recursive Views use a common deterministic responsive grid selected from View size, not node identity or depth.
- Semantic relationship family is visible through text or another non-color-only signal. Trust continues to control reviewed-solid versus candidate-dashed treatment.
- Candidate Decomposition and Candidate Collaboration are both candidate/unreviewed but must not share a misleading semantic label.
- Edge endpoints, labels and interaction targets continue to use the same stable Relationship ID.
- The implementation adds no drag, pan/zoom, physics layout, network rendering service or second graph library.

### 10. Trust preservation

- Existing formal Relationship, formal Evidence IDs and ordering, Review Map, Review Source, Review State, Node Review Decision, Node Review State and Continuity are immutable inputs for this slice.
- The approved `worker-launch-boundary` claim remains limited to its accepted responsibility and partial source assignment. Approval does not flow through Node identity, Appearance, View Transition, decomposition or collaboration.
- Existing Candidate status remains candidate even when an approved projection is also displayed for the same Node.
- Source Fact, Candidate Architecture, Reviewed Architecture Claim and UI Projection remain separately labeled.
- Static call resolution, exact target navigation, shared source file or View co-location cannot create Candidate Decomposition, Candidate Collaboration, source ownership or approval.
- View Projection hashes are additive and cannot replace existing candidate, navigation, Source Facts or review hashes.

### 11. Failure behavior

- Duplicate Node, Relationship or View IDs fail closed within their respective namespaces.
- A View referencing an unknown Node or Relationship fails closed.
- A visible Relationship whose endpoints are not available to the View fails closed rather than drawing a partial edge.
- Duplicate Appearance of one Node in one View fails closed for this phase.
- Unknown Evidence, malformed Source Fact reference or Candidate input attempting approval fails closed.
- Architecture Decomposition self-loops and longer cycles fail closed with a deterministic diagnostic identifying the involved stable IDs.
- Reciprocal collaboration, call or View Transition is not rejected merely for being cyclic.
- Recursive navigation data cannot cause automatic page redirection or renderer recursion.
- Existing path-containment, stable-read, UTF-8, syntax, linked-path and output-boundary safeguards remain inherited.

### 12. Rollback and reversibility

- Existing inputs are not migrated or overwritten, so reverting the additive candidate input, View definition, adapter extension and v2 contract restores the v1 product shape.
- The generated View Projection is disposable build output and does not need data rollback.
- No production datastore, user-authored state or review record is mutated.
- The v1 acceptance contract remains available as immutable historical evidence. A code rollback restores v1 as current rather than editing its historical text.
- The compatibility adapter is isolated at generation time, so it can later remain, be replaced by gradual migration, or be removed after migration without changing the renderer contract.

## Testing Decisions

1. **Primary acceptance seam.** The highest test seam builds the real offline Explorer from the fixed repository baseline, applies Source Facts, Candidate, review, continuity and normalized View Projection, opens the generated site in real Chrome, and completes the path from system overview through production control and Worker launch boundary into the internal Candidate View.

2. **Good-test rule.** Tests assert user-visible navigation, explicit trust labels, stable identities, preserved Evidence, fail-closed validation and renderer output. They do not assert helper call order, internal dictionary construction, private adapter functions or pixel-perfect screenshots.

3. **Real three-level product path.** Chrome must activate each Appearance by pointer and keyboard, verify the full breadcrumb, verify that the internal View uses the shared renderer, and confirm that all new internal objects remain candidate/unreviewed.

4. **Depth-independent fixture.** A controlled immutable test input extends valid Views beyond the real product's three architecture levels. The same navigation actions, breadcrumb, BACK, HOME, focus restoration and renderer must work without any depth-specific configuration or branch. This fixture is test-only and does not claim additional production architecture.

5. **Node reuse.** Tests prove that `worker-launch-boundary` and `deterministic-worker-wrapper` retain one Node identity across Views, while their Appearance-local primary actions differ and no review state is copied or widened.

6. **Compatibility regression.** The existing seven top-level modules, 18 legacy Candidate Nodes, 8 legacy Candidate Collaborations, ordering, descriptions, Evidence references and View behavior are deeply equivalent before and after adaptation, apart from the explicitly added recursive transition.

7. **Inventory contract.** Against the fixed v2 baseline, tests require 7 top-level modules, 20 Candidate Nodes, 9 Candidate Collaborations, 2 Candidate Decomposition relationships, 1 formal Relationship, 13 approved formal Evidence records and 9 rendered Views.

8. **Trust regression.** Existing formal Relationship data, 13 Evidence IDs and order, Node Review projection, Decision, State and Continuity are unchanged. New children and relationships never receive approved, reviewed, partial-source-assignment or exact-match treatment merely from their parent.

9. **Evidence validity.** Every new Candidate Node and relationship cites validated Source Fact Evidence IDs from the fixed baseline. Unknown Evidence, automatic Evidence inheritance and exact-call-target promotion are negative cases.

10. **Relationship semantics.** Tests distinguish Candidate Decomposition, Candidate Collaboration, formal Relationship, static call and Evidence linkage. Candidate styling cannot erase the semantic label, and semantic type cannot upgrade trust styling.

10a. **Closed relationship evidence.** Tests bind the two new Candidate Decomposition relationships and the one new Candidate Collaboration to the exact Source Fact Evidence IDs listed in this Spec. They also assert that the existing boundary-to-wrapper collaboration remains a collaboration and that no launch-construction-to-wrapper relationship is synthesized.

11. **Decomposition validation.** Fixtures cover a self-loop, a multi-Node cycle, duplicate decomposition ID, unknown endpoint and valid acyclic shared-child shape. Only illegal decomposition shapes fail.

12. **View Transition validation.** Fixtures cover missing View targets, missing destination labels, invalid action kinds, valid decomposition scope match, invalid decomposition scope match, valid cross-Node target and valid Perspective return transition.

13. **Cycle-safe navigation.** A View A → View B → View A transition truncates to the existing A path entry rather than pushing endlessly. BACK and HOME remain deterministic after the transition.

14. **Default action.** An Appearance without a primary action opens details. No node type, Evidence presence or child count changes this default.

15. **Source Trail isolation.** From the internal View, right-click Evidence opens the existing Source Trail. Source Trail back/close/Escape behavior and focus restoration work without changing the architecture path.

16. **Context-menu preservation.** Pointer and Shift+F10 entry, outside click, Escape, text selection and menu focus behavior remain unchanged at every View depth.

17. **Responsive renderer.** The nine real Views remain valid at the inherited 1440×900, 1280×900 and 960×900 viewports, with no horizontal overflow and with attached edges and readable labels. Same-page resize continues to reconnect geometry.

18. **No depth-specific renderer.** A structural regression check ensures normalized Views use the same renderer entry point. Tests must not add separate third-level or fourth-level renderer fixtures that call different production rendering paths.

19. **Offline boundary.** The generated recursive site works from `file://` and performs no network, database, AI, GitHub or production-service request.

20. **Complete validation.** The authoritative Architecture Explorer validation command must collect and run the complete existing suite plus v2 additions, with no deselection and no skipped required Chrome tests. Firefox remains Owner manual visual/interaction acceptance; Edge remains supplemental and a blocked Edge run is never counted as Chrome PASS.

21. **Contract activation.** Candidate acceptance explicitly targets the proposed v2 contract at candidate Head H while separately proving v1 remains the only current contract. This validation target is test/build input only and is never read by runtime generation. After the activation change, tests prove v2 is the sole current contract, v1 is superseded by v2, v2 records H as its effective baseline, and complete validation still passes.

## Out of Scope

- Migrating or rewriting all seven Candidate Fragment v1 files.
- Replacing existing Module Map, Source Facts, Candidate, Review, Continuity or Evidence authorities.
- Creating a graph database, generic graph framework, query language or graph algorithm service.
- Creating a generic routing engine, URL router or automatic destination inference system.
- Creating a generic review platform or redesigning T8/T9 review mechanisms.
- Approving the two new Candidate Nodes, two Candidate Decomposition relationships, the one new Candidate Collaboration or the reused existing Candidate Collaboration.
- Expanding the approved scope of `worker-launch-boundary`, the formal Supervisor → Worker Relationship or its 13 Evidence records.
- Automatically treating imports, exact calls, shared files or View co-location as Architecture Decomposition or module ownership.
- Supporting duplicate Appearance of one Node within the same View.
- Making Perspective an independently addressable object with lifecycle, inheritance, registry or shared configuration.
- Defining a permanent closed list of all future node types or Perspectives.
- Adding every desired Perspective in the first slice; the feature proves the model with one real internal responsibility-oriented View.
- Building a full source-code graph, whole-repository call graph, runtime trace or coverage visualization.
- Replacing Source Trail or moving its local navigation stack into the architecture View path.
- Adding drag, pan/zoom, physics layout, arbitrary user editing or persisted personal layouts.
- Adding network, database, AI or production runtime dependencies to normal generation or browsing.
- Changing translation, Worker, Supervisor, Provider, QA, checkpoint, candidate-document or release behavior.
- Running `$to-tickets`, `$implement`, committing, pushing, creating a PR, merging, or changing GitHub under this authorization.

## Further Notes

### Acceptance contract decision

This draft recommends an explicit successor contract with a two-stage, file-only state transition rather than a ticket-only exception or a contract-management platform:

- **Design acceptance:** Owner acceptance records v2 as the approved target design. v1 remains the only current product contract; v2 is proposed and has no effective baseline.
- **Candidate acceptance:** freeze candidate Head H containing the complete feature while v2 remains proposed, then run the complete v2 acceptance suite against H.
- **Activation:** after H passes, a metadata-only change sets v2 to current, records H as `effective_baseline`, and changes only v1 status metadata to superseded-by-v2. Its normative body remains unchanged. Contract checks and complete validation run again on the activation Head.
- The activation change and its “exactly one current contract” test land atomically. A failed candidate or activation validation leaves v1 current and v2 proposed.
- v2 inherits all v1 rules not replaced by the Delta in this Spec.
- Contract tests must enforce that exactly one version is current.

This decision is necessary because the feature directly reverses the v1 prohibition on a third layer, changes navigation semantics, adds a relationship family and changes fixed inventories. Treating those changes as a silent v1 clarification would violate the existing contract's change rules.

### Risks and mitigations

- **Trust leakage:** Reused Node identity could make new children look approved. Mitigation: trust is read from each authoritative object; Appearance and View cannot override it; regression tests assert non-propagation.
- **Compatibility adapter becoming a second model:** Mitigation: adapter output is generated, one-way and disposable; it contains references and UI Projection only.
- **Layout special cases returning:** Mitigation: existing layouts are preserved, while new Views use one count-based deterministic grid with no depth or ID branches.
- **Navigation cycles growing history:** Mitigation: View cycles are legal, but a transition to an active-path View truncates to the existing entry.
- **Candidate evidence overreach:** Mitigation: every new candidate explicitly cites validated Source Fact Evidence; exact calls never create candidate records automatically.
- **Contract drift:** Mitigation: v1 remains current during development; v2 inventory and inherited v1 invariants become current only in the atomic activation change after passing integration acceptance.
- **Temporary dual schema lasting indefinitely:** This is acceptable while it remains a narrow adapter. Migration is optional future work, not a prerequisite or hidden obligation of this slice.

### Local draft status

At the time this Spec was written, the fixed integration Worktree was at the exact baseline commit and contained an untracked `GLOSSARY.md`. This Spec is also a local draft. Neither file is part of baseline `4c7f8be1` until separately authorized, reviewed and committed. GitHub Issue #1 and existing review materials were not modified.

### Required Owner decisions before ticket publication

1. Accept `architecture-explorer/v2` as the approved successor design, with v1 remaining current until candidate Head H passes v2 acceptance and a metadata-only activation change records H as the effective baseline while atomically switching statuses.
2. Accept the evidence-audited first-slice inventory: 20 Candidate Nodes, 9 Candidate Collaborations, 2 Candidate Decomposition relationships and 9 Views, while preserving 7/1/13 for modules/formal Relationship/formal Evidence.
3. Accept the active-path cycle rule: a transition to a View already in the active path truncates to the existing entry rather than pushing a duplicate.
4. Accept the audited internal View content shape: two new Candidate Nodes, reuse of two existing Nodes, two new Candidate Decomposition relationships, reuse of the existing boundary-to-wrapper Candidate Collaboration and one new wrapper-to-entry Candidate Collaboration, with the exact Source Fact Evidence bindings listed in this Spec.
5. Confirm that the primary acceptance seam remains the real offline build plus Chrome end-to-end traversal, with focused validator tests and a deeper test-only recursive fixture.

### Readiness

The product goal, domain vocabulary, trust boundaries, compatibility strategy, data contract, first vertical slice, acceptance seam, contract Delta, rollback boundary and non-goals are sufficiently defined for `$to-tickets` after the Owner decisions above are accepted. No additional Grilling is required. Publishing or modifying tracker items remains separately authorized.
