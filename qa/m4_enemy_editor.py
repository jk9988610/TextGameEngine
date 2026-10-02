# -*- coding: utf-8 -*-
"""
M4 回归套件：敌人编辑器 / 场景敌人配置 / 金币掉落 / 战斗中喝药。

战斗公式（引擎约定，写预期值的依据）：
  玩家命中 = 武器 damage - 敌人防御（最小 1）
  敌人反击 = 敌人攻击 - 玩家防御（最小 1，玩家 50HP / 攻 5 / 防 2，铁剑 damage 10）
测试敌人 qa_slime：HP22 / 攻3 / 防1 / 金币2 / 掉治疗药水 → 每回合互伤 9 vs 1。
"""
from qa.tge_api import (
    GameClient, QaRunner,
    editor_snapshot, editor_restore,
    inventory_ids, enemy_ids_here, usable_item_ids,
)

SUITE = "M4 敌人编辑/战斗喝药/金币掉落"

SLIME = {
    "id": "qa_slime",
    "name": "QA史莱姆",
    "description": "QA 专用敌人，套件结束应被自动删除。",
    "hp": 22,
    "attack": 3,
    "defense": 1,
    "reward_items": ["healing_potion"],
    "reward_gold": 2,
}


def run(r: QaRunner) -> None:
    c = GameClient("qa_m4")
    snap = editor_snapshot(c)
    try:
        # ---------- E1 编辑器首屏含敌人表 ----------
        r.section("E1 编辑器数据包含敌人")
        d = c.editor_data()
        r.check("enemies-table-present",
                isinstance(d.get("enemies"), dict) and "cave_goblin" in d["enemies"],
                d.get("enemies"))

        # ---------- E2 新建 qa_slime ----------
        r.section("E2 新建测试敌人")
        res = c.editor_save("enemy", SLIME)
        r.check("create-slime-ok", res.get("success") is True, res)
        got = c.editor_data()["enemies"].get("qa_slime")
        r.check("slime-fields",
                got and got["hp"] == 22 and got["attack"] == 3 and got["defense"] == 1
                and got["reward_gold"] == 2 and got["reward_items"] == ["healing_potion"],
                got)

        # ---------- E3 校验拦截 ----------
        r.section("E3 敌人数值/引用校验")
        r.check("hp0-blocked",
                c.editor_save("enemy", {**SLIME, "id": "qa_bad", "hp": 0}).get("success") is False)
        r.check("empty-name-blocked",
                c.editor_save("enemy", {**SLIME, "id": "qa_bad", "name": "  "}).get("success") is False)
        r.check("bad-reward-item-blocked",
                c.editor_save("enemy", {**SLIME, "id": "qa_bad",
                                        "reward_items": ["qa_no_such_item"]}).get("success") is False)
        r.check("negative-attack-blocked",
                c.editor_save("enemy", {**SLIME, "id": "qa_bad", "attack": -5}).get("success") is False)
        r.check("bad-enemy-not-saved",
                "qa_bad" not in c.editor_data()["enemies"])

        # ---------- E4 把史莱姆挂到森林 ----------
        r.section("E4 场景敌人配置 + 新游戏生效")
        forest = c.editor_data()["scenes"]["forest"]
        forest["enemies_here"] = forest.get("enemies_here", []) + ["qa_slime"]
        r.check("forest-save-ok", c.editor_save("scene", forest).get("success") is True)
        c.reset()
        st = c.state()
        r.check("tavern-no-slime", "qa_slime" not in enemy_ids_here(st), enemy_ids_here(st))
        c.teleport("forest")
        st = c.state()
        r.check("forest-has-slime", "qa_slime" in enemy_ids_here(st), enemy_ids_here(st))

        # ---------- E5 战斗中喝药 + 金币掉落 ----------
        r.section("E5 战斗喝药与金币掉落")
        c.reset()
        c.give("healing_potion")
        c.teleport("forest")
        r.check("battle-start", c.combat_start("qa_slime").get("success") is True)
        c.attack()
        c.attack()
        cb = c.combat()
        r.check("slime-4hp-after-2-hits", cb.get("active") and cb["enemy"]["hp"] == 4, cb.get("enemy"))
        r.check("player-48hp", cb["player"]["hp"] == 48, cb.get("player"))
        r.check("potion-usable-in-battle",
                "healing_potion" in usable_item_ids(cb), usable_item_ids(cb))
        drank = c.drink("healing_potion")
        r.check("drink-success", drank.get("success") is True, drank)
        r.check("drink-log-2-lines", len(drank.get("log", [])) == 2, drank.get("log"))
        r.check("player-49-after-counter", drank.get("player", {}).get("hp") == 49,
                drank.get("player"))
        r.check("potion-consumed",
                "healing_potion" not in usable_item_ids(c.combat()),
                usable_item_ids(c.combat()))
        kill = c.attack()
        r.check("slime-killed", kill.get("defeated") == "qa_slime", kill)
        r.check("kill-log-4-lines", len(kill.get("log", [])) == 4, kill.get("log"))
        r.check("gold-reward-2", c.state().get("player_gold") == 2, c.state().get("player_gold"))
        r.check("loot-potion-take", c.take("healing_potion").get("success") is True)

        # ---------- E6 满血喝药拒绝 / 非消耗品不能喝 ----------
        r.section("E6 喝药边界")
        c.reset()
        c.give("healing_potion")
        c.teleport("forest")
        c.combat_start("qa_slime")
        refused = c.drink("healing_potion")
        r.check("full-hp-refused", refused.get("success") is False and "满" in refused.get("message", ""),
                refused)
        r.check("potion-still-in-bag", "healing_potion" in inventory_ids(c.state()))
        r.check("sword-not-drinkable",
                c.drink("rusty_sword").get("success") is False)
        r.check("sword-still-in-bag", "rusty_sword" in inventory_ids(c.state()))

        # ---------- E7 删除保护 ----------
        r.section("E7 引用删除保护")
        r.check("referenced-enemy-blocked",
                c.editor_delete("enemy", "qa_slime").get("success") is False)
        r.check("potion-delete-blocked",
                c.editor_delete("item", "healing_potion").get("success") is False)
        r.check("coin-delete-blocked",
                c.editor_delete("item", "gold_coin").get("success") is False)

        # ---------- E8 战斗中保护，战后可删，渲染消失 ----------
        r.section("E8 存活战斗保护与清理")
        # E6 的战斗仍在（满血，没打掉血）
        r.check("delete-in-battle-blocked",
                c.editor_delete("enemy", "qa_slime").get("success") is False)
        last = c.attack_until("qa_slime")
        r.check("slime-killed-for-delete", last.get("defeated") == "qa_slime", last)
        forest_ok = c.editor_data()["scenes"]["forest"]
        forest_ok["enemies_here"] = [e for e in forest_ok.get("enemies_here", [])
                                     if e != "qa_slime"]
        c.editor_save("scene", forest_ok)
        r.check("delete-after-battle-ok",
                c.editor_delete("enemy", "qa_slime").get("success") is True)
        r.check("slime-gone-from-editor",
                "qa_slime" not in c.editor_data()["enemies"])
        r.check("slime-gone-from-render",
                "qa_slime" not in enemy_ids_here(c.state()), enemy_ids_here(c.state()))

        # ---------- E9 回归：哥布林无金币掉落键 → 击杀后金币仍为 0 ----------
        r.section("E9 哥布林回归（reward_gold 缺省=0）")
        c.reset()
        c.teleport("cave")
        r.check("goblin-battle-start",
                c.combat_start("cave_goblin").get("success") is True)
        last = c.attack_until("cave_goblin")
        r.check("goblin-killed", last.get("defeated") == "cave_goblin", last)
        r.check("goblin-no-gold", c.state().get("player_gold") == 0,
                c.state().get("player_gold"))

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
