# Architecture Explorer Retro R3 性能调查

Issue #101 的两条历史离线构建均完成正式测量和独立诊断。**广域 Source Facts 和窄范围 Relationship Continuity 扫描是主要耗时来源。** 两者合计约占诊断用例调用时间的 89%／92%。当前证据支持结束调查并保留既有测试覆盖。独立审查待执行。[Issue #101](https://github.com/nnewlander/translate/issues/101)

固定基线 `50bff1145d1533db28610ada5fe9c89eb4a2c7ff`，分支 `codex/architecture-explorer-retro-r3`。本轮新增内容位于 Git 忽略的 `output/retro-r3/`。

## 正式基线

同一 Windows 25H2 build 26200 主机，Python 3.12.14、pytest 9.1.1、Git 2.52.0.windows.1，源码扫描调用的 Windows PowerShell 为 5.1.26100.9818。Owner 授权的既有宿主机制成功执行原有准备脚本。项目解释器从 Worktree 的 `.venv\Scripts\python.exe` 解析，`sys.prefix` 解析到共享 `D:\translate\.venv`，`pip check` 通过，`booktrans` 来自 R3 Worktree 的 `src`。准备脚本及其依赖保持原状。[环境证据摘要](README.md#环境与宿主采样)

宿主初查发现 R2 正在运行。20:59 后连续四次采样未见 R2 或其他验证进程，CPU 12%–30%，磁盘活动 0%–1%，队列均为 0。正式批次从 21:00:12 开始，21:12:56 结束。中段快照只识别到 R3 的两个 pytest 进程，宿主 CPU 64%，磁盘活动 3%，队列为 0。进程与负载采用离散快照，耗时波动包含普通宿主负载变化。[宿主采样摘要](README.md#环境与宿主采样)

运行 `tests/architecture_explorer/test_t8_security_repairs.py::test_github_import_flows_through_offline_build_to_generated_site[approved]` 及同一函数的 `[rejected]`。每组两条，按组交换先后，共五组。每个样本均使用全新的独立 pytest 进程及 `D:\translate\output\r3-b01\` 下新的短路径 `--basetemp`。从 Shell 单调计时器记录进程墙钟，另保存 pytest 的 setup、call、teardown。十次均通过，全部纳入统计。下表单位为秒，括号内为最小值至最大值。

| 参数 | 按运行顺序的进程墙钟 | 墙钟中位数［范围］ | 墙钟极差 / 样本标准差 | pytest call 中位数［范围］ |
|---|---|---|---|---|
| approved | 75.20 / 87.23 / 99.60 / 88.89 / 85.38 | 87.23［75.20–99.60］ | 24.40 / 8.72 | 86.03［74.10–98.37］ |
| rejected | 60.43 / 64.63 / 76.87 / 62.86 / 63.35 | 63.35［60.43–76.87］ | 16.44 / 6.46 | 62.12［59.39–75.69］ |

五组两条合计墙钟分别为 **135.64、151.85、176.47、151.76、148.72 秒**，中位数 **151.76 秒**，范围 135.64–176.47 秒。Approved 和 Rejected 的单项墙钟中位数之和为 150.57 秒。正式样本中 Rejected 第 06、07 次运行的 pytest setup 为 0.01 秒，其余为 0.00 秒。所有 teardown 均为 0.00 秒，显示分辨率为 0.01 秒。[正式原始运行记录](baseline-b01/runs.jsonl) · [复算方式](README.md#独立复算)

Owner 核验的 T12 历史 JUnit 记录为 Approved 271.457 秒，Rejected 215.541 秒，两条合计 486.998 秒，全量 JUnit 3380.781 秒。该记录仅供历史参照。本机独立进程墙钟与历史 CI JUnit 记录的运行环境和口径不同。[T12 CI](https://github.com/nnewlander/translate/actions/runs/46126708575/job/140922786839)

## 调用链与计数

以下是固定提交上的调用职责与每个用例计数。诊断原始事件验证了关键阶段计数，包括两个晚拒绝的调用链。每列对应只选中一个参数化 node ID 的全新 pytest 进程。行间有包含关系。

| 操作 | approved | rejected | 覆盖职责 |
|---|---:|---:|---|
| Git fixture 命令 | 5 | 5 | 创建真实 Git 仓库，执行一次 init 和两组 add/commit。首个提交包含候选扫描白名单，第二个提交加入实际 Source Facts 所需源码。 |
| 构造导入用 Explorer fixture | 1 | 1 | 从基线 Worktree 发布 T3 Relationship、扫描 Source Facts、应用导航和七个 Candidate Fragments，再返回深拷贝，作为导入和篡改断言的可信语义输入。每进程缓存首次构造。 |
| `import-node-review` | 7 | 2 | 公共入口拒绝未独立锚定的材料。approved 另验五种 Evidence 篡改均拒绝且不落盘，并在成功导入中接受位置重定位及无关 Evidence 变更。两个用例的 GitHub 响应均由测试注入，接受的材料写为 Decision 和 source receipt。 |
| Relationship State 更新 | 1 | 1 | 公共 `update-review-state` 重新核验正式 Relationship 的当前 Evidence 并写 State。 |
| CLI Node Review State 更新 | 2 | 2 | 两个 `update-node-review-state` 各独立扫描。第一次评估导入材料，第二次评估 material fragment 对 Continuity 的影响，并断言 `needs-review` 与 `material-change`。 |
| 直接 v2 Node Review State 更新 | 1 | 0 | 从 generic v1 生成的 Explorer 数据构建 v2 Review Definition、材料及独立锚定，调用 `update_node_review_v2_state` 写入状态。该调用使用传入的已生成数据。 |
| Build 参数校验预期拒绝 | 3 | 1 | 两边均拒绝隐式 legacy v1。approved 另经公共入口拒绝单独传 Decision 或 source receipt。三个变体均在发布和广域扫描前拒绝。 |
| 进入实际 build 链 | 8 | 6 | 各执行 T3 发布、广域扫描及导航和片段装配。其中特定公共入口和篡改收据用例在校验可信材料时拒绝。 |
| `publish_reviewed_explorer` | 9 | 7 | 实际 build 加导入 fixture。构建基础架构，重新校验固定白名单文件哈希、受信 T3 Review Source 和 Mapping，评估正式 Relationship Continuity，采集实现 revision 与工作区状态。 |
| `scan_supervisor_worker_candidate` | 10 | 8 | 上一行每次发布都因固定 T3 映射为 APPROVED 且使用受信收据而评估一次，另加一次 Relationship State 更新。窄范围扫描读取六个白名单文件，生成正式 Relationship 的候选 Evidence，由 Continuity 比对 13 个 production Evidence ID。 |
| CLI 内的 `scan_python_source_facts` | 10 | 8 | 八/六次实际 build 加两次 CLI Node Review State 更新。基线断言恰为 10/8。 |
| 全部 `scan_python_source_facts` | 11 | 9 | 再加一次导入 Explorer fixture 的独立 Worktree 扫描。以 Git 记录限定源码，执行 Python AST、Definition Index、Call Facts 和 Supervisor PowerShell AST，生成含 revision 与工作区状态的可校验 Source Facts。 |
| `apply_view_projection` | 6 | 4 | 每次成功 `write_site` 的首步，装配和校验 UI Projection 并生成摘要。 |
| 站点落盘 | 6 | 4 | 每次生成 `explorer-data.json`、`explorer-data.js`，复制四个 Web 资产，合计 36/24 次站点文件写入或复制。测试另外读取生成数据验证投影。 |

表中的两种源码扫描都各启动一次 Windows PowerShell AST 子进程，合计静态预期 21/17 次。它们使用不同的解析查询和 Evidence 合同。限定表中四处 Git 调用的计数，fixture 为 5/5，广域扫描为 44/36（每次两次 ls-files、一次 status 和一次 rev-parse），窄扫描为 20/16（每次 status、rev-parse），T3 发布本身为 18/14（每次 status、rev-parse）。合计 87/71 次。Git 元数据归入发起它的扫描或发布阶段。诊断纠正了第一版静态计数。广域扫描除枚举 Python 文件，还单独用 ls-files 确认 Supervisor 受 Git 跟踪。

### 每次实际 build 的验证职责

| Build | approved | rejected | 证明 |
|---|---|---|---|
| Legacy，缺 Node State | 生成 | — | 有材料但缺 State 时，生成站点投影受限为 Unknown。 |
| Legacy，公共入口 | 拒绝 | 拒绝 | 即使提供完整参数与盘上材料，仍必须有独立可信锚点。 |
| Legacy，有 Node State | 生成 | 生成 | JSON 中有对应 Current 状态，核对持久 State 的评估快照与证据位置，JS 中包含状态与路径。 |
| Generic v1 | 生成 | 生成 | Review Definitions 路径复现 v1 的 Current 投影与 State 字段。 |
| Generic v2 | 生成 | — | v2 Definition 与目录材料路径生成 Current Approved，核对 State 与 JS 证据路径。 |
| Material input，公共入口 | 生成 | 生成 | 在候选片段追加换行改变输入摘要，生成不含直接 Node Review 材料的实际站点，提供后续 Continuity 评估输入。 |
| Historical | 生成 | 生成 | 用已失效 State 重建，验证 JSON 和 JS 都只投影相应 Historical 状态。 |
| Historical，收据篡改 | 拒绝 | 拒绝 | 将权限降为 write 并重算自有摘要之后仍须拒绝，证明历史状态也不能绕过对 receipt 的信任验证。 |

入口参考 `tests/architecture_explorer/test_t8_security_repairs.py:196,684`、`t5_lite_fixture.py:14,28,40`、`test_t8_node_review.py:44,114`。实现参考 `review_cli.py:330,345,554,586,605,817,826,995`、`review_mapping.py:257,335,453`、`continuity.py:89,138,183`、`candidate_scanner.py:853,985`、`source_scanner.py:486,1873,2160`、`generator.py:426,431` 和 `view_projection.py:593`。实现文件均位于 `src/booktrans/architecture_explorer/`。

## 诊断计时

正式批次结束后重新核验宿主，随后独立运行每个参数三次，六次全部通过。诊断使用新的短路径及临时 pytest 插件。插件转发原函数与既有断言，附加单调纳秒时钟、父子调用 ID、顺序、结果等记录。已核对每条 trace 的父子时间区间、独占耗时和原有计数，另核对 16 个正式/诊断运行的实际生成站点文件清单。诊断 call 的中位数和范围为 Approved 75.706［75.349–75.851］ 秒、Rejected 60.673［60.485–60.954］ 秒。诊断跑在不同时间窗，正式批次和诊断批次的差值不用于估算探针开销。[独立诊断运行记录](diagnostic-d01/runs.jsonl) · [宿主采样摘要](README.md#环境与宿主采样)

下表对各次调用的独占时间按职责归并，未探测的间隙计入其余测试及 CLI 工作。每个样本的各行相加为该样本的诊断 call 时间。各行独立取三次中位数，再列最小值至最大值。单位为秒。

| 互斥归因 | Approved 中位数［范围］ | Rejected 中位数［范围］ |
|---|---|---|
| Git fixture，总计 | 0.63［0.62–0.70］ | 0.62［0.58–0.74］ |
| 广域 Source Facts，扫描及附加校验 | 56.30［55.91–56.35］ | 47.04［46.98–47.27］ |
| 窄范围 Relationship Continuity 扫描 | 10.95［10.94–11.22］ | 8.74［8.69–8.85］ |
| T3 Relationship 发布余项 | 1.11［1.01–1.16］ | 0.91［0.87–0.95］ |
| 投影构建与装配 | 1.54［1.49–1.59］ | 1.05［1.03–1.09］ |
| 站点序列化与落盘 | 1.37［1.35–1.38］ | 0.91［0.90–0.94］ |
| Review State、导入和材料写入 | 0.43［0.42–0.43］ | 0.14［0.13–0.14］ |
| 其余测试及 CLI 工作 | 3.34［3.32–3.36］ | 1.18［1.15–1.19］ |

下表的包含时间计入被探测子调用，独占时间扣除直接子调用。同一阶段先在每个样本内按所有调用相加，再取三次中位数和范围。阶段间有嵌套关系。比如 `write_site` 的独占值同时扣除了嵌套的投影和 JSON 写入。上表的站点序列化与落盘只扣除 UI Projection，计入 JSON、JS 和资产写入。

| 阶段 | Approved 次数 | Approved 包含（秒） | Approved 独占（秒） | Rejected 次数 | Rejected 包含（秒） | Rejected 独占（秒） |
|---|---:|---|---|---:|---|---|
| Git fixture 命令 | 5 | 0.608［0.587–0.673］ | 0.608［0.587–0.673］ | 5 | 0.591［0.559–0.721］ | 0.591［0.559–0.721］ |
| 广域 Source Facts 扫描 | 11 | 55.135［54.790–55.184］ | 0.564［0.561–0.577］ | 9 | 46.104［45.983–46.304］ | 0.482［0.472–0.500］ |
| Python 模块解析及证据提取 | 500 | 16.756［16.675–16.757］ | 16.756［16.675–16.757］ | 464 | 14.008［13.884–14.034］ | 14.008［13.884–14.034］ |
| Call Facts 构建 | 11 | 13.297［13.279–13.346］ | 13.297［13.279–13.346］ | 9 | 11.045［10.971–11.133］ | 11.045［10.971–11.133］ |
| 广域 PowerShell AST | 11 | 21.845［21.840–21.859］ | 21.845［21.840–21.859］ | 9 | 18.319［18.314–18.432］ | 18.319［18.314–18.432］ |
| 广域扫描 Git 元数据 | 44 | 2.424［2.094–2.508］ | 2.424［2.094–2.508］ | 36 | 2.064［2.015–2.119］ | 2.064［2.015–2.119］ |
| T3 Relationship 发布 | 9 | 10.963［10.962–11.098］ | 0.026［0.025–0.027］ | 7 | 8.550［8.473–8.655］ | 0.020［0.020–0.020］ |
| 窄范围 Continuity 扫描 | 10 | 10.946［10.940–11.217］ | 0.893［0.884–0.911］ | 8 | 8.741［8.685–8.853］ | 0.710［0.693–0.720］ |
| 窄范围 PowerShell AST | 10 | 8.899［8.876–8.977］ | 8.899［8.876–8.977］ | 8 | 7.134［7.130–7.213］ | 7.134［7.130–7.213］ |
| UI Projection | 6 | 0.636［0.636–0.667］ | 0.636［0.636–0.667］ | 4 | 0.379［0.377–0.381］ | 0.379［0.377–0.381］ |
| write_site（含 UI Projection） | 6 | 2.008［1.987–2.042］ | 0.458［0.456–0.459］ | 4 | 1.287［1.279–1.320］ | 0.305［0.294–0.323］ |
| 站点 JSON 序列化与写入 | 6 | 0.914［0.895–0.916］ | 0.914［0.895–0.916］ | 4 | 0.606［0.604–0.619］ | 0.606［0.604–0.619］ |

- 导入 fixture 的完整 Worktree 广域扫描每次各一次，用时 9.332［9.268–9.372］／9.391［9.329–9.407］ 秒。对临时源码仓库的 CLI 广域扫描共 10／8 次，合计 45.763［45.522–45.852］／36.713［36.654–36.897］ 秒。重复扫描既服务于构建与 State 更新，也出现在需要证明晚拒绝的路径。
- 两套 PowerShell AST 的计时覆盖子进程启动、解析查询和输出处理，共 21／17 次，合计 30.758［30.721–30.817］／25.449［25.448–25.644］ 秒。Python 模块解析与 Call Facts 构建也是广域扫描的大宗开销。AST 调用内部的启动和查询成本尚未单独归因。
- Git fixture 自身约 0.6 秒。所有被跟踪 Git 命令合计 5.256［4.966–5.403］／4.353［4.350–4.610］ 秒，其中包含 Git fixture 的 5 次命令。投影构建和站点落盘占比较小，实际输出保留了六个/四个站点及每站六个文件。

## 优化判断

本轮建议保留当前实现并以调查收尾。按正式两条合计中位数计算，10% 目标约需减少 **15.18 秒**。两用例所有被追踪 Git 命令的诊断中位耗时之和约 9.61 秒，且其中有必需的真实仓库与工作区验证。投影和写站的规模更小，优先优化这些阶段缺乏足够收益。削减晚拒绝扫描、跨 CLI 调用缓存实时 Source Facts、合并两个不同 Evidence 合同的扫描，都会影响当前测试所证明的行为或改变其可信边界。

如后续单独授权，最小的可调查方向是广域 Supervisor PowerShell AST 查询。当前代码对同类 CommandAst 重复遍历以分别识别 alias、provider、session、variable 和 dot invocation。可优先量化这些遍历在查询内的成本。如果该成本足够大，再考虑在单次真实 AST 内复用按原顺序取得的 CommandAst 输入，保留各分类的判定条件、作用域、输出顺序、异常处理与 Evidence 摘要。当前计时尚无法证明该方向能达到准入线。[查询实现参考](https://github.com/nnewlander/translate/blob/50bff1145d1533db28610ada5fe9c89eb4a2c7ff/src/booktrans/architecture_explorer/source_scanner.py#L258)

未来候选对照在同一 Windows 主机按 B→C、C→B 交替五组，每组各跑基线和候选的两个相同 node ID。维持独立进程、新短路径和参数先后配平。准入按 Issue 执行，合计墙钟中位数至少降低 10%、五组至少四组改善、任一用例无明显退化。对照前先约定“明显退化”的具体阈值，以现有验证确认公共入口、真实扫描、可信边界、Continuity 和生成站点覆盖。

候选实施、候选对照、完整验证器、正式 CI 和独立审查本轮均未执行。Git 核验确认 HEAD、暂存区及已跟踪内容与基线一致，独立审查交接可复查原始数据。[归档证据索引](README.md)
