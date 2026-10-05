# -*- coding: utf-8 -*-
"""
M13 回归套件：事件字典（P2 槽位开放）。

引擎只认「槽位」，可用条目（触发器/条件/效果）全部来自数据侧词汇表
game_data/events.json；条目用 atom 字段复用引擎原子，因此可以换标签、改参数，
甚至登记全新的自定义条目。本套件验证：

  D1  /api/editor/data 暴露事件字典，条目结构符合约定
  D2  自定义条目复用原子（lose_gold = gold 负值、rich = gold_gte）+ 热更新生效
  D3  触发器清单完全由字典决定（登记即可选，无发布点则静默不触发）
  D4  字典写坏（原子不存在 / 参数不配对）会被拦住，且范围校验来自字典

注意：本套件会临时改写 events.json，finally 里按原文（含格式）还原。
"""
import json
import os

from qa.tge_api import (
    GameClient, QaRunner, editor_snapshot, editor_restore,
)

SUITE = "M13 事件字典"

_EVENTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "game_data", "events.json")

# ---- 自定义条目：只换标签与参数，行为复用引擎原子 ----
CUSTOM_EFFECT = {
    "key": "lose_gold", "label": "扣除金币", "atom": "gold",
    "params": [{"field": "amount", "label": "扣除量", "type": "int",
                "min": -99999, "max": -1, "required": True}],
}
CUSTOM_COND = {
    "key": "rich", "label": "金币达标", "atom": "gold_gte",
    "params": [{"field": "rich", "label": "金币门槛", "type": "int",
                "min": 0, "required": True}],
}
CUSTOM_TRIGGER = {
    "key": "QA_CUSTOM_EVENT", "label": "QA自定义事件",
    "params": [{"field": "flag", "label": "标志名", "type": "text",
                "required": False}],
}
# ---- 坏条目：原子不存在 / 参数没覆盖原子所需字段 ----
BAD_ATOM_EFFECT = {"key": "explode", "label": "爆炸"}
BAD_PARAM_EFFECT = {
    "key": "juice", "label": "果汁", "atom": "heal",
    "params": [{"field": "amount_x", "label": "量", "type": "int",
                "required": True}],
}

RULES = [
    {"id": "qa_m13_rich_high", "label": "QA金币门槛未达", "on": "SCENE_ENTER",
     "if": {"scene_id": "forest"}, "when": {"rich": 1000},
     "do": [{"type": "set_flag", "flag": "qa_m13_high"}]},
    {"id": "qa_m13_rich_low", "label": "QA金币门槛已达", "on": "SCENE_ENTER",
     "if": {"scene_id": "cave"}, "when": {"rich": 0},
     "do": [{"type": "set_flag", "flag": "qa_m13_low"}]},
    {"id": "qa_m13_lose", "label": "QA先加后扣金币", "on": "SCENE_ENTER",
     "if": {"scene_id": "forest"},
     "do": [{"type": "gold", "amount": 8},
            {"type": "lose_gold", "amount": -5}]},
]
CUSTOM_TRIGGER_RULE = {
    "id": "qa_m13_dead_event", "label": "QA无发布点的自定义触发器",
    "on": "QA_CUSTOM_EVENT",
    "do": [{"type": "set_flag", "flag": "qa_m13_dead"}],
}


def _write_text(text: str) -> None:
    with open(_EVENTS_PATH, "w", encoding="utf-8") as f:
        f.write(text)


def _edit_dict(mutate) -> dict:
    """读当前字典 → mutate → 写回，返回写回的字典。"""
    with open(_EVENTS_PATH, encoding="utf-8") as f:
        ev = json.load(f)
    mutate(ev)
    _write_text(json.dumps(ev, ensure_ascii=False, indent=2))
    return ev


def run(r: QaRunner) -> None:
    c = GameClient("qa_m13")
    snap = editor_snapshot(c)
    with open(_EVENTS_PATH, encoding="utf-8") as f:
        original = f.read()

    try:
        # ---------- D1 字典接口 ----------
        r.section("D1 /api/editor/data 暴露事件字典")
        ev = c.editor_data().get("events") or {}
        trig = ev.get("triggers") or []
        cond = ev.get("conditions") or []
        eff = ev.get("effects") or []
        r.check("triggers-7", len(trig) == 7, [t.get("key") for t in trig])
        r.check("conditions-4", len(cond) == 4, [t.get("key") for t in cond])
        r.check("effects-9", len(eff) == 9, [t.get("key") for t in eff])
        r.check("trigger-labels",
                all(t.get("key") and t.get("label") and t.get("params")
                    for t in trig), trig)
        r.check("condition-keys-declared",
                all(any(p.get("field") == e.get("key")
                        for p in (e.get("params") or [])) for e in cond),
                [e.get("key") for e in cond])
        r.check("params-have-field-type",
                all(p.get("field") and p.get("type")
                    for e in trig + cond + eff for p in (e.get("params") or [])))

        # ---------- D2 自定义条目复用原子 + 热更新 ----------
        r.section("D2 自定义条目（复用原子）+ 热更新")
        _edit_dict(lambda d: (d["triggers"].append(CUSTOM_TRIGGER),
                              d["conditions"].append(CUSTOM_COND),
                              d["effects"].append(CUSTOM_EFFECT)))
        ev2 = c.editor_data().get("events") or {}
        r.check("hot-reload-trigger",
                "QA_CUSTOM_EVENT" in [x["key"] for x in ev2["triggers"]])
        r.check("hot-reload-condition",
                "rich" in [x["key"] for x in ev2["conditions"]])
        r.check("hot-reload-effect",
                "lose_gold" in [x["key"] for x in ev2["effects"]])

        for rule in RULES:
            res = c.event_rule_save(rule)
            r.check(f"saved-{rule['id']}", res.get("success") is True, res)
        # 整数范围来自字典条目（lose_gold: max = -1）
        bad = c.event_rule_save({
            "id": "qa_m13_bad", "label": "QA扣除量越界", "on": "SCENE_ENTER",
            "if": {"scene_id": "forest"},
            "do": [{"type": "lose_gold", "amount": 5}]})
        r.check("dict-int-range-blocked", bad.get("success") is False, bad)
        # 自定义触发器条目登记后即可选用（引擎当前没有它的发布点）
        r.check("custom-trigger-rule-saved",
                c.event_rule_save(CUSTOM_TRIGGER_RULE).get("success") is True)

        # ---------- D2b 运行时：自定义条件/效果真的生效 ----------
        r.section("D2b 运行时：自定义条件/效果生效")
        c.reset()
        base = c.state().get("player_gold")
        r.check("base-gold-read", isinstance(base, int) and base < 1000, base)
        c.teleport("forest")            # R1 条件不满足（跳过）；R3 自定义效果生效
        r.check("custom-cond-not-met",
                not c.state().get("flags", {}).get("qa_m13_high"),
                c.state().get("flags"))
        r.check("custom-effect-applied",
                c.state().get("player_gold") == base + 3,
                c.state().get("player_gold"))
        c.teleport("cave")              # R2 自定义条件满足
        r.check("custom-cond-met",
                c.state().get("flags", {}).get("qa_m13_low") is True,
                c.state().get("flags"))

        # ---------- D3 触发器清单完全由字典决定 ----------
        r.section("D3 无发布点的自定义触发器静默不触发")
        t = GameClient("qa_m13t")
        t.reset()
        t.teleport("forest")
        t.teleport("tavern")
        r.check("dead-trigger-silent",
                not t.state().get("flags", {}).get("qa_m13_dead"),
                t.state().get("flags"))
        t.cleanup_saves()

        # ---------- D4 字典写坏会被拦住 ----------
        r.section("D4 字典条目自检")
        _edit_dict(lambda d: (d["effects"].extend(
            [BAD_ATOM_EFFECT, BAD_PARAM_EFFECT])))
        res = c.event_rule_save({
            "id": "qa_m13_x", "label": "QA未知效果", "on": "SCENE_ENTER",
            "if": {"scene_id": "forest"}, "do": [{"type": "no_such_effect"}]})
        r.check("unknown-effect-blocked", res.get("success") is False
                and "未知效果类型" in res.get("message", ""), res)
        res = c.event_rule_save({
            "id": "qa_m13_x", "label": "QA坏原子", "on": "SCENE_ENTER",
            "if": {"scene_id": "forest"}, "do": [{"type": "explode"}]})
        r.check("bad-atom-blocked", res.get("success") is False
                and "原子不存在" in res.get("message", ""), res)
        res = c.event_rule_save({
            "id": "qa_m13_x", "label": "QA参数不配对", "on": "SCENE_ENTER",
            "if": {"scene_id": "forest"}, "do": [{"type": "juice"}]})
        r.check("bad-params-blocked", res.get("success") is False
                and "缺少原子所需参数" in res.get("message", ""), res)
        live = {x["id"] for x in c.editor_data()["config"]["event_rules"]}
        r.check("bad-rule-not-saved", "qa_m13_x" not in live, live)

        # 还原字典后，自定义条目应立刻消失（热更新双向生效）
        _write_text(original)
        ev3 = c.editor_data().get("events") or {}
        r.check("restore-removes-custom",
                "lose_gold" not in [x["key"] for x in ev3["effects"]]
                and "rich" not in [x["key"] for x in ev3["conditions"]]
                and "QA_CUSTOM_EVENT" not in [x["key"] for x in ev3["triggers"]])

    finally:
        _write_text(original)               # 字典按原文还原（含格式）
        for rule in RULES + [CUSTOM_TRIGGER_RULE]:
            c.event_rule_delete(rule["id"])
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
