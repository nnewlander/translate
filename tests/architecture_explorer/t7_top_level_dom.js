(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const openMenu = (node) => node.dispatchEvent(new MouseEvent("contextmenu", {
    bubbles: true, cancelable: true, clientX: 40, clientY: 40,
  }));
  const toggle = document.querySelector("#context-detail-toggle");
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 50));
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

  try {
    await nextPaint();
    const cards = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(cards.length === 7, "top level must contain seven cards");
    assert(cards.every((node) => !node.classList.contains("expanded")), "cards must start compact");
    assert(cards.every((node) => node.querySelector(".module-inline-detail")?.hidden), "compact cards expose responsibility text");
    assert(cards.every((node) => node.querySelectorAll(".statuses .status").length === 2), "minimum trust states missing");
    const reviewedEdges = Array.from(document.querySelectorAll(".graph-edge.reviewed"));
    assert(reviewedEdges.length === 1, "top level must contain exactly one reviewed SVG edge");
    assert(reviewedEdges[0].getAttribute("marker-end") === "url(#reviewed-arrowhead)", "reviewed edge lacks target arrowhead");
    const initialPath = reviewedEdges[0].getAttribute("d");
    let points = coordinates(reviewedEdges[0]);
    assert(onBoundary(points.start, cardBoundsInSurface(card("production-control"))), "edge does not start on production-control boundary");
    assert(onBoundary(points.end, cardBoundsInSurface(card("translation-execution"))), "edge does not end on translation-execution boundary");

    openMenu(card("production-control"));
    assert(toggle?.textContent === "详细展示", "collapsed card menu lacks detail action");
    toggle.click();
    await nextPaint();
    assert(card("production-control").classList.contains("expanded"), "card did not expand in place");
    assert(document.activeElement === card("production-control"), "focus did not return to the toggled card");
    assert(!card("production-control").querySelector(".module-inline-detail").hidden, "expanded responsibility remains hidden");
    const expandedEdge = document.querySelector(".graph-edge.reviewed");
    points = coordinates(expandedEdge);
    assert(onBoundary(points.start, cardBoundsInSurface(card("production-control"))), "expanded edge left source boundary");
    assert(onBoundary(points.end, cardBoundsInSurface(card("translation-execution"))), "expanded edge left target boundary");

    openMenu(card("translation-execution"));
    toggle.click();
    await nextPaint();
    assert(document.querySelector(".graph-edge.reviewed").getAttribute("d") !== initialPath, "edge path did not update after endpoint expansion");
    assert(card("production-control").classList.contains("expanded"), "second expansion collapsed first card");
    assert(card("translation-execution").classList.contains("expanded"), "second card did not expand");

    openMenu(card("production-control"));
    assert(toggle.textContent === "关闭展示", "expanded card menu lacks close action");
    toggle.click();
    await nextPaint();
    assert(!card("production-control").classList.contains("expanded"), "card did not collapse in place");
    assert(card("translation-execution").classList.contains("expanded"), "closing one card collapsed another");

    const graphLabel = document.querySelector('.graph-edge-label[data-relationship-id="relationship:production-control:launches:translation-execution"]');
    assert(graphLabel?.textContent.includes("当前已审核"), "graph label does not state reviewed trust");
    graphLabel.click();
    assert(document.querySelector("#detail-content").dataset.relationshipId === "relationship:production-control:launches:translation-execution", "graph edge opened a different relationship record");
    assert(document.querySelectorAll(".evidence-group .evidence-card").length === 13, "approved evidence count changed");
    document.querySelector("#relationship-detail").close();
    document.querySelector(".graph-edge-hit").dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    assert(document.querySelector("#detail-content").dataset.relationshipId === "relationship:production-control:launches:translation-execution", "SVG path opened a different relationship record");
    document.querySelector("#relationship-detail").close();

    const selectedText = card("translation-execution").querySelector(".module-responsibility");
    const range = document.createRange(); range.selectNodeContents(selectedText);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    selectedText.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }));
    assert(document.querySelector(".hero h1").textContent === "顶层架构", "selecting expanded text triggered navigation");
    selection.removeAllRanges();

    const stablePath = document.querySelector(".graph-edge.reviewed").getAttribute("d");
    window.scrollTo(0, document.documentElement.scrollHeight);
    await nextPaint();
    assert(document.querySelector(".graph-edge.reviewed").getAttribute("d") === stablePath, "scroll introduced endpoint drift");
    window.scrollTo(0, 0);

    const workflowToggle = document.querySelector("#workflow-toggle");
    workflowToggle.click(); await nextPaint();
    assert(document.body.classList.contains("workflow-highlight"), "workflow highlight no longer activates");
    assert(document.querySelector(".graph-edge.reviewed").getAttribute("d") === stablePath, "workflow highlight changed edge geometry");
    workflowToggle.click();

    const releaseCard = card("candidate-and-release");
    releaseCard.focus();
    releaseCard.dispatchEvent(new KeyboardEvent("keydown", { key: "F10", shiftKey: true, bubbles: true, cancelable: true }));
    assert(!document.querySelector("#module-context-menu").hidden, "Shift+F10 did not open the node menu");
    assert(document.activeElement === toggle, "keyboard menu did not focus the leading detail action");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));

    card("production-control").click();
    await nextPaint();
    assert(document.querySelector(".hero h1").textContent === "生产调度与控制", "left click no longer drills down");
    assert(document.querySelector("#relationship-graph").hidden, "top-level relationship leaked into candidate drilldown");
    assert(Array.from(document.querySelectorAll("#module-grid .module-card")).every((node) => node.classList.contains("expanded")), "T6 candidate cards changed before T7-2");
    document.querySelector("#back-button").click();
    await nextPaint();
    assert(Array.from(document.querySelectorAll("#module-grid .module-card")).every((node) => !node.classList.contains("expanded")), "BACK did not clear inline expansion");
    assert(document.querySelectorAll(".graph-edge.reviewed").length === 1, "reviewed graph did not return after BACK");

    document.documentElement.dataset.t7InlineRegression = "pass";
  } catch (error) {
    document.documentElement.dataset.t7InlineRegression = "fail";
    document.documentElement.dataset.t7InlineRegressionError = String(error?.message || error);
  }
})();
