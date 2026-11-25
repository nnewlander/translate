# v4-04 集中审核与首次确认交接

- B0：`09aaf4a033123c7eca8d280fa8982e5b0e711b86`
- Bstart：`2267de3288282a68dac157d1b29bef3c24324ff5`
- 范围：离线材料准备和首次 Owner 确认；此记录本身不产生可信 Decision、Receipt、Anchor 或审核状态。

## 公共接口

`booktrans.architecture_explorer.batch_review` 提供：

1. `prepare_batch_review(explorer, node_definitions, relationship_definitions, analysis_by_key)`：调用已有三类对象的 Definition 校验和 Packet 构建，要求 AI 分析精确覆盖对象集合，返回含完整 Packet 的 `architecture-explorer.batch-review.v1`。
2. `render_batch_checklist(batch)`：按主题生成中文正文，并在同一文件附上全部 Claims、Evidence 定位、源码快照、依赖和完整批准范围。正文不堆长 ID；完整技术原文仍在 `batch-review.json`。
3. `render_final_decision_list(batch, decisions)`：Codex 将自然语言意图映射后，生成确定性的逐对象最终清单，列明中文名称、稳定身份、结果、理由和准确范围。未明确决定者写 `PENDING`。`APPROVED` 必须显式声明 `scope: FULL_PACKET`；若 Owner 只认可部分 Claims，暂缓并重备 Packet。
4. `confirm_batch_review(batch, decisions, final_decision_list=...)`：仅在传入清单与上述规范内容逐字一致时，生成 `architecture-explorer.batch-first-confirmation.v1`。记录同时绑定 `batch-review.json`、`checklist.md` 和最终决定清单的内容摘要。此函数只封存内容，不验证聊天中的 Owner 身份。
5. `validate_batch_confirmation(record, batch)`：重新检查上述内容绑定、Packet、对象身份、Claims/Evidence 摘要、源码快照与逐对象决定，返回决定副本。材料或决定改变时旧记录失效。
6. `decided_objects(record, batch)`：#79、#80 的稳定读取入口，仅返回经过内容校验的 `APPROVED` / `REJECTED` 对象；暂缓或未决定保持 Pending。

对象键采用 `(kind, object_id)` 的稳定字符串形式；历史 Node Packet 的 `node_id` 在这一边界适配。AI 建议不会默认采纳。首次确认记录和两个读取接口**只是经内容校验的离线材料，不是独立可信授权凭证**。当前聊天与 CLI 不提供独立可核验的人类授权凭证；Codex 只能依照实际 Owner 的明确聊天确认调用 CLI，不能把命令参数或本地摘要说成身份认证。#79 负责真实 GitHub 来源、逐对象评论、Receipt、第二次确认及 Anchor 安装；#80 再做整批可信复核和原子导入。

## Owner 操作

Owner 只需按清单中文名称或序号说明逐项意图与理由。Codex 将意图映射到稳定键，未提及对象填 `PENDING`，生成最终决定清单并展示全文。Owner 核对对象、决定、理由和完整范围后，对这份明确清单**一次集中确认**；Codex 随后调用离线 CLI。Owner 无需撰写 JSON、复制对象 ID 或操作命令行。若意图只是局部批准，Codex 不得把它改写为 `FULL_PACKET`，应保留 Pending/暂缓并重备材料。

## Codex 离线操作

从 Worktree 根目录使用 `.venv\Scripts\python.exe`：

```powershell
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $python -m booktrans.architecture_explorer.batch_review_cli prepare --explorer-data <explorer.json> --node-definitions <nodes.json> --relationship-definitions <relationships.json> --ai-analysis <analysis.json> --output-directory <new-output-directory>
& $python -m booktrans.architecture_explorer.batch_review_cli prepare-decisions --batch <batch-review.json> --decisions <codex-generated-owner-decisions.json> --output <final-decisions.md>
# 先把 final-decisions.md 全文展示给 Owner；仅在 Owner 明确确认后执行：
& $python -m booktrans.architecture_explorer.batch_review_cli confirm --batch <batch-review.json> --decisions <codex-generated-owner-decisions.json> --final-list <final-decisions.md> --output <new-confirmation.json>
```

Node 或 Relationship 定义可单独提供。输出路径不会覆盖已有文件。CLI 在确认时核对同目录 `checklist.md` 和最终决定清单的 UTF-8 原始字节，防止已展示的材料被换掉。Codex 生成的决定文件必须逐对象列出，例如：

```json
{
  "candidate-node::example": {"result": "APPROVED", "reason": "认可 Packet 的完整静态职责范围", "scope": "FULL_PACKET"}
}
```

验收用真实候选清单位于 `output/architecture-explorer/v4-ticket-04/checklist.md`，完整 Packet 在同目录 `batch-review.json`。它基于 #77 干净源码快照，与 Bstart 的 Git tree 相同；选取一项 Node、一项 Collaboration 和一项 Decomposition，逐条核对了 Evidence 源文件哈希。三项 AI 分析分别对应自身的 Evidence 与 Claims，建议分别是受限静态批准、受限静态批准和因分解粒度暂缓；清单中的 Owner 决定均为 `Pending`。它仅供审阅产品形式，不是批准记录；正式批次需在目标干净源码快照上重新准备材料。
