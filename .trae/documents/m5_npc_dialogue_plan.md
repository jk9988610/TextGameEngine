# M5：NPC 与对话编辑 实施计划

## Repository Research（现状结论）

**对话引擎** [npc_system.py](file:///d:/TextGameEngine/engine/npc_system.py)：
- NPC 数据已在 `game_data/npc_dialogues.json`（id/name/scene_id/greeting/greetings/nodes），节点选项支持 `next` + `requires_item`，但：
  - `_pick_greeting`（52-62 行）把条件**写死**为 `cave_goblin` 击杀、`rusty_key` 在背包；
  - 选项没有任何 **effect**——莉娅"最大 HP +5"是纯文本假效果；
  - 条件只有 requires_item 一种。
- 引擎模块间互不直接调用，跨系统动作都在 app.py 路由层编排（保持此约定）。

**状态与存档**：game_state 无通用 flags；[save_manager.py](file:///d:/TextGameEngine/engine/save_manager.py) 读档时用 defaults 并集补字段，加 `flags: {}` 默认值即可让老存档自动兼容；reset 命令在 [console_handler.py](file:///d:/TextGameEngine/engine/console_handler.py) 122-146 行。

**编辑器**：三页签（地点/物品/敌人）+ 左侧列表 + 右侧表单，[editor_manager.py](file:///d:/TextGameEngine/engine/editor_manager.py) 已有成熟的校验/原子写/.bak/引用保护模式可直接复刻；`_file_map` 加 npcs 即可接第四种数据。

**前端**：对话弹窗逻辑在 [game.js](file:///d:/TextGameEngine/static/js/game.js) 177-257 行；选项推进后会 `fetchState()` 刷新，效果引起的背包/HP 变化能自动反映。

## 数据结构设计（npc_dialogues.json 新格式）

```json
{
  "npc_x": {
    "id": "npc_x", "name": "商人", "scene_id": "tavern",
    "greeting": "greet",
    "greeting_rules": [
      {"if": {"enemy_killed": "cave_goblin"}, "node": "hero_welcome"},
      {"if": {"has_item": "rusty_key"}, "node": "has_key"}
    ],
    "nodes": {
      "greet": {
        "text": "……",
        "choices": [
          {"text": "接受祝福", "next": "blessing_done",
           "if": {"flag": "quest_done"},
           "effects": [{"type": "max_hp", "amount": 5}]}
        ]
      }
    }
  }
}
```

- **条件库（v1）**：`flag`（可带 value）、`has_item`、`enemy_killed`、`gold_gte`；纯读 state，抽成共享函数供问候规则和选项 `if` 复用。
- **效果库（v1，引擎通用，不含具体内容）**：
  | type | 参数 | 行为 |
  |---|---|---|
  | set_flag | flag, value(默认 true) | 写通用 flags |
  | give_item | item | 复用 item_system（货币物品自动折算金币） |
  | remove_item | item | 任务收物（没有也不报错） |
  | heal | amount | 回血，不超上限 |
  | max_hp | amount | 上限与当前 HP 同增减（莉娅祝福成真） |
  | gold | amount | 金币增减（编辑器限制非负） |
  | teleport | scene | 复用 scene_manager.teleport |
  | start_combat | enemy | 结束对话并开战 |
- 效果挂在**选项**上（点击该选项时触发），v1 不做节点进入效果。
- 兼容：引擎继续认旧 `requires_item`（只读不写）；旧 `greetings` 映射不保留，随数据文件一并迁移（开发期，无外部游戏）。

## Files and Modules

**引擎**
- 新增 [engine/effects.py](file:///d:/TextGameEngine/engine/effects.py)：`condition_matches(state, cond)` 条件求值 + `EffectExecutor`（持有 game_data/game_state/scene_manager/item_system/combat_system，`apply(effects) -> {messages, combat_started}`）
- [engine/npc_system.py](file:///d:/TextGameEngine/engine/npc_system.py)：问候改走 `greeting_rules`；选项过滤同时检查 `if`；`select_choice` 返回待执行的 `effects`（不跨模块调用，由路由层执行）；旧 requires_item 仍认
- [engine/session_manager.py](file:///d:/TextGameEngine/engine/session_manager.py)：game_state + defaults 加 `flags: {}`；装配 EffectExecutor 为 `engines["effects"]`；新增 `live_dialogue_npcs()`
- [engine/console_handler.py](file:///d:/TextGameEngine/engine/console_handler.py)：reset 清 flags；新增 `set_flag <名> [值]` 控制台命令（QA/调试用）
- [engine/save_manager.py](file:///d:/TextGameEngine/engine/save_manager.py)：FALLBACK_DEFAULTS 加 `flags: {}`（老存档自动补全）
- [engine/editor_manager.py](file:///d:/TextGameEngine/engine/editor_manager.py)：file_map 加 `npcs: npc_dialogues.json`；新增 `upsert_npc`/`delete_npc`（含全套校验，见下）
- [app.py](file:///d:/TextGameEngine/app.py)：editor/data 带 npcs；新增 POST/DELETE `/api/editor/npc`；对话 POST 后执行 effects（start_combat 时先 end_dialogue），响应附 `effect_messages`

**编辑器校验规则**（upsert_npc）
- ID 正则、名称非空；scene_id 必须存在；greeting 节点必须存在
- 节点：ID 合法、text 非空；选项 text 非空；next 必须指向本 NPC 已有节点或为空（结束）
- requires_item / has_item 物品必须存在；enemy_killed 敌人必须存在；gold_gte 非负整数
- 效果参数：set_flag 的 flag 非空；give/remove_item 物品存在；heal 1~999999；max_hp 编辑器限 1~999；gold ≥0；teleport 场景存在；start_combat 敌人存在
- 警告：从 greeting + greeting_rules 起点 BFS 不可达的节点

**数据迁移** [game_data/npc_dialogues.json](file:///d:/TextGameEngine/game_data/npc_dialogues.json)
- 醉汉：`greetings.after_kill` → `greeting_rules: [{enemy_killed cave_goblin → hero_welcome}]`
- 莉娅：has_key / after_kill 两条规则；祝福选项加 `effects: [{type: max_hp, amount: 5}]`；requires_item 选项迁到 `if.has_item`

**前端编辑器**
- [editor.html](file:///d:/TextGameEngine/static/editor.html)：加「NPC」页签与 `#form-npc`：基本字段（ID/名称/所在地点下拉）+ 问候规则行 + 对话节点卡片列表
- [editor.js](file:///d:/TextGameEngine/static/js/editor.js)：NPC 列表/表单/节点与选项的动态增删；选项行 = 文本 + 下一节点下拉 + 条件（无/物品/标志/击败敌人/金币）+ 效果行（类型下拉驱动参数控件：物品/地点/敌人用下拉，数值用数字框）；新建 NPC 自带 greet 节点
- [editor.css](file:///d:/TextGameEngine/static/editor.css)：节点卡片/选项行/效果行样式（内部滚动，沿用 .ed-* 命名）
- 说明：对话树采用讨论过的**列表式卡片**（非节点连线图）。与原讨论的小调整：NPC 属性与对话节点合并在**一个「NPC」页签**内（而非两个页签），避免跨页签维护"当前选中 NPC"状态，一张表单就是一场完整对话
- [game.js](file:///d:/TextGameEngine/static/js/game.js)：selectDialogueChoice 收到 effect_messages 时在操作提示条展示

**QA**
- [qa/tge_api.py](file:///d:/TextGameEngine/qa/tge_api.py)：snapshot/restore 支持 npcs（还原顺序 scenes→items→enemies→npcs，删除顺序 npcs 最先）；editor_save 支持 npc
- 新增 `qa/m5_npc_dialogue.py`（见 Validation）

## Implementation Steps（依赖顺序）

1. engine/effects.py：条件求值 + EffectExecutor
2. state 接线：flags（session_manager/save_manager/console reset + set_flag 命令）
3. npc_system 改造（greeting_rules / if / 返回 effects）
4. app.py：效果执行编排 + npc 编辑器路由
5. editor_manager：npc CRUD + 校验
6. 迁移 npc_dialogues.json（醉汉/莉娅，祝福真效果）
7. 编辑器前端：NPC 页签 + 节点/选项/效果编辑
8. game.js 效果消息展示
9. QA 框架扩展 + m5 套件；py_compile；重启；全量回归
10. 浏览器抽查 + 清理 + 记忆更新

## Dependencies and Considerations

- 老存档：靠 normalize 补 `flags`，无需手动迁移存档
- 热更新：NPC 系统每次调用实时读 GAME_DATA["npcs"]，保存即对存活会话生效；删除正在对话的 NPC 由 `live_dialogue_npcs()` 保护
- 效果跨系统：遵循现有"模块不互调、路由编排"的约定；teleport 会发 SCENE_ENTER 自动结束对话；start_combat 效果由 app 先 end_dialogue
- 节点改名会导致选项引用悬空 → 后端校验报明确错误，v1 不做自动改名联动
- 击杀敌人沿用 killed_enemies 列表（enemy_killed 条件直接读），不强制写 flag

## Validation

- `py_compile` 全部改动的引擎文件；重启 Flask
- `python -m qa.run_all`：M3/M4 保持全绿
- 新增 `qa/m5_npc_dialogue.py` 覆盖：
  - N1 editor 数据含 npcs
  - N2 新建 qa_ NPC（多节点/多选项）字段往返
  - N3 校验：悬空 next、缺名称/地点、不存在物品/敌人/场景、非法效果参数、不可达节点警告
  - N4 删除保护：对话中拒绝、结束后可删
  - N5 greeting_rules：flag / has_item / enemy_killed 三种起点
  - N6 选项 if 条件隐藏导致索引变化；requires_item 旧格式仍生效
  - N7 效果：set_flag/give_item（含货币折算）/remove_item/heal/max_hp+5/gold/teleport/start_combat（对话结束、战斗激活）
  - N8 回归：莉娅祝福后 max_hp=55；醉汉杀哥布林后走 hero_welcome
- 浏览器：编辑器打开两个存量 NPC 回填正确；新建一个带效果的 NPC 并试玩闭环；Console 无报错
- 收尾：QA 数据自动还原、qa_ 存档/.bak 清零

## Risks

- 节点编辑器 JS 量较大（约 300+ 行动态表单）→ 严格按现有 renderOptionGroups 模式分函数（renderNodeCards/renderChoiceRow/renderEffectRow），避免嵌套失控
- 效果执行时序（对话跳转 vs 开战/传送）→ 路由层固定顺序：先处理选项导航→执行效果→start_combat/teleport 时确保对话已结束，QA N7 专门断言
- 编辑器误改存量两个 NPC → 快照还原 + N8 回归双保险
