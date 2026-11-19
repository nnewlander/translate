(() => {
  "use strict";

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);
  const pathText = () => document.querySelector("#breadcrumb").textContent;

  card("production-control").click();
  assert(card("worker-launch-boundary"), "production-control View did not open");
  card("worker-launch-boundary").dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));

  assert(card("supervisor-process-launch-construction"), "internal Candidate View did not open");
  assert(card("worker-production-entry-guard"), "Worker production entry guard is missing");
  assert(
    pathText().includes("整体架构")
      && pathText().includes("生产调度与控制")
      && pathText().includes("Worker 启动边界内部 Candidate View"),
    `complete breadcrumb is missing: ${pathText()}`,
  );
  assert(
    card("worker-production-entry-guard").textContent.includes("职责状态：未审核"),
    "new child inherited an approved state",
  );
  assert(
    document.querySelector("#candidate-collaboration-list").textContent.includes("分解状态：未审核")
      && document.querySelector("#candidate-collaboration-list").textContent.includes("协作状态：未审核"),
    "candidate decomposition and collaboration are not visibly distinct",
  );
  assert(document.documentElement.scrollWidth <= window.innerWidth, "recursive View has horizontal overflow");
  assert(document.querySelectorAll("#candidate-collaboration-list .candidate-collaboration").length === 4, "recursive View did not expose all four visible relationships");

  const architecturePath = pathText();
  card("supervisor-process-launch-construction").click();
  assert(document.querySelector("#relationship-detail").open, "Appearance without a primary action did not open details");
  document.querySelector("#relationship-detail").close();
  assert(pathText() === architecturePath, "default detail action changed the architecture View path");
  const entry = card("worker-production-entry-guard");
  entry.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 20, clientY: 20 }));
  document.querySelector("#context-evidence").click();
  assert(document.querySelector("#relationship-detail").open, "deep Evidence did not open");
  assert(pathText() === architecturePath, "Evidence changed the architecture View path");
  const browse = document.querySelector("#detail-content .browse-definitions");
  assert(browse, "Definition Evidence did not expose Source Trail");
  browse.click();
  assert(document.querySelector("#detail-content").dataset.sourceTrailLevel === "file", "Source Trail did not open from the deepest View");
  assert(pathText() === architecturePath, "Source Trail changed the architecture View path");
  document.querySelector("#relationship-detail").close();

  window.setTimeout(() => {
    assert(document.activeElement?.dataset.moduleId === "worker-production-entry-guard", "Source Trail close did not restore focus");
    document.querySelector("#back-button").click();
    window.setTimeout(() => {
      assert(card("worker-launch-boundary"), "BACK did not return exactly one View frame");
      assert(document.activeElement?.dataset.moduleId === "worker-launch-boundary", "BACK did not restore origin Appearance focus");
      document.querySelector("#home-button").click();
      assert(card("production-control"), "HOME did not return to the system overview");
      card("production-control").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      assert(card("worker-launch-boundary"), "Enter did not invoke the same explicit action as pointer activation");
      document.documentElement.dataset.t12RecursiveView = "pass";
    }, 30);
  }, 30);
})();
