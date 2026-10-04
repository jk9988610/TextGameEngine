# -*- coding: utf-8 -*-
"""离线校验一份游戏工程数据（5 个 JSON）是否自洽。

用法：
    python tools/validate_game_data.py [工程目录]
    默认校验 games/demo_minimal/；也可指向任意含 5 个 JSON 的目录。

纯标准库、不启动服务器、不写任何文件。校验通过打印 OK 并退出 0；
发现问题逐条打印并以退出码 1 结束。供人/AI 填数据后快速自检。
"""
import json
import os
import re
import sys

ID_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
FILES = ["game_config.json", "scenes.json", "items.json",
         "enemies.json", "npc_dialogues.json"]
TRIGGER_COND_KEYS = {"item_id", "enemy_id", "scene_id", "npc_id",
                     "flag", "from_scene", "gold_gte"}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "games", "demo_minimal")
    errors, warns = [], []

    data = {}
    for fn in FILES:
        path = os.path.join(root, fn)
        if not os.path.exists(path):
            errors.append(f"缺少文件 {fn}")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data[fn[:-5]] = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"{fn} JSON 解析失败：{e}")
    if errors:
        return finish(errors, warns)

    cfg, scenes = data["game_config"], data["scenes"]
    items, enemies, npcs = data["items"], data["enemies"], data["npc_dialogues"]

    def err(msg): errors.append(msg)
    def warn(msg): warns.append(msg)

    # ---- ID 格式 ----
    for name, coll in (("场景", scenes), ("物品", items),
                       ("敌人", enemies), ("NPC", npcs)):
        for k, v in coll.items():
            if not ID_PATTERN.match(k):
                err(f"{name} ID 非法：{k}")
            if isinstance(v, dict) and v.get("id") != k:
                err(f"{name} {k} 的内部 id 字段与键不一致")

    # ---- 开局配置 ----
    if cfg.get("initial_scene") not in scenes:
        err(f"initial_scene 不存在：{cfg.get('initial_scene')}")
    for it in cfg.get("initial_inventory", []):
        if it not in items:
            err(f"初始背包物品不存在：{it}")
    pl = cfg.get("player", {})
    for k in ("hp", "attack", "defense"):
        if not isinstance(pl.get(k), int) or pl[k] < 0:
            err(f"player.{k} 必须是非负整数，实际 {pl.get(k)!r}")
    if pl.get("hp", 0) < 1:
        err("player.hp 至少为 1")

    # ---- 场景引用 ----
    for sid, sc in scenes.items():
        for ex in sc.get("exits", []):
            if ex not in scenes:
                err(f"场景 {sid} 的出口指向不存在场景：{ex}")
        for it in sc.get("items_here", []):
            if it not in items:
                err(f"场景 {sid} 地上物品不存在：{it}")
        for en in sc.get("enemies_here", []):
            if en not in enemies:
                err(f"场景 {sid} 的敌人不存在：{en}")
        for nid in sc.get("npcs_here", []):
            if nid not in npcs:
                err(f"场景 {sid} 的 NPC 不存在：{nid}")
        for sh in sc.get("shop_items", []):
            iid = sh.get("item_id")
            if iid not in items:
                err(f"场景 {sid} 商店货物不存在：{iid}")
            if not isinstance(sh.get("price"), int) or sh["price"] < 0:
                err(f"场景 {sid} 货物 {iid} 价格需为非负整数")
        for lk in (sc.get("locked_exits") or {}):
            if lk not in sc.get("exits", []):
                err(f"场景 {sid} 的锁门 {lk} 不在 exits 中")

    # ---- 敌人掉落 ----
    for eid, en in enemies.items():
        for it in en.get("reward_items", []):
            if it not in items:
                err(f"敌人 {eid} 掉落物品不存在：{it}")

    # ---- NPC / 对话树 ----
    cond_keys = {"flag", "has_item", "enemy_killed", "gold_gte"}
    for nid, npc in npcs.items():
        if npc.get("scene_id") not in scenes:
            err(f"NPC {nid} 的 scene_id 不存在：{npc.get('scene_id')}")
        nodes = npc.get("nodes", {})
        if npc.get("greeting") not in nodes:
            err(f"NPC {nid} 的默认入口节点不存在：{npc.get('greeting')}")
        for i, r in enumerate(npc.get("greeting_rules", [])):
            node, cond = r.get("node"), r.get("if")
            if node not in nodes:
                err(f"NPC {nid} 问候规则 {i} 的起点不存在：{node}")
            if cond:
                k = set(cond.keys()) & cond_keys
                if len(k) != 1:
                    err(f"NPC {nid} 问候规则 {i} 条件需恰好一种类型，实际 {sorted(cond.keys())}")
                else:
                    t = next(iter(k))
                    if t in ("has_item",) and cond[t] not in items:
                        err(f"NPC {nid} 规则 {i} 条件物品不存在：{cond[t]}")
                    if t == "enemy_killed" and cond[t] not in enemies:
                        err(f"NPC {nid} 规则 {i} 条件敌人不存在：{cond[t]}")
        for node_id, node in nodes.items():
            for ci, ch in enumerate(node.get("choices", [])):
                nxt = ch.get("next")
                if nxt and nxt not in nodes:
                    err(f"NPC {nid} 节点 {node_id} 选项 {ci} 指向不存在节点：{nxt}")
                c = ch.get("if")
                if c:
                    t = set(c.keys()) & cond_keys
                    if len(t) != 1:
                        err(f"NPC {nid} 节点 {node_id} 选项 {ci} 条件类型非法")
        # 可达性：默认入口 + 规则入口 BFS
        entries = [npc.get("greeting")] + [r.get("node") for r in npc.get("greeting_rules", [])]
        seen, queue = set(), [e for e in entries if e in nodes]
        while queue:
            cur = queue.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for ch in nodes.get(cur, {}).get("choices", []):
                if ch.get("next") and ch["next"] not in seen:
                    queue.append(ch["next"])
        for node_id in nodes:
            if node_id not in seen:
                warn(f"NPC {nid} 节点不可达（无任何入口能走到）：{node_id}")

    # ---- 事件规则（与 engine/editor_manager.py 的 TRIGGER_SPECS 对齐）----
    effects = {"set_flag", "give_item", "remove_item", "heal", "max_hp",
               "gold", "teleport", "start_combat", "unlock"}
    trigger_specs = {
        "ITEM_TAKEN": {"item_id": ("item", False), "from_scene": ("scene", True)},
        "ITEM_USED": {"item_id": ("item", False)},
        "ITEM_BOUGHT": {"item_id": ("item", False), "from_scene": ("scene", True)},
        "ENEMY_KILLED": {"enemy_id": ("enemy", False)},
        "SCENE_ENTER": {"scene_id": ("scene", False)},
        "SCENE_LEAVE": {"scene_id": ("scene", False)},
        "NPC_TALK": {"npc_id": ("npc", False), "node_id": ("node", True)},
    }
    for i, rule in enumerate(cfg.get("event_rules", [])):
        rid = rule.get("id", f"#{i}")
        if not ID_PATTERN.match(rid):
            err(f"事件规则 id 非法：{rid}")
        on = rule.get("on")
        spec = trigger_specs.get(on)
        if not spec:
            err(f"规则 {rid} 触发器未知：{on}")
        else:
            cond_if = rule.get("if") or {}
            for field, (kind, optional) in spec.items():
                val = cond_if.get(field)
                if not optional and not val:
                    err(f"规则 {rid} 缺少必填触发参数：{field}")
                if not val:
                    continue
                if kind == "item" and val not in items:
                    err(f"规则 {rid} 触发物品不存在：{val}")
                elif kind == "enemy" and val not in enemies:
                    err(f"规则 {rid} 触发敌人不存在：{val}")
                elif kind == "scene" and val not in scenes:
                    err(f"规则 {rid} 触发场景不存在：{val}")
                elif kind == "npc" and val not in npcs:
                    err(f"规则 {rid} 触发 NPC 不存在：{val}")
                elif kind == "node":
                    owner = cond_if.get("npc_id")
                    if owner and val not in npcs.get(owner, {}).get("nodes", {}):
                        err(f"规则 {rid} 触发节点 {val} 不属于 NPC {owner}")
        for j, d in enumerate(rule.get("do", [])):
            t = d.get("type")
            if t not in effects:
                err(f"规则 {rid} 效果 {j} 类型未知：{t}")
                continue
            if t in ("give_item", "remove_item") and d.get("item") not in items:
                err(f"规则 {rid} 效果 {j} 物品不存在：{d.get('item')}")
            if t == "start_combat" and d.get("enemy") not in enemies:
                err(f"规则 {rid} 效果 {j} 敌人不存在：{d.get('enemy')}")
            if t == "teleport" and d.get("scene") not in scenes:
                err(f"规则 {rid} 效果 {j} 场景不存在：{d.get('scene')}")
            if t == "unlock":
                sc, ex = d.get("scene"), d.get("exit")
                if sc not in scenes or ex not in scenes[sc].get("exits", []):
                    err(f"规则 {rid} 效果 {j} unlock 目标无效：{sc}->{ex}")

    # ---- 锁门必须有解锁途径（事件 unlock 或仅提示） ----
    for sid, sc in scenes.items():
        for lk in (sc.get("locked_exits") or {}):
            has_unlock = any(
                d.get("type") == "unlock" and d.get("scene") == sid and d.get("exit") == lk
                for rule in cfg.get("event_rules", []) for d in rule.get("do", []))
            if not has_unlock:
                warn(f"场景 {sid} 的锁门 {lk} 没有任何事件规则解锁，玩家将永远无法通过")

    return finish(errors, warns)


def finish(errors, warns):
    for w in warns:
        print(f"[警告] {w}")
    for e in errors:
        print(f"[错误] {e}")
    if errors:
        print(f"\n校验失败：{len(errors)} 个错误，{len(warns)} 个警告")
        return 1
    print(f"OK：数据自洽，{len(warns)} 个警告")
    return 0


if __name__ == "__main__":
    sys.exit(main())
