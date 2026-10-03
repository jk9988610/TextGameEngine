# 画布工作台化（第一步：NPC 对话画布外壳对齐 demo）实施计划

## 背景与决策（已与用户确认）

- 终态方向：**画布是展现大局的主编辑器，表单是点选节点后的属性栏**（蓝图式工作台）。
- 本期只做第一步：顶栏「列表编辑 | 画布编辑」**双模式切换** + 画布工作台外壳对齐
  `static/demo_drawflow.html`（通栏 + 全屏画布 + 右侧可收纳属性抽屉 + 底栏状态栏）。
- 不在本期：世界图层（地点卡片/出口拉线）、NPC 钻取、敌人改名「对战」界面归组、
  数据层 actor 合并、物品配方图。这些是后续阶段，本计划不实现，但结构要为它们留位置。
- 参照文件：`static/demo_drawflow.html`（外壳：48 顶栏 / 340 抽屉 Ctrl+B / 34 状态栏 /
  多 NPC 切换 / 每 NPC 独立视图持久化）。正式版与 demo 的区别：数据走 API 不走 localStorage，
  抽屉放**真实表单控件**而非 JSON 文本框。

## Repository Research（现状）

- 顶栏 `header.ed-topbar`（44px，含返回游戏/游戏设置/试玩）；主体 `.ed-main` =
  240px `.ed-sidebar`（页签+列表）+ `.ed-form-panel`（表单，NPC 表单 id=`form-npc`）。
- 现有画布：NPC 表单内嵌 `#npc-canvas-wrap`（560px 高），上一步刚加的 `body.canvas-open`
  全屏 overlay + 内嵌工具栏（含返回列表/保存）。本期用**工作台外壳替换该方案**：
  画布不再属于 NPC 表单，编辑器有列表/画布两个平级工作台。
- 画布控制器 `static/js/npc_canvas.js`：单 Drawflow 实例、拉线/删线/增删节点直接改
  `DATA.npcs[id].nodes`、坐标 800ms 防抖 POST `/api/editor/npc-layout/<id>`、
  `mergeForSave` 是为"保存走列表 DOM"打的补丁。本期改为**画布模式直接保存 DATA 实体**后，
  该补丁随之删除。
- 转换层 `static/js/graph_model.js` 纯函数无 DOM，本期不动。
- 保存出口现状：`saveNpc()` 从列表 DOM `collectNpcPayload()`；后端
  `POST /api/editor/npc` 接受完整 NPC 对象并做全部校验（悬空 next/引用/ID 正则），
  直接提交 `DATA.npcs[id]` 同样满足契约（M7 G3 已验证）。
- demo 已验证：单实例多图切换（NPC 下拉）、每 NPC 视图（平移/缩放）localStorage 持久化、
  ☰/Ctrl+B 抽屉、状态栏网格坐标/缩放。

## 目标交互

1. 顶栏新增一组分段开关 `[列表编辑 | 画布编辑]`，任何页签下都可切到画布；
   切到画布 = `body.workbench-canvas`：隐藏 `.ed-main`，显示画布工作台；切回反之。
2. 画布工作台布局（参照 demo）：
   - 现有 ed-topbar（44px，分段开关放在这里）；
   - 其下 44px 画布工具条：NPC 选择下拉（所有 NPC，含"＋新建 NPC"入口）、
     ＋卡片、保存、缩放三键、☰ 抽屉开关；
   - 右侧 340px 属性抽屉 `#nc-inspector`（可 Ctrl+B / ☰ 滑出收纳，状态记忆）；
   - 34px 底栏：当前 NPC、鼠标格点坐标、缩放%、吸附开关、网格精度（28/14）、dirty 提示。
   - 画布区夹在工具条与底栏之间铺满，抽屉为覆盖式（收纳后画布占满，无留白/双滚动条）。
3. 点画布节点 → 抽屉显示该节点的真实编辑控件（数据双向绑定到内存对象）：
   - 节点 ID（只读，改名涉及全树 next 重映射，本期不做，列表里仍可改）；
   - 台词 textarea；
   - 选项行列表：选项文字、下一节点（datalist 下拉）、显示条件（类型+参数）、
     效果（复用现有 addEffectRow 控件与 build/collect 逻辑）、删选项、＋添加选项；
   - 删除节点（二次确认；后端删除保护/校验不变）。
   - 抽屉顶部「编辑 NPC 基本信息/问候规则」按钮 → 切回列表模式并选中该 NPC
     （基本字段与 greeting_rules 本期仍在列表表单编辑，避免表单搬家的大改）。
4. 抽屉里的编辑直接改 `DATA.npcs[id].nodes`：文字类改动即时刷新卡片摘要；
   next/条件/选项数等结构改动后重渲染画布（沿用现有 render() 防抖/全量重渲染模式）；
   顶部「保存」按钮提交（或 Ctrl+S），成功后 loadData + onSaved，清 dirty。
5. NPC 切换：下拉切换前若 dirty 弹 confirm；切换后按该 NPC 数据渲染、恢复其视图
   （平移/缩放，localStorage key `tge_canvas_views`，仅本机视图偏好）。
6. 坐标仍走 `/api/editor/npc-layout/<id>` 防抖独立保存（不变）。

## Files and Modules

- `static/editor.html`：
  - ed-topbar 加分段开关 `#mode-list / #mode-canvas`；
  - 删除旧 `#npc-canvas-wrap` 内嵌块，新增工作台骨架：
    `#cw-bar`（工具条）、`#cw-canvas`（Drawflow 挂载点）、`#cw-marquee`、
    `#nc-inspector`（抽屉，内含节点控件容器）、`#cw-status`（底栏）。
- `static/editor.css`：
  - 删除 `body.canvas-open` 旧 overlay 规则与内嵌 `.nc-canvas-wrap/toolbar` 布局；
  - 新增 body.workbench-canvas 布局（.ed-main 隐藏；工具条/画布/抽屉/底栏 fixed 定位，
    参照 demo 数值）；抽屉 collapsed transform、底栏、分段开关样式；
  - 节点/端口/框选/多选样式沿用现有 .nc-canvas 规则（选择器从 #npc-canvas 改为 #cw-canvas）；
  - 框选矩形保持 position:fixed（clientX/Y 视口坐标）。
- `static/js/npc_canvas.js`（主要改动）：
  - 控制器升级为工作台：模式切换（被 editor.js 顶栏调用）、NPC 下拉填充/切换、
    挂载/卸载（模式级单实例：进画布 mount、出画布 destroy+清 DOM）；
  - 新增 inspector 模块：nodeSelected 填充控件、控件 input/change → 改内存对象 →
    局部摘要更新或 render()；选项/效果控件复用 editor.js 暴露的构造器
    （addChoiceRow/addEffectRow/buildCond/buildEffect 需挂到 window 或抽共享小模块，
    优先 window 暴露，避免大搬家）；
  - 保存：画布模式直接 POST `DATA.npcs[view.npcId]`（含 id/name/scene_id/greeting/
    greeting_rules/nodes 全字段），成功 toast + loadData + 清 dirty；
  - 视图持久化：每 NPC 的 canvas_x/y/zoom 存 localStorage；
  - 底栏坐标/缩放监听（demo 已验证公式）。
- `static/js/editor.js`：
  - 顶栏模式开关事件；`switchTab/showEmpty/openConfig` 不再需要 exit 画布（模式独立），
    但「游戏设置」打开时若在画布模式需切回列表（两个全屏体互斥）；
  - 删除 saveNpc 里的 `NpcCanvas.mergeForSave` 补丁与 mergeForSave 本身；
  - 暴露抽屉复用的小构造器（addChoiceRow 等）或抽出共享函数；
  - loadData 后若处于画布模式则刷新下拉并重渲染（不强制跳回列表）。
- `static/js/graph_model.js`：不改。
- QA：后端接口零变化，`qa/m7_npc_canvas.py` 与全量回归应继续全绿；
  抽屉/拉线/视觉走人工清单（不做合成点击）。

## Implementation Steps

1. editor.html/css：顶栏分段开关 + 工作台骨架与布局（先放静态内容，沿用 demo 尺寸）。
2. npc_canvas.js 模式化：进/出工作台的 mount/destroy、NPC 下拉与切换、视图持久化；
   把现有拉线/框选/网格/坐标保存迁到新挂载点。
3. 节点属性抽屉：nodeSelected 绑定；台词/选项/条件/效果控件接内存对象；
   增删选项后端口与连线重渲染；删除节点确认。
4. 保存链路改直存 DATA 实体（Ctrl+S + 按钮）；删除 mergeForSave 补丁；
   dirty 管理与切换 NPC confirm。
5. 底栏状态（坐标/缩放/吸附/精度/dirty）、抽屉 ☰/Ctrl+B 与记忆；
   「编辑 NPC 基本信息」回跳列表。
6. 清理旧内嵌标记/样式；浏览器人工走查；跑 m7 与 run_all 回归。

## Dependencies and Considerations

- 抽屉控件与 editor.js 表单共用构造逻辑：用 window 暴露最小集合，不重构表单文件，
  控制本期 diff。
- Drawflow `nodeSelected` 在拖动后也会触发：抽屉填充是幂等的（读对象重绘控件），
  但要避免用户在抽屉输入时被重绘夺权——仅在选中 id 变化时重建控件，同 id 只刷新摘要。
- 抽屉改了未保存就切 NPC/切模式：confirm 拦截；未保存内容只存在内存，落盘唯一出口保存钮。
- 节点 ID 只读：避免改名引发的 next 全量重映射（列表表单仍保留改名能力，后端校验兜底）。
- 世界图层是下一阶段：工具条的 NPC 下拉将来升级为「世界 / NPC：xx」面包屑，
  本期命名/布局预留位置即可，不提前实现。
- 在线模式冻结不变；编辑器接口守卫不变；坐标与内容双通道不变。

## Validation

- 人工走查（浏览器，http://127.0.0.1:5000/static/editor.html）：
  1. 顶栏切画布：工作台铺满（工具条下、底栏上、无白边/双滚动条），切回列表正常；
  2. NPC 下拉切换、每 NPC 视图（平移/缩放）记忆、刷新后仍在画布模式且布局保留；
  3. 点节点出抽屉：改台词卡片即时变；改 next 连线跟随；增删选项端口/连线正确；
     条件/效果下拉可用；删节点 confirm；
  4. 抽屉 ☰/Ctrl+B 收纳，画布占满；偏好记忆；
  5. 画布保存：成功 toast、dirty 消除、重拉数据一致；未保存切 NPC 有 confirm；
     拖坐标只发 npc-layout，游戏 JSON 无 x/y；
  6. 「编辑 NPC 基本信息」回列表并选中；游戏设置与画布模式互斥；
  7. 控制台无报错。
- 自动：`python -m qa.m7_npc_canvas` 12 条 + `python -m qa.run_all` 173 条全绿；
  git status 确认 npc_dialogues.json 无意外脏写。

## Risks

- **抽屉与表单双份编辑逻辑分叉**：靠复用 editor.js 的条件/效果构造器与 build 函数收敛，
  不在抽屉里重写一套。
- **工作台单实例生命周期**：沿用 destroy+清容器做法，防止多 Drawflow 实例/监听残留；
  模式切换是唯一挂载点。
- **直存 DATA 实体的字段完整性**：DATA 来自 /api/editor/data 的 npcs（含全字段），
  画布只改 nodes，提交体形状与列表保存一致；G3 已有同形请求的后端验证。
- 旧全屏 overlay 代码删除后，上一步的 mergeForSave 必须同步删净，避免死代码误导。
