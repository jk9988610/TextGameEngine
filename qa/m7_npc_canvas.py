# -*- coding: utf-8 -*-
"""M7 回归套件：角色对话画布 —— 布局坐标独立通道 + 后端契约 + 内容/坐标分离。

浏览器内 graph_model.js 的纯函数与拉线 UI 交互由人工走查（无 JS 运行时，
见 m7 plan 人工清单）；本套件覆盖所有可经 API 验证的后端/数据契约。

P1 起「角色」合并（characters.json + 派生视图 npcs），套件自建一个贴了
`talkable` 标签的 QA 角色做内容契约验证，不再依赖具体工程里的 NPC。
"""
import tempfile

from qa.tge_api import (
    GameClient,
    QaRunner,
    editor_snapshot,
    editor_restore,
)
from engine.editor_manager import EditorManager

SUITE = "M7 角色对话画布布局"

CHAR_ID = "qa_m7_char"


def _probe_char(scene_id):
    """自建的可对话角色：greet →(ask|leave)，ask →(leave)。"""
    return {
        "id": CHAR_ID, "name": "QA画布角色", "description": "测完即删",
        "tags": ["talkable"], "scene_id": scene_id,
        "greeting": "greet", "greeting_rules": [],
        "nodes": {
            "greet": {"text": "你好。", "choices": [
                {"text": "问点事", "next": "ask"},
                {"text": "走了", "next": "leave"},
            ]},
            "ask": {"text": "问吧。", "choices": [{"text": "哦", "next": "leave"}]},
            "leave": {"text": "再见。", "choices": []},
        },
    }


def run_unit(r: QaRunner) -> None:
    """纯函数段：开始卡保留键 __start__ 的落盘与修剪保护（临时目录，不碰工程数据）。"""
    r.section("G0 开始卡 __start__ 坐标与普通节点同通道")
    with tempfile.TemporaryDirectory() as tmp:
        em = EditorManager(data_dir=tmp)
        ok = em.save_layout("qa_n", {
            "greet": {"x": 28, "y": 56},
            "__start__": {"x": -420, "y": 0},
        })
        r.check("G0-开始卡坐标可保存", ok.get("success") and ok.get("nodes") == 2, ok)
        got = em.load_layouts().get("qa_n", {})
        r.check("G0-开始卡坐标回读",
                got.get("__start__") == {"x": -420, "y": 0}
                and got.get("greet") == {"x": 28, "y": 56}, got)
        # 保存 NPC（节点只剩 greet）触发修剪：孤儿节点清掉，__start__ 必须保留
        em.prune_layouts({"qa_n": {"nodes": {"greet": {}}}})
        kept = em.load_layouts()["qa_n"]
        r.check("G0-修剪保留开始卡键",
                set(kept) == {"greet", "__start__"}
                and kept["__start__"] == {"x": -420, "y": 0}, kept)
        # NPC 整体删除时布局（含 __start__）一并删除
        em.prune_layouts({})
        r.check("G0-NPC删除布局全清", "qa_n" not in em.load_layouts(),
                list(em.load_layouts().keys()))


def run(r: QaRunner) -> None:
    # 与 M3~M6 套件一致：run_all 只传 runner，client 在套件内自建
    run_unit(r)
    c = GameClient("qa_m7")
    try:
        _run(r, c)
    finally:
        c.cleanup_saves()


def _run(r: QaRunner, c: GameClient) -> None:
    # ---------- G1 布局 CRUD + 校验 ----------
    r.section("G1 布局坐标 CRUD/校验")
    snap = editor_snapshot(c)
    try:
        # 合法坐标存入并重拉一致
        res = c.layout_save("npc_2_sprite",
                            {"greet": {"x": 28, "y": 56},
                             "secret": {"x": 420, "y": 140}})
        r.check("G1-合法坐标保存", res.get("success") is True, res)
        got = c.layout_get("npc_2_sprite")
        r.check("G1-坐标重拉一致",
                got.get("greet") == {"x": 28, "y": 56}, got)

        # 非法条目（非数字、超范围）被丢弃，合法的保留
        res2 = c.layout_save("npc_2_sprite", {
            "greet": {"x": 56, "y": 84},
            "badtype": {"x": "xx", "y": 1},
            "huge": {"x": 999999, "y": 0},
            "badnode": "nope",
        })
        got2 = c.layout_get("npc_2_sprite")
        r.check("G1-非法条目丢弃",
                set(got2.keys()) == {"greet"} and got2["greet"] == {"x": 56, "y": 84},
                got2)

        # 非法 NPC ID 被拦
        r.check("G1-非法NPCID拦截",
                c.layout_save("BAD ID!", {})["success"] is False)

        # 空布局不保留空键
        c.layout_save("npc_2_sprite", {})
        r.check("G1-空布局无空键", "npc_2_sprite" not in c.layout_all(),
                list(c.layout_all().keys()))

    finally:
        editor_restore(c, snap)
    # 还原后布局回到快照基线（不假设基线为空）
    r.check("G1-还原后布局回基线", c.layout_get("npc_2_sprite") ==
            (snap["layouts"].get("npc_2_sprite") or {}),
            c.layout_get("npc_2_sprite"))

    # ---------- G4 坐标与内容分离：布局 API 不动 characters 内容 ----------
    r.section("G4 坐标通道不污染游戏内容")
    snap = editor_snapshot(c)
    try:
        scene_id = sorted(c.editor_data()["scenes"].keys())[0]
        created = c.editor_save("character", _probe_char(scene_id))
        r.check("G4-QA角色已建", created.get("success") is True, created)
        npc_before = c.editor_data()["npcs"][CHAR_ID]
        # 角色画布的坐标写入 nodes.greet（含保留键 __start__）
        c.layout_save(CHAR_ID, {"greet": {"x": 100, "y": 100},
                                "__start__": {"x": -420, "y": 0}})
        npc_after = c.editor_data()["npcs"][CHAR_ID]
        # 游戏数据里的节点不含 x/y，且内容完全没变
        greet_node = npc_after["nodes"]["greet"]
        r.check("G4-节点无坐标字段", "x" not in greet_node and "y" not in greet_node,
                list(greet_node.keys()))
        r.check("G4-内容未被布局改动", npc_after == npc_before)
        r.check("G4-坐标只进布局通道",
                c.layout_get(CHAR_ID).get("greet") == {"x": 100, "y": 100}
                and c.layout_get(CHAR_ID).get("__start__") == {"x": -420, "y": 0},
                c.layout_get(CHAR_ID))
    finally:
        c.editor_delete("character", CHAR_ID)
        editor_restore(c, snap)

    # ---------- G3 角色内容保存仍正常（画布结构改动走同一出口） ----------
    r.section("G3 角色内容契约不回退")
    snap = editor_snapshot(c)
    try:
        scene_id = sorted(c.editor_data()["scenes"].keys())[0]
        c.editor_save("character", _probe_char(scene_id))
        # 画布式结构改动：greet 的"问点事"选项从 ask 改指 leave
        ch = c.editor_data()["characters"][CHAR_ID]
        r.check("G3-基线选项指向ask",
                ch["nodes"]["greet"]["choices"][0].get("next") == "ask")
        ch["nodes"]["greet"]["choices"][0]["next"] = "leave"
        res = c.editor_save("character", ch)
        r.check("G3-画布式结构改动能保存", res.get("success") is True, res)
        after = c.editor_data()["characters"][CHAR_ID]
        r.check("G3-next 写入成功",
                after["nodes"]["greet"]["choices"][0].get("next") == "leave")

        # 画布不会绕过校验：悬空 next 仍被后端拒绝
        bad = c.editor_data()["characters"][CHAR_ID]
        bad["nodes"]["greet"]["choices"][0]["next"] = "qa_ghost_node"
        r.check("G3-悬空next仍拦截",
                c.editor_save("character", bad).get("success") is False)
    finally:
        c.editor_delete("character", CHAR_ID)
        editor_restore(c, snap)
    r.check("G3-删除后派生视图不含它",
            CHAR_ID not in (c.editor_data()["npcs"] or {}),
            list(c.editor_data()["npcs"] or {}))


if __name__ == "__main__":
    import sys
    runner = QaRunner(SUITE)
    run(runner)
    runner.report()
    sys.exit(1 if runner.failed else 0)
