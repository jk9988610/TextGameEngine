"""
声明式事件规则引擎（引擎核心模块）
负责：把写在 game_config.json 里的 event_rules 注册到 EventBus，
      条件全等匹配事件参数，命中后执行声明式效果。

规则格式（纯数据，可由可视化编辑器产出）：
{
  "on": "ITEM_TAKEN",                 # 事件名（见 event_bus.GameEvents）
  "if": {"item_id": "rusty_key"},     # 所有键值必须与事件 kwargs 全等（省略 if 表示无条件）
  "do": [                             # 命中后依次执行的效果
    {"type": "unlock", "scene": "forest", "exit": "cave"}
  ]
}

后续扩展只需要在 _EFFECTS 里加新的效果类型（give_item / set_flag / teleport ...），
调用方和已有规则数据都不用改。
"""
from typing import Dict, List, Any, Callable


class DeclarativeRules:
    """把声明式规则挂到事件总线上 —— 所有文字游戏通用"""

    def __init__(self, game_state: Dict[str, Any]):
        self._state = game_state
        # 效果分发表：type -> handler(rule)
        self._effects: Dict[str, Callable[[Dict[str, Any]], None]] = {
            "unlock": self._effect_unlock,
        }

    # ---------- 公开接口 ----------
    def register(self, bus, rules: List[Dict[str, Any]]) -> None:
        """把一批规则注册到事件总线"""
        for rule in rules or []:
            event_name = rule.get("on")
            if not event_name:
                continue
            bus.subscribe(event_name, self._make_handler(rule))

    # ---------- 内部 ----------
    def _make_handler(self, rule: Dict[str, Any]) -> Callable[..., None]:
        """生成一条规则的事件回调（闭包绑定该规则）"""
        def handler(**kwargs):
            if self._match(rule.get("if"), kwargs):
                for effect in rule.get("do", []):
                    self._apply(effect)
        return handler

    @staticmethod
    def _match(conditions: Dict[str, Any], kwargs: Dict[str, Any]) -> bool:
        """条件全等匹配：conditions 中每个键值都要等于事件参数（无条件 = 恒命中）"""
        if not conditions:
            return True
        return all(kwargs.get(k) == v for k, v in conditions.items())

    def _apply(self, effect: Dict[str, Any]) -> None:
        """执行一个效果；未知类型静默忽略（向前兼容：新版编辑器产出旧引擎不认识的效果时不崩）"""
        handler = self._effects.get(effect.get("type"))
        if handler:
            handler(effect)

    # ---------- 具体效果 ----------
    def _effect_unlock(self, effect: Dict[str, Any]) -> None:
        """解锁某场景的某个出口：scene_lock_states[scene][exit] = False"""
        scene_id = effect.get("scene")
        exit_id = effect.get("exit")
        locks = self._state.get("scene_lock_states", {}).get(scene_id)
        if locks is not None and exit_id in locks:
            locks[exit_id] = False
