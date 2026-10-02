# -*- coding: utf-8 -*-
"""
M3 回归套件：金币体系 / 货币折算 / 商店购买 / 消耗品使用 / 编辑器物品保护。

依赖的游戏数据（当前 game_data）：
  - 初始：tavern 场景、背包 [rusty_sword]、金币 0、玩家 50HP
  - gold_coin：currency_value=1（拿到即折算金币，不进背包）
  - potion_store（药水店）：地上 1 瓶治疗药水，商店无限供应 healing_potion@1 金币
"""
from qa.tge_api import (
    GameClient, QaRunner,
    editor_snapshot, editor_restore,
    inventory_ids,
)

SUITE = "M3 金币/商店/喝药"

BREAD = {
    "id": "qa_bread",
    "name": "QA面包",
    "description": "QA 专用零金币商品，套件结束应被自动删除。",
    "usable": True,
    "heal": 10,
}


def run(r: QaRunner) -> None:
    c = GameClient("qa_m3")
    snap = editor_snapshot(c)
    try:
        # ---------- S1 新游戏初始经济状态 ----------
        r.section("S1 初始状态")
        c.reset()
        st = c.state()
        r.check("initial-gold-0", st.get("player_gold") == 0, st.get("player_gold"))
        r.check("initial-inventory-sword", inventory_ids(st) == ["rusty_sword"],
                inventory_ids(st))

        # ---------- S2 货币类物品直接折算金币 ----------
        r.section("S2 货币折算")
        out = c.give("gold_coin")
        st = c.state()
        r.check("give-coin-gold-1", st.get("player_gold") == 1, out)
        r.check("coin-not-in-bag", "gold_coin" not in inventory_ids(st), inventory_ids(st))

        # ---------- S3 商店数据随场景下发 ----------
        r.section("S3 药水店在售货物")
        c.teleport("potion_store")
        shop = c.state()["scene"].get("shop_items", [])
        r.check("potion-on-sale-1g",
                any(g.get("id") == "healing_potion" and g.get("price") == 1
                    for g in shop),
                shop)

        # ---------- S4 没钱买不了 ----------
        r.section("S4 金币不足拦截")
        c.reset()
        c.teleport("potion_store")
        poor = c.buy("healing_potion")
        r.check("broke-buy-blocked",
                poor.get("success") is False and "金币不足" in poor.get("message", ""),
                poor)
        r.check("gold-still-0", c.state().get("player_gold") == 0)

        # ---------- S5 有钱成交、可复购、余额扣减 ----------
        r.section("S5 购买成交")
        c.give("gold_coin")
        deal = c.buy("healing_potion")
        r.check("buy-success", deal.get("success") is True, deal)
        st = c.state()
        r.check("gold-deducted", st.get("player_gold") == 0, st.get("player_gold"))
        r.check("potion-in-bag", "healing_potion" in inventory_ids(st), inventory_ids(st))
        r.check("rebuy-blocked", c.buy("healing_potion").get("success") is False)

        # ---------- S6 地上的药水也能捡；满血喝药被拒 ----------
        r.section("S6 拾取与满血拦截")
        take = c.take("healing_potion")
        r.check("ground-potion-take", take.get("success") is True, take)
        r.check("two-potions", inventory_ids(c.state()).count("healing_potion") == 2,
                inventory_ids(c.state()))
        full = c.use_item("healing_potion")
        r.check("full-hp-use-blocked",
                full.get("success") is False and "满" in full.get("message", ""), full)
        r.check("potion-not-consumed",
                inventory_ids(c.state()).count("healing_potion") == 2)

        # ---------- S7 编辑器：物品 CRUD + 无法获得警告 + 校验 ----------
        r.section("S7 编辑器物品")
        res = c.editor_save("item", BREAD)
        r.check("bread-create-ok", res.get("success") is True, res)
        r.check("unobtainable-warning",
                any("无法获得" in w for w in res.get("warnings", [])),
                res.get("warnings"))
        r.check("weapon-neg-damage-blocked",
                c.editor_save("item", {**BREAD, "id": "qa_bad", "is_weapon": True,
                                       "damage": -1, "usable": False}).get("success") is False)
        r.check("currency-zero-blocked",
                c.editor_save("item", {**BREAD, "id": "qa_bad", "usable": False,
                                       "is_currency": True,
                                       "currency_value": 0}).get("success") is False)
        r.check("empty-name-blocked",
                c.editor_save("item", {**BREAD, "id": "qa_bad", "name": ""}).get("success") is False)
        r.check("bad-item-not-saved", "qa_bad" not in c.editor_data()["items"])

        # ---------- S8 零金币商品也能买（价格边界） ----------
        r.section("S8 零价格商品")
        tavern = c.editor_data()["scenes"]["tavern"]
        tavern["shop_items"] = [{"item_id": "qa_bread", "price": 0}]
        r.check("tavern-shop-save", c.editor_save("scene", tavern).get("success") is True)
        c.reset()  # 初始就在酒馆
        shop = c.state()["scene"].get("shop_items", [])
        r.check("bread-on-sale-0g",
                any(g.get("id") == "qa_bread" and g.get("price") == 0 for g in shop),
                shop)
        free = c.buy("qa_bread")
        r.check("free-buy-success", free.get("success") is True, free)
        r.check("bread-in-bag", "qa_bread" in inventory_ids(c.state()))
        r.check("free-bread-full-hp-blocked",
                c.use_item("qa_bread").get("success") is False)

        # ---------- S9 引用中的物品禁止删除 ----------
        r.section("S9 删除保护")
        r.check("potion-delete-blocked",
                c.editor_delete("item", "healing_potion").get("success") is False)
        r.check("sword-delete-blocked",
                c.editor_delete("item", "rusty_sword").get("success") is False)

    finally:
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
