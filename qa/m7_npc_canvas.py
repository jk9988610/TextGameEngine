# -*- coding: utf-8 -*-
"""M7 回归套件：NPC 画布 —— 布局坐标独立通道 + 后端契约 + 内容/坐标分离。

浏览器内 graph_model.js 的纯函数与拉线 UI 交互由人工走查（无 JS 运行时，
见 m7 plan 人工清单）；本套件覆盖所有可经 API 验证的后端/数据契约。
"""
from qa.tge_api import (
    GameClient,
    QaRunner,
    editor_snapshot,
    editor_restore,
)

SUITE = "M7 NPC 画布布局"


def run(r: QaRunner) -> None:
    # 与 M3~M6 套件一致：run_all 只传 runner，client 在套件内自建
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

    # ---------- G4 坐标与内容分离：布局 API 不动 npc_dialogues ----------
    r.section("G4 坐标通道不污染游戏内容")
    snap = editor_snapshot(c)
    try:
        npc_before = c.editor_data()["npcs"]["npc_2_sprite"]
        c.layout_save("npc_2_sprite", {"greet": {"x": 100, "y": 100}})
        npc_after = c.editor_data()["npcs"]["npc_2_sprite"]
        # 游戏数据里的节点不含 x/y，且内容完全没变
        greet_node = npc_after["nodes"]["greet"]
        r.check("G4-节点无坐标字段", "x" not in greet_node and "y" not in greet_node,
                list(greet_node.keys()))
        r.check("G4-内容未被布局改动", npc_after == npc_before)
    finally:
        editor_restore(c, snap)

    # ---------- G3 NPC 内容保存仍正常（画布结构改动走同一出口） ----------
    r.section("G3 NPC 内容契约不回退")
    snap = editor_snapshot(c)
    try:
        npc = c.editor_data()["npcs"]["npc_1_drunk"]
        # 用 ask_key 节点做结构改动目标（其选项"谢谢提示"本就指向 leave，
        # 改为指向 drink_no 验证 next 可被画布式写入），结束后 restore 还原
        npc["nodes"]["ask_key"]["choices"][0]["next"] = "drink_no"
        res = c.editor_save("npc", npc)
        r.check("G3-画布式结构改动能保存", res.get("success") is True, res)
        after = c.editor_data()["npcs"]["npc_1_drunk"]
        r.check("G3-next 写入成功",
                after["nodes"]["ask_key"]["choices"][0].get("next") == "drink_no")

        # 画布不会绕过校验：悬空 next 仍被后端拒绝
        bad = c.editor_data()["npcs"]["npc_1_drunk"]
        bad["nodes"]["greet"]["choices"][0]["next"] = "qa_ghost_node"
        r.check("G3-悬空next仍拦截",
                c.editor_save("npc", bad).get("success") is False)
    finally:
        editor_restore(c, snap)
    r.check("G3-还原后ask_key回基线",
            c.editor_data()["npcs"]["npc_1_drunk"]
            ["nodes"]["ask_key"]["choices"][0].get("next") == "leave")


if __name__ == "__main__":
    import sys
    runner = QaRunner(SUITE)
    run(runner)
    runner.report()
    sys.exit(1 if runner.failed else 0)
