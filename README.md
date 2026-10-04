# TextGameEngine 文字游戏引擎

Flask + 原生 HTML/CSS/JS（零前端框架、零构建步骤）的**点击式文字游戏引擎**。
引擎逻辑与游戏数据严格分离：场景、物品、敌人、NPC 对话全部是 JSON，既能手改，也能用内置的可视化编辑器制作。

- 单机离线游玩：多槽存档 + 自动存档，存本地 SQLite
- 可视化编辑：地点 / 物品 / 敌人 / NPC 对话树（列表 + 拉线画布双视图）/ 商店，即改即玩
- 部署形态：本地 venv 直接跑，也可 Gunicorn + Nginx 部署（`deploy/`）

---

## 1. 快速开始（Windows / Python 3.13）

```powershell
.\.venv\Scripts\python.exe app.py
# 浏览器访问（必须走 http，双击 file:// 打开会连不上 /api）
#   游戏：   http://127.0.0.1:5000/static/index.html
#   编辑器： http://127.0.0.1:5000/static/editor.html
```

游戏内开发者控制台：底栏「控制台」按钮或按 `~`。常用命令：
`reset`（新游戏）、`teleport <场景id>`、`add_item <物品id>`、`set_flag <名> [值]`、`help`。

---

## 2. 目录结构

```
app.py                  # Flask 路由层：只做编排，不含游戏规则
engine/                 # 引擎（与具体游戏内容无关）
  session_manager.py    # 每玩家一套引擎实例（见第 5 节：多人遗产，勿再扩展）
  scene_manager.py      # 场景/出口/锁
  item_system.py        # 背包、拾取、货币折算、消耗品
  shop_system.py        # 无限供应商店
  combat_system.py      # 回合制战斗、战斗喝药、金币掉落
  npc_system.py         # 对话树：条件问候、选项条件（效果交路由层执行）
  effects.py            # 通用条件 + 效果库（M5 起所有"规则"都走这里）
  event_rules.py        # 声明式事件（7 类触发器，条件/效果复用 effects.py）
  console_handler.py    # 开发者控制台
  save_manager.py       # SQLite 多槽存档（读档自动补全新字段）
  editor_manager.py     # 编辑器数据校验/原子写/.bak/引用保护
  auth_manager.py       # 在线模式账号（在线已屏蔽，见第 5 节）
game_data/              # 游戏数据（JSON，全部可配）
  game_config.json      # 初始场景/背包/金币/玩家属性/操作耗时/event_rules
  scenes.json items.json enemies.json npc_dialogues.json
  npc_layouts.json      # 对话画布的节点坐标（仅布局，非游戏内容；M7）
  offline_saves.db      # 离线存档（运行后生成）
static/                 # 前端：index.html + editor.html + js/*（原生 JS）
  lib/drawflow.*        # 对话画布 vendored 库（零构建）
qa/                     # 标准库 HTTP 回归测试（见第 4 节）
deploy/                 # 阿里云生产部署文件（Gunicorn/Nginx/systemd）
```

---

## 3. 游戏数据模型（改内容前必读）

| 文件 | 结构要点 |
|---|---|
| `scenes.json` | 场景含 `exits` / `locked_exits` / `items_here` / `shop_items:[{item_id,price}]` / `enemies_here` |
| `items.json` | 普通物品；`is_weapon+damage` 武器；`usable+heal` 消耗品；`currency_value` 货币（拾取即折算金币，不进背包） |
| `enemies.json` | `hp/attack/defense/reward_items/reward_gold`（金币直接入账，物品掉地上需拾取） |
| `npc_dialogues.json` | `greeting` + `greeting_rules:[{if,node}]` 起点；`nodes` 节点树；选项可挂 `if` 条件与 `effects` 效果 |
| `npc_layouts.json` | **仅画布坐标** `{npc_id:{node_id:{x,y}}}`，运行时引擎完全不读；节点增删后自动清孤儿坐标 |
| `game_config.json` | 一切初始值：游戏标题/简介、初始场景/背包/金币/玩家属性；`event_rules` 事件规则 |

**条件**（effects.py）：`{flag}` / `{has_item}` / `{enemy_killed}` / `{gold_gte}`。
**效果**：`set_flag` / `give_item` / `remove_item` / `heal` / `max_hp` / `gold` / `teleport` / `start_combat` / `unlock`（对话选项和事件规则共用同一效果库）。
玩家状态里的 `flags: {}` 是通用剧情标志，reset 清空，老存档读档自动补全。

**事件规则**（config.event_rules，编辑器「事件」页签）：
`{id, label, on, if, when?, do}`。
- `on` 触发器 7 类：`ITEM_TAKEN` / `ITEM_USED` / `ITEM_BOUGHT` / `ENEMY_KILLED` /
  `SCENE_ENTER` / `SCENE_LEAVE` / `NPC_TALK`
- `if` 事件参数全等过滤（如指定 item_id/scene_id/enemy_id/npc_id+node_id；地点类留空=任意地点）
- `when` 可选，世界状态条件（同上条件库）；和 `if` 是 AND
- ⚠️ **规则在游戏会话创建时装配一次，编辑器改规则只对新开游戏生效（reset 不重建规则，
  要新开会话）**；编辑器保存时也有此提示。控制台 `add_item` 发的 ITEM_TAKEN 带
  `from_scene="__console__"`，不会命中限定场景的拾取规则——这是特性不是 bug。

战斗公式：玩家命中 = 武器 damage − 敌防；敌人反击 = max(1, 敌攻 − 玩家防)。
（注意：玩家自身的 attack 属性当前不参与命中，别按它算预期伤害。）

**NPC 对话画布**（编辑器顶栏「列表编辑 | 画布编辑」双工作台，蓝图式）：
- 列表工作台 = 原五页签表单；画布工作台 = 全屏画布 + 右侧 340px 属性抽屉（☰/Ctrl+B 收纳）
  + 底栏状态栏（当前 NPC/节点数/格点坐标/缩放/吸附）。在列表选中 NPC 后切画布会跟随该 NPC。
- 点画布节点 → 抽屉直接编辑台词/选项/显示条件/效果（与列表共用同一套控件构造器）；
  拉线 = 选项 `next`，＋卡片 = 增节点；画布模式点「保存」/Ctrl+S 直接提交
  `DATA.npcs[id]` 全字段走 `/api/editor/npc`（后端校验不绕过）；未保存切 NPC 有确认。
- NPC 基本信息/问候规则仍在列表表单（抽屉「NPC 设置」一键回跳）；**节点 ID 在抽屉里可直接改名**
  （失焦/回车提交，自动重映射全树 choices.next/greeting/greeting_rules 和布局坐标；
  列表表单里已有节点 ID 只读，避免漏改引用）。
- 左键行为：点卡片（位移≤4px）才弹出属性抽屉，拖动卡片只移动不弹窗；空白拖框多选；
  右缘蓝点拉线；滚轮缩放；28·14 网格吸附。卡片上没有删除按钮，删节点在抽屉里做
  （连线仍可点选后用红叉删除）。
- 顶栏「编辑器设置」（本机偏好，改完立即生效）：**拖动画布的按键**选鼠标右键或中键——
  该键在画布任意位置（卡片/连线/空白）都只平移画布，不选中不拖卡；
  **自动保存**开关（默认关，勾选后停顿 1.5s 静默保存当前 NPC）。
- **「▶ 开始对话」合成卡片**：画布最左侧一张只有右侧输出端口的卡片（与 LEAVE 只有左侧输入
  口对称），从它引出该 NPC 的所有对话入口——绿色实线＝默认起始节点，橙色虚线＝每条条件
  问候（行内显示条件文本）。点它打开「入口设置」抽屉：改默认起始节点、增删条件问候
  （条件＋目标节点，复用列表表单的条件控件）；删除节点时会自动清理对入口的引用。
  该卡不进数据（npc_dialogues.json/npc_layouts.json），位置随入口自动排布、不可拖、不可删。
- 拖动坐标走**独立通道**防抖存 `/api/editor/npc-layout/<id>` → `npc_layouts.json`，
  游戏 JSON 永远不含 x/y；每 NPC 的平移/缩放与编辑器设置都在 localStorage（本机偏好）。
- 前端分层：`js/graph_model.js`（数据⇄图模型纯函数，零 DOM）+ `js/npc_canvas.js`（工作台控制器），
  换库只换后者。规划中：世界图层（地点+出口拉线）、场景→NPC 钻取、敌人改名「对战」界面归组。

里程碑：M1 引擎去游戏化 → M2 地点/物品编辑器 → M3 金币/商店/喝药 → M4 敌人编辑 →
M5 NPC/对话/条件效果 → M6 事件规则/开局配置 → M7 NPC 对话画布。
后续规划：战斗深化（技能/防御/逃跑/多敌人/经验）、限量商店、任务日志 UI。

---

## 4. 测试：`qa/` 包（仅 Python 标准库，无第三方依赖）

```powershell
.\.venv\Scripts\python.exe -m qa.run_all          # 全部回归（M3~M7，173 条）
.\.venv\Scripts\python.exe -m qa.m7_npc_canvas    # 单跑一个里程碑
```

- 新里程碑：新建 `qa/m7_xxx.py`，提供 `SUITE` 名和 `run(r)`（套件内自建 GameClient）即可被自动发现。
- 共享层 `qa/tge_api.py`：`GameClient`（自动带 client_id/mode、UTF-8 JSON、reset/teleport/give/set_flag/move/combat/dialogue/editor_*/config/event-rule/layout/game-info 等动词）、`editor_snapshot/editor_restore`（场景/物品/敌人/NPC/config/**layouts** 六份数据自动还原）。
- 画布的视觉/交互（拉线、框选、拖卡）不走合成点击，按 M7 计划文档的人工清单走查；
  `graph_model.js` 纯函数可在浏览器控制台直接断言（本机无 Node 运行时）。
- 安全约定：测试实体一律 `qa_` 前缀；套件结束自动清 qa_ 存档与 game_data/*.bak；跑前确认 `app.py` 已启动。
- 可用 `TGE_BASE=https://...` 指向其他服务器跑冒烟测试。

---

## 5. ⚠️ 在线模式（多人）已屏蔽 —— 重要

**2026-10-03 起，在线模式入口已从模式选择页隐藏**（index.html 卡片 `display:none` +
`js/ui.js` 的 `chooseMode('online')` 直接拦截）。后端 auth/在线存档/双模式路由代码全部保留，未删除。

背景：`SessionManager` 多会话隔离、Flask session 双隔离键（离线 client_id / 在线 user_id）、
在线离线存档分轨、切换模式清 session——**这一整套复杂度都是为多人同场服务的**。
项目早期在隔离逻辑上耗费了大量排查时间（见第 6 节前几条），而当前没有多人需求，
因此冻结，避免后续开发继续为它分心或把它改坏。

给后续 AI / 开发者的硬约束：

- **唯一受支持的游戏路径是离线模式**；QA 也只测离线（`mode=offline`）。
- 不要为在线模式新增功能、适配、分支判断或测试；不要"顺手重构"隔离层。
- 改动 `/api/state`、存档、会话相关代码时，知道它们在多实例隔离下运行即可，按现有模式写。
- 需要重新开放多人时，先做整体规划（隔离键、存档归属、在线时编辑器 403 策略、测试矩阵），
  再按 `static/index.html` 中在线卡片上方注释的三步放开。

---

## 6. 避坑清单（每条都是实际踩过的）

### 开发环境 / Windows / PowerShell

1. **重启后端要杀干净残留进程**：`StopCommand` 停掉后台作业后 python.exe 可能仍占着 5000 端口；
   `SO_REUSEADDR` 允许多进程同绑端口，请求会随机命中新旧代码（症状：同一接口新旧逻辑混杂、新路由 404）。
   重启后必须用 `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -like '*app.py*' }`
   确认只剩一个实例，再启动。
2. **PowerShell 5.1 编码坑**：`Invoke-RestMethod` 发字符串 body 默认 ISO-8859-1，中文会变 `?` 落盘，
   写 JSON 必须用 `[System.Text.Encoding]::UTF8.GetBytes($json)` + `charset=utf-8`；
   无 BOM 的 .ps1 按 GBK 解析，中文注释会让脚本挂掉。→ 自动化测试已改用 Python（`qa/`），不要再写 PS 测试脚本。
3. PowerShell 里跑内联 `python -c "..."` 的引号会被宿主吃掉，含中文/引号的临时代码写成临时 .py 文件执行。

### 引擎语义

4. **热更新是"内存 dict 原地改"**：编辑器保存后对新会话立即生效；**已存在会话的旧场景快照不动**
   （items_here/锁/enemies），改完要 `reset` 才看到。新加场景由 `SM.refresh_new_scenes()` 补状态键。
   **例外：事件规则只在会话创建时注册一次**，改规则必须新开会话（reset 不够）；
   开局配置改了对 reset 立即生效（reset 实时读 config）。
5. **`/api/state` 的 `npcs_here` / `enemies_here` 在响应顶层**，不在 `scene` 里；
   `scene.shop_items` 是 join 后的 `{id,name,price}`（编辑器存的是 `{item_id,price}`），断言别取错键。
6. **对话/战斗不自动触发**：进场景只渲染按钮，玩家点「和 XX 说话/挑战」才开始；
   玩家和敌人都保留残血、跨场景保留（战斗对象按当前场景的 enemies_here 判断 active，不在同场景则 inactive 但不清战）。
7. **编辑器删除保护**：场景被出口引用/有玩家在场不能删；物品被放置/在售/敌人掉落/初始背包引用不能删；
   敌人被场景引用或正被战斗不能删；NPC 正被对话不能删。QA 改编辑器数据必须走快照还原，
   删除新增实体的顺序是 NPC→敌人→物品→场景（引用关系反向）。
8. 对话选项的效果**不在 npc_system 里执行**（引擎模块互不调用），由 `app.py` 路由层统一
   `effects.apply()`；`start_combat` 效果要强制结束对话，`teleport` 靠 SCENE_ENTER 事件结束对话。
9. 玩家死亡：回酒馆满血复活、清战斗，无惩罚。

### 前端

10. fetch 拦截器自动给所有请求加 `client_id`（localStorage 里的稳定 UUID）与 `mode`。
11. 战斗面板的 HP/药水按钮只在 `renderCombat` 时渲染；直接用 fetch 调 API 绕过前端点击链路时
    弹窗不会出现（做 UI 验证要走真实点击，或只做静态渲染链路审查）。
12. 浏览器自动化代理：长 async IIFE 容易报 `GUEST_VIEW_MANAGER_CALL` 失败，拆成单行短脚本多次 evaluate；
    代理的物理点击曾出现落点不准的假失败，关键断言优先用 `browser_evaluate` 读 DOM。
13. UI 约定：深灰毛玻璃风、弹窗 `position:fixed` flex 居中（动画不能含 translate，会抖动）、
    不用 emoji、一屏自适应内部滚动；游戏内不显示"换模式"按钮。

### 协作约定

14. 不主动 `git commit`；不主动同步阿里云部署；不主动建 .md 文档（用户明确要求时才写，如本文件）。
15. 模式选择页三卡：离线 / ~~在线（已屏蔽）~~ / 编辑；编辑器顶栏「返回游戏」回到模式选择。

---

## 7. 生产部署（当前不主动更新，仅供参考）

阿里云 Ubuntu：venv 隔离依赖，Gunicorn **单 worker**（多进程会破坏内存态会话），
Nginx 反代，systemd 管理；`SECRET_KEY` 环境变量注入；`ONLINE_MODE=online` 时编辑器写接口全部 403。
文件见 `deploy/`。注意在线模式既已屏蔽，重新部署前应确认开放范围。
