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
    const ordinal = moduleCard.querySelector(".ordinal");
    const heading = moduleCard.querySelector("h3");
    const professional = moduleCard.querySelector(".module-responsibility");
    const plain = moduleCard.querySelector(plainSelector);
    const statuses = moduleCard.querySelector(".statuses");
    assert(ordinal && heading && professional && plain && statuses, "card text layers missing");
    const textProperties = ["fontFamily", "fontSize", "fontWeight", "lineHeight", "color", "marginTop", "marginBottom"];
    return {
      card: styleValues(moduleCard, ["backgroundColor", "borderColor", "borderRadius", "boxShadow", "padding"]),
      ordinal: styleValues(ordinal, ["fontFamily", "fontSize", "fontWeight", "lineHeight", "color", "letterSpacing"]),
      heading: styleValues(heading, textProperties),
      professional: styleValues(professional, textProperties),
      plain: styleValues(plain, textProperties),
      headingTop: Math.round(heading.getBoundingClientRect().top - cardRect.top),
      ordinalToHeading: Math.round(heading.getBoundingClientRect().top - ordinal.getBoundingClientRect().bottom),
      headingToProfessional: Math.round(professional.getBoundingClientRect().top - heading.getBoundingClientRect().bottom),
      professionalToPlain: Math.round(plain.getBoundingClientRect().top - professional.getBoundingClientRect().bottom),
    };
  };
  const assertCandidateMatchesTopStandard = (reference) => {
    const candidateCards = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(candidateCards.length > 0, "candidate cards missing");
    for (const candidateCard of candidateCards) {
      const candidateType = candidateCard.querySelector(".candidate-node-type");
      const professional = candidateCard.querySelector(".module-responsibility");
      const plain = candidateCard.querySelector(".candidate-plain-explanation");
      const evidence = candidateCard.querySelector(".candidate-evidence-count");
      const statuses = candidateCard.querySelector(".statuses");
      assert(candidateType && professional && plain && evidence && statuses, "candidate metadata layers missing");
      const actual = captureCardStandard(candidateCard, ".candidate-plain-explanation");
      for (const key of ["card", "ordinal", "heading", "professional", "plain", "headingTop", "ordinalToHeading", "headingToProfessional", "professionalToPlain"]) {
        assert(JSON.stringify(actual[key]) === JSON.stringify(reference[key]), `candidate ${key} differs from top-level standard`);
      }
      const heading = candidateCard.querySelector("h3");
      assert(candidateType.getBoundingClientRect().bottom <= heading.getBoundingClientRect().top, "candidate type obscures title rhythm");
      assert(heading.getBoundingClientRect().bottom <= professional.getBoundingClientRect().top, "candidate title overlaps professional text");
      assert(professional.getBoundingClientRect().bottom <= plain.getBoundingClientRect().top, "candidate text layers overlap");
      assert(plain.getBoundingClientRect().bottom <= evidence.getBoundingClientRect().top, "candidate evidence precedes plain explanation");
      assert(evidence.getBoundingClientRect().bottom <= statuses.getBoundingClientRect().top, "candidate evidence overlaps statuses");
      assert(candidateCard.scrollWidth <= candidateCard.clientWidth + 1, "candidate card overflows horizontally");
      assert(professional.scrollWidth <= professional.clientWidth + 1, "professional text is clipped");
      assert(plain.scrollWidth <= plain.clientWidth + 1, "plain text is clipped");
    }
    assert(document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1, "page overflows horizontally");
  };
  try {
    if (title() !== "顶层架构") document.querySelector("#back-button").click();
    const topLevelStandard = captureCardStandard(card("translation-execution"), ".module-plain-explanation");
    card("translation-execution").click();
    assert(title() === "翻译执行与模型接入", "translation drilldown did not open");
    let children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2, "translation candidate count changed");
    assertCandidateMatchesTopStandard(topLevelStandard);
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
    assertCandidateMatchesTopStandard(topLevelStandard);
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
