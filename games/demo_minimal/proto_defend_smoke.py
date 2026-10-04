# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：战斗防御原型的可复用冒烟脚本（长期保留，勿删）
#
# 运行（仓库根目录，先启动 app.py 并加载 demo_minimal 工程）：
#   .\.venv\Scripts\python.exe games\demo_minimal\proto_defend_smoke.py
#
# demo 数值：史莱姆攻6/玩家防2 → 原伤 4，防御减半向下取整 = 2。
#
# 覆盖：
#   T0 无战斗拦截 / T1 开关下发
#   T2 防御减半：受伤 2、敌人不掉血（本回合未攻击）、战斗继续、耗时 60
#   T3 连续 3 次防御：HP 48/46/44、敌人满血、战斗仍在
#   T4 防御后接攻击可击杀（敌人先手：先挨打再出手，防御不破坏战斗链路）
#   T5 已脱离（proto_retreat）态防御被拒——两个原型互不串状态
"""防御原型冒烟（《森林试炼》，阶段一）。退出码 0=全过。"""
import os
import sys
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qa.tge_api import GameClient, QaRunner, cleanup_qa_saves  # noqa: E402

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
r = QaRunner("战斗防御原型冒烟")

# Beat 制启用后旧防御路由 409 拒答（格挡已并入 beat），开关互斥 → 整体跳过
_probe = GameClient(client_id="qa_probe_" + uuid.uuid4().hex[:8], base=BASE)
_probe.reset()
_probe.teleport("forest")
_probe.combat_start("slime")
if (_probe.combat().get("proto_beat") or {}).get("enabled"):
    cleanup_qa_saves()
    print("SKIP：Beat 制已启用，防御旧原型 smoke 不适用（开关互斥）")
    sys.exit(0)


def in_battle():
    """新会话：reset → 森林 → 与史莱姆开战。"""
    c = GameClient(client_id="qa_def_" + uuid.uuid4().hex[:10], base=BASE)
    c.reset()
    c.teleport("forest")
    c.combat_start("slime")
    return c


def defend(c):
    return c.post("/api/combat/defend", {})[1]


# ---- T0 无战斗拦截 ----
r.section("T0 边界：无战斗")
c = GameClient(client_id="qa_def_" + uuid.uuid4().hex[:10], base=BASE)
c.reset()
res = defend(c)
r.check("defend-without-battle-blocked", res.get("success") is False, res)

# ---- T1 开关下发 ----
r.section("T1 开关下发")
c = in_battle()
battle = c.combat()
r.check("battle-active", battle.get("active") is True, battle)
r.check("proto-defend-enabled",
        bool(battle.get("proto_defend", {}).get("enabled")), battle)

# ---- T2 防御减半 4→2 ----
r.section("T2 一次防御（伤害 4→2）")
out = defend(c)
cb = c.combat()
st = c.state()
r.check("defend-success", out.get("success") is True, out)
r.check("halved-damage-log",
        any("**2** 点伤害" in line for line in out.get("log", [])), out.get("log"))
r.check("player-hp-48", cb["player"]["hp"] == 48, cb["player"])
r.check("enemy-still-10", cb["enemy"]["hp"] == 10, cb["enemy"])
r.check("battle-continues", cb.get("active") is True, cb)
r.check("turn-time-60", st.get("game_time") == 60, st.get("game_time"))

# ---- T3 再接 3 次防御：46/44/42（连同 T2 共 4 次）----
r.section("T3 再接 3 次防御：46/44/42")
ok_chain = True
for expected_hp in (46, 44, 42):
    out = defend(c)
    cb = c.combat()
    if not (out.get("success") and cb["player"]["hp"] == expected_hp
            and cb["enemy"]["hp"] == 10 and cb.get("active")):
        ok_chain = False
        break
r.check("defends-46-44-42", ok_chain, (out, cb))
r.check("time-240", c.state().get("game_time") == 240, c.state().get("game_time"))

# ---- T4 防御后攻击可击杀（敌人先手）----
r.section("T4 防御后击杀链路")
c = in_battle()
defend(c)                 # 50→48
k1 = c.attack()           # 先挨打4→44，玩家打9：敌10→1
cb = c.combat()
r.check("player-44", cb["player"]["hp"] == 44, cb["player"])
r.check("enemy-1hp-after-hit", cb.get("active") and cb["enemy"]["hp"] == 1, cb)
k2 = c.attack()           # 先挨打4→40，玩家击杀
r.check("kill-after-defend", k2.get("defeated") == "slime", k2)
r.check("battle-ended", c.combat().get("active") is False)

# ---- T5 已脱离态防御被拒 ----
r.section("T5 脱离态防御拒答")
c = in_battle()
c.attack()   # 敌人先制后玩家出手（time=60，敌人 1 血，玩家46）
# 必定脱离：敌人更快，先挨 4（46→42）再脱离成功
flee = c.post("/api/combat/flee", {})[1]
r.check("flee-ok-first", flee.get("fled") is True, flee)
res = defend(c)
r.check("defend-blocked-while-detached",
        res.get("success") is False and "脱离" in res.get("message", ""), res)

removed = cleanup_qa_saves()
print(f"（清理 qa_ 存档 {removed} 行）")
print()
print(r.report())
sys.exit(1 if r.failed else 0)
