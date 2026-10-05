/* npc_canvas.js —— 画布工作台（顶栏「画布编辑」模式）
 *
 * 定位（见 .trae/documents/canvas_workbench_phase1_plan.md）：
 *  - 与列表编辑平级的工作台：列表 = 表单式精编；画布 = 大局拉线 + 右侧属性抽屉。
 *  - 数据源：全局 DATA.npcs[id]（editor.js loadData 拉取）；抽屉编辑直接改内存对象。
 *  - 保存：显式点「保存」/Ctrl+S，直接提交 DATA.npcs[id] 全字段走 /api/editor/npc
 *    （后端校验不绕过）；坐标仍走防抖 /api/editor/npc-layout/<id> 独立通道。
 *  - 不做浏览器持久化：平移/缩放/面板刷新即复位；「开始对话」卡坐标与普通卡片一样
 *    存 npc_layouts.json（保留键 __start__）；设置偏好存 editor_settings.json。
 *
 * 依赖：window.Drawflow（vendored）、window.GraphModel（graph_model.js）、
 *       editor.js 的全局 DATA 与条件/效果控件构造器（addChoiceRow/addEffectRow 等）。
 */
(function () {
  "use strict";

  const G_COARSE = 28, G_FINE = 14;
  const GM = window.GraphModel;
  const CLICK_THRESHOLD = 4;   // 左键按下后位移 ≤4px 视为点击（开抽屉），超过则是拖卡
  const ID_PATTERN = /^[a-z0-9_]{1,32}$/;
  /* 「开始对话」合成卡在 npc_layouts.json 中的保留键：它虽不是对话节点，
   *  但和普通卡片一样持久化坐标（同一条防抖 layout 通道），不做任何特殊重置 */
  const START_CARD_ID = "__start__";

  /* 编辑器偏好（自动保存/收起键）：初始默认值先用于首屏，loadData 后由 editor.js
   * 用 editor_settings.json 的内容覆盖；设置弹窗的每次修改防抖 POST 回该文件。
   * 挂在 window 上供 scene_canvas.js 共享同一个自动保存开关，绝不使用浏览器存储。 */
  window.EditorSettings = window.EditorSettings || { collapse: "middle", autosave: false };
  const settings = window.EditorSettings;
  let settingsTimer = null;
  const saveSettings = () => {
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(() => {
      fetch("/api/editor/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collapse: settings.collapse, autosave: settings.autosave }),
      }).then(r => r.json()).then(res => {
        if (!res || !res.success) console.warn("编辑器设置保存失败：", res && res.message);
      }).catch(err => console.warn("编辑器设置保存失败：", err));
    }, 200);
  };
  const panButton = () => 2;                    // 平移键固定右键
  const collapseButton = () => settings.collapse === "right" ? 2 : 1;  // 右键/中键

  /* 编辑器设置弹窗（脚本加载即可用，不依赖画布挂载；所有改动当次会话立即生效） */
  function initSettingsModal() {
    const overlay = $("es-overlay");
    if (!overlay) return;
    const syncUI = () => {
      overlay.querySelectorAll('input[name="es-collapse"]').forEach(r =>
        { r.checked = r.value === settings.collapse; });
      $("es-autosave").checked = settings.autosave;
    };
    const close = () => overlay.classList.add("hidden");
    $("btn-editor-settings").addEventListener("click", () => { syncUI(); overlay.classList.remove("hidden"); });
    $("es-close").addEventListener("click", close);
    overlay.addEventListener("click", e => { if (e.target === overlay) close(); });
    overlay.querySelectorAll('input[name="es-collapse"]').forEach(r =>
      r.addEventListener("change", () => { settings.collapse = r.value; saveSettings(); }));
    $("es-autosave").addEventListener("change", e => {
      settings.autosave = e.target.checked; saveSettings();
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !overlay.classList.contains("hidden")) close();
    });
  }

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
    panning: null,          // 平移键按下时的平移状态 {x,y,cx,cy}
    pending: null,          // 左键按下候选点击 {dfId,x,y,moved}，mouseup 判定点击/拖卡
    collapsePending: null,  // 收起键候选 {x,y,moved}，未移动＝单击收起
    pendingPan: null,       // 收起键同时是平移键时的平移起点（待提升）
    startDrag: null,        // 开始卡拖动 {x,y,nodeX,nodeY}；null=未拖
    mounted: false,
    listeners: [],
    layoutTimer: null,
    renderTimer: null,
    saveTimer: null,
    selNode: null,          // 抽屉当前编辑的节点 id
    startDfId: null,        // 合成「开始对话」卡片的 drawflow id（不在 idMap/数据中）
    inspMode: "node",       // 抽屉当前模式：node（点节点）| entry（点开始卡片）
  };

  const COND_LABELS = {
    flag: "标志", has_item: "持有", enemy_killed: "已击败", gold_gte: "金币≥",
  };
  function condLabel(cond) {
    const k = cond && ["flag", "has_item", "enemy_killed", "gold_gte"].find(t => t in cond);
    return k ? `${COND_LABELS[k]} ${cond[k]}` : "";
  }

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
    document.body.classList.remove("workbench-scenes");
    $("cw-workbench").classList.remove("hidden");
    // 工具条切回对话树形态（世界地图画布会改这些共享控件的文案/可见性）
    $("cw-mode-label").textContent = "对话树";
    $("cw-st-npc-label").textContent = "对话";
    $("cw-st-nodes-label").textContent = "节点";
    $("cw-add").textContent = "＋ 卡片";
    $("cw-autolayout").classList.add("hidden");
    $("nci-edit-npc").classList.remove("hidden");
    if (prefId && DATA.npcs[prefId]) view.npcId = prefId;
    else if (!view.npcId || !DATA.npcs[view.npcId]) {
      view.npcId = Object.keys(DATA.npcs || {}).sort()[0] || null;
    }
    refreshNpcOptions();
    mount();
    render();
    syncStatus();
  }

  function exit() {
    if (view.mode === "list") return;
    // 自动保存开启时，离开工作台前把未落盘的内容静默存掉（不等待，避免卡住切换）
    if (view.dirty && settings.autosave) save({ silent: true });
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
    if (view.mounted) render();   // 实例不重建：平移/缩放作为会话内状态自然保留
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
    setPanel(true);                     // 每次进入默认展开属性栏（不做浏览器持久化）
    $("cw-snap").checked = view.snap;
    $("cw-grid").value = String(view.grid);
    box().style.setProperty("--nc-grid", view.grid + "px");
    centerOrigin();                     // 初始视图：(0,0) 格落在视口中心
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
    clearTimeout(view.layoutTimer);
    clearTimeout(view.renderTimer);
    clearTimeout(view.saveTimer);
    try { view.editor.destroy(); } catch (_) {}
    box().innerHTML = "";
    view.editor = null;
    view.mounted = false;
    view.idMap = {};
    view.multi.clear();
    view.group = null;
    view.panning = null;
    view.pending = null;
    view.startDfId = null;
    box().classList.remove("grabbing");
    box().classList.remove("panning");
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

  async function switchNpc(id) {
    if (id === view.npcId) return;
    if (view.dirty) {
      if (settings.autosave) {
        await save({ silent: true });   // 自动保存：切人前先落盘
      } else if (!confirm("当前对话有未保存的改动，切换 NPC 将丢弃它们。确定切换？")) {
        $("cw-npc").value = view.npcId || "";
        return;
      }
    }
    view.npcId = id;
    view.selNode = null;
    view.dirty = false;
    updateDirty();
    render();
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
    // 合成「开始对话」卡片：只有输出端口，连向所有入口
    renderStartCard(g, npc);
    view.rendering = false;
    fillNodeDatalist(npc);
    // 抽屉按当前模式保持
    if (view.inspMode === "entry") renderEntryInspector();
    else if (view.selNode && npc.nodes[view.selNode]) renderInspector();
    else showPlaceholder();
    syncStatus();
  }

  /** 开始对话卡片：位置与普通卡片一样取 npc_layouts.json（保留键 __start__），
   *  拖动保存、刷新/重进后原地恢复；从未摆过才默认放最左入口向左一列 */
  function renderStartCard(g, npc) {
    // 入口 = 默认入口 + 每条条件问候（graph_model 已并入 graph.entries）
    const entries = (g.entries || [])
      .map(en => ({ ...en, node: en.node }))
      .filter(en => npc.nodes && npc.nodes[en.node]);
    if (!entries.length) { view.startDfId = null; return; }
    // 找到各入口节点的画布坐标
    const refs = entries
      .map(en => g.nodes.find(n => n.id === en.node))
      .filter(Boolean);
    if (!refs.length) { view.startDfId = null; return; }
    // 用户摆过就用记录位置（与普通卡片完全同通道）；否则默认最左入口左侧一列
    const saved = curLayout()[START_CARD_ID];
    const startX = saved && Number.isFinite(saved.x) ? saved.x
      : Math.min(...refs.map(r => r.x)) - 280;
    const startY = saved && Number.isFinite(saved.y) ? saved.y : refs[0].y;

    const html =
      `<div class="nc-node-head start-head">▶ 开始对话</div>` +
      entries.map((en, i) =>
        `<div class="nc-entry-row" data-idx="${i}"><span class="t">${esc(en.node)}</span>` +
        `<span class="c">${en.cond ? esc(condLabel(en.cond)) : "默认"}</span></div>`).join("");
    const dfId = view.editor.addNode("__start__", 0, entries.length,
      snap(startX), snap(startY), "npc-node nc-start", {}, html);
    view.startDfId = dfId;
    // 端口定位须等该卡渲染后读 offsetTop（整批渲染中放一帧）；
    // 端口移动后必须重算连接路径，否则连线钉在默认 50% 位置（保存后重渲染必现）
    requestAnimationFrame(() => {
      if (!view.mounted || view.startDfId !== dfId) return;
      placeEntryPorts(dfId, entries.length);
      try { view.editor.updateConnectionNodes("node-" + dfId); } catch (_) {}
    });
    // 连线：入口 i → 对应节点 input_1；默认实线、条件虚线
    entries.forEach((en, i) => {
      const toDf = dfIdOf(en.node);
      if (toDf == null) return;
      view.editor.addConnection(dfId, toDf, `output_${i + 1}`, "input_1");
      const conns = box().querySelectorAll(".connection");
      const last = conns[conns.length - 1];
      if (last) last.classList.add(en.cond ? "entry-cond" : "entry-default");
    });
  }

  function placeEntryPorts(dfId, count) {
    const el = box().querySelector(`#node-${dfId}`);
    if (!el) return;
    [...el.querySelectorAll(".nc-entry-row")].forEach((row, i) => {
      if (i < count) el.style.setProperty(`--y${i + 1}`, row.offsetTop + row.offsetHeight / 2 + "px");
    });
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
      if (dfId === view.startDfId) {   // 开始卡不可删，重新渲染恢复
        render();
        return;
      }
      const id = view.idMap[dfId];
      const npc = curNpc();
      if (!id || !npc) return;
      delete npc.nodes[id];
      Object.values(npc.nodes).forEach(n => n.choices.forEach(c => {
        if (c.next === id) delete c.next;
      }));
      // 被删节点可能是入口：同步清理 greeting / greeting_rules 引用，避免保存被后端拦
      if ((npc.greeting || "") === id) {
        npc.greeting = Object.keys(npc.nodes)[0] || "greet";
      }
      npc.greeting_rules = (npc.greeting_rules || []).filter(r => r.node !== id);
      if (view.selNode === id) { view.selNode = null; showPlaceholder(); }
      markDirty(); render();
    });
    // 选中（mousedown 即派发）不立即弹抽屉：点击/拖卡的判定在 mouseup 做，
    // 避免拖一下卡片就弹出编辑栏。库的 .selected 高亮仍由它自己加。
    // 单卡拖动结束：吸附 + 保存坐标
    ed.on("nodeMoved", dfId => {
      if (dfId === view.startDfId) {
        // 开始卡与普通卡一样：吸附定位后走同一条防抖坐标保存（__start__ 保留键）
        const d = liveData()[dfId];
        d.pos_x = view.snap ? snap(d.pos_x) : Math.round(d.pos_x);
        d.pos_y = view.snap ? snap(d.pos_y) : Math.round(d.pos_y);
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
        ed.updateConnectionNodes("node-" + dfId);
        scheduleLayoutSave();
        return;
      }
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
    ed.on("translate", updateZoomLabel);
  }

  function markDirty() {
    view.dirty = true;
    updateDirty();
    if (settings.autosave) {
      clearTimeout(view.saveTimer);
      view.saveTimer = setTimeout(() => save({ silent: true }), 1500);
    }
  }
  function updateDirty() { $("cw-dirty").classList.toggle("hidden", !view.dirty); }

  /* ================= 节点属性抽屉 ================= */
  function selectNode(id) {
    if (view.selNode !== id) view.selNode = id;
    view.inspMode = "node";
    renderInspector();
  }

  function validateNodeId(newId) {
    const npc = curNpc();
    if (!newId) return "节点 ID 不能为空";
    if (!ID_PATTERN.test(newId)) return "只能用小写字母、数字、下划线，最长 32 位";
    if (npc.nodes[newId]) return "已存在同名节点";
    return null;
  }

  /** 节点改名：同步重映射全树 choices.next / greeting / greeting_rules / 布局坐标键 */
  function renameNode(oldId, newId) {
    const npc = curNpc();
    const rebuilt = {};
    for (const [k, v] of Object.entries(npc.nodes)) {
      rebuilt[k === oldId ? newId : k] = v;
      (v.choices || []).forEach(c => { if (c && c.next === oldId) c.next = newId; });
    }
    npc.nodes = rebuilt;
    if (npc.greeting === oldId) npc.greeting = newId;
    (npc.greeting_rules || []).forEach(r => { if (r.node === oldId) r.node = newId; });
    const lay = (DATA.layouts || {})[view.npcId];
    if (lay && lay[oldId]) { lay[newId] = lay[oldId]; delete lay[oldId]; }
    view.selNode = newId;
    markDirty();
    render();                            // 重建卡片/连线，并按 selNode 重开抽屉
    requestAnimationFrame(() => saveLayout());
  }

  function showPlaceholder() {
    view.inspMode = "none";
    $("nci-title").textContent = "节点属性";
    $("nci-body").innerHTML =
      '<div class="nci-empty">在画布上点选一张卡片进行编辑<br><br>点左侧「▶ 开始对话」可设置对话入口<br>拉线＝设置选项跳转</div>';
  }

  /* ================= 入口设置抽屉（点「开始对话」卡片） =================
   * 编辑 NPC 的 greeting（默认入口）+ greeting_rules（条件问候），
   * 复用列表表单的条件控件（editor.js 的 EditorShared）。 */
  function renderEntryInspector() {
    const npc = curNpc();
    if (!npc) { showPlaceholder(); return; }
    $("nci-title").textContent = "入口设置";
    const body = $("nci-body");
    body.innerHTML = "";

    const tip = ce("div");
    tip.className = "nci-empty";
    tip.style.cssText = "text-align:left;padding:.4rem .2rem;line-height:1.6";
    tip.innerHTML =
      "<b>「开始对话」</b>卡片是所有入口：<br>" +
      "· 绿色实线 ＝ 默认起始节点（无条件）<br>" +
      "· 橙色虚线 ＝ 条件问候（满足时作为起点，按顺序首条命中）";
    body.append(tip);

    // 默认起始节点
    const defLab = ce("label", "nci-sec");
    defLab.textContent = "默认起始节点";
    const defSel = ce("select");
    defSel.innerHTML = Object.keys(npc.nodes).map(id =>
      `<option value="${esc(id)}"${(npc.greeting || "greet") === id ? " selected" : ""}>${esc(id)}</option>`
    ).join("");
    defSel.addEventListener("change", () => {
      npc.greeting = defSel.value || "greet";
      markDirty();
      render();                       // 重画开始卡连线
    });
    body.append(defLab, defSel);

    // 条件问候规则
    const rulesLab = ce("label", "nci-sec");
    rulesLab.textContent = "条件问候（按顺序判断，首条命中作起始节点）";
    const rulesBox = ce("div");
    rulesBox.style.cssText = "display:flex;flex-direction:column;gap:6px";
    (npc.greeting_rules || []).forEach(r => addEntryRuleRow(rulesBox, r));
    const addBtn = ce("button", "cw-tb nci-add-rule");
    addBtn.type = "button";
    addBtn.textContent = "＋ 添加条件问候";
    addBtn.addEventListener("click", () => {
      addEntryRuleRow(rulesBox, null);
      commitEntryRules(npc, rulesBox);
      markDirty();
      refreshStartCard();             // 只重画开始卡，不重建抽屉（保住新行与焦点）
    });
    body.append(rulesLab, rulesBox, addBtn);
  }

  /** 轻量刷新「开始对话」卡（增删入口/改条件后调用），不触碰抽屉 */
  function refreshStartCard() {
    if (view.startDfId != null) {
      try { view.editor.removeNodeId("node-" + view.startDfId); } catch (_) {}
      view.startDfId = null;
    }
    const npc = curNpc();
    if (!npc) return;
    renderStartCard(GM.npcToGraph(npc, curLayout()), npc);
  }

  function addEntryRuleRow(container, rule) {
    const c = {
      opt: window.EditorShared?.COND_OPTIONS || EditorShared?.COND_OPTIONS || [],
      cfg: window.EditorShared?.COND_PARAM_CFG || EditorShared?.COND_PARAM_CFG || {},
    };
    const row = ce("div", "nci-rule-row");
    const typeSel = ce("select");
    const t = (() => { const r = (rule || {}).if; if (!r) return ""; return ["flag","has_item","enemy_killed","gold_gte"].find(k => k in r) || ""; })();
    typeSel.innerHTML = (c.opt.map ? c.opt.map(o =>
      `<option value="${o.v}"${o.v === t ? " selected" : ""}>${o.label}</option>`).join("") : "");
    // 条件参数：引用型（物品/敌人）用下拉列出全部候选，其余用输入框
    let paramInp = ce("input");
    paramInp.className = "nr-param";
    paramInp = configureParamInput(paramInp, c.cfg[t] || null);
    // 起始节点：下拉列出全部节点（同引用型）
    const targetSel = ce("select");
    targetSel.className = "nr-target";
    targetSel.innerHTML = refOptionsHtmlFromDl("npc-dl-nodes", (rule && rule.node) || "");
    targetSel.value = (rule && rule.node) || "";
    const del = ce("button", "ed-btn mini danger", "删除");
    del.type = "button";
    del.addEventListener("click", () => {
      row.remove();
      commitEntryRules(curNpc(), container);
      markDirty();
      refreshStartCard();
      renderEntryInspector();          // 重画规则列表（删除后行数已变）
    });

    const applyParamConfig = () => {
      const cfg = c.cfg[typeSel.value];
      paramInp = configureParamInput(paramInp, cfg || null);
      paramInp.value = "";
    };
    const commitAndRefresh = () => {
      commitEntryRules(curNpc(), container);
      markDirty();
      refreshStartCard();
    };
    typeSel.addEventListener("change", () => {
      applyParamConfig();
      commitAndRefresh();
    });
    [paramInp, targetSel].forEach(ctl =>
      ctl.addEventListener("change", commitAndRefresh));   // 失焦/选中时提交，避免打字掉焦点
    // 初次填充条件参数值
    applyParamConfig();
    if (t && (rule.if || {})[t] != null) paramInp.value = String(rule.if[t]);

    row.append("条件", typeSel, paramInp, "→ 起始", targetSel, del);
    container.appendChild(row);
  }

  /** 从抽屉规则行收集，回写 npc.greeting_rules */
  function commitEntryRules(npc, container) {
    const rules = [];
    container.querySelectorAll(".nci-rule-row").forEach(row => {
      const node = row.querySelector(".nr-target").value.trim();
      if (!node) return;
      const t = row.querySelector("select").value;
      const rule = { node };
      if (t) {
        const raw = row.querySelector(".nr-param").value.trim();
        rule.if = t === "gold_gte" ? { gold_gte: parseInt(raw, 10) } : { [t]: raw };
      }
      rules.push(rule);
    });
    npc.greeting_rules = rules;
  }

  function renderInspector() {
    const npc = curNpc();
    const node = npc && view.selNode ? npc.nodes[view.selNode] : null;
    if (!node) { showPlaceholder(); return; }
    $("nci-title").textContent = "节点 " + view.selNode;

    const body = $("nci-body");
    body.innerHTML = "";

    // ID 可改名：失焦/回车提交，自动重映射全树引用与布局坐标
    const idLab = ce("label", "nci-sec");
    idLab.textContent = "节点 ID（小写字母/数字/下划线，保存后改名即生效）";
    const idRow = ce("div", "nci-id-row");
    const idInput = ce("input");
    idInput.value = view.selNode;
    idInput.spellcheck = false;
    idInput.maxLength = 32;
    const idErr = ce("div", "nci-id-err hidden");
    const commitRename = () => {
      const newId = idInput.value.trim();
      idInput.classList.remove("invalid");
      idErr.classList.add("hidden");
      if (newId === view.selNode) { idInput.value = view.selNode; return; }
      const msg = validateNodeId(newId);
      if (msg) {
        idInput.classList.add("invalid");
        idErr.textContent = msg;
        idErr.classList.remove("hidden");
        idInput.value = view.selNode;
        return;
      }
      renameNode(view.selNode, newId);
    };
    idInput.addEventListener("change", commitRename);
    idInput.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); idInput.blur(); }
      if (e.key === "Escape") { idInput.value = view.selNode; idInput.blur(); }
    });
    idRow.append(idInput);

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

    body.append(idLab, idRow, idErr, textLab, ta, chLab, choicesBox, addChBtn, delBtn);
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
    // 「开始对话」合成卡与普通卡同等待遇：位置写保留键 __start__
    if (view.startDfId != null && exp[view.startDfId]) {
      const sd = exp[view.startDfId];
      layout[START_CARD_ID] = { x: snap(sd.pos_x), y: snap(sd.pos_y) };
    }
    try {
      await fetch(`/api/editor/npc-layout/${encodeURIComponent(view.npcId)}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(layout),
      });
      if (DATA.layouts) DATA.layouts[view.npcId] = layout;
    } catch (_) { /* 坐标保存失败不阻断编辑，下次拖动会重试 */ }
  }

  /* ================= 画布保存（直接提交 DATA 实体全字段） =================
   * silent=true：自动保存用，成功不弹提示、不重拉（服务端认可即内存为真） */
  async function save(opts) {
    const silent = !!(opts || {}).silent;
    clearTimeout(view.saveTimer);
    // 入口面板规则行若还在输入中（未失焦提交），保存前强制收集一次
    if (view.mounted && view.inspMode === "entry") {
      const npc0 = curNpc();
      const rulesBox = $("nci-body");
      if (npc0 && rulesBox && rulesBox.querySelectorAll(".nci-rule-row").length) {
        commitEntryRules(npc0, rulesBox);
      }
    }
    const npc = curNpc();
    if (!npc) { if (!silent) toast("请先在列表中新建并保存一个 NPC", "error"); return; }
    let data;
    try {
      const res = await fetch("/api/editor/npc", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(npc),
      });
      data = await res.json();
    } catch (_) {
      if (!silent) toast("保存失败：无法连接服务器", "error");
      return;
    }
    if (!data.success) { toast(data.message, "error"); return; }
    view.dirty = false;
    updateDirty();
    if (silent) return;
    await window.EditorActions.reloadData();   // 手动保存走统一重拉（保持画布模式）
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

  /* 统一鼠标状态机（一套监听，避免多套手势互相覆盖）：
     - 配置的平移键（右键/中键）：画布任意位置（卡片/连线/端口/空白）只平移，
       捕获阶段阻断 Drawflow，绝不选中或移动卡片；
     - 左键空白：框选（阻断库的空白平移）；
     - 左键卡片：候选点击，mouseup 时位移 ≤4px 才弹抽屉，超过就是拖卡；
     - 左键多选组：整组拖动；左键端口：交给库拉线（不干预）。 */
  function onPortOrWire(t) {
    return t.classList && (t.classList.contains("output") || t.classList.contains("input") ||
      t.classList.contains("main-path") || t.classList.contains("point") ||
      t.closest?.(".connection"));
  }

  function onDocMouseDown(e) {
    if (!box().contains(e.target)) return;

    // 左/中/右键统一进入候选状态；key 记录是哪种手势
    const btn = e.button;
    // 左键：交由下方卡片候选处理；中键：仅当它是收起键才接管；右键：平移候选或收起键
    if (btn === 2 || (btn === 1 && btn === collapseButton())) {
      // 平移键(右键)按下 → 先按"按下即平移"处理，但保留一次"单击收起"的机会
      e.preventDefault();
      e.stopPropagation();          // 阻断 Drawflow 的节点选中/拖动/删除钮
      if (btn === 1) {
        // 中键作为收起键：不引用平移，直接记收起候选
        view.collapsePending = { x: e.clientX, y: e.clientY, moved: false };
        return;
      }
      // 右键：若是收起键 → 记候选（未移动＝单击收起；移动＝平移）
      if (btn === collapseButton()) {
        view.collapsePending = { x: e.clientX, y: e.clientY, moved: false };
        view.pendingPan = {
          x: e.clientX, y: e.clientY,
          cx: view.editor.canvas_x, cy: view.editor.canvas_y,
        };
        box().classList.add("panning");
        return;
      }
      // 右键且不是收起键：纯平移
      view.panning = {
        x: e.clientX, y: e.clientY,
        cx: view.editor.canvas_x, cy: view.editor.canvas_y,
      };
      box().classList.add("panning");
      return;
    }
    if (btn !== 0) return;

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
      // 多选组拖动：交给我们自己，阻断库的单卡拖动
      e.stopPropagation();
      const members = new Map();
      view.multi.forEach(id => members.set(id, nodePos(id)));
      view.group = { members, lastX: e.clientX, lastY: e.clientY };
      box().classList.add("grabbing");
      return;
    }
    // 普通左键按下卡片（非端口）：记录候选，是否开抽屉等 mouseup 判定
    if (nodeEl && !onPortOrWire(e.target)) {
      view.pending = { dfId: +nodeEl.id.slice(5), x: e.clientX, y: e.clientY, moved: false };
      // 开始卡：我们自管拖动（阻断 drawflow，避免合成/内部事件不可靠），
      // 并预存拖动起点供 mousemove 直接移动
      if (nodeEl.id.slice(5) == view.startDfId) {
        e.stopPropagation();
        const d = liveData()[+nodeEl.id.slice(5)];
        view.startDrag = { x: e.clientX, y: e.clientY, nodeX: d.pos_x, nodeY: d.pos_y, moved: false };
      }
    }
  }

  function doPan(e) {
    const p = view.pendingPan || view.panning;
    const z = view.editor.zoom;
    view.editor.canvas_x = p.cx + (e.clientX - p.x);
    view.editor.canvas_y = p.cy + (e.clientY - p.y);
    view.editor.precanvas.style.transform =
      `translate(${view.editor.canvas_x}px, ${view.editor.canvas_y}px) scale(${z})`;
    updateZoomLabel();
  }

  function onMouseMove(e) {
    // 开始卡自管拖动：超过阈值才进入移动（未超＝仍可能是点击开面板）
    if (view.startDrag) {
      const dz = Math.hypot(e.clientX - view.startDrag.x, e.clientY - view.startDrag.y);
      if (!view.startDrag.moved && dz <= CLICK_THRESHOLD) return;
      view.startDrag.moved = true;
      if (view.pending) view.pending.moved = true;   // 已判定拖动，不再弹入口设置
      const z = view.editor.zoom;
      const nx = view.startDrag.nodeX + (e.clientX - view.startDrag.x) / z;
      const ny = view.startDrag.nodeY + (e.clientY - view.startDrag.y) / z;
      const d = liveData()[view.startDfId];
      if (d) {
        d.pos_x = nx; d.pos_y = ny;
        const el = box().querySelector(`#node-${view.startDfId}`);
        if (el) { el.style.left = nx + "px"; el.style.top = ny + "px"; }
        view.editor.updateConnectionNodes("node-" + view.startDfId);
      }
      return;
    }
    // 收起键按住且已移动超过阈值 → 判定为平移而非单击收起
    if (view.collapsePending && !view.collapsePending.moved &&
        Math.hypot(e.clientX - view.collapsePending.x, e.clientY - view.collapsePending.y) > CLICK_THRESHOLD) {
      view.collapsePending.moved = true;
      // 收起键同时是平移键时，把 pendingPan 提升为正式平移；否则中键只取消候选
      if (view.pendingPan && collapseButton() === 2) view.panning = view.pendingPan;
      view.pendingPan = null;
      if (!view.panning) view.collapsePending = null;   // 中键收起键不平移：直接结束候选
    }
    if (view.collapsePending && view.panning) { doPan(e); return; }
    if (view.panning) { doPan(e); return; }
    if (view.pending &&
        Math.hypot(e.clientX - view.pending.x, e.clientY - view.pending.y) > CLICK_THRESHOLD) {
      view.pending.moved = true;    // 已判定为拖卡，本回合不再弹抽屉
    }
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
    // 开始卡：未拖动＝点击开入口面板；已拖动＝吸附并记录位置
    if (view.startDrag) {
      const sd = view.startDrag;
      view.startDrag = null;
      view.pending = null;
      if (!sd.moved) {
        if (inspector().classList.contains("collapsed")) setPanel(true);
        view.inspMode = "entry";
        renderEntryInspector();
        return;
      }
      const dfId = view.startDfId;
      if (dfId != null) {
        const d = liveData()[dfId];
        d.pos_x = view.snap ? snap(d.pos_x) : Math.round(d.pos_x);
        d.pos_y = view.snap ? snap(d.pos_y) : Math.round(d.pos_y);
        const el = box().querySelector(`#node-${dfId}`);
        if (el) { el.style.left = d.pos_x + "px"; el.style.top = d.pos_y + "px"; }
        view.editor.updateConnectionNodes("node-" + dfId);
        scheduleLayoutSave();   // 与普通卡同通道持久化（__start__ 保留键）
      }
      return;
    }
    // 收起键单击（未移动）→ 切换属性栏；随后正常结束平移
    if (view.collapsePending) {
      const { moved } = view.collapsePending;
      view.collapsePending = null;
      view.pendingPan = null;
      if (!moved) {
        setPanel(inspector().classList.contains("collapsed"));
      }
    }
    // 平移结束：不选中任何东西
    if (view.panning) {
      view.panning = null;
      box().classList.remove("panning");
      updateCoord(e.clientX, e.clientY);
      return;
    }
    // 左键卡片点击（未拖动）→ 打开属性抽屉
    if (view.pending) {
      const { dfId, moved } = view.pending;
      view.pending = null;
      if (!moved) {
        // 「开始对话」合成卡 → 入口设置；普通节点 → 节点属性
        if (dfId === view.startDfId) {
          if (inspector().classList.contains("collapsed")) setPanel(true);
          view.inspMode = "entry";
          renderEntryInspector();
        } else {
          const id = view.idMap[dfId];
          if (id) {
            if (inspector().classList.contains("collapsed")) setPanel(true);
            selectNode(id);
          }
        }
      }
    }
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
    // 工作台区域内（画布/工具条/底栏/属性栏/编辑器顶栏）彻底接管右键菜单，
    // 必须在 document 捕获阶段拦截（库的 contextmenu 监听挂容器且注册更早）
    on(document, "contextmenu", e => {
      const hit = e.target.closest &&
        (e.target.closest("#cw-bar") || e.target.closest("#cw-status") ||
         e.target.closest("#nc-inspector") || e.target.closest(".ed-topbar") ||
         e.target.closest("#cw-canvas"));
      if (hit) {
        e.preventDefault();
        e.stopPropagation();
      }
    }, true);
    on(window, "blur", () => {
      view.dragBox = null; view.group = null; view.panning = null; view.pending = null;
      view.collapsePending = null; view.pendingPan = null;
      view.startDrag = null;
      marquee().style.display = "none";
      box().classList.remove("grabbing");
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

    $("cw-npc").onchange = e => switchNpc(e.target.value);
    $("cw-add").onclick = addNode;
    $("cw-save").onclick = () => save();   // 不能直接传 save（事件对象会被当成 opts.silent）
    $("cw-zin").onclick = () => view.editor.zoom_in();
    $("cw-zout").onclick = () => view.editor.zoom_out();
    $("cw-zreset").onclick = centerOrigin;   // 复位 = 100% 缩放且 (0,0) 格回中
    $("cw-panel-toggle").onclick = () =>
      setPanel(inspector().classList.contains("collapsed"));
    // 回列表编辑该 NPC 的基本信息/问候规则 → 改为弹窗修改 NPC 信息（不离开画布）
    $("nci-edit-npc").onclick = openNpcInfoModal;
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
    // 节点 ID 改名有自己的提交逻辑（失焦/回车），打字过程不应标脏或触发自动保存
    if (e.target.closest && e.target.closest(".nci-id-row")) return;
    const node = currentInspectorNode();
    const boxEl = $("nci-choices");
    if (!node || !boxEl) return;
    commitChoices(node, boxEl);
    markDirty();
    // 下一节点改动影响连线（重绘）；其余只刷新卡片摘要，不抢输入焦点
    if (e.target.closest && e.target.closest(".nc-next")) scheduleRender();
    else updateCardSummary(view.selNode);
  }

  // 顶栏「编辑器设置」按钮始终可用（列表/画布模式都可打开）
  initSettingsModal();

  /* ================= NPC 信息编辑弹窗（不离开画布） ================= */
  function openNpcInfoModal() {
    const npc = curNpc();
    if (!npc) { toast("请先选择要编辑的 NPC", "error"); return; }
    $("npc-edit-id").value = npc.id;
    $("npc-edit-name").value = npc.name || "";
    const sceneSel = $("npc-edit-scene");
    sceneSel.innerHTML = Object.keys(DATA.scenes || {}).sort().map(sid =>
      `<option value="${esc(sid)}"${npc.scene_id === sid ? " selected" : ""}>${
        esc((DATA.scenes[sid] || {}).name || sid)}（${esc(sid)}）</option>`).join("");
    $("npc-edit-overlay").classList.remove("hidden");
  }
  function closeNpcInfoModal() { $("npc-edit-overlay").classList.add("hidden"); }
  function bindNpcInfoModal() {
    $("npc-edit-cancel").addEventListener("click", closeNpcInfoModal);
    $("npc-edit-overlay").addEventListener("click", e => { if (e.target === $("npc-edit-overlay")) closeNpcInfoModal(); });
    $("npc-edit-save").addEventListener("click", async () => {
      const npc = curNpc();
      if (!npc) { closeNpcInfoModal(); return; }
      npc.name = $("npc-edit-name").value.trim();
      npc.scene_id = $("npc-edit-scene").value;
      if (!npc.name) { toast("名称不能为空", "error"); return; }
      closeNpcInfoModal();
      await save();                      // 走统一保存（提交整实体、后端校验）
    });
  }
  bindNpcInfoModal();
})();
