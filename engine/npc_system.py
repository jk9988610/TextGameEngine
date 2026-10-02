"""
NPC 对话系统（引擎核心模块）
纯事件驱动：订阅 SCENE_ENTER 事件，进入有 NPC 的场景自动触发对话
对话状态存在 game_state['current_dialogue'] —— 不自己维护独立状态字典
依赖全部通过构造函数注入，不 import app.py
"""
from typing import Dict, Any, List, Optional


class NPCSystem:
    """管理 NPC 对话树 —— 订阅事件自动触发，按选项推进"""

    def __init__(self, event_bus, game_data: Dict, game_state: Dict):
        """
        构造函数注入所有依赖
        :param event_bus: 共享的 EventBus 实例
        :param game_data: 游戏数据字典（含 npcs 键，存放所有 NPC 对话树）
        :param game_state: 游戏状态字典（current_dialogue 存在这里）
        """
        self._bus = event_bus
        self._data = game_data
        self._state = game_state

        # 【关键】初始化对话状态（和 current_scene/player_inventory 同级）
        self._state["current_dialogue"] = None  # 例如 {"npc_id": "npc_1_drunk", "node_id": "greet"}

        # 【订阅 SCENE_ENTER 事件】玩家进场景 → 自动触发该场景 NPC 的对话
        self._bus.subscribe("SCENE_ENTER", self._on_scene_enter)
        # 【订阅 SCENE_LEAVE 事件】玩家离开场景 → 自动结束对话
        self._bus.subscribe("SCENE_LEAVE", self._on_scene_leave)

    # ---------- 事件回调（纯被动，不主动调用） ----------
    def _on_scene_enter(self, scene_id: str, **kwargs) -> None:
        """订阅 SCENE_ENTER：进入场景 → 找场景里的第一个 NPC → 自动开始对话"""
        npc = self._find_first_npc_in_scene(scene_id)
        if npc:
            self._state["current_dialogue"] = {
                "npc_id": npc["id"],
                "node_id": npc["greeting"],
            }
            # 也发布 NPC_TALK 事件（后续战斗系统等可以订阅）
            self._bus.publish("NPC_TALK", npc_id=npc["id"], node_id=npc["greeting"])
        else:
            self.end_dialogue()

    def _on_scene_leave(self, scene_id: str, **kwargs) -> None:
        """订阅 SCENE_LEAVE：离开场景 → 清空对话状态"""
        self.end_dialogue()

    # ---------- 内部工具 ----------
    def _find_first_npc_in_scene(self, scene_id: str) -> Optional[Dict]:
        """找出属于该场景的第一个 NPC（目前一个场景一个NPC，后续可扩展多个）"""
        npcs = self._data.get("npcs", {})
        for npc_id, npc_data in npcs.items():
            if npc_data.get("scene_id") == scene_id:
                return npc_data
        return None

    def _get_npc(self, npc_id: str) -> Optional[Dict]:
        return self._data.get("npcs", {}).get(npc_id)

    def _get_node(self, npc_id: str, node_id: str) -> Optional[Dict]:
        npc = self._get_npc(npc_id)
        if npc:
            return npc.get("nodes", {}).get(node_id)
        return None

    # ---------- 公开接口（给路由层调用） ----------
    def get_dialogue_for_api(self) -> Dict[str, Any]:
        """获取当前对话的可渲染数据（给前端用）"""
        dialogue = self._state.get("current_dialogue")
        if not dialogue:
            return {"active": False}

        npc = self._get_npc(dialogue["npc_id"])
        node = self._get_node(dialogue["npc_id"], dialogue["node_id"])
        if not npc or not node:
            self.end_dialogue()
            return {"active": False}

        # 【过滤 requires_item 条件选项】玩家没有对应物品的选项不显示
        player_inv = self._state.get("player_inventory", [])
        filtered_choices = []
        for choice in node.get("choices", []):
            required = choice.get("requires_item")
            if required and required not in player_inv:
                continue  # 条件不满足，跳过
            filtered_choices.append({
                "text": choice["text"],
                "has_next": bool(choice.get("next")),  # 有 next 就能继续推进
            })

        return {
            "active": True,
            "npc_id": npc["id"],
            "npc_name": npc["name"],
            "npc_avatar": npc.get("avatar", "🧑"),
            "node_id": dialogue["node_id"],
            "text": node["text"],
            "choices": filtered_choices,
        }

    def select_choice(self, choice_index: int) -> Dict[str, Any]:
        """玩家点击某个选项 → 推进对话树"""
        response = {"success": False, "message": ""}
        dialogue = self._state.get("current_dialogue")
        if not dialogue:
            response["message"] = "❌ 当前没有对话"
            return response

        node = self._get_node(dialogue["npc_id"], dialogue["node_id"])
        if not node:
            response["message"] = "❌ 对话节点不存在"
            return response

        player_inv = self._state.get("player_inventory", [])
        valid_choices = [c for c in node.get("choices", [])
                         if not c.get("requires_item") or c["requires_item"] in player_inv]

        if choice_index < 0 or choice_index >= len(valid_choices):
            response["message"] = "❌ 选项索引无效"
            return response

        chosen = valid_choices[choice_index]
        next_node_id = chosen.get("next")

        if next_node_id:
            # 推进到下一个节点
            self._state["current_dialogue"]["node_id"] = next_node_id
            self._bus.publish("NPC_TALK", npc_id=dialogue["npc_id"], node_id=next_node_id)
            response["success"] = True
            response["message"] = "✅ 对话推进成功"
        else:
            # 没有 next → 对话结束
            self.end_dialogue()
            response["success"] = True
            response["message"] = "✅ 对话已结束"

        return response

    def end_dialogue(self) -> None:
        """手动结束当前对话"""
        self._state["current_dialogue"] = None

    def reset(self) -> None:
        """重置对话状态（游戏重置时调）"""
        self.end_dialogue()
