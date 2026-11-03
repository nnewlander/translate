"use strict";

const assert = require("node:assert/strict");
const { initialState, nextSnapshotOpen, reduce } = require("../../architecture-explorer/web/t4-state.js");

const productionControl = { type: "MODULE_PRIMARY", moduleId: "production-control", hasChildren: true };
let state = reduce(initialState(), productionControl);
assert.equal(state.levelId, "production-control");
assert.equal(state.parentModuleId, "production-control");

state = reduce(state, { type: "BACK" });
assert.deepEqual(state, initialState());
state = reduce(reduce(initialState(), productionControl), { type: "HOME" });
assert.deepEqual(state, initialState());

state = reduce(initialState(), { type: "MODULE_PRIMARY", moduleId: "document-understanding", hasChildren: false });
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
assert.equal(state.levelId, "top-level", "outside click must not also drill down");
assert.equal(state.suppressPrimaryOnce, false);

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
