/* npc_canvas.js —— NPC 对话画布视图（Drawflow 控制器）
 *
 * 与 editor.js 的边界（见 .trae/documents/npc_canvas_editor_plan.md）：
 *  - 数据源：编辑器全局 DATA.npcs[id] / DATA.layouts[id]（editor.js loadData 已拉取）
 *  - 结构改动（增删节点/改 next）→ 改内存 DATA.npcs[id].nodes，标记 dirty，由用户点表单
 *    「保存」走既有 saveNpc()（/api/editor/npc），本文件不直接提交游戏内容。
 *  - 坐标改动 → 防抖 POST /api/editor/npc-layout/<id>（独立通道，不触发 NPC 校验）。
 *  - 精细编辑（台词/条件/效果）仍在列表表单；画布卡片提供「在列表编辑」跳转。
 *  - 视图切换、挂载/卸载、Drawflow 事件、网格/框选/整组拖都在本文件。
 *
 * 依赖：window.Drawflow（vendored）、window.GraphModel（graph_model.js）、
 *       editor.js 的全局 DATA / $ / saveNpc / toast。
 */
(function () {
  "use strict";

  const G_COARSE = 28, G_FINE = 14;
  const GM = window.GraphModel;
  const esc = s => (s || "").replace(/[&<>]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  /* 视图状态 */
  const view = {
    mode: "list",           // list | canvas
    npcId: null,
    editor: null,           // Drawflow 实例（canvas 视图时存在）
    idMap: {},              // drawflow 数字 id -> 节点 id
    snap: true,
    grid: G_COARSE,
    dirty: false,
    rendering: false,       // 程序化渲染中：屏蔽连线事件回触发 render（防递归）
    multi: new Set(),
    group: null,
    dragBox: null,
    mounted: false,
    listeners: [],          // 卸载时要移除的 document/window 监听
    layoutTimer: null,
  };

  /* DOM 快捷 */
  const $ = id => document.getElementById(id);
  const wrap = () => $("npc-canvas-wrap");
  const box = () => $("npc-canvas");
  const marquee = () => $("npc-canvas-marquee");
  const curNpc = () => DATA.npcs[view.npcId];
  const curLayout = () => (DATA.layouts || {})[view.npcId] || {};

  const snap = v => view.snap ? Math.round(v / view.grid) * view.grid : Math.round(v);
  const liveData = () => view.editor.drawflow.drawflow.Home.data;
  const nodePos = dfId => { const d = liveData()[dfId]; return { x: d.pos_x, y: d.pos_y }; };
  const dfIdOf = id => +Object.keys(view.idMap).find(k => view.idMap[k] === id);

  /* ================= 视图切换 ================= */
  function setMode(mode) {
    view.mode = mode;
    document.body.classList.toggle("canvas-open", mode === "canvas");
    document.querySelectorAll(".nc-view-btn").forEach(b =>
      b.classList.toggle("active", b.dataset.view === mode));
    if (mode === "canvas") openCanvas();
    else closeCanvas();
  }

  function canUseCanvas() {
    // 新建未保存（DATA.npcs 里还没有该 id）时不能用画布
    return view.npcId && DATA.npcs[view.npcId];
  }

  function openCanvas() {
    if (!canUseCanvas()) {
      toast("请先在列表中填写 NPC 并保存一次，再使用画布", "warning");
      setMode("list");
      return;
    }
    $("npc-nodes").classList.add("hidden");
    $("npc-add-node").classList.add("hidden");
    wrap().classList.remove("hidden");
    mount();
  }

  function closeCanvas() {
    wrap().classList.add("hidden");
    $("npc-nodes") && $("npc-nodes").classList.remove("hidden");
    $("npc-add-node") && $("npc-add-node").classList.remove("hidden");
    unmount();
  }

  /* editor.js 在切换/选中 NPC 后调此入口，告知当前编辑的 npcId */
  window.NpcCanvas = {
    bind(id) { view.npcId = id; if (view.mode === "canvas" && view.mounted) render(); },
    onSaved(id) {
      // 保存成功（loadData 后）：清 dirty；若画布开着则用新数据重渲染
      view.npcId = id;
      view.dirty = false;
      updateDirty();
      if (view.mode === "canvas" && view.mounted) render();
    },
    setMode,
    // 切走页签 / 显示空态 / 打开游戏设置时退出全屏画布
    exit() { if (view.mode === "canvas") setMode("list"); },
    /**
     * saveNpc() 从列表表单 DOM 收集 payload；画布开着时 DOM 不含画布的结构改动。
     * 这里以画布内存为准合并：节点集合/next 用画布，台词/条件/效果用 DOM 收集值。
     */
    mergeForSave(payload) {
      if (view.mode !== "canvas" || !view.npcId) return payload;
      const live = DATA.npcs[view.npcId];
      if (!live || !live.nodes) return payload;
      const prev = {};
      Object.assign(prev, live.nodes);            // 画布新增节点的兜底内容
      Object.assign(prev, payload.nodes || {});   // 列表 DOM 里编辑过的文本/条件/效果覆盖
      const st = GM.structureFromNodes(live.nodes);
      payload.nodes = GM.applyGraphStructure(prev, st);
      return payload;
    },
  };

  /* ================= 挂载 / 卸载 ================= */
  function mount() {
    if (view.mounted) return;
    box().innerHTML = "";               // 清掉上一实例残留的节点/SVG（destroy 不清 DOM）
    view.editor = new Drawflow(box());
    view.editor.start();
    view.editor.force_first_input = true;
    view.editor.zoom_max = 1.6;
    view.editor.zoom_min = 0.4;
    view.mounted = true;
    bindEditorEvents();
    bindDomEvents();
    render();
  }

  function unmount() {
    if (!view.mounted) return;
    view.listeners.forEach(({ el, type, fn, opt }) => el.removeEventListener(type, fn, opt));
    view.listeners = [];
    try { view.editor.destroy(); } catch (_) {}
    box().innerHTML = "";               // 关键：移除所有 Drawflow 注入的 DOM
    view.editor = null;
    view.mounted = false;
    view.idMap = {};
    view.multi.clear();
    view.group = null;
  }

  /* ================= 渲染（GraphModel → Drawflow） ================= */
  function render() {
    const npc = curNpc();
    if (!npc || !view.mounted) return;
    const g = GM.npcToGraph(npc, curLayout());
    const reachable = GM.reachable(g);
    view.rendering = true;             // 批量建边期间事件不回触发 render
    view.editor.clear();
    view.idMap = {};
    g.nodes.forEach(n => {
      const html =
        `<div class="nc-node-head">${n.id}` +
        `<button type="button" class="nc-edit-here" data-edit="${n.id}">在列表编辑</button></div>` +
        `<div class="nc-node-text">${esc(n.text) || '<span style="opacity:.5">（空台词）</span>'}</div>` +
        n.choices.map((c, i) =>
          `<div class="nc-choice"><span class="t">${esc(c.text || "（未填文字）")}</span>` +
          (c.next ? `<span class="n">→ ${c.next}</span>` : "") + `</div>`).join("");
      const dfId = view.editor.addNode(n.id, 1, n.choices.length,
        snap(n.x), snap(n.y), "npc-node", {}, html);
      view.idMap[dfId] = n.id;
      placePorts(dfId, n.choices);
      if (!reachable.has(n.id)) {
        box().querySelector(`#node-${dfId}`)?.classList.add("unreachable");
      }
    });
    g.edges.forEach(e => {
      view.editor.addConnection(dfIdOf(e.from), dfIdOf(e.to),
        `output_${e.choiceIdx + 1}`, "input_1");
    });
    view.rendering = false;
    updateDirty();
  }

  function placePorts(dfId, choices) {
    const el = box().querySelector(`#node-${dfId}`);
    if (!el) return;
    [...el.querySelectorAll(".nc-choice")].forEach((row, i) => {
      el.style.setProperty(`--y${i + 1}`, row.offsetTop + row.offsetHeight / 2 + "px");
    });
  }

  /* ================= Drawflow 事件 → 内存数据 ================= */
  function bindEditorEvents() {
    const ed = view.editor;
    const choiceIdx = cls => parseInt(String(cls).replace("output_", ""), 10) - 1;

    ed.on("connectionCreated", d => {
      if (view.rendering) return;        // render() 自己建的边，忽略
      const from = view.idMap[d.output_id], to = view.idMap[d.input_id];
      const i = choiceIdx(d.output_class);
      const npc = curNpc();
      if (from && to && npc.nodes[from]?.choices[i]) {
        npc.nodes[from].choices[i].next = to;
        markDirty(); render();
      }
    });
    ed.on("connectionRemoved", d => {
      if (view.rendering) return;
      const from = view.idMap[d.output_id], i = choiceIdx(d.output_class);
      const npc = curNpc();
      if (from && npc.nodes[from]?.choices[i]) {
        delete npc.nodes[from].choices[i].next;
        markDirty(); render();
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
      markDirty(); render();
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
  }

  function markDirty() { view.dirty = true; updateDirty(); }
  function updateDirty() {
    const el = $("nc-canvas-dirty");
    if (el) el.classList.toggle("hidden", !view.dirty);
  }

  /* ================= 布局坐标：防抖独立保存 ================= */
  function scheduleLayoutSave() {
    clearTimeout(view.layoutTimer);
    view.layoutTimer = setTimeout(saveLayout, 800);
  }
  async function saveLayout() {
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

  /* ================= 鼠标交互（mousedown 捕获 + move/up） ================= */
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
    // 「在列表编辑」按钮：切回列表并定位节点（独立处理，不进框选/拖卡）
    const editBtn = e.target.closest?.("[data-edit]");
    if (editBtn) {
      e.stopPropagation(); e.preventDefault();
      jumpToList(editBtn.dataset.edit);
      return;
    }
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

  /* 跳回列表视图并高亮对应节点卡片（精细编辑走列表） */
  function jumpToList(nodeId) {
    setMode("list");
    // renderNpcForm 由 editor.js 在切视图时已渲染当前 NPC；这里只做定位
    requestAnimationFrame(() => {
      const card = [...document.querySelectorAll("#npc-nodes .npc-card")]
        .find(c => c.querySelector(".nnode-id")?.value === nodeId);
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("flash-card");
        setTimeout(() => card.classList.remove("flash-card"), 1400);
      }
    });
  }

  /* ================= DOM 监听（仅画布挂载期间存在） ================= */
  function bindDomEvents() {
    on(document, "mousedown", onDocMouseDown, true);
    on(window, "mousemove", onMouseMove);
    on(window, "mouseup", onMouseUp);
    on(window, "blur", () => {
      view.dragBox = null; view.group = null;
      marquee().style.display = "none"; box().classList.remove("grabbing");
    });

    $("nc-canvas-add").onclick = addNode;
    $("nc-canvas-exit").onclick = () => setMode("list");
    // 保存走 editor.js 唯一出口；mergeForSave 会把画布结构并进 DOM 收集结果
    $("nc-canvas-save").onclick = () => {
      if (typeof window.saveNpc === "function") window.saveNpc();
    };
    $("nc-canvas-zoomin").onclick = () => view.editor.zoom_in();
    $("nc-canvas-zoomout").onclick = () => view.editor.zoom_out();
    $("nc-canvas-reset").onclick = () => view.editor.zoom_reset();
    $("nc-canvas-snap").onchange = e => { view.snap = e.target.checked; };
    $("nc-canvas-grid").onchange = e => {
      view.grid = +e.target.value;
      box().style.setProperty("--nc-grid", view.grid + "px");
    };
  }
  function on(el, type, fn, opt) {
    el.addEventListener(type, fn, opt);
    view.listeners.push({ el, type, fn, opt });
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
    // 新节点坐标先给默认，立即落一次布局
    requestAnimationFrame(() => {
      const dfId = dfIdOf(id);
      if (dfId) {
        const d = liveData()[dfId];
        d.pos_x = x; d.pos_y = y;
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = x + "px"; el.style.top = y + "px"; }
        view.editor.updateConnectionNodes("node-" + dfId);
        saveLayout();
      }
    });
  }

  /* ================= 初始化：视图切换按钮 ================= */
  document.querySelectorAll(".nc-view-btn").forEach(btn => {
    btn.addEventListener("click", () => setMode(btn.dataset.view));
  });
  // 默认列表视图
  document.querySelector('.nc-view-btn[data-view="list"]')?.classList.add("active");
})();
