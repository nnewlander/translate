(() => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  try {
    assert(document.querySelectorAll(".relationship-line").length === 1, "formal relationship count changed");
    assert(document.querySelector(".relationship-line .relation-status")?.textContent.includes("当前已审核"), "formal relationship trust changed");
    assert(card("production-control").textContent.includes("源码覆盖：未核验"), "top-level source coverage was promoted");
    assert(document.querySelector("#source-facts-count")?.textContent.includes("1 个 Supervisor"), "bounded PowerShell scan scope is not visible");

    card("production-control").click();
    assert(document.querySelector(".hero h1")?.textContent === "生产调度与控制", "production drilldown did not open");
    const children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 5, "production candidate count changed");
    assert(children.filter((item) => item.textContent.includes("候选职责")).length === 3, "T4 responsibilities were not preserved");
    assert(children.filter((item) => item.textContent.includes("候选组件")).length === 2, "production components missing");
    assert(children.every((item) => item.textContent.includes("未审核")), "candidate review status is not visible");
    assert(children.every((item) => item.querySelector(".candidate-plain-explanation")?.textContent), "nearby plain explanations missing");

    card("worker-launch-boundary").click();
    let detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("唯一正式 Supervisor→Worker 关系"), "formal/candidate boundary explanation missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 3, "worker boundary evidence did not resolve");
    assert(!detail.querySelector(".load-error"), "worker boundary has unresolved evidence");
    assert(detail.textContent.includes("production_campaign_supervisor.ps1"), "PowerShell evidence path missing");
    assert(detail.textContent.includes("run_qwen_deterministic_worker.py"), "Python wrapper evidence path missing");
    assert(detail.textContent.includes("静态 AST") && detail.textContent.includes("不证明"), "PowerShell evidence limitation missing");
    assert(Array.from(detail.querySelectorAll(".evidence-link")).some((item) => item.textContent.includes("进程启动")), "reviewed relationship link missing from T4 node");
    document.querySelector("#relationship-detail").close();

    card("production-control-adapters").click();
    detail = document.querySelector("#detail-content");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 2, "adapter evidence did not resolve");
    assert(detail.textContent.includes("dot-source") && detail.textContent.includes("未审核候选"), "dot-source explanation missing");
    document.querySelector("#relationship-detail").close();

    const collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 2, "production candidate collaborations missing");
    collaborations[0].click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选协作") && detail.textContent.includes("不证明运行时"), "candidate collaboration trust boundary missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 2, "candidate collaboration evidence missing");
    document.querySelector("#relationship-detail").close();
    document.querySelector("#back-button").click();

    document.documentElement.dataset.t6Production = "pass";
  } catch (error) {
    document.documentElement.dataset.t6Production = "fail";
    document.documentElement.dataset.t6ProductionError = String(error?.message || error);
  }
})();
