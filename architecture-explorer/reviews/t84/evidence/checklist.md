# Architecture Explorer 集中审核清单

> 这是候选材料。AI 建议不等于 Owner 决定；每项默认 Pending。技术原文见同目录 `batch-review.json`。

本批 3 项。您可在聊天中按下方序号说明各项意图和理由；Codex 先展示逐对象最终决定清单，您核对后一次集中确认。未明确决定的对象保持 Pending。

## 审核主题：生产控制如何启动确定性 Worker

### 1. Worker 启动边界（候选职责）

**职责 / 关系**：候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。

**需要判断的 Claims**

- 职责：候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。
- 分类依据：worker-launch-boundary 描述的是生产控制把受参数和守卫约束的启动请求跨越进程边界交给翻译执行这一稳定架构职责，而不是某一个文件、函数、脚本包装器或可部署组件。三条 Candidate Evidence 跨越控制侧启动准备与 Worker 入口定位，只支持该职责边界值得作为候选节点表达；它们不足以证明一个物理组件、完整源码边界或排他所有权。
- 通俗解释：像一道受控发车闸门，Supervisor 准备好明确的 Worker 脚本后才跨进程启动。这里新增的是未审核源码说明；唯一正式 Supervisor→Worker 关系仍以原有 13 条批准证据为准。

**Evidence 与源码支持**

- 证据 1：`scripts/production/production_campaign_supervisor.ps1:224-291`，`Start-DeterministicWorkerProcess`。
- 证据 2：`scripts/production/production_campaign_supervisor.ps1:52-52`，`$deterministicWorkerScript`。
- 证据 3：`scripts/production/run_qwen_deterministic_worker.py:14-15`，`main`。
- AI 源码分析：Supervisor 脚本第 52 行绑定确定性 Worker 包装脚本，第 224–291 行构造并启动子进程。另核对第 1280 行把脚本放入参数向量，第 1305 行用项目解释器调用该启动函数。包装器第 14–15 行在直接执行时进入 Worker main。既有 Supervisor→Worker 正式依赖在本快照仍为 current-reviewed / exact-match。

**不能证明**：这些静态证据无法确认某次生产运行真的跨过边界、启动成功、凭据可用或完整源码覆盖。

**保留未知项**：是否在某次实际运行中到达或成功跨越该边界；运行时前置条件、凭据和外部依赖是否可用；该职责的完整源码覆盖；任何文件、symbol 或代码区域是否排他归属该节点；观察到的名称、字面值和直接绑定以外的参数业务含义；相邻组件是否获批；任何 Candidate Collaboration 是否获批；顶层模块源码覆盖是否完成

**疑点**：源码指派为 partial，三处定位只支持已列明的职责范围。

**风险**：若把跨进程交接读成每次运行均已成功启动或通过生产入口检查，会超出 Packet。

**AI 建议**：APPROVED。参数交接、进程启动与 Worker 入口都能直接定位，支持批准这个职责和明示的部分源码指派。（仅供参考）

**Owner 决定：Pending**。可选择批准、拒绝或暂缓，并说明理由。

### 2. 启动边界指向 Worker 包装入口（候选协作）

**职责 / 关系**：候选协作：Supervisor 的明确脚本绑定指向确定性 Python Worker 包装入口。

**需要判断的 Claims**

- 起点职责：候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。
- 终点职责：候选组件：把 Supervisor 的明确脚本绑定连接到 Python Worker 的受控包装入口。
- 关系与方向：协作；从上述起点指向上述终点。
- 关系解释：像发车闸门旁的目的地标签明确写着确定性 Worker 入口。这是未审核的脚本绑定候选，不替代或扩张唯一正式 Supervisor→Worker 关系。

**Evidence 与源码支持**

- 证据 1：`scripts/production/production_campaign_supervisor.ps1:52-52`，`$deterministicWorkerScript`。
- 证据 2：`scripts/production/run_qwen_deterministic_worker.py:14-15`，`main`。
- AI 源码分析：Supervisor 脚本第 52 行把确定性入口定位到 run_qwen_deterministic_worker.py，第 1280、1305 行显示它怎样随参数交给子进程。Python 包装器第 12 行导入 Worker main，第 14–15 行在直接执行时调用它。Packet 绑定两端职责和控制侧到包装入口的方向。

**不能证明**：静态源码无法确认实际命中这个分支、子进程执行成功或所有 Worker 入口采用同一方式。

**保留未知项**：实际生产请求是否进入此确定性路径及包装器是否成功运行。；其他 Worker 启动路径是否采用同一包装器。

**疑点**：此处只审明确的确定性脚本绑定，已有正式 Supervisor→Worker 关系有自己的证据和决定。

**风险**：容易把某条明确脚本路径扩大理解为所有 Worker 的启动协议。

**AI 建议**：APPROVED。开始于 Supervisor 的静态脚本绑定，落到 Python 包装入口，交接方向和范围都有直接证据。（仅供参考）

**Owner 决定：Pending**。可选择批准、拒绝或暂缓，并说明理由。

### 3. 启动边界分解为进程启动构造（候选分解）

**职责 / 关系**：候选分解把受控进程参数、环境和子进程启动处理解释为 Worker 启动边界的一项内部职责。

**需要判断的 Claims**

- 起点职责：候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。
- 终点职责：构造确定性 Worker 子进程的参数、工作目录、输出重定向、规范化环境和启动结果处理。
- 关系与方向：分解；从上述起点指向上述终点。
- 关系解释：Worker 启动边界包含一项候选职责：把受约束的启动信息构造成子进程；这不是已审核结论，也不证明运行发生。

**Evidence 与源码支持**

- 证据 1：`scripts/production/production_campaign_supervisor.ps1:224-291`，`Start-DeterministicWorkerProcess`。
- AI 源码分析：Supervisor 脚本第 224–291 行把参数与工作目录组装为 ProcessStartInfo，设置输出重定向和子进程环境，执行 Start，等待退出并写出日志。第 1305 行把它用在确定性 Worker 启动上。箭头从完整启动边界指向其中这项控制侧职责。

**不能证明**：这段源码无法证明整个边界的职责已列全、全部失败路径已覆盖，或实际启动成功。该子职责的源码归属仍为 unverified。

**保留未知项**：启动边界的完整子职责和全部启动路径。；源码排他归属以及某次实际启动和退出结果。

**疑点**：区分控制侧的进程构造和 Worker 侧的入口检查。此 Packet 只审前者作为父职责的一部分。

**风险**：若把分解箭头读成直接调用或源码所有权，会给 Evidence 添加它没有证明的结论。

**AI 建议**：APPROVED。这一步跨越参数、环境、进程与输出处理，形成可解释且有清晰边界的子职责。批准范围只覆盖这条静态分解。（仅供参考）

**Owner 决定：Pending**。可选择批准、拒绝或暂缓，并说明理由。

## 一次集中确认

您可以直接回复，例如："对象 1 可批准完整 Packet，理由……；对象 2 暂缓，理由……；对象 3 拒绝，理由……。请整理最终决定清单。" 未提及的对象保持 Pending。Codex 展示包含中文名称、稳定身份、决定、理由和完整范围的最终清单后，您只需对该明确清单确认一次。

## 审核附录

以下是同一份清单的完整审核范围。批准只覆盖对应对象的整个 Packet 和全部 Claims；若只认可其中一部分，请暂缓并重新准备材料。

### 对象 1：Worker 启动边界 的完整范围

- 源码快照：`ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`；工作树状态：`clean`。
- 如批准：批准此对象完整 Packet 的全部 Claims、下列 Evidence 所界定的静态解释；不批准保留未知项、其他对象或未经证实的运行时结论。
- 完整 Claims：

  - **IDENTITY**（来源：candidate-fact.object-key）：{"kind": "candidate-node", "node_id": "worker-launch-boundary"}
  - **NAME**（来源：candidate-fact.name）："Worker 启动边界"
  - **CLASSIFICATION**（来源：node-review-definition.classification）：{"node_type": "responsibility", "rationale": "worker-launch-boundary 描述的是生产控制把受参数和守卫约束的启动请求跨越进程边界交给翻译执行这一稳定架构职责，而不是某一个文件、函数、脚本包装器或可部署组件。三条 Candidate Evidence 跨越控制侧启动准备与 Worker 入口定位，只支持该职责边界值得作为候选节点表达；它们不足以证明一个物理组件、完整源码边界或排他所有权。"}
  - **RESPONSIBILITY**（来源：candidate-fact.responsibility）："候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。"
  - **PLAIN_EXPLANATION**（来源：candidate-fact.plain-explanation）："像一道受控发车闸门，Supervisor 准备好明确的 Worker 脚本后才跨进程启动。这里新增的是未审核源码说明；唯一正式 Supervisor→Worker 关系仍以原有 13 条批准证据为准。"
  - **SOURCE_ASSIGNMENT**（来源：candidate-fact.source-assignment）：{"evidence_ids": ["evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a", "evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429", "evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8"], "note": "T3 已审核关系只支持启动边界及其证据链；单条调用关系不足以证明完整源码归属。", "relationship_ids": ["relationship:production-control:launches:translation-execution"], "status": "partial"}
  - **CANDIDATE_EVIDENCE**（来源：node-review-definition.candidate-evidence-ids）：[{"evidence_id": "evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a", "role": "powershell_function_entry"}, {"evidence_id": "evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429", "role": "powershell_script_binding"}, {"evidence_id": "evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8", "role": "deterministic_worker_wrapper"}]
  - **DEPENDENCIES_AND_LIMITS**（来源：node-review-definition.dependencies-and-limits）：{"dependencies": [{"claims_sha256": "7197e0b96a1aafc1520a01d5e52a039a92532d7339c44a06ed586e6b4aaa2e1a", "decision_id": "github-comment:6516894339", "evidence_ids": ["evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13", "evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711", "evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3", "evidence:sha256:3929e17745639510a1a3c6a39b5be55b5a6afc2070c3f18cb300fa0107983308", "evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a", "evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc", "evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2", "evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa", "evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2", "evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d", "evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5", "evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914", "evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d"], "kind": "reviewed-relationship", "object_id": "relationship:production-control:launches:translation-execution", "result": "APPROVED", "reviewed_revision": "857a4b85cf4763b74ecff0d02e660b005a0c8bae"}], "retained_unknowns": ["是否在某次实际运行中到达或成功跨越该边界", "运行时前置条件、凭据和外部依赖是否可用", "该职责的完整源码覆盖", "任何文件、symbol 或代码区域是否排他归属该节点", "观察到的名称、字面值和直接绑定以外的参数业务含义", "相邻组件是否获批", "任何 Candidate Collaboration 是否获批", "顶层模块源码覆盖是否完成"], "zero_dependency_reason": null}

- Evidence 定位：

  - `evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a`：`scripts/production/production_campaign_supervisor.ps1:224-291`；symbol `Start-DeterministicWorkerProcess`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429`：`scripts/production/production_campaign_supervisor.ps1:52-52`；symbol `$deterministicWorkerScript`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8`：`scripts/production/run_qwen_deterministic_worker.py:14-15`；symbol `main`；文件 SHA-256 `8c2b76fcb1e1ac8499a431626c28b7751e6e2f8c1d3180634828629e3e63c0c9`。
  - `evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13`：`scripts/production/production_campaign_supervisor.ps1:52-52`；symbol `$deterministicWorkerScript`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711`：`scripts/production/production_campaign_supervisor.ps1:224-291`；symbol `Start-DeterministicWorkerProcess`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3`：`scripts/production/production_campaign_supervisor.ps1:1279-1284`；symbol `$workerArgumentVector`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:3929e17745639510a1a3c6a39b5be55b5a6afc2070c3f18cb300fa0107983308`：`scripts/production/production_campaign_supervisor.ps1:1305-1305`；symbol `Start-DeterministicWorkerProcess`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a`：`scripts/production/run_qwen_deterministic_worker.py:11-11`；symbol `module`；文件 SHA-256 `8c2b76fcb1e1ac8499a431626c28b7751e6e2f8c1d3180634828629e3e63c0c9`。
  - `evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc`：`scripts/production/run_qwen_deterministic_worker.py:14-15`；symbol `module`；文件 SHA-256 `8c2b76fcb1e1ac8499a431626c28b7751e6e2f8c1d3180634828629e3e63c0c9`。
  - `evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2`：`src/booktrans/production_worker/worker.py:741-741`；symbol `build_parser`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。
  - `evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa`：`src/booktrans/production_worker/worker.py:744-744`；symbol `build_parser`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。
  - `evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2`：`src/booktrans/production_worker/worker.py:799-799`；symbol `main`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。
  - `evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d`：`src/booktrans/production_worker/worker.py:801-820`；symbol `main`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。
  - `evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5`：`src/booktrans/production_worker/worker.py:821-822`；symbol `main`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。
  - `evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914`：`src/booktrans/production_worker/worker.py:823-824`；symbol `main`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。
  - `evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d`：`src/booktrans/production_worker/worker.py:825-839`；symbol `main`；文件 SHA-256 `7901a807e1cee6fdee2139f37074505b10bceb227de00816eac1d33f8429ab2a`。

- 依赖：
  - {"claims_sha256": "7197e0b96a1aafc1520a01d5e52a039a92532d7339c44a06ed586e6b4aaa2e1a", "decision_id": "github-comment:6516894339", "evidence_ids": ["evidence:sha256:05ea188dd9085a65c4642f0c75961b731a4a8477fd86ed4f02f1633e4ca77b13", "evidence:sha256:208e42baa3b95b1ef9f4400e521f3ec5ecc558d4091371667614b793e425b711", "evidence:sha256:5f59c334ec000f69e01750b17e111d71662c2e183a54ce25bdd62a6cc0f362a3", "evidence:sha256:3929e17745639510a1a3c6a39b5be55b5a6afc2070c3f18cb300fa0107983308", "evidence:sha256:c09b17cb3de85d77888286adef6daee1cfd6ca9ab4b4e1efcf6639addcfa1d6a", "evidence:sha256:179b1775ba7d00c1b9b4d371c4ec714f98259b47738f63b6073625f0b75bd1dc", "evidence:sha256:2d7d1ecfb242d54ccf66abf727985ae2ee84ea3db028d95a261479e8ef3a3de2", "evidence:sha256:b1641bdedc290731af75efff179ba3aeb50f4d00e2f261021c5f69137c6157aa", "evidence:sha256:abb2483c873946cca5de88a0e9f8405f69d5be723c190eb7ced3f0b5d4771fc2", "evidence:sha256:0f0af96ec0e1e8cefac855e55e6618e30355053e0d0f85e66c1f09cf342b010d", "evidence:sha256:ebb4142d4597073e24236794886dd8365f271e21672af5f25af8152f283ad8a5", "evidence:sha256:d3006bdb27376812bd512a822f55db071eef6cf77bdbe700ecb69417bff21914", "evidence:sha256:8174f96a8fd30d19c2a08a3d1da987f018eba6d3ea294b391c7d8986f23e715d"], "kind": "reviewed-relationship", "object_id": "relationship:production-control:launches:translation-execution", "result": "APPROVED", "reviewed_revision": "857a4b85cf4763b74ecff0d02e660b005a0c8bae"}
- 保留未知项：是否在某次实际运行中到达或成功跨越该边界；运行时前置条件、凭据和外部依赖是否可用；该职责的完整源码覆盖；任何文件、symbol 或代码区域是否排他归属该节点；观察到的名称、字面值和直接绑定以外的参数业务含义；相邻组件是否获批；任何 Candidate Collaboration 是否获批；顶层模块源码覆盖是否完成

### 对象 2：启动边界指向 Worker 包装入口 的完整范围

- 源码快照：`ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`；工作树状态：`clean`。
- 如批准：批准此对象完整 Packet 的全部 Claims、下列 Evidence 所界定的静态解释；不批准保留未知项、其他对象或未经证实的运行时结论。
- 完整 Claims：

  - **IDENTITY**（来源：relationship-review-definition.object-key）：{"kind": "candidate-collaboration", "object_id": "candidate-collaboration:supervisor-to-worker-wrapper"}
  - **ENDPOINT_RESPONSIBILITIES**（来源：relationship-review-definition.endpoint-bindings）：{"source": {"node_id": "worker-launch-boundary", "responsibility": "候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。"}, "target": {"node_id": "deterministic-worker-wrapper", "responsibility": "候选组件：把 Supervisor 的明确脚本绑定连接到 Python Worker 的受控包装入口。"}}
  - **SEMANTICS_AND_DIRECTION**（来源：relationship-review-definition.semantics）：{"direction": {"source_node_id": "worker-launch-boundary", "target_node_id": "deterministic-worker-wrapper"}, "family": "collaboration"}
  - **RELATIONSHIP_DESCRIPTION**（来源：candidate-relationship）：{"name": "启动边界指向 Worker 包装入口", "plain_explanation": "像发车闸门旁的目的地标签明确写着确定性 Worker 入口。这是未审核的脚本绑定候选，不替代或扩张唯一正式 Supervisor→Worker 关系。", "professional_description": "候选协作：Supervisor 的明确脚本绑定指向确定性 Python Worker 包装入口。"}
  - **CANDIDATE_EVIDENCE**（来源：relationship-review-definition.candidate-evidence-ids）：[{"evidence_id": "evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429", "role": "powershell_script_binding"}, {"evidence_id": "evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8", "role": "deterministic_worker_wrapper"}]
  - **DEPENDENCIES_AND_LIMITS**（来源：relationship-review-definition.dependencies-and-limits）：{"dependencies": [], "retained_unknowns": ["实际生产请求是否进入此确定性路径及包装器是否成功运行。", "其他 Worker 启动路径是否采用同一包装器。"], "zero_dependency_reason": "脚本路径绑定及包装器 main 入口直接支撑这条候选协作，两端职责另由 Packet 精确绑定。"}

- Evidence 定位：

  - `evidence:sha256:936eda38f0f420588192ddf9d50174979ae5106c36139459f1576bd31f78b429`：`scripts/production/production_campaign_supervisor.ps1:52-52`；symbol `$deterministicWorkerScript`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
  - `evidence:sha256:dc80ec834e36e03a4b7cca8c95fa77a6ec0f3391480ca03e49fcf990c4b607e8`：`scripts/production/run_qwen_deterministic_worker.py:14-15`；symbol `main`；文件 SHA-256 `8c2b76fcb1e1ac8499a431626c28b7751e6e2f8c1d3180634828629e3e63c0c9`。
- 零正式架构依赖：脚本路径绑定及包装器 main 入口直接支撑这条候选协作，两端职责另由 Packet 精确绑定。
- 保留未知项：实际生产请求是否进入此确定性路径及包装器是否成功运行。；其他 Worker 启动路径是否采用同一包装器。

### 对象 3：启动边界分解为进程启动构造 的完整范围

- 源码快照：`ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`；工作树状态：`clean`。
- 如批准：批准此对象完整 Packet 的全部 Claims、下列 Evidence 所界定的静态解释；不批准保留未知项、其他对象或未经证实的运行时结论。
- 完整 Claims：

  - **IDENTITY**（来源：relationship-review-definition.object-key）：{"kind": "candidate-decomposition", "object_id": "candidate-decomposition:worker-launch-boundary-to-supervisor-process-launch-construction"}
  - **ENDPOINT_RESPONSIBILITIES**（来源：relationship-review-definition.endpoint-bindings）：{"source": {"node_id": "worker-launch-boundary", "responsibility": "候选职责：在生产控制与翻译执行之间建立受参数和守卫约束的进程启动边界。"}, "target": {"node_id": "supervisor-process-launch-construction", "responsibility": "构造确定性 Worker 子进程的参数、工作目录、输出重定向、规范化环境和启动结果处理。"}}
  - **SEMANTICS_AND_DIRECTION**（来源：relationship-review-definition.semantics）：{"direction": {"source_node_id": "worker-launch-boundary", "target_node_id": "supervisor-process-launch-construction"}, "family": "decomposition"}
  - **RELATIONSHIP_DESCRIPTION**（来源：candidate-relationship）：{"name": "启动边界分解为进程启动构造", "plain_explanation": "Worker 启动边界包含一项候选职责：把受约束的启动信息构造成子进程；这不是已审核结论，也不证明运行发生。", "professional_description": "候选分解把受控进程参数、环境和子进程启动处理解释为 Worker 启动边界的一项内部职责。"}
  - **CANDIDATE_EVIDENCE**（来源：relationship-review-definition.candidate-evidence-ids）：[{"evidence_id": "evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a", "role": "powershell_function_entry"}]
  - **DEPENDENCIES_AND_LIMITS**（来源：relationship-review-definition.dependencies-and-limits）：{"dependencies": [], "retained_unknowns": ["启动边界的完整子职责和全部启动路径。", "源码排他归属以及某次实际启动和退出结果。"], "zero_dependency_reason": "子进程构造函数的静态实现直接支撑此项分解解释，两端职责另由 Packet 精确绑定。"}

- Evidence 定位：

  - `evidence:sha256:1eddc71f14cd226542bc4e6da258d6522db36cb9d5d2106052f10605022c851a`：`scripts/production/production_campaign_supervisor.ps1:224-291`；symbol `Start-DeterministicWorkerProcess`；文件 SHA-256 `63c6c8556f684d2b12d695869be456ac0d9614d7c778a998d1731df54811ed47`。
- 零正式架构依赖：子进程构造函数的静态实现直接支撑此项分解解释，两端职责另由 Packet 精确绑定。
- 保留未知项：启动边界的完整子职责和全部启动路径。；源码排他归属以及某次实际启动和退出结果。

## 技术定位

以下索引仅供追溯；完整 Packet 原文也保存在 `batch-review.json`。

- 对象 1：`batch-review.json` → `objects` → `candidate-node::worker-launch-boundary`；Packet `5a5ff7e276e66a201d8678dc4bb702bcd44a5a1020d84f471e246601c85002bc`。
- 对象 2：`batch-review.json` → `objects` → `candidate-collaboration::candidate-collaboration:supervisor-to-worker-wrapper`；Packet `9710545fdfdaacf24ad972498399d2ec45c455634aebffeed32a6515a1060247`。
- 对象 3：`batch-review.json` → `objects` → `candidate-decomposition::candidate-decomposition:worker-launch-boundary-to-supervisor-process-launch-construction`；Packet `88b516236a653b15be13e579f3ef211e50344dde588aee20fe985fac71386d45`。

