/* npc_canvas.js —— 画布工作台（顶栏「画布编辑」模式）
 *
 * 定位（见 .trae/documents/canvas_workbench_phase1_plan.md）：
 *  - 与列表编辑平级的工作台：列表 = 表单式精编；画布 = 大局拉线 + 右侧属性抽屉。
 *  - 数据源：全局 DATA.npcs[id]（editor.js loadData 拉取）；抽屉编辑直接改内存对象。
 *  - 保存：显式点「保存」/Ctrl+S，直接提交 DATA.npcs[id] 全字段走 /api/editor/npc
 *    （后端校验不绕过）；坐标仍走防抖 /api/editor/npc-layout/<id> 独立通道。
 *  - 每 NPC 的平移/缩放视图偏好存 localStorage（仅本机，非游戏数据）。
 *
 * 依赖：window.Drawflow（vendored）、window.GraphModel（graph_model.js）、
 *       editor.js 的全局 DATA 与条件/效果控件构造器（addChoiceRow/addEffectRow 等）。
 */
(function () {
  "use strict";

  const G_COARSE = 28, G_FINE = 14;
  const GM = window.GraphModel;
  const VIEW_LS_KEY = "tge_canvas_views_v1";
  const PANEL_LS_KEY = "tge_canvas_panel_open";

  const esc = s => (s || "").replace(/[&<>]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  const view = {
    mode: "list",           // list | canvas
    npcId: null,
    editor: null,           // Drawflow 实例（canvas 模式时存在）
    idMap: {},              // drawflow 数字 id -> 节点 id
    snap: true,
    grid: G_COARSE,
    dirty: false,
    rendering: false,       // 程序化渲染中：屏蔽连线事件回触发（防递归）
    multi: new Set(),
    group: null,
    dragBox: null,
    mounted: false,
    listeners: [],
    layoutTimer: null,
    renderTimer: null,
    selNode: null,          // 抽屉当前编辑的节点 id
  };

  const $ = id => document.getElementById(id);
  const box = () => $("cw-canvas");
  const marquee = () => $("cw-marquee");
  const inspector = () => $("nc-inspector");
  const curNpc = () => DATA.npcs[view.npcId];
  const curLayout = () => (DATA.layouts || {})[view.npcId] || {};

  const snap = v => view.snap ? Math.round(v / view.grid) * view.grid : Math.round(v);
  const liveData = () => view.editor.drawflow.drawflow.Home.data;
  const nodePos = dfId => { const d = liveData()[dfId]; return { x: d.pos_x, y: d.pos_y }; };
  const dfIdOf = id => +Object.keys(view.idMap).find(k => view.idMap[k] === id);

  /* ================= 模式切换（editor.js 顶栏开关调用） =================
   * enter(prefId)：从列表切过来时 prefId = 列表当前选中的 NPC（跟随选中）；
   * 没有则沿用上一次画布查看的 NPC，再不行取第一个。 */
  function enter(prefId) {
    if (view.mode === "canvas") return;
    view.mode = "canvas";
    document.body.classList.add("workbench-canvas");
    $("cw-workbench").classList.remove("hidden");
    if (prefId && DATA.npcs[prefId]) view.npcId = prefId;
    else if (!view.npcId || !DATA.npcs[view.npcId]) {
      view.npcId = Object.keys(DATA.npcs || {}).sort()[0] || null;
    }
    refreshNpcOptions();
    mount();
    render();
    applyView();
    syncStatus();
  }

  function exit() {
    if (view.mode === "list") return;
    view.mode = "list";
    document.body.classList.remove("workbench-canvas");
    $("cw-workbench").classList.add("hidden");
    unmount();
  }

  /* loadData 完成后由 editor.js 调用：保留模式/选中，刷新下拉与画布 */
  function refresh() {
    if (view.mode !== "canvas") return;
    if (view.npcId && !DATA.npcs[view.npcId]) {
      view.npcId = Object.keys(DATA.npcs || {}).sort()[0] || null;
      view.selNode = null;
    }
    refreshNpcOptions();
    if (view.mounted) { render(); applyView(); }
  }

  window.NpcCanvas = { enter, exit, refresh };

  /* ================= 挂载 / 卸载 ================= */
  function mount() {
    if (view.mounted || !view.npcId) return;
    box().innerHTML = "";               // 清掉上一实例残留（destroy 不清 DOM）
    view.editor = new Drawflow(box());
    view.editor.start();
    view.editor.force_first_input = true;
    view.editor.zoom_max = 1.6;
    view.editor.zoom_min = 0.4;
    view.mounted = true;
    fillRefDatalists();                 // 物品/敌人/地点候选（抽屉条件/效果用）
    bindEditorEvents();
    bindDomEvents();
    setPanel(localStorage.getItem(PANEL_LS_KEY) !== "0");
    $("cw-snap").checked = view.snap;
    $("cw-grid").value = String(view.grid);
    box().style.setProperty("--nc-grid", view.grid + "px");
  }

  function unmount() {
    if (!view.mounted) return;
    view.listeners.forEach(({ el, type, fn, opt }) => el.removeEventListener(type, fn, opt));
    view.listeners = [];
    clearTimeout(view.layoutTimer);
    clearTimeout(view.renderTimer);
    try { view.editor.destroy(); } catch (_) {}
    box().innerHTML = "";
    view.editor = null;
    view.mounted = false;
    view.idMap = {};
    view.multi.clear();
    view.group = null;
  }

  /* ================= NPC 下拉 ================= */
  function refreshNpcOptions() {
    const sel = $("cw-npc");
    const ids = Object.keys(DATA.npcs || {}).sort();
    sel.innerHTML = ids.map(id =>
      `<option value="${esc(id)}">${esc(DATA.npcs[id].name || id)}（${esc(id)}）</option>`
    ).join("");
    sel.value = view.npcId || "";
  }

  function switchNpc(id) {
    if (id === view.npcId) return;
    if (view.dirty && !confirm("当前对话有未保存的改动，切换 NPC 将丢弃它们。确定切换？")) {
      $("cw-npc").value = view.npcId || "";
      return;
    }
    saveViewNow();                      // 记住旧 NPC 的视图
    view.npcId = id;
    view.selNode = null;
    view.dirty = false;
    updateDirty();
    render();
    applyView();
    showPlaceholder();
    syncStatus();
  }

  /* ================= 渲染：GraphModel → Drawflow ================= */
  function render() {
    const npc = curNpc();
    if (!npc || !view.mounted) return;
    const g = GM.npcToGraph(npc, curLayout());
    const reach = GM.reachable(g);
    view.rendering = true;
    view.editor.clear();
    view.idMap = {};
    g.nodes.forEach(n => {
      const html =
        `<div class="nc-node-head">${esc(n.id)}</div>` +
        `<div class="nc-node-text">${esc(n.text) || '<span style="opacity:.5">（空台词）</span>'}</div>` +
        n.choices.map(c =>
          `<div class="nc-choice"><span class="t">${esc(c.text || "（未填文字）")}</span>` +
          (c.next ? `<span class="n">→ ${esc(c.next)}</span>` : "") + `</div>`).join("");
      const dfId = view.editor.addNode(n.id, 1, n.choices.length,
        snap(n.x), snap(n.y), "npc-node", {}, html);
      view.idMap[dfId] = n.id;
      placePorts(dfId, n.choices);
      if (!reach.has(n.id)) {
        box().querySelector(`#node-${dfId}`)?.classList.add("unreachable");
      }
    });
    g.edges.forEach(e => {
      view.editor.addConnection(dfIdOf(e.from), dfIdOf(e.to),
        `output_${e.choiceIdx + 1}`, "input_1");
    });
    view.rendering = false;
    fillNodeDatalist(npc);
    // 抽屉正在编辑的节点若仍存在，保持抽屉；否则收占位
    if (view.selNode && npc.nodes[view.selNode]) renderInspector();
    else showPlaceholder();
    syncStatus();
  }

  function fillNodeDatalist(npc) {
    $("npc-dl-nodes").innerHTML =
      Object.keys(npc.nodes || {}).map(id => `<option value="${esc(id)}"></option>`).join("");
  }

  function placePorts(dfId, choices) {
    const el = box().querySelector(`#node-${dfId}`);
    if (!el) return;
    [...el.querySelectorAll(".nc-choice")].forEach((row, i) => {
      el.style.setProperty(`--y${i + 1}`, row.offsetTop + row.offsetHeight / 2 + "px");
    });
  }

  /* 结构合并后重绘（抽屉增删选项等用 rAF 合并连续事件，焦点留在抽屉不丢） */
  function scheduleRender() {
    clearTimeout(view.renderTimer);
    view.renderTimer = setTimeout(render, 60);
  }

  /* ================= Drawflow 事件 → 内存数据 ================= */
  function bindEditorEvents() {
    const ed = view.editor;
    const choiceIdx = cls => parseInt(String(cls).replace("output_", ""), 10) - 1;

    ed.on("connectionCreated", d => {
      if (view.rendering) return;
      const from = view.idMap[d.output_id], to = view.idMap[d.input_id];
      const i = choiceIdx(d.output_class);
      const npc = curNpc();
      if (from && to && npc.nodes[from]?.choices[i]) {
        npc.nodes[from].choices[i].next = to;
        markDirty(); render();
        if (view.selNode === from) renderInspector();
      }
    });
    ed.on("connectionRemoved", d => {
      if (view.rendering) return;
      const from = view.idMap[d.output_id], i = choiceIdx(d.output_class);
      const npc = curNpc();
      if (from && npc.nodes[from]?.choices[i]) {
        delete npc.nodes[from].choices[i].next;
        markDirty(); render();
        if (view.selNode === from) renderInspector();
      }
    });
    ed.on("nodeRemoved", dfId => {
      if (view.rendering) return;
      const id = view.idMap[dfId];
      const npc = curNpc();
      if (!id || !npc) return;
      delete npc.nodes[id];
      Object.values(npc.nodes).forEach(n => n.choices.forEach(c => {
        if (c.next === id) delete c.next;
      }));
      if (view.selNode === id) { view.selNode = null; showPlaceholder(); }
      markDirty(); render();
    });
    ed.on("nodeSelected", dfId => {
      const id = view.idMap[dfId];
      if (!id) return;
      if (inspector().classList.contains("collapsed")) setPanel(true);
      selectNode(id);
    });
    // 单卡拖动结束：吸附 + 保存坐标
    ed.on("nodeMoved", dfId => {
      if (view.snap) {
        const d = liveData()[dfId];
        d.pos_x = snap(d.pos_x); d.pos_y = snap(d.pos_y);
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
        ed.updateConnectionNodes("node-" + dfId);
      }
      scheduleLayoutSave();
    });
    ed.on("zoom", updateZoomLabel);
    ed.on("translate", () => { updateZoomLabel(); saveViewSoon(); });
  }

  function markDirty() { view.dirty = true; updateDirty(); }
  function updateDirty() { $("cw-dirty").classList.toggle("hidden", !view.dirty); }

  /* ================= 节点属性抽屉 ================= */
  function selectNode(id) {
    if (view.selNode !== id) view.selNode = id;
    renderInspector();
  }

  function showPlaceholder() {
    $("nci-title").textContent = "节点属性";
    $("nci-body").innerHTML =
      '<div class="nci-empty">在画布上点选一张卡片进行编辑<br><br>拉线＝设置选项跳转<br>右侧蓝点拖到目标卡片即可</div>';
  }

  function renderInspector() {
    const npc = curNpc();
    const node = npc && view.selNode ? npc.nodes[view.selNode] : null;
    if (!node) { showPlaceholder(); return; }
    $("nci-title").textContent = "节点 " + view.selNode;

    const body = $("nci-body");
    body.innerHTML = "";

    // ID 只读（改名涉及全树 next 重映射，本期在列表表单做）
    const idRow = ce("div", "nci-node-id");
    idRow.innerHTML = `节点 ID：<b>${esc(view.selNode)}</b>（ID 改名请到列表编辑）`;

    // 台词
    const textLab = ce("label", "nci-sec");
    textLab.append("台词");
    const ta = ce("textarea");
    ta.rows = 5;
    ta.placeholder = "NPC 在这个节点说的话";
    ta.value = node.text || "";
    ta.addEventListener("input", () => {
      node.text = ta.value;
      const dfId = dfIdOf(view.selNode);
      const card = dfId ? box().querySelector(`#node-${dfId} .nc-node-text`) : null;
      if (card) card.textContent = ta.value || "（空台词）";
      markDirty();
    });

    // 选项
    const chLab = ce("div", "nci-sec");
    chLab.append("玩家选项（下一节点留空＝结束对话）");
    const choicesBox = ce("div");
    choicesBox.id = "nci-choices";
    choicesBox.style.cssText = "display:flex;flex-direction:column;gap:8px";
    (node.choices || []).forEach(ch => addChoiceRow(choicesBox, ch));

    const addChBtn = ce("button", "cw-tb");
    addChBtn.type = "button";
    addChBtn.textContent = "＋ 添加选项";
    addChBtn.addEventListener("click", () => {
      addChoiceRow(choicesBox, { text: "" });
      commitChoices(node, choicesBox);
      markDirty();
      scheduleRender();
    });

    // 删除节点
    const delBtn = ce("button", "cw-tb nci-del-node");
    delBtn.type = "button";
    delBtn.style.borderColor = "var(--danger)";
    delBtn.style.color = "var(--danger)";
    delBtn.textContent = "删除该节点";
    delBtn.addEventListener("click", () => {
      if (!confirm(`确认删除节点【${view.selNode}】？指向它的连线会一并断开（保存后生效）。`)) return;
      const dfId = dfIdOf(view.selNode);
      // 走 Drawflow 删除通道（触发 nodeRemoved 统一处理内存与重绘）
      if (dfId != null) view.editor.removeNodeId("node-" + dfId);
    });

    body.append(idRow, textLab, ta, chLab, choicesBox, addChBtn, delBtn);
  }

  /** 从抽屉 DOM 收集选项行，回写节点 choices（保持行序） */
  function commitChoices(node, choicesBox) {
    const choices = [];
    choicesBox.querySelectorAll(".npc-choice").forEach(row => {
      const choice = { text: row.querySelector(".nc-text").value };
      const next = row.querySelector(".nc-next").value.trim();
      if (next) choice.next = next;
      const t = row.querySelector(".nc-cond-type").value;
      const p = row.querySelector(".nc-cond-param").value.trim();
      if (t) choice.if = buildCond(t, p);
      const effects = [];
      row.querySelectorAll(".nc-effects .npc-effect-row").forEach(er => {
        const collected = collectEffectFromRow(er);
        if (collected) effects.push(collected);
      });
      if (effects.length) choice.effects = effects;
      choices.push(choice);
    });
    node.choices = choices;
  }

  /** 只更新某张卡片的文字摘要（不动端口/连线，不抢抽屉焦点） */
  function updateCardSummary(nodeId) {
    const npc = curNpc();
    const node = npc && npc.nodes[nodeId];
    const dfId = nodeId ? dfIdOf(nodeId) : null;
    const card = dfId != null ? box().querySelector(`#node-${dfId}`) : null;
    if (!node || !card) return;
    const textEl = card.querySelector(".nc-node-text");
    if (textEl) textEl.textContent = node.text || "（空台词）";
    card.querySelectorAll(".nc-choice").forEach((row, i) => {
      const c = node.choices[i] || {};
      row.querySelector(".t").textContent = c.text || "（未填文字）";
      const n = row.querySelector(".n");
      if (n) n.textContent = c.next ? "→ " + c.next : "";
    });
  }

  /* ================= 布局坐标：防抖独立保存 ================= */
  function scheduleLayoutSave() {
    clearTimeout(view.layoutTimer);
    view.layoutTimer = setTimeout(saveLayout, 800);
  }
  async function saveLayout() {
    if (!view.mounted || !view.npcId) return;
    const exp = view.editor.export().drawflow.Home.data;
    const layout = {};
    for (const [dfId, d] of Object.entries(exp)) {
      const id = view.idMap[dfId];
      if (id) layout[id] = { x: snap(d.pos_x), y: snap(d.pos_y) };
    }
    try {
      await fetch(`/api/editor/npc-layout/${encodeURIComponent(view.npcId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(layout),
      });
      if (DATA.layouts) DATA.layouts[view.npcId] = layout;
    } catch (_) { /* 坐标保存失败不阻断编辑，下次拖动会重试 */ }
  }

  /* ================= 画布保存（直接提交 DATA 实体全字段） ================= */
  async function save() {
    const npc = curNpc();
    if (!npc) { toast("请先在列表中新建并保存一个 NPC", "error"); return; }
    const res = await fetch("/api/editor/npc", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(npc),
    });
    const data = await res.json();
    if (!data.success) { toast(data.message, "error"); return; }
    await window.EditorActions.reloadData();   // 走 editor.js 统一重拉（保持画布模式）
    view.dirty = false;
    updateDirty();
    toast([data.message, ...(data.warnings || [])].join("\n"),
      (data.warnings || []).length ? "warning" : "success");
  }

  /* ================= 鼠标交互（捕获 mousedown + window move/up） ================= */
  function isBlank(t) {
    return t.classList && (t.classList.contains("drawflow") ||
                           t.classList.contains("parent-drawflow"));
  }
  function clearLibSelection() {
    box().querySelectorAll(".drawflow-node.selected")
      .forEach(el => el.classList.remove("selected"));
    view.editor.node_selected = null;
    if (view.editor.connection_selected) {
      view.editor.connection_selected.classList.remove("selected");
      try { view.editor.removeReouteConnectionSelected(); } catch (_) {}
      view.editor.connection_selected = null;
    }
  }

  function onDocMouseDown(e) {
    if (e.button !== 0) return;
    if (isBlank(e.target)) {
      e.stopPropagation();
      view.dragBox = { x0: e.clientX, y0: e.clientY };
      marquee().style.display = "block";
      Object.assign(marquee().style,
        { left: e.clientX + "px", top: e.clientY + "px", width: "0px", height: "0px" });
      return;
    }
    const nodeEl = e.target.closest?.(".drawflow-node");
    if (nodeEl && view.multi.has(+nodeEl.id.slice(5))) {
      e.stopPropagation();
      const members = new Map();
      view.multi.forEach(id => members.set(id, nodePos(id)));
      view.group = { members, lastX: e.clientX, lastY: e.clientY };
      box().classList.add("grabbing");
    }
  }

  function onMouseMove(e) {
    if (view.group) {
      const z = view.editor.zoom;
      const sx = (e.clientX - view.group.lastX) / z;
      const sy = (e.clientY - view.group.lastY) / z;
      view.group.lastX = e.clientX; view.group.lastY = e.clientY;
      view.group.members.forEach((p, dfId) => {
        const d = liveData()[dfId];
        d.pos_x = p.x + sx; d.pos_y = p.y + sy; p.x = d.pos_x; p.y = d.pos_y;
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
      });
      view.group.members.forEach((_p, dfId) =>
        view.editor.updateConnectionNodes("node-" + dfId));
      return;
    }
    if (view.dragBox) {
      Object.assign(marquee().style, {
        left: Math.min(e.clientX, view.dragBox.x0) + "px",
        top: Math.min(e.clientY, view.dragBox.y0) + "px",
        width: Math.abs(e.clientX - view.dragBox.x0) + "px",
        height: Math.abs(e.clientY - view.dragBox.y0) + "px",
      });
    }
    updateCoord(e.clientX, e.clientY);
  }

  function onMouseUp(e) {
    if (view.group) {
      if (view.snap) view.group.members.forEach((_p, dfId) => {
        const d = liveData()[dfId];
        d.pos_x = snap(d.pos_x); d.pos_y = snap(d.pos_y);
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
      });
      view.group.members.forEach((_p, dfId) =>
        view.editor.updateConnectionNodes("node-" + dfId));
      view.group = null;
      box().classList.remove("grabbing");
      saveLayout();
      return;
    }
    if (!view.dragBox) return;
    const x1 = Math.min(e.clientX, view.dragBox.x0), y1 = Math.min(e.clientY, view.dragBox.y0);
    const x2 = Math.max(e.clientX, view.dragBox.x0), y2 = Math.max(e.clientY, view.dragBox.y0);
    view.dragBox = null;
    marquee().style.display = "none";
    if (Math.max(x2 - x1, y2 - y1) < 3) { clearLibSelection(); clearMulti(); return; }
    const additive = e.ctrlKey || e.metaKey;
    if (!additive) clearMulti();
    box().querySelectorAll(".drawflow-node").forEach(el => {
      const r = el.getBoundingClientRect();
      const hit = r.left < x2 && r.right > x1 && r.top < y2 && r.bottom > y1;
      const id = +el.id.slice(5);
      if (hit) { view.multi.add(id); el.classList.add("multi-sel"); }
      else if (!additive) { view.multi.delete(id); el.classList.remove("multi-sel"); }
    });
  }

  function clearMulti() {
    view.multi.clear();
    box().querySelectorAll(".multi-sel").forEach(el => el.classList.remove("multi-sel"));
  }

  /* ================= 每 NPC 视图（平移/缩放）localStorage 持久化 ================= */
  function readViews() {
    try { return JSON.parse(localStorage.getItem(VIEW_LS_KEY) || "{}"); } catch (_) { return {}; }
  }
  function saveViewNow() {
    if (!view.mounted || !view.npcId) return;
    const all = readViews();
    all[view.npcId] = {
      x: view.editor.canvas_x, y: view.editor.canvas_y, zoom: view.editor.zoom,
    };
    localStorage.setItem(VIEW_LS_KEY, JSON.stringify(all));
  }
  let viewTimer = null;
  function saveViewSoon() {
    clearTimeout(viewTimer);
    viewTimer = setTimeout(saveViewNow, 400);
  }
  function applyView() {
    if (!view.mounted || !view.npcId) return;
    const v = readViews()[view.npcId];
    if (v && Number.isFinite(v.x)) {
      view.editor.canvas_x = v.x; view.editor.canvas_y = v.y;
      view.editor.zoom = v.zoom || 1;
      view.editor.precanvas.style.transform =
        `translate(${v.x}px, ${v.y}px) scale(${v.zoom || 1})`;
    } else {
      view.editor.zoom_reset();
    }
    updateZoomLabel();
  }

  /* ================= 底栏 / 抽屉 ================= */
  function updateZoomLabel() {
    if (!view.mounted) return;
    $("cw-st-zoom").textContent = Math.round(view.editor.zoom * 100) + "%";
  }
  function updateCoord(cx, cy) {
    if (!view.mounted) return;
    const r = box().getBoundingClientRect();
    const wx = (cx - r.left - view.editor.canvas_x) / view.editor.zoom;
    const wy = (cy - r.top - view.editor.canvas_y) / view.editor.zoom;
    $("cw-st-xy").textContent = `${Math.round(wx / view.grid)}, ${Math.round(wy / view.grid)}`;
  }
  function syncStatus() {
    $("cw-st-npc").textContent = view.npcId
      ? ((curNpc()?.name || view.npcId) + "（" + view.npcId + "）") : "—";
    $("cw-st-nodes").textContent = view.npcId ? Object.keys(curNpc()?.nodes || {}).length : 0;
    $("cw-npc").value = view.npcId || "";
    updateZoomLabel();
  }

  function setPanel(open) {
    inspector().classList.toggle("collapsed", !open);
    $("cw-panel-toggle").classList.toggle("active", !open);
    try { localStorage.setItem(PANEL_LS_KEY, open ? "1" : "0"); } catch (_) {}
  }

  function addNode() {
    const npc = curNpc();
    if (!npc) return;
    let i = Object.keys(npc.nodes).length + 1;
    let id = "node_" + i;
    while (npc.nodes[id]) { i++; id = "node_" + i; }
    const z = view.editor.zoom;
    const x = snap((-view.editor.canvas_x) / z + 56);
    const y = snap((-view.editor.canvas_y) / z + 84);
    npc.nodes[id] = { text: "新节点", choices: [] };
    markDirty();
    render();
    requestAnimationFrame(() => {
      const dfId = dfIdOf(id);
      if (dfId) {
        const d = liveData()[dfId];
        d.pos_x = x; d.pos_y = y;
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = x + "px"; el.style.top = y + "px"; }
        view.editor.updateConnectionNodes("node-" + dfId);
        saveLayout();
        view.selNode = id;
        renderInspector();
      }
    });
  }

  function on(el, type, fn, opt) {
    el.addEventListener(type, fn, opt);
    view.listeners.push({ el, type, fn, opt });
  }

  function bindDomEvents() {
    on(document, "mousedown", onDocMouseDown, true);
    on(window, "mousemove", onMouseMove);
    on(window, "mouseup", onMouseUp);
    on(window, "blur", () => {
      view.dragBox = null; view.group = null;
      marquee().style.display = "none"; box().classList.remove("grabbing");
    });
    on(window, "keydown", e => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault(); save();
      }
      if ((e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B")) {
        e.preventDefault();
        setPanel(inspector().classList.contains("collapsed"));
      }
    });

    $("cw-npc").onchange = e => switchNpc(e.target.value);
    $("cw-add").onclick = addNode;
    $("cw-save").onclick = save;
    $("cw-zin").onclick = () => view.editor.zoom_in();
    $("cw-zout").onclick = () => view.editor.zoom_out();
    $("cw-zreset").onclick = () => view.editor.zoom_reset();
    $("cw-panel-toggle").onclick = () =>
      setPanel(inspector().classList.contains("collapsed"));
    // 回列表编辑该 NPC 的基本信息/问候规则
    $("nci-edit-npc").onclick = () => {
      if (view.npcId) window.EditorActions?.editNpc(view.npcId);
    };
    $("cw-snap").onchange = e => { view.snap = e.target.checked; };
    $("cw-grid").onchange = e => {
      view.grid = +e.target.value;
      box().style.setProperty("--nc-grid", view.grid + "px");
      render();
    };

    /* 抽屉编辑委托（挂载期只绑一次，避免重建控件时重复累积监听） */
    const nci = $("nci-body");
    on(nci, "input", onInspectorEdit);
    on(nci, "change", onInspectorEdit);
    // 选项/效果行内的删除或新增按钮（addChoiceRow/addEffectRow 自带处理），
    // 捕获阶段记录，等行 DOM 变动后统一回写 + 必要时重绘端口
    on(nci, "click", e => {
      if (!e.target.closest("button")) return;
      setTimeout(() => {
        const node = currentInspectorNode();
        const boxEl = $("nci-choices");
        if (!node || !boxEl) return;
        commitChoices(node, boxEl);
        markDirty();
        scheduleRender();
      }, 0);
    }, true);
  }

  function currentInspectorNode() {
    const npc = curNpc();
    return npc && view.selNode ? npc.nodes[view.selNode] : null;
  }

  function onInspectorEdit(e) {
    const node = currentInspectorNode();
    const boxEl = $("nci-choices");
    if (!node || !boxEl) return;
    commitChoices(node, boxEl);
    markDirty();
    // 下一节点改动影响连线（重绘）；其余只刷新卡片摘要，不抢输入焦点
    if (e.target.closest && e.target.closest(".nc-next")) scheduleRender();
    else updateCardSummary(view.selNode);
  }
})();
