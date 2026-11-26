(() => {
  "use strict";

  window.addEventListener("error", (event) => {
    document.documentElement.dataset.t13Error = event.message;
  });

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);
  const breadcrumbText = () => document.querySelector("#breadcrumb").textContent;
  const overlaps = (left, right, inset = 1) => !(
    left.right <= right.left + inset
      || left.left >= right.right - inset
      || left.bottom <= right.top + inset
      || left.top >= right.bottom - inset
  );
  const segmentIntersectsInterior = (start, end, rect) => {
    const inset = 2;
    const bounds = {
      left: rect.left + inset,
      right: rect.right - inset,
      top: rect.top + inset,
      bottom: rect.bottom - inset,
    };
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const p = [-dx, dx, -dy, dy];
    const q = [
      start.x - bounds.left,
      bounds.right - start.x,
      start.y - bounds.top,
      bounds.bottom - start.y,
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
  const assertGraphGeometry = () => {
    const parent = card("translation-provider-adapters");
    const children = [card("qwen-mt-single-text-adapter"), card("qwen-max-structured-translation-adapter")];
    const parentBounds = parent.getBoundingClientRect();
    assert(
      children.every((child) => parentBounds.bottom < child.getBoundingClientRect().top),
      "the current scope was not laid out above both decomposed components",
    );

    const groups = Array.from(document.querySelectorAll("#relationship-edge-layer > g"));
    assert(groups.length === 2, `expected two decomposition edges, found ${groups.length}`);
    assert(
      groups.every((group) => group.dataset.sourceNodeId === "translation-provider-adapters"),
      "a decomposition edge did not originate at the parent scope",
    );
    assert(
      new Set(groups.map((group) => group.dataset.targetNodeId)).size === 2
        && groups.some((group) => group.dataset.targetNodeId === "qwen-mt-single-text-adapter")
        && groups.some((group) => group.dataset.targetNodeId === "qwen-max-structured-translation-adapter"),
      "the graph implied a Provider-to-Provider relationship",
    );

    const surface = document.querySelector("#graph-surface").getBoundingClientRect();
    const relativeBounds = (node) => {
      const rect = node.getBoundingClientRect();
      return {
        left: rect.left - surface.left,
        right: rect.right - surface.left,
        top: rect.top - surface.top,
        bottom: rect.bottom - surface.top,
      };
    };
    for (const group of groups) {
      const values = group.querySelector(".graph-edge").getAttribute("d").match(/-?\d+(?:\.\d+)?/g).map(Number);
      const points = [];
      for (let index = 0; index < values.length; index += 2) points.push({ x: values[index], y: values[index + 1] });
      const unrelated = children.filter((child) => (
        child.dataset.moduleId !== group.dataset.sourceNodeId
          && child.dataset.moduleId !== group.dataset.targetNodeId
      ));
      for (const node of unrelated) {
        const bounds = relativeBounds(node);
        const crosses = points.slice(1).some((point, index) => (
          segmentIntersectsInterior(points[index], point, bounds)
        ));
        assert(!crosses, `${group.dataset.decompositionId} crossed unrelated node ${node.dataset.moduleId}`);
      }
    }

    const cards = [parent, ...children];
    const labels = Array.from(document.querySelectorAll(".graph-edge-label[data-decomposition-id]"));
    assert(labels.length === 2, `expected two decomposition labels, found ${labels.length}`);
    assert(
      labels.every((label) => cards.every((node) => !overlaps(label.getBoundingClientRect(), node.getBoundingClientRect()))),
      "a decomposition label overlapped a node card",
    );
    assert(!overlaps(labels[0].getBoundingClientRect(), labels[1].getBoundingClientRect()), "decomposition labels overlapped each other");
  };

  card("translation-execution").click();
  const providerEntry = card("translation-provider-adapters");
  assert(providerEntry, "translation-execution did not expose the Provider Appearance");
  providerEntry.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));

  assert(card("qwen-mt-single-text-adapter"), "Qwen MT adapter Appearance is missing");
  assert(card("qwen-max-structured-translation-adapter"), "Qwen Max adapter Appearance is missing");
  assert(document.querySelector(".hero h1").textContent === "Qwen 提供方适配", "Qwen View title did not use the clicked Node display name");
  const viewStatus = document.querySelector("#view-status");
  assert(
    viewStatus && !viewStatus.hidden
      && viewStatus.textContent.includes("对象可信状态：")
      && viewStatus.textContent.includes("未审核"),
    "the explicit Pending Review projection was not exposed as independent View status",
  );
  const scope = card("translation-provider-adapters");
  assert(
    scope?.dataset.scopeNode === "true"
      && scope.querySelector(".scope-node-label")?.textContent.includes("当前范围")
      && scope.querySelector(".scope-node-label")?.textContent.includes("父节点"),
    "the parent Appearance was not identified as the current scope",
  );
  assert(
    breadcrumbText().includes("整体架构")
      && breadcrumbText().includes("翻译执行与模型接入")
      && breadcrumbText().includes("Qwen 提供方适配"),
    `complete Qwen breadcrumb is missing: ${breadcrumbText()}`,
  );
  assert(
    card("qwen-mt-single-text-adapter").textContent.includes("组件状态：未审核")
      && card("qwen-max-structured-translation-adapter").textContent.includes("组件状态：未审核"),
    "Qwen adapter child inherited a reviewed state",
  );
  const visibleRelationships = Array.from(
    document.querySelectorAll("#candidate-collaboration-list .candidate-collaboration"),
  );
  assert(visibleRelationships.length === 2, "Qwen View did not expose exactly two decompositions");
  assert(
    visibleRelationships.every(
      (item) => item.textContent.includes("分解状态：未审核") && !item.textContent.includes("协作状态"),
    ),
    "Qwen View invented a Provider-to-Provider Collaboration",
  );
  assert(document.documentElement.scrollWidth <= window.innerWidth, "Qwen internal View has horizontal overflow");

  window.setTimeout(() => {
    assertGraphGeometry();
    const mt = card("qwen-mt-single-text-adapter");
    mt.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 20, clientY: 20 }));
    document.querySelector("#context-evidence").click();
    assert(document.querySelector("#relationship-detail").open, "Qwen MT Evidence did not open");
    const browse = document.querySelector("#detail-content .browse-definitions");
    assert(browse, "Qwen MT Definition Evidence did not expose Source Trail");
    browse.click();
    assert(
      document.querySelector("#detail-content").dataset.sourceTrailLevel === "file",
      "Qwen MT Source Trail did not open",
    );
    assert(
      document.querySelector("#detail-content").textContent.includes("qwen_mt.py"),
      "Qwen MT Source Trail lost its real source path",
    );
    document.querySelector("#relationship-detail").close();

    window.setTimeout(() => {
    assert(
      document.activeElement?.dataset.moduleId === "qwen-mt-single-text-adapter",
      "Source Trail close did not restore Qwen MT focus",
    );
    document.querySelector("#back-button").click();
    window.setTimeout(() => {
      assert(card("translation-provider-adapters"), "BACK did not return to translation-execution");
      assert(
        document.activeElement?.dataset.moduleId === "translation-provider-adapters",
        "BACK did not restore Provider Appearance focus",
      );
      document.querySelector("#home-button").click();
      assert(card("translation-execution"), "HOME did not return to the system overview");
      card("translation-execution").dispatchEvent(
        new KeyboardEvent("keydown", { key: " ", bubbles: true }),
      );
      assert(card("translation-provider-adapters"), "Space did not invoke top-level navigation");
      document.documentElement.dataset.t13QwenProviderInternal = "pass";
    }, 30);
    }, 30);
  }, 100);
})();
