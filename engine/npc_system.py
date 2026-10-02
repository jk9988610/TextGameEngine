"""
NPC 对话系统（引擎核心模块）
纯事件驱动：订阅 SCENE_ENTER 事件，进入有 NPC 的场景自动触发对话
对话状态存在 game_state['current_dialogue'] —— 不自己维护独立状态字典
依赖全部通过构造函数注入，不 import app.py
"""
from typing import Dict, Any, List, Optional

from .effects import condition_matches


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
        """🆕 进场景不再自动开对话！只清掉跨场景残留的对话"""
        self.end_dialogue()

    def _on_scene_leave(self, scene_id: str, **kwargs) -> None:
        """离开场景 → 清空对话状态"""
        self.end_dialogue()

    # ---------- 🆕 主动触发接口（给前端按钮点时调用） ----------
    def start_dialogue(self, npc_id: str) -> Dict[str, Any]:
        """玩家点"和 XX 说话"按钮 → 手动启动对话"""
        npc = self._get_npc(npc_id)
        if not npc:
            return {"success": False, "message": "NPC 不存在"}
        node_id = self._pick_greeting(npc)
        self._state["current_dialogue"] = {"npc_id": npc_id, "node_id": node_id}
        self._bus.publish("NPC_TALK", npc_id=npc_id, node_id=node_id)
        return {"success": True, "message": "对话已开始"}

    def _pick_greeting(self, npc: Dict) -> str:
        """按 greeting_rules 的顺序取第一个满足条件的起始节点，都不满足用 greeting。

        规则格式（全部游戏内容可配，引擎不写死任何 NPC/物品/敌人）：
          {"if": {"flag": "x"} | {"has_item": "id"} |
                  {"enemy_killed": "id"} | {"gold_gte": n},
           "node": "node_id"}
        """
        for rule in npc.get("greeting_rules", []) or []:
            cond = rule.get("if")
            node_id = rule.get("node")
            if node_id and condition_matches(self._state, cond):
                return node_id
        return npc.get("greeting", "greet")

    def list_npcs_in_scene(self, scene_id: str) -> list:
        """🆕 返回场景里所有 NPC 列表（给前端渲染按钮用）"""
        result = []
        for npc_data in self._data.get("npcs", {}).values():
            if npc_data.get("scene_id") == scene_id:
                result.append({
                    "id": npc_data["id"],
                    "name": npc_data["name"],
                })
        return result

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

    # ---------- 内部：选项条件 ----------
    def _choice_visible(self, choice: Dict) -> bool:
        """选项是否对当前玩家可见：requires_item（旧格式兼容）+ 通用 if 条件都要满足"""
        required = choice.get("requires_item")
        if required and required not in self._state.get("player_inventory", []):
            return False
        return condition_matches(self._state, choice.get("if"))

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

        # 条件不满足的选项不发给前端（索引按可见选项重新排）
        filtered_choices = [
            {"text": choice["text"], "has_next": bool(choice.get("next"))}
            for choice in node.get("choices", [])
            if self._choice_visible(choice)
        ]

        return {
            "active": True,
            "npc_id": npc["id"],
            "npc_name": npc["name"],
            "node_id": dialogue["node_id"],
            "text": node["text"],
            "choices": filtered_choices,
        }

    def select_choice(self, choice_index: int) -> Dict[str, Any]:
        """玩家点击某个选项 → 推进对话树；效果不在本模块执行（跨系统动作交路由层编排）"""
        response = {"success": False, "message": ""}
        dialogue = self._state.get("current_dialogue")
        if not dialogue:
            response["message"] = "当前没有对话"
            return response

        node = self._get_node(dialogue["npc_id"], dialogue["node_id"])
        if not node:
            response["message"] = "对话节点不存在"
            return response

        valid_choices = [c for c in node.get("choices", []) if self._choice_visible(c)]

        if choice_index < 0 or choice_index >= len(valid_choices):
            response["message"] = "选项索引无效"
            return response

        chosen = valid_choices[choice_index]
        next_node_id = chosen.get("next")

        if next_node_id:
            # 推进到下一个节点
            self._state["current_dialogue"]["node_id"] = next_node_id
            self._bus.publish("NPC_TALK", npc_id=dialogue["npc_id"], node_id=next_node_id)
            response["success"] = True
            response["message"] = "对话推进成功"
        else:
            # 没有 next → 对话结束
            self.end_dialogue()
            response["success"] = True
            response["message"] = "对话已结束"

        # 把选项挂的效果原样交给路由层执行（本模块不直接调用其他子系统）
        if chosen.get("effects"):
            response["effects"] = chosen["effects"]
        return response

    def end_dialogue(self) -> None:
        """手动结束当前对话"""
        self._state["current_dialogue"] = None

    def reset(self) -> None:
        """重置对话状态（游戏重置时调）"""
        self.end_dialogue()
