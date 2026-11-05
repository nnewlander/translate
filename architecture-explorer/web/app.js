(() => {
  "use strict";

  const data = window.ARCHITECTURE_EXPLORER_DATA;
  const stateApi = window.ARCHITECTURE_EXPLORER_STATE;
  const error = document.querySelector("#load-error");
  if (!data || !data.architecture || !Array.isArray(data.architecture.modules) || !stateApi) {
    error.hidden = false;
    error.textContent = "Explorer 数据缺失或格式无效。请重新生成离线站点。";
    return;
  }

  const coverageLabels = { unverified: "源码覆盖：未核验", partial: "源码覆盖：部分核验", verified: "源码覆盖：已核验" };
  const reviewLabels = { candidate: "候选", "historical-reviewed": "历史审核", "current-reviewed": "当前已审核", "needs-review": "需要复核", unknown: "未知" };
  const assignmentLabels = { unverified: "源码归属：未核验", partial: "源码归属：部分核验" };
  const modules = data.architecture.modules;
  const relationships = Array.isArray(data.architecture.relationships) ? data.architecture.relationships : [];
  const navigation = data.navigation || {
    root: { id: "top-level", name: "整体架构" }, drilldowns: [],
    history_and_auxiliary: { title: "历史与辅助区", description: "暂无导航数据。", default_expanded: false, items: [] },
  };
  const drilldownByParent = Object.fromEntries(navigation.drilldowns.map((item) => [item.parent_module_id, item]));
  const relationshipById = Object.fromEntries(relationships.map((item) => [item.id, item]));
  const childById = Object.fromEntries(navigation.drilldowns.flatMap((item) => item.children).map((item) => [item.id, item]));
  const moduleById = Object.fromEntries([...modules, ...Object.values(childById)].map((item) => [item.id, item]));
  let state = stateApi.initialState();
  let suppressNextClick = false;

  const grid = document.querySelector("#module-grid");
  const moduleCount = document.querySelector("#module-count");
  const modulesHeading = document.querySelector("#modules-heading");
  const heroTitle = document.querySelector(".hero h1");
  const heroLede = document.querySelector(".hero .lede");
  const breadcrumb = document.querySelector("#breadcrumb");
  const bottomNavigation = document.querySelector("#bottom-navigation");
  const backButton = document.querySelector("#back-button");
  const homeButton = document.querySelector("#home-button");
  const relationList = document.querySelector("#relationship-list");
  const relationshipCount = document.querySelector("#relationship-count");
  const dialog = document.querySelector("#relationship-detail");
  const detailContent = document.querySelector("#detail-content");
  const contextMenu = document.querySelector("#module-context-menu");
  const contextEvidence = document.querySelector("#context-evidence");
  const snapshotDetails = document.querySelector("#source-snapshot");
  const sourceFactsDetails = document.querySelector("#source-facts");
  const sourceFactsCount = document.querySelector("#source-facts-count");
  const sourceModuleSelect = document.querySelector("#source-module-select");
  const sourceModuleDetail = document.querySelector("#source-module-detail");

  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    element.textContent = value;
    if (className) element.className = className;
    return element;
  };

  const metadata = document.querySelector("#snapshot-metadata");
  for (const [term, description] of [
    ["Revision", data.snapshot.revision],
    ["工作区", data.snapshot.worktree.state === "clean" ? "干净" : "有未提交变更"],
    ["扫描器", data.snapshot.scanner_version],
    ["扫描时间", data.snapshot.scanned_at],
    ["确定性摘要", data.deterministic_content_sha256],
  ]) metadata.append(text("dt", term), text("dd", description));
  const hasSelectedSnapshotText = () => {
    const selection = window.getSelection?.();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return false;
    return snapshotDetails.contains(selection.getRangeAt(0).commonAncestorContainer);
  };
  snapshotDetails.addEventListener("click", (event) => {
    if (event.target.closest?.("summary")) event.preventDefault();
    snapshotDetails.open = stateApi.nextSnapshotOpen(snapshotDetails.open, {
      isMouseActivation: event.detail > 0,
      hasSelectedText: hasSelectedSnapshotText(),
    });
  });

  const copyLocation = async (location, button) => {
    try { await navigator.clipboard.writeText(location); }
    catch (_) {
      const area = document.createElement("textarea");
      area.value = location; area.style.position = "fixed"; area.style.opacity = "0";
      document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
    }
    button.textContent = "已复制";
    window.setTimeout(() => { button.textContent = "复制定位信息"; }, 1200);
  };

  const expandable = (label, className = "technical-details") => {
    const details = document.createElement("details");
    details.className = className; details.append(text("summary", label)); return details;
  };

  const sourceImportExplanation = (module, item) => {
    const importedModule = `${".".repeat(item.level)}${item.module}`;
    const importedNames = item.names.join("、");
    if (item.kind === "from") {
      return `“${module.path}”声明从“${importedModule}”直接引入“${importedNames}”，也就是当前文件点名要使用这些符号。静态扫描能确认这条引用写在源码中，但不能证明运行时一定执行到这里，也不能据此判断业务职责或架构归属。`;
    }
    return `“${module.path}”声明直接引用模块“${item.module}”，相当于在当前文件的工具清单中点名了它。静态扫描能确认引用存在，但不能证明运行时一定执行到这里，也不能据此判断业务职责或架构归属。`;
  };

  const sourceEntryExplanation = (module, item) => {
    if (item.kind === "declarative_cli") {
      return `“${item.symbol}”通过 Typer 声明为命令行应用入口，可以把它理解为接收命令的服务台。这个声明说明命令可以从这里组织和转交，但不证明任何命令已经运行。`;
    }
    if (item.kind === "package_entry") {
      const packageName = module.module_id.replace(/\.__main__$/, "");
      return `当用户使用“python -m ${packageName}”按包方式启动时，Python 会查找“${module.path}”。这能确认启动机制会先来到这里；之后具体动作以源码证据为准。`;
    }
    if (item.kind === "deterministic_worker_wrapper") {
      return `这个包装入口在文件被直接运行时，把执行交给“booktrans.production_worker.worker.main”，并把结果作为进程退出状态。它证明静态启动路径存在，不代表 Worker 在某次运行中真的启动。`;
    }
    if (item.kind === "main_guard") {
      return `条件“__name__ == "__main__"”像一道门禁：只有“${module.path}”被直接运行时才会进入；如果文件只是被其他代码 import，它不会因此启动。`;
    }
    return `扫描在“${module.path}”中找到了“${item.kind}”入口事实。它只描述源码里可见的启动结构，不证明运行已经发生。`;
  };

  const sourceEvidence = (fact, label, explanation) => {
    const evidence = fact.evidence;
    const location = `${evidence.path}:${evidence.current_location.line_start} · symbol: ${evidence.symbol}`;
    const card = document.createElement("article"); card.className = "source-fact-card";
    const traceSubject = evidence.evidence_kind === "direct_import" ? "这条引用" : "这个入口";
    card.append(
      text("h4", label),
      text("p", explanation, "source-fact-explanation"),
      text("p", `源码位置“${evidence.path}:${evidence.current_location.line_start}”把${traceSubject}定位到当前文件；Evidence ID 像这条扫描记录的收据号，便于之后引用并核对同一项证据。`, "source-evidence-trace")
    );
    const details = expandable("查看定位和原始证据", "source-fact-evidence");
    const facts = document.createElement("dl"); facts.className = "raw-evidence-facts";
    for (const [term, value] of [
      ["Evidence ID", evidence.evidence_id], ["源码定位", location], ["事实种类", evidence.evidence_kind],
    ]) facts.append(text("dt", term), text("dd", value));
    const button = text("button", "复制定位信息", "copy-location"); button.type = "button";
    button.addEventListener("click", () => copyLocation(location, button));
    details.append(facts, text("pre", evidence.snippet), button); card.append(details); return card;
  };

  const renderSourceModule = () => {
    const sourceFacts = data.source_facts;
    const module = sourceFacts?.modules?.[Number(sourceModuleSelect.value)];
    sourceModuleDetail.replaceChildren();
    if (!module) { sourceModuleDetail.append(text("p", "当前范围内没有可展示的 Python 源码事实。", "empty-evidence")); return; }
    const identity = document.createElement("dl"); identity.className = "source-module-identity";
    for (const [term, value] of [
      ["模块身份", module.module_id], ["仓库相对路径", module.path], ["文件 SHA-256", module.file_sha256],
    ]) identity.append(text("dt", term), text("dd", value));
    sourceModuleDetail.append(
      identity,
      text(
        "p",
        `扫描把仓库文件“${module.path}”记录为 Python 模块“${module.module_id}”。路径用于找到原文件，SHA-256 是本次内容的指纹，方便以后核对是否仍是同一版；这些身份信息不表示它属于某个架构模块。`,
        "source-module-trace"
      )
    );
    const imports = document.createElement("section"); imports.append(text("h3", `顶层直接 import（${module.direct_imports.length}）`));
    if (module.direct_imports.length) {
      for (const item of module.direct_imports) {
        const imported = item.kind === "from" ? `${".".repeat(item.level)}${item.module} → ${item.names.join(", ")}` : item.module;
        imports.append(sourceEvidence(item, imported, sourceImportExplanation(module, item)));
      }
    } else imports.append(text("p", `扫描范围内没有记录到顶层直接 import。这只说明“${module.path}”没有此类静态记录，不代表它运行时不会通过其他机制取得依赖。`, "empty-evidence"));
    const entries = document.createElement("section"); entries.append(text("h3", `明确入口事实（${module.entries.length}）`));
    if (module.entries.length) {
      for (const item of module.entries) entries.append(sourceEvidence(item, `${item.kind} · ${item.symbol}`, sourceEntryExplanation(module, item)));
    } else entries.append(text("p", `扫描范围内没有记录到明确入口。这不等于文件没有用途，也不能据此判断业务职责；其他启动机制仍然未知。`, "empty-evidence"));
    sourceModuleDetail.append(imports, entries);
  };

  const initializeSourceFacts = () => {
    const sourceFacts = data.source_facts;
    if (!sourceFacts || !Array.isArray(sourceFacts.modules)) { sourceFactsDetails.hidden = true; return; }
    sourceFactsCount.textContent = `${sourceFacts.scope.files_scanned} 个文件 · 默认收起`;
    sourceFacts.modules.forEach((module, index) => {
      const option = document.createElement("option"); option.value = String(index); option.textContent = module.path; sourceModuleSelect.append(option);
    });
    sourceModuleSelect.addEventListener("change", renderSourceModule); renderSourceModule();
  };

  const evidencePlainExplanation = (evidence) => {
    const explanations = {
      worker_script_binding: `这像调度员先在通讯录里记下准确联系人：变量指向“scripts/production/run_qwen_deterministic_worker.py”，让后续启动知道要找哪个包装脚本。静态源码只证明目标路径被绑定，不证明脚本已经启动。`,
      process_boundary_definition: `这像一套写好的“发车流程”：函数准备可执行文件、参数、工作目录和环境，收集输出，并定义等待子进程结束的步骤。它说明 Supervisor 具备这条独立进程边界，但不表示流程在某次运行中真的走过。`,
      production_argument_vector: `这组参数像一张派工单：它写明“--mode production”、生产写入授权、worker-id，以及当前 batch-label 和 checkpoint。静态源码能确认派工单如何组装，不能确认它在真实运行中已经发出或被接受。`,
      process_boundary_invocation: `这一行像把解释器、派工单和工作目录一起递到启动窗口“Start-DeterministicWorkerProcess”。它把前面的参数组装连接到进程边界；静态源码只证明存在这次调用。`,
      worker_wrapper_import: `包装脚本通过这条 import 找到实际执行入口“booktrans.production_worker.worker.main”，像从总机转到真正接手工作的人员。它只证明入口引用关系，尚未证明 main 被调用。`,
      worker_wrapper_main_call: `条件“__name__ == "__main__"”像门禁：只有包装脚本被直接运行时，才把工作交给 main，并把结果作为退出状态。它证明静态转交条件，不代表某次运行真的进入过这里。`,
      worker_mode_contract: `这里像派工单上的模式选项：Worker 只接受 canary 或 production，且默认是 canary。它证明 production 是正式参数值，但不证明真实启动选择了 production。`,
      worker_authorization_contract: `这里像派工单上的生产写入授权栏：“--authorize-production-checkpoint-write”出现时才记为开启。它证明 Worker 能识别这张通行证，不证明授权在某次运行中实际提供或有效。`,
      worker_argument_parse: `这一步像 Worker 收到派工单后先拆单，把 argv 解析为后续条件检查使用的 args。它把外部参数接入控制流程，但静态源码不能证明真实参数已经到达。`,
      worker_canary_exit_guard: `这是一道分流闸门：如果 mode 是 canary，就走隔离的 Canary 路径并在这里返回，不继续经过后面的生产守卫。它说明生产路径位于该分支之后，不证明某次运行选择了哪一边。`,
      worker_id_guard: `这像开工前检查工牌：没有 worker-id 就立即停止，不能继续进入生产路径。源码证明这项必填条件存在，但没有提供任何一次运行的检查结果。`,
      worker_batch_checkpoint_guard: `这像开工前核对成套派工信息：batch-label 和 checkpoint 必须一起提供，少一个就停止。它证明批次与检查点之间有配对守卫，不证明某次启动已经通过。`,
      worker_production_branch: `这是静态证据链最后的交接门：通过前面的分支和守卫后，main 把已解析参数交给“run_production_checkpoint”。它证明调用在条件满足时可达，不等于真实生产 Checkpoint 已经执行。`,
    };
    return explanations[evidence.evidence_kind] || null;
  };

  const evidenceCard = (evidence, guide) => {
    const location = `${evidence.path}:${evidence.current_location.line_start} · symbol: ${evidence.symbol}`;
    const card = document.createElement("article"); card.className = "evidence-card"; card.dataset.evidenceKind = evidence.evidence_kind;
    card.append(
      text("p", evidence.source_role === "production" ? "生产源码证据" : "辅助测试证据 · 非生产调用事实", "evidence-role"),
      text("h4", guide.name), text("p", guide.explanation, "evidence-explanation"),
      text("p", guide.supports, "evidence-supports")
    );
    const plainExplanation = evidence.source_role === "production" ? evidencePlainExplanation(evidence) : null;
    if (plainExplanation) card.append(text("p", `通俗理解：${plainExplanation}`, "evidence-plain-explanation"));
    const raw = expandable("展开原始技术证据", "evidence-technical");
    const facts = document.createElement("dl"); facts.className = "raw-evidence-facts";
    for (const [term, value] of [["技术种类", evidence.evidence_kind], ["Evidence ID", evidence.evidence_id], ["源码定位", location]]) {
      facts.append(text("dt", term), text("dd", value));
    }
    const button = text("button", "复制定位信息", "copy-location"); button.type = "button";
    button.addEventListener("click", () => copyLocation(location, button));
    raw.append(facts, text("pre", evidence.snippet), button); card.append(raw); return card;
  };

  const openDialog = () => {
    if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
  };

  const relationshipPlainExplanation = (relation, guide) => {
    if (relation.id !== "relationship:production-control:launches:translation-execution") return null;
    return `可以把“${guide.source_module_name}”看作调度员：它准备启动条件并发起任务；“${guide.target_module_name}”像接手具体工作的工人，接收参数后进入执行流程。这样调度和执行可以各自处理自己的步骤；现有证据只证明代码中存在这条有条件的协作路径，不代表某次运行真的启动过 Worker。`;
  };

  const modulePlainExplanations = {
    "document-understanding": "可以把它看作档案整理员：把不可改的原书整理成每段都能准确找到的工作底稿，同时防止原文和位置被意外改乱。",
    "scope-and-request": "可以把它看作派单前的选单员：确认这次处理哪些内容，并把范围、上下文和依据一起装进请求。",
    "translation-execution": "可以把它看作翻译窗口：把已批准的请求交给合适的模型接口，收回候选译文，也能接着已有原始回复继续。",
    "quality-and-repair": "可以把它看作质检员和受限返修台：先按规则查问题，再只在合同允许范围内修复，并保留每一版的来历。",
    "production-evidence": "可以把它看作带封条的工作记录柜：保存并核对每一步生产事实，在 Checkpoint 封存可追溯证据。",
    "production-control": "可以把它看作现场调度员：依据状态决定继续、恢复或停下，并确保同一批次只有一个写入者。",
    "candidate-and-release": "可以把它看作装订与出厂检查环节：把获批译文放入候选文档，再确认包装内容和发布边界没有被意外改变。",
  };

  const showRelationship = (relation) => {
    const guide = relation.presentation_zh;
    detailContent.replaceChildren(); detailContent.dataset.relationshipId = relation.id;
    const title = text("h2", `${guide.source_module_name} → ${guide.target_module_name}`, "detail-title"); title.id = "detail-title";
    detailContent.append(
      text("p", "架构关系解读", "eyebrow"),
      title,
      text("p", guide.relationship_name, "plain-relation-name"), text("p", guide.summary, "relationship-summary")
    );
    const plainExplanation = relationshipPlainExplanation(relation, guide);
    if (plainExplanation) detailContent.append(text("p", plainExplanation, "plain-collaboration-explanation"));
    const statusGrid = document.createElement("section"); statusGrid.className = "meaning-grid";
    for (const [title, description] of [
      [reviewLabels[relation.review_status] || "未知", guide.status_explanations[relation.review_status] || "当前没有可用的有效审核结论。"],
      ["静态条件可达", guide.status_explanations[relation.code_reachability.status]],
      ["真实运行状态未采集", guide.status_explanations[relation.runtime_state]],
    ]) { const item = document.createElement("article"); item.append(text("h3", title), text("p", description)); statusGrid.append(item); }
    detailContent.append(statusGrid);
    const limits = document.createElement("aside"); limits.className = "evidence-limits"; limits.append(text("h3", "这条关系不能证明什么"));
    const limitList = document.createElement("ul"); for (const item of guide.limits) limitList.append(text("li", item));
    limits.append(limitList); detailContent.append(limits, text("h3", "关键证据链"));
    const evidenceById = Object.fromEntries(relation.evidence.map((item) => [item.evidence_id, item]));
    for (const group of guide.evidence_groups) {
      const section = document.createElement("section"); section.className = "evidence-group";
      section.append(text("h4", group.title), text("p", group.description, "group-description"));
      for (const evidenceId of group.evidence_ids) section.append(evidenceCard(evidenceById[evidenceId], guide.evidence_items[evidenceId]));
      detailContent.append(section);
    }
    if (relation.supporting_evidence.length) {
      const auxiliary = expandable("辅助测试证据（3 条，非生产调用事实）", "auxiliary-evidence");
      auxiliary.append(text("p", "这些测试只提供支持性上下文，不属于 Owner 批准的 13 条生产调用证据，也不能单独证明生产启动发生。", "group-description"));
      for (const evidence of relation.supporting_evidence) auxiliary.append(evidenceCard(evidence, guide.evidence_items[evidence.evidence_id]));
      detailContent.append(auxiliary);
    }
    detailContent.append(text("h3", "静态条件"));
    const conditions = document.createElement("ul"); for (const item of guide.static_conditions) conditions.append(text("li", item));
    detailContent.append(conditions, text("h3", "仍然未知的事项"));
    const unknowns = document.createElement("ul"); for (const item of guide.retained_unknowns) unknowns.append(text("li", item)); detailContent.append(unknowns);
    const original = expandable("原始审核与英文技术字段", "original-review");
    const facts = document.createElement("dl"); facts.className = "detail-facts";
    for (const [term, value] of [
      ["Relationship ID", relation.id], ["Direction", relation.direction], ["Relationship type", relation.type],
      ["Review status", relation.review_status], ["Code reachability", relation.code_reachability.status],
      ["Runtime state", relation.runtime_state], ["Evidence continuity", relation.source_continuity.status],
    ]) facts.append(text("dt", term), text("dd", value));
    original.append(facts, text("h4", "原始 Owner 审核文字"), text("pre", relation.architecture_explanation || "—", "review-notes"));
    detailContent.append(original); openDialog();
  };

  const relationshipsForModule = (moduleId) => {
    const childReferences = childById[moduleId]?.source_assignment?.relationship_ids || [];
    const direct = relationships.filter((item) => item.source_module === moduleId || item.target_module === moduleId).map((item) => item.id);
    return [...new Set([...childReferences, ...direct])].map((id) => relationshipById[id]).filter(Boolean);
  };

  const showModuleDetail = (module) => {
    const isCandidate = module.review_status === "candidate";
    const related = relationshipsForModule(module.id);
    detailContent.replaceChildren(); delete detailContent.dataset.relationshipId;
    const title = text("h2", module.name, "detail-title"); title.id = "detail-title";
    detailContent.append(text("p", isCandidate ? "候选逻辑职责" : "模块详情", "eyebrow"), title, text("p", module.responsibility, "relationship-summary"));
    const statuses = document.createElement("div"); statuses.className = "statuses detail-statuses";
    statuses.append(text("span", isCandidate ? "职责状态：候选" : "逻辑定义：已批准", `status ${isCandidate ? "candidate" : "approved"}`));
    if (isCandidate) {
      statuses.append(text("span", assignmentLabels[module.source_assignment.status], `status coverage-${module.source_assignment.status}`));
      detailContent.append(statuses, text("p", module.source_assignment.note, "candidate-warning"));
    } else {
      statuses.append(text("span", coverageLabels[module.source_coverage.status], `status coverage-${module.source_coverage.status}`)); detailContent.append(statuses);
    }
    detailContent.append(text("h3", "关系与源码证据"));
    if (related.length) {
      const list = document.createElement("div"); list.className = "module-evidence-links";
      for (const relation of related) {
        const button = text("button", `打开：${relation.presentation_zh.relationship_name}`, "evidence-link");
        button.type = "button"; button.dataset.relationshipId = relation.id; button.addEventListener("click", () => showRelationship(relation)); list.append(button);
      }
      detailContent.append(list);
    } else detailContent.append(text("p", "当前发布数据中没有与此模块直接关联的关系证据；这不等于模块没有关系。", "empty-evidence"));
    openDialog();
  };

  const dispatch = (action) => { state = stateApi.reduce(state, action); };
  const currentDrilldown = () => drilldownByParent[state.parentModuleId] || null;

  const renderNavigation = () => {
    const drilldown = currentDrilldown(); const atTop = !drilldown;
    breadcrumb.replaceChildren(); breadcrumb.append(text("span", navigation.root.name, "crumb"));
    if (drilldown) breadcrumb.append(text("span", "›", "crumb-separator"), text("span", drilldown.name, "crumb current"));
    bottomNavigation.hidden = atTop;
    heroTitle.textContent = atTop ? "顶层架构" : drilldown.name;
    heroLede.textContent = atTop ? "已批准的逻辑职责，与源码覆盖核验分别呈现。" : drilldown.coverage_note;
    modulesHeading.textContent = atTop ? "七个顶层模块" : "候选逻辑职责";
  };

  const cardFor = (module, index, isChild) => {
    const article = document.createElement("article"); article.className = "module-card interactive-module";
    article.dataset.moduleId = module.id; article.tabIndex = 0; article.setAttribute("role", "button");
    const hasChildren = Boolean(drilldownByParent[module.id]);
    article.setAttribute("aria-label", `${module.name}，${hasChildren ? "左键下钻" : "左键打开详情"}，右键查看关系与源码证据`);
    article.append(text("span", String(index + 1).padStart(2, "0"), "ordinal"), text("h3", module.name), text("p", module.responsibility, "module-responsibility"));
    if (!isChild && modulePlainExplanations[module.id]) article.append(text("p", modulePlainExplanations[module.id], "module-plain-explanation"));
    const statuses = document.createElement("div"); statuses.className = "statuses";
    if (isChild) statuses.append(text("span", "职责状态：候选", "status candidate"), text("span", assignmentLabels[module.source_assignment.status], `status coverage-${module.source_assignment.status}`));
    else statuses.append(text("span", "逻辑定义：已批准", "status approved"), text("span", coverageLabels[module.source_coverage.status] || "源码覆盖：未知", `status coverage-${module.source_coverage.status}`));
    article.append(statuses);
    const activate = () => {
      dispatch({ type: "MODULE_PRIMARY", moduleId: module.id, hasChildren }); render();
      if (state.detailModuleId) showModuleDetail(moduleById[state.detailModuleId]);
    };
    article.addEventListener("click", activate);
    article.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } });
    article.addEventListener("contextmenu", (event) => {
      event.preventDefault(); dispatch({ type: "CONTEXT_OPEN", moduleId: module.id, x: event.clientX, y: event.clientY }); renderContextMenu();
    });
    return article;
  };

  const renderModules = () => {
    const drilldown = currentDrilldown(); const visible = drilldown ? drilldown.children : modules;
    grid.replaceChildren(); visible.forEach((module, index) => grid.append(cardFor(module, index, Boolean(drilldown))));
    moduleCount.textContent = `${visible.length} 个${drilldown ? "候选职责" : "模块"}`;
  };

  const renderRelationships = () => {
    relationList.replaceChildren(); relationshipCount.textContent = `${relationships.length} 条关系`;
    for (const relation of relationships) {
      const guide = relation.presentation_zh; const button = document.createElement("button"); button.type = "button";
      button.className = `relationship-line review-${relation.review_status}`; button.dataset.relationshipId = relation.id;
      const arrow = text("span", "→", "relation-arrow"); arrow.setAttribute("aria-hidden", "true");
      button.append(text("span", guide.source_module_name, "relation-node"), arrow, text("span", guide.target_module_name, "relation-node"), text("span", `${guide.relationship_name} · ${relation.type}`, "relation-type"), text("span", reviewLabels[relation.review_status] || "未知", "relation-status"));
      button.setAttribute("aria-label", `${guide.source_module_name}到${guide.target_module_name}，打开同一关系证据详情`);
      button.addEventListener("click", () => { dispatch({ type: "RELATIONSHIP_PRIMARY", relationshipId: relation.id }); showRelationship(relationshipById[state.relationshipId]); });
      relationList.append(button);
    }
  };

  const renderContextMenu = () => {
    if (!state.contextMenu) { contextMenu.hidden = true; return; }
    contextMenu.hidden = false; contextMenu.dataset.moduleId = state.contextMenu.moduleId;
    const maxLeft = Math.max(8, window.innerWidth - contextMenu.offsetWidth - 8);
    const maxTop = Math.max(8, window.innerHeight - contextMenu.offsetHeight - 8);
    contextMenu.style.left = `${Math.min(state.contextMenu.x, maxLeft)}px`; contextMenu.style.top = `${Math.min(state.contextMenu.y, maxTop)}px`;
    contextEvidence.focus({ preventScroll: true });
  };
  const render = () => { renderNavigation(); renderModules(); renderContextMenu(); };

  backButton.addEventListener("click", () => { dispatch({ type: "BACK" }); render(); });
  homeButton.addEventListener("click", () => { dispatch({ type: "HOME" }); render(); });
  contextEvidence.addEventListener("click", () => {
    const module = moduleById[state.contextMenu?.moduleId]; if (!module) return;
    const related = relationshipsForModule(module.id); dispatch({ type: "MENU_EVIDENCE", relationshipId: related[0]?.id || null }); renderContextMenu();
    if (state.relationshipId) showRelationship(relationshipById[state.relationshipId]); else showModuleDetail(module);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.contextMenu) { event.preventDefault(); dispatch({ type: "ESCAPE" }); renderContextMenu(); }
  });
  document.addEventListener("pointerdown", (event) => {
    if (state.contextMenu && !contextMenu.contains(event.target)) {
      suppressNextClick = true; dispatch({ type: "OUTSIDE_POINTER" }); renderContextMenu(); event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener("click", (event) => {
    if (suppressNextClick) { suppressNextClick = false; event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  dialog.addEventListener("close", () => dispatch({ type: "DETAIL_CLOSED" }));

  const history = navigation.history_and_auxiliary;
  const historyDetails = document.querySelector("#history-and-auxiliary"); historyDetails.open = Boolean(history.default_expanded);
  document.querySelector("#history-title").textContent = history.title;
  const historyContent = document.querySelector("#history-content"); historyContent.append(text("p", history.description, "history-description"));
  for (const item of history.items) {
    const card = document.createElement("article"); card.className = "history-item";
    card.append(text("span", item.classification === "unclassified" ? "待归类" : item.classification, "status unclassified"), text("h3", item.name), text("p", item.note)); historyContent.append(card);
  }

  const workflowToggle = document.querySelector("#workflow-toggle");
  workflowToggle.addEventListener("click", () => {
    const active = workflowToggle.getAttribute("aria-pressed") !== "true";
    workflowToggle.setAttribute("aria-pressed", String(active)); document.body.classList.toggle("workflow-highlight", active);
    workflowToggle.textContent = active ? "取消流程高亮" : "高亮最小生产流程";
  });

  initializeSourceFacts(); renderRelationships(); render();
})();
