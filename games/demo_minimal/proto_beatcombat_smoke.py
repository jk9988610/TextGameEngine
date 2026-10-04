# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：Beat 制战斗原型的可复用冒烟脚本（长期保留，勿删）
#
# 运行：.\.venv\Scripts\python.exe games\demo_minimal\proto_beatcombat_smoke.py
#
# demo：玩家 HP50/防2/铁剑；史莱姆 HP26/攻6/防1。
# L2 决策器（拍首纯函数锁定，不偷看玩家本拍选择）：
#   承诺重击 > 打断喘息 > 闪避后承诺撞击 > 玩家蓄力且健康→闪避
#   > 玩家蓄力且残血→撞击 > 残血蜷缩 > 节奏 bash→charge→重击
# 模糊征兆：普通拍面显示动态叙述；重击被躲后的破绽拍才显示具体动作名
"""Beat 制战斗原型冒烟（《森林试炼》，L2 敌人决策器）。退出码 0=全过。"""
import os
import sys
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qa.tge_api import GameClient, QaRunner, cleanup_qa_saves  # noqa: E402

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
r = QaRunner("Beat 制战斗原型冒烟（L2 决策器）")


def battle_new():
    c = GameClient(client_id="qa_beat_" + uuid.uuid4().hex[:10], base=BASE)
    c.reset()
    c.teleport("forest")
    c.combat_start("slime")
    return c


def beat(c, action):
    return c.post("/api/combat/beat", {"action": action})[1]


def face(c):
    return c.combat()["proto_beat"]


def hp(c):
    return c.combat()["player"]["hp"]


def ehp(c):
    return c.combat()["enemy"]["hp"]


# ---- T1 默认节奏 bash→charge→heavy（不打断时的三拍循环）----
r.section("T1 节奏与拍面")
c = battle_new()
r.check("b1-bash", (face(c)["beat"], face(c)["intent"]) == (1, "bash"))
r.check("b1-ambiguous-telegraph",
        face(c)["intent_label"] == "敌人的动作"
        and "弹簧" in face(c)["intent_hint"], face(c))
beat(c, "block")                        # 48
r.check("b2-charge", face(c)["intent"] == "charge", face(c))
beat(c, "block")                        # 蓄力拍无伤 48
r.check("b3-heavy-promised", face(c)["intent"] == "heavy", face(c))
beat(c, "block")                        # 重击格挡 48-4=44
r.check("b4-back-to-bash", face(c)["intent"] == "bash", face(c))
beat(c, "block")                        # 42
r.check("b5-charge-again", face(c)["intent"] == "charge", face(c))

# ---- T2 AP 门槛 ----
r.section("T2 AP 不足拦截")
c = battle_new()
r.check("dodge-blocked", beat(c, "dodge").get("success") is False)
r.check("flee-blocked", beat(c, "disengage").get("success") is False)
b = face(c)
r.check("unchanged", (b["beat"], b["ap"]) == (1, 1), b)

# ---- T3 打断蓄力：b2 攻击 → b3 喘息可白打，节奏重置 ----
r.section("T3 打断链")
c = battle_new()
out = beat(c, "attack")                 # b1 bash：46，敌17
r.check("b1", hp(c) == 46 and ehp(c) == 17)
out = beat(c, "attack")                 # b2 charge 打断：46，敌8
r.check("interrupt-log", any("打断" in s for s in out["log"]), out["log"])
r.check("b3-recover", face(c)["intent"] == "recover", face(c))
out = beat(c, "attack")                 # b3 喘息：0 伤，敌8-9 击杀
r.check("recover-free-hit",
        hp(c) == 46 and out.get("defeated") == "slime", (hp(c), out))
r.check("victory-payload",
        out["victory"]["gold"] == 5
        and [i["id"] for i in out["victory"]["items"]] == ["rusty_key"]
        and out["victory"]["items"][0]["description"], out["victory"])
# 新节奏从 bash 重新开始
r.check("key-on-ground", "rusty_key" in [i["id"] for i in c.state()["scene"]["items_here"]])

# ---- T4 互蓄→重击对重击（同时结算，敌死玩家活）----
r.section("T4 双方重击同时揭晓")
c = battle_new()
beat(c, "block")                        # 48
out = beat(c, "charge")                 # b2 互蓄
r.check("mutual-charge", hp(c) == 48 and face(c)["charged"] is True, out)
r.check("b3-still-heavy", face(c)["intent"] == "heavy")  # 承诺不可取消
out = beat(c, "attack")                 # 26-22=4 敌死？不，26-22=4 存活
r.check("enemy-4-hp", ehp(c) == 4 and hp(c) == 38 and not out.get("defeated"),
        (ehp(c), hp(c), out))
r.check("b4-brace-lowhp", face(c)["intent"] == "brace", face(c))  # 4≤6.5 残血龟息

# ---- T5 残血 brace → 玩家蓄力 → charged 触发撞击反制 → 重击收头 ----
r.section("T5 残血龟息与蓄力反制")
out = beat(c, "charge")                 # b4 brace 拍玩家蓄力成功
r.check("charge-on-brace", face(c)["charged"] is True, out)
r.check("b5-bash-vs-charge", face(c)["intent"] == "bash", face(c))  # 残血不躲
out = beat(c, "attack")                 # 重击 4-22 击杀；吃 bash 4：38→34
r.check("heavy-kill-through-bash",
        out.get("defeated") == "slime" and hp(c) == 34, (out, hp(c)))

# ---- T6 重击闪避→破绽一拍（决策器不改招，情报提前给玩家）----
r.section("T6 破绽")
c = battle_new()
beat(c, "block")                        # 48
beat(c, "block")                        # b2 charge 48
out = beat(c, "dodge")                  # b3 heavy 0 伤
r.check("dodge-0", hp(c) == 48 and out.get("enemy_flaw") is True)
b = face(c)
r.check("flaw-see-b4-bash",
        b["enemy_flaw"] is True and b["beat"] == 4 and b["intent"] == "bash"
        and "撞击" in b["intent_hint"], b)
beat(c, "block")                        # 46，破绽过期
r.check("flaw-expired", face(c)["enemy_flaw"] is False)

# ---- T7 玩家蓄力过期；普攻打断后敌人不再重击 ----
r.section("T7 蓄力过期")
c = battle_new()
beat(c, "block"); beat(c, "block")     # b2 charge
beat(c, "dodge")                        # b3 heavy 48
out = beat(c, "charge")                 # b4 bash 拍蓄力挨打4：被打断
r.check("player-charge-broken",
        hp(c) == 44 and any("蓄力被打断" in s for s in out["log"]), (hp(c), out))
r.check("b5-charge-normal", face(c)["intent"] == "charge", face(c))
out = beat(c, "attack")                 # b5 charge 普攻 9 打断：敌17
r.check("plain-9-interrupt",
        ehp(c) == 17 and any("**9** 点伤害" in s for s in out["log"])
        and any("打断" in s for s in out["log"]), (ehp(c), out["log"]))
r.check("b6-recover", face(c)["intent"] == "recover", face(c))

# ---- T8 脱离：charge 拍窗口；bash 失败→挣扎折扣后 charge 拍再走 ----
r.section("T8 脱离与挣扎")
c = battle_new()
beat(c, "block")                        # b1 bash 48 ap2
out = beat(c, "disengage")              # b2 charge：无伤 → 成功
r.check("flee-charge-window", out.get("fled") is True and hp(c) == 48, out)
r.check("time-120", c.state().get("game_time") == 120)

c = battle_new()
beat(c, "block")                        # b1
beat(c, "block")                        # b2 charge ap3
beat(c, "dodge")                        # b3 heavy 48 ap2
out = beat(c, "disengage")              # b4 bash：吃4失败 44，挣扎→b5
r.check("flee-fail-bash", out.get("fled") is not True and hp(c) == 44, out)
b = face(c)
r.check("struggle-b5-charge",
        b["struggle"] is True and b["intent"] == "charge" and b["costs"]["disengage"] == 1, b)
out = beat(c, "disengage")              # b5 charge 折扣1AP 无伤 → 成功
r.check("struggle-flee-ok", out.get("fled") is True and hp(c) == 44, out)

# ---- T9 脱离静默恢复与续战重置（含决策器状态）----
r.section("T9 恢复与续战")
c.move("village"); c.move("forest")    # 600 秒
r.check("healed", hp(c) == 50)
c.combat_start("slime")
b = face(c)
r.check("reengage-reset",
        ehp(c) == 26 and b["beat"] == 1 and b["ap"] == 1 and b["intent"] == "bash"
        and not b["charged"] and not b["struggle"]
        and not b["enemy_flaw"] and not b.get("player_flaw"), b)

# ---- T10 同归于尽：磨到残血后在承诺重击拍换命（惨胜）----
r.section("T10 同归于尽")
c = battle_new()
beat(c, "attack")                       # b1 bash 换血：玩家46，敌17（重击22可击杀）
guard = 0
while True:
    b = face(c)
    # 在敌人蓄力拍且自己残血时互蓄，下一拍承诺重击拍换命
    if b["intent"] == "charge" and hp(c) <= 10:
        out = beat(c, "charge")         # 互蓄成功（0 伤）
        assert out.get("success") and face(c)["charged"], out
        break
    beat(c, "block")
    guard += 1
    assert guard < 50, "等不到换命时机"
r.check("heavy-promised-next", face(c)["intent"] == "heavy", face(c))
out = beat(c, "attack")                 # 重击拍：22 杀敌，10 点重击打死玩家
r.check("mutual-death",
        out.get("defeated") == "slime" and out.get("player_dead") is True, out)
r.check("pyrrhic", "惨胜" in out.get("message", "") and out["victory"]["gold"] == 5, out)
st = c.state()
r.check("revived", st["scene"]["id"] == "village" and hp(c) == 50)

# ---- T11 旧接口 409 ----
r.section("T11 旧接口拒答")
c = battle_new()
for path, body in (("/api/combat/attack", {}), ("/api/combat/use-item", {"item_id": "x"}),
                   ("/api/combat/defend", {}), ("/api/combat/flee", {})):
    status, _ = c.post(path, body)
    r.check(f"409-{path}", status == 409, status)

# ---- T12 健康时看见蓄力→闪避；重击落空→破绽+承诺撞击；残血仍不躲（T5）----
r.section("T12 敌人闪避")
c = battle_new()
beat(c, "attack")                       # b1 bash 换血：46 / 17
beat(c, "attack")                       # b2 charge 打断：46 / 8（8>6.5 未残血）
r.check("b3-recover-for-dodge", face(c)["intent"] == "recover", face(c))
out = beat(c, "charge")                 # 喘息拍蓄力成功
r.check("charge-on-recover", face(c)["charged"] is True, out)
r.check("b4-dodge-healthy", face(c)["intent"] == "dodge", face(c))
ehp_before = ehp(c)
out = beat(c, "attack")                 # 重击被躲：0 伤，破绽
r.check("heavy-whiff",
        ehp(c) == ehp_before and hp(c) == 46
        and out.get("player_flaw") is True
        and any("躲开" in s or "落了空" in s or "落空" in s for s in out["log"]),
        (ehp(c), hp(c), out))
b = face(c)
r.check("promised-bash-after-dodge",
        b["intent"] == "bash" and b["player_flaw"] is True, b)
beat(c, "block")                        # 吃撞击格挡 46-2=44，破绽过期
r.check("flaw-expired-after-bash", face(c)["player_flaw"] is False)
r.check("no-second-dodge", face(c)["intent"] != "dodge", face(c))

# 闪避拍再蓄力：承诺撞击优先，不会连躲
c = battle_new()
beat(c, "attack")
beat(c, "attack")
beat(c, "charge")
r.check("setup-dodge", face(c)["intent"] == "dodge")
out = beat(c, "charge")                 # 闪避拍蓄力成功（0 伤）
r.check("recharge-on-dodge", face(c)["charged"] is True, out)
r.check("promised-bash-not-dodge", face(c)["intent"] == "bash", face(c))

removed = cleanup_qa_saves()
print(f"（清理 qa_ 存档 {removed} 行）")
print()
print(r.report())
sys.exit(1 if r.failed else 0)
