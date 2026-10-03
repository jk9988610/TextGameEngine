# ABOUT —— 这个工程是什么（30 秒读完）

**这是什么**：一个点击式文字游戏引擎。玩家不输入指令，只点按钮——
走到哪、和谁说话、买什么、打谁。引擎负责规则，**游戏内容全部是 JSON 数据**，
不写代码也能用内置编辑器做出一款文字冒险。

**技术形态**：Flask（Python）+ 原生 HTML/CSS/JS（无前端框架、无构建步骤）。
本地 venv 跑 `app.py`，也可 Gunicorn 部署。在线/多人模式已冻结（见 README 第 5 节）。

**引擎在服务谁**：两类使用者——
1. 玩家：浏览器里玩用它做出来的游戏（单机、离线存档）
2. 游戏作者：用可视化编辑器把场景/物品/敌人/NPC对话/事件规则/开局配置做成游戏，不碰代码

**已完成（里程碑）**：
- M1 引擎与游戏内容分离　M2 地点/物品编辑器　M3 金币/商店/喝药
- M4 敌人编辑/战斗喝药/金币掉落　M5 NPC 对话树+条件+效果库
- M6 事件规则（7类触发器）+ 开局配置（游戏标题/初始属性）
- M7 NPC 对话画布：编辑器顶栏「列表编辑 | 画布编辑」双工作台；画布为全屏拉线图
  （Drawflow，拉线=选项跳转、框选/整组拖/网格吸附），点节点在右侧属性抽屉直接编辑
  台词/选项/条件/效果；坐标独立持久化（npc_layouts.json，不进游戏 JSON）
- 回归测试 `python -m qa.run_all`（M3~M7，173 条）

早期技术验证产物（正式版已接入 `static/lib/` + `js/graph_model.js` + `js/npc_canvas.js`）：
- `static/demo_drawflow.html`、`static/demo_graph_style.html`：画布原型 demo
- `static/demo-lib/`：demo 用的 drawflow 副本（正式版在 `static/lib/`）
- `tools/mouse_studio.py` / `mouse_watch.py`：人工测交互用的鼠标录制+区域截图工具

**细节去哪看（按需要深入，不用全读）**：
- `README.md`：架构、数据模型、操作约定、避坑清单（最权威）
- `game_data/`：引擎实际读取的游戏数据（5 个内容 JSON + 1 个画布坐标 JSON，一看就懂）
- `engine/`：引擎代码；`qa/`：回归测试（也是最准确的用法示例）
- `.trae/documents/*_plan.md`：各里程碑的实施计划与决策记录
