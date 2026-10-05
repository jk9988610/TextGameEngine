# -*- coding: utf-8 -*-
# M10 QA：编辑器偏好文件通道（editor_settings.json）
#
# 约定：
#   - 偏好是辅助数据（自动保存/收起键），存 editor_settings.json，引擎不读；
#   - 白名单仅 collapse∈middle/right、autosave 布尔，未知键/非法值拒绝；
#   - 纯函数段任何工程都跑；HTTP 段编辑器 API 不可用时自动跳过。
"""编辑器设置套件（editor_settings 通道）。"""
import json
import os
import tempfile
import uuid

from qa.tge_api import GameClient, editor_snapshot, editor_restore
from engine.editor_manager import EditorManager

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
SUITE = "M10 编辑器设置（editor_settings）"

DEFAULTS = {"collapse": "middle", "autosave": False}


def run_unit(r):
    """纯函数段：EditorManager 偏好读写 + 白名单清洗（临时目录）。"""
    r.section("U1 默认值与落盘回读")
    with tempfile.TemporaryDirectory() as tmp:
        em = EditorManager(data_dir=tmp)
        r.check("defaults-on-missing", em.load_editor_settings() == DEFAULTS,
                em.load_editor_settings())
        ok = em.save_editor_settings({"collapse": "right", "autosave": True})
        r.check("save-valid", ok.get("success")
                and ok.get("settings") == {"collapse": "right", "autosave": True}, ok)
        r.check("roundtrip", em.load_editor_settings() == {"collapse": "right", "autosave": True},
                em.load_editor_settings())
        # 落盘文件内容就是白名单两键，无额外字段
        with open(os.path.join(tmp, "editor_settings.json"), encoding="utf-8") as f:
            on_disk = json.load(f)
        r.check("on-disk-whitelist-only", set(on_disk) == {"collapse", "autosave"}, on_disk)

        r.section("U2 合并保存（只传部分键）")
        em.save_editor_settings({"autosave": False})
        r.check("partial-merge-keeps-collapse",
                em.load_editor_settings() == {"collapse": "right", "autosave": False})

        r.section("U3 非法输入拦截")
        r.check("bad-collapse",
                em.save_editor_settings({"collapse": "left"}).get("success") is False)
        r.check("bad-autosave-type",
                em.save_editor_settings({"autosave": "yes"}).get("success") is False)
        r.check("bad-payload-list",
                em.save_editor_settings(["right"]).get("success") is False)
        r.check("rejected-no-side-effect",
                em.load_editor_settings() == {"collapse": "right", "autosave": False},
                em.load_editor_settings())

        r.section("U4 损坏文件回默认")
        with open(os.path.join(tmp, "editor_settings.json"), "w", encoding="utf-8") as f:
            f.write("{ broken json")
        r.check("corrupt-falls-back", em.load_editor_settings() == DEFAULTS,
                em.load_editor_settings())


def run_http(r):
    """HTTP 段：GET data 暴露偏好；POST 合并落盘、回读、非法拦截（快照还原）。"""
    c = GameClient(client_id="qa_m10_" + uuid.uuid4().hex[:10], base=BASE)
    snap = editor_snapshot(c)
    try:
        r.section("H1 数据端点暴露设置通道")
        data = c.editor_data()
        r.check("data-has-editor-settings",
                isinstance(data.get("editor_settings"), dict)
                and set(data["editor_settings"]) == {"collapse", "autosave"},
                data.get("editor_settings"))

        r.section("H2 设置落盘与回读")
        out = c.editor_settings_save({"collapse": "right", "autosave": True})
        r.check("settings-saved", out.get("success") is True
                and out.get("settings") == {"collapse": "right", "autosave": True}, out)
        r.check("settings-roundtrip",
                c.editor_settings_get() == {"collapse": "right", "autosave": True},
                c.editor_settings_get())
        # 部分键合并
        c.editor_settings_save({"autosave": False})
        r.check("http-partial-merge",
                c.editor_settings_get() == {"collapse": "right", "autosave": False},
                c.editor_settings_get())

        r.section("H3 非法输入拦截")
        r.check("http-bad-collapse",
                c.editor_settings_save({"collapse": "up"}).get("success") is False)
        r.check("http-bad-autosave",
                c.editor_settings_save({"autosave": 1}).get("success") is False)
        r.check("http-no-side-effect",
                c.editor_settings_get() == {"collapse": "right", "autosave": False},
                c.editor_settings_get())
    finally:
        editor_restore(c, snap)
        c.cleanup_saves()


def run(r):
    run_unit(r)
    probe = GameClient(client_id="qa_m10_probe_" + uuid.uuid4().hex[:8], base=BASE)
    if probe.editor_data().get("success"):
        run_http(r)
    else:
        print("（编辑器 API 不可用（线上模式/403）：HTTP 段跳过，仅跑纯函数段）")
