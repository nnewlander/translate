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
  const reviewLabels = { candidate: "候选", "current-reviewed": "当前已审核", "needs-review": "需要复核", unknown: "未知" };
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
  const backButton = document.querySelector("#back-button");
  const homeButton = document.querySelector("#home-button");
  const relationList = document.querySelector("#relationship-list");
  const relationshipCount = document.querySelector("#relationship-count");
  const dialog = document.querySelector("#relationship-detail");
  const detailContent = document.querySelector("#detail-content");
  const contextMenu = document.querySelector("#module-context-menu");
  const contextEvidence = document.querySelector("#context-evidence");

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

  const evidenceCard = (evidence, guide) => {
    const location = `${evidence.path}:${evidence.current_location.line_start} · symbol: ${evidence.symbol}`;
    const card = document.createElement("article"); card.className = "evidence-card";
    card.append(
      text("p", evidence.source_role === "production" ? "生产源码证据" : "辅助测试证据 · 非生产调用事实", "evidence-role"),
      text("h4", guide.name), text("p", guide.explanation, "evidence-explanation"),
      text("p", guide.supports, "evidence-supports")
    );
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

  const showRelationship = (relation) => {
    const guide = relation.presentation_zh;
    detailContent.replaceChildren(); detailContent.dataset.relationshipId = relation.id;
    const title = text("h2", `${guide.source_module_name} → ${guide.target_module_name}`, "detail-title"); title.id = "detail-title";
    detailContent.append(
      text("p", "架构关系解读", "eyebrow"),
      title,
      text("p", guide.relationship_name, "plain-relation-name"), text("p", guide.summary, "relationship-summary")
    );
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
    backButton.hidden = atTop; homeButton.hidden = atTop;
    heroTitle.textContent = atTop ? "顶层架构" : drilldown.name;
    heroLede.textContent = atTop ? "已批准的逻辑职责，与源码覆盖核验分别呈现。" : drilldown.coverage_note;
    modulesHeading.textContent = atTop ? "七个顶层模块" : "候选逻辑职责";
  };

  const cardFor = (module, index, isChild) => {
    const article = document.createElement("article"); article.className = "module-card interactive-module";
    article.dataset.moduleId = module.id; article.tabIndex = 0; article.setAttribute("role", "button");
    const hasChildren = Boolean(drilldownByParent[module.id]);
    article.setAttribute("aria-label", `${module.name}，${hasChildren ? "左键下钻" : "左键打开详情"}，右键查看关系与源码证据`);
    article.append(text("span", String(index + 1).padStart(2, "0"), "ordinal"), text("h3", module.name), text("p", module.responsibility));
    const statuses = document.createElement("div"); statuses.className = "statuses";
    if (isChild) statuses.append(text("span", "职责状态：候选", "status candidate"), text("span", assignmentLabels[module.source_assignment.status], `status coverage-${module.source_assignment.status}`));
    else statuses.append(text("span", "逻辑定义：已批准", "status approved"), text("span", coverageLabels[module.source_coverage.status] || "源码覆盖：未知", `status coverage-${module.source_coverage.status}`));
    article.append(statuses, text("span", hasChildren ? "左键下钻 · 右键查看证据" : "左键查看详情 · 右键查看证据", "interaction-hint"));
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

  renderRelationships(); render();
})();
