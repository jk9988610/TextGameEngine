# M6：事件规则编辑 + 开局配置向导 实施计划

## Repository Research（现状结论）

**规则引擎** [event_rules.py](file:///d:/TextGameEngine/engine/event_rules.py)：
- `DeclarativeRules(game_state).register(bus, rules)`，规则格式 `{on, if(事件参数全等), do:[unlock]}`；效果只有 unlock 一种，且只持有 game_state。
- 注册时机：[session_manager.py:136-141](file:///d:/TextGameEngine/engine/session_manager.py) 创建会话时注册一次。**规则热更只对新会话生效**（旧会话 bus 上挂旧回调），QA 必须注意"先建规则、后产生游戏请求"。
- `/api/editor/data` 不调用 `_get_engines()`，不会创建游戏会话——QA 建规则阶段不产生会话。

**M5 已有可复用资产**：[effects.py](file:///d:/TextGameEngine/engine/effects.py) 的 `condition_matches`（flag/has_item/enemy_killed/gold_gte）+ `EffectExecutor`（8 种效果，已按会话装配为 `engines["effects"]`）；编辑器前端已有条件行/效果行组件。

**事件总线实际发布点**（grep 实证）：
- `SCENE_ENTER(scene_id)` / `SCENE_LEAVE(scene_id)`（移动与 teleport 都发；新会话在初始场景也发一次）
- `ITEM_TAKEN(item_id, from_scene)`（控制台 add_item 的 from_scene=`__console__`）
- `ITEM_USED(item_id, target_id)`、`ITEM_DROPPED`、`ITEM_BOUGHT(item_id, price, from_scene)`（商店已发，常量未登记）
- `NPC_TALK(npc_id, node_id)`、`BATTLE_START(enemy_id)`、`COMBAT_DEATH(dead=enemy_id|"player")`
- **缺一个语义干净的击杀事件**：M6 在敌人击杀分支补发 `ENEMY_KILLED(enemy_id=...)`（不与玩家死亡混用）。

**开局配置** [game_config.json](file:///d:/TextGameEngine/game_config/game_config.json)：initial_scene/initial_inventory/initial_gold/player/action_time/event_rules；无标题/简介。
- config 被 GAME_DATA 引用，console reset 每次实时读 → 改 config 对 reset 立即生效；新会话初始值也读它。
- SaveManager 的 defaults 在启动时由 `SM.initial_state_defaults()` 固化一次（仅用于老存档补缺失字段），需要一个刷新入口。
- 标题写死在 [index.html](file:///d:/TextGameEngine/static/index.html) 三处：`<title>`、模式层 `<h2>`、顶栏 `<h1>`；[main.js](file:///d:/TextGameEngine/static/js/main.js) initApp 目前只拉 /api/auth/me。

**编辑器**：四页签左列表右表单；event_rules 目前只能手改 JSON。`_file_map` 可加 config 走同一套原子写/.bak/加锁。

## 数据结构设计

**game_config.json 新增**：`game_title`（字符串，默认"文字游戏引擎"）、`game_intro`（可空简介）。

**event_rules 规则新格式**（旧字段全保留，加 id/label/when）：
```json
{
  "id": "rusty_key_unlocks_cave",
  "label": "拿到锈钥匙 → 解锁洞穴门",
  "on": "ITEM_TAKEN",
  "if": { "item_id": "rusty_key", "from_scene": "forest" },
  "when": { "flag": "..." },
  "do": [ { "type": "unlock", "scene": "forest", "exit": "cave" } ]
}
```
- `if`：事件参数全等过滤（语义不变，保证旧规则兼容）。
- `when`：**可选**世界状态条件，复用 M5 condition_matches；与 if 是 AND。
- `do`：效果统一走 M5 EffectExecutor；**unlock 并入效果库**成为第 9 种效果（参数 scene+exit），DeclarativeRules 不再自带效果实现。

**M6 支持的触发器（v1）**：

| on | 必填参数（if 键） | 可选参数 |
|---|---|---|
| ITEM_TAKEN | item_id（物品下拉） | from_scene（场景下拉） |
| ITEM_USED | item_id | — |
| ITEM_BOUGHT | item_id | from_scene |
| ENEMY_KILLED（新事件） | enemy_id（敌人下拉） | — |
| SCENE_ENTER | scene_id（场景下拉） | — |
| SCENE_LEAVE | scene_id | — |
| NPC_TALK | npc_id（NPC 下拉） | node_id |

## Files and Modules

**引擎**
- [engine/effects.py](file:///d:/TextGameEngine/engine/effects.py)：EffectExecutor 加 `unlock` 效果（操作 scene_lock_states，只依赖 state）；加 `condition_matches(cond)` 实例方法委托模块函数
- [engine/event_rules.py](file:///d:/TextGameEngine/engine/event_rules.py)：`DeclarativeRules(executor)` 持有 EffectExecutor；handler = 事件参数全等 `if` + 世界条件 `when` 都满足才 `executor.apply(do)`；删除自带 unlock 实现
- [engine/event_bus.py](file:///d:/TextGameEngine/engine/event_bus.py)：GameEvents 补 `ENEMY_KILLED`、`ITEM_BOUGHT` 常量
- [engine/combat_system.py](file:///d:/TextGameEngine/engine/combat_system.py)：敌人击杀分支（COMBAT_DEATH 敌人分支处）补发 `ENEMY_KILLED(enemy_id=...)`
- [engine/session_manager.py](file:///d:/TextGameEngine/engine/session_manager.py)：改为 `DeclarativeRules(effect_executor).register(...)`
- [engine/save_manager.py](file:///d:/TextGameEngine/engine/save_manager.py)：加 `update_defaults(d)`
- [engine/editor_manager.py](file:///d:/TextGameEngine/engine/editor_manager.py)：
  - `_file_map` 加 `"config": "game_config.json"`
  - `upsert_config(config, payload)`：校验后写盘并**原地更新**传入 config dict
  - 规则 CRUD：`upsert_event_rule(config, scenes/items/enemies/npcs, payload)` / `delete_event_rule(config, rule_id)`（操作 config["event_rules"]，整体原子写）
- [app.py](file:///d:/TextGameEngine/app.py)：
  - 新增 `GET /api/game-info`（无需会话，返回 title/intro）
  - `/api/editor/data` 附加 config
  - `POST /api/editor/config`（保存后 `SM_SAVE.update_defaults(SM.initial_state_defaults())`）
  - `POST /api/editor/event-rule`、`DELETE /api/editor/event-rule/<id>`（同样 `_editor_guard()`）

**校验规则**
- config：title 非空 ≤40；intro ≤200；initial_scene 存在；initial_inventory 物品都存在；initial_gold 非负整数；player.hp 1~999999、attack/defense 0~999999
- 事件规则：id 正则、label 可空、on 必须在七类内；按 on 校验必填参数与引用（物品/敌人/场景/NPC 存在；NPC_TALK 的 node_id 若非空必须是该 NPC 节点）；when 复用 `_condition_errors`；do 复用 `_effect_errors` + unlock 校验（scene 存在且 exit 在该场景 exits 中）

**数据迁移** [game_config.json](file:///d:/TextGameEngine/game_config/game_config.json)：加 `game_title/game_intro`；现有钥匙规则补 `id:"rusty_key_unlocks_cave"` + label，结构不变。

**前端编辑器**
- [editor.html](file:///d:/TextGameEngine/static/editor.html)：第五页签「事件」（左列表=规则 label/id + on 摘要，右侧 `#form-event`）；顶栏加「游戏设置」按钮；`#form-config` 开局面板（标题/简介/初始场景下拉/初始背包勾选/初始金币/玩家 HP·攻·防）
- [editor.js](file:///d:/TextGameEngine/static/js/editor.js)：
  - DATA 加 config；events 页签列表/表单；on 下拉驱动事件参数行（物品/敌人/场景/NPC 用 select，场景类参数可选）
  - when 复用条件行；do 复用效果行，**效果行扩展 unlock（地点 select + 出口 select 联动）**——M5 的 NPC 效果行同步受益
  - 游戏设置面板（隐藏 sidebar 的全屏表单，完成后返回）；保存走 /api/editor/config
- [editor.css](file:///d:/TextGameEngine/static/editor.css)：事件参数行/设置面板少量样式

**游戏前端**
- [index.html](file:///d:/TextGameEngine/static/index.html)：模式层 h2 下加 `#game-intro` 段落
- [main.js](file:///d:/TextGameEngine/static/js/main.js)：initApp 拉 `/api/game-info`，填 document.title / 模式层 h2 / 顶栏 h1 / intro（空则隐藏）

**QA**
- [qa/tge_api.py](file:///d:/TextGameEngine/qa/tge_api.py)：snapshot/restore 加 config（还原用 POST /api/editor/config）；加 `config_save`、`event_rule_save`、`event_rule_delete`、`game_info`、`move`（action move_scene）动词
- 新增 `qa/m6_events_config.py`（见 Validation）

## Implementation Steps（依赖顺序）

1. effects.py：unlock 效果 + condition_matches 实例方法
2. event_bus/combat_system：ENEMY_KILLED 事件
3. event_rules.py 重构走 executor；session_manager 改装配
4. editor_manager：config upsert + 事件规则 CRUD/校验
5. app.py：game-info / config / event-rule 路由 + defaults 刷新
6. 迁移 game_config.json
7. save_manager update_defaults
8. 前端：游戏标题注入（main.js + index.html）
9. 编辑器：事件页签 + 效果行 unlock 扩展
10. 编辑器：游戏设置面板
11. QA 框架扩展 + m6 套件；py_compile；重启；全量回归
12. 浏览器抽查 + 清理 + 记忆 + README 更新（事件章节/触发器语义）

## Dependencies and Considerations

- **规则热更新边界**：只对新建会话生效（bus 回调注册一次）；编辑器 toast 提示"事件规则对新开游戏生效，进行中的游戏需重置"。QA 先建规则再发游戏请求；删除验证用新 client。
- 旧规则兼容：on/if/do 语义不变，只多 id/when/label；DeclarativeRules 对缺 id 的旧规则照常注册（编辑器保存时补 id）。
- unlock 效果两处可用：事件规则与 M5 对话选项（共享效果行与后端校验）。
- config 原地更新：editor_manager 直接改传入 dict（与 scenes 等同模式）；reset 立即读到新初始值；进行中的会话不回溯。
- ENEMY_KILLED 只在敌人死亡分支发，玩家死亡不发。
- SCENE_ENTER 规则在会话创建于初始场景时会触发一次——编辑器警告/QA 避开初始场景做实验规则。

## Validation

- py_compile；重启；`python -m qa.run_all` M3/M4/M5 保持全绿
- `qa/m6_events_config.py`：
  - G1 /api/game-info 默认标题/简介
  - G2 开局配置：改标题后 game-info 变化；改初始金币/背包/初始场景/玩家 HP，reset 后 /api/state 全部生效
  - G3 校验：不存在的初始场景/背包物品、负金币、HP=0 均拦截
  - R1 ITEM_TAKEN 规则（forest 捡 torch → set_flag + gold 2）；控制台 add_item（from_scene=__console__）不触发
  - R2 ENEMY_KILLED：击杀哥布林 → give_item 治疗药水
  - R3 SCENE_ENTER：传送 cave → set_flag
  - R4 when 条件不满足时规则不触发、满足时触发
  - R5 **unlock 回归**：迁移后的钥匙规则，tavern→forest 捡锈钥匙→move_scene cave 成功（原先锁定时失败）
  - R6 NPC_TALK 到指定 node 触发；ITEM_BOUGHT 在药水店购买触发
  - R7 规则校验（坏 on/坏物品引用/坏 unlock 出口）；删除规则后新 client 不再触发
- 浏览器：编辑器事件页签回填钥匙规则；新建一条规则并试玩；游戏设置改标题后模式层/顶栏生效；Console 无错
- 收尾：config/四表快照还原、qa_ 存档/.bak 清零

## Risks

- 规则注册时序导致"改了不生效"的误判 → toast 明示 + QA 用新会话验证删除
- 效果行从单参数扩成 unlock 双参数可能动到 M5 编辑器 → 扩展方式保持向后兼容（type 变化时重建参数区），M5 回归套件兜底
- config 写盘与规则写盘是同一文件，两个保存入口并发 → editor_manager 已有全局写锁串行化
