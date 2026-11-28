"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");

// The four Views and 12 relationships from the original independent review.
const reviewedViews = new Map([
  ["top-level", 1],
  ["release-membership-derivation-internal", 3],
  ["quality-candidate-disposition", 5],
  ["checkpoint-to-batch-release", 3],
]);

async function verifyRelationshipMouse({ cdp, evaluate, waitForPaint, pressKey, evidencePath }) {
  const inPage = (fn, ...args) => evaluate(
    `(${fn})(...${JSON.stringify(args)})`
  );
  const mouseClick = async ({ x, y }, button = "left") => {
    for (const type of ["mousePressed", "mouseReleased"]) {
      await cdp.send("Input.dispatchMouseEvent", { type, x, y, button, clickCount: 1 });
    }
  };
  const locate = (selector, focus = false) => inPage((selector, focus) => {
    const element = document.querySelector(selector);
    element.scrollIntoView({ block: "center" });
    const { left, top, width, height } = element.getBoundingClientRect();
    if (focus) element.focus({ preventScroll: true });
    return { x: left + width / 2, y: top + height / 2 };
  }, selector, focus);
  const detail = () => inPage(() => {
    if (!document.querySelector("#relationship-detail").open) return null;
    const panel = document.querySelector("#detail-content");
    return {
      id: panel.dataset.relationshipId || panel.dataset.collaborationId,
      direction: panel.querySelector("#detail-title").textContent,
      evidence: panel.textContent,
    };
  });
  const closeDetail = async () => {
    await pressKey("Escape");
    await waitForPaint();
    assert.equal(await detail(), null);
  };
  const routes = await inPage((ids) => {
    const { root_view_id: root, view_index: views } = ARCHITECTURE_EXPLORER_DATA.view_projection;
    const routes = new Map([[root, []]]);
    for (const [view, route] of routes) {
      for (const { node_id, primary_action: action } of views[view].appearances) {
        if (action?.kind === "open-view" && !routes.has(action.target_view_id)) {
          routes.set(action.target_view_id, [...route, node_id]);
        }
      }
    }
    return ids.map((id) => routes.get(id));
  }, [...reviewedViews.keys()]);
  const observations = [];
  for (const width of [1440, 500]) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width, height: 900, mobile: false, deviceScaleFactor: 1 });
    await waitForPaint();
    assert.equal(await evaluate("innerWidth"), width);
    for (const [index, [view, count]] of [...reviewedViews].entries()) {
      if (await evaluate("document.querySelector('#home-button').getClientRects().length > 0")) {
        await locate("#home-button", true);
        await pressKey("Enter");
      }
      for (const nodeId of routes[index]) {
        await locate(`[data-module-id="${nodeId}"]`, true);
        await pressKey("Enter");
        if (await detail()) {
          await locate("#detail-content .view-navigation-link", true);
          await pressKey("Enter");
        }
      }
      await waitForPaint();
      if (view === "top-level") {
        const card = '[data-module-id="document-understanding"]';
        const menuOpen = () => evaluate("!document.querySelector('#module-context-menu').hidden");
        const atRoot = () => evaluate("document.querySelector('#home-button').getClientRects().length === 0");
        const cardCenter = await locate(card);
        await mouseClick(cardCenter, "right");
        assert.equal(await menuOpen(), true);
        const blank = await inPage(() => {
          const { left, top } = document.querySelector("#module-grid").getBoundingClientRect();
          return { x: left + 4, y: top + 4 };
        });
        await mouseClick(blank);
        assert.equal(await menuOpen(), false);
        await mouseClick(blank);
        assert.equal(await detail(), null);
        assert.equal(await atRoot(), true);
        await mouseClick(await locate(card));
        assert.equal(await atRoot(), false);
        await locate("#back-button", true);
        await pressKey("Enter");
        await waitForPaint();
        assert.equal(await atRoot(), true);
        assert.equal(await evaluate(`document.activeElement.matches(${JSON.stringify(card)})`), true);
      }
      const edges = await inPage((view) => {
        const kinds = view === "top-level" ? ["relationship"] : ["collaboration", "decomposition"];
        const { node_index: nodes } = ARCHITECTURE_EXPLORER_DATA.view_projection;
        return [...document.querySelectorAll("#relationship-edge-layer g")].flatMap(({ dataset }) =>
          kinds.filter((kind) => dataset[`${kind}Id`]).map((kind) => [kind, dataset[`${kind}Id`],
            `${nodes[dataset.sourceNodeId].name} → ${nodes[dataset.targetNodeId].name}`]));
      }, view);
      assert.equal(edges.length, count, view);
      for (const [kind, id, direction] of edges) {
        const selector = `[data-${kind}-id="${id}"]`;
        const point = await inPage((selector) => {
          const line = document.querySelector(`g${selector} .graph-edge`);
          const blockers = [...document.querySelectorAll(".module-card, .graph-edge-label")]
            .map((element) => element.getBoundingClientRect());
          const others = [...document.querySelectorAll(".graph-edge-hit")]
            .filter((path) => path.parentNode !== line.parentNode);
          const length = line.getTotalLength();
          for (let offset = 10; offset < length - 8; offset += 2) {
            const point = line.getPointAtLength(offset);
            if (!line.isPointInStroke(point) || others.some((path) => path.isPointInStroke(point))) continue;
            const { x, y } = point.matrixTransform(line.getScreenCTM());
            if (blockers.some((rect) => x >= rect.left - 2 && x <= rect.right + 2
              && y >= rect.top - 2 && y <= rect.bottom + 2)) continue;
            window.scrollBy(0, y - innerHeight / 2);
            const screen = point.matrixTransform(line.getScreenCTM());
            return { x: screen.x, y: screen.y };
          }
          return null;
        }, selector);
        assert.ok(point, `No separate painted segment for ${id} at ${width}px`);
        await waitForPaint();
        const target = await inPage(({ x, y }) => document.elementFromPoint(x, y)?.getAttribute("class"), point);
        await mouseClick(point);
        const byLine = await detail();
        if (byLine) await closeDetail();
        const label = `button.graph-edge-label${selector}`;
        await locate(label);
        await waitForPaint();
        await mouseClick(await locate(label));
        const byLabel = await detail();
        assert.equal(byLabel?.id, id, `Label opened the wrong relationship at ${width}px`);
        assert.equal(byLabel.direction, direction, id);
        assert.match(byLabel.evidence, /关键证据链|源码依据（/, id);
        await closeDetail();
        assert.equal(await evaluate(`document.activeElement.matches(${JSON.stringify(label)})`), true, id);
        observations.push({
          width, view, kind, id, point, target, opened: byLine?.id ?? null,
          direction: byLabel.direction, sameEvidence: Boolean(byLine && byLine.evidence === byLabel.evidence),
        });
      }
    }
  }
  fs.writeFileSync(evidencePath, JSON.stringify({
    browser: (await cdp.send("Browser.getVersion")).product, observations,
  }, null, 2), "utf8");
  const failures = observations.filter(({ opened, id, sameEvidence }) => opened !== id || !sameEvidence);
  assert.equal(failures.length, 0, failures.map(({ width, id, target, opened }) => `${width}px ${id}: ${target} → ${opened}`).join("\n"));
}

module.exports = { verifyRelationshipMouse };
