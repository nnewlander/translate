(() => {
  "use strict";

  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);
  card("quality-and-repair").click();
  card("quality-semantic-review-and-repair").click();
  window.setTimeout(() => {
    document.querySelector(".hero").hidden = true;
    document.querySelector(".architecture-navigation").hidden = true;
    document.querySelector("#source-snapshot").hidden = true;
    document.querySelector(".section-heading").hidden = true;
    document.querySelector("main").style.paddingTop = "24px";
    document.querySelector("#graph-surface").scrollIntoView({ block: "start" });
    document.documentElement.dataset.ticket63ScreenshotReady = "true";
  }, 250);
})();
