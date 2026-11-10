(async () => {
  "use strict";

  const parentIds = [
    "document-understanding",
    "scope-and-request",
    "translation-execution",
    "quality-and-repair",
    "production-evidence",
    "production-control",
    "candidate-and-release",
  ];
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const nextPaint = () => new Promise((resolve) => window.setTimeout(resolve, 80));
  const card = (id) => document.querySelector(`[data-module-id="${id}"]`);
  const openMenu = (node) => node.dispatchEvent(new MouseEvent("contextmenu", {
    bubbles: true, cancelable: true, clientX: 48, clientY: 48,
  }));

  try {
    const drilldowns = Object.fromEntries(
      window.ARCHITECTURE_EXPLORER_DATA.navigation.drilldowns.map((item) => [item.parent_module_id, item]),
    );
    for (const parentId of parentIds) {
      const drilldown = drilldowns[parentId];
      assert(drilldown, `${parentId} drilldown is missing`);
      card(parentId).click();
      await nextPaint();

      for (const candidate of drilldown.children) {
        const node = card(candidate.id);
        const inlineDetail = node.querySelector(".module-inline-detail");
        assert(inlineDetail?.hidden, `${candidate.id} exposes inline evidence while collapsed`);
        openMenu(node);
        document.querySelector("#context-detail-toggle").click();
        await nextPaint();

        const expandedNode = card(candidate.id);
        assert(expandedNode.classList.contains("expanded"), `${candidate.id} did not expand`);
        const links = Array.from(expandedNode.querySelectorAll(".candidate-evidence-link"));
        const expectedIds = candidate.source_assignment.evidence_ids;
        assert(links.length === expectedIds.length, `${candidate.id} does not expose every stable Evidence ID inline`);
        assert(
          links.map((link) => link.dataset.evidenceId).join("|") === expectedIds.join("|"),
          `${candidate.id} inline Evidence IDs changed or were duplicated`,
        );
        assert(
          links.every((link, index) => link.textContent.includes(expectedIds[index])),
          `${candidate.id} inline Evidence ID is not visible and locatable`,
        );

        const firstLink = links[0];
        assert(firstLink.tagName === "BUTTON" && firstLink.type === "button", `${candidate.id} Evidence reference lacks native keyboard activation semantics`);
        firstLink.focus();
        assert(document.activeElement === firstLink, `${candidate.id} Evidence reference is not keyboard focusable`);
        firstLink.click();
        await nextPaint();
        const detail = document.querySelector("#detail-content");
        assert(detail.dataset.evidenceId === expectedIds[0], `${candidate.id} opened the wrong evidence record`);
        assert(detail.querySelector("#detail-title")?.textContent === "源码证据详情", `${candidate.id} evidence dialog lacks an accessible title`);
        assert(detail.querySelector(".candidate-evidence-card")?.textContent.includes(expectedIds[0]), `${candidate.id} evidence detail omits its stable ID`);
        assert(detail.querySelector(".raw-evidence-facts")?.textContent.includes("源码定位"), `${candidate.id} evidence detail is not locatable`);
        assert(card(candidate.id).classList.contains("expanded"), `${candidate.id} Evidence click collapsed its node`);
        assert(document.querySelector(".crumb.current")?.textContent === drilldown.name, `${candidate.id} Evidence click triggered navigation`);
        document.querySelector("#relationship-detail").close();
      }

      document.querySelector("#home-button").click();
      await nextPaint();
    }

    document.documentElement.dataset.t7CandidateInlineEvidence = "pass";
  } catch (error) {
    document.documentElement.dataset.t7CandidateInlineEvidence = "fail";
    document.documentElement.dataset.t7CandidateInlineEvidenceError = String(error?.message || error);
  }
})();
