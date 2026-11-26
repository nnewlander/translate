# v4-05 修复交接

- Base、HEAD 与 Bstart 均为 `3b0da7fcb74600efa63734c3b575293e91d6f0f1`。B0 为 `09aaf4a033123c7eca8d280fa8982e5b0e711b86`。
- 分支 `codex/architecture-explorer-v4-ticket-05`，Worktree `D:\translate\worktrees\architecture-explorer-v4-ticket-05`。
- 范围 [Spec #74](https://github.com/nnewlander/translate/issues/74)、[Ticket #79](https://github.com/nnewlander/translate/issues/79)、[Ticket #80](https://github.com/nnewlander/translate/issues/80)。原 Reviewer 最新聚焦复审为 Standards PASS、Spec FAIL。最新增量只处理 Receipt 异常归因及 #80 测试加载兼容。独立结论待原 Reviewer 确认。

## 本轮增量及验证

- `src/booktrans/architecture_explorer/trusted_batch_review.py` 将实时权限 `live_permission` 与逐对象的 `receipt_permission` 分离。首条为 write、次条完全正常的场景只归因首条，整批仍为 BLOCKED，无法完成第二次确认或写入安装目标。
- `tests/architecture_explorer/test_v4_trusted_batch_review.py` 增加上述公共接口回归，并在执行 #80 动态模块前用 pytest 的 `monkeypatch` 将它注册到 `sys.modules`。两组用例结束时自动恢复模块状态。#80 consumer 业务实现未修改。
- 本文件更新交接证据及末尾的原始 Git 清点。其余源码和 fixture 为既有交付。

```powershell
$env:ARCHITECTURE_EXPLORER_T6_CONSUMER=(Resolve-Path '../architecture-explorer-v4-ticket-06/src/booktrans/architecture_explorer/batch_import.py').Path
$python=(Resolve-Path '.venv\Scripts\python.exe').Path
& $python -m pytest -q -p no:cacheprovider --basetemp D:\translate\.test-tmp\t5round4 tests/architecture_explorer/test_v4_trusted_batch_review.py -k 'first_receipt_with_write or producer_installation'
```

同一命令的 Red 退出 1，3 failed、28 deselected。首条坏 Receipt 令次条被误报，两个 #80 用例在执行 `@dataclass` 时因模块未注册而失败。Green 退出 0，3 passed、28 deselected，两组均实际进入 #80 公共入口。完整聚焦回归命令及结果见下方。

## 前轮五项 Finding

| 维度 | Finding | 修复与证据 |
|---|---|---|
| Standards | 宿主授权与安装 | 注册表只登记授权。宿主将同一批次原子写入正式位置后，loader 和 Trust Policy 才接受它。真实文件系统测试复现普通路径预备字节绕过，修复后拒绝。#80 同批实测拒绝未安装材料且零写入，正式安装后导入并可重放。 |
| Standards | 交接清点 | 本文件末尾附四条清点命令的原始输出和完整已跟踪 diff，未跟踪源码按清点逐一读取。 |
| Spec | 最新写前权限 | 复用同一 Owner/admin 权限校验，每次 POST/PATCH 写入前刷新。admin → write 原反例从一次 POST 变为零 POST、零 PATCH。 |
| Spec | 稳定发布状态 | 默认锁与意向存储于宿主用户目录。真实 Windows 文件系统上，两次模拟 CLI 运行切换工作目录与 TEMP，第二次恢复看到同一意向，POST 次数从两次降为一次。 |
| Spec | 第二次确认清单 | 缺失与 null 保留对象行及原因。数组型 Issue ID 或评论 ID 进入 BLOCKED 清单。同一对象错误集合生成对象行和汇总，正整数 ID 才参与重复检查。 |

## 七项 Finding

1. **Standards 1，宿主授权。** `review_trust.PRODUCTION_BATCH_INSTALLATION_DIGESTS` 是宿主独立配置，默认空。读取和转换 Trust Policy 均核对已登记摘要及正式宿主安装文件。测试仅在进程内提供临时宿主路径及摘要。
2. **Standards 2，审查交接。** 本文件包含基线、全部变更、独立审查覆盖、复现命令和限制。
3. **Spec 1，写入前后核验。** 固定 `github.com`、仓库名和数字身份、Issue URL/编号/数字 ID、固定 Owner 和 admin。写入前预检所有相关已有评论的来源和正文。POST/PATCH 检查返回来源与正文，PATCH 校验原 ID，之后再 GET 实际评论构建 Receipt。
4. **Spec 2，兼容。** Node 和 Relationship 复用 T3 的既有规范渲染和验证，新增可选 `Publication-Key` 行，原正文保持兼容。两种结果分配均以实际 #79 producer 输出进入 #80 公共导入和 T3 公共验证。
5. **Spec 3，恢复。** 同主机 CLI 使用 Issue 级 OS 锁和持久 POST 意向记录。再次执行先读评论，精确匹配则复用，存在未决 POST 且评论不可见则停止。Receipt 与 Anchor 采用同目录 staging 后原子无覆盖发布。相同内容可重试，冲突和并发不同内容拒绝覆盖。
6. **Spec 4，共享 fixture。** 从隔离 publisher 的实际业务流程重新生成 UTF-8 fixture。测试逐字段对比重新生成结果，并检查替换字符。
7. **Spec 5，Owner 清单。** 缺失、非法、重复或不匹配条目汇总为 BLOCKED。真实 CLI 准备清单时重新只读核对 GitHub Receipt。清单展示中文名、首次决定、完整正文及其链接、评论 ID、时间、权限、摘要和精确 Anchor。示例有醒目的隔离标识。

## 共用接口与摘要

`trusted_batch_review` 提供 `prepare_comment_publication`、`publish_review_comments`、`prepare_anchor_confirmation`、`render_second_confirmation_checklist`、`confirm_anchor_batch`、`prepare_anchor_installation`、`install_anchor_batch`、`load_installed_anchor_batch(path, batch, first_confirmation)`、`installed_anchor_trust_policy(installed)` 和 `review_batch_materials(batch, first_confirmation, publication, receipts)`。最后一个函数给 #80 返回原有的 `{decision, source}` 逐对象资料。

唯一安装 schema 为 `architecture-explorer.batch-anchor-installation.v1`。固定字段为 `schema, status, batch_id, batch_sha256, confirmation_sha256, objects, installation_sha256`。其中状态是 `installed`，`batch_id` 是第二次确认记录摘要，`confirmation_sha256` 是首次确认摘要。`objects` 精确覆盖 T4 的已决定对象。稳定键为 `kind::id`，原始 Node Packet 的 `node_id` 和 Relationship Packet 的 `object_id` 保持原样。每项带 `object_key, decision_id, decision_sha256, source_receipt_sha256, anchor`。

- T4 `first_decision_sha256` 从首次确认的对象 binding 获取。它绑定 Owner 原始决定、理由和范围。
- `packet_sha256` 是 T4 权威 Packet 摘要。发布键绑定首次确认、Packet、决定和目的地，最终正文包含此发布键。
- 安装条目的 `decision_sha256` 覆盖 T3 格式的最终 Decision，包括真实 `github-comment:<id>`、Receipt 摘要和发布键。
- `installation_sha256` 覆盖整个安装记录（除自身外），只有宿主独立固定此摘要才允许安装和读取。Reader 还核验 T4 首次确认和完整已决定对象集合。

#80 公共入口联调：

```python
import_review_batch(
    explorer, batch, first_confirmation, node_definitions,
    relationship_definitions, publication, receipts, installed_anchor_path,
    batch_directory, assessed_at=assessed_at,
)
```

#80 独占整批预检、原子导入与 State 维护。双方开发会话已同步接口，并分别调用实际 producer/consumer。#79 测试以 `ARCHITECTURE_EXPLORER_T6_CONSUMER` 指向 #80 的 `batch_import.py`。

## 两次 Owner 确认与恢复

从 Worktree 根目录以 `PYTHONPATH` 指向本地 `src`，并使用显式项目解释器运行模块 `booktrans.architecture_explorer.trusted_batch_review_cli`。

1. T4 完成首次决定确认后，`prepare-publication --batch ... --first-confirmation ... --issue-number ... --issue-id ... --output ...` 绑定预期 GitHub 目标。操作人核对首次确认覆盖该发布任务，传入该计划的摘要执行 `publish-comments --batch ... --first-confirmation ... --publication ... --confirmed-publication-sha256 ... --output ...`。命令参数用于匹配材料内容，Owner 身份仍通过实际 GitHub 来源校验。
2. `prepare-anchor-confirmation --batch ... --first-confirmation ... --publication ... --receipts ... --output ... --checklist-output ...` 读取 GitHub 并准备清单。Owner 一次独立确认清单中的精确 Anchor 及宿主登记与安装授权。
3. 获得该授权后，执行 `confirm-anchors --candidate ... --checklist ... --output ...` 计算同一授权对应的 `host_authorization_digest`。宿主维护者在已审核的运行配置中登记该摘要，再执行 `install-anchors --candidate ... --second-confirmation ...`，将打印的正式路径传给 #80。Windows 默认位置为 `%USERPROFILE%\.booktrans\architecture-explorer\installed-anchors\<digest>.json`，公共函数 `host_anchor_installation_path(digest)` 返回同一路径。可选的 `--output` 只接受该正式路径。

同一 Owner 的 Windows 会话在 `%USERPROFILE%\.booktrans\architecture-explorer\publication` 共用锁与未决 POST 意向。使用同一计划重试，确认 GitHub 已有记录后复用，暂时无法确定则保持阻塞。宿主目录在本机用户配置文件下，维护者应保留其内容及访问控制。

## 共享隔离材料

`tests/fixtures/architecture_explorer/v4_ticket_05/handoff.v1.json` 自包含 Explorer、Definitions、T4 Batch、首次确认、发布计划、Receipt、第二次确认和安装文件内容。其 `fixture_only` 标识和显眼的 Markdown 示例只适用于隔离验证。原三份独立 fixture 文件由此单一回放包替代。用实际 generator 输出重新生成 JSON：

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path + ';' + (Resolve-Path 'tests/architecture_explorer').Path
$python=(Resolve-Path '.venv\Scripts\python.exe').Path
& $python -c "import json; from test_v4_trusted_batch_review import isolated_handoff_fixture; print(json.dumps(isolated_handoff_fixture(), ensure_ascii=True))"
```

生成命令输出 ASCII JSON 转义以避免 Windows 控制台代码页污染。保存时可将 JSON 解析后以 UTF-8 格式化。清单样例由其中的 `checklist` 字段生成。测试只在自身进程内临时 pin 摘要，测试安装和导入目标都在隔离临时目录。

## 完整变更与审查覆盖

命令 `git status --short --untracked-files=all`、`git diff --stat 3b0da7fcb74600efa63734c3b575293e91d6f0f1`、`git diff --no-ext-diff 3b0da7fcb74600efa63734c3b575293e91d6f0f1` 和 `git ls-files --others --exclude-standard` 用于复审覆盖。

四份已跟踪差异全部在 `src/booktrans/architecture_explorer/`，无排除项：

- `node_review_definitions.py`、`relationship_review.py`：T3 正文兼容及发布键校验。
- `review_source.py`：Receipt ID 类型和时间时区校验。
- `review_trust.py`：宿主安装摘要注册表。

以下八项未跟踪文件均需从本 Worktree 打开全文审查：

- `docs/architecture-explorer/v4-ticket-05-handoff.md`：合同、操作步骤和复审证据。
- `docs/architecture-explorer/v4-ticket-05-second-confirmation-example.md`：Owner 清单。
- `src/booktrans/architecture_explorer/github_review_publisher.py`：固定 GitHub 宿主、锁、POST 意向。
- `src/booktrans/architecture_explorer/review_artifacts.py`：原子无覆盖落盘。
- `src/booktrans/architecture_explorer/trusted_batch_review.py`：发布、Receipt、确认、宿主安装和交接。
- `src/booktrans/architecture_explorer/trusted_batch_review_cli.py`：业务入口。
- `tests/architecture_explorer/test_v4_trusted_batch_review.py`：正反例、故障注入与跨票联调。
- `tests/fixtures/architecture_explorer/v4_ticket_05/handoff.v1.json`：可回放隔离材料。

## 验证证据与限制

```powershell
$env:ARCHITECTURE_EXPLORER_T6_CONSUMER=(Resolve-Path '../architecture-explorer-v4-ticket-06/src/booktrans/architecture_explorer/batch_import.py').Path
$python=(Resolve-Path '.venv\Scripts\python.exe').Path
& $python -m pytest -q -p no:cacheprovider --basetemp D:\translate\.test-tmp\t5round4 tests/architecture_explorer/test_v4_trusted_batch_review.py tests/architecture_explorer/test_v4_batch_review.py tests/architecture_explorer/test_v4_relationship_review.py tests/architecture_explorer/test_t3_security_repairs.py
$env:PYTHONPATH=(Resolve-Path src).Path
$python=(Resolve-Path '.venv\Scripts\python.exe').Path
& $python -m compileall -q src/booktrans/architecture_explorer
```

- 聚焦测试退出 0，67 passed，其中 31 项为 T5 工作流测试，包括 2 项实际 #80 consumer 联调。短临时目录避免 Windows 默认沙箱临时路径过长。所用 #80 consumer 的 `git hash-object` 为 `256d93bb47246bd1b012ebcd2ceff4a29ab20bdb`。
- 前轮修复前聚焦反例以 `-k latest_permission`、`-k only_exact_second`、`-k across_working`、`-k blocked_checklist` 分别执行新增或扩展的公开行为测试，退出均为 1，分别捕获发生一次 POST、普通路径被接受、跨目录重复 POST、对象错误缺失及数组 ID 抛错。本轮聚焦套件保留相关回归。
- Compileall、已跟踪和未跟踪文件空白检查均退出 0。解释器前缀和 Worktree 源码导入已核对，`pip check` 通过。
- 空白检查使用 `git diff --check 3b0da7fcb74600efa63734c3b575293e91d6f0f1` 和各未跟踪文件的 `git -c core.safecrlf=false diff --no-index --check -- NUL <path>`。本交接文件额外加 `-c core.whitespace=-blank-at-eol`，保留下方原始 diff 的空白上下文行。
- Standards 原 Finding 1、2 和 Spec 原 Finding 1–5 的修复覆盖如上，独立结论留待原 Reviewer。
- 本轮依据 Owner 要求未重跑昂贵的 Architecture Explorer 或仓库全量。前一轮完整尝试分别受沙箱/浏览器配置和缺失生产 fixture 限制。未运行未安装的类型检查器。
- 本轮验证使用隔离 GitHub adapter 与真实本地文件系统。真实 GitHub POST/PATCH、远端中断、生产宿主 pin 与真实混合批次验收待后续授权及 #84。正式位置的边界依赖宿主用户配置文件的访问控制，远端权限仍可能在检查与请求之间变化。本轮没有安装生产 Anchor。

## 原始 Git 清点及已跟踪 diff

下列四条命令均退出 0。未跟踪文件的完整变更可从上面的原始文件路径直接查看，或逐个执行 `git diff --no-index -- NUL <path>`。

```text
> git status --short
 M src/booktrans/architecture_explorer/node_review_definitions.py
 M src/booktrans/architecture_explorer/relationship_review.py
 M src/booktrans/architecture_explorer/review_source.py
 M src/booktrans/architecture_explorer/review_trust.py
?? docs/architecture-explorer/v4-ticket-05-handoff.md
?? docs/architecture-explorer/v4-ticket-05-second-confirmation-example.md
?? src/booktrans/architecture_explorer/github_review_publisher.py
?? src/booktrans/architecture_explorer/review_artifacts.py
?? src/booktrans/architecture_explorer/trusted_batch_review.py
?? src/booktrans/architecture_explorer/trusted_batch_review_cli.py
?? tests/architecture_explorer/test_v4_trusted_batch_review.py
?? tests/fixtures/architecture_explorer/
```

```text
> git diff --stat 3b0da7fcb74600efa63734c3b575293e91d6f0f1
 .../architecture_explorer/node_review_definitions.py         |  9 ++++++++-
 src/booktrans/architecture_explorer/relationship_review.py   | 12 ++++++++++--
 src/booktrans/architecture_explorer/review_source.py         |  8 +++++---
 src/booktrans/architecture_explorer/review_trust.py          |  9 +++++++++
 4 files changed, 32 insertions(+), 6 deletions(-)
```

```text
> git diff --no-ext-diff 3b0da7fcb74600efa63734c3b575293e91d6f0f1
diff --git a/src/booktrans/architecture_explorer/node_review_definitions.py b/src/booktrans/architecture_explorer/node_review_definitions.py
index 63e21f5..eda7ab2 100644
--- a/src/booktrans/architecture_explorer/node_review_definitions.py
+++ b/src/booktrans/architecture_explorer/node_review_definitions.py
@@ -407,6 +407,8 @@ def render_node_review_decision_body(decision: dict[str, Any]) -> str:
         lines.append(
             f"Rejection-Basis-SHA256: {_canonical_sha256(decision['rejection_basis'])}"
         )
+    if "batch_publication_sha256" in decision:
+        lines.append(f"Publication-Key: {decision['batch_publication_sha256']}")
     return "\n".join(lines)
 
 
@@ -490,7 +492,12 @@ def verify_node_review_v2_integrity(
         "inputs",
         "claims",
     }
-    actual_fields = set(decision)
+    actual_fields = set(decision) - {"batch_publication_sha256"}
+    publication = decision.get("batch_publication_sha256")
+    if "batch_publication_sha256" in decision and (
+        not isinstance(publication, str) or not re.fullmatch(r"[0-9a-f]{64}", publication)
+    ):
+        raise ExplorerInputError("Invalid Node Review batch publication binding")
     if (
         frozenset(actual_fields)
         not in {
diff --git a/src/booktrans/architecture_explorer/relationship_review.py b/src/booktrans/architecture_explorer/relationship_review.py
index c76786b..4e369d3 100644
--- a/src/booktrans/architecture_explorer/relationship_review.py
+++ b/src/booktrans/architecture_explorer/relationship_review.py
@@ -499,7 +499,7 @@ def write_relationship_review_packets(
 def render_relationship_review_decision_body(decision: dict[str, Any]) -> str:
     object_key = decision.get("object_key", {})
     inputs = decision.get("inputs", {})
-    return "\n".join(
+    body = "\n".join(
         (
             "ARCHITECTURE_EXPLORER_RELATIONSHIP_REVIEW_V1",
             f"Decision-ID: {decision.get('decision_id')}",
@@ -510,6 +510,9 @@ def render_relationship_review_decision_body(decision: dict[str, Any]) -> str:
             f"Reviewed-Revision: {decision.get('reviewed_source_snapshot', {}).get('revision')}",
         )
     )
+    if "batch_publication_sha256" in decision:
+        body += f"\nPublication-Key: {decision['batch_publication_sha256']}"
+    return body
 
 
 def _matching_anchor(
@@ -543,10 +546,15 @@ def verify_relationship_review_integrity(
     }
     if (
         not isinstance(decision, dict)
-        or set(decision) != expected_fields
+        or set(decision) - {"batch_publication_sha256"} != expected_fields
         or decision.get("schema") != RELATIONSHIP_REVIEW_DECISION_SCHEMA
     ):
         raise ExplorerInputError("Relationship Review Decision must use the closed schema")
+    publication = decision.get("batch_publication_sha256")
+    if "batch_publication_sha256" in decision and (
+        not isinstance(publication, str) or not re.fullmatch(r"[0-9a-f]{64}", publication)
+    ):
+        raise ExplorerInputError("Invalid Relationship Review batch publication binding")
     key = stable_review_object_key(definition["object_key"])
     if decision.get("object_key") != definition["object_key"]:
         raise ExplorerInputError(f"Relationship Review Decision object mismatch: {key}")
diff --git a/src/booktrans/architecture_explorer/review_source.py b/src/booktrans/architecture_explorer/review_source.py
index ac4493a..c39c9c0 100644
--- a/src/booktrans/architecture_explorer/review_source.py
+++ b/src/booktrans/architecture_explorer/review_source.py
@@ -137,11 +137,11 @@ def _validate_owner_admin_receipt(source: dict[str, Any]) -> None:
             and owner.get("id") == TRUSTED_OWNER_ID
             and owner.get("type") == "User"
         ),
-        "issue.id": isinstance(issue.get("id"), int),
-        "issue.number": isinstance(issue_number, int) and issue_number > 0,
+        "issue.id": type(issue.get("id")) is int and issue["id"] > 0,
+        "issue.number": type(issue_number) is int and issue_number > 0,
         "issue.html_url": issue.get("html_url") == expected_issue_url,
         "issue.repository_url": issue.get("repository_url") == expected_repository_url,
-        "comment.id": isinstance(comment_id, int) and comment_id > 0,
+        "comment.id": type(comment_id) is int and comment_id > 0,
         "comment.html_url": comment.get("html_url") == expected_comment_url,
         "comment.issue_url": comment.get("issue_url") == f"{expected_repository_url}/issues/{issue_number}",
         "comment.author": (
@@ -168,6 +168,8 @@ def _validate_owner_admin_receipt(source: dict[str, Any]) -> None:
         updated = datetime.fromisoformat(comment["updated_at"].replace("Z", "+00:00"))
     except ValueError as error:
         raise ExplorerInputError("GitHub review source timestamps are invalid") from error
+    if created.utcoffset() is None or updated.utcoffset() is None:
+        raise ExplorerInputError("GitHub review source timestamps need a timezone")
     if updated < created:
         raise ExplorerInputError("GitHub review source update time precedes creation time")
 
diff --git a/src/booktrans/architecture_explorer/review_trust.py b/src/booktrans/architecture_explorer/review_trust.py
index a32139d..0ee2dff 100644
--- a/src/booktrans/architecture_explorer/review_trust.py
+++ b/src/booktrans/architecture_explorer/review_trust.py
@@ -8,6 +8,7 @@ against an independently configured approval anchor.
 from __future__ import annotations
 
 from dataclasses import dataclass
+from pathlib import Path
 from typing import Any
 
 from .generator import ExplorerInputError
@@ -38,6 +39,14 @@ PRODUCTION_RELATIONSHIP_REVIEW_APPROVAL_ANCHORS: tuple[
     ReviewApprovalAnchor, ...
 ] = ()
 
+# Host configuration records the exact installation digest authorized by the
+# Owner's second confirmation. A repository passed to a CLI cannot set this.
+PRODUCTION_BATCH_INSTALLATION_DIGESTS: frozenset[str] = frozenset()
+
+# Per-owner host state shared by every local Worktree. Neither the selected
+# repository nor its temporary directory controls this location.
+HOST_REVIEW_STATE_DIRECTORY = Path.home() / ".booktrans" / "architecture-explorer"
+
 
 def production_relationship_review_trust_policy() -> ReviewTrustPolicy:
     return ReviewTrustPolicy(PRODUCTION_RELATIONSHIP_REVIEW_APPROVAL_ANCHORS)
```

```text
> git ls-files --others --exclude-standard
docs/architecture-explorer/v4-ticket-05-handoff.md
docs/architecture-explorer/v4-ticket-05-second-confirmation-example.md
src/booktrans/architecture_explorer/github_review_publisher.py
src/booktrans/architecture_explorer/review_artifacts.py
src/booktrans/architecture_explorer/trusted_batch_review.py
src/booktrans/architecture_explorer/trusted_batch_review_cli.py
tests/architecture_explorer/test_v4_trusted_batch_review.py
tests/fixtures/architecture_explorer/v4_ticket_05/handoff.v1.json
```
