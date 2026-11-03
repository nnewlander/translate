# Architecture Explorer T3：真实审核映射与已审核关系

T3 把 GitHub Issue #3 中经 API 核验的人工审核决定转成版本化映射，并在所有绑定和生产源码连续性检查通过时，发布一条“当前已审核”的 `production-control -> translation-execution` 关系。它不从 Issue 状态、标签、关闭时间或正文自述的审核身份推断授权。

## 权威边界

- 被审核生产源码快照固定为 `857a4b85cf4763b74ecff0d02e660b005a0c8bae`，候选摘要为 `f08de10f90f76c07754b1e024ec16468874965bdc01d5ec53a002e140468ee98`。
- 固定候选输入位于 `architecture-explorer/review-inputs/`，由上述干净 Commit 直接重生成，不依赖临时 worktree 输出。
- 版本化审核映射位于 `architecture-explorer/reviews/supervisor-worker.v1.json`。它由 GitHub API 评论 `6516894339` 与仓库权限 API 响应导入；真实作者是 `nnewlander`，`author_association=OWNER`，仓库权限是 `admin`。
- 独立 GitHub 来源收据位于 `architecture-explorer/reviews/supervisor-worker.source.v1.json`，保存固定仓库、Issue、评论、真实作者、评论时间、原始正文和权限响应的规范化 API 字段。
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

首次发布的在线导入器采取封闭授权：它自己调用已认证的 `gh api`，不接受调用者提供的 comment/permission JSON。导入器只允许 `nnewlander/translate`、Issue #3 和评论 `6516894339`，并核对仓库 ID、Issue ID、评论 ID/URL、真实作者 login/ID、创建和更新时间、原始正文、`author_association=OWNER` 与 `admin` 权限。正文必须符合 Issue #3 的封闭字段格式；正文中自称 reviewer 不参与授权。

离线 build 同时需要审核映射和独立来源收据。实现中固定了来源收据的规范化 SHA-256；build 先验证收据身份和摘要，再从收据中的原始评论与权限重新生成审核映射，并要求结果与版本化映射逐字段一致。缺少收据、收据被改写或映射与收据不一致时，关系保守降级为 `unknown`。

该方案的信任根是：经过独立 Code Review 的实现代码、其中固定的来源摘要，以及受版本控制的来源收据。普通 SHA-256 不是数字签名，不能防御能够同时改写实现代码、固定摘要、来源收据、审核映射和测试的攻击者；T3 不虚构这种防篡改保证。它解决的是普通离线构建不能仅凭一份自述 OWNER/admin 的本地映射提升审核状态的问题。

映射导入和离线站点构建都会校验：生产源码 Commit 和干净状态、扫描器版本、候选摘要、13 条生产 Evidence ID 的集合与顺序、源/目标模块、方向、关系类型、静态可达状态、运行状态和保留未知事项。构建还逐个计算三份生产源码当前 SHA-256；辅助测试和 Explorer 自身文件不参与“当前生产源码精确一致”的判定。

源码连续性读取使用固定三文件白名单：Supervisor、Python 包装入口和 Worker。candidate 中的绝对路径、`..`、额外路径或不同顺序会在读取前失败；每个白名单路径还会拒绝 symlink、junction、reparse point、仓库外解析结果和非文件目标。candidate 不能扩大读取范围。

保守状态规则：

| 条件 | 发布状态 |
|---|---|
| 没有审核决定 | `candidate` |
| 有效 `REJECTED` | `needs-review` |
| 作者无权、格式/绑定/证据不匹配 | `unknown` |
| 有效 `APPROVED`，但当前生产源码不精确匹配 | `needs-review` |
| 有效 `APPROVED` 且全部生产源码精确匹配 | `current-reviewed` |

## 离线生成

只有更新真实 GitHub 审核材料时才运行在线导入；该命令直接使用 `gh api` 并把结果写到 `output/` 供审阅，不接收本地 API JSON：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $python scripts\architecture_explorer\generate_reviewed_relationship.py import-review `
  --repository . `
  --candidate architecture-explorer\review-inputs\supervisor-worker-857a4b8.json `
  --github-repository nnewlander/translate `
  --issue 3 `
  --comment-id 6516894339 `
  --output output\architecture-explorer\review-import\supervisor-worker.v1.json `
  --source-output output\architecture-explorer\review-import\supervisor-worker.source.v1.json
```

日常离线使用不连接 GitHub。先用 T1 命令生成当前 Explorer 实现快照，再使用已版本化的映射和来源收据构建 T3 页面：

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
  --review-source architecture-explorer\reviews\supervisor-worker.source.v1.json `
  --web-assets architecture-explorer\web `
  --output-directory output\architecture-explorer\t3-site\site
```

直接打开 `output/architecture-explorer/t3-site/site/index.html`。页面不需要服务器、网络、数据库或 AI 服务。关系线可点击查看统一证据详情；定位复制包含路径、当前行号和 symbol；“高亮最小生产流程”只改变视觉强调，不改变审核状态或点击含义。

## 中文关系解读

关系详情按初学者阅读顺序展示：中文模块关系和通俗摘要、三种状态的含义、关键证据链、静态条件与未知事项，最后才是可展开的英文审核原文和技术字段。

13 条生产证据仍来自固定候选记录，并按 Evidence ID 关联到三组中文阅读提示：

- Supervisor：4 条，覆盖脚本绑定、生产参数、进程边界定义和调用；
- Python 包装入口：2 条，覆盖 Worker main 的导入与调用；
- Worker：7 条，覆盖模式、授权参数、参数解析、Canary 分支、守卫和生产入口调用。

3 条辅助测试证据另列在默认折叠的“辅助测试证据”区域，明确标为支持性上下文，不属于 Owner 批准的 13 条生产调用证据。每条中文说明由生成器按原始 Evidence ID 绑定，原始 evidence 对象、review mapping、Evidence ID、源码哈希、关系类型和审核状态不被改写。英文技术种类、完整 Evidence ID、源码位置、symbol、代码片段和 Owner 审核原文仍可展开查看。

页面明确提示：静态条件可达不等于某次 Worker 启动成功，也不等于翻译任务成功；真实运行状态继续保持 `NOT_COLLECTED`。
