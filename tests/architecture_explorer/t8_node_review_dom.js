(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);

  try {
    const expectedProjection = window.T8_EXPECTED_PROJECTION;
    const expectedReviewLabel = window.T8_EXPECTED_REVIEW_LABEL;
    const expectedState = window.T8_EXPECTED_STATE || (expectedProjection === "Pending Review"
      ? "not-reviewed · not-assessed"
      : "current · exact-match");
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

    const targetData = window.ARCHITECTURE_EXPLORER_DATA.navigation.drilldowns
      .flatMap((item) => item.children)
      .find((item) => item.id === "worker-launch-boundary");
    const reviewData = targetData.node_review;
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
    const evidenceCards = Array.from(detail.querySelectorAll(".candidate-evidence-card"));
    const evidenceTechnicalSections = evidenceCards.map((item) => item.querySelector(".candidate-evidence-technical"));
    const nodeReviewTechnical = detail.querySelector(".node-review-technical");
    const nodeReviewStatus = detail.querySelector(".node-detail-review-status");
    assert(evidenceCards.length === 3, "Candidate Evidence is not isolated to three records");
    assert(evidenceTechnicalSections.every((item) => item && !item.open), "raw Candidate Evidence is not collapsed by default");
    assert(nodeReviewTechnical && !nodeReviewTechnical.open, "professional node-review information is not collapsed by default");
    const isCurrentApproved = expectedProjection === "Current · Approved"
      && reviewData.state.status === "current"
      && reviewData.state.continuity === "exact-match";
    assert(nodeReviewStatus, "node-review status badge is missing from detail");
    assert(nodeReviewStatus.classList.contains("approved") === isCurrentApproved, "node-review status badge has misleading approved semantics");
    assert(nodeReviewStatus.classList.contains(isCurrentApproved ? "approved" : "candidate"), "node-review status badge does not reuse the expected status style");
    if (!isCurrentApproved) {
      const approvedReference = document.createElement("span");
      approvedReference.className = "status approved";
      document.body.append(approvedReference);
      const approvedBackground = getComputedStyle(approvedReference).backgroundColor;
      const actualBackground = getComputedStyle(nodeReviewStatus).backgroundColor;
      approvedReference.remove();
      assert(actualBackground !== approvedBackground, "non-approved node-review status still uses the approved visual color");
    }
    targetData.source_assignment.evidence_ids.forEach((evidenceId, index) => {
      assert(evidenceTechnicalSections[index].textContent.includes(evidenceId), `Candidate Evidence order changed at ${index}`);
    });
    assert(evidenceCards[0].textContent.includes("scripts/production/production_campaign_supervisor.ps1"), "launch-boundary evidence path is missing");
    assert(evidenceCards[0].textContent.includes("Start-DeterministicWorkerProcess"), "launch-boundary evidence symbol is missing");
    assert(evidenceCards[0].textContent.includes("受控进程启动边界"), "launch-boundary evidence meaning is missing");
    assert(evidenceCards[1].textContent.includes("$deterministicWorkerScript"), "worker-script binding symbol is missing");
    assert(evidenceCards[1].textContent.includes("确定性 Worker 包装脚本"), "worker-script binding meaning is missing");
    assert(evidenceCards[2].textContent.includes("scripts/production/run_qwen_deterministic_worker.py"), "wrapper evidence path is missing");
    assert(evidenceCards[2].textContent.includes("Worker 主入口"), "wrapper evidence meaning is missing");
    if (expectedProjection === "Current · Approved") {
      assert(detail.textContent.includes("节点职责审核通过（当前有效）"), "current approved responsibility status is missing");
      assert(!detail.textContent.includes("职责状态：未审核"), "conflicting unreviewed responsibility status remains in current approved detail");
      assert(detail.textContent.includes("源码归属：部分确认"), "partial source assignment is not clearly separated");
      assert(detail.textContent.includes("候选协作：未审核"), "candidate collaboration review status is not clearly separated");
      assert(detail.textContent.includes("受控发车闸门"), "approved everyday analogy is missing");
      assert(detail.textContent.includes("不证明 Worker 实际启动"), "static-evidence runtime limitation is missing");
    } else assert(!detail.textContent.includes("节点职责审核通过（当前有效）"), "non-approved projection received current approval wording");
    assert(detail.textContent.includes(expectedDetailDecision), "full Decision disappeared from detail");
    assert(detail.textContent.includes(expectedState), "independent Node Review State disappeared from detail");
    nodeReviewTechnical.querySelector("summary").click();
    assert(nodeReviewTechnical.open, "professional node-review information cannot be expanded");
    const claims = nodeReviewTechnical.querySelectorAll(".node-review-claim");
    if (expectedProjection === "Pending Review") {
      assert(claims.length === 0, "pending projection fabricated Review Claims");
      assert(detail.textContent.includes("无可信审核决定"), "pending trust explanation is missing");
      const rawReview = nodeReviewTechnical.querySelector(".node-review-json");
      assert(rawReview && !rawReview.open, "pending full State JSON is not available in a collapsed disclosure");
      assert(rawReview.textContent.includes("Node Review State"), "pending full State JSON heading is missing");
      assert(rawReview.textContent.includes('"status": "not-reviewed"'), "pending full State JSON content is missing");
      assert(!rawReview.textContent.includes("Node Review Decision"), "pending projection fabricated a Decision JSON record");
    } else {
      assert(claims.length === 8, "eight Review Claims are not shown");
      assert(detail.textContent.includes("Classification Rationale"), "classification rationale is missing");
      assert(detail.textContent.includes("顶层模块源码覆盖是否完成"), "retained unknowns are missing");
      assert(detail.textContent.includes("github-comment:6516894339"), "T3 Decision dependency is missing");
      assert(detail.textContent.includes("13 条正式 Evidence"), "formal Evidence isolation is missing");
      if (window.T8_EXPECTED_REASON) {
        assert(detail.textContent.includes(window.T8_EXPECTED_REASON), "continuity reason is missing");
      }
      if (window.T8_EXPECTS_CURRENT_LOCATORS) {
        assert(detail.querySelectorAll(".node-review-current-locations li").length === 3, "current Evidence locators are missing");
      }
    }
    evidenceTechnicalSections[0].querySelector("summary").click();
    assert(evidenceTechnicalSections[0].open, "raw Candidate Evidence cannot be expanded");
    assert(evidenceTechnicalSections[0].textContent.includes("Evidence ID"), "expanded Candidate Evidence lost its Evidence ID");
    assert(evidenceTechnicalSections[0].querySelector("pre"), "expanded Candidate Evidence lost its raw snippet");
    assert(evidenceTechnicalSections[0].querySelector(".copy-location"), "expanded Candidate Evidence lost copy-location access");
    const dialog = document.querySelector("#relationship-detail");
    assert(document.documentElement.scrollWidth <= window.innerWidth + 1, "node detail creates page-level horizontal overflow");
    assert(dialog.scrollWidth <= dialog.clientWidth + 1, "expanded node detail creates horizontal overflow");
    assert(!Array.from(detail.querySelectorAll("button")).some((button) => /批准|拒绝|刷新/.test(button.textContent)), "detail exposes a review write control");

    dialog.querySelector(".dialog-close").click();
    await nextPaint();
    const legacyData = window.ARCHITECTURE_EXPLORER_DATA.navigation.drilldowns
      .flatMap((item) => item.children)
      .find((item) => item.id === "state-and-control-decisions");
    legacyData.node_review = structuredClone(reviewData);
    const legacyCard = card("state-and-control-decisions");
    legacyCard.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: 48, clientY: 48,
    }));
    document.querySelector("#context-evidence").click();
    await nextPaint();
    const legacyDetail = document.querySelector("#detail-content");
    assert(!legacyDetail.querySelector(".node-review-technical"), "progressive disclosure leaked to a non-target node");
    assert(!legacyDetail.querySelector(".node-review-json"), "new raw review JSON leaked to a non-target node");
    assert(!legacyDetail.textContent.includes("审核源码快照"), "new reviewed snapshot row leaked to a non-target node");
    if (reviewData.decision) {
      assert(Array.from(legacyDetail.querySelectorAll("h3")).some((heading) => heading.textContent === "Review Claims"), "legacy Review Claims heading changed for a non-target node");
    } else {
      assert(!Array.from(legacyDetail.querySelectorAll("h3")).some((heading) => heading.textContent.includes("Review Claims")), "pending non-target node fabricated Review Claims");
    }
    delete legacyData.node_review;

    document.documentElement.dataset.t8NodeReview = "pass";
  } catch (error) {
    document.documentElement.dataset.t8NodeReview = "fail";
    document.documentElement.dataset.t8NodeReviewError = String(error?.message || error);
  }
})();
