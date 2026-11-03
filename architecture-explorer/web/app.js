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

  const expandable = (label, className = "technical-details") => {
    const details = document.createElement("details");
    details.className = className;
    details.append(text("summary", label));
    return details;
  };

  const evidenceCard = (evidence, guide) => {
    const location = `${evidence.path}:${evidence.current_location.line_start} · symbol: ${evidence.symbol}`;
    const card = document.createElement("article");
    card.className = "evidence-card";
    card.append(
      text("p", evidence.source_role === "production" ? "生产源码证据" : "辅助测试证据 · 非生产调用事实", "evidence-role"),
      text("h4", guide.name),
      text("p", guide.explanation, "evidence-explanation"),
      text("p", guide.supports, "evidence-supports")
    );
    const raw = expandable("展开原始技术证据", "evidence-technical");
    const facts = document.createElement("dl");
    facts.className = "raw-evidence-facts";
    for (const [term, value] of [
      ["技术种类", evidence.evidence_kind],
      ["Evidence ID", evidence.evidence_id],
      ["源码定位", location],
    ]) facts.append(text("dt", term), text("dd", value));
    const snippet = text("pre", evidence.snippet);
    const button = text("button", "复制定位信息", "copy-location");
    button.type = "button";
    button.addEventListener("click", () => copyLocation(location, button));
    raw.append(facts, snippet, button);
    card.append(raw);
    return card;
  };

  const showRelationship = (relation) => {
    const guide = relation.presentation_zh;
    detailContent.replaceChildren();
    detailContent.append(
      text("p", "架构关系解读", "eyebrow"),
      text("h2", `${guide.source_module_name} → ${guide.target_module_name}`, "detail-title"),
      text("p", guide.relationship_name, "plain-relation-name"),
      text("p", guide.summary, "relationship-summary")
    );

    const statusGrid = document.createElement("section");
    statusGrid.className = "meaning-grid";
    const meanings = [
      [reviewLabels[relation.review_status] || "未知", guide.status_explanations[relation.review_status] || "当前没有可用的有效审核结论。"],
      ["静态条件可达", guide.status_explanations[relation.code_reachability.status]],
      ["真实运行状态未采集", guide.status_explanations[relation.runtime_state]],
    ];
    for (const [title, description] of meanings) {
      const item = document.createElement("article");
      item.append(text("h3", title), text("p", description));
      statusGrid.append(item);
    }
    detailContent.append(statusGrid);

    const limits = document.createElement("aside");
    limits.className = "evidence-limits";
    limits.append(text("h3", "这条关系不能证明什么"));
    const limitList = document.createElement("ul");
    for (const item of guide.limits) limitList.append(text("li", item));
    limits.append(limitList);
    detailContent.append(limits, text("h3", "关键证据链"));

    const evidenceById = Object.fromEntries(relation.evidence.map((item) => [item.evidence_id, item]));
    for (const group of guide.evidence_groups) {
      const section = document.createElement("section");
      section.className = "evidence-group";
      section.append(text("h4", group.title), text("p", group.description, "group-description"));
      for (const evidenceId of group.evidence_ids) {
        section.append(evidenceCard(evidenceById[evidenceId], guide.evidence_items[evidenceId]));
      }
      detailContent.append(section);
    }

    if (relation.supporting_evidence.length) {
      const auxiliary = expandable("辅助测试证据（3 条，非生产调用事实）", "auxiliary-evidence");
      auxiliary.append(text("p", "这些测试只提供支持性上下文，不属于 Owner 批准的 13 条生产调用证据，也不能单独证明生产启动发生。", "group-description"));
      for (const evidence of relation.supporting_evidence) {
        auxiliary.append(evidenceCard(evidence, guide.evidence_items[evidence.evidence_id]));
      }
      detailContent.append(auxiliary);
    }

    detailContent.append(text("h3", "静态条件"));
    const conditions = document.createElement("ul");
    for (const item of guide.static_conditions) conditions.append(text("li", item));
    detailContent.append(conditions, text("h3", "仍然未知的事项"));
    const unknowns = document.createElement("ul");
    for (const item of guide.retained_unknowns) unknowns.append(text("li", item));
    detailContent.append(unknowns);

    const original = expandable("原始审核与英文技术字段", "original-review");
    const facts = document.createElement("dl");
    facts.className = "detail-facts";
    for (const [term, value] of [
      ["Direction", relation.direction],
      ["Relationship type", relation.type],
      ["Review status", relation.review_status],
      ["Code reachability", relation.code_reachability.status],
      ["Runtime state", relation.runtime_state],
      ["Evidence continuity", relation.source_continuity.status],
    ]) facts.append(text("dt", term), text("dd", value));
    original.append(facts, text("h4", "原始 Owner 审核文字"), text("pre", relation.architecture_explanation || "—", "review-notes"));
    detailContent.append(original);
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  };

  for (const relation of relationships) {
    const guide = relation.presentation_zh;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `relationship-line review-${relation.review_status}`;
    button.dataset.relationshipId = relation.id;
    const source = text("span", guide.source_module_name, "relation-node");
    const arrow = text("span", "→", "relation-arrow");
    arrow.setAttribute("aria-hidden", "true");
    const target = text("span", guide.target_module_name, "relation-node");
    button.append(source, arrow, target, text("span", `${guide.relationship_name} · ${relation.type}`, "relation-type"), text("span", reviewLabels[relation.review_status] || "未知", "relation-status"));
    button.setAttribute("aria-label", `${guide.source_module_name}到${guide.target_module_name}，${guide.relationship_name}，${reviewLabels[relation.review_status] || "未知"}，打开证据详情`);
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
