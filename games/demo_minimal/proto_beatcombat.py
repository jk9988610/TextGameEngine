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
# 敌人动作表（史莱姆，4 拍循环 撞→撞→重击→蜷缩；第二刀再做敌人蓄力对称化）：
#   撞击伤害 max(1,攻-防)=4（格挡减半）；重击 10（格挡先减防再减半 = 4，可被闪避
#   完全躲开，闪避后重击者露破绽）；蜷缩 = 本拍只防不攻。
#
# 两个一拍状态（第二刀起双向化）：
#   破绽：重击被闪避 → 重击者下一拍动作被看穿（当前只给拍面提示；敌人有决策器后
#         才有实质情报增量）。只给信息，无任何数值增益。
#   挣扎：脱离失败（本拍受伤）→ 下一拍闪避/脱离消耗 -1，不叠加、不用即过期，
#         不突破 AP 上限。
#
# 状态（随存档走）：
#   game_state["_proto_ap"]                 —— 当前 AP
#   current_battle["_proto_beat"]           —— 本场交战的拍序号（1 起，接战重置）
#   current_battle["_proto_charge"]         —— "readied"=蓄力就绪（仅下一拍有效），否则 None
#   current_battle["_proto_enemy_flaw_beat"]—— 敌人破绽生效的拍序号（重击被闪避后的下一拍）
#   current_battle["_proto_struggle_beat"]  —— 玩家挣扎生效的拍序号（脱离失败后的下一拍）
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
    "heavy": ("蓄力重击", "重击 10 点，格挡无效，只有闪避能完全避开"),
    "brace": ("蜷缩防守", "这一拍它缩起身子不攻击，普攻无效，是脱离/蓄力的窗口"),
}
ACTIONS = ("attack", "block", "dodge", "charge", "disengage")


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
        battle.pop("_proto_enemy_flaw_beat", None)
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


def _intent(cfg: Dict, beat: int) -> str:
    pattern = cfg.get("pattern", ["bash"])
    return pattern[(int(beat) - 1) % len(pattern)]


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
    intent = _intent(cfg, beat)
    label, hint = INTENT_TEXT.get(intent, (intent, ""))
    flaw = battle.get("_proto_enemy_flaw_beat") == beat
    struggle = battle.get("_proto_struggle_beat") == beat
    return {
        "enabled": True, "in_battle": True,
        "ap": int(state.get("_proto_ap", cfg.get("ap_start", 1))),
        "ap_max": int(cfg.get("ap_max", 3)),
        "beat": beat,
        "charged": battle.get("_proto_charge") == "readied",
        "enemy_flaw": flaw,
        "struggle": struggle,
        "intent": intent, "intent_label": label,
        "intent_hint": ("它上一拍重击扑空露出了破绽，这一拍动作你已看穿：" if flaw else "") + hint,
        "costs": {a: _effective_cost(cfg, battle, a, beat) for a in ACTIONS},
    }


def _player_stats(state: Dict) -> Dict[str, int]:
    return {"hp": int(state.get("player_hp", 50)),
            "max_hp": int(state.get("player_max_hp", 50)),
            "attack": int(state.get("player_attack", 5)),
            "defense": int(state.get("player_defense", 2))}


def _kill_resolve(combat_system, state: Dict, data: Dict, battle: Dict,
                  enemy: Dict, response: Dict[str, Any]) -> None:
    """玩家击杀结算（复制自 combat_system.player_attack 击杀段）。"""
    response["log"].append(f"【{enemy['name']}】被你打倒了！")
    combat_system._bus.publish("COMBAT_DEATH", dead=enemy["id"])
    combat_system._bus.publish("ENEMY_KILLED", enemy_id=enemy["id"])
    state.setdefault("killed_enemies", [])
    if enemy["id"] not in state["killed_enemies"]:
        state["killed_enemies"].append(enemy["id"])
    current_scene = state["current_scene"]
    for item_id in enemy.get("reward_items", []):
        state.setdefault("scene_item_states", {}).setdefault(current_scene, []).append(item_id)
        response["log"].append(
            f"战利品掉落：【{data['items'][item_id]['name']}】出现在地上！")
    reward_gold = int(enemy.get("reward_gold", 0))
    if reward_gold > 0:
        state["player_gold"] = state.get("player_gold", 0) + reward_gold
        response["log"].append(
            f"战利品：金币 +{reward_gold}（当前金币：{state['player_gold']}）")
    combat_system.end_battle()
    state.pop(DETACH_KEY, None)


def _enemy_damage_taken(cfg: Dict, action: str, intent: str, state: Dict,
                        enemy: Dict) -> int:
    """本拍玩家承受的伤害。
    闪避对一切攻击为 0；蜷缩拍敌人不出手为 0；格挡对撞击与重击都"先减防再减半"
    （重击 10→4）；攻击/蓄力/脱离均无防护，吃全额。"""
    if intent == "brace":
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
    intent = _intent(cfg, beat)
    label, _ = INTENT_TEXT.get(intent, (intent, ""))
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

    # ---------- 玩家出手 ----------
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
        # 普攻打不动蜷缩；重击克制一切防御姿态，蜷缩也吃全额
        if intent == "brace" and not readied:
            response["log"].append(
                f"【{enemy_name}】蜷成一团，你的攻击被弹开，没有造成伤害！")
        else:
            bonus = int(cfg.get("player_heavy_bonus", 2)) if readied else 0
            mult = int(cfg.get("player_heavy_mult", 2)) if readied else 1
            p_damage = max(1, int(weapon.get("damage", 5)) + bonus - int(enemy["defense"])) * mult
            battle["enemy_hp"] -= p_damage
            if readied:
                response["log"].append(
                    f"你踏前一步全力出手，蓄力化为重击，对【{enemy_name}】造成 **{p_damage}** 点伤害！")
            else:
                response["log"].append(
                    f"你用【{weapon['name']}】对【{enemy_name}】造成 **{p_damage}** 点伤害！")
            combat_system._bus.publish("COMBAT_DAMAGE",
                                       attacker="player", defender=enemy["id"], damage=p_damage)
            if battle["enemy_hp"] <= 0:
                _kill_resolve(combat_system, state, data, battle, enemy, response)
                response["success"] = True
                response["message"] = "战斗胜利！"
                response["defeated"] = enemy["id"]
                response["ap"] = state["_proto_ap"]
                response["charged"] = False
                return response
    elif action == "charge":
        response["log"].append("你沉身拧腰、暗暗蓄力，等待致命的一拍……")

    # ---------- 敌人行动（同拍揭晓）----------
    taken = _enemy_damage_taken(cfg, action, intent, state, enemy)
    if intent == "brace":
        response["log"].append(f"【{enemy_name}】缩起身子严密防守，没有出手。")
        if action == "dodge":
            response["log"].append("你扑了个空——宝贵的行动力白白浪费了。")
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

    new_hp = int(state.get("player_hp", 0)) - taken
    state["player_hp"] = new_hp
    if taken > 0:
        combat_system._bus.publish("COMBAT_DAMAGE",
                                   attacker=enemy["id"], defender="player", damage=taken)
    if new_hp <= 0:
        combat_system._bus.publish("COMBAT_DEATH", dead="player")
        combat_system._handle_player_death(response)
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

    # ---------- 拍末：推进拍序号、AP 回复、过期一拍状态清理 ----------
    next_beat = beat + 1
    # 破绽/挣扎都只在"下一拍"生效；过期（如连续脱离失败只保留最近一次，不叠加）
    if battle.get("_proto_enemy_flaw_beat") not in (None, next_beat):
        battle.pop("_proto_enemy_flaw_beat", None)
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
    response["struggle"] = battle.get("_proto_struggle_beat") == battle["_proto_beat"]
    response["next_intent"] = _intent(cfg, battle["_proto_beat"])
    return response
