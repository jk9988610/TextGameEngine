"""
用户认证管理器（引擎模块）
用 werkzeug.security 做密码哈希（Flask 自带，零额外依赖）
SQLite 独立 auth.db —— 和 offline_saves.db 分开，后续迁移 MySQL 互不影响

职责：纯数据层（register/login/verify），不碰 Flask session —— app.py 管 session 绑定
"""
import sqlite3
import os
import threading
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

# werkzeug.security 是 Flask 自带的，零额外 pip 安装
from werkzeug.security import generate_password_hash, check_password_hash


class AuthManager:
    """SQLite 用户认证管理器 —— 在线模式专属"""

    def __init__(self, db_path: str = "game_data/auth.db"):
        self._db_path = db_path
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
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                )
            """)
            conn.commit()

    # ---------- 核心接口 ----------
    def register(self, username: str, password: str) -> Tuple[bool, str, Optional[int]]:
        """
        注册新用户
        :return: (success, message, user_id_or_None)
        """
        username = username.strip()
        # 基本校验
        if not username or len(username) < 2:
            return False, "❌ 用户名至少 2 个字符", None
        if not password or len(password) < 4:
            return False, "❌ 密码至少 4 个字符", None
        if len(username) > 32:
            return False, "❌ 用户名太长（最多 32 个字符）", None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pw_hash = generate_password_hash(password)

        with self._lock, self._get_conn() as conn:
            # 检查用户名重复
            existing = conn.execute(
                "SELECT id FROM users WHERE username=?", (username,)
            ).fetchone()
            if existing:
                return False, f"❌ 用户名「{username}」已被占用", None

            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at, last_login_at) "
                "VALUES (?, ?, ?, ?)",
                (username, pw_hash, now, None)
            )
            conn.commit()
            user_id = cursor.lastrowid

        return True, f"✅ 注册成功，欢迎「{username}」！", user_id

    def login(self, username: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        登录验证
        :return: (success, message, user_info_or_None)
        """
        username = username.strip()
        if not username or not password:
            return False, "❌ 请输入用户名和密码", None

        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash FROM users WHERE username=?", (username,)
            ).fetchone()
            if not row:
                return False, f"❌ 用户「{username}」不存在", None
            if not check_password_hash(row["password_hash"], password):
                return False, "❌ 密码错误", None

            # 更新最后登录时间
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE users SET last_login_at=? WHERE id=?", (now, row["id"])
            )
            conn.commit()

            user_info = {"user_id": row["id"], "username": row["username"]}
        return True, f"✅ 欢迎回来，「{row['username']}」！", user_info

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """根据 user_id 查用户（用于 /api/auth/me 接口）"""
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT id, username, created_at, last_login_at FROM users WHERE id=?", (user_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "user_id": row["id"],
            "username": row["username"],
            "created_at": row["created_at"],
            "last_login_at": row["last_login_at"],
        }
