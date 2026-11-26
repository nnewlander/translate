# 首次确认：逐对象最终决定清单

> 请核对下列每项决定及完整批准范围。此清单只是离线内容绑定；真实 Owner 身份须由后续 GitHub Trust Policy 独立验证。

- 审核材料：`batch-review.json`，规范清单：`checklist.md`

## 对象 1：Worker 启动边界

- 稳定身份：`candidate-node::worker-launch-boundary`
- 最终决定：**APPROVED**
- 理由：源码支持受控进程启动边界及明确限定的部分源码指派。
- 批准范围：此对象完整 Packet 的全部 Claims（IDENTITY、NAME、CLASSIFICATION、RESPONSIBILITY、PLAIN_EXPLANATION、SOURCE_ASSIGNMENT、CANDIDATE_EVIDENCE、DEPENDENCIES_AND_LIMITS）；包含 Packet 中全部候选 Evidence（3 条）与依赖 Evidence（13 条），受下述源码快照及保留未知项约束。不覆盖其他对象或运行时结论。不能只批准其中一部分；若需缩小范围，应暂缓并重新准备 Packet。
- Evidence 精确身份与定位：见同一份 `checklist.md` 的本对象审核附录；Packet 摘要见下。
- 保留未知项（不批准）：是否在某次实际运行中到达或成功跨越该边界；运行时前置条件、凭据和外部依赖是否可用；该职责的完整源码覆盖；任何文件、symbol 或代码区域是否排他归属该节点；观察到的名称、字面值和直接绑定以外的参数业务含义；相邻组件是否获批；任何 Candidate Collaboration 是否获批；顶层模块源码覆盖是否完成
- 源码快照：`ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`（clean）
- Packet：`5a5ff7e276e66a201d8678dc4bb702bcd44a5a1020d84f471e246601c85002bc`；Claims：`cb1e1fd9a564203a60da2e5d53489498058f34ebf5532d86bb5e0942160ba5a0`

## 对象 2：启动边界指向 Worker 包装入口

- 稳定身份：`candidate-collaboration::candidate-collaboration:supervisor-to-worker-wrapper`
- 最终决定：**APPROVED**
- 理由：脚本绑定、包装入口及协作方向具有直接源码依据。
- 批准范围：此对象完整 Packet 的全部 Claims（IDENTITY、ENDPOINT_RESPONSIBILITIES、SEMANTICS_AND_DIRECTION、RELATIONSHIP_DESCRIPTION、CANDIDATE_EVIDENCE、DEPENDENCIES_AND_LIMITS）；包含 Packet 中全部候选 Evidence（2 条）与依赖 Evidence（0 条），受下述源码快照及保留未知项约束。不覆盖其他对象或运行时结论。不能只批准其中一部分；若需缩小范围，应暂缓并重新准备 Packet。
- Evidence 精确身份与定位：见同一份 `checklist.md` 的本对象审核附录；Packet 摘要见下。
- 保留未知项（不批准）：实际生产请求是否进入此确定性路径及包装器是否成功运行。；其他 Worker 启动路径是否采用同一包装器。
- 源码快照：`ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`（clean）
- Packet：`9710545fdfdaacf24ad972498399d2ec45c455634aebffeed32a6515a1060247`；Claims：`9cda7faa9dbff8fd11f6ce2ad1cd1d2faf7925eafa2f37abb1385288afc71771`

## 对象 3：启动边界分解为进程启动构造

- 稳定身份：`candidate-decomposition::candidate-decomposition:worker-launch-boundary-to-supervisor-process-launch-construction`
- 最终决定：**APPROVED**
- 理由：进程参数、环境、启动及输出处理构成有证据支持的内部子职责。
- 批准范围：此对象完整 Packet 的全部 Claims（IDENTITY、ENDPOINT_RESPONSIBILITIES、SEMANTICS_AND_DIRECTION、RELATIONSHIP_DESCRIPTION、CANDIDATE_EVIDENCE、DEPENDENCIES_AND_LIMITS）；包含 Packet 中全部候选 Evidence（1 条）与依赖 Evidence（0 条），受下述源码快照及保留未知项约束。不覆盖其他对象或运行时结论。不能只批准其中一部分；若需缩小范围，应暂缓并重新准备 Packet。
- Evidence 精确身份与定位：见同一份 `checklist.md` 的本对象审核附录；Packet 摘要见下。
- 保留未知项（不批准）：启动边界的完整子职责和全部启动路径。；源码排他归属以及某次实际启动和退出结果。
- 源码快照：`ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`（clean）
- Packet：`88b516236a653b15be13e579f3ef211e50344dde588aee20fe985fac71386d45`；Claims：`35048bd84be163a6307877a50c357556709db90b7a307ca455e624b1a0bc9163`

请仅在以上对象、决定、理由及范围均准确时，一次明确确认这份最终清单。
