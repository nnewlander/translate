# Retro R3 调查证据

[Issue #101](https://github.com/nnewlander/translate/issues/101) 的固定测量基线为 [`50bff1145d1533db28610ada5fe9c89eb4a2c7ff`](https://github.com/nnewlander/translate/commit/50bff1145d1533db28610ada5fe9c89eb4a2c7ff)。Owner 已确认独立 Standards PASS、Spec PASS，零阻塞发现。报告记录调查阶段结束时的状态。本次归档仅更正第 06、07 个正式样本的 setup 显示值，并更新证据链接。集成分支、归档提交与新 PR 的 CI 都使用各自的提交身份。

## 文件

| 路径 | 证据 |
|---|---|
| [report.md](report.md) | 调查过程、归因、正式基线及诊断结果 |
| [baseline-b01/runs.jsonl](baseline-b01/runs.jsonl) | 10 次独立正式运行。同目录保存全部 pytest stdout/stderr。 |
| [diagnostic-d01/runs.jsonl](diagnostic-d01/runs.jsonl) | 6 次独立诊断。同目录保存全部 pytest stdout/stderr 和 6 份事件 trace。 |
| [site-files.json](site-files.json) | 原测量期间观察到的 16 次运行的站点目录、文件名和字节数。按各批的 stdout 文件名对应运行。 |
| [measure.ps1](measure.ps1)、[r3_trace.py](r3_trace.py) | 调查期间使用的计时脚本及临时诊断插件，按原文件归档。 |
| [recompute.py](recompute.py) | 从归档计算墙钟、阶段、调用次数及统计结果。复用原计算方法，从归档站点清单核验产物记录。 |

两个 `runs.jsonl` 中的解释器及临时目录路径前缀以 `<worktree>`、`<run-output>` 表示，保留每次运行的独立目录标识。所有时间戳、耗时、参数顺序和结果保持原记录。pytest 输出和 trace 原样归档。16 个 stderr 文件均为空。生成站点、临时仓库、原机器路径明细、完整宿主采样与可重新生成的统计留在本地。

## 环境与宿主采样

- Windows 25H2 build 26200，Python 3.12.14、pytest 9.1.1、Git 2.52.0.windows.1，AST 子进程使用 Windows PowerShell 5.1.26100.9818。原项目准备脚本核验 Worktree junction、解释器前缀、`pip check`、Worktree 源码导入及干净 Git 状态。正式与诊断批次在各自会话复核环境。
- 2026-11-28（UTC+08:00）初查时 R2 验证仍在运行。20:59:16 至 21:00:03 连续四次采样未见 R2 或其他验证进程，CPU 12%–30%，磁盘活动 0%–1%，磁盘队列为 0。正式测量中段 21:05:55 仅识别到 R3 pytest，CPU 64%，磁盘活动 3%，队列为 0。
- 正式批次结束后，21:13:08 至 21:13:16 再次连续四次采样，未见验证进程，CPU 20%–34%，磁盘活动及队列均为 0。诊断后 21:20:38 至 21:20:46 的四次采样未见验证进程，CPU 19%–33%，磁盘活动 0%–1%，队列为 0。以上是离散宿主快照。
- Owner 已核验 T12 JUnit。Approved 271.457 秒、Rejected 215.541 秒、两条合计 486.998 秒、全量 3380.781 秒。[历史 T12 CI](https://github.com/nnewlander/translate/actions/runs/46126708575/job/140922786839)

## 独立复算

从项目根解析项目解释器并运行下列命令。脚本仅使用 Python 标准库，按脚本路径加载同目录证据。Windows 项目会话按其环境契约先验证解释器。复算直接读取记录，耗时和测试不再执行。

```powershell
$python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $python .\docs\architecture-explorer\retro-r3\recompute.py
```

默认输出 JSON 到 stdout，可传一个新文件路径以便另存。脚本验证两批记录、勘误处的 pytest setup 值、站点清单、诊断调用次数和父子区间的耗时守恒。

