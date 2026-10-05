# -*- coding: utf-8 -*-
# M12 QA：角色合并（characters.json 为权威数据，enemies / npcs 为派生视图）
#
# 约定：
#   - 引擎只有一个"人物"概念：角色（characters.json）；
#   - 贴 enemy 标签即可战斗（hp/attack/defense/reward_items/reward_gold 由标签字段组提供），
#     贴 talkable 标签即可对话（scene_id + greeting/greeting_rules/nodes）；
#   - 两个标签可同时贴；运行时 app.py 从 characters 派生 enemies / npcs 两个视图，
#     引擎核心（combat_system / npc_system）零改动；
#   - 复杂结构（Beat 配置 / 对话树）不走标签字段机制，只在贴了对应标签时才清洗与校验。
"""角色合并套件（characters.json + 派生 enemies/npcs 视图）。"""
import os
import shutil
import tempfile
import uuid

from qa.tge_api import GameClient, editor_snapshot, editor_restore
from engine.editor_manager import EditorManager

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
SUITE = "M12 角色合并（characters.json + 派生视图）"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAGS_SRC = os.path.join(REPO_ROOT, "game_data", "tags.json")

CHAR_ID = "qa_probe"

# 纯函数段用的最小上下文：对话要落地点、掉落要落物品，校验会查引用
UNIT_SCENES = {"village": {"id": "village", "name": "村庄", "description": "测试用"}}
UNIT_ITEMS = {"gold_coin": {"id": "gold_coin", "name": "金币", "description": "测试用"}}

# 既会打又会聊的角色：enemy 字段组 + talkable 对话树 + Beat 配置
COMBAT_CHAR = {
    "id": CHAR_ID, "name": "QA探针", "description": "测试用角色，别当真。",
    "tags": ["enemy", "talkable"],
    "hp": 11, "attack": 2, "defense": 1,
    "reward_items": ["gold_coin"], "reward_gold": 3,
    "heavy_attack": 7,
    "brain": {"rhythm": ["bash", "charge"], "low_hp_mode": "frenzy", "low_hp_ratio": 0.3},
    "scene_id": "village", "greeting": "greet", "greeting_rules": [],
    "nodes": {"greet": {"text": "你好，旅人。",
                        "choices": [{"text": "再见"}]}},
}


def _mk(tmp):
    """临时目录里建一个 EditorManager（tags 用仓库里的真货）。"""
    if os.path.exists(TAGS_SRC):
        shutil.copy(TAGS_SRC, os.path.join(tmp, "tags.json"))
    em = EditorManager(data_dir=tmp)
    return em, em.load_tags()


def run_unit(r):
    """纯函数段：角色 CRUD + 标签字段组 + 派生视图素材 + 引用保护。"""
    r.section("U1 角色 CRUD（enemy 字段组平铺 + Beat / 对话树保留）")
    with tempfile.TemporaryDirectory() as tmp:
        em, tags = _mk(tmp)
        characters = {}
        out = em.upsert_character(characters, UNIT_SCENES, UNIT_ITEMS, tags,
                                  dict(COMBAT_CHAR))
        r.check("character-created", out.get("success") is True, out)
        got = characters.get(CHAR_ID, {})
        r.check("base-fields",
                got.get("name") == "QA探针" and got.get("description") == "测试用角色，别当真。",
                got)
        r.check("tags-saved", got.get("tags") == ["enemy", "talkable"], got.get("tags"))
        r.check("enemy-fields-flat",
                got.get("hp") == 11 and got.get("attack") == 2 and got.get("defense") == 1
                and got.get("reward_items") == ["gold_coin"] and got.get("reward_gold") == 3,
                got)
        r.check("beat-structure-kept",
                got.get("heavy_attack") == 7
                and got.get("brain", {}).get("rhythm") == ["bash", "charge"]
                and got.get("brain", {}).get("low_hp_ratio") == 0.3, got.get("brain"))
        r.check("dialogue-structure-kept",
                got.get("greeting") == "greet" and "greet" in (got.get("nodes") or {}), got)

        r.section("U2 标签取舍：未贴的标签不落字段 / 不落结构")
        chars2 = {}
        em.upsert_character(chars2, UNIT_SCENES, UNIT_ITEMS, tags,
                            {"id": "qa_talker", "name": "QA说书人", "description": "只聊天",
                             "tags": ["talkable"], "hp": 999, "scene_id": "village",
                             "greeting": "greet", "nodes": {"greet": {"text": "嗯。", "choices": []}}})
        t = chars2["qa_talker"]
        r.check("enemy-fields-absent", "hp" not in t and "reward_gold" not in t, t)
        r.check("talkable-fields-present", "greeting" in t and "nodes" in t, t)

        chars3 = {}
        em.upsert_character(chars3, UNIT_SCENES, UNIT_ITEMS, tags,
                            {"id": "qa_dummy", "name": "QA木桩", "description": "只会挨打",
                             "tags": ["enemy"], "hp": 5, "attack": 1, "defense": 0,
                             "greeting": "greet", "nodes": {"greet": {"text": "无", "choices": []}}})
        d = chars3["qa_dummy"]
        r.check("dialogue-structure-absent",
                "nodes" not in d and "greeting" not in d, d)
        r.check("enemy-fields-present", d.get("hp") == 5, d)

        r.section("U3 非法输入拦截")
        bad = [
            ("empty-description",
             {**COMBAT_CHAR, "id": "qa_bad", "description": "  "}),
            ("unknown-tag",
             {**COMBAT_CHAR, "id": "qa_bad", "tags": ["nope"]}),
            ("wrong-target-tag",
             {**COMBAT_CHAR, "id": "qa_bad", "tags": ["weapon"]}),
            ("hp-out-of-range",
             {**COMBAT_CHAR, "id": "qa_bad", "hp": 0}),
            ("reward-item-missing",
             {**COMBAT_CHAR, "id": "qa_bad", "reward_items": ["no_such_item"]}),
            ("bad-rhythm-action",
             {**COMBAT_CHAR, "id": "qa_bad", "brain": {"rhythm": ["dance"]}}),
            ("bad-choice-next",
             {**COMBAT_CHAR, "id": "qa_bad",
              "nodes": {"greet": {"text": "嗨", "choices": [{"text": "走", "next": "ghost"}]}}}),
        ]
        for name, payload in bad:
            r.check(name + "-blocked",
                    em.upsert_character({}, UNIT_SCENES, UNIT_ITEMS, tags, payload)
                    .get("success") is False, payload.get("id"))

        r.section("U4 标签引用保护（角色也算贴用者）")
        chars4 = {CHAR_ID: dict(COMBAT_CHAR)}
        r.check("tag-used-by-character-blocked",
                em.delete_tag(tags, {}, chars4, "enemy").get("success") is False)
        r.check("tag-free-deletable",
                em.delete_tag(tags, {}, {}, "enemy").get("success") is True)

        r.section("U5 删除角色：场景引用 / 战斗中 / 对话中都要拦")
        chars5 = {CHAR_ID: dict(COMBAT_CHAR)}
        scenes5 = {"forest": {"id": "forest", "enemies_here": [CHAR_ID]}}
        r.check("scene-reference-blocked",
                em.delete_character(scenes5, chars5, CHAR_ID).get("success") is False)
        r.check("live-battle-blocked",
                em.delete_character({"forest": {"enemies_here": []}}, chars5, CHAR_ID,
                                    live_battle_ids={CHAR_ID}).get("success") is False)
        r.check("live-dialogue-blocked",
                em.delete_character({"forest": {"enemies_here": []}}, chars5, CHAR_ID,
                                    live_dialogue_ids={CHAR_ID}).get("success") is False)
        ok = em.delete_character({"forest": {"enemies_here": []}}, chars5, CHAR_ID)
        r.check("delete-ok-after-unreferenced", ok.get("success") is True, ok)
        r.check("character-gone", CHAR_ID not in chars5)


def run_http(r):
    """HTTP 段：角色端点 + 派生视图（enemies/npcs）+ 引用保护（快照还原）。"""
    c = GameClient(client_id="qa_m12_" + uuid.uuid4().hex[:10], base=BASE)
    snap = editor_snapshot(c)
    try:
        r.section("H1 数据端点同时暴露 characters 与派生 enemies/npcs")
        data = c.editor_data()
        r.check("data-has-characters", isinstance(data.get("characters"), dict),
                type(data.get("characters")))
        r.check("data-has-derived-views",
                isinstance(data.get("enemies"), dict) and isinstance(data.get("npcs"), dict))
        # 权威数据里每个敌人 / 可对话角色都能在 characters 里找到
        r.check("views-are-derivations",
                set(data["enemies"]) <= set(data["characters"])
                and set(data["npcs"]) <= set(data["characters"]),
                (sorted(data["enemies"]), sorted(data["npcs"]), sorted(data["characters"])))

        r.section("H2 新建双标签角色 → 同时出现在 enemies 与 npcs")
        out = c.editor_save("character", dict(COMBAT_CHAR))
        r.check("http-character-saved", out.get("success") is True, out)
        data = c.editor_data()
        got = data["characters"].get(CHAR_ID, {})
        r.check("http-fields-flat", got.get("hp") == 11 and got.get("reward_gold") == 3, got)
        r.check("http-in-enemies", CHAR_ID in data["enemies"], sorted(data["enemies"]))
        r.check("http-in-npcs", CHAR_ID in data["npcs"], sorted(data["npcs"]))

        r.section("H3 摘掉 enemy 标签 → 派生的 enemies 视图立刻不含它")
        talker = dict(COMBAT_CHAR)
        talker["tags"] = ["talkable"]
        out = c.editor_save("character", talker)
        r.check("http-retag-ok", out.get("success") is True, out)
        data = c.editor_data()
        r.check("http-left-enemies", CHAR_ID not in data["enemies"], sorted(data["enemies"]))
        r.check("http-still-in-npcs", CHAR_ID in data["npcs"], sorted(data["npcs"]))
        r.check("http-enemy-fields-dropped", "hp" not in data["characters"][CHAR_ID],
                data["characters"][CHAR_ID])

        r.section("H4 非法输入拦截")
        r.check("http-empty-description",
                c.editor_save("character", {**COMBAT_CHAR, "id": "qa_bad",
                                            "description": ""}).get("success") is False)
        r.check("http-wrong-target-tag",
                c.editor_save("character", {**COMBAT_CHAR, "id": "qa_bad",
                                            "tags": ["weapon"]}).get("success") is False)
        r.check("http-bad-rhythm",
                c.editor_save("character", {**COMBAT_CHAR, "id": "qa_bad",
                                            "brain": {"rhythm": ["dance"]}}
                              ).get("success") is False)
        r.check("no-bad-side-effect",
                "qa_bad" not in (c.editor_data()["characters"] or {}))

        r.section("H5 删除保护：场景出没引用 / 实时战斗")
        back = dict(COMBAT_CHAR)
        back["tags"] = ["enemy"]
        c.editor_save("character", back)
        scene_id = "forest" if "forest" in c.editor_data()["scenes"] else \
            sorted(c.editor_data()["scenes"])[0]
        scene = dict(c.editor_data()["scenes"][scene_id])
        original_here = list(scene.get("enemies_here") or [])
        scene["enemies_here"] = original_here + [CHAR_ID]
        r.check("scene-save-ok", c.editor_save("scene", scene).get("success") is True)
        r.check("http-scene-reference-blocked",
                c.editor_delete("character", CHAR_ID).get("success") is False)
        scene["enemies_here"] = original_here
        c.editor_save("scene", scene)
        r.check("http-delete-ok",
                c.editor_delete("character", CHAR_ID).get("success") is True)
        r.check("http-gone-from-views",
                CHAR_ID not in (c.editor_data().get("enemies") or {}))
    finally:
        editor_restore(c, snap)
        c.cleanup_saves()


def run(r):
    run_unit(r)
    probe = GameClient(client_id="qa_m12_probe_" + uuid.uuid4().hex[:8], base=BASE)
    if probe.editor_data().get("success"):
        run_http(r)
    else:
        print("（编辑器 API 不可用（线上模式/403）：HTTP 段跳过，仅跑纯函数段）")
