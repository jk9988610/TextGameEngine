# -*- coding: utf-8 -*-
# M11 QA：标签驱动字段组（tags.json 通道）
#
# 约定：
#   - 标签 = 目标类型(item/scene/character) + 字段组，定义存 tags.json；
#   - 贴了标签的对象把该标签的字段平铺存在自己身上（物品：tags + damage/heal/...）；
#   - 标签可带 runtime.flag，保存时派生旧键（is_weapon/usable），引擎核心不用认识"标签"；
#   - 仍被对象贴用的标签禁止删除；字段值按标签规格校验（int 区间 / item_list 引用）。
"""标签驱动字段组套件（tags.json 通道）。"""
import json
import os
import shutil
import tempfile
import uuid

from qa.tge_api import GameClient, editor_snapshot, editor_restore
from engine.editor_manager import EditorManager

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
SUITE = "M11 标签驱动字段组（tags.json）"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAGS_SRC = os.path.join(REPO_ROOT, "game_data", "tags.json")

NEW_TAG = {
    "id": "qa_sharp",
    "label": "QA锋利",
    "target": "item",
    "fields": [{"key": "qa_bonus", "label": "额外伤害", "type": "int",
                "default": 2, "min": 0}],
    "runtime": {"flag": "is_sharp"},
}


def run_unit(r):
    """纯函数段：EditorManager 标签读写 + 字段组归一 + 引用保护（临时目录）。"""
    r.section("U1 标签定义读写")
    with tempfile.TemporaryDirectory() as tmp:
        if os.path.exists(TAGS_SRC):
            shutil.copy(TAGS_SRC, os.path.join(tmp, "tags.json"))
        em = EditorManager(data_dir=tmp)
        tags = em.load_tags()
        r.check("tags-loaded", isinstance(tags, dict) and len(tags) > 0, list(tags))

        ok = em.upsert_tag(tags, NEW_TAG)
        r.check("tag-created", ok.get("success") is True, ok)
        r.check("tag-roundtrip", tags.get("qa_sharp", {}).get("runtime") ==
                {"flag": "is_sharp"}, tags.get("qa_sharp"))

        r.section("U2 字段组归一与运行时派生键")
        items = {}
        res = em.upsert_item({}, items, {}, {}, tags,
                             {"id": "qa_dagger", "name": "QA匕首", "description": "测试用",
                              "tags": ["weapon", "qa_sharp"], "damage": 6, "qa_bonus": 3})
        r.check("item-saved", res.get("success") is True, res)
        got = items.get("qa_dagger", {})
        r.check("flat-fields", got.get("damage") == 6 and got.get("qa_bonus") == 3, got)
        r.check("derived-runtime-flags",
                got.get("is_weapon") is True and got.get("is_sharp") is True, got)
        # 未勾的标签，其字段不应出现在数据里
        em.upsert_item({}, items, {}, {}, tags,
                       {"id": "qa_plain", "name": "QA素件", "description": "测试用",
                        "tags": ["weapon"], "damage": 1})
        r.check("untagged-fields-absent",
                "qa_bonus" not in items["qa_plain"] and "is_sharp" not in items["qa_plain"],
                items["qa_plain"])

        r.section("U3 非法输入拦截")
        r.check("unknown-tag-blocked",
                em.upsert_item({}, items, {}, {}, tags,
                               {"id": "qa_bad", "name": "x", "description": "y",
                                "tags": ["nope"]}).get("success") is False)
        r.check("field-min-blocked",
                em.upsert_item({}, items, {}, {}, tags,
                               {"id": "qa_bad", "name": "x", "description": "y",
                                "tags": ["weapon"], "damage": -1}).get("success") is False)
        r.check("bad-tag-id-blocked",
                em.upsert_tag({}, {"id": "Bad ID", "label": "坏", "target": "item",
                                   "fields": []}).get("success") is False)
        r.check("bad-target-blocked",
                em.upsert_tag({}, {"id": "qa_x", "label": "坏", "target": "monster",
                                   "fields": []}).get("success") is False)
        r.check("bad-field-type-blocked",
                em.upsert_tag({}, {"id": "qa_x", "label": "坏", "target": "item",
                                   "fields": [{"key": "k", "type": "magic"}]}
                              ).get("success") is False)
        r.check("no-side-effect", "qa_bad" not in items and "qa_x" not in tags, list(tags))

        r.section("U4 引用保护")
        r.check("tag-in-use-blocked",
                em.delete_tag(tags, items, {}, "qa_sharp").get("success") is False)
        del items["qa_dagger"]
        r.check("tag-free-deletable",
                em.delete_tag(tags, items, {}, "qa_sharp").get("success") is True)


def run_http(r):
    """HTTP 段：标签端点 CRUD + 物品按标签存字段（快照还原）。"""
    c = GameClient(client_id="qa_m11_" + uuid.uuid4().hex[:10], base=BASE)
    snap = editor_snapshot(c)
    try:
        r.section("H1 数据端点暴露标签")
        data = c.editor_data()
        r.check("data-has-tags", isinstance(data.get("tags"), dict), type(data.get("tags")))

        r.section("H2 标签 CRUD")
        out = c.editor_save("tag", NEW_TAG)
        r.check("http-tag-created", out.get("success") is True, out)
        r.check("http-tag-visible",
                "qa_sharp" in (c.editor_data().get("tags") or {}),
                list(c.editor_data().get("tags") or {}))

        r.section("H3 物品按标签存字段 + 派生运行时键")
        item = {"id": "qa_tagged", "name": "QA标签物品", "description": "测试用",
                "tags": ["weapon", "qa_sharp"], "damage": 7, "qa_bonus": 4}
        out = c.editor_save("item", item)
        r.check("http-item-saved", out.get("success") is True, out)
        got = (c.editor_data().get("items") or {}).get("qa_tagged", {})
        r.check("http-flat-fields", got.get("damage") == 7 and got.get("qa_bonus") == 4, got)
        r.check("http-derived-is-weapon", got.get("is_weapon") is True, got)

        r.section("H4 拦截与清理")
        r.check("http-unknown-tag",
                c.editor_save("item", {**item, "id": "qa_bad", "tags": ["nope"]}
                              ).get("success") is False)
        r.check("http-tag-in-use",
                c.editor_delete("tag", "qa_sharp").get("success") is False)
    finally:
        editor_restore(c, snap)
        c.cleanup_saves()


def run(r):
    run_unit(r)
    probe = GameClient(client_id="qa_m11_probe_" + uuid.uuid4().hex[:8], base=BASE)
    if probe.editor_data().get("success"):
        run_http(r)
    else:
        print("（编辑器 API 不可用（线上模式/403）：HTTP 段跳过，仅跑纯函数段）")
