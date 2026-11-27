# Architecture Explorer v4 Ticket 09 实施交接

## 重复 Chrome 超时与增量审查

第二轮 [Draft Windows / Chrome](https://github.com/nnewlander/translate/actions/runs/45834005433/job/139957908909) 在 `764501d0d3d8830f11e7dd14233a5539b0cac89e` 上得到 739 通过、1 失败、2 跳过。原 18 项历史审核用例与递归 View 用例已经通过。两轮 CI 均记录 `test_real_browser_shows_candidate_nodes_collaborations_evidence_and_typography[1280-900]` 在 Chrome `--dump-dom` 调用上抛出 `TimeoutExpired`，达到 30 秒墙钟限制。

[正式集成 Head](https://github.com/nnewlander/translate/commit/acdb484b8d97b9d7b4a8a8d572d0fa121aeaeb82) 已通过本地 Merge Commit `cdf1085a2685679d58ec560e4d1bc6073ca7f020` 同步。父提交分别为 `764501d0d3d8830f11e7dd14233a5539b0cac89e`、`acdb484b8d97b9d7b4a8a8d572d0fa121aeaeb82`，tree 为 `b59dfeb26f4972d2134806058bcb0f4ed0c2ecb0`。唯一冲突是共享测试的固定 v3 Fragment 组织方式，保留已经验证的 T9 blob。已审查功能提交 `c8eead17fdf0b6a474d50ca77072d381301c9e9f` 仍在历史中，组合运行只用本 Worktree 的正式集成输入。

### 定位与变更依据

- `write_site` 在启动浏览器之前写完 Projection、静态数据、renderer 和样式。历史用例以独立 profile 打开本地 `file:` 页面，依次加载生产、文档与范围、翻译与质量、生产证据与发布四份 DOM 脚本。四份脚本使用有限的 DOM 交互和布局断言来验证页面，各自写出通过或错误标记。
- `--virtual-time-budget=2000` 是 Chrome 的页面虚拟时钟。Python 的 30 秒约束覆盖浏览器启动、页面处理和 stdout/stderr 收取。达到该约束时，`subprocess.run` 终止其直接启动的进程并抛出异常，后续退出码及四份 DOM 标记断言没有执行。旧 CI 日志没有这次 Chrome 调用的完成 DOM，也没有可区分启动、渲染和输出收取耗时的记录。Runner 上的触发条件和子进程状态仍未确定。
- Windows Chrome `158.0.8127.38` 的原预算测量保留 30 秒。原 1280 × 900 用例整体耗时 13.40 秒，Chrome 调用耗时 1.765 秒，退出码 0，stdout 52,013 字节，stderr 为空，四个标记齐全。包含 T7、T8、T9 的正式构建中，T9 1440 × 900 导航用例的 viewport 探针耗时 0.785 秒，页面 Chrome 进程耗时 1.469 秒，退出码 0，stdout 54,336 字节，stderr 为空。两个 pytest 用例共通过，完成后按本轮专属 profile 路径检查，匹配的 Chrome 进程数为 0。
- 基于重复的 Runner 进程超时和本地实际探针结果，测试函数的单一 Chrome 调用预算从 30 秒调到 60 秒，适用于该函数的三组视口。四份探针、断言、命令行、虚拟时钟、进程清理及重试行为保持原样。本地旧预算也通过，因此 CI 日志是失败证据，本地 A/B 是聚焦回归与测量，尚未证明 Runner 在 60 秒内完成。

### 聚焦回归与交付门槛

- 60 秒预算下，历史用例三个视口的 Chrome 进程耗时依次为 1.551、1.644、1.696 秒。三个进程退出码均为 0，stderr 为空，每份 DOM 都有四个通过标记。原 1280 × 900 的整个测试调用耗时 13.21 秒。
- 用 `--basetemp=D:/translate/t9time60` 执行该历史用例的三组参数、`test_v4_ticket_09_quality.py`、`test_recursive_views.py` 及 `test_v4_checkpoint_release_content.py`，共 41 通过，用时 127.48 秒。正式入口自动发现 T7、T8、T9 的 View Definition，组合 Projection 为 31 个 View、63 个 Node、66 条 Relationship。T9 1440 与 500 CSS px 的 Chrome 页面调用分别耗时 1.470、1.520 秒。完成后本轮专属 profile 匹配的 Chrome 进程数为 0。
- 计时包装器原样传递 `subprocess.run` 的参数和结果，发生超时时从原异常退出。两轮记录及 JUnit 位于 `output/architecture-explorer/ticket-09-timeout/`。本轮未运行全量 CI。

增量审查固定点为 `cdf1085a2685679d58ec560e4d1bc6073ca7f020`。从此固定点核对 `tests/architecture_explorer/test_candidate_fragments.py` 的一行预算变更及本节证据。双 PASS 后按授权调整 Draft PR #95 Base、推送并运行最终正式 Windows / Chrome CI。

## Draft PR #95 预验证跟进

[预验证 Windows / Chrome](https://github.com/nnewlander/translate/actions/runs/45825103412/job/139927117084) 在远端 Head `8686a1c60bff5f80deda1ef37f74c2708538f217` 上得到 720 通过、20 失败、2 跳过。18 项历史 Node Review 用例依赖旧测试探针改写投影后点击卡片重绘。另有两项 30 秒超时，分别发生在 Chrome 页面 DOM 抓取和 viewport 探针启动。

在 [T7 PR #93](https://github.com/nnewlander/translate/pull/93) 核验修复提交 `a821bed7b100914bb7f1976e590fcca0e98db803` 后，本地 Merge Commit `63eda88dd20fc3a03bbb806f524dac39bcac7bb6` 同步了该提交，其 tree 为 `bb0a855c379dfc5bd4d0c9bd639c2a554160ab48`。原 T9 功能提交 `c8eead17fdf0b6a474d50ca77072d381301c9e9f` 保留。同步仅引入 T7 handoff 和 `t8_node_review_dom.js` 的既有修复，两文件的 Git blob 均与 T7 一致。审核状态由生成页面提供，六种状态及三种宽度继续实际覆盖，中文标签、布局、详情和 Evidence 断言保留。同期校验确认 T9 功能、共享发现测试和产品导航及审核代码的内容与原草稿提交一致。

使用项目解释器在 Windows 上以 Chrome `158.0.8127.38` 对本地 Merge Commit 执行两次聚焦检查：

- `--basetemp=D:/translate/t9ci1`。对照 CI 日志逐项核对 20 个失败标识，全部通过。另有 4 项 T9 构建、Projection、Perspective 导航与 Evidence 用例及 1 项共享 View 发现用例通过。合计 25 通过，用时 130.22 秒。
- `--basetemp=D:/translate/t9ci2`。单独执行 `test_candidate_fragments.py::test_real_browser_shows_candidate_nodes_collaborations_evidence_and_typography[1280-900]` 和 `test_recursive_views.py::test_real_offline_chrome_explores_the_internal_candidate_view[1440]`，2 通过，用时 39.83 秒。两项超时未在两次本地运行中复现，GitHub Runner 的触发条件仍未确定。测试时限及重试行为沿用既有实现。

本地日志和 JUnit 位于 `output/architecture-explorer/ticket-09-ci-follow-up/`。本轮仅更新 [Draft PR #95](https://github.com/nnewlander/translate/pull/95) 的说明，本地同步留待下一次推送。远端预验证仍保留失败记录。待 T7 与 T8 依次正式集成，再同步实际集成 Head、调整 Base 并运行正式 Windows / Chrome CI。

## 审查固定点

```text
Base / Bstart: 72864310175049a34e0c8ed789e096ead9c73a65
HEAD:          72864310175049a34e0c8ed789e096ead9c73a65
B0:            09aaf4a033123c7eca8d280fa8982e5b0e711b86
Branch:        codex/architecture-explorer-v4-ticket-09
Worktree:      D:\translate\worktrees\architecture-explorer-v4-ticket-09
```

#75 和 #76 在 GitHub 均为 completed，其合入提交均为 Bstart 的祖先。Bstart 是开工时查询到的 `experiment/matt-skills` Head。B0 是固定设计和内容对比基线。[Ticket #83](https://github.com/nnewlander/translate/issues/83) · [Spec #74](https://github.com/nnewlander/translate/issues/74) · [T1 覆盖审计](../architecture-explorer-v4-ticket-01.md) · [T2 公共接口](v4-ticket-02-interface-contract.md)

## 独立审查 Finding 修复

初审 Standards FAIL / Spec FAIL。本轮处理两项 Finding，保留已通过审查的架构分解、对象身份和处置 Perspective。

- **导航。** 沿用 T7 最终全局交互。普通点击、Enter 和 Space 在 QA 中心节点或祖先目标上打开节点详情。Chrome 验证六次普通激活均保持当时的路径，通过详情中的两个命名按钮切换及返回。返回验证面包屑精确恢复原路径，并将焦点还给祖先 View 的 QA Appearance。共享发现测试保留 T7 新增的深层导航和 Source Trail 用例、完整 View Definition 路径、逐文件 SHA-256 和完整 View ID 集合。历史 v3 测试继续固定四个 v3 Fragment。
- **恢复依据。** 正式 Supervisor 的 `Get-AutoRecoveryPolicy` 先通过 `Get-ValidatedRecoveryPolicyEvidence` 调用 `scripts/production/build_recovery_evidence.py`。脚本收集阶段报告和校验结果，调用 `canonicalize_qa_failure`，再把分诊结果组装为策略输入。Supervisor 的 `Invoke-RecoveryPolicyRegistry` 通过 `scripts/production/decide_recovery_policy.py` 调用注册表判断资格。隔离 Canary 的 `continue_canary_from_raw → decide_numeric_recovery_policy` 是另一个入口。当前 View 限于正式生产的数值恢复证据及判定，处理器执行、实际授权与结果仍为 Unknown。

### 本轮隔离组合

以 T7 `a5f794636ad40d084af09a071492afb24f5a8e5d` 建立 detached checkout `D:\translate\worktrees\architecture-explorer-t9-review-combo`。在其中组合 T8 `3a95e1bf94a7b670eee343c87bd3f677e7240405` 的五个 Fragment、View Definition 和两份专属测试，以及 T9 的 dirty 内容和测试。共享测试以 T7 最新版为基础，保留历史固定输入。隔离 checkout 的生产源码、Explorer 运行模块和 renderer 与 T7 commit 相同。36 个实际输入文件、代码版本、tree 和逐文件 SHA-256 见 [组合输入清单](D:/translate/worktrees/architecture-explorer-t9-review-combo/output/t9-review-combination-inputs.json)，清单 SHA-256 为 `d0b4be57c42d6d5e4e564c9f845438c95bffda9b99d13ed74b32e90dbeaf7ef9`。

正式 build 默认发现四份 View Definition。组合 Projection v2 为 31 个 View、63 个 Node、66 条 Relationship 和 5037 项 Source Evidence。正式关系仍为 `APPROVED/current`，本票对象保持 Pending，T9 Fragment 现引用 27 个不同 Evidence ID。组合站点的 `explorer-data.json` SHA-256 为 `3d0675078af27bee831ea19c5038e31fa232ce2d21fecb4aa753d04627e3f088`，生成页面使用 T7 的 `app.js`。

在隔离 checkout 执行 `& .\scripts\architecture_explorer\prepare_worktree.ps1`，以 `$python=(Resolve-Path '.venv\Scripts\python.exe').Path` 使用项目解释器。浏览器用例设置 `$env:ARCHITECTURE_EXPLORER_BROWSER='chrome'`。

- 导航 Red 用 `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py -k chrome --basetemp=D:/translate/t9rrnav`。1440 和 500 CSS px 均在旧脚本的 `Perspective switch did not open` 处失败。
- 证据 Red 用 `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py -k checkpoint --basetemp=D:/translate/t9rrfacts`，因关系缺少正式生产的 `collect_immutable_recovery_facts` 证据而失败。
- Green 与聚焦组合命令为 `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_v4_checkpoint_release_content.py tests/architecture_explorer/test_recursive_views.py --basetemp=D:/translate/t9rrgreen`。退出 0，42 项通过，用时 125.21 秒。T7、T8、T9 各自的真实 Chrome 测试均通过 1440 和 500 CSS px。
- `& $python -m pytest -q tests/architecture_explorer/test_v3_ticket_06_integration.py --basetemp=D:/translate/t9rrv3` 退出 0，3 项通过，用时 32.78 秒。Python 编译、`node --check`、`git diff --check` 和 36 个输入文件的 SHA-256 复核均通过。

T9 实施分支仍固定在 Bstart。新的交互回归随 T7 最终共享代码运行。下面的首次交付记录属于历史基线，本轮按授权范围执行聚焦回归。

### Owner 验收清单

从 [最新离线预览](D:/translate/worktrees/architecture-explorer-t9-review-combo/output/architecture-explorer/ticket-09-tests/t9rrgreen/index.html) 进入「质量判断与受限修复」。

- [ ] **确定性结构 QA 内部职责。** 查到格式校验和单元 QA 记录。分别以点击、Enter、Space 激活 QA 中心节点，详情打开且面包屑保持。使用「切换到候选结果处置」进入处置 View。
- [ ] **格式结构校验。** 从「格式锚点与文本岛校验」进入。核对箭头指向被委托的文本诊断，Node 或关系证据可定位到 `FormatAnchorQA.validate`。
- [ ] **确定性文本诊断。** 下钻核对 D050/D051 精确数字证据与数字锚点归属的职责差异。从证据打开 Source Trail，再关闭并确认焦点返回；用 Back 核对逐层返回。
- [ ] **候选结果处置。** 区分 QA 摘要交给语义审核与失败码交给修复门禁两条箭头。打开「提交失败码」的关系详情，核对方向、待审核状态和证据。普通激活中心节点保持路径，通过「返回确定性结构 QA 内部职责」截断路径并恢复焦点。
- [ ] **Checkpoint QA 恢复依据。** 从「单元 QA 记录」进入，核对阶段报告、失败归一和生产 Supervisor 注册表资格的顺序及 Evidence。确认 Canary 独立入口和 Unknown 边界明确。用 Home 回到总览。

推荐在 1440 和 500 CSS px 检查标签、箭头、焦点及详情滚动。早期审查观察到窄屏长源码标识会让共享详情弹窗内部产生横向滚动，产品可读性仍待 Owner 验收。

## 内容与源码依据

| View | 问题、关系和证据边界 |
| --- | --- |
| 确定性结构 QA 内部职责 | 从结构 QA 分解出格式校验与单元 QA 记录。生产适配器把响应段放进带标签的候选，返回单元身份、`passed` 和 `diagnostics`。依据为 `FormatAnchorQA.validate`、`harness_qa_record` 和 `qa_codes`。 |
| 格式结构校验 | `FormatAnchorQA.validate` 比较文本岛顺序及集合、核对格式与保护锚点，并委托和汇集 `DeterministicCandidateQA.validate` 的诊断。分解反映组合职责。 |
| 确定性文本诊断 | 普通 token 检查与数字锚点归属检查协作生成诊断。分解独立展示 D050/D051 对精确记录的使用，以及唯一独立锚点上的额外数字检查。D050 的上游证据路由由 T7 调查，本票描述 QA 对已绑定输入的消费。 |
| 候选结果处置 | `candidate-disposition` 以候选数据和决策输入为对象。箭头沿 RAW、QA 摘要、失败码、语义审核结果与修复合同的交接方向。证据包括 `process_unit` 的定义和调用点、记录适配器和修复门禁。注入 QA 缺省可只翻译。配置 QA 后，失败分支可用精确诊断进入门禁，语义审核使用 QA 摘要且可按快路径跳过。语义修复和确定性修复分别使用审核结果与诊断码向同一门禁提供输入。修复返回重跑 QA，语义修复通过后还会再次审核。 |
| Checkpoint QA 恢复依据 | 正式 Supervisor 获取 Checkpoint QA 阶段报告的归一及分诊证据，再向注册表查询数值恢复资格。上节记录生产调用链、Canary 边界及可核验范围。 |

代表路径以三条显式候选分解边连接「确定性结构 QA → 格式锚点与文本岛校验 → 确定性文本诊断 → 精确数字证据匹配／数字锚点归属检查」。从总览到文本诊断 View 共四次 View 跳转。处置 Perspective 的切换、返回和跨模块跳转属于 View Transition。具体 Provider receiver、运行时分支命中、修复结果、完整源码归属和其他恢复策略仍待核实。

| 固定 B0 与初次交付运行投影 | B0 | 初次 T9 |
| --- | ---: | ---: |
| 顶层模块 | 7 | 7 |
| Candidate Node | 27 | 34 |
| Candidate Collaboration | 12 | 17 |
| Candidate Decomposition | 9 | 14 |
| 正式 Relationship | 1 | 1 |
| View | 12 | 17 |
| Appearance | 39 | 56 |
| 可达最大 View 跳转深度，不重复 View | 2 | 4 |
| Perspective | 3 | 4 |
| 去重 Candidate Evidence ID | 72 | 89 |

初次交付的 7 个 Node 和 10 条 Relationship 共引用 21 个不同的 Evidence ID。相对 B0 新增 17 个被引用的 ID，其余引用复用。B0 使用 [T1 固定 Git 清单](../architecture-explorer-v4-ticket-01-baseline.json)，T9 使用正式 build 生成的 Projection v2。新增对象均为 Pending，批准范围为空。正式 Supervisor → Worker Relationship 仍为 APPROVED/current，保留 13 条既有 Evidence。

## 完整改动清单

```powershell
git status --short
git diff --stat 72864310175049a34e0c8ed789e096ead9c73a65
git diff --no-ext-diff 72864310175049a34e0c8ed789e096ead9c73a65
git ls-files --others --exclude-standard
```

- 跟踪文件 `tests/architecture_explorer/test_recursive_views.py`。完整 diff 由上述命令提供。v3 基线用例显式固定 v3 输入。默认发现用例采用 T7 的全命名空间断言，逐文件比对 SHA-256，并核对全部已投影 View。
- 跟踪文件 `tests/architecture_explorer/test_v3_ticket_06_integration.py`。完整 diff 由上述命令提供。固定使用 v3 的四个递归 Fragment，保留历史清单和 Review 边界的原断言。与 T7 同文件的修复逐字节一致。
- 未跟踪 `architecture-explorer/recursive-candidates/v4-ticket-09-quality.v2.json`。审查完整文件，包含 Candidate 声明及 Evidence 引用。
- 未跟踪 `architecture-explorer/views/v4-ticket-09-quality.v1.json`。审查完整文件，包含 QA Appearance 的共享入口、View 与显式导航。
- 未跟踪 `tests/architecture_explorer/test_v4_ticket_09_quality.py`。审查完整文件，包含正常构建、投影和 Chrome 测试。
- 未跟踪 `tests/architecture_explorer/v4-ticket-09-quality-dom.js`。审查完整文件，包含直接运行于离线页面的导航和详情断言。
- 未跟踪 `docs/architecture-explorer/v4-ticket-09-handoff.md`。审查完整文件，包含本次交接内容。

共享入口由 T2 的 View Definition 发现机制装配。Candidate Fragment 走现有 `--candidate-fragment` 输入，构建时按有依赖关系的既有文件在前、T9 在后的顺序传入。本票复用语义路由与候选获取、语义审核合同边界、修复授权门禁与受限修复调用边界的稳定身份和两条既有协作。源码事实生成、Projection、renderer、审核权威及 v3 合同沿用现有实现。

## 验证与独立审查状态

在 T9 Worktree 根目录执行 `& .\scripts\architecture_explorer\prepare_worktree.ps1`，返回 `PROJECT_VENV_READY`。Junction 目标为 `D:\translate\.venv`，解释器为 Worktree 的 `.venv\Scripts\python.exe`，解析后的 prefix 为共享目录。脚本验证 `PYTHONPATH` 与 `booktrans` 来自 T9 的 `src`，`& $python -m pip check` 返回 `No broken requirements found`。下列 Python 命令均在同一 PowerShell 进程内先执行准备脚本，再执行 `$python=(Resolve-Path '.venv\Scripts\python.exe').Path`。Chrome 用例设置 `$env:ARCHITECTURE_EXPLORER_BROWSER='chrome'`。

| 执行记录 | 结果 |
| --- | --- |
| `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py --basetemp=D:/translate/t9red1` | 退出 1，实际 Projection 缺少 QA 职责 View。 |
| 同一命令以 `--basetemp=D:/translate/t9green1b` 执行 | 退出 0，1 项通过，正式 build 和最小处置 View 路径可用。首次 Green 尝试发现既有修复调用关系 ID 重复，改为直接复用。 |
| 同一命令以 `--basetemp=D:/translate/t9red2b` 在 Chrome 执行 | 退出 1，两种宽度均到达实际 QA 子视图入口，均缺少文本诊断 Appearance。 |
| `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py -k checkpoint_recovery --basetemp=D:/translate/t9red3` | 退出 1，实际 Projection 缺少 Checkpoint 恢复 View。 |
| `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py tests/architecture_explorer/test_recursive_views.py --basetemp=D:/translate/t9final` | 退出 0，33 项通过。关系详情的合成点击先聚焦按钮，与实际指针或键盘激活一致。 |
| `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_09_quality.py --basetemp=D:/translate/t9final3` | 退出 0，4 项通过。Chrome DOM 测得 1440 和 500 CSS px，覆盖四次 View 跳转、面包屑、Back、Home、焦点恢复、处置切换、关系详情、两种 Source Trail 入口，以及 Checkpoint 恢复 View。此机 Chrome 将 `--window-size` 请求 480 px 夹到 500 px，测试结果以实测视口为准。 |
| `& $python -m pytest -q tests/architecture_explorer/test_recursive_views.py tests/architecture_explorer/test_v4_ticket_09_quality.py --basetemp=D:/translate/t9handoff` | 退出 0，最终共享测试与 T9 内容共 33 项通过，用时 162.75 秒。 |
| `& $python -m pytest -q tests/architecture_explorer/test_v3_ticket_06_integration.py --basetemp=D:/translate/t9regression` | 退出 0，固定 v3 输入后的三项历史用例全部通过，用时 57.28 秒。 |
| `git diff --check` 与 `node --check tests/architecture_explorer/v4-ticket-09-quality-dom.js` | 退出 0。 |

第一次标准 Explorer 验证执行 `& .\scripts\architecture_explorer\verify.ps1`，临时目录设为 `D:/translate/t9tmp`。环境和静态检查通过。全套结果为 710 通过、2 跳过、1 失败。唯一失败为 v3 清单测试，以通配符加载了新增 v4 Fragment，实际 34 个 Candidate Node。修复固定其 v3 输入并保留原来 27 个 Candidate Node 及其他边界断言。

最终标准运行的命令如下。非 Explorer 仓库全套测试、Firefox/Edge 人工回归、正式 CI 和候选合同验证未运行。

```powershell
& .\scripts\architecture_explorer\prepare_worktree.ps1
New-Item -ItemType Directory -Path D:/translate/t9tmpv -Force | Out-Null
$env:TEMP='D:/translate/t9tmpv'
$env:TMP=$env:TEMP
& .\scripts\architecture_explorer\verify.ps1 | Tee-Object -FilePath D:/translate/t9check/output/t9-handoff-verify.txt
```

最终运行退出 0，环境和静态检查均通过。JUnit 记录 713 项，711 通过、2 跳过、0 失败、0 错误。69 项必跑 Chrome 回归全部通过，浏览器为 Chrome 158.0.8127.38。两个跳过项是 T5 对 T6 实际导入器的可选跨 Worktree 回归，默认未设置 `ARCHITECTURE_EXPLORER_T6_CONSUMER`。它们与下述 T7/T8/T9 内容联调独立。

### T7/T8/T9 联调

- 隔离 checkout 为 `D:\translate\t9check`，三组输入的 HEAD 均为 Bstart。用 `& D:/translate/t9check/output/capture.ps1` 只读捕获各自的实际未提交 Candidate Fragment、View Definition 和 DOM 脚本。捕获前后校验文件 SHA-256。每项路径、HEAD 和完整 SHA-256 保存在 `D:\translate\t9check\output\ticket-09-combination-inputs.json`，清单 SHA-256 为 `35095a89a3356f34623bc79c3b7a111793730eaa948ff2d4fbbad0b25f80ffc5`。
- 在 T9 环境执行 `Get-Content -Raw D:/translate/t9check/output/verify-combination.py | & $python - | Tee-Object -FilePath D:/translate/t9check/output/combination-result.txt`。正式 build 使用隔离 checkout 作为 `--repository`，传入排序的 Candidate Fragment，以及同一既有映射、快照和正式 Review 输入。View Definition 由 CLI 默认发现。退出 0。
- 最后一轮 Projection v2 输出 31 个 View、63 个 Node、66 条 Relationship、5037 项 Source Evidence。验证所有 Appearance、目标 View、关系端点和 Candidate Evidence 引用可解析，候选关系保持 Pending，正式关系保留 13 项 Evidence 与 APPROVED/current。另核对了发现的 View Definition 路径、逐文件 SHA-256 和 Node Evidence。T7、T8、T9 的六次 Chrome DOM 运行全部通过，实测桌面 1440 px 和窄屏 500 px。离线站点位于 `D:\translate\t9check\output\architecture-explorer\ticket-09-combination\index.html`。
- 共享测试的真实冲突集中在 `test_cli_build_discovers_authoritative_view_documents_without_an_option`。以隔离 checkout 的 Bstart 测试作为中间参数，`git merge-file -p` 对早期 T9/T7 快照返回 1。T9 已在自己的共享测试中保留固定 v3 输入，并采用 T7 的发现断言。对最终 T9/T7 重跑返回 0。T9/T8 仍有同一发现用例的一处文本冲突，最小解决办法是保留 T9 当前合并后的测试。T9 共享测试 SHA-256 为 `c4115a808eb57cafa13a500207876157f20419248527cc7a239c83af6908d3a0`。正式集成时仍需针对届时的分支和批次快照复核。

### 语义与视觉检查

已逐图对照源码定义、调用和条件分支，核对展示的箭头、标题、说明和审核边界。五个 View 的 1440 px 桌面与 500 px 窄屏截图，以及处置视角的关系详情截图，位于本 Worktree 的 `output/architecture-explorer/ticket-09-tests/t9final3/visual/`。已检查箭头方向、标签、面包屑、候选状态和详情布局。新关系原先在窄屏侧边箭头上出现过长的逐字换行，已缩短自有标签并重新截屏。部分侧边标签仍纵向折行。窄屏详情里，长限定符号和源码片段会触发弹窗内部横向滚动。处置说明和候选状态可读，源码预览的可读性仍待 Owner 验收。若后续调整共享 renderer，可先验证让 Evidence 标题中的长标识自动换行，将源码横向滚动限制在代码块中。

等待原 Reviewer 的 Standards、Spec 聚焦复审和 Owner 产品验收。正式候选 Head 和固定 Commit-to-Commit Content Delta 随后续授权的集成执行，当前 dirty-worktree 投影提供本地内容对比。
