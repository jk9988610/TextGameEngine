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

# Beat 战斗意图名集合（敌人 brain/征兆/意图文案的合法动作键以此为校验依据）
from .beat_combat import INTENT_TEXT

ID_PATTERN = re.compile(r'^[a-z0-9_]{1,32}$')
NAME_MAX = 40
DESC_MAX = 2000
TITLE_MAX = 40
INTRO_MAX = 200

# 事件规则触发器规格：on → {事件参数字段: 引用类型}
# 引用类型：item/enemy/scene/nc 为必填，带 ? 为可选
TRIGGER_SPECS: Dict[str, Dict[str, str]] = {
    "ITEM_TAKEN": {"item_id": "item", "from_scene": "scene?"},
    "ITEM_USED": {"item_id": "item"},
    "ITEM_BOUGHT": {"item_id": "item", "from_scene": "scene?"},
    "ENEMY_KILLED": {"enemy_id": "enemy"},
    "SCENE_ENTER": {"scene_id": "scene"},
    "SCENE_LEAVE": {"scene_id": "scene"},
    "NPC_TALK": {"npc_id": "npc", "node_id": "node?"},
}


class ValidationError(Exception):
    """校验失败（消息直接展示给编辑器用户）"""


class EditorManager:
    """地点/物品数据的读写与校验"""

    def __init__(self, data_dir: str = "game_data"):
        self._dir = data_dir
        self._file_map = {
            "scenes": "scenes.json",
            "items": "items.json",
            "enemies": "enemies.json",
            "npcs": "npc_dialogues.json",
            "config": "game_config.json",
            "layouts": "npc_layouts.json",   # 画布手动布局坐标（非游戏内容）
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
            return {"success": True, "message": f"地点【{name}】已删除"}

    # ---------- 物品 upsert ----------
    def upsert_item(self, scenes: Dict[str, Any], items: Dict[str, Any],
                    enemies: Dict[str, Any], config: Dict[str, Any],
                    payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建或更新一个物品"""
        with self._lock:
            cleaned, is_new, iid = self._clean_item(payload, items)
            errors = self._validate_item(cleaned, items, is_new)
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

    # ---------- 敌人 upsert ----------
    def upsert_enemy(self, items: Dict[str, Any], enemies: Dict[str, Any],
                     payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建或更新一个敌人"""
        with self._lock:
            cleaned, is_new, eid = self._clean_enemy(payload, enemies)
            errors = self._validate_enemy(cleaned, items, is_new)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            enemies[eid] = cleaned
            self._write_json("enemies", enemies)
            msg = f"敌人【{cleaned['name']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "id": eid}

    def delete_enemy(self, scenes: Dict[str, Any], enemies: Dict[str, Any],
                     enemy_id: str,
                     live_battle_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
        """删除一个敌人（被场景放置 / 有玩家正在与之战斗时拒绝）"""
        with self._lock:
            if enemy_id not in enemies:
                return {"success": False, "message": f"敌人不存在：{enemy_id}"}
            if enemy_id in (live_battle_ids or set()):
                return {"success": False,
                        "message": "当前有玩家正在与这个敌人战斗，不能删除"}
            refs = [sid for sid, s in scenes.items()
                    if enemy_id in s.get("enemies_here", [])]
            if refs:
                return {"success": False,
                        "message": f"被以下地点作为出没敌人使用中，不能删除："
                                   f"{', '.join(sorted(refs))}。请先移除。"}
            name = enemies[enemy_id].get("name", enemy_id)
            del enemies[enemy_id]
            self._write_json("enemies", enemies)
            return {"success": True, "message": f"敌人【{name}】已删除"}

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

        is_usable = bool(payload.get("usable", False))
        if is_usable:
            try:
                heal = int(payload.get("heal", 0))
            except (TypeError, ValueError):
                heal = -1
            cleaned["usable"] = True
            cleaned["heal"] = heal

        is_currency = bool(payload.get("is_currency", False))
        if is_currency:
            try:
                currency_value = int(payload.get("currency_value", 1))
            except (TypeError, ValueError):
                currency_value = -1
            cleaned["currency_value"] = currency_value
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
        if item.get("usable"):
            if not isinstance(item.get("heal"), int) or item["heal"] < 0:
                errors.append("回复生命值必须是非负整数")
        if "currency_value" in item:
            if not isinstance(item["currency_value"], int) or item["currency_value"] <= 0:
                errors.append("货币面值必须是正整数")
        return errors

    # ---------- NPC upsert / delete ----------
    def upsert_npc(self, npcs: Dict[str, Any], scenes: Dict[str, Any],
                   items: Dict[str, Any], enemies: Dict[str, Any],
                   payload: Dict[str, Any]) -> Dict[str, Any]:
        """新建/更新一个 NPC（属性 + 问候规则 + 对话节点树）"""
        with self._lock:
            cleaned, is_new, nid = self._clean_npc(payload, npcs)
            errors, warnings = self._validate_npc(
                cleaned, scenes, items, enemies)
            if errors:
                return {"success": False, "message": "；".join(errors)}

            npcs[nid] = cleaned
            self._write_json("npcs", npcs)
            msg = f"NPC【{cleaned['name']}】已{'新建' if is_new else '保存'}"
            return {"success": True, "message": msg, "warnings": warnings, "id": nid}

    def delete_npc(self, npcs: Dict[str, Any], npc_id: str,
                   live_dialogue_ids: Optional[Set[str]] = None) -> Dict[str, Any]:
        """删除一个 NPC（有玩家正在与其对话时拒绝）"""
        with self._lock:
            if npc_id not in npcs:
                return {"success": False, "message": f"NPC 不存在：{npc_id}"}
            if npc_id in (live_dialogue_ids or set()):
                return {"success": False,
                        "message": "当前有玩家正在和这个 NPC 对话，不能删除"}
            name = npcs[npc_id].get("name", npc_id)
            del npcs[npc_id]
            self._write_json("npcs", npcs)
            return {"success": True, "message": f"NPC【{name}】已删除"}

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

    # ---------- NPC 画布布局（坐标；非游戏内容，独立于 npc_dialogues.json） ----------
    LAYOUT_RANGE = (-10000, 10000)

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
                # 删除该 NPC 已不存在节点的坐标
                valid_ids = set((npcs[npc_id] or {}).get("nodes", {}).keys())
                kept = {nid: p for nid, p in layouts[npc_id].items() if nid in valid_ids}
                if len(kept) != len(layouts[npc_id]):
                    layouts[npc_id] = kept; changed = True
            if changed:
                self._write_json("layouts", layouts)

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

    # ---------- 内部：事件规则清洗/校验 ----------
    def _clean_event_rule(self, payload: Dict[str, Any]
                          ) -> Tuple[Dict[str, Any], bool, str]:
        rid = str(payload.get("id", "")).strip()
        is_new = rid not in {r.get("id") for r in
                             self.load("config").get("event_rules", [])}
        on = str(payload.get("on", "")).strip()

        # 事件参数过滤 if：只保留该触发器规格里允许的字段
        event_args: Dict[str, Any] = {}
        spec = TRIGGER_SPECS.get(on, {})
        raw_if = payload.get("if") or {}
        if isinstance(raw_if, dict):
            for field in spec:
                if field in raw_if:
                    val = str(raw_if.get(field, "")).strip()
                    if val:
                        event_args[field] = val

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

        on = rule.get("on", "")
        spec = TRIGGER_SPECS.get(on)
        if not spec:
            errors.append(f"不支持的触发器：{on or '（未选择）'}")
            return errors  # 后续引用校验都依赖 on，直接返回

        # 必填的事件参数
        for field, ref in spec.items():
            kind = ref[:-1] if ref.endswith("?") else ref
            optional = ref.endswith("?")
            val = rule.get("if", {}).get(field)
            if not optional and not val:
                errors.append(self._trigger_field_label(on, field) + "为必填项")
                continue
            if not val:
                continue
            if kind == "node":
                # NPC_TALK 的节点必须属于该规则选中的 NPC
                npc_id = rule.get("if", {}).get("npc_id", "")
                npc = npcs.get(npc_id)
                if npc and val not in npc.get("nodes", {}):
                    errors.append(f"触发节点【{val}】不属于 NPC【{npc_id}】")
                elif not npc:
                    errors.append("请先选择触发 NPC，再指定触发节点")
            else:
                errors += self._check_trigger_ref(kind, val, scenes, items,
                                                  enemies, npcs, on, field)

        # 世界状态条件
        if "when" in rule:
            errors += self._condition_errors(rule["when"], items, enemies, prefix="附加条件")

        # 至少一个效果，且逐个校验
        if not rule.get("do"):
            errors.append("至少需要配置一个效果")
        for i, eff in enumerate(rule.get("do", [])):
            errors += self._effect_errors(
                eff, scenes, items, enemies, prefix=f"第 {i + 1} 个效果")
        return errors

    @staticmethod
    def _trigger_field_label(on: str, field: str) -> str:
        labels = {
            "item_id": "触发物品", "from_scene": "触发地点",
            "enemy_id": "触发敌人", "scene_id": "触发地点",
            "npc_id": "触发 NPC", "node_id": "触发节点",
        }
        return labels.get(field, field)

    def _check_trigger_ref(self, kind: str, val: str, scenes: Dict[str, Any],
                           items: Dict[str, Any], enemies: Dict[str, Any],
                           npcs: Dict[str, Any], on: str, field: str) -> List[str]:
        """校验触发器参数引用是否存在（node 类型还要属于该 NPC 的节点）。"""
        label = self._trigger_field_label(on, field)
        if kind == "item":
            return [] if val in items else [f"{label}不存在：{val}"]
        if kind == "enemy":
            return [] if val in enemies else [f"{label}不存在：{val}"]
        if kind == "scene":
            return [] if val in scenes else [f"{label}不存在：{val}"]
        if kind == "npc":
            return [] if val in npcs else [f"{label}不存在：{val}"]
        return []

    # ---------- 内部：NPC 清洗 ----------
    def _clean_npc(self, payload: Dict[str, Any],
                   npcs: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        nid = str(payload.get("id", "")).strip()
        is_new = nid not in npcs

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

        cleaned = {
            "id": nid,
            "name": str(payload.get("name", "")).strip(),
            "scene_id": str(payload.get("scene_id", "")).strip(),
            "greeting": str(payload.get("greeting", "") or "greet").strip() or "greet",
            "greeting_rules": rules,
            "nodes": nodes,
        }
        return cleaned, is_new, nid

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
        """把前端提交的条件整理成单键规范条件；空条件返回 None。"""
        if not isinstance(raw, dict) or not raw:
            return None
        if "flag" in raw:
            name = str(raw.get("flag", "")).strip()
            if not name:
                return None
            cond = {"flag": name}
            if "value" in raw:
                cond["value"] = raw.get("value")
            return cond
        if "has_item" in raw:
            return {"has_item": str(raw.get("has_item", "")).strip()}
        if "enemy_killed" in raw:
            return {"enemy_killed": str(raw.get("enemy_killed", "")).strip()}
        if "gold_gte" in raw:
            return {"gold_gte": self._to_int(raw.get("gold_gte"), -1)}
        return None

    def _clean_effect(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """按效果类型白名单整理字段（未知类型保留 type 交给校验报错）。"""
        etype = str(raw.get("type", "")).strip()
        eff = {"type": etype}
        if etype == "set_flag":
            eff["flag"] = str(raw.get("flag", "")).strip()
            if "value" in raw:
                eff["value"] = raw.get("value")
        elif etype in ("give_item", "remove_item"):
            eff["item"] = str(raw.get("item", "")).strip()
        elif etype in ("heal", "max_hp", "gold"):
            eff["amount"] = self._to_int(raw.get("amount"), -1)
        elif etype == "teleport":
            eff["scene"] = str(raw.get("scene", "")).strip()
        elif etype == "start_combat":
            eff["enemy"] = str(raw.get("enemy", "")).strip()
        elif etype == "unlock":
            eff["scene"] = str(raw.get("scene", "")).strip()
            eff["exit"] = str(raw.get("exit", "")).strip()
        return eff

    # ---------- 内部：NPC 校验 ----------
    def _validate_npc(self, npc: Dict[str, Any], scenes: Dict[str, Any],
                      items: Dict[str, Any], enemies: Dict[str, Any]
                      ) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []

        if not ID_PATTERN.match(npc["id"]):
            errors.append("NPC ID 只能用小写字母/数字/下划线，长度 1~32（例：npc_merchant）")
        if not npc["name"]:
            errors.append("名称不能为空")
        elif len(npc["name"]) > NAME_MAX:
            errors.append(f"名称不能超过 {NAME_MAX} 个字")
        if not npc["scene_id"]:
            errors.append("必须选择所在地点")
        elif npc["scene_id"] not in scenes:
            errors.append(f"所在地点不存在：{npc['scene_id']}（请先在地点页新建）")

        nodes = npc["nodes"]
        if not nodes:
            errors.append("至少需要一个对话节点（默认起点 greet）")
        for node_id in nodes:
            if not ID_PATTERN.match(node_id):
                errors.append(f"节点 ID 非法：{node_id}（小写字母/数字/下划线）")

        if npc["greeting"] not in nodes:
            errors.append(f"起始节点不存在：{npc['greeting']}")

        # 问候规则
        for i, rule in enumerate(npc["greeting_rules"]):
            if rule["node"] not in nodes:
                errors.append(f"问候规则第 {i + 1} 条指向的节点不存在：{rule['node']}")
            if "if" in rule:
                errors += self._condition_errors(rule["if"], items, enemies,
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
                    errors += self._condition_errors(
                        choice["if"], items, enemies, prefix=where)
                for k, eff in enumerate(choice.get("effects", [])):
                    errors += self._effect_errors(
                        eff, scenes, items, enemies, prefix=f"{where}第 {k + 1} 个效果")

        # 不可达节点警告（从起始节点 + 问候规则起点 BFS）
        if not errors:
            entries = {npc["greeting"]} | {r["node"] for r in npc["greeting_rules"]}
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

    def _condition_errors(self, cond: Dict[str, Any], items: Dict[str, Any],
                          enemies: Dict[str, Any], prefix: str = "条件") -> List[str]:
        """校验单个条件 dict（假设已通过 _clean_condition 整理）。"""
        if "flag" in cond:
            if not cond["flag"]:
                return [f"{prefix}：标志名不能为空"]
            return []
        if "has_item" in cond:
            if cond["has_item"] not in items:
                return [f"{prefix}：物品不存在：{cond['has_item']}"]
            return []
        if "enemy_killed" in cond:
            if cond["enemy_killed"] not in enemies:
                return [f"{prefix}：敌人不存在：{cond['enemy_killed']}"]
            return []
        if "gold_gte" in cond:
            if not isinstance(cond["gold_gte"], int) or cond["gold_gte"] < 0:
                return [f"{prefix}：金币数量必须是非负整数"]
            return []
        return [f"{prefix}：未知条件类型"]

    def _effect_errors(self, eff: Dict[str, Any], scenes: Dict[str, Any],
                       items: Dict[str, Any], enemies: Dict[str, Any],
                       prefix: str = "效果") -> List[str]:
        """校验单个效果 dict（假设已通过 _clean_effect 整理）。"""
        t = eff.get("type")
        if t == "set_flag":
            return [] if eff.get("flag") else [f"{prefix}（设置标志）：标志名不能为空"]
        if t in ("give_item", "remove_item"):
            iid = eff.get("item", "")
            return [] if iid in items else [f"{prefix}：物品不存在：{iid}"]
        if t == "heal":
            ok = isinstance(eff.get("amount"), int) and 1 <= eff["amount"] <= 999999
            return [] if ok else [f"{prefix}（治疗）：数值必须是 1~999999 的整数"]
        if t == "max_hp":
            ok = isinstance(eff.get("amount"), int) and 1 <= eff["amount"] <= 999
            return [] if ok else [f"{prefix}（生命上限）：数值必须是 1~999 的整数"]
        if t == "gold":
            ok = isinstance(eff.get("amount"), int) and eff["amount"] >= 0
            return [] if ok else [f"{prefix}（金币）：数值必须是非负整数"]
        if t == "teleport":
            sid = eff.get("scene", "")
            return [] if sid in scenes else [f"{prefix}：目标地点不存在：{sid}"]
        if t == "start_combat":
            eid = eff.get("enemy", "")
            return [] if eid in enemies else [f"{prefix}：敌人不存在：{eid}"]
        if t == "unlock":
            sid = eff.get("scene", "")
            exit_id = eff.get("exit", "")
            if sid not in scenes:
                return [f"{prefix}：锁定出口所在地点不存在：{sid}"]
            if not exit_id:
                return [f"{prefix}：必须指定要解锁的出口"]
            if exit_id not in scenes[sid].get("exits", []):
                return [f"{prefix}：地点【{scenes[sid].get('name', sid)}】没有出口：{exit_id}"]
            return []
        return [f"{prefix}：未知效果类型：{t}"]

    # ---------- 内部：敌人清洗/校验 ----------
    def _clean_enemy(self, payload: Dict[str, Any],
                     enemies: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, str]:
        eid = str(payload.get("id", "")).strip()
        is_new = eid not in enemies
        cleaned: Dict[str, Any] = {
            "id": eid,
            "name": str(payload.get("name", "")).strip(),
            "hp": self._to_int(payload.get("hp"), 30),
            "attack": self._to_int(payload.get("attack"), 5),
            "defense": self._to_int(payload.get("defense"), 0),
            "description": str(payload.get("description", "")).strip(),
            "reward_items": self._dedupe_strs(payload.get("reward_items", [])),
            "reward_gold": self._to_int(payload.get("reward_gold"), 0),
        }
        # Beat 战斗可选字段：重击伤害 + 决策器参数（brain）+ 征兆/意图文案
        heavy = payload.get("heavy_attack")
        if heavy not in (None, ""):
            cleaned["heavy_attack"] = self._to_int(heavy, -1)
        brain_raw = payload.get("brain")
        if isinstance(brain_raw, dict):
            brain: Dict[str, Any] = {}
            for key in ("rhythm", "frenzy_rhythm"):
                seq = self._dedupe_strs(brain_raw.get(key, []))
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
        tg = self._clean_intent_phrase_map(payload.get("telegraphs"))
        if tg:
            cleaned["telegraphs"] = tg
        it = self._clean_intent_label_map(payload.get("intents"))
        if it:
            cleaned["intents"] = it
        return cleaned, is_new, eid

    def _validate_enemy(self, enemy: Dict[str, Any], items: Dict[str, Any],
                        is_new: bool) -> List[str]:
        errors: List[str] = []
        if not ID_PATTERN.match(enemy["id"]):
            errors.append("敌人 ID 只能用小写字母/数字/下划线，长度 1~32（例：cave_goblin）")
        if not enemy["name"]:
            errors.append("名称不能为空")
        elif len(enemy["name"]) > NAME_MAX:
            errors.append(f"名称不能超过 {NAME_MAX} 个字")
        if not enemy["description"]:
            errors.append("描述不能为空")
        elif len(enemy["description"]) > DESC_MAX:
            errors.append(f"描述不能超过 {DESC_MAX} 个字")
        if not isinstance(enemy["hp"], int) or not (1 <= enemy["hp"] <= 999999):
            errors.append("生命值必须是 1~999999 的整数")
        if not isinstance(enemy["attack"], int) or not (0 <= enemy["attack"] <= 999999):
            errors.append("攻击力必须是非负整数")
        if not isinstance(enemy["defense"], int) or not (0 <= enemy["defense"] <= 999999):
            errors.append("防御力必须是非负整数")
        if not isinstance(enemy["reward_gold"], int) or enemy["reward_gold"] < 0:
            errors.append("金币掉落必须是非负整数")
        for iid in enemy["reward_items"]:
            if iid not in items:
                errors.append(f"掉落物品不存在：{iid}（请先在物品页新建）")
        # ---- Beat 战斗字段（可选；配置了才校验，动作名以引擎 INTENT_TEXT 为准）----
        if "heavy_attack" in enemy:
            if not isinstance(enemy["heavy_attack"], int) or enemy["heavy_attack"] < 0:
                errors.append("重击伤害必须是非负整数")
        brain = enemy.get("brain")
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
            for intent, val in (enemy.get(field) or {}).items():
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
