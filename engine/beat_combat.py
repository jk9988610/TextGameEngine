# -*- coding: utf-8 -*-
"""
Beat 制战斗（引擎模块）—— 拍制（Beat）AP+意图 战斗，从《森林试炼》原型抽离。

一拍（Beat）流程：
  拍首敌人意图明牌（普通拍给模糊征兆，破绽拍才点名）→ 玩家选一个主动作
  → 同拍同时结算 → 拍末 AP 回复。AP 开场 1、每拍 +1、上限 3、跨拍保留，
  同一拍只能选一个主动作。

配置（工程 game_config.json 的 "beat_combat" 块；未启用/缺失时全部接口安全降级）：
  enabled / ap_start / ap_per_beat / ap_max / heavy_damage / low_hp_ratio /
  player_heavy_bonus / player_heavy_mult / recover_seconds（脱离休整秒数）
  actions{attack/charge/block/dodge/disengage}：label/description/tag/cost +
    effects（weapon_damage/apply_state/avoid_damage）+
    interactions（miss/interrupt/halve_damage_after_defense，支持
    charged/not_charged/hit 条件）——动作与克制关系完全由配置声明。

敌人数据字段（characters.json 里贴了 enemy 标签的角色；运行时经派生视图 enemies 读入；全部可选）：
  heavy_attack            —— 重击伤害（缺省用配置 heavy_damage）
  brain                   —— 决策器参数：rhythm / frenzy_rhythm /
                             dodge_player_charge / low_hp_mode(brace|frenzy) /
                             low_hp_ratio（未配置走引擎默认：bash→charge、
                             会躲重击、残血龟息）
  telegraphs              —— {intent: [模糊征兆文案...]}，覆盖引擎默认叙述
  intents                 —— {intent: [名称, 提示]}，破绽拍/点名时显示

状态（随存档走；旧 _proto_* 键由 migrate_state 在读档时迁移）：
  game_state["beat_ap"]            —— 当前 AP
  game_state["detached_at"]        —— 脱离时刻 game_time（脱离休整中）
  current_battle["beat"]           —— 本场交战的拍序号（1 起，接战重置）
  current_battle["charge"]         —— 蓄力状态名（如 "readied"），None=无
  current_battle["charge_expires"] —— 蓄力到期拍（含），过期即清
  current_battle["enemy_step"]     —— 敌人节奏步下标（循环 brain.rhythm）
  current_battle["enemy_promised"]       —— True=上拍蓄力成功，本拍承诺重击
  current_battle["enemy_promised_bash"]  —— True=上拍闪避，本拍承诺撞击
  current_battle["enemy_interrupted"]    —— 上拍蓄力被打断，本拍喘息
  current_battle["enemy_flaw_beat"]      —— 敌人破绽生效的拍序号
  current_battle["player_flaw_beat"]     —— 玩家破绽生效的拍序号
  current_battle["struggle_beat"]        —— 玩家挣扎生效的拍序号

边界：不 import app.py；经注入的 combat_system 公开接口（publish /
settle_kill / handle_player_death / end_battle）操作事件与结算，
不直接触碰其内部字段。
"""  # noqa: RUF002
from typing import Any, Dict, Optional

CONFIG_KEY = "beat_combat"
AP_KEY = "beat_ap"
DETACH_KEY = "detached_at"   # 脱离时刻的 game_time；键不存在=未脱离

ACTIONS = ("attack", "block", "dodge", "charge", "disengage")

INTENT_TEXT = {
    "bash": ("撞击", "这一拍它会撞过来"),
    "charge": ("蓄力中", "下一拍重击 10 点！它本拍不会出手——攻击可以打断蓄力"),
    "heavy": ("蓄力重击", "重击 10 点，格挡只受 4 点，闪避可完全避开"),
    "brace": ("蜷缩防守", "这一拍它缩起身子不攻击，普攻无效，是脱离/蓄力的窗口"),
    "recover": ("喘息", "蓄力被你打散，这一拍它毫无防备，不会出手"),
    "dodge": ("闪避", "这一拍它会躲开攻击——重击也会落空，它本拍不会出手"),
}

# 模糊征兆：普通拍面只给动态叙述，不直接显示 intent 名；按拍序确定性轮换，
# 方便复盘和测试。敌人可用 telegraphs 覆盖。
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

# ---------- 旧原型存档键迁移（幂等） ----------
_LEGACY_STATE_KEYS = {
    "_proto_ap": AP_KEY,
    "_proto_detached_at": DETACH_KEY,
}
_LEGACY_BATTLE_KEYS = {
    "_proto_beat": "beat",
    "_proto_charge": "charge",
    "_proto_charge_expires": "charge_expires",
    "_proto_enemy_step": "enemy_step",
    "_proto_enemy_promised": "enemy_promised",
    "_proto_enemy_promised_bash": "enemy_promised_bash",
    "_proto_enemy_interrupted": "enemy_interrupted",
    "_proto_enemy_flaw_beat": "enemy_flaw_beat",
    "_proto_player_flaw_beat": "player_flaw_beat",
    "_proto_struggle_beat": "struggle_beat",
}


def migrate_state(state: Dict) -> None:
    """把旧原型存档（_proto_* 键）迁移为正式键；幂等，读档/接管会话时调用。"""
    for old, new in _LEGACY_STATE_KEYS.items():
        if old in state:
            state.setdefault(new, state.pop(old))
    battle = state.get("current_battle")
    if isinstance(battle, dict):
        for old, new in _LEGACY_BATTLE_KEYS.items():
            if old in battle:
                battle.setdefault(new, battle.pop(old))


def telegraph(intent: str, beat: int, enemy: Optional[Dict] = None) -> str:
    """按拍序给同一动作选择稳定的近义动态描述；敌人可用 telegraphs 覆盖。"""
    phrases = ((enemy or {}).get("telegraphs") or {}).get(intent) \
        or TELEGRAPH_TEXT.get(intent, ("它的动作让你暂时无法判断。",))
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
    state[AP_KEY] = int(cfg.get("ap_start", 1))
    battle = state.get("current_battle")
    if battle:
        battle["beat"] = 1
        battle["charge"] = None              # 每次接战清空蓄力（含残血续战）
        battle.pop("charge_expires", None)
        battle["enemy_step"] = 0             # 敌人节奏从撞击开始
        battle["enemy_promised"] = False
        battle["enemy_promised_bash"] = False
        battle["enemy_interrupted"] = False
        battle.pop("enemy_flaw_beat", None)
        battle.pop("player_flaw_beat", None)
        battle.pop("struggle_beat", None)


def _action_def(cfg: Dict, action: str) -> Dict[str, Any]:
    """读取动作词典；旧 cost_* 字段作为兼容兜底。"""
    raw = (cfg.get("actions") or {}).get(action)
    return raw if isinstance(raw, dict) else {}


def _cost(cfg: Dict, action: str) -> int:
    action_def = _action_def(cfg, action)
    if "cost" in action_def:
        return int(action_def["cost"])
    return int(cfg.get(f"cost_{action}", 0))


def _action_payload(cfg: Dict) -> Dict[str, Dict[str, Any]]:
    """给前端的动作定义；未配置的动作仍从旧字段生成最小定义。"""
    return {
        action: {
            "label": _action_def(cfg, action).get("label", action),
            "description": _action_def(cfg, action).get("description", ""),
            "tag": _action_def(cfg, action).get("tag", ""),
            "cost": _cost(cfg, action),
        }
        for action in ACTIONS
    }


def _configured_interactions(cfg: Dict, action: str, opponent_action: str):
    """None 表示旧配置未声明交互，应走兼容逻辑；列表表示以配置为准。"""
    interactions = _action_def(cfg, action).get("interactions")
    if not isinstance(interactions, dict):
        return None
    return interactions.get(opponent_action, [])


def _interaction_enabled(cfg: Dict, action: str, opponent_action: str,
                         effect_type: str, context: Optional[Dict] = None) -> Optional[bool]:
    """查动作交互声明；支持 charged/not_charged/hit 三种局部条件。"""
    rules = _configured_interactions(cfg, action, opponent_action)
    if rules is None:
        return None
    context = context or {}
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != effect_type:
            continue
        condition = rule.get("when")
        if condition == "charged" and not context.get("charged"):
            continue
        if condition == "not_charged" and context.get("charged"):
            continue
        if condition == "hit" and not context.get("hit"):
            continue
        return True
    return False


def _weapon_damage(cfg: Dict, weapon: Dict, enemy: Dict, charged: bool) -> int:
    """按攻击动作的 weapon_damage 声明结算；无声明时沿用引擎默认公式。"""
    effects = _action_def(cfg, "attack").get("effects")
    effect = next((e for e in effects or []
                   if isinstance(e, dict) and e.get("type") == "weapon_damage"), None)
    if effect is None:
        bonus = int(cfg.get("player_heavy_bonus", 2)) if charged else 0
        multiplier = int(cfg.get("player_heavy_mult", 2)) if charged else 1
        return max(1, int(weapon.get("damage", 5)) + bonus
                   - int(enemy.get("defense", 0))) * multiplier

    damage = int(weapon.get("damage", 5))
    if charged and effect.get("charged_bonus_key"):
        damage += int(cfg.get(effect["charged_bonus_key"], 0))
    if effect.get("subtract_target_defense", True):
        damage -= int(enemy.get("defense", 0))
    damage = max(int(effect.get("minimum", 1)), damage)
    if charged and effect.get("charged_multiplier_key"):
        damage *= int(cfg.get(effect["charged_multiplier_key"], 1))
    return damage


def _effect_declared(cfg: Dict, action: str, effect_type: str) -> Optional[Dict]:
    effects = _action_def(cfg, action).get("effects")
    if not isinstance(effects, list):
        return None
    return next((e for e in effects
                 if isinstance(e, dict) and e.get("type") == effect_type), {})


def _charge_state_name(cfg: Dict) -> str:
    effect = _effect_declared(cfg, "charge", "apply_state")
    return str(effect.get("state", "readied")) if effect is not None else "readied"


def _charge_apply_effect(cfg: Dict) -> Optional[Dict]:
    return _effect_declared(cfg, "charge", "apply_state")


def _charge_is_ready(cfg: Dict, battle: Dict, beat: int) -> bool:
    """状态在到期拍结束前有效；旧存档没有到期拍时按旧 readied 兼容。"""
    if battle.get("charge") != _charge_state_name(cfg):
        return False
    expires = battle.get("charge_expires")
    if expires is None:
        return True
    return beat <= int(expires)


def _charge_duration(cfg: Dict) -> int:
    effect = _charge_apply_effect(cfg)
    try:
        return max(1, int((effect or {}).get("duration_beats", 1)))
    except (TypeError, ValueError):
        return 1


def _attack_consumes_charge(cfg: Dict) -> bool:
    return _action_def(cfg, "attack").get("consume_state") == _charge_state_name(cfg)


def _avoids_damage(cfg: Dict, action: str, intent: str) -> bool:
    """由动作效果声明该动作能否规避指定敌人动作；未配置沿用默认闪避行为。"""
    effect = _effect_declared(cfg, action, "avoid_damage")
    if effect is None:
        return action == "dodge"
    return intent in effect.get("against", [])


def _effective_cost(cfg: Dict, battle: Dict, action: str, beat: int) -> int:
    """挣扎生效拍：闪避/脱离消耗 -1；其余保持。不会低于 0。"""
    cost = _cost(cfg, action)
    if battle.get("struggle_beat") == beat and action in ("dodge", "disengage"):
        return max(0, cost - 1)
    return cost


def _low_hp(cfg: Dict, enemy: Dict, battle: Dict) -> bool:
    """敌人是否进入残血区间；阈值可被 brain.low_hp_ratio 覆盖。"""
    max_hp = int(enemy.get("hp", 1))
    raw = enemy.get("brain") or {}
    ratio = float(raw.get("low_hp_ratio", cfg.get("low_hp_ratio", 0.25)))
    return int(battle.get("enemy_hp", max_hp)) <= max_hp * ratio


def _rhythm(cfg: Dict, enemy: Dict, battle: Dict) -> list:
    """敌人节奏环（bash/charge 序列，重击由承诺位打出）；残血狂暴可换更快的环。"""
    raw = enemy.get("brain") or {}
    if (raw.get("low_hp_mode") == "frenzy" and raw.get("frenzy_rhythm")
            and _low_hp(cfg, enemy, battle)):
        return list(raw["frenzy_rhythm"])
    return list(raw.get("rhythm") or ("bash", "charge"))


def decide(cfg: Dict, battle: Dict, state: Dict, enemy: Dict) -> str:
    """拍首决策器：纯函数，锁定敌人本拍动作。只读拍首状态，不接收玩家本拍
    选择（AI 不得偷看改招）；无随机，同局面结果恒定。返回 INTENT_TEXT 的键。
    行为参数来自敌人 brain（未配置走默认）。破绽拍决策器不改招——情报价值归玩家。"""
    raw = enemy.get("brain") or {}
    # ① 承诺：上拍蓄力成功且没被打断 → 重击必须打出
    if battle.get("enemy_promised"):
        return "heavy"
    # ② 上拍蓄力被打断 → 本拍喘息
    if battle.get("enemy_interrupted"):
        return "recover"
    # ③ 上拍闪避 → 承诺撞击（不可再躲）
    if battle.get("enemy_promised_bash"):
        return "bash"
    player_charged = _charge_is_ready(cfg, battle, int(battle.get("beat", 1)))
    low_hp = _low_hp(cfg, enemy, battle)
    # ④ 玩家蓄力就绪：能躲的敌人躲开重击（残血不躲，否则杀不死）；
    #    躲不动的敌人（石头类）只能换血反制
    if player_charged:
        if raw.get("dodge_player_charge", True):
            return "bash" if low_hp else "dodge"
        return "bash"
    # ⑤ 残血：龟息性格蜷缩挡普攻；狂暴性格不蜷缩，节奏交给（更快的）环
    if low_hp and raw.get("low_hp_mode", "brace") != "frenzy":
        return "brace"
    # ⑥ 节奏步：循环 brain.rhythm（重击由承诺位打出）
    rhythm = _rhythm(cfg, enemy, battle)
    return rhythm[int(battle.get("enemy_step", 0)) % len(rhythm)]


def describe(state: Dict, data: Dict) -> Optional[Dict]:
    """给 /api/combat 的拍面信息：AP、本拍意图与费用。非战斗/未启用返回 None。"""
    cfg = beat_config(data)
    if not cfg:
        return None
    battle, enemy = _battle_enemy(state, data)
    if not battle or not enemy:
        return {"enabled": True, "ap": int(state.get(AP_KEY, cfg.get("ap_start", 1))),
                "ap_max": int(cfg.get("ap_max", 3)), "in_battle": False}
    if state.get(DETACH_KEY) is not None:
        return {"enabled": True, "ap": int(state.get(AP_KEY, 0)),
                "ap_max": int(cfg.get("ap_max", 3)), "in_battle": False, "detached": True}
    beat = int(battle.get("beat", 1))
    intent = decide(cfg, battle, state, enemy)
    label, hint = INTENT_TEXT.get(intent, (intent, ""))
    override = (enemy.get("intents") or {}).get(intent)
    if isinstance(override, (list, tuple)) and len(override) == 2:
        label, hint = override
    flaw = battle.get("enemy_flaw_beat") == beat
    player_flaw = battle.get("player_flaw_beat") == beat
    struggle = battle.get("struggle_beat") == beat
    hidden_hint = telegraph(intent, beat, enemy)
    if flaw:
        hidden_hint = f"你看穿了它的动作：{label}。{hint}"
    elif player_flaw:
        hidden_hint = "你上一拍重击落空、重心不稳：" + hidden_hint
    return {
        "enabled": True, "in_battle": True,
        "ap": int(state.get(AP_KEY, cfg.get("ap_start", 1))),
        "ap_max": int(cfg.get("ap_max", 3)),
        "beat": beat,
        "charged": _charge_is_ready(cfg, battle, beat),
        "enemy_flaw": flaw,
        "player_flaw": player_flaw,
        "struggle": struggle,
        "intent": intent,
        "intent_label": "敌人的动作",
        "intent_hint": hidden_hint,
        "actions": _action_payload(cfg),
        "costs": {a: _effective_cost(cfg, battle, a, beat) for a in ACTIONS},
    }


def _player_stats(state: Dict) -> Dict[str, int]:
    return {"hp": int(state.get("player_hp", 50)),
            "max_hp": int(state.get("player_max_hp", 50)),
            "attack": int(state.get("player_attack", 5)),
            "defense": int(state.get("player_defense", 2))}


def _enemy_damage_taken(cfg: Dict, action: str, intent: str, state: Dict,
                        enemy: Dict) -> int:
    """按动作 effects/interactions 计算玩家本拍承伤；未配置时兼容默认公式。"""
    if intent in ("brace", "charge", "recover", "dodge"):
        return 0
    heavy = int(enemy.get("heavy_attack", cfg.get("heavy_damage", 10)))
    if intent == "heavy":
        if action == "dodge" and _avoids_damage(cfg, action, intent):
            return 0
        if action == "block":
            rule = _interaction_enabled(cfg, "block", "heavy",
                                        "halve_damage_after_defense")
            if rule is not False:
                pdef = int(state.get("player_defense", 0))
                return max(1, heavy - pdef) // 2
        return heavy
    raw = max(1, int(enemy.get("attack", 1)) - int(state.get("player_defense", 0)))
    if action == "dodge" and _avoids_damage(cfg, action, intent):
        return 0
    if action == "block":
        rule = _interaction_enabled(cfg, "block", "bash",
                                    "halve_damage_after_defense")
        if rule is not False:
            return raw // 2
    return raw


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

    beat = int(battle.get("beat", 1))
    # 拍首锁定：本拍敌人动作由决策器一次性决定，拍内状态变化不再改招
    player_charged_at_lock = (_charge_is_ready(cfg, battle, beat)
                              and _attack_consumes_charge(cfg))
    intent = decide(cfg, battle, state, enemy)
    ap = int(state.get(AP_KEY, 0))
    cost = _effective_cost(cfg, battle, action, beat)
    if ap < cost:
        base = _cost(cfg, action)
        hint = "（挣扎折扣后仍不足）" if cost != base else ""
        response["message"] = f"行动力不足{hint}（需要 {cost}，当前 {ap}）"
        return response
    state[AP_KEY] = ap - cost
    enemy_name = enemy.get("name", enemy["id"])

    # 状态生命周期由 charge.apply_state / attack.consume_state 声明；未声明时兼容默认。
    readied = (_charge_is_ready(cfg, battle, beat)
               and (action != "attack" or _attack_consumes_charge(cfg)))
    if action == "attack" and _attack_consumes_charge(cfg):
        battle["charge"] = None
        battle.pop("charge_expires", None)

    # 承诺/打断标记在本拍锁定后即视为消费（下一拍状态由拍末推进重新设定）
    battle["enemy_promised"] = False
    battle["enemy_promised_bash"] = False

    # ---------- 玩家进攻（先结算数值，伤害与敌人同时生效，不因击杀提前结束）----------
    p_damage = 0
    damage_logged = False
    charge_interrupted = False
    weapon = {}
    if action == "attack":
        weapon = (data.get("items") or {}).get(weapon_id, {})
        if weapon_id not in state.get("player_inventory", []):
            state[AP_KEY] = ap   # 校验失败退还 AP
            response["message"] = f"你没有【{weapon.get('name', weapon_id)}】"
            return response
        if not weapon.get("is_weapon"):
            state[AP_KEY] = ap
            response["message"] = f"【{weapon.get('name', weapon_id)}】不是武器"
            return response
        # 闪避躲过一切攻击（含重击）；普攻打不动蜷缩；重击克制蜷缩。
        # 蓄力/喘息拍不防御，正常吃伤害
        if intent == "dodge":
            miss = _interaction_enabled(cfg, "attack", "dodge", "miss",
                                        {"charged": readied})
            if miss is not False:
                if readied:
                    response["log"].append(
                        f"你全力砸出的重击被【{enemy_name}】侧身躲开，一击落空！"
                        "用力过猛让你下一拍露出破绽。")
                    battle["player_flaw_beat"] = beat + 1
                else:
                    response["log"].append(
                        f"【{enemy_name}】身形一晃，你的攻击落了空！")
            else:
                p_damage = _weapon_damage(cfg, weapon, enemy, readied)
        elif intent == "brace" and not readied:
            miss = _interaction_enabled(cfg, "attack", "brace", "miss",
                                        {"charged": readied})
            if miss is not False:
                response["log"].append(
                    f"【{enemy_name}】蜷成一团，你的攻击被弹开，没有造成伤害！")
            else:
                p_damage = _weapon_damage(cfg, weapon, enemy, readied)
        else:
            p_damage = _weapon_damage(cfg, weapon, enemy, readied)
            if readied:
                response["log"].append(
                    f"你踏前一步全力出手，蓄力化为重击，对【{enemy_name}】造成 **{p_damage}** 点伤害！")
            else:
                response["log"].append(
                    f"你用【{weapon['name']}】对【{enemy_name}】造成 **{p_damage}** 点伤害！")
            combat_system.publish("COMBAT_DAMAGE",
                                  attacker="player", defender=enemy["id"], damage=p_damage)
            damage_logged = True
            if intent == "charge":
                charge_interrupted = _interaction_enabled(
                    cfg, "attack", "charge", "interrupt", {"charged": readied, "hit": True}) is not False
                if charge_interrupted:
                    response["log"].append("这一击正中蓄力中的【%s】，它的重击被打断了！" % enemy_name)
    if action == "attack" and p_damage > 0 and not damage_logged:
        response["log"].append(
            f"你用【{weapon['name']}】对【{enemy_name}】造成 **{p_damage}** 点伤害！")
        combat_system.publish("COMBAT_DAMAGE",
                              attacker="player", defender=enemy["id"], damage=p_damage)
        if intent == "charge":
            charge_interrupted = _interaction_enabled(
                cfg, "attack", "charge", "interrupt", {"charged": readied, "hit": True}) is not False
            if charge_interrupted:
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
            battle["enemy_flaw_beat"] = beat + 1
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
        combat_system.publish("COMBAT_DAMAGE",
                              attacker=enemy["id"], defender="player", damage=taken)

    enemy_dead = battle["enemy_hp"] <= 0 and p_damage > 0
    player_dead = new_hp <= 0

    if enemy_dead:
        victory = combat_system.settle_kill(response)
        state.pop(DETACH_KEY, None)
        response["defeated"] = enemy["id"]
        response["victory"] = victory
    if player_dead:
        combat_system.publish("COMBAT_DEATH", dead="player")
        combat_system.handle_player_death(response)
    if enemy_dead:
        response["success"] = True
        response["ap"] = state[AP_KEY]
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
            response["ap"] = state[AP_KEY]
            response["charged"] = False
            return response
        battle["struggle_beat"] = beat + 1
        response["log"].append(
            "你被攻击缠住，没能脱离。挣扎之间你调整了脚步——下一拍闪避或脱离少花 1 点行动力。")
        response["message"] = "脱离失败！敌人的攻击缠住了你。"
    elif action == "charge":
        charge_effect = _charge_apply_effect(cfg)
        if taken == 0:
            battle["charge"] = (charge_effect or {}).get("state", "readied")
            battle["charge_expires"] = beat + _charge_duration(cfg)
            preview = _heavy_preview(cfg, state, data, enemy)
            response["log"].append(
                f"蓄力完成！下一拍选择攻击，将打出 **{preview}** 点重击。")
            response["message"] = "蓄力就绪"
        else:
            clear_on_damage = (charge_effect or {}).get("clear_state_on_damage", True)
            if clear_on_damage:
                battle["charge"] = None
                battle.pop("charge_expires", None)
            response["log"].append("蓄力被打断了，下一拍的攻击没有加成。")
            response["message"] = "蓄力被打断"

    # ---------- 拍末：敌人节奏推进（决策状态机的唯一写入点）----------
    battle["enemy_interrupted"] = False
    if intent == "charge":
        if p_damage > 0 and charge_interrupted:
            # 蓄力拍被攻击命中 → 下拍喘息，节奏从头再来
            battle["enemy_interrupted"] = True
            battle["enemy_step"] = 0
        else:
            # 蓄力成功 → 下拍承诺重击（不可取消）
            battle["enemy_promised"] = True
    elif intent == "dodge":
        # 闪避成功 → 下拍承诺撞击（不可再躲）
        battle["enemy_promised_bash"] = True
    elif intent in ("heavy", "recover"):
        battle["enemy_step"] = 0
    elif intent == "bash" and not player_charged_at_lock:
        # 节奏内的撞击（非"玩家蓄力"临时反制）→ 推进到环内下一格
        rhythm = _rhythm(cfg, enemy, battle)
        battle["enemy_step"] = (int(battle.get("enemy_step", 0)) + 1) % len(rhythm)
    # brace 与临时 bash 不推进节奏步

    # ---------- 拍末：推进拍序号、AP 回复、过期一拍状态清理 ----------
    next_beat = beat + 1
    # 破绽/挣扎都只在"下一拍"生效；过期（如连续脱离失败只保留最近一次，不叠加）
    if battle.get("enemy_flaw_beat") not in (None, next_beat):
        battle.pop("enemy_flaw_beat", None)
    if battle.get("player_flaw_beat") not in (None, next_beat):
        battle.pop("player_flaw_beat", None)
    if (battle.get("charge") is not None
            and battle.get("charge_expires") is not None
            and next_beat > int(battle["charge_expires"])):
        battle["charge"] = None
        battle.pop("charge_expires", None)
    if battle.get("struggle_beat") not in (None, next_beat):
        battle.pop("struggle_beat", None)
    battle["beat"] = next_beat
    ap_max = int(cfg.get("ap_max", 3))
    state[AP_KEY] = min(ap_max, state[AP_KEY] + int(cfg.get("ap_per_beat", 1)))

    response["success"] = True
    if not response["message"]:
        response["message"] = "回合结束"
    response["player"] = _player_stats(state)
    response["ap"] = state[AP_KEY]
    response["beat"] = battle["beat"]
    response["charged"] = _charge_is_ready(cfg, battle, battle["beat"])
    response["enemy_flaw"] = battle.get("enemy_flaw_beat") == battle["beat"]
    response["player_flaw"] = battle.get("player_flaw_beat") == battle["beat"]
    response["struggle"] = battle.get("struggle_beat") == battle["beat"]
    response["next_intent"] = decide(cfg, battle, state, enemy)
    return response


# ============================================================
# 脱离休整（detached）：脱离后双方残血挂机，行动累计 recover_seconds
# 静默回满、战斗重置。恢复静默发生，无任何 UI 通知。
# ============================================================
def is_detached(state: Dict) -> bool:
    """是否处于"已脱离、计时恢复中"：计时标记在，且底层战斗仍保留着残血。"""
    return state.get(DETACH_KEY) is not None and bool(state.get("current_battle"))


def cancel_detach(state: Dict) -> None:
    """玩家重新接战（点挑战/攻击/喝药）→ 取消休整计时。"""
    state.pop(DETACH_KEY, None)


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

    cfg = beat_config(game_data)
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
