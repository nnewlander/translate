---
contract_family: architecture-explorer-acceptance
contract_version: 1
contract_ref: architecture-explorer/v1
status: current
effective_baseline: 47ca4276075735508c23e7caa3c03965d93cde5b
runtime_authority: false
reviewer_conflict_policy: report-and-block
downstream_reference_fields: contract,inherits,delta,owner-overrides
spec_reviewer_inputs: contract,ticket-delta,owner-overrides,fixed-base-head
top_level_modules: 7
top_level_module_ids: document-understanding,scope-and-request,translation-execution,quality-and-repair,production-evidence,production-control,candidate-and-release
reviewed_relationships: 1
reviewed_relationship_id: relationship:production-control:launches:translation-execution
approved_evidence: 13
candidate_nodes: 18
candidate_collaborations: 8
inline_evidence: false
evidence_entry: context-menu-detail
reviewed_edge_style: solid
candidate_edge_style: dashed
reviewed_relationship_gate: current-reviewed+exact-match
desktop_viewports: 1440x900,1280x900,960x900
continuous_resize: 1440x900>1280x900>960x900>1440x900
automated_browser: chrome
owner_manual_browser: firefox
supplemental_browser: edge
supplemental_blocked_is_pass: false
---

# Architecture Explorer v1 当前验收契约

本文件是 Architecture Explorer 当前产品行为的唯一验收入口。它冻结固定基线已经通过
Owner 验收的 T7 行为，供 T8 及后续 Ticket 引用；它不参与运行时生成、审核或 Evidence
解析，也不授权从本文重建架构数据。

## 权威边界

产品实现继续只消费现有生成数据、Review Map、Review Source、Review State 和候选片段。
本契约只描述如何验收这些数据的现有展示，不是第二份模块、Relationship、Candidate 或
Evidence 事实。

发生差异时按以下顺序确定 Spec 审查输入：

1. 本契约的当前版本；
2. 当前 Ticket 明确声明的差异；
3. Owner 对该 Ticket 最新、明确批准的覆盖决定。

更早的 Spec、Issue、PR、交付记录和会话只作为历史证据。Architecture Explorer 父 Spec
#1 以及 T7 Issues #22–#25 保持可追溯，但不与本文件并列充当当前验收规范。历史材料不因
本契约而被删除或重写。

## 规范性验收条款

下表是正文关键职责的稳定索引。条款值只用于最小结构一致性检查；其解释和产品边界以紧随
其后的对应章节为准，运行时不得读取本表来生成架构或审核数据。

| 条款 ID | 封闭值 |
| --- | --- |
| AE-NODE-DETAIL | responsibility+plain-explanation+required-trust-state |
| AE-EVIDENCE-ENTRY | context-menu-detail-only |
| AE-REVIEWED-RELATIONSHIP | solid+current-reviewed+exact-match |
| AE-CANDIDATE-COLLABORATION | dashed+candidate-unreviewed |
| AE-CHROME | required-automated |
| AE-FIREFOX | owner-manual |
| AE-EDGE | supplemental+blocked-never-pass |
| AE-REVIEWER-INPUTS | contract+ticket-delta+owner-overrides+fixed-base-head |
| AE-REVIEWER-CONFLICT | report-and-block |
| AE-T8-INHERITANCE | contract+inherits+delta+owner-overrides |

## 当前验收清单

七个已批准的顶层逻辑模块是：

| 稳定 ID | 当前名称 |
| --- | --- |
| `document-understanding` | 文档理解与定位 |
| `scope-and-request` | 工作范围与请求构造 |
| `translation-execution` | 翻译执行与模型接入 |
| `quality-and-repair` | 质量判断与受限修复 |
| `production-evidence` | 生产证据与 Checkpoint |
| `production-control` | 生产调度与控制 |
| `candidate-and-release` | 候选文档与发布验证 |

这些模块的逻辑定义已批准，源码覆盖仍是 `unverified`；契约不把设计批准提升为源码覆盖
完成。

当前正式图只包含一个稳定 Relationship：
`relationship:production-control:launches:translation-execution`。它引用现有 Review Map
批准的 13 条生产 Evidence。Evidence ID、顺序、源码定位和审核来源继续由 Review Map、
Review Source 与 Review State 持有，本文不复制它们。

七个现有候选片段合计包含 18 个候选节点和 8 条候选协作。它们全部保持
candidate/unreviewed；具体 ID、端点、Evidence 和说明继续由候选片段持有，本文只冻结
验收数量和可信边界。

## 节点、详情与 Evidence

- 折叠的顶层节点只显示名称，以及区分逻辑定义批准与源码覆盖所需的最小可信状态。
- 折叠的候选节点显示名称、节点类型及明确的 candidate/unreviewed 状态；`partial` 不能暗示
  节点已批准或源码覆盖完整。
- “详细展示”在原卡片内显示既有专业职责、通俗解释和必要可信状态；允许多个节点同时展开。
  下钻、BACK 或 HOME 清空展开状态，打开或关闭 Evidence 详情不清空。
- 内联节点不显示 Evidence 数量、ID 或入口。全部可定位 Evidence 继续通过既有右键
  “查看关系与源码证据”详情访问，并由现有 Evidence renderer 展示。
- 左键、Enter/Space、Context Menu/Shift+F10、Escape、焦点恢复、文字选择和菜单外点击继续
  保持 T7 已验收语义；本契约不引入新操作。

## 关系线与可信状态

- 图上主标签优先使用既有可读中文关系名称。完整类型、审核状态、稳定 ID、Evidence 与限制
  继续在既有详情中显示。
- 只有同时满足 `current-reviewed + exact-match` 的正式 Relationship 获得实线和当前已审核
  表达；颜色不是唯一状态信号。
- 候选协作始终使用虚线及 candidate/unreviewed 表达，Evidence 完整也不能提升其审核状态。
- historical、needs-review、unknown、material-change、missing 或已记录失效的正式关系不得获得
  当前有效实线待遇，也不得错误支撑 `partial`。源码恢复不能自动清除已记录失效。
- 所有线都从当前数据中的 source 指向 target，不从 import、文件名、节点位置或相似名称推断
  新关系。边、标签和辅助入口按同一稳定 ID 打开同一详情。
- 黄色最小生产流程只高亮两个正式节点及唯一当前有效正式边，不移动节点、改变 Evidence 或
  提升候选协作。

## 导航与离线边界

保留 T6/T7 已验收的白底与植物绿节点语言、粉色固定 BACK/HOME、只读面包屑、源码快照、
源码事实、历史与辅助区，以及 `file://` 离线只读构建。BACK 返回一级，HOME 返回最高层；
左键对有下级节点执行下钻，对叶子节点打开既有详情。不得加入原型工具栏、调试面板、
第三层、自动布局、拖拽、pan/zoom、网络、数据库、AI 或生产运行依赖。

## 响应式与浏览器职责

- `1440×900`、`1280×900`、`960×900` 三种真实桌面 viewport 均不得出现页面或图面横向
  溢出；端点保持附着，箭头方向正确，关系标签避开节点主要内容。
- 同一页面不 reload 地完成 `1440→1280→960→1440` 后，八个图层的节点顺序稳定，九条边
  重新连接；展开、收起、滚动和布局变化后也必须重新核对几何。
- Chrome 是必须通过的自动化回归浏览器，测试必须核对实际 viewport，而非只相信请求的
  window size。
- Firefox 是 Owner 人工视觉与交互验收浏览器；未执行时不得声明 PASS。
- Edge 是补充兼容性验证。环境阻塞或空 DOM 必须独立报告，既不能算作 Chrome PASS，也
  不能被静默隐藏。

## Spec Reviewer 前置核对

Reviewer 在开始 Spec 轴审查前必须记录：

- 采用的契约引用和版本；
- 固定 Base/Head；
- 当前 Ticket 的差异；
- Owner 最新批准的覆盖决定，以及被它取代的旧条款。

若这些输入互相冲突，Reviewer 先列出冲突并将 Spec 依据标为未收敛；不得自行选择旧规则、
替 Owner 作产品决定，或要求实现同时满足矛盾条款。冲突解决后才继续判断实现符合性。

## 下游引用格式

T8 及后续 Spec 使用以下最小声明，不复制本契约全文：

- `Contract: architecture-explorer/v1`
- `Inherits: all current invariants`
- `Delta:` 本 Ticket 新增或改变的行为
- `Owner overrides:` 最新正式批准的覆盖决定；没有则写 `None`

未列入 `Delta` 的 Architecture Explorer 行为继续继承本契约。下游 Ticket 不得借引用本契约
获得实施、审核、Push、PR、Merge 或生产操作授权。

## 变更规则

任何改变当前行为或可信边界的提议都必须通过新 Ticket 和 Owner 决定；不得静默修改本文件
来追认实现。仅澄清文字且不改变验收含义时可保持 v1；改变产品行为、数据清单或可信门禁时
必须显式说明版本影响。历史版本继续保留为历史证据，且任何时刻只能有一个版本标记为
`current`。
