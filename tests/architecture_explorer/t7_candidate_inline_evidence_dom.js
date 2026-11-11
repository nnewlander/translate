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
        const expectedIds = candidate.source_assignment.evidence_ids;
        assert(
          expectedIds.every((evidenceId) => !expandedNode.textContent.includes(evidenceId)),
          `${candidate.id} lays out full Evidence IDs inside the expanded card`,
        );
        assert(
          expandedNode.querySelector(".candidate-evidence-count")?.textContent.includes(`源码依据：${expectedIds.length} 条`),
          `${candidate.id} inline evidence count changed`,
        );
        const links = Array.from(expandedNode.querySelectorAll(".candidate-evidence-link"));
        assert(links.length === 1, `${candidate.id} does not use one compact evidence entry`);

        const firstLink = links[0];
        assert(firstLink.textContent === "查看源码依据", `${candidate.id} evidence entry label changed`);
        assert(firstLink.tagName === "BUTTON" && firstLink.type === "button", `${candidate.id} Evidence reference lacks native keyboard activation semantics`);
        firstLink.focus();
        assert(document.activeElement === firstLink, `${candidate.id} Evidence reference is not keyboard focusable`);
        firstLink.click();
        await nextPaint();
        const detail = document.querySelector("#detail-content");
        assert(detail.dataset.moduleId === candidate.id, `${candidate.id} opened evidence for a different node`);
        assert(detail.querySelector("#detail-title")?.textContent.includes(candidate.name), `${candidate.id} evidence dialog lacks an accessible node title`);
        const evidenceCards = Array.from(detail.querySelectorAll(".candidate-evidence-card"));
        assert(evidenceCards.length === expectedIds.length, `${candidate.id} evidence detail changed its evidence count`);
        assert(
          evidenceCards.every((evidenceCard, index) => evidenceCard.textContent.includes(expectedIds[index])),
          `${candidate.id} evidence detail changed stable Evidence IDs or order`,
        );
        assert(evidenceCards.every((evidenceCard) => evidenceCard.querySelector(".raw-evidence-facts")?.textContent.includes("源码定位") && evidenceCard.querySelector(".raw-evidence-facts")?.textContent.includes("事实种类")), `${candidate.id} evidence detail is not locatable`);
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
