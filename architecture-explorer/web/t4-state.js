(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ARCHITECTURE_EXPLORER_STATE = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const initialState = () => ({
    levelId: "top-level",
    navigationPath: ["top-level"],
    parentModuleId: null,
    detailModuleId: null,
    relationshipId: null,
    contextMenu: null,
    expandedNodeIds: [],
  });

  const navigationState = (state, navigationPath) => {
    const levelId = navigationPath[navigationPath.length - 1];
    return {
      ...state,
      levelId,
      navigationPath,
      parentModuleId: levelId === "top-level" ? null : levelId,
      detailModuleId: null,
      relationshipId: null,
      contextMenu: null,
      expandedNodeIds: [],
    };
  };

  const reduce = (state, action) => {
    if (action.type === "NODE_DETAIL_TOGGLE") {
      const expanded = state.expandedNodeIds.includes(action.moduleId)
        ? state.expandedNodeIds.filter((moduleId) => moduleId !== action.moduleId)
        : [...state.expandedNodeIds, action.moduleId];
      return { ...state, contextMenu: null, expandedNodeIds: expanded };
    }
    if (action.type === "CONTEXT_OPEN") {
      return { ...state, contextMenu: { moduleId: action.moduleId, x: action.x, y: action.y } };
    }
    if (action.type === "ESCAPE" || action.type === "OUTSIDE_POINTER") {
      if (!state.contextMenu) return state;
      return { ...state, contextMenu: null };
    }
    if (action.type === "MODULE_PRIMARY") {
      if (state.contextMenu) return { ...state, contextMenu: null };
      if (action.hasChildren) {
        return navigationState(state, [...state.navigationPath, action.moduleId]);
      }
      return { ...state, detailModuleId: action.moduleId, relationshipId: null };
    }
    if (action.type === "BACK") {
      if (state.navigationPath.length <= 1) return navigationState(state, ["top-level"]);
      return navigationState(state, state.navigationPath.slice(0, -1));
    }
    if (action.type === "HOME") return navigationState(state, ["top-level"]);
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
