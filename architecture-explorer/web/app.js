(() => {
  "use strict";

  const data = window.ARCHITECTURE_EXPLORER_DATA;
  const error = document.querySelector("#load-error");
  if (!data || !data.architecture || !Array.isArray(data.architecture.modules)) {
    error.hidden = false;
    error.textContent = "Explorer 数据缺失或格式无效。请重新生成离线站点。";
    return;
  }

  const coverageLabels = {
    unverified: "源码覆盖：未核验",
    partial: "源码覆盖：部分核验",
    verified: "源码覆盖：已核验",
  };
  const reviewLabels = {
    candidate: "候选",
    "current-reviewed": "当前已审核",
    "needs-review": "需要复核",
    unknown: "未知",
  };
  const modules = data.architecture.modules;
  document.querySelector("#module-count").textContent = `${modules.length} 个模块`;

  const metadata = document.querySelector("#snapshot-metadata");
  const rows = [
    ["Revision", data.snapshot.revision],
    ["工作区", data.snapshot.worktree.state === "clean" ? "干净" : "有未提交变更"],
    ["扫描器", data.snapshot.scanner_version],
    ["扫描时间", data.snapshot.scanned_at],
    ["确定性摘要", data.deterministic_content_sha256],
  ];
  for (const [term, description] of rows) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = term;
    dd.textContent = description;
    metadata.append(dt, dd);
  }

  const grid = document.querySelector("#module-grid");
  for (const [index, module] of modules.entries()) {
    const article = document.createElement("article");
    article.className = "module-card";
    article.dataset.moduleId = module.id;

    const ordinal = document.createElement("span");
    ordinal.className = "ordinal";
    ordinal.textContent = String(index + 1).padStart(2, "0");
    const title = document.createElement("h3");
    title.textContent = module.name;
    const responsibility = document.createElement("p");
    responsibility.textContent = module.responsibility;
    const statuses = document.createElement("div");
    statuses.className = "statuses";
    const approval = document.createElement("span");
    approval.className = "status approved";
    approval.textContent = "逻辑定义：已批准";
    const coverage = document.createElement("span");
    coverage.className = `status coverage-${module.source_coverage.status}`;
    coverage.textContent = coverageLabels[module.source_coverage.status] || "源码覆盖：未知";
    statuses.append(approval, coverage);
    article.append(ordinal, title, responsibility, statuses);
    grid.append(article);
  }

  const relationships = Array.isArray(data.architecture.relationships) ? data.architecture.relationships : [];
  document.querySelector("#relationship-count").textContent = `${relationships.length} 条关系`;
  const relationList = document.querySelector("#relationship-list");
  const dialog = document.querySelector("#relationship-detail");
  const detailContent = document.querySelector("#detail-content");

  const text = (tag, value, className) => {
    const element = document.createElement(tag);
    element.textContent = value;
    if (className) element.className = className;
    return element;
  };

  const copyLocation = async (location, button) => {
    try {
      await navigator.clipboard.writeText(location);
    } catch (_) {
      const area = document.createElement("textarea");
      area.value = location;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    button.textContent = "已复制";
    window.setTimeout(() => { button.textContent = "复制定位信息"; }, 1200);
  };

  const showRelationship = (relation) => {
    detailContent.replaceChildren();
    detailContent.append(
      text("p", "统一源码证据详情", "eyebrow"),
      text("h2", `${relation.direction}`, "detail-title")
    );
    const facts = document.createElement("dl");
    facts.className = "detail-facts";
    const rows = [
      ["审核状态", reviewLabels[relation.review_status] || "未知"],
      ["关系类型", relation.type],
      ["代码可达", relation.code_reachability.status],
      ["真实运行", relation.runtime_state === "NOT_COLLECTED" ? "未采集" : relation.runtime_state],
      ["证据连续性", relation.source_continuity.status],
    ];
    for (const [term, value] of rows) facts.append(text("dt", term), text("dd", value));
    detailContent.append(facts, text("h3", "架构解释"), text("p", relation.architecture_explanation || "—"));
    detailContent.append(text("h3", "静态可达条件"));
    const conditions = document.createElement("ul");
    for (const item of relation.code_reachability.proven_conditions || []) conditions.append(text("li", item));
    detailContent.append(conditions, text("h3", "保留未知事项"));
    const unknowns = document.createElement("ul");
    for (const item of relation.code_reachability.retained_unknowns || []) unknowns.append(text("li", item));
    detailContent.append(unknowns, text("h3", `源码证据 · ${relation.evidence.length}`));
    for (const evidence of relation.evidence) {
      const location = `${evidence.path}:${evidence.current_location.line_start} · symbol: ${evidence.symbol}`;
      const card = document.createElement("article");
      card.className = "evidence-card";
      card.append(
        text("p", evidence.source_role === "production" ? "源码事实 · 生产" : "源码事实 · 辅助", "evidence-role"),
        text("h4", evidence.evidence_kind),
        text("code", evidence.evidence_id, "evidence-id"),
        text("p", `${location} · ${evidence.symbol}`, "evidence-location")
      );
      const snippet = text("pre", evidence.snippet);
      const button = text("button", "复制定位信息", "copy-location");
      button.type = "button";
      button.addEventListener("click", () => copyLocation(location, button));
      card.append(snippet, button);
      detailContent.append(card);
    }
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };

  for (const relation of relationships) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `relationship-line review-${relation.review_status}`;
    button.dataset.relationshipId = relation.id;
    const source = text("span", relation.source_module, "relation-node");
    const arrow = text("span", "→", "relation-arrow");
    arrow.setAttribute("aria-hidden", "true");
    const target = text("span", relation.target_module, "relation-node");
    button.append(source, arrow, target, text("span", relation.type, "relation-type"), text("span", reviewLabels[relation.review_status] || "未知", "relation-status"));
    button.setAttribute("aria-label", `${relation.direction}，${reviewLabels[relation.review_status] || "未知"}，打开证据详情`);
    button.addEventListener("click", () => showRelationship(relation));
    relationList.append(button);
  }

  const workflowToggle = document.querySelector("#workflow-toggle");
  workflowToggle.addEventListener("click", () => {
    const active = workflowToggle.getAttribute("aria-pressed") !== "true";
    workflowToggle.setAttribute("aria-pressed", String(active));
    document.body.classList.toggle("workflow-highlight", active);
    workflowToggle.textContent = active ? "取消流程高亮" : "高亮最小生产流程";
  });
})();
