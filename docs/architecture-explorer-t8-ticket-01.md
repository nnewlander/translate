# 01: 可信单节点审核决定与只读投影

**What to build:** 为唯一候选节点 `candidate-node + production-control + worker-launch-boundary` 建立端到端节点审核能力：复用现有 GitHub Review Source、Candidate Fragment、正式 Relationship 和 Explorer build 边界，校验可信 Owner/admin 来源与八类 Review Claims，并在没有生产决定时显示 `Pending Review`，在隔离 fixture 中正确投影 `Current · Approved` 或 `Current · Rejected`。同一纵向切片必须从审核来源和数据契约贯穿到生成数据、现有候选卡片及右键 Evidence 详情；不得安装或制造真实生产 `APPROVED` 决定。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Fixed inputs

- Fixed baseline: `95e219a926023aff0022dbe0174e9a361e20c8b1`
- Contract: `architecture-explorer/v1`
- Source Spec: Architecture Explorer T8 单节点审核投影
- Review object: `candidate-node + production-control + worker-launch-boundary`

## Goal and scope

本 Ticket 只建立 Node Review Decision、八类 Claims 和初始只读 Projection。生产构建在没有可信节点审核来源时必须保持 `Pending Review`；测试可用隔离 fixture 模拟可信 `APPROVED` 和 `REJECTED`。不得扩展其他 17 个候选节点、8 条 Candidate Collaboration、顶层模块源码覆盖或新 Relationship。

八类 Claims 必须作为封闭集合逐项导入和验证：`IDENTITY`、`NAME`、`CLASSIFICATION`、`RESPONSIBILITY`、`PLAIN_EXPLANATION`、`SOURCE_ASSIGNMENT`、`CANDIDATE_EVIDENCE`、`FORMAL_DEPENDENCY_AND_LIMITS`。`CLASSIFICATION=responsibility` 必须携带 Classification Rationale；实际运行、完整源码覆盖、排他归属、相邻组件和 Candidate Collaboration 是否批准等 unknowns 必须保留。

## Existing-system dependencies

- 复用现有 Review Source 的 GitHub 仓库、Issue/comment、作者身份、`OWNER` association、admin permission、时间戳和收据摘要验证；只做节点审核所需的最小参数化/复用，不复制一套平行认证框架，也不得改变既有 T3 来源语义。
- 复用 Candidate Fragment 合并后的唯一目标 Candidate Fact。节点投影必须在 Source Facts、Navigation 和 Candidate Fragments 已形成后执行，不能从文件名或相似名称重建候选事实。
- 复用唯一正式 Relationship、其 T3 Decision 和 13 条批准 Evidence；节点审核只保存精确引用和输入摘要，不复制 Relationship/Evidence payload。
- 复用现有 Explorer build、离线站点、候选卡片、右键菜单、详情 dialog 和 Evidence renderer。普通 build 不访问 GitHub。
- 保持现有 Relationship Review State、T5-Lite 连续性、Candidate Collaboration 虚线和正式关系实线门禁不变。

## TDD seam

使用 Spec 已确认的最高层 seam：

`Candidate Fact + trusted Node Review Decision source + current source facts + existing formal dependencies -> immutable Candidate Fact + Node Review Projection`

测试通过公开的审核导入/离线 build 接口和生成站点观察行为，不测试私有函数，不 mock 自有模块；仅在 GitHub 外部边界使用隔离 fixture。

## TDD — RED

- 先证明无 Node Review Decision 时目标节点只能投影 `Pending Review`，不能借用 T3 Relationship 的批准状态。
- 逐类删除、重复、改名、改值、交换来源绑定或只提供七类 Claims，均必须失败或投影 `Unknown`，不得产生 Current 状态。
- 错误 object kind、parent module、node id、近似名称及相邻 `deterministic-worker-wrapper` 必须被拒绝。
- `CLASSIFICATION` 改为 `component`、缺少 Classification Rationale，或把完整源码覆盖/排他归属写成已知，必须被拒绝。
- 三条 Candidate Evidence 的缺失、重复、换序后的角色错配、未知 ID、相邻 Candidate Collaboration Evidence 替代，以及正式 13 条 Evidence 与候选三条 Evidence 混用，必须被拒绝。
- 错误 Relationship、T3 Decision、T3 result、13 条正式 Evidence、版本化输入哈希、Candidate/Claims 摘要或扫描器版本必须被拒绝。
- 伪造作者、自报 Owner、本地改写正文、非 admin、错误仓库/Issue/comment 或篡改来源收据不得产生可信 Decision。
- fixture 的 `APPROVED` 与 `REJECTED` 必须产生不同 Projection；`REJECTED` 不能被折叠为 Pending 或 `needs-review`。
- 构建前后 Candidate Fact、正式 Relationship 和 Evidence 对象必须深度相等；其他 17 个节点和 8 条协作不能获得节点审核字段。

## TDD — GREEN

- 以最小实现导入并 closed-world 校验可信节点审核来源、八类 Claims、固定对象键、版本化输入哈希和正式依赖。
- 在无来源的真实默认路径生成 `Pending Review + not-reviewed + not-assessed`，不创建伪 Decision。
- 在隔离 fixture 中生成 `Current · Approved` 和 `Current · Rejected`，同时保留 Candidate Fact 的 `candidate` 身份与 unknowns。
- 在现有候选卡片上只增加必要的 Decision/State 可信表达；内联详情继续只显示职责、通俗解释和必要可信状态。
- 在既有右键 Evidence 详情中只读展示八类 Claims、Classification Rationale、unknowns、可信来源、三条 Candidate Evidence 和正式依赖；继续调用同一 Evidence renderer，不增加审核写入控件。

## Acceptance criteria

- [ ] 只有精确对象键 `candidate-node + production-control + worker-launch-boundary` 能获得 Node Review Projection；其余 17 个候选节点和 8 条 Candidate Collaboration 保持未审核。
- [ ] 八类 Claims 以封闭集合逐项存在、绑定和校验；缺失、重复、未知、错值、错来源或部分批准均不能产生 Current 状态。
- [ ] `CLASSIFICATION=responsibility` 的 Rationale 与保留 unknowns 完整存在，且相邻组件/协作未被暗示批准。
- [ ] 可信来源同时验证 Owner 身份、admin 权限、仓库/Issue/comment 身份和内容完整性；普通离线 build 不访问 GitHub。
- [ ] 生产默认不包含真实 `APPROVED`/`REJECTED` 决定，显示 `Pending Review`；隔离 fixture 可覆盖 Current Approved/Rejected。
- [ ] 三条 Candidate Evidence 与 13 条正式 Evidence 精确隔离；唯一 Relationship 和 T3 Decision 只作为正式依赖引用。
- [ ] Candidate Fact、正式 Relationship、T3 Review Source/Map/State 和 Evidence payload 均未被修改、覆盖或复制为第二权威。
- [ ] T7 折叠卡片、内联限制、右键详情、键盘/焦点行为及 Candidate/正式边样式保持不变。
- [ ] 相关 Python 契约测试、离线 build 测试和现有候选 DOM 测试通过；本 Ticket 不得以固定测试数量替代完整收集。

## Trust and UI constraints

- 不自动创建真实审核评论、Decision、Review Source 或批准映射；fixture 不能进入生产输入。
- Pending 是“无可信 Decision”，Rejected 是可信否决，二者不得互换。
- 内联卡片不得显示 Claims 清单、Evidence 数量、ID 或 Evidence 入口。
- 右键详情只读；不得增加登录、批准、拒绝、编辑或联网刷新控件。
- 节点 Decision 不改变正式 Relationship 线型，不批准 Candidate Collaboration，不把 `partial` 提升为完整或排他源码归属。

## Owner verification

Owner 在一个隔离输出中检查三种演示输入：无 Decision、fixture APPROVED、fixture REJECTED。确认目标卡片分别显示 Pending、Current Approved、Current Rejected；右键详情显示八类 Claims、Rationale、unknowns、3 条 Candidate Evidence 和正式依赖；其他节点/协作、正式实线与 T7 内联内容完全不变。Owner 同时确认仓库中没有新增真实生产审核决定。
