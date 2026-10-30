# Architecture Explorer T3：真实审核映射与已审核关系

T3 把 GitHub Issue #3 中经 API 核验的人工审核决定转成版本化映射，并在所有绑定和生产源码连续性检查通过时，发布一条“当前已审核”的 `production-control -> translation-execution` 关系。它不从 Issue 状态、标签、关闭时间或正文自述的审核身份推断授权。

## 权威边界

- 被审核生产源码快照固定为 `857a4b85cf4763b74ecff0d02e660b005a0c8bae`，候选摘要为 `f08de10f90f76c07754b1e024ec16468874965bdc01d5ec53a002e140468ee98`。
- 固定候选输入位于 `architecture-explorer/review-inputs/`，由上述干净 Commit 直接重生成，不依赖临时 worktree 输出。
- 版本化审核映射位于 `architecture-explorer/reviews/supervisor-worker.v1.json`。它由 GitHub API 评论 `6516894339` 与仓库权限 API 响应导入；真实作者是 `nnewlander`，`author_association=OWNER`，仓库权限是 `admin`。
- T3 Explorer 实现 Commit 只记录在生成数据的 `implementation` 字段中。它与 `reviewed_source_snapshot` 分开，Explorer-only 变化不会被当作生产源码变化。

## T3–T5 最小公共数据约定

版本为 `architecture-explorer.t3-t5.v1`：

- 源码快照：revision、工作区状态、参与扫描文件的 SHA-256、扫描器版本。
- 模块：沿用 T1 的稳定模块 ID、批准名称/职责、逻辑批准和源码覆盖状态。
- 证据：稳定 Evidence ID、种类、路径、symbol、结构锚点、片段/文件哈希、当前行号、生产/辅助身份。
- 关系：稳定 ID、源/目标模块、方向、类型、代码可达、真实运行、证据引用和审核状态。
- 审核决定：决定 ID、结果、审核快照、候选摘要、13 条生产 Evidence ID、方向、类型、可达条件、真实 GitHub 评论 URL/作者/时间。

审核状态仅为 `candidate`、`current-reviewed`、`needs-review`、`unknown`。连续性状态保留 `exact-match`、`equivalent-relocation`、`material-change`、`missing`、`ambiguous`、`parse-failure`；T3 首次发布只会把 `exact-match` 提升为当前已审核，等价重新定位留给 T5。

## 严格发布门禁

首次发布的导入器采取封闭授权：API 评论作者与权限主体必须一致，`author_association` 必须为 `OWNER`，仓库权限必须为 `admin`。正文必须符合 Issue #3 的封闭字段格式。正文中自称 reviewer 不参与授权。

映射导入和离线站点构建都会校验：生产源码 Commit 和干净状态、扫描器版本、候选摘要、13 条生产 Evidence ID 的集合与顺序、源/目标模块、方向、关系类型、静态可达状态、运行状态和保留未知事项。构建还逐个计算三份生产源码当前 SHA-256；辅助测试和 Explorer 自身文件不参与“当前生产源码精确一致”的判定。

保守状态规则：

| 条件 | 发布状态 |
|---|---|
| 没有审核决定 | `candidate` |
| 有效 `REJECTED` | `needs-review` |
| 作者无权、格式/绑定/证据不匹配 | `unknown` |
| 有效 `APPROVED`，但当前生产源码不精确匹配 | `needs-review` |
| 有效 `APPROVED` 且全部生产源码精确匹配 | `current-reviewed` |

## 离线生成

先用 T1 命令生成当前 Explorer 实现快照，再构建 T3 页面：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $python scripts\architecture_explorer\generate_top_level.py scan `
  --repository . --source-file pyproject.toml `
  --output output\architecture-explorer\t3-site\snapshot.json
& $python scripts\architecture_explorer\generate_reviewed_relationship.py build `
  --repository . `
  --snapshot output\architecture-explorer\t3-site\snapshot.json `
  --mapping architecture-explorer\modules.json `
  --candidate architecture-explorer\review-inputs\supervisor-worker-857a4b8.json `
  --review-mapping architecture-explorer\reviews\supervisor-worker.v1.json `
  --web-assets architecture-explorer\web `
  --output-directory output\architecture-explorer\t3-site\site
```

直接打开 `output/architecture-explorer/t3-site/site/index.html`。页面不需要服务器、网络、数据库或 AI 服务。关系线可点击查看统一证据详情；定位复制包含路径、当前行号和 symbol；“高亮最小生产流程”只改变视觉强调，不改变审核状态或点击含义。
