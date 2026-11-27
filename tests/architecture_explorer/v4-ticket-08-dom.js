(() => {
  "use strict";

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  window.addEventListener("error", (event) => {
    document.documentElement.dataset.ticket08Error = event.message;
  });
  const card = (id) => document.querySelector(`[data-module-id="${CSS.escape(id)}"]`);
  const path = () => document.querySelector("#breadcrumb").textContent;
  const detail = document.querySelector("#relationship-detail");
  const detailContent = document.querySelector("#detail-content");
  const relationships = document.querySelector("#candidate-collaboration-list");
  const back = document.querySelector("#back-button");
  const home = document.querySelector("#home-button");
  const settle = () => new Promise((resolve) => window.setTimeout(resolve, 40));
  const open = (id) => {
    const appearance = card(id);
    assert(appearance, `missing Appearance for ${id} in ${path()}`);
    appearance.click();
  };
  const checkDetail = async (id, name) => {
    const before = path();
    card(id)?.focus();
    open(id);
    assert(path() === before, `${id} changed the breadcrumb instead of opening details: ${before} → ${path()}`);
    assert(detail.open && detailContent.querySelector("#detail-title").textContent === name,
      `${id} did not open its Node details`);
    detail.querySelector(".dialog-close").click();
    await settle();
  };
  const checkView = (name, count) => {
    assert(path().includes(name), `could not open ${name}`);
    assert(document.documentElement.scrollWidth <= innerWidth, `${name} overflows the viewport`);
    const buttons = [...relationships.querySelectorAll("button")];
    assert(buttons.length === count, `${name} is missing relationship rows`);
    for (const button of buttons) {
      assert(button.offsetWidth > 0 && button.scrollWidth <= button.clientWidth + 1,
        `unreadable relationship row in ${name}`);
    }
  };

  card("production-evidence").click();
  card("checkpoint-manifest-seal").dispatchEvent(new KeyboardEvent("keydown", {
    key: "Enter", bubbles: true,
  }));
  assert(card("prepared-seal-generation"), "Prepared Seal did not open");
  checkView("Checkpoint 封存职责", 3);
  assert(relationships.textContent.includes("分解状态：未审核"), "decomposition state is missing");
  assert(relationships.textContent.includes("协作状态：未审核"), "collaboration state is missing");
  const sealPath = path();
  const relation = [...relationships.querySelectorAll("button")].find(
    (button) => button.textContent.includes("完整待封存代次进入清单投影校验")
  );
  assert(relation, "prepared-to-projection relationship is missing");
  relation.focus();
  relation.click();
  assert(detail.open, "relationship detail did not open");
  assert(detailContent.querySelector("#detail-title").textContent.includes("工件代次 → Checkpoint 封存投影"),
    "relationship direction changed");
  const evidence = detailContent.querySelector(".browse-definitions");
  assert(evidence, "relationship evidence is missing a Source Trail entry");
  evidence.click();
  assert(detailContent.dataset.sourceTrailLevel === "file", "Source Trail did not open");
  assert(path() === sealPath, "opening Source Trail changed the architecture route");
  detail.close();

  const checkNavigation = async () => {
    await settle();
    assert(document.activeElement === relation, "closing Source Trail did not restore relationship focus");
    await checkDetail("checkpoint-manifest-seal", "Checkpoint 清单封存组件");
    await checkDetail("prepared-seal-generation", "Prepared Seal 工件代次");
    open("sealed-checkpoint-manifest-projection");
    checkView("清单发布与恢复", 2);
    await checkDetail("checkpoint-manifest-seal", "Checkpoint 清单封存组件");
    await checkDetail("sealed-checkpoint-manifest-projection", "Checkpoint 封存投影");
    open("checkpoint-manifest-publication");
    checkView("从封存工件到批次发布", 3);
    await checkDetail("checkpoint-manifest-publication", "批次清单发布与恢复");
    back.click();
    await settle();
    assert(document.activeElement === card("checkpoint-manifest-publication"), "Back did not restore publication Appearance");
    back.click();
    assert(card("prepared-seal-generation"), "Back did not return from publication to the seal View");
    back.click();
    await settle();
    assert(document.activeElement === card("checkpoint-manifest-seal"), "Back did not restore Appearance focus");
    open("checkpoint-evidence-persistence");
    checkView("Checkpoint 证据工件", 2);
    await checkDetail("checkpoint-evidence-persistence", "Checkpoint 证据持久化");
    await checkDetail("checkpoint-raw-artifacts", "RAW 与阶段证据");
    open("checkpoint-final-qa-evidence");
    checkView("从封存工件到批次发布", 3);
    await checkDetail("checkpoint-final-qa-evidence", "Checkpoint 最终 QA 证据");
    document.documentElement.setAttribute("data-ticket-08-repeated", "pass");
    await checkDetail("batch-release-debt-reconciliation", "批次发布债务与门禁");
    await checkDetail("checkpoint-manifest-publication", "批次清单发布与恢复");
    const handoff = [...relationships.querySelectorAll("button")].find(
      (button) => button.textContent.includes("最终 QA 工件提供发布成员依据")
    );
    assert(handoff, "cross-module collaboration is missing");
    handoff.click();
    assert(detailContent.querySelector("#detail-title").textContent.includes(
      "Checkpoint 最终 QA 证据 → 发布成员资格派生"
    ), "cross-module direction changed");
    assert(detailContent.querySelector(".browse-definitions"), "cross-module Evidence is missing");
    detail.querySelector(".dialog-close").click();
    await settle();
    open("release-membership-derivation");
    checkView("发布成员资格与后继选择", 3);
    assert(path().includes("从封存工件到批次发布"), "cross-module breadcrumb is missing");
    await checkDetail("release-membership-derivation", "发布成员资格派生");
    await checkDetail("batch-release-debt-reconciliation", "批次发布债务与门禁");
    await checkDetail("release-membership-evidence-rules", "发布成员证据准入");
    await checkDetail("release-rollover-phase-selection", "后继批次与 TOC 阶段选择");

    home.click();
    assert(card("production-evidence"), "Home did not restore the overview");
    open("candidate-and-release");
    open("release-membership-derivation");
    await checkDetail("batch-release-debt-reconciliation", "批次发布债务与门禁");
    back.click();
    open("package-preservation-verifier");
    checkView("候选 DOCX 的包保真与回检", 3);
    await checkDetail("tagged-candidate-output", "带标记的候选 DOCX 写回");
    await checkDetail("package-preservation-verifier", "DOCX 包保真验证组件");
    await checkDetail("protected-package-payload-check", "受保护的 DOCX 包负载");
    await checkDetail("projected-anchor-roundtrip-check", "候选重抽取与锚点回检");
    back.click();
    open("candidate-document-projection");
    checkView("候选 DOCX 的生成入口", 3);
    await checkDetail("candidate-document-projection", "候选文档受控投影");
    await checkDetail("candidate-harness-entrypoints", "合成往返与校准导入入口");
    await checkDetail("rendered-document-diagnostic", "独立渲染差异诊断");
    open("tagged-candidate-output");
    checkView("候选 DOCX 的包保真与回检", 3);
    await checkDetail("tagged-candidate-output", "带标记的候选 DOCX 写回");
    back.click();
    await settle();
    assert(document.activeElement === card("tagged-candidate-output"), "Back did not restore the projection Appearance");
    home.click();
    open("production-evidence");
    open("checkpoint-manifest-seal");
    open("sealed-checkpoint-manifest-projection");
    open("checkpoint-manifest-publication");
    await checkDetail("checkpoint-final-qa-evidence", "Checkpoint 最终 QA 证据");
    document.documentElement.setAttribute("data-ticket-08-width", String(innerWidth));
    document.documentElement.setAttribute("data-ticket-08", "pass");
  };
  checkNavigation().catch((error) => {
    document.documentElement.dataset.ticket08Error = error.message;
  });
})();
