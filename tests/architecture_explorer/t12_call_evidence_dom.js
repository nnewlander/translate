(async () => {
  "use strict";
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);

  card("production-control").click();
  card("worker-launch-boundary").click();
  const architecturePath = document.querySelector("#breadcrumb").textContent;

  card("supervisor-process-launch-construction").click();
  assert(document.querySelector("#relationship-detail").open, "Candidate detail did not open");
  assert(document.querySelector("#detail-content").textContent.includes("Python 静态调用事实"), "Call Fact Evidence is not displayable");
  assert(!document.querySelector("#detail-content").textContent.includes("无法解析源码依据"), "Call Fact Evidence was not resolved through the shared index");
  document.querySelector("#relationship-detail").close();

  card("worker-production-entry-guard").click();
  assert(document.querySelector("#detail-content").dataset.sourceTrailLevel === "call", "open-source-trail did not open the Call Fact");
  assert(document.querySelector("#breadcrumb").textContent === architecturePath, "Source Trail changed the architecture path");
  assert(document.querySelector("#detail-content").textContent.includes("未确认目标"), "unresolved Call Fact did not retain its uncertainty");
  assert(!document.querySelector("#detail-content .call-target"), "unresolved Call Fact inferred a target navigation");
  const dialog = document.querySelector("#relationship-detail");
  dialog.querySelector(".dialog-close").click();
  const scope = card("worker-launch-boundary");
  scope.click();
  assert(dialog.open && !document.querySelector("#detail-content").dataset.sourceTrailLevel,
    "ordinary scope activation must show detail before opening its declared Source Trail");
  const explicit = dialog.querySelector(".view-navigation-link");
  assert(explicit?.textContent === "查看当前范围的源码调用", "the declared Source Trail control is missing");
  explicit.click();
  assert(document.querySelector("#detail-content").dataset.sourceTrailLevel === "call",
    "explicit Source Trail did not use the declared Call Fact target");
  dialog.querySelector(".definition-back").click();
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert(dialog.open && document.querySelector("#detail-content h2").textContent === "Worker 启动边界",
    "Source Trail return did not restore the scope detail");
  assert(document.querySelector("#breadcrumb").textContent === architecturePath,
    "Source Trail return changed the architecture path");
  assert(document.activeElement === dialog.querySelector(".view-navigation-link"),
    "returning from explicit Source Trail did not restore its detail button focus");
  dialog.querySelector(".dialog-close").click();
  assert(document.activeElement === scope && document.querySelector("#breadcrumb").textContent === architecturePath,
    "closing explicit Source Trail did not restore the scope Appearance");
  document.documentElement.dataset.t12CallEvidence = "pass";
})().catch((error) => {
  document.documentElement.dataset.t12CallError = error.message;
});
