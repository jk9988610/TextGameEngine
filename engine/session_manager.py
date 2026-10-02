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
from .shop_system import ShopSystem
from .event_rules import DeclarativeRules


# config 缺失时的兜底默认值（保证即使没有 game_config.json 引擎也能跑）
DEFAULT_CONFIG: Dict[str, Any] = {
    "initial_scene": "tavern",
    "initial_inventory": [],
    "initial_gold": 0,
    "player": {"hp": 50, "attack": 5, "defense": 2},
    "action_time": {"move_scene": 300, "take_item": 20, "drop_item": 20,
                   "combat_turn": 60, "buy_item": 20},
    "event_rules": [],
}


class SessionManager:
    """管理所有活跃的玩家会话 —— 多会话隔离的唯一入口"""

    def __init__(self, game_data: Dict[str, Any]):
        """
        :param game_data: 全局只读的游戏数据字典（scenes/items/npcs/enemies/config）
        """
        self._game_data = game_data
        # session_id -> { "created_at": float, "last_accessed": float, "state": dict }
        self._sessions: Dict[str, Any] = {}
        self._lock = threading.Lock()  # 简单的线程锁，防止并发创建同一 session

    def _config(self) -> Dict[str, Any]:
        """取游戏配置（内容层数据），缺字段用兜底值补全"""
        cfg = self._game_data.get("config") or {}
        merged = {**DEFAULT_CONFIG, **cfg}
        merged["player"] = {**DEFAULT_CONFIG["player"], **cfg.get("player", {})}
        merged["action_time"] = {**DEFAULT_CONFIG["action_time"], **cfg.get("action_time", {})}
        return merged

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
        cfg = self._config()  # 游戏初始配置（内容层）

        # 独立的游戏状态（每个玩家各一份）—— 初始值全部来自 config，引擎不写死任何游戏内容
        player_cfg = cfg["player"]
        game_state = {
            "current_scene": cfg["initial_scene"],
            "player_inventory": list(cfg["initial_inventory"]),
            "player_gold": int(cfg.get("initial_gold", 0)),  # 金币余额（货币系统）
            "scene_item_states": {},
            "scene_lock_states": {},
            "current_dialogue": None,
            "current_battle": None,
            "killed_enemies": [],          # 被永久击杀的敌人 id
            "game_time": 0,                # 游戏内时间（秒数，每次操作推进）
            "player_hp": player_cfg["hp"],
            "player_max_hp": player_cfg["hp"],
            "player_attack": player_cfg["attack"],
            "player_defense": player_cfg["defense"],
        }

        # 独立的事件总线 + 所有引擎模块
        bus = EventBus()
        scene_manager = SceneManager(bus, gd, game_state)
        item_system = ItemSystem(bus, gd, game_state)
        shop_system = ShopSystem(bus, gd, game_state)
        console_handler = ConsoleHandler(bus, gd, game_state, scene_manager,
                                        item_system, shop_system=shop_system)
        npc_system = NPCSystem(bus, gd, game_state)
        combat_system = CombatSystem(bus, gd, game_state)

        # 初始化场景状态
        scene_manager.init_scene_states()

        # 初始场景触发 SCENE_ENTER → 让 NPC/战斗系统同步场景
        bus.publish("SCENE_ENTER", scene_id=game_state["current_scene"])

        # 声明式事件规则（拿钥匙开门之类的具体游戏逻辑写在 game_config.json，
        # 引擎这里只负责装配，不含任何具体规则）
        DeclarativeRules(game_state).register(bus, cfg.get("event_rules", []))

        return {
            "game_state": game_state,
            "bus": bus,
            "scene_manager": scene_manager,
            "item_system": item_system,
            "shop_system": shop_system,
            "console_handler": console_handler,
            "npc_system": npc_system,
            "combat_system": combat_system,
        }

    def live_current_scenes(self) -> set:
        """所有存活会话中玩家当前所在场景的 id 集合（编辑器删场景时做保护）"""
        with self._lock:
            return {info["state"]["game_state"].get("current_scene")
                    for info in self._sessions.values()
                    if info["state"]["game_state"].get("current_scene")}

    def live_battle_enemies(self) -> set:
        """所有存活会话中玩家正在与之战斗的敌人 id 集合（编辑器删敌人时做保护）"""
        with self._lock:
            return {info["state"]["game_state"]["current_battle"].get("enemy_id")
                    for info in self._sessions.values()
                    if info["state"]["game_state"].get("current_battle")}

    def refresh_new_scenes(self) -> None:
        """
        编辑器热更新数据后调用：给所有存活会话补齐"新场景"的物品/锁状态键，
        避免 get_current_scene() 访问到不存在的场景状态键。
        已存在的场景状态保持不动（不抹掉玩家已拾取/已解锁的进度）。
        """
        with self._lock:
            for info in self._sessions.values():
                info["state"]["scene_manager"].init_missing_scene_states()

    def initial_state_defaults(self) -> Dict[str, Any]:
        """给 SaveManager 做老存档 normalize 用的默认值（来自 config）"""
        cfg = self._config()
        p = cfg["player"]
        return {
            "current_scene": cfg["initial_scene"],
            "player_inventory": list(cfg["initial_inventory"]),
            "player_gold": int(cfg.get("initial_gold", 0)),
            "scene_item_states": {},
            "scene_lock_states": {},
            "current_dialogue": None,
            "current_battle": None,
            "killed_enemies": [],
            "game_time": 0,
            "player_hp": p["hp"],
            "player_max_hp": p["hp"],
            "player_attack": p["attack"],
            "player_defense": p["defense"],
        }

