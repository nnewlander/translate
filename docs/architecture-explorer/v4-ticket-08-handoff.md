# Architecture Explorer v4 Ticket 08 本地审查交接

```text
Base / Bstart: 72864310175049a34e0c8ed789e096ead9c73a65
HEAD:          72864310175049a34e0c8ed789e096ead9c73a65
B0:            09aaf4a033123c7eca8d280fa8982e5b0e711b86
Branch:        codex/architecture-explorer-v4-ticket-08
Worktree:      D:\translate\worktrees\architecture-explorer-v4-ticket-08
```

Issue #75 和 #76 在 GitHub 均为 closed/completed。开工及第一轮组合构建时，GitHub 将集成分支解析到 Bstart。T1 的 #87 合并及 T2 的 #89 合并均是其祖先。B0 用于内容对比，Bstart 用于审查本票工作树。Spec #74 仍处于 proposed，现行合同为 v3。[Spec #74](https://github.com/nnewlander/translate/issues/74) · [Ticket #82](https://github.com/nnewlander/translate/issues/82) · [七模块覆盖审计](../architecture-explorer-v4-ticket-01.md) · [T2 接口](v4-ticket-02-interface-contract.md)

## 两项 P2 的聚焦修订

本轮依据「审查v4 T8」的 Standards PASS / Spec FAIL 记录，修订三个 Candidate Node 和四条 Candidate Relationship 的表述及 Evidence。对上一轮组合产物逐项比对，63 个 Node 身份、66 条关系身份、31 个 View 的完整记录、导航以及正式架构与相关审核状态一致。两项修订涉及的 Evidence 均可进入 Definition Source Trail。

- **RAW 与阶段证据。** Canary 在首次创建整份 RAW 时经 `persist_checkpoint_raw` 走独占创建。正式 Checkpoint 的普通采集分支调用 `append_raw_response_line`，逐条追加、flush/fsync 并跳过已有 UID。历史 RAW 绑定旧请求而需重新采集时，当前请求绑定的证据单独保存于 `provider_reacquisitions/<uid>.<request_fingerprint>.json`，原 RAW 保留。正式采集是否齐备要根据当前请求的有效采集集合判断。复跑 Reviewer 的临时文件反例，正式追加保留首条、跳过同 UID 的不同响应并允许新 UID，Canary 拒绝第二次创建。[Canary 调用](../../src/booktrans/production_worker/canary.py#L383) · [生产分支与齐套判断](../../src/booktrans/production_worker/production_checkpoint.py#L5481) · [重新采集与追加实现](../../src/booktrans/production_worker/unit_store.py#L503)
- **候选 DOCX 写回。** `touched_ooxml_parts` 取自投影输入的分组，在尝试定位前形成，用于界定包保真比较范围。投影正常返回后才累计定位、写入尝试和写入核对计数。 `writes_verified` 核对构造中的文本节点，不能代替候选重抽取、锚点回检或视觉 QA。复跑 Reviewer 的缺失部件反例，`word/missing.xml` 仍列在计划内，`locators_resolved`、`writes_attempted`、`writes_verified` 均为零，出现 `LOCATOR_PART_MISSING`，源有部件的输出字节保持一致。实际整书交付仍需其自身证据。[投影实现](../../src/booktrans/ooxml/tagged_projection.py#L45) · [构造文本核对](../../src/booktrans/ooxml/tagged_projection.py#L116) · [验证范围](../../src/booktrans/harness/package_preservation.py#L14)

### 本轮预览和共享版本

最新离线入口位于隔离工作树 `D:\translate\worktrees\architecture-explorer-v4-ticket-08-p2-preview\output\architecture-explorer\v4-ticket-08-tests\t8-p2-final\index.html`。其 HEAD 是 T7 已提交的本地合并点 `a5f794636ad40d084af09a071492afb24f5a8e5d`，包含 T7 的内容和最终 Web。只复制 T8、T9 的 8 份内容 JSON 及 T8 浏览器测试，预览使用该提交自身的 Web 文件，未覆盖或复制 Web 文件到 T8 工作树。

| 共享文件 | 上轮 Owner 导航验证快照 SHA-256 | 本轮 T7 最终提交 SHA-256 |
| --- | --- | --- |
| `app.js` | `55480febfec37edd0943e1b13b748e64e96928e24ed58b2d427f6e7f75736aa8` | `5078e529bc876ea789f77077a97474569473a299edcbddc0cf71b1ac7a0e9333` |
| `t4-state.js` | `58c79479919a5ec9259a54c55f9ce170c3ea4980a0fd58fa37a5fd48d79e61ad` | `58c79479919a5ec9259a54c55f9ce170c3ea4980a0fd58fa37a5fd48d79e61ad` |

正式集成分支在 GitHub 实时核对仍为 `ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`。此处已在 T7 最终 Git 内容上完成兼容性验证。正式交付前仍需确认 T7 进入集成分支，并在届时的集成 Head 复验；本轮 T8 HEAD 保持 Bstart。

### 本轮验证

以下构建、Chrome 及既有行为用例均从预览工作树根目录通过项目解释器运行。`PYTHONPATH` 指向该工作树的 `src`。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:PYTHONUTF8 = '1'
$env:ARCHITECTURE_EXPLORER_BROWSER = 'chrome'
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp output/t8-p2-final tests/architecture_explorer/test_v4_checkpoint_release_content.py tests/unit/test_request_supersession.py::test_request_bound_reacquisition_supersedes_only_unbound_base_raw tests/unit/test_tagged_roundtrip.py::test_tagged_projection_moves_bold_anchor_without_source_run_position
```

- **7 passed**（17.55s），包含正式构建、七个 View 在实际 1440/500px 的 Chrome DOM、请求绑定重新采集和成功写回核对。临时工件反例重跑两条通过，执行脚本为 `p2/reviewer-counterexamples.py`。
- 受影响的三页使用既有 Chrome CDP 矩阵在 480、960、1280、1440px 核验箭头、标签、遮挡与横向溢出。三页均通过，已查看代表性桌面和窄屏截图。此前独立通过的四处导航修复保持原样。完整套件及独立双轴审查未重复执行。
- 证据位于 T8 的 `output/architecture-explorer/v4-ticket-08/p2/`。其中 `inputs.json` 的 SHA-256 为 `8687a51765ed817e1bcf4671714d18e21b0d00de225720c1c10911f91ae2ba5a`。`committed-web.json` 记录实际 Git blob。`focused.log`、`projection.log`、`counterexamples.log` 及 `visual/` 保存结果。

### 七个 View 的 Owner 验收清单

建议桌面和窄屏各浏览一次。核对候选状态、关系方向和标签、详情内 Evidence/Source Trail，以及 Back 后的面包屑。下面列出每页的语义与视觉检查点。

| View | 语义核对 | 视觉与操作核对 |
| --- | --- | --- |
| Checkpoint 封存职责 | Prepared Seal 的创建和预检是条件路径，投影来自所选工件。 | 区分分解和协作箭头，从投影卡进入清单发布。 |
| 清单发布与恢复 | 根清单更新、后继发布和恢复分别有前置条件。 | 清单投影箭头指向发布，从发布卡进入跨模块页。 |
| Checkpoint 证据工件 | 区分 Canary、正式 RAW 追加和请求绑定重新采集，RAW 存在无法确定采集齐套。 | 打开 RAW 详情并沿 Evidence 核查生产与 Canary 入口，窄屏检查技术名换行。 |
| 从封存工件到批次发布 | 清单、最终 QA 与门禁交接各自有边界，门禁执行有条件。 | 两个重复出现的证据节点打开详情并保持面包屑。 |
| 发布成员资格与后继选择 | 范围、最终 QA、门禁补充证据分别参与派生，TOC 有显式阶段条件。 | 确认门禁卡打开详情，后继选择箭头的方向清晰。 |
| 候选 DOCX 的生成入口 | 计划部件、定位与写入计数、失败诊断各有含义，渲染事实来自独立入口。 | 查看写回详情，再从写回卡进入包验证。 |
| 候选 DOCX 的包保真与回检 | 计划部件划定比较范围，重抽取和锚点核对分别提供往返证据。 | 计划部件交接标签在窄屏可读；重复的写回卡打开详情。 |

## 历史 Owner 验收反馈修复

隔离工作树 `D:\translate\worktrees\architecture-explorer-v4-ticket-08-combined-latest` 承载对照验证，HEAD 为 `ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`。从 T7、T8、T9 复制 13 份真实内容 JSON，由正式 `review_cli build` 发现 View 文档并生成离线页面。实际使用的是 T7 分支 `codex/architecture-explorer-v4-ticket-07` 在 `72864310175049a34e0c8ed789e096ead9c73a65` 上的未提交 Web 文件。实测哈希与 T7 最新交接引用的 `output/t7-global-shared-assets.json` 一致；该清单的 SHA-256 为 `50fb8b1a31cbd1ec843e0321d3feb3ed8575dd40742efbead0b7cbf3c48e6340`。

| 共享文件 | 本轮实测 SHA-256 |
| --- | --- |
| `architecture-explorer/web/app.js` | `55480febfec37edd0943e1b13b748e64e96928e24ed58b2d427f6e7f75736aa8` |
| `architecture-explorer/web/t4-state.js` | `58c79479919a5ec9259a54c55f9ce170c3ea4980a0fd58fa37a5fd48d79e61ad` |

- **基线重现。** 原共享 Web 和原 T8 View Definition 下，1440px 与 500px 均分别复现「批次清单发布与恢复」和「Checkpoint 最终 QA 证据」普通点击截断面包屑并回到祖先。回跳没有打开 Node 详情。
- **仅换入 T7。** T7 在导航路径内拦住报告的两个回跳，点击重复 Appearance 显示详情。但从「Checkpoint 证据工件」进入跨模块 View 时，点击「批次清单发布与恢复」仍会另开「清单发布与恢复」。该目标对这条访问路径并非祖先，回跳由 T8 的 Appearance 声明触发。两档 Chrome 均复现。这个反例归属 T8 View Definition。
- **T8 配置修复。** 删除四个反向普通点击入口。跨模块 View 中的「批次清单发布与恢复」和「Checkpoint 最终 QA 证据」、成员资格 View 中的「批次发布债务与门禁」，以及包保真 View 中的「带标记的候选 DOCX 写回」现在打开详情。相同 Node 在正向入口 View 内仍可有效下钻。Back 和 Home 保留原返回路径。
- **产品与可信边界。** 两次组合投影的 63 个 Node、66 条 Relationship、Evidence Index 和正式架构逐项一致。31 个 View 的完整投影差异恰为上述四个 `primary_action` 的移除。T8 的交付只改自身 View Definition、浏览器回归和交接，组合用的两份 T7 Web 文件留在隔离环境。

以下命令在各自工作树根目录执行，使用已验证的项目 Junction，并将 `PYTHONPATH` 指到当前 checkout 的 `src`。三次组合均走同一正式构建 seam 和实际 Chrome。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:PYTHONUTF8 = '1'
$env:ARCHITECTURE_EXPLORER_BROWSER = 'chrome'
$python = (Resolve-Path '.venv\Scripts\python.exe').Path

# 在隔离组合工作树。第一轮使用基线 Web，第二轮覆加 T7 Web，第三轮更新 T8 View Definition。
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/owner-t8-red-click tests/architecture_explorer/test_v4_checkpoint_release_content.py
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/owner-t8-t7-only tests/architecture_explorer/test_v4_checkpoint_release_content.py
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/owner-t8-green tests/architecture_explorer/test_v4_checkpoint_release_content.py

# 在 T8 自身工作树。$logs 为本票 output/architecture-explorer/v4-ticket-08/owner-nav 的绝对路径。
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp "$logs/owner-t8-standalone" tests/architecture_explorer/test_v4_checkpoint_release_content.py

# 回到隔离组合工作树，保持 $logs 指向 T8 的验证目录。
& $python -c "import runpy; runpy.run_path(r'$logs/baseline_reported.py', run_name='__main__')"
& $python -c "import runpy; runpy.run_path(r'$logs/perspective_probe.py', run_name='__main__')"
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/owner-t8-regression-matched tests/architecture_explorer/test_t4_navigation.py tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_recursive_views.py tests/architecture_explorer/test_v4_ticket_09_quality.py::test_build_exposes_quality_responsibilities_and_candidate_disposition tests/architecture_explorer/test_v4_ticket_09_quality.py::test_checkpoint_recovery_uses_canonical_qa_facts_as_policy_inputs
```

两轮 Red 均为 3 passed、2 failed。配置修复后组合为 **5 passed**（19.07s），T8 独立构建为 **5 passed**（17.33s）。覆盖全部 7 个 T8 新 View 的有效下钻、中心和叶节点详情、重复 Appearance、Back/Home、面包屑、关系详情及 Source Trail。T9 显式 Perspective 使用真实 QA View，1440、960、500px 三档均通过普通点击显示详情、显式切换及显式返回。

首轮额外共享回归为 51 passed、1 failed。新版 T7 的四层递归 DOM 脚本读取了早期 fixture，该 fixture 缺少对应的 Perspective 声明。已在隔离目录对齐 T7 的成套 Python fixture 和 DOM 脚本，重跑得到 **52 passed**（115.39s），涵盖共享状态机、显式 Perspective、合法协作环与非法分解环、递归浏览和真实组合发现。T8 原有共享测试文件未替换。

本轮证据保存在 `output/architecture-explorer/v4-ticket-08/owner-nav/`，包括 `content-red.json`、`content-green.json`（最终清单 SHA-256 `2e517e965a8f269f2c0b4e1ea9554e8436607feab6b8803ad9fd964fceefc795`）、`web-red.json`、`web-t7.json`、`t7-tests.json` 与 `projection-diff.log`。此前 714 项完整验证属于初次交付。此次使用受影响范围复测。

## 内容与导航

| 维度 | B0（T1 审计） | Bstart（本地正式构建） | Bstart + T8 |
| --- | ---: | ---: | ---: |
| 正式顶层模块 | 7 | 7 | 7 |
| Candidate Node | 27 | 27 | 40 |
| Candidate Collaboration | 12 | 12 | 22 |
| Candidate Decomposition | 9 | 9 | 17 |
| 正式 Relationship 及其 Evidence | 1 / 13 | 1 / 13 | 1 / 13 |
| View / Appearance | 12 / 39 | 12 / 39 | 19 / 64 |
| 入口覆盖声明 | 4 | 4 | 9 |
| 最大最短导航深度（root 为 0） | 2 | 2 | 3 |
| Perspective | 3 | 3 | 3 |

本票新增对象引用 33 条去重后的 Definition Evidence。13 个新 Node 与 18 条新关系均投影为 Pending，批准范围为空。以正式构建输出比对，正式模块及其职责、正式关系、原有关系投影、历史 Node Review 投影和 v1 Navigation 内容一致。7 个 View 和 25 个 Appearance 使用既有权威对象身份。以显式 open-view 路径做广度优先搜索，19 个 View 均可达。

| View | 该 View 回答的问题 |
| --- | --- |
| Checkpoint 封存职责 | 如何准备、校验代次并形成可发布的清单投影？ |
| 清单发布与恢复 | 已有投影如何通过根清单或后继版本登记，恢复重入会做什么？ |
| Checkpoint 证据工件 | Worker 保存哪些工件，最终 QA 可以交给谁？ |
| 从封存工件到批次发布 | 完成清单、最终 QA 与门禁分别向哪一侧提供数据？ |
| 发布成员资格与后继选择 | 哪些证据支持成员资格，集合如何限制后继工单和 TOC 阶段？ |
| 候选 DOCX 的生成入口 | 哪些显式入口写回文件，什么输入可以送到独立渲染诊断？ |
| 候选 DOCX 的包保真与回检 | 包负载、重抽取和结构锚点如何各自验证？ |

- 从「生产证据与 Checkpoint」进入「Checkpoint 封存职责」，经「Checkpoint 封存投影」进入「清单发布与恢复」，再经「批次清单发布与恢复」进入「从封存工件到批次发布」。
- 从「生产证据与 Checkpoint」进入「Checkpoint 证据工件」，经「Checkpoint 最终 QA 证据」进入跨模块 View，再进入「发布成员资格与后继选择」。可打开「最终 QA 工件提供发布成员依据」的关系详情，并从 Definition Evidence 进入 Source Trail。
- 从「候选文档与发布验证」进入「候选 DOCX 的生成入口」，经「带标记的候选 DOCX 写回」前往「候选 DOCX 的包保真与回检」。也可以从模块直接进入包验证 View。
- Decomposition 箭头从范围指向其所包含的候选职责，Collaboration 箭头沿列出的工件、参数或证据交接方向。跨 View 往返属于导航。发布门禁在相关视图作为协作方呈现，锚点回检与包负载校验作为 Harness 的并列检查呈现。

## 源码核对与证据边界

- **Worker 接入及封存。** Worker 主入口通过同名代理调用生产 Checkpoint。CURRENT 完成路径使用最终化输入指纹选择 Prepared Seal；必需工件齐全才尝试从已有代次形成投影。缺件时走生成路径，完整但无效的代次会报错。Worker 写语义审核、最终 QA 和 Checkpoint Evidence，代次路径向后继清单发布，兼容分支可追加至根清单。确定性通过计数可以包含暂缓处理的债务，批准名单另有筛选。[Worker 入口](../../src/booktrans/production_worker/worker.py#L798) · [Prepared Seal 选择和预检](../../src/booktrans/production_worker/production_checkpoint.py#L3473) · [最终化写入及发布](../../src/booktrans/production_worker/production_checkpoint.py#L8218)
- **Manifest 与恢复。** 封存投影读取不可变工件并验证顺序、计数、选择和绑定，清单用于定位工件和登记进度。根索引以临时文件原子替换，后继文件绑定前驱。恢复提交可校验复用相同条目。Supervisor 的旧根目录恢复入口要求根位置 Evidence 和最终 QA；Worker 内的 Prepared Seal 复用另有前置条件。这些源码可证实校验及发布顺序，实际磁盘原子性和事故恢复结果仍未知。[清单实现](../../src/booktrans/harness/batch_manifest.py#L16) · [恢复提交](../../src/booktrans/harness/batch_manifest.py#L163) · [Supervisor 恢复入口](../../scripts/production/production_campaign_supervisor.ps1#L592)
- **批次边界及 Members。** Supervisor 仅对 `BATCH_BOUNDARY_READY` 进入 release boundary。脚本读取完成批次、核验 Checkpoint 序列，审计封存输入并处理当前债务；APPLY 通过后才调用零债务、无 Provider 的 FINALIZE_ONLY 收口，再调用 terminal closure。终结收口解析清单继承链及工件绑定。通用成员派生递归读取 `translation_final_qa_report.json`，核验通过记录、批准 UID（存在时）及延后债务，另由已绑定人工终态和历史、通用门禁路径补充。该文件扫描与终结收口的清单绑定检查是不同接入点。没有读取运行产物来认定某次门禁或收口已执行。[Supervisor 批次边界](../../scripts/production/production_campaign_supervisor.ps1#L617) · [债务和门禁](../../scripts/production/reconcile_release_boundary.py#L397) · [终结收口](../../scripts/production/reconcile_terminal_batch_closure.py#L43) · [成员派生](../../src/booktrans/harness/release_membership.py#L224)
- **生产阶段。** 推进脚本默认按源顺序选择剩余通常阶段工单并排除 `toc_late_stage`。显式阶段参数走 TOC 选择。Close-only 可在没有后继工单时登记 hardening pending。实际运行阶段及所选集合未知。本票没有执行、生成或替换选择器。[批次推进](../../scripts/advance_full_book_batch013.py#L212) · [TOC 选择](../../src/booktrans/harness/toc_late_stage.py#L33)
- **DOCX 和视觉 QA。** TaggedSyntheticRoundTrip 与 CalibrationImporter 是可定位的独立入口。校准导入合同失败或没有可投影记录时提前结束。两个 Harness 都可处理写回、包保真和重抽取/锚点检查。未找到 Supervisor 或 Worker 对这些候选整书产物的常驻调用。渲染诊断单独接收已经渲染的 PDF、受限页面观察、DOCX 和锚点，只输出差异事实。整书候选、视觉 QA 或交付需要对应授权和运行证据，本票没有执行这些生产操作。[校准导入](../../src/booktrans/cli.py#L552) · [Harness](../../src/booktrans/harness/tagged_roundtrip.py#L205) · [包校验](../../src/booktrans/harness/package_preservation.py#L14) · [渲染诊断](../../scripts/diagnostics/run_rendered_differential_visual_gate.py#L33) · [生产约束](../../AGENTS.md)

Explorer 内使用 Definition Evidence 精确绑定源码定义。不得由这些引用推断某次执行、调用频率或产物通过状态。Supervisor release 边界的具体分支在上述 PowerShell 行位复核，现有自动索引未覆盖该函数。Worker 最终报告的写入证据由完整的 `run_production_checkpoint` 定义支持，审查时可直接定位到第 8218–8379 行。这个定义较长，现有 Source Trail 保留完整定义。

## 共享入口

五份 v2 Fragment 放入既有 `recursive-candidates/` 命名空间，通过正式 CLI 的 `--candidate-fragment` 输入，供内容对比的既有发现规则复用。单独的 `views/v4-ticket-08.v1.json` 由 T2 的递归发现机制自动装配。五个入口覆盖只附加到现有 Appearance，本票没有改原注册文件、Projection、renderer 或 GitHub 审核资产。

`test_recursive_views.py` 里，v3 专项测试显式选用四份历史 Fragment。全量发现测试核对实际 View 文件集合并保留原 v3 入口断言。该改动与用于联合测试的 T9 共享测试文件字节一致。T7 的当前共享文件另有精确摘要及 View 全集断言，后续合并时可保留这些断言，并沿用 T8/T9 对历史 Chrome 直接构建明确指定四份 v3 Fragment 的做法。

`test_v3_ticket_06_integration.py` 同样固定四份历史 Fragment。完整测试曾实际将新 Fragment 纳入历史 inventory，报告 40 个 Candidate Node 对 27 个的失败；修复固定输入并保持所有原断言。该文件与 T7、T9 的当前修复字节一致。

## 完整改动清单

```powershell
git status --short
git diff --stat 72864310175049a34e0c8ed789e096ead9c73a65
git diff --no-ext-diff 72864310175049a34e0c8ed789e096ead9c73a65
git ls-files --others --exclude-standard
```

Tracked diff 包含 `tests/architecture_explorer/test_recursive_views.py` 和 `tests/architecture_explorer/test_v3_ticket_06_integration.py`。以上命令涵盖全部 tracked 变化，原始清点保存在 `output/architecture-explorer/v4-ticket-08/review-*.txt` 及 `review-tracked.diff`。独立审查需要在工作树直接读取以下全部未跟踪文件。

| 路径 | 全文审查内容 |
| --- | --- |
| `architecture-explorer/recursive-candidates/candidate-document-projection.v2.json` | 独立 Harness、写回和诊断器 |
| `architecture-explorer/recursive-candidates/checkpoint-evidence-persistence.v2.json` | Worker 工件及最终 QA 跨模块交接 |
| `architecture-explorer/recursive-candidates/checkpoint-manifest-seal.v2.json` | Prepared Seal、投影、发布与恢复 |
| `architecture-explorer/recursive-candidates/package-preservation-verifier.v2.json` | 包负载、独立锚点检查及职责关系 |
| `architecture-explorer/recursive-candidates/release-membership-derivation.v2.json` | 准入规则、批次门禁、TOC 及跨模块清单交接 |
| `architecture-explorer/views/v4-ticket-08.v1.json` | 七个 View、Appearance 与跨文件入口 |
| `tests/architecture_explorer/test_v4_checkpoint_release_content.py` | 正式生成与 Chrome 回归 |
| `tests/architecture_explorer/v4-ticket-08-dom.js` | 浏览器导航、详情、Evidence、焦点和关系文本 |
| `docs/architecture-explorer/v4-ticket-08-handoff.md` | 本交接，含已验证结果及限制 |

## 复现与验证

在各自准备过 Junction 的工作树根目录运行。准备脚本验证源解释器、Junction 目标、`sys.prefix`、`pip check`、项目工作树的 `booktrans` import 和 Git 状态。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$env:PYTHONUTF8 = '1'
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
$argsBuild = @(
  'build', '--repository', '.',
  '--snapshot', 'output/architecture-explorer/t1-delivery-4be1c19/snapshot.json',
  '--mapping', 'architecture-explorer/modules.json',
  '--candidate', 'architecture-explorer/review-inputs/supervisor-worker-857a4b8.json',
  '--review-mapping', 'architecture-explorer/reviews/supervisor-worker.v1.json',
  '--review-source', 'architecture-explorer/reviews/supervisor-worker.source.v1.json',
  '--review-state', 'output/architecture-explorer/t5-lite/review-state.v1.json',
  '--navigation', 'architecture-explorer/navigation.v1.json',
  '--legacy-node-review-v1',
  '--web-assets', 'architecture-explorer/web',
  '--output-directory', 'output/architecture-explorer/v4-ticket-08/site'
)
foreach ($fragment in (Get-ChildItem -LiteralPath architecture-explorer/candidates,architecture-explorer/recursive-candidates -Filter *.json -File | Sort-Object FullName)) {
  $argsBuild += @('--candidate-fragment', $fragment.FullName)
}
& $python scripts/architecture_explorer/generate_reviewed_relationship.py @argsBuild

$env:ARCHITECTURE_EXPLORER_BROWSER = 'chrome'
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp output/architecture-explorer/v4-ticket-08/focused-final tests/architecture_explorer/test_v4_checkpoint_release_content.py
```

- 正式 CLI 返回 0，历史正式关系输出 `current-reviewed / exact-match`。三个 Red 依次因封存入口缺失、证据跨模块 View 缺失及包验证 View 缺失失败。最终聚焦回归 5 passed（26.59s，Chrome 实际视口 1440、500）。
- 使用现有 CDP 检查器的本地派生脚本检查七个新 View 的 480、960、1280、1440px 布局，逐条核验箭头端点和状态、标签可见性及遮挡。初轮 480px 封存图出现共享走廊和一处隐藏标签。拆分准备/发布 View 后矩阵返回 0。已查看各 View 的桌面与窄屏关系列表截图，确认关键文案、状态和关系仍可浏览。截图与结构证据位于 `output/architecture-explorer/v4-ticket-08/visual-final/`。这是 Explorer 页面验收证据。
- `& $python -m compileall -q src/booktrans/architecture_explorer tests/architecture_explorer`、`git diff --check` 与 `node --check tests/architecture_explorer/v4-ticket-08-dom.js` 返回 0。
- 完整 `verify.ps1` 的两次早期运行用于定位故障。 `verify.log` 对较早测试输入记录 710 passed、2 failed、2 skipped，失败点为 390px 测试设定及 v3 固定 inventory 的 Fragment 输入。 `verify-final.log` 对视口修订后的测试输入记录 711 passed、1 failed、2 skipped，仅剩固定 inventory 输入错误。两处都已在最终测试输入修复。
- 最终工作树运行 `& .\scripts\architecture_explorer\verify.ps1 *> output/architecture-explorer/v4-ticket-08/verify-current.log`，退出 0。官方验证的环境、静态、Explorer 全部为 PASS。JUnit 共 714 项，712 passed、2 skipped、0 failures、0 errors，pytest 约 29 分钟。71 项必需 Chrome 检查均通过，浏览器为 Chrome 158.0.8127.38。两项跳过是旧 Bstart 的可选 T5/T6 producer-consumer 参数用例，要求显式设置 #80 consumer 文件路径。结构化结果保存在同目录 `verify-current.junit.xml`。

```powershell
$root = (Resolve-Path 'output/architecture-explorer/v4-ticket-08').Path
$page = ([uri](Join-Path $root 'site/index.html')).AbsoluteUri
& node (Join-Path $root 'visual-final/capture.cjs') 'C:\Program Files\Google\Chrome\Application\chrome.exe' $page (Join-Path $root 'visual-final/profile') (Join-Path $root 'visual-final/view-layout.json') all-views-matrix
```

### 真实联合构建

隔离工作树 `D:\translate\worktrees\architecture-explorer-v4-ticket-08-combined` 的 detached HEAD 为 Bstart。T7、T8、T9 的输入快照均来自同一 HEAD 上各自未提交的工作树。复制前后逐文件比对 SHA-256，首次最终快照有 20 个内容与测试来源文件，之后追加三票共有的 v3 固定清单测试修复，内容快照继续沿用原哈希。原工作树未被写入。完整路径及哈希位于本票工作树的 `output/architecture-explorer/v4-ticket-08/combined-final-inputs.json`。

在隔离工作树运行上面的正式 CLI 命令，得到同一页上的 21 份 Fragment、4 份自动发现的 View 文档、63 个投影 Node、66 条关系和 31 个 View。四份 View 文档的路径与摘要可从联合产物 `output/architecture-explorer/v4-ticket-08/site/explorer-data.json` 的 `generation_inputs.view_definitions` 复核。该产物 SHA-256 为 `ddc66bc5b280636d91626d7fbca64d318f5d2bf0d078322dcba04700a6bd290e`。

```powershell
# 在隔离工作树，沿用上面的项目解释器环境并创建临时目录父路径。
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$env:ARCHITECTURE_EXPLORER_BROWSER = 'chrome'
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/t8-joint-final tests/architecture_explorer/test_recursive_views.py::test_cli_build_discovers_authoritative_view_documents_without_an_option tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_v4_checkpoint_release_content.py tests/architecture_explorer/test_v4_ticket_09_quality.py
```

结果为 14 passed（95.88s），涵盖真实组合输入的发现、跨文件引用、投影、统一 renderer、各批导航与 Chrome DOM。初次联合回归在准备临时目录时失败。重试发现 headless Chrome 把请求的 390px 提升到实际 500px，当次结果 41 passed、1 failed。最终使用断言实际视口的测试，并另外通过 CDP 覆盖 480px。这是当前三份快照的联合验证，合并后仍需在集成 Head 回归。

```powershell
# 固定历史 inventory 的回归。在 T8 工作树运行，3 passed（62.89s）。
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp output/architecture-explorer/v4-ticket-08/historical-v3 tests/architecture_explorer/test_v3_ticket_06_integration.py

# 在 Bstart 隔离组合工作树运行，4 passed（78.89s）。
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/t8-joint-historical tests/architecture_explorer/test_v3_ticket_06_integration.py tests/architecture_explorer/test_recursive_views.py::test_cli_build_discovers_authoritative_view_documents_without_an_option
```

随后集成分支合入 #80。新隔离工作树 `D:\translate\worktrees\architecture-explorer-v4-ticket-08-combined-latest` 的 detached HEAD 是 `ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`，它继承 Bstart，并修改共享 `review_cli.py`。原 21 份输入按同一清单逐字节核验后装配。正式 CLI 构建返回 0，新产物 SHA-256 为 `5908e4615ba278bf2c8cda1b9d924c417b90703867e766eac03aa46932f70d6d`。它与 Bstart 联合构建的 63 个 Node、66 条关系、31 个 View、正式关系及 View 文件摘要逐记录一致。

```powershell
# 在最新隔离工作树，先执行同一正式构建，并使用项目解释器。
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$env:ARCHITECTURE_EXPLORER_BROWSER = 'chrome'
& $python -m pytest -q --tb=short -p no:cacheprovider --basetemp .test-tmp/t8-shared-head tests/architecture_explorer/test_recursive_views.py::test_cli_build_discovers_authoritative_view_documents_without_an_option tests/architecture_explorer/test_v3_ticket_06_integration.py tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_v4_checkpoint_release_content.py tests/architecture_explorer/test_v4_ticket_09_quality.py tests/architecture_explorer/test_v4_batch_import.py
```

结果为 42 passed（137.17s），覆盖三个批次的联合站点与 Chrome 导航、历史 inventory、发现和 #80 的正式消费回归。此处只有隔离验证，本票实际待审工作树和 Bstart 未移动。

## 独立审查与剩余验证

- Standards 轴由独立审查者使用 AGENTS 与上述完整 tracked / untracked 文件得出结论，当前待审查。
- Spec 轴由独立审查者使用 #74、#82、T1/T2 材料及 Owner 本轮范围得出结论，当前待审查。
- 本轮的生产分析基于固定源码。实际运行接入、现场文件状态、生产故障恢复、DOCX 视觉 QA、整书候选及发布没有运行验证。Owner 的逐 View 语义与视觉验收仍待完成。合并后需要在最新集成 Head 跑受影响回归。
- Explorer 以外的翻译测试、Firefox 和 Edge 未运行。
