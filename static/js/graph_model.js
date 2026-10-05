/* graph_model.js —— NPC 对话数据 ⇄ 画布图模型的纯转换层
 *
 * 设计原则（对应 npc_canvas_editor_plan.md）：
 *  - 纯函数，无 DOM、无 Drawflow、无网络依赖；可被 QA 直接 require/eval 测试。
 *  - 游戏内容（characters.json 里贴了 talkable 的角色的对话树 nodes）不含坐标；坐标来自独立 layout。
 *  - 画布编辑结构（增删节点、改 next）后用 graphToNodes 合并，
 *    节点上未在画布编辑的字段（text/if/effects/requires_item）原样保留。
 *
 * 暴露：window.GraphModel（浏览器）；module.exports（Node QA，若有 module 对象）。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.GraphModel = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  /* 默认布局：以画布原点 (0,0)（视口中心）为起点，新节点按 BFS 顺序蛇形阶梯摆放；
   *  单位像素，由画布层再吸附网格 */
  const DEFAULT_X = 0, DEFAULT_Y = 0;
  const COL_DX = 420, ROW_DY = 168;

  /**
   * npcToGraph：NPC 对象 + 布局 → 画布节点/边
   * @param {object} npc    DATA.npcs[id]，含 greeting/greeting_rules/nodes
   * @param {object} layout {nodeId:{x,y}}，缺坐标的节点给默认位置
   * @returns {{nodes:Array, edges:Array, entries:Array}}
   *   node:  {id,x,y,text,choices(原 choices 引用), degreeOut}
   *   edge:  {from,to,choiceIdx,text,if,effects}  一条非空 next 一条边
   *   entries: [{node, cond}]  默认入口 + 条件问候（供画布画虚边/高亮，本函数只给数据）
   */
  function npcToGraph(npc, layout) {
    const nodes = {};
    const nodesSrc = (npc && npc.nodes) || {};
    const lay = layout || {};
    let auto = 0;
    for (const [id, n] of Object.entries(nodesSrc)) {
      const pos = lay[id];
      nodes[id] = {
        id,
        x: pos && Number.isFinite(pos.x) ? pos.x : DEFAULT_X + (auto % 2) * COL_DX,
        y: pos && Number.isFinite(pos.y) ? pos.y : DEFAULT_Y + Math.floor(auto / 2) * ROW_DY,
        text: n.text || "",
        choices: Array.isArray(n.choices) ? n.choices : [],
      };
      auto++;
    }
    const edges = [];
    for (const [id, n] of Object.entries(nodes)) {
      n.choices.forEach((c, i) => {
        if (c && c.next) {
          edges.push({
            from: id, to: c.next, choiceIdx: i,
            text: c.text || "", if: c.if || null,
            effects: Array.isArray(c.effects) ? c.effects : [],
          });
        }
      });
    }
    const entries = [{ node: (npc && npc.greeting) || "greet", cond: null }];
    ((npc && npc.greeting_rules) || []).forEach(r => {
      if (r && r.node) entries.push({ node: r.node, cond: r.if || null });
    });
    return {
      nodes: Object.values(nodes),
      edges,
      entries,
    };
  }

  /**
   * applyGraphStructure：把画布上的结构（节点集合 + 选项 next）合并回 nodes。
   * 只接受画布当前实际存在的节点；对每个节点：
   *  - 保留旧节点上同序号选项的 text/if/effects（画布不改这些），只覆盖 next
   *  - 画布新增的选项（旧的没有）给 {text:""}
   *  - 删除画布上已不存在的选项
   * @param {object} prevNodes  编辑前的 npc.nodes（用于保留未编辑字段）
   * @param {object} structure  {nodeIds:string[], nexts:{nodeId: (string|null)[]}}
   *                  nexts[nodeId][i] = 该节点第 i 个选项指向的节点 id 或 null
   * @returns {object} 新的 nodes 对象
   */
  function applyGraphStructure(prevNodes, structure) {
    const out = {};
    for (const id of structure.nodeIds) {
      const prev = prevNodes[id] || { text: "", choices: [] };
      const nexts = structure.nexts[id] || [];
      const prevChoices = Array.isArray(prev.choices) ? prev.choices : [];
      const choices = nexts.map((next, i) => {
        const old = prevChoices[i] || {};
        const c = { text: old.text || "" };
        // 旧条件/效果原样保留（这些在列表表单编辑）
        if (old.requires_item) c.requires_item = old.requires_item;
        if (old.if) c.if = old.if;
        if (Array.isArray(old.effects) && old.effects.length) c.effects = old.effects;
        if (next) c.next = next;
        return c;
      });
      out[id] = { text: prev.text || "", choices };
    }
    return out;
  }

  /** 从当前 npc.nodes 直接导出结构（用于打开画布时初始化 nexts） */
  function structureFromNodes(nodes) {
    const nodeIds = Object.keys(nodes || {});
    const nexts = {};
    for (const id of nodeIds) {
      nexts[id] = (nodes[id].choices || []).map(c => (c && c.next) || null);
    }
    return { nodeIds, nexts };
  }

  /**
   * pruneLayout：剔除布局里已不在 nodeIds 中的节点坐标。
   * 保留键 "__start__" 是「开始对话」合成卡的坐标（不是对话节点，但同样持久化）。
   * @returns {{layout:object, removed:string[]}}
   */
  function pruneLayout(layout, nodeIds) {
    const keep = new Set(nodeIds);
    keep.add("__start__");
    const out = {}, removed = [];
    for (const [id, pos] of Object.entries(layout || {})) {
      if (keep.has(id)) out[id] = pos; else removed.push(id);
    }
    return { layout: out, removed };
  }

  /**
   * reachable：从所有入口 BFS 能到达的节点 id 集合（画布做不可达灰显，校验仍在后端）
   */
  function reachable(graph) {
    const adj = {};
    graph.nodes.forEach(n => { adj[n.id] = []; });
    graph.edges.forEach(e => { if (adj[e.from]) adj[e.from].push(e.to); });
    const seen = new Set();
    const queue = graph.entries.map(en => en.node).filter(id => adj[id] !== undefined);
    while (queue.length) {
      const id = queue.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      (adj[id] || []).forEach(n => queue.push(n));
    }
    return seen;
  }

  /** danglingEdges：next 指向不存在节点的边（{from,choiceIdx,to}） */
  function danglingEdges(graph) {
    const ids = new Set(graph.nodes.map(n => n.id));
    return graph.edges.filter(e => !ids.has(e.to));
  }

  return {
    npcToGraph,
    applyGraphStructure,
    structureFromNodes,
    pruneLayout,
    reachable,
    danglingEdges,
    constants: { DEFAULT_X, DEFAULT_Y, COL_DX, ROW_DY },
  };
});
