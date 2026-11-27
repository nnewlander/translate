(async () => {
  const require = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const card = (id) => document.querySelector(`[data-module-id="${CSS.escape(id)}"]`);
  const visible = (element) => element?.getClientRects().length
    && getComputedStyle(element).visibility === "visible";
  try {
    card("document-understanding").click();
    card("document-translation-view").click();
    card("v4-t07-translation-segments").click();
    require(document.querySelector("h1").textContent === "投影片段与格式锚点", "Wrong source heading");
    const cases = [
      ["v4-t07-logical-work-order-projection", "形成逻辑工作单元", "逻辑工作单元选择", "物理内容如何划入冻结范围？"],
      ["v4-t07-calibration-request", "组装校准请求", "请求上下文与证据组装", "请求字段与脚注依据来自哪里？"],
    ];
    const missing = cases.filter(([id, , scope, question]) => {
      const hint = card(id).querySelector(".view-destination");
      return !visible(hint) || !hint.innerText.includes(scope) || !hint.innerText.includes(question);
    });
    require(!missing.length, `Missing visible destinations: ${missing.map(([, name]) => name).join(", ")}`);
    for (const [id, target, scope, question] of cases) {
      const origin = card(id);
      origin.focus();
      origin.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
      require(document.querySelector("h1").textContent === scope, `Wrong heading for ${target}`);
      const subtitle = document.querySelector("#view-subtitle");
      require(visible(subtitle) && subtitle.textContent === question, `Wrong subtitle for ${target}`);
      require(document.querySelector("#breadcrumb .current").textContent === question, "Wrong breadcrumb");
      document.querySelector("#back-button").click();
      await new Promise((resolve) => setTimeout(resolve, 30));
      require(document.activeElement === card(id), `Back did not restore focus for ${target}`);
    }

    const {root_view_id: root, view_index: views, node_index: nodes} =
      window.ARCHITECTURE_EXPLORER_DATA.view_projection;
    const currentCrumb = () => document.querySelector("#breadcrumb .current").textContent;
    const headingFor = (view) => view.id === root ? "顶层架构" : nodes[view.scope_node_id].name;
    const checkHeading = (view) => {
      const title = headingFor(view);
      const subtitle = document.querySelector("#view-subtitle");
      require(document.querySelector("h1").textContent === title, `Wrong center for ${view.id}`);
      if (view.id !== root && title !== view.display_name) {
        require(visible(subtitle) && subtitle.textContent === view.display_name, `Wrong View question for ${view.id}`);
      }
      require(currentCrumb() === view.display_name, `Wrong breadcrumb for ${view.id}`);
    };
    const actions = (view) => view.appearances.filter(({primary_action}) => primary_action?.kind === "open-view");
    const routes = new Map([[root, []]]);
    for (const [source, route] of routes) {
      for (const {node_id, primary_action: action} of actions(views[source])) {
        if (!routes.has(action.target_view_id)) {
          routes.set(action.target_view_id, [...route, [node_id, action.target_view_id]]);
        }
      }
    }
    require(routes.size === Object.keys(views).length, "A View is unreachable");
    const dialog = document.querySelector("#relationship-detail");
    const detail = document.querySelector("#detail-content");
    const openRoute = (route) => {
      document.querySelector("#home-button").click();
      for (const [nodeId, targetId] of route) {
        card(nodeId).click();
        if (dialog.open) detail.querySelector(".view-navigation-link").click();
        checkHeading(views[targetId]);
      }
    };
    const checkHint = (hint, target) => {
      require(visible(hint) && hint.textContent.includes(headingFor(target))
        && hint.textContent.includes(target.display_name), `Missing or inaccurate hint for ${target.id}`);
      require(hint.scrollWidth <= hint.clientWidth + 1, `Clipped hint for ${target.id}`);
    };
    let crossNode = 0;
    let viewActions = 0;
    let appearances = 0;
    for (const [source, route] of routes) {
      const view = views[source];
      openRoute(route);
      checkHeading(view);
      await new Promise((resolve) => setTimeout(resolve, 55));
      require(!document.querySelector(".graph-layout-error"), `Graph layout failed in ${source}`);
      require(document.documentElement.scrollWidth <= innerWidth, `Page overflow in ${source}`);
      for (const {node_id, primary_action: action} of view.appearances) {
        appearances++;
        const target = action?.kind === "open-view" && views[action.target_view_id];
        const cross = target && target.scope_node_id !== node_id;
        const detailsFirst = node_id === view.scope_node_id || target?.id === source
          || route.some(([, targetId]) => targetId === target?.id);
        const hint = card(node_id).querySelector(".view-destination");
        if (cross && !detailsFirst) checkHint(hint, target);
        else require(!visible(hint), `Misleading card hint in ${source} for ${node_id}`);
        if (!target) continue;
        viewActions++;
        if (cross) crossNode++;
        card(node_id).click();
        if (detailsFirst) {
          require(dialog.open, `Detail activation was bypassed in ${source} for ${node_id}`);
          checkHeading(view);
          const link = detail.querySelector(".view-navigation-link");
          if (target.id === source) {
            require(!link, `Current View offered as a destination in ${source}`);
            document.querySelector(".dialog-close").click();
            continue;
          }
          require(visible(link) && link.textContent === action.destination_label, `Missing explicit action in ${source}`);
          if (cross) checkHint(detail.querySelector(".view-destination"), target);
          link.click();
        }
        checkHeading(target);
        openRoute(route);
      }
    }
    document.documentElement.dataset.t11NavCoverage =
      `${routes.size}/${appearances}/${viewActions}/${crossNode}`;
    document.documentElement.dataset.t11NavigationTitles = "pass";
  } catch (error) {
    document.documentElement.dataset.t11NavigationTitlesError = error.message;
  }
})();
