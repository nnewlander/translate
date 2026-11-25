(() => {
  "use strict";

  const data = window.ARCHITECTURE_EXPLORER_DATA;
  const stateApi = window.ARCHITECTURE_EXPLORER_STATE;
  const error = document.querySelector("#load-error");
  if (!data || !data.view_projection || !stateApi) {
    error.hidden = false;
    error.textContent = "Explorer 数据缺失或格式无效。请重新生成离线站点。";
    return;
  }

  const coverageLabels = { unverified: "源码覆盖：未核验", partial: "源码覆盖：部分核验", verified: "源码覆盖：已核验" };
  const assignmentLabels = { unverified: "源码归属：未核验", partial: "源码归属：部分核验" };
  const nodeReviewLabels = {
    "Pending Review": "待审核",
    "Current · Approved": "已批准 · 当前有效",
    "Current · Rejected": "已拒绝 · 当前有效",
    "Historical · Approved": "曾批准 · 需复核",
    "Historical · Rejected": "曾拒绝 · 需复核",
    Unknown: "审核有效性未知",
  };
  const projectedReviewLabel = (review) => {
    if (review?.result === "Pending" && review?.state === "not-reviewed") return "未审核";
    if (review?.result === "APPROVED" && review?.state === "current") return "已批准 · 当前有效";
    if (review?.result === "REJECTED" && review?.state === "current") return "已拒绝 · 当前有效";
    if (review?.state === "needs-review") return `${review.result} · 需要复核`;
    return `${review?.result || "Pending"} · ${review?.state || "unknown"}`;
  };
  const projectedReviewStatus = (prefix, review) => review?.result === "Pending" && review?.state === "not-reviewed"
    ? `${prefix}状态：未审核`
    : `${prefix}审核：${projectedReviewLabel(review)}`;
  const projectedReviewClass = (review) => {
    if (review?.result === "APPROVED" && review?.state === "current") return "approved";
    if (review?.result === "REJECTED" && review?.state === "current") return "rejected";
    if (review?.state === "needs-review") return "needs-review";
    if (review?.state === "unknown") return "unknown";
    return "pending";
  };
  const approvedScopeLabel = (review) => {
    const scopes = review?.approved_scope;
    if (!Array.isArray(scopes) || !scopes.length) return "无";
    return scopes.map((scope) => {
      if (Array.isArray(scope?.evidence_ids)) return `${scope.evidence_ids.length} 条 Evidence · ${scope.decision_id}`;
      if (scope?.legacy_claim_kind) return `历史 Claim：${scope.legacy_claim_kind}`;
      if (scope?.legacy_review_scope) return `历史范围：${scope.legacy_review_scope}`;
      return "结构化批准范围";
    }).join("；");
  };
  const navigation = data.navigation || {
    root: { id: "top-level", name: "整体架构" }, drilldowns: [],
    history_and_auxiliary: { title: "历史与辅助区", description: "暂无导航数据。", default_expanded: false, items: [] },
  };
  const projection = data.view_projection;
  const nodeReviewFor = (module) => module.review_detail || null;
  const viewById = projection.view_index || {};
  const rootView = viewById[projection.root_view_id];
  const moduleById = projection.node_index || {};
  const relationshipRecordById = projection.relationship_index || {};
  const relationships = Object.entries(projection.relationship_index || {})
    .filter(([, relationship]) => relationship.semantic === "formal")
    .map(([, relationship]) => relationship);
  const relationshipById = Object.fromEntries(relationships.map((item) => [item.id, item]));
  const childById = Object.fromEntries(
    Object.entries(moduleById).filter(([, item]) => item?.object_key?.kind === "candidate-node"),
  );
  const modules = (rootView?.appearances || []).map((appearance) => moduleById[appearance.node_id]);
  const projectedEvidenceById = projection.evidence_index || {};
  const evidenceRecords = (ids, field) => (ids || [])
    .map((evidenceId) => projectedEvidenceById[evidenceId]?.[field])
    .filter(Boolean);
  const sourceFileByPath = Object.fromEntries(
    Object.entries(projection.source_file_index || {}).map(([path, sourceFile]) => {
      const facts = evidenceRecords(sourceFile.fact_evidence_ids, "fact");
      return [path, {
        ...sourceFile,
        direct_imports: facts.filter((fact) => fact.evidence?.evidence_kind === "direct_import"),
        entries: facts.filter((fact) => fact.evidence?.evidence_kind !== "direct_import"),
        definitions: evidenceRecords(sourceFile.definition_evidence_ids, "definition"),
        calls: evidenceRecords(sourceFile.call_evidence_ids, "call"),
      }];
    }),
  );
  const sourceCatalog = projection.source_catalog || {};
  const sourceEvidenceById = Object.fromEntries(
    Object.entries(projectedEvidenceById).map(([evidenceId, record]) => [
      evidenceId,
      record.file_path ? { ...record, file: sourceFileByPath[record.file_path] } : record,
    ]),
  );
  const definitionFiles = Object.values(sourceFileByPath).filter((item) => item.definition_evidence_ids?.length);
  const definitionFileByPath = Object.fromEntries(definitionFiles.map((item) => [item.path, item]));
  const sourceModules = (sourceCatalog.python_module_paths || [])
    .map((path) => sourceFileByPath[path])
    .filter(Boolean);
  const definitionByEvidenceId = Object.fromEntries(
    Object.entries(sourceEvidenceById)
      .filter(([, record]) => record.definition)
      .map(([evidenceId, record]) => [evidenceId, record]),
  );
  const callsByDefinitionEvidenceId = {};
  for (const record of Object.values(sourceEvidenceById)) {
    if (record.call) {
      (callsByDefinitionEvidenceId[record.call.enclosing_definition_evidence_id] ||= []).push(record.call);
    }
  }
  let state = stateApi.initialState();
  let suppressNextClick = false;
  let dialogOpener = null;

  const grid = document.querySelector("#module-grid");
  const moduleCount = document.querySelector("#module-count");
  const candidateCollaborations = document.querySelector("#candidate-collaborations");
  const candidateCollaborationList = document.querySelector("#candidate-collaboration-list");
  const modulesHeading = document.querySelector("#modules-heading");
  const heroTitle = document.querySelector(".hero h1");
  const heroStatus = document.querySelector("#view-status");
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
  const contextDetailToggle = document.querySelector("#context-detail-toggle");
  const contextEvidence = document.querySelector("#context-evidence");
  const graphSurface = document.querySelector("#graph-surface");
  const relationshipGraph = document.querySelector("#relationship-graph");
  const relationshipEdgeLayer = document.querySelector("#relationship-edge-layer");
  const relationshipLabelLayer = document.querySelector("#relationship-label-layer");
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

  const progressiveEvidenceSummary = (evidence) => {
    const summaries = {
      powershell_function_entry: {
        what: "这段代码是什么：Supervisor 中定义的 Worker 进程启动函数。",
        proves: "它证明什么：源码定义了受参数、工作目录和输出收集约束的受控进程启动边界；不证明函数在真实运行中被调用。",
      },
      powershell_script_binding: {
        what: "这段代码是什么：Supervisor 为确定性 Worker 包装脚本保存的明确路径。",
        proves: "它证明什么：源码把启动目标绑定到确定性 Worker 包装脚本；不证明脚本已经启动或形成完整源码归属。",
      },
      deterministic_worker_wrapper: {
        what: "这段代码是什么：Python 包装脚本直接运行时使用的 Worker 主入口。",
        proves: "它证明什么：源码在直接执行包装脚本时把控制交给 Worker 主入口；不证明某次运行实际到达或成功完成。",
      },
    };
    return summaries[evidence.evidence_kind] || null;
  };

  const candidateEvidenceCard = (evidenceId, returnContext, progressive = false) => {
    const record = sourceEvidenceById[evidenceId];
    if (!record) return text("p", `无法解析源码依据：${evidenceId}`, "load-error");
    const { fact, language, definition, call, file } = record;
    const sourceModule = file;
    const evidence = fact?.evidence || definition?.evidence || call?.evidence;
    const location = `${evidence.path}:${evidence.current_location.line_start} · symbol: ${evidence.symbol}`;
    const label = language === "powershell"
      ? `${fact.kind} · ${fact.symbol || `$${fact.variable}`}`
      : definition
      ? `${definition.kind} · ${definition.qualified_symbol}`
      : call
      ? `调用 · ${call.callee}`
      : fact.kind === "from"
      ? `${".".repeat(fact.level)}${fact.module} → ${fact.names.join(", ")}`
      : fact.module || `${fact.kind} · ${evidence.symbol}`;
    const explanation = language === "powershell"
      ? fact.kind === "script_binding"
        ? `PowerShell 静态 AST 把这个脚本变量解析为明确路径；它支持当前对象的证据调查，但不证明目标脚本已经执行或形成正式架构协作。`
        : fact.kind === "dot_source"
          ? `PowerShell 静态 AST 确认这里使用 dot-source 接入明确绑定的脚本；它支持当前对象的证据调查，但不证明运行时调用或决策结果。`
          : `PowerShell 静态 AST 确认这个关键函数入口写在 Supervisor 中；它支持当前对象的证据调查，但不证明入口在某次运行中被调用。`
      : call
        ? `这条静态调用事实来自“${file.path}”。它支持当前对象的调查方向，但不证明调用已经执行；只有 Call Fact 明确给出的静态目标才允许继续导航。`
        : `这条依据来自“${definition ? file.path : sourceModule.path}”。它支持当前对象的调查方向，但不证明架构归属、运行可达或真实协作。`;
    const card = document.createElement("article"); card.className = "candidate-evidence-card";
    const evidenceRole = language === "powershell"
      ? "PowerShell AST 自动静态源码事实"
      : call
        ? "Python 静态调用事实"
        : definition
          ? "Python 静态定义事实"
          : "Python AST 自动静态源码事实";
    card.append(text("p", evidenceRole, "evidence-role"), text("h4", label));
    const summary = progressive ? progressiveEvidenceSummary(evidence) : null;
    if (summary) {
      card.classList.add("progressive-candidate-evidence");
      card.append(
        text("p", `源码位置：${location}`, "candidate-evidence-location"),
        text("p", summary.what, "candidate-evidence-summary"),
        text("p", summary.proves, "candidate-evidence-summary candidate-evidence-proves")
      );
    } else card.append(text("p", explanation, "evidence-explanation"));
    const facts = document.createElement("dl"); facts.className = "raw-evidence-facts";
    for (const [term, value] of [["Evidence ID", evidence.evidence_id], ["源码定位", location], ["事实种类", evidence.evidence_kind]]) {
      facts.append(text("dt", term), text("dd", value));
    }
    if (summary) {
      const raw = expandable("查看原始源码与技术定位", "candidate-evidence-technical");
      const copy = text("button", "复制定位信息", "copy-location"); copy.type = "button";
      copy.addEventListener("click", () => copyLocation(location, copy));
      raw.append(facts, text("pre", evidence.snippet), copy); card.append(raw);
    } else card.append(facts, text("pre", evidence.snippet));
    const definitionFile = definition ? file : (!language && sourceModule ? definitionFileByPath[sourceModule.path] : null);
    if (definitionFile && !call) {
      const browse = text("button", "浏览此文件定义", "browse-definitions");
      browse.type = "button";
      browse.dataset.evidenceId = evidenceId;
      browse.addEventListener("click", () => showDefinitionFile(definitionFile, returnContext, evidenceId));
      card.append(browse);
    }
    if (call && record.source_trail_kind === "call") {
      const browse = text("button", "在 Source Trail 中查看此调用", "browse-call-fact");
      browse.type = "button";
      browse.dataset.evidenceId = evidenceId;
      browse.addEventListener("click", () => showSourceTrailEvidence(evidenceId, returnContext));
      card.append(browse);
    }
    return card;
  };

  const definitionKindLabels = {
    class: "类",
    function: "函数",
    method: "方法",
    "nested-function": "嵌套函数",
  };

  const callResolutionLabels = {
    "exact-local": "exact-local · 已确认同文件静态目标",
    "exact-import": "exact-import · 已确认显式导入静态目标",
    "unresolved-dispatch": "unresolved-dispatch · 运行时分派未确定",
    "dynamic-or-ambiguous": "dynamic-or-ambiguous · 动态或有歧义",
  };

  const callReasonLabels = {
    "unique-unshadowed-same-module-definition": "同一模块中只有一个未被遮蔽的静态定义。",
    "unique-unshadowed-explicit-first-party-import": "显式 first-party import 唯一绑定到一个已索引定义。",
    "runtime-receiver-attribute-dispatch": "调用经过运行时接收者属性；静态扫描不能确定实际对象或 Provider 实现。",
    "dynamic-or-unsupported-callee-shape": "调用对象由动态表达式产生，静态扫描不能确认唯一目标。",
    "name-is-parameter-or-rebound-in-enclosing-scope": "名称是参数或在当前作用域重新绑定，不能确认唯一目标。",
    "star-import-makes-name-resolution-ambiguous": "star import 使名称来源不唯一。",
    "local-definition-binding-is-not-unique": "同名本地定义不唯一。",
    "imported-definition-target-is-not-unique": "导入符号不能唯一对应一个已索引定义。",
    "import-binding-is-rebound-or-ambiguous": "导入绑定被重新绑定或存在歧义。",
    "no-unique-static-definition-binding": "没有找到可证明唯一的静态定义绑定。",
  };

  let sourceTrail = null;

  const clearDefinitionFileState = () => {
    sourceTrail = null;
    delete detailContent.dataset.definitionFile;
    delete detailContent.dataset.sourceTrailLevel;
  };

  const returnToCandidateEvidence = (returnContext, returnEvidenceId) => {
    if (returnContext.kind === "candidate-node") {
      showModuleDetail(returnContext.module);
    } else {
      showCandidateRelationship(returnContext.collaboration);
    }
    window.setTimeout(() => {
      detailContent.querySelector(`.browse-definitions[data-evidence-id="${CSS.escape(returnEvidenceId)}"]`)?.focus();
    }, 0);
  };

  const sourceTrailCrumbs = (entries) => entries.map((entry) => {
    if (entry.kind === "file") return entry.file.path;
    if (entry.kind === "definition") return entry.definition.qualified_symbol;
    return entry.call.callee;
  }).join(" › ");

  const renderSourceTrail = (focusSelector = null) => {
    if (!sourceTrail) return;
    const entry = sourceTrail.entries.at(-1);
    detailContent.replaceChildren();
    delete detailContent.dataset.relationshipId;
    delete detailContent.dataset.collaborationId;
    delete detailContent.dataset.evidenceId;
    detailContent.dataset.definitionFile = entry.file.path;
    detailContent.dataset.sourceTrailLevel = entry.kind;
    const back = text(
      "button",
      sourceTrail.entries.length === 1 ? "返回候选源码依据" : "返回上一步",
      "definition-back",
    );
    back.type = "button";
    back.addEventListener("click", () => {
      if (sourceTrail.entries.length === 1) {
        returnToCandidateEvidence(sourceTrail.returnContext, sourceTrail.returnEvidenceId);
        return;
      }
      const removed = sourceTrail.entries.pop();
      renderSourceTrail(removed.returnFocusSelector || null);
    });
    const titleText = entry.kind === "file"
      ? "Python 文件定义"
      : entry.kind === "definition"
        ? "Python 静态定义"
        : "Python 静态调用事实";
    const title = text("h2", titleText, "detail-title");
    title.id = "detail-title";
    detailContent.append(
      back,
      text("p", "静态 Source Facts", "eyebrow"),
      title,
      text("p", sourceTrailCrumbs(sourceTrail.entries), "source-trail-breadcrumb"),
    );

    if (entry.kind === "file") {
      const file = entry.file;
      detailContent.append(
        text("p", file.path, "definition-file-path"),
        text(
          "p",
          "以下内容是 Python AST 提取的静态定义事实，不证明运行时执行，也不批准文件的架构归属。",
          "candidate-warning",
        ),
        text("p", `${file.definitions.length} 个定义`, "definition-total"),
      );
      const controls = document.createElement("div");
      controls.className = "definition-filters";
      const nameLabel = text("label", "按名称过滤");
      nameLabel.htmlFor = "definition-name-filter";
      const nameFilter = document.createElement("input");
      nameFilter.id = "definition-name-filter";
      nameFilter.type = "search";
      nameFilter.autocomplete = "off";
      nameFilter.value = entry.query || "";
      const kindLabel = text("label", "按类型过滤");
      kindLabel.htmlFor = "definition-kind-filter";
      const kindFilter = document.createElement("select");
      kindFilter.id = "definition-kind-filter";
      for (const [value, label] of [["", "全部类型"], ...Object.entries(definitionKindLabels)]) {
        const option = document.createElement("option");
        option.value = value; option.textContent = label; kindFilter.append(option);
      }
      kindFilter.value = entry.definitionKind || "";
      controls.append(nameLabel, nameFilter, kindLabel, kindFilter);
      const list = document.createElement("div");
      list.className = "definition-list";
      detailContent.append(controls, list, text("section", "", "definition-selection"));
      const renderList = () => {
        entry.query = nameFilter.value;
        entry.definitionKind = kindFilter.value;
        list.replaceChildren();
        const query = entry.query.trim().toLocaleLowerCase();
        const matches = file.definitions.filter((definition) => (
          (!query || definition.qualified_symbol.toLocaleLowerCase().includes(query))
          && (!entry.definitionKind || definition.kind === entry.definitionKind)
        ));
        if (!matches.length) {
          list.append(text("p", file.definitions.length ? "没有符合过滤条件的定义。" : "此文件没有静态定义。", "empty-evidence"));
          return;
        }
        for (const definition of matches) {
          const row = text(
            "button",
            `${definitionKindLabels[definition.kind]} · ${definition.qualified_symbol} · line ${definition.evidence.current_location.line_start}`,
            "definition-row",
          );
          row.type = "button";
          row.dataset.definitionId = definition.evidence.evidence_id;
          row.addEventListener("click", () => {
            sourceTrail.entries.push({
              kind: "definition",
              file,
              definition,
              returnFocusSelector: `.definition-row[data-definition-id="${CSS.escape(definition.evidence.evidence_id)}"]`,
            });
            renderSourceTrail();
          });
          list.append(row);
        }
      };
      nameFilter.addEventListener("input", renderList);
      kindFilter.addEventListener("change", renderList);
      renderList();
      if (focusSelector) window.setTimeout(() => detailContent.querySelector(focusSelector)?.focus(), 0);
      else nameFilter.focus();
      return;
    }

    if (entry.kind === "definition") {
      const definition = entry.definition;
      const evidence = definition.evidence;
      const location = `${evidence.path}:${evidence.current_location.line_start}:${evidence.current_location.column_start}`;
      const facts = document.createElement("dl");
      facts.className = "raw-evidence-facts";
      for (const [term, value] of [
        ["类型", definitionKindLabels[definition.kind]],
        ["Qualified symbol", definition.qualified_symbol],
        ["Parent symbol", definition.parent_symbol],
        ["Evidence ID", evidence.evidence_id],
        ["源码定位", location],
      ]) facts.append(text("dt", term), text("dd", value));
      const selected = document.createElement("section");
      selected.className = "definition-selection definition-selected";
      selected.append(
        text("h3", definition.qualified_symbol),
        entry.transition === "exact-static-target"
          ? text("p", "已确认静态目标：唯一静态绑定允许继续定位；这不证明运行时执行，也不新增 Candidate Evidence 或架构归属。", "candidate-warning")
          : text("p", "静态定义不等于真实运行事实，也不表示架构归属已获批准。", "candidate-warning"),
        facts,
        text("pre", evidence.snippet, "definition-source"),
        text("h3", "此定义中的静态调用"),
      );
      const calls = callsByDefinitionEvidenceId[evidence.evidence_id] || [];
      if (!calls.length) {
        selected.append(text("p", "限定的四文件 Call Facts 中没有记录此定义的调用。", "empty-evidence"));
      } else {
        const callList = document.createElement("div");
        callList.className = "call-list";
        for (const call of calls) {
          const row = text(
            "button",
            `${call.callee} · ${callResolutionLabels[call.resolution]} · line ${call.evidence.current_location.line_start}`,
            `call-row call-${call.resolution}`,
          );
          row.type = "button";
          row.dataset.callId = call.evidence.evidence_id;
          row.addEventListener("click", () => {
            sourceTrail.entries.push({
              kind: "call",
              file: entry.file,
              definition,
              call,
              returnFocusSelector: `.call-row[data-call-id="${CSS.escape(call.evidence.evidence_id)}"]`,
            });
            renderSourceTrail();
          });
          callList.append(row);
        }
        selected.append(callList);
      }
      detailContent.append(selected);
      if (focusSelector) window.setTimeout(() => detailContent.querySelector(focusSelector)?.focus(), 0);
      else back.focus();
      return;
    }

    const call = entry.call;
    const evidence = call.evidence;
    const location = `${evidence.path}:${evidence.current_location.line_start}:${evidence.current_location.column_start}`;
    const facts = document.createElement("dl");
    facts.className = "raw-evidence-facts";
    for (const [term, value] of [
      ["Callee", call.callee],
      ["分类", callResolutionLabels[call.resolution]],
      ["原因", callReasonLabels[call.resolution_reason] || call.resolution_reason],
      ["Evidence ID", evidence.evidence_id],
      ["源码定位", location],
    ]) facts.append(text("dt", term), text("dd", value));
    const callDetail = document.createElement("section");
    callDetail.className = "call-selection";
    callDetail.append(
      text("h3", call.callee),
      text(
        "p",
        "这是 Python AST 提取的静态调用事实，不证明该调用已经执行，也不形成正式架构关系。",
        "candidate-warning",
      ),
      facts,
      text("pre", evidence.snippet, "call-source"),
    );
    if (call.target_definition_evidence_id) {
      const target = definitionByEvidenceId[call.target_definition_evidence_id];
      if (target) {
        const openTarget = text("button", "打开已确认静态目标", "call-target");
        openTarget.type = "button";
        openTarget.addEventListener("click", () => {
          sourceTrail.entries.push({
            kind: "definition",
            file: target.file,
            definition: target.definition,
            transition: "exact-static-target",
            returnFocusSelector: ".call-target",
          });
          renderSourceTrail();
        });
        callDetail.append(openTarget);
      }
    } else {
      callDetail.append(text("p", "未确认目标：不会列出或跳转到名称相似的 Provider 实现。", "empty-evidence"));
    }
    detailContent.append(callDetail);
    if (focusSelector) window.setTimeout(() => detailContent.querySelector(focusSelector)?.focus(), 0);
    else back.focus();
  };

  const showDefinitionFile = (file, returnContext, returnEvidenceId) => {
    sourceTrail = {
      returnContext,
      returnEvidenceId,
      entries: [{ kind: "file", file, query: "", definitionKind: "" }],
    };
    renderSourceTrail();
  };

  const showSourceTrailEvidence = (evidenceId, returnContext) => {
    const record = sourceEvidenceById[evidenceId];
    if (!record || !record.source_trail_kind) return false;
    if (record.definition) {
      sourceTrail = {
        returnContext,
        returnEvidenceId: evidenceId,
        entries: [{ kind: "definition", file: record.file, definition: record.definition }],
      };
    } else if (record.call) {
      const enclosing = definitionByEvidenceId[record.enclosing_definition_evidence_id];
      if (!enclosing) return false;
      sourceTrail = {
        returnContext,
        returnEvidenceId: evidenceId,
        entries: [{
          kind: "call",
          file: record.file,
          definition: enclosing.definition,
          call: record.call,
        }],
      };
    } else return false;
    renderSourceTrail();
    openDialog();
    return true;
  };

  const renderSourceModule = () => {
    const module = sourceModules[Number(sourceModuleSelect.value)];
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
    if (!sourceModules.length) { sourceFactsDetails.hidden = true; return; }
    const supervisorCount = Number(sourceCatalog.powershell_files_scanned || 0);
    sourceFactsCount.textContent = `${Number(sourceCatalog.python_files_scanned || sourceModules.length)} 个 Python 文件 · ${supervisorCount} 个 Supervisor · 默认收起`;
    sourceModules.forEach((module, index) => {
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
    if (!dialog.open) {
      const opener = dialogOpener || document.activeElement;
      dialogOpener = opener instanceof HTMLElement ? opener : null;
    }
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

  const relationshipReviewMeaning = (review) => {
    const label = review.result === "APPROVED" && review.state === "current"
      ? `当前已审核 · ${projectedReviewLabel(review)}`
      : projectedReviewLabel(review);
    const item = document.createElement("article");
    item.append(
      text("h3", label),
      text("p", `审核结果与当前状态由对象自己的显式投影提供；批准范围：${approvedScopeLabel(review)}。`),
    );
    return item;
  };

  const showRelationship = (relation) => {
    const guide = relation.presentation_zh;
    detailContent.replaceChildren(); clearDefinitionFileState(); delete detailContent.dataset.collaborationId; delete detailContent.dataset.evidenceId;
    detailContent.dataset.relationshipId = relation.id;
    const title = text("h2", `${guide.source_module_name} → ${guide.target_module_name}`, "detail-title"); title.id = "detail-title";
    detailContent.append(
      text("p", "架构关系解读", "eyebrow"),
      title,
      text("p", guide.relationship_name, "plain-relation-name"), text("p", guide.summary, "relationship-summary")
    );
    const plainExplanation = relationshipPlainExplanation(relation, guide);
    if (plainExplanation) detailContent.append(text("p", plainExplanation, "plain-collaboration-explanation"));
    const statusGrid = document.createElement("section"); statusGrid.className = "meaning-grid";
    statusGrid.append(relationshipReviewMeaning(relation.review));
    for (const [title, description] of [
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
      ["Review result/state", projectedReviewLabel(relation.review)], ["Code reachability", relation.code_reachability.status],
      ["Runtime state", relation.runtime_state], ["Evidence continuity", relation.source_continuity.status],
    ]) facts.append(text("dt", term), text("dd", value));
    original.append(facts, text("h4", "原始 Owner 审核文字"), text("pre", relation.architecture_explanation || "—", "review-notes"));
    detailContent.append(original); openDialog();
  };

  const candidateRelationshipsForNode = (nodeId) => Object.entries(projection.relationship_index || {})
    .filter(([, relationship]) => Boolean(relationship.object_key))
    .map(([, relationship]) => relationship)
    .filter((item) => item.source_node_id === nodeId || item.target_node_id === nodeId);

  const showCandidateRelationship = (collaboration) => {
    const source = childById[collaboration.source_node_id];
    const target = childById[collaboration.target_node_id];
    const isDecomposition = collaboration.semantic === "decomposition";
    const semanticLabel = isDecomposition ? "候选分解" : "候选协作";
    detailContent.replaceChildren(); clearDefinitionFileState(); delete detailContent.dataset.relationshipId; delete detailContent.dataset.evidenceId;
    detailContent.dataset.collaborationId = collaboration.id;
    const title = text("h2", `${source.name} → ${target.name}`, "detail-title"); title.id = "detail-title";
    detailContent.append(
      text("p", `${semanticLabel}：${projectedReviewLabel(collaboration.review)}`, "eyebrow"),
      title,
      text("p", collaboration.name, "plain-relation-name"),
      text("p", `关系类型：${semanticLabel}`, "candidate-collaboration-type"),
      text("p", `${isDecomposition ? "Candidate Decomposition" : "Candidate Collaboration"} ID：${collaboration.id}`, "candidate-collaboration-id"),
      text("p", collaboration.professional_description, "relationship-summary"),
      text("p", collaboration.plain_explanation, "plain-collaboration-explanation")
    );
    const statuses = document.createElement("div"); statuses.className = "statuses detail-statuses";
    statuses.append(text("span", projectedReviewStatus(isDecomposition ? "分解" : "协作", collaboration.review), `status ${projectedReviewClass(collaboration.review)}`), text("span", "源码归属：未核验", "status coverage-unverified"));
    const reviewMeaning = document.createElement("section"); reviewMeaning.className = "meaning-grid";
    reviewMeaning.append(relationshipReviewMeaning(collaboration.review));
    detailContent.append(statuses, reviewMeaning, text("p", `这些静态源码依据只支持${semanticLabel}解释，不证明运行时行为或审核批准。`, "candidate-warning"));
    detailContent.append(text("h3", `源码依据（${collaboration.evidence_ids.length}）`));
    const returnContext = { kind: collaboration.object_key.kind, collaboration };
    for (const evidenceId of collaboration.evidence_ids) {
      detailContent.append(candidateEvidenceCard(evidenceId, returnContext));
    }
    openDialog();
  };

  const relationshipsForModule = (moduleId) => {
    const childReferences = childById[moduleId]?.source_assignment?.relationship_ids || [];
    const direct = relationships.filter((item) => item.source_node_id === moduleId || item.target_node_id === moduleId).map((item) => item.id);
    return [...new Set([...childReferences, ...direct])].map((id) => relationshipById[id]).filter(Boolean);
  };

  const candidateKindFor = (module) => module.node_type === "component" ? "组件" : "职责";

  const isProgressiveNodeDetail = (module) => Boolean(nodeReviewFor(module));

  const nodeDetailReviewLabel = (review) => {
    if (review.projection === "Current · Approved" && review.state.status === "current" && review.state.continuity === "exact-match") {
      return "节点职责审核通过（当前有效）";
    }
    return `节点职责审核：${nodeReviewLabels[review.projection] || nodeReviewLabels.Unknown}`;
  };

  const nodeDetailReviewStatusClass = (review) => review.projection === "Current · Approved"
    && review.state.status === "current"
    && review.state.continuity === "exact-match"
    ? "approved"
    : "candidate";

  const appendNodeReviewStatuses = (statuses, module) => {
    const review = nodeReviewFor(module);
    if (!review) return;
    statuses.classList.add("node-review-statuses");
    statuses.append(text("span", `人工审核：${nodeReviewLabels[review.projection] || nodeReviewLabels.Unknown}`, "candidate-card-status node-review-status"));
  };

  const appendNodeReviewDetail = (module) => {
    const review = nodeReviewFor(module);
    if (!review) return;
    const progressive = isProgressiveNodeDetail(module);
    const container = progressive ? expandable("专业技术信息（审核记录与完整证据）", "node-review-technical") : detailContent;
    const rawReview = () => {
      const disclosure = expandable("查看 Decision 与 State 原始 JSON", "node-review-json");
      if (review.decision) disclosure.append(text("h4", "Node Review Decision"), text("pre", JSON.stringify(review.decision, null, 2)));
      disclosure.append(text("h4", "Node Review State"), text("pre", JSON.stringify(review.state, null, 2)));
      return disclosure;
    };
    container.append(text("h3", "节点审核（只读）"));
    const identity = document.createElement("dl"); identity.className = "raw-evidence-facts node-review-identity";
    const identityRows = [
      ["对象键", [review.object_key.kind, review.object_key.parent_module_id, review.object_key.node_id].filter(Boolean).join(" + ")],
      ["Decision", review.decision?.result || "Pending Review"],
      ["Node Review State", `${review.state.status} · ${review.state.continuity}`],
    ];
    if (review.decision) {
      identityRows.push(
        ["Decision ID", review.decision.decision_id],
        ["可信来源", `${review.source.author} · ${review.source.author_association} · ${review.source.permission}`],
        ["来源收据", review.source.sha256],
      );
      if (review.state.reasons?.length) identityRows.push(["连续性原因", review.state.reasons.join("；")]);
      if (review.state.evaluated_source_snapshot) {
        identityRows.push(["评估源码快照", `${review.state.evaluated_source_snapshot.revision} · ${review.state.evaluated_source_snapshot.worktree_state}`]);
      }
      if (progressive && review.decision.reviewed_source_snapshot) {
        identityRows.push(["审核源码快照", `${review.decision.reviewed_source_snapshot.revision} · ${review.decision.reviewed_source_snapshot.worktree_state}`]);
      }
    }
    for (const [term, value] of identityRows) identity.append(text("dt", term), text("dd", value));
    container.append(identity);
    if (!review.decision) {
      container.append(text("p", "当前无可信审核决定；没有可用的可信 Owner/admin 节点审核决定。", "candidate-warning"));
      if (progressive) container.append(rawReview());
      if (progressive) detailContent.append(container);
      return;
    }

    if (review.state.current_evidence_locations?.length) {
      container.append(text("h3", "当前 Evidence 定位"));
      const locations = document.createElement("ul"); locations.className = "node-review-current-locations";
      for (const location of review.state.current_evidence_locations) {
        locations.append(text("li", `${location.path}:${location.line_start}-${location.line_end} · ${location.evidence_id}`));
      }
      container.append(locations);
    }

    container.append(text("h3", progressive ? "Review Claims（原始 JSON）" : "Review Claims"));
    for (const claim of review.decision.claims) {
      const item = document.createElement("article"); item.className = "node-review-claim";
      item.append(
        text("h4", claim.kind),
        text("p", `绑定来源：${claim.source} · 校验：精确匹配`, "group-description"),
      );
      if (claim.kind === "CLASSIFICATION") {
        item.append(text("p", `Classification Rationale：${claim.value.rationale}`, "candidate-warning"));
      }
      if (claim.kind === "FORMAL_DEPENDENCY_AND_LIMITS") {
        item.append(text("p", `正式依赖：${claim.value.relationship_id} · ${claim.value.t3_decision_id} · ${claim.value.formal_evidence_ids.length} 条正式 Evidence`, "candidate-warning"));
      }
      item.append(text("pre", JSON.stringify(claim.value, null, 2)));
      container.append(item);
    }
    if (progressive) container.append(rawReview());
    if (progressive) detailContent.append(container);
  };

  const showModuleDetail = (module) => {
    const isCandidate = module.object_key?.kind === "candidate-node";
    const candidateKind = candidateKindFor(module);
    const related = relationshipsForModule(module.id);
    detailContent.replaceChildren(); clearDefinitionFileState(); delete detailContent.dataset.relationshipId; delete detailContent.dataset.collaborationId; delete detailContent.dataset.evidenceId;
    const title = text("h2", module.name, "detail-title"); title.id = "detail-title";
    detailContent.append(text("p", isCandidate ? `候选${candidateKind}` : "模块详情", "eyebrow"), title, text("p", module.responsibility, "relationship-summary"));
    if (isCandidate && module.plain_explanation) detailContent.append(text("p", module.plain_explanation, "plain-collaboration-explanation candidate-plain-explanation"));
    const statuses = document.createElement("div"); statuses.className = "statuses detail-statuses";
    const nodeReview = nodeReviewFor(module);
    const progressive = isProgressiveNodeDetail(module);
    const candidateLinks = isCandidate ? candidateRelationshipsForNode(module.id) : [];
    if (progressive) {
      statuses.append(
        text("span", nodeDetailReviewLabel(nodeReview), `status ${nodeDetailReviewStatusClass(nodeReview)} node-detail-review-status`),
        text("span", module.source_assignment.status === "partial" ? "源码归属：部分确认" : assignmentLabels[module.source_assignment.status], `status coverage-${module.source_assignment.status}`)
      );
      const candidateLinkStates = new Map(candidateLinks.map((relationship) => {
        const kind = relationship.semantic === "decomposition" ? "候选分解" : "候选协作";
        const label = projectedReviewLabel(relationship.review);
        return [`${kind}:${label}`, { kind, label, reviewClass: projectedReviewClass(relationship.review) }];
      }));
      for (const { kind, label, reviewClass } of candidateLinkStates.values()) {
        statuses.append(text("span", `${kind}：${label}`, `status ${reviewClass}`));
      }
    } else {
      statuses.append(text("span", isCandidate ? projectedReviewStatus(candidateKind, module.review) : "逻辑定义：已批准", `status ${isCandidate ? projectedReviewClass(module.review) : "approved"}`));
      appendNodeReviewStatuses(statuses, module);
    }
    if (isCandidate) {
      if (!progressive) statuses.append(text("span", assignmentLabels[module.source_assignment.status], `status coverage-${module.source_assignment.status}`));
      detailContent.append(statuses, text("p", module.source_assignment.note, "candidate-warning"));
      if (progressive) detailContent.append(text("p", "这些是静态源码事实和审核结论：它们不证明该职责在真实运行中被到达，也不证明运行成功、凭据或外部依赖可用。", "candidate-warning node-review-runtime-limit"));
      const visibleDecomposition = currentView().relationship_ids.some((relationshipId) => {
        const reference = projection.relationship_index[relationshipId];
        const record = relationshipRecordById[relationshipId];
        return reference?.semantic === "decomposition"
          && record?.source_node_id === module.id;
      });
      if (visibleDecomposition) detailContent.append(text("p", "此节点既有审核只适用于已批准的职责主张；不批准当前 View 中新增的候选分解、候选协作或子节点。", "candidate-warning node-review-scope-limit"));
      if (module.source_assignment.evidence_ids?.length) {
        detailContent.append(text("h3", `源码依据（${module.source_assignment.evidence_ids.length}）`));
        const returnContext = { kind: "candidate-node", module };
        for (const evidenceId of module.source_assignment.evidence_ids) {
          detailContent.append(candidateEvidenceCard(evidenceId, returnContext, progressive));
        }
      }
      if (candidateLinks.length) {
        detailContent.append(text("h3", "候选协作"));
        const links = document.createElement("div"); links.className = "module-evidence-links";
        for (const collaboration of candidateLinks) {
          const button = text("button", `打开：${collaboration.name}`, "evidence-link"); button.type = "button";
          button.addEventListener("click", () => showCandidateRelationship(collaboration)); links.append(button);
        }
        detailContent.append(links);
      }
    } else {
      statuses.append(text("span", coverageLabels[module.source_coverage.status], `status coverage-${module.source_coverage.status}`)); detailContent.append(statuses);
    }
    appendNodeReviewDetail(module);
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
  const currentView = () => viewById[state.levelId] || rootView;
  let edgeFrame = 0;
  let edgeTimer = 0;

  const visibleGraphEdges = () => {
    const view = currentView();
    const visibleNodeIds = new Set(view.appearances.map((appearance) => appearance.node_id));
    return view.relationship_ids.map((relationshipId) => {
      const reference = projection.relationship_index[relationshipId];
      const record = relationshipRecordById[relationshipId];
      if (reference.object_key) {
        return {
          id: relationshipId,
          sourceId: record.source_node_id,
          targetId: record.target_node_id,
          kind: "candidate",
          semantic: reference.semantic,
          record,
        };
      }
      return {
        id: relationshipId,
        sourceId: record.source_node_id,
        targetId: record.target_node_id,
        kind: "reviewed",
        record,
      };
    }).filter((edge) => (
      visibleNodeIds.has(edge.sourceId)
        && visibleNodeIds.has(edge.targetId)
        && (edge.kind === "candidate"
          ? Boolean(edge.record.object_key)
          : edge.record.review.result === "APPROVED" && edge.record.review.state === "current")
    ));
  };

  const edgeEndpoints = (sourceRect, targetRect, surfaceRect) => {
    const sourceCenter = { x: sourceRect.left + sourceRect.width / 2, y: sourceRect.top + sourceRect.height / 2 };
    const targetCenter = { x: targetRect.left + targetRect.width / 2, y: targetRect.top + targetRect.height / 2 };
    const horizontal = Math.abs(targetCenter.x - sourceCenter.x) >= Math.abs(targetCenter.y - sourceCenter.y);
    const start = horizontal
      ? { x: targetCenter.x >= sourceCenter.x ? sourceRect.right : sourceRect.left, y: sourceCenter.y }
      : { x: sourceCenter.x, y: targetCenter.y >= sourceCenter.y ? sourceRect.bottom : sourceRect.top };
    const end = horizontal
      ? { x: targetCenter.x >= sourceCenter.x ? targetRect.left : targetRect.right, y: targetCenter.y }
      : { x: targetCenter.x, y: targetCenter.y >= sourceCenter.y ? targetRect.top : targetRect.bottom };
    return {
      start: { x: start.x - surfaceRect.left, y: start.y - surfaceRect.top },
      end: { x: end.x - surfaceRect.left, y: end.y - surfaceRect.top },
    };
  };

  const segmentIntersectsCard = (start, end, cardRect, surfaceRect) => {
    const inset = 2;
    const bounds = {
      left: cardRect.left - surfaceRect.left + inset,
      right: cardRect.right - surfaceRect.left - inset,
      top: cardRect.top - surfaceRect.top + inset,
      bottom: cardRect.bottom - surfaceRect.top - inset,
    };
    const dx = end.x - start.x; const dy = end.y - start.y;
    const p = [-dx, dx, -dy, dy];
    const q = [start.x - bounds.left, bounds.right - start.x, start.y - bounds.top, bounds.bottom - start.y];
    let entry = 0; let exit = 1;
    for (let index = 0; index < p.length; index += 1) {
      if (p[index] === 0) {
        if (q[index] < 0) return false;
        continue;
      }
      const ratio = q[index] / p[index];
      if (p[index] < 0) entry = Math.max(entry, ratio);
      else exit = Math.min(exit, ratio);
      if (entry > exit) return false;
    }
    return true;
  };

  const edgePath = (edge, sourceCard, targetCard, surfaceRect, width, routeIndex) => {
    const sourceRect = sourceCard.getBoundingClientRect();
    const targetRect = targetCard.getBoundingClientRect();
    let direct = edgeEndpoints(sourceRect, targetRect, surfaceRect);
    if (edge.semantic === "decomposition" && targetRect.top >= sourceRect.bottom) {
      direct = {
        start: {
          x: sourceRect.left + sourceRect.width / 2 - surfaceRect.left,
          y: sourceRect.bottom - surfaceRect.top,
        },
        end: {
          x: targetRect.left + targetRect.width / 2 - surfaceRect.left,
          y: targetRect.top - surfaceRect.top,
        },
      };
    }
    const cards = Array.from(grid.querySelectorAll(".module-card"));
    const unrelatedCards = cards.filter((card) => card !== sourceCard && card !== targetCard);
    const candidates = [[direct.start, direct.end]];
    const sourceCenter = {
      x: sourceRect.left + sourceRect.width / 2 - surfaceRect.left,
      y: sourceRect.top + sourceRect.height / 2 - surfaceRect.top,
    };
    const targetCenter = {
      x: targetRect.left + targetRect.width / 2 - surfaceRect.left,
      y: targetRect.top + targetRect.height / 2 - surfaceRect.top,
    };
    const targetBelow = targetRect.top >= sourceRect.bottom;
    const targetAbove = targetRect.bottom <= sourceRect.top;
    if (targetBelow || targetAbove) {
      const end = {
        x: targetCenter.x,
        y: (targetBelow ? targetRect.top : targetRect.bottom) - surfaceRect.top,
      };
      const beforeTarget = cards
        .filter((card) => card !== targetCard)
        .map((card) => card.getBoundingClientRect())
        .filter((rect) => targetBelow ? rect.bottom <= targetRect.top : rect.top >= targetRect.bottom);
      const adjacentBoundary = targetBelow
        ? Math.max(...beforeTarget.map((rect) => rect.bottom), sourceRect.bottom)
        : Math.min(...beforeTarget.map((rect) => rect.top), sourceRect.top);
      const landingY = (
        (targetBelow ? targetRect.top + adjacentBoundary : targetRect.bottom + adjacentBoundary) / 2
      ) - surfaceRect.top;
      const leftGap = Math.min(...cards.map((card) => card.getBoundingClientRect().left)) - surfaceRect.left;
      const rightGap = surfaceRect.right - Math.max(...cards.map((card) => card.getBoundingClientRect().right));
      const sideOptions = routeIndex % 2
        ? [["right", rightGap], ["left", leftGap]]
        : [["left", leftGap], ["right", rightGap]];
      for (const [side, gap] of sideOptions) {
        if (gap < 12) continue;
        const corridorInset = Math.max(gap / 2, Math.min(28, gap - 2));
        const corridorX = side === "left" ? corridorInset : width - corridorInset;
        const start = {
          x: (side === "left" ? sourceRect.left : sourceRect.right) - surfaceRect.left,
          y: sourceCenter.y,
        };
        const sideEnd = {
          x: (side === "left" ? targetRect.left : targetRect.right) - surfaceRect.left,
          y: targetCenter.y,
        };
        candidates.push(edge.semantic === "decomposition"
          ? [
            start,
            { x: corridorX, y: start.y },
            { x: corridorX, y: sideEnd.y },
            sideEnd,
          ]
          : [
            start,
            { x: corridorX, y: start.y },
            { x: corridorX, y: landingY },
            { x: end.x, y: landingY },
            end,
          ]);
      }
    } else {
      const targetRight = targetRect.left >= sourceRect.right;
      const targetLeft = targetRect.right <= sourceRect.left;
      if (targetRight || targetLeft) {
        const end = {
          x: (targetRight ? targetRect.left : targetRect.right) - surfaceRect.left,
          y: targetCenter.y,
        };
        const beforeTarget = cards
          .filter((card) => card !== targetCard)
          .map((card) => card.getBoundingClientRect())
          .filter((rect) => targetRight ? rect.right <= targetRect.left : rect.left >= targetRect.right);
        const adjacentBoundary = targetRight
          ? Math.max(...beforeTarget.map((rect) => rect.right), sourceRect.right)
          : Math.min(...beforeTarget.map((rect) => rect.left), sourceRect.left);
        const landingX = (
          (targetRight ? targetRect.left + adjacentBoundary : targetRect.right + adjacentBoundary) / 2
        ) - surfaceRect.left;
        const topGap = Math.min(...cards.map((card) => card.getBoundingClientRect().top)) - surfaceRect.top;
        const bottomGap = surfaceRect.bottom - Math.max(...cards.map((card) => card.getBoundingClientRect().bottom));
        for (const [side, gap] of [["top", topGap], ["bottom", bottomGap]]) {
          if (gap < 12) continue;
          const corridorY = side === "top" ? gap / 2 : surfaceRect.height - gap / 2;
          const start = {
            x: sourceCenter.x,
            y: (side === "top" ? sourceRect.top : sourceRect.bottom) - surfaceRect.top,
          };
          candidates.push([
            start,
            { x: start.x, y: corridorY },
            { x: landingX, y: corridorY },
            { x: landingX, y: end.y },
            end,
          ]);
        }
      }
    }

    return candidates
      .filter((points) => unrelatedCards.every((card) => (
        !points.slice(1).some((end, index) => (
          segmentIntersectsCard(points[index], end, card.getBoundingClientRect(), surfaceRect)
        ))
      )))
      .sort((left, right) => {
        const score = (points) => points.slice(1).reduce((total, end, index) => (
          total + Math.hypot(end.x - points[index].x, end.y - points[index].y)
        ), 0) + Math.max(0, points.length - 2) * 16;
        return score(left) - score(right);
      })[0] || null;
  };

  const rectanglesOverlap = (left, right, gap = 4) => !(
    left.right + gap <= right.left
      || left.left >= right.right + gap
      || left.bottom + gap <= right.top
      || left.top >= right.bottom + gap
  );

  const segmentIntersectsBounds = (start, end, bounds) => {
    const dx = end.x - start.x; const dy = end.y - start.y;
    const p = [-dx, dx, -dy, dy];
    const q = [start.x - bounds.left, bounds.right - start.x, start.y - bounds.top, bounds.bottom - start.y];
    let entry = 0; let exit = 1;
    for (let index = 0; index < p.length; index += 1) {
      if (p[index] === 0) {
        if (q[index] < 0) return false;
        continue;
      }
      const ratio = q[index] / p[index];
      if (p[index] < 0) entry = Math.max(entry, ratio);
      else exit = Math.min(exit, ratio);
      if (entry > exit) return false;
    }
    return true;
  };

  const placeGraphLabel = (label, points, otherPaths, width, height, surfaceRect, occupied) => {
    relationshipLabelLayer.append(label);
    const labelWidth = label.offsetWidth; const labelHeight = label.offsetHeight;
    const cardBounds = Array.from(grid.querySelectorAll(".module-card")).map((card) => {
      const rect = card.getBoundingClientRect();
      return {
        left: rect.left - surfaceRect.left,
        right: rect.right - surfaceRect.left,
        top: rect.top - surfaceRect.top,
        bottom: rect.bottom - surfaceRect.top,
      };
    });
    const boundsFor = (point) => ({
      left: point.x - labelWidth / 2,
      right: point.x + labelWidth / 2,
      top: point.y - labelHeight / 2,
      bottom: point.y + labelHeight / 2,
    });
    const fractions = [0.5, 0.35, 0.65, 0.25, 0.75];
    const segments = points.slice(1).map((end, index) => {
      const start = points[index];
      return { start, end, length: Math.hypot(end.x - start.x, end.y - start.y) };
    }).sort((left, right) => right.length - left.length);
    const candidates = segments.flatMap((segment) => fractions.map((fraction) => ({
      segment,
      point: {
        x: segment.start.x + (segment.end.x - segment.start.x) * fraction,
        y: segment.start.y + (segment.end.y - segment.start.y) * fraction,
      },
    })));
    const selected = candidates.find(({ point, segment }) => {
      const bounds = boundsFor(point);
      const withinSurface = bounds.left >= 8 && bounds.right <= width - 8
        && bounds.top >= 8 && bounds.bottom <= height - 8;
      const lineContinuesThroughLabel = !(
        segment.start.x >= bounds.left && segment.start.x <= bounds.right
          && segment.start.y >= bounds.top && segment.start.y <= bounds.bottom
      ) && !(
        segment.end.x >= bounds.left && segment.end.x <= bounds.right
          && segment.end.y >= bounds.top && segment.end.y <= bounds.bottom
      );
      const clearOfOtherPaths = otherPaths.every((path) => (
        !path.slice(1).some((end, index) => segmentIntersectsBounds(path[index], end, bounds))
      ));
      return withinSurface
        && lineContinuesThroughLabel
        && clearOfOtherPaths
        && cardBounds.every((card) => !rectanglesOverlap(bounds, card, 8))
        && occupied.every((other) => !rectanglesOverlap(bounds, other, 6));
    });
    if (!selected) {
      label.remove();
      return false;
    }
    const point = selected.point;
    label.style.left = `${point.x}px`; label.style.top = `${point.y}px`;
    occupied.push(boundsFor(point));
    return true;
  };

  const activateRelationship = (relationshipId) => {
    dispatch({ type: "RELATIONSHIP_PRIMARY", relationshipId });
    showRelationship(relationshipById[state.relationshipId]);
  };

  const activateGraphEdge = (edge) => {
    if (edge.kind === "candidate") showCandidateRelationship(edge.record);
    else activateRelationship(edge.id);
  };

  const drawRelationshipGraph = () => {
    edgeFrame = 0;
    edgeTimer = 0;
    relationshipEdgeLayer.replaceChildren(); relationshipLabelLayer.replaceChildren();
    delete graphSurface.dataset.layoutStatus;
    const visibleEdges = visibleGraphEdges();
    relationshipGraph.hidden = visibleEdges.length === 0;
    if (!visibleEdges.length) return;
    const surfaceRect = graphSurface.getBoundingClientRect();
    const width = Math.max(1, graphSurface.clientWidth); const height = Math.max(1, graphSurface.clientHeight);
    relationshipGraph.setAttribute("viewBox", `0 0 ${width} ${height}`);
    relationshipGraph.setAttribute("width", String(width)); relationshipGraph.setAttribute("height", String(height));
    const layoutFailures = [];
    const positionedEdges = visibleEdges.map((edge, routeIndex) => {
      const sourceCard = grid.querySelector(`[data-module-id="${CSS.escape(edge.sourceId)}"]`);
      const targetCard = grid.querySelector(`[data-module-id="${CSS.escape(edge.targetId)}"]`);
      if (!sourceCard || !targetCard) return null;
      const points = edgePath(edge, sourceCard, targetCard, surfaceRect, width, routeIndex);
      if (!points) {
        layoutFailures.push({ edge, reason: "没有不穿过其他节点的安全路径" });
        return null;
      }
      return { edge, points };
    }).filter(Boolean);
    const occupiedLabels = [];
    for (const { edge, points } of positionedEdges) {
      const start = points[0]; const end = points[points.length - 1];
      const pathData = points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
      const group = document.createElementNS(relationshipGraph.namespaceURI, "g");
      group.dataset.sourceNodeId = edge.sourceId; group.dataset.targetNodeId = edge.targetId;
      if (edge.semantic === "decomposition") group.dataset.decompositionId = edge.id;
      else if (edge.kind === "candidate") group.dataset.collaborationId = edge.id;
      else group.dataset.relationshipId = edge.id;
      const title = document.createElementNS(relationshipGraph.namespaceURI, "title");
      const sourceName = moduleById[edge.sourceId].name; const targetName = moduleById[edge.targetId].name;
      const candidateType = edge.semantic === "decomposition" ? "候选分解" : "候选协作";
      const trustClass = projectedReviewClass(edge.record.review);
      const trustLabel = projectedReviewLabel(edge.record.review);
      title.textContent = `${sourceName} 到 ${targetName}，${edge.kind === "candidate" ? candidateType : "正式关系"}·${trustLabel}`;
      const hit = document.createElementNS(relationshipGraph.namespaceURI, "path");
      hit.setAttribute("d", pathData); hit.setAttribute("class", "graph-edge-hit");
      const line = document.createElementNS(relationshipGraph.namespaceURI, "path");
      const relationshipKind = edge.semantic === "decomposition"
        ? "decomposition"
        : edge.semantic === "collaboration" ? "collaboration" : "formal";
      line.setAttribute("d", pathData); line.setAttribute("class", `graph-edge ${edge.kind} ${relationshipKind} review-${trustClass}`.trim());
      line.setAttribute(
        "marker-end",
        trustClass === "approved" ? "url(#reviewed-arrowhead)" : "url(#candidate-arrowhead)",
      );
      hit.addEventListener("click", () => activateGraphEdge(edge));
      group.append(title, hit, line); relationshipEdgeLayer.append(group);

      const labelText = edge.kind === "candidate" ? edge.record.name : edge.record.presentation_zh.relationship_name;
      const label = text("button", labelText, `graph-edge-label ${edge.kind} ${relationshipKind} review-${trustClass}`.trim());
      label.type = "button";
      label.classList.toggle("corridor", points.length > 2);
      if (edge.semantic === "decomposition") label.dataset.decompositionId = edge.id;
      else if (edge.kind === "candidate") label.dataset.collaborationId = edge.id;
      else label.dataset.relationshipId = edge.id;
      label.setAttribute("aria-label", `${sourceName}到${targetName}，${labelText}，${trustLabel}，打开关系证据`);
      const otherPaths = positionedEdges
        .filter((positioned) => positioned.edge.id !== edge.id)
        .map((positioned) => positioned.points);
      const labelPlaced = placeGraphLabel(
        label, points, otherPaths, width, height, surfaceRect, occupiedLabels,
      );
      if (!labelPlaced) layoutFailures.push({ edge, reason: "没有不冲突的标签位置" });
      label.addEventListener("click", () => activateGraphEdge(edge));
    }
    if (layoutFailures.length) {
      graphSurface.dataset.layoutStatus = "failed";
      const descriptions = layoutFailures.map(({ edge, reason }) => {
        const name = edge.kind === "candidate"
          ? edge.record.name
          : edge.record.presentation_zh.relationship_name;
        return `${name}（${reason}）`;
      });
      const failure = text(
        "p",
        `关系布局失败：${descriptions.join("；")}。请调整窗口或页面布局后重试。`,
        "graph-layout-error",
      );
      failure.setAttribute("role", "alert");
      relationshipLabelLayer.append(failure);
    }
  };

  const scheduleRelationshipGraph = () => {
    if (edgeFrame) cancelAnimationFrame(edgeFrame);
    if (edgeTimer) clearTimeout(edgeTimer);
    edgeFrame = requestAnimationFrame(drawRelationshipGraph);
    edgeTimer = window.setTimeout(() => {
      if (!edgeFrame) return;
      cancelAnimationFrame(edgeFrame); drawRelationshipGraph();
    }, 40);
  };

  const renderNavigation = () => {
    const view = currentView(); const atTop = view.id === projection.root_view_id;
    breadcrumb.replaceChildren();
    state.navigationPath.forEach((frame, index) => {
      if (index) breadcrumb.append(text("span", "›", "crumb-separator"));
      breadcrumb.append(text("span", viewById[frame.viewId].display_name, index === state.navigationPath.length - 1 ? "crumb current" : "crumb"));
    });
    bottomNavigation.hidden = atTop;
    const scopeAppearsInView = view.appearances.some((appearance) => appearance.node_id === view.scope_node_id);
    heroTitle.textContent = atTop ? "顶层架构" : (scopeAppearsInView ? moduleById[view.scope_node_id]?.name : null) || view.display_name;
    heroTitle.classList.toggle("internal-view-title", !atTop);
    const visibleReviews = [
      ...view.appearances.map((appearance) => moduleById[appearance.node_id]?.review),
      ...view.relationship_ids.map((relationshipId) => projection.relationship_index[relationshipId]?.review),
    ].filter(Boolean);
    const reviewLabels = [...new Set(visibleReviews.map(projectedReviewLabel))];
    heroStatus.hidden = !reviewLabels.length;
    heroStatus.textContent = reviewLabels.length ? `对象可信状态：${reviewLabels.join(" / ")}` : "";
    heroLede.textContent = view.coverage_note;
    modulesHeading.textContent = atTop ? "七个顶层模块" : "候选职责与组件";
  };

  const cardFor = (module, appearance, isChild, isScopeNode = false) => {
    const article = document.createElement("article"); article.className = "module-card interactive-module";
    article.dataset.moduleId = module.id; article.tabIndex = 0; article.setAttribute("role", "button");
    if (isScopeNode) {
      article.dataset.scopeNode = "true";
      article.classList.add("scope-node-card");
      article.append(text("span", "当前范围 · 父节点", "scope-node-label"));
    }
    const primaryAction = appearance.primary_action;
    const opensView = primaryAction?.kind === "open-view";
    const expanded = state.expandedNodeIds.includes(module.id);
    article.classList.toggle("expanded", expanded); article.setAttribute("aria-expanded", String(expanded));
    article.setAttribute("aria-label", `${module.name}，${primaryAction?.destination_label || (opensView ? "进入下一视图" : "打开详情")}，右键打开节点操作`);
    if (isChild && module.node_type) article.append(text("span", `候选${candidateKindFor(module)}`, "candidate-node-type"));
    article.append(text("h3", module.name));
    const inlineDetail = document.createElement("div"); inlineDetail.className = "module-inline-detail"; inlineDetail.hidden = !expanded;
    inlineDetail.append(text("p", module.responsibility, "module-responsibility"));
    if (!isChild && modulePlainExplanations[module.id]) inlineDetail.append(text("p", modulePlainExplanations[module.id], "module-plain-explanation"));
    if (isChild && module.plain_explanation) inlineDetail.append(text("p", module.plain_explanation, "candidate-plain-explanation"));
    inlineDetail.addEventListener("click", (event) => event.stopPropagation()); article.append(inlineDetail);
    const statuses = document.createElement("div"); statuses.className = "statuses";
    if (isChild) {
      if (nodeReviewFor(module)) {
        statuses.append(text("span", assignmentLabels[module.source_assignment.status], "candidate-card-status source-assignment-status"));
        appendNodeReviewStatuses(statuses, module);
      } else {
        statuses.append(text("span", projectedReviewStatus(candidateKindFor(module), module.review), `status ${projectedReviewClass(module.review)}`), text("span", assignmentLabels[module.source_assignment.status], `status coverage-${module.source_assignment.status}`));
      }
    }
    else statuses.append(text("span", "逻辑定义：已批准", "status approved"), text("span", coverageLabels[module.source_coverage.status] || "源码覆盖：未知", `status coverage-${module.source_coverage.status}`));
    article.append(statuses);
    const activate = () => {
      dialogOpener = article;
      dispatch({ type: "APPEARANCE_PRIMARY", nodeId: module.id, primaryAction }); render();
      if (state.detailModuleId) showModuleDetail(moduleById[state.detailModuleId]);
      if (primaryAction?.kind === "open-source-trail") {
        showSourceTrailEvidence(primaryAction.target_evidence_id, { kind: "candidate-node", module });
      }
    };
    article.addEventListener("click", activate);
    article.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); }
      if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
        event.preventDefault(); const rect = article.getBoundingClientRect();
        dispatch({ type: "CONTEXT_OPEN", moduleId: module.id, x: rect.left + 24, y: rect.top + 24 }); renderContextMenu();
      }
    });
    article.addEventListener("contextmenu", (event) => {
      event.preventDefault(); dispatch({ type: "CONTEXT_OPEN", moduleId: module.id, x: event.clientX, y: event.clientY }); renderContextMenu();
    });
    return article;
  };

  const renderModules = () => {
    const view = currentView(); const atTop = view.id === projection.root_view_id;
    const visible = view.appearances.map((appearance) => ({ appearance, module: moduleById[appearance.node_id] }));
    const includesScopeNode = visible.some(({ module }) => module.id === view.scope_node_id);
    const nonScopeNodeIds = visible
      .map(({ module }) => module.id)
      .filter((nodeId) => nodeId !== view.scope_node_id);
    const decomposedTargets = new Set(view.relationship_ids.flatMap((relationshipId) => {
      const reference = projection.relationship_index[relationshipId];
      const record = relationshipRecordById[relationshipId];
      return reference?.semantic === "decomposition"
          && record?.source_node_id === view.scope_node_id
        ? [record.target_node_id]
        : [];
    }));
    const decompositionScope = includesScopeNode
      && nonScopeNodeIds.length > 0
      && nonScopeNodeIds.every((nodeId) => decomposedTargets.has(nodeId));
    grid.classList.toggle("top-level-grid", atTop);
    grid.classList.toggle("candidate-graph-grid", !atTop);
    grid.classList.toggle("production-candidate-grid", view.scope_node_id === "production-control");
    grid.classList.toggle("decomposition-scope-grid", decompositionScope);
    grid.replaceChildren(); visible.forEach(({ appearance, module }) => (
      grid.append(cardFor(module, appearance, !atTop, module.id === view.scope_node_id))
    ));
    moduleCount.textContent = includesScopeNode
      ? `1 个当前范围 · ${visible.length - 1} 个候选节点`
      : `${visible.length} 个${atTop ? "模块" : "候选节点"}`;
    const collaborations = view.relationship_ids
      .filter((relationshipId) => Boolean(projection.relationship_index[relationshipId]?.object_key))
      .map((relationshipId) => relationshipRecordById[relationshipId]);
    candidateCollaborations.hidden = collaborations.length === 0;
    document.querySelector("#candidate-collaborations-heading").textContent = "候选关系";
    candidateCollaborationList.replaceChildren();
    for (const collaboration of collaborations) {
      const button = document.createElement("button"); button.type = "button";
      button.className = `candidate-collaboration review-${projectedReviewClass(collaboration.review)}`;
      button.setAttribute(
        "aria-label",
        `${collaboration.name}，${projectedReviewLabel(collaboration.review)}，打开关系证据`,
      );
      button.append(
        text("span", projectedReviewStatus(collaboration.semantic === "decomposition" ? "分解" : "协作", collaboration.review), `status ${projectedReviewClass(collaboration.review)}`),
        text("h4", collaboration.name),
        text("p", collaboration.professional_description, "candidate-collaboration-professional"),
        text("p", collaboration.plain_explanation, "candidate-plain-explanation"),
        text("span", `源码依据：${collaboration.evidence_ids.length} 条`, "candidate-evidence-count")
      );
      button.addEventListener("click", () => showCandidateRelationship(collaboration));
      candidateCollaborationList.append(button);
    }
  };

  const renderRelationships = () => {
    relationList.replaceChildren(); relationshipCount.textContent = `${relationships.length} 条关系`;
    for (const relation of relationships) {
      const guide = relation.presentation_zh; const button = document.createElement("button"); button.type = "button";
      const reviewClass = relation.review.state === "needs-review"
        ? "needs-review"
        : projectedReviewClass(relation.review);
      button.className = `relationship-line review-${reviewClass}`; button.dataset.relationshipId = relation.id;
      const arrow = text("span", "→", "relation-arrow"); arrow.setAttribute("aria-hidden", "true");
      const reviewLabel = relation.review.result === "APPROVED" && relation.review.state === "current"
        ? `当前已审核 · ${projectedReviewLabel(relation.review)}`
        : projectedReviewLabel(relation.review);
      button.append(text("span", guide.source_module_name, "relation-node"), arrow, text("span", guide.target_module_name, "relation-node"), text("span", `${guide.relationship_name} · ${relation.type}`, "relation-type"), text("span", reviewLabel, "relation-status"));
      button.setAttribute("aria-label", `${guide.source_module_name}到${guide.target_module_name}，打开同一关系证据详情`);
      button.addEventListener("click", () => { dispatch({ type: "RELATIONSHIP_PRIMARY", relationshipId: relation.id }); showRelationship(relationshipById[state.relationshipId]); });
      relationList.append(button);
    }
  };

  const renderContextMenu = () => {
    if (!state.contextMenu) { contextMenu.hidden = true; return; }
    contextMenu.hidden = false; contextMenu.dataset.moduleId = state.contextMenu.moduleId;
    contextDetailToggle.hidden = false;
    contextDetailToggle.textContent = state.expandedNodeIds.includes(state.contextMenu.moduleId) ? "关闭展示" : "详细展示";
    const maxLeft = Math.max(8, window.innerWidth - contextMenu.offsetWidth - 8);
    const maxTop = Math.max(8, window.innerHeight - contextMenu.offsetHeight - 8);
    contextMenu.style.left = `${Math.min(state.contextMenu.x, maxLeft)}px`; contextMenu.style.top = `${Math.min(state.contextMenu.y, maxTop)}px`;
    contextDetailToggle.focus({ preventScroll: true });
  };
  const render = () => {
    renderNavigation(); renderModules(); renderContextMenu(); scheduleRelationshipGraph();
    if (state.focusNodeId) {
      const focusNodeId = state.focusNodeId;
      window.setTimeout(() => grid.querySelector(`[data-module-id="${CSS.escape(focusNodeId)}"]`)?.focus({ preventScroll: true }), 0);
      dispatch({ type: "FOCUS_RESTORED" });
    }
  };

  backButton.addEventListener("click", () => { dispatch({ type: "BACK" }); render(); });
  homeButton.addEventListener("click", () => { dispatch({ type: "HOME" }); render(); });
  contextDetailToggle.addEventListener("click", () => {
    const moduleId = state.contextMenu?.moduleId; if (!moduleId) return;
    dispatch({ type: "NODE_DETAIL_TOGGLE", moduleId }); render();
    grid.querySelector(`[data-module-id="${CSS.escape(moduleId)}"]`)?.focus({ preventScroll: true });
  });
  contextEvidence.addEventListener("click", () => {
    const moduleId = state.contextMenu?.moduleId;
    const module = moduleById[moduleId]; if (!module) return;
    dialogOpener = grid.querySelector(`[data-module-id="${CSS.escape(moduleId)}"]`);
    if (module.object_key?.kind === "candidate-node") {
      dispatch({ type: "MENU_EVIDENCE", relationshipId: null }); renderContextMenu(); showModuleDetail(module); return;
    }
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
  const restoreDialogOpener = () => {
    const opener = dialogOpener;
    dialogOpener = null;
    window.setTimeout(() => {
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    }, 20);
  };
  dialog.addEventListener("close", () => {
    clearDefinitionFileState();
    dispatch({ type: "DETAIL_CLOSED" });
    restoreDialogOpener();
  });
  dialog.addEventListener("cancel", () => {
    clearDefinitionFileState();
    restoreDialogOpener();
  });
  dialog.querySelector(".dialog-close")?.addEventListener("click", () => {
    clearDefinitionFileState();
    restoreDialogOpener();
  });
  window.addEventListener("resize", scheduleRelationshipGraph);
  window.addEventListener("scroll", scheduleRelationshipGraph, { passive: true });
  if (typeof ResizeObserver === "function") new ResizeObserver(scheduleRelationshipGraph).observe(graphSurface);

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
