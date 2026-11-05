(() => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  try {
    const details = document.querySelector("#source-facts");
    const select = document.querySelector("#source-module-select");
    const detail = document.querySelector("#source-module-detail");
    assert(details && !details.open, "source facts must be collapsed by default");
    assert(select.options.length === 4, "source module selector must expose the four scoped files");
    assert(document.querySelectorAll("#module-grid .module-card").length === 7, "seven-module view changed");

    details.open = true;
    const cli = Array.from(select.options).find((option) => option.textContent.endsWith("src/booktrans/cli.py"));
    assert(cli, "CLI source option missing");
    select.value = cli.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    assert(detail.textContent.includes("booktrans.cli"), "module identity missing");
    assert(detail.querySelector(".source-module-trace")?.textContent.includes("src/booktrans/cli.py"), "file-specific trace explanation missing");
    assert(detail.textContent.includes("declarative_cli"), "declarative CLI entry missing");
    assert(detail.textContent.includes("booktrans.domain"), "direct import missing");
    assert(Array.from(detail.querySelectorAll(".source-fact-explanation")).some((item) => item.textContent.includes("typer") && item.textContent.includes("不能证明运行时")), "specific import explanation missing");
    assert(Array.from(detail.querySelectorAll(".source-fact-explanation")).some((item) => item.textContent.includes("app") && item.textContent.includes("命令行应用入口")), "specific CLI entry explanation missing");
    assert(detail.querySelectorAll(".source-module-identity").length === 1, "more than one module rendered");

    const packageEntry = Array.from(select.options).find((option) => option.textContent.endsWith("src/booktrans/__main__.py"));
    assert(packageEntry, "package entry option missing");
    select.value = packageEntry.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    assert(Array.from(detail.querySelectorAll(".source-fact-explanation")).some((item) => item.textContent.includes("包方式启动") && item.textContent.includes("具体动作以源码证据为准")), "package entry explanation overclaims or is missing");

    const worker = Array.from(select.options).find((option) => option.textContent.endsWith("run_qwen_deterministic_worker.py"));
    assert(worker, "deterministic worker wrapper option missing");
    select.value = worker.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    assert(detail.textContent.includes("deterministic_worker_wrapper"), "worker wrapper entry missing");
    assert(Array.from(detail.querySelectorAll(".source-fact-explanation")).some((item) => item.textContent.includes("booktrans.production_worker.worker.main") && item.textContent.includes("不代表 Worker 在某次运行中真的启动")), "worker wrapper explanation missing runtime limit");
    assert(detail.textContent.includes("Evidence ID"), "locatable evidence missing");
    document.documentElement.dataset.t6SourceFacts = "pass";
  } catch (error) {
    document.documentElement.dataset.t6SourceFacts = "fail";
    document.documentElement.dataset.t6SourceFactsError = String(error?.message || error);
  }
})();
