"""
场景管理（引擎核心模块）
负责：场景切换、出口锁定判断、当前场景状态查询
依赖全部通过构造函数注入，不 import app.py，避免循环依赖
"""
from typing import Dict, Any


class SceneManager:
    """管理场景加载/切换/锁定状态 —— 所有文字游戏通用"""

    def __init__(self, event_bus, game_data: Dict, game_state: Dict):
        """
        构造函数注入所有依赖
        :param event_bus: 共享的 EventBus 实例
        :param game_data: 游戏数据字典（含 scenes / items）
        :param game_state: 游戏状态字典（含 current_scene / scene_item_states 等）
        """
        self._bus = event_bus
        self._data = game_data
        self._state = game_state

    # ---------- 初始化 ----------
    def _initial_scene(self) -> str:
        """初始场景 ID（来自游戏配置，引擎不写死具体场景）"""
        return (self._data.get("config") or {}).get("initial_scene", "tavern")

    def init_scene_states(self) -> None:
        """启动时同步场景里的物品和锁定状态到内存"""
        for scene_id, scene in self._data["scenes"].items():
            self._state["scene_item_states"][scene_id] = scene.get("items_here", []).copy()
            self._state["scene_lock_states"][scene_id] = scene.get("locked_exits", {}).copy()

    def init_missing_scene_states(self) -> None:
        """热更新后只补齐数据里新出现、但当前会话状态里还没有的场景（不动旧进度）"""
        for scene_id, scene in self._data["scenes"].items():
            if scene_id not in self._state["scene_item_states"]:
                self._state["scene_item_states"][scene_id] = scene.get("items_here", []).copy()
            if scene_id not in self._state["scene_lock_states"]:
                self._state["scene_lock_states"][scene_id] = scene.get("locked_exits", {}).copy()

    # ---------- 核心查询 ----------
    def get_current_scene(self) -> Dict[str, Any]:
        """获取当前场景的完整可渲染数据（给前端用）"""
        scene_id = self._state["current_scene"]
        scene = self._data["scenes"].get(scene_id)
        if scene is None:
            # 兜底：当前场景被编辑器删除时回到初始场景，而不是 500
            scene_id = self._initial_scene()
            self._state["current_scene"] = scene_id
            scene = self._data["scenes"][scene_id]
        # 编辑器热更新后可能缺状态键 → setdefault 防御
        items_state = self._state["scene_item_states"].setdefault(
            scene_id, scene.get("items_here", []).copy())
        locks_state = self._state["scene_lock_states"].setdefault(
            scene_id, scene.get("locked_exits", {}).copy())
        return {
            "id": scene["id"],
            "name": scene["name"],
            "description": scene["description"],
            "exits": [
                {
                    "id": exit_id,
                    "name": self._data["scenes"].get(exit_id, {}).get("name", exit_id),
                    "locked": locks_state.get(exit_id, False),
                }
                for exit_id in scene.get("exits", [])
            ],
            "items_here": [
                {"id": item_id, "name": self._data["items"].get(item_id, {}).get("name", item_id)}
                for item_id in items_state
            ],
            "shop_items": [
                {
                    "id": g.get("item_id", ""),
                    "name": self._data["items"].get(g.get("item_id", ""), {}).get(
                        "name", g.get("item_id", "")),
                    "price": int(g.get("price", 0)),
                }
                for g in scene.get("shop_items", [])
                if g.get("item_id") in self._data["items"]
            ]
        }

    def scene_exists(self, scene_id: str) -> bool:
        return scene_id in self._data["scenes"]

    # ---------- 核心操作 ----------
    def move_to(self, target_scene_id: str) -> Dict[str, Any]:
        """移动到目标场景，返回操作结果"""
        current_scene_id = self._state["current_scene"]
        response = {"success": False, "message": ""}

        # 检查出口是否存在
        if target_scene_id not in self._data["scenes"][current_scene_id]["exits"]:
            response["message"] = "这个出口不存在"
            return response

        # 检查出口是否锁定
        if self._state["scene_lock_states"][current_scene_id].get(target_scene_id, False):
            response["message"] = "这个出口被锁住了，你需要找到钥匙"
            return response

        # 执行移动 + 发布事件（订阅者会响应，比如 NPC 触发对话）
        self._state["current_scene"] = target_scene_id
        target_name = self._data["scenes"][target_scene_id]["name"]
        self._bus.publish("SCENE_LEAVE", scene_id=current_scene_id)
        self._bus.publish("SCENE_ENTER", scene_id=target_scene_id)

        response["success"] = True
        response["message"] = f"你走进了【{target_name}】"
        return response

    def teleport(self, target_scene_id: str) -> Dict[str, Any]:
        """控制台命令：传送（跳过出口/锁定检查）"""
        if not self.scene_exists(target_scene_id):
            return {"success": False, "message": f"场景不存在：{target_scene_id}"}
        self._state["current_scene"] = target_scene_id
        self._bus.publish("SCENE_ENTER", scene_id=target_scene_id)
        target_name = self._data["scenes"][target_scene_id]["name"]
        return {"success": True, "message": f"传送到【{target_name}】"}

    def reset(self) -> None:
        """重置所有场景状态（回初始场景、物品与锁恢复为数据文件里的初始配置）"""
        self._state["current_scene"] = self._initial_scene()
        self.init_scene_states()
