"""
开发者控制台命令分发（引擎核心模块）
负责：解析控制台命令字符串，调用引擎模块方法
依赖全部通过构造函数注入，不 import app.py
"""
import json
import os
from typing import Dict, Any


class ConsoleHandler:
    """开发者控制台命令处理器 —— 所有文字游戏通用"""

    def __init__(self, event_bus, game_data: Dict, game_state: Dict,
                 scene_manager, item_system, save_path: str = "game_data/snapshot.json"):
        """
        构造函数注入所有依赖
        :param event_bus: 共享的 EventBus 实例
        :param game_data: 游戏数据字典
        :param game_state: 游戏状态字典
        :param scene_manager: SceneManager 实例（处理 teleport 等场景命令）
        :param item_system: ItemSystem 实例（处理 add_item 等物品命令）
        :param save_path: 快照存储路径
        """
        self._bus = event_bus
        self._data = game_data
        self._state = game_state
        self._sm = scene_manager
        self._item = item_system
        self._save_path = save_path

    # ---------- 命令解析入口 ----------
    def handle(self, raw_command: str) -> str:
        """
        解析并执行一条控制台命令
        :param raw_command: 前端发来的原始命令字符串（比如 "teleport cave"）
        :return: 输出结果字符串（显示在控制台）
        """
        parts = raw_command.strip().split()
        if not parts:
            return "错误：空命令"

        cmd_name = parts[0].lower()
        cmd_args = parts[1:]

        # 【命令映射】哈希表 O(1) 匹配，避免长长的 if-elif 链
        handlers = {
            "teleport": self._cmd_teleport,
            "add_item": self._cmd_add_item,
            "drop_item": self._cmd_drop_item,
            "save": self._cmd_save,
            "load": self._cmd_load,
            "reset": self._cmd_reset,
            "help": self._cmd_help,
        }

        handler = handlers.get(cmd_name)
        if handler:
            return handler(cmd_args)
        else:
            return f"错误：未知命令：{cmd_name}\n可用命令：{list(handlers.keys())}"

    # ---------- 具体命令实现（每个只做一件事） ----------
    def _cmd_teleport(self, args: list) -> str:
        if not args:
            return "错误：用法：teleport <场景ID>（可用：tavern/forest/cave）"
        result = self._sm.teleport(args[0])
        return result["message"]

    def _cmd_add_item(self, args: list) -> str:
        if not args or not self._item.item_exists(args[0]):
            return f"错误：用法：add_item <物品ID>（可用：{list(self._data['items'].keys())}）"
        result = self._item.add_to_inventory(args[0])
        return result["message"]

    def _cmd_drop_item(self, args: list) -> str:
        if not args:
            return "错误：用法：drop_item <物品ID>"
        result = self._item.drop_item(args[0])
        return result["message"]

    def _cmd_save(self, args: list) -> str:
        snapshot = {
            "current_scene": self._state["current_scene"],
            "player_inventory": self._state["player_inventory"],
            "scene_item_states": self._state["scene_item_states"],
            "scene_lock_states": self._state["scene_lock_states"]
        }
        os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
        with open(self._save_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        return f"快照已保存到 {self._save_path}"

    def _cmd_load(self, args: list) -> str:
        try:
            with open(self._save_path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
            self._state.update(snapshot)
            scene_name = self._data["scenes"][self._state["current_scene"]]["name"]
            return f"已读取快照，回到【{scene_name}】"
        except FileNotFoundError:
            return "错误：没有找到存档快照"
        except Exception as e:
            return f"错误：读取失败：{str(e)}"

    def _cmd_reset(self, args: list) -> str:
        """完整重置 game_state —— 新开游戏专用（初始值来自 game_config.json）"""
        cfg = self._data.get("config") or {}

        self._state["current_scene"] = cfg.get("initial_scene", "tavern")
        # 恢复初始背包（而不是清空）
        self._state["player_inventory"] = list(cfg.get("initial_inventory", []))
        self._sm.reset()

        # 补全所有状态字段（之前漏了导致"新开游戏沿用旧状态"）
        self._state["current_battle"] = None
        self._state["current_dialogue"] = None
        self._state["killed_enemies"] = []      # 敌人全复活
        self._state["game_time"] = 0            # 时间归零
        player_cfg = cfg.get("player", {})
        initial_hp = player_cfg.get("hp", self._state.get("player_max_hp", 50))
        self._state["player_hp"] = initial_hp
        self._state["player_max_hp"] = initial_hp
        if "attack" in player_cfg:
            self._state["player_attack"] = player_cfg["attack"]
        if "defense" in player_cfg:
            self._state["player_defense"] = player_cfg["defense"]

        return "游戏已完全重置（场景/背包/敌人/战斗全清）"

    def _cmd_help(self, args: list) -> str:
        return (
            "可用命令：\n"
            "  teleport <场景ID>  传送（跳过出口检查）\n"
            "  add_item <物品ID>  加物品到背包\n"
            "  drop_item <物品ID> 丢物品到当前场景\n"
            "  save              保存快照\n"
            "  load              读取快照\n"
            "  reset             重置游戏\n"
            "  help              显示本帮助"
        )
