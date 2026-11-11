(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 60));
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
  const assertVerticalCandidateStatuses = () => {
    for (const candidate of document.querySelectorAll("#module-grid .module-card")) {
      const statuses = Array.from(candidate.querySelectorAll(":scope > .statuses > .status"));
      assert(statuses.length === 2, `${candidate.dataset.moduleId} status count changed`);
      const first = statuses[0].getBoundingClientRect();
      const second = statuses[1].getBoundingClientRect();
      assert(second.top >= first.bottom - 1, `${candidate.dataset.moduleId} statuses are not stacked vertically`);
      assert(Math.abs(second.left - first.left) <= 1, `${candidate.dataset.moduleId} statuses are not left aligned`);
      const title = candidate.querySelector("h3");
      assert(title.scrollWidth <= title.clientWidth + 1, `${candidate.dataset.moduleId} title is horizontally clipped`);
    }
  };
  const assertCompactProductionLayout = () => {
    const candidates = Array.from(document.querySelectorAll("#module-grid .module-card"));
    const gridBounds = document.querySelector("#module-grid").getBoundingClientRect();
    assert(candidates.length === 5, "production candidate layout node count changed");
    for (const candidate of candidates) {
      const width = candidate.getBoundingClientRect().width;
      assert(width >= 259 && width <= 300, `${candidate.dataset.moduleId} is not close to the accepted top-level node width (${width}px)`);
    }
    const rowGroups = new Map();
    for (const candidate of candidates) {
      const top = Math.round(candidate.getBoundingClientRect().top);
      rowGroups.set(top, [...(rowGroups.get(top) || []), candidate]);
    }
    const rows = Array.from(rowGroups.entries()).sort(([left], [right]) => left - right).map(([, row]) => row);
    const expectedRows = window.innerWidth >= 1180 ? [3, 2] : [2, 2, 1];
    assert(rows.map((row) => row.length).join("|") === expectedRows.join("|"), `production candidate rows changed at ${window.innerWidth}px`);
    const firstRowBounds = rows[0].map((candidate) => candidate.getBoundingClientRect());
    const leftWhitespace = Math.min(...firstRowBounds.map((bounds) => bounds.left)) - gridBounds.left;
    const rightWhitespace = gridBounds.right - Math.max(...firstRowBounds.map((bounds) => bounds.right));
    assert(Math.abs(leftWhitespace - rightWhitespace) <= 2, `production candidate first row leaves asymmetric whitespace at ${window.innerWidth}px`);
    assert(document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1, `production candidate layout overflows at ${window.innerWidth}px`);
  };
  const assertCandidateLabelGeometry = () => {
    const surface = document.querySelector("#graph-surface").getBoundingClientRect();
    const records = Array.from(document.querySelectorAll(".graph-edge-label.candidate"), (label) => {
      const id = label.dataset.collaborationId;
      const edge = document.querySelector(`[data-collaboration-id="${id}"] .graph-edge.candidate`);
      const points = coordinates(edge);
      const bounds = label.getBoundingClientRect();
      const deltaX = points.end.x - points.start.x;
      const deltaY = points.end.y - points.start.y;
      const edgeLength = Math.hypot(deltaX, deltaY);
      const unitX = deltaX / edgeLength;
      const unitY = deltaY / edgeLength;
      return {
        id, bounds,
        centerX: (bounds.left + bounds.right) / 2,
        centerY: (bounds.top + bounds.bottom) / 2,
        midpointX: surface.left + (points.start.x + points.end.x) / 2,
        midpointY: surface.top + (points.start.y + points.end.y) / 2,
        runway: edgeLength / 2 - (Math.abs(unitX) * bounds.width / 2 + Math.abs(unitY) * bounds.height / 2),
      };
    }).sort((left, right) => left.midpointX - right.midpointX);
    assert(records.length === 2, "candidate label geometry requires the two published collaborations");
    assert(rectangleDistance(records[0].bounds, records[1].bounds) >= 24, "candidate relationship labels lack 24px separation");
    for (const record of records) {
      assert(Math.abs(record.centerX - record.midpointX) <= 2, `${record.id} label is not horizontally centered on its line`);
      assert(Math.abs(record.centerY - record.midpointY) <= 2, `${record.id} label is not vertically centered on its line`);
      assert(record.runway >= 12, `${record.id} lacks visible line on both sides of its label`);
      for (const candidate of document.querySelectorAll("#module-grid .module-card")) {
        assert(rectangleDistance(record.bounds, candidate.getBoundingClientRect()) >= 8, `${record.id} label crowds a candidate node`);
      }
    }
  };

  try {
    const expectedViewport = window.T7_EXPECTED_VIEWPORT;
    assert(Array.isArray(expectedViewport), "expected viewport was not provided by the browser harness");
    assert(window.innerWidth === expectedViewport[0] && window.innerHeight === expectedViewport[1], `browser viewport mismatch: expected ${expectedViewport.join("x")}, actual ${window.innerWidth}x${window.innerHeight}`);
    document.querySelector('[data-module-id="production-control"]').click();
    await nextPaint();

    const cards = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(cards.length === 5, "production candidate node count changed");
    assert(cards.every((candidate) => !candidate.querySelector(".ordinal")), "production candidates still show 01-05 ordinals");
    assert(cards.every((card) => !card.classList.contains("expanded")), "candidate nodes must start compact");
    assert(cards.every((card) => card.querySelector(".module-inline-detail")?.hidden), "compact candidates expose detail");
    const compactHeights = cards.map((candidate) => `${candidate.dataset.moduleId}:${candidate.getBoundingClientRect().height}`);
    assert(cards.every((candidate) => candidate.getBoundingClientRect().height <= 165), `production candidate remains taller than the accepted compact node (${compactHeights.join(", ")})`);
    assert(cards.every((candidate) => Number.parseFloat(getComputedStyle(candidate).paddingTop) <= 18), "production candidate retains excessive padding");
    assert(cards.filter((card) => card.textContent.includes("候选职责")).length === 3, "candidate responsibility count changed");
    assert(cards.filter((card) => card.textContent.includes("候选组件")).length === 2, "candidate component count changed");
    assert(card("worker-launch-boundary").textContent.includes("职责状态：未审核"), "worker boundary lost candidate status");
    assert(card("worker-launch-boundary").textContent.includes("源码归属：部分核验"), "worker partial assignment was hidden or promoted");
    assertVerticalCandidateStatuses();
    assertCompactProductionLayout();

    const expected = [
      ["candidate-collaboration:supervisor-to-control-adapters", "supervisor-orchestration", "production-control-adapters", "Supervisor 装载控制适配器"],
      ["candidate-collaboration:supervisor-to-worker-wrapper", "worker-launch-boundary", "deterministic-worker-wrapper", "启动边界指向 Worker 包装入口"],
    ];
    const edges = Array.from(document.querySelectorAll(".graph-edge.candidate"));
    assert(edges.length === 2, "production candidate edge count changed");
    for (const [id, source, target, name] of expected) {
      const group = document.querySelector(`[data-collaboration-id="${id}"]`);
      assert(group, `missing stable candidate collaboration ${id}`);
      assert(group.dataset.sourceNodeId === source, `${id} source changed`);
      assert(group.dataset.targetNodeId === target, `${id} target changed`);
      const edge = group.querySelector(".graph-edge.candidate");
      assert(edge.getAttribute("marker-end") === "url(#candidate-arrowhead)", `${id} lacks directed arrowhead`);
      assert(getComputedStyle(edge).strokeDasharray !== "none", `${id} is not dashed`);
      const points = coordinates(edge);
      assert(Math.hypot(points.end.x - points.start.x, points.end.y - points.start.y) >= 48, `${id} is too short to show a clear dashed line`);
      const label = document.querySelector(`.graph-edge-label[data-collaboration-id="${id}"]`);
      assert(label?.textContent === name, `${id} does not prioritize its existing collaboration name`);
      assert(!label.textContent.includes("候选·未审核"), `${id} still uses trust state as its main label`);
    }
    assert(document.querySelector("#graph-legend")?.textContent.includes("候选关系·未经审核"), "candidate graph lacks a concise unreviewed legend");
    assertCandidateLabelGeometry();

    const candidateStroke = getComputedStyle(edges[0]).stroke;
    const candidateLabelBorder = getComputedStyle(document.querySelector(".graph-edge-label.candidate")).borderColor;
    document.querySelector("#workflow-toggle").click();
    assert(getComputedStyle(edges[0]).stroke === candidateStroke, "workflow highlight promoted candidate edge colour");
    assert(getComputedStyle(document.querySelector(".graph-edge-label.candidate")).borderColor === candidateLabelBorder, "workflow highlight promoted candidate label");
    document.querySelector("#workflow-toggle").click();

    const workerEdge = () => document.querySelector('[data-collaboration-id="candidate-collaboration:supervisor-to-worker-wrapper"] .graph-edge.candidate');
    const initialWorkerPath = workerEdge().getAttribute("d");
    openMenu(card("worker-launch-boundary"));
    assert(document.querySelector("#context-detail-toggle").textContent === "详细展示", "candidate menu lacks detail toggle");
    document.querySelector("#context-detail-toggle").click();
    await nextPaint();
    assert(card("worker-launch-boundary").classList.contains("expanded"), "worker candidate did not expand inline");
    assertVerticalCandidateStatuses();
    assertCompactProductionLayout();
    assert(card("worker-launch-boundary").scrollHeight <= card("worker-launch-boundary").clientHeight + 1, "expanded worker candidate clips its explanation or evidence summary");
    assert(workerEdge().getAttribute("d") !== initialWorkerPath, "candidate edge did not recompute after expansion");
    let points = coordinates(workerEdge());
    assert(onBoundary(points.start, cardBoundsInSurface(card("worker-launch-boundary"))), "expanded candidate edge left source boundary");
    assert(onBoundary(points.end, cardBoundsInSurface(card("deterministic-worker-wrapper"))), "expanded candidate edge left target boundary");
    assertCandidateLabelGeometry();

    openMenu(card("supervisor-orchestration"));
    document.querySelector("#context-detail-toggle").click();
    await nextPaint();
    assert(card("worker-launch-boundary").classList.contains("expanded"), "second expansion collapsed worker candidate");
    assert(card("supervisor-orchestration").classList.contains("expanded"), "second candidate did not expand independently");
    assertCompactProductionLayout();
    assertCandidateLabelGeometry();
    openMenu(card("worker-launch-boundary"));
    assert(document.querySelector("#context-detail-toggle").textContent === "关闭展示", "expanded candidate menu lacks close action");
    document.querySelector("#context-detail-toggle").click();
    await nextPaint();
    assert(!card("worker-launch-boundary").classList.contains("expanded"), "worker candidate did not collapse");
    assert(card("supervisor-orchestration").classList.contains("expanded"), "collapsing worker collapsed another candidate");
    assertCandidateLabelGeometry();

    const candidateLabel = document.querySelector('[data-collaboration-id="candidate-collaboration:supervisor-to-worker-wrapper"].graph-edge-label');
    candidateLabel.click();
    assert(document.querySelector("#detail-content").dataset.collaborationId === "candidate-collaboration:supervisor-to-worker-wrapper", "candidate edge opened a copied or different record");
    assert(document.querySelector("#detail-content").textContent.includes("启动边界指向 Worker 包装入口"), "candidate detail omits the existing collaboration name");
    assert(document.querySelector("#detail-content").textContent.includes("关系类型：候选协作"), "candidate detail omits its collaboration type");
    assert(document.querySelector("#detail-content").textContent.includes("协作状态：未审核"), "candidate detail omits its review status");
    assert(document.querySelector("#detail-content").textContent.includes("candidate-collaboration:supervisor-to-worker-wrapper"), "candidate detail hides its stable Collaboration ID");
    assert(document.querySelectorAll("#detail-content .candidate-evidence-card").length === 2, "candidate edge evidence IDs changed");
    document.querySelector("#relationship-detail").close();

    openMenu(card("worker-launch-boundary"));
    document.querySelector("#context-evidence").click();
    assert(document.querySelector("#detail-title").textContent === "Worker 启动边界", "worker context evidence opened a different candidate node");
    assert(document.querySelectorAll("#detail-content .candidate-evidence-card").length === 3, "worker candidate evidence count changed");
    assert(document.querySelector("#detail-content").textContent.includes("源码归属：部分核验"), "worker context evidence promoted source assignment");
    assert(document.querySelector('#detail-content [data-relationship-id="relationship:production-control:launches:translation-execution"]'), "worker detail lost the stable formal Relationship entry");
    document.querySelector("#relationship-detail").close();

    const stablePaths = Array.from(document.querySelectorAll(".graph-edge.candidate"), (edge) => edge.getAttribute("d"));
    window.scrollTo(0, document.documentElement.scrollHeight);
    await nextPaint();
    assert(Array.from(document.querySelectorAll(".graph-edge.candidate"), (edge) => edge.getAttribute("d")).every((path, index) => path === stablePaths[index]), "scroll introduced candidate endpoint drift");
    window.scrollTo(0, 0);

    document.documentElement.dataset.t7ProductionCandidate = "pass";
  } catch (error) {
    document.documentElement.dataset.t7ProductionCandidate = "fail";
    document.documentElement.dataset.t7ProductionCandidateError = String(error?.message || error);
  }
})();
