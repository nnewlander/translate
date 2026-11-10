(async () => {
  "use strict";

  const expectedByParent = {
    "document-understanding": {
      nodes: ["document-package-extraction", "document-translation-view"],
      collaboration: ["candidate-collaboration:document-extraction-to-view", "document-package-extraction", "document-translation-view", "提取事实交给视图构造"],
    },
    "scope-and-request": {
      nodes: ["scope-logical-work-order-selection", "scope-request-context-assembly"],
      collaboration: ["candidate-collaboration:scope-selection-to-request", "scope-logical-work-order-selection", "scope-request-context-assembly", "选定工作单元后准备请求材料"],
    },
    "translation-execution": {
      nodes: ["translation-routing-and-acquisition", "translation-provider-adapters"],
      collaboration: ["candidate-collaboration:translation-routing-to-provider", "translation-routing-and-acquisition", "translation-provider-adapters", "路由决定衔接提供方适配"],
    },
    "quality-and-repair": {
      nodes: ["quality-deterministic-qa", "quality-semantic-review-and-repair"],
      collaboration: ["candidate-collaboration:quality-qa-to-semantic-repair", "quality-deterministic-qa", "quality-semantic-review-and-repair", "确定性检查衔接语义审核与修复门禁"],
    },
    "production-evidence": {
      nodes: ["checkpoint-evidence-persistence", "checkpoint-manifest-seal"],
      collaboration: ["candidate-collaboration:checkpoint-evidence-to-manifest-seal", "checkpoint-evidence-persistence", "checkpoint-manifest-seal", "证据工件支撑清单封存"],
    },
    "candidate-and-release": {
      nodes: ["candidate-document-projection", "package-preservation-verifier", "release-membership-derivation"],
      collaboration: ["candidate-collaboration:projection-to-package-verification", "candidate-document-projection", "package-preservation-verifier", "候选投影进入包保真校验"],
    },
  };
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const openMenu = (node) => node.dispatchEvent(new MouseEvent("contextmenu", {
    bubbles: true, cancelable: true, clientX: 48, clientY: 48,
  }));
  const coordinates = (path) => {
    const values = path.getAttribute("d").match(/-?\d+(?:\.\d+)?/g).map(Number);
    return { start: { x: values[0], y: values[1] }, end: { x: values[2], y: values[3] } };
  };
  const cardBoundsInSurface = (node) => {
    const surface = document.querySelector("#graph-surface").getBoundingClientRect();
    const bounds = node.getBoundingClientRect();
    return {
      left: bounds.left - surface.left, right: bounds.right - surface.left,
      top: bounds.top - surface.top, bottom: bounds.bottom - surface.top,
    };
  };
  const onBoundary = (point, bounds) => {
    const epsilon = 1.5;
    const withinX = point.x >= bounds.left - epsilon && point.x <= bounds.right + epsilon;
    const withinY = point.y >= bounds.top - epsilon && point.y <= bounds.bottom + epsilon;
    const touchesX = Math.abs(point.x - bounds.left) <= epsilon || Math.abs(point.x - bounds.right) <= epsilon;
    const touchesY = Math.abs(point.y - bounds.top) <= epsilon || Math.abs(point.y - bounds.bottom) <= epsilon;
    return (touchesX && withinY) || (touchesY && withinX);
  };
  const rectangleDistance = (first, second) => {
    const horizontal = Math.max(0, first.left - second.right, second.left - first.right);
    const vertical = Math.max(0, first.top - second.bottom, second.top - first.bottom);
    return Math.hypot(horizontal, vertical);
  };

  const assertCompactCandidateLayout = (expectedCount, referenceWidth) => {
    const candidates = Array.from(document.querySelectorAll("#module-grid .module-card"));
    const gridBounds = document.querySelector("#module-grid").getBoundingClientRect();
    assert(candidates.length === expectedCount, "candidate layout node count changed");
    for (const candidate of candidates) {
      const bounds = candidate.getBoundingClientRect();
      assert(bounds.width <= referenceWidth + 2, `${candidate.dataset.moduleId} is wider than the accepted top-level node`);
      assert(bounds.width >= referenceWidth - 24, `${candidate.dataset.moduleId} became too narrow for its title`);

      const statuses = Array.from(candidate.querySelectorAll(":scope > .statuses > .status"));
      assert(statuses.length === 2, `${candidate.dataset.moduleId} status count changed`);
      const firstStatus = statuses[0].getBoundingClientRect();
      const secondStatus = statuses[1].getBoundingClientRect();
      assert(secondStatus.top >= firstStatus.bottom - 1, `${candidate.dataset.moduleId} statuses are not stacked vertically`);
      assert(Math.abs(secondStatus.left - firstStatus.left) <= 1, `${candidate.dataset.moduleId} statuses are not left aligned`);

      const title = candidate.querySelector("h3");
      assert(title.scrollWidth <= title.clientWidth + 1, `${candidate.dataset.moduleId} title is horizontally clipped`);
      const type = candidate.querySelector(".candidate-node-type");
      const typeBounds = type?.getBoundingClientRect();
      assert(typeBounds && typeBounds.right <= bounds.right && typeBounds.top < title.getBoundingClientRect().bottom, `${candidate.dataset.moduleId} lost its top-right candidate marker`);
    }

    const tops = candidates.map((candidate) => Math.round(candidate.getBoundingClientRect().top));
    if (window.innerWidth >= 1180 || expectedCount === 2) {
      assert(new Set(tops).size === 1, `${expectedCount}-node candidate layout does not share one compact row at ${window.innerWidth}px`);
    } else if (expectedCount === 3) {
      assert(tops[0] === tops[1] && tops[2] > tops[0], "three-node candidate layout does not reflow to two plus one at 960px");
      const lastBounds = candidates[2].getBoundingClientRect();
      const lastCenter = (lastBounds.left + lastBounds.right) / 2;
      const gridCenter = (gridBounds.left + gridBounds.right) / 2;
      assert(Math.abs(lastCenter - gridCenter) <= 2, "three-node candidate layout does not center its final row");
    }
  };

  try {
    const parentId = window.T7_TEST_PARENT;
    const expected = expectedByParent[parentId];
    assert(expected, `unknown candidate module ${parentId}`);
    const acceptedTopLevelWidth = card(parentId).getBoundingClientRect().width;
    card(parentId).click();
    await nextPaint();

    const cards = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(cards.length === expected.nodes.length, `${parentId} node count changed`);
    assert(cards.map((node) => node.dataset.moduleId).join("|") === expected.nodes.join("|"), `${parentId} node IDs changed or reordered`);
    assert(cards.every((node) => !node.querySelector(".ordinal")), `${parentId} still displays meaningless ordinals`);
    assert(cards.every((node) => !node.classList.contains("expanded")), `${parentId} candidates do not start compact`);
    assert(cards.every((node) => node.textContent.includes("状态：未审核")), `${parentId} compact nodes hide candidate trust`);
    assertCompactCandidateLayout(expected.nodes.length, acceptedTopLevelWidth);

    const [collaborationId, sourceId, targetId, collaborationName] = expected.collaboration;
    const group = document.querySelector(`#relationship-edge-layer [data-collaboration-id="${collaborationId}"]`);
    assert(group, `${collaborationId} is not drawn by the shared renderer`);
    assert(group.dataset.sourceNodeId === sourceId, `${collaborationId} source changed`);
    assert(group.dataset.targetNodeId === targetId, `${collaborationId} target changed`);
    const currentEdge = () => document.querySelector(`[data-collaboration-id="${collaborationId}"] .graph-edge.candidate`);
    const currentLabel = () => document.querySelector(`.graph-edge-label[data-collaboration-id="${collaborationId}"]`);
    assert(currentEdge().getAttribute("marker-end") === "url(#candidate-arrowhead)", `${collaborationId} lacks a directed arrowhead`);
    assert(getComputedStyle(currentEdge()).strokeDasharray !== "none", `${collaborationId} is not dashed`);
    assert(currentLabel()?.textContent === collaborationName, `${collaborationId} does not use its existing Chinese name`);
    assert(!currentLabel().textContent.includes("候选·未审核"), `${collaborationId} uses trust state as its main label`);

    const assertGeometry = () => {
      const points = coordinates(currentEdge());
      assert(onBoundary(points.start, cardBoundsInSurface(card(sourceId))), `${collaborationId} source endpoint left its card boundary`);
      assert(onBoundary(points.end, cardBoundsInSurface(card(targetId))), `${collaborationId} target endpoint left its card boundary`);
      const labelBounds = currentLabel().getBoundingClientRect();
      for (const candidate of document.querySelectorAll("#module-grid .module-card")) {
        assert(rectangleDistance(labelBounds, candidate.getBoundingClientRect()) >= 8, `${collaborationId} label crowds a candidate node`);
      }
    };
    assertGeometry();

    for (const nodeId of expected.nodes) {
      openMenu(card(nodeId));
      assert(document.querySelector("#context-detail-toggle").textContent === "详细展示", `${nodeId} lacks inline detail action`);
      document.querySelector("#context-detail-toggle").click();
      await nextPaint();
      assert(card(nodeId).classList.contains("expanded"), `${nodeId} did not expand inline`);
    }
    assert(Array.from(document.querySelectorAll("#module-grid .module-card")).every((node) => node.classList.contains("expanded")), `${parentId} does not support multiple inline expansions`);
    assertCompactCandidateLayout(expected.nodes.length, acceptedTopLevelWidth);
    assertGeometry();

    openMenu(card(sourceId));
    assert(document.querySelector("#context-detail-toggle").textContent === "关闭展示", `${sourceId} lacks independent close action`);
    document.querySelector("#context-detail-toggle").click();
    await nextPaint();
    assert(!card(sourceId).classList.contains("expanded"), `${sourceId} did not collapse independently`);
    assert(card(targetId).classList.contains("expanded"), `${sourceId} collapse also collapsed ${targetId}`);
    assertGeometry();

    currentLabel().click();
    const detail = document.querySelector("#detail-content");
    assert(detail.dataset.collaborationId === collaborationId, `${collaborationId} label opened a different record`);
    assert(detail.textContent.includes(collaborationName), `${collaborationId} detail omits its existing name`);
    assert(detail.textContent.includes("协作状态：未审核"), `${collaborationId} detail omits unreviewed trust`);
    assert(detail.textContent.includes(collaborationId), `${collaborationId} detail omits its stable ID`);
    document.querySelector("#relationship-detail").close();

    openMenu(card(targetId));
    document.querySelector("#context-evidence").click();
    assert(document.querySelector("#detail-content").textContent.includes("源码归属：未核验"), `${targetId} detail promoted source assignment`);
    assert(document.querySelector("#detail-content").textContent.includes(`打开：${collaborationName}`), `${targetId} detail lacks the existing collaboration entry`);
    document.querySelector("#relationship-detail").close();

    for (const width of [1440, 960]) {
      window.resizeTo(width, 900);
      await nextPaint();
      assert(document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1, `${parentId} overflows at ${width}px`);
      assertCompactCandidateLayout(expected.nodes.length, acceptedTopLevelWidth);
      assertGeometry();
    }

    document.documentElement.dataset.t7RemainingCandidate = "pass";
  } catch (error) {
    document.documentElement.dataset.t7RemainingCandidate = "fail";
    document.documentElement.dataset.t7RemainingCandidateError = String(error?.message || error);
  }
})();
