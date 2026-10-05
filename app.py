"""
文字游戏引擎 MVP —— Flask 多会话隔离版本
架构：SessionManager 管理每个玩家浏览器会话的独立引擎实例
      GAME_DATA 全局只读共享，game_state 按 session_id 完全隔离
"""
from flask import Flask, request, jsonify, send_from_directory, session, redirect
import json
import os
import uuid
import random

# ---------- 导入引擎模块 ----------
from engine import SessionManager, SaveManager, AuthManager, beat_combat
from engine.editor_manager import EditorManager

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
    result = {"scenes": {}, "items": {}, "npcs": {}, "enemies": {}, "config": {}}
    for fname, key in [("scenes.json", "scenes"), ("items.json", "items"),
                       ("npc_dialogues.json", "npcs"), ("enemies.json", "enemies"),
                       ("game_config.json", "config")]:
        path = os.path.join(base, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                result[key] = json.load(f)
    return result

GAME_DATA = load_game_data()
# 全局唯一的 SessionManager —— 管理所有玩家的会话
SM = SessionManager(GAME_DATA)
# 全局唯一的 SaveManager —— SQLite 多槽存档（默认值由 game_config 派生，兼容老存档）
SM_SAVE = SaveManager(defaults=SM.initial_state_defaults())
# 全局唯一的 AuthManager —— SQLite 用户账号（在线模式用，独立 auth.db）
SM_AUTH = AuthManager()
# 可视化编辑器数据管理（地点/物品 JSON 的校验与写盘）
EDITOR = EditorManager(data_dir=os.path.join(os.path.dirname(__file__), "game_data"))


def _editor_guard():
    """线上环境（ONLINE_MODE=online）关闭所有编辑器写接口，避免匿名改游戏数据"""
    if os.environ.get('ONLINE_MODE', '').lower() == 'online':
        return jsonify({"success": False, "message": "线上环境已关闭编辑器接口"}), 403
    return None


def _action_time(name: str, default: int = 0) -> int:
    """从 game_config 读每种操作推进的游戏时间（秒）"""
    return (GAME_DATA.get("config") or {}).get("action_time", {}).get(name, default)

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
    """
    自动存档：
      - 离线模式 → slot_name="auto"（30 秒防抖，覆盖自己）
      - 在线模式 → slot_name="online_auto"（每次操作都写，服务器实时存档）
    三路隔离：auto(离线) / quick(离线快速) / slot_1~5(离线槽位) / online_auto(在线)
    """
    sid = e['_sid']
    mode = e.get('_mode')

    if mode == 'online':
        # 在线模式：每次操作都自动写（服务器实时存档）
        SM_SAVE.save_slot(sid, "online_auto", e["game_state"], is_auto=True)
    else:
        # 离线模式：30 秒防抖
        import time
        now = time.time()
        last = session.get('_last_autosave', 0)
        if now - last >= 30:
            SM_SAVE.save_slot(sid, "auto", e["game_state"], is_auto=True)
            session['_last_autosave'] = now


# ============================================================
# Flask 路由层 —— 每个路由都先拿 engines dict 再分发
# ============================================================
@app.route('/')
def serve_index():
    # 前端已拆分为 index.html + styles.css + js/*，静态资源统一挂在 /static/ 下。
    # 根路径重定向到 /static/index.html，保证页面里的相对路径（styles.css、js/*.js）
    # 能正确解析；GitHub Pages 直接访问 /static/index.html 也与之一致。
    return redirect('/static/index.html')


@app.route('/api/game-info', methods=['GET'])
def game_info():
    """游戏标题/简介（无需会话，模式选择层和页面顶栏启动时拉取）"""
    cfg = GAME_DATA.get("config") or {}
    return jsonify({
        "title": cfg.get("game_title", "文字游戏引擎"),
        "intro": cfg.get("game_intro", ""),
    })


@app.route('/api/state', methods=['GET'])
def get_state():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    scene_id = e["game_state"].get("current_scene", "")
    return jsonify({
        "scene": e["scene_manager"].get_current_scene(),
        "player_inventory": e["item_system"].get_player_inventory(),
        "player_gold": e["game_state"].get("player_gold", 0),
        "flags": e["game_state"].get("flags", {}),
        "game_time": e["game_state"].get("game_time", 0),
        "npcs_here": e["npc_system"].list_npcs_in_scene(scene_id),      # 🆕 场景里的 NPC
        "enemies_here": e["combat_system"].list_enemies_in_scene(scene_id),  # 🆕 场景里的敌人
    })


# ---------- 🆕 主动触发对话/战斗的路由 ----------
@app.route('/api/dialogue/start', methods=['POST'])
def start_dialogue():
    """玩家点"和 XX 说话" → 手动启动对话"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json
    result = e["npc_system"].start_dialogue(data.get("npc_id", ""))
    return jsonify(result)


@app.route('/api/combat/start', methods=['POST'])
def start_combat():
    """玩家点"挑战 XX" → 手动开始/继续战斗（保留敌人残血）"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json
    result = e["combat_system"].start_battle(data.get("enemy_id", ""))
    if result.get("success"):
        # 玩家主动接战（含休整中重新挑战）→ 取消恢复计时 + 重置 AP/拍序号
        # （两个调用在未启用 Beat 战斗的工程里都是无害空操作）
        beat_combat.cancel_detach(e["game_state"])
        beat_combat.init_engagement(e["game_state"], GAME_DATA)
    return jsonify(result)


@app.route('/api/action', methods=['POST'])
def handle_action():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json
    action_type = data.get("type")
    action_target = data.get("target")

    result = {"success": False, "message": ""}
    if action_type == "take_item":
        result = e["item_system"].take_item(action_target)
    elif action_type == "move_scene":
        result = e["scene_manager"].move_to(action_target)
    elif action_type == "buy_item":
        result = e["shop_system"].buy_item(action_target)
    elif action_type == "use_item":
        result = e["item_system"].use_item(action_target, data.get("target_id"))
    else:
        result = {"success": False, "message": f"未知操作类型：{action_type}"}

    # 玩家做了操作 → 推进游戏时间（每种操作的秒数由 game_config 配置）
    if result.get("success"):
        time_delta = _action_time(action_type, 0)
        e["game_state"]["game_time"] = e["game_state"].get("game_time", 0) + time_delta
        # 行动时间推进后静默结算脱离休整恢复（未脱离/未启用 Beat 时空操作）
        beat_combat.tick_recover(e["combat_system"], e["game_state"], GAME_DATA)

    # 🆕 玩家做了操作 → 尝试自动存档（离线模式 + 30秒防抖）
    if result.get("success"):
        _maybe_autosave(e)
    return jsonify(result)


@app.route('/api/console', methods=['POST'])
def handle_console():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json
    return jsonify({"output": e["console_handler"].handle(data.get("command", ""))})


@app.route('/api/dialogue', methods=['GET'])
def get_dialogue():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    return jsonify(e["npc_system"].get_dialogue_for_api())


@app.route('/api/dialogue', methods=['POST'])
def handle_dialogue():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json
    result = e["npc_system"].select_choice(data.get("choice_index", 0))

    # 执行选项挂的效果（给物品/置标志/回血/传送/开战等跨系统动作统一在此编排）
    effects = result.pop("effects", None)
    if result.get("success") and effects:
        eff_res = e["effects"].apply(effects)
        result["effect_messages"] = eff_res.get("messages", [])
        # 开战/传送类效果会让对话无法继续：确保对话已结束
        if eff_res.get("combat_started"):
            e["npc_system"].end_dialogue()
    return jsonify(result)


@app.route('/api/combat', methods=['GET'])
def get_combat():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    result = e["combat_system"].get_battle_for_api()
    # 战斗中：附带背包里可使用的消耗品（给战斗面板渲染"喝药"按钮）
    if result.get("active"):
        result["usable_items"] = [
            it for it in e["item_system"].get_player_inventory()
            if it.get("usable")
        ]
        # Beat 制战斗：下发拍面（意图/AP/费用）；脱离/未开拍时隐藏战斗面板
        if beat_combat.beat_config(GAME_DATA):
            result["beat"] = beat_combat.describe(e["game_state"], GAME_DATA)
            if result["beat"] and not result["beat"].get("in_battle"):
                result["active"] = False
        # 已脱离（休整中）：底层 current_battle 保留残血，但对外隐藏战斗面板
        if result.get("active") and beat_combat.is_detached(e["game_state"]):
            result["active"] = False
    return jsonify(result)


@app.route('/api/combat/attack', methods=['POST'])
def handle_attack():
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json
    # 前端无需指定武器：不传时自动使用背包中第一把武器（引擎不绑定具体物品 ID）
    weapon_id = data.get("weapon_id") or e["item_system"].first_weapon_id()
    # 一出手即视为重新接战，取消休整计时（未脱离时空操作）
    beat_combat.cancel_detach(e["game_state"])
    result = e["combat_system"].player_attack(weapon_id)
    # 战斗推进游戏时间（每回合秒数由 game_config 配置）
    e["game_state"]["game_time"] = e["game_state"].get("game_time", 0) + _action_time("combat_turn", 60)
    # 🆕 战斗后也自动存档
    if result.get("success") or result.get("player_dead"):
        beat_combat.tick_recover(e["combat_system"], e["game_state"], GAME_DATA)
        _maybe_autosave(e)
    return jsonify(result)


@app.route('/api/combat/use-item', methods=['POST'])
def handle_combat_use_item():
    """战斗中使用消耗品（喝药算一回合，敌人会趁机反击）"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    data = request.json or {}
    # 战斗中喝药即视为继续接战，取消休整计时（未脱离时空操作）
    beat_combat.cancel_detach(e["game_state"])
    item_id = data.get("item_id", "")
    result = e["combat_system"].use_item_in_battle(item_id)
    # 与攻击回合同等耗时
    e["game_state"]["game_time"] = e["game_state"].get("game_time", 0) + _action_time("combat_turn", 60)
    if result.get("success") or result.get("player_dead"):
        beat_combat.tick_recover(e["combat_system"], e["game_state"], GAME_DATA)
        _maybe_autosave(e)
    return jsonify(result)


@app.route('/api/combat/beat', methods=['POST'])
def handle_combat_beat():
    """Beat 制：提交本拍主动作 attack/block/dodge/charge/disengage，同拍揭晓结算"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401
    if not beat_combat.beat_config(GAME_DATA):
        return jsonify({"success": False, "message": "当前工程未启用 Beat 战斗"}), 409
    data = request.json or {}
    # 重新接战统一走 /api/combat/start（「挑战」按钮：残血复用 + 重置 AP/拍序号）；
    # 休整态直接提交动作会被拒答（"你已经脱离战斗了"）
    weapon_id = data.get("weapon_id") or e["item_system"].first_weapon_id()
    result = beat_combat.resolve(e["combat_system"], e["game_state"], GAME_DATA,
                                 data.get("action", ""), weapon_id)
    # 每个 beat 与一次战斗回合同等耗时（脱离拍也计入）
    if result.get("success") or result.get("player_dead"):
        e["game_state"]["game_time"] = e["game_state"].get("game_time", 0) + _action_time("combat_turn", 60)
        beat_combat.tick_recover(e["combat_system"], e["game_state"], GAME_DATA)
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
    """
    退出登录 —— 只清在线登录态，保留离线隔离键 sid
    sid 是离线模式用的存档隔离键，和在线登录态完全独立，不能清！
    """
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('_last_autosave', None)
    session.pop('mode', None)       # 清掉 mode，让前端重新选
    # session.pop('sid', None)      # ❌ 不要清！sid 是离线模式隔离键
    return jsonify({"success": True, "message": "已退出登录"})


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
# 4. 存档路由
#    在线模式：自动存档（online_auto），禁手动
#    离线模式：auto（自动）/ quick（快速保存）/ slot_1~5（手动槽位）
# ============================================================
@app.route('/api/saves', methods=['GET'])
def list_saves():
    """
    列出当前隔离键的所有存档槽位（带完整状态视图）
    返回格式：{auto, quick, slots: [{slot_name, has_data, updated_at}]}
    """
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401

    # 从 DB 拉已有的存档记录
    existing = {s["slot_name"]: s for s in SM_SAVE.list_slots(e['_sid'])}

    if e['_mode'] == 'online':
        # 在线模式只有 online_auto
        online = existing.get("online_auto")
        return jsonify({
            "mode": "online",
            "can_manual_save": False,
            "online_auto": bool(online),
            "online_auto_updated_at": online["updated_at"] if online else None,
        })

    # 离线模式：完整三路
    auto = existing.get("auto")
    quick = existing.get("quick")
    slots = []
    for i in range(1, 6):
        sname = f"slot_{i}"
        s = existing.get(sname)
        slots.append({
            "slot_name": sname,
            "has_data": bool(s),
            "updated_at": s["updated_at"] if s else None,
        })
    return jsonify({
        "mode": "offline",
        "can_manual_save": True,
        "auto_has_data": bool(auto),
        "auto_updated_at": auto["updated_at"] if auto else None,
        "quick_has_data": bool(quick),
        "quick_updated_at": quick["updated_at"] if quick else None,
        "slots": slots,
    })


@app.route('/api/save', methods=['POST'])
def manual_save():
    """
    手动存档到指定槽位 —— 离线模式专属
    在线模式返回 403（自动存档，不允许手动）
    """
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401

    if e['_mode'] == 'online':
        return jsonify({"success": False, "message": "在线模式自动存档，无需手动保存"}), 403

    data = request.json or {}
    slot_name = (data.get("slot_name") or "slot_1").strip()
    # 安全校验：只允许 slot_1 ~ slot_5
    if slot_name not in [f"slot_{i}" for i in range(1, 6)]:
        return jsonify({"success": False, "message": "槽位名无效，可用：slot_1~slot_5"})

    result = SM_SAVE.save_slot(e['_sid'], slot_name, e["game_state"], is_auto=False)
    session['_last_autosave'] = 0
    return jsonify(result)


@app.route('/api/save/quick', methods=['POST'])
def quick_save():
    """
    快速保存 —— 快捷键一键存，覆盖 quick 槽位
    离线模式专属
    """
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401

    if e['_mode'] == 'online':
        return jsonify({"success": False, "message": "在线模式自动存档，无需手动保存"}), 403

    result = SM_SAVE.save_slot(e['_sid'], "quick", e["game_state"], is_auto=False)
    session['_last_autosave'] = 0
    return jsonify(result)


@app.route('/api/load', methods=['POST'])
def load_save():
    """从指定槽位读档 → 覆盖当前 game_state"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401

    data = request.json or {}
    slot_name = data.get("slot_name", "").strip()
    if not slot_name:
        return jsonify({"success": False, "message": "请指定槽位名"})

    loaded = SM_SAVE.load_slot(e['_sid'], slot_name)
    if not loaded:
        return jsonify({"success": False, "message": f"槽位 {slot_name} 是空的"})

    # 覆盖当前隔离键的 game_state
    e["game_state"].clear()
    e["game_state"].update(loaded)
    # 旧版原型存档键迁移（_proto_* → 正式键，幂等）
    beat_combat.migrate_state(e["game_state"])
    # 触发 SCENE_ENTER → 让 NPC/战斗系统同步场景（对话、刷怪）
    e["bus"].publish("SCENE_ENTER", scene_id=e["game_state"]["current_scene"])
    session['_last_autosave'] = 0
    return jsonify({"success": True, "message": f"已读取槽位 {slot_name} 的存档"})


@app.route('/api/save', methods=['DELETE'])
def delete_save():
    """删除一个槽位的存档 —— 离线模式专属"""
    e = _get_engines()
    if not e:
        return jsonify({"success": False, "message": "请先登录（在线模式）"}), 401

    if e['_mode'] == 'online':
        return jsonify({"success": False, "message": "在线模式不允许删除存档"}), 403

    data = request.json or {}
    slot_name = data.get("slot_name", "").strip()
    if not slot_name:
        return jsonify({"success": False, "message": "请指定槽位名"})
    ok = SM_SAVE.delete_slot(e['_sid'], slot_name)
    if ok:
        return jsonify({"success": True, "message": f"已删除槽位 {slot_name}"})
    return jsonify({"success": False, "message": f"槽位 {slot_name} 不存在"})


# ============================================================
# 5. 可视化编辑器 API（地点 / 物品）
#    仅本地开发环境开放；线上 ONLINE_MODE=online 时全部 403
# ============================================================
@app.route('/api/editor/data', methods=['GET'])
def editor_get_data():
    """编辑器首屏数据：全部地点 + 全部物品 + 初始场景标记"""
    denied = _editor_guard()
    if denied:
        return denied
    return jsonify({
        "success": True,
        "scenes": GAME_DATA["scenes"],
        "items": GAME_DATA["items"],
        "enemies": GAME_DATA["enemies"],
        "npcs": GAME_DATA["npcs"],
        "config": GAME_DATA["config"],
        "layouts": EDITOR.load_layouts(),
        "scene_layouts": EDITOR.load_scene_layouts(),
        "initial_scene": (GAME_DATA.get("config") or {}).get("initial_scene", ""),
    })


@app.route('/api/editor/scene', methods=['POST'])
def editor_upsert_scene():
    """新建/更新一个地点（按 id 区分新建与编辑）"""
    denied = _editor_guard()
    if denied:
        return denied
    payload = request.json or {}
    result = EDITOR.upsert_scene(
        GAME_DATA["scenes"], GAME_DATA["items"], GAME_DATA["enemies"], payload)
    if result.get("success"):
        SM.refresh_new_scenes()  # 让所有存活会话补齐新场景的状态键
    return jsonify(result)


@app.route('/api/editor/scene/<scene_id>', methods=['DELETE'])
def editor_delete_scene(scene_id):
    """删除一个地点（初始场景/被引用/有玩家在里面时拒绝）"""
    denied = _editor_guard()
    if denied:
        return denied
    initial_scene = (GAME_DATA.get("config") or {}).get("initial_scene", "")
    result = EDITOR.delete_scene(
        GAME_DATA["scenes"], GAME_DATA["items"], scene_id,
        initial_scene=initial_scene,
        live_scene_ids=SM.live_current_scenes())
    return jsonify(result)


@app.route('/api/editor/scene-layout/<scene_id>', methods=['POST'])
def editor_save_scene_layout(scene_id):
    """保存场景地图画布上单个地点的坐标（辅助数据，不影响游戏内容）"""
    denied = _editor_guard()
    if denied:
        return denied
    return EDITOR.save_scene_layout(scene_id, request.json or {})


@app.route('/api/editor/item', methods=['POST'])
def editor_upsert_item():
    """新建/更新一个物品"""
    denied = _editor_guard()
    if denied:
        return denied
    payload = request.json or {}
    result = EDITOR.upsert_item(
        GAME_DATA["scenes"], GAME_DATA["items"], GAME_DATA["enemies"],
        GAME_DATA.get("config") or {}, payload)
    return jsonify(result)


@app.route('/api/editor/item/<item_id>', methods=['DELETE'])
def editor_delete_item(item_id):
    """删除一个物品（被地点放置/商店售卖/敌人掉落/初始背包引用时拒绝）"""
    denied = _editor_guard()
    if denied:
        return denied
    result = EDITOR.delete_item(
        GAME_DATA["scenes"], GAME_DATA["items"], GAME_DATA["enemies"],
        GAME_DATA.get("config") or {}, item_id)
    return jsonify(result)


@app.route('/api/editor/enemy', methods=['POST'])
def editor_upsert_enemy():
    """新建/更新一个敌人（属性/掉落物品/金币掉落，按 id 区分新建与编辑）"""
    denied = _editor_guard()
    if denied:
        return denied
    payload = request.json or {}
    result = EDITOR.upsert_enemy(
        GAME_DATA["items"], GAME_DATA["enemies"], payload)
    return jsonify(result)


@app.route('/api/editor/enemy/<enemy_id>', methods=['DELETE'])
def editor_delete_enemy(enemy_id):
    """删除一个敌人（被场景引用/有玩家正在战斗时拒绝）"""
    denied = _editor_guard()
    if denied:
        return denied
    result = EDITOR.delete_enemy(
        GAME_DATA["scenes"], GAME_DATA["enemies"], enemy_id,
        live_battle_ids=SM.live_battle_enemies())
    return result


@app.route('/api/editor/npc', methods=['POST'])
def editor_upsert_npc():
    """新建/更新一个 NPC（属性 + 问候规则 + 对话节点树/条件/效果，按 id 区分）"""
    denied = _editor_guard()
    if denied:
        return denied
    payload = request.json or {}
    result = EDITOR.upsert_npc(
        GAME_DATA["npcs"], GAME_DATA["scenes"], GAME_DATA["items"],
        GAME_DATA["enemies"], payload)
    if result.get("success"):
        # 节点可能增删，顺带清理该 NPC 布局里的孤儿坐标（坐标独立保存，不影响结果）
        EDITOR.prune_layouts(GAME_DATA["npcs"])
    return result


@app.route('/api/editor/npc-layout/<npc_id>', methods=['POST'])
def editor_save_npc_layout(npc_id):
    """保存单个 NPC 画布的节点坐标（与游戏内容分离，仅坐标）"""
    denied = _editor_guard()
    if denied:
        return denied
    return EDITOR.save_layout(npc_id, request.json or {})


@app.route('/api/editor/npc/<npc_id>', methods=['DELETE'])
def editor_delete_npc(npc_id):
    """删除一个 NPC（有玩家正在与其对话时拒绝）"""
    denied = _editor_guard()
    if denied:
        return denied
    result = EDITOR.delete_npc(
        GAME_DATA["npcs"], npc_id,
        live_dialogue_ids=SM.live_dialogue_npcs())
    if result.get("success"):
        EDITOR.prune_layouts(GAME_DATA["npcs"])
    return result


@app.route('/api/editor/config', methods=['POST'])
def editor_save_config():
    """保存游戏开局配置（标题/简介/初始场景/背包/金币/玩家属性）"""
    denied = _editor_guard()
    if denied:
        return denied
    payload = request.json or {}
    result = EDITOR.upsert_config(
        GAME_DATA["config"], GAME_DATA["scenes"], GAME_DATA["items"], payload)
    if result.get("success"):
        # 刷新老存档 normalize 用的默认值（新会话/reset 直接读 config，不受影响）
        SM_SAVE.update_defaults(SM.initial_state_defaults())
    return result


@app.route('/api/editor/event-rule', methods=['POST'])
def editor_upsert_event_rule():
    """新建/更新一条事件规则"""
    denied = _editor_guard()
    if denied:
        return denied
    payload = request.json or {}
    result = EDITOR.upsert_event_rule(
        GAME_DATA["config"], GAME_DATA["scenes"], GAME_DATA["items"],
        GAME_DATA["enemies"], GAME_DATA["npcs"], payload)
    return result


@app.route('/api/editor/event-rule/<rule_id>', methods=['DELETE'])
def editor_delete_event_rule(rule_id):
    """删除一条事件规则"""
    denied = _editor_guard()
    if denied:
        return denied
    return EDITOR.delete_event_rule(GAME_DATA["config"], rule_id)


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
