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

        # 🆕 不再订阅 SCENE_ENTER 自动开怪！改成玩家主动点按钮触发

    # ---------- 事件回调（不再自动开战斗） ----------
    def _on_scene_enter(self, scene_id: str, **kwargs) -> None:
        """🆕 进场景不再自动开战斗！只检查：已有 current_battle 但敌人已死 → 清掉"""
        battle = self._state.get("current_battle")
        if battle:
            killed = self._state.get("killed_enemies", [])
            if battle["enemy_id"] in killed:
                self.end_battle()

    # ---------- 🆕 主动触发接口（给前端按钮点时调用） ----------
    def start_battle(self, enemy_id: str) -> Dict[str, Any]:
        """玩家点"挑战 XX"按钮 → 手动开始/继续战斗
        
        关键：current_battle 永久保留敌人残血，不管玩家在不在这个场景
        """
        # 永久死亡检查
        killed = self._state.get("killed_enemies", [])
        if enemy_id in killed:
            return {"success": False, "message": "这个敌人已经被你打死了"}

        enemy = self._data.get("enemies", {}).get(enemy_id)
        if not enemy:
            return {"success": False, "message": "敌人不存在"}

        # 🆕 残血复用：已有 current_battle 且敌人匹配 → 直接返回（保留残血！）
        existing = self._state.get("current_battle")
        if existing and existing.get("enemy_id") == enemy_id:
            return {"success": True, "message": f"你继续与【{enemy['name']}】战斗！（敌人HP: {existing['enemy_hp']}/{enemy['hp']}）"}

        # 新开战斗 → 满血
        self._state["current_battle"] = {
            "enemy_id": enemy_id,
            "enemy_hp": enemy["hp"],
        }
        self._bus.publish("BATTLE_START", enemy_id=enemy_id)
        return {"success": True, "message": f"你向【{enemy['name']}】发起了攻击！"}

    def list_enemies_in_scene(self, scene_id: str) -> list:
        """🆕 返回场景里活着的敌人列表（给前端渲染按钮用）"""
        killed = self._state.get("killed_enemies", [])
        return [
            {"id": e["id"], "name": e["name"]}
            for e in self._get_scene_enemies(scene_id)
            if e["id"] not in killed
        ]

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
        """获取当前战斗的可渲染数据（给前端用）
        
        🆕 关键改进：只有当玩家在敌人所在场景时才 active=true
        否则 active=false 但**不清 current_battle**（保留敌人残血！）
        """
        battle = self._state.get("current_battle")
        player = self._get_player_stats()
        if not battle:
            return {"active": False, "player": player}

        enemy = self._get_current_enemy()
        if not enemy:
            self.end_battle()
            return {"active": False, "player": player}

        # 🆕 检查玩家当前场景有没有这个敌人 —— 没有就返回 inactive（但保留残血）
        current_scene = self._state.get("current_scene", "")
        scene_enemy_ids = self._data.get("scenes", {}).get(current_scene, {}).get("enemies_here", [])
        if battle["enemy_id"] not in scene_enemy_ids:
            return {"active": False, "player": player}

        return {
            "active": True,
            "enemy": {
                "id": enemy["id"],
                "name": enemy["name"],
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
            response["message"] = "当前没有战斗"
            return response

        enemy = self._get_current_enemy()
        if not enemy:
            self.end_battle()
            response["message"] = "敌人不见了"
            return response

        player = self._get_player_stats()
        # 检查武器：必须在玩家背包里，且 is_weapon=true
        weapon = self._data.get("items", {}).get(weapon_id, {})
        if weapon_id not in self._state.get("player_inventory", []):
            response["message"] = f"你没有【{weapon.get('name', weapon_id)}】"
            return response
        if not weapon.get("is_weapon"):
            response["message"] = f"【{weapon.get('name', weapon_id)}】不是武器"
            return response

        # --- 玩家攻击 ---
        weapon_dmg = weapon.get("damage", 5)
        p_damage = self._damage(weapon_dmg, enemy["defense"])
        battle["enemy_hp"] -= p_damage
        response["log"].append(f"你用【{weapon['name']}】对【{enemy['name']}】造成 **{p_damage}** 点伤害！")
        self._bus.publish("COMBAT_DAMAGE", attacker="player", defender=enemy["id"], damage=p_damage)

        # 敌人死了？
        if battle["enemy_hp"] <= 0:
            response["log"].append(f"【{enemy['name']}】被你打倒了！")
            self._bus.publish("COMBAT_DEATH", dead=enemy["id"])
            # 语义干净的击杀事件（玩家死亡不发），供事件规则做"击杀任务/掉落"
            self._bus.publish("ENEMY_KILLED", enemy_id=enemy["id"])
            # 🆕 关键：标记永久死亡 → 以后再进洞穴不会复活了
            self._state.setdefault("killed_enemies", [])
            if enemy["id"] not in self._state["killed_enemies"]:
                self._state["killed_enemies"].append(enemy["id"])
            # 战利品掉落（进场景物品池）
            current_scene = self._state["current_scene"]
            for item_id in enemy.get("reward_items", []):
                self._state.setdefault("scene_item_states", {}).setdefault(current_scene, []).append(item_id)
                item_name = self._data["items"][item_id]["name"]
                response["log"].append(f"战利品掉落：【{item_name}】出现在地上！")
            # 金币掉落（直接入余额，不占背包）
            reward_gold = int(enemy.get("reward_gold", 0))
            if reward_gold > 0:
                self._state["player_gold"] = self._state.get("player_gold", 0) + reward_gold
                response["log"].append(
                    f"战利品：金币 +{reward_gold}（当前金币：{self._state['player_gold']}）")
            self.end_battle()
            response["success"] = True
            response["message"] = "战斗胜利！"
            response["defeated"] = enemy["id"]
            return response

        # --- 敌人反击 ---
        e_damage = self._damage(enemy["attack"], player["defense"])
        new_hp = player["hp"] - e_damage
        self._state["player_hp"] = new_hp
        response["log"].append(f"【{enemy['name']}】反击，对你造成 **{e_damage}** 点伤害！")
        self._bus.publish("COMBAT_DAMAGE", attacker=enemy["id"], defender="player", damage=e_damage)

        # 玩家死了？
        if new_hp <= 0:
            self._bus.publish("COMBAT_DEATH", dead="player")
            self._handle_player_death(response)
            return response

        response["success"] = True
        response["message"] = "回合结束"
        response["player"] = self._get_player_stats()
        return response

    def use_item_in_battle(self, item_id: str) -> Dict[str, Any]:
        """战斗中使用消耗品（如喝治疗药水）：回血 + 消耗一瓶 + 敌人趁机反击一回合"""
        response = {"success": False, "message": "", "log": []}
        battle = self._state.get("current_battle")
        if not battle:
            response["message"] = "当前没有战斗"
            return response

        enemy = self._get_current_enemy()
        if not enemy:
            self.end_battle()
            response["message"] = "敌人不见了"
            return response

        # 校验物品：在背包里 + 标记 usable
        if item_id not in self._state.get("player_inventory", []):
            response["message"] = "你没有这个物品"
            return response
        item = self._data.get("items", {}).get(item_id, {})
        if not item.get("usable"):
            response["message"] = f"【{item.get('name', item_id)}】不能在战斗中使用"
            return response

        # 回血（满血时拒绝，防止白白浪费一瓶）
        heal = int(item.get("heal", 0))
        if heal > 0:
            hp = self._state.get("player_hp", 0)
            max_hp = self._state.get("player_max_hp", hp)
            if hp >= max_hp:
                response["message"] = "生命值已满，不需要使用治疗药水"
                return response
            healed = min(heal, max_hp - hp)
            self._state["player_hp"] = hp + healed
            response["log"].append(
                f"你喝下【{item.get('name', item_id)}】，恢复 **{healed}** 点生命！")

        # 消耗一瓶，再发事件
        self._state["player_inventory"].remove(item_id)
        self._bus.publish("ITEM_USED", item_id=item_id, target_id=enemy["id"])

        # 喝药占用一回合 → 敌人趁机反击
        player = self._get_player_stats()
        e_damage = self._damage(enemy["attack"], player["defense"])
        new_hp = self._state["player_hp"] - e_damage
        self._state["player_hp"] = new_hp
        response["log"].append(
            f"【{enemy['name']}】趁机反击，对你造成 **{e_damage}** 点伤害！")
        self._bus.publish("COMBAT_DAMAGE", attacker=enemy["id"], defender="player", damage=e_damage)

        if new_hp <= 0:
            self._bus.publish("COMBAT_DEATH", dead="player")
            self._handle_player_death(response)
            return response

        response["success"] = True
        response["message"] = "回合结束"
        response["player"] = self._get_player_stats()
        return response

    def _handle_player_death(self, response: Dict[str, Any]) -> None:
        """玩家被打倒的统一处理：回酒馆满血复活，结束战斗（MVP 无惩罚）"""
        response["log"].append("你被打倒了...")
        # 玩家死亡：回酒馆满血复活，敌人重置（MVP 不做惩罚）
        self._state["player_hp"] = self._state["player_max_hp"]
        self._state["current_scene"] = "tavern"
        self.end_battle()
        response["success"] = False
        response["message"] = "你被打倒了！被好心人救回了酒馆，满血复活。"
        response["player_dead"] = True

    def end_battle(self) -> None:
        """手动结束战斗"""
        self._state["current_battle"] = None

    def reset(self) -> None:
        """重置战斗状态 + 玩家属性"""
        self.end_battle()
        self._state["player_hp"] = self._state.get("player_max_hp", 50)
