(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 100));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);

  try {
    card("translation-execution").click();
    await nextPaint();
    const collaborationButton = document.querySelector("#candidate-collaboration-list .candidate-collaboration");
    collaborationButton.focus();
    collaborationButton.click();
    await nextPaint();
    const detail = document.querySelector("#detail-content");
    const collaborationId = "candidate-collaboration:translation-routing-to-provider";
    assert(detail.dataset.collaborationId === collaborationId, "candidate collaboration detail did not open");
    const evidenceCard = Array.from(detail.querySelectorAll(".candidate-evidence-card"))
      .find((item) => item.textContent.includes("src/booktrans/production_worker/pipeline.py"));
    assert(evidenceCard, "collaboration did not expose pipeline.py evidence");
    const browse = evidenceCard.querySelector(".browse-definitions");
    assert(browse, "collaboration Evidence has no definition browser action");
    browse.click();
    await nextPaint();
    assert(detail.dataset.definitionFile === "src/booktrans/production_worker/pipeline.py", "wrong collaboration file opened");

    detail.querySelector(".definition-back").click();
    await nextPaint();
    assert(detail.dataset.collaborationId === collaborationId, "Back did not restore collaboration detail");
    assert(!detail.dataset.definitionFile, "Back left stale definition-file state on collaboration details");
    assert(document.activeElement?.classList.contains("browse-definitions"), "Back did not restore collaboration Evidence focus");
    assert(document.querySelector(".crumb.current")?.textContent === "翻译执行与模型接入", "collaboration source browsing changed macro navigation");

    document.querySelector("#relationship-detail").close();
    assert(document.activeElement === collaborationButton, "closing collaboration detail did not restore opener focus");
    collaborationButton.click();
    await nextPaint();
    assert(detail.dataset.collaborationId === collaborationId, "collaboration detail could not reopen after close");
    assert(!detail.dataset.definitionFile, "reopened collaboration inherited stale definition-file state");
    document.querySelector("#relationship-detail").close();
    document.documentElement.dataset.t10CollaborationDefinitionBrowser = "pass";
  } catch (error) {
    document.documentElement.dataset.t10CollaborationDefinitionBrowser = "fail";
    document.documentElement.dataset.t10CollaborationDefinitionBrowserError = String(error?.message || error);
  }
})();
