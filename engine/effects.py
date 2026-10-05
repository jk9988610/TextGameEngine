"""
通用条件与效果引擎（引擎核心模块）

M5 起，对话选项/事件规则的"条件"和"效果"全部走这里，引擎里不再写死
任何具体游戏内容（如 cave_goblin/rusty_key）。

P2 起进一步"槽位开放"：可用条目清单不再写在本模块，而由数据侧事件字典
（game_data/events.json）决定。本模块只提供少量**原子**（atom）——真正
求值/执行的最小逻辑；字典条目通过 atom 字段复用它们，因此可以改写标签、
参数默认值，甚至新增自定义条目。

原子契约：
条件（fn(state, cond, key)，key = 条件字典里的判别键，也就是取值字段名）：
  flag           标志为真值（可选 value 表示等于某值）
  has_item       背包里有某物
  enemy_killed   某敌人已被永久击杀
  gold_gte       金币至少 n
  （条目 key 必须是自己 params 里的一个字段名；改名即改取值字段，
    因此别名可自由命名参数，如 {"key":"rich","atom":"gold_gte"} 读 cond["rich"]）
效果（handler(eff)，字段名固定，字典条目的 params 必须覆盖标 * 的字段）：
  set_flag       *flag               可选 value
  give_item      *item               货币类物品自动折算金币
  remove_item    *item               没有也不报错（任务收物）
  heal           *amount
  max_hp         *amount             上限与当前 HP 同增减
  gold           *amount
  teleport       *scene
  start_combat   *enemy
  unlock         *scene *exit        解锁某场景的出口锁

依赖通过构造函数注入，不 import app.py。
"""
from typing import Dict, Any, List, Optional


# ============================================================
# 条件原子
# ============================================================
def _cond_flag(state: Dict[str, Any], cond: Dict[str, Any], key: str) -> bool:
    name = cond.get(key)
    flags = state.get("flags", {}) or {}
    if "value" in cond:
        return flags.get(name) == cond["value"]
    return bool(flags.get(name))


def _cond_has_item(state: Dict[str, Any], cond: Dict[str, Any], key: str) -> bool:
    return cond.get(key) in state.get("player_inventory", [])


def _cond_enemy_killed(state: Dict[str, Any], cond: Dict[str, Any], key: str) -> bool:
    return cond.get(key) in state.get("killed_enemies", [])


def _cond_gold_gte(state: Dict[str, Any], cond: Dict[str, Any], key: str) -> bool:
    try:
        return int(state.get("player_gold", 0)) >= int(cond.get(key))
    except (TypeError, ValueError):
        return False


# 合法条件原子名（编辑器据此校验字典条目绑定的原子是否存在）。
# 条件原子的取值字段就是「条目 key」本身（见上方契约），所以字典条目可以
# 自由命名别名参数（如 rich 复用 gold_gte），无需再约束参数名。
CONDITION_ATOMS = {
    "flag": _cond_flag,
    "has_item": _cond_has_item,
    "enemy_killed": _cond_enemy_killed,
    "gold_gte": _cond_gold_gte,
}

# 效果原子的参数名是固定的（handler 直接读 eff["amount"] 等），
# 编辑器据此校验字典条目的 params 是否配得对。
EFFECT_ATOM_FIELDS = {
    "set_flag": ("flag",),
    "give_item": ("item",),
    "remove_item": ("item",),
    "heal": ("amount",),
    "max_hp": ("amount",),
    "gold": ("amount",),
    "teleport": ("scene",),
    "start_combat": ("enemy",),
    "unlock": ("scene", "exit"),
}


# ============================================================
# 条件求值
# ============================================================
def condition_matches(state: Dict[str, Any], cond: Optional[Dict[str, Any]],
                      atom_map: Optional[Dict[str, str]] = None) -> bool:
    """对 game_state 求值一个条件；None/空条件视为无条件满足。

    :param atom_map: 字典的 {条目 key: 原子名}；缺省时条目 key 即原子名。
    """
    if not cond:
        return True

    for key in cond:
        if key == "value":       # value 只是 flag 原子的附加参数，不是判别键
            continue
        fn = CONDITION_ATOMS.get((atom_map or {}).get(key, key))
        if fn is not None:
            return bool(fn(state, cond, key))
    # 未知条件类型：保守地不满足（编辑器校验会拦，这是运行时兜底）
    return False


# ============================================================
# 效果执行器
# ============================================================
class EffectExecutor:
    """按顺序执行对话/事件效果。跨系统动作通过注入的子系统完成。"""

    def __init__(self, game_data: Dict[str, Any], game_state: Dict[str, Any],
                 scene_manager=None, item_system=None, combat_system=None,
                 event_dict: Optional[Dict[str, Any]] = None):
        self._data = game_data
        self._state = game_state
        self._sm = scene_manager
        self._items = item_system
        self._combat = combat_system
        # 事件字典（可用条目清单）；缺省时条目 key 即原子名，等价于旧行为
        event_dict = event_dict if event_dict is not None else (game_data or {}).get("events")
        event_dict = event_dict or {}
        self._effect_atoms = {e["key"]: (e.get("atom") or e["key"])
                              for e in event_dict.get("effects", []) if e.get("key")}
        self._cond_atoms = {e["key"]: (e.get("atom") or e["key"])
                            for e in event_dict.get("conditions", []) if e.get("key")}

    def condition_matches(self, cond: Optional[Dict[str, Any]]) -> bool:
        """对当前玩家状态求值世界条件（供事件规则的 when 使用）。"""
        return condition_matches(self._state, cond, self._cond_atoms)

    def apply(self, effects: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        执行一组效果。
        :return: {"success": bool, "messages": [str], "combat_started": bool}
        """
        messages: List[str] = []
        combat_started = False

        for eff in effects or []:
            etype = eff.get("type")
            # 字典条目（type）→ 引擎原子；字典缺省时两者相同
            handler = self._handlers().get(self._effect_atoms.get(etype, etype))
            if handler is None:
                messages.append(f"未知效果类型：{etype}")
                continue
            msg, started = handler(eff)
            if msg:
                messages.append(msg)
            combat_started = combat_started or started

        return {"success": True, "messages": messages, "combat_started": combat_started}

    # ---------- 内部 ----------
    def _handlers(self):
        return {
            "set_flag": self._eff_set_flag,
            "give_item": self._eff_give_item,
            "remove_item": self._eff_remove_item,
            "heal": self._eff_heal,
            "max_hp": self._eff_max_hp,
            "gold": self._eff_gold,
            "teleport": self._eff_teleport,
            "start_combat": self._eff_start_combat,
            "unlock": self._eff_unlock,
        }

    def _eff_set_flag(self, eff: Dict[str, Any]):
        name = str(eff.get("flag", "")).strip()
        if not name:
            return "效果缺少标志名", False
        value = eff.get("value", True)
        self._state.setdefault("flags", {})[name] = value
        return "", False

    def _eff_give_item(self, eff: Dict[str, Any]):
        item_id = eff.get("item", "")
        if self._items is None:
            return "物品系统未启用", False
        result = self._items.add_to_inventory(item_id)
        return result.get("message", ""), False

    def _eff_remove_item(self, eff: Dict[str, Any]):
        item_id = eff.get("item", "")
        inv = self._state.setdefault("player_inventory", [])
        name = self._data.get("items", {}).get(item_id, {}).get("name", item_id)
        if item_id in inv:
            inv.remove(item_id)
            return f"你交出了【{name}】", False
        return "", False  # 没有也不阻断对话

    def _eff_heal(self, eff: Dict[str, Any]):
        try:
            amount = int(eff.get("amount", 0))
        except (TypeError, ValueError):
            return "治疗数值无效", False
        hp = self._state.get("player_hp", 0)
        max_hp = self._state.get("player_max_hp", hp)
        healed = max(0, min(amount, max_hp - hp))
        self._state["player_hp"] = hp + healed
        if healed > 0:
            return f"恢复 {healed} 点生命（当前 {hp + healed}/{max_hp}）", False
        return "", False

    def _eff_max_hp(self, eff: Dict[str, Any]):
        try:
            amount = int(eff.get("amount", 0))
        except (TypeError, ValueError):
            return "生命上限数值无效", False
        new_max = self._state.get("player_max_hp", 50) + amount
        if new_max < 1:
            return "生命上限不能低于 1", False
        self._state["player_max_hp"] = new_max
        # 当前 HP 随上限同增减（祝福时同步变多；受伤状态也保持差值一致）
        self._state["player_hp"] = max(1, self._state.get("player_hp", new_max) + amount)
        if amount > 0:
            return f"生命上限提升 {amount}（当前上限 {new_max}）", False
        if amount < 0:
            return f"生命上限降低 {-amount}（当前上限 {new_max}）", False
        return "", False

    def _eff_gold(self, eff: Dict[str, Any]):
        try:
            amount = int(eff.get("amount", 0))
        except (TypeError, ValueError):
            return "金币数值无效", False
        gold = max(0, int(self._state.get("player_gold", 0)) + amount)
        self._state["player_gold"] = gold
        if amount > 0:
            return f"金币 +{amount}（当前金币：{gold}）", False
        if amount < 0:
            return f"金币 {-amount}（当前金币：{gold}）", False
        return "", False

    def _eff_teleport(self, eff: Dict[str, Any]):
        scene_id = eff.get("scene", "")
        if self._sm is None:
            return "场景系统未启用", False
        result = self._sm.teleport(scene_id)  # 内部会发 SCENE_ENTER → 自动结束对话
        return result.get("message", ""), False

    def _eff_start_combat(self, eff: Dict[str, Any]):
        enemy_id = eff.get("enemy", "")
        if self._combat is None:
            return "战斗系统未启用", False
        result = self._combat.start_battle(enemy_id)
        return result.get("message", ""), bool(result.get("success"))

    def _eff_unlock(self, eff: Dict[str, Any]):
        """解锁某场景的出口锁（拿钥匙开门的声明式效果）；无提示文本。"""
        scene_id = eff.get("scene", "")
        exit_id = eff.get("exit", "")
        locks = self._state.get("scene_lock_states", {}).get(scene_id)
        if locks is not None and exit_id in locks:
            locks[exit_id] = False
        return "", False
