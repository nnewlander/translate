(async () => {
  "use strict";

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);
  const breadcrumb = () => document.querySelector("#breadcrumb").textContent;
  const overlaps = (left, right, inset = 1) => !(
    left.right <= right.left + inset
      || left.left >= right.right - inset
      || left.bottom <= right.top + inset
      || left.top >= right.bottom - inset
  );
  const pathPoints = (group) => {
    const values = group.querySelector(".graph-edge").getAttribute("d")
      .match(/-?\d+(?:\.\d+)?/g).map(Number);
    const points = [];
    for (let index = 0; index < values.length; index += 2) {
      points.push({ x: values[index], y: values[index + 1] });
    }
    return points;
  };
  const pointToSegmentDistance = (point, start, end) => {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const lengthSquared = dx * dx + dy * dy;
    const ratio = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, (
      (point.x - start.x) * dx + (point.y - start.y) * dy
    ) / lengthSquared));
    return Math.hypot(point.x - (start.x + ratio * dx), point.y - (start.y + ratio * dy));
  };
  const pointInside = (point, rect) => (
    point.x >= rect.left && point.x <= rect.right
      && point.y >= rect.top && point.y <= rect.bottom
  );
  const segmentIntersectsRect = (start, end, rect) => {
    if (pointInside(start, rect) || pointInside(end, rect)) return true;
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const p = [-dx, dx, -dy, dy];
    const q = [
      start.x - rect.left,
      rect.right - start.x,
      start.y - rect.top,
      rect.bottom - start.y,
    ];
    let entry = 0;
    let exit = 1;
    for (let index = 0; index < p.length; index += 1) {
      if (p[index] === 0) {
        if (q[index] < 0) return false;
        continue;
      }
      const ratio = q[index] / p[index];
      if (p[index] < 0) entry = Math.max(entry, ratio);
      else exit = Math.min(exit, ratio);
      if (entry > exit) return false;
    }
    return true;
  };
  const expectedRelationships = new Map([
    [
      "candidate-decomposition:translation-routing-and-acquisition-to-semantic-unit-route-decision",
      {
        source: "translation-routing-and-acquisition",
        target: "semantic-unit-route-decision",
        label: "候选获取分解为语义单元路由判定",
        attribute: "decompositionId",
      },
    ],
    [
      "candidate-decomposition:translation-routing-and-acquisition-to-translation-provider-dispatch-and-raw-projection",
      {
        source: "translation-routing-and-acquisition",
        target: "translation-provider-dispatch-and-raw-projection",
        label: "候选获取分解为 Provider 分派与 RAW 投影",
        attribute: "decompositionId",
      },
    ],
    [
      "candidate-collaboration:semantic-unit-route-decision-to-translation-provider-dispatch-and-raw-projection",
      {
        source: "semantic-unit-route-decision",
        target: "translation-provider-dispatch-and-raw-projection",
        label: "路由决定交给 Provider 分派与 RAW 投影",
        attribute: "collaborationId",
      },
    ],
  ]);
  const assertGraphGeometry = () => {
    const cards = [
      card("translation-routing-and-acquisition"),
      card("semantic-unit-route-decision"),
      card("translation-provider-dispatch-and-raw-projection"),
    ];
    const groups = Array.from(document.querySelectorAll("#relationship-edge-layer > g"));
    const labels = Array.from(document.querySelectorAll("#relationship-label-layer .graph-edge-label"));
    assert(groups.length === 3, `expected three graph relationships, found ${groups.length}`);
    assert(labels.length === 3, `expected three graph labels, found ${labels.length}`);

    for (const [relationshipId, expected] of expectedRelationships) {
      const selector = `[data-${expected.attribute.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}="${CSS.escape(relationshipId)}"]`;
      const group = document.querySelector(`#relationship-edge-layer > g${selector}`);
      const label = document.querySelector(`#relationship-label-layer .graph-edge-label${selector}`);
      assert(group, `missing graph edge ${relationshipId}`);
      assert(label, `missing graph label ${relationshipId}`);
      assert(
        group.dataset.sourceNodeId === expected.source && group.dataset.targetNodeId === expected.target,
        `${relationshipId} has incorrect source or target`,
      );
      assert(label.textContent === expected.label, `${relationshipId} has incorrect label`);
      assert(
        group.querySelector(".graph-edge")?.getAttribute("marker-end") === "url(#candidate-arrowhead)",
        `${relationshipId} is missing its candidate arrowhead`,
      );

      const surfaceRect = document.querySelector("#graph-surface").getBoundingClientRect();
      const labelRect = label.getBoundingClientRect();
      const labelBounds = {
        left: labelRect.left - surfaceRect.left,
        right: labelRect.right - surfaceRect.left,
        top: labelRect.top - surfaceRect.top,
        bottom: labelRect.bottom - surfaceRect.top,
      };
      const labelCenter = {
        x: (labelBounds.left + labelBounds.right) / 2,
        y: (labelBounds.top + labelBounds.bottom) / 2,
      };
      const points = pathPoints(group);
      const ownSegments = points.slice(1).map((end, index) => [points[index], end]);
      assert(
        ownSegments.some(([start, end]) => (
          pointToSegmentDistance(labelCenter, start, end) <= 1.5
            && !pointInside(start, labelBounds)
            && !pointInside(end, labelBounds)
        )),
        `${relationshipId} label is not centered on a connected segment of its own path`,
      );
      for (const otherGroup of groups.filter((candidate) => candidate !== group)) {
        const otherPoints = pathPoints(otherGroup);
        assert(
          !otherPoints.slice(1).some((end, index) => (
            segmentIntersectsRect(otherPoints[index], end, labelBounds)
          )),
          `${relationshipId} label covers a different relationship path`,
        );
      }
    }

    for (const label of labels) {
      assert(
        cards.every((node) => !overlaps(label.getBoundingClientRect(), node.getBoundingClientRect())),
        `${label.textContent} overlaps a node card`,
      );
    }
    for (let left = 0; left < labels.length; left += 1) {
      for (let right = left + 1; right < labels.length; right += 1) {
        assert(
          !overlaps(labels[left].getBoundingClientRect(), labels[right].getBoundingClientRect()),
          `${labels[left].textContent} overlaps ${labels[right].textContent}`,
        );
      }
    }
  };
  const click = (selector, message) => {
    const target = document.querySelector(selector);
    assert(target, message);
    target.click();
    return target;
  };

  try {
    assert(card("translation-execution"), "system overview lacks translation-execution");
    card("translation-execution").click();
    const scope = card("translation-routing-and-acquisition");
    assert(scope, "translation-execution View did not expose the routing scope");
    scope.click();
    await nextPaint();

    const route = card("semantic-unit-route-decision");
    const dispatch = card("translation-provider-dispatch-and-raw-projection");
    assert(route && dispatch, "Ticket 61 internal Candidate Nodes are missing");
    assert(
      document.querySelector(".hero h1").textContent === "语义路由与候选获取",
      "Ticket 61 title did not use the approved plain-language name",
    );
    const viewStatus = document.querySelector("#view-status");
    assert(
      viewStatus && !viewStatus.hidden && viewStatus.textContent === "Candidate · Pending Review",
      "Candidate/Pending Review was not exposed independently from the title",
    );
    const parentScope = card("translation-routing-and-acquisition");
    assert(
      parentScope?.dataset.scopeNode === "true"
        && parentScope.querySelector(".scope-node-label")?.textContent === "当前范围 · 父节点",
      "the parent Appearance was not identified as the current scope",
    );
    assert(
      breadcrumb().includes("整体架构")
        && breadcrumb().includes("翻译执行与模型接入")
        && breadcrumb().includes("语义路由与候选获取"),
      `Ticket 61 breadcrumb is incomplete: ${breadcrumb()}`,
    );
    assert(
      route.textContent.includes("职责状态：未审核")
        && dispatch.textContent.includes("职责状态：未审核"),
      "a Ticket 61 child inherited reviewed state",
    );
    assert(
      document.querySelectorAll("#candidate-collaboration-list .candidate-collaboration").length === 3,
      "Ticket 61 View did not expose exactly its three approved-scope relationships",
    );
    assert(
      document.querySelector("#candidate-collaboration-list").textContent.includes("分解状态：未审核")
        && document.querySelector("#candidate-collaboration-list").textContent.includes("协作状态：未审核"),
      "Ticket 61 relationship semantics or Pending Review state are missing",
    );
    assert(
      !document.querySelector("#candidate-collaboration-list").textContent.includes("providers.mt.translate"),
      "unresolved Provider dispatch was promoted into an architecture relationship",
    );
    assert(
      document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      "Ticket 61 View has horizontal overflow",
    );
    assertGraphGeometry();

    const internalPath = breadcrumb();
    card("translation-routing-and-acquisition").click();
    await nextPaint();
    assert(document.querySelector("#relationship-detail").open, "internal Scope did not default to details");
    assert(breadcrumb() === internalPath, "internal Scope recursively opened its own View");
    document.querySelector("#relationship-detail").close();
    await nextPaint();

    click("#back-button", "BACK control is missing");
    await nextPaint();
    assert(card("translation-routing-and-acquisition"), "BACK did not return to translation-execution");
    assert(
      document.activeElement?.dataset.moduleId === "translation-routing-and-acquisition",
      "BACK did not restore the routing scope Appearance focus",
    );
    const keyboardScope = card("translation-routing-and-acquisition");
    keyboardScope.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Enter", code: "Enter", bubbles: true, cancelable: true,
    }));
    await nextPaint();
    assert(card("semantic-unit-route-decision"), "keyboard action did not enter the Ticket 61 View");

    const dispatchCard = card("translation-provider-dispatch-and-raw-projection");
    dispatchCard.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, clientX: 20, clientY: 20,
    }));
    click("#context-evidence", "Evidence context action is missing");
    await nextPaint();
    const detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("runtime receiver dispatch"), "retained runtime receiver unknown is not visible");
    const browseUnresolved = detail.querySelector(
      '.browse-call-fact[data-evidence-id="evidence:sha256:991cf2dc83a87d39bd8950c9e6b62cb695517ce6aef5abd60ab0189436075c42"]',
    );
    assert(browseUnresolved, "unresolved Provider Evidence lacks its Source Trail action");
    browseUnresolved.click();
    await nextPaint();
    assert(detail.dataset.sourceTrailLevel === "call", "Source Trail did not open the unresolved call");
    assert(detail.textContent.includes("未确认目标"), "unresolved call lost its trust boundary");
    assert(!detail.querySelector(".call-target"), "unresolved call exposed a guessed target");
    assert(breadcrumb() === internalPath, "Source Trail changed the architecture View path");
    click(".dialog-close", "Source Trail close control is missing");
    await nextPaint();
    assert(
      document.activeElement?.dataset.moduleId === "translation-provider-dispatch-and-raw-projection",
      "Source Trail close did not restore the dispatch Appearance focus",
    );

    click("#home-button", "HOME control is missing");
    await nextPaint();
    assert(card("translation-execution"), "HOME did not return to the system overview");
    document.documentElement.setAttribute("data-ticket-61-semantic-routing", "pass");
  } catch (error) {
    document.documentElement.setAttribute("data-ticket-61-semantic-routing", "fail");
    document.documentElement.setAttribute("data-ticket-61-semantic-routing-error", String(error?.message || error));
  }
})();
