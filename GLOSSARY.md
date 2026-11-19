# Architecture Explorer

Architecture Explorer 用可信证据帮助用户逐层理解软件架构，同时明确区分源码观察、候选解释、审核结论与界面展示。

## Language

**Architecture Depth（架构深度）**:
从当前上下文继续探索时，能够带来新的架构或实现理解的有意义路径长度；它由实际结构和理解价值决定，没有预设的最大层数。
_Avoid_: 固定层级数、第三层上限

**Node（节点）**:
具有稳定身份、可被理解和引用的架构或源码对象。Node 可以出现在多个 View 中，但身份复用不扩大任何职责、关系、流程位置或源码归属审核结论的适用范围。
_Avoid_: 卡片、固定层级成员、审核状态容器

**Appearance（视图呈现）**:
同一 Node 在特定 View 中的一次展示与导航位置。Appearance 引用 Node，并只携带该 View 所需的局部展示和导航信息。
_Avoid_: Node 副本、独立审核对象

**View（视图）**:
具有稳定身份、可进入和导航的具体投影，选择需要共同展示的 Node Appearance 与关系。View 不拥有或改变其中对象的事实、Evidence 或可信状态；View 之间可以通过明确的导航往返。
_Avoid_: 权威架构副本、Perspective

**Perspective（观察视角）**:
View 所回答问题的类别，例如职责、流程或代码结构。Perspective 在当前领域中只是 View 的可扩展分类，不是拥有独立生命周期、继承或配置规则的对象；已有分类不构成永久封闭的类型体系。
_Avoid_: View、固定架构分类法

**Primary Navigation Action（主要导航操作）**:
Appearance 明确声明的默认进入行为及其目标；没有声明时打开 Node 详情。该操作及其次级入口必须使用可理解的目的地名称，并且不改变 Node、Relationship 或 Review 的可信状态。
_Avoid_: 按节点类型推断、通用路由规则

**View Transition（视图跳转）**:
从一个 Appearance 明确进入有效目标 View 的导航连接。它可以表示递归分解、跨 Node 导航或观察视角切换，允许有意的往返，但本身不声明架构分解或审核批准。
_Avoid_: 架构关系、可信状态传播

**Architecture Decomposition Relationship（架构分解关系）**:
表达一个 Node 在特定架构解释中被分解为另一个 Node 的有向关系。由架构分解关系构成的结构不得形成循环；这不禁止真实的循环调用、双向协作或往返 View Transition。
_Avoid_: 固定 parent、源码调用、所有权声明

**Navigation Depth（导航深度）**:
Node 的 Appearance 相对于当前 View 入口的路径距离。它由当前导航路径计算，不是 Node 的永久属性。
_Avoid_: Node level、固定层级

**Source Fact（源码事实）**:
从指定源码快照中可重复观察到的静态事实。Source Fact 本身不声明架构归属、运行时行为或审核结论。
_Avoid_: 架构结论、运行时证明

**Candidate Architecture（候选架构）**:
由 Source Facts 和其他明确证据支持、但尚未完成 Owner 审核的架构解释。它可以展示和扩展，但不得冒充已审核事实。
_Avoid_: 已确认架构、正式架构

**Candidate Fragment（候选片段）**:
一组由真实 Source Facts 支持、保持未审核状态的 Candidate Architecture Node 与关系。Candidate Fragment 不拥有 View 导航，也不产生 Reviewed Architecture Claim。
_Avoid_: View 定义、审核决定、第二事实源

**Reviewed Architecture Claim（已审核架构主张）**:
Owner 针对特定证据、源码快照和明确边界所接受的架构结论。其效力不超出审核时批准的主张范围。
_Avoid_: 全面源码覆盖、运行时保证

**UI Projection（界面投影）**:
对 Source Facts、Candidate Architecture、Reviewed Architecture Claims 及其当前可信状态的只读展示。界面投影不创造事实，也不提升任何对象的可信状态。
_Avoid_: 权威数据源、审核结果
