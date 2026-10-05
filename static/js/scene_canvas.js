/* scene_canvas.js —— 世界地图画布工作台（顶栏「画布编辑」+ 地点页签/默认）
 *
 * 定位（沿用 NPC 对话画布三原则）：
 *  - 画布只管结构：节点 = 地点卡片，连线 = 出口关系；内容（描述/物品/商店/敌人）
 *    仍由列表表单编辑，卡片上只放摘要；
 *  - 坐标是独立通道：scene_layouts.json（引擎不读），拖动防抖保存；
 *  - 出口/锁定改动直接改内存 DATA.scenes[id]，显式「保存」批量 POST /api/editor/scene
 *    （后端 upsert_scene 校验兜底，不绕过）。
 *
 * 交互（与 NPC 对话画布同一套手感）：
 *  - 左键拖卡片摆位；左键空白拖框选（Ctrl 追加多选），多选组整组拖动；
 *  - 从卡片右侧端口拉线到另一张卡片 = 新建出口；
 *  - 点连线 = 线条状态（只读）+ 删除线条；点卡片 = 地点抽屉（出口行内锁定/移除）；
 *  - 右键拖动平移，滚轮缩放；Ctrl+S 保存。
 *
 * 依赖：window.Drawflow（vendored）、editor.js 的全局 DATA / toast / EditorActions。
 */
(function () {
  "use strict";

  const G_COARSE = 28, G_FINE = 14;
  const CLICK_THRESHOLD = 4;
  const ID_PATTERN = /^[a-z0-9_]{1,32}$/;

  const esc = s => (s || "").replace(/[&<>]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const ce = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  /* 自动保存开关：偏好存数据文件 editor_settings.json（npc_canvas 设置弹窗维护，
   *  editor.js loadData 时回填 window.EditorSettings），不使用浏览器存储 */
  const autosaveOn = () => !!(window.EditorSettings && window.EditorSettings.autosave);

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
    multi: new Set(),       // 框选多选的 drawflow id
    group: null,            // 多选组拖动 {members:Map(dfId->{x,y}), lastX,lastY}
    dragBox: null,          // 左键空白框选中 {x0,y0}
    dragHeal: false,        // 拖卡中：rAF 兜底重算连线的标志
    wireLog: [],            // M10 连线消失取证：连线健康异常/移除事件环形日志
    wireMO: null,           // 连线 svg 增删的 MutationObserver
    selScene: null,         // 抽屉当前显示的地点
    selEdge: null,          // 抽屉当前显示的出口对 [a, b]
    mounted: false,
    listeners: [],
    layoutTimer: null,
    saveTimer: null,
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
  const marquee = () => $("cw-marquee");
  const nodePos = dfId => { const d = liveData()[dfId]; return { x: d.pos_x, y: d.pos_y }; };

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
    $("cw-autolayout").classList.remove("hidden");
    $("nci-edit-npc").classList.add("hidden");
    mount();
    render();
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
    if (view.mounted) render();   // 实例不重建：当前平移/缩放作为会话内状态自然保留
  }

  window.SceneCanvas = {
    enter, exit, refresh,
    getWireLog: () => view.wireLog,
    clearWireLog: () => { view.wireLog = []; },
    auditWires: tag => auditWires(tag || "manual"),
  };

  /* ================= 挂载 / 卸载 ================= */
  function mount() {
    if (view.mounted) return;
    box().innerHTML = "";               // 清掉上一实例残留（destroy 不清 DOM）
    view.editor = new Drawflow(box());
    view.editor.start();
    view.editor.force_first_input = true;
    view.editor.zoom_max = 1.6;
    view.editor.zoom_min = 0.3;
    window.__sceneEditor = view.editor;   // 调试钩子（拖线几何排查用）
    view.mounted = true;
    injectMarkerDefs();
    bindEditorEvents();
    bindDomEvents();
    installWireObserver();
    setPanel(true);   // 每次进入默认展开属性栏（不做浏览器持久化）
    $("cw-snap").checked = view.snap;
    $("cw-grid").value = String(view.grid);
    box().style.setProperty("--nc-grid", view.grid + "px");
    centerOrigin();                 // 初始视图：(0,0) 格落在视口中心
    requestAnimationFrame(centerOrigin);   // 兜底：首帧布局完成后再校正一次
  }

  /** 视图复位：zoom=1 且让世界坐标 (0,0) 格居中显示在视口 */
  function centerOrigin() {
    const ed = view.editor;
    if (!ed || !view.mounted) return;
    ed.zoom = 1;
    ed.canvas_x = box().clientWidth / 2;
    ed.canvas_y = box().clientHeight / 2;
    ed.precanvas.style.transform =
      `translate(${ed.canvas_x}px, ${ed.canvas_y}px) scale(1)`;
    updateZoomLabel();
  }

  function unmount() {
    if (!view.mounted) return;
    view.listeners.forEach(({ el, type, fn, opt }) => el.removeEventListener(type, fn, opt));
    view.listeners = [];
    if (view.wireMO) { view.wireMO.disconnect(); view.wireMO = null; }
    clearTimeout(view.layoutTimer);
    clearTimeout(view.saveTimer);
    try { view.editor.destroy(); } catch (_) {}
    box().innerHTML = "";
    view.editor = null;
    view.mounted = false;
    view.idMap = {};
    view.dirty.clear();
    view.layoutDirty.clear();
    view.panning = null;
    view.pending = null;
    view.multi.clear();
    view.group = null;
    view.dragBox = null;
    $("cw-autolayout").classList.add("hidden");
    box().classList.remove("panning");
    box().classList.remove("grabbing");
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

  const COL_GAP = 308, ROW_GAP = 224;   // 坐标系以 (0,0) 为视口中心，向四象限延伸
  /** 从出生点沿 exits 做 BFS：layer[id]=距出生点跳数；order[id]=BFS 发现序。
   *  与出生点不通的孤岛统一放到 maxLayer+1 列（同 NPC 画布「开始对话」锚点语义） */
  function computeLayers() {
    const ids = sceneIds();
    const adj = {};
    ids.forEach(i => {
      adj[i] = (DATA.scenes[i].exits || []).filter(b => b !== i && DATA.scenes[b]);
    });
    const layer = {}, order = {};
    let seq = 0;
    const start = DATA.initial_scene && DATA.scenes[DATA.initial_scene]
      ? DATA.initial_scene : ids[0];
    if (start) {
      layer[start] = 0; order[start] = seq++;
      const q = [start];
      while (q.length) {
        const a = q.shift();
        for (const b of adj[a]) {
          if (layer[b] === undefined) { layer[b] = layer[a] + 1; order[b] = seq++; q.push(b); }
        }
      }
    }
    let maxL = 0;
    Object.values(layer).forEach(l => { maxL = Math.max(maxL, l); });
    ids.filter(i => layer[i] === undefined).sort().forEach(i => {
      layer[i] = maxL + 1; order[i] = seq++;
    });
    return { layer, order };
  }

  /** 自动布局坐标：出生点列 x=0（视口中心），旅程向右每列 +COL_GAP；
   *  同列多行以 y=0 为中轴上下对称排开 */
  function computeLayout() {
    const ids = sceneIds();
    const { layer, order } = computeLayers();
    const cols = {};
    ids.forEach(i => { (cols[layer[i]] = cols[layer[i]] || []).push(i); });
    const pos = {};
    Object.keys(cols).sort((a, b) => a - b).forEach(l => {
      cols[l].sort((a, b) => order[a] - order[b]);
      const mid = (cols[l].length - 1) / 2;
      cols[l].forEach((id, row) => {
        pos[id] = { x: snap(l * COL_GAP), y: snap((row - mid) * ROW_GAP) };
      });
    });
    return pos;
  }

  function render() {
    if (!view.mounted) return;
    const layout = posMap();
    const autoPos = computeLayout();
    view.rendering = true;
    view.editor.clear();
    view.idMap = {};
    view.multi.clear();                 // 重建后 dfId 全变，多选失效
    box().querySelectorAll(".multi-sel").forEach(el => el.classList.remove("multi-sel"));
    // 节点：地点卡片（无已存坐标的按 BFS 分层自动排布）
    for (const id of sceneIds()) {
      const s = DATA.scenes[id] || {};
      let x = layout[id] && Number.isFinite(layout[id].x) ? layout[id].x : null;
      let y = layout[id] && Number.isFinite(layout[id].y) ? layout[id].y : null;
      if (x == null) ({ x, y } = autoPos[id] || { x: 0, y: 0 });
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
    // 连线：每个无向地点对一条（装饰），但物理锚点必须沿真实流向——
    // output（右端口）锚在「离出生点更近」的一端，玩家旅程天然从左走到右；
    // 单向边（如抄近道回起点）按出口真实方向锚，会自然绕回左侧。
    const { layer } = computeLayers();
    const pairs = new Map();   // pairKey -> {x,y（按 id 排序）, ab:x→y, ba:y→x}
    for (const a of sceneIds()) {
      for (const b of DATA.scenes[a].exits || []) {
        if (b === a || !DATA.scenes[b]) continue;
        const k = pairKey(a, b);
        let p = pairs.get(k);
        if (!p) { p = { x: a < b ? a : b, y: a < b ? b : a, ab: false, ba: false }; pairs.set(k, p); }
        if (a === p.x && b === p.y) p.ab = true; else p.ba = true;
      }
    }
    for (const p of pairs.values()) {
      const lx = layer[p.x], ly = layer[p.y];
      let out, inn;
      if (lx < ly && p.ab) { out = p.x; inn = p.y; }          // x 更靠近出生点：x→y 正向
      else if (ly < lx && p.ba) { out = p.y; inn = p.x; }     // y 更靠近出生点：y→x 正向
      else if (p.ab && !p.ba) { out = p.x; inn = p.y; }       // 仅单向 x→y（回流捷径也忠实于数据）
      else if (p.ba && !p.ab) { out = p.y; inn = p.x; }       // 仅单向 y→x
      else { out = p.x; inn = p.y; }                          // 同层互通：按 id 序兜底
      const fo = dfIdOf(out), fi = dfIdOf(inn);
      if (fo == null || fi == null) continue;
      const mutual = p.ab && p.ba;
      const locked = (p.ab && (DATA.scenes[p.x].locked_exits || {})[p.y]) ||
                     (p.ba && (DATA.scenes[p.y].locked_exits || {})[p.x]);
      view.editor.addConnection(fo, fi, "output_1", "input_1");
      const conns = box().querySelectorAll(".connection");
      const el = conns[conns.length - 1];
      if (el) {
        el.classList.add("sm-edge");
        if (!mutual) el.classList.add("sm-one");
        if (locked) el.classList.add("sm-locked");
        el.dataset.from = out;
        el.dataset.to = inn;
      }
    }
    view.rendering = false;
    syncStatus();
    auditWires("render");
  }

  /* ================= 连线消失取证（M10 常驻轻量日志，window.SceneCanvas.getWireLog 取） ================= */
  function wire(o) {
    o.t = Date.now();
    view.wireLog.push(o);
    if (view.wireLog.length > 200) view.wireLog.splice(0, view.wireLog.length - 200);
  }
  function shortStack() {
    try {
      return new Error().stack.split("\n").slice(2, 7).map(s => s.trim()).join(" | ");
    } catch (_) { return ""; }
  }
  /** 钩住库的事件派发与 clear：连线/节点被移除或画布重建时留下事件名与调用栈 */
  function installWireTaps(ed) {
    const WATCH = ["connectionCreated", "connectionRemoved", "connectionCancel",
      "nodeRemoved", "nodeMoved", "nodeUnselected"];
    const origDispatch = ed.dispatch.bind(ed);
    ed.dispatch = function (name, arg) {
      if (WATCH.indexOf(name) >= 0) {
        const rec = { tag: "ev:" + name };
        if (name === "connectionRemoved" || name === "nodeRemoved") rec.stack = shortStack();
        wire(rec);
      }
      return origDispatch(name, arg);
    };
    const origClear = ed.clear.bind(ed);
    ed.clear = function () { wire({ tag: "ev:clear", stack: shortStack() }); return origClear(); };
  }
  /** 监听 precanvas 内连线 svg 的真实增删（DOM 层铁证） */
  function installWireObserver() {
    if (view.wireMO) view.wireMO.disconnect();
    const pc = view.editor && view.editor.precanvas;   // 库结构：container=.parent-drawflow，内层 precanvas=.drawflow
    if (!pc) return;
    view.wireMO = new MutationObserver(muts => {
      muts.forEach(m => {
        if (m.type !== "childList") return;
        m.removedNodes.forEach(n => {
          if (n.tagName === "svg" && (n.className.baseVal || "").indexOf("connection") >= 0)
            wire({ tag: "svg-removed", cls: n.className.baseVal });
        });
        m.addedNodes.forEach(n => {
          if (n.tagName === "svg" && (n.className.baseVal || "").indexOf("connection") >= 0)
            wire({ tag: "svg-added", cls: n.className.baseVal });
        });
      });
    });
    view.wireMO.observe(pc, { childList: true, subtree: true });
  }
  /** 体检：每条连线的 d 是否空/NaN，端点是否与两端端口实际坐标一致（偏差>8px＝失联） */
  function auditWires(tag) {
    if (!view.mounted) return;
    try {
      const ed = view.editor, s = ed.precanvas;   // 内层 .drawflow（transform 作用层）
      if (!s) return;
      const z = ed.zoom || 1;
      const u = s.clientWidth / (s.clientWidth * z) || 0;
      const p = s.clientHeight / (s.clientHeight * z) || 0;
      const sr = s.getBoundingClientRect();
      const portXY = (nodeSel, portCls) => {
        const el = s.querySelector("#" + nodeSel + " ." + portCls);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return [el.offsetWidth / 2 + (r.x - sr.x) * u, el.offsetHeight / 2 + (r.y - sr.y) * p];
      };
      box().querySelectorAll("svg.connection").forEach(svg => {
        const cl = svg.className.baseVal || "";
        const path = svg.querySelector("path");
        const d = path ? (path.getAttribute("d") || "") : "";
        if (!d.trim() || /NaN|Infinity/.test(d)) { wire({ tag, kind: "d-bad", cls: cl, d: d.slice(0, 80) }); return; }
        const mi = cl.match(/node_in_(node-\d+)/), mo = cl.match(/node_out_(node-\d+)/);
        const parts = cl.split(" ");
        if (!mi || !mo) { wire({ tag, kind: "cls-bad", cls: cl }); return; }
        const out = portXY(mo[1], parts[3]), inp = portXY(mi[1], parts[4]);
        const nums = d.match(/-?\d*\.?\d+(?:e-?\d+)?/g);
        if (out && inp && nums && nums.length >= 2) {
          const ax = +nums[0], ay = +nums[1];
          const bx = +nums[nums.length - 2], by = +nums[nums.length - 1];
          const e1 = Math.hypot(ax - out[0], ay - out[1]);
          const e2 = Math.hypot(bx - inp[0], by - inp[1]);
          if (Math.max(e1, e2) > 8)
            wire({ tag, kind: "stale", cls: cl.slice(0, 80), e1: Math.round(e1), e2: Math.round(e2) });
        }
      });
    } catch (err) { wire({ tag, kind: "audit-throw", msg: String(err && err.message) }); }
  }

  /* ================= Drawflow 事件 → 内存数据 ================= */
  function bindEditorEvents() {
    const ed = view.editor;
    installWireTaps(ed);
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
      auditWires("nodeMoved:" + id);
    });
    ed.on("zoom", updateZoomLabel);
    ed.on("translate", updateZoomLabel);
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
  }

  function showPlaceholder() {
    view.selScene = null;
    view.selEdge = null;
    $("nci-title").textContent = "世界地图";
    $("nci-body").innerHTML =
      '<div class="nci-empty">点选地点卡片查看摘要并编辑出口；点连线查看线条状态、可删除。<br><br>' +
      "左键空白拖动＝框选多卡（Ctrl 追加），多选可整组拖动<br>" +
      "从卡片右侧端口拉线到另一张卡片＝新建出口<br>" +
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

    body.append(ce("div", "nci-sec", `出口（${(s.exits || []).length}）—— 从卡片端口拉线新建`));
    if (!(s.exits || []).length) {
      body.append(ce("div", "nci-empty", "（暂无出口：从卡片右侧端口拉线到目标卡片）"));
    }
    for (const b of s.exits || []) {
      const t = DATA.scenes[b];
      const locked = !!(s.locked_exits || {})[b];
      const line = ce("div", "sm-exit-row");
      const name = ce("span", "sm-exit-name",
        `→ ${t ? (t.name || b) : b + "（尚未创建）"}（${b}）${locked ? " 🔒" : ""}`);
      const lockBtn = ce("button", "sm-mini", locked ? "解锁" : "锁定");
      lockBtn.type = "button";
      lockBtn.addEventListener("click", () => {
        setLock(id, b, !locked);
        render();
        showSceneInspector(id);
      });
      const rmBtn = ce("button", "sm-mini sm-danger", "移除");
      rmBtn.type = "button";
      rmBtn.addEventListener("click", () => {
        setExit(id, b, false);
        render();
        showSceneInspector(id);
      });
      line.append(name, lockBtn, rmBtn);
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

  /** 线条状态（只读）：两端是谁、方向、锁定；附「删除这条线」。
   *  线条只是连接装饰，出口的增删/锁定在两端卡片属性栏编辑。 */
  function showEdgeInspector(a, b) {
    const s = DATA.scenes[a], t = DATA.scenes[b];
    if (!s || !t) { showPlaceholder(); return; }
    view.selScene = null;
    view.selEdge = [a, b];
    $("nci-title").textContent = "线条状态";
    const body = $("nci-body");
    body.innerHTML = "";

    const row = (k, v) => {
      const r = ce("div", "sm-row");
      r.append(ce("span", "k", k), ce("span", "v", v));
      return r;
    };
    const abOn = (s.exits || []).includes(b);
    const baOn = (t.exits || []).includes(a);
    const locks = [
      abOn && (s.locked_exits || {})[b] ? `${s.name || a}→${t.name || b}` : null,
      baOn && (t.locked_exits || {})[a] ? `${t.name || b}→${s.name || a}` : null,
    ].filter(Boolean);

    body.append(row("一端", `${s.name || a}（${a}）`));
    body.append(row("另一端", `${t.name || b}（${b}）`));
    body.append(row("方向", abOn && baOn ? "双向" : abOn ? "单向 →" : baOn ? "单向 ←" : "（无出口）"));
    body.append(row("锁定", locks.length ? locks.join("、") : "无"));
    body.append(ce("div", "nci-empty",
      "线条只是两端卡片的连接装饰：新建靠拉线，方向与锁定的编辑在两端卡片的属性栏里。" +
      "画错了就点下面按钮删掉这条线。"));

    const delBtn = ce("button", "cw-tb", "删除这条线");
    delBtn.type = "button";
    delBtn.style.cssText = "border-color:var(--danger);color:var(--danger);align-self:flex-start";
    delBtn.addEventListener("click", () => {
      const msg = abOn && baOn
        ? `这条线包含两个方向的出口（${a}→${b} 与 ${b}→${a}），删除会一并移除并清掉对应锁定。确定删除？`
        : `确定删除 ${a} → ${b} 的出口连线？`;
      if (!confirm(msg)) return;
      removeEdgePair(a, b);
    });
    body.append(delBtn);
  }

  /** 删除两地点间的连线 = 清掉两个方向的出口（setExit 顺带清锁定），重绘并回到占位 */
  function removeEdgePair(a, b) {
    setExit(a, b, false);
    setExit(b, a, false);
    render();
    showPlaceholder();
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

  /* ================= 一键自动布局（BFS 分层，覆盖当前坐标） ================= */
  function autoLayoutAll() {
    if (!view.mounted) return;
    if (!confirm("按出生点流向重新排列全部地点（出生点在最左），覆盖当前手动位置。\n" +
                 "只改画布坐标（自动落盘），不影响出口/锁定等内容。继续？")) return;
    const pos = computeLayout();
    for (const id of sceneIds()) {
      const dfId = dfIdOf(id);
      if (dfId == null || !pos[id]) continue;
      const d = liveData()[dfId];
      d.pos_x = pos[id].x; d.pos_y = pos[id].y;
      const el = box().querySelector("#node-" + dfId);
      if (el) { el.style.left = pos[id].x + "px"; el.style.top = pos[id].y + "px"; }
      view.layoutDirty.add(id);
    }
    healConnections();
    auditWires("auto-layout");
    scheduleLayoutFlush();
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
    if (isBlank(e.target)) {
      // 左键空白：框选（阻断库的空白平移，与 NPC 画布一致）
      e.stopPropagation();
      view.dragBox = { x0: e.clientX, y0: e.clientY };
      marquee().style.display = "block";
      Object.assign(marquee().style,
        { left: e.clientX + "px", top: e.clientY + "px", width: "0px", height: "0px" });
      return;
    }
    const nodeEl = e.target.closest?.(".drawflow-node");
    if (nodeEl && view.multi.has(+nodeEl.id.slice(5))) {
      // 多选组整组拖动：我们自己处理，阻断库的单卡拖动
      e.stopPropagation();
      const members = new Map();
      view.multi.forEach(dfId => members.set(dfId, nodePos(dfId)));
      view.group = { members, lastX: e.clientX, lastY: e.clientY };
      box().classList.add("grabbing");
      return;
    }
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
      return;
    }
    if (view.pending &&
        Math.hypot(e.clientX - view.pending.x, e.clientY - view.pending.y) > CLICK_THRESHOLD) {
      view.pending.moved = true;    // 已判定为拖卡，mouseup 不再弹抽屉
    }
    if (view.group) {
      const z = view.editor.zoom;
      const sx = (e.clientX - view.group.lastX) / z;
      const sy = (e.clientY - view.group.lastY) / z;
      view.group.lastX = e.clientX; view.group.lastY = e.clientY;
      view.group.members.forEach((p, dfId) => {
        const d = liveData()[dfId];
        if (!d) return;                     // 组成员已被重建/删除，跳过
        d.pos_x = p.x + sx; d.pos_y = p.y + sy; p.x = d.pos_x; p.y = d.pos_y;
        const el = box().querySelector("#node-" + dfId);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
      });
      view.group.members.forEach((_p, dfId) => {
        if (!liveData()[dfId]) return;
        view.editor.updateConnectionNodes("node-" + dfId);
      });
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
    // 拖卡中的连线兜底：库自身的 updateConnectionNodes 偶发失联（线条不动/消失），
    // 每帧补一次重算，成本可忽略（地图节点量级小）
    if (view.editor.drag && view.editor.ele_selected) {
      if (!view.dragHeal) {
        view.dragHeal = true;
        requestAnimationFrame(() => {
          view.dragHeal = false;
          if (view.mounted && view.editor.drag && view.editor.ele_selected) {
            try { view.editor.updateConnectionNodes(view.editor.ele_selected.id); } catch (_) {}
          }
        });
      }
    }
    updateCoord(e.clientX, e.clientY);
  }

  function onMouseUp(e) {
    if (e.__synthetic) return;        // 我们补发给容器的合成 mouseup，不重跑本地收尾
    if (view.panning) {
      view.panning = null;
      box().classList.remove("panning");
      updateCoord(e.clientX, e.clientY);
      return;
    }
    // 库的 mouseup 只监听画布容器：在容器外松手时 dragEnd 丢失、drag 状态卡死。
    // 补发给容器，让库正常派发 nodeMoved（snap/落盘由既有 handler 完成）
    if (view.mounted && view.editor.drag === true && !box().contains(e.target)) {
      view.pending = null;
      const syn = new MouseEvent("mouseup", {
        bubbles: true, cancelable: true, view: window,
        button: 0, clientX: e.clientX, clientY: e.clientY,
      });
      syn.__synthetic = true;
      box().dispatchEvent(syn);
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
          auditWires("click-card");   // 「点击卡片线条恢复」瞬间的体检
        }
      }
    }
    if (view.group) {
      if (view.snap) view.group.members.forEach((_p, dfId) => {
        const d = liveData()[dfId];
        if (!d) return;
        d.pos_x = snap(d.pos_x); d.pos_y = snap(d.pos_y);
        const el = box().querySelector("#node-" + dfId);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
      });
      view.group.members.forEach((_p, dfId) => {
        if (!liveData()[dfId]) return;
        view.editor.updateConnectionNodes("node-" + dfId);
        const id = view.idMap[dfId];
        if (id) view.layoutDirty.add(id);
      });
      view.group = null;
      box().classList.remove("grabbing");
      scheduleLayoutFlush();
      auditWires("group-up");
      return;
    }
    if (!view.dragBox) {
      auditWires("up-before-heal");
      healConnections();
      auditWires("up-after-heal");
      return;
    }
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
      const dfId = +el.id.slice(5);
      if (hit) { view.multi.add(dfId); el.classList.add("multi-sel"); }
      else if (!additive) { view.multi.delete(dfId); el.classList.remove("multi-sel"); }
    });
  }

  /** 手势收尾兜底：全量重算连线（修「拖卡后线条消失/失联」——
   *  无论库内部哪一步漏了重算，松手后强制对齐一次） */
  function healConnections() {
    if (!view.mounted) return;
    for (const dfId of Object.keys(liveData())) {
      try { view.editor.updateConnectionNodes("node-" + dfId); } catch (_) {}
    }
  }

  /** 清空库自身的节点/连线选中态（框选空白开始前调用） */
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

  function clearMulti() {
    view.multi.clear();
    box().querySelectorAll(".multi-sel").forEach(el => el.classList.remove("multi-sel"));
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
      view.group = null;
      view.dragBox = null;
      marquee().style.display = "none";
      box().classList.remove("panning");
      box().classList.remove("grabbing");
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
    // 点连线 → 线条状态（只读 + 删除）（库自身仍会加 selected 高亮，不冲突）
    on(box(), "click", e => {
      const conn = e.target.closest?.(".connection");
      if (conn && conn.dataset && conn.dataset.from) {
        if (inspector().classList.contains("collapsed")) setPanel(true);
        showEdgeInspector(conn.dataset.from, conn.dataset.to);
      }
    });

    $("cw-add").onclick = addScene;
    $("cw-autolayout").onclick = autoLayoutAll;
    $("cw-save").onclick = () => save();   // 不能直接传 save（事件对象会被当成 opts.silent）
    $("cw-zin").onclick = () => view.editor.zoom_in();
    $("cw-zout").onclick = () => view.editor.zoom_out();
    $("cw-zreset").onclick = centerOrigin;   // 复位 = 100% 缩放且 (0,0) 格回中
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
