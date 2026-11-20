(() => {
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
  document.documentElement.dataset.t12CallEvidence = "pass";
})();
