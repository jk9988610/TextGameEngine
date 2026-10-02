# -*- coding: utf-8 -*-
"""
M5 回归套件：NPC 对话编辑 / 通用 flags / 条件问候 / 选项条件 / 效果库。

测试 NPC qa_sage（酒馆）：
  四条问候规则（flag → 钥匙 → 击杀哥布林 → 金币≥5）+ 效果实验室节点
  （set_flag/give_item/remove_item/heal/max_hp/gold/teleport/start_combat）
另有 qa_fighter（洞穴，开战效果）、qa_legacy（旧 requires_item 兼容）等临时 NPC。
"""
from qa.tge_api import GameClient, QaRunner, editor_snapshot, editor_restore, inventory_ids

SUITE = "M5 NPC对话/flags/条件效果"

SAGE = {
    "id": "qa_sage",
    "name": "QA贤者",
    "scene_id": "tavern",
    "greeting": "greet",
    "greeting_rules": [
        {"if": {"flag": "qa_blessed"}, "node": "flag_node"},
        {"if": {"has_item": "rusty_key"}, "node": "key_node"},
        {"if": {"enemy_killed": "cave_goblin"}, "node": "kill_node"},
        {"if": {"gold_gte": 5}, "node": "rich_node"},
    ],
    "nodes": {
        "greet": {"text": "默认问候", "choices": [
            {"text": "给我药水", "next": "give_node",
             "effects": [{"type": "give_item", "item": "healing_potion"}]},
            {"text": "拿钥匙才看得到的选项", "next": "key_node",
             "if": {"has_item": "rusty_key"}},
            {"text": "结束", "next": None},
        ]},
        "give_node": {"text": "拿去吧", "choices": [
            {"text": "去效果实验室", "next": "effects_node"},
        ]},
        "effects_node": {"text": "选一个效果", "choices": [
            {"text": "设标志", "next": "done_node",
             "effects": [{"type": "set_flag", "flag": "qa_seen"}]},
            {"text": "回血", "next": "done_node",
             "effects": [{"type": "heal", "amount": 5}]},
            {"text": "上限+5", "next": "done_node",
             "effects": [{"type": "max_hp", "amount": 5}]},
            {"text": "金币+3", "next": "done_node",
             "effects": [{"type": "gold", "amount": 3}]},
            {"text": "收回药水", "next": "done_node",
             "effects": [{"type": "remove_item", "item": "healing_potion"}]},
            {"text": "送我去森林",
             "effects": [{"type": "teleport", "scene": "forest"}]},
        ]},
        "done_node": {"text": "好了", "choices": []},
        "flag_node": {"text": "flag问候", "choices": []},
        "key_node": {"text": "钥匙问候", "choices": []},
        "kill_node": {"text": "击杀问候", "choices": []},
        "rich_node": {"text": "有钱问候", "choices": []},
    },
}

FIGHTER = {
    "id": "qa_fighter",
    "name": "QA好战者",
    "scene_id": "cave",
    "greeting": "greet",
    "greeting_rules": [],
    "nodes": {
        "greet": {"text": "想挨打吗？", "choices": [
            {"text": "开打",
             "effects": [{"type": "start_combat", "enemy": "cave_goblin"}]},
        ]},
    },
}


def base_npc(npc_id, name="QA临时NPC", scene="tavern"):
    return {"id": npc_id, "name": name, "scene_id": scene, "greeting": "greet",
            "greeting_rules": [],
            "nodes": {"greet": {"text": "你好", "choices": [
                {"text": "再见"},
            ]}}}


def to_lab(c):
    """greet(给我药水→give_node) → 效果实验室。"""
    c.dialogue_choose(0)
    c.dialogue_choose(0)


def run(r: QaRunner) -> None:
    c = GameClient("qa_m5")
    snap = editor_snapshot(c)
    try:
        # ---------- N1 编辑器首屏含 NPC ----------
        r.section("N1 编辑器数据包含 NPC")
        d = c.editor_data()
        r.check("npcs-table-present",
                isinstance(d.get("npcs"), dict)
                and {"npc_1_drunk", "npc_2_sprite"} <= set(d["npcs"]),
                list(d.get("npcs", {})))

        # ---------- N2 新建 qa_sage ----------
        r.section("N2 新建测试 NPC")
        res = c.editor_save("npc", SAGE)
        r.check("sage-create-ok", res.get("success") is True, res)
        got = c.editor_data()["npcs"].get("qa_sage")
        r.check("sage-rules-roundtrip", got and len(got["greeting_rules"]) == 4, got)
        r.check("sage-nodes-roundtrip", got and len(got["nodes"]) == 8,
                list(got["nodes"]) if got else None)
        r.check("sage-choice-effect-roundtrip",
                got and got["nodes"]["greet"]["choices"][0]["effects"][0]["item"]
                == "healing_potion",
                got)

        # ---------- N3 校验拦截 ----------
        r.section("N3 NPC 校验")
        r.check("bad-name-blocked",
                c.editor_save("npc", {**base_npc("qa_bad"), "name": ""}).get("success") is False)
        r.check("bad-scene-blocked",
                c.editor_save("npc", {**base_npc("qa_bad"),
                                      "scene_id": "qa_no_scene"}).get("success") is False)
        bad_next = base_npc("qa_bad")
        bad_next["nodes"]["greet"]["choices"][0]["next"] = "qa_no_node"
        r.check("dangling-next-blocked",
                c.editor_save("npc", bad_next).get("success") is False)
        bad_eff_item = base_npc("qa_bad")
        bad_eff_item["nodes"]["greet"]["choices"][0]["effects"] = [
            {"type": "give_item", "item": "qa_no_item"}]
        r.check("bad-effect-item-blocked",
                c.editor_save("npc", bad_eff_item).get("success") is False)
        bad_heal = base_npc("qa_bad")
        bad_heal["nodes"]["greet"]["choices"][0]["effects"] = [
            {"type": "heal", "amount": 0}]
        r.check("bad-heal-blocked",
                c.editor_save("npc", bad_heal).get("success") is False)
        bad_tp = base_npc("qa_bad")
        bad_tp["nodes"]["greet"]["choices"][0]["effects"] = [
            {"type": "teleport", "scene": "qa_no_scene"}]
        r.check("bad-teleport-blocked",
                c.editor_save("npc", bad_tp).get("success") is False)
        bad_fight = base_npc("qa_bad", scene="cave")
        bad_fight["nodes"]["greet"]["choices"][0]["effects"] = [
            {"type": "start_combat", "enemy": "qa_no_enemy"}]
        r.check("bad-combat-blocked",
                c.editor_save("npc", bad_fight).get("success") is False)
        bad_cond_item = base_npc("qa_bad")
        bad_cond_item["nodes"]["greet"]["choices"][0]["if"] = {
            "has_item": "qa_no_item"}
        r.check("bad-cond-item-blocked",
                c.editor_save("npc", bad_cond_item).get("success") is False)
        bad_rule = base_npc("qa_bad")
        bad_rule["greeting_rules"] = [{"if": {"flag": "x"}, "node": "qa_no_node"}]
        r.check("bad-rule-node-blocked",
                c.editor_save("npc", bad_rule).get("success") is False)
        r.check("bad-npc-not-saved", "qa_bad" not in c.editor_data()["npcs"])

        # 不可达节点 → 警告但不阻止保存
        warn_npc = base_npc("qa_warn")
        warn_npc["nodes"]["orphan"] = {"text": "没人能走到我", "choices": []}
        wres = c.editor_save("npc", warn_npc)
        r.check("unreachable-warning",
                wres.get("success") and any("orphan" in w for w in wres.get("warnings", [])),
                wres)
        r.check("warn-npc-delete", c.editor_delete("npc", "qa_warn").get("success") is True)

        # ---------- N4 对话中删除保护 ----------
        r.section("N4 删除保护")
        r.check("dlg-create-ok",
                c.editor_save("npc", base_npc("qa_dlg")).get("success") is True)
        c.reset()
        r.check("dlg-start", c.dialogue_start("qa_dlg").get("success") is True)
        r.check("delete-in-dialogue-blocked",
                c.editor_delete("npc", "qa_dlg").get("success") is False)
        c.dialogue_choose(0)  # 唯一选项无 next → 结束
        r.check("dialogue-ended", c.dialogue().get("active") is False)
        r.check("delete-after-dialogue-ok",
                c.editor_delete("npc", "qa_dlg").get("success") is True)

        # ---------- N5 条件问候 ----------
        r.section("N5 greeting_rules 起点选择")
        c.reset()
        c.dialogue_start("qa_sage")
        r.check("default-greet", c.dialogue().get("node_id") == "greet")
        c.set_flag("qa_blessed")
        c.dialogue_start("qa_sage")
        r.check("flag-greeting", c.dialogue().get("node_id") == "flag_node")
        c.reset()
        c.give("rusty_key")
        c.dialogue_start("qa_sage")
        r.check("has-item-greeting", c.dialogue().get("node_id") == "key_node")
        c.reset()
        c.teleport("cave")
        c.combat_start("cave_goblin")
        c.attack_until("cave_goblin")
        c.teleport("tavern")
        c.dialogue_start("qa_sage")
        r.check("enemy-killed-greeting", c.dialogue().get("node_id") == "kill_node")
        c.reset()
        for _ in range(5):
            c.give("gold_coin")
        c.dialogue_start("qa_sage")
        r.check("gold-gte-greeting", c.dialogue().get("node_id") == "rich_node")

        # ---------- N6 选项条件隐藏 + requires_item 旧格式 ----------
        r.section("N6 选项条件")
        # 注意：qa_sage 带钥匙时问候规则会直接把起点跳到 key_node，
        # 所以 greet 节点上的条件选项用专门的 qa_cond NPC（无问候规则）验证
        cond_npc = base_npc("qa_cond")
        cond_npc["nodes"]["a_node"] = {"text": "隐藏分支", "choices": [
            {"text": "回来", "next": "greet"},
        ]}
        cond_npc["nodes"]["greet"]["choices"] = [
            {"text": "拿钥匙才看得到的选项", "next": "a_node",
             "if": {"has_item": "rusty_key"}},
            {"text": "结束"},
        ]
        r.check("cond-create-ok", c.editor_save("npc", cond_npc).get("success") is True)
        c.reset()
        c.dialogue_start("qa_cond")
        dl = c.dialogue()
        r.check("choice-hidden-without-key",
                dl["node_id"] == "greet" and len(dl["choices"]) == 1, dl.get("choices"))
        c.give("rusty_key")
        c.dialogue_start("qa_cond")
        dl = c.dialogue()
        r.check("choice-shown-with-key", len(dl["choices"]) == 2, dl.get("choices"))
        resp = c.dialogue_choose(0)  # 条件选项 → a_node
        r.check("conditional-choice-works",
                resp.get("success") and c.dialogue().get("node_id") == "a_node", resp)

        legacy = base_npc("qa_legacy")
        legacy["nodes"]["greet"]["choices"].append(
            {"text": "旧格式条件选项", "requires_item": "healing_potion"})
        r.check("legacy-create-ok", c.editor_save("npc", legacy).get("success") is True)
        c.reset()
        c.dialogue_start("qa_legacy")
        r.check("legacy-choice-hidden",
                len(c.dialogue()["choices"]) == 1, c.dialogue().get("choices"))
        c.give("healing_potion")
        c.dialogue_start("qa_legacy")
        r.check("legacy-choice-shown",
                len(c.dialogue()["choices"]) == 2, c.dialogue().get("choices"))

        # ---------- N7 效果库 ----------
        r.section("N7 对话效果")
        c.reset()
        c.dialogue_start("qa_sage")
        resp = c.dialogue_choose(0)  # 给我药水（give_item）
        r.check("give-item-effect", resp.get("success") is True
                and "healing_potion" in inventory_ids(c.state()), resp)
        r.check("give-item-message",
                any("治疗药水" in m for m in resp.get("effect_messages", [])),
                resp.get("effect_messages"))
        c.dialogue_choose(0)  # → 效果实验室
        resp = c.dialogue_choose(0)  # 设标志
        r.check("set-flag-effect",
                c.state().get("flags", {}).get("qa_seen") is True
                and c.dialogue().get("node_id") == "done_node", resp)

        # heal：先去洞穴挨两轮（每轮 -3，HP 44），回酒馆对话
        c.reset()
        c.teleport("cave")
        c.combat_start("cave_goblin")
        c.attack()
        c.attack()
        c.teleport("tavern")
        c.dialogue_start("qa_sage")
        to_lab(c)
        c.dialogue_choose(1)  # 回血
        r.check("heal-effect", c.combat()["player"]["hp"] == 49,
                c.combat()["player"])

        # max_hp +5（当前 49 → 54/55）
        c.reset()
        c.teleport("cave")
        c.combat_start("cave_goblin")
        c.attack()
        c.attack()
        c.teleport("tavern")
        c.dialogue_start("qa_sage")
        to_lab(c)
        c.dialogue_choose(2)  # 上限+5（44+5=49，上限 55）
        p = c.combat()["player"]
        r.check("max-hp-effect", p["max_hp"] == 55 and p["hp"] == 49, p)

        # gold +3
        c.reset()
        c.dialogue_start("qa_sage")
        to_lab(c)
        c.dialogue_choose(3)
        r.check("gold-effect", c.state().get("player_gold") == 3,
                c.state().get("player_gold"))

        # remove_item
        c.reset()
        c.dialogue_start("qa_sage")
        c.dialogue_choose(0)  # 拿到药水
        c.dialogue_choose(0)  # 进实验室
        c.dialogue_choose(4)  # 收回药水
        r.check("remove-item-effect",
                "healing_potion" not in inventory_ids(c.state()),
                inventory_ids(c.state()))

        # teleport（无 next + 传送效果，对话应被 SCENE_ENTER 结束）
        c.reset()
        c.dialogue_start("qa_sage")
        to_lab(c)
        resp = c.dialogue_choose(5)
        st = c.state()
        r.check("teleport-effect",
                st["scene"]["id"] == "forest" and c.dialogue().get("active") is False,
                resp)

        # start_combat（qa_fighter 在洞穴）
        r.check("fighter-create-ok",
                c.editor_save("npc", FIGHTER).get("success") is True)
        c.reset()
        c.teleport("cave")
        c.dialogue_start("qa_fighter")
        resp = c.dialogue_choose(0)
        cb = c.combat()
        r.check("start-combat-effect",
                cb.get("active") and cb["enemy"]["id"] == "cave_goblin"
                and c.dialogue().get("active") is False,
                {"resp": resp, "combat": cb})

        # ---------- N8 存量 NPC 回归 ----------
        r.section("N8 醉汉/莉娅回归")
        c.reset()
        c.teleport("tavern")
        c.dialogue_start("npc_1_drunk")
        r.check("drunk-default-greet", c.dialogue().get("node_id") == "greet")
        c.teleport("cave")
        c.combat_start("cave_goblin")
        c.attack_until("cave_goblin")
        c.teleport("tavern")
        c.dialogue_start("npc_1_drunk")
        r.check("drunk-after-kill-greeting",
                c.dialogue().get("node_id") == "hero_welcome")

        c.reset()
        c.teleport("forest")
        c.dialogue_start("npc_2_sprite")
        r.check("sprite-choices-2-without-key",
                len(c.dialogue()["choices"]) == 2, c.dialogue().get("choices"))
        c.give("rusty_key")
        c.dialogue_start("npc_2_sprite")
        dl = c.dialogue()
        # 带钥匙时问候规则把起点直接切到 has_key 节点（该节点只有 1 个选项）
        r.check("sprite-has-key-greeting",
                dl["node_id"] == "has_key" and len(dl["choices"]) == 1, dl)
        # 祝福真效果：杀哥布林 → 难对付 → 祝福 → 最大 HP 55
        c.reset()
        c.teleport("cave")
        c.combat_start("cave_goblin")
        c.attack_until("cave_goblin")
        c.teleport("forest")
        c.dialogue_start("npc_2_sprite")
        r.check("sprite-after-kill-greeting",
                c.dialogue().get("node_id") == "after_kill")
        c.dialogue_choose(1)  # 它确实还挺难对付的
        rr = c.dialogue_choose(0)  # 接受祝福 max_hp +5
        p = c.combat()["player"]
        # 击杀哥布林共挨 3 轮反击（5-2=3/轮）→ 41 血，祝福后 46/55
        r.check("sprite-blessing-real",
                p["max_hp"] == 55 and p["hp"] == 46
                and any("生命上限" in m for m in rr.get("effect_messages", [])),
                {"player": p, "resp": rr})

    finally:
        editor_restore(c, snap)
        c.cleanup_saves()


if __name__ == "__main__":
    import sys
    from qa.tge_api import ping, DEFAULT_BASE
    if not ping():
        print(f"服务器未响应（{DEFAULT_BASE}），请先启动：.venv\\Scripts\\python.exe app.py")
        sys.exit(2)
    r = QaRunner(SUITE)
    run(r)
    print("\n" + "=" * 60)
    print(r.report())
    sys.exit(1 if r.failed else 0)
