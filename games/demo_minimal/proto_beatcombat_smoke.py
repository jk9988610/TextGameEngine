# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：Beat 制战斗原型的可复用冒烟脚本（长期保留，勿删）
#
# 运行：.\.venv\Scripts\python.exe games\demo_minimal\proto_beatcombat_smoke.py
#
# demo 数值：玩家 HP50/防2/铁剑；史莱姆 HP26/攻6/防1。
# 循环 撞→撞→重击→蜷缩；撞击4（格挡2）、重击10（格挡先减防再减半=4、闪避0）。
# AP：开场1、每拍+1、上限3、跨拍保留；攻1/格挡0/闪避2/蓄力0/脱离2。
# 重击（蓄力就绪后的攻击）：(10+2-1)×2 = 22，克制蜷缩，只能被闪避躲。
# 重击被闪避→敌人下一拍破绽（看穿动作，无数值增益）；脱离失败→下一拍闪避/脱离-1AP。
"""Beat 制战斗原型冒烟（《森林试炼》，阶段一）。退出码 0=全过。"""
import os
import sys
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qa.tge_api import GameClient, QaRunner, cleanup_qa_saves  # noqa: E402

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
r = QaRunner("Beat 制战斗原型冒烟（第一刀：蓄力/重击/条件脱离）")


def battle_new():
    c = GameClient(client_id="qa_beat_" + uuid.uuid4().hex[:10], base=BASE)
    c.reset()
    c.teleport("forest")
    c.combat_start("slime")
    return c


def beat(c, action):
    return c.post("/api/combat/beat", {"action": action})[1]


def hp(c):
    return c.combat()["player"]["hp"]


def ehp(c):
    return c.combat()["enemy"]["hp"]


# ---- T1 开场拍面（含蓄力费用）----
r.section("T1 开场拍面")
c = battle_new()
b = c.combat().get("proto_beat", {})
r.check("beat1-ap1-bash",
        (b["beat"], b["ap"], b["intent"], b["charged"]) == (1, 1, "bash", False), b)
r.check("costs",
        b["costs"] == {"attack": 1, "block": 0, "dodge": 2, "charge": 0, "disengage": 2},
        b["costs"])

# ---- T2 AP 门槛 ----
r.section("T2 AP 不足拦截")
r.check("dodge-blocked", beat(c, "dodge").get("success") is False)
r.check("flee-blocked", beat(c, "disengage").get("success") is False)
b = c.combat()["proto_beat"]
r.check("unchanged", (b["beat"], b["ap"]) == (1, 1), b)
r.check("enemy-full-26", ehp(c) == 26)

# ---- T3 蓄力→重击 22→击杀链路 ----
r.section("T3 蓄力重击链路（brace 窗口蓄力）")
beat(c, "block")                       # b1 bash：48, ap2
beat(c, "block")                       # b2 bash：46, ap3
beat(c, "dodge")                       # b3 heavy：46, ap2
out = beat(c, "charge")                # b4 brace：蓄力成功，46, ap3
b = c.combat()["proto_beat"]
r.check("charge-ok", out.get("success") and b["charged"] is True, (out, b))
r.check("charge-preview-22", any("**22** 点重击" in s for s in out["log"]), out["log"])
r.check("beat5-bash", (b["beat"], b["intent"]) == (5, "bash"), b)
out = beat(c, "attack")                # b5 bash：重击 22（敌26→4），吃撞4→42
r.check("heavy-22", ehp(c) == 4 and hp(c) == 42, (ehp(c), hp(c)))
r.check("heavy-log", any("重击" in s and "**22**" in s for s in out["log"]), out["log"])
b = c.combat()["proto_beat"]
r.check("charge-consumed", b["charged"] is False, b)
gold_before = c.state()["player_gold"]
out = beat(c, "attack")                # b6 bash：普攻9，敌4→击杀，无反击
r.check("kill", out.get("defeated") == "slime" and hp(c) == 42, out)
st = c.state()
r.check("gold+5", st["player_gold"] == gold_before + 5)
r.check("key-drop", "rusty_key" in [i["id"] for i in st["scene"]["items_here"]])

# ---- T4 蓄力被重击打断 ----
r.section("T4 蓄力打断")
c = battle_new()
beat(c, "block")                       # 48 ap2
beat(c, "block")                       # 46 ap3
out = beat(c, "charge")                # b3 heavy：吃 10 → 36，打断
r.check("interrupted-36", hp(c) == 36 and not c.combat()["proto_beat"]["charged"],
        (hp(c), out))
r.check("interrupt-log", any("打断" in s for s in out["log"]), out["log"])
out = beat(c, "attack")                # b4 brace：普攻被弹开 0 伤（无重击加成）
r.check("no-bonus-after-break", ehp(c) == 26, ehp(c))

# ---- T4b 重击格挡：先减防再减半 = 4 ----
r.section("T4b 重击格挡减伤")
c = battle_new()
beat(c, "block")                       # 48
beat(c, "block")                       # 46
out = beat(c, "block")                 # b3 heavy：(10-2)//2=4 → 42
r.check("heavy-block-4", hp(c) == 42, (hp(c), out))
r.check("heavy-block-log", any("**4**" in s for s in out["log"]), out["log"])

# ---- T4c 重击被闪避→敌人下一拍破绽 ----
r.section("T4c 重击闪避与破绽")
c = battle_new()
beat(c, "block"); beat(c, "block")    # 46
out = beat(c, "dodge")                 # b3 heavy：0 伤，敌人破绽挂到 b4
r.check("dodge-heavy-0", hp(c) == 46)
r.check("flaw-next-beat", out.get("enemy_flaw") is True, out)
b = c.combat()["proto_beat"]
r.check("face-shows-flaw", b["enemy_flaw"] is True and b["beat"] == 4, b)
r.check("flaw-see-brace", b["intent"] == "brace" and "破绽" in b["intent_hint"], b)
beat(c, "block")                       # b4 度过，破绽在拍末清除
b = c.combat()["proto_beat"]
r.check("flaw-expired", b["beat"] == 5 and b["enemy_flaw"] is False, b)

# ---- T5 蓄力就绪过期（下一拍不攻击即清空）----
r.section("T5 蓄力过期")
c = battle_new()
beat(c, "block"); beat(c, "block"); beat(c, "dodge")
beat(c, "charge")                      # b4 brace 蓄力成功
r.check("readied", c.combat()["proto_beat"]["charged"] is True)
beat(c, "block")                       # b5 bash：格挡，蓄力过期
r.check("expired", c.combat()["proto_beat"]["charged"] is False)
out = beat(c, "attack")                # b6 bash：只是普攻 9
r.check("plain-9", ehp(c) == 17 and any("**9** 点伤害" in s for s in out["log"]),
        (ehp(c), out["log"]))

# ---- T6 条件脱离：攻击拍失败（AP照扣，获得挣扎），蜷缩拍成功 ----
r.section("T6 脱离窗口与挣扎")
c = battle_new()
r.check("flee-blocked-b1", beat(c, "disengage").get("success") is False)
beat(c, "block")                       # b1 bash：48 ap2
out = beat(c, "disengage")             # b2 bash：吃4失败 44，ap1，挣扎→b3
r.check("flee-fails-on-bash",
        out.get("fled") is not True and hp(c) == 44 and c.combat().get("active"), out)
r.check("flee-fail-log", any("没能脱离" in s for s in out["log"]), out["log"])
b = c.combat()["proto_beat"]
r.check("struggle-set-at-b3",
        b["struggle"] is True and b["costs"]["dodge"] == 1 and b["costs"]["disengage"] == 1, b)
out = beat(c, "dodge")                 # b3 heavy：挣扎折扣后 1AP 可闪，0 伤 44
r.check("struggle-dodge-affordable",
        out.get("success") and hp(c) == 44, (out, hp(c)))
b = c.combat()["proto_beat"]
r.check("struggle-expired-at-b4",
        b["beat"] == 4 and b["struggle"] is False and b["costs"]["dodge"] == 2, b)
r.check("flee-unsupported-at-b4",
        beat(c, "disengage").get("success") is False)  # ap1，脱离恢复 2AP 不足

r.section("T6b 蜷缩拍脱离成功")
c = battle_new()
beat(c, "block")                       # 48 ap2
beat(c, "block")                       # 46 ap3
beat(c, "dodge")                       # b3 heavy：46 ap2（重击被闪，敌人破绽→b4）
out = beat(c, "disengage")             # b4 brace：未受伤 → 成功
r.check("flee-ok-on-brace", out.get("fled") is True and hp(c) == 46, (out, hp(c)))
r.check("panel-hidden", c.combat().get("active") is False)
r.check("time-240", c.state().get("game_time") == 240)

# ---- T7 脱离后 600 秒静默恢复，残血续战重置 AP/拍/蓄力/状态 ----
r.section("T7 脱离恢复与续战")
c.move("village")                     # 540 elapsed300
r.check("no-heal-300", hp(c) == 46)
c.move("forest")                       # 840 elapsed600
r.check("healed-50", hp(c) == 50)
c.combat_start("slime")
b = c.combat()["proto_beat"]
r.check("reengage-reset",
        ehp(c) == 26 and b["beat"] == 1 and b["ap"] == 1
        and b["charged"] is False and b["struggle"] is False and b["enemy_flaw"] is False, b)

# ---- T8 格挡重击：先减防再减半 = 4 ----
r.section("T8 重击格挡减伤")
c = battle_new()
beat(c, "block"); beat(c, "block")    # 48 → 46
beat(c, "block")                       # b3 heavy：(10-2)//2=4 → 42
r.check("heavy-block-4", hp(c) == 42, hp(c))

# ---- T9 旧接口 beat 模式 409 ----
r.section("T9 旧接口拒答")
c = battle_new()
for path, body in (("/api/combat/attack", {}), ("/api/combat/use-item", {"item_id": "x"}),
                   ("/api/combat/defend", {}), ("/api/combat/flee", {})):
    status, _ = c.post(path, body)
    r.check(f"409-{path}", status == 409, status)

# ---- T10 持续格挡最终死亡 → 引擎统一复活（循环伤害 8/4拍，约 25 拍）----
r.section("T10 格挡磨死")
c = battle_new()
last, died = {}, False
for _ in range(30):
    last = beat(c, "block")
    if last.get("player_dead"):
        died = True
        break
r.check("died", died, last)
r.check("revived", c.state()["scene"]["id"] == "village" and hp(c) == 50)
r.check("battle-cleared", c.combat().get("active") is False)

removed = cleanup_qa_saves()
print(f"（清理 qa_ 存档 {removed} 行）")
print()
print(r.report())
sys.exit(1 if r.failed else 0)
