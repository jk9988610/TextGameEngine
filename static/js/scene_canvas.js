/* scene_canvas.js —— 世界地图画布工作台（顶栏「画布编辑」+ 地点页签/默认）
 *
 * 定位（沿用 NPC 对话画布三原则）：
 *  - 画布只管结构：节点 = 地点卡片，连线 = 出口关系；内容（描述/物品/商店/敌人）
 *    仍由列表表单编辑，卡片上只放摘要；
 *  - 坐标是独立通道：scene_layouts.json（引擎不读），拖动防抖保存；
 *  - 出口/锁定改动直接改内存 DATA.scenes[id]，显式「保存」批量 POST /api/editor/scene
 *    （后端 upsert_scene 校验兜底，不绕过）。
 *
 * 交互：
 *  - 拖卡片摆位；从右侧端口拉线到另一张卡片 = 新建出口；
 *  - 点连线 = 出口检查器（勾选 A→B / B→A / 各自锁定）；点卡片 = 地点摘要抽屉；
 *  - 右键拖动平移，滚轮缩放；Ctrl+S 保存。
 *
 * 依赖：window.Drawflow（vendored）、editor.js 的全局 DATA / toast / EditorActions。
 */
(function () {
  "use strict";

  const G_COARSE = 28, G_FINE = 14;
  const CLICK_THRESHOLD = 4;
  const ID_PATTERN = /^[a-z0-9_]{1,32}$/;
  const VIEW_LS_KEY = "tge_scene_canvas_view_v1";
  const PANEL_LS_KEY = "tge_canvas_panel_open";
  const SETTINGS_KEY = "tge_editor_settings_v1";

  const esc = s => (s || "").replace(/[&<>]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const ce = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  /* 编辑器本机偏好（与 npc_canvas 同一份设置：自动保存开关） */
  function pref(key, dft) {
    try {
      const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      return s[key] !== undefined ? s[key] : dft;
    } catch (_) { return dft; }
  }
  const autosaveOn = () => pref("autosave", false);

  const view = {
    mode: "list",           // list | canvas
    editor: null,           // Drawflow 实例
    idMap: {},              // drawflow 数字 id -> 场景 id
    snap: true,
    grid: G_COARSE,
    dirty: new Set(),       // 内容有改动的场景 id（出口/锁定）
    layoutDirty: new Set(), // 坐标待落盘的场景 id
    rendering: false,
    panning: null,
    pending: null,          // 左键按下候选点击 {dfId,x,y,moved}，mouseup 判定点击/拖卡
    selScene: null,         // 抽屉当前显示的地点
    selEdge: null,          // 抽屉当前显示的出口对 [a, b]
    mounted: false,
    listeners: [],
    layoutTimer: null,
    saveTimer: null,
    viewTimer: null,
  };

  const $ = id => document.getElementById(id);
  const box = () => $("cw-canvas");
  const inspector = () => $("nc-inspector");
  const sceneIds = () => Object.keys(DATA.scenes || {}).sort();
  const posMap = () => DATA.scene_layouts || {};

  const snap = v => view.snap ? Math.round(v / view.grid) * view.grid : Math.round(v);
  const liveData = () => view.editor.drawflow.drawflow.Home.data;
  const dfIdOf = id => {
    const k = Object.keys(view.idMap).find(k => view.idMap[k] === id);
    return k === undefined ? null : +k;
  };
  const pairKey = (a, b) => (a < b ? a + "|" + b : b + "|" + a);

  /* ================= 模式切换（editor.js 顶栏开关调用） ================= */
  function enter() {
    if (view.mode === "canvas") return;
    view.mode = "canvas";
    document.body.classList.add("workbench-canvas", "workbench-scenes");
    $("cw-workbench").classList.remove("hidden");
    $("cw-mode-label").textContent = "世界地图";
    $("cw-st-npc-label").textContent = "地点";
    $("cw-st-nodes-label").textContent = "未保存";
    $("cw-add").textContent = "＋ 地点";
    $("nci-edit-npc").classList.add("hidden");
    mount();
    render();
    applyView();
    syncStatus();
    showPlaceholder();
  }

  function exit() {
    if (view.mode !== "canvas") return;
    if (view.dirty.size && autosaveOn()) save({ silent: true });
    clearTimeout(view.layoutTimer);
    flushLayout();
    view.mode = "list";
    document.body.classList.remove("workbench-canvas", "workbench-scenes");
    $("cw-workbench").classList.add("hidden");
    $("nci-edit-npc").classList.remove("hidden");
    unmount();
  }

  /* loadData 完成后由 editor.js 调用：保留模式/视图，重绘画布 */
  function refresh() {
    if (view.mode !== "canvas") return;
    if (view.selScene && !DATA.scenes[view.selScene]) showPlaceholder();
    if (view.selEdge && (!DATA.scenes[view.selEdge[0]] || !DATA.scenes[view.selEdge[1]])) {
      showPlaceholder();
    }
    if (view.mounted) { render(); applyView(); }
  }

  window.SceneCanvas = { enter, exit, refresh };

  /* ================= 挂载 / 卸载 ================= */
  function mount() {
    if (view.mounted) return;
    box().innerHTML = "";               // 清掉上一实例残留（destroy 不清 DOM）
    view.editor = new Drawflow(box());
    view.editor.start();
    view.editor.force_first_input = true;
    view.editor.zoom_max = 1.6;
    view.editor.zoom_min = 0.3;
    view.mounted = true;
    injectMarkerDefs();
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
    clearTimeout(view.saveTimer);
    clearTimeout(view.viewTimer);
    try { view.editor.destroy(); } catch (_) {}
    box().innerHTML = "";
    view.editor = null;
    view.mounted = false;
    view.idMap = {};
    view.dirty.clear();
    view.layoutDirty.clear();
    view.panning = null;
    view.pending = null;
    box().classList.remove("panning");
  }

  /** 单向出口的箭头 marker（挂在画布根下，供 CSS marker-end 引用） */
  function injectMarkerDefs() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.style.cssText = "position:absolute;width:0;height:0";
    svg.innerHTML =
      '<defs><marker id="sm-arrow" viewBox="0 0 10 10" refX="9" refY="5"' +
      ' markerWidth="8" markerHeight="8" orient="auto-start-reverse" markerUnits="userSpaceOnUse">' +
      '<path d="M0,0 L10,5 L0,10 z" fill="#8a93a6"></path></marker></defs>';
    box().appendChild(svg);
  }

  /* ================= 渲染：DATA.scenes → Drawflow ================= */
  function render() {
    if (!view.mounted) return;
    const layout = posMap();
    view.rendering = true;
    view.editor.clear();
    view.idMap = {};
    // 节点：地点卡片（无坐标的按 4 列自动排布）
    let auto = 0;
    for (const id of sceneIds()) {
      const s = DATA.scenes[id] || {};
      let x = layout[id] && Number.isFinite(layout[id].x) ? layout[id].x : null;
      let y = layout[id] && Number.isFinite(layout[id].y) ? layout[id].y : null;
      if (x == null) { x = 60 + (auto % 4) * 300; y = 60 + Math.floor(auto / 4) * 210; auto++; }
      const born = id === DATA.initial_scene ? '<span class="sm-born">★ 出生点</span>' : "";
      const chips = [];
      chips.push(`出口 ${(s.exits || []).length}`);
      if ((s.items_here || []).length) chips.push(`物 ${s.items_here.length}`);
      if ((s.shop_items || []).length) chips.push(`店 ${s.shop_items.length}`);
      if ((s.enemies_here || []).length) chips.push(`敌 ${s.enemies_here.length}`);
      const npcN = Object.values(DATA.npcs || {}).filter(n => n.scene_id === id).length;
      if (npcN) chips.push(`话 ${npcN}`);
      const html =
        `<div class="sm-head">${esc(s.name || id)}${born}</div>` +
        `<div class="sm-id">${esc(id)}</div>` +
        `<div class="sm-meta">${chips.map(c => `<span>${esc(c)}</span>`).join("")}</div>`;
      const dfId = view.editor.addNode(id, 1, 1, snap(x), snap(y), "sm-node", {}, html);
      view.idMap[dfId] = id;
    }
    // 连线：每个无向地点对最多一条（方向/锁定用样式表达）
    const done = new Set();
    for (const a of sceneIds()) {
      for (const b of (DATA.scenes[a].exits || [])) {
        if (b === a || !DATA.scenes[b] || done.has(pairKey(a, b))) continue;
        done.add(pairKey(a, b));
        const fa = dfIdOf(a), fb = dfIdOf(b);
        if (fa == null || fb == null) continue;
        const mutual = (DATA.scenes[b].exits || []).includes(a);
        const locked = !!(DATA.scenes[a].locked_exits || {})[b] ||
                       !!(mutual && (DATA.scenes[b].locked_exits || {})[a]);
        view.editor.addConnection(fa, fb, "output_1", "input_1");
        const conns = box().querySelectorAll(".connection");
        const el = conns[conns.length - 1];
        if (el) {
          el.classList.add("sm-edge");
          if (!mutual) el.classList.add("sm-one");
          if (locked) el.classList.add("sm-locked");
          el.dataset.from = a;
          el.dataset.to = b;
        }
      }
    }
    view.rendering = false;
    syncStatus();
  }

  /* ================= Drawflow 事件 → 内存数据 ================= */
  function bindEditorEvents() {
    const ed = view.editor;
    // 用户拉线：from.exits 增加 to（重复/自环直接重绘还原）
    ed.on("connectionCreated", d => {
      if (view.rendering) return;
      const from = view.idMap[d.output_id], to = view.idMap[d.input_id];
      const s = from && to ? DATA.scenes[from] : null;
      if (!s || from === to) { render(); return; }
      if (!(s.exits || []).includes(to)) {
        s.exits = [...(s.exits || []), to];
        markDirty(from);
      }
      render();
    });
    // 数据为真：库内移除连线（复制/拖拽副作用）一律重绘还原，删除走检查器
    ed.on("connectionRemoved", () => { if (!view.rendering) render(); });
    // 地点删除必须走后端 API（有引用/占用保护），库内删卡一律重绘还原
    ed.on("nodeRemoved", () => { if (!view.rendering) render(); });
    // 拖完卡片：吸附 + 记录待落盘坐标
    ed.on("nodeMoved", dfId => {
      const id = view.idMap[dfId];
      if (!id) return;
      if (view.snap) {
        const d = liveData()[dfId];
        d.pos_x = snap(d.pos_x); d.pos_y = snap(d.pos_y);
        const el = box().querySelector("#node-" + dfId);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
        view.editor.updateConnectionNodes("node-" + dfId);
      }
      view.layoutDirty.add(id);
      scheduleLayoutFlush();
    });
    ed.on("zoom", updateZoomLabel);
    ed.on("translate", () => { updateZoomLabel(); saveViewSoon(); });
  }

  function markDirty(id) {
    view.dirty.add(id);
    updateDirty();
    if (autosaveOn()) {
      clearTimeout(view.saveTimer);
      view.saveTimer = setTimeout(() => save({ silent: true }), 1500);
    }
  }
  function updateDirty() {
    $("cw-dirty").classList.toggle("hidden", !view.dirty.size);
    $("cw-st-nodes").textContent = String(view.dirty.size);
  }

  /* ================= 出口/锁定的数据变更 ================= */
  function setExit(from, to, on) {
    const s = DATA.scenes[from];
    if (!s || from === to) return;
    s.exits = s.exits || [];
    const i = s.exits.indexOf(to);
    if (on && i < 0) { s.exits.push(to); markDirty(from); }
    if (!on && i >= 0) {
      s.exits.splice(i, 1);
      if (s.locked_exits) delete s.locked_exits[to];
      markDirty(from);
    }
  }
  function setLock(from, to, on) {
    const s = DATA.scenes[from];
    if (!s) return;
    if (on) {
      s.locked_exits = s.locked_exits || {};
      if (!s.locked_exits[to]) { s.locked_exits[to] = true; markDirty(from); }
    } else if (s.locked_exits && s.locked_exits[to] !== undefined) {
      delete s.locked_exits[to];
      markDirty(from);
    }
  }

  /* ================= 属性抽屉 ================= */
  function setPanel(open) {
    inspector().classList.toggle("collapsed", !open);
    $("cw-panel-toggle").classList.toggle("active", !open);
    try { localStorage.setItem(PANEL_LS_KEY, open ? "1" : "0"); } catch (_) {}
  }

  function showPlaceholder() {
    view.selScene = null;
    view.selEdge = null;
    $("nci-title").textContent = "世界地图";
    $("nci-body").innerHTML =
      '<div class="nci-empty">点选地点卡片查看摘要，点连线编辑出口。<br><br>' +
      "从卡片右侧端口拉线到另一张卡片＝新建出口<br>拖动卡片摆位（坐标自动保存）<br>" +
      "右键拖动＝平移，滚轮＝缩放<br>「＋ 地点」直接在地图上新建</div>";
  }

  function showSceneInspector(id) {
    const s = DATA.scenes[id];
    if (!s) { showPlaceholder(); return; }
    view.selScene = id;
    view.selEdge = null;
    $("nci-title").textContent = "地点 " + (s.name || id);
    const body = $("nci-body");
    body.innerHTML = "";

    const row = (k, v) => {
      const r = ce("div", "sm-row");
      r.append(ce("span", "k", k), ce("span", "v", v));
      return r;
    };
    body.append(row("ID", id));
    if (id === DATA.initial_scene) body.append(row("角色", "★ 出生点"));

    body.append(ce("div", "nci-sec", `出口（${(s.exits || []).length}）`));
    if (!(s.exits || []).length) {
      body.append(ce("div", "nci-empty", "（暂无出口：从卡片右侧端口拉线添加）"));
    }
    for (const b of s.exits || []) {
      const line = ce("div", "sm-line sm-click",
        `→ ${b}${(s.locked_exits || {})[b] ? " 🔒" : ""}${DATA.scenes[b] ? "" : "（尚未创建）"}`);
      line.addEventListener("click", () => showEdgeInspector(id, b));
      body.append(line);
    }

    const back = sceneIds().filter(k => k !== id && (DATA.scenes[k].exits || []).includes(id));
    body.append(ce("div", "nci-sec", `入口（来自 ${back.length} 个地点）`));
    if (back.length) {
      body.append(ce("div", "sm-line",
        back.map(k => `${(DATA.scenes[k].name || k)}（${k}）`).join("、")));
    }

    body.append(ce("div", "nci-sec", "在场内容"));
    const npcsHere = Object.values(DATA.npcs || {}).filter(n => n.scene_id === id);
    const summary = [
      (s.items_here || []).length ? `物品 ${s.items_here.length}` : null,
      (s.shop_items || []).length ? `商店 ${s.shop_items.length}` : null,
      (s.enemies_here || []).length ? `敌人 ${s.enemies_here.length}` : null,
      npcsHere.length ? `NPC ${npcsHere.length}` : null,
    ].filter(Boolean).join(" · ");
    body.append(ce("div", "sm-line", summary || "（空场景）"));
    if (npcsHere.length) {
      body.append(ce("div", "sm-line", "NPC：" + npcsHere.map(n => n.name || n.id).join("、")));
    }

    const editBtn = ce("button", "cw-tb", "编辑详情（描述/物品/商店/敌人）");
    editBtn.type = "button";
    editBtn.style.marginTop = "8px";
    editBtn.addEventListener("click", () => window.EditorActions.editScene(id));
    const delBtn = ce("button", "cw-tb", "删除该地点");
    delBtn.type = "button";
    delBtn.style.cssText = "border-color:var(--danger);color:var(--danger);align-self:flex-start";
    delBtn.addEventListener("click", () => deleteScene(id));
    body.append(editBtn, delBtn);
  }

  function showEdgeInspector(a, b) {
    const s = DATA.scenes[a], t = DATA.scenes[b];
    if (!s || !t) { showPlaceholder(); return; }
    view.selScene = null;
    view.selEdge = [a, b];
    $("nci-title").textContent = `出口 ${a} ↔ ${b}`;
    const body = $("nci-body");
    body.innerHTML = "";

    const mkToggle = (label, checked, disabled, onChange) => {
      const lab = ce("label", "ed-check");
      const cb = ce("input");
      cb.type = "checkbox";
      cb.checked = checked;
      cb.disabled = disabled;
      cb.addEventListener("change", onChange);
      lab.append(cb, ce("span", null, label));
      return lab;
    };
    const abOn = (s.exits || []).includes(b);
    const baOn = (t.exits || []).includes(a);
    const commit = () => { render(); showEdgeInspector(a, b); };
    body.append(mkToggle(`→ ${b}（${s.name || a} 通向 ${t.name || b}）`, abOn, false,
      () => { setExit(a, b, !abOn); commit(); }));
    body.append(mkToggle(`← ${a}（${t.name || b} 通向 ${s.name || a}）`, baOn, false,
      () => { setExit(b, a, !baOn); commit(); }));
    body.append(mkToggle(`🔒 锁定 ${a} → ${b}`, !!(s.locked_exits || {})[b], !abOn,
      e => { setLock(a, b, e.target.checked); commit(); }));
    body.append(mkToggle(`🔒 锁定 ${b} → ${a}`, !!(t.locked_exits || {})[a], !baOn,
      e => { setLock(b, a, e.target.checked); commit(); }));
    body.append(ce("div", "nci-empty",
      "锁定的出口在游戏中无法通行，需要事件规则（unlock 效果）解锁。改动后点「保存」落盘。"));
  }

  async function deleteScene(id) {
    const s = DATA.scenes[id];
    if (!s) return;
    if (!confirm(`确认删除地点【${s.name || id}】？（被出口引用/有玩家在内时后端会拒绝）`)) return;
    try {
      const res = await fetch("/api/editor/scene/" + encodeURIComponent(id), { method: "DELETE" });
      const data = await res.json();
      if (!data.success) { toast(data.message, "error"); return; }
      view.dirty.delete(id);
      updateDirty();
      toast(data.message, "success");
      await window.EditorActions.reloadData();
    } catch (_) { toast("删除失败：无法连接服务器", "error"); }
  }

  /* ================= 新建地点（画布上直接建骨架） ================= */
  function addScene() {
    const raw = prompt("新地点 ID（小写字母/数字/下划线，保存后不可改）：", "");
    if (raw == null) return;
    const id = raw.trim();
    if (!ID_PATTERN.test(id)) {
      toast("ID 只能用小写字母、数字、下划线，最长 32 位", "error");
      return;
    }
    if (DATA.scenes[id]) { toast("已存在同名地点", "error"); return; }
    DATA.scenes[id] = {
      id,
      name: id,
      description: "（待补充描述：请点「编辑详情」完善）",
      exits: [],
      items_here: [],
      locked_exits: {},
      enemies_here: [],
      shop_items: [],
    };
    markDirty(id);
    render();
    requestAnimationFrame(() => {
      const dfId = dfIdOf(id);
      if (dfId == null) return;
      const z = view.editor.zoom;
      const x = snap((-view.editor.canvas_x) / z + 120);
      const y = snap((-view.editor.canvas_y) / z + 80);
      const d = liveData()[dfId];
      d.pos_x = x; d.pos_y = y;
      const el = box().querySelector("#node-" + dfId);
      if (el) { el.style.left = x + "px"; el.style.top = y + "px"; }
      view.editor.updateConnectionNodes("node-" + dfId);
      view.layoutDirty.add(id);
      scheduleLayoutFlush();
      if (inspector().classList.contains("collapsed")) setPanel(true);
      showSceneInspector(id);
    });
  }

  /* ================= 内容保存（批量 POST upsert_scene） ================= */
  async function save(opts) {
    const silent = !!(opts || {}).silent;
    clearTimeout(view.saveTimer);
    if (!view.dirty.size) {
      if (!silent) toast("没有需要保存的改动");
      return;
    }
    const ids = [...view.dirty].filter(id => DATA.scenes[id]);
    const errors = [];
    let ok = 0;
    for (const id of ids) {
      try {
        const res = await fetch("/api/editor/scene", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(DATA.scenes[id]),
        });
        const data = await res.json();
        if (data.success) { view.dirty.delete(id); ok++; }
        else errors.push(`${id}：${data.message}`);
      } catch (_) { errors.push(`${id}：无法连接服务器`); }
    }
    updateDirty();
    if (errors.length) { toast(errors.join("\n"), "error"); return; }
    if (silent) return;
    await window.EditorActions.reloadData();  // 手动保存走统一重拉（保持画布模式）
    toast(`已保存 ${ok} 个地点`, "success");
  }

  /* ================= 坐标：防抖独立保存 ================= */
  function scheduleLayoutFlush() {
    clearTimeout(view.layoutTimer);
    view.layoutTimer = setTimeout(flushLayout, 800);
  }
  async function flushLayout() {
    // 先同步快照坐标（退出画布后 liveData 不可用），再逐个落盘
    const shots = [];
    for (const id of view.layoutDirty) {
      const dfId = dfIdOf(id);
      if (dfId == null) continue;
      const d = liveData()[dfId];
      if (d) shots.push({ id, pos: { x: snap(d.pos_x), y: snap(d.pos_y) } });
    }
    view.layoutDirty.clear();
    for (const { id, pos } of shots) {
      try {
        const res = await fetch(`/api/editor/scene-layout/${encodeURIComponent(id)}`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(pos),
        });
        const data = await res.json();
        if (data.success && DATA.scene_layouts) DATA.scene_layouts[id] = pos;
      } catch (_) { /* 坐标保存失败不阻断编辑，下次拖动重试 */ }
    }
  }

  /* ================= 鼠标交互（右键平移 / 左键点击判定） ================= */
  function isBlank(t) {
    return t.classList && (t.classList.contains("drawflow") ||
                           t.classList.contains("parent-drawflow"));
  }
  function onPortOrWire(t) {
    return t.classList && (t.classList.contains("output") || t.classList.contains("input") ||
      t.classList.contains("main-path") || t.classList.contains("point") ||
      t.closest?.(".connection"));
  }

  function onDocMouseDown(e) {
    if (!box().contains(e.target)) return;
    if (e.button === 2) {   // 右键平移：捕获阶段阻断库的选中/拖动
      e.preventDefault();
      e.stopPropagation();
      view.panning = {
        x: e.clientX, y: e.clientY,
        cx: view.editor.canvas_x, cy: view.editor.canvas_y,
      };
      box().classList.add("panning");
      return;
    }
    if (e.button !== 0) return;
    if (isBlank(e.target)) return;   // 空白处交给库（无框选需求）
    const nodeEl = e.target.closest?.(".drawflow-node");
    if (nodeEl && !onPortOrWire(e.target)) {
      // 记录候选：mouseup 时位移 ≤4px 才弹抽屉，超过就是拖卡（拖动由库处理）
      view.pending = { dfId: +nodeEl.id.slice(5), x: e.clientX, y: e.clientY, moved: false };
    }
  }

  function onMouseMove(e) {
    if (view.panning) {
      const p = view.panning, z = view.editor.zoom;
      view.editor.canvas_x = p.cx + (e.clientX - p.x);
      view.editor.canvas_y = p.cy + (e.clientY - p.y);
      view.editor.precanvas.style.transform =
        `translate(${view.editor.canvas_x}px, ${view.editor.canvas_y}px) scale(${z})`;
      updateZoomLabel();
      saveViewSoon();
      return;
    }
    if (view.pending &&
        Math.hypot(e.clientX - view.pending.x, e.clientY - view.pending.y) > CLICK_THRESHOLD) {
      view.pending.moved = true;    // 已判定为拖卡，mouseup 不再弹抽屉
    }
    updateCoord(e.clientX, e.clientY);
  }

  function onMouseUp(e) {
    if (view.panning) {
      view.panning = null;
      box().classList.remove("panning");
      updateCoord(e.clientX, e.clientY);
      return;
    }
    if (view.pending) {
      const { dfId, moved } = view.pending;
      view.pending = null;
      if (!moved) {
        const id = view.idMap[dfId];
        if (id) {
          if (inspector().classList.contains("collapsed")) setPanel(true);
          showSceneInspector(id);
        }
      }
    }
  }

  /* ================= 视图（平移/缩放）localStorage 持久化 ================= */
  function saveViewNow() {
    if (!view.mounted) return;
    try {
      localStorage.setItem(VIEW_LS_KEY, JSON.stringify({
        x: view.editor.canvas_x, y: view.editor.canvas_y, zoom: view.editor.zoom,
      }));
    } catch (_) {}
  }
  function saveViewSoon() {
    clearTimeout(view.viewTimer);
    view.viewTimer = setTimeout(saveViewNow, 400);
  }
  function applyView() {
    if (!view.mounted) return;
    let v = null;
    try { v = JSON.parse(localStorage.getItem(VIEW_LS_KEY) || "null"); } catch (_) {}
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

  /* ================= 底栏 / 工具条 ================= */
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
    $("cw-st-npc").textContent = `共 ${sceneIds().length} 个`;
    updateDirty();
    updateZoomLabel();
  }

  function on(el, type, fn, opt) {
    el.addEventListener(type, fn, opt);
    view.listeners.push({ el, type, fn, opt });
  }

  function bindDomEvents() {
    on(document, "mousedown", onDocMouseDown, true);
    on(window, "mousemove", onMouseMove);
    on(window, "mouseup", onMouseUp);
    // 工作台区域内彻底接管右键菜单（库的 contextmenu 监听注册更早，需捕获阶段拦截）
    on(document, "contextmenu", e => {
      const hit = e.target.closest &&
        (e.target.closest("#cw-canvas") || e.target.closest("#cw-bar") ||
         e.target.closest("#cw-status") || e.target.closest("#nc-inspector") ||
         e.target.closest(".ed-topbar"));
      if (hit) {
        e.preventDefault();
        e.stopPropagation();
      }
    }, true);
    on(window, "blur", () => {
      view.panning = null;
      view.pending = null;
      box().classList.remove("panning");
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
    // 点连线 → 出口检查器（库自身仍会加 selected 高亮，不冲突）
    on(box(), "click", e => {
      const conn = e.target.closest?.(".connection");
      if (conn && conn.dataset && conn.dataset.from) {
        if (inspector().classList.contains("collapsed")) setPanel(true);
        showEdgeInspector(conn.dataset.from, conn.dataset.to);
      }
    });

    $("cw-add").onclick = addScene;
    $("cw-save").onclick = () => save();   // 不能直接传 save（事件对象会被当成 opts.silent）
    $("cw-zin").onclick = () => view.editor.zoom_in();
    $("cw-zout").onclick = () => view.editor.zoom_out();
    $("cw-zreset").onclick = () => view.editor.zoom_reset();
    $("cw-panel-toggle").onclick = () =>
      setPanel(inspector().classList.contains("collapsed"));
    $("cw-snap").onchange = e => { view.snap = e.target.checked; };
    $("cw-grid").onchange = e => {
      view.grid = +e.target.value;
      box().style.setProperty("--nc-grid", view.grid + "px");
      render();
    };
  }
})();
