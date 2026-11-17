(async () => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 100));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);

  try {
    card("translation-execution").click();
    await nextPaint();
    const candidate = card("translation-routing-and-acquisition");
    candidate.dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, cancelable: true, clientX: 48, clientY: 48,
    }));
    document.querySelector("#context-evidence").click();
    await nextPaint();

    const evidenceCard = Array.from(document.querySelectorAll(".candidate-evidence-card"))
      .find((item) => item.textContent.includes("src/booktrans/production_worker/pipeline.py"));
    assert(evidenceCard, "translation candidate did not expose pipeline.py evidence");
    const browse = evidenceCard.querySelector(".browse-definitions");
    assert(browse, "pipeline.py evidence has no definition browser action");
    browse.focus();
    browse.click();
    await nextPaint();

    const detail = document.querySelector("#detail-content");
    assert(detail.dataset.definitionFile === "src/booktrans/production_worker/pipeline.py", "wrong definition file opened");
    assert(detail.textContent.includes("静态定义事实") && detail.textContent.includes("不证明运行时执行"), "static trust warning is missing");
    assert(detail.querySelector(".definition-total")?.textContent.includes("个定义"), "definition total is missing");
    assert(!detail.querySelector(".definition-source"), "all source was rendered before a definition was selected");

    const nameFilter = detail.querySelector("#definition-name-filter");
    nameFilter.value = "process_unit";
    nameFilter.dispatchEvent(new Event("input", { bubbles: true }));
    const kindFilter = detail.querySelector("#definition-kind-filter");
    kindFilter.value = "function";
    kindFilter.dispatchEvent(new Event("change", { bubbles: true }));
    const rows = Array.from(detail.querySelectorAll(".definition-row"));
    assert(rows.length === 1 && rows[0].textContent.includes("process_unit"), "filters did not isolate process_unit");
    rows[0].click();
    await nextPaint();

    const selected = detail.querySelector(".definition-selected");
    assert(selected?.textContent.includes("booktrans.production_worker.pipeline.process_unit"), "qualified symbol is missing");
    assert(selected.textContent.includes("Evidence ID") && selected.textContent.includes("源码定位"), "definition evidence metadata is missing");
    assert(selected.querySelector(".definition-source")?.textContent.includes("def process_unit("), "process_unit source is missing");

    detail.querySelector(".definition-back").click();
    await nextPaint();
    assert(detail.dataset.sourceTrailLevel === "file", "Back did not restore the file definition list");
    assert(document.activeElement?.classList.contains("definition-row"), "Back did not restore definition-row focus");
    detail.querySelector(".definition-back").click();
    await nextPaint();
    assert(detail.querySelector("#detail-title")?.textContent === "语义路由与候选获取", "Back did not restore candidate details");
    assert(!detail.dataset.definitionFile, "Back left stale definition-file state on candidate details");
    assert(document.activeElement?.classList.contains("browse-definitions"), "Back did not restore focus to the Evidence action");
    assert(document.querySelector(".crumb.current")?.textContent === "翻译执行与模型接入", "definition browsing changed macro navigation");

    document.activeElement.click();
    await nextPaint();
    assert(detail.dataset.definitionFile, "definition view did not reopen before close regression");
    document.querySelector("#relationship-detail").close();
    document.querySelector("#relationship-list .relationship-line").click();
    await nextPaint();
    assert(detail.dataset.relationshipId, "relationship detail did not open after closing definition view");
    assert(!detail.dataset.definitionFile, "relationship detail inherited stale definition-file state");
    document.querySelector("#relationship-detail").close();
    document.documentElement.dataset.t10DefinitionBrowser = "pass";
  } catch (error) {
    document.documentElement.dataset.t10DefinitionBrowser = "fail";
    document.documentElement.dataset.t10DefinitionBrowserError = String(error?.message || error);
  }
})();
