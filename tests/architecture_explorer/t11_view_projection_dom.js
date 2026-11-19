(() => {
  "use strict";

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };

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
  assert(document.querySelector(".hero h1")?.textContent === "规范化投影视图", "hero ignored current View display name");
  assert(document.querySelector(".crumb.current")?.textContent === "规范化投影视图", "breadcrumb ignored current View display name");
  document.documentElement.dataset.t11ViewProjection = "pass";
})();
