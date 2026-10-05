---
name: tge-game-prototyping
description: 文字游戏引擎两阶段玩法扩展工作流（游戏内原型→试玩→抽离引擎）。用于在本仓库新增玩法、扩展战斗/事件/商店等机制或迭代游戏原型时；不用于纯数据录入或通用代码审查。
---

# TGE 玩法原型两阶段工作流

为 TextGameEngine 扩展玩法时，严格遵守「先在具体游戏里做原型，用户试玩确认好玩后，
再抽离成通用引擎」。完整纪律是 `.trae/documents/ai_handoff_game_extension.md`；
本 skill 是它的操作入口，冲突时以该文档为准。

## 启动检查

1. 首次接触任务：按文档 §0 读交接文档、`README.md`、`games/demo_minimal/`。
   同一会话已读过且任务连续时，**不要重复读**。
2. 先归类需求，拿不准先问、不动 `engine/`：
   - 纯内容（场景/NPC/敌人/物品/对话/事件）或 flags+条件+事件能拼出的玩法 → 只改游戏工程 JSON
   - 引擎不支持的新机制 → 阶段一游戏内代码原型（清单见文档 §2 / README「能力缺口」）
3. 规则决策点用一次 AskUserQuestion 批量问清（最多 4 问），不要一问一答挤牙膏。

## 阶段一：游戏内原型

- 只在 `games/<工程>/` 内做最小、自包含实现；新机制独立模块，统一标注
  `# PROTOTYPE-GAME：<机制名> —— 待试玩验证后抽离，勿当通用API`
- 游戏专用配置放该工程 `game_config.json` 的 `_proto_` 前缀块；
  `app.py` 只加最小接线（路由/视图开关），不散落到多个引擎文件。
- **配套固化冒烟脚本** `games/<工程>/<机制>_smoke.py`：长期保留，随机制迭代直接改，
  同一条命令复用，禁止用完即删、下轮重写。
  **2026-10-05 起新约定**：新模块默认**不再编写** smoke（用户自行试玩验收）；
  已有的固化 smoke 只改不删，作为回归资产维护。
- 做完即停：给试玩 URL + 验证点 + 改动文件表。用户说「通过」后机制进入
  **待抽离池**（状态记录在交付回复里），仍不碰 `engine/`；直到用户明确说
  「开始抽离这批」才启动阶段二。
- **攒池子模式**：允许多个已验证原型攒一批统一抽离（建议同主题或 2~4 个一批，
  存活原型 ≤5 个）。原型间禁止互相依赖，各自独立模块/`_proto_` 配置/smoke，
  抽离时逐模块过清单、逐个验证，一批抽完标注与原型文件清零。

## 固定命令（仓库根目录 / PowerShell）

- 数据自检：`.\.venv\Scripts\python.exe tools\validate_game_data.py games\<工程>`
- 切工程：`powershell -ExecutionPolicy Bypass -File .\tools\load_game.ps1 -Game <工程>`
- 还原酒馆工程：`powershell -ExecutionPolicy Bypass -File .\tools\load_game.ps1 -Restore`
- 启服务：`.\.venv\Scripts\python.exe app.py`（改启动期数据或 .py 后必须重启；
  重启前杀净残留 app.py 进程，见 README 避坑 1）
- 原型冒烟：`.\.venv\Scripts\python.exe games\<工程>\<机制>_smoke.py`
- 全量回归：`.\.venv\Scripts\python.exe -m qa.run_all`
  （用例按「酒馆奇遇」数据编写，跑前 `-Restore`，跑完切回 demo）
- 游玩：http://127.0.0.1:5000/static/index.html ；编辑：/static/editor.html

## 省钱与提效（硬约定）

- **视觉/交互/手感一律由用户判断**，AI 不做浏览器画面测试；AI 只做数据校验、
  API 级冒烟、回归。
- 按需验证，不搞全套仪式：
  - 只改 JSON → 只跑 validator
  - 改原型模块或后端接线 → 只跑该原型的固化冒烟
  - 纯删代码/文案改动且未碰 engine 与共享路由 → 说明理由后可跳过 `qa.run_all`
  - 动了 `engine/` 或 `app.py` 共享路径 → 还原酒馆跑全量回归
- 已确认并记录过的既有失败不重复排查。当前回归基线 **172/173**：
  M5 `sprite-has-key-greeting` 是酒馆数据（莉娅选项带 forest_wolf 条件）与用例断言的
  既有偏差，与原型改动无关。
- 用户说「我来跑」时，只给命令并等待结果，不代执行（切工程/重启/冒烟/回归均可代办）。
- 回复精简：不复述文档、不贴大段代码；交付只给试玩路径、验证点、结果数字。
- 小步快跑：一次只改一个机制点，交付版本必须立即可玩。

## 阶段二：抽离（仅在用户明确批准后）

配置化进 JSON → `engine/` 模块 + `app.py` 接线 → 编辑器支持与引用校验 →
原型冒烟断言迁入 `qa/m*.py` 且 `run_all` 全绿 → 更新 README 数据模型/能力缺口、
移除全部 PROTOTYPE 标注。全程保持在线模式冻结，不主动 git commit。
