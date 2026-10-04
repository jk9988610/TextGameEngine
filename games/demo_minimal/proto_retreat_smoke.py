# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：战斗脱离原型的可复用冒烟脚本（长期保留，勿删）
#
# 用途：阶段一每轮迭代后，黑盒验证脱离机制全链路；随机制演进直接改本文件，
#       不要每次重新生成。阶段二抽离进 engine/ 后，其断言应迁入 qa/m*.py。
#
# 运行（仓库根目录，先启动 app.py 并加载 demo_minimal 工程）：
#   .\.venv\Scripts\python.exe games\demo_minimal\proto_retreat_smoke.py
#
# 数值环境（先后手原型已启用，史莱姆攻6 > 玩家攻5 → 敌人恒先手）：
#   攻击回合玩家 50→46、敌人 10→1；脱离时先挨 4（→42）再脱离，脱离回合耗时 60。
#
# 覆盖：
#   T0 无战斗拦截 / T1 开关下发
#   T2 必定脱离：原地、双方残血、面板关、无告知字段（敌人更快时先挨一击）
#   T3 600 秒静默恢复（300 秒不恢复）、恢复后全新战斗
#   T4 残血重新接战取消恢复计时，且可正常击杀
#   T5 连续 10 局脱离全部成功（确定性）
"""脱离战斗原型冒烟（《森林试炼》，阶段一）。退出码 0=全过。"""
import os
import sys
import uuid

# 本文件位于 <repo>/games/demo_minimal/ 下，需要上溯两级到仓库根
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qa.tge_api import GameClient, QaRunner, cleanup_qa_saves  # noqa: E402

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
r = QaRunner("脱离战斗原型冒烟")

# Beat 制启用后旧脱离路由 409 拒答，两套原型开关互斥 → 本脚本整体跳过
_probe = GameClient(client_id="qa_probe_" + uuid.uuid4().hex[:8], base=BASE)
_probe.reset()
_probe.teleport("forest")
_probe.combat_start("slime")
if (_probe.combat().get("proto_beat") or {}).get("enabled"):
    cleanup_qa_saves()
    print("SKIP：Beat 制已启用，脱离旧原型 smoke 不适用（开关互斥）")
    sys.exit(0)


def fresh():
    """新会话：reset → 森林 → 开战。"""
    c = GameClient(client_id="qa_flee_" + uuid.uuid4().hex[:10], base=BASE)
    c.reset()
    c.teleport("forest")
    c.combat_start("slime")
    return c


def wound_and_disengage():
    """攻击一回合（敌先手：玩家 50→46、敌 10→1、time=60）后脱离
    （敌人更快：先挨 4 → 42，再脱离成功，time=120）。返回 (client, 响应)。"""
    c = fresh()
    atk = c.attack()
    assert atk.get("success"), atk
    out = c.post("/api/combat/flee", {})[1]
    return c, out


# ---- T0 无战斗拦截 ----
r.section("T0 边界：无战斗")
c = GameClient(client_id="qa_flee_" + uuid.uuid4().hex[:10], base=BASE)
c.reset()
res = c.post("/api/combat/flee", {})[1]
r.check("flee-without-battle-blocked", res.get("success") is False, res)

# ---- T1 战斗中开关下发 ----
r.section("T1 开关下发")
c = fresh()
battle = c.combat()
r.check("battle-active", battle.get("active") is True, battle)
r.check("proto-enabled", bool(battle.get("proto_retreat", {}).get("enabled")), battle)

# ---- T2 必定立即脱离 ----
r.section("T2 必定立即脱离")
c, out = wound_and_disengage()
st = c.state()
cb = c.combat()
r.check("always-success",
        out.get("success") is True and out.get("fled") is True, out)
r.check("msg-neutral", out.get("message") == "你脱离了战斗。", out.get("message"))
# 允许先手原型的"造成 X 点伤害"行；只禁止旧版"被缠住/带着伤"等解释性文案
r.check("log-no-wound-talk",
        all("缠" not in line and "带着伤" not in line for line in out.get("log", [])),
        out.get("log"))
r.check("no-proto-message", "proto_message" not in out, out)
r.check("stay-in-forest", st["scene"]["id"] == "forest", st["scene"])
r.check("panel-hidden", cb.get("active") is False, cb)
r.check("player-42", cb["player"]["hp"] == 42, cb["player"])
r.check("enemy-still-present",
        any(e["id"] == "slime" for e in st["enemies_here"]), st["enemies_here"])
r.check("state-no-proto-field", "proto_retreat" not in st, st)
# 敌人更快：脱离前先挨一击，该先制占一个回合 → time=120
r.check("flee-strike-costs-60", st.get("game_time") == 120, st.get("game_time"))

# ---- T3 600 秒静默恢复（脱离时刻 time=120）----
r.section("T3 600 秒静默恢复")
m1 = c.move("village")   # +300 → time=420, elapsed=300
r.check("no-recover-at-300", not m1.get("proto_message"), m1)
r.check("player-42-at-300", c.combat()["player"]["hp"] == 42)
m2 = c.move("forest")    # +300 → time=720, elapsed=600 → 静默恢复
r.check("silent-at-600", not m2.get("proto_message"), m2)
cb2 = c.combat()
r.check("battle-reset", cb2.get("active") is False, cb2)
r.check("player-full-50", cb2["player"]["hp"] == 50, cb2["player"])
c.combat_start("slime")
cb3 = c.combat()
r.check("fresh-enemy-10",
        cb3.get("active") and cb3["enemy"]["hp"] == 10, cb3)

# ---- T4 残血接战取消计时 ----
r.section("T4 残血接战取消计时")
c, _ = wound_and_disengage()
c.combat_start("slime")
cb = c.combat()
r.check("resume-enemy-1hp", cb.get("active") and cb["enemy"]["hp"] == 1, cb)
r.check("resume-player-42", cb["player"]["hp"] == 42, cb["player"])
c.move("village")
m = c.move("forest")    # 已满 600 秒，但计时早已取消
r.check("no-silent-heal-after-cancel", not m.get("proto_message"), m)
cb = c.combat()
r.check("enemy-still-1hp", cb.get("active") and cb["enemy"]["hp"] == 1, cb)
r.check("player-still-42", cb["player"]["hp"] == 42, cb["player"])
kill = c.attack()       # 敌人先制 4（42→38），玩家击杀
r.check("killable-after-resume", kill.get("defeated") == "slime", kill)

# ---- T5 确定性：连续 10 局全部脱离成功 ----
r.section("T5 连续 10 局必成")
all_ok = True
for _ in range(10):
    _, out = wound_and_disengage()
    if not (out.get("success") and out.get("fled")):
        all_ok = False
        break
r.check("10-10-disengage", all_ok)

removed = cleanup_qa_saves()
print(f"（清理 qa_ 存档 {removed} 行）")
print()
print(r.report())
sys.exit(1 if r.failed else 0)
