# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：先后手（按攻击判定） —— 待试玩验证后抽离，勿当通用API
#
# 《森林试炼》专用的「回合先后手」阶段一原型（两阶段开发纪律，见
# .trae/documents/ai_handoff_game_extension.md §3）。
#
# 玩法规则（用户拍板，刻意不引入新属性）：
#   - 每回合比较 attack：攻击高的一方先出手；攻击相同则玩家先；
#   - 仍是一回合一人一下，只是敌人更快时顺序翻转为「敌人打 → 玩家动作」：
#       攻击：先挨全额一击，再出手（敌人被这一击打死则无后续，引擎同理）
#       喝药：先挨全额一击，再回血（可能来不及，喝药失败也已挨打）
#       脱离：先挨全额一击，存活才脱离（脱离本身仍必定成功、无消耗）
#       防御：与先后手无关——架盾本就先于攻击，那一击减半，走 proto_defend
#   - demo 数值：玩家 attack=5，史莱姆 attack=6 → 森林里敌人恒先手。
#
# 为什么需要本模块：engine/combat_system.py 的 player_attack 把"敌人反击"
# 写死在玩家出手之后，敌人先手时无法靠调用它实现顺序翻转（会变成挨打两下）。
# 阶段一不动 engine：玩家更快时一律走引擎原函数（主路径零改动）；敌人更快时
# 走本模块的回合器。下方玩家打击+击杀结算复制自 combat_system.player_attack
# 对应段落，阶段二统一回合执行器时本文件整段删除替换。
#
# 隔离设计：不 import 其它原型模块（脱离/防御的组合由 app.py 路由层编排）；
# 不 import engine；仅经注入实例与 state/data 字典工作，复用 combat 的
# _bus / _handle_player_death / end_battle，与既有原型同口径。
"""回合先后手原型（《森林试炼》专用，阶段一）。"""
from typing import Any, Dict, Optional

CONFIG_KEY = "_proto_turnorder"


def turnorder_config(game_data: Dict) -> Optional[Dict]:
    cfg = (game_data.get("config") or {}).get(CONFIG_KEY)
    return cfg if isinstance(cfg, dict) and cfg.get("enabled") else None


def _current_enemy(state: Dict, data: Dict):
    battle = state.get("current_battle")
    if not battle:
        return None, None
    return battle, (data.get("enemies") or {}).get(battle.get("enemy_id", ""))


def enemy_attacks_first(state: Dict, data: Dict) -> bool:
    """本回合敌人是否先手：敌人攻击严格大于玩家攻击；相同/更小则玩家先。"""
    if not turnorder_config(data):
        return False
    _, enemy = _current_enemy(state, data)
    if not enemy:
        return False
    return int(enemy.get("attack", 0)) > int(state.get("player_attack", 0))


def _player_stats(state: Dict) -> Dict[str, int]:
    return {
        "hp": int(state.get("player_hp", 50)),
        "max_hp": int(state.get("player_max_hp", 50)),
        "attack": int(state.get("player_attack", 5)),
        "defense": int(state.get("player_defense", 2)),
    }


def enemy_strike(combat_system, state: Dict, data: Dict) -> Dict[str, Any]:
    """敌人先制的全额一击（攻击/喝药/脱离前调用）。死亡时走引擎统一死亡流程。"""
    response: Dict[str, Any] = {"success": False, "player_dead": False,
                                "message": "", "log": []}
    _, enemy = _current_enemy(state, data)
    if not enemy:
        response["message"] = "敌人不见了"
        return response
    enemy_name = enemy.get("name", enemy.get("id", "敌人"))
    raw = max(1, int(enemy.get("attack", 1)) - int(state.get("player_defense", 0)))
    new_hp = int(state.get("player_hp", 0)) - raw
    state["player_hp"] = new_hp
    response["log"].append(f"【{enemy_name}】眼疾手快，抢先对你造成 **{raw}** 点伤害！")
    combat_system._bus.publish("COMBAT_DAMAGE",
                               attacker=enemy.get("id", ""), defender="player", damage=raw)
    if new_hp <= 0:
        combat_system._bus.publish("COMBAT_DEATH", dead="player")
        combat_system._handle_player_death(response)
    else:
        response["success"] = True
        response["player"] = _player_stats(state)
    return response


def _player_strike_and_kill(combat_system, state: Dict, data: Dict,
                            weapon_id: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """敌人先挨打后，玩家出手（含击杀结算）。复制自 combat_system.player_attack
    的武器校验+玩家打击+击杀段，仅去掉本回合不该再有的敌人反击。"""
    battle, enemy = _current_enemy(state, data)
    weapon = (data.get("items") or {}).get(weapon_id, {})
    if weapon_id not in state.get("player_inventory", []):
        response["message"] = f"你没有【{weapon.get('name', weapon_id)}】"
        return response
    if not weapon.get("is_weapon"):
        response["message"] = f"【{weapon.get('name', weapon_id)}】不是武器"
        return response

    p_damage = max(1, int(weapon.get("damage", 5)) - int(enemy["defense"]))
    battle["enemy_hp"] -= p_damage
    response["log"].append(
        f"你用【{weapon['name']}】对【{enemy['name']}】造成 **{p_damage}** 点伤害！")
    combat_system._bus.publish("COMBAT_DAMAGE",
                               attacker="player", defender=enemy["id"], damage=p_damage)

    if battle["enemy_hp"] <= 0:
        response["log"].append(f"【{enemy['name']}】被你打倒了！")
        combat_system._bus.publish("COMBAT_DEATH", dead=enemy["id"])
        combat_system._bus.publish("ENEMY_KILLED", enemy_id=enemy["id"])
        state.setdefault("killed_enemies", [])
        if enemy["id"] not in state["killed_enemies"]:
            state["killed_enemies"].append(enemy["id"])
        current_scene = state["current_scene"]
        for item_id in enemy.get("reward_items", []):
            state.setdefault("scene_item_states", {}).setdefault(current_scene, []).append(item_id)
            item_name = data["items"][item_id]["name"]
            response["log"].append(f"战利品掉落：【{item_name}】出现在地上！")
        reward_gold = int(enemy.get("reward_gold", 0))
        if reward_gold > 0:
            state["player_gold"] = state.get("player_gold", 0) + reward_gold
            response["log"].append(
                f"战利品：金币 +{reward_gold}（当前金币：{state['player_gold']}）")
        combat_system.end_battle()
        response["success"] = True
        response["message"] = "战斗胜利！"
        response["defeated"] = enemy["id"]
        return response

    response["success"] = True
    response["message"] = "回合结束"
    response["player"] = _player_stats(state)
    return response


def attack_enemy_first(combat_system, state: Dict, data: Dict,
                       weapon_id: str) -> Dict[str, Any]:
    """敌人先手的攻击回合：先挨打 → 存活则玩家出手（无二次反击）。"""
    response: Dict[str, Any] = {"success": False, "message": "", "log": []}
    battle, enemy = _current_enemy(state, data)
    if not battle:
        response["message"] = "当前没有战斗"
        return response
    if not enemy:
        combat_system.end_battle()
        response["message"] = "敌人不见了"
        return response

    strike = enemy_strike(combat_system, state, data)
    response["log"].extend(strike.get("log", []))
    if strike.get("player_dead"):
        # 死亡消息/标记以引擎统一死亡处理为准
        response["success"] = False
        response["player_dead"] = True
        response["message"] = strike.get("message", "")
        return response

    return _player_strike_and_kill(combat_system, state, data, weapon_id, response)


def drink_enemy_first(combat_system, item_system, state: Dict, data: Dict,
                      item_id: str) -> Dict[str, Any]:
    """敌人先手的喝药回合：先挨打 → 存活再喝药（满血/没有则白挨打，消息说明）。"""
    response: Dict[str, Any] = {"success": False, "message": "", "log": []}
    battle, enemy = _current_enemy(state, data)
    if not battle:
        response["message"] = "当前没有战斗"
        return response

    strike = enemy_strike(combat_system, state, data)
    response["log"].extend(strike.get("log", []))
    if strike.get("player_dead"):
        response["player_dead"] = True
        response["message"] = strike.get("message", "")
        return response

    used = item_system.use_item(item_id, enemy["id"])
    if not used.get("success"):
        # 敌人已经先手打完，这瓶药没喝成（如已满血）；回合仍算发生（计时/存档）
        response["success"] = True
        response["message"] = f"你来不及喝药：{used.get('message', '')}"
        response["player"] = _player_stats(state)
        return response

    response["log"].append(used.get("message", "你使用了物品。"))
    response["success"] = True
    response["message"] = "回合结束"
    response["player"] = _player_stats(state)
    return response
