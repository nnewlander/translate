(() => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const title = () => document.querySelector(".hero h1")?.textContent;
  const styleValues = (element, properties) => {
    const style = getComputedStyle(element);
    return Object.fromEntries(properties.map((property) => [property, style[property]]));
  };
  const captureCardStandard = (moduleCard, plainSelector) => {
    const cardRect = moduleCard.getBoundingClientRect();
    const heading = moduleCard.querySelector("h3");
    const professional = moduleCard.querySelector(".module-responsibility");
    const plain = moduleCard.querySelector(plainSelector);
    const statuses = moduleCard.querySelector(".statuses");
    assert(!moduleCard.querySelector(".ordinal") && heading && professional && plain && statuses, "compact candidate text layers changed");
    const textProperties = ["fontFamily", "fontSize", "fontWeight", "lineHeight", "color", "marginTop", "marginBottom"];
    return {
      card: styleValues(moduleCard, ["backgroundColor", "borderColor", "borderRadius", "boxShadow", "padding"]),
      heading: styleValues(heading, textProperties),
      professional: styleValues(professional, textProperties),
      plain: styleValues(plain, textProperties),
      headingTop: Math.round(heading.getBoundingClientRect().top - cardRect.top),
      headingToProfessional: Math.round(professional.getBoundingClientRect().top - heading.getBoundingClientRect().bottom),
      professionalToPlain: Math.round(plain.getBoundingClientRect().top - professional.getBoundingClientRect().bottom),
    };
  };
  const expandAllCandidates = () => {
    for (const moduleCard of document.querySelectorAll("#module-grid .module-card")) {
      moduleCard.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true, clientX: 40, clientY: 40,
      }));
      document.querySelector("#context-detail-toggle").click();
    }
    document.activeElement?.blur();
  };
  const assertStatusesShareRow = (moduleCard) => {
    const container = moduleCard.querySelector(".statuses");
    const badges = Array.from(container?.querySelectorAll(".status") || []);
    assert(badges.length >= 2, "module status badges missing");
    const tops = badges.map((badge) => Math.round(badge.getBoundingClientRect().top));
    const widths = badges.map((badge) => Math.round(badge.getBoundingClientRect().width));
    assert(
      tops.every((top) => top === tops[0]),
      `module status badges do not share a row when space is available (${container.clientWidth}px: ${widths.join("+")})`,
    );
  };
  const assertCandidateStatusesStack = (moduleCard) => {
    const badges = Array.from(moduleCard.querySelectorAll(":scope > .statuses > .status"));
    assert(badges.length >= 2, "candidate status badges missing");
    const first = badges[0].getBoundingClientRect();
    const second = badges[1].getBoundingClientRect();
    assert(second.top >= first.bottom - 1, "candidate status badges must use separate rows");
    assert(Math.abs(second.left - first.left) <= 1, "candidate status badges must be left aligned");
  };
  const assertStatusesWrapAsWholeBadges = () => {
    const source = card("translation-execution");
    const sourceBadgeHeight = source.querySelector(".status").getBoundingClientRect().height;
    const fixture = document.createElement("div");
    fixture.className = "top-level-grid";
    fixture.style.cssText = "position:fixed;left:-10000px;top:0;visibility:hidden";
    const compactCard = source.cloneNode(true);
    const narrowCard = source.cloneNode(true);
    compactCard.style.width = "283px";
    narrowCard.style.width = "180px";
    fixture.append(compactCard, narrowCard);
    document.body.append(fixture);
    try {
      assertStatusesShareRow(compactCard);
      const container = narrowCard.querySelector(".statuses");
      const badges = Array.from(container.querySelectorAll(".status"));
      const containerRect = container.getBoundingClientRect();
      const badgeRects = badges.map((badge) => badge.getBoundingClientRect());
      assert(Math.round(badgeRects[1].top) > Math.round(badgeRects[0].top), "narrow status badges must wrap to another row");
      assert(badgeRects.every((rect) => Math.abs(rect.height - sourceBadgeHeight) < 1), "status badge text must not wrap internally");
      assert(badgeRects.every((rect) => rect.left >= containerRect.left && rect.right <= containerRect.right + 1), "narrow status badge overflows its card");
      assert(badgeRects[0].bottom <= badgeRects[1].top, "wrapped status badges overlap");
      assert(narrowCard.scrollWidth <= narrowCard.clientWidth + 1, "narrow module card overflows horizontally");
    } finally {
      fixture.remove();
    }
  };
  const assertCandidateMatchesStandard = (reference = null) => {
    const candidateCards = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(candidateCards.length > 0, "candidate cards missing");
    const standard = reference || captureCardStandard(candidateCards[0], ".candidate-plain-explanation");
    for (const candidateCard of candidateCards) {
      const candidateType = candidateCard.querySelector(".candidate-node-type");
      const professional = candidateCard.querySelector(".module-responsibility");
      const plain = candidateCard.querySelector(".candidate-plain-explanation");
      const statuses = candidateCard.querySelector(".statuses");
      assert(candidateType && professional && plain && statuses, "candidate metadata layers missing");
      assert(!candidateCard.querySelector(".candidate-evidence-count") && !candidateCard.querySelector(".candidate-evidence-link"), "candidate card repeats evidence controls inline");
      const actual = captureCardStandard(candidateCard, ".candidate-plain-explanation");
      for (const key of ["card", "heading", "professional", "plain", "headingTop", "headingToProfessional", "professionalToPlain"]) {
        assert(
          JSON.stringify(actual[key]) === JSON.stringify(standard[key]),
          `candidate ${key} differs from candidate standard: ${JSON.stringify(actual[key])} != ${JSON.stringify(standard[key])}`,
        );
      }
      const heading = candidateCard.querySelector("h3");
      const typeRect = candidateType.getBoundingClientRect();
      const headingRect = heading.getBoundingClientRect();
      assert(
        headingRect.right <= typeRect.left || typeRect.right <= headingRect.left || headingRect.bottom <= typeRect.top || typeRect.bottom <= headingRect.top,
        "candidate type overlaps its title",
      );
      assert(heading.getBoundingClientRect().bottom <= professional.getBoundingClientRect().top, "candidate title overlaps professional text");
      assert(professional.getBoundingClientRect().bottom <= plain.getBoundingClientRect().top, "candidate text layers overlap");
      assert(plain.getBoundingClientRect().bottom <= statuses.getBoundingClientRect().top, "candidate explanation overlaps statuses");
      assert(candidateCard.scrollWidth <= candidateCard.clientWidth + 1, "candidate card overflows horizontally");
      assert(professional.scrollWidth <= professional.clientWidth + 1, "professional text is clipped");
      assert(plain.scrollWidth <= plain.clientWidth + 1, "plain text is clipped");
      assertCandidateStatusesStack(candidateCard);
    }
    assert(document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1, "page overflows horizontally");
    return standard;
  };
  try {
    if (title() !== "顶层架构") document.querySelector("#back-button").click();
    const topLevelReference = card("translation-execution");
    topLevelReference.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: 40, clientY: 40,
    }));
    document.querySelector("#context-detail-toggle").click();
    assert(card("translation-execution").classList.contains("expanded"), "top-level typography reference did not expand");
    document.activeElement?.blur();
    if (innerWidth >= 1280) {
      Array.from(document.querySelectorAll("#module-grid .module-card")).forEach(assertStatusesShareRow);
    }
    assertStatusesWrapAsWholeBadges();
    card("translation-execution").click();
    assert(title() === "翻译执行与模型接入", "translation drilldown did not open");
    assert(Array.from(document.querySelectorAll("#module-grid .module-card")).every((item) => !item.classList.contains("expanded")), "translation candidates do not start compact");
    assert(!document.querySelector("#relationship-graph").hidden, "translation candidates are missing the shared graph");
    assert(document.querySelectorAll(".graph-edge.candidate").length === 1, "translation candidate edge count changed");
    let children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2, "translation candidate count changed");
    expandAllCandidates();
    const candidateStandard = assertCandidateMatchesStandard();
    assert(card("translation-routing-and-acquisition").textContent.includes("Worker/pipeline") && card("translation-routing-and-acquisition").textContent.includes("RAW 投影"), "Worker orchestration boundary missing");
    assert(card("translation-provider-adapters").textContent.includes("Provider") && card("translation-provider-adapters").textContent.includes("不负责 Worker 的流程编排"), "Provider adapter boundary missing");
    assert(card("translation-provider-adapters").textContent.includes("组件状态：未审核"), "provider adapter is not visibly unreviewed");
    let collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1 && collaborations[0].textContent.includes("模型柜台"), "translation collaboration missing");

    card("translation-routing-and-acquisition").click();
    let detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选职责") && detail.textContent.includes("未审核"), "translation detail status missing");
    assert(detail.textContent.includes("src/booktrans/production_worker/pipeline.py"), "translation evidence is not locatable");
    document.querySelector("#relationship-detail").close();

    document.querySelector("#back-button").click();
    card("quality-and-repair").click();
    assert(title() === "质量判断与受限修复", "quality drilldown did not open");
    children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2, "quality candidate count changed");
    assert(children.every((item) => !item.classList.contains("expanded")), "quality candidates do not start compact");
    assert(document.querySelectorAll(".graph-edge.candidate").length === 1, "quality candidate edge count changed");
    expandAllCandidates();
    assertCandidateMatchesStandard(candidateStandard);
    assert(card("quality-deterministic-qa").textContent.includes("固定量规"), "deterministic QA explanation missing");
    assert(card("quality-semantic-review-and-repair").textContent.includes("组件状态：未审核"), "semantic repair is not visibly unreviewed");
    collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1 && collaborations[0].textContent.includes("返修门禁"), "quality collaboration missing");

    card("quality-semantic-review-and-repair").click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("booktrans.production_worker.reviewer_context"), "semantic review context evidence missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 4, "semantic review evidence count changed");
    document.querySelector("#relationship-detail").close();

    collaborations[0].click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选协作") && detail.textContent.includes("静态 import"), "quality collaboration detail missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 3, "quality collaboration evidence changed");
    document.querySelector("#relationship-detail").close();

    document.documentElement.dataset.t6ThreeCandidate = "pass";
  } catch (error) {
    document.documentElement.dataset.t6ThreeCandidate = "fail";
    document.documentElement.dataset.t6ThreeCandidateError = String(error?.message || error);
  }
})();
