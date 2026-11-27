(() => {
  "use strict";

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };

  try {

  const productionControl = document.querySelector('[data-module-id="production-control"]');
  assert(productionControl, "production-control Appearance is missing from the root View");
  productionControl.click();

  const expectedNodeIds = [
    "deterministic-worker-wrapper",
    "production-control-adapters",
    "state-and-control-decisions",
    "worker-launch-boundary",
    "supervisor-orchestration",
  ];
  const actualNodeIds = Array.from(document.querySelectorAll("#module-grid [data-module-id]"))
    .map((item) => item.dataset.moduleId);
  assert(
    JSON.stringify(actualNodeIds) === JSON.stringify(expectedNodeIds),
    `renderer ignored normalized Appearance order: ${actualNodeIds}`,
  );
  assert(document.querySelector(".hero h1")?.textContent === "生产调度与控制", "hero ignored the View scope Node name");
  const subtitle = document.querySelector("#view-subtitle");
  assert(subtitle && !subtitle.hidden && subtitle.textContent === "规范化投影视图", "subtitle ignored current View display name");
  assert(document.querySelector(".crumb.current")?.textContent === "规范化投影视图", "breadcrumb ignored current View display name");
  const relationshipStatuses = Array.from(document.querySelectorAll(".candidate-collaboration .status"))
    .map((item) => item.textContent);
  assert(relationshipStatuses.some((item) => item.includes("已批准 · 当前有效")), "approved Relationship review projection is missing");
  assert(relationshipStatuses.some((item) => item.includes("REJECTED · 需要复核")), "needs-review Relationship projection is missing");
  const viewStatus = document.querySelector("#view-status")?.textContent || "";
  for (const label of ["未审核", "已批准 · 当前有效", "已拒绝 · 当前有效", "APPROVED · 需要复核", "APPROVED · unknown"]) {
    assert(viewStatus.includes(label), `View status omitted ${label}`);
  }
  const relationshipButtons = Array.from(document.querySelectorAll(".candidate-collaboration"));
  assert(relationshipButtons.some((item) => item.getAttribute("aria-label")?.includes("已批准 · 当前有效")), "approved keyboard trust label is missing");
  assert(relationshipButtons.some((item) => item.getAttribute("aria-label")?.includes("REJECTED · 需要复核")), "needs-review keyboard trust label is missing");
  assert(document.querySelector(".graph-edge.review-approved"), "approved Relationship has no approved visual style");
  assert(document.querySelector(".candidate-collaboration.review-needs-review"), "needs-review Relationship has no needs-review visual style");
  document.documentElement.dataset.t11ViewProjection = "pass";
  } catch (error) {
    document.documentElement.dataset.t11ViewProjection = "fail";
    document.documentElement.dataset.t11ViewProjectionError = String(error?.message || error);
  }
})();
