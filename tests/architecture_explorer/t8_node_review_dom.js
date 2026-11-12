(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);

  try {
    const expectedProjection = window.T8_EXPECTED_PROJECTION;
    const expectedState = expectedProjection === "Pending Review"
      ? "not-reviewed · not-assessed"
      : "current · exact-match";
    card("production-control").click();
    await nextPaint();

    const target = card("worker-launch-boundary");
    assert(target.textContent.includes(expectedProjection), "node decision projection is not visible");
    assert(target.textContent.includes(expectedState), "independent node state is not visible");
    assert(target.textContent.includes("候选"), "candidate fact boundary disappeared");
    assert(!target.textContent.includes("IDENTITY"), "Review Claims leaked into the inline card");
    assert(!target.querySelector(".candidate-evidence-count"), "Evidence count leaked inline");

    const otherCards = Array.from(document.querySelectorAll("#module-grid .module-card"))
      .filter((item) => item !== target);
    assert(otherCards.every((item) => !item.querySelector(".node-review-decision")), "another candidate received a node review");

    target.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: 48, clientY: 48,
    }));
    document.querySelector("#context-evidence").click();
    await nextPaint();

    const detail = document.querySelector("#detail-content");
    assert(detail.querySelector("#detail-title")?.textContent === "Worker 启动边界", "wrong detail opened");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 3, "Candidate Evidence is not isolated to three records");
    const claims = detail.querySelectorAll(".node-review-claim");
    if (expectedProjection === "Pending Review") {
      assert(claims.length === 0, "pending projection fabricated Review Claims");
      assert(detail.textContent.includes("没有可用的可信 Owner/admin"), "pending trust explanation is missing");
    } else {
      assert(claims.length === 8, "eight Review Claims are not shown");
      assert(detail.textContent.includes("Classification Rationale"), "classification rationale is missing");
      assert(detail.textContent.includes("顶层模块源码覆盖是否完成"), "retained unknowns are missing");
      assert(detail.textContent.includes("github-comment:6516894339"), "T3 Decision dependency is missing");
      assert(detail.textContent.includes("13 条正式 Evidence"), "formal Evidence isolation is missing");
    }
    assert(!Array.from(detail.querySelectorAll("button")).some((button) => /批准|拒绝|刷新/.test(button.textContent)), "detail exposes a review write control");

    document.documentElement.dataset.t8NodeReview = "pass";
  } catch (error) {
    document.documentElement.dataset.t8NodeReview = "fail";
    document.documentElement.dataset.t8NodeReviewError = String(error?.message || error);
  }
})();
