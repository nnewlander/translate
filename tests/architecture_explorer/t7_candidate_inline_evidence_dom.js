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
        assert(expandedNode.querySelector(".module-responsibility")?.textContent === candidate.responsibility, `${candidate.id} lost its professional responsibility`);
        assert(expandedNode.querySelector(".candidate-plain-explanation")?.textContent === candidate.plain_explanation, `${candidate.id} lost its plain Chinese explanation`);
        assert(expandedNode.querySelector(".candidate-node-type")?.textContent.includes("候选"), `${candidate.id} lost its candidate type`);
        assert(expandedNode.textContent.includes("状态：未审核") && expandedNode.textContent.includes("源码归属："), `${candidate.id} lost its candidate trust state`);
        assert(
          expectedIds.every((evidenceId) => !expandedNode.textContent.includes(evidenceId)),
          `${candidate.id} lays out full Evidence IDs inside the expanded card`,
        );
        assert(!expandedNode.querySelector(".candidate-evidence-count"), `${candidate.id} still repeats its evidence count inline`);
        assert(!expandedNode.querySelector(".candidate-evidence-link"), `${candidate.id} still exposes an inline evidence button`);

        expandedNode.focus();
        expandedNode.dispatchEvent(new KeyboardEvent("keydown", { key: "F10", shiftKey: true, bubbles: true, cancelable: true }));
        const contextEvidence = document.querySelector("#context-evidence");
        assert(!document.querySelector("#module-context-menu").hidden, `${candidate.id} keyboard menu did not open`);
        contextEvidence.focus();
        assert(document.activeElement === contextEvidence, `${candidate.id} context evidence action is not keyboard focusable`);
        contextEvidence.click();
        await nextPaint();
        const detail = document.querySelector("#detail-content");
        assert(detail.querySelector("#detail-title")?.textContent === candidate.name, `${candidate.id} context action opened evidence for a different record`);
        const evidenceCards = Array.from(detail.querySelectorAll(".candidate-evidence-card"));
        assert(evidenceCards.length === expectedIds.length, `${candidate.id} context evidence detail changed its evidence count`);
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
