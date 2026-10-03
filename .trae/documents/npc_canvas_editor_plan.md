# NPC 对话画布编辑器 正式接入 实施计划

## Repository Research

现状（基于 editor.js / editor.html / app.py）：
- NPC 表单视图：`editor.html #form-npc`（基本信息 + 条件问候 + `#npc-nodes` 节点卡片 + 保存钮）。
- 数据入口：`loadData()` 一次性 GET `/api/editor/data` → 全局 `DATA`（含 `DATA.npcs`）。
- 数据出口：`saveNpc()` → `collectNpcPayload()` **从表单 DOM 收集** → POST `/api/editor/npc` → 成功后 `loadData()` 重拉。
- 选中/回填：`showForm('npcs',id)` → `renderNpcForm(npc)` 渲染卡片（`addNodeCard/addChoiceRow/renderRuleRow`）。
- 后端校验/热更新/引用保护均已完备（M5），画布不改后端游戏数据格式。

demo 已验证的成熟资产（`static/demo_drawflow.html`）：
- vendored Drawflow：`static/demo-lib/drawflow.min.js/.css`（单文件、无构建）。
- 网格吸附（28/14）、统一卡宽、拉线=选项 next、框选多选、整组拖动连线跟随、
  空白取消、右键平移、缩放、VS Code 式顶栏+可收纳侧栏。

关键约束与设计决策：
1. **游戏 JSON 不含坐标**：`npc_dialogues.json` 的 nodes 维持 `{text, choices}`，
   运行时引擎完全不感知布局。手动摆放坐标独立存放（见下"布局存储"）。
2. **表单不删、画布是并列视图**：同一个 NPC 上提供「列表 / 画布」切换，
   两者编辑同一个内存 NPC 对象；保存仍只走现有 `/api/editor/npc`。
3. 不做 `collectNpcPayload 读 DOM` 与画布互相拼凑 DOM 的脆弱方案。
   本期让画布视图独立成区：进入画布时从**当前内存数据**（已保存的 DATA.npcs[id]，
   或新建未保存时给出提示）渲染；画布上的结构改动（增删节点、拉线改 next）直接改内存对象，
   保存时画布把 nodes 合并回一次提交。节点台词/选项文字/条件/效果的**精细编辑仍在列表表单**做
   （画布 tooltip 上提供"在列表中编辑此节点"入口），避免在画布上重复实现 M5 的全部表单控件。

## 布局存储（坐标不进游戏 JSON）

- 新增数据文件 `game_data/npc_layouts.json`：`{ "<npc_id>": { "<node_id>": {"x":N,"y":N} } }`。
- 后端 `editor_manager.py` 增加 `load_layouts()/save_layout(id, layout)` 与原子写（复用现有写盘锁）。
- 路由（`app.py`，仅本地、`_editor_guard`）：
  - `GET  /api/editor/npc-layout`        全量布局（画布进入时拉取）
  - `POST /api/editor/npc-layout/<id>`   保存单个 NPC 的节点坐标 `{node_id:{x,y}}`
- 校验：只接受 `{string:{x:int,y:int}}`，数值限定合理范围（如 -10000~10000），非法丢弃；
  NPC/节点不存在不报错（布局是辅助数据，节点被删后布局成为孤儿，保存该 NPC 时顺带清理）。
- 布局保存与游戏内容保存**分离**：拖完卡片即时防抖保存布局（不触发 NPC 校验）；
  游戏内容（台词/选项/条件/效果）仍由显式「保存」按钮走 `/api/editor/npc`。

## Files and Modules

- `static/lib/drawflow.min.js`、`static/lib/drawflow.min.css`：从 demo-lib 移到正式 lib 目录（vendored，加来源注释）。
- `static/css/editor.css`：新增画布容器/顶栏/底栏/侧栏/网格样式（从 demo 迁移 .drawflow*、#npc-canvas* 等，统一 .ed-* 命名空间前缀，避免与现有样式冲突）。
- `static/editor.html`：
  - `#form-npc` 的"对话节点"区头部加视图切换：`[列表] [画布]` 两个小按钮 + 一个画布容器 `#npc-canvas-wrap`（默认 hidden，列表默认）。
  - 引入 drawflow 的 js/css 与新脚本。
- `static/js/graph_model.js`（**新增，纯函数无 DOM 依赖，可被 QA 单测**）：
  - `npcToGraph(npc, layout)` → `{nodes:[{id,x,y,text,choiceCount}], edges:[{from,to,choiceIdx,label,hasCond,hasEff}]}`
  - `graphToNodes(graph, prevNodes)` → 合并出 nodes（保留画布未编辑的 text/if/effects，只按结构增删/改 next）
  - `mergeLayout(npc, graphLayout)` → 清理孤儿坐标
  - BFS 可达性、悬空 next 等纯计算（只做展示辅助，校验仍在后端）。
- `static/js/npc_canvas.js`（**新增**，画布控制器，持有一个 Drawflow 实例）：
  - `openCanvas(npcId)` / `closeCanvas()`：挂载/卸载；从 `DATA.npcs[id]` + 布局渲染。
  - 节点/连线的增删与改 next 直接更新 `DATA.npcs[id].nodes`（内存）；
    结构变更后在画布顶栏显示「有未保存的内容改动」点保存才提交 `/api/editor/npc`；
    纯坐标变更防抖（800ms）提交 `/api/editor/npc-layout/<id>`。
  - 迁移 demo 的：网格吸附、框选多选、整组拖动+连线跟随、空白取消、缩放、右键平移。
  - 卡片上提供「✎ 在列表编辑」按钮 → 切回列表视图并滚动/高亮对应 `.npc-card`。
  - 侧栏 JSON 面板：去掉（正式编辑器里由列表表单承担精细编辑，不再需要手改 JSON，降低双写风险）。
  - 新建但**尚未保存**的 NPC：画布按钮禁用并提示"请先在列表中填写并保存一次 NPC"。
- `engine/editor_manager.py`：新增布局文件映射 `_file_map["layouts"]`、`load_layouts`、`upsert_layout`（含清洗与校验）；`.bak` 机制自动覆盖布局文件。
- `app.py`：`/api/editor/data` 附带 `layouts`；新增上述两个 npc-layout 路由；保存 NPC 成功后不强制重布局。
- `qa/tge_api.py`：增加 `layout_save/layout_get` 动词；snapshot/restore 增加 layouts（六份数据）。
- `qa/m7_npc_canvas.py`（**新增**套件，见 Validation）。

## Implementation Steps

1. 后端：布局文件读写+校验+路由（`editor_manager.py`、`app.py`）；py_compile、重启；先写 API 冒烟。
2. vendored 库落位 `static/lib/`；graph_model.js 纯函数先行（**Layout 层零 DOM**，历史教训：布局/渲染必须分层）。
3. editor.html / editor.css：视图切换按钮 + 画布容器骨架（列表默认、画布 hidden）。
4. npc_canvas.js 最小挂载：open/close + 只读渲染（graph_model → Drawflow），验证与现有 NPC 数据一致。
5. 迁移交互：网格吸附、框选、整组拖动、拉线改 next、删除节点、空白取消、缩放平移、端口对齐（全部从 demo 搬，改数据源为 DATA.npcs）。
6. 双视图同步：画布改结构 → 内存对象 + 未保存标记；切回列表时 `renderNpcForm` 重渲染同一对象；列表保存后刷新画布。
7. 坐标防抖保存到 npc-layout；孤儿布局清理；「在列表编辑」联动。
8. QA 框架扩展 + m7 套件；全量回归；浏览器端到端由人工按清单走（不再用合成点击测视觉）。

## Dependencies and Considerations

- Drawflow 活动数据路径 `editor.drawflow.drawflow.Home.data`；连线重绘要传 `"node-"+id`；
  `export()` 是深拷贝只能读；事件回调直接收 detail（demo 已踩过并固化）。
- 一个 input 收多线 vs 对话多入边：沿用 demo 现状（input 唯一，多入边视觉汇聚已可接受），
  本期不改库行为。
- 画布只在 NPC 页签挂载一个实例，切换 NPC/页签时 destroy 重建，避免多实例串话。
- 热更新边界：布局坐标立即生效无妨（仅坐标）；nodes 内容改动仍需新会话才影响游戏运行（与 M5/M6 一致，非本期范围）。
- 在线模式 `_editor_guard` 对新路由同样生效。
- 布局文件缺失/损坏：按空布局处理，画布用网格默认位置兜底，不阻断编辑。

## Validation（QA + 人工，不再用代理测画面）

新增 `qa/m7_npc_canvas.py`（建议用例）：
- G1 布局 CRUD：保存坐标→重拉一致；非法 x/y 被拒；清理不存在节点的孤儿坐标。
- G2 graph_model 纯函数：npcToGraph 边数=所有非空 next 数；graphToNodes 合并后 text/if/effects 不丢；
  悬空 next、可达集合结果与 M5 后端口径一致。
- G3 拉线即改：在画布把 A 选项连到 B → 提交 `/api/editor/npc` → 重新 GET data，nodes 里 next=B；
  后端原有校验（悬空节点等）对画布提交同样生效。
- G4 坐标与内容分离：拖动只产生 npc-layout 请求，不改 npc_dialogues.json；
  改台词只产生 npc 请求，不产生 layout 请求。
- G5 双视图一致：画布增节点 → 切列表能看到；列表改 next 保存 → 回画布连线更新。
- G6 回归：M3~M6 全量保持绿；snapshot/restore 覆盖第六份数据 layouts，无 .bak/存档残留。

人工走查清单（交给用户，不做合成点击）：
- 打开莉娅画布，节点/条件问候/祝福支线位置与连线正确；拖卡吸附、整组拖线跟随、
  拉线/删线、框选、右键平移、缩放、抽屉侧栏；刷新后布局保留、游戏 JSON 不含 x/y。

## Risks

- **双视图状态不同步**（最高风险）：收口到"同一个内存 DATA.npcs[id] 对象 + 切换时重渲染"，
  禁止画布直接拼表单 DOM；保存唯一出口 `/api/editor/npc`。
- Drawflow 与编辑器现有事件冲突：画布用独立容器和独立实例，挂载时才初始化、卸载时移除监听；
  全局快捷键（如 Ctrl+B）加命名空间判断，仅在画布视图生效。
- 布局文件成为新的需要快照还原的数据：已纳入 qa snapshot/restore 第六份，遗漏会污染后续套件。
- 库升级/替换：graph_model 与 Drawflow 隔离，将来换库只换 npc_canvas.js 的画布层。
