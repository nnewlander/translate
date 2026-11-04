# Architecture Explorer T5-Lite：证据连续性

T5-Lite 只维护首条已审核的 Supervisor → Worker 关系。正式范围由 Owner 在 GitHub Issue #6 的[范围修订评论](https://github.com/nnewlander/translate/issues/6#issuecomment-6614063161)中确认；该评论替代与本文件冲突的旧 T5 验收要求。

## 保留的契约

- 使用 T2 真实扫描比较原 T3 决定批准的 13 条生产 Evidence ID；不修改 T2 身份规则。
- 在扫描完整且 13 条 ID 唯一时按身份集合比较，源码位置导致的排列变化不失效；T3 原始证据展示顺序保持不变。
- 空行和纯位置移动保持稳定 ID，不触发无意义告警。证据缺失、歧义、解析失败或 ID 改变时保守降级。
- T3 GitHub Owner 来源、权限收据和审核映射仍是唯一人工批准来源。review-state 只记录连续性结果，不复制 Evidence ID 或授予批准。
- T4 只有在 T3 来源可信、review-state 有效、状态为 `current-reviewed` 且当前 13 条 ID 精确匹配时，才允许显示 `partial` 源码归属。
- review-state 缺失、损坏或与 T3 decision 不一致时，关系只显示 `historical-reviewed`，不能代表当前源码已审核。

## 简化后的状态模型

唯一 T5 状态文件是 `output/architecture-explorer/t5-lite/review-state.v1.json`。它由 Git 正式记录，只包含关系 ID、T3 decision ID、状态、连续性结果、评估时间和原因。

- `build` 是只读操作：它重新执行 T2 扫描并读取状态，但绝不创建或修改 review-state。
- `update-review-state` 是唯一写入口，使用同目录临时文件、`fsync` 和原子替换。
- 同一 T3 decision 一旦被显式记录为 `needs-review` 或 `unknown`，后续源码恢复和重复更新都不能自动恢复 `current-reviewed`。
- 只有新的、通过现有 T3 可信来源验证的 decision 才能重新建立 `current-reviewed`；如果 Evidence ID 改变，新决定必须明确批准新的 13 条 ID。
- 损坏或结构不一致的现有状态拒绝覆盖，需由维护者先诊断，而不是静默初始化。

## 明确不再提供的保证

第一版不包含 T5 专用 authoritative head、GitHub head 评论/receipt、hash-chain、pin、generation、复杂并发协议或 `equivalent-relocation`。完整旧 Git 版本回滚、恶意历史改写以及没有通过显式更新保存的短暂失效观察不在保障范围内。

特别地，若只读构建观察到失效，但尚未执行并提交 `update-review-state`，随后源码恢复，下一次构建会再次得到 `current-reviewed`。这是 T5-Lite 的公开边界，不应描述为可检测的历史失效。

## 操作顺序

所有命令使用项目专用解释器并让当前 Worktree 的 `src` 优先导入：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
$assessedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
& $python scripts\architecture_explorer\generate_reviewed_relationship.py update-review-state `
  --repository . `
  --candidate architecture-explorer\review-inputs\supervisor-worker-857a4b8.json `
  --review-mapping architecture-explorer\reviews\supervisor-worker.v1.json `
  --review-source architecture-explorer\reviews\supervisor-worker.source.v1.json `
  --review-state output\architecture-explorer\t5-lite\review-state.v1.json `
  --assessed-at $assessedAt
```

检查生成的差异并将 review-state 与对应源码变更一起提交。普通离线构建额外传入：

```text
--review-state output\architecture-explorer\t5-lite\review-state.v1.json
```

构建不需要网络，也不创建新的 GitHub 评论。真实生产脚本、T3 正式决定和生产数据均保持只读。
