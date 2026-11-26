(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ARCHITECTURE_EXPLORER_STATE = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const initialState = () => ({
    levelId: "top-level",
    navigationPath: [{ viewId: "top-level", originNodeId: null }],
    parentModuleId: null,
    detailModuleId: null,
    relationshipId: null,
    contextMenu: null,
    expandedNodeIds: [],
    focusNodeId: null,
  });

  const navigationState = (state, navigationPath) => {
    const levelId = navigationPath[navigationPath.length - 1].viewId;
    return {
      ...state,
      levelId,
      navigationPath,
      parentModuleId: levelId === "top-level" ? null : levelId,
      detailModuleId: null,
      relationshipId: null,
      contextMenu: null,
      expandedNodeIds: [],
      focusNodeId: null,
    };
  };

  const appearancePrimaryAction = (state, primaryAction, isScopeNode) => {
    if (isScopeNode || (
      primaryAction?.kind === "open-view"
      && state.navigationPath.some(({ viewId }) => viewId === primaryAction.target_view_id)
    )) return null;
    return primaryAction;
  };

  const returnToFrame = (state, index) => ({
    ...navigationState(state, state.navigationPath.slice(0, index + 1)),
    focusNodeId: state.navigationPath[index + 1].originNodeId,
  });

  const enterView = (state, targetViewId, originNodeId) => navigationState(state, [
    ...state.navigationPath,
    { viewId: targetViewId, originNodeId },
  ]);

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
    if (action.type === "APPEARANCE_PRIMARY") {
      if (state.contextMenu) return { ...state, contextMenu: null };
      const primaryAction = appearancePrimaryAction(state, action.primaryAction, action.isScopeNode);
      if (primaryAction?.kind === "open-view") {
        return enterView(state, primaryAction.target_view_id, action.nodeId);
      }
      return { ...state, detailModuleId: action.nodeId, relationshipId: null };
    }
    if (action.type === "APPEARANCE_EXPLICIT_VIEW") {
      if (action.targetViewId === state.levelId) return state;
      const index = state.navigationPath.findIndex(({ viewId }) => viewId === action.targetViewId);
      return index < 0
        ? enterView(state, action.targetViewId, action.nodeId)
        : returnToFrame(state, index);
    }
    if (action.type === "BACK") {
      if (state.navigationPath.length <= 1) return navigationState(state, initialState().navigationPath);
      return returnToFrame(state, state.navigationPath.length - 2);
    }
    if (action.type === "HOME") return navigationState(state, initialState().navigationPath);
    if (action.type === "FOCUS_RESTORED") return { ...state, focusNodeId: null };
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

  return { appearancePrimaryAction, initialState, nextSnapshotOpen, reduce };
});
