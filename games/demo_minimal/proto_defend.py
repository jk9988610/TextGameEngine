# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：战斗防御 —— 待试玩验证后抽离，勿当通用API
#
# 《森林试炼》专用的「战斗防御」阶段一原型（两阶段开发纪律，见
# .trae/documents/ai_handoff_game_extension.md §3）。
#
# 玩法规则（用户拍板）：
#   - 战斗面板有「防御」按钮；无消耗、无次数限制；
#   - 防御 = 本回合放弃攻击，敌人照常攻击，但伤害按 reduction（0.5）减半、
#     向下取整，可以为 0；
#   - 一次性结算、无跨回合状态：防御不叠加、不留 buff，本回合吃完伤害即结束，
#     因此与攻击/喝药/脱离天然互斥（同一个回合只能选一个行动）；
#   - 防御占用一个战斗回合（60 秒，由路由层计时）；被打死仍走引擎统一死亡流程。
#
# 隔离设计：
#   - 本模块不 import 任何 engine 模块；只通过注入实例与 game_state/game_data
#     字典工作，唯一例外是复用 combat_system._handle_player_death()；
#   - 与 proto_retreat 零耦合：防御接口在"已脱离（面板 inactive）"态直接拒答，
#     不读不写脱离模块的任何状态；
#   - 配置只在 game_config.json 的 "_proto_defend" 块；非本工程安全降级。
"""战斗防御原型（《森林试炼》专用，阶段一）。"""
from typing import Any, Dict, Optional

CONFIG_KEY = "_proto_defend"


def defend_config(game_data: Dict) -> Optional[Dict]:
    """返回当前工程的防御配置；未启用/非本工程时返回 None。"""
    cfg = (game_data.get("config") or {}).get(CONFIG_KEY)
    if isinstance(cfg, dict) and cfg.get("enabled"):
        return cfg
    return None


def _player_defense(state: Dict) -> int:
    return int(state.get("player_defense", 2))


def _player_hp(state: Dict) -> int:
    return int(state.get("player_hp", 50))


def try_defend(combat_system, game_state: Dict, game_data: Dict) -> Dict[str, Any]:
    """本回合防御：不攻击，承受减半后的敌人伤害。

    返回与 combat_system.player_attack 同构的 dict：
      success/player_dead/log/message/player
    """
    response: Dict[str, Any] = {"success": False, "player_dead": False,
                                "message": "", "log": []}

    if not defend_config(game_data):
        response["message"] = "这里无法防御"
        return response

    battle = game_state.get("current_battle")
    if not battle:
        response["message"] = "当前没有战斗"
        return response

    enemy_id = battle.get("enemy_id", "")
    enemy = (game_data.get("enemies") or {}).get(enemy_id)
    if not enemy:
        combat_system.end_battle()
        response["message"] = "敌人不见了"
        return response

    # 已脱离休整态（面板 inactive，正常走不到）——防御拒答，两个原型互不串状态
    detached = game_state.get("_proto_detached_at")
    if detached is not None:
        response["message"] = "你已经脱离战斗了"
        return response

    # 仅同场可防御（与战斗面板 active 判定一致）
    current_scene = game_state.get("current_scene", "")
    scene_enemy_ids = (game_data.get("scenes", {})
                       .get(current_scene, {}).get("enemies_here", []))
    if enemy_id not in scene_enemy_ids:
        response["message"] = "你眼下不在战斗中"
        return response

    # ---------- 结算：先按引擎公式算原伤害 max(1, 攻-防)，再减半向下取整 ----------
    reduction = float(defend_config(game_data).get("reduction", 0.5))
    raw_damage = max(1, int(enemy.get("attack", 1)) - _player_defense(game_state))
    taken = int(raw_damage * reduction)   # 向下取整，可以为 0

    enemy_name = enemy.get("name", enemy_id)
    response["log"].append("你举盾防御，放弃了本回合的攻击。")
    response["log"].append(
        f"【{enemy_name}】的攻击被你化解大半，对你造成 **{taken}** 点伤害！")

    new_hp = _player_hp(game_state) - taken
    game_state["player_hp"] = new_hp

    if new_hp <= 0:
        # 复用引擎统一死亡流程（满血复活、清战斗），原型不另造死亡语义
        combat_system._handle_player_death(response)
        return response

    response["success"] = True
    response["message"] = "你摆出防御姿态，稳住了阵脚。"
    response["player"] = {
        "hp": new_hp,
        "max_hp": int(game_state.get("player_max_hp", 50)),
        "attack": int(game_state.get("player_attack", 5)),
        "defense": _player_defense(game_state),
    }
    return response
