"""
事件字典（引擎核心模块）

事件系统的"词汇表"完全由数据文件 game_data/events.json 决定：有哪些触发器
（规则的 on）、哪些条件（规则的 when / 对话选项的 if）、哪些效果（规则的 do），
以及各自的参数字段。引擎只提供少量"原子"（atom）——真正干活的几段小逻辑；
字典负责把原子包装成用户在编辑器里看得见、可自由增删的条目。

文件结构（三段都是条目列表）：
{
  "triggers":   [{"key": "ITEM_TAKEN", "label": "拾取物品时", "params": [...]}],
  "conditions": [{"key": "flag", "label": "需要标志", "atom": "flag", "params": [...]}],
  "effects":    [{"key": "heal", "label": "治疗生命", "atom": "heal", "params": [...]}]
}

条目字段：
  key    引擎/数据里实际存的值（条件的判别键、效果的 type、触发器的 on）
  label  编辑器显示名
  atom   引擎原子名；省略时等于 key（自定义条目可换 key 而复用已有 atom）
  params 参数字段列表，每项：
         {"field", "label", "type": "text|int|ref", "ref"?, "parent"?,
          "required"?, "min"?, "max"?, "placeholder"?}

参数类型 type=ref 时，ref 取值 item/scene/enemy/npc/node/exit，表示引用哪类对象；
parent 指向同组内另一个字段（node 依赖 npc_id、exit 依赖 scene）。

本模块只负责"读字典 + 查规格"，不含任何游戏内容。
"""
import json
import os
from typing import Any, Dict, List, Optional

SECTIONS = ("triggers", "conditions", "effects")


def normalize(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """把任意输入整理成 {"triggers": [...], "conditions": [...], "effects": [...]}。"""
    out: Dict[str, List[Dict[str, Any]]] = {s: [] for s in SECTIONS}
    if not isinstance(raw, dict):
        return out
    for section in SECTIONS:
        entries = raw.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("key", "")).strip():
                out[section].append(entry)
    return out


# 模块级 mtime 缓存：同一文件未改动就不重复读盘（编辑器校验会频繁取字典）
_CACHE: Dict[str, Any] = {}


def load(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """读取事件字典；文件缺失/损坏按空字典处理（引擎不会因此崩）。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return normalize({})
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        raw = {}
    data = normalize(raw)
    _CACHE[path] = (mtime, data)
    return data


def by_key(entries: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """条目列表 → {key: entry}（key 重复时后者覆盖）。"""
    return {e["key"]: e for e in entries or [] if e.get("key")}


def atom_of(entry: Dict[str, Any]) -> str:
    """条目的引擎原子名（缺省等于 key）。"""
    return str(entry.get("atom") or entry.get("key") or "")


def atom_map(entries: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """{条目 key: 引擎原子名}——运行时按键分发用。"""
    return {e["key"]: atom_of(e) for e in entries or [] if e.get("key")}


def params_of(entry: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """条目的参数列表（非 dict 项丢弃）。"""
    if not isinstance(entry, dict):
        return []
    params = entry.get("params")
    if not isinstance(params, list):
        return []
    return [p for p in params if isinstance(p, dict) and str(p.get("field", "")).strip()]
