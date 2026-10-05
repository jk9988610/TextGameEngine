"""
编辑器数据管理层（引擎配套工具模块）
负责：对 scenes / items / tags / characters / events 游戏数据做校验、引用完整性检查、原子写盘与自动备份。

其中 events.json 是**只读**的事件字典（触发器/条件/效果的词汇表），
由用户手写或引擎自带示例；本模块只读取它来驱动清洗与校验，不写入。

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

# Beat 战斗意图名集合（敌人 brain/征兆/意图文案的合法动作键以此为校验依据）
from .beat_combat import INTENT_TEXT
# 事件字典（触发器/条件/效果词汇表）与引擎原子契约
from . import event_dictionary
from .effects import CONDITION_ATOMS, EFFECT_ATOM_FIELDS

ID_PATTERN = re.compile(r'^[a-z0-9_]{1,32}$')
NAME_MAX = 40
DESC_MAX = 2000
TITLE_MAX = 40
INTRO_MAX = 200

# 标签（tags.json）规格：标签 = 目标类型 + 一组字段组
#   target  —— 标签能贴到哪类对象上（item 物品 / scene 地点 / character 角色）
#   fields  —— 贴了该标签后编辑器展示、校验并持久化的字段
#   runtime —— 可选：兼容运行时读取的派生键（flag 为真值键名）
TAG_TARGETS = ("item", "scene", "character")
FIELD_TYPES = ("int", "text", "textarea", "text_list", "item_list")
FIELD_KEY_PATTERN = re.compile(r'^[a-z0-9_]{1,32}$')

# 事件系统的可用触发器 / 条件 / 效果清单来自 events.json（事件字典），
# 本模块只按字典里的 params 规格做通用清洗与校验，不再硬编码任何条目。


class ValidationError(Exception):
    """校验失败（消息直接展示给编辑器用户）"""


class EditorManager:
    """地点/物品数据的读写与校验"""

    def __init__(self, data_dir: str = "game_data"):
        self._dir = data_dir
        self._file_map = {
            "scenes": "scenes.json",
            "items": "items.json",
            "tags": "tags.json",
            "characters": "characters.json",
            "events": "events.json",          # 事件字典（只读词汇表）
            "config": "game_config.json",
            "layouts": "npc_layouts.json",   # 画布手动布局坐标（非游戏内容）
            "scene_layouts": "scene_layouts.json",  # 场景地图画布坐标（非游戏内容）
            "editor_settings": "editor_settings.json",  # 编辑器偏好（非游戏内容）
        }
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
            # 场景地图坐标是辅助数据：场景没了，坐标一并清掉
            layouts = self.load_scene_layouts()
            if scene_id in layouts:
                del layouts[scene_id]
                self._write_json("scene_layouts", layouts)
            return {"success": True, "message": f"地点【{name}】已删除"}

    # ---------- 物品 upsert ----------
    def upsert_item(self, scenes: Dict[str, Any], items: Dict[str, Any],
                    enemies: Dict[str, Any], config: Dict[str, Any],
                    tags: Dict[str, Any],
                    payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建或更新一个物品"""
        with self._lock:
            cleaned, is_new, iid = self._clean_item(payload, items, tags)
            errors = self._validate_item(cleaned, items, tags, is_new)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            items[iid] = cleaned
            self._write_json("items", items)

            warnings = []
            obtainable = (
                any(iid in s.get("items_here", []) for s in scenes.values())
                or any(any(g.get("item_id") == iid for g in s.get("shop_items", []))
                    for s in scenes.values())
                or any(iid in e.get("reward_items", []) for e in enemies.values())
                or iid in config.get("initial_inventory", [])
            )
            if not obtainable:
                warnings.append("该物品还没有放在任何地点、没有商店在售、没有敌人掉落、"
                                "也不在初始背包里 —— 游戏中暂时无法获得。")
            msg = f"物品【{cleaned['name']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "warnings": warnings, "id": iid}

    def delete_item(self, scenes: Dict[str, Any], items: Dict[str, Any],
                    enemies: Dict[str, Any], config: Dict[str, Any],
                    item_id: str) -> Dict[str, Any]:
        """删除一个物品（被场景放置/商店售卖/敌人掉落/初始背包引用时拒绝）。"""
        with self._lock:
            if item_id not in items:
                return {"success": False, "message": f"物品不存在：{item_id}"}
            if item_id in config.get("initial_inventory", []):
                return {"success": False, "message": "该物品在游戏配置的初始背包里，不能删除"}
            refs = [sid for sid, s in scenes.items()
                    if item_id in s.get("items_here", [])]
            shop_refs = [sid for sid, s in scenes.items()
                         if any(g.get("item_id") == item_id for g in s.get("shop_items", []))]
            enemy_refs = [eid for eid, e in enemies.items()
                          if item_id in e.get("reward_items", [])]
            if refs or shop_refs or enemy_refs:
                places = sorted(set(refs + shop_refs))
                msg_parts = []
                if places:
                    msg_parts.append(f"被以下地点使用中（放置或在售）：{', '.join(places)}")
                if enemy_refs:
                    msg_parts.append(f"被以下敌人作为战利品掉落：{', '.join(sorted(enemy_refs))}")
                return {"success": False,
                        "message": "；".join(msg_parts) + "，不能删除。请先移除。"}
            name = items[item_id].get("name", item_id)
            del items[item_id]
            self._write_json("items", items)
            return {"success": True, "message": f"物品【{name}】已删除"}

    # ---------- 标签 upsert / delete ----------
    def load_tags(self) -> Dict[str, Any]:
        """读取全部标签定义（tags.json）；缺失/损坏按空处理。"""
        try:
            data = self.load("tags")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def upsert_tag(self, tags: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建/更新一个标签（id/label/target/fields）。"""
        with self._lock:
            cleaned, is_new = self._clean_tag(payload, tags)
            errors = self._validate_tag(cleaned, tags, is_new)
            if errors:
                return {"success": False, "message": "；".join(errors)}
            tags[cleaned["id"]] = cleaned
            self._write_json("tags", tags)
            msg = f"标签【{cleaned['label']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "id": cleaned["id"]}

    def delete_tag(self, tags: Dict[str, Any], items: Dict[str, Any],
                   characters: Dict[str, Any],
                   tag_id: str) -> Dict[str, Any]:
        """删除一个标签（仍被物品或角色贴用时拒绝）。"""
        with self._lock:
            if tag_id not in tags:
                return {"success": False, "message": f"标签不存在：{tag_id}"}
            item_refs = [iid for iid, it in items.items()
                         if tag_id in (it.get("tags") or [])]
            char_refs = [cid for cid, c in characters.items()
                         if tag_id in (c.get("tags") or [])]
            if item_refs or char_refs:
                parts = []
                if item_refs:
                    parts.append(f"{len(item_refs)} 个物品（{', '.join(sorted(item_refs))}）")
                if char_refs:
                    parts.append(f"{len(char_refs)} 个角色（{', '.join(sorted(char_refs))}）")
                return {"success": False,
                        "message": f"还有 {'、'.join(parts)} 贴用了这个标签，"
                                   f"不能删除。请先摘掉。"}
            label = tags[tag_id].get("label", tag_id)
            del tags[tag_id]
            self._write_json("tags", tags)
            return {"success": True, "message": f"标签【{label}】已删除"}

    def _clean_tag(self, payload: Dict[str, Any],
                   tags: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        tid = str(payload.get("id", "")).strip()
        target = str(payload.get("target", "item")).strip() or "item"
        fields: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for raw in payload.get("fields") or []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key", "")).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            field: Dict[str, Any] = {
                "key": key,
                "label": str(raw.get("label", "")).strip() or key,
                "type": str(raw.get("type", "text")).strip() or "text",
            }
            if "default" in raw and raw["default"] not in (None, ""):
                field["default"] = raw["default"]
            for k in ("min", "max"):
                if raw.get(k) not in (None, ""):
                    field[k] = self._to_int(raw.get(k), -1)
            fields.append(field)
        cleaned: Dict[str, Any] = {"id": tid, "label": str(payload.get("label", "")).strip(),
                                   "target": target, "fields": fields}
        rt_flag = str((payload.get("runtime") or {}).get("flag", "")).strip()
        if rt_flag:
            cleaned["runtime"] = {"flag": rt_flag}
        return cleaned, tid not in tags

    def _validate_tag(self, tag: Dict[str, Any], tags: Dict[str, Any],
                      is_new: bool) -> List[str]:
        errors: List[str] = []
        tid = str(tag.get("id", "")).strip()
        if not ID_PATTERN.match(tid):
            errors.append("标签 ID 只能用小写字母/数字/下划线，长度 1~32（例：weapon）")
        if not tag.get("label"):
            errors.append("标签名称不能为空")
        elif len(tag["label"]) > NAME_MAX:
            errors.append(f"标签名称不能超过 {NAME_MAX} 个字")
        if tag.get("target") not in TAG_TARGETS:
            errors.append(f"标签目标类型只能是：{'/'.join(TAG_TARGETS)}")
        for field in tag.get("fields", []):
            key = field["key"]
            if not FIELD_KEY_PATTERN.match(key):
                errors.append(f"字段 key 非法：{key}（小写字母/数字/下划线）")
            if field.get("type") not in FIELD_TYPES:
                errors.append(f"字段【{key}】类型不支持：{field.get('type')}"
                              f"（可用：{'/'.join(FIELD_TYPES)}）")
            if field.get("type") == "int":
                for k in ("min", "max"):
                    if k in field and not isinstance(field[k], int):
                        errors.append(f"字段【{key}】的 {k} 必须是整数")
        return errors

    # ---------- 角色 upsert / delete ----------
    def upsert_character(self, characters: Dict[str, Any], scenes: Dict[str, Any],
                         items: Dict[str, Any], tags: Dict[str, Any],
                         payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建/更新一个角色（基础字段 + 标签字段组 + 可选对话树 / Beat 配置）。

        角色是引擎唯一的"人物"概念：贴 enemy 标签即可战斗，贴 talkable 标签即可
        对话，两个标签可以同时贴。字段由标签（tags.json）声明，引擎只认派生视图。
        """
        with self._lock:
            cleaned, is_new, cid = self._clean_character(payload, characters, tags)
            errors, warnings = self._validate_character(
                cleaned, characters, scenes, items, tags)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            characters[cid] = cleaned
            self._write_json("characters", characters)
            msg = f"角色【{cleaned['name']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "warnings": warnings, "id": cid}

    def delete_character(self, scenes: Dict[str, Any], characters: Dict[str, Any],
                         character_id: str,
                         live_battle_ids: Optional[Set[str]] = None,
                         live_dialogue_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
        """删除一个角色（被场景放置 / 有玩家正在与之战斗或对话时拒绝）"""
        with self._lock:
            if character_id not in characters:
                return {"success": False, "message": f"角色不存在：{character_id}"}
            if character_id in (live_battle_ids or set()):
                return {"success": False,
                        "message": "当前有玩家正在与这个角色战斗，不能删除"}
            if character_id in (live_dialogue_ids or set()):
                return {"success": False,
                        "message": "当前有玩家正在和这个角色对话，不能删除"}
            refs = [sid for sid, s in scenes.items()
                    if character_id in s.get("enemies_here", [])]
            if refs:
                return {"success": False,
                        "message": f"被以下地点作为出没角色使用中，不能删除："
                                   f"{', '.join(sorted(refs))}。请先移除。"}
            name = characters[character_id].get("name", character_id)
            del characters[character_id]
            self._write_json("characters", characters)
            return {"success": True, "message": f"角色【{name}】已删除"}

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
        # 商店货物：[{"item_id": ..., "price": ...}]，按物品去重（同物品保留最后一次价格）
        shop_items: List[Dict[str, Any]] = []
        seen_shop: set = set()
        for g in payload.get("shop_items") or []:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("item_id", "")).strip()
            if not gid or gid in seen_shop:
                continue
            try:
                price = int(g.get("price", 0))
            except (TypeError, ValueError):
                price = -1  # 交给校验报错
            seen_shop.add(gid)
            shop_items.append({"item_id": gid, "price": price})

        cleaned = {
            "id": sid,
            "name": str(payload.get("name", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
            "exits": exits,
            "items_here": self._dedupe_strs(payload.get("items_here", [])),
            "locked_exits": {k: locked[k] for k in exits if k in locked},
            "enemies_here": self._dedupe_strs(payload.get("enemies_here", [])),
            "shop_items": shop_items,
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

        # 商店货物：物品必须存在、价格必须是非负整数
        for g in scene["shop_items"]:
            gid = g["item_id"]
            if gid not in items:
                errors.append(f"商店售卖的物品不存在：{gid}（请先在物品页新建）")
            if not isinstance(g["price"], int) or g["price"] < 0:
                errors.append(f"商品【{gid}】价格必须是非负整数")

        # 敌人：数据文件里没有时只给警告（允许先布置场景再建敌人）
        for eid in scene["enemies_here"]:
            if not ID_PATTERN.match(eid):
                errors.append(f"敌人 ID 非法：{eid}")
            elif enemies and eid not in enemies:
                warnings.append(f"敌人「{eid}」还不存在（请在敌人页签新建，否则游戏中不会出现）")

        return errors, warnings

    # ---------- 内部：物品清洗/校验 ----------
    def _clean_item(self, payload: Dict[str, Any], items: Dict[str, Any],
                    tags: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        """物品 = 基础三字段 + 标签 + 各标签声明的字段（扁平存放，键即字段 key）。

        字段值按 tags.json 的字段类型归一；标签自带的 runtime.flag 会派生成运行时
        读取的布尔键（如 is_weapon / usable），让引擎核心暂时不必知道"标签"概念。
        """
        iid = str(payload.get("id", "")).strip()
        is_new = iid not in items
        cleaned: Dict[str, Any] = {
            "id": iid,
            "name": str(payload.get("name", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
        }
        applied = self._dedupe_strs(payload.get("tags", []))
        if applied:
            cleaned["tags"] = applied
        for tid in applied:
            spec = tags.get(tid)
            if not isinstance(spec, dict):
                continue  # 未知标签交给校验报错
            for field in spec.get("fields", []) or []:
                key = str(field.get("key", "")).strip()
                if not key:
                    continue
                cleaned[key] = self._coerce_field(
                    field, payload.get(key, field.get("default")))
            flag = (spec.get("runtime") or {}).get("flag")
            if flag:
                cleaned[str(flag)] = True
        return cleaned, is_new, iid

    @staticmethod
    def _coerce_field(field: Dict[str, Any], value: Any) -> Any:
        """按字段类型归一字段值；解析不了的返回哨兵值交给校验报错。"""
        ftype = field.get("type", "text")
        if ftype == "int":
            if value is None or value == "":
                value = field.get("default", 0)
            try:
                return int(value)
            except (TypeError, ValueError):
                return -1
        if ftype in ("text", "textarea"):
            return str(value if value is not None else "").strip()
        if ftype in ("text_list", "item_list"):
            if isinstance(value, str):
                value = value.split(",")
            return EditorManager._dedupe_strs(value)
        return value

    def _validate_item(self, item: Dict[str, Any], items: Dict[str, Any],
                       tags: Dict[str, Any], is_new: bool) -> List[str]:
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

        for tid in item.get("tags", []):
            spec = tags.get(tid)
            if not isinstance(spec, dict):
                errors.append(f"标签不存在：{tid}（请先在标签页新建）")
                continue
            target = spec.get("target", "item")
            if target != "item":
                errors.append(f"标签【{spec.get('label') or tid}】只能贴在"
                              f"{'地点' if target == 'scene' else '角色'}上，不能用于物品")
                continue
            errors += self._field_errors(spec, item, items,
                                         prefix=f"标签【{spec.get('label') or tid}】")
        return errors

    def _field_errors(self, spec: Dict[str, Any], entity: Dict[str, Any],
                      items: Dict[str, Any], prefix: str) -> List[str]:
        """按字段规格校验实体上的字段值（int 区间 / item_list 引用）。"""
        errors: List[str] = []
        for field in spec.get("fields", []) or []:
            key = str(field.get("key", "")).strip()
            if not key:
                continue
            label = field.get("label") or key
            value = entity.get(key)
            ftype = field.get("type", "text")
            if ftype == "int":
                if not isinstance(value, int) or isinstance(value, bool):
                    errors.append(f"{prefix}的{label}必须是整数")
                    continue
                lo = field.get("min")
                hi = field.get("max")
                if isinstance(lo, int) and value < lo:
                    errors.append(f"{prefix}的{label}不能小于 {lo}")
                if isinstance(hi, int) and value > hi:
                    errors.append(f"{prefix}的{label}不能大于 {hi}")
            elif ftype == "item_list":
                for iid in value or []:
                    if iid not in items:
                        errors.append(f"{prefix}的{label}引用了不存在的物品：{iid}")
        return errors

    # ---------- 开局配置 upsert ----------
    def upsert_config(self, config: Dict[str, Any], scenes: Dict[str, Any],
                      items: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """保存游戏开局配置（标题/简介/初始场景/背包/金币/玩家属性）。

        action_time / event_rules 等未在表单里的字段原样保留；
        直接原地更新传入的 config dict（app.py 的 GAME_DATA 被会话共享引用）。
        """
        with self._lock:
            cleaned, errors = self._clean_and_validate_config(payload, scenes, items)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            # 白名单覆盖开局字段，其余字段（action_time/event_rules）保留
            for k in ("game_title", "game_intro", "initial_scene",
                      "initial_inventory", "initial_gold", "player"):
                config[k] = cleaned[k]
            self._write_json("config", config)
            return {"success": True, "message": "游戏设置已保存（对新开游戏/重置生效）"}

    # ---------- 事件规则 upsert / delete ----------
    def upsert_event_rule(self, config: Dict[str, Any], scenes: Dict[str, Any],
                          items: Dict[str, Any], enemies: Dict[str, Any],
                          npcs: Dict[str, Any], payload: Dict[str, Any]
                          ) -> Dict[str, Any]:
        """新建/更新一条事件规则（按 id 区分），整体重写 config.event_rules。"""
        with self._lock:
            cleaned, is_new, rid = self._clean_event_rule(payload)
            errors = self._validate_event_rule(
                cleaned, scenes, items, enemies, npcs,
                is_new=is_new, rules=config.get("event_rules", []))
            if errors:
                return {"success": False, "message": "；".join(errors)}

            rules = [r for r in config.get("event_rules", []) if r.get("id") != rid]
            rules.append(cleaned)
            rules.sort(key=lambda r: r.get("id", ""))
            config["event_rules"] = rules
            self._write_json("config", config)
            msg = f"事件规则【{cleaned.get('label') or rid}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg + "（对新开游戏/重置生效）", "id": rid}

    def delete_event_rule(self, config: Dict[str, Any], rule_id: str) -> Dict[str, Any]:
        """删除一条事件规则。"""
        with self._lock:
            rules = config.get("event_rules", [])
            if not any(r.get("id") == rule_id for r in rules):
                return {"success": False, "message": f"事件规则不存在：{rule_id}"}
            config["event_rules"] = [r for r in rules if r.get("id") != rule_id]
            self._write_json("config", config)
            return {"success": True, "message": f"事件规则【{rule_id}】已删除（对新开游戏/重置生效）"}

    # ---------- 角色对话画布布局（坐标；非游戏内容，独立于 characters.json） ----------
    LAYOUT_RANGE = (-10000, 10000)
    # 「开始对话」合成卡的布局保留键（不是对话节点，但坐标与普通节点同通道持久化，
    # 修剪孤儿坐标时必须保留；ID_PATTERN 同样接受该键）
    START_CARD_ID = "__start__"

    def load_layouts(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        """读取全部布局：{npc_id: {node_id: {x,y}}}；文件缺失/损坏按空处理。"""
        try:
            data = self.load("layouts")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save_layout(self, npc_id: str, layout: Dict[str, Any]) -> Dict[str, Any]:
        """保存单个 NPC 的节点坐标。只接受 {node_id:{x:int,y:int}}，非法条目丢弃；
        布局是辅助数据，NPC/节点是否存在不在此拦截（孤儿在 prune 时清理）。"""
        if not (isinstance(npc_id, str) and ID_PATTERN.match(npc_id)):
            return {"success": False, "message": "NPC ID 非法"}
        lo, hi = self.LAYOUT_RANGE
        clean: Dict[str, Dict[str, int]] = {}
        for node_id, pos in (layout or {}).items():
            if not (isinstance(node_id, str) and ID_PATTERN.match(node_id)):
                continue
            if not isinstance(pos, dict):
                continue
            try:
                x, y = int(pos.get("x")), int(pos.get("y"))
            except (TypeError, ValueError):
                continue
            if lo <= x <= hi and lo <= y <= hi:
                clean[node_id] = {"x": x, "y": y}
        with self._lock:
            all_layouts = self.load_layouts()
            if clean:
                all_layouts[npc_id] = clean
            else:
                all_layouts.pop(npc_id, None)   # 无有效坐标时不保留空键
            self._write_json("layouts", all_layouts)
        return {"success": True, "id": npc_id, "nodes": len(clean)}

    def prune_layouts(self, npcs: Dict[str, Any]) -> None:
        """删除布局里指向已不存在 NPC/节点的孤儿坐标（保存 NPC 后可调用）。"""
        with self._lock:
            layouts = self.load_layouts()
            changed = False
            # 删除整个已不存在的 NPC 布局
            for npc_id in list(layouts.keys()):
                if npc_id not in npcs:
                    del layouts[npc_id]; changed = True; continue
                # 删除该 NPC 已不存在节点的坐标（__start__ 是开始卡保留键，始终保留）
                valid_ids = set((npcs[npc_id] or {}).get("nodes", {}).keys())
                valid_ids.add(self.START_CARD_ID)
                kept = {nid: p for nid, p in layouts[npc_id].items() if nid in valid_ids}
                if len(kept) != len(layouts[npc_id]):
                    layouts[npc_id] = kept; changed = True
            if changed:
                self._write_json("layouts", layouts)

    # ---------- 场景地图布局（坐标；非游戏内容，独立于 scenes.json） ----------
    def load_scene_layouts(self) -> Dict[str, Dict[str, int]]:
        """读取场景地图坐标：{scene_id: {x,y}}；文件缺失/损坏按空处理。"""
        try:
            data = self.load("scene_layouts")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save_scene_layout(self, scene_id: str, pos: Dict[str, Any]) -> Dict[str, Any]:
        """保存单个场景在地图画布上的坐标。只接受 {x:int,y:int}；
        布局是辅助数据，场景是否存在不在此拦截（删除场景时顺带清理）。"""
        if not (isinstance(scene_id, str) and ID_PATTERN.match(scene_id)):
            return {"success": False, "message": "地点 ID 非法"}
        lo, hi = self.LAYOUT_RANGE
        try:
            x, y = int((pos or {}).get("x")), int((pos or {}).get("y"))
        except (TypeError, ValueError):
            return {"success": False, "message": "坐标必须是整数"}
        if not (lo <= x <= hi and lo <= y <= hi):
            return {"success": False, "message": "坐标超出范围"}
        with self._lock:
            layouts = self.load_scene_layouts()
            layouts[scene_id] = {"x": x, "y": y}
            self._write_json("scene_layouts", layouts)
        return {"success": True, "id": scene_id}

    # ---------- 编辑器设置（偏好；非游戏内容，独立于游戏数据文件） ----------
    DEFAULT_EDITOR_SETTINGS: Dict[str, Any] = {"collapse": "middle", "autosave": False}

    def _clean_settings(self, data: Any) -> Dict[str, Any]:
        """按白名单清洗设置；未知键/非法值一律回默认。"""
        clean = dict(self.DEFAULT_EDITOR_SETTINGS)
        if isinstance(data, dict):
            if data.get("collapse") in ("middle", "right"):
                clean["collapse"] = data["collapse"]
            if isinstance(data.get("autosave"), bool):
                clean["autosave"] = data["autosave"]
        return clean

    def load_editor_settings(self) -> Dict[str, Any]:
        """读取编辑器偏好；文件缺失/损坏按默认 {collapse:'middle', autosave:False}。"""
        try:
            data = self.load("editor_settings")
        except (json.JSONDecodeError, OSError):
            return dict(self.DEFAULT_EDITOR_SETTINGS)
        return self._clean_settings(data)

    def save_editor_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """合并保存编辑器偏好（仅 collapse∈middle/right、autosave 布尔两个白名单键）。"""
        if not isinstance(payload, dict):
            return {"success": False, "message": "设置必须是 JSON 对象"}
        if "collapse" in payload and payload["collapse"] not in ("middle", "right"):
            return {"success": False, "message": "收起/展开按键取值非法"}
        if "autosave" in payload and not isinstance(payload["autosave"], bool):
            return {"success": False, "message": "自动保存必须是 true/false"}
        with self._lock:
            clean = self.load_editor_settings()
            if "collapse" in payload:
                clean["collapse"] = payload["collapse"]
            if "autosave" in payload:
                clean["autosave"] = payload["autosave"]
            self._write_json("editor_settings", clean)
        return {"success": True, "settings": clean}

    # ---------- 内部：开局配置清洗/校验 ----------
    def _clean_and_validate_config(self, payload: Dict[str, Any],
                                   scenes: Dict[str, Any],
                                   items: Dict[str, Any]
                                   ) -> Tuple[Dict[str, Any], List[str]]:
        errors: List[str] = []
        title = str(payload.get("game_title", "")).strip()
        intro = str(payload.get("game_intro", "")).strip()
        initial_scene = str(payload.get("initial_scene", "")).strip()
        initial_inventory = self._dedupe_strs(payload.get("initial_inventory", []))
        initial_gold = self._to_int(payload.get("initial_gold"), -1)

        raw_player = payload.get("player") or {}
        player = {
            "hp": self._to_int(raw_player.get("hp"), -1),
            "attack": self._to_int(raw_player.get("attack"), -1),
            "defense": self._to_int(raw_player.get("defense"), -1),
        }

        if not title:
            errors.append("游戏标题不能为空")
        elif len(title) > TITLE_MAX:
            errors.append(f"游戏标题不能超过 {TITLE_MAX} 个字")
        if len(intro) > INTRO_MAX:
            errors.append(f"游戏简介不能超过 {INTRO_MAX} 个字")
        if initial_scene not in scenes:
            errors.append(f"初始地点不存在：{initial_scene or '（未选择）'}")
        for iid in initial_inventory:
            if iid not in items:
                errors.append(f"初始背包中的物品不存在：{iid}")
        if not isinstance(initial_gold, int) or initial_gold < 0:
            errors.append("初始金币必须是非负整数")
        if not (isinstance(player["hp"], int) and 1 <= player["hp"] <= 999999):
            errors.append("玩家生命值必须是 1~999999 的整数")
        for stat in ("attack", "defense"):
            if not (isinstance(player[stat], int) and 0 <= player[stat] <= 999999):
                errors.append(f"玩家{('攻击力' if stat == 'attack' else '防御力')}必须是非负整数")

        cleaned = {
            "game_title": title,
            "game_intro": intro,
            "initial_scene": initial_scene,
            "initial_inventory": initial_inventory,
            "initial_gold": initial_gold if initial_gold >= 0 else 0,
            "player": player,
        }
        return cleaned, errors

    # ---------- 事件字典（只读词汇表） ----------
    def load_events(self) -> Dict[str, Any]:
        """读取事件字典（events.json）；文件缺失/损坏按空字典处理。"""
        return event_dictionary.load(self._path("events"))

    @staticmethod
    def _ref_ctx(scenes: Any = None, items: Any = None, enemies: Any = None,
                 npcs: Any = None) -> Dict[str, Any]:
        """引用校验上下文：把各实体集合统一成一份 dict 传递。"""
        return {"scenes": scenes or {}, "items": items or {},
                "enemies": enemies or {}, "npcs": npcs or {}}

    # ---------- 内部：事件规则清洗/校验 ----------
    def _clean_event_rule(self, payload: Dict[str, Any]
                          ) -> Tuple[Dict[str, Any], bool, str]:
        rid = str(payload.get("id", "")).strip()
        is_new = rid not in {r.get("id") for r in
                             self.load("config").get("event_rules", [])}
        on = str(payload.get("on", "")).strip()

        # 事件参数过滤 if：只保留字典里该触发器声明的字段
        entry = event_dictionary.by_key(self.load_events()["triggers"]).get(on)
        event_args: Dict[str, Any] = {}
        raw_if = payload.get("if") or {}
        if entry is not None and isinstance(raw_if, dict):
            event_args = self._clean_entry_params(
                event_dictionary.params_of(entry), raw_if)

        effects = []
        for raw_eff in payload.get("do", []) or []:
            if isinstance(raw_eff, dict) and str(raw_eff.get("type", "")).strip():
                effects.append(self._clean_effect(raw_eff))

        cleaned = {
            "id": rid,
            "label": str(payload.get("label", "")).strip(),
            "on": on,
        }
        if event_args:
            cleaned["if"] = event_args
        world_cond = self._clean_condition(payload.get("when"))
        if world_cond:
            cleaned["when"] = world_cond
        cleaned["do"] = effects
        return cleaned, is_new, rid

    def _validate_event_rule(self, rule: Dict[str, Any], scenes: Dict[str, Any],
                             items: Dict[str, Any], enemies: Dict[str, Any],
                             npcs: Dict[str, Any], is_new: bool,
                             rules: List[Dict[str, Any]]) -> List[str]:
        errors: List[str] = []
        rid = rule["id"]
        if not ID_PATTERN.match(rid):
            errors.append("规则 ID 只能用小写字母/数字/下划线，长度 1~32（例：key_unlocks_cave）")
        elif len(rule.get("label", "")) > NAME_MAX:
            errors.append(f"规则备注不能超过 {NAME_MAX} 个字")
        if is_new and any(r.get("id") == rid for r in rules):
            errors.append(f"规则 ID 已存在：{rid}")

        ctx = self._ref_ctx(scenes, items, enemies, npcs)
        on = rule.get("on", "")
        entry = event_dictionary.by_key(self.load_events()["triggers"]).get(on)
        if entry is None:
            errors.append(f"不支持的触发器：{on or '（未选择）'}（事件字典里没有这个条目）")
            return errors  # 后续引用校验都依赖 on，直接返回

        # 触发条件（if）按字典参数表校验
        errors += self._params_errors(entry, rule.get("if") or {}, ctx, "触发条件")

        # 世界状态条件
        if "when" in rule:
            errors += self._condition_errors(rule["when"], ctx, prefix="附加条件")

        # 至少一个效果，且逐个校验
        if not rule.get("do"):
            errors.append("至少需要配置一个效果")
        for i, eff in enumerate(rule.get("do", [])):
            errors += self._effect_errors(
                eff, ctx, prefix=f"第 {i + 1} 个效果")
        return errors

    # ---------- 内部：通用参数清洗/校验（字典驱动） ----------
    def _clean_entry_params(self, params: List[Dict[str, Any]],
                            payload: Dict[str, Any]) -> Dict[str, Any]:
        """按字典参数表从提交数据里取值：只保留声明的字段，空值丢弃。"""
        out: Dict[str, Any] = {}
        for p in params:
            field = p["field"]
            if field not in (payload or {}):
                continue
            raw = payload.get(field)
            if p.get("type") == "int":
                try:
                    out[field] = int(raw)
                except (TypeError, ValueError):
                    out[field] = raw      # 非法值原样保留，交给校验报错
                continue
            text = str(raw).strip()
            if text:
                out[field] = text
        return out

    def _params_errors(self, entry: Dict[str, Any], data: Dict[str, Any],
                       ctx: Dict[str, Any], prefix: str) -> List[str]:
        """按字典条目的 params 校验一份数据（触发器 if / 条件 / 效果 通用）。"""
        errors: List[str] = []
        for p in event_dictionary.params_of(entry):
            label = p.get("label") or p["field"]
            val = (data or {}).get(p["field"])
            if val is None or val == "":
                if p.get("required"):
                    errors.append(f"{prefix}：{label}为必填项")
                continue
            errors += self._param_value_errors(p, val, data, ctx, prefix)
        return errors

    def _param_value_errors(self, p: Dict[str, Any], val: Any, data: Dict[str, Any],
                            ctx: Dict[str, Any], prefix: str) -> List[str]:
        label = p.get("label") or p["field"]
        ptype = p.get("type") or "text"
        if ptype == "int":
            if not isinstance(val, int) or isinstance(val, bool):
                return [f"{prefix}：{label}必须是整数"]
            errors = []
            if isinstance(p.get("min"), int) and val < p["min"]:
                errors.append(f"{prefix}：{label}不能小于 {p['min']}")
            if isinstance(p.get("max"), int) and val > p["max"]:
                errors.append(f"{prefix}：{label}不能大于 {p['max']}")
            return errors
        if ptype == "ref":
            return self._ref_errors(p, val, data, ctx, prefix)
        return []

    def _ref_errors(self, p: Dict[str, Any], val: str, data: Dict[str, Any],
                    ctx: Dict[str, Any], prefix: str) -> List[str]:
        """引用型参数的校验：直接引用查集合，node/exit 还要属于 parent 字段指定的对象。"""
        kind = p.get("ref")
        label = p.get("label") or p["field"]
        plain = {"item": "items", "scene": "scenes",
                 "enemy": "enemies", "npc": "npcs"}
        if kind in plain:
            return [] if val in ctx[plain[kind]] else [f"{prefix}：{label}不存在：{val}"]

        parent_val = (data or {}).get(p.get("parent") or "")
        if kind == "node":
            npc = ctx["npcs"].get(parent_val)
            if not npc:
                return [f"{prefix}：请先选择 NPC，再指定{label}"]
            if val not in (npc.get("nodes") or {}):
                return [f"{prefix}：{label}【{val}】不属于 NPC【{parent_val}】"]
        elif kind == "exit":
            scene = ctx["scenes"].get(parent_val)
            if not scene:
                return [f"{prefix}：请先选择地点，再指定{label}"]
            if val not in (scene.get("exits") or []):
                return [f"{prefix}：地点【{scene.get('name', parent_val)}】没有{label}：{val}"]
        return []

    @staticmethod
    def _entry_atom_errors(entry: Dict[str, Any], atoms: Dict[str, Any],
                           prefix: str, kind: str,
                           fields_table: Optional[Dict[str, Any]] = None) -> List[str]:
        """字典条目自检：绑定的原子必须存在（atoms 登记合法原子名）。

        fields_table 给出「原子 → 必须提供的参数名」，只对参数名固定的原子
        （效果）适用；条件原子以条目 key 为取值字段，故不传该表。
        """
        name = entry.get("label") or entry.get("key")
        atom = event_dictionary.atom_of(entry)
        if atom not in atoms:
            return [f"{prefix}：{kind}条目【{name}】绑定的原子不存在：{atom}"]
        if not fields_table:
            return []
        have = {p["field"] for p in event_dictionary.params_of(entry)}
        missing = [f for f in fields_table.get(atom, ()) if f not in have]
        if missing:
            return [f"{prefix}：{kind}条目【{name}】缺少原子所需参数：{'、'.join(missing)}"]
        return []

    # ---------- 内部：角色清洗 ----------
    def _clean_character(self, payload: Dict[str, Any], characters: Dict[str, Any],
                         tags: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        """角色 = 基础三字段 + 标签 + 各标签声明的字段（扁平存放，键即字段 key）。

        字段值按 tags.json 的字段类型归一；标签自带的 runtime.flag 会派生成运行时
        读取的布尔键。复杂结构（Beat 战斗配置 / 对话树）不走标签字段机制，作为顶层
        可选结构保留：只在贴了对应标签时才清洗。
        """
        cid = str(payload.get("id", "")).strip()
        is_new = cid not in characters
        cleaned: Dict[str, Any] = {
            "id": cid,
            "name": str(payload.get("name", "")).strip(),
            "description": str(payload.get("description", "")).strip(),
        }
        applied = self._dedupe_strs(payload.get("tags", []))
        if applied:
            cleaned["tags"] = applied
        scene_id = str(payload.get("scene_id", "") or "").strip()
        if scene_id:
            cleaned["scene_id"] = scene_id

        for tid in applied:
            spec = tags.get(tid)
            if not isinstance(spec, dict):
                continue  # 未知标签交给校验报错
            for field in spec.get("fields", []) or []:
                key = str(field.get("key", "")).strip()
                if not key:
                    continue
                cleaned[key] = self._coerce_field(
                    field, payload.get(key, field.get("default")))
            flag = (spec.get("runtime") or {}).get("flag")
            if flag:
                cleaned[str(flag)] = True

        if "enemy" in applied:
            self._clean_beat_fields(cleaned, payload)
        if "talkable" in applied:
            self._clean_dialogue_fields(cleaned, payload)
        return cleaned, is_new, cid

    @staticmethod
    def _clean_beat_fields(cleaned: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """Beat 战斗配置（可选）：重击伤害 + 决策器参数 + 征兆/意图文案。"""
        heavy = payload.get("heavy_attack")
        if heavy not in (None, ""):
            cleaned["heavy_attack"] = EditorManager._to_int(heavy, -1)
        brain_raw = payload.get("brain")
        if isinstance(brain_raw, dict):
            brain: Dict[str, Any] = {}
            for key in ("rhythm", "frenzy_rhythm"):
                seq = EditorManager._dedupe_strs(brain_raw.get(key, []))
                if seq:
                    brain[key] = seq
            if "dodge_player_charge" in brain_raw:
                brain["dodge_player_charge"] = bool(brain_raw.get("dodge_player_charge"))
            mode = str(brain_raw.get("low_hp_mode", "")).strip()
            if mode:
                brain["low_hp_mode"] = mode
            ratio = brain_raw.get("low_hp_ratio")
            if ratio not in (None, ""):
                try:
                    brain["low_hp_ratio"] = float(ratio)
                except (TypeError, ValueError):
                    brain["low_hp_ratio"] = -1
            if brain:
                cleaned["brain"] = brain
        tg = EditorManager._clean_intent_phrase_map(payload.get("telegraphs"))
        if tg:
            cleaned["telegraphs"] = tg
        it = EditorManager._clean_intent_label_map(payload.get("intents"))
        if it:
            cleaned["intents"] = it

    def _clean_dialogue_fields(self, cleaned: Dict[str, Any],
                               payload: Dict[str, Any]) -> None:
        """对话树（贴 talkable 时）：起始节点 + 问候规则 + 对话节点树。"""
        # 问候规则：[{"if": 条件dict|None, "node": 节点id}]
        rules: List[Dict[str, Any]] = []
        for rule in payload.get("greeting_rules", []) or []:
            if not isinstance(rule, dict):
                continue
            node = str(rule.get("node", "")).strip()
            if node:
                entry = {"node": node}
                cond = self._clean_condition(rule.get("if"))
                if cond:
                    entry["if"] = cond
                rules.append(entry)

        # 对话节点（保持前端提交顺序）
        nodes: Dict[str, Any] = {}
        for raw_id, raw_node in (payload.get("nodes") or {}).items():
            node_id = str(raw_id).strip()
            if not node_id or not isinstance(raw_node, dict):
                continue
            choices = []
            for raw_choice in raw_node.get("choices", []) or []:
                if isinstance(raw_choice, dict):
                    choices.append(self._clean_choice(raw_choice))
            nodes[node_id] = {
                "text": str(raw_node.get("text", "")).strip(),
                "choices": choices,
            }

        cleaned["greeting"] = str(payload.get("greeting", "") or "greet").strip() or "greet"
        cleaned["greeting_rules"] = rules
        cleaned["nodes"] = nodes

    def _clean_choice(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        choice: Dict[str, Any] = {"text": str(raw.get("text", "")).strip()}
        nxt = str(raw.get("next", "") or "").strip()
        if nxt:
            choice["next"] = nxt
        required = str(raw.get("requires_item", "") or "").strip()
        if required:
            choice["requires_item"] = required
        cond = self._clean_condition(raw.get("if"))
        if cond:
            choice["if"] = cond
        effects = []
        for raw_eff in raw.get("effects", []) or []:
            if isinstance(raw_eff, dict) and str(raw_eff.get("type", "")).strip():
                effects.append(self._clean_effect(raw_eff))
        if effects:
            choice["effects"] = effects
        return choice

    def _clean_condition(self, raw: Any) -> Optional[Dict[str, Any]]:
        """把前端提交的条件整理成单键规范条件；字典里没有的类型/空条件返回 None。

        条件的判别键 = 字典条目的 key，且必须与某个 params 字段同名。
        """
        if not isinstance(raw, dict) or not raw:
            return None
        for entry in self.load_events()["conditions"]:
            if entry["key"] in raw:
                return self._clean_entry_params(
                    event_dictionary.params_of(entry), raw) or None
        return None

    def _clean_effect(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """按事件字典的效果参数表整理字段（未知类型保留 type 交给校验报错）。"""
        etype = str(raw.get("type", "")).strip()
        entry = event_dictionary.by_key(self.load_events()["effects"]).get(etype)
        eff: Dict[str, Any] = {"type": etype}
        if entry is not None:
            eff.update(self._clean_entry_params(
                event_dictionary.params_of(entry), raw))
        return eff

    # ---------- 内部：角色校验 ----------
    def _validate_character(self, char: Dict[str, Any], characters: Dict[str, Any],
                            scenes: Dict[str, Any], items: Dict[str, Any],
                            tags: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []

        if not ID_PATTERN.match(char["id"]):
            errors.append("角色 ID 只能用小写字母/数字/下划线，长度 1~32（例：forest_wolf）")
        if not char["name"]:
            errors.append("名称不能为空")
        elif len(char["name"]) > NAME_MAX:
            errors.append(f"名称不能超过 {NAME_MAX} 个字")
        if not char["description"]:
            errors.append("描述不能为空")
        elif len(char["description"]) > DESC_MAX:
            errors.append(f"描述不能超过 {DESC_MAX} 个字")

        applied = char.get("tags", [])
        for tid in applied:
            spec = tags.get(tid)
            if not isinstance(spec, dict):
                errors.append(f"标签不存在：{tid}（请先在标签页新建）")
                continue
            target = spec.get("target", "item")
            if target != "character":
                errors.append(f"标签【{spec.get('label') or tid}】只能贴在"
                              f"{'物品' if target == 'item' else '地点'}上，不能用于角色")
                continue
            errors += self._field_errors(spec, char, items,
                                         prefix=f"标签【{spec.get('label') or tid}】")

        # 运行时视图：敌人 / NPC（对话条件与效果里的引用按它们校验）
        enemies = {cid: c for cid, c in characters.items()
                   if "enemy" in (c.get("tags") or [])}
        npcs = {cid: c for cid, c in characters.items()
                if "talkable" in (c.get("tags") or [])}
        if "enemy" in applied:
            errors += self._beat_field_errors(char)
        if "talkable" in applied:
            d_err, d_warn = self._dialogue_errors(char, scenes, items, enemies, npcs)
            errors += d_err
            warnings += d_warn
        return errors, warnings

    @staticmethod
    def _beat_field_errors(char: Dict[str, Any]) -> List[str]:
        """Beat 战斗结构字段校验（动作名以引擎 INTENT_TEXT 为准）。"""
        errors: List[str] = []
        if "heavy_attack" in char:
            if not isinstance(char["heavy_attack"], int) or char["heavy_attack"] < 0:
                errors.append("重击伤害必须是非负整数")
        brain = char.get("brain")
        if brain:
            for key in ("rhythm", "frenzy_rhythm"):
                for step in brain.get(key, []):
                    if step not in INTENT_TEXT:
                        errors.append(
                            f"节奏环 {key} 里有未知动作：{step}"
                            f"（可用：{'/'.join(INTENT_TEXT)}）")
            mode = brain.get("low_hp_mode")
            if mode and mode not in ("brace", "frenzy"):
                errors.append("残血行为只能是 brace（龟息）或 frenzy（狂暴）")
            ratio = brain.get("low_hp_ratio")
            if ratio is not None and not (isinstance(ratio, (int, float)) and 0 < ratio <= 1):
                errors.append("残血阈值必须是 0~1 的小数（如 0.25）")
        for field in ("telegraphs", "intents"):
            for intent, val in (char.get(field) or {}).items():
                if intent not in INTENT_TEXT:
                    errors.append(
                        f"{field} 里有未知动作：{intent}（可用：{'/'.join(INTENT_TEXT)}）")
                if field == "telegraphs":
                    for p in val:
                        if len(p) > DESC_MAX:
                            errors.append(f"征兆文案不能超过 {DESC_MAX} 个字")
                else:
                    if len(val[0]) > NAME_MAX:
                        errors.append(f"意图名称不能超过 {NAME_MAX} 个字")
                    if len(val[1]) > DESC_MAX:
                        errors.append(f"意图提示不能超过 {DESC_MAX} 个字")
        return errors

    def _dialogue_errors(self, char: Dict[str, Any], scenes: Dict[str, Any],
                         items: Dict[str, Any], enemies: Dict[str, Any],
                         npcs: Dict[str, Any]
                         ) -> Tuple[List[str], List[str]]:
        """可对话角色（talkable）的对话树校验。"""
        errors: List[str] = []
        warnings: List[str] = []
        ctx = self._ref_ctx(scenes, items, enemies, npcs)

        if not char.get("scene_id"):
            errors.append("可对话角色必须选择所在地点")
        elif char["scene_id"] not in scenes:
            errors.append(f"所在地点不存在：{char['scene_id']}（请先在地点页新建）")

        nodes = char.get("nodes") or {}
        if not nodes:
            errors.append("可对话角色至少需要一个对话节点（默认起点 greet）")
        for node_id in nodes:
            if not ID_PATTERN.match(node_id):
                errors.append(f"节点 ID 非法：{node_id}（小写字母/数字/下划线）")

        if char.get("greeting") not in nodes:
            errors.append(f"起始节点不存在：{char.get('greeting')}")

        # 问候规则
        for i, rule in enumerate(char.get("greeting_rules") or []):
            if rule["node"] not in nodes:
                errors.append(f"问候规则第 {i + 1} 条指向的节点不存在：{rule['node']}")
            if "if" in rule:
                errors += self._condition_errors(rule["if"], ctx,
                                                 prefix=f"问候规则第 {i + 1} 条")

        # 节点与选项
        for node_id, node in nodes.items():
            if not node["text"]:
                errors.append(f"节点【{node_id}】的对话文本不能为空")
            elif len(node["text"]) > DESC_MAX:
                errors.append(f"节点【{node_id}】文本不能超过 {DESC_MAX} 个字")
            for j, choice in enumerate(node["choices"]):
                where = f"节点【{node_id}】第 {j + 1} 个选项"
                if not choice["text"]:
                    errors.append(f"{where}的文本不能为空")
                if "next" in choice and choice["next"] not in nodes:
                    errors.append(f"{where}指向的节点不存在：{choice['next']}")
                if "requires_item" in choice and choice["requires_item"] not in items:
                    errors.append(f"{where}需要的物品不存在：{choice['requires_item']}")
                if "if" in choice:
                    errors += self._condition_errors(choice["if"], ctx, prefix=where)
                for k, eff in enumerate(choice.get("effects", [])):
                    errors += self._effect_errors(
                        eff, ctx, prefix=f"{where}第 {k + 1} 个效果")

        # 不可达节点警告（从起始节点 + 问候规则起点 BFS）
        if not errors:
            entries = {char.get("greeting")} | {r["node"] for r in char.get("greeting_rules") or []}
            seen: Set[str] = set()
            stack = list(entries)
            while stack:
                cur = stack.pop()
                if cur in seen or cur not in nodes:
                    continue
                seen.add(cur)
                for choice in nodes[cur]["choices"]:
                    if choice.get("next"):
                        stack.append(choice["next"])
            unreachable = [nid for nid in nodes if nid not in seen]
            if unreachable:
                warnings.append("以下节点从任何入口都走不到，玩家永远看不到："
                                + "、".join(unreachable))

        return errors, warnings

    def _condition_errors(self, cond: Dict[str, Any], ctx: Dict[str, Any],
                          prefix: str = "条件") -> List[str]:
        """校验单个条件 dict（假设已通过 _clean_condition 整理）；条目来自事件字典。"""
        for entry in self.load_events()["conditions"]:
            if entry["key"] not in cond:
                continue
            fields = {p["field"] for p in event_dictionary.params_of(entry)}
            if entry["key"] not in fields:
                name = entry.get("label") or entry["key"]
                return [f"{prefix}：条件条目【{name}】的 params 必须有名称为"
                        f"{entry['key']} 的参数（它就是条件字典的判别键）"]
            fatal = self._entry_atom_errors(entry, CONDITION_ATOMS, prefix, "条件")
            if fatal:
                return fatal
            return self._params_errors(entry, cond, ctx, prefix)
        return [f"{prefix}：未知条件类型"]

    def _effect_errors(self, eff: Dict[str, Any], ctx: Dict[str, Any],
                       prefix: str = "效果") -> List[str]:
        """校验单个效果 dict（假设已通过 _clean_effect 整理）；条目来自事件字典。"""
        key = eff.get("type")
        entry = event_dictionary.by_key(self.load_events()["effects"]).get(key)
        if entry is None:
            return [f"{prefix}：未知效果类型：{key}"]
        fatal = self._entry_atom_errors(entry, EFFECT_ATOM_FIELDS, prefix, "效果",
                                       EFFECT_ATOM_FIELDS)
        if fatal:
            return fatal
        return self._params_errors(entry, eff, ctx, prefix)

    # ---------- 内部：Beat 战斗文案清洗 ----------
    @staticmethod
    def _clean_intent_phrase_map(raw: Any) -> Dict[str, List[str]]:
        """telegraphs：{intent: [征兆文案, ...]}；未知键/空列表丢弃。"""
        out: Dict[str, List[str]] = {}
        if not isinstance(raw, dict):
            return out
        for k, v in raw.items():
            key = str(k).strip()
            phrases = [str(p).strip() for p in v if str(p).strip()] if isinstance(v, list) else []
            if key and phrases:
                out[key] = phrases
        return out

    @staticmethod
    def _clean_intent_label_map(raw: Any) -> Dict[str, List[str]]:
        """intents：{intent: [名称, 提示]}；名称为空的条目丢弃。"""
        out: Dict[str, List[str]] = {}
        if not isinstance(raw, dict):
            return out
        for k, v in raw.items():
            key = str(k).strip()
            if key and isinstance(v, (list, tuple)) and len(v) == 2:
                label, hint = str(v[0]).strip(), str(v[1]).strip()
                if label:
                    out[key] = [label, hint]
        return out

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        """安全转 int；空值用默认值，非法值返回 -1 交给校验报错"""
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

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
