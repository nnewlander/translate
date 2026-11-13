(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);

  try {
    const expectedProjection = window.T8_EXPECTED_PROJECTION;
    const expectedReviewLabel = window.T8_EXPECTED_REVIEW_LABEL;
    const expectedState = expectedProjection === "Pending Review"
      ? "not-reviewed · not-assessed"
      : "current · exact-match";
    card("production-control").click();
    await nextPaint();

    let target = card("worker-launch-boundary");
    assert(target.textContent.includes("候选"), "candidate fact boundary disappeared");
    assert(target.textContent.includes("源码归属：部分核验"), "source assignment is not visible");
    assert(target.textContent.includes(`人工审核：${expectedReviewLabel}`), "concise Chinese review status is not visible");
    assert(!target.textContent.includes("职责状态：未审核"), "duplicate candidate review status remains inline");
    assert(!target.textContent.includes("节点决定"), "technical decision label remains inline");
    assert(!target.textContent.includes("Node Review State"), "technical state label remains inline");
    assert(!target.textContent.includes(expectedProjection), "raw projection remains inline");
    assert(!target.textContent.includes(expectedState), "raw node state remains inline");
    assert(target.querySelectorAll(".statuses > *").length === 2, "reviewed candidate card does not have exactly two status rows");
    assert(!target.textContent.includes("IDENTITY"), "Review Claims leaked into the inline card");
    assert(!target.querySelector(".candidate-evidence-count"), "Evidence count leaked inline");
    assert(target.scrollWidth <= target.clientWidth + 1, "reviewed candidate card overflows horizontally");
    const targetRect = target.getBoundingClientRect();
    for (const row of target.querySelectorAll(".statuses > *")) {
      const rowRect = row.getBoundingClientRect();
      assert(rowRect.right <= targetRect.right + 1, "review status row overflows the candidate card");
    }

    const reviewData = window.ARCHITECTURE_EXPLORER_DATA.navigation.drilldowns
      .flatMap((item) => item.children)
      .find((item) => item.id === "worker-launch-boundary").node_review;
    const originalProjection = reviewData.projection;
    const presentationCases = [
      ["Pending Review", "待审核"],
      ["Current · Approved", "已批准 · 当前有效"],
      ["Current · Rejected", "已拒绝 · 当前有效"],
      ["Historical · Approved", "曾批准 · 需复核"],
      ["Historical · Rejected", "曾拒绝 · 需复核"],
      ["Unknown", "审核有效性未知"],
    ];
    for (const [projection, label] of presentationCases) {
      reviewData.projection = projection;
      target.click();
      await nextPaint();
      target = card("worker-launch-boundary");
      assert(target.textContent.includes(`人工审核：${label}`), `missing Chinese review label for ${projection}`);
      assert(!target.textContent.includes(projection), `raw projection leaked for ${projection}`);
      assert(target.scrollWidth <= target.clientWidth + 1, `candidate card overflows for ${projection}`);
    }
    reviewData.projection = originalProjection;
    target.click();
    await nextPaint();
    target = card("worker-launch-boundary");

    const otherCards = Array.from(document.querySelectorAll("#module-grid .module-card"))
      .filter((item) => item !== target);
    assert(otherCards.every((item) => !item.querySelector(".node-review-status")), "another candidate received a node review");

    target.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: 48, clientY: 48,
    }));
    document.querySelector("#context-evidence").click();
    await nextPaint();

    const detail = document.querySelector("#detail-content");
    assert(detail.querySelector("#detail-title")?.textContent === "Worker 启动边界", "wrong detail opened");
    const expectedDetailDecision = window.T8_EXPECTED_DECISION_RESULT || "Pending Review";
    assert(detail.textContent.includes(expectedDetailDecision), "full Decision disappeared from detail");
    assert(detail.textContent.includes(expectedState), "independent Node Review State disappeared from detail");
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
