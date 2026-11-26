# Architecture Explorer v4 Ticket 07 本地审查交接

## PR #93 正式 CI 增量

- 本轮复审基线为 PR Head `a5f794636ad40d084af09a071492afb24f5a8e5d`。Windows / Chrome 正式 CI 报告 718 passed、18 failed、2 skipped。18 项失败集中在两个节点审核浏览器测试的 1440、1280、960 三档矩阵。[CI Run 45817738052 / Job 139901720845](https://github.com/nnewlander/translate/actions/runs/45817738052/job/139901720845)
- Red 在原 Head 上选取 1440 宽度的 Pending、Approved、Rejected 三例，3 failed、58 deselected（21.51s）。三例均已通过生成页面首次绘制的中文状态检查，随后在旧探针改写 `review_detail.projection` 并点击卡片时报告 `missing Chinese review label`。同一 View 内的普通激活打开详情并保留卡片 DOM，用于关闭详情时恢复焦点。旧探针依赖该点击重绘整张卡片，生成页面的审核状态显示无此问题。Red 日志为 `output/t7-ci-red.log`。
- 代码差异仅移除 `tests/architecture_explorer/t8_node_review_dom.js` 中的投影改写和点击重绘循环。两个既有参数化测试各自通过 `write_site` 在 Chrome 启动前生成相应状态，覆盖 Pending、当前批准、当前拒绝、历史批准、历史拒绝和 Unknown。保留中文标签、卡片布局、Evidence、审核可信边界与详情断言，也保留原有通用详情兼容探针。该探针仍为另一节点临时注入测试数据，仅检查通用详情，不要求重绘卡片。产品 renderer、导航 reducer、审核逻辑和交互规范均无本轮差异。
- Green 复跑正式 CI 失败的全部 18 例，18 passed、70 deselected（76.33s），日志为 `output/t7-ci-green.log`。导航聚焦回归 16 passed、34 deselected（57.57s），涵盖普通节点激活、显式跨 View 和 Perspective、Back/Home、Source Trail、焦点恢复及合法协作环，日志为 `output/t7-ci-nav.log`。两次均使用本 Worktree 的项目解释器与真实 Chrome。
- 本轮仅交接以上增量。历史 Owner 验收和 Standards / Spec 双 PASS 见下文。增量尚待独立复审，正式 CI 未重新运行。

  ```powershell
  & .\scripts\architecture_explorer\prepare_worktree.ps1
  $python = (Resolve-Path '.venv\Scripts\python.exe').Path
  $env:ARCHITECTURE_EXPLORER_BROWSER = 'chrome'
  $env:PYTHONUTF8 = '1'
  & $python -m pytest -q tests/architecture_explorer/test_t8_node_review.py tests/architecture_explorer/test_t8_node_review_continuity.py -k 'test_real_browser_reuses_candidate_card_and_context_evidence_detail or test_real_browser_renders_historical_and_unknown_node_review_states' --tb=short --basetemp=output/t7-ci-green
  & $python -m pytest -q tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_recursive_views.py tests/architecture_explorer/test_t4_navigation.py -k 'ticket_07 or controlled_data_recurses or collaboration_cycles or decomposition_cycle or explicit_perspective_return_transition or source_trail or interaction_state_suite or browser_dom_order' --tb=short --basetemp=output/t7-ci-nav
  ```

## 固定点与范围

- Base、HEAD、Bstart 均为 `72864310175049a34e0c8ed789e096ead9c73a65`，2026-11-26 开工时核实为 `experiment/matt-skills` 的集成 Head，包含 T1 `c43e62a` 与 T2 `dad00a7`。
- 固定内容对比 B0 为 `09aaf4a033123c7eca8d280fa8982e5b0e711b86`。用既有 `project_authoritative_commit` 从 B0 和 Bstart 两个不可变 Git 对象各重算一次，再与 T7 dirty worktree 的实际离线投影比较，记录见 `output/t7-baseline-projection.log`。逐对象复核了旧 Node、旧关系、根 View 和两处新增入口以外的既有 View。
- 分支 `codex/architecture-explorer-v4-ticket-07`。Worktree `D:\translate\worktrees\architecture-explorer-v4-ticket-07`。
- 内容范围为 [Ticket #81](https://github.com/nnewlander/translate/issues/81) 与 [proposed Spec #74](https://github.com/nnewlander/translate/issues/74)。依赖为 [#75](https://github.com/nnewlander/translate/issues/75) 与 [#76](https://github.com/nnewlander/translate/issues/76)。现行合同为 `architecture-explorer/v3`。

## 全局普通节点激活规则增量

- 在修改前与「审查v4 T7」协调。Reviewer 确认原修复已覆盖普通激活的路径守卫，并指出 Spec P2 涉及被一并隐藏的显式入口。复审只覆盖本节新增差异。参照 `output/t7-global-review-delta.patch` 和 `output/t7-global-reviewed/` 中上一轮已审文件。
- 长期规则记录在 [T4 交互不变量](../architecture-explorer-t4.md#交互不变量)，根 `AGENTS.md` 只指向同一权威段落并要求通过共享浏览器验证。现行 Spec 和历史验收合同的文件未改。
- 普通激活仍按当前 Appearance 拦截当前 View 或祖先 View。详情复用被拦截的已声明跨 View、Perspective 和 Source Trail 操作。显式进入路径外 View 时入栈，显式返回祖先时复用已有路径位置并恢复对应 Appearance 焦点。Self 目标打开详情即可。Back、Home 及关系语义继续由同一状态机制处理。
- Chrome 首次使用新增断言时复现已声明的祖先 Perspective 入口丢失。最终聚焦 **14 passed、36 deselected**（46.06s），覆盖当前和祖先目标、重复 Appearance、T7 的「回看冻结范围」、显式 Perspective 返回、显式和普通 Source Trail、合法协作环和非法分解环。记录见 `output/t7-global-red.log` 和 `output/t7-global-tests-final.log`。本轮只执行受影响回归。
- 真实组合使用集成 Head `ee6b8241b3075e48fe9dc5e6254b7086f23f13ae` 与现有隔离 checkout。输入清单 `output/t7-global-combined-inputs.json` SHA-256 为 `9d5f98155a2e6eb2de56bc495875c3c2d45d832393f589e9dffd3dd2b34710c3`，共享 Web 清单 `output/t7-global-shared-assets.json` SHA-256 为 `50fb8b1a31cbd1ec843e0321d3feb3ed8575dd40742efbead0b7cbf3c48e6340`。正式构建保持 `current-reviewed` 和 `exact-match`。真实 Chrome 在 1440、960、500 各遍历 19 个新增 View，额外分别从清单发布和 QA 证据入口进入同一 T8 View，确认两个重复 Appearance 在目标为祖先时普通激活开详情、显式返回恢复焦点，在目标处于路径外时普通激活仍下钻。三档均验证真实 T9 Perspective 的显式返回。浏览记录见 `output/t7-global-browser.log`。
- T8 随后把组合 View 中「批次清单发布与恢复」和「Checkpoint 最终 QA 证据」都改为仅显示详情的 Appearance。用当前 13 份 JSON 和同一份共享 Web 复建后，三档仍分别通过 19 个 View。两个节点从各自来源仍能进入组合 View，在组合 View 中打开对象详情，Back 返回各自的来源并恢复焦点。T9 显式祖先视角返回仍通过。新清单 `output/t7-global-current-combined-inputs.json` SHA-256 为 `a1bb290c4edb02b90cb177ed14f34e111894892503b2701b281e4c235fb85b66`，新日志为 `output/t7-global-current-browser.log`。以下命令将 `t7-global-combine.ps1` 和 `t7-global-browser.py` 分别换为 `t7-global-current-combine.ps1` 和 `t7-global-current-browser.py` 即可复现当前组合。
- 首次增量复审确认原导航 P2 已解决，并复现显式 Source Trail 内部返回时焦点落到页面。按 Reviewer 确认的方案，现有 `returnContext` 保存显式发起按钮的选择器。返回详情恢复按钮焦点，关闭整个对话框恢复节点卡片焦点。T12 Chrome Red 在 `output/t7-global-focus-red.log`，焦点修复的两文件差异在 `output/t7-global-focus-delta.patch`。将下列 pytest 的 `--basetemp` 改为 `output/t7-global-focus-final` 后，最终 **14 passed、36 deselected**（43.36s），记录在 `output/t7-global-focus-final.log`。以同一内容输入和修复后的 Web 文件重建，三档组合再次通过。最终共享 Web 清单 `output/t7-global-current-shared-assets.json` SHA-256 为 `0ab81491ff642284b95311281199203072652ab11c55d1ba799d84b79d1edfae`。
- 「审查v4 T7」对最终两文件焦点增量复审为 **Standards PASS / Spec PASS**，关闭剩余 P2。独立聚焦复测 10 项通过，确认详情内返回恢复按钮、关闭详情恢复卡片，并核对了当前组合的 13 份 JSON、2 份 Web 文件和三档浏览日志。

  ```powershell
  & .\output\t7-global-combine.ps1
  & .\scripts\architecture_explorer\prepare_worktree.ps1
  $python=(Resolve-Path '.venv\Scripts\python.exe').Path
  $env:ARCHITECTURE_EXPLORER_BROWSER='chrome'
  $env:PYTHONUTF8='1'
  & $python -m pytest -q tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_recursive_views.py tests/architecture_explorer/test_t4_navigation.py -k 'ticket_07 or controlled_data_recurses or collaboration_cycles or decomposition_cycle or source_trail or state_interactions or browser_dom_order' --basetemp=output/t7-global-tests-final
  & $python -c "import runpy; runpy.run_path('output/t7-global-browser.py', run_name='__main__')"
  ```

## 前轮 Owner 验收修复

- 「可翻译视图构造」只调整四个 Appearance 的顺序，继续用既有关系路由。浏览器 Red 复现了桌面交叉，第一种试排在窄屏仍有一段共线，再按实际路由与箭头落点选定最终排布。窄屏有跨行边沿两侧通道绕行，长标签仍可能折行。T7 Node、Relationship、Evidence、Appearance 与跳转声明均与初次交付投影逐项对照一致，View 中的呈现顺序是本轮的内容文件增量。
- D050 回跳来自共享 reducer 把普通激活当成返回，截断了导航路径。renderer 与 reducer 现在按当前 Appearance 判断。中心节点、无跳转目标的叶节点，以及指向当前或祖先 View 的普通激活打开详情。有效下钻入栈，Back 与 Home 走原有路径，面包屑反映实际导航。架构关系以及 View Definition 的方向和类型继续作为独立数据。
- 从普通节点详情进入 Source Trail 的焦点 Red 已修复。打开详情时不重建卡片，关闭后原 Appearance 仍可聚焦。真实 Chrome 覆盖普通详情关闭、鼠标进入 Source Trail、Space 打开同一 Node 的另一个 Appearance、Back 和 Home。
- 真实组合发现 T9 的「候选结果处置」原先只有 QA 中心卡片上的 Perspective 入口。现在普通激活打开详情，详情内的显式按钮按该 Appearance 的声明切换视角，遇到路径中的目标时由 Back 返回。T7 持有 `app.js` 和 `t4-state.js` 的共享修复，T8/T9 可直接复用，两个并行 worktree 均未写入。
- 最终组合以 `ee6b8241b3075e48fe9dc5e6254b7086f23f13ae` 为隔离 checkout 的 HEAD，覆加 T7 的两份共享 Web 文件和三票最新可用的 13 份 JSON。正式构建退出 0，Supervisor → Worker 为 `current-reviewed`、`exact-match`。输入清单 `output/t7-owner-combined-inputs.json` 的 SHA-256 为 `9d5f98155a2e6eb2de56bc495875c3c2d45d832393f589e9dffd3dd2b34710c3`，Web 哈希见 `output/t7-owner-shared-assets.json`。
- Chrome 在 1440、960、500 像素每档遍历 19 个新增 View，分别验证 37 次有效下钻、19 次中心详情、7 次当前路径跳转拦截、21 次叶节点详情、46 次节点 Source Trail、19 次关系 Source Trail、1 次显式 Perspective 切换及 17 个重复出现的 Node。T7 七个 View 在三档宽度均渲染全部关系线和标签，检测到 0 处相交、0 处标签重叠或覆盖卡片。日志为 `output/t7-owner-browser.log`。
- `output/t7-browser/construction-1440.png` 和 `construction-500.png` 保存修改前截图，`output/t7-owner-browser/` 保存修复后七个 View 的桌面与窄屏截图。已目检构造图的修改前后截图。其余六个 View 的两档新截图与上轮已目检的截图逐个 SHA-256 相同。桌面构造图中两条边在回找节点上缘相接，分别用标签与目标箭头辨认。共享图例仍把两类候选边放在同一类别中，语义可从边标签和关系详情核对。Owner 重新验收待完成。
- 本轮五个受影响文件 **62 passed**（180.08s）。最终排布及共线断言另跑 T7 文件 **4 passed**（21.48s）。日志分别为 `output/t7-owner-final-regression.log`、`output/t7-owner-layout-final.log`。Node reducer 断言、JavaScript 语法与 Git 空白检查通过。下文 713 项全量和 36 项复测属于初次交付，本轮按受影响范围复测。

  ```powershell
  & .\output\t7-owner-combine.ps1
  $env:ARCHITECTURE_EXPLORER_BROWSER='chrome'
  & $python -c "import runpy; runpy.run_path('output/t7-owner-browser.py', run_name='__main__')"
  & $python -m pytest -q tests/architecture_explorer/test_v4_ticket_07.py tests/architecture_explorer/test_t4_navigation.py tests/architecture_explorer/test_recursive_views.py tests/architecture_explorer/test_v3_ticket_06_integration.py tests/architecture_explorer/test_ticket_63_semantic_review_view.py --basetemp=D:/translate/tmp/t7-owner-final
  & $python -m pytest -q tests/architecture_explorer/test_v4_ticket_07.py --basetemp=output/t7-owner-layout-final
  ```

## 内容与证据

| 投影对象 | B0 | T7 独立构建 | 增量 |
| --- | ---: | ---: | ---: |
| 顶层模块 | 7 | 7 | 0 |
| Candidate Node | 27 | 36 | 9 |
| Candidate Collaboration | 12 | 20 | 8 |
| Candidate Decomposition | 9 | 17 | 8 |
| 正式关系 | 1 | 1 | 0 |
| View | 12 | 19 | 7 |
| Appearance | 39 | 60 | 21 |
| 从根可达 View 的最大最短跳数 | 2 | 3 | 1 |

T7 的 25 个去重 Evidence ID 均解析为当前扫描的 Python Definition Source Fact，文件和符号锚点来自既有扫描器。下表列出支持主要主张的定义。运行时数据流是对这些定义体的架构解释，跨 View 跳转使用显式 `cross-node` 类型。

| View 问题 | 静态职责与交接 | 代表性 Evidence |
| --- | --- | --- |
| DOCX 如何成为带定位的 IR？ | 只读提取识别 Word XML story，以 XML 部件和段落／文本序号建立 SourceLocator，形成带身份的 BlockIR 与 SpanIR | `DocumentExtractor.extract`、`_parse_word_parts`、`_story_part_names`、`_blocks_for_part`、`_spans_for_paragraph` |
| IR 地址如何成为翻译视图？ | 通过 SourceLocator 回找原 XML，以 IR 身份投影片段、格式和结构锚点、可编辑岛与邻文 | `TranslationViewBuilder.build`、`translation_view._resolve`、`RootLocatorIndex`、`resolve_locator_in_root` |
| 翻译视图如何进入范围与请求？ | 范围脚本以视图的 `display_id` 集合匹配 IR 块，再排序和投影逻辑工单。默认生产 loader 用冻结 ID 选出视图与块，再复制请求字段 | `write_full_book_translation_scope.main`、`select_final_logical_work_orders`、`frozen_batch_checkpoint_inputs`、`load_checkpoint_requests` |
| 物理内容如何划入冻结范围？ | 已确认的复合组替代物理成员。Checkpoint 分支消费已经冻结的逻辑顺序，通常每片 25 项 | `select_final_logical_work_orders`、`frozen_checkpoint_order`、`validate_frozen_checkpoint_members` |
| 冻结顺序如何约束请求？ | 冻结切片给出逐项查找顺序，缺少对应 TranslationView 或 BlockIR 会阻断装配 | `frozen_checkpoint_order`、`load_checkpoint_requests` |
| 请求字段与脚注依据来自哪里？ | 区分字段构造与逐工单精确匹配，从匹配节点进入上游 preflight 协作 | `load_checkpoint_requests`、`exact_evidence_for_work_order`、`build_calibration_request` |
| D050 证据如何绑定到这张请求？ | preflight 在限定条件下验证并保存，重载时按 `(logical_work_order_id, unit_uid)` 取证，请求仍检查标记与源片段 | `load_exact_verified_evidence_registry`、`exact_evidence_for_work_order`、`verified_numeric_bindings_for_request` |

代表性导航从「文档理解与定位」进入「DOCX 内容提取」，经「构造带定位的文档 IR」进入翻译视图，再从「投影片段与格式锚点」进入跨模块协作 View。通过「形成逻辑工作单元」可进入冻结切片、请求上下文和 D050 View。「工作范围与请求构造」也直接提供逻辑选择与请求上下文两个入口。

这些定义支持静态数据交接、条件和错误边界。实际书稿的覆盖与 locator 成功率、活动批次使用了哪份 reconciled selector、PDF 页关联和唯一性、运行时是否重建视图以及模型调用结果仍属 Unknown。Explorer 投影保持候选对象逐对象 Pending；本票的 Node 和 Relationship 未安装审核决定。正式 Supervisor → Worker 关系的构建状态为 `current-reviewed` 与 `exact-match`。

## 初次交付的公共接口与组合输入

- 四份文件使用既有 Candidate Fragment v2 接口，编号顺序让跨文件引用指向前面已经装配的 Node。新 Node / View 使用 `v4-t07-`，关系沿用 `candidate-{semantic}:v4-t07-`。父 Node 引用使用现有稳定 ID。
- `architecture-explorer/views/v4-ticket-07.v1.json` 通过 T2 正常发现，入口用独立的 `(View ID, Node ID)` 选择 Appearance；同一 Node 在不同 View 复用。Candidate Fragment 由正式 build 的 `--candidate-fragment` 显式传入。两处共享测试文件分别检查当前仓库发现的路径、文件哈希和投影身份，以及固定历史输入的准确清单。公共 Python 实现、CLI、renderer、旧权威 JSON 与审核资产的 Git tracked diff 为空。
- Evidence 使用扫描器生成的稳定 ID。T2 投影将 Definition 解析到 `evidence_index[id].definition.qualified_symbol`，保留 `file_path`、Evidence 定位和进入 Source Trail 所需的类型。所有引用由真实 build 同时校验。
- 组合验证使用同一 Bstart 的 detached checkout `output/t7-combined-7286431`。从同一基线读取 T8、T9 的真实 Candidate 和 View JSON 副本，用统一 CLI 装配。输入文件和 SHA-256 见 `output/t7-combined-inputs.json`，复制与构建命令见 `output/t7-combine.ps1`。浏览器的 View 深入、关系详情、Source Trail 与逐层 Back 探测见 `output/t7-combined-smoke.js`，执行及截图命令见 `output/t7-browser-run.py`。
- Bstart 组合快照共 13 份 JSON，T7 / T8 / T9 分别为 5 / 6 / 2 份，清单 SHA-256 为 `7a6360dc24d98f7dc5d139f5b0ca3e29a62bf336d7424fd7c3d0b9a035e0d473`。两种 Chrome 宽度分别遍历 19 个新 View（T7 / T8 / T9 为 7 / 7 / 5），通过真实 View 自动发现、跨文件引用校验、Projection、统一 renderer、关系详情、Definition Source Trail 与逐层 Back。结果见 `output/t7-browser.log`。该结论绑定已校验哈希的本地输入。
- 交接前 GitHub 集成 Head 前进到 `ee6b8241b3075e48fe9dc5e6254b7086f23f13ae`（合入 #92 / T6）。在 `output/t7-combined-ee6b824` 的独立 checkout，用最新代码并核对了 `review_cli` 的模块导入路径。纳入 T9 更新后的两份 JSON，T7、T8 文件哈希与上轮一致。13 份输入见 `output/t7-combined-latest-inputs.json`，清单 SHA-256 为 `baa8659eea2e1b70d243e582393f46ca1ba994921c2b3282d53bc4a22bd3c0f8`。实际命令为 `& .\output\t7-combine-latest.ps1`（退出 0），及设置 `T07_COMBINED_CHECKOUT=t7-combined-ee6b824` 后运行 `& $python -c "import runpy; runpy.run_path('output/t7-browser-run.py', run_name='__main__')"`。两种 Chrome 宽度各通过 19 个新 View，退出 0。构建及浏览日志为 `output/t7-combine-latest.log`、`output/t7-browser-latest.log`。本票分支仍以 Bstart 为 HEAD。

## 初次交付的验证记录

- 准备脚本返回 `PROJECT_VENV_READY`。Junction 精确指向 `D:\translate\.venv`，解释器为当前 worktree 的 `.venv\Scripts\python.exe`（Python 3.12.14），解析后的 `sys.prefix` 为共享路径，`PYTHONPATH` 与 `booktrans.__file__` 位于当前 worktree 的 `src`。独立 `pip check` 返回 `No broken requirements found.`。
- 首个 Red 的真实 build 成功，但原投影缺少 DOCX Appearance 的 View 跳转。补入提取内容后该断言及 Source Trail 证据断言通过。第二个 Red 缺少翻译视图进入工作范围的真实入口。加入两个可从源码追溯的协作后，真实 Projection v2 生成并通过断言。
- 真实 Chrome 158.0.8127.38 使用 1440 和 500 像素 CSS viewport。Windows Chrome 的窗口宽度下限使最初请求的 480 像素实际为 500，最终验证以实际宽度为准。聚焦浏览器验证覆盖关系详情、未审核范围、候选节点菜单到 Source Trail、关闭后的焦点以及多层 Back/Home。Chrome 在普通工作沙箱内被 Crashpad 阻断，实际 Chrome 验证通过正式审批在沙箱外运行。
- 聚焦命令 `& $python -m pytest -q tests/architecture_explorer/test_v4_ticket_07.py --basetemp=output/t7-chrome-final`，`ARCHITECTURE_EXPLORER_BROWSER=chrome`、`PYTHONUTF8=1`，4 passed（24.88s），退出 0。两组直接 Chrome DOM 和 14 张截图的记录在 `output/t7-browser.log` 和 `output/t7-browser/`。`node --check tests/architecture_explorer/v4_ticket_07_dom.js`、`git diff --check`、`git diff --cached --check` 均退出 0。
- 既有 CLI 发现测试原来只接受 12 个 View 和一份定义。单测 Red 实际生成了 19 个 View。下列命令在 T7 独立站点环境通过 2 项（18.09s），在组合路径改用 `--basetemp=output/t7-combined-discovery` 后同样通过 2 项（21.08s）。隔离路径使用同一 Bstart 的代码及复制后的回归测试，日志为 `output/t7-combined-discovery.log`。

  ```powershell
  & $python -m pytest -q tests/architecture_explorer/test_recursive_views.py -k 'cli_build_discovers_authoritative_view_documents or view_definition_discovery_is' --basetemp=output/t7-legacy-discovery-green
  ```
- 权威全量先运行 `& .\scripts\architecture_explorer\verify.ps1 *> output/t7-full-verify.log`，`TEMP` 与 `TMP` 为 `D:\translate\tmp`。环境和静态检查 PASS，真实 Chrome 参与全量 pytest。原始结果为 708 passed、3 failed、2 skipped（36m40s，退出 1）。三项失败分别是已识别的自动发现旧断言、递归 View 固定清单、v3 Ticket 06 固定清单。后两项输入原先用当前目录 glob，现固定到对应旧 View Definition 的四份历史 Fragment，原有数量、Evidence 及审核边界断言保留。完整原始日志保留。
- 修正后完整重跑三个受影响的测试文件，36 passed（201.46s），退出 0，包含首轮失败的三项以及桌面和窄屏 Chrome。最新 Head 的三票组合同时复测 View 发现命令，2 passed（17.85s），退出 0。对应日志为 `output/t7-affected-regression.log` 和 `output/t7-latest-discovery.log`。修正后未再次运行整个 713 项全量验证。

  ```powershell
  & $python -m pytest -q tests/architecture_explorer/test_recursive_views.py tests/architecture_explorer/test_v3_ticket_06_integration.py tests/architecture_explorer/test_v4_ticket_07.py --basetemp=D:/translate/tmp/t7-final-regression -rs
  ```
- 直接点击叶节点，再从详情打开 Source Trail，关闭后焦点未回到该节点，两个测试宽度均可复现。共享 renderer 的 `cardFor` 在 primary action 保存旧 `article` 后调用 `render()`，而 `renderModules` 用 `grid.replaceChildren()` 替换 DOM，`restoreDialogOpener` 无法聚焦已脱离 DOM 的 opener。最小修复位置为重绘后按 Node ID 解析 opener，或在关闭时用当前 View 内的 Appearance 解析焦点。已存在的节点操作菜单入口和 Back 焦点恢复通过真实浏览器验证。
- 本地视觉检查使用两个宽度的实际 Chrome 截图。将断字的请求卡片标题改为「组装校准请求」，并按 preflight、精确匹配、请求的顺序展示 D050。500 像素下的请求 View 最初出现「没有不冲突的标签位置」，浏览器回归测试复现，preflight 作为上游协作保留在 D050 View。窄屏浮动 Back/Home 在部分滚动位置覆盖标题或流程高亮按钮，远端边标签可能沿窄通道逐字折行。构造视图有两条不同语义的边交叉，通用图例将两类候选边归到「其他关系」。协作页的通用 scope 徽标仍显示「父节点」，协作语义在覆盖说明、边标签与详情中可核对。Owner 的独立桌面和窄屏视觉验收仍待完成。
- 全量翻译测试、生产执行、固定 B0/H 的最终 Content Delta Manifest、可信审核发布和正式合同激活未执行。本地交接后由独立 Standards 和 Spec 分别审查，其结论均待审。后续在 Owner 授权后及时把本票入口集成并复核其他已完成批次。

## 完整 Git 清点

```powershell
git status --short
git diff --stat 72864310175049a34e0c8ed789e096ead9c73a65
git diff --no-ext-diff 72864310175049a34e0c8ed789e096ead9c73a65
git ls-files --others --exclude-standard
```

Tracked diff 覆盖两份协作文档、两个共享 Web 文件与五个回归测试文件。下列未跟踪文件均须直接打开全文，分别是四份 Candidate 内容、一份 View 注册、两份测试和本交接。可用 `git diff --no-index -- NUL <path>` 获取每个未跟踪文件的完整差异。

```text
9 files changed, 230 insertions(+), 49 deletions(-)
```

```text
 M AGENTS.md
 M architecture-explorer/web/app.js
 M architecture-explorer/web/t4-state.js
 M docs/architecture-explorer-t4.md
 M tests/architecture_explorer/t12_call_evidence_dom.js
 M tests/architecture_explorer/t12_deep_recursive_dom.js
 M tests/architecture_explorer/t4_state_interactions.cjs
 M tests/architecture_explorer/test_recursive_views.py
 M tests/architecture_explorer/test_v3_ticket_06_integration.py
?? architecture-explorer/recursive-candidates/v4-t07-01-docx-extraction.v2.json
?? architecture-explorer/recursive-candidates/v4-t07-02-document-view.v2.json
?? architecture-explorer/recursive-candidates/v4-t07-03-logical-selection.v2.json
?? architecture-explorer/recursive-candidates/v4-t07-04-request-context.v2.json
?? architecture-explorer/views/v4-ticket-07.v1.json
?? docs/architecture-explorer/v4-ticket-07-handoff.md
?? tests/architecture_explorer/test_v4_ticket_07.py
?? tests/architecture_explorer/v4_ticket_07_dom.js
```
