"""
文字游戏引擎 MVP —— Flask 薄路由层
架构：app.py 只做【加载数据 → 初始化引擎 → 路由转发】，所有业务逻辑委托 engine/ 模块
      游戏专属事件（如钥匙解锁门）作为 EventBus 订阅回调留在本文件
"""
from flask import Flask, request, jsonify, send_from_directory
import json
import os

# ---------- 导入引擎模块（全部从 engine 包统一导出） ----------
from engine import EventBus, SceneManager, ItemSystem, ConsoleHandler, NPCSystem, CombatSystem

# ---------- Flask 初始化 ----------
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


# ============================================================
# 1. 加载游戏数据（从 game_data/ JSON 文件，纯数据，可热替换）
# ============================================================
def load_game_data() -> dict:
    """从 JSON 文件加载游戏数据 —— 后续改游戏内容只动 JSON，不动引擎"""
    base = os.path.join(os.path.dirname(__file__), "game_data")
    with open(os.path.join(base, "scenes.json"), "r", encoding="utf-8") as f:
        scenes = json.load(f)
    with open(os.path.join(base, "items.json"), "r", encoding="utf-8") as f:
        items = json.load(f)
    # 新增：NPC 对话数据
    npcs = {}
    npc_file = os.path.join(base, "npc_dialogues.json")
    if os.path.exists(npc_file):
        with open(npc_file, "r", encoding="utf-8") as f:
            npcs = json.load(f)
    # 🆕 新增：敌人数据
    enemies = {}
    enemy_file = os.path.join(base, "enemies.json")
    if os.path.exists(enemy_file):
        with open(enemy_file, "r", encoding="utf-8") as f:
            enemies = json.load(f)
    return {"scenes": scenes, "items": items, "npcs": npcs, "enemies": enemies}


# ============================================================
# 2. 初始化引擎 + 游戏状态
# ============================================================
def init_game():
    """构建完整的引擎运行时 —— 构造函数注入依赖，无循环导入"""
    game_data = load_game_data()

    # 全局游戏状态（内存字典，Phase 3 换 SQLite，引擎模块通过构造函数拿到引用）
    game_state = {
        "current_scene": "tavern",
        "player_inventory": ["rusty_sword"],  # 🆕 初始给一把剑（MVP简化）
        "scene_item_states": {},
        "scene_lock_states": {},
        "current_dialogue": None,
        "current_battle": None,           # 🆕 战斗状态（CombatSystem 会管）
        # 🆕 玩家属性（CombatSystem 用）
        "player_hp": 50,
        "player_max_hp": 50,
        "player_attack": 5,
        "player_defense": 2,
    }

    # 引擎模块初始化（全部通过构造函数注入依赖）
    bus = EventBus()
    scene_manager = SceneManager(bus, game_data, game_state)
    item_system = ItemSystem(bus, game_data, game_state)
    console_handler = ConsoleHandler(bus, game_data, game_state, scene_manager, item_system)
    npc_system = NPCSystem(bus, game_data, game_state)
    combat_system = CombatSystem(bus, game_data, game_state)  # 🆕 战斗系统（订阅 SCENE_ENTER 刷怪）

    # 初始化场景状态（把 JSON 里的初始值同步到内存）
    scene_manager.init_scene_states()

    # 🆕 关键！发布初始场景的 SCENE_ENTER 事件 —— 否则 NPCSystem 不会触发初始对话
    # 因为玩家"一出生就在酒馆"不是通过 move_to() 进去的，不会自动触发事件
    bus.publish("SCENE_ENTER", scene_id=game_state["current_scene"])

    # ---------- 游戏专属事件订阅（放在 app.py，因为是"这个游戏"的内容逻辑） ----------
    def unlock_cave_door(**kwargs):
        """【游戏专属逻辑】拿了森林的 rusty_key → 自动解锁森林→洞穴的出口"""
        taken_item = kwargs.get("item_id")
        from_scene = kwargs.get("from_scene")
        if taken_item == "rusty_key" and from_scene == "forest":
            game_state["scene_lock_states"]["forest"]["cave"] = False
            print("🔓 控制台日志：玩家拿到了钥匙，洞穴门已解锁")

    bus.subscribe("ITEM_TAKEN", unlock_cave_door)

    return game_data, game_state, bus, scene_manager, item_system, console_handler, npc_system, combat_system


# 启动时一次性初始化（避免每次请求都读 JSON）
GAME_DATA, GAME_STATE, EVENT_BUS, SCENE_MGR, ITEM_SYS, CONSOLE, NPC_SYS, COMBAT_SYS = init_game()


# ============================================================
# 3. Flask 路由层 —— 每个路由只转发到引擎模块，不自己做业务逻辑
# ============================================================
@app.route('/')
def serve_index():
    """首页：返回前端单文件"""
    return send_from_directory('static', 'index.html')


@app.route('/api/state', methods=['GET'])
def get_state():
    """获取当前场景+背包状态（前端每次刷新都调这个）"""
    return jsonify({
        "scene": SCENE_MGR.get_current_scene(),
        "player_inventory": ITEM_SYS.get_player_inventory(),
    })


@app.route('/api/action', methods=['POST'])
def handle_action():
    """处理前端点击：拿物品 / 移动场景 —— 全委托给引擎"""
    data = request.json
    action_type = data.get("type")
    action_target = data.get("target")

    # 【路由层只做分发，不做判断】
    if action_type == "take_item":
        return jsonify(ITEM_SYS.take_item(action_target))
    elif action_type == "move_scene":
        return jsonify(SCENE_MGR.move_to(action_target))
    elif action_type == "use_item":
        target = data.get("target_id")
        return jsonify(ITEM_SYS.use_item(action_target, target))
    else:
        return jsonify({"success": False, "message": f"❌ 未知操作类型：{action_type}"})


@app.route('/api/console', methods=['POST'])
def handle_console():
    """开发者控制台：按~键触发 —— 全委托给 ConsoleHandler"""
    data = request.json
    raw_cmd = data.get("command", "")
    output = CONSOLE.handle(raw_cmd)
    return jsonify({"output": output})


# ============================================================
# 3.5 NPC 对话路由 —— 全委托给 NPCSystem
# ============================================================
@app.route('/api/dialogue', methods=['GET'])
def get_dialogue():
    """获取当前对话状态（前端每次刷新都调）—— 没在对话就返回 {"active": false}"""
    return jsonify(NPC_SYS.get_dialogue_for_api())


@app.route('/api/dialogue', methods=['POST'])
def handle_dialogue():
    """处理玩家点击对话选项"""
    data = request.json
    choice_index = data.get("choice_index", 0)
    result = NPC_SYS.select_choice(choice_index)
    return jsonify(result)


# ============================================================
# 3.6 战斗路由 —— 全委托给 CombatSystem
# ============================================================
@app.route('/api/combat', methods=['GET'])
def get_combat():
    """获取当前战斗状态 + 玩家属性（前端每次刷新都调）"""
    return jsonify(COMBAT_SYS.get_battle_for_api())


@app.route('/api/combat/attack', methods=['POST'])
def handle_attack():
    """玩家用武器攻击敌人"""
    data = request.json
    weapon_id = data.get("weapon_id", "")
    result = COMBAT_SYS.player_attack(weapon_id)
    return jsonify(result)


# ============================================================
# 4. 启动入口
# ============================================================
if __name__ == '__main__':
    print("=" * 50)
    print("🎮 文字游戏引擎 MVP（已抽引擎模块）")
    print("📍 访问 http://localhost:5000 开始玩")
    print("⌨️  按 ~ 键打开开发者控制台")
    print("📋 控制台命令：help 查看全部")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
