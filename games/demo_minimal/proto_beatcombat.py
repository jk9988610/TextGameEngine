# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：Beat 制战斗（AP+意图） —— 待试玩验证后抽离，勿当通用API
#
# 《森林试炼》专用的战斗 2.0 阶段一原型（两阶段开发纪律，见
# .trae/documents/ai_handoff_game_extension.md §3）。它是"拳击式预判"讨论的
# 首版切片，启用后与旧三原型（脱离/防御/先后手）开关互斥，旧代码保留可回退。
#
# 一拍（Beat）流程：
#   拍首敌人意图明牌 → 玩家选一个主动作（攻/格挡/闪避/脱离）→ 同时结算
#   → 拍末 AP+1。AP 开场 1、每拍 +1、上限 3、跨拍保留，同一拍只能选一个动作。
#
# 动作费用与效果（demo，每拍只能选一个主动作）：
#   攻击 1AP：武器伤害 9；敌人蜷缩时 0 伤害；对攻/重击拍 = 换血吃全额
#   格挡 0AP（占拍）：撞击减半 4→2；重击不减半，吃全额 10；蜷缩无事
#   闪避 2AP：撞击/重击完全免伤；蜷缩拍 = AP 白花
#   蓄力 0AP（占拍）：本拍不行动且无防护；未受伤→获得「蓄力就绪」（只保留下一拍），
#       受伤→蓄力被打断。下一拍攻击 = 重击 (武器+2−敌防)×2 = 22；重击克制防御
#       （敌人蜷缩也吃全额），只能被闪避躲；下一拍不攻击则蓄力清空，不可叠加
#   脱离 2AP：本拍未受伤才成功（敌人蜷缩/蓄力等不出手的拍子是窗口）；
#       在敌人攻击拍脱离 = 吃全额且失败、AP 照扣（不掷骰、不必定成功）
#
# 敌人动作（L2 决策器每拍拍首锁定，候选集与玩家结算规则同源）：
#   撞击 bash（4 伤/格挡 2）、蓄力 charge（不出手，下拍承诺重击）、
#   重击 heavy（10/格挡 4/闪避 0）、蜷缩 brace（不出手，普攻无效）、
#   喘息 recover（蓄力被打断后的当拍，不出手、可被正常攻击）、
#   闪避 dodge（不出手；玩家本拍攻击/重击落空；下一拍承诺撞击）。
# 决策优先级（纯函数，只看拍首公开状态，不偷看玩家本拍选择）：
#   ① 上拍蓄力成功 → 承诺 heavy（不可取消；互蓄对撞仍走这条）
#   ② 上拍蓄力被打断 → recover
#   ③ 上拍闪避成功 → 承诺 bash（不可取消；防「互蓄/再蓄力→再闪避」死循环）
#   ④ 玩家「蓄力就绪」且自身 HP > 25% → dodge（躲开重击）
#   ⑤ 玩家「蓄力就绪」且残血 → bash（重击必须能收头，不躲）
#   ⑥ 自身 HP ≤ 25% 且玩家未蓄力 → brace（龟息）
#   ⑦ 否则按节奏步：bash→charge→(承诺 heavy) 三拍循环
#   破绽拍决策器不改招——情报价值归玩家。
#
# 两个一拍状态：
#   破绽：重击被闪避 → 重击者下一拍动作被看穿（只给拍面提示，无数值增益）。
#         玩家重击被躲 → 额外承诺敌人下一拍撞击（这是闪避的机械惩罚）。
#   挣扎：脱离失败（本拍受伤）→ 下一拍闪避/脱离消耗 -1，不叠加、不用即过期，
#         不突破 AP 上限。
#
# 状态（随存档走）：
#   game_state["_proto_ap"]                 —— 当前 AP
#   current_battle["_proto_beat"]           —— 本场交战的拍序号（1 起，接战重置）
#   current_battle["_proto_charge"]         —— "readied"=蓄力就绪（仅下一拍有效），否则 None
#   current_battle["_proto_enemy_flaw_beat"]—— 敌人破绽生效的拍序号（重击被闪避后的下一拍）
#   current_battle["_proto_player_flaw_beat"]—— 玩家破绽生效的拍序号（重击被躲开后的下一拍）
#   current_battle["_proto_struggle_beat"]  —— 玩家挣扎生效的拍序号（脱离失败后的下一拍）
#   current_battle["_proto_enemy_step"]     —— 敌人节奏步：0=下拍 bash，1=下拍 charge
#   current_battle["_proto_enemy_promised"]—— True=上拍蓄力成功，本拍承诺重击
#   current_battle["_proto_enemy_promised_bash"] —— True=上拍闪避，本拍承诺撞击
#   current_battle["_proto_enemy_interrupted"] —— 上拍蓄力被玩家攻击打断，本拍喘息
#   game_state["_proto_detached_at"]        —— 脱离时刻（与 proto_retreat 同一数据
#       契约键，app.py 的 tick_recover 原样为两者服务；阶段二统一删除）
#
# 隔离：不 import engine 与其它原型；击杀结算复制自 combat_system.player_attack
# （与 proto_turnorder 同口径），阶段二随统一战斗器整体删除。
"""Beat 制战斗原型（《森林试炼》专用，阶段一）。"""
from typing import Any, Dict, Optional

CONFIG_KEY = "_proto_beatcombat"
DETACH_KEY = "_proto_detached_at"   # 与 proto_retreat 共用的数据契约键

INTENT_TEXT = {
    "bash": ("撞击", "这一拍它会撞过来"),
    "charge": ("蓄力中", "下一拍重击 10 点！它本拍不会出手——攻击可以打断蓄力"),
    "heavy": ("蓄力重击", "重击 10 点，格挡只受 4 点，闪避可完全避开"),
    "brace": ("蜷缩防守", "这一拍它缩起身子不攻击，普攻无效，是脱离/蓄力的窗口"),
    "recover": ("喘息", "蓄力被你打散，这一拍它毫无防备，不会出手"),
    "dodge": ("闪避", "这一拍它会躲开攻击——重击也会落空，它本拍不会出手"),
}
ACTIONS = ("attack", "block", "dodge", "charge", "disengage")

# PROTOTYPE-GAME：模糊征兆 —— 待试玩验证后抽离，勿当通用API
# 普通拍面只给动态叙述，不直接显示 intent 名；按拍序确定性轮换，方便复盘和测试。
TELEGRAPH_TEXT = {
    "bash": (
        "它压低身子，四肢猛地蹬紧，像一块弹簧一样逼近。",
        "它贴着地面蓄势，身体朝你一侧偏斜，下一刻就要撞上来。",
    ),
    "charge": (
        "它的腹部缓缓鼓起，周身的力气正往一点收拢。",
        "它伏在原地不动，表皮下的力量一阵阵聚集起来。",
    ),
    "heavy": (
        "它的身躯完全绷直，沉重的力量沿着四肢压向你。",
        "它把全身的重量都压了下来，空气像被挤开了一样。",
    ),
    "brace": (
        "它缩成一团，软乎乎的身体紧贴地面，几乎没有破绽。",
        "它把要害藏进身体深处，只留下微微起伏的外壳。",
    ),
    "recover": (
        "它的身体一阵颤抖，刚才聚起的力量散了，只能急促喘息。",
        "它瘫在原地调整姿势，攻势断了，暂时无力追击。",
    ),
    "dodge": (
        "它的身体左右晃动，脚下不断试探着后撤的空隙。",
        "它盯着你的动作轻轻弹动，像是在等你先把力气用出去。",
    ),
}


def telegraph(intent: str, beat: int) -> str:
    """按拍序给同一动作选择稳定的近义动态描述。"""
    phrases = TELEGRAPH_TEXT.get(intent, ("它的动作让你暂时无法判断。",))
    return phrases[(max(1, int(beat)) - 1) % len(phrases)]


def beat_config(game_data: Dict) -> Optional[Dict]:
    cfg = (game_data.get("config") or {}).get(CONFIG_KEY)
    return cfg if isinstance(cfg, dict) and cfg.get("enabled") else None


def _battle_enemy(state: Dict, data: Dict):
    battle = state.get("current_battle")
    if not battle:
        return None, None
    return battle, (data.get("enemies") or {}).get(battle.get("enemy_id", ""))


def init_engagement(state: Dict, data: Dict) -> None:
    """每次 start_battle 成功（含残血续战）后由路由层调用：重置 AP 与拍序号。"""
    cfg = beat_config(data)
    if not cfg:
        return
    state["_proto_ap"] = int(cfg.get("ap_start", 1))
    battle = state.get("current_battle")
    if battle:
        battle["_proto_beat"] = 1
        battle["_proto_charge"] = None          # 每次接战清空蓄力（含残血续战）
        battle["_proto_enemy_step"] = 0         # 敌人节奏从撞击开始
        battle["_proto_enemy_promised"] = False
        battle["_proto_enemy_promised_bash"] = False
        battle["_proto_enemy_interrupted"] = False
        battle.pop("_proto_enemy_flaw_beat", None)
        battle.pop("_proto_player_flaw_beat", None)
        battle.pop("_proto_struggle_beat", None)


def _cost(cfg: Dict, action: str) -> int:
    return int(cfg.get(f"cost_{action}", 0))


def _effective_cost(cfg: Dict, battle: Dict, action: str, beat: int) -> int:
    """挣扎生效拍：闪避/脱离消耗 -1；其余保持。不会低于 0。"""
    cost = _cost(cfg, action)
    if (battle.get("_proto_struggle_beat") == beat
            and action in ("dodge", "disengage")):
        return max(0, cost - 1)
    return cost


def decide(cfg: Dict, battle: Dict, state: Dict, enemy: Dict) -> str:
    """L2 决策器：拍首纯函数，锁定敌人本拍动作。只读拍首状态，不接收玩家本拍
    选择（AI 不得偷看改招）；无随机，同局面结果恒定。返回 INTENT_TEXT 的键。"""
    # ① 承诺：上拍蓄力成功且没被打断 → 重击必须打出
    if battle.get("_proto_enemy_promised"):
        return "heavy"
    # ② 上拍蓄力被打断 → 本拍喘息
    if battle.get("_proto_enemy_interrupted"):
        return "recover"
    # ③ 上拍闪避 → 承诺撞击（不可再躲）
    if battle.get("_proto_enemy_promised_bash"):
        return "bash"
    player_charged = battle.get("_proto_charge") == "readied"
    max_hp = int(enemy.get("hp", 1))
    low_hp = int(battle.get("enemy_hp", max_hp)) <= max_hp * float(cfg.get("low_hp_ratio", 0.25))
    # ④ 玩家蓄力就绪 + 健康：躲开重击（残血不躲，否则杀不死）
    if player_charged:
        return "bash" if low_hp else "dodge"
    # ⑤ 残血龟息：玩家没在蓄力时蜷缩，期望把普攻挡在外面
    if low_hp:
        return "brace"
    # ⑥ 节奏步：0=撞击，1=蓄力（重击由承诺位打出）
    return "charge" if int(battle.get("_proto_enemy_step", 0)) >= 1 else "bash"


def describe(state: Dict, data: Dict) -> Optional[Dict]:
    """给 /api/combat 的拍面信息：AP、本拍意图与费用。非战斗/未启用返回 None。"""
    cfg = beat_config(data)
    if not cfg:
        return None
    battle, enemy = _battle_enemy(state, data)
    if not battle or not enemy:
        return {"enabled": True, "ap": int(state.get("_proto_ap", cfg.get("ap_start", 1))),
                "ap_max": int(cfg.get("ap_max", 3)), "in_battle": False}
    if state.get(DETACH_KEY) is not None:
        return {"enabled": True, "ap": int(state.get("_proto_ap", 0)),
                "ap_max": int(cfg.get("ap_max", 3)), "in_battle": False, "detached": True}
    beat = int(battle.get("_proto_beat", 1))
    intent = decide(cfg, battle, state, enemy)
    label, hint = INTENT_TEXT.get(intent, (intent, ""))
    flaw = battle.get("_proto_enemy_flaw_beat") == beat
    player_flaw = battle.get("_proto_player_flaw_beat") == beat
    struggle = battle.get("_proto_struggle_beat") == beat
    hidden_hint = telegraph(intent, beat)
    if flaw:
        hidden_hint = f"你看穿了它的动作：{label}。{hint}"
    elif player_flaw:
        hidden_hint = "你上一拍重击落空、重心不稳：" + hidden_hint
    return {
        "enabled": True, "in_battle": True,
        "ap": int(state.get("_proto_ap", cfg.get("ap_start", 1))),
        "ap_max": int(cfg.get("ap_max", 3)),
        "beat": beat,
        "charged": battle.get("_proto_charge") == "readied",
        "enemy_flaw": flaw,
        "player_flaw": player_flaw,
        "struggle": struggle,
        "intent": intent,
        "intent_label": "敌人的动作",
        "intent_hint": hidden_hint,
        "costs": {a: _effective_cost(cfg, battle, a, beat) for a in ACTIONS},
    }


def _player_stats(state: Dict) -> Dict[str, int]:
    return {"hp": int(state.get("player_hp", 50)),
            "max_hp": int(state.get("player_max_hp", 50)),
            "attack": int(state.get("player_attack", 5)),
            "defense": int(state.get("player_defense", 2))}


def _kill_resolve(combat_system, state: Dict, data: Dict, battle: Dict,
                  enemy: Dict, response: Dict[str, Any]) -> None:
    """玩家击杀结算（复制自 combat_system.player_attack 击杀段）。
    同时下发结构化 rewards 供前端战斗结算弹窗渲染（金币自动入账，
    物品落在当前场景地上，由玩家在弹窗中点击拾取）。"""
    response["log"].append(f"【{enemy['name']}】被你打倒了！")
    combat_system._bus.publish("COMBAT_DEATH", dead=enemy["id"])
    combat_system._bus.publish("ENEMY_KILLED", enemy_id=enemy["id"])
    state.setdefault("killed_enemies", [])
    if enemy["id"] not in state["killed_enemies"]:
        state["killed_enemies"].append(enemy["id"])
    current_scene = state["current_scene"]
    reward_items = []
    for item_id in enemy.get("reward_items", []):
        state.setdefault("scene_item_states", {}).setdefault(current_scene, []).append(item_id)
        item_data = data["items"][item_id]
        reward_items.append({"id": item_id, "name": item_data["name"],
                             "description": item_data.get("description", "")})
        response["log"].append(f"战利品掉落：【{item_data['name']}】出现在地上！")
    reward_gold = int(enemy.get("reward_gold", 0))
    if reward_gold > 0:
        state["player_gold"] = state.get("player_gold", 0) + reward_gold
        response["log"].append(
            f"战利品：金币 +{reward_gold}（当前金币：{state['player_gold']}）")
    combat_system.end_battle()
    state.pop(DETACH_KEY, None)
    response["victory"] = {
        "enemy": enemy.get("name", enemy["id"]),
        "gold": reward_gold,
        "items": reward_items,
    }


def _enemy_damage_taken(cfg: Dict, action: str, intent: str, state: Dict,
                        enemy: Dict) -> int:
    """本拍玩家承受的伤害（由决策器锁定的 intent 决定）。
    蓄力/喘息/蜷缩/闪避拍敌人不出手为 0；玩家闪避对一切攻击为 0；格挡对撞击与重击都
    "先减防再减半"（重击 10→4）；攻击/蓄力/脱离均无防护，吃全额。"""
    if intent in ("brace", "charge", "recover", "dodge"):
        return 0
    if intent == "heavy":
        if action == "dodge":
            return 0
        # 格挡：先减防御力再减半（与撞击格挡同公式），def 仍生效；其余动作吃全额
        if action == "block":
            pdef = int(state.get("player_defense", 0))
            return max(1, int(cfg.get("heavy_damage", 10)) - pdef) // 2
        return int(cfg.get("heavy_damage", 10))        # attack/charge/disengage
    # bash
    raw = max(1, int(enemy.get("attack", 1)) - int(state.get("player_defense", 0)))
    if action == "dodge":
        return 0
    if action == "block":
        return raw // 2
    return raw                                       # attack/charge/disengage


def _heavy_preview(cfg: Dict, state: Dict, data: Dict, enemy: Dict) -> int:
    """按背包第一把武器预算重击伤害（仅用于蓄力成功的提示文案）。"""
    items = data.get("items") or {}
    weapon = next((items[i] for i in state.get("player_inventory", [])
                   if items.get(i, {}).get("is_weapon")), {})
    bonus = int(cfg.get("player_heavy_bonus", 2))
    mult = int(cfg.get("player_heavy_mult", 2))
    return max(1, int(weapon.get("damage", 5)) + bonus - int(enemy.get("defense", 0))) * mult


def resolve(combat_system, state: Dict, data: Dict, action: str,
            weapon_id: str = "") -> Dict[str, Any]:
    """结算一个 beat。返回兼容前端战斗面板的响应 dict。"""
    response: Dict[str, Any] = {"success": False, "player_dead": False,
                                "message": "", "log": []}
    cfg = beat_config(data)
    if not cfg:
        response["message"] = "当前工程未启用 Beat 战斗"
        return response
    battle, enemy = _battle_enemy(state, data)
    if not battle:
        response["message"] = "当前没有战斗"
        return response
    if state.get(DETACH_KEY) is not None:
        response["message"] = "你已经脱离战斗了"
        return response
    if action not in ACTIONS:
        response["message"] = f"未知动作：{action}"
        return response

    current_scene = state.get("current_scene", "")
    if enemy["id"] not in (data.get("scenes", {}).get(current_scene, {})
                           .get("enemies_here", [])):
        response["message"] = "你眼下不在战斗中"
        return response

    beat = int(battle.get("_proto_beat", 1))
    # 拍首锁定：本拍敌人动作由决策器一次性决定，拍内状态变化不再改招
    player_charged_at_lock = battle.get("_proto_charge") == "readied"
    intent = decide(cfg, battle, state, enemy)
    ap = int(state.get("_proto_ap", 0))
    cost = _effective_cost(cfg, battle, action, beat)
    if ap < cost:
        base = _cost(cfg, action)
        hint = "（挣扎折扣后仍不足）" if cost != base else ""
        response["message"] = f"行动力不足{hint}（需要 {cost}，当前 {ap}）"
        return response
    state["_proto_ap"] = ap - cost
    enemy_name = enemy.get("name", enemy["id"])

    # 蓄力就绪只保留下一拍：只有本拍出「攻击」能兑现；其余动作（含再次蓄力）
    # 都让旧蓄力过期清空，不能叠加、不能囤
    readied = battle.get("_proto_charge") == "readied"
    if action != "charge":
        battle["_proto_charge"] = None

    # 承诺/打断标记在本拍锁定后即视为消费（下一拍状态由拍末推进重新设定）
    battle["_proto_enemy_promised"] = False
    battle["_proto_enemy_promised_bash"] = False

    # ---------- 玩家进攻（先结算数值，伤害与敌人同时生效，不因击杀提前结束）----------
    p_damage = 0
    if action == "attack":
        weapon = (data.get("items") or {}).get(weapon_id, {})
        if weapon_id not in state.get("player_inventory", []):
            state["_proto_ap"] = ap   # 校验失败退还 AP
            response["message"] = f"你没有【{weapon.get('name', weapon_id)}】"
            return response
        if not weapon.get("is_weapon"):
            state["_proto_ap"] = ap
            response["message"] = f"【{weapon.get('name', weapon_id)}】不是武器"
            return response
        # 闪避躲过一切攻击（含重击）；普攻打不动蜷缩；重击克制蜷缩。
        # 蓄力/喘息拍不防御，正常吃伤害
        if intent == "dodge":
            if readied:
                response["log"].append(
                    f"你全力砸出的重击被【{enemy_name}】侧身躲开，一击落空！"
                    "用力过猛让你下一拍露出破绽。")
                battle["_proto_player_flaw_beat"] = beat + 1
            else:
                response["log"].append(
                    f"【{enemy_name}】身形一晃，你的攻击落了空！")
        elif intent == "brace" and not readied:
            response["log"].append(
                f"【{enemy_name}】蜷成一团，你的攻击被弹开，没有造成伤害！")
        else:
            bonus = int(cfg.get("player_heavy_bonus", 2)) if readied else 0
            mult = int(cfg.get("player_heavy_mult", 2)) if readied else 1
            p_damage = max(1, int(weapon.get("damage", 5)) + bonus - int(enemy["defense"])) * mult
            if readied:
                response["log"].append(
                    f"你踏前一步全力出手，蓄力化为重击，对【{enemy_name}】造成 **{p_damage}** 点伤害！")
            else:
                response["log"].append(
                    f"你用【{weapon['name']}】对【{enemy_name}】造成 **{p_damage}** 点伤害！")
            combat_system._bus.publish("COMBAT_DAMAGE",
                                       attacker="player", defender=enemy["id"], damage=p_damage)
            # 打到蓄力拍 = 打断下一拍重击（打断状态由拍末按 p_damage 统一写入）
            if intent == "charge":
                response["log"].append("这一击正中蓄力中的【%s】，它的重击被打断了！" % enemy_name)
    elif action == "charge":
        response["log"].append("你沉身拧腰、暗暗蓄力，等待致命的一拍……")

    # ---------- 敌人行动（同拍揭晓）----------
    taken = _enemy_damage_taken(cfg, action, intent, state, enemy)
    if intent == "recover":
        response["log"].append(
            f"【{enemy_name}】蓄力被打散，攻势全消，缩起身子喘息——这一拍它没有出手。")
    elif intent == "brace":
        response["log"].append(f"【{enemy_name}】缩起身子严密防守，没有出手。")
        if action == "dodge":
            response["log"].append("你扑了个空——宝贵的行动力白白浪费了。")
    elif intent == "charge":
        if action == "dodge":
            response["log"].append("你摆开闪避架势，可它只顾着蓄力，行动力白白浪费了。")
        elif p_damage == 0 and action != "attack":
            response["log"].append(f"【{enemy_name}】鼓着肚皮蓄力，对你这一拍的动静毫无防备。")
    elif intent == "dodge":
        if action == "attack":
            pass  # 落空文案已在进攻段写下
        elif action == "dodge":
            response["log"].append("两边都在躲——这一拍谁也没碰到谁。")
        elif action == "block":
            response["log"].append(f"【{enemy_name}】跳开了，你的格挡对着空处。")
        elif action == "charge":
            response["log"].append(f"【{enemy_name}】忙着闪身，没有出手——你的蓄力没有被打断。")
        elif action == "disengage":
            response["log"].append(f"【{enemy_name}】忙着闪身，没有封你的退路。")
        else:
            response["log"].append(f"【{enemy_name}】侧身躲开，这一拍没有出手。")
    elif intent == "heavy":
        if action == "dodge":
            response["log"].append(
                f"【{enemy_name}】的重击轰然落空，你毫发无伤！它用力过猛露出破绽，下一拍的动作你将一目了然。")
            battle["_proto_enemy_flaw_beat"] = beat + 1
        elif action == "block":
            response["log"].append(f"你勉强架住重击，防御力卸掉大半劲道，受到 **{taken}** 点伤害！")
        elif action == "charge":
            response["log"].append(f"重击砸在你身上，造成 **{taken}** 点伤害！")
        elif action == "disengage":
            response["log"].append(f"重击封住了你的退路，你受到 **{taken}** 点伤害！")
        else:
            response["log"].append(f"【{enemy_name}】的重击砸实了，你受到 **{taken}** 点伤害！")
    else:  # bash
        if action == "dodge":
            response["log"].append(f"你侧身一闪，【{enemy_name}】的撞击落空！")
        elif action == "block":
            response["log"].append(f"你架住撞击，受到 **{taken}** 点伤害。")
        elif action == "charge":
            response["log"].append(f"撞击打断了你的架势，你受到 **{taken}** 点伤害！")
        elif action == "disengage":
            response["log"].append(f"撞击缠住了你的脚步，你受到 **{taken}** 点伤害！")
        else:
            response["log"].append(f"【{enemy_name}】抢上一步撞来，对你造成 **{taken}** 点伤害！")

    # ---------- 双方伤害同时生效，死亡一起判定（结算顺序不决定谁先死）----------
    battle["enemy_hp"] -= p_damage
    new_hp = int(state.get("player_hp", 0)) - taken
    state["player_hp"] = new_hp
    if taken > 0:
        combat_system._bus.publish("COMBAT_DAMAGE",
                                   attacker=enemy["id"], defender="player", damage=taken)

    enemy_dead = battle["enemy_hp"] <= 0 and p_damage > 0
    player_dead = new_hp <= 0

    if enemy_dead:
        _kill_resolve(combat_system, state, data, battle, enemy, response)
        response["defeated"] = enemy["id"]
    if player_dead:
        combat_system._bus.publish("COMBAT_DEATH", dead="player")
        combat_system._handle_player_death(response)
    if enemy_dead:
        response["success"] = True
        response["ap"] = state["_proto_ap"]
        response["charged"] = False
        # 同归于尽：不做 1HP 硬保底（留给以后装备/光环 buff），按惨胜处理——
        # 敌人掉落照常、玩家回酒馆满血复活
        response["message"] = ("惨胜！【%s】被你打倒，但你也力竭倒下，被人抬回了村口。"
                               % enemy_name) if player_dead else "战斗胜利！"
        return response
    if player_dead:
        return response

    # ---------- 动作专属收尾 ----------
    if action == "disengage":
        if taken == 0:
            # 本拍没受伤（敌人蜷缩/蓄力等不出手的拍子）→ 成功脱离，不推进拍
            state[DETACH_KEY] = int(state.get("game_time", 0))
            response["log"].append("趁它没有出手，你抽身急退，成功脱离了战斗。")
            response["success"] = True
            response["fled"] = True
            response["message"] = "你抓住空隙，成功脱离了战斗。"
            response["ap"] = state["_proto_ap"]
            response["charged"] = False
            return response
        battle["_proto_struggle_beat"] = beat + 1
        response["log"].append(
            "你被攻击缠住，没能脱离。挣扎之间你调整了脚步——下一拍闪避或脱离少花 1 点行动力。")
        response["message"] = "脱离失败！敌人的攻击缠住了你。"
    elif action == "charge":
        if taken == 0:
            battle["_proto_charge"] = "readied"
            preview = _heavy_preview(cfg, state, data, enemy)
            response["log"].append(
                f"蓄力完成！下一拍选择攻击，将打出 **{preview}** 点重击。")
            response["message"] = "蓄力就绪"
        else:
            battle["_proto_charge"] = None
            response["log"].append("蓄力被打断了，下一拍的攻击没有加成。")
            response["message"] = "蓄力被打断"

    # ---------- 拍末：敌人节奏推进（决策状态机的唯一写入点）----------
    battle["_proto_enemy_interrupted"] = False
    if intent == "charge":
        if p_damage > 0:
            # 蓄力拍被攻击命中 → 下拍喘息，节奏从头再来
            battle["_proto_enemy_interrupted"] = True
            battle["_proto_enemy_step"] = 0
        else:
            # 蓄力成功 → 下拍承诺重击（不可取消）
            battle["_proto_enemy_promised"] = True
    elif intent == "dodge":
        # 闪避成功 → 下拍承诺撞击（不可再躲）
        battle["_proto_enemy_promised_bash"] = True
    elif intent in ("heavy", "recover"):
        battle["_proto_enemy_step"] = 0
    elif intent == "bash" and not player_charged_at_lock:
        # 节奏内的撞击（非"玩家蓄力"临时反制）→ 下拍进入蓄力
        battle["_proto_enemy_step"] = 1
    # brace 与临时 bash 不推进节奏步

    # ---------- 拍末：推进拍序号、AP 回复、过期一拍状态清理 ----------
    next_beat = beat + 1
    # 破绽/挣扎都只在"下一拍"生效；过期（如连续脱离失败只保留最近一次，不叠加）
    if battle.get("_proto_enemy_flaw_beat") not in (None, next_beat):
        battle.pop("_proto_enemy_flaw_beat", None)
    if battle.get("_proto_player_flaw_beat") not in (None, next_beat):
        battle.pop("_proto_player_flaw_beat", None)
    if battle.get("_proto_struggle_beat") not in (None, next_beat):
        battle.pop("_proto_struggle_beat", None)
    battle["_proto_beat"] = next_beat
    ap_max = int(cfg.get("ap_max", 3))
    state["_proto_ap"] = min(ap_max, state["_proto_ap"] + int(cfg.get("ap_per_beat", 1)))

    response["success"] = True
    if not response["message"]:
        response["message"] = "回合结束"
    response["player"] = _player_stats(state)
    response["ap"] = state["_proto_ap"]
    response["beat"] = battle["_proto_beat"]
    response["charged"] = battle.get("_proto_charge") == "readied"
    response["enemy_flaw"] = battle.get("_proto_enemy_flaw_beat") == battle["_proto_beat"]
    response["player_flaw"] = battle.get("_proto_player_flaw_beat") == battle["_proto_beat"]
    response["struggle"] = battle.get("_proto_struggle_beat") == battle["_proto_beat"]
    response["next_intent"] = decide(cfg, battle, state, enemy)
    return response
