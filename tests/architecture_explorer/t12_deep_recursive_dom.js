(() => {
  "use strict";
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);

  card("production-control").click();
  card("worker-launch-boundary").click();
  card("supervisor-process-launch-construction").click();

  window.setTimeout(() => {
    const crumbs = Array.from(document.querySelectorAll("#breadcrumb .crumb")).map((item) => item.textContent);
    assert(crumbs.length === 4, `test-only recursion did not reach four Views: ${crumbs}`);
    assert(card("test-only-depth-four"), "test-only fourth-depth Node is missing");
    assert(document.querySelector("#relationship-graph .graph-edge.candidate"), "deep View bypassed the shared T7 renderer");
    document.querySelector("#back-button").click();
    window.setTimeout(() => {
      assert(document.activeElement?.dataset.moduleId === "supervisor-process-launch-construction", "deep BACK did not restore focus");
      document.querySelector("#home-button").click();
      assert(card("production-control"), "deep HOME did not return to root");
      document.documentElement.dataset.t12DeepRecursive = "pass";
    }, 30);
  }, 30);
})();
