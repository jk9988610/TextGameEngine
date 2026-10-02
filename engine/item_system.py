"""
物品系统（引擎核心模块）
负责：背包管理、场景物品增删、物品查询
依赖全部通过构造函数注入，不 import app.py
"""
from typing import Dict, Any


class ItemSystem:
    """管理玩家背包和场景里的物品 —— 所有文字游戏通用"""

    def __init__(self, event_bus, game_data: Dict, game_state: Dict):
        """
        构造函数注入所有依赖
        :param event_bus: 共享的 EventBus 实例
        :param game_data: 游戏数据字典（含 scenes / items）
        :param game_state: 游戏状态字典（含 player_inventory / scene_item_states 等）
        """
        self._bus = event_bus
        self._data = game_data
        self._state = game_state

    # ---------- 查询 ----------
    def get_player_inventory(self) -> list:
        """返回玩家背包的可渲染列表（给前端用）"""
        return [
            {
                "id": item_id,
                "name": self._data["items"][item_id]["name"],
                "is_weapon": bool(self._data["items"][item_id].get("is_weapon", False)),
                "usable": bool(self._data["items"][item_id].get("usable", False)),
            }
            for item_id in self._state["player_inventory"]
            if item_id in self._data["items"]
        ]

    def currency_value(self, item_id: str) -> int:
        """物品作为货币的面值（0 表示不是货币类物品，拾取后进背包）"""
        return int(self._data["items"].get(item_id, {}).get("currency_value", 0))

    def first_weapon_id(self) -> str:
        """返回背包中第一把武器的 id，没有则空串（攻击时自动选武器用）"""
        for item_id in self._state["player_inventory"]:
            if self._data["items"].get(item_id, {}).get("is_weapon"):
                return item_id
        return ""

    def item_exists(self, item_id: str) -> bool:
        return item_id in self._data["items"]

    def get_item_name(self, item_id: str) -> str:
        return self._data["items"][item_id]["name"] if self.item_exists(item_id) else item_id

    # ---------- 核心操作 ----------
    def take_item(self, item_id: str) -> Dict[str, Any]:
        """从当前场景拿物品到背包，返回操作结果"""
        scene_id = self._state["current_scene"]
        response = {"success": False, "message": ""}

        if item_id not in self._state["scene_item_states"][scene_id]:
            response["message"] = "这里没有这个物品"
            return response

        # 执行拿取 + 发布事件（订阅者会响应，比如钥匙解锁门）
        self._state["scene_item_states"][scene_id].remove(item_id)

        # 货币类物品：不进背包，按面值折算成玩家金币余额
        value = self.currency_value(item_id)
        if value > 0:
            self._state["player_gold"] = self._state.get("player_gold", 0) + value
            self._bus.publish("ITEM_TAKEN", item_id=item_id, from_scene=scene_id)
            response["success"] = True
            response["message"] = (
                f"你捡起了【{self.get_item_name(item_id)}】，金币 +{value}"
                f"（当前金币：{self._state['player_gold']}）"
            )
            return response

        self._state["player_inventory"].append(item_id)
        self._bus.publish("ITEM_TAKEN", item_id=item_id, from_scene=scene_id)

        response["success"] = True
        response["message"] = f"你拿到了【{self.get_item_name(item_id)}】"
        return response

    def drop_item(self, item_id: str) -> Dict[str, Any]:
        """从背包丢物品到当前场景（预留，暂未开放给前端）"""
        scene_id = self._state["current_scene"]
        response = {"success": False, "message": ""}

        if item_id not in self._state["player_inventory"]:
            response["message"] = "你没有这个物品"
            return response

        self._state["player_inventory"].remove(item_id)
        self._state["scene_item_states"][scene_id].append(item_id)
        self._bus.publish("ITEM_DROPPED", item_id=item_id, to_scene=scene_id)

        response["success"] = True
        response["message"] = f"你把【{self.get_item_name(item_id)}】丢在了地上"
        return response

    def use_item(self, item_id: str, target_id: str = None) -> Dict[str, Any]:
        """使用物品：标记 usable 的消耗品生效（如治疗药水回血），用掉一瓶"""
        response = {"success": False, "message": ""}
        if item_id not in self._state["player_inventory"]:
            response["message"] = "你没有这个物品"
            return response

        item = self._data["items"].get(item_id, {})
        if not item.get("usable"):
            response["message"] = f"【{item.get('name', item_id)}】现在不能使用"
            return response

        name = item.get("name", item_id)

        # 效果：回血（可叠加其他效果时在这里扩展）
        heal = int(item.get("heal", 0))
        if heal > 0:
            hp = self._state.get("player_hp", 0)
            max_hp = self._state.get("player_max_hp", hp)
            if hp >= max_hp:
                response["message"] = "生命值已满，不需要使用治疗药水"
                return response
            healed = min(heal, max_hp - hp)
            self._state["player_hp"] = hp + healed
            response["hp_healed"] = healed
            response["player_hp"] = self._state["player_hp"]

        # 消耗品：从背包移除一瓶，再发事件
        self._state["player_inventory"].remove(item_id)
        self._bus.publish("ITEM_USED", item_id=item_id, target_id=target_id)
        response["success"] = True
        tail = f"，恢复 {response.get('hp_healed', 0)} 点生命（{self._state.get('player_hp')}/{self._state.get('player_max_hp')}）" if heal > 0 else ""
        response["message"] = f"你使用了【{name}】{tail}"
        return response

    def add_to_inventory(self, item_id: str) -> Dict[str, Any]:
        """控制台命令：直接加物品到背包（货币类物品直接折算金币）"""
        response = {"success": False, "message": ""}
        if not self.item_exists(item_id):
            response["message"] = f"物品不存在：{item_id}"
            return response

        value = self.currency_value(item_id)
        if value > 0:
            self._state["player_gold"] = self._state.get("player_gold", 0) + value
            response["success"] = True
            response["message"] = (
                f"已获得【{self.get_item_name(item_id)}】，金币 +{value}"
                f"（当前金币：{self._state['player_gold']}）"
            )
            return response

        if item_id in self._state["player_inventory"]:
            response["message"] = "你已经有这个物品了"
            return response

        self._state["player_inventory"].append(item_id)
        self._bus.publish("ITEM_TAKEN", item_id=item_id, from_scene="__console__")
        response["success"] = True
        response["message"] = f"已添加【{self.get_item_name(item_id)}】到背包"
        return response

    def reset_inventory(self) -> None:
        """重置背包"""
        self._state["player_inventory"] = []
