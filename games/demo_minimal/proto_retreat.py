# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：战斗脱离 —— 待试玩验证后抽离，勿当通用API
#
# 《森林试炼》专用的「战斗脱离」阶段一原型（两阶段开发纪律，见
# .trae/documents/ai_handoff_game_extension.md §3）。
#
# 玩法规则（用户试玩迭代后拍板）：
#   - 战斗面板有「脱离战斗」按钮；点击必定、立即脱离，无概率、无挨打、
#     不占用战斗回合时间；
#   - 脱离 = 原地停战而不是结束战斗：玩家和敌人都留在当前场景，双方残血，
#     战斗面板关闭；引擎底层 current_battle 保留（敌人残血跨场景本就保留）；
#   - 脱离后行动累计游戏时间 recover_seconds（600 秒 = 走两个场景），
#     双方静默回满血，战斗重置为一场全新的战斗（敌人未被击杀，按钮还在）；
#   - 恢复计时期间重新点「挑战」= 带着残血重新接战，恢复计时作废。
#
# 不做"伤情/休整中"的告知 UI（用户明确不要）：恢复静默发生，前端不显示
# 休整标记/倒计时/恢复通知；玩家重新挑战时直接看 HP 条即可。
#
# 状态机（本原型新增的唯一状态，随 game_state 存档/读档）：
#   game_state["_proto_detached_at"] = 脱离时刻的 game_time（int）
#     战斗中 --脱离--> 已脱离（残血+计时）
#     已脱离 --重新挑战/攻击/喝药--> 战斗中（残血继续，清计时）
#     已脱离 --行动累计满 recover_seconds--> 无战斗（双方回满，清战斗）
#     已脱离 --离开场景--> 已脱离（引擎原生跨场景保留，计时继续）
#     战斗中 --击杀/死亡--> 无战斗（引擎原生；tick 自愈清残留标记）
#
# 隔离设计：
#   - 本模块不 import 任何 engine 模块，只通过注入实例的公开方法与
#     game_state / game_data 字典工作；唯一例外是复用
#     combat_system.end_battle()，避免复制战斗清理语义；
#   - 配置只放在本工程 game_config.json 的 "_proto_retreat" 块；非本工程
#     （没有该配置）时一切接口安全降级；
#   - 路由层在 /api/combat 把"已脱离"的战斗对外呈现为 active=false（关面板），
#     但不清底层 current_battle——这是本原型唯一的只读视图覆盖。
#
# 阶段二抽离时：删除本文件、app.py 中 PROTOTYPE-GAME 标注的接线、
# 前端标注代码与 _proto_retreat 配置后，游戏行为应被通用实现等价替换。
"""战斗脱离原型（《森林试炼》专用，阶段一）。"""
from typing import Any, Dict, Optional

CONFIG_KEY = "_proto_retreat"
DETACH_KEY = "_proto_detached_at"   # 脱离时刻的 game_time；键不存在=未脱离


def retreat_config(game_data: Dict) -> Optional[Dict]:
    """返回当前工程的脱离配置；未启用/非本工程时返回 None（调用方据此隐藏入口）。"""
    cfg = (game_data.get("config") or {}).get(CONFIG_KEY)
    if isinstance(cfg, dict) and cfg.get("enabled"):
        return cfg
    return None


def is_detached(state: Dict) -> bool:
    """是否处于"已脱离、计时恢复中"：计时标记在，且底层战斗仍保留着残血。"""
    return state.get(DETACH_KEY) is not None and bool(state.get("current_battle"))


def cancel_detach(state: Dict) -> None:
    """玩家重新接战（点挑战/攻击/喝药）→ 取消休整计时。"""
    state.pop(DETACH_KEY, None)


def try_disengage(combat_system, game_state: Dict, game_data: Dict) -> Dict[str, Any]:
    """玩家在战斗中脱离：必定、立即成功。

    返回与 combat_system.player_attack 同构的 dict（字段保持稳定）：
      success/fled —— 恒为 True（合法调用时）
      player_dead  —— 恒为 False（脱离无挨打，保留字段仅为兼容前端分支）
      log/message  —— 前端战斗日志与提示
    """
    response: Dict[str, Any] = {"success": False, "fled": False,
                                "player_dead": False, "message": "", "log": []}

    if not retreat_config(game_data):
        response["message"] = "这里无法脱离战斗"
        return response

    battle = game_state.get("current_battle")
    if not battle:
        response["message"] = "当前没有战斗"
        return response
    # 已处于休整态（理论上前端面板已关，走不到这里）——幂等拒绝
    if is_detached(game_state):
        response["message"] = "你已经脱离战斗了"
        return response

    enemy_id = battle.get("enemy_id", "")
    if not (game_data.get("enemies") or {}).get(enemy_id):
        combat_system.end_battle()
        response["message"] = "敌人不见了"
        return response

    # 仅当玩家与敌人同场（战斗面板激活态）才允许脱离
    current_scene = game_state.get("current_scene", "")
    scene_enemy_ids = (game_data.get("scenes", {})
                       .get(current_scene, {}).get("enemies_here", []))
    if enemy_id not in scene_enemy_ids:
        response["message"] = "你眼下不在战斗中"
        return response

    # ---------- 必定脱离：不清 current_battle（双方残血保留）、不传送、
    #             不解释伤情；只记下脱离时刻，由 tick_recover 静默恢复 ----------
    response["log"].append("你抽身急退，脱离了战斗。")
    game_state[DETACH_KEY] = int(game_state.get("game_time", 0))
    response["success"] = True
    response["fled"] = True
    response["message"] = "你脱离了战斗。"
    return response


def tick_recover(combat_system, game_state: Dict, game_data: Dict) -> None:
    """时间推进点调用：脱离休整时间满 → 双方静默回满、战斗重置。无返回、无通知。

    - 玩家回满：player_hp = player_max_hp（在任何场景都生效）；
    - 敌人回满：清 current_battle，下次 start_battle 自然开满血新战斗；
    - 自愈：计时标记在但战斗已不在（击杀/死亡等终止路径）→ 只清标记。
    """
    det = game_state.get(DETACH_KEY)
    if det is None:
        return

    battle = game_state.get("current_battle")
    if not battle:
        # 战斗已被击杀/死亡等路径清掉：标记是残留，静默回收
        game_state.pop(DETACH_KEY, None)
        return

    cfg = retreat_config(game_data)
    if not cfg:
        game_state.pop(DETACH_KEY, None)
        return

    need = int(cfg.get("recover_seconds", 600))
    elapsed = int(game_state.get("game_time", 0)) - int(det)
    if elapsed < need:
        return

    # ---------- 休整时间到：双方静默恢复，不发任何提示 ----------
    game_state["player_hp"] = game_state.get(
        "player_max_hp", game_state.get("player_hp", 50))
    combat_system.end_battle()
    game_state.pop(DETACH_KEY, None)
