"""
声明式事件规则引擎（引擎核心模块）
负责：把写在 game_config.json 里的 event_rules 注册到 EventBus，
      事件参数全等匹配 + 世界状态条件都满足时，执行 EffectExecutor 效果组。

规则格式（纯数据，由可视化编辑器产出）：
{
  "id": "rusty_key_unlocks_cave",      # 编辑器用，引擎不依赖
  "label": "拿到锈钥匙解锁洞穴门",       # 备注，引擎不依赖
  "on": "ITEM_TAKEN",                  # 事件名（见 event_bus.GameEvents）
  "if": {"item_id": "rusty_key"},      # 事件参数全等过滤（省略 = 不过滤）
  "when": {"flag": "quest_started"},   # 可选：世界状态条件（effects.condition_matches）
  "do": [                              # 命中后依次执行（效果类型见 engine/effects.py）
    {"type": "unlock", "scene": "forest", "exit": "cave"}
  ]
}

效果执行全部委托给 EffectExecutor（对话选项与事件规则共用同一份效果库）。
"""
from typing import Dict, List, Any, Callable, Optional

from .effects import EffectExecutor


class DeclarativeRules:
    """把声明式规则挂到事件总线上 —— 所有文字游戏通用"""

    def __init__(self, executor: EffectExecutor):
        self._executor = executor

    # ---------- 公开接口 ----------
    def register(self, bus, rules: Optional[List[Dict[str, Any]]]) -> None:
        """把一批规则注册到事件总线（会话创建时调用一次，规则热更只对新会话生效）"""
        for rule in rules or []:
            event_name = rule.get("on")
            if not event_name:
                continue
            bus.subscribe(event_name, self._make_handler(rule))

    # ---------- 内部 ----------
    def _make_handler(self, rule: Dict[str, Any]) -> Callable[..., None]:
        """生成一条规则的事件回调（闭包绑定该规则）"""
        def handler(**kwargs):
            if not self._match_event_args(rule.get("if"), kwargs):
                return
            if not self._executor.condition_matches(rule.get("when")):
                return
            self._executor.apply(rule.get("do", []))
        return handler

    @staticmethod
    def _match_event_args(conditions: Optional[Dict[str, Any]],
                          kwargs: Dict[str, Any]) -> bool:
        """事件参数全等匹配：conditions 中每个键值都要等于事件参数（无条件 = 恒命中）"""
        if not conditions:
            return True
        return all(kwargs.get(k) == v for k, v in conditions.items())
