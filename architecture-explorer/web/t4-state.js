(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ARCHITECTURE_EXPLORER_STATE = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const initialState = () => ({
    levelId: "top-level",
    parentModuleId: null,
    detailModuleId: null,
    relationshipId: null,
    contextMenu: null,
    suppressPrimaryOnce: false,
  });

  const reduce = (state, action) => {
    if (action.type === "CONTEXT_OPEN") {
      return { ...state, contextMenu: { moduleId: action.moduleId, x: action.x, y: action.y }, suppressPrimaryOnce: false };
    }
    if (action.type === "ESCAPE" || action.type === "OUTSIDE_POINTER") {
      if (!state.contextMenu) return state;
      return { ...state, contextMenu: null, suppressPrimaryOnce: action.type === "OUTSIDE_POINTER" };
    }
    if (action.type === "MODULE_PRIMARY") {
      if (state.contextMenu) return { ...state, contextMenu: null };
      if (state.suppressPrimaryOnce) return { ...state, suppressPrimaryOnce: false };
      if (action.hasChildren) {
        return {
          ...state,
          levelId: action.moduleId,
          parentModuleId: action.moduleId,
          detailModuleId: null,
          relationshipId: null,
        };
      }
      return { ...state, detailModuleId: action.moduleId, relationshipId: null };
    }
    if (action.type === "BACK" || action.type === "HOME") return initialState();
    if (action.type === "MENU_EVIDENCE") {
      return { ...state, contextMenu: null, relationshipId: action.relationshipId || null };
    }
    if (action.type === "RELATIONSHIP_PRIMARY") {
      return { ...state, contextMenu: null, relationshipId: action.relationshipId };
    }
    if (action.type === "DETAIL_CLOSED") {
      return { ...state, detailModuleId: null, relationshipId: null };
    }
    return state;
  };

  const nextSnapshotOpen = (open, { isMouseActivation, hasSelectedText }) => {
    if (isMouseActivation && hasSelectedText) return open;
    return !open;
  };

  return { initialState, nextSnapshotOpen, reduce };
});
