# Architecture Explorer T5-Lite Delivery Record

本记录对应 GitHub Issue #6 经 Owner 正式修订后的 T5-Lite 第一版范围。它记录实现基线、独立 Code Review、Owner 最终功能验收、自动化验证和本地集成准备状态。本记录不代表 Push、PR 已创建、Merge、Issue 关闭或远端集成完成。

## 版本与集成状态

- T5-Lite 固定起点：`23f9833c2cf732972eda374d6707f371b3359426`。
- T5-Lite 实现与独立 Review HEAD：`3e9addb8db247ad7996a00f15d5e46056e7e3536`。
- 开发分支：`codex/architecture-explorer-t5-lite`。
- 本 Delivery Record 所在提交为单独的 documentation-only 提交；其父提交固定为上述实现 HEAD。
- 目标分支：`experiment/matt-skills`。
- 2026-11-04 实时只读查询 `origin`：`refs/heads/experiment/matt-skills` 指向固定起点 `23f9833c2cf732972eda374d6707f371b3359426`。
- 当前本地同名分支仍停留在较早的 `aacc55cd1f2d0ced3ce80b95c20a1dfe0763f8f6`，比远端目标少 37 个提交；后续 PR 必须以远端目标引用为准，不能把该过期本地分支当作集成基线。
- 远端尚无 `codex/architecture-explorer-t5-lite` 分支；本轮没有 Push 或创建 PR。

## 正式范围

Owner 在 Issue #6 的[范围修订评论](https://github.com/nnewlander/translate/issues/6#issuecomment-6614063161)中用 T5-Lite 取代旧的复杂 T5 可信审计契约。本交付只实现以下闭环：

```text
T2 真实源码扫描与稳定 Evidence ID
  → 比较 T3 已审核的首条 Supervisor → Worker 关系和 13 条证据
  → 显式 update-review-state 记录连续性状态
  → 只读 build/check 重新扫描并保守评估
  → T4 仅在可信状态完整匹配时发布 partial 下钻
```

正式 review-state 位于：

`output/architecture-explorer/t5-lite/review-state.v1.json`

该文件由 Git 实际追踪。起点至实现 HEAD 的差异中，它是唯一新增的 T5-Lite `output/` 状态文件；Owner 验收页面、截图和浏览器证据均位于被 `.gitignore` 排除的生成输出中，不属于 PR。

## 交付行为

- 复用 T2 的真实源码扫描和稳定 Evidence ID，不修改身份规则。
- 对 T3 正式批准的 13 条生产 Evidence ID 按集合做精确比较；扫描顺序变化不影响审核状态，T3 原始展示顺序保持不变。
- 空行和纯位置移动保持 `current-reviewed / exact-match`。
- Evidence ID 变化、证据缺失、歧义或解析失败保守进入 `needs-review` 或 `unknown`。
- 已通过显式命令写入、审阅并提交的失效状态不会被普通 build/check 或源码恢复自动清除。
- 只有新的、通过现有 T3 Owner/admin 来源验证的审核决定可以重新建立 `current-reviewed`。
- review-state 缺失、损坏或 decision 不一致时只允许历史查看，不能宣称当前审核有效。
- T4 只在 review-state 有效、T3 来源可信、当前扫描完整且 13 条正式 Evidence ID 精确匹配时发布 `partial` 下钻。
- `build` 严格只读；`update-review-state` 是唯一状态写入口，使用同目录临时文件、`fsync` 和原子替换。

## 废弃复杂 T5 排除确认

当前分支从固定起点只包含以下两个实现提交：

- `d8f06fcb6a4d024abbf44d789c5013abbf8d1ed8` — `feat: simplify Architecture Explorer T5 continuity`
- `3e9addb8db247ad7996a00f15d5e46056e7e3536` — `fix: ignore T5 evidence ordering changes`

旧复杂 T5 的 `fa868d6...`、`1e39331...`、`36ca0f2...`、`ac57d4a...` 和 `b15561c...` 均不是当前 HEAD 的祖先。PR 不包含 authoritative head、GitHub continuity-head receipt、hash-chain、pin、decision generation、`INITIAL/CONTINUE/NEW_REVIEW` 状态机、复杂多 writer 协议或 ID-changing equivalent-relocation 实现。

## 独立 Code Review

- 最终结论：`PASS`。
- Standards findings：0。
- Spec findings：0。
- 独立完整 Architecture Explorer 测试：`128 passed`。
- 独立复审确认 P1 证据顺序变化误报和 P2 跨干净 Worktree 状态持久化均已解决。
- 独立复审确认未修改原 T3 审核事实、13 条正式 Evidence ID 或生产源码。

## Owner 最终功能验收

Owner 已确认 T5-Lite 全部验收场景满意，并在排除一次非代码下钻疑问后重新验证：

- `current-valid`：13 条正式证据有效，关系为 `current-reviewed / exact-match`，T4 合法显示 `partial`；“生产调度与控制”可下钻到3个候选职责。
- `position-move`：完整移动 `build_parser` 后 Evidence ID 集合保持不变，不产生误报，T4 下钻正常。
- `material-change`：真实 Evidence ID 变化进入 `needs-review / material-change`，不发布可信 T4 下钻。
- `recorded-invalidation-restored`：失效状态经显式更新并提交后，即使源码恢复并在新干净 Worktree 构建，仍保持 `needs-review`。
- “返回上一级”和“返回最高级”交互均正常。

此前的模块下钻疑问使用 Chrome headless 真实鼠标事件复查后未发现代码、数据或资源加载缺陷；Owner 随后重新打开 `current-valid` 和 `position-move` 页面确认正常。

## 验证环境与结果

所有 Python 检查使用项目解释器 `D:\translate\.venv\Scripts\python.exe`，`sys.prefix` 为 `D:\translate\.venv`，`python -m pip check` 报告 `No broken requirements found.`。

实现 HEAD 上的本地完整回归：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
$env:PYTHONDONTWRITEBYTECODE = '1'
& $python -m pytest -p no:cacheprovider tests\architecture_explorer -q
```

结果：`128 passed in 98.06s`。

真实 Chrome headless 验证使用最终生成的离线站点和真实鼠标事件，而不是只调用 DOM `.click()` 或 reducer：

- `current-valid`：进入第二层，显示3个候选职责；BACK 和 HOME 均返回顶层；0 个 JavaScript 运行时错误。
- `position-move`：相同流程通过；0 个 JavaScript 运行时错误。
- `material-change` 和 `recorded-invalidation-restored`：`navigation.drilldowns` 为0，关系状态为 `needs-review`，未发布失效关系的 `partial` 下钻。

Owner 验收输出是本地生成材料，不作为 PR 受控交付物；可复现事实由测试、正式 review-state、实现代码和本 Delivery Record 固定。

## 已接受的 T5-Lite 第一版边界

- 未经 `update-review-state` 写入并提交的临时观察，不构成跨源码恢复的持久失效事实。
- 不防御恶意 Git 历史回滚或受信任代码与状态文件整体回滚；该风险依赖受保护分支、PR 审核和正常 Git 治理。
- 不支持 ID-changing equivalent-relocation；任何真实 Evidence ID 变化都需要新的可信 T3 审核。
- 不提供 authoritative head、hash-chain、pin、复杂审核世代状态机或复杂多 writer 协议。
- 不以防御任意伪造的 Explorer JSON、任意伪造 Git 状态或脱离仓库的独立审计材料为目标。

这些是 Owner 正式接受的 T5-Lite 第一版范围边界，不是未解决的 Code Review findings。

## 本地集成准备结论

- 实现范围、独立 Review、Owner 功能验收和完整 Architecture Explorer 回归均已完成。
- 正式 review-state 已追踪，验收输出未进入 PR。
- 目标远端基线与固定起点一致，T5-Lite 实现 HEAD 在其上仅增加两个实现提交；本 Delivery Record 将作为第三个 documentation-only 提交。
- 当前仅完成本地准备。Push、创建 PR、Merge、Issue #6 关闭和任何远端分支更新仍需 Owner 另行明确授权。
