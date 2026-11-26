(async () => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const card = (id) => document.querySelector(`[data-module-id="${CSS.escape(id)}"]`);
  const wait = () => new Promise((resolve) => setTimeout(resolve, 80));
  const back = () => document.querySelector("#back-button").click();
  const home = () => document.querySelector("#home-button").click();
  const breadcrumb = () => document.querySelector("#breadcrumb").textContent;
  const detail = document.querySelector("#detail-content");
  const dialog = document.querySelector("#relationship-detail");
  const closeDetail = () => document.querySelector(".dialog-close").click();
  const relation = (name) => [...document.querySelectorAll("#candidate-collaboration-list button")]
    .find((button) => button.querySelector("h4")?.textContent === name);
  const assertGraphLayout = async () => {
    await wait();
    const failure = document.querySelector(".graph-layout-error");
    assert(!failure, failure?.textContent);
  };
  const graphPaths = () => [...document.querySelectorAll("#relationship-edge-layer .graph-edge")]
    .map((line) => [...line.getAttribute("d").matchAll(/[ML] (-?[\d.]+) (-?[\d.]+)/g)]
      .map(([, x, y]) => ({ x: Number(x), y: Number(y) })));
  const orientation = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  const crosses = (a, b, c, d) => orientation(a, b, c) * orientation(a, b, d) < 0
    && orientation(c, d, a) * orientation(c, d, b) < 0;
  const overlaps = (a, b, c, d) => {
    if (Math.abs(orientation(a, b, c)) > 0.01 || Math.abs(orientation(a, b, d)) > 0.01) return false;
    const axis = Math.abs(a.x - b.x) > Math.abs(a.y - b.y) ? "x" : "y";
    return Math.min(Math.max(a[axis], b[axis]), Math.max(c[axis], d[axis]))
      - Math.max(Math.min(a[axis], b[axis]), Math.min(c[axis], d[axis])) > 10;
  };
  const pathsConflict = (left, right) => left.slice(1).some((b, i) =>
    right.slice(1).some((d, j) =>
      crosses(left[i], b, right[j], d) || overlaps(left[i], b, right[j], d)));

  document.documentElement.dataset.t07Width = window.innerWidth;
  card("document-understanding").click();
  card("document-package-extraction").dispatchEvent(
    new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
  );
  const story = card("v4-t07-docx-story-parts");
  assert(story?.textContent.includes("职责状态：未审核"), "DOCX story Candidate is missing");
  const edge = "candidate-collaboration:v4-t07-story-parts-to-located-ir";
  const collaboration = relation("内容部件进入定位");
  assert(collaboration, "DOCX collaboration is missing");
  collaboration.click();
  assert(detail.dataset.collaborationId === edge, "Wrong collaboration detail opened");
  assert(detail.textContent.includes("关系类型：候选协作"), "Collaboration semantics missing");
  assert(detail.textContent.includes("批准范围：无"), "Edge inherited an approval");
  document.querySelector(".dialog-close").click();
  const path = breadcrumb();
  story.focus();
  story.click();
  detail.querySelector(".browse-definitions").click();
  assert(detail.dataset.sourceTrailLevel === "file", "Definition Source Trail did not open");
  assert(detail.textContent.includes("extractor.py"), "Source Trail opened a different file");
  assert(breadcrumb() === path, "Source Trail changed the architecture path");
  document.querySelector(".dialog-close").click();
  await wait();
  assert(document.activeElement === card("v4-t07-docx-story-parts"), "Closing Source Trail lost origin focus");
  assert(document.documentElement.scrollWidth <= window.innerWidth, "View overflows horizontally");
  await assertGraphLayout();
  const irNodeId = "v4-t07-located-document-ir";
  card(irNodeId).click();
  assert(breadcrumb().includes("IR 地址如何成为翻译视图"), "IR did not open its valid target");
  const constructionPath = breadcrumb();
  card(irNodeId).dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
  assert(breadcrumb() === constructionPath, "The same IR Appearance returned to an ancestor");
  assert(dialog.open && detail.querySelector("h2").textContent === "构造带定位的文档 IR",
    "The blocked non-scope Appearance did not open its own detail");
  closeDetail();
  await wait();
  assert(document.activeElement === card(irNodeId), "Closing ordinary node detail lost keyboard focus");
  back();
  await wait();
  assert(document.activeElement === card(irNodeId), "Back did not restore the first IR Appearance");
  back();
  await wait();
  assert(document.activeElement === card("document-package-extraction"), "Back did not restore focus");
  card("document-translation-view").click();
  await assertGraphLayout();
  const constructionPaths = graphPaths();
  assert(constructionPaths.length === 4, "View construction must render all four relationships");
  assert(!constructionPaths.some((left, i) =>
    constructionPaths.slice(i + 1).some((right) => pathsConflict(left, right))),
  "View construction rendered crossing or overlapping relationships");
  card("v4-t07-translation-segments").click();
  assert(breadcrumb().includes("翻译视图如何进入范围与请求"), "Cross-module View did not open");
  await assertGraphLayout();
  for (const [label, target] of [
    ["视图身份确定范围输入", "v4-t07-logical-work-order-projection"],
    ["视图片段进入请求", "v4-t07-calibration-request"],
  ]) {
    const button = relation(label);
    assert(button?.textContent.includes("协作状态：未审核"), `Missing Candidate collaboration ${label}`);
    assert(card(target), `Missing collaboration target ${target}`);
  }
  card("v4-t07-logical-work-order-projection").click();
  card("v4-t07-frozen-checkpoint-slice").click();
  card("v4-t07-calibration-request").click();
  await assertGraphLayout();
  card("v4-t07-d050-exact-binding").click();
  assert(breadcrumb().includes("D050 证据如何绑定"), "D050 View is unreachable");
  await assertGraphLayout();
  const d050Path = breadcrumb();
  const d050Scope = card("v4-t07-d050-exact-binding");
  d050Scope.click();
  assert(breadcrumb() === d050Path, "Clicking the D050 scope jumped to an ancestor");
  assert(document.querySelector("#relationship-detail").open, "D050 scope detail did not open");
  assert(detail.textContent.includes("按工单和源单元匹配 D050"), "Wrong scope detail opened");
  document.querySelector(".dialog-close").click();
  await wait();
  const exactBinding = relation("精确证据进入请求");
  exactBinding.focus();
  exactBinding.click();
  assert(detail.textContent.includes("双键"), "D050 detail omitted its identity boundary");
  assert(detail.textContent.includes("批准范围：无"), "D050 edge inherited an approval");
  assert(detail.querySelector(".browse-definitions"), "Deep relationship lacks Source Trail");
  detail.querySelector(".browse-definitions").click();
  assert(detail.dataset.sourceTrailLevel === "file", "Deep relationship Source Trail did not open");
  document.querySelector(".dialog-close").click();
  await wait();
  assert(document.activeElement === exactBinding, "Deep relationship Source Trail lost focus");
  assert(document.documentElement.scrollWidth <= window.innerWidth, "D050 View overflows horizontally");
  back();
  await wait();
  assert(document.activeElement === card("v4-t07-d050-exact-binding"), "Deep Back lost focus");
  home();
  assert(card("document-understanding") && !card("document-package-extraction"), "Home did not restore overview");
  card("scope-and-request").click();
  card("scope-request-context-assembly").click();
  card("v4-t07-d050-exact-binding").click();
  card("v4-t07-calibration-request").click();
  const checkpointPath = breadcrumb();
  card("v4-t07-frozen-checkpoint-slice").click();
  assert(dialog.open && breadcrumb() === checkpointPath, "Checkpoint scope activation must show detail");
  const frozenRange = dialog.querySelector(".view-navigation-link");
  assert(frozenRange?.textContent === "回看冻结范围", "The declared cross-View entry is unavailable");
  frozenRange.click();
  assert(!dialog.open && breadcrumb().includes("物理内容如何划入冻结范围"), "Explicit cross-View entry did not open");
  back();
  await wait();
  assert(breadcrumb() === checkpointPath && document.activeElement === card("v4-t07-frozen-checkpoint-slice"),
    "Back did not restore the cross-View origin");
  document.documentElement.dataset.t07 = "pass";
})().catch((error) => {
  document.documentElement.dataset.t07Error = error.message;
});
