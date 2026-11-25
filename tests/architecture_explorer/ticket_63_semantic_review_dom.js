(async () => {
  "use strict";

  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const waitForLayout = () => new Promise((resolve) => setTimeout(resolve, 80));
  const card = (nodeId) => document.querySelector(`[data-module-id="${CSS.escape(nodeId)}"]`);
  const breadcrumb = () => document.querySelector("#breadcrumb").textContent;
  const intersects = (a, b, inset = 0) => !(
    a.right <= b.left + inset || a.left >= b.right - inset
      || a.bottom <= b.top + inset || a.top >= b.bottom - inset
  );
  const pathPoints = (path) => {
    const values = [...path.getAttribute("d").matchAll(/-?\d+(?:\.\d+)?/g)].map((match) => Number(match[0]));
    return Array.from({ length: values.length / 2 }, (_, index) => ({ x: values[index * 2], y: values[index * 2 + 1] }));
  };
  const pointOnBoundary = (point, rect, surface, tolerance = 3) => {
    const x = point.x + surface.left;
    const y = point.y + surface.top;
    const onVertical = (Math.abs(x - rect.left) <= tolerance || Math.abs(x - rect.right) <= tolerance)
      && y >= rect.top - tolerance && y <= rect.bottom + tolerance;
    const onHorizontal = (Math.abs(y - rect.top) <= tolerance || Math.abs(y - rect.bottom) <= tolerance)
      && x >= rect.left - tolerance && x <= rect.right + tolerance;
    return onVertical || onHorizontal;
  };
  const segmentCrossesRect = (a, b, rect, surface, inset = 5) => {
    const left = rect.left - surface.left + inset;
    const right = rect.right - surface.left - inset;
    const top = rect.top - surface.top + inset;
    const bottom = rect.bottom - surface.top - inset;
    if (Math.abs(a.x - b.x) < 0.5) {
      return a.x > left && a.x < right && Math.max(a.y, b.y) > top && Math.min(a.y, b.y) < bottom;
    }
    if (Math.abs(a.y - b.y) < 0.5) {
      return a.y > top && a.y < bottom && Math.max(a.x, b.x) > left && Math.min(a.x, b.x) < right;
    }
    return false;
  };
  const segmentCrossesBounds = (start, end, bounds) => {
    let entry = 0; let exit = 1;
    for (const [origin, delta, minimum, maximum] of [
      [start.x, end.x - start.x, bounds.left, bounds.right],
      [start.y, end.y - start.y, bounds.top, bounds.bottom],
    ]) {
      if (Math.abs(delta) < 0.5) {
        if (origin <= minimum || origin >= maximum) return false;
        continue;
      }
      let near = (minimum - origin) / delta; let far = (maximum - origin) / delta;
      if (near > far) [near, far] = [far, near];
      entry = Math.max(entry, near); exit = Math.min(exit, far);
      if (entry >= exit) return false;
    }
    return exit > 0 && entry < 1;
  };
  const assertGraphGeometry = (expected, label) => {
    const surface = document.querySelector("#graph-surface").getBoundingClientRect();
    const cards = [...document.querySelectorAll("#module-grid .module-card")];
    const labels = [];
    const paths = new Map(expected.map((relation) => {
      const attribute = relation.family === "candidate-decomposition" ? "data-decomposition-id" : "data-collaboration-id";
      const group = document.querySelector(`#relationship-edge-layer [${attribute}="${CSS.escape(relation.id)}"]`);
      return [relation.id, group ? pathPoints(group.querySelector(".graph-edge")) : []];
    }));
    for (const relation of expected) {
      const attribute = relation.family === "candidate-decomposition" ? "data-decomposition-id" : "data-collaboration-id";
      const group = document.querySelector(`#relationship-edge-layer [${attribute}="${CSS.escape(relation.id)}"]`);
      const edgeLabel = document.querySelector(`#relationship-label-layer [${attribute}="${CSS.escape(relation.id)}"]`);
      assert(group, `${label}: missing edge ${relation.id}`);
      assert(edgeLabel, `${label}: missing label ${relation.id}`);
      assert(group.dataset.sourceNodeId === relation.source, `${label}: wrong source for ${relation.id}`);
      assert(group.dataset.targetNodeId === relation.target, `${label}: wrong target for ${relation.id}`);
      assert(edgeLabel.textContent === relation.name, `${label}: wrong label for ${relation.id}`);
      const points = pathPoints(group.querySelector(".graph-edge"));
      assert(points.length >= 2, `${label}: invalid path for ${relation.id}`);
      const semanticClass = relation.family === "candidate-decomposition" ? "decomposition" : "collaboration";
      assert(group.querySelector(".graph-edge").classList.contains(semanticClass), `${label}: ${relation.id} lost its relationship visual type`);
      assert(edgeLabel.classList.contains(semanticClass), `${label}: ${relation.id} label lost its relationship visual type`);
      assert(pointOnBoundary(points[0], card(relation.source).getBoundingClientRect(), surface), `${label}: source endpoint detached for ${relation.id}`);
      assert(pointOnBoundary(points.at(-1), card(relation.target).getBoundingClientRect(), surface), `${label}: target endpoint detached for ${relation.id}`);
      for (const unrelated of cards.filter((item) => ![relation.source, relation.target].includes(item.dataset.moduleId))) {
        for (let index = 1; index < points.length; index += 1) {
          assert(!segmentCrossesRect(points[index - 1], points[index], unrelated.getBoundingClientRect(), surface), `${label}: ${relation.id} crosses ${unrelated.dataset.moduleId}`);
        }
      }
      if (relation.family === "candidate-decomposition" && points.at(-1).y > points[0].y) {
        assert(
          points.every((point) => point.y >= points[0].y - 1 && point.y <= points.at(-1).y + 1),
          `${label}: ${relation.id} takes an unnecessary long-distance vertical detour`,
        );
      }
      if (label === "Ticket 63" && relation.family === "candidate-collaboration") {
        const pathLength = points.slice(1).reduce((sum, point, index) => (
          sum + Math.hypot(point.x - points[index].x, point.y - points[index].y)
        ), 0);
        const directLength = Math.hypot(points.at(-1).x - points[0].x, points.at(-1).y - points[0].y);
        assert(pathLength <= directLength * 1.05 + 1, `${label}: ${relation.id} takes an unnecessary collaboration detour`);
      }
      const labelRect = edgeLabel.getBoundingClientRect();
      assert(cards.every((item) => !intersects(labelRect, item.getBoundingClientRect(), 2)), `${label}: ${relation.id} label overlaps a node`);
      assert(labels.every((item) => !intersects(labelRect, item, 2)), `${label}: ${relation.id} label overlaps another label`);
      const localLabel = {
        left: labelRect.left - surface.left,
        right: labelRect.right - surface.left,
        top: labelRect.top - surface.top,
        bottom: labelRect.bottom - surface.top,
      };
      const associatedBounds = {
        left: localLabel.left - 24,
        right: localLabel.right + 24,
        top: localLabel.top - 24,
        bottom: localLabel.bottom + 24,
      };
      assert(
        points.slice(1).some((point, index) => segmentCrossesBounds(points[index], point, associatedBounds)),
        `${label}: ${relation.id} label is detached from its own line`,
      );
      for (const other of expected.filter((item) => item.id !== relation.id)) {
        const otherPoints = paths.get(other.id);
        assert(
          !otherPoints.slice(1).some((point, index) => segmentCrossesBounds(otherPoints[index], point, localLabel)),
          `${label}: ${relation.id} label sits on ${other.id}`,
        );
      }
      labels.push(labelRect);
    }
  };

  const ticketRelationships = [
    { id: "candidate-decomposition:quality-semantic-review-and-repair-to-semantic-review-contract-boundary", family: "candidate-decomposition", source: "quality-semantic-review-and-repair", target: "semantic-review-contract-boundary", name: "语义审核与受限修复分解为审核合同边界" },
    { id: "candidate-decomposition:quality-semantic-review-and-repair-to-repair-authorization-gate", family: "candidate-decomposition", source: "quality-semantic-review-and-repair", target: "repair-authorization-gate", name: "语义审核与受限修复分解为修复授权门禁" },
    { id: "candidate-decomposition:quality-semantic-review-and-repair-to-constrained-repair-call-boundary", family: "candidate-decomposition", source: "quality-semantic-review-and-repair", target: "constrained-repair-call-boundary", name: "语义审核与受限修复分解为受限修复调用边界" },
    { id: "candidate-collaboration:semantic-review-contract-boundary-to-repair-authorization-gate", family: "candidate-collaboration", source: "semantic-review-contract-boundary", target: "repair-authorization-gate", name: "审核合同信息衔接修复授权范围判断" },
    { id: "candidate-collaboration:repair-authorization-gate-to-constrained-repair-call-boundary", family: "candidate-collaboration", source: "repair-authorization-gate", target: "constrained-repair-call-boundary", name: "批准合同衔接受限修复调用" },
  ];

  const projectedRelationships = window.ARCHITECTURE_EXPLORER_DATA.view_projection.relationship_index;
  const approvalScope = (relationship, decisionId, evidenceCount) => [{
    object_key: { ...relationship.object_key },
    reviewed_source_snapshot: {
      revision: "09aaf4a033123c7eca8d280fa8982e5b0e711b86",
      worktree_state: "test-fixture",
    },
    claims_sha256: "1".repeat(64),
    evidence_ids: relationship.evidence_ids.slice(0, evidenceCount),
    decision_id: decisionId,
  }];
  const relationshipReviews = [
    { result: "APPROVED", state: "current", scope: ["github-comment:current", 2], expectedScope: "2 条 Evidence · github-comment:current", expectedStatus: "已批准 · 当前有效", current: true },
    { result: "APPROVED", state: "needs-review", scope: ["github-comment:historical", 1], expectedScope: "1 条 Evidence · github-comment:historical", expectedStatus: "APPROVED · 需要复核", current: false },
    { result: "APPROVED", state: "unknown", scope: ["github-comment:unknown", 1], expectedScope: "1 条 Evidence · github-comment:unknown", expectedStatus: "APPROVED · unknown", current: false },
    { result: "REJECTED", state: "current", scope: null, expectedScope: "无", expectedStatus: "已拒绝 · 当前有效", current: false },
    { result: "Pending", state: "not-reviewed", scope: null, expectedScope: "无", expectedStatus: "未审核", current: false },
  ];
  ticketRelationships.forEach((relationship, index) => {
    const projected = projectedRelationships[relationship.id];
    const fixture = relationshipReviews[index];
    projected.review = {
      result: fixture.result,
      state: fixture.state,
      approved_scope: fixture.scope ? approvalScope(projected, ...fixture.scope) : [],
      reasons: fixture.state === "current" ? [] : [`test-fixture:${fixture.state}`],
    };
  });

  card("quality-and-repair").click();
  card("quality-semantic-review-and-repair").dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
  await waitForLayout();

  assert(document.querySelector(".hero h1").textContent === "语义审核与受限修复", "main title did not use the clicked node name");
  const titleStyle = getComputedStyle(document.querySelector(".hero h1"));
  assert(parseFloat(titleStyle.fontSize) <= parseFloat(getComputedStyle(document.documentElement).fontSize) * 3.5, "internal View title is oversized");
  assert(document.querySelector("#view-status")?.textContent.includes("对象可信状态："), "View trust projection is not independently visible");
  assert(card("quality-semantic-review-and-repair").dataset.scopeNode === "true", "parent Scope identity is missing");
  assert(card("quality-semantic-review-and-repair").textContent.includes("当前范围 · 父节点"), "parent Scope label is missing");
  assert(card("semantic-review-contract-boundary") && card("repair-authorization-gate") && card("constrained-repair-call-boundary"), "Ticket 63 child nodes are missing");
  assert(breadcrumb().includes("整体架构") && breadcrumb().includes("质量判断与受限修复"), `complete breadcrumb is missing: ${breadcrumb()}`);
  assert(["semantic-review-contract-boundary", "repair-authorization-gate", "constrained-repair-call-boundary"].every((id) => card(id).textContent.includes("职责状态：未审核")), "a Ticket 63 child inherited parent trust");
  const relationships = document.querySelector("#candidate-collaboration-list");
  assert(relationships.querySelectorAll(".candidate-collaboration").length === 5, "Ticket 63 View did not expose exactly five relationships");
  assert(relationships.textContent.includes("分解审核：已批准 · 当前有效"), "Ticket 63 approved/current Decomposition is not explicit");
  assert(relationships.textContent.includes("分解审核：APPROVED · 需要复核"), "Ticket 63 needs-review Decomposition is not explicit");
  assert(relationships.textContent.includes("分解审核：APPROVED · unknown"), "Ticket 63 unknown Decomposition is not explicit");
  assert(relationships.textContent.includes("协作审核：已拒绝 · 当前有效"), "Ticket 63 rejected Collaboration is not explicit");
  assert(relationships.textContent.includes("协作状态：未审核"), "Ticket 63 pending Collaboration is not explicit");
  assert(relationships.textContent.includes("不证明实际运行顺序或运行可达"), "Candidate collaborations lost runtime uncertainty");
  assert(document.documentElement.scrollWidth <= window.innerWidth, "Ticket 63 internal View has horizontal overflow");
  assertGraphGeometry(ticketRelationships, "Ticket 63");

  const relationshipButtons = Array.from(relationships.querySelectorAll(".candidate-collaboration"));
  ticketRelationships.forEach((relationship, index) => {
    const button = relationshipButtons.find((candidate) => candidate.querySelector("h4")?.textContent === relationship.name);
    const fixture = relationshipReviews[index];
    assert(button, `Ticket 63 relationship button is missing: ${relationship.id}`);
    button.click();
    const detail = document.querySelector("#detail-content");
    assert(detail.dataset.collaborationId === relationship.id, `Ticket 63 opened the wrong Relationship detail: ${relationship.id}`);
    assert(detail.textContent.includes(fixture.expectedStatus), `Ticket 63 detail omitted ${fixture.expectedStatus}: ${relationship.id}`);
    assert(detail.textContent.includes(`批准范围：${fixture.expectedScope}`), `Ticket 63 detail omitted its projected approved_scope: ${relationship.id}`);
    assert(detail.textContent.includes(relationship.family === "candidate-decomposition" ? "关系类型：候选分解" : "关系类型：候选协作"), `Ticket 63 detail changed Relationship semantics: ${relationship.id}`);
    if (!fixture.current) assert(!detail.textContent.includes("当前已审核"), `Ticket 63 promoted non-current approval scope: ${relationship.id}`);
    document.querySelector("#relationship-detail").close();
  });

  const architecturePath = breadcrumb();
  card("semantic-review-contract-boundary").click();
  assert(document.querySelector("#relationship-detail").open, "candidate detail did not open");
  assert(document.querySelector("#detail-content").textContent.includes("具体 reviewer receiver dispatch 仍未知"), "reviewer receiver uncertainty is not visible");
  assert(document.querySelector("#detail-content").textContent.includes("Python 静态调用事实"), "review boundary Call Fact Evidence is not visible");
  document.querySelector(".dialog-close").click();
  assert(breadcrumb() === architecturePath, "candidate detail changed the View path");

  const repair = card("constrained-repair-call-boundary");
  repair.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 20, clientY: 20 }));
  document.querySelector("#context-evidence").click();
  assert(document.querySelector("#relationship-detail").open, "repair Evidence did not open");
  assert(document.querySelector("#detail-content").textContent.includes("具体 repair receiver dispatch 仍未知"), "repair receiver uncertainty is not visible");
  const browse = document.querySelector("#detail-content .browse-definitions");
  assert(browse, "Ticket 63 Definition Evidence did not expose Source Trail");
  browse.click();
  assert(document.querySelector("#detail-content").dataset.sourceTrailLevel === "file", "Source Trail did not open from the Ticket 63 internal View");
  assert(breadcrumb() === architecturePath, "Source Trail changed the View path");
  document.querySelector(".dialog-close").click();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert(document.activeElement?.dataset.moduleId === "constrained-repair-call-boundary", "Source Trail close did not restore focus");
  document.querySelector("#back-button").click();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert(card("quality-semantic-review-and-repair"), "BACK did not return exactly one View frame");
  assert(document.activeElement?.dataset.moduleId === "quality-semantic-review-and-repair", "BACK did not restore the origin Appearance focus");
  document.querySelector("#home-button").click();
  assert(card("quality-and-repair"), "HOME did not return to the system overview");
  card("quality-and-repair").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  assert(card("quality-semantic-review-and-repair"), "Enter did not invoke the top-level action");
  card("quality-semantic-review-and-repair").click();
  assert(card("repair-authorization-gate"), "pointer activation did not re-enter the Ticket 63 View");
  document.querySelector("#home-button").click();
  card("production-control").click();
  card("worker-launch-boundary").click();
  await waitForLayout();
  const workerIds = [
    "candidate-decomposition:worker-launch-boundary-to-supervisor-process-launch-construction",
    "candidate-decomposition:worker-launch-boundary-to-worker-production-entry-guard",
    "candidate-collaboration:supervisor-to-worker-wrapper",
    "candidate-collaboration:worker-wrapper-to-production-entry-guard",
  ];
  const workerRelations = workerIds.map((id) => {
    const group = document.querySelector(`[data-decomposition-id="${CSS.escape(id)}"], [data-collaboration-id="${CSS.escape(id)}"]`);
    const label = document.querySelector(`#relationship-label-layer [data-decomposition-id="${CSS.escape(id)}"], #relationship-label-layer [data-collaboration-id="${CSS.escape(id)}"]`);
    assert(group && label, `Worker regression relation missing: ${id}`);
    return { id, family: group.dataset.decompositionId ? "candidate-decomposition" : "candidate-collaboration", source: group.dataset.sourceNodeId, target: group.dataset.targetNodeId, name: label.textContent };
  });
  assertGraphGeometry(workerRelations, "Worker mixed View");
  document.documentElement.dataset.ticket63SemanticReview = "pass";
})().catch((error) => {
  document.documentElement.dataset.ticket63Error = error.message;
});
