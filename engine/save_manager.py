"""
离线存档系统（引擎模块）
用 Python 标准库 sqlite3 —— 零额外依赖，GitHub clone 直接能用
特点：
  1. normalize_save_data() —— 每次读档都用默认值补全缺失字段，老存档不崩
  2. 每个 session 可以有多槽存档（slot_1 ~ slot_5）
  3. 自动存档 + 手动存档并存
  4. 不修改任何现有 7 个引擎模块 —— 新增模块独立
"""
import sqlite3
import json
import os
import copy
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional


class SaveManager:
    """SQLite 多槽存档管理器 —— 离线模式专属"""

    # 每个 session 最多几个手动存档槽位
    MAX_SLOTS = 5

    # 老存档兜底默认值（app 层会用 game_config 派生的值覆盖，保持与初始游戏一致）
    FALLBACK_DEFAULTS: Dict[str, Any] = {
        "current_scene": "tavern",
        "player_inventory": [],
        "player_gold": 0,
        "scene_item_states": {},
        "scene_lock_states": {},
        "current_dialogue": None,
        "current_battle": None,
        "killed_enemies": [],
        "game_time": 0,
        "player_hp": 50,
        "player_max_hp": 50,
        "player_attack": 5,
        "player_defense": 2,
    }

    def __init__(self, db_path: str = "game_data/offline_saves.db",
                 defaults: Optional[Dict[str, Any]] = None):
        """
        :param db_path: SQLite 数据库文件路径（相对项目根目录）
        :param defaults: 老存档补全用的默认 game_state（通常由 game_config 派生）
        """
        self._db_path = db_path
        self._defaults = defaults or dict(self.FALLBACK_DEFAULTS)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """首次运行时建表"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS saves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    slot_name TEXT NOT NULL,
                    is_auto INTEGER DEFAULT 0,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, slot_name)  -- 每个 session 的每个槽位唯一
                )
            """)

    # ---------- 核心接口 ----------
    def save_slot(self, session_id: str, slot_name: str, game_state: Dict[str, Any],
                  is_auto: bool = False) -> Dict[str, Any]:
        """存档到指定槽位"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_json = json.dumps(game_state, ensure_ascii=False)
        with self._lock, self._get_conn() as conn:
            # 存在则更新，不存在则插入
            existing = conn.execute(
                "SELECT id FROM saves WHERE session_id=? AND slot_name=?",
                (session_id, slot_name)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE saves SET data=?, is_auto=?, updated_at=? WHERE id=?",
                    (data_json, 1 if is_auto else 0, now, existing["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO saves (session_id, slot_name, is_auto, data, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, slot_name, 1 if is_auto else 0, data_json, now, now)
                )
            conn.commit()
        return {"success": True, "slot_name": slot_name, "created_at": now}

    def load_slot(self, session_id: str, slot_name: str) -> Optional[Dict[str, Any]]:
        """读取存档 + normalize（补全缺失字段）→ 兼容老存档"""
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT data FROM saves WHERE session_id=? AND slot_name=?",
                (session_id, slot_name)
            ).fetchone()
        if not row:
            return None
        raw = json.loads(row["data"])
        return self._normalize_save_data(raw)

    def list_slots(self, session_id: str) -> List[Dict[str, Any]]:
        """列出当前 session 所有存档槽位（带时间戳）"""
        with self._lock, self._get_conn() as conn:
            rows = conn.execute(
                "SELECT slot_name, is_auto, created_at, updated_at FROM saves "
                "WHERE session_id=? ORDER BY is_auto DESC, updated_at DESC",
                (session_id,)
            ).fetchall()
        return [
            {
                "slot_name": r["slot_name"],
                "is_auto": bool(r["is_auto"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def delete_slot(self, session_id: str, slot_name: str) -> bool:
        """删除一个槽位的存档"""
        with self._lock, self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM saves WHERE session_id=? AND slot_name=?",
                (session_id, slot_name)
            )
            conn.commit()
        return cursor.rowcount > 0

    # ---------- 关键：normalize 兼容老存档 ----------
    def _normalize_save_data(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        读取存档时调用 —— 把 game_state 的所有字段和默认值做并集
        这样以后我们给 game_state 加新字段（比如 player_mana），
        老存档读进来也会自动补默认值，不会 KeyError 或崩溃
        """
        defaults = self._defaults
        # 用默认值填充缺失字段（不覆盖已有值）；深拷贝避免多个存档共享同一个 list/dict
        for key, default_val in defaults.items():
            if key not in raw:
                raw[key] = copy.deepcopy(default_val)
        return raw
