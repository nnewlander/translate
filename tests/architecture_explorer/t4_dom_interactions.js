(() => {
  "use strict";

  const fail = (message) => { throw new Error(message); };
  const assert = (condition, message) => { if (!condition) fail(message); };
  const contextMenu = document.querySelector("#module-context-menu");
  const title = () => document.querySelector(".hero h1")?.textContent;
  const productionCard = () => document.querySelector('[data-module-id="production-control"]');
  const openMenu = (card) => card.dispatchEvent(new MouseEvent("contextmenu", {
    bubbles: true, cancelable: true, clientX: 24, clientY: 24,
  }));
  const outsideActivation = (card) => {
    card.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true }));
    card.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }));
  };

  try {
    const topCards = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(topCards.length === 7, "seven top-level module cards missing");
    const plainModuleExplanations = topCards.map((item) => item.querySelector(".module-plain-explanation")?.textContent || "");
    assert(plainModuleExplanations.every(Boolean), "a top-level module lacks its plain explanation");
    assert(new Set(plainModuleExplanations).size === 7, "top-level module explanations are generic duplicates");
    assert(document.querySelector('[data-module-id="document-understanding"] .module-plain-explanation')?.textContent.includes("档案整理员"), "document module analogy missing");
    assert(document.querySelector('[data-module-id="quality-and-repair"] .module-plain-explanation')?.textContent.includes("质检员"), "quality module analogy missing");
    assert(document.querySelector('[data-module-id="candidate-and-release"] .module-plain-explanation')?.textContent.includes("装订与出厂检查"), "release module analogy missing");
    assert(!document.querySelector(".interaction-hint"), "removed mouse interaction hint is still visible");
    assert(topCards.every((item) => {
      const statuses = item.querySelector(".statuses");
      return statuses?.children.length === 2 && getComputedStyle(statuses).display === "flex" && getComputedStyle(statuses).flexWrap === "wrap";
    }), "module statuses are not paired with responsive wrapping");

    let card = productionCard();
    assert(card, "production-control card missing");
    openMenu(card);
    assert(!contextMenu.hidden, "context menu did not open");
    outsideActivation(card);
    assert(contextMenu.hidden, "outside activation did not close the menu");
    assert(title() === "顶层架构", "outside activation also drilled down");
    card.click();
    assert(title() === "生产调度与控制", "next normal click after outside dismissal was suppressed");

    document.querySelector("#home-button").click();
    card = productionCard();
    openMenu(card);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    assert(contextMenu.hidden, "Escape did not close the menu");
    card.click();
    assert(title() === "生产调度与控制", "next normal click after Escape was suppressed");

    const relationship = document.querySelector(".relationship-line");
    assert(relationship, "reviewed relationship missing");
    relationship.click();
    const explanation = document.querySelector(".plain-collaboration-explanation");
    assert(explanation?.textContent.includes("调度员"), "plain relationship analogy missing");
    assert(explanation.textContent.includes("生产调度与控制"), "source module role missing from explanation");
    assert(explanation.textContent.includes("翻译执行与模型接入"), "target module role missing from explanation");
    assert(explanation.textContent.includes("不代表某次运行真的启动过 Worker"), "runtime limit missing from explanation");
    const productionEvidence = Array.from(document.querySelectorAll(".evidence-group .evidence-card"));
    const evidenceExplanations = productionEvidence.map((item) => item.querySelector(".evidence-plain-explanation")?.textContent || "");
    assert(productionEvidence.length === 13, "approved production evidence count changed");
    assert(evidenceExplanations.every(Boolean), "approved production evidence lacks a nearby plain explanation");
    assert(new Set(evidenceExplanations).size === 13, "evidence explanations are generic duplicates");
    const argumentVector = document.querySelector('[data-evidence-kind="production_argument_vector"] .evidence-plain-explanation');
    assert(argumentVector?.textContent.includes("派工单") && argumentVector.textContent.includes("--mode production") && argumentVector.textContent.includes("静态源码"), "production argument explanation is not concrete or bounded");
    const wrapperImport = document.querySelector('[data-evidence-kind="worker_wrapper_import"] .evidence-plain-explanation');
    assert(wrapperImport?.textContent.includes("实际执行入口") && wrapperImport.textContent.includes("booktrans.production_worker.worker"), "worker import explanation is not concrete");
    const workerGuard = document.querySelector('[data-evidence-kind="worker_batch_checkpoint_guard"] .evidence-plain-explanation');
    assert(workerGuard?.textContent.includes("开工前") && workerGuard.textContent.includes("batch-label") && workerGuard.textContent.includes("checkpoint"), "worker guard explanation is not concrete");

    document.documentElement.dataset.domRegression = "pass";
  } catch (error) {
    document.documentElement.dataset.domRegression = "fail";
    document.documentElement.dataset.domRegressionError = String(error?.message || error);
  }
})();
