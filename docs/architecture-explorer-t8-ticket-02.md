# 02: 节点审核连续性、历史失效与 Chrome 门禁

**What to build:** 在 Ticket 01 的可信 Decision/Claims 投影之上，增加独立、显式、可原子更新的 Node Review State。它必须区分版本化审核输入哈希与当前源码连续性，正确处理无关源码编辑、纯行号移动、材料变化、缺失、歧义、解析失败和已记录失效；并把 Current、Historical、Unknown 状态贯穿到现有卡片、右键详情和权威 Windows/Chrome 验证入口。

**Blocked by:** 01: 可信单节点审核决定与只读投影。

**Status:** ready-for-agent

## Fixed inputs

- Fixed baseline: `95e219a926023aff0022dbe0174e9a361e20c8b1`
- Contract: `architecture-explorer/v1`
- Source Spec: Architecture Explorer T8 单节点审核投影
- Review object: `candidate-node + production-control + worker-launch-boundary`

## Goal and scope

本 Ticket 只完成同一节点 Decision 的当前有效性评估、历史失效记忆及最终 UI/CI 验收。它不新增审核对象、不创建真实 Decision、不改变八类 Claims 或其批准范围。

Node Review State 与 Decision 必须独立：Decision 仍为 APPROVED/REJECTED 或不存在；State 仅为 `current`、`needs-review`、`unknown`、`not-reviewed`，并记录对应 continuity、decision id、评估快照、时间和原因。UI Projection 必须封闭映射为 `Pending Review`、`Current · Approved`、`Current · Rejected`、`Historical · Approved`、`Historical · Rejected`、`Unknown`。

## Existing-system dependencies

- 复用现有显式 Review State 更新、原子写入、损坏状态拒绝覆盖和“已记录失效不能由源码恢复自动清除”的 T5-Lite 语义；不得修改 Relationship Review State 或用它代替 Node Review State。
- 复用现有 Source Facts scanner 的稳定 Evidence ID、结构锚点、symbol、规范化 snippet、角色绑定和 locator；不建设通用调用图或新扫描框架。
- 复用 Ticket 01 的目标对象、八类 Claims、可信 Decision 来源及三条/十三条 Evidence 隔离。
- 复用现有 Explorer build、T7 卡片/详情、真实 Chrome DOM harness、三种桌面 viewport、同页连续 Resize 和权威 Windows/Chrome 验证入口。

## TDD seam

继续使用 Ticket 01 的同一最高层 seam；本 Ticket 只扩大该 seam 的当前源码输入和可观察 State/Projection 输出，不建立第二个连续性 API 或横向测试层。

测试通过显式状态更新命令、离线 build 输出和真实浏览器页面观察行为。源码变化全部使用隔离 repository fixture；不得修改真实生产源码制造负向场景。

## TDD — RED

- 版本化 Candidate Fragment、Navigation、T3 Review Map/Source、Candidate/Claims 摘要或扫描器版本不匹配时必须 `needs-review + material-change`；不得被“当前源码仍相似”掩盖。
- 仅 revision、扫描时间、相关源码文件哈希或 locator 变化时不能直接判失效；必须继续完成语义连续性评估。
- 在目标 Evidence 前后插入空行、移动完整函数/片段或进行其他唯一可重定位的纯行号移动时，三条 Evidence 的身份、结构锚点、symbol、规范化 snippet 和角色绑定仍匹配，结果必须保持 `current + exact-match` 并只更新 locator。
- 与目标绑定无关的源码或文档编辑不得导致材料失效。
- Candidate Fact 语义、对象键、任一 Candidate Evidence 身份/数量/角色、正式 Relationship、T3 Decision、13 条正式 Evidence、unknowns 或 Claims 摘要改变时必须 `needs-review + material-change`。
- 必需对象/Evidence 缺失或正式依赖失去既有可信门禁时必须 `needs-review + missing`。
- 多重定位必须 `unknown + ambiguous`；扫描/解析失败必须 `unknown + parse-failure`，不得猜测。
- 已显式记录 `needs-review` 或 `unknown` 后，即使源码恢复，重复状态更新也不得自动回到 current；只有新的可信 Owner/admin Decision 才能恢复。
- 状态文件缺失、损坏、字段超集/缺集、decision id 不一致或原因无效时不得显示 Current；损坏/不一致文件不得被静默覆盖。
- Approved 与 Rejected 在失效后必须分别投影 Historical Approved/Rejected；不可判定时必须投影 Unknown，而不是 Pending。
- 任一历史或 Unknown 状态不得改变 Candidate Fact、正式 Relationship、Candidate Collaboration 或边样式。

## TDD — GREEN

- 实现节点级显式状态更新与只读评估，使用原子写入并保持 closed-world schema。
- 将版本化审核输入精确性与当前源码连续性分成两个判断阶段：前者保护 Decision 输入，后者允许稳定 Evidence 的等价重定位。
- 对 pure relocation 更新当前 locator 但保持 Decision、Claims 和 Candidate Fact 不变；对材料变化/缺失/歧义/解析失败输出封闭状态与原因。
- 在现有卡片和右键详情中投影 Current、Historical 和 Unknown；右键详情展示连续性原因和当前 locator，内联仍不展示 Claims/Evidence 清单。
- 将节点审核关键数据、历史失效及真实 Chrome UI 场景加入权威验证的 required collection/execution 门禁，确保被 deselect、skip、未执行或缺少 viewport 证据时整体不能 PASS。

## Acceptance criteria

- [ ] Node Review State 独立记录 decision id、state、continuity、评估快照、时间和原因；Decision 与 Candidate Fact 均不被状态更新改写。
- [ ] 版本化审核输入哈希不匹配与当前源码变化被分开判断；前者是材料变化，后者只触发重新扫描和连续性评估。
- [ ] 无关编辑和唯一等价重定位的纯行号移动保持 `current + exact-match`；locator 可更新但审核语义不变。
- [ ] 材料变化、缺失、歧义和解析失败分别进入规定的 `needs-review`/`unknown` 状态并给出封闭原因。
- [ ] 已记录失效具有记忆；源码恢复不能自动重新批准或重新拒绝，只有新的可信 Owner/admin Decision 可恢复 Current。
- [ ] Pending、Current Approved/Rejected、Historical Approved/Rejected 和 Unknown 在数据与 UI 中不混淆。
- [ ] 三条 Candidate Evidence、13 条正式 Evidence、唯一 Relationship、八类 Claims 和其余 17/8 对象在全部状态下继续精确隔离。
- [ ] T7 卡片、右键 Evidence renderer、键盘/焦点、正式实线、候选虚线、导航和离线只读行为保持不变。
- [ ] 1440×900、1280×900、960×900 及同页 1440→1280→960→1440 的真实 Chrome 测试覆盖新增状态，无横向溢出、遮挡、边端点脱离或 reload。
- [ ] 权威验证入口收集并执行完整 Architecture Explorer suite；节点审核关键测试和必需 Chrome 测试不得 skip、deselect、漏收集或漏执行。
- [ ] Firefox 继续由 Owner 人工验收；Edge 仍为补充，未运行或环境阻塞不得声明 PASS。

## Trust and UI constraints

- 状态更新只评估已验证 Decision，不授予批准；不得自动创建或替换真实 Owner/admin Decision。
- 历史状态保留原 Decision 以供审计，但不得获得当前有效样式或暗示当前源码已批准/拒绝。
- UI 只消费生成投影；不得从 CSS、节点位置、文件名或相似 Evidence 推断状态。
- 右键详情保持只读；内联卡片不显示 Claims 清单、Evidence 数量、ID 或入口。
- 不弱化现有 Chrome、正式 Relationship 或 `current-reviewed + exact-match` 门禁来获得绿色结果。

## Owner verification

Owner 使用隔离 fixture 依次验证：当前 Approved、当前 Rejected、纯行号移动、无关编辑、材料变化、缺失、歧义、解析失败和“失效后源码恢复”。确认前两类保持 Current，材料/异常类按 Spec 降级，恢复不会自动清除历史失效；页面在三种 viewport 和连续 Resize 中保持 T7 行为。最后检查统一验证摘要明确报告完整测试执行、必需 Chrome PASS，以及 Firefox/Edge/非 Explorer 的真实 NOT_RUN/BLOCKED 状态。

