(() => {
  "use strict";

  const find = (selector) => document.querySelector(selector);
  const card = (id) => find(`[data-module-id="${CSS.escape(id)}"]`);
  const require = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const frame = () => new Promise((resolve) => setTimeout(resolve, 40));
  const go = (id, key = "Enter") => {
    require(card(id), `Missing Appearance ${id}`);
    card(id).dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  };
  const qa = "quality-deterministic-qa";
  const format = "quality-format-anchor-validation";
  const token = "quality-deterministic-token-validation";
  const ownership = "quality-numeric-anchor-ownership";
  const breadcrumb = () => find("#breadcrumb").textContent;
  const dialog = find("#relationship-detail");
  const openExplicitPerspective = async (label) => {
    const path = breadcrumb();
    for (const activation of ["click", "Enter", " "]) {
      const appearance = card(qa);
      appearance.focus();
      if (activation === "click") appearance.click();
      else go(qa, activation);
      require(dialog.open && breadcrumb() === path, `${activation} must open QA detail in place`);
      require(find("#detail-title")?.textContent === "确定性结构 QA", "Wrong QA detail opened");
      require(dialog.querySelector(".view-navigation-link")?.textContent === label, "Explicit Perspective action is missing");
      if (activation === " ") break;
      dialog.querySelector(".dialog-close").click();
      await frame();
      require(document.activeElement === card(qa), "Closing QA detail did not restore Appearance focus");
    }
    dialog.querySelector(".view-navigation-link").click();
    require(!dialog.open, "Explicit Perspective action left the detail open");
  };
  const noOverflow = () => {
    require(document.documentElement.scrollWidth <= innerWidth, "View overflows the viewport");
  };

  (async () => {
    go("quality-and-repair", " ");
    go(qa);
    go(format);
    go(token);
    require(card(ownership), "Numeric ownership responsibility is missing");
    for (const label of ["质量判断与受限修复", "确定性结构 QA 内部职责", "格式结构校验", "确定性文本诊断"]) {
      require(breadcrumb().includes(label), `Breadcrumb omits ${label}`);
    }
    noOverflow();

    card(ownership).dispatchEvent(new MouseEvent("contextmenu", {
      bubbles: true, clientX: 20, clientY: 20,
    }));
    find("#context-evidence").click();
    require(find("#relationship-detail").open, "Deep Evidence dialog did not open");
    find("#detail-content .browse-definitions").click();
    require(find("#detail-content").dataset.sourceTrailLevel === "file", "Source Trail did not open");
    require(find("#detail-content").textContent.includes("deterministic_qa.py"), "Source Trail points outside token QA");
    find(".dialog-close").click();
    await frame();
    require(document.activeElement === card(ownership), "Source Trail close did not restore focus");

    find("#back-button").click();
    await frame();
    require(document.activeElement === card(token), "Back did not return focus to token QA");
    find("#back-button").click();
    await frame();
    require(document.activeElement === card(format), "Back did not return focus to format QA");

    const responsibilityPath = breadcrumb();
    await openExplicitPerspective("切换到候选结果处置");
    require(breadcrumb().includes("候选结果处置"), "Perspective switch did not open");
    require(card("translation-routing-and-acquisition"), "Translation boundary is missing from disposition");
    noOverflow();

    const row = Array.from(document.querySelectorAll("#candidate-collaboration-list button"))
      .find((button) => button.textContent.includes("提交失败码"));
    require(row, "Deterministic failure and semantic review are not distinguishable");
    row.focus();
    row.click();
    const detail = find("#detail-content");
    require(find("#relationship-detail").open && detail.textContent.includes("review=None"), "Repair gate input is missing");
    require(detail.textContent.includes("协作") && detail.textContent.includes("未审核"), "Candidate relationship trust or meaning is missing");
    const trail = detail.querySelector(".browse-definitions");
    require(trail, "Relationship does not expose Source Trail");
    trail.click();
    require(detail.dataset.sourceTrailLevel === "file", "Relationship Source Trail did not open");
    find(".dialog-close").click();
    await frame();
    require(document.activeElement === row, "Relationship Source Trail did not return focus");

    await openExplicitPerspective("返回确定性结构 QA 内部职责");
    await frame();
    require(breadcrumb() === responsibilityPath, "Perspective return did not truncate to the existing responsibility path");
    require(document.activeElement === card(qa), "Perspective return did not restore ancestor Appearance focus");
    require(card(format), "Perspective return did not show responsibility View");
    go("quality-qa-record");
    require(breadcrumb().includes("Checkpoint QA 恢复依据"), "Checkpoint recovery View did not open");
    require(card("quality-canonical-qa-failure"), "Canonical failure evidence is missing");
    require(card("quality-numeric-recovery-eligibility"), "Recovery eligibility decision is missing");
    noOverflow();
    find("#home-button").click();
    require(card("quality-and-repair"), "Home did not return to the system overview");
    document.documentElement.dataset.t9QualityWidth = innerWidth;
    document.documentElement.dataset.t9Quality = "pass";
  })().catch((error) => {
    document.documentElement.dataset.t9QualityError = error.message;
  });
})();
