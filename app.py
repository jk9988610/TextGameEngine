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
from engine import SessionManager, SaveManager, AuthManager

# ---------- Flask 初始化 ----------
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
# 【安全】secret_key 生产环境从环境变量读，没有才用开发默认值
app.secret_key = os.environ.get('SECRET_KEY') or 'text-game-engine-dev-secret-key-change-in-production'
# 【安全】生产环境强制 debug=False，只有显式设 FLASK_DEBUG=true 才开
DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

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
# 全局唯一的 SaveManager —— SQLite 多槽存档（离线模式用）
SM_SAVE = SaveManager()
# 全局唯一的 AuthManager —— SQLite 用户账号（在线模式用，独立 auth.db）
SM_AUTH = AuthManager()

# ---------- 请求计数：每 N 次请求清理一次过期 session ----------
_request_counter = 0
CLEANUP_INTERVAL = 100  # 每 100 个请求清一次


def _get_engines():
    """
    每个路由的入口：从 Flask session 拿 sid → 从 SessionManager 拿该玩家的引擎实例
    返回 dict：{game_state, scene_manager, item_system, npc_system, combat_system, console_handler}
    额外附带：_sid（存档隔离键）、_mode（offline/online）、_user_id（在线模式才有）
    
    隔离键优先级：
      在线模式  → user_id（数据库主键，稳定不变）
      离线模式  → client_id（localStorage，稳定不变） → fallback Flask session sid
    """
    global _request_counter
    _request_counter += 1

    # 每 CLEANUP_INTERVAL 次请求触发过期 session 清理
    if _request_counter % CLEANUP_INTERVAL == 0:
        cleaned = SM.cleanup_stale(max_age_minutes=30)
        if cleaned > 0:
            print(f"🧹 SessionManager 清理了 {cleaned} 个过期会话，当前活跃 {SM.session_count()} 个")

    # 模式：query param 优先，否则 session 里已存的，否则默认 offline
    mode = request.args.get('mode') or session.get('mode') or 'offline'
    session['mode'] = mode  # 持久化到 cookie

    # ---------- 核心隔离键逻辑 ----------
    user_id = session.get('user_id')  # 在线模式登录后存进 session 的

    if mode == 'online':
        # 在线模式：必须登录，用 user_id 当存档隔离键
        if not user_id:
            # 没登录 —— 返回 None，调用方检查
            return None
        sid = f"user_{user_id}"  # 加前缀和离线 client_id 区分开
    else:
        # 离线模式：用 localStorage client_id（前端 fetch 自动带上）
        client_id = request.args.get('client_id')
        if client_id:
            sid = client_id
        else:
            sid = session.get('sid')
            if not sid:
                sid = uuid.uuid4().hex[:16]
                session['sid'] = sid

    # 从 SessionManager 获取/创建该玩家的独立引擎实例
    engines = SM.get_or_create(sid)
    engines['_sid'] = sid        # 存档隔离键
    engines['_mode'] = mode      # 模式
    engines['_user_id'] = user_id  # 在线模式才可能有值
    return engines


def _maybe_autosave(e: dict) -> None:
    """自动存档：只在离线模式下生效，30 秒防抖"""
    if e.get('_mode') != 'offline':
        return
    import time
    sid = e['_sid']
    now = time.time()
    last = session.get('_last_autosave', 0)
    if now - last >= 30:  # 30 秒才真正写磁盘一次
        SM_SAVE.save_slot(sid, "auto", e["game_state"], is_auto=True)
        session['_last_autosave'] = now


# ============================================================
# Flask 路由层 —— 每个路由都先拿 engines dict 再分发
# ============================================================
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')


@app.route('/api/state', methods=['GET'])
def get_state():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    return jsonify({
        "scene": e["scene_manager"].get_current_scene(),
        "player_inventory": e["item_system"].get_player_inventory(),
    })


@app.route('/api/action', methods=['POST'])
def handle_action():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    action_type = data.get("type")
    action_target = data.get("target")

    result = {"success": False, "message": ""}
    if action_type == "take_item":
        result = e["item_system"].take_item(action_target)
    elif action_type == "move_scene":
        result = e["scene_manager"].move_to(action_target)
    elif action_type == "use_item":
        result = e["item_system"].use_item(action_target, data.get("target_id"))
    else:
        result = {"success": False, "message": f"❌ 未知操作类型：{action_type}"}

    # 🆕 玩家做了操作 → 尝试自动存档（离线模式 + 30秒防抖）
    if result.get("success"):
        _maybe_autosave(e)
    return jsonify(result)


@app.route('/api/console', methods=['POST'])
def handle_console():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    return jsonify({"output": e["console_handler"].handle(data.get("command", ""))})


@app.route('/api/dialogue', methods=['GET'])
def get_dialogue():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    return jsonify(e["npc_system"].get_dialogue_for_api())


@app.route('/api/dialogue', methods=['POST'])
def handle_dialogue():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    return jsonify(e["npc_system"].select_choice(data.get("choice_index", 0)))


@app.route('/api/combat', methods=['GET'])
def get_combat():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    return jsonify(e["combat_system"].get_battle_for_api())


@app.route('/api/combat/attack', methods=['POST'])
def handle_attack():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    result = e["combat_system"].player_attack(data.get("weapon_id", ""))
    # 🆕 战斗后也自动存档
    if result.get("success") or result.get("player_dead"):
        _maybe_autosave(e)
    return jsonify(result)


# ============================================================
# 3.5 用户认证路由（在线模式专属，全部返回 JSON）
# ============================================================
@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    """注册新用户"""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    ok, msg, user_id = SM_AUTH.register(username, password)
    if ok:
        # 注册成功 → 自动登录（session 绑定 user_id）
        session['user_id'] = user_id
        session['username'] = username
        session['mode'] = 'online'
        return jsonify({"success": True, "message": msg, "user_id": user_id, "username": username})
    return jsonify({"success": False, "message": msg})


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """用户登录"""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    ok, msg, user_info = SM_AUTH.login(username, password)
    if ok:
        # 登录成功 → session 绑定 user_id
        session['user_id'] = user_info['user_id']
        session['username'] = user_info['username']
        session['mode'] = 'online'
        return jsonify({"success": True, "message": msg, "user": user_info})
    return jsonify({"success": False, "message": msg})


@app.route('/api/auth/logout', methods=['POST', 'GET'])
def auth_logout():
    """退出登录"""
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('_last_autosave', None)
    # 退出后默认切回离线模式（不强制，前端可以让用户选）
    session['mode'] = 'offline'
    return jsonify({"success": True, "message": "👋 已退出登录"})


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """查询当前登录用户信息（前端用来判断登录态）"""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"logged_in": False})
    user = SM_AUTH.get_user(user_id)
    if not user:
        # user_id 在 session 里但数据库里没了（极端情况）→ 清 session
        session.pop('user_id', None)
        session.pop('username', None)
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "user": user})


# ============================================================
# 4. 存档路由（离线模式 + 在线模式都能用，隔离键不同）
# ============================================================
@app.route('/api/saves', methods=['GET'])
def list_saves():
    """列出当前隔离键的所有存档槽位"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    return jsonify({
        "mode": e['_mode'],
        "slots": SM_SAVE.list_slots(e['_sid']),
    })


@app.route('/api/save', methods=['POST'])
def manual_save():
    """手动存档到指定槽位"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    slot_name = (data.get("slot_name") or "slot_1").strip()
    # 安全校验：只允许 slot_1 ~ slot_5 或 auto
    if slot_name not in [f"slot_{i}" for i in range(1, 6)] and slot_name != "auto":
        return jsonify({"success": False, "message": "❌ 槽位名无效，可用：slot_1~slot_5"})
    result = SM_SAVE.save_slot(e['_sid'], slot_name, e["game_state"], is_auto=False)
    # 保存后刷新 auto 存档时间戳
    session['_last_autosave'] = 0
    return jsonify(result)


@app.route('/api/load', methods=['POST'])
def load_save():
    """从指定槽位读档 → 覆盖当前 game_state"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    slot_name = data.get("slot_name", "").strip()
    if not slot_name:
        return jsonify({"success": False, "message": "❌ 请指定槽位名"})

    loaded = SM_SAVE.load_slot(e['_sid'], slot_name)
    if not loaded:
        return jsonify({"success": False, "message": f"❌ 槽位 {slot_name} 是空的"})

    # 覆盖当前隔离键的 game_state
    e["game_state"].clear()
    e["game_state"].update(loaded)
    # 触发 SCENE_ENTER → 让 NPC/战斗系统同步场景（对话、刷怪）
    e["bus"].publish("SCENE_ENTER", scene_id=e["game_state"]["current_scene"])
    session['_last_autosave'] = 0
    return jsonify({"success": True, "message": f"📂 已读取槽位 {slot_name} 的存档"})


@app.route('/api/save', methods=['DELETE'])
def delete_save():
    """删除一个槽位的存档"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "⚠️ 请先登录（在线模式）"}), 401
    data = request.json
    slot_name = data.get("slot_name", "").strip()
    if not slot_name:
        return jsonify({"success": False, "message": "❌ 请指定槽位名"})
    ok = SM_SAVE.delete_slot(e['_sid'], slot_name)
    if ok:
        return jsonify({"success": True, "message": f"🗑️ 已删除槽位 {slot_name}"})
    return jsonify({"success": False, "message": f"❌ 槽位 {slot_name} 不存在"})


# ============================================================
# 启动入口
# ============================================================
if __name__ == '__main__':
    print("=" * 50)
    print("🎮 文字游戏引擎 MVP —— 多会话隔离版")
    print("📍 访问 http://localhost:5000 开始玩")
    print(f"🔧 debug={DEBUG} | SECRET_KEY={'(env)' if os.environ.get('SECRET_KEY') else '(开发默认值)'}")
    print("=" * 50)
    # 开发模式才跑 Flask 内置服务器，生产用 gunicorn
    if os.environ.get('GUNICORN_RUNNING', '').lower() != 'true':
        app.run(host='0.0.0.0', port=5000, debug=DEBUG)
