Ticket #84 真实混合审核资产与离线构建

本目录保留 Issue #84 的真实审核资产，等待 Owner 产品验收与独立审查后随同代码交付。
evidence/ 保存 10 份原审核材料、原源码扫描快照和宿主安装文件的取证副本。
definitions/ 保存两个原 Review Definition 文件。published/ 是完整 T6 导入批次。
sha256.json 列出上述 24 个原文件的逐字节 SHA-256。不要格式化或改写归档文件。
真实评论为 Issue #84 的 7192133892、7192137877、7192141675。初始导入、两次确认、Decision、Receipt、Anchor 和批准范围均沿用原件。

Owner 第一次集中确认了 3 项决定及其完整 Packet 范围，记录在 evidence/first-confirmation.json。
第二次集中确认了这 3 条评论对应的精确 Approval Anchor，记录在 evidence/second-confirmation.json。
正式工作流用同一份清单绑定逐项决定，自动发布 3 条独立评论、取回并核验 3 份 Receipt，再生成精确 Anchor 清单。
Owner 在对话中完成两次业务确认，无需逐条发布评论、复制评论 ID、整理 Receipt 或手工组装 3 个 Anchor。

正式信任与消费

运行受信任并经审核的发布版代码。review_trust.PRODUCTION_BATCH_INSTALLATION_DIGESTS 只登记本批已授权摘要
1041dab86073f3ea4aea04a9b492d27e4310566b203ac360f858275780a10a08。
T6 正式读取器还要求已有 T5 安装文件位于用户主目录下
.booktrans/architecture-explorer/installed-anchors/1041dab86073f3ea4aea04a9b492d27e4310566b203ac360f858275780a10a08.json。
evidence/installed-anchor.json 供审查与核对。发布目录本身不授予信任。
对于已经安装本批的宿主，直接复用原安装。需要换宿主时，由宿主维护者按独立授权流程使用正式 T5 安装入口，
输入 evidence/anchor-candidate.json 和 evidence/second-confirmation.json。校验未通过时保留失败并核对宿主权限与授权。
禁止用归档文件冒充已安装 Anchor，也不要用命令行参数、环境变量或运行时补丁临时加入信任摘要。

复现本批当前审核结果

使用包含修复与宿主配置的正常运行 Worktree，从该 Worktree 根执行以下 PowerShell。
Reviewed source 单独检出为干净的 ee6b8241b3075e48fe9dc5e6254b7086f23f13ae。
先将 BOOKTRANS_T84_REVIEWED_SOURCE 设置为该源码 Worktree 的路径。
用后续源码作为评估目标时，既有连续性规则会重新判定状态。关系 Packet 绑定精确源码快照，因此后续提交不能冒充这一历史快照。
在当前修复版 renderer 中复现本批 current 结果时，使用下面的受审源码和正常运行版本组合。

& .\scripts\architecture_explorer\prepare_worktree.ps1
if ($LASTEXITCODE) { exit $LASTEXITCODE }
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
$runtime = (Get-Location).Path
if (-not $env:BOOKTRANS_T84_REVIEWED_SOURCE) { throw 'Set BOOKTRANS_T84_REVIEWED_SOURCE to the clean reviewed Worktree' }
$source = (Resolve-Path -LiteralPath $env:BOOKTRANS_T84_REVIEWED_SOURCE).Path
if ((git -C $source rev-parse HEAD) -cne 'ee6b8241b3075e48fe9dc5e6254b7086f23f13ae' -or (git -C $source status --porcelain)) { throw 'Reviewed source is not clean at the approved revision' }
$archive = Join-Path $runtime 'architecture-explorer/reviews/t84'
$ae = Join-Path $source 'architecture-explorer'
$build = @(
    '--repository', $source,
    '--snapshot', "$archive/evidence/source-snapshot.json",
    '--mapping', "$ae/modules.json",
    '--candidate', "$ae/review-inputs/supervisor-worker-857a4b8.json",
    '--review-mapping', "$ae/reviews/supervisor-worker.v1.json",
    '--review-source', "$ae/reviews/supervisor-worker.source.v1.json",
    '--review-state', 'output/architecture-explorer/t5-lite/review-state.v1.json',
    '--navigation', "$ae/navigation.v1.json",
    '--view-definition', "$ae/views/recursive-views.v1.json",
    '--web-assets', "$runtime/architecture-explorer/web",
    '--node-review-definitions', "$archive/definitions/node-definitions.json",
    '--relationship-review-definitions', "$archive/definitions/relationship-definitions.json",
    '--review-batch-directory', "$archive/published",
    '--output-directory', 'output/t84-preview'
)
foreach ($file in Get-ChildItem "$ae/candidates","$ae/recursive-candidates" -Filter '*.json') {
    $build += @('--candidate-fragment', $file.FullName)
}
& $python scripts/architecture_explorer/generate_reviewed_relationship.py build @build

Chrome 聚焦验收

设置 ARCHITECTURE_EXPLORER_BROWSER=chrome，ARCHITECTURE_EXPLORER_T84_PREVIEW 指向上述预览目录，
ARCHITECTURE_EXPLORER_T84_BATCH 指向本目录的 published/。使用项目解释器运行
tests/architecture_explorer/test_ticket_84_real_review_details.py。
测试读取原 Decision 与 Receipt 作为预期，核对三类对象的可信来源、批准范围、保留未知项及未审核关系状态。
