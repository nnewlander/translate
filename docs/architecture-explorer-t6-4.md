# Architecture Explorer T6-4：PowerShell 关键入口与生产调度候选下钻

T6-4 在既有 T6-1/T6-2 数据与 UI 契约上增加一个严格有界的
PowerShell 静态事实来源，以及“生产调度与控制”的未审核候选下钻。
它不执行生产脚本，也不改变 T3/T5-Lite 的正式审核结论。

## 静态事实边界

- 唯一 PowerShell 输入是
  `scripts/production/production_campaign_supervisor.ps1`。
- Windows PowerShell Parser AST 识别固定白名单中的关键函数入口、
  `Join-Path $Root` 的明确 `.py`/`.ps1` 脚本绑定，以及 dot-source。
- 带参数覆盖、条件分支或其他不能静态唯一确定的绑定保留为
  `resolution=unknown`；解析错误记录为 `parse_status=parse-error`，不产生伪事实。
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
