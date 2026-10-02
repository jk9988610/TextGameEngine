"""
战斗系统（引擎核心模块）
纯事件驱动：订阅 SCENE_ENTER 刷怪，订阅 ITEM_USED 处理攻击
战斗状态存在 game_state['current_battle'] —— 和 current_dialogue 平级
依赖全部通过构造函数注入，不 import app.py
伤害公式：damage = max(1, attacker_atk - defender_def) —— 简单不会写反
"""
import random
from typing import Dict, Any, Optional


class CombatSystem:
    """回合制战斗系统 —— 玩家用武器攻击敌人，敌人反击"""

    def __init__(self, event_bus, game_data: Dict, game_state: Dict):
        """
        构造函数注入所有依赖
        :param event_bus: 共享的 EventBus 实例
        :param game_data: 游戏数据字典（需含 enemies 键）
        :param game_state: 游戏状态字典（player stats + current_battle 存在这里）
        """
        self._bus = event_bus
        self._data = game_data
        self._state = game_state

        # 初始化战斗状态（和 current_scene/current_dialogue 同级）
        self._state["current_battle"] = None  # {"enemy_id": "...", "enemy_hp": 20}

        # 【订阅 SCENE_ENTER】进场景 → 检查有没有敌人 → 自动开怪
        self._bus.subscribe("SCENE_ENTER", self._on_scene_enter)

    # ---------- 事件回调 ----------
    def _on_scene_enter(self, scene_id: str, **kwargs) -> None:
        """
        订阅 SCENE_ENTER：进场景 → 如果场景有活着的敌人 → 自动开战斗
        修复：
        1. 读档时保留存档里的 enemy_hp，新开战斗才用满血
        2. 敌人死了就真的死了（在 killed_enemies 里就跳过）
        """
        enemies_here = self._get_scene_enemies(scene_id)
        if enemies_here:
            enemy = enemies_here[0]

            # 🆕 永久死亡检查：这个敌人已经被玩家打死过？
            killed = self._state.get("killed_enemies", [])
            if enemy["id"] in killed:
                return  # 哥布林死了就不会再复活，什么都不做

            existing = self._state.get("current_battle")
            if existing and existing.get("enemy_id") == enemy["id"]:
                # 存档里已有这个敌人的战斗状态 → 保留（不重置满血！）
                pass
            else:
                # 新开战斗 → 用满血
                self._state["current_battle"] = {
                    "enemy_id": enemy["id"],
                    "enemy_hp": enemy["hp"],
                }
                self._bus.publish("BATTLE_START", enemy_id=enemy["id"])
        else:
            self.end_battle()

    # ---------- 内部工具 ----------
    def _get_scene_enemies(self, scene_id: str) -> list:
        """获取场景里的敌人数据"""
        scene = self._data.get("scenes", {}).get(scene_id, {})
        enemy_ids = scene.get("enemies_here", [])
        enemies = self._data.get("enemies", {})
        return [enemies[eid] for eid in enemy_ids if eid in enemies]

    def _get_current_enemy(self) -> Optional[Dict]:
        battle = self._state.get("current_battle")
        if not battle:
            return None
        return self._data.get("enemies", {}).get(battle["enemy_id"])

    def _get_player_stats(self) -> Dict[str, int]:
        """获取玩家当前属性（带兜底默认值）"""
        s = self._state
        return {
            "hp": s.get("player_hp", 50),
            "max_hp": s.get("player_max_hp", 50),
            "attack": s.get("player_attack", 5),
            "defense": s.get("player_defense", 2),
        }

    def _damage(self, atk: int, defense: int) -> int:
        """伤害公式：max(1, atk - def) —— 简单不会写反"""
        return max(1, atk - defense)

    # ---------- 公开接口（给路由层调用） ----------
    def get_battle_for_api(self) -> Dict[str, Any]:
        """获取当前战斗的可渲染数据（给前端用）"""
        battle = self._state.get("current_battle")
        player = self._get_player_stats()
        if not battle:
            return {"active": False, "player": player}

        enemy = self._get_current_enemy()
        if not enemy:
            self.end_battle()
            return {"active": False, "player": player}

        return {
            "active": True,
            "enemy": {
                "id": enemy["id"],
                "name": enemy["name"],
                "avatar": enemy.get("avatar", "👾"),
                "hp": battle["enemy_hp"],
                "max_hp": enemy["hp"],
                "attack": enemy["attack"],
                "defense": enemy["defense"],
                "description": enemy["description"],
            },
            "player": player,
        }

    def player_attack(self, weapon_id: str) -> Dict[str, Any]:
        """玩家用武器攻击当前敌人 → 敌人还击中 → 检查死亡"""
        response = {"success": False, "message": "", "log": []}
        battle = self._state.get("current_battle")
        if not battle:
            response["message"] = "❌ 当前没有战斗"
            return response

        enemy = self._get_current_enemy()
        if not enemy:
            self.end_battle()
            response["message"] = "❌ 敌人不见了"
            return response

        player = self._get_player_stats()
        # 检查武器：必须在玩家背包里，且 is_weapon=true
        weapon = self._data.get("items", {}).get(weapon_id, {})
        if weapon_id not in self._state.get("player_inventory", []):
            response["message"] = f"❌ 你没有【{weapon.get('name', weapon_id)}】"
            return response
        if not weapon.get("is_weapon"):
            response["message"] = f"❌ 【{weapon.get('name', weapon_id)}】不是武器"
            return response

        # --- 玩家攻击 ---
        weapon_dmg = weapon.get("damage", 5)
        p_damage = self._damage(weapon_dmg, enemy["defense"])
        battle["enemy_hp"] -= p_damage
        response["log"].append(f"⚔️ 你用【{weapon['name']}】对【{enemy['name']}】造成 **{p_damage}** 点伤害！")
        self._bus.publish("COMBAT_DAMAGE", attacker="player", defender=enemy["id"], damage=p_damage)

        # 敌人死了？
        if battle["enemy_hp"] <= 0:
            response["log"].append(f"💀 【{enemy['name']}】被你打倒了！")
            self._bus.publish("COMBAT_DEATH", dead=enemy["id"])
            # 🆕 关键：标记永久死亡 → 以后再进洞穴不会复活了
            self._state.setdefault("killed_enemies", [])
            if enemy["id"] not in self._state["killed_enemies"]:
                self._state["killed_enemies"].append(enemy["id"])
            # 战利品掉落（进场景物品池）
            current_scene = self._state["current_scene"]
            for item_id in enemy.get("reward_items", []):
                self._state.setdefault("scene_item_states", {}).setdefault(current_scene, []).append(item_id)
                item_name = self._data["items"][item_id]["name"]
                response["log"].append(f"🎁 战利品掉落：【{item_name}】出现在地上！")
            self.end_battle()
            response["success"] = True
            response["message"] = "🏆 战斗胜利！"
            response["defeated"] = enemy["id"]
            return response

        # --- 敌人反击 ---
        e_damage = self._damage(enemy["attack"], player["defense"])
        new_hp = player["hp"] - e_damage
        self._state["player_hp"] = new_hp
        response["log"].append(f"🩸 【{enemy['name']}】反击，对你造成 **{e_damage}** 点伤害！")
        self._bus.publish("COMBAT_DAMAGE", attacker=enemy["id"], defender="player", damage=e_damage)

        # 玩家死了？
        if new_hp <= 0:
            self._bus.publish("COMBAT_DEATH", dead="player")
            response["log"].append("☠️ 你被打倒了...")
            # 玩家死亡：回酒馆满血复活，敌人重置（MVP 不做惩罚）
            self._state["player_hp"] = self._state["player_max_hp"]
            self._state["current_scene"] = "tavern"
            self.end_battle()
            response["success"] = False
            response["message"] = "☠️ 你被打倒了！被好心人救回了酒馆，满血复活。"
            response["player_dead"] = True
            return response

        response["success"] = True
        response["message"] = "⚔️ 回合结束"
        response["player"] = self._get_player_stats()
        return response

    def end_battle(self) -> None:
        """手动结束战斗"""
        self._state["current_battle"] = None

    def reset(self) -> None:
        """重置战斗状态 + 玩家属性"""
        self.end_battle()
        self._state["player_hp"] = self._state.get("player_max_hp", 50)
