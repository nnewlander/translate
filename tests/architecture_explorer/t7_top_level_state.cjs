"use strict";

const assert = require("node:assert/strict");
const { initialState, reduce } = require("../../architecture-explorer/web/t4-state.js");

const toggle = (state, moduleId) => reduce(state, { type: "NODE_DETAIL_TOGGLE", moduleId });
const productionControl = { type: "MODULE_PRIMARY", moduleId: "production-control", hasChildren: true };

let state = initialState();
assert.deepEqual(state.expandedNodeIds, []);

state = toggle(state, "production-control");
state = toggle(state, "translation-execution");
assert.deepEqual(state.expandedNodeIds, ["production-control", "translation-execution"]);

state = toggle(state, "production-control");
assert.deepEqual(state.expandedNodeIds, ["translation-execution"], "toggling one card must not collapse another");

state = reduce(state, { type: "CONTEXT_OPEN", moduleId: "translation-execution", x: 12, y: 18 });
state = reduce(state, { type: "MENU_EVIDENCE", relationshipId: "relationship:production-control:launches:translation-execution" });
assert.deepEqual(state.expandedNodeIds, ["translation-execution"], "evidence detail must preserve inline expansion");

state = reduce(toggle(initialState(), "production-control"), productionControl);
assert.deepEqual(state.expandedNodeIds, [], "drilldown must clear inline expansion");

state = reduce(toggle(initialState(), "production-control"), { type: "BACK" });
assert.deepEqual(state.expandedNodeIds, [], "BACK must clear inline expansion");

state = reduce(toggle(initialState(), "production-control"), { type: "HOME" });
assert.deepEqual(state.expandedNodeIds, [], "HOME must clear inline expansion");

console.log("T7 top-level reducer: all assertions passed");
