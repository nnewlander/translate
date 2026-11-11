"use strict";

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const [browserPath, pageUrl, profilePath, evidencePath] = process.argv.slice(2);
if (!browserPath || !pageUrl || !profilePath || !evidencePath) {
  throw new Error("usage: node t7_continuous_resize.cjs BROWSER PAGE_URL PROFILE_PATH EVIDENCE_PATH");
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const chrome = spawn(browserPath, [
  "--headless=new",
  "--disable-gpu",
  "--disable-background-networking",
  "--disable-breakpad",
  "--disable-crash-reporter",
  "--no-first-run",
  "--no-default-browser-check",
  "--allow-file-access-from-files",
  "--force-device-scale-factor=1",
  "--remote-debugging-port=0",
  `--user-data-dir=${profilePath}`,
  "--window-size=1440,900",
  pageUrl,
], { stdio: ["ignore", "ignore", "pipe"] });

const activePortPath = path.join(profilePath, "DevToolsActivePort");

async function waitForActivePort() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (fs.existsSync(activePortPath)) {
      const [port] = fs.readFileSync(activePortPath, "utf8").trim().split(/\r?\n/);
      if (port) return Number(port);
    }
    await delay(50);
  }
  throw new Error("Chrome did not publish DevToolsActivePort");
}

async function waitForPage(port) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
    const target = targets.find((item) => item.type === "page" && item.url === pageUrl);
    if (target) return target;
    await delay(50);
  }
  throw new Error("Chrome did not expose the offline page target");
}

function connect(webSocketDebuggerUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(webSocketDebuggerUrl);
    const pending = new Map();
    let nextId = 1;
    socket.addEventListener("open", () => {
      resolve({
        send(method, params = {}) {
          const id = nextId;
          nextId += 1;
          socket.send(JSON.stringify({ id, method, params }));
          return new Promise((resolveCommand, rejectCommand) => pending.set(id, { resolveCommand, rejectCommand }));
        },
        close() { socket.close(); },
      });
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !pending.has(message.id)) return;
      const { resolveCommand, rejectCommand } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) rejectCommand(new Error(`${message.error.code}: ${message.error.message}`));
      else resolveCommand(message.result);
    });
    socket.addEventListener("error", reject);
  });
}

async function main() {
  const port = await waitForActivePort();
  const target = await waitForPage(port);
  const cdp = await connect(target.webSocketDebuggerUrl);
  const evaluate = async (expression) => {
    const result = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    return result.result.value;
  };

  try {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      if (await evaluate("document.readyState === 'complete' && document.querySelectorAll('#module-grid .module-card').length === 7")) break;
      await delay(50);
    }
    const initialIdentity = await evaluate("JSON.stringify({ time_origin: performance.timeOrigin, navigation_count: performance.getEntriesByType('navigation').length })");
    const { windowId } = await cdp.send("Browser.getWindowForTarget");
    const browserVersion = await cdp.send("Browser.getVersion");
    const evidence = {
      schema: "architecture-explorer.chrome-viewport-evidence.v1",
      requested_browser: "chrome",
      browser: {
        product: browserVersion.product,
        user_agent: browserVersion.userAgent,
        protocol_version: browserVersion.protocolVersion,
        executable: browserPath,
      },
      page_identity: JSON.parse(initialIdentity),
      graphs: [],
    };

    const resize = async (width, height) => {
      const current = await evaluate("({ width: window.innerWidth, height: window.innerHeight })");
      const { bounds } = await cdp.send("Browser.getWindowBounds", { windowId });
      await cdp.send("Browser.setWindowBounds", {
        windowId,
        bounds: {
          width: bounds.width + width - current.width,
          height: bounds.height + height - current.height,
          windowState: "normal",
        },
      });
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const viewport = await evaluate("({ width: window.innerWidth, height: window.innerHeight })");
        if (viewport.width === width && viewport.height === height) break;
        await delay(25);
      }
      await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 80))))");
      return evaluate(`(() => {
        const epsilon = 1.5;
        const surface = document.querySelector('#graph-surface').getBoundingClientRect();
        const cards = Array.from(document.querySelectorAll('#module-grid .module-card'));
        const cardBounds = (id) => {
          const rect = document.querySelector('[data-module-id="' + CSS.escape(id) + '"]').getBoundingClientRect();
          return { left: rect.left - surface.left, right: rect.right - surface.left, top: rect.top - surface.top, bottom: rect.bottom - surface.top };
        };
        const onBoundary = (point, rect) => {
          const withinX = point.x >= rect.left - epsilon && point.x <= rect.right + epsilon;
          const withinY = point.y >= rect.top - epsilon && point.y <= rect.bottom + epsilon;
          const touchesX = Math.abs(point.x - rect.left) <= epsilon || Math.abs(point.x - rect.right) <= epsilon;
          const touchesY = Math.abs(point.y - rect.top) <= epsilon || Math.abs(point.y - rect.bottom) <= epsilon;
          return withinX && withinY && (touchesX || touchesY);
        };
        const edges = Array.from(document.querySelectorAll('#relationship-edge-layer > g')).map((edge) => {
          const values = edge.querySelector('.graph-edge').getAttribute('d').match(/-?\\d+(?:\\.\\d+)?/g).map(Number);
          const id = edge.dataset.relationshipId || edge.dataset.collaborationId;
          const label = document.querySelector('.graph-edge-label[data-relationship-id="' + CSS.escape(id) + '"], .graph-edge-label[data-collaboration-id="' + CSS.escape(id) + '"]');
          const labelBounds = label.getBoundingClientRect();
          const labelClearsCards = cards.every((node) => {
            const bounds = node.getBoundingClientRect();
            return labelBounds.right <= bounds.left || labelBounds.left >= bounds.right
              || labelBounds.bottom <= bounds.top || labelBounds.top >= bounds.bottom;
          });
          return {
            id,
            sourceAttached: onBoundary({ x: values[0], y: values[1] }, cardBounds(edge.dataset.sourceNodeId)),
            targetAttached: onBoundary({ x: values[2], y: values[3] }, cardBounds(edge.dataset.targetNodeId)),
            marker: edge.querySelector('.graph-edge').getAttribute('marker-end'),
            labelClearsCards,
          };
        });
        return {
          viewport: [window.innerWidth, window.innerHeight],
          identity: JSON.stringify({ time_origin: performance.timeOrigin, navigation_count: performance.getEntriesByType('navigation').length }),
          order: JSON.stringify(cards.map(node => node.dataset.moduleId)),
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          edges,
        };
      })()`);
    };

    const sequence = [[1440, 900], [1280, 900], [960, 900], [1440, 900]];
    const assertResizeSequence = async (graphId, expectedEdgeCount, expectedMarker) => {
      const initialOrder = await evaluate("JSON.stringify(Array.from(document.querySelectorAll('#module-grid .module-card'), node => node.dataset.moduleId))");
      const graphEvidence = { graph_id: graphId, observations: [] };
      for (const [width, height] of sequence) {
        const observation = await resize(width, height);
        assert(observation.viewport[0] === width && observation.viewport[1] === height, `${graphId} actual viewport was ${observation.viewport.join("x")}, expected ${width}x${height}`);
        assert(observation.identity === initialIdentity, `${graphId} reloaded while resizing to ${width}x${height}`);
        assert(observation.order === initialOrder, `${graphId} node order changed while resizing to ${width}x${height}`);
        assert(observation.overflow <= 1, `${graphId} overflowed by ${observation.overflow}px at ${width}x${height}`);
        assert(observation.edges.length === expectedEdgeCount, `${graphId} expected ${expectedEdgeCount} edges at ${width}x${height}`);
        assert(observation.edges.every((edge) => edge.sourceAttached && edge.targetAttached), `${graphId} detached an edge at ${width}x${height}: ${JSON.stringify(observation.edges)}`);
        assert(observation.edges.every((edge) => edge.marker === expectedMarker), `${graphId} lost target arrow direction at ${width}x${height}: ${JSON.stringify(observation.edges)}`);
        assert(observation.edges.every((edge) => edge.labelClearsCards), `${graphId} placed a relationship label over a node at ${width}x${height}: ${JSON.stringify(observation.edges)}`);
        graphEvidence.observations.push({
          requested: [width, height],
          actual: observation.viewport,
          navigation_identity: JSON.parse(observation.identity),
        });
      }
      evidence.graphs.push(graphEvidence);
    };

    await assertResizeSequence("top-level", 1, "url(#reviewed-arrowhead)");
    const drilldowns = [
      ["document-understanding", 1],
      ["scope-and-request", 1],
      ["production-control", 2],
      ["translation-execution", 1],
      ["quality-and-repair", 1],
      ["production-evidence", 1],
      ["candidate-and-release", 1],
    ];
    for (const [parentId, edgeCount] of drilldowns) {
      await evaluate(`document.querySelector('[data-module-id="${parentId}"]').click()`);
      await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 80))))");
      await assertResizeSequence(parentId, edgeCount, "url(#candidate-arrowhead)");
      await evaluate("document.querySelector('#home-button').click()");
      await evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 80))))");
    }
    fs.writeFileSync(evidencePath, JSON.stringify(evidence, null, 2), "utf8");
    console.log("1440x900 -> 1280x900 -> 960x900 -> 1440x900");
    console.log("9 edges across 8 graphs");
    cdp.close();
  } finally {
    chrome.kill();
  }
}

main().catch((error) => {
  chrome.kill();
  console.error(error.stack || error);
  process.exitCode = 1;
});
