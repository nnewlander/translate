"use strict";

const assert = require("node:assert/strict");
const { initialState, nextSnapshotOpen, reduce } = require("../../architecture-explorer/web/t4-state.js");

const productionControl = {
  type: "APPEARANCE_PRIMARY",
  nodeId: "production-control",
  primaryAction: { kind: "open-view", target_view_id: "production-control" },
};
let state = reduce(initialState(), productionControl);
assert.equal(state.levelId, "production-control");
assert.equal(state.parentModuleId, "production-control");
assert.deepEqual(state.navigationPath, [
  { viewId: "top-level", originNodeId: null },
  { viewId: "production-control", originNodeId: "production-control" },
]);

state = reduce(state, { type: "BACK" });
assert.equal(state.levelId, "top-level");
assert.equal(state.focusNodeId, "production-control");
state = reduce(reduce(initialState(), productionControl), { type: "HOME" });
assert.deepEqual(state, initialState());

state = reduce(initialState(), { type: "APPEARANCE_PRIMARY", nodeId: "document-understanding" });
assert.equal(state.levelId, "top-level");
assert.equal(state.detailModuleId, "document-understanding");

state = reduce(initialState(), { type: "CONTEXT_OPEN", moduleId: "production-control", x: 12, y: 18 });
assert.equal(state.contextMenu.moduleId, "production-control");
state = reduce(state, { type: "ESCAPE" });
assert.equal(state.contextMenu, null);
assert.equal(state.levelId, "top-level");

state = reduce(initialState(), { type: "CONTEXT_OPEN", moduleId: "production-control", x: 12, y: 18 });
state = reduce(state, { type: "OUTSIDE_POINTER" });
assert.equal(state.contextMenu, null);
state = reduce(state, productionControl);
assert.equal(state.levelId, "production-control", "the next normal click after menu dismissal must work immediately");

state = reduce(initialState(), { type: "CONTEXT_OPEN", moduleId: "production-control", x: 12, y: 18 });
state = reduce(state, { type: "ESCAPE" });
state = reduce(state, productionControl);
assert.equal(state.levelId, "production-control", "the next click after Escape must work immediately");

const simulatedThirdLevel = {
  type: "APPEARANCE_PRIMARY",
  nodeId: "worker-launch-boundary",
  primaryAction: { kind: "open-view", target_view_id: "simulated-nested-level" },
};
state = reduce(reduce(initialState(), productionControl), simulatedThirdLevel);
assert.deepEqual(state.navigationPath.map((frame) => frame.viewId), ["top-level", "production-control", "simulated-nested-level"]);
const backState = reduce(state, { type: "BACK" });
assert.deepEqual(backState.navigationPath.map((frame) => frame.viewId), ["top-level", "production-control"], "BACK removes exactly one path segment");
assert.equal(backState.focusNodeId, "worker-launch-boundary", "BACK restores the Appearance that opened the departed View");
const homeState = reduce(state, { type: "HOME" });
assert.deepEqual(homeState.navigationPath.map((frame) => frame.viewId), ["top-level"], "HOME returns directly to the root");

const returned = reduce(state, {
  type: "APPEARANCE_PRIMARY",
  nodeId: "return-to-root",
  primaryAction: { kind: "open-view", target_view_id: "top-level" },
});
assert.deepEqual(returned.navigationPath.map((frame) => frame.viewId), ["top-level"], "a legal return transition truncates to the existing View frame");

const relationshipId = "relationship:production-control:launches:translation-execution";
state = reduce(initialState(), { type: "CONTEXT_OPEN", moduleId: "production-control", x: 12, y: 18 });
state = reduce(state, { type: "MENU_EVIDENCE", relationshipId });
assert.equal(state.contextMenu, null);
assert.equal(state.relationshipId, relationshipId);
const fromLine = reduce(initialState(), { type: "RELATIONSHIP_PRIMARY", relationshipId });
assert.equal(fromLine.relationshipId, state.relationshipId, "menu and line must select the same relationship id");

assert.equal(nextSnapshotOpen(false, { isMouseActivation: true, hasSelectedText: false }), true);
assert.equal(nextSnapshotOpen(true, { isMouseActivation: true, hasSelectedText: false }), false);
assert.equal(
  nextSnapshotOpen(true, { isMouseActivation: true, hasSelectedText: true }),
  true,
  "selecting snapshot text must not collapse the card"
);
assert.equal(
  nextSnapshotOpen(false, { isMouseActivation: false, hasSelectedText: true }),
  true,
  "keyboard activation must remain available even if text was previously selected"
);

console.log("T4 interaction reducer: all assertions passed");
