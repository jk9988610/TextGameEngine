# -*- coding: utf-8 -*-
"""
QA 共享模块 —— 文字游戏引擎 HTTP 黑盒测试工具（仅 Python 标准库）。

设计目标：把每个里程碑 QA 脚本里重复的部分沉淀成可复用的一层：
  - GameClient：自动拼 client_id/mode、UTF-8 JSON 请求；封装游戏/战斗/编辑器动词
  - QaRunner：PASS/FAIL 断言计数与小节标题
  - editor_snapshot/editor_restore：编辑器数据自动备份与还原（含新增实体的清理）

约定：
  - 测试 client_id 一律 qa_ 前缀，套件结束自动删自己的存档；run_all 最后再扫尾
  - 编辑器数据（地点/物品/敌人）改动必须放在 try/finally 中还原
  - 服务地址默认 http://127.0.0.1:5000，可用环境变量 TGE_BASE 覆盖
    （例如对部署后的服务器跑冒烟测试：TGE_BASE=https://xxx python -m qa.run_all）
"""
import copy
import glob
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.environ.get("TGE_DB", os.path.join(_REPO_ROOT, "game_data", "offline_saves.db"))


# ============================================================
# 断言统计
# ============================================================
class QaRunner:
    """极简断言器：check 一条记一条，最后汇总 PASS/FAIL。"""

    def __init__(self, title: str):
        self.title = title
        self.passed = 0
        self.failed = 0

    def section(self, name: str) -> None:
        print(f"\n==== {name} ====")

    def check(self, name: str, cond, detail="") -> bool:
        if cond:
            self.passed += 1
            print(f"PASS  {name}")
            return True
        self.failed += 1
        print(f"FAIL  {name} :: {detail}")
        return False

    def report(self) -> str:
        total = self.passed + self.failed
        return f"{self.title}: {self.passed}/{total} passed" + (
            f", {self.failed} FAILED" if self.failed else "")


# ============================================================
# HTTP 客户端
# ============================================================
class GameClient:
    """一个隔离玩家：固定 client_id，请求自动带 client_id/mode（与前端拦截器一致）。"""

    def __init__(self, client_id: str = "qa_case", mode: str = "offline",
                 base: str = DEFAULT_BASE):
        self.client_id = client_id
        self.mode = mode
        self.base = base.rstrip("/")

    # ---------- 底层请求 ----------
    def request(self, method: str, path: str, body=None, with_client: bool = True):
        """返回 (http_status, json_obj)。UTF-8 编码，杜绝 PS 时代的中文乱码。"""
        url = self.base + path
        if with_client:
            sep = "&" if "?" in url else "?"
            url += (f"{sep}client_id={urllib.parse.quote(self.client_id)}"
                    f"&mode={self.mode}")
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"success": False, "message": raw}

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def delete(self, path: str, body=None, **kw):
        return self.request("DELETE", path, body, **kw)

    # ---------- 控制台 / 状态 ----------
    def console(self, command: str) -> str:
        _, d = self.post("/api/console", {"command": command})
        return d.get("output", "")

    def reset(self) -> str:
        return self.console("reset")

    def teleport(self, scene_id: str) -> str:
        return self.console(f"teleport {scene_id}")

    def give(self, item_id: str) -> str:
        return self.console(f"add_item {item_id}")

    def set_flag(self, name: str, value=None) -> str:
        return self.console(f"set_flag {name}" + (f" {value}" if value is not None else ""))

    def state(self) -> dict:
        _, d = self.get("/api/state")
        return d

    def action(self, atype: str, target=None, **extra) -> dict:
        body = {"type": atype}
        if target is not None:
            body["target"] = target
        body.update(extra)
        _, d = self.post("/api/action", body)
        return d

    def take(self, item_id: str) -> dict:
        return self.action("take_item", item_id)

    def move(self, scene_id: str) -> dict:
        return self.action("move_scene", scene_id)

    def buy(self, item_id: str) -> dict:
        return self.action("buy_item", item_id)

    def use_item(self, item_id: str) -> dict:
        return self.action("use_item", item_id)

    # ---------- 战斗 ----------
    def combat(self) -> dict:
        _, d = self.get("/api/combat")
        return d

    def combat_start(self, enemy_id: str) -> dict:
        _, d = self.post("/api/combat/start", {"enemy_id": enemy_id})
        return d

    def attack(self) -> dict:
        _, d = self.post("/api/combat/attack", {})
        return d

    def drink(self, item_id: str) -> dict:
        _, d = self.post("/api/combat/use-item", {"item_id": item_id})
        return d

    def attack_until(self, enemy_id: str, max_rounds: int = 10) -> dict:
        """连续攻击直到击杀（返回击杀那轮的响应）或超过回合上限。"""
        last = {}
        for _ in range(max_rounds):
            last = self.attack()
            if last.get("defeated") or last.get("player_dead"):
                return last
        return last

    # ---------- 对话 ----------
    def dialogue_start(self, npc_id: str) -> dict:
        _, d = self.post("/api/dialogue/start", {"npc_id": npc_id})
        return d

    def dialogue(self) -> dict:
        _, d = self.get("/api/dialogue")
        return d

    def dialogue_choose(self, index: int) -> dict:
        _, d = self.post("/api/dialogue", {"choice_index": index})
        return d

    # ---------- 存档 ----------
    def saves(self) -> dict:
        _, d = self.get("/api/saves")
        return d

    def save_slot(self, slot_name: str) -> dict:
        _, d = self.post("/api/save", {"slot_name": slot_name})
        return d

    def quick_save(self) -> dict:
        _, d = self.post("/api/save/quick", {})
        return d

    def load_slot(self, slot_name: str) -> dict:
        _, d = self.post("/api/load", {"slot_name": slot_name})
        return d

    def delete_slot(self, slot_name: str) -> dict:
        _, d = self.delete("/api/save", {"slot_name": slot_name})
        return d

    # ---------- 编辑器 ----------
    def editor_data(self) -> dict:
        _, d = self.get("/api/editor/data")
        return d

    def editor_save(self, kind: str, payload: dict) -> dict:
        """kind: 'scene' | 'item' | 'enemy'。"""
        payload = copy.deepcopy(payload)
        if kind == "item" and "currency_value" in payload:
            # 前端靠 is_currency 复选框决定是否保留 currency_value，
            # 直接回放数据文件时必须补上，否则还原会把货币字段洗掉
            payload.setdefault("is_currency", True)
        _, d = self.post(f"/api/editor/{kind}", payload)
        return d

    def editor_delete(self, kind: str, entity_id: str) -> dict:
        _, d = self.delete(f"/api/editor/{kind}/{urllib.parse.quote(entity_id)}")
        return d

    # ---------- 开局配置 / 事件规则 ----------
    def game_info(self) -> dict:
        _, d = self.get("/api/game-info", with_client=False)
        return d

    def config_save(self, payload: dict) -> dict:
        _, d = self.post("/api/editor/config", payload)
        return d

    def event_rule_save(self, payload: dict) -> dict:
        _, d = self.post("/api/editor/event-rule", payload)
        return d

    def event_rule_delete(self, rule_id: str) -> dict:
        return self.editor_delete("event-rule", rule_id)

    # ---------- NPC 画布布局（坐标，独立通道） ----------
    def layout_all(self) -> dict:
        return self.editor_data().get("layouts") or {}
    def layout_get(self, npc_id: str) -> dict:
        return self.layout_all().get(npc_id, {})
    def layout_save(self, npc_id: str, layout: dict) -> dict:
        _, d = self.post(
            f"/api/editor/npc-layout/{urllib.parse.quote(npc_id)}", layout)
        return d

    # ---------- 清理 ----------
    def cleanup_saves(self, db_path: str = DEFAULT_DB) -> int:
        """删掉本 client 在 SQLite 里留下的所有存档槽，返回删除行数。"""
        if not os.path.exists(db_path):
            return 0
        con = sqlite3.connect(db_path)
        try:
            cur = con.execute("DELETE FROM saves WHERE session_id = ?", (self.client_id,))
            con.commit()
            return cur.rowcount
        finally:
            con.close()


# ============================================================
# 编辑器数据快照 / 还原
# ============================================================
def editor_snapshot(c: GameClient) -> dict:
    """跑用例前拍下 scenes/items/enemies/npcs/config/layouts 六份数据的深拷贝。"""
    d = c.editor_data()
    return {k: copy.deepcopy(d[k]) for k in
            ("scenes", "items", "enemies", "npcs", "config", "layouts")}


def editor_restore(c: GameClient, snap: dict) -> None:
    """把编辑器数据还原到快照：先 upsert 旧实体（恢复内容/引用），再删测试新增实体。

    顺序按引用关系：还原 scenes→items→enemies→npcs→config（config 的事件规则
    引用所有实体，必须最后存）；删除反向 npcs→enemies→items→scenes。
    """
    # 1) 内容还原
    for scene in snap["scenes"].values():
        c.editor_save("scene", scene)
    for item in snap["items"].values():
        c.editor_save("item", item)
    for enemy in snap["enemies"].values():
        c.editor_save("enemy", enemy)
    for npc in snap["npcs"].values():
        c.editor_save("npc", npc)
    if "config" in snap:
        c.config_save(snap["config"])
        # config_save 只覆盖开局字段、保留 event_rules —— 规则集需要单独还原
        snap_rules = {r["id"]: r for r in snap["config"].get("event_rules", [])}
        for rule in snap_rules.values():
            c.event_rule_save(rule)
        live_rules = c.editor_data()["config"].get("event_rules", [])
        for rule in live_rules:
            if rule["id"] not in snap_rules:
                c.event_rule_delete(rule["id"])

    # 2) 删除测试新增实体（NPC 引用场景/物品/敌人，最先删）
    live = c.editor_data()
    for nid in list(live["npcs"].keys()):
        if nid not in snap["npcs"]:
            c.editor_delete("npc", nid)
    for eid in list(live["enemies"].keys()):
        if eid not in snap["enemies"]:
            c.editor_delete("enemy", eid)
    for iid in list(live["items"].keys()):
        if iid not in snap["items"]:
            c.editor_delete("item", iid)
    for _ in range(3):
        live_scenes = c.editor_data()["scenes"]
        extras = [sid for sid in live_scenes if sid not in snap["scenes"]]
        if not extras:
            break
        progress = False
        for sid in extras:
            r = c.editor_delete("scene", sid)
            progress = progress or r.get("success", False)
        if not progress:
            break

    # 3) 布局还原（独立通道）：快照里的逐份写回，快照外的写空删除
    snap_layouts = snap.get("layouts") or {}
    for npc_id, layout in snap_layouts.items():
        c.layout_save(npc_id, layout)
    for npc_id in c.layout_all():
        if npc_id not in snap_layouts:
            c.layout_save(npc_id, {})


def cleanup_qa_saves(db_path: str = DEFAULT_DB) -> int:
    """run_all 收尾：扫掉所有 qa_% 前缀的测试存档（含历史残留）。"""
    if not os.path.exists(db_path):
        return 0
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute("DELETE FROM saves WHERE session_id LIKE 'qa_%'")
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def cleanup_backups(data_dir: str = None) -> int:
    """删掉编辑器在测试期间滚动产生的 *.bak（备份机制产物，非源数据）。"""
    data_dir = data_dir or os.path.join(_REPO_ROOT, "game_data")
    n = 0
    for p in glob.glob(os.path.join(data_dir, "*.bak")):
        os.remove(p)
        n += 1
    return n


# ============================================================
# 常用小工具
# ====================================================
def inventory_ids(state: dict) -> list:
    """从 /api/state 响应里取出背包物品 id 列表。"""
    return [it.get("id") for it in state.get("player_inventory", [])]


def enemy_ids_here(state: dict) -> list:
    """从 /api/state 响应顶层取出当前场景的活着的敌人 id 列表。"""
    return [e.get("id") for e in state.get("enemies_here", [])]


def usable_item_ids(combat_state: dict) -> list:
    return [it.get("id") for it in combat_state.get("usable_items", [])]


def ping(base: str = DEFAULT_BASE) -> bool:
    """探测服务器是否在跑（给 run_all 友好报错用）。"""
    try:
        urllib.request.urlopen(base + "/static/index.html", timeout=5)
        return True
    except Exception:
        return False
