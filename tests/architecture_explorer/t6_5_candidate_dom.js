(() => {
  "use strict";

  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const closeDetail = () => document.querySelector("#relationship-detail").close();
  try {
    document.querySelector("#home-button").click();
    assert(document.querySelectorAll(".relationship-line").length === 1, "formal relationship count changed");
    assert(document.querySelector(".relationship-line .relation-status")?.textContent.includes("当前已审核"), "formal relationship trust changed");
    assert(card("production-evidence").textContent.includes("源码覆盖：未核验"), "production evidence coverage was promoted");
    assert(card("candidate-and-release").textContent.includes("源码覆盖：未核验"), "candidate release coverage was promoted");

    const workflowHelp = document.querySelector("#workflow-help");
    assert(workflowHelp?.textContent.includes("Supervisor → Worker"), "workflow helper omits the highlighted path");
    assert(workflowHelp?.textContent.includes("不表示七模块按此顺序执行"), "workflow helper omits its boundary");
    const positions = new Map(Array.from(document.querySelectorAll("#module-grid .module-card"), (item) => {
      const rect = item.getBoundingClientRect();
      return [item.dataset.moduleId, [rect.left, rect.top, rect.width, rect.height]];
    }));
    document.querySelector("#workflow-toggle").click();
    assert(document.body.classList.contains("workflow-highlight"), "workflow highlight did not activate");
    for (const item of document.querySelectorAll("#module-grid .module-card")) {
      const rect = item.getBoundingClientRect();
      assert(JSON.stringify(positions.get(item.dataset.moduleId)) === JSON.stringify([rect.left, rect.top, rect.width, rect.height]), "workflow highlight moved module cards");
    }
    assert(document.querySelectorAll("#module-grid .module-card").length === 7, "workflow highlight changed module count");

    card("production-evidence").click();
    assert(document.querySelector(".hero h1")?.textContent === "生产证据与 Checkpoint", "production evidence drilldown did not open");
    let children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 2, "production evidence candidate count changed");
    assert(children.every((item) => item.textContent.includes("未审核")), "production evidence candidate trust is hidden");
    assert(children.every((item) => item.querySelector(".candidate-plain-explanation")?.textContent), "production evidence plain explanation missing");
    assert(document.querySelector(".lede")?.textContent.includes("七模块源码覆盖已完成正式审核"), "seven-module review boundary missing");

    card("checkpoint-evidence-persistence").click();
    let detail = document.querySelector("#detail-content");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 1, "checkpoint persistence evidence missing");
    assert(detail.textContent.includes("production_checkpoint.py") && detail.textContent.includes("write_json_once"), "checkpoint persistence source fact missing");
    assert(detail.textContent.includes("不能证明") || detail.textContent.includes("不证明"), "checkpoint evidence limitation missing");
    closeDetail();

    card("checkpoint-manifest-seal").click();
    detail = document.querySelector("#detail-content");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 1, "checkpoint manifest evidence missing");
    assert(detail.textContent.includes("production_checkpoint.py") && detail.textContent.includes("booktrans.harness.batch_manifest") && detail.textContent.includes("sealed_checkpoint_manifest_projection"), "checkpoint manifest source semantics missing");
    assert(detail.textContent.includes("档案管理员") && detail.textContent.includes("不代表每个 Checkpoint 都已通过或发布"), "checkpoint manifest plain explanation missing");
    assert(detail.textContent.includes("组件状态：未审核") && detail.textContent.includes("源码归属：未核验"), "checkpoint manifest candidate status missing");
    closeDetail();

    let collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1, "production evidence collaboration count changed");
    collaborations[0].click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("候选协作") && detail.textContent.includes("不新增正式架构关系"), "checkpoint collaboration boundary missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 2, "checkpoint collaboration evidence missing");
    closeDetail();
    document.querySelector("#back-button").click();

    card("candidate-and-release").click();
    assert(document.querySelector(".hero h1")?.textContent === "候选文档与发布验证", "candidate release drilldown did not open");
    children = Array.from(document.querySelectorAll("#module-grid .module-card"));
    assert(children.length === 3, "candidate release candidate count changed");
    assert(children.every((item) => item.textContent.includes("未审核")), "candidate release trust is hidden");
    assert(children.every((item) => item.querySelector(".candidate-plain-explanation")?.textContent), "candidate release plain explanation missing");
    assert(document.querySelector(".lede")?.textContent.includes("不是每个 Checkpoint"), "checkpoint workflow boundary missing");

    card("candidate-document-projection").click();
    detail = document.querySelector("#detail-content");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 1, "candidate projection evidence missing");
    assert(detail.textContent.includes("tagged_roundtrip.py") && detail.textContent.includes("booktrans.ooxml.tagged_projection") && detail.textContent.includes("apply_tagged_projections"), "candidate projection source semantics missing");
    assert(detail.textContent.includes("精确坐标") && detail.textContent.includes("不证明生产 Checkpoint 必然生成候选文档"), "candidate projection plain explanation missing");
    assert(detail.textContent.includes("职责状态：未审核") && detail.textContent.includes("源码归属：未核验"), "candidate projection candidate status missing");
    closeDetail();

    card("package-preservation-verifier").click();
    detail = document.querySelector("#detail-content");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 2, "package preservation evidence missing");
    assert(detail.textContent.includes("tagged_roundtrip.py") && detail.textContent.includes("package_preservation.py"), "package preservation source paths missing");
    assert(detail.textContent.includes("不等于视觉 QA"), "visual QA boundary missing");
    closeDetail();

    card("release-membership-derivation").click();
    detail = document.querySelector("#detail-content");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 1, "release membership evidence missing");
    assert(detail.textContent.includes("advance_full_book_batch013.py") && detail.textContent.includes("derive_release_membership"), "release membership source fact missing");
    closeDetail();

    collaborations = Array.from(document.querySelectorAll("#candidate-collaborations .candidate-collaboration"));
    assert(collaborations.length === 1, "candidate release collaboration count changed");
    collaborations[0].click();
    detail = document.querySelector("#detail-content");
    assert(detail.textContent.includes("不能替代视觉 QA 或发布验收"), "candidate release collaboration boundary missing");
    assert(detail.querySelectorAll(".candidate-evidence-card").length === 2, "candidate release collaboration evidence missing");
    closeDetail();

    document.documentElement.dataset.t6FiveCandidate = "pass";
  } catch (error) {
    document.documentElement.dataset.t6FiveCandidate = "fail";
    document.documentElement.dataset.t6FiveCandidateError = String(error?.message || error);
  }
})();
