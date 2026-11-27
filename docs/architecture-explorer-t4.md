# Architecture Explorer T4：可信下钻与导航

T4 在 T3 已审核关系之上增加一层纯展示导航：从“整体架构”进入“生产调度与控制”，查看有限的候选逻辑职责，并从模块右键菜单或关系线进入同一份 T3 关系证据。T4 不重新扫描生产源码，不创建第二份关系事实，也不实现 T5 的源码变化检测。

## 数据边界

- `architecture-explorer/navigation.v1.json` 只保存导航层级、候选职责、源码归属核验状态和现有关系 ID 引用。
- `apply_navigation` 对这份数据做封闭校验：T4 只允许一个 `production-control` 下钻；内部职责必须保持 `candidate`；部分核验必须引用已存在的 T3 关系；未核验项不能声称关系证据。
- T3 的 `architecture.relationships` 对象不会被复制或改写。右键入口和关系线都按同一个稳定 Relationship ID 查找并调用同一详情渲染函数。
- 第二层明确声明没有完成全部源码覆盖。调用关系只支持“启动边界存在”这一关系事实，不能自动把源码归属提升为已审核架构。
- 历史与辅助区默认收起。证据不足的内容固定显示“待归类”，不根据文件名推断停用、实验或历史状态。

## 交互不变量

- 普通节点激活（左键、Enter、Space）按当前 View 的 Appearance 处理。中心节点、未声明主要操作的叶节点，以及目标为当前 View 或导航路径中祖先 View 的 Appearance 打开对象详情，保留当前路径。其余有效主要操作按该 Appearance 的声明进入目标 View 或 Source Trail。同一 Node 在不同 View 中可以有不同操作。
- 普通激活进入架构 View，且目标页主标题与发起节点名称不一致时，卡片的可见导航提示仅显示「进入相关架构视图」。提示使用淡樱花粉 `#E9A4BC`，保持简洁、低视觉权重。目标页继续以中心节点为主标题，以有区分价值的 View 名称为副标题，面包屑使用 View 名称。
- 被普通激活拦截的已声明操作可以在对象详情中显式执行。外部 View 入栈，祖先 View 复用原路径及返回焦点，当前 View 无需再次进入。Source Trail 使用声明的 Evidence，关闭时恢复发起操作的焦点。这些规则只约束导航，合法循环协作关系仍可展示。
- 面包屑只读；“返回上一级”和“返回最高级”是两个独立动作，并在历史与辅助区之后分别位于页面底部左右两侧。
- 模块右键打开“查看关系与源码证据”菜单；菜单项执行后关闭菜单。
- Esc 关闭菜单。菜单外的第一次点击只关闭菜单，不能同时触发模块下钻或其他页面动作。
- 流程高亮使用暖金色主边框和向白色背景自然消散的 LED 灯带式外发光，只改变样式，不改变左键、右键、详情或导航含义。
- 架构图不得出现交叉连线。优先调整节点排列或连线路径。若无法在合理范围内消除，记录涉及的 View、关系和视口并报告 Owner。保留全部真实关系和架构语义，沿用现有布局机制。
- 源码快照默认折叠，整张卡片可点击展开或收起，同时保留原生键盘操作；选择或复制卡片文字时不会主动折叠，展开后仍显示全部原始快照字段。
- 「返回上一级」和「返回最高级」的默认颜色为 `#99546D`，与卡片提示颜色独立。保留原有背景、边框、悬停、键盘焦点反馈、位置和导航规则。

Owner 在 Ticket #85 的最终人工验收中接受淡樱花粉卡片提示在白底上约 `2.00:1` 的低对比度限制。该提示未达到 WCAG AA 的小字号文字对比度要求。返回按钮默认图形在浅粉底上的实测对比度为 `5.08:1`。

交互状态位于 `architecture-explorer/web/t4-state.js`，作为浏览器和 Node 共用的纯 reducer；浏览器事件接线位于 `app.js`。

## 离线构建

使用项目解释器和当前 worktree 的 `src`：

```powershell
$python = (Resolve-Path 'D:\translate\.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $python scripts\architecture_explorer\generate_top_level.py scan `
  --repository . --source-file pyproject.toml `
  --output output\architecture-explorer\t4-site\snapshot.json
& $python scripts\architecture_explorer\generate_reviewed_relationship.py build `
  --repository . `
  --snapshot output\architecture-explorer\t4-site\snapshot.json `
  --mapping architecture-explorer\modules.json `
  --candidate architecture-explorer\review-inputs\supervisor-worker-857a4b8.json `
  --review-mapping architecture-explorer\reviews\supervisor-worker.v1.json `
  --review-source architecture-explorer\reviews\supervisor-worker.source.v1.json `
  --review-state output\architecture-explorer\t5-lite\review-state.v1.json `
  --navigation architecture-explorer\navigation.v1.json `
  --web-assets architecture-explorer\web `
  --output-directory output\architecture-explorer\t4-site\site
```

直接打开 `output/architecture-explorer/t4-site/site/index.html`。页面仍为纯离线静态站点，不需要服务器、网络、数据库或 AI 服务。
