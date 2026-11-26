# v4-06 整批校验、原子导入与安全重试交接

## 固定评审范围

```text
Base: 3b0da7fcb74600efa63734c3b575293e91d6f0f1
HEAD: 3b0da7fcb74600efa63734c3b575293e91d6f0f1
Branch: codex/architecture-explorer-v4-ticket-06
Worktree: D:\translate\worktrees\architecture-explorer-v4-ticket-06
B0: 09aaf4a033123c7eca8d280fa8982e5b0e711b86
```

合同见 [Spec #74](https://github.com/nnewlander/translate/issues/74)、[T5 #79](https://github.com/nnewlander/translate/issues/79)、[T6 #80](https://github.com/nnewlander/translate/issues/80) 及本轮 Owner 指示。

## 变更清点

`git status --short`：

```text
 M src/booktrans/architecture_explorer/review_cli.py
?? docs/architecture-explorer/v4-ticket-06-handoff.md
?? src/booktrans/architecture_explorer/batch_import.py
?? tests/architecture_explorer/test_v4_batch_import.py
```

`git diff --stat 3b0da7fcb74600efa63734c3b575293e91d6f0f1`：

```text
 src/booktrans/architecture_explorer/review_cli.py | 135 +++++++++++++++++-----
 1 file changed, 105 insertions(+), 30 deletions(-)
```

`git ls-files --others --exclude-standard`：

```text
docs/architecture-explorer/v4-ticket-06-handoff.md
src/booktrans/architecture_explorer/batch_import.py
tests/architecture_explorer/test_v4_batch_import.py
```

完整补丁附在 `output/a80-review-diff.txt`。首段是 `git diff --no-ext-diff 3b0da7fcb74600efa63734c3b575293e91d6f0f1` 的全部输出。其余三段来自 `git diff --no-index --no-ext-diff -- /dev/null <未跟踪路径>`，包含三个文件的完整内容。评审须检查全部文件，分别覆盖 CLI 接线、预检和正式读取实现、测试与交接证据。附件位于 Git 忽略的 `output/`。

## 正式入口和恢复

- `batch_import.import_review_batch` 消费 #79 Publication、Receipt 和 `install-anchors` 输出的正式宿主安装路径。按 T4 的已决定集合预检，聚合 Receipt、Anchor、当前 Packet 与 T3 错误。可取得的 Decision/source 用于验证独立错误。材料不全时保留已证实的错误。所有写入集中于预检成功后的同级 staging，目录 rename 一次发布。
- `review_cli build --review-batch-directory <发布批目录>` 可重复传入多个批次。继续提供已有的 `--node-review-definitions` 和按需提供 `--relationship-review-definitions`。读取器核对 Manifest、批准摘要、宿主实际安装、完整对象集合，以及每项 Decision 和 Receipt，随后由原有 T3 投影处理。旧逐对象参数继续接受合法历史资产，批次绑定的 Decision 需走批次入口。
- `review_cli update-node-review-state` 和 `update-relationship-review-state` 使用同一个 `--review-batch-directory` 与 `--review-object-key <kind::object-id>`。保留各命令所需的 Explorer、Definitions 和 `--assessed-at` 参数。State 更新只能写选中成员在批目录中的 `state.json`。关系更新使用已经验证的批次 Node State 评估端点。正式读取接受合法更新的 State，初始 State 摘要仍用于判断导入能否原样重放。
- 合法 REJECTED 正常消费。PENDING 和 DEFERRED 保持待审。同批 Node 拒绝直接否定关系所依赖的职责时，既有 Continuity 机制把关系标为需复核。暂存目录、缺件批次、仅授权但未安装的 Anchor 均无法进入正式消费流程。

## 最终增量复审：Publication 缺失

原 Reviewer 的最新结论为 Standards PASS / Spec FAIL。本轮只改 `batch_import.py` 的预检材料交接及对应公共入口回归。Publication 缺失时保留批级 `RECEIPT_BATCH`，并把可读取的 Decision/source 对交给既有逐对象校验。缺少 Publication 的验证步骤不产生推断错误。

红灯退出 1，`1 failed, 1 passed`。Publication 缺失反例仅返回 `RECEIPT_BATCH`，遗漏了 Decomposition 的独立校验错误。绿灯退出 0，`2 passed`。该反例现在只返回预期的 `RECEIPT_BATCH`、`ANCHOR_BINDING` 和 `OBJECT_VALIDATION`，没有导入任何对象，输出父目录未创建。原 Receipt 缺失用例保持通过。

最终聚焦联调退出 0，`32 passed in 2.05s`，零跳过。包含 #80 公共导入与正式 Review CLI/站点组合以及 T4 批量审核回归。环境准备、解释器前缀、本工作树导入、`pip check` 和内存语法编译通过。首次宽范围运行误从已更新的 #79 HEAD 加载 View Projection，导致 2 个站点夹具失败。下列复跑命令只选用 #79 producer 及其审核依赖，并断言 #80 导入、CLI 和 View Projection 的实际来源。

#79 干净 HEAD 为 `6ed357b6a82c0edfafe5e477449ca9d9aa7e6106`，下节列出的 #79 producer、审核依赖、测试及 fixture blob 与该 HEAD 上的实测文件一致。#80 HEAD 和 Git 清点与上文一致。本轮实际 blob 更新为：

```text
dc98d6361ea19f9ba703bff28e5173b654549df7  #80/src/booktrans/architecture_explorer/batch_import.py
51bce9bd2e1346168d6dd19ac08c264984531944  #80/tests/architecture_explorer/test_v4_batch_import.py
```

```powershell
& .\scripts\architecture_explorer\prepare_worktree.ps1
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $python -c "import pathlib, sys; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path('.venv').resolve(); import booktrans; assert pathlib.Path(booktrans.__file__).is_relative_to(pathlib.Path.cwd())"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -c "import importlib.util,pathlib,sys; t6=pathlib.Path.cwd(); t5=t6.parent/'architecture-explorer-v4-ticket-05'; package='booktrans.architecture_explorer.'; overlay={'trusted_batch_review','trusted_batch_review_cli','github_review_publisher','review_artifacts','review_trust','review_source','node_review_definitions','relationship_review'}; sys.meta_path.insert(0,type('ProducerOverlay',(),{'find_spec':lambda self,name,path=None,target=None: importlib.util.spec_from_file_location(name,t5/'src/booktrans/architecture_explorer'/ (name.rsplit('.',1)[-1]+'.py')) if name.startswith(package) and name.rsplit('.',1)[-1] in overlay else None})()); sys.path.insert(0,str(t5/'tests/architecture_explorer')); from booktrans.architecture_explorer import batch_import,review_cli,view_projection,trusted_batch_review; assert all(pathlib.Path(m.__file__).is_relative_to(t6) for m in (batch_import,review_cli,view_projection)); assert pathlib.Path(trusted_batch_review.__file__).is_relative_to(t5); import pytest; raise SystemExit(pytest.main(sys.argv[1:]))" tests/architecture_explorer/test_v4_batch_import.py tests/architecture_explorer/test_v4_batch_review.py -q --tb=short -p no:cacheprovider
```

红灯和首次绿灯沿用下节的跨工作树导入方式，调用 `pytest.main(sys.argv[1:])`，参数为 `tests/architecture_explorer/test_v4_batch_import.py -k missing_batch_input -q --tb=short`。本轮未重跑历史全量或执行真实审核。

## 上一轮验证记录

并行联调使用 #79 工作树 `D:\translate\worktrees\architecture-explorer-v4-ticket-05`，分支 `codex/architecture-explorer-v4-ticket-05`，HEAD `3b0da7fcb74600efa63734c3b575293e91d6f0f1`。以下是实际输入和测试文件的 `git hash-object --no-filters` blob 标识：

```text
2e6718c0b9796877f70c6d0669b09c28f49810e7  #80/src/booktrans/architecture_explorer/batch_import.py
60d0831186ff373860ad62cd378cbac57440c845  #80/src/booktrans/architecture_explorer/review_cli.py
f28b6f1478a2355317aa2abba20864178a2b5645  #80/tests/architecture_explorer/test_v4_batch_import.py
354beabd60d346c4773468f697567a4b08a06bc3  #79/src/booktrans/architecture_explorer/trusted_batch_review.py
232ad920653c8b0da57612352592d8f177fe0b35  #79/src/booktrans/architecture_explorer/trusted_batch_review_cli.py
bf5880bb3dd289d6adca210b4258f296f06c8175  #79/src/booktrans/architecture_explorer/github_review_publisher.py
5f98a9c863feaf54b6c6c3409832b69743bb6f1f  #79/src/booktrans/architecture_explorer/review_artifacts.py
5501310d779dddc9f4f81ddeb341e1af6515d107  #79/src/booktrans/architecture_explorer/review_trust.py
7bb0faca62fb2e102f21fec3f85c0efdaf3b87ce  #79/src/booktrans/architecture_explorer/review_source.py
1e4dd39970b01fb1bf04d86d78d8fd93a45551e2  #79/src/booktrans/architecture_explorer/node_review_definitions.py
14b0bff6d8b123e5bd3d76a8d68b2ef62b8943fb  #79/src/booktrans/architecture_explorer/relationship_review.py
d25fc17a110615340560d6e46a5520649cb6039a  #79/tests/architecture_explorer/test_v4_trusted_batch_review.py
f36c717006f88c843bf854698d33d07f14c4ee9e  #79/tests/fixtures/architecture_explorer/v4_ticket_05/handoff.v1.json
```

从 #80 Worktree 根目录执行，使用短临时目录规避 Windows 路径长度限制。环境准备、解释器前缀、本 Worktree 的 `booktrans` 导入、`pip check` 和内存语法编译均通过。

```powershell
& .\scripts\architecture_explorer\prepare_worktree.ps1
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $python -c "import pathlib, sys; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path('.venv').resolve(); import booktrans; assert pathlib.Path(booktrans.__file__).is_relative_to(pathlib.Path.cwd()); print(sys.executable); print(sys.prefix)"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -c "from pathlib import Path; import sys; [compile(Path(name).read_bytes(), name, 'exec') for name in sys.argv[1:]]; print('SYNTAX_OK')" src/booktrans/architecture_explorer/batch_import.py src/booktrans/architecture_explorer/review_cli.py tests/architecture_explorer/test_v4_batch_import.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:ARCHITECTURE_EXPLORER_T6_CONSUMER = (Resolve-Path 'src/booktrans/architecture_explorer/batch_import.py').Path
& $python -c "import pathlib,sys; t6=pathlib.Path.cwd(); t5=t6.parent/'architecture-explorer-v4-ticket-05'; sys.path[:0]=[str(t5/'src'),str(t5/'tests/architecture_explorer')]; import booktrans.architecture_explorer as ae; import booktrans.architecture_explorer.trusted_batch_review; ae.__path__.insert(0,str(t6/'src/booktrans/architecture_explorer')); import pytest; raise SystemExit(pytest.main(['tests/architecture_explorer/test_v4_batch_import.py','tests/architecture_explorer/test_v4_batch_review.py','tests/architecture_explorer/test_v4_relationship_review.py','tests/architecture_explorer/test_v3_node_review.py',str(t5/'tests/architecture_explorer/test_v4_trusted_batch_review.py'),'-q','--tb=short','-rs','-p','no:cacheprovider','--rootdir',str(t6),'--basetemp',str(t6/'.pytest_cache'/'a80-r8')]))"
```

组合测试退出 0，`105 passed in 24.29s`，零跳过。反向联调另外在仅收集 #79 测试的进程验证，用同一环境准备和解释器检查，设置同一个 `ARCHITECTURE_EXPLORER_T6_CONSUMER` 后执行：

```powershell
& $python -c "import pathlib,sys; t6=pathlib.Path.cwd(); t5=t6.parent/'architecture-explorer-v4-ticket-05'; sys.path[:0]=[str(t5/'src'),str(t5/'tests/architecture_explorer')]; import booktrans.architecture_explorer as ae; import booktrans.architecture_explorer.trusted_batch_review; ae.__path__.insert(0,str(t6/'src/booktrans/architecture_explorer')); import pytest; raise SystemExit(pytest.main([str(t5/'tests/architecture_explorer/test_v4_trusted_batch_review.py'),'-k','producer_installation_passes_real_t6_consumer','-q','--tb=short','-rs','-p','no:cacheprovider','--rootdir',str(t6),'--basetemp',str(t6/'.pytest_cache'/'a80-r7')]))"
```

反向联调退出 0，2 passed、29 deselected。预检反例的红灯为 4 failed，分别复现对象错误遗漏和三种 AttributeError。正式 CLI 反例先因缺少批次参数失败，接通后以真实站点输出与 State 更新通过。早期聚焦组合 102 passed、2 skipped，两个 skip 来自未配置 #80 路径。最终命令显式指定路径。一次并行 `py_compile` 在 Windows 替换缓存文件时失败，最终检查使用内存编译。

## 验证限制与独立复审

- 历史 670 passed 属于旧版代码的 Architecture Explorer 全集，历史 59 passed 属于上一版本联调。两者不作为本轮结论。
- 本轮按 Owner 指示运行聚焦回归。仓库全量此前在收集时缺少 Git 忽略的生产运行选择文件，本轮没有重跑。Browser、真实 GitHub/Owner 授权、生产 Anchor 安装、#84 与突然断电恢复不在本轮验证内。测试替换了 GitHub、历史基底和源码扫描输入，实际调用 #79 producer、#80 导入与正式 Review CLI，并执行 State 更新、Review 投影、View Projection 和离线站点写入。
- Standards 复审输入为仓库规则、此 handoff、原始补丁及 blob。原 Standards FAIL 的交接和环境项已补齐，结论由原 Reviewer 复核。
- Spec 复审输入为 #74/#79/#80 与 Owner 修复要求。原 Spec FAIL 的正式消费、独立预检和行为测试项有上述证据，结论由原 Reviewer 复核。
- #79 和 #80 仍是并行工作树。合入时应对照这里记录的 producer 和 consumer blob。#76 无本轮文件修改。
