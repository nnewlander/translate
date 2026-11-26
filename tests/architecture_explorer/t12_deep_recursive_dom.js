(() => {
  "use strict";
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);

  card("production-control").click();
  card("worker-launch-boundary").click();
  card("supervisor-process-launch-construction").click();

  window.setTimeout(async () => {
    const crumbs = Array.from(document.querySelectorAll("#breadcrumb .crumb")).map((item) => item.textContent);
    assert(crumbs.length === 4, `test-only recursion did not reach four Views: ${crumbs}`);
    assert(card("test-only-depth-four"), "test-only fourth-depth Node is missing");
    assert(document.querySelector("#relationship-graph .graph-edge.candidate"), "deep View bypassed the shared T7 renderer");
    const scopeId = "supervisor-process-launch-construction";
    const dialog = document.querySelector("#relationship-detail");
    const declaredAction = () => dialog.querySelector(".view-navigation-link");
    const currentView = () => document.querySelector("#breadcrumb .current").textContent;
    const back = () => document.querySelector("#back-button").click();
    const wait = () => new Promise((resolve) => setTimeout(resolve, 40));
    card(scopeId).click();
    assert(dialog.open && currentView() === "测试专用第四层 View", "ordinary scope activation must open detail");
    const perspective = declaredAction();
    assert(perspective?.textContent === "查看测试专用组件视角", "the configured Perspective must remain explicitly available");
    perspective.click();
    assert(!dialog.open && currentView() === "测试专用组件视角", "explicit Perspective did not open");
    back();
    await wait();
    assert(currentView() === "测试专用第四层 View", "Perspective BACK missed the origin View");
    assert(document.activeElement === card(scopeId), "Perspective BACK did not restore focus");
    card(scopeId).click();
    declaredAction().click();
    card("test-only-depth-four").click();
    assert(dialog.open && currentView() === "测试专用组件视角", "a current-View target must open node detail");
    assert(!declaredAction(), "the current View needs no return control");
    dialog.querySelector(".dialog-close").click();
    card(scopeId).click();
    assert(dialog.open && currentView() === "测试专用组件视角", "ordinary scope activation must not return to an ancestor");
    const returnLink = declaredAction();
    assert(returnLink?.textContent === "返回测试专用第四层", "the declared ancestor Perspective must remain explicitly available");
    returnLink.click();
    await wait();
    assert(!dialog.open && currentView() === "测试专用第四层 View", "explicit Perspective return missed the ancestor");
    assert(document.querySelectorAll("#breadcrumb .crumb").length === 4, "explicit return must reuse the existing path");
    assert(document.activeElement === card(scopeId), "explicit return did not restore the ancestor Appearance focus");
    document.querySelector("#back-button").click();
    window.setTimeout(() => {
      assert(document.activeElement?.dataset.moduleId === "supervisor-process-launch-construction", "deep BACK did not restore focus");
      document.querySelector("#home-button").click();
      assert(card("production-control"), "deep HOME did not return to root");
      document.documentElement.dataset.t12DeepRecursive = "pass";
    }, 30);
  }, 30);
})();
