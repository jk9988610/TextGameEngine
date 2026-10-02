"""
会话管理器（引擎基础设施模块）
多会话隔离的核心：每个玩家浏览器会话对应一套独立的引擎实例 + game_state
GAME_DATA 是全局只读共享的，所有玩家读同一份场景/物品/NPC/敌人数据
但每个玩家的 current_scene / player_hp / current_dialogue / current_battle 是独立的

不修改任何现有引擎模块文件 —— 只是按 session_id 创建多套引擎实例
"""
import time
import threading
from typing import Dict, Any, Optional

from .event_bus import EventBus
from .scene_manager import SceneManager
from .item_system import ItemSystem
from .console_handler import ConsoleHandler
from .npc_system import NPCSystem
from .combat_system import CombatSystem


class SessionManager:
    """管理所有活跃的玩家会话 —— 多会话隔离的唯一入口"""

    def __init__(self, game_data: Dict[str, Any]):
        """
        :param game_data: 全局只读的游戏数据字典（scenes/items/npcs/enemies）
        """
        self._game_data = game_data
        # session_id -> { "created_at": float, "last_accessed": float, "state": dict }
        self._sessions: Dict[str, Any] = {}
        self._lock = threading.Lock()  # 简单的线程锁，防止并发创建同一 session

    # ---------- 核心接口 ----------
    def get_or_create(self, session_id: str) -> Dict[str, Any]:
        """获取或创建某个 session 的完整引擎实例包"""
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["last_accessed"] = time.time()
                return self._sessions[session_id]["state"]
            # 不存在 → 新建
            engines = self._create_fresh(session_id)
            self._sessions[session_id] = {
                "created_at": time.time(),
                "last_accessed": time.time(),
                "state": engines,
            }
            return engines

    def cleanup_stale(self, max_age_minutes: int = 30) -> int:
        """清理超过 N 分钟没访问的 session，防止内存泄漏。返回清理了多少个"""
        now = time.time()
        cutoff = now - max_age_minutes * 60
        removed = 0
        with self._lock:
            stale_ids = [
                sid for sid, info in self._sessions.items()
                if info["last_accessed"] < cutoff
            ]
            for sid in stale_ids:
                del self._sessions[sid]
                removed += 1
        return removed

    def session_count(self) -> int:
        """当前活跃会话数（监控用）"""
        return len(self._sessions)

    # ---------- 内部：创建全新的引擎实例包（复刻原 init_game 逻辑） ----------
    def _create_fresh(self, session_id: str) -> Dict[str, Any]:
        """为新 session 创建一套完整独立的引擎实例"""
        gd = self._game_data  # 共享只读数据

        # 独立的游戏状态（每个玩家各一份）
        game_state = {
            "current_scene": "tavern",
            "player_inventory": ["rusty_sword"],
            "scene_item_states": {},
            "scene_lock_states": {},
            "current_dialogue": None,
            "current_battle": None,
            "killed_enemies": [],          # 🆕 被永久击杀的敌人 id（死了就不能复活）
            "player_hp": 50,
            "player_max_hp": 50,
            "player_attack": 5,
            "player_defense": 2,
        }

        # 独立的事件总线 + 所有引擎模块
        bus = EventBus()
        scene_manager = SceneManager(bus, gd, game_state)
        item_system = ItemSystem(bus, gd, game_state)
        console_handler = ConsoleHandler(bus, gd, game_state, scene_manager, item_system)
        npc_system = NPCSystem(bus, gd, game_state)
        combat_system = CombatSystem(bus, gd, game_state)

        # 初始化场景状态
        scene_manager.init_scene_states()

        # 🆕 关键！初始场景触发 SCENE_ENTER → 让 NPCSystem 弹出醉汉对话
        bus.publish("SCENE_ENTER", scene_id=game_state["current_scene"])

        # ---------- 游戏专属事件订阅（每个 session 独立注册） ----------
        # 钥匙解锁洞穴门（复制原 app.py 的逻辑，闭包引用当前 session 的 game_state）
        def unlock_cave_door(**kwargs):
            taken_item = kwargs.get("item_id")
            from_scene = kwargs.get("from_scene")
            if taken_item == "rusty_key" and from_scene == "forest":
                game_state["scene_lock_states"]["forest"]["cave"] = False
                print(f"🔓 会话[{session_id[:8]}] 控制台日志：玩家拿到钥匙，洞穴门已解锁")

        bus.subscribe("ITEM_TAKEN", unlock_cave_door)

        return {
            "game_state": game_state,
            "bus": bus,
            "scene_manager": scene_manager,
            "item_system": item_system,
            "console_handler": console_handler,
            "npc_system": npc_system,
            "combat_system": combat_system,
        }
