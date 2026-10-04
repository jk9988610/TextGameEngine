# -*- coding: utf-8 -*-
# PROTOTYPE-GAME：先后手原型的可复用冒烟脚本（长期保留，勿删）
#
# 运行：.\.venv\Scripts\python.exe games\demo_minimal\proto_turnorder_smoke.py
#
# demo 数值：玩家 HP50/攻5/防2；史莱姆 HP10/攻6/防1 → 敌人严格先手。
#   先制伤害 = max(1,6-2)=4；玩家命中 = max(1,10-1)=9（两刀击杀）；
#   防御减伤 = 4//2=2。
#
# 覆盖：
#   T1 攻击回合顺序翻转：敌先打4（50→46）→ 玩家打9（敌10→1），日志有序
#   T2 第二刀：敌先打4（46→42）→ 玩家击杀；金币/钥匙掉落链路不变
#   T3 脱离先制：先挨4再脱离成功，耗时 60（玩家更快时才是 0 耗时）
#   T4 喝药先制：先挨打后回血，日志顺序证明；药水消耗
#   T5 持续防御累积伤害最终死亡 → 走引擎统一死亡流程（回酒馆满血、清战斗）
#
# 未覆盖：玩家先手分支（demo 无 attack≤5 的敌人），该分支直接走引擎
#         player_attack/use_item_in_battle 原函数，由 qa 既有战斗用例守护。
"""先后手原型冒烟（《森林试炼》，阶段一）。退出码 0=全过。"""
import os
import sys
import uuid

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from qa.tge_api import GameClient, QaRunner, cleanup_qa_saves  # noqa: E402

BASE = os.environ.get("TGE_BASE", "http://127.0.0.1:5000")
r = QaRunner("先后手原型冒烟")

# Beat 制启用后旧攻击/喝药路由 409 拒答（对攻顺序已并入 beat），开关互斥 → 跳过
_probe = GameClient(client_id="qa_probe_" + uuid.uuid4().hex[:8], base=BASE)
_probe.reset()
_probe.teleport("forest")
_probe.combat_start("slime")
if (_probe.combat().get("proto_beat") or {}).get("enabled"):
    cleanup_qa_saves()
    print("SKIP：Beat 制已启用，先后手旧原型 smoke 不适用（开关互斥）")
    sys.exit(0)


def battle_new(buy_potion=False):
    c = GameClient(client_id="qa_ord_" + uuid.uuid4().hex[:10], base=BASE)
    c.reset()
    if buy_potion:
        c.buy("healing_potion")   # 村口 5 金币一瓶
    c.teleport("forest")
    c.combat_start("slime")
    return c


# ---- T1 攻击：敌人先打、玩家后打 ----
r.section("T1 攻击回合顺序")
c = battle_new()
out = c.attack()
cb = c.combat()
logs = out.get("log", [])
r.check("turn-1-success", out.get("success") is True, out)
r.check("player-46", cb["player"]["hp"] == 46, cb["player"])
r.check("enemy-1", cb["enemy"]["hp"] == 1, cb["enemy"])
i_strike = next((i for i, s in enumerate(logs) if "抢先" in s), -1)
i_hit = next((i for i, s in enumerate(logs) if "你用" in s), -1)
r.check("strike-before-hit", 0 <= i_strike < i_hit, logs)
r.check("strike-4-dmg", any("**4** 点伤害" in s for s in logs), logs)
r.check("time-60", c.state().get("game_time") == 60)

# ---- T2 第二刀击杀，掉落链路不变 ----
r.section("T2 先制下击杀与掉落")
st_before = c.state()
gold_before = st_before["player_gold"]
out2 = c.attack()
st_after = c.state()
r.check("slime-defeated", out2.get("defeated") == "slime", out2)
# 先制 4 点（46→42）后玩家击杀，击杀轮无敌人反击，HP 停在 42
r.check("player-42", c.combat()["player"]["hp"] == 42, c.combat()["player"])
r.check("gold-plus-5", st_after["player_gold"] == gold_before + 5,
        (gold_before, st_after["player_gold"]))
# 钥匙掉在森林地上（unlock 事件由 ENEMY_KILLED 触发）
items_here = [it["id"] for it in st_after["scene"]["items_here"]]
r.check("key-dropped", "rusty_key" in items_here, items_here)
r.check("battle-ended", c.combat().get("active") is False)

# ---- T3 脱离先挨一下 ----
r.section("T3 脱离先制")
c = battle_new()
out = c.post("/api/combat/flee", {})[1]
r.check("flee-still-success", out.get("fled") is True, out)
r.check("flee-costs-hp", c.combat()["player"]["hp"] == 46, c.combat()["player"])
r.check("flee-costs-60", c.state().get("game_time") == 60, c.state().get("game_time"))
r.check("panel-hidden", c.combat().get("active") is False)
logs = out.get("log", [])
r.check("strike-before-flee-log",
        0 <= next((i for i, s in enumerate(logs) if "抢先" in s), -1)
        < next((i for i, s in enumerate(logs) if "脱离了战斗" in s), 99), logs)

# ---- T4 喝药：先挨打后回血 ----
r.section("T4 喝药先制")
c = battle_new(buy_potion=True)
c.attack()                       # 46 血、敌人 1 血
out = c.drink("healing_potion")  # 先挨打 4 → 42，回 30 cap 到 50
logs = out.get("log", [])
r.check("drink-success", out.get("success") is True, out)
r.check("hp-full-after-drink", c.combat()["player"]["hp"] == 50, c.combat()["player"])
i_strike = next((i for i, s in enumerate(logs) if "抢先" in s), -1)
i_drink = next((i for i, s in enumerate(logs) if "治疗药水" in s), -1)
r.check("strike-before-drink", 0 <= i_strike < i_drink, logs)
inv = [it["id"] for it in c.state()["player_inventory"]]
r.check("potion-consumed", "healing_potion" not in inv, inv)

# ---- T5 持续防御最终死亡，走引擎死亡流程 ----
r.section("T5 防御磨死（2 点/回合 ×25）")
c = battle_new()
last = {}
for _ in range(25):
    last = c.post("/api/combat/defend", {})[1]
    if last.get("player_dead"):
        break
r.check("died-via-defend", last.get("player_dead") is True, last)
st = c.state()
r.check("revived-at-tavern", st["scene"]["id"] == "village", st["scene"])
r.check("revived-full-hp", c.combat()["player"]["hp"] == 50)
r.check("battle-cleared", c.combat().get("active") is False)

removed = cleanup_qa_saves()
print(f"（清理 qa_ 存档 {removed} 行）")
print()
print(r.report())
sys.exit(1 if r.failed else 0)
