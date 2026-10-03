# -*- coding: utf-8 -*-
"""
M6 回归套件：事件规则编辑 + 开局配置。

时序约定（引擎语义）：事件规则在游戏会话创建时装配，
所以所有测试规则必须在 qa_m6 的第一次游戏 API 调用之前建好（编辑器接口不创建会话）。
"""
from qa.tge_api import (
    GameClient, QaRunner, editor_snapshot, editor_restore, inventory_ids,
)

SUITE = "M6 事件规则/开局配置"

# 六张规则一次性建好（钥匙额外奖励/击杀给药水/进入洞穴/星之城条件/对话节点/购买）
RULES = [
    {
        "id": "qa_key_extra", "label": "QA捡钥匙加金币",
        "on": "ITEM_TAKEN",
        "if": {"item_id": "rusty_key", "from_scene": "forest"},
        "do": [{"type": "set_flag", "flag": "qa_key_flag"},
               {"type": "gold", "amount": 2}],
    },
    {
        "id": "qa_kill_goblin", "label": "QA击杀哥布林给药水",
        "on": "ENEMY_KILLED",
        "if": {"enemy_id": "cave_goblin"},
        "do": [{"type": "give_item", "item": "healing_potion"}],
    },
    {
        "id": "qa_enter_cave", "label": "QA进入洞穴",
        "on": "SCENE_ENTER",
        "if": {"scene_id": "cave"},
        "do": [{"type": "set_flag", "flag": "qa_cave_flag"}],
    },
    {
        "id": "qa_star_rich", "label": "QA带资格进星之城",
        "on": "SCENE_ENTER",
        "if": {"scene_id": "star_city"},
        "when": {"flag": "qa_star_ok"},
        "do": [{"type": "gold", "amount": 5}],
    },
    {
        "id": "qa_sprite_secret", "label": "QA听到森林秘密",
        "on": "NPC_TALK",
        "if": {"npc_id": "npc_2_sprite", "node_id": "secret"},
        "do": [{"type": "set_flag", "flag": "qa_secret"}],
    },
    {
        "id": "qa_bought", "label": "QA药水店消费",
        "on": "ITEM_BOUGHT",
        "if": {"item_id": "healing_potion", "from_scene": "potion_store"},
        "do": [{"type": "set_flag", "flag": "qa_bought"}],
    },
]


def run(r: QaRunner) -> None:
    c = GameClient("qa_m6")
    snap = editor_snapshot(c)
    try:
        # ---------- G1 游戏信息接口 ----------
        r.section("G1 /api/game-info")
        info = c.game_info()
        r.check("default-title", info.get("title") == "酒馆奇遇", info)
        r.check("default-intro", "文字冒险" in info.get("intro", ""), info)

        # ---------- 先建全部测试规则（此时 qa_m6 会话尚未创建） ----------
        r.section("R0 测试规则落盘")
        for rule in RULES:
            res = c.event_rule_save(rule)
            r.check(f"rule-saved-{rule['id']}", res.get("success") is True, res)
        rule_ids = {x["id"] for x in c.editor_data()["config"]["event_rules"]}
        r.check("all-rules-present",
                {x["id"] for x in RULES} <= rule_ids, rule_ids)

        # ---------- R6a 对话节点触发（必须在杀哥布林之前，否则莉娅换问候起点） ----------
        r.section("R6a NPC_TALK 触发器")
        c.reset()
        c.teleport("forest")
        c.dialogue_start("npc_2_sprite")
        dl = c.dialogue()
        r.check("sprite-greet-default", dl["node_id"] == "greet"
                and len(dl["choices"]) == 2, dl.get("choices"))
        c.dialogue_choose(0)  # 询问秘密 → secret 节点，触发 NPC_TALK
        r.check("secret-flag-set",
                c.state().get("flags", {}).get("qa_secret") is True,
                c.state().get("flags"))

        # ---------- R1 ITEM_TAKEN 触发器（控制台发放不触发，场景拾取才触发） ----------
        r.section("R1 ITEM_TAKEN 触发器")
        c.give("rusty_key")  # from_scene=__console__，规则不应命中
        r.check("console-give-no-trigger",
                not c.state().get("flags", {}).get("qa_key_flag"),
                c.state().get("flags"))
        take = c.take("rusty_key")  # forest 地上的钥匙
        flags = c.state().get("flags", {})
        r.check("take-key-success", take.get("success") is True, take)
        r.check("take-key-flag", flags.get("qa_key_flag") is True, flags)
        r.check("take-key-gold-2", c.state().get("player_gold") == 2)

        # ---------- R5 unlock 回归（原钥匙规则，捡钥匙后森林→洞穴锁已开） ----------
        r.section("R5 unlock 效果回归")
        move = c.move("cave")
        r.check("move-to-cave-unlocked", move.get("success") is True, move)

        # ---------- R3 SCENE_ENTER 触发器 ----------
        r.section("R3 SCENE_ENTER 触发器")
        r.check("cave-enter-flag",
                c.state().get("flags", {}).get("qa_cave_flag") is True,
                c.state().get("flags"))

        # ---------- R2 ENEMY_KILLED 触发器 ----------
        r.section("R2 ENEMY_KILLED 触发器")
        c.combat_start("cave_goblin")
        last = c.attack_until("cave_goblin")
        r.check("goblin-killed", last.get("defeated") == "cave_goblin", last)
        r.check("kill-gives-potion",
                inventory_ids(c.state()).count("healing_potion") == 1,
                inventory_ids(c.state()))

        # ---------- R4 when 世界条件 ----------
        r.section("R4 规则的 when 条件")
        c.teleport("star_city")  # 无 qa_star_ok → 不加金币
        r.check("star-no-gold-without-flag",
                c.state().get("player_gold") == 2, c.state().get("player_gold"))
        c.set_flag("qa_star_ok")
        c.teleport("tavern")
        c.teleport("star_city")  # 再进 → 条件满足 +5
        r.check("star-gold-with-flag",
                c.state().get("player_gold") == 7, c.state().get("player_gold"))

        # ---------- R6b ITEM_BOUGHT 触发器 ----------
        r.section("R6b ITEM_BOUGHT 触发器")
        c.teleport("potion_store")
        buy = c.buy("healing_potion")
        r.check("buy-success", buy.get("success") is True, buy)
        r.check("buy-flag-set",
                c.state().get("flags", {}).get("qa_bought") is True,
                c.state().get("flags"))
        r.check("two-potions-now",
                inventory_ids(c.state()).count("healing_potion") == 2,
                inventory_ids(c.state()))

        # ---------- R7 规则校验 + 删除后对新会话失效 ----------
        r.section("R7 校验与热更新边界")
        r.check("bad-on-blocked",
                c.event_rule_save({"id": "qa_bad", "on": "QA_NO_EVENT",
                                   "do": [{"type": "set_flag", "flag": "x"}]}
                                  ).get("success") is False)
        r.check("bad-item-blocked",
                c.event_rule_save({"id": "qa_bad", "on": "ITEM_TAKEN",
                                   "if": {"item_id": "qa_no_item"},
                                   "do": [{"type": "gold", "amount": 1}]}
                                  ).get("success") is False)
        r.check("bad-unlock-blocked",
                c.event_rule_save({"id": "qa_bad", "on": "SCENE_ENTER",
                                   "if": {"scene_id": "forest"},
                                   "do": [{"type": "unlock", "scene": "forest",
                                           "exit": "qa_no_exit"}]}
                                  ).get("success") is False)
        r.check("no-effect-blocked",
                c.event_rule_save({"id": "qa_bad", "on": "SCENE_ENTER",
                                   "if": {"scene_id": "forest"}, "do": []}
                                  ).get("success") is False)
        live_ids = {x["id"] for x in c.editor_data()["config"]["event_rules"]}
        r.check("bad-rule-not-saved", "qa_bad" not in live_ids)

        z_rule = {"id": "qa_z_star", "on": "SCENE_ENTER",
                  "if": {"scene_id": "star_city"},
                  "do": [{"type": "set_flag", "flag": "qa_z"}]}
        r.check("z-rule-saved", c.event_rule_save(z_rule).get("success") is True)
        z1 = GameClient("qa_m6z1")
        z1.reset()
        z1.teleport("star_city")
        r.check("new-rule-fires-new-session",
                z1.state().get("flags", {}).get("qa_z") is True)
        z1.cleanup_saves()
        r.check("z-rule-deleted", c.event_rule_delete("qa_z_star").get("success") is True)
        z2 = GameClient("qa_m6z2")
        z2.reset()
        z2.teleport("star_city")
        r.check("deleted-rule-silent-new-session",
                not z2.state().get("flags", {}).get("qa_z"),
                z2.state().get("flags"))
        z2.cleanup_saves()

        # ---------- G2 开局配置编辑 ----------
        r.section("G2 开局配置生效")
        cfg = dict(snap["config"])
        cfg.update({
            "game_title": "QA测试游戏",
            "game_intro": "QA 用简介",
            "initial_scene": "potion_store",
            "initial_inventory": ["rusty_sword", "healing_potion"],
            "initial_gold": 7,
            "player": {"hp": 42, "attack": 9, "defense": 1},
        })
        res = c.config_save(cfg)
        r.check("config-save-ok", res.get("success") is True, res)
        r.check("game-info-updated", c.game_info().get("title") == "QA测试游戏")
        c.reset()
        st = c.state()
        r.check("initial-scene-applied", st["scene"]["id"] == "potion_store",
                st["scene"]["id"])
        r.check("initial-gold-applied", st.get("player_gold") == 7)
        r.check("initial-inventory-applied",
                inventory_ids(st) == ["rusty_sword", "healing_potion"],
                inventory_ids(st))
        cb = c.combat()
        r.check("player-stats-applied",
                cb["player"]["hp"] == 42 and cb["player"]["max_hp"] == 42
                and cb["player"]["attack"] == 9 and cb["player"]["defense"] == 1,
                cb["player"])

        # ---------- G3 开局配置校验 ----------
        r.section("G3 开局配置校验")
        bad = dict(cfg)
        bad["initial_scene"] = "qa_no_scene"
        r.check("bad-initial-scene-blocked",
                c.config_save(bad).get("success") is False)
        bad = dict(cfg); bad["initial_inventory"] = ["qa_no_item"]
        r.check("bad-initial-item-blocked",
                c.config_save(bad).get("success") is False)
        bad = dict(cfg); bad["initial_gold"] = -1
        r.check("negative-gold-blocked",
                c.config_save(bad).get("success") is False)
        bad = dict(cfg); bad["player"] = {"hp": 0, "attack": 9, "defense": 1}
        r.check("zero-hp-blocked", c.config_save(bad).get("success") is False)
        bad = dict(cfg); bad["game_title"] = ""
        r.check("empty-title-blocked", c.config_save(bad).get("success") is False)
        r.check("config-unchanged-after-fails",
                c.game_info().get("title") == "QA测试游戏")

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
