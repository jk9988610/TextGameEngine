# -*- coding: utf-8 -*-
# M9 QA：世界地图画布（场景 layouts 坐标通道）
#
# 运行环境约定（沿用旧冒烟的 SKIP 惯例）：
#   - 纯函数段：EditorManager + 临时目录，不依赖工程数据，任何工程下都跑；
#   - HTTP 段：依赖编辑器 API 开放（线上 ONLINE_MODE=online 时 403 → 自动跳过）。
#
# 画布语义约定：
#   - 坐标是辅助数据（scene_layouts.json，引擎不读），只校验 ID/整数/范围；
#   - 场景删除时顺带清理该场景的坐标（delete_scene 内联清理）；
#   - 出口/锁定改动走既有 /api/editor/scene（upsert_scene 校验），不在此重复测。
"""世界地图画布套件（scene_layouts 通道）。"""
import os
import tempfile
import uuid

from qa.tge_api import GameClient
from engine.editor_manager import EditorManager

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
SUITE = "M9 世界地图画布（scene_layouts）"

TMP_SCENE = "qa_map_tmp"
TMP_SCENE_PAYLOAD = {
    "id": TMP_SCENE,
    "name": "QA地图临时地点",
    "description": "测完即删",
    "exits": [],
    "items_here": [],
    "locked_exits": {},
    "enemies_here": [],
    "shop_items": [],
}


def run_unit(r):
    """纯函数段：EditorManager 场景布局读写 + 删除清理（临时目录，不碰工程数据）。"""
    r.section("U1 布局读写")
    with tempfile.TemporaryDirectory() as tmp:
        em = EditorManager(data_dir=tmp)
        r.check("layouts-empty-on-missing", em.load_scene_layouts() == {},
                em.load_scene_layouts())
        ok = em.save_scene_layout("cave", {"x": 120, "y": -80})
        r.check("save-valid", ok.get("success")
                and em.load_scene_layouts() == {"cave": {"x": 120, "y": -80}}, ok)
        again = em.save_scene_layout("cave", {"x": 0, "y": 56})
        r.check("save-overwrite", again.get("success")
                and em.load_scene_layouts()["cave"] == {"x": 0, "y": 56}, again)
        second = em.save_scene_layout("village", {"x": 300, "y": 60})
        r.check("save-second-scene", second.get("success")
                and set(em.load_scene_layouts()) == {"cave", "village"}, second)

        r.section("U2 非法输入拦截")
        r.check("bad-id-uppercase",
                em.save_scene_layout("Cave", {"x": 1, "y": 1}).get("success") is False)
        r.check("bad-id-chinese",
                em.save_scene_layout("洞穴", {"x": 1, "y": 1}).get("success") is False)
        r.check("bad-id-empty",
                em.save_scene_layout("", {"x": 1, "y": 1}).get("success") is False)
        r.check("bad-coord-str",
                em.save_scene_layout("cave", {"x": "abc", "y": 1}).get("success") is False)
        r.check("bad-coord-missing",
                em.save_scene_layout("cave", {"y": 1}).get("success") is False)
        r.check("bad-coord-empty",
                em.save_scene_layout("cave", {}).get("success") is False)
        r.check("bad-coord-range",
                em.save_scene_layout("cave", {"x": 99999, "y": 0}).get("success") is False)
        r.check("bad-inputs-no-side-effect",
                em.load_scene_layouts()["cave"] == {"x": 0, "y": 56},
                em.load_scene_layouts())

        r.section("U3 删除场景顺带清理坐标")
        scenes = {
            "cave": {"id": "cave", "name": "洞穴", "description": "d",
                     "exits": [], "items_here": [], "locked_exits": {},
                     "enemies_here": [], "shop_items": []},
            "keep": {"id": "keep", "name": "村口", "description": "d",
                     "exits": [], "items_here": [], "locked_exits": {},
                     "enemies_here": [], "shop_items": []},
        }
        out = em.delete_scene(scenes, {}, "cave", initial_scene="")
        r.check("delete-ok", out.get("success") and "cave" not in scenes, out)
        r.check("layout-pruned", set(em.load_scene_layouts()) == {"village"},
                em.load_scene_layouts())


def run_http(r):
    """HTTP 段：编辑器 API 开放时验证端到端链路（临时场景 + 坐标通道）。"""
    c = GameClient(client_id="qa_m9_" + uuid.uuid4().hex[:10], base=BASE)
    saved = c.editor_save("scene", TMP_SCENE_PAYLOAD)
    r.check("tmp-scene-created", saved.get("success") is True, saved)
    try:
        r.section("H1 数据端点暴露布局通道")
        data = c.editor_data()
        r.check("data-has-scene-layouts", isinstance(data.get("scene_layouts"), dict),
                type(data.get("scene_layouts")))

        r.section("H2 坐标落盘与回读")
        pos = {"x": 84, "y": 140}
        out = c.scene_layout_save(TMP_SCENE, pos)
        r.check("layout-saved", out.get("success") is True, out)
        back = c.editor_data().get("scene_layouts", {}).get(TMP_SCENE)
        r.check("layout-roundtrip", back == pos, back)

        r.section("H3 非法输入拦截")
        r.check("http-bad-id",
                c.scene_layout_save("QA Bad", pos).get("success") is False)
        r.check("http-bad-coord-str",
                c.scene_layout_save(TMP_SCENE, {"x": "abc", "y": 1}).get("success") is False)
        r.check("http-bad-coord-range",
                c.scene_layout_save(TMP_SCENE, {"x": 99999, "y": 0}).get("success") is False)
        r.check("http-no-side-effect",
                c.editor_data().get("scene_layouts", {}).get(TMP_SCENE) == pos)

        r.section("H4 删除场景清理坐标")
        gone = c.editor_delete("scene", TMP_SCENE)
        r.check("scene-deleted", gone.get("success") is True, gone)
        r.check("layout-gone-with-scene",
                TMP_SCENE not in (c.editor_data().get("scene_layouts") or {}),
                c.editor_data().get("scene_layouts"))
    finally:
        # 兜底清理：临时场景删成功时坐标已被内联清理；失败时也要尽量还原
        if TMP_SCENE in c.editor_data().get("scenes", {}):
            c.editor_delete("scene", TMP_SCENE)


def run(r):
    run_unit(r)
    probe = GameClient(client_id="qa_m9_probe_" + uuid.uuid4().hex[:8], base=BASE)
    if probe.editor_data().get("success"):
        run_http(r)
    else:
        print("（编辑器 API 不可用（线上模式/403）：HTTP 段跳过，仅跑纯函数段）")
