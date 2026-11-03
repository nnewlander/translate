# Architecture Explorer T3 定向修复交付记录

本记录对应 GitHub Issue #4 的 P1/P2 Code Review 修复。它是本地复审材料，不是 Push、Merge、Issue 关闭或 T4/T5 实施授权。

## 修订与状态

- 授权修复起点：`3efcdeb6503206258268b1ce2b7fe8e3dd6c3536`。
- 实际运行最终应用测试的 Commit：`ce3461e163b0f40ce4655fcb9f24e9f1f52b84e5`。
- 分支：`codex/architecture-explorer-t3`。
- 本记录所在提交只增加交付文档；应用、测试、来源收据和离线页面均以 `ce3461e...` 为验证基线。
- T3 尚未获得集成授权，未 Push、未 Merge，Issue #4 未关闭。
- T4/T5 的正式起始 Commit 必须等待 T3 未来完成授权集成后另行确定，本记录不预先指定。

## Findings 修复结果

### P1：GitHub 审核来源可信性

在线 `import-review` 不再接受本地 comment/permission JSON。它直接使用已认证的 `gh api` 读取固定仓库 `nnewlander/translate`、Issue #3、评论 `6516894339` 和真实评论作者的仓库权限，并核对仓库/Issue/评论 ID、URL、作者 login/ID、评论创建/更新时间、原始正文、`OWNER` 关联和 `admin` 权限。

新增版本化来源收据：

- `architecture-explorer/reviews/supervisor-worker.source.v1.json`
- 规范化来源 SHA-256：`10593b61f8db5c6e09e041e59a9929aeceee99d1f2bffd6bbb81de2509d7478e`
- 来源收据文件 SHA-256：`62c296838c8d76c6e621ebb473e38c3b45b67d970c88a41b59cf73ed2de6ad2b`
- 审核映射文件 SHA-256 保持：`03ad7e0b78c454326dbeab311d6133ed141c4bc1b54de0c6bf537ae9e4c707f1`

离线 build 现在要求独立来源收据：先验证实现中固定的规范化摘要和身份字段，再使用收据里的原始正文与权限重建审核映射，并要求重建结果与版本化映射逐字段相同。收据缺失、作者/评论 ID/正文被改或映射被改都会降级为 `unknown`。

该机制不是数字签名，不能防御同时修改实现代码、固定摘要、来源收据、审核映射和测试的全仓库攻击者；它不声称提供超出版本控制与独立 Code Review 边界的安全保证。

### P2：源码读取边界

生产源码连续性只接受 T2 固定的三文件白名单，并在读取前复用候选扫描器的路径验证。绝对路径、`..`、非白名单路径、仓库外解析、symlink、junction、reparse point 和非文件目标不会被读取。candidate 中的其他路径不能扩大读取范围。

### P2：公开接口回归

移除了 `__init__.py` 的重复 `__all__` 覆盖。T1 的 `SCANNER_VERSION`、`build_explorer`、`scan_snapshot`，T2 的扫描/报告接口，以及 T3 的审核来源、映射与发布接口现在统一导出，并有回归测试。

## GitHub 来源复现

最终在线导入命令：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $python scripts\architecture_explorer\generate_reviewed_relationship.py import-review `
  --repository . `
  --candidate architecture-explorer\review-inputs\supervisor-worker-857a4b8.json `
  --output output\architecture-explorer\t3-security-import-final\supervisor-worker.v1.json `
  --source-output output\architecture-explorer\t3-security-import-final\supervisor-worker.source.v1.json
```

结果：`validation=valid`；重新生成的 mapping 和 source receipt 分别与版本化文件字节一致。

## 测试命令与结果

所有命令使用 `D:\translate\.venv\Scripts\python.exe`、当前 worktree 的 `src`，并禁用 pytest cache。

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t1-ce3461e `
  tests\architecture_explorer\test_top_level_explorer.py
```

结果：`39 passed in 15.32s`。

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t2-ce3461e `
  tests\architecture_explorer\test_supervisor_worker_candidate.py
```

结果：`28 passed in 25.88s`。

```powershell
& $python -m pytest -q -rs -p no:cacheprovider `
  --basetemp D:\translate\.test-tmp\architecture-explorer-t3-ce3461e `
  tests\architecture_explorer\test_reviewed_relationship.py `
  tests\architecture_explorer\test_t3_security_repairs.py
```

结果：`28 passed in 2.16s`。其中定向安全反例为 `13 passed`，覆盖：可信来源缺失、伪造映射作者、改写审核决定、伪造来源作者/评论 ID/正文、`..`、绝对路径、非白名单文件、模拟链接路径和 T1/T2/T3 公开接口。

Python compileall、JavaScript `node --check` 和 `git diff --check` 均通过。

## 离线页面

- 页面：`output/architecture-explorer/t3-ce3461e/site/index.html`
- `explorer-data.json` SHA-256：`5b9f76cc660f515e480d5c17e19d82cfcea6662700152b724cd578defc3008d4`
- 生成状态：实现 Commit `ce3461e...`、worktree `clean`、review source `trusted`、关系 `current-reviewed`、源码连续性 `exact-match`。
- 生产证据 13 条、辅助测试证据 3 条、中文证据组 `supervisor:4` / `wrapper:2` / `worker:7`。

## 已知未验证事项

- 安全修复没有改变已验收的白底、黑字、植物绿界面；最终 `ce3461e...` 页面已生成和数据核验，但尚未进行新一轮人工浏览器视觉验收。
- 链接拒绝反例在隔离 fixture 中通过模拟链接属性执行；实现复用 T2 已测试的 Windows symlink/junction/reparse-point 验证。受管环境中未另外创建真实外部 NTFS junction 做手工攻击演练。
- 未运行仓库全量生产测试；本轮只运行 T1–T3 专项测试，不创建或修改生产数据。
