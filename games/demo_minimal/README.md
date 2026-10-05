# 《森林试炼》最小可玩 Demo

一个用本引擎**纯数据配置**出来的 3 场景 + 2 场战斗的完整小游戏，不写一行引擎代码。
它同时是「让 AI 用这个引擎做游戏」的参照模板：覆盖了引擎最常用的全套机制，数据量压到最小。

## 一、加载与还原（仓库根目录执行）

```powershell
# 若系统默认禁止运行 .ps1，用 Bypass 临时执行（只对本条命令生效，不改系统策略）
# 0. 先做数据自检（可选，不启动服务）
.\.venv\Scripts\python.exe tools\validate_game_data.py games\demo_minimal

# 1. 切换到本 demo（首次会自动把当前 game_data 备份到 game_data.bak）
powershell -ExecutionPolicy Bypass -File .\tools\load_game.ps1 -Game demo_minimal
#   已放开本机脚本策略（Set-ExecutionPolicy -Scope CurrentUser RemoteSigned）后，
#   也可直接： .\tools\load_game.ps1 -Game demo_minimal

# 2. 重启服务（数据在 app.py 启动时读入内存），然后浏览器打开
#    http://127.0.0.1:5000/static/index.html （玩）
#    http://127.0.0.1:5000/static/editor.html （看/改配置）

# 玩完还原回原来的「酒馆奇遇」工程
powershell -ExecutionPolicy Bypass -File .\tools\load_game.ps1 -Restore
```

> 切换只替换 5 个游戏 JSON，画布坐标 `npc_layouts.json` 等其它文件不动。
> 注：`tools/load_game.ps1` 刻意保持纯英文（ASCII），否则 Windows PowerShell 5.1
> 会按 GBK 解析无 BOM 的 UTF-8 脚本，中文字节可能吞掉花括号导致解析失败。

## 二、通关走查（约 2 分钟）

| 步 | 地点 | 操作 | 验证到的机制 |
|---|---|---|---|
| 1 | 村口 `village` | 新游戏出生，身上有铁剑 + 10 金币 | 开局配置 / 初始背包 / 初始金币 |
| 2 | 村口 | 和「老守卫」对话：问补给 → 听药水提示 | NPC 对话分支 |
| 3 | 村口 | 在商店花 5 金币买 1 瓶治疗药水 | 商店购买 / 货币 |
| 4 | 森林 `forest` | 往北走，立刻进入与「森林史莱姆」的战斗，打赢它 | 战斗 / 武器伤害公式 |
| 5 | 森林 | 史莱姆死亡：获得 5 金币 + 石门钥匙，石门「当啷」解锁 | 敌人掉落金币/物品 + `ENEMY_KILLED` 事件 + `unlock` 效果 |
| 6 | 森林 | 走进洞穴（没打史莱姆之前这里是锁着的） | 锁门 `locked_exits` / 单向出口 |
| 7 | 洞穴 `cave` | 挑战挡在石台前的「石门守卫」，打倒它 | Boss 战：`brain` 差异化决策器 / 掉落金币 + 核晶 |
| 8 | 洞穴 `cave` | 捡起石台上的金币 | 场景物品拾取 / 货币自动折算 + `ITEM_TAKEN` 事件 + `set_flag` |
| 9 | 回村口 | 再找老守卫：台词变成夸奖「你通过了试炼」 | 条件问候（按 `flag` 切换入口节点） |

战斗数值（有意设计成新手能稳赢）：
- 玩家铁剑 damage 10、攻击 5、防御 2、HP 50；史莱姆 HP 26、攻击 6、防御 1，普攻每击 9 伤。
- **Beat 制战斗（引擎模块 `engine/beat_combat.py`，本工程 `game_config.json` 的 `beat_combat` 块启用，
  经典回合制与它由配置开关二选一）**：
  拍首亮明敌人意图 → 玩家选一个主动作 → 同拍结算。AP 开场 1、每拍 +1、上限 3、
  跨拍保留；攻击 1AP、格挡 0AP、闪避 2AP（确定免伤）、蓄力 0AP、脱离 2AP（条件式）。
  玩家蓄力：本拍不行动且不受伤→下一拍攻击化为重击 `(10+2−1)×2 = 22`（一拍过期、
  受伤打断、不可叠加；重击克制蜷缩，格挡按普通规则先减防再减半=4，可被闪避躲）。
  一拍状态：重击被闪避→重击者下一拍露破绽（动作被看穿，仅信息无增益）；
  脱离失败→挣扎（下一拍闪避/脱离 −1AP，不叠加、过期失效）。脱离：本拍未受伤才成功，
  敌人攻击拍脱离吃全额且失败（AP 照扣），蜷缩拍是逃跑窗口；脱离后行动 10 分钟回满。
  **L2 敌人决策器**（拍首纯函数锁定，不偷看玩家本拍选择，无随机）：
  ① 上拍蓄力成功→承诺重击（不可取消；互蓄对撞仍走这条）
  ② 蓄力被打断→喘息（不出手、可被白打）
  ③ 上拍闪避成功→承诺撞击（不可再躲，防互蓄死循环）
  ④ 看到玩家蓄力就绪且自身 HP>25%→闪避（躲开重击，本拍不出手）
  ⑤ 玩家蓄力就绪且残血→撞击（重击必须能收头）
  ⑥ 自身 HP≤25% 且玩家未蓄力→蜷缩龟息
  ⑦ 否则按节奏 撞击→蓄力→(承诺重击) 三拍循环。
  决策参数支持按敌人 `brain` 覆盖：`rhythm`（撞击/蓄力节奏环）、`frenzy_rhythm`
  （残血狂暴的更快节奏环）、`dodge_player_charge`（能否躲玩家重击）、
  `low_hp_mode`（brace 龟息 | frenzy 狂暴不蜷缩）、`low_hp_ratio`（残血阈值）；
  史莱姆未配置走上面默认。敌人还可覆盖 `heavy_attack`（重击伤害）、
  `telegraphs`（动态征兆文案）、`intents`（看穿拍的动作说明）——
  这些字段都能在编辑器「敌人」页签的「Beat 战斗配置」里直接填。
  普通拍面不直接显示动作名，而是用由实际动作决定的动态词汇表描述史莱姆的姿态；同一动作
  按拍序轮换近义表述，玩家应从表现中判断其意图。重击被躲后的破绽拍会明确显示已看穿的动作，
  作为成功读招的奖励。决策器不改招，情报价值归玩家。玩家重击被躲→下一拍露破绽（仅提示）
  + 敌人承诺撞击。闪避拍也是脱离窗口。
  双方伤害同拍同时生效、同归于尽按惨胜处理（敌人掉落照常，玩家回村口满血复活，
  不做 1HP 硬保底——留给以后装备/光环 buff）。
  胜利后弹出**战斗结算弹窗**：金币自动入账，掉落物点击拾取并弹出物品简介
  （描述取自 items.json 的 description）；不拾取可关闭，物品留在地上。
  下一阶段候选：假动作。

  **动作设计词典（配置驱动）**：`beat_combat.actions` 可定义动作的 `label`、
  `description`、`cost`、`tag`。当前只接入攻击/格挡/闪避的按钮名称、说明和 AP 费用。
  这三个动作还可声明 `effects` 与 `interactions`：已验证 `weapon_damage`、`avoid_damage`、
  `miss`、`halve_damage_after_defense`、`interrupt` 和蓄力/非蓄力条件，配置会实际参与
  伤害、格挡、闪避和打断结算。未声明时保留引擎默认行为。
  还支持状态生命周期：`charge.effects` 的 `apply_state` 添加状态名，
  `duration_beats` 控制状态到期拍，`attack` 的 `consume_state` 消费它，
  `clear_state_on_damage` 在蓄力受伤时清除。本 demo 保持 1 拍；
  qa/m8 另用 2 拍配置验证状态确实能多保留一拍。
- **石门守卫（Boss，洞穴深处）**：HP 60、攻击 7、防御 2、重击 12（格挡受 5）。
  `brain`：节奏 **撞→撞→蓄力**（重击由承诺位打出，比史莱姆更压制）；
  石头躲不动玩家重击（`dodge_player_charge=false`，你蓄力它只能换血）；
  残血 ≤30% **狂暴**（`frenzy`：不蜷缩、节奏加速为 撞→蓄）。掉落 20 金币 +
  守卫核晶（纪念品，描述里留了钩子）。通关走查第 7 步。
- 旧三原型（脱离/防御/先后手）已随 Beat 抽离批次删除；其行为并入 `engine/beat_combat.py`
  （脱离=Beat 动作，恢复计时=`beat_combat.recover_seconds`）。旧存档的 `_proto_*`
  状态键在读档时自动迁移为正式键。回归见 `qa/m8_beat_combat.py`。

控制台快速验证分支（不用真打）：`reset` → `set_flag demo_complete` → 立刻回村找守卫看夸奖；
`teleport forest` → `set_flag` / `give rusty_key` 测试锁门。

## 三、数据文件与设计要点

| 文件 | 内容 | 关键约定（容易踩坑） |
|---|---|---|
| `game_config.json` | 标题/简介/出生点/初始背包金币/玩家属性 + 2 条事件规则 | 事件只在**新会话**装配，改完要 reset |
| `scenes.json` | village / forest / cave | 出口是**单向**的，要双向得两边互写；锁门写 `locked_exits`，解锁靠事件 |
| `items.json` | 铁剑 / 药水 / 钥匙 / 金币 | 武器 `is_weapon+damage`；消耗品 `usable+heal`；货币要 `is_currency+currency_value` |
| `enemies.json` | slime / stone_guard（Boss）两只 | `reward_items` 击杀落在地上，`reward_gold` 直接加钱；`heavy_attack`/`brain`/`telegraphs`/`intents` 为 beat 战斗的敌人级覆盖（编辑器敌人页签可配） |
| `npc_dialogues.json` | old_guard：greet / prep / after_victory / leave | `greeting_rules` 按序首匹配；flag 满足时入口切到 after_victory |

两条事件规则串起任务线：
1. `ENEMY_KILLED slime → unlock forest 的 cave 出口`（打怪开门）
2. `ITEM_TAKEN gold_coin @cave → set_flag demo_complete`（拿宝藏→守卫态度变化）

## 四、引擎能力覆盖矩阵

本 demo 演示了：场景移动与单向出口、锁门、地面拾取、商店、货币折算、
回合战斗、敌人掉落、NPC 多分支对话、条件问候、事件规则（2 种触发器、2 种效果）、
flags、开局配置。

**故意没演示**（引擎当前不支持，需要先扩引擎，不能在数据层硬凑）：
技能/防御/逃跑、多敌人同场、经验升级、限量商店、任务日志 UI、背包合成。

## 五、自检

```powershell
# 离线校验任意一份工程数据的引用完整性（不启动服务器、不写文件）
.\.venv\Scripts\python.exe tools\validate_game_data.py games\demo_minimal
# 通过输出：OK：数据自洽，0 个警告
```

校验器检查：ID 格式、跨文件引用（出口/物品/敌人/NPC/掉落/商店）、锁门有解锁途径、
事件触发器参数（与 `engine/editor_manager.py` 的 `TRIGGER_SPECS` 对齐）、
效果引用、对话树可达性。AI 改完数据后应先跑它，再进游戏。
