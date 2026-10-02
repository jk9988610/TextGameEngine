"""
编辑器数据管理层（引擎配套工具模块）
负责：对 scenes / items 两类游戏数据做校验、引用完整性检查、原子写盘与自动备份。

设计原则：
  1. 只产出/修改 JSON 数据文件，绝不改引擎代码；
  2. 所有写操作先整表校验通过才落盘，错误时原文件不动；
  3. 每次覆盖前把旧文件复制成 *.bak（回滚保险）；
  4. 直接在传入的内存数据 dict 上原地修改 —— app.py 的 GAME_DATA 被所有会话
     共享引用，改完所有存活会话立刻看到新数据（单 worker 前提下）。
"""
import os
import re
import json
import shutil
import tempfile
import threading
from typing import Dict, Any, List, Tuple, Optional, Set

ID_PATTERN = re.compile(r'^[a-z0-9_]{1,32}$')
NAME_MAX = 40
DESC_MAX = 2000


class ValidationError(Exception):
    """校验失败（消息直接展示给编辑器用户）"""


class EditorManager:
    """地点/物品数据的读写与校验"""

    def __init__(self, data_dir: str = "game_data"):
        self._dir = data_dir
        self._file_map = {"scenes": "scenes.json", "items": "items.json"}
        self._lock = threading.Lock()  # 写盘串行化，避免并发保存互相覆盖

    # ---------- 读取 ----------
    def load(self, key: str) -> Dict[str, Any]:
        """从磁盘重新读取一份数据（scenes 或 items）"""
        path = self._path(key)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- 场景 upsert ----------
    def upsert_scene(self, scenes: Dict[str, Any], items: Dict[str, Any],
                     enemies: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        新建或更新一个场景。
        :return: {"success", "message", "warnings"?, "id"?}
        """
        with self._lock:
            cleaned, is_new, sid = self._clean_scene(payload, scenes)
            errors, warnings = self._validate_scene(cleaned, scenes, items, enemies, is_new)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            scenes[sid] = cleaned
            self._write_json("scenes", scenes)
            msg = f"地点【{cleaned['name']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "warnings": warnings, "id": sid}

    def delete_scene(self, scenes: Dict[str, Any], items: Dict[str, Any],
                     scene_id: str, initial_scene: str,
                     live_scene_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
        """删除一个场景（带引用保护）"""
        with self._lock:
            if scene_id not in scenes:
                return {"success": False, "message": f"地点不存在：{scene_id}"}
            if scene_id == initial_scene:
                return {"success": False, "message": "初始场景不能删除（可在游戏配置中更换初始场景后再删）"}

            # 有玩家正身处该场景 → 拒绝（删掉会把人家卡进不存在的场景）
            if scene_id in (live_scene_ids or set()):
                return {"success": False, "message": "当前有玩家正在这个场景里，不能删除"}

            # 被其他场景的出口引用 → 拒绝
            refs = [sid for sid, s in scenes.items()
                    if sid != scene_id and scene_id in s.get("exits", [])]
            if refs:
                return {"success": False,
                        "message": f"被其他地点的出口引用，不能删除：{', '.join(refs)}。请先删掉这些出口。"}

            name = scenes[scene_id].get("name", scene_id)
            del scenes[scene_id]
            self._write_json("scenes", scenes)
            return {"success": True, "message": f"地点【{name}】已删除"}

    # ---------- 物品 upsert ----------
    def upsert_item(self, scenes: Dict[str, Any], items: Dict[str, Any],
                    config: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建或更新一个物品"""
        with self._lock:
            cleaned, is_new, iid = self._clean_item(payload, items)
            errors = self._validate_item(cleaned, items, is_new)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            items[iid] = cleaned
            self._write_json("items", items)

            warnings = []
            if not any(iid in s.get("items_here", []) for s in scenes.values()) \
                    and iid not in config.get("initial_inventory", []):
                warnings.append("该物品还没有放在任何地点，也不在初始背包里 —— 游戏中暂时无法获得。")
            msg = f"物品【{cleaned['name']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "warnings": warnings, "id": iid}

    def delete_item(self, scenes: Dict[str, Any], items: Dict[str, Any],
                    config: Dict[str, Any], item_id: str) -> Dict[str, Any]:
        """删除一个物品（被场景放置/初始背包引用时拒绝）。直接在内存 dict 上原地删除。"""
        with self._lock:
            if item_id not in items:
                return {"success": False, "message": f"物品不存在：{item_id}"}
            if item_id in config.get("initial_inventory", []):
                return {"success": False, "message": "该物品在游戏配置的初始背包里，不能删除"}
            refs = [sid for sid, s in scenes.items()
                    if item_id in s.get("items_here", [])]
            if refs:
                return {"success": False,
                        "message": f"被以下地点放置着，不能删除：{', '.join(refs)}。请先从这些地点移除。"}
            name = items[item_id].get("name", item_id)
            del items[item_id]
            self._write_json("items", items)
            return {"success": True, "message": f"物品【{name}】已删除"}

    # ---------- 内部：场景清洗/校验 ----------
    def _clean_scene(self, payload: Dict[str, Any],
                     scenes: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        sid = str(payload.get("id", "")).strip()
        is_new = sid not in scenes
        exits = self._dedupe_strs(payload.get("exits", []))
        locked = {}
        raw_locked = payload.get("locked_exits") or {}
        if isinstance(raw_locked, dict):
            for k, v in raw_locked.items():
                k = str(k).strip()
                if k:
                    locked[k] = bool(v)
        elif isinstance(raw_locked, list):
            # 前端也可能直接传"被锁定的出口 id 列表"
            for k in raw_locked:
                locked[str(k).strip()] = True
        cleaned = {
            "id": sid,
            "name": str(payload.get("name", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
            "exits": exits,
            "items_here": self._dedupe_strs(payload.get("items_here", [])),
            "locked_exits": {k: locked[k] for k in exits if k in locked},
            "enemies_here": self._dedupe_strs(payload.get("enemies_here", [])),
        }
        return cleaned, is_new, sid

    def _validate_scene(self, scene: Dict[str, Any], scenes: Dict[str, Any],
                        items: Dict[str, Any], enemies: Dict[str, Any],
                        is_new: bool) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []
        sid = scene["id"]

        if not ID_PATTERN.match(sid):
            errors.append("地点 ID 只能用小写字母/数字/下划线，长度 1~32（例：deep_cave_2）")
        if not scene["name"]:
            errors.append("名称不能为空")
        elif len(scene["name"]) > NAME_MAX:
            errors.append(f"名称不能超过 {NAME_MAX} 个字")
        if not scene["description"]:
            errors.append("描述不能为空")
        elif len(scene["description"]) > DESC_MAX:
            errors.append(f"描述不能超过 {DESC_MAX} 个字")

        # 出口
        for ex in scene["exits"]:
            if not ID_PATTERN.match(ex):
                errors.append(f"出口 ID 非法：{ex}")
            elif ex == sid:
                errors.append("出口不能指向场景自己")
            elif ex not in scenes:
                warnings.append(f"出口「{ex}」指向的地点还不存在（保存后记得新建，或这是稍后要建的地点）")
            elif sid not in scenes[ex].get("exits", []):
                warnings.append(f"「{scenes[ex].get('name', ex)}」还没有通向本场景的反向出口（单向通路，确认是否符合预期）")

        # 锁定的出口必须在出口列表里
        for k in scene["locked_exits"]:
            if k not in scene["exits"]:
                errors.append(f"锁定出口 {k} 不在出口列表中")

        # 场景物品必须存在（缺失会让游戏渲染直接报错，所以是硬错误）
        for iid in scene["items_here"]:
            if iid not in items:
                errors.append(f"场景里的物品不存在：{iid}（请先在物品页新建）")

        # 敌人：数据文件里没有时只给警告（敌人编辑器是后续里程碑）
        for eid in scene["enemies_here"]:
            if not ID_PATTERN.match(eid):
                errors.append(f"敌人 ID 非法：{eid}")
            elif enemies and eid not in enemies:
                warnings.append(f"敌人「{eid}」在敌人数据中不存在（敌人编辑功能尚未开放，需手动改 enemies.json）")

        return errors, warnings

    # ---------- 内部：物品清洗/校验 ----------
    def _clean_item(self, payload: Dict[str, Any],
                    items: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        iid = str(payload.get("id", "")).strip()
        is_new = iid not in items
        is_weapon = bool(payload.get("is_weapon", False))
        cleaned: Dict[str, Any] = {
            "id": iid,
            "name": str(payload.get("name", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
        }
        if is_weapon:
            raw_damage = payload.get("damage", 5)
            try:
                damage = int(raw_damage)
            except (TypeError, ValueError):
                damage = -1  # 交给校验报错
            cleaned["is_weapon"] = True
            cleaned["damage"] = damage
        return cleaned, is_new, iid

    def _validate_item(self, item: Dict[str, Any], items: Dict[str, Any],
                       is_new: bool) -> List[str]:
        errors: List[str] = []
        if not ID_PATTERN.match(item["id"]):
            errors.append("物品 ID 只能用小写字母/数字/下划线，长度 1~32（例：healing_potion）")
        if not item["name"]:
            errors.append("名称不能为空")
        elif len(item["name"]) > NAME_MAX:
            errors.append(f"名称不能超过 {NAME_MAX} 个字")
        if not item["description"]:
            errors.append("描述不能为空")
        elif len(item["description"]) > DESC_MAX:
            errors.append(f"描述不能超过 {DESC_MAX} 个字")
        if item.get("is_weapon"):
            if not isinstance(item.get("damage"), int) or item["damage"] < 0:
                errors.append("武器伤害必须是非负整数")
        return errors

    # ---------- 内部：工具 ----------
    @staticmethod
    def _dedupe_strs(value: Any) -> List[str]:
        """把任意输入整理成去重、去空的字符串列表（保持顺序）"""
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list):
            return []
        result: List[str] = []
        for v in value:
            s = str(v).strip()
            if s and s not in result:
                result.append(s)
        return result

    def _path(self, key: str) -> str:
        return os.path.join(self._dir, self._file_map[key])

    def _write_json(self, key: str, data: Dict[str, Any]) -> None:
        """备份旧文件 + 临时文件原子替换（调用方已持锁）"""
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        # 同目录临时文件，保证 os.replace 在同一文件系统上（原子）
        fd, tmp_path = tempfile.mkstemp(
            prefix=".tmp_", suffix=".json", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
