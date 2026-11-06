# Architecture Explorer T6-4：PowerShell 关键入口与生产调度候选下钻

T6-4 在既有 T6-1/T6-2 数据与 UI 契约上增加一个严格有界的
PowerShell 静态事实来源，以及“生产调度与控制”的未审核候选下钻。
它不执行生产脚本，也不改变 T3/T5-Lite 的正式审核结论。

## 静态事实边界

- 唯一 PowerShell 输入是
  `scripts/production/production_campaign_supervisor.ps1`。
- Windows PowerShell Parser AST 识别固定白名单中的关键函数入口、
  `Join-Path $Root` 的明确 `.py`/`.ps1` 脚本绑定，以及 dot-source。
- 变量身份按 PowerShell 的大小写不敏感规则归一化；在根脚本中，无限定变量和
  `script:`、`local:`、`private:` 限定变量视为同一脚本变量。其他作用域限定保持
  `resolution=unknown`。
- 只有根脚本中、无条件、普通 `=`、且 RHS 恰好是唯一受支持的
  `Join-Path $Root 'scripts\production\*.py|*.ps1'` 的赋值，才有资格成为
  `known`。`+=` 等复合赋值、动态 RHS、非 `Join-Path` RHS 和条件赋值都会作为
  竞争赋值保留，并使相关绑定无法冒充唯一静态绑定。
- 根脚本直接赋值之外，扫描器只把下列明确写入识别为保守失效信号：嵌套作用域的
  `$script:` 赋值、`foreach` 迭代变量、直接变量解构，以及 `Set-Variable`/`sv`。
  `Set-Variable` 名称或影响作用域无法静态确定时，所有相关绑定均降级为 `unknown`；
  扫描器不尝试推导这些写入的值。
- `Join-Path` 只有在绑定之前没有发现当前脚本中的命令解析污染时才可能为 `known`。
  有界污染集合包括同名函数、Alias 创建/修改/删除、显式
  `Function:\Join-Path`/`Alias:\Join-Path` provider 修改、`Import-Module`、
  `Invoke-Expression`、dot-source，以及绑定前发生且 provider 无法排除的根脚本
  Item 修改命令。污染后的绑定保留候选路径，但 `resolution=unknown`。
- dot-source 按源码顺序只考虑此前已经建立的同一规范变量绑定；未来赋值不回溯，
  此前存在多个赋值或任何不合格赋值时保持 `unknown`。
- 白名单函数名同样按大小写不敏感规则匹配；只有无条件的根脚本函数定义且不存在
  同名竞争定义时才是 `known`。嵌套、条件或重复定义均为 `unknown`。
- 函数局部赋值不会污染根脚本绑定；解析错误记录为
  `parse_status=parse-error`，不产生伪事实。
- 不读取或扫描绑定指向的专项脚本，不构建通用 PowerShell 调用图，
  不把绑定、dot-source 或函数声明解释为运行事实或正式架构协作。

扫描复用现有 `scan_python_source_facts(repository)` 入口；返回值保留原有
Python `modules`，并增加 `powershell_scope` 和 `powershell_scripts`。

## 生产调度候选

独立片段
`architecture-explorer/candidates/production-control.v1.json` 为 T4 的三个既有节点
补充就近通俗中文解释和真实静态证据，并增加两个候选组件与两条候选协作：

- Supervisor 编排；
- Worker 启动边界；
- 状态与控制决策；
- 控制决策适配器；
- 确定性 Worker 包装入口。

所有新增内容保持 `candidate` / 未审核。Worker 启动边界继续继承 T4 的
`partial` 源码归属和原有正式关系引用；其他候选保持 `unverified`。
顶层七模块的 `source_coverage` 不变。

## 不变量

- 正式架构关系仍只有
  `relationship:production-control:launches:translation-execution`；
- 正式关系仍为 `current-reviewed`，源码连续性仍为 `exact-match`；
- 正式关系仍精确包含 13 条批准证据；
- 候选脚本绑定不进入正式关系证据，不提升任何顶层源码覆盖；
- T5-Lite 审核来源和连续性门禁保持原样。

## 离线构建

在项目根目录为每个 Python Shell 设置当前工作树源码：

```powershell
$env:PYTHONPATH = (Resolve-Path '.\src').Path
$python = (Resolve-Path '.\.venv\Scripts\python.exe').Path
& $python scripts\architecture_explorer\generate_reviewed_relationship.py build `
  --repository . `
  --snapshot output\architecture-explorer\t1-delivery-4be1c19\snapshot.json `
  --mapping architecture-explorer\modules.json `
  --candidate architecture-explorer\review-inputs\supervisor-worker-857a4b8.json `
  --review-mapping architecture-explorer\reviews\supervisor-worker.v1.json `
  --review-source architecture-explorer\reviews\supervisor-worker.source.v1.json `
  --review-state output\architecture-explorer\t5-lite\review-state.v1.json `
  --navigation architecture-explorer\navigation.v1.json `
  --candidate-fragment architecture-explorer\candidates\production-control.v1.json `
  --candidate-fragment architecture-explorer\candidates\document-understanding.v1.json `
  --candidate-fragment architecture-explorer\candidates\scope-and-request.v1.json `
  --web-assets architecture-explorer\web `
  --output-directory output\architecture-explorer\t6-4-local
```

该命令只构建离线 Explorer；不会执行 Supervisor、Worker 或任何生产任务。

当前扫描器有意不解释一般值流、间接命令调用、嵌套作用域中的动态 provider 路径、
作用域跨越调用、运行时模块内容、任意脚本块的执行可达性或外部会话预先存在的命令覆盖。
它只对上文列出的源码形状做失效判定；这些限制不会扩展为通用 PowerShell 调用图，
`known` 也仍只表示在该有界源码契约内关系明确，不表示生产运行实际发生。
