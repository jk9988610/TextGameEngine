"""
商店系统（引擎通用模块）
负责：查询当前场景的在售货物、花金币购买（无限供应，不消耗库存）。

数据约定（游戏内容层，全部可在编辑器里配）：
  场景 scenes.json 可选字段 shop_items: [{"item_id": "xxx", "price": 2}, ...]
  玩家状态 game_state["player_gold"]: int —— 金币余额
  物品 items.json 可带 currency_value —— 拾取时折算成金币（见 item_system）
"""
from typing import Dict, Any, List


class ShopSystem:
    """场景商店 —— 所有文字游戏通用，不含任何具体物品/价格"""

    def __init__(self, event_bus, game_data: Dict, game_state: Dict):
        self._bus = event_bus
        self._data = game_data
        self._state = game_state

    # ---------- 查询 ----------
    def list_shop_items(self, scene_id: str = None) -> List[Dict[str, Any]]:
        """返回某个场景（默认当前场景）的在售货物可渲染列表"""
        sid = scene_id or self._state["current_sc"]
        scene = self._data["scenes"].get(sid)
        if not scene:
            return []
        result = []
        for entry in scene.get("shop_items", []):
            item_id = entry.get("item_id", "")
            item = self._data["items"].get(item_id)
            if not item:
                continue  # 数据里已删除的物品：编辑器删除保护会拦，这里再兜一层
            result.append({
                "id": item_id,
                "name": item.get("name", item_id),
                "price": int(entry.get("price", 0)),
            })
        return result

    # ---------- 购买 ----------
    def buy_item(self, item_id: str) -> Dict[str, Any]:
        """
        花金币购买当前场景商店里的物品（无限供应）。
        成功 → 扣金币、物品入背包、发布 ITEM_BOUGHT 事件
        """
        response = {"success": False, "message": ""}
        sid = self._state["current_scene"]
        scene = self._data["scenes"].get(sid, {})

        entry = next((g for g in scene.get("shop_items", [])
                     if g.get("item_id") == item_id), None)
        if entry is None:
            response["message"] = "这家店不卖这个东西"
            return response
        if item_id not in self._data["items"]:
            response["message"] = f"商品数据不存在：{item_id}"
            return response

        price = int(entry.get("price", 0))
        gold = self._state.get("player_gold", 0)
        if gold < price:
            response["message"] = f"金币不足：{self._data['items'][item_id]['name']}售价 {price} 金币，你只有 {gold} 金币"
            return response

        # 成交：扣款 + 入包（允许重复购买，背包里可有多瓶）
        self._state["player_gold"] = gold - price
        self._state["player_inventory"].append(item_id)
        self._bus.publish("ITEM_BOUGHT", item_id=item_id, price=price, from_scene=sid)

        response["success"] = True
        response["message"] = (
            f"你花 {price} 金币买下了【{self._data['items'][item_id]['name']}】，"
            f"剩余金币：{self._state['player_gold']}"
        )
        response["player_gold"] = self._state["player_gold"]
        return response
