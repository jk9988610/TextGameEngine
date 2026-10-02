"""
事件总线（引擎核心模块）
观察者模式：只有订阅者才响应事件，绝对不用轮询
所有引擎模块通过构造函数接收 EventBus 实例，不直接 import，避免循环依赖
"""
from typing import Dict, List, Callable, Any


class EventBus:
    """游戏全局事件总线 —— 所有子系统通信的唯一通道"""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """订阅事件：event_type 触发时自动调用 callback"""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """取消订阅（可选，方便热插拔模块卸载）"""
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(callback)

    def publish(self, event_type: str, **kwargs: Any) -> None:
        """发布事件：通知所有订阅者"""
        for cb in self._subscribers.get(event_type, []):
            cb(**kwargs)

    def clear(self) -> None:
        """清空所有订阅（游戏重置时用）"""
        self._subscribers.clear()


# ---------- 事件名称常量（方便后续模块统一引用，避免拼写错误） ----------
class GameEvents:
    """所有事件名称集中管理，后续加新事件直接在这追加"""
    SCENE_ENTER = "SCENE_ENTER"       # 玩家进入某个场景
    SCENE_LEAVE = "SCENE_LEAVE"       # 玩家离开某个场景
    ITEM_TAKEN = "ITEM_TAKEN"         # 玩家拿了某个物品
    ITEM_DROPPED = "ITEM_DROPPED"     # 玩家丢了某个物品
    ITEM_USED = "ITEM_USED"           # 玩家使用某个物品
    NPC_TALK = "NPC_TALK"             # 玩家和NPC对话（预留给对话系统）
    COMBAT_DAMAGE = "COMBAT_DAMAGE"   # 战斗伤害结算（预留给战斗系统）
    COMBAT_DEATH = "COMBAT_DEATH"     # 战斗死亡（预留给战斗系统）
