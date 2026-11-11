(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));

  try {
    document.querySelector('[data-module-id="document-understanding"]').click();
    await nextPaint();
    assert(document.querySelectorAll("#module-grid .module-card").length === 2, "candidate nodes disappeared with the collaboration record");
    assert(document.querySelector("#relationship-graph").hidden, "an inferred candidate graph was drawn without a collaboration record");
    assert(document.querySelectorAll(".graph-edge.candidate").length === 0, "an inferred candidate edge was drawn without a collaboration record");
    assert(document.querySelectorAll(".graph-edge-label.candidate").length === 0, "an inferred candidate label was drawn without a collaboration record");
    assert(document.querySelector("#candidate-collaborations").hidden, "the empty candidate collaboration fallback remains visible");

    document.querySelector('[data-module-id="document-package-extraction"]').click();
    const detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("当前发布数据中没有与此模块直接关联的关系证据"), "empty relationship evidence notice disappeared");
    assert(detail.textContent.includes("这不等于模块没有关系"), "empty collaboration state overclaims that no relationship exists");

    document.documentElement.dataset.t7NoCollaboration = "pass";
  } catch (error) {
    document.documentElement.dataset.t7NoCollaboration = "fail";
    document.documentElement.dataset.t7NoCollaborationError = String(error?.message || error);
  }
})();
