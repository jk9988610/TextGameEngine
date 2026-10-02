"""
文字游戏引擎 MVP —— Flask 多会话隔离版本
架构：SessionManager 管理每个玩家浏览器会话的独立引擎实例
      GAME_DATA 全局只读共享，game_state 按 session_id 完全隔离
"""
from flask import Flask, request, jsonify, send_from_directory, session
import json
import os
import uuid
import random

# ---------- 导入引擎模块 ----------
from engine import SessionManager

# ---------- Flask 初始化 ----------
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
# 【关键】Flask session cookie 需要 secret_key 才能工作
app.secret_key = 'text-game-engine-dev-secret-key-change-in-production'

# ---------- 加载游戏数据（全局只读，所有玩家共享） ----------
def load_game_data() -> dict:
    """从 JSON 文件加载游戏数据 —— 只读，所有 session 共用"""
    base = os.path.join(os.path.dirname(__file__), "game_data")
    result = {"scenes": {}, "items": {}, "npcs": {}, "enemies": {}}
    for fname, key in [("scenes.json", "scenes"), ("items.json", "items"),
                       ("npc_dialogues.json", "npcs"), ("enemies.json", "enemies")]:
        path = os.path.join(base, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                result[key] = json.load(f)
    return result

GAME_DATA = load_game_data()
# 全局唯一的 SessionManager —— 管理所有玩家的会话
SM = SessionManager(GAME_DATA)

# ---------- 请求计数：每 N 次请求清理一次过期 session ----------
_request_counter = 0
CLEANUP_INTERVAL = 100  # 每 100 个请求清一次


def _get_engines():
    """
    每个路由的入口：从 Flask session 拿 sid → 从 SessionManager 拿该玩家的引擎实例
    返回 dict：{game_state, scene_manager, item_system, npc_system, combat_system, console_handler}
    """
    global _request_counter
    _request_counter += 1

    # 每 CLEANUP_INTERVAL 次请求触发过期 session 清理
    if _request_counter % CLEANUP_INTERVAL == 0:
        cleaned = SM.cleanup_stale(max_age_minutes=30)
        if cleaned > 0:
            print(f"🧹 SessionManager 清理了 {cleaned} 个过期会话，当前活跃 {SM.session_count()} 个")

    # 从 Flask session cookie 拿或创建 session_id
    sid = session.get('sid')
    if not sid:
        sid = uuid.uuid4().hex[:16]
        session['sid'] = sid

    # 从 SessionManager 获取/创建该玩家的独立引擎实例
    return SM.get_or_create(sid)


# ============================================================
# Flask 路由层 —— 每个路由都先拿 engines dict 再分发
# ============================================================
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')


@app.route('/api/state', methods=['GET'])
def get_state():
    e = _get_engines()
    return jsonify({
        "scene": e["scene_manager"].get_current_scene(),
        "player_inventory": e["item_system"].get_player_inventory(),
    })


@app.route('/api/action', methods=['POST'])
def handle_action():
    e = _get_engines()
    data = request.json
    action_type = data.get("type")
    action_target = data.get("target")

    if action_type == "take_item":
        return jsonify(e["item_system"].take_item(action_target))
    elif action_type == "move_scene":
        return jsonify(e["scene_manager"].move_to(action_target))
    elif action_type == "use_item":
        return jsonify(e["item_system"].use_item(action_target, data.get("target_id")))
    else:
        return jsonify({"success": False, "message": f"❌ 未知操作类型：{action_type}"})


@app.route('/api/console', methods=['POST'])
def handle_console():
    e = _get_engines()
    data = request.json
    return jsonify({"output": e["console_handler"].handle(data.get("command", ""))})


@app.route('/api/dialogue', methods=['GET'])
def get_dialogue():
    e = _get_engines()
    return jsonify(e["npc_system"].get_dialogue_for_api())


@app.route('/api/dialogue', methods=['POST'])
def handle_dialogue():
    e = _get_engines()
    data = request.json
    return jsonify(e["npc_system"].select_choice(data.get("choice_index", 0)))


@app.route('/api/combat', methods=['GET'])
def get_combat():
    e = _get_engines()
    return jsonify(e["combat_system"].get_battle_for_api())


@app.route('/api/combat/attack', methods=['POST'])
def handle_attack():
    e = _get_engines()
    data = request.json
    return jsonify(e["combat_system"].player_attack(data.get("weapon_id", "")))


# ============================================================
# 启动入口
# ============================================================
if __name__ == '__main__':
    print("=" * 50)
    print("🎮 文字游戏引擎 —— 多会话隔离版")
    print("📍 访问 http://localhost:5000 开始玩")
    print("📊 当前架构：每个玩家浏览器会话独立引擎实例")
    print("⏱️  过期会话自动清理（30分钟）")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
