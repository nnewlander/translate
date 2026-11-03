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

    document.documentElement.dataset.domRegression = "pass";
  } catch (error) {
    document.documentElement.dataset.domRegression = "fail";
    document.documentElement.dataset.domRegressionError = String(error?.message || error);
  }
})();
