(() => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const title = () => document.querySelector(".hero h1")?.textContent;
  try {
    if (title() !== "顶层架构") document.querySelector("#back-button").click();
    card("translation-execution").click();
    assert(title() === "翻译执行与模型接入", "translation drilldown did not open");
    let children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2, "translation candidate count changed");
    assert(card("translation-routing-and-acquisition").textContent.includes("翻译窗口"), "translation routing explanation missing");
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
    assert(card("quality-deterministic-qa").textContent.includes("固定量规"), "deterministic QA explanation missing");
    assert(card("quality-semantic-review-and-repair").textContent.includes("组件状态：未审核"), "semantic repair is not visibly unreviewed");
    collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1 && collaborations[0].textContent.includes("返修门禁"), "quality collaboration missing");

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
