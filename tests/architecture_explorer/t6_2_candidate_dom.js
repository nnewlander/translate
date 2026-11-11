(() => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const title = () => document.querySelector(".hero h1")?.textContent;
  try {
    assert(document.querySelectorAll("#module-grid .module-card").length === 7, "top-level modules changed");
    assert(document.querySelectorAll(".relationship-line").length === 1, "formal relationship count changed");
    assert(document.querySelector(".relationship-line .relation-status")?.textContent.includes("当前已审核"), "formal review status changed");

    card("document-understanding").click();
    assert(title() === "文档理解与定位", "document drilldown did not open");
    let children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2, "document candidate count changed");
    assert(document.querySelector("#modules-heading")?.textContent === "候选职责与组件", "candidate section type is incomplete");
    assert(document.querySelector("#module-count")?.textContent === "2 个候选节点", "candidate count type is incomplete");
    assert(card("document-package-extraction").textContent.includes("职责状态：未审核"), "candidate responsibility is not visibly unreviewed");
    assert(card("document-translation-view").textContent.includes("组件状态：未审核"), "candidate component is not visibly unreviewed");
    assert(children.every((item) => item.querySelector(".candidate-plain-explanation")?.textContent), "document candidate plain explanation missing");
    assert(children.some((item) => item.textContent.includes("档案拆包员")), "document explanation is not specific");
    assert(children.every((item) => !item.querySelector(".candidate-evidence-count") && !item.querySelector(".candidate-evidence-link")), "document candidates repeat evidence controls inline");
    let collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1, "document candidate collaboration missing");
    assert(collaborations[0].textContent.includes("未审核") && collaborations[0].textContent.includes("档案拆包员"), "document collaboration explanation or state missing");

    card("document-package-extraction").click();
    let detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选职责") && detail.textContent.includes("未审核"), "candidate detail status missing");
    assert(detail.textContent.includes("档案拆包员"), "candidate detail plain explanation missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 3, "candidate source evidence not resolved");
    assert(detail.textContent.includes("src/booktrans/ooxml/extractor.py") && detail.textContent.includes("Evidence ID"), "candidate evidence is not locatable");
    document.querySelector("#relationship-detail").close();

    card("document-translation-view").click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选组件") && detail.textContent.includes("组件状态：未审核"), "candidate component detail type changed");
    document.querySelector("#relationship-detail").close();

    collaborations[0].click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选协作") && detail.textContent.includes("静态 import"), "collaboration detail explanation missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 2, "collaboration evidence not resolved");
    document.querySelector("#relationship-detail").close();

    document.querySelector("#back-button").click();
    assert(title() === "顶层架构" && document.querySelectorAll("#module-grid .module-card").length === 7, "return to top changed");
    card("scope-and-request").click();
    assert(title() === "工作范围与请求构造", "scope drilldown did not open");
    children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2 && children.some((item) => item.textContent.includes("资料袋")), "scope candidates are not concrete");
    collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1 && collaborations[0].textContent.includes("先确定要办哪些工单"), "scope collaboration missing or generic");

    document.documentElement.dataset.t6Candidate = "pass";
  } catch (error) {
    document.documentElement.dataset.t6Candidate = "fail";
    document.documentElement.dataset.t6CandidateError = String(error?.message || error);
  }
})();
