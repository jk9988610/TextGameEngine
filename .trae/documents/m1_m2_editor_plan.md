# M1 引擎去游戏化 + M2 地点/物品可视化编辑器 实施计划

## 目标（最小闭环）

1. **M1**：把硬编码在引擎里的具体游戏内容（初始场景、初始铁剑、玩家属性、操作耗时、「拿钥匙解锁洞穴门」规则）全部抽到 `game_data/game_config.json`，引擎变为纯通用层。
2. **M2**：新增网页编辑器 `/static/editor.html`，用鼠标表单即可新增/编辑/删除**地点**和**物品**，保存后新游戏立刻能玩到改动（编辑器只产出 JSON，不碰引擎代码）。
3. 现有游戏（酒馆→森林→洞穴、钥匙开门、哥布林战斗）行为保持不变。

## 仓库调研结论

- 数据层已是 JSON 驱动：`scenes.json`（id/name/description/exits/items_here/locked_exits/enemies_here）、`items.json`（id/name/description/可选 is_weapon+damage）。
- `app.py` 启动时一次性 `load_game_data()` 成全局 `GAME_DATA`，`SessionManager` 与所有会话持有**同一个 dict 引用**——编辑器保存后原地 `clear()+update()` 即可让所有会话看到新数据，无需重启。
- 游戏内容泄漏点共 5 处：
  1. `session_manager.py` L75-86：初始场景 `tavern`、初始背包 `["rusty_sword"]`、玩家 HP50/攻5/防2 写死；L105-112：`unlock_cave_door` 具体规则闭包。
  2. `scene_manager.py` L89：`reset()` 写死回 `tavern`。
  3. `console_handler.py` L109-117：reset 后背包清空（应为初始背包）、HP 取自旧 state。
  4. `app.py` L193、L244：操作耗时 300/20/20、战斗 60 秒写死。
  5. `game.js`：攻击武器 ID 写死 `rusty_sword`，且靠按钮文字包含「铁剑」判断能否攻击。
- `save_manager.py` normalize 有静态默认值（L127-143），需改为可注入，避免老存档补出错误的初始场景。
- 生产部署用 Gunicorn **1 worker**（项目记忆中已确认），单进程内存重载有效；多 worker 会失效（在风险中记录）。

## 文件与改动

### 新增

- `game_data/game_config.json` —— 游戏配置（内容层，不是引擎）：
  ```json
  {
    "initial_scene": "tavern",
    "initial_inventory": ["rusty_sword"],
    "player": { "hp": 50, "attack": 5, "defense": 2 },
    "action_time": { "move_scene": 300, "take_item": 20, "drop_item": 20, "combat_turn": 60 },
    "event_rules": [
      { "on": "ITEM_TAKEN",
        "if": { "item_id": "rusty_key", "from_scene": "forest" },
        "do": [ { "type": "unlock", "scene": "forest", "exit": "cave" } ] }
    ]
  }
  ```
- `engine/event_rules.py` —— 声明式规则引擎（引擎层，通用）：`DeclarativeRules.register(bus, rules, game_state)`；条件为 kwargs 全等匹配；效果走可扩展分发表，M1 只实现 `unlock`（后续 M3/M4 加 `give_item/set_flag/teleport` 等不动调用方）。
- `engine/editor_manager.py` —— 编辑器读写层：JSON 校验、引用完整性检查、写盘前自动备份（`scenes.json.bak` / `items.json.bak`）、临时文件 + `os.replace` 原子写。
- `static/editor.html`、`static/editor.css`、`static/js/editor.js` —— 编辑器页面（原生 JS，零依赖，不加载游戏页的 core.js）。

### 修改

- `app.py`
  - `load_game_data()` 增加读取 `game_config.json` 到 `GAME_DATA["config"]`（缺文件时用内置兜底默认值）。
  - 操作耗时、战斗耗时改读 config。
  - `SaveManager(defaults=...)` 用 config 构造 normalize 默认值。
  - `/api/combat/attack`：未传 `weapon_id` 时自动选背包中第一把 `is_weapon` 武器。
  - 新增编辑器路由（见下），统一用 `_editor_allowed()` 守卫：仅当环境变量 `ONLINE_MODE != 'online'` 时开放（本地/GitHub 本地服可用；阿里云线上自动禁用，避免匿名写文件）。
  - 保存后原地刷新 `GAME_DATA`，并调 `SM.refresh_new_scenes()` 给存量会话补齐新场景的状态键。
- `engine/session_manager.py`：初始 state 全部读 config；删除 `unlock_cave_door` 闭包，改为注册 config 的 `event_rules`；新增 `refresh_new_scenes()`（遍历会话调 `init_missing_scene_states()`）。
- `engine/scene_manager.py`：`reset()` 初始场景读 config；新增 `init_missing_scene_states()`；`get_current_scene()` 对缺失状态键用 `setdefault` 防御。
- `engine/console_handler.py`：reset 时背包恢复为 config 的 `initial_inventory`、HP 恢复 config 值。
- `engine/item_system.py`：`get_player_inventory()` 返回项增加 `is_weapon` 字段（非破坏性）。
- `engine/save_manager.py`：`__init__` 增加可选 `defaults` 参数。
- `engine/__init__.py`：导出新模块。
- `static/js/game.js`：攻击不再传写死的武器 ID（后端自动选）；能否攻击改由 `/api/state` 背包数据里的 `is_weapon` 判断，不再匹配「铁剑」文字。

## 编辑器接口设计

- `GET  /api/editor/data` → `{scenes, items}` 全量
- `POST /api/editor/scene`：upsert，body 为完整场景对象（新建时 id 必填，编辑时 id 不可改）
- `DELETE /api/editor/scene/<id>`
- `POST /api/editor/item` / `DELETE /api/editor/item/<id>`

校验规则（不通过返回中文错误，不落盘）：

- id：`^[a-z0-9_]{1,32}$`；名称/描述非空，长度上限。
- 场景：出口 id 合法；`locked_exits` 的键必须在 exits 内；`items_here` 必须引用已存在物品（当前代码对缺失物品会直接 500，所以这条是硬约束）。
- 出口指向不存在的场景：**警告但允许保存**（新建场景分两次保存的场景需要），响应里带 `warnings`。
- 删除场景：被其他场景出口引用、或等于 config 初始场景时拒绝；删除物品：被任何场景 `items_here` 引用时拒绝。
- 物品：`is_weapon=true` 时 `damage` 必须为非负整数。

## 编辑器页面（M2 只做地点 + 物品两个表单）

- 顶栏：标题、「返回游戏」（打开 index.html）、「在新标签页试玩」。
- 左侧：「地点 / 物品」页签 + 列表 + 「新建」按钮；右侧表单。
- 地点表单：ID（编辑态禁用）、名称、描述、出口（其他场景复选框 + 每个出口一个「锁定」勾选）、初始物品（物品复选框）、敌人 ID（逗号分隔文本，敌人编辑器属于后续里程碑）。
- 物品表单：ID、名称、描述、「是武器」勾选、伤害（勾选武器时显示）。
- 保存/删除/取消按钮 + 消息条（成功绿色、校验错误红色、警告黄色）。
- 沿用现有深色毛玻璃风格（`:root` 变量），编辑器单独 `.ed-*` 命名空间，class 不与游戏页冲突。
- 试玩方式：保存后在游戏页开**新游戏**（reset 会从最新数据初始化场景物品/锁状态）。

## 实施步骤（依赖顺序）

1. 新建 `game_config.json`；`app.py` 加载 config + 兜底默认。
2. 新建 `event_rules.py`；改造 `session_manager.py`、`scene_manager.py`、`console_handler.py`、`save_manager.py` 读 config。
3. `item_system.py` 加 `is_weapon`；`app.py` 攻击自动选武器；`game.js` 去硬编码。
4. 本地验证 M1：老流程行为不变（新游戏铁剑在背包、拿钥匙开门、reset 复原、哥布林可攻击）。
5. 新建 `editor_manager.py`（校验/备份/原子写/引用检查）。
6. `app.py` 加 5 个编辑器路由 + 环境变量守卫 + 存后内存刷新。
7. 写 `editor.html / editor.css / editor.js`。
8. 端到端验证（见下），清理测试数据。

## 验证

- `python -m py_compile` 全部改动文件；浏览器 Console 无报错。
- curl 走通：建物品 `test_potion` → 建场景 `test_room`（出口指向 tavern、放该物品）→ tavern 加反向出口 → GET data 确认落盘；浏览器新开游戏验证能走到新场景、拿到物品；删除被引用对象时确认被拒绝。
- 回归：酒馆/森林/洞穴、钥匙解锁、战斗、存档/读档、reset 全部保持原行为。
- 检查 `game_data/*.bak` 备份生成；验证后删除测试场景/物品。
- 确认编辑器请求头不带在线模式时可用（本地默认）。

## 风险与处理

- **存量会话遇到新加载的数据**：`get_current_scene` 加 `setdefault` 防御 + 保存后批量补场景状态键，避免 KeyError。
- **老存档兼容**：normalize 默认值改由 config 注入，缺失字段仍照补；旧存档可正常读。
- **多 worker 部署内存刷新不一致**：当前生产固定 1 worker，已满足；在编辑器响应/计划中注明 >1 worker 需重启或改共享存储。
- **生产环境匿名写文件**：`ONLINE_MODE=online` 时编辑器 API 全部 403；页面不登录也无数据。
- **出口单向引用**：允许保存但给警告，不做自动双向（把控制权留给作者）。
- **不动线上**：本轮不同步阿里云；线上 `ONLINE_MODE` 已设置，即使将来同步代码编辑器接口也是关闭状态。
