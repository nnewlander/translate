(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 100));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const clickCall = async (callee) => {
    const row = Array.from(document.querySelectorAll(".call-row"))
      .find((item) => item.textContent.includes(callee));
    assert(row, `missing call row: ${callee}`);
    row.focus();
    row.click();
    await nextPaint();
    return row;
  };

  try {
    const topLevelCard = card("translation-execution");
    topLevelCard.focus();
    topLevelCard.dispatchEvent(new KeyboardEvent("keydown", {
      key: "Enter", code: "Enter", bubbles: true, cancelable: true,
    }));
    await nextPaint();
    const candidate = card("translation-routing-and-acquisition");
    candidate.focus();
    candidate.dispatchEvent(new KeyboardEvent("keydown", {
      key: "F10", code: "F10", shiftKey: true, bubbles: true, cancelable: true,
    }));
    assert(document.activeElement?.id === "context-detail-toggle", "Shift+F10 did not focus the context menu");
    document.querySelector("#context-evidence").focus();
    document.querySelector("#context-evidence").click();
    await nextPaint();
    const evidenceCard = Array.from(document.querySelectorAll(".candidate-evidence-card"))
      .find((item) => item.textContent.includes("src/booktrans/production_worker/pipeline.py"));
    assert(evidenceCard, "translation candidate did not expose pipeline.py Evidence");
    evidenceCard.querySelector(".browse-definitions").click();
    await nextPaint();
    const detail = document.querySelector("#detail-content");
    const filter = detail.querySelector("#definition-name-filter");
    filter.value = "process_unit";
    filter.dispatchEvent(new Event("input", { bubbles: true }));
    detail.querySelector(".definition-row").click();
    await nextPaint();

    assert(detail.dataset.sourceTrailLevel === "definition", "definition trail level is missing");
    assert(detail.querySelector(".source-trail-breadcrumb")?.textContent.includes("process_unit"), "definition breadcrumb is missing");
    assert(detail.querySelector(".definition-source")?.textContent.includes("def process_unit("), "process_unit source is missing");
    assert(Array.from(detail.querySelectorAll(".call-row")).some((item) => item.textContent.includes("route_semantic_unit") && item.textContent.includes("exact-import")), "route call is not exact-import");
    assert(Array.from(detail.querySelectorAll(".call-row")).some((item) => item.textContent.includes("enrich_request") && item.textContent.includes("exact-local")), "enrich call is not exact-local");
    assert(Array.from(detail.querySelectorAll(".call-row")).some((item) => item.textContent.includes("translate_unit") && item.textContent.includes("exact-local")), "translation helper call is not exact-local");

    await clickCall("route_semantic_unit");
    assert(detail.dataset.sourceTrailLevel === "call", "call trail level is missing");
    assert(detail.textContent.includes("exact-import") && detail.textContent.includes("静态调用事实"), "exact call trust label is missing");
    const routeTarget = detail.querySelector(".call-target");
    assert(routeTarget, "exact imported call has no target action");
    routeTarget.focus();
    routeTarget.click();
    await nextPaint();
    assert(detail.dataset.sourceTrailLevel === "definition", "target definition did not open");
    assert(detail.textContent.includes("booktrans.providers.router.route_semantic_unit"), "wrong exact imported target opened");
    assert(detail.textContent.includes("已确认静态目标") && detail.textContent.includes("不证明运行时执行"), "target trust boundary is missing");
    detail.querySelector(".definition-back").click();
    await nextPaint();
    assert(detail.dataset.sourceTrailLevel === "call", "Back did not restore route call");
    assert(document.activeElement?.classList.contains("call-target"), "Back did not restore exact-target focus");
    detail.querySelector(".definition-back").click();
    await nextPaint();
    assert(detail.textContent.includes("booktrans.production_worker.pipeline.process_unit"), "Back did not restore process_unit");
    assert(document.activeElement?.classList.contains("call-row"), "Back did not restore call-row focus");

    await clickCall("translate_unit");
    detail.querySelector(".call-target").click();
    await nextPaint();
    assert(detail.textContent.includes("booktrans.production_worker.pipeline.translate_unit"), "local helper target did not open");
    await clickCall("providers.mt.translate");
    assert(detail.textContent.includes("unresolved-dispatch"), "Provider dispatch was not unresolved");
    assert(detail.textContent.includes("运行时接收者") || detail.textContent.includes("runtime-receiver"), "unresolved reason is missing");
    assert(!detail.querySelector(".call-target"), "unresolved Provider dispatch exposed a guessed target");
    assert(detail.querySelector(".source-trail-breadcrumb")?.textContent.includes("providers.mt.translate"), "call breadcrumb is missing");
    const dialog = document.querySelector("#relationship-detail");
    assert(document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1, "page overflowed while Source Trail was open");
    assert(dialog.scrollWidth <= dialog.clientWidth + 1, "Source Trail dialog overflowed horizontally");

    document.querySelector(".dialog-close").click();
    await nextPaint();
    assert(!detail.dataset.definitionFile && !detail.dataset.sourceTrailLevel, "close left stale Source Trail state");
    assert(
      document.activeElement === candidate,
      `Close did not restore the candidate opener focus (active=${document.activeElement?.tagName}.${document.activeElement?.className}; connected=${candidate.isConnected})`,
    );
    candidate.dispatchEvent(new KeyboardEvent("keydown", {
      key: "F10", code: "F10", shiftKey: true, bubbles: true, cancelable: true,
    }));
    document.querySelector("#context-evidence").focus();
    document.querySelector("#context-evidence").click();
    await nextPaint();
    assert(!detail.dataset.definitionFile && !detail.dataset.sourceTrailLevel, "reopen inherited stale Source Trail state");
    document.querySelector("#relationship-detail").close();
    document.documentElement.dataset.t10SourceTrail = "pass";
  } catch (error) {
    document.documentElement.dataset.t10SourceTrail = "fail";
    document.documentElement.dataset.t10SourceTrailError = String(error?.message || error);
  }
})();
