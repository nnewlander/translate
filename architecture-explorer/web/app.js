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
})();
