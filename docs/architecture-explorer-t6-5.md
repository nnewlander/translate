# Architecture Explorer T6-5：生产证据、候选文档发布下钻与流程辅助说明

T6-5 在既有 T6 候选片段契约和 T4/T5-Lite UI 上，为最后两个顶层模块增加
有真实静态源码事实支持的未审核候选下钻。它不新增扫描器、导航模式、审批系统或
正式架构关系，也不提升任何顶层模块的源码覆盖状态。

## 生产证据与 Checkpoint

独立片段 `architecture-explorer/candidates/production-evidence.v1.json` 包含两个节点和
一条候选协作：

- `Checkpoint 证据持久化`：由生产 Worker 对一次写入工件接口的明确 import 支持；
- `Checkpoint 清单封存组件`：由生产 Worker 对权威批次清单、封存投影与后继发布接口的
  明确 import 支持；
- `证据工件支撑清单封存`：把上述两个静态事实组织为未审核候选协作。

这些事实不证明运行时调用、真实封存结果或正式模块归属。候选协作也不是新的正式关系。

## 候选文档与发布验证

独立片段 `architecture-explorer/candidates/candidate-and-release.v1.json` 包含三个节点和
一条候选协作：

- `候选文档受控投影`：由 tagged roundtrip 对 tagged projection 的明确 import 支持；
- `DOCX 包保真验证组件`：由 tagged roundtrip 对 package preservation，以及 package
  preservation 对 DOCX 包读取边界的明确 import 支持；
- `发布成员资格派生`：由批次推进脚本对 release membership 派生接口的明确 import 支持；
- `候选投影进入包保真校验`：把投影与包保真接口组织为未审核候选协作。

候选投影、包保真验证、发布成员资格派生和视觉 QA 不被描述为每个 Checkpoint 的
常驻步骤。包保真验证也不等同于视觉 QA 或 Owner 发布验收。

## 流程辅助说明

既有“高亮最小生产流程”按钮附近新增简短说明：高亮只帮助定位已审核的
Supervisor → Worker 最小协作，不表示七模块按该顺序执行，也不改变模块位置、下钻、
证据内容或审核状态。

## 信任边界

- 两个片段的所有节点与协作保持 `candidate` / 未审核；
- 所有候选源码归属保持 `unverified`；
- 七模块都具备候选下钻不表示源码覆盖已完成正式审核；
- 唯一正式关系仍是
  `relationship:production-control:launches:translation-execution`；
- 正式关系继续使用 13 条批准证据，保留 T5-Lite `current-reviewed` 与
  `exact-match` 连续性门禁。

## 离线构建

构建沿用 `generate_reviewed_relationship.py build`，在 T6-4 的参数后继续传入：

```text
--candidate-fragment architecture-explorer/candidates/translation-execution.v1.json
--candidate-fragment architecture-explorer/candidates/quality-and-repair.v1.json
--candidate-fragment architecture-explorer/candidates/production-evidence.v1.json
--candidate-fragment architecture-explorer/candidates/candidate-and-release.v1.json
```

每个 Python Shell 必须把当前 Worktree 的 `src` 设置为 `PYTHONPATH`。构建只生成离线
Explorer，不执行 Supervisor、Worker、翻译、候选文档投影、视觉 QA 或发布任务。
