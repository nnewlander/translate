# Architecture Explorer T4 Delivery Record

本记录对应 GitHub Issue #5 的 T4 实施、独立 Code Review FAIL 定向修复和正式集成准备。它记录可复现证据；Owner 已确认最终界面验收通过，独立定向复审结论为 `PASS WITH RESIDUAL RISK`。本记录不代表 Push、Merge、Issue 关闭、正式集成完成或 T5 授权。

## 版本与集成状态

- T4 授权起始 Commit：`ac73b3612b496ac45f0bc707db434bc1d621697a`。
- 独立 Review FAIL 待修复 Commit：`32b807b692d7d4f3d665cd9454fb36c5e70d9ce3`。
- 本轮实际应用验证 Commit：`3d2600b9734ac9532023b2181a4dab6538bf8c13`。
- 独立定向复审 Commit：`e34ee4fc4835e87bc91b79df66c368574b67a483`。
- 分支：`codex/architecture-explorer-t4`。
- 正式集成基线：**Pending — 尚未进入**。
- 独立 Reviewer 复审：**完成 — `PASS WITH RESIDUAL RISK`，Standards 与 Spec 均为 0 findings**。
- Owner 最终界面验收：**完成 — Owner 于 2026-11-03 确认最终浏览器界面没有问题，同意结束 T4 开发与 UI 修复阶段**。本记录不推断 Owner 使用的浏览器、窗口尺寸或具体操作步骤。

## 独立 Review FAIL 修复

### P1：右键菜单双重点击抑制

- reducer 不再保存第二份 `suppressPrimaryOnce` 状态。`OUTSIDE_POINTER` 和 `ESCAPE` 只关闭菜单。
- DOM 捕获层只吞掉触发菜单外关闭的同一次 pointer/click 序列；之后的正常模块点击立即执行。
- 新增真实 Chrome/Edge 无头浏览器 DOM 测试，使用生成站点和实际 `app.js` 验证捕获/冒泡顺序：菜单外点击只关闭菜单、不下钻；下一次点击立即下钻；Esc 关闭后下一次点击也立即下钻。

### P2：失效关系不能支持 partial

- T4 `partial` 源码归属只接受已存在、`review_status=current-reviewed` 且 `source_continuity.status=exact-match` 的 T3 关系。
- 不满足门禁时构建拒绝并抛出 `ExplorerInputError`，不会继续发布暗示证据当前有效的 `partial` 文字。
- 反例覆盖 `unknown`、`needs-review` 和 `missing`。
- T3 审核映射、关系对象、Evidence ID 和审核语义均未修改。

### 返回导航语义

- 导航状态使用统一的 `navigationPath`。
- `BACK` 删除一个路径段，返回上一级。
- `HOME` 直接重置为根路径 `top-level`。
- 模拟三段路径的纯状态测试证明二者语义不同；没有新增虚假的第三层产品模块。当前产品只有两层，因此实际界面目的地相同。

### T4 Delivery Record 完整性

- Delivery Record 明确区分应用验证 Commit、独立复审 Commit 和后续交付文档 Commit。
- Owner 界面验收、独立复审结论、自动化证据、正式集成状态与剩余风险分别记录，不把未执行检查描述成通过。
- 正式集成、Push、Merge、Issue 关闭和 T5 仍需后续明确授权。

## 验证环境

所有 Python 命令使用项目解释器 `D:\translate\.venv\Scripts\python.exe` 和当前 worktree 的 `src`。虚拟环境前缀校验通过，`python -m pip check` 结果为 `No broken requirements found.`。

## 测试命令与结果

以下结果在应用验证 Commit `3d2600b9734ac9532023b2181a4dab6538bf8c13` 上执行，均禁用 pytest cache：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t1-3d2600b `
  tests\architecture_explorer\test_top_level_explorer.py
```

结果：`39 passed in 14.02s`。

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t2-3d2600b `
  tests\architecture_explorer\test_supervisor_worker_candidate.py
```

结果：`28 passed in 24.90s`。

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t3-3d2600b `
  tests\architecture_explorer\test_reviewed_relationship.py `
  tests\architecture_explorer\test_t3_security_repairs.py
```

结果：`28 passed in 2.00s`。

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t4-3d2600b `
  tests\architecture_explorer\test_t4_navigation.py
```

结果：`16 passed in 1.29s`。其中包含真实无头浏览器 DOM 顺序测试、P2 三组失效关系反例、BACK/HOME 多段路径语义和已有 T4 交互回归。

`node --check architecture-explorer\web\t4-state.js`、`node --check architecture-explorer\web\app.js` 和 `git diff --check` 均通过。

## 独立定向复审验证

独立 Reviewer 在干净的临时 Git 工作树中检出最终待审查 HEAD
`e34ee4fc4835e87bc91b79df66c368574b67a483`，使用项目虚拟环境运行完整
Architecture Explorer 测试：

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\t4re-tests-e34ee4f\all-escalated `
  tests\architecture_explorer
```

结果：`111 passed in 43.92s`，无失败、无 skip。该次执行包含真实 Chrome headless DOM 交互测试和 Windows junction 安全测试。独立复审还确认：

- 菜单外点击只关闭菜单，下一次模块点击立即生效；Esc 关闭后下一次点击正常；
- reducer 不再保留第二份点击抑制状态；
- `partial` 只接受 `current-reviewed + exact-match`，并拒绝 `unknown`、`needs-review`、`missing`、`material-change`、`ambiguous`、`parse-failure` 和 `equivalent-relocation`；
- `BACK` 只返回上一级，`HOME` 直接回到根节点；模拟路径不进入产品导航数据；
- T3 关系对象、Relationship ID、13 条生产 Evidence ID 和来源记录保持不变；
- 最终 HEAD 离线构建为 `current-reviewed`、`exact-match`，实现 Commit 与工作树状态记录正确。

独立复审最终结论为 `PASS WITH RESIDUAL RISK`；Standards 和 Spec 均为 0 findings。

## 离线页面与数据核验

- 页面：`output/architecture-explorer/t4-3d2600b/site/index.html`
- `explorer-data.json` SHA-256：`6dd04586535b3166854f08839c2a117cc392c56f8e0f49b526cf085ff48883c1`
- 生成时实现 Commit：`3d2600b9734ac9532023b2181a4dab6538bf8c13`
- 生成时 worktree：`clean`
- T3 关系状态：`current-reviewed`
- T3 源码连续性：`exact-match`
- T3 生产 Evidence ID：13 条

## 已完成验证

- **Complete**：T1–T4 专项自动化测试。
- **Complete**：真实 Chrome 无头 DOM 捕获/冒泡顺序测试。
- **Complete**：P2 `unknown`、`needs-review`、`missing` 反例。
- **Complete**：BACK/HOME 统一路径模型和模拟多层状态测试。
- **Complete**：离线构建、JS 语法、数据摘要、T3 状态和证据数量核验。

## Pending 与已知风险

- **Pending**：正式集成授权、Push 和 Merge；当前 Commit 尚未进入正式集成基线。
- **Pending**：仓库全量非 Architecture Explorer 测试；本轮只运行 T1–T4 专项测试。
- 已知风险：自动 DOM 回归和独立自动化验证使用 Chrome headless；Firefox、其它浏览器及更多窗口尺寸未完成独立交叉验证。
- 已知风险：Owner 已确认最终界面没有问题，但该确认不提供可推断的浏览器、窗口尺寸或逐步操作证据。
- 环境记录：独立复审中沙箱内 Chrome 因 crashpad 命名管道权限失败；按环境权限规则在沙箱外重跑后，T4 专项测试 `16 passed`，完整 Architecture Explorer 测试 `111 passed`。

## T3 与 T5 语义确认

- T3 正式审核记录、关系 ID、关系状态、连续性状态、13 条生产 Evidence ID 和证据解释未修改。
- T4 只新增对现有 T3 关系可信状态的消费门禁，不维护第二份关系事实。
- 未实现或启动 T5 的源码变化检测、等价重新定位或证据失效更新流程；T5 数据语义未改变。
