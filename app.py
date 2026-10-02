"""
文字游戏引擎 MVP 骨架 —— Flask 后端
核心边界：游戏数据（GAME_DATA）与引擎逻辑（EventBus/路由）严格分离，方便 Phase 2 抽引擎
"""
from flask import Flask, request, jsonify, send_from_directory
import os
import json
from typing import Dict, List, Callable, Any

# ---------- Flask 初始化 ----------
app = Flask(__name__, static_folder='static', static_url_path='/static')
# 禁用缓存方便开发
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# ---------- 极简事件总线（引擎逻辑） ----------
class EventBus:
    """观察者模式：只有订阅者才响应事件，绝对不用轮询"""
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
    
    def subscribe(self, event_type: str, callback: Callable) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
    
    def publish(self, event_type: str, **kwargs) -> None:
        for cb in self._subscribers.get(event_type, []):
            cb(**kwargs)

event_bus = EventBus()

# ---------- 游戏数据（硬编码字典，Phase 2 换 JSON/SQLite） ----------
# 【架构边界】所有具体游戏内容都在这里，引擎逻辑在上面，改内容不动引擎
GAME_DATA = {
    "scenes": {
        "tavern": {
            "id": "tavern",
            "name": "破旧的酒馆",
            "description": "你站在昏暗的酒馆里，地板上散落着几个空酒杯。炉火噼啪作响，角落里有个醉汉在打鼾。",
            "exits": ["forest"],  # 出口ID
            "items_here": [],
            "locked_exits": {}  # 出口ID → 锁定状态
        },
        "forest": {
            "id": "forest",
            "name": "幽暗的森林",
            "description": "你走进了茂密的森林，阳光被层层树叶遮挡，只能听到虫鸣鸟叫。一棵老橡树下好像有什么东西。",
            "exits": ["tavern", "cave"],
            "items_here": ["rusty_key"],  # 钥匙在这里！
            "locked_exits": {"cave": True}  # 洞穴默认锁定
        },
        "cave": {
            "id": "cave",
            "name": "神秘的洞穴",
            "description": "洞穴里一片漆黑，但你能听到远处传来滴水声。这里好像藏着什么秘密。",
            "exits": ["forest"],
            "items_here": ["gold_coin", "torch"],
            "locked_exits": {}
        }
    },
    "items": {
        "rusty_key": {
            "id": "rusty_key",
            "name": "生锈的钥匙",
            "description": "一把沾满泥土的钥匙，看起来能开什么门。"
        },
        "torch": {
            "id": "torch",
            "name": "火把",
            "description": "一根没点燃的火把，在黑暗的洞穴里很有用。"
        },
        "gold_coin": {
            "id": "gold_coin",
            "name": "金币",
            "description": "一枚闪闪发光的金币，能买不少东西。"
        }
    }
}

# ---------- 全局游戏状态（内存字典，Phase 2 换 SQLite） ----------
game_state = {
    "current_scene": "tavern",  # 当前场景ID
    "player_inventory": [],     # 玩家背包（物品ID列表）
    "scene_item_states": {},    # 场景里的物品状态（谁拿走了）
    "scene_lock_states": {}     # 场景出口的锁定状态
}

# 初始化游戏状态：把场景里的物品、锁定状态同步到内存
for scene_id, scene in GAME_DATA["scenes"].items():
    game_state["scene_item_states"][scene_id] = scene["items_here"].copy()
    game_state["scene_lock_states"][scene_id] = scene["locked_exits"].copy()

# ---------- 事件订阅：钥匙解锁洞穴门（观察者模式演示） ----------
def unlock_cave_door(**kwargs):
    """订阅 ITEM_TAKEN 事件：如果拿的是 rusty_key，解锁森林→洞穴的出口"""
    taken_item = kwargs.get("item_id")
    from_scene = kwargs.get("from_scene")
    if taken_item == "rusty_key" and from_scene == "forest":
        game_state["scene_lock_states"]["forest"]["cave"] = False
        print("🔓 控制台日志：玩家拿到了钥匙，洞穴门已解锁")

event_bus.subscribe("ITEM_TAKEN", unlock_cave_door)

# ---------- Flask 路由 ----------
@app.route('/')
def serve_index():
    """首页：返回前端单文件"""
    return send_from_directory('static', 'index.html')

@app.route('/api/state', methods=['GET'])
def get_game_state():
    """获取当前游戏状态：前端每次刷新都调这个"""
    scene_id = game_state["current_scene"]
    scene = GAME_DATA["scenes"][scene_id]
    # 构建当前场景的可点击数据
    response = {
        "scene": {
            "id": scene["id"],
            "name": scene["name"],
            "description": scene["description"],
            "exits": [
                {"id": exit_id, "locked": game_state["scene_lock_states"][scene_id].get(exit_id, False)}
                for exit_id in scene["exits"]
            ],
            "items_here": [
                {"id": item_id, "name": GAME_DATA["items"][item_id]["name"]}
                for item_id in game_state["scene_item_states"][scene_id]
            ]
        },
        "player_inventory": [
            {"id": item_id, "name": GAME_DATA["items"][item_id]["name"]}
            for item_id in game_state["player_inventory"]
        ]
    }
    return jsonify(response)

@app.route('/api/action', methods=['POST'])
def handle_action():
    """处理前端点击操作：拿物品/移动场景"""
    data = request.json
    action_type = data.get("type")
    action_target = data.get("target")
    response = {"status": "ok", "message": ""}
    
    # 【操作1】拿物品
    if action_type == "take_item":
        scene_id = game_state["current_scene"]
        if action_target in game_state["scene_item_states"][scene_id]:
            game_state["scene_item_states"][scene_id].remove(action_target)
            game_state["player_inventory"].append(action_target)
            # 【事件发布】拿钥匙的时候触发解锁
            event_bus.publish("ITEM_TAKEN", item_id=action_target, from_scene=scene_id)
            response["message"] = f"✅ 你拿到了【{GAME_DATA['items'][action_target]['name']}】"
        else:
            response["message"] = f"❌ 这里没有这个物品"
    
    # 【操作2】移动场景
    elif action_type == "move_scene":
        current_scene_id = game_state["current_scene"]
        # 检查出口是否存在
        if action_target not in GAME_DATA["scenes"][current_scene_id]["exits"]:
            response["message"] = "❌ 这个出口不存在"
        # 检查出口是否锁定
        elif game_state["scene_lock_states"][current_scene_id].get(action_target, False):
            response["message"] = "🔒 这个出口被锁住了，你需要找到钥匙"
        else:
            game_state["current_scene"] = action_target
            response["message"] = f"🚶 你走进了【{GAME_DATA['scenes'][action_target]['name']}】"
    
    return jsonify(response)

@app.route('/api/console', methods=['POST'])
def handle_console():
    """开发者控制台：按~键触发"""
    data = request.json
    cmd = data.get("command", "").strip().split()
    if not cmd:
        return jsonify({"output": "❌ 空命令"})
    
    cmd_name = cmd[0]
    cmd_args = cmd[1:]
    output = ""
    
    # 【控制台命令1】传送场景
    if cmd_name == "teleport":
        if not cmd_args:
            output = "❌ 用法：teleport <场景ID>（可用：tavern/forest/cave）"
        elif cmd_args[0] not in GAME_DATA["scenes"]:
            output = f"❌ 场景不存在：{cmd_args[0]}"
        else:
            game_state["current_scene"] = cmd_args[0]
            output = f"🚀 传送到【{GAME_DATA['scenes'][cmd_args[0]]['name']}】"
    
    # 【控制台命令2】加物品到背包
    elif cmd_name == "add_item":
        if not cmd_args or cmd_args[0] not in GAME_DATA["items"]:
            output = f"❌ 用法：add_item <物品ID>（可用：{list(GAME_DATA['items'].keys())}）"
        elif cmd_args[0] in game_state["player_inventory"]:
            output = "❌ 你已经有这个物品了"
        else:
            game_state["player_inventory"].append(cmd_args[0])
            output = f"📦 已添加【{GAME_DATA['items'][cmd_args[0]]['name']}】到背包"
    
    # 【控制台命令3】保存快照（Phase 2 换 SQLite）
    elif cmd_name == "save":
        # 把内存状态存到 game_data/snapshot.json
        snapshot = {
            "current_scene": game_state["current_scene"],
            "player_inventory": game_state["player_inventory"],
            "scene_item_states": game_state["scene_item_states"],
            "scene_lock_states": game_state["scene_lock_states"]
        }
        with open("game_data/snapshot.json", "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        output = "💾 快照已保存到 game_data/snapshot.json"
    
    # 【控制台命令4】读取快照
    elif cmd_name == "load":
        try:
            with open("game_data/snapshot.json", "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            game_state.update(snapshot)
            output = f"📂 已读取快照，回到【{GAME_DATA['scenes'][game_state['current_scene']]['name']}】"
        except FileNotFoundError:
            output = "❌ 没有找到存档快照"
    
    # 【控制台命令5】重置游戏
    elif cmd_name == "reset":
        game_state["current_scene"] = "tavern"
        game_state["player_inventory"] = []
        for scene_id, scene in GAME_DATA["scenes"].items():
            game_state["scene_item_states"][scene_id] = scene["items_here"].copy()
            game_state["scene_lock_states"][scene_id] = scene["locked_exits"].copy()
        output = "🔄 游戏已重置"
    
    else:
        output = f"❓ 未知命令：{cmd_name}\n可用命令：teleport/add_item/save/load/reset"
    
    return jsonify({"output": output})

# ---------- 启动入口 ----------
if __name__ == '__main__':
    print("=" * 50)
    print("🎮 文字游戏引擎 MVP 启动中...")
    print("📍 访问 http://localhost:5000 开始玩")
    print("⌨️  按 ~ 键打开开发者控制台")
    print("📋 控制台可用命令：teleport/add_item/save/load/reset")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
