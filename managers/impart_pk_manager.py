# managers/impart_pk_manager.py
"""传承PK系统管理器"""
import random
from typing import Tuple, Optional
from ..data import DataBase
from ..models import Player
from .combat_manager import CombatManager, CombatStats

__all__ = ["ImpartPkManager"]


class ImpartPkManager:
    """传承PK管理器 - 玩家间争夺传承的战斗"""
    
    def __init__(self, db: DataBase, combat_mgr: CombatManager, config_manager=None):
        self.db = db
        self.combat_mgr = combat_mgr
        self.config_manager = config_manager

    async def _build_combat_stats(self, player: Player) -> Optional[CombatStats]:
        """构建战斗属性（与切磋/决斗一致：传承加成 + 装备加成）

        原实现调用了 CombatManager 上不存在的 calculate_combat_stats()，
        导致传承挑战必然抛 AttributeError。这里按现有战斗系统的同一套公式构建。
        """
        if not player:
            return None

        impart_info = await self.db.ext.get_impart_info(player.user_id)
        hp_buff = impart_info.impart_hp_per if impart_info else 0.0
        mp_buff = impart_info.impart_mp_per if impart_info else 0.0
        atk_buff = impart_info.impart_atk_per if impart_info else 0.0

        max_hp, max_mp = self.combat_mgr.calculate_hp_mp(player.experience, hp_buff, mp_buff)

        magic_damage = player.magic_damage
        physical_damage = player.physical_damage
        defense = player.physical_defense + player.magic_defense

        if self.config_manager:
            from ..core import EquipmentManager

            equipment_mgr = EquipmentManager(self.db, self.config_manager)
            equipped_items = equipment_mgr.get_equipped_items(
                player,
                self.config_manager.items_data,
                self.config_manager.weapons_data,
            )
            total_attrs = player.get_total_attributes(equipped_items)
            magic_damage = total_attrs.get("magic_damage", magic_damage)
            physical_damage = total_attrs.get("physical_damage", physical_damage)

        atk = self.combat_mgr.calculate_atk(
            player.experience,
            player.cultivation_type,
            magic_damage,
            physical_damage,
            player.atkpractice,
            atk_buff,
        )

        return CombatStats(
            user_id=player.user_id,
            name=player.user_name if player.user_name else f"道友{player.user_id}",
            hp=max_hp,
            max_hp=max_hp,
            mp=max_mp,
            max_mp=max_mp,
            atk=atk,
            defense=defense,
            exp=player.experience,
        )

    async def challenge_impart(self, attacker: Player, defender: Player) -> Tuple[bool, str, dict]:
        """发起传承挑战
        
        Args:
            attacker: 挑战者
            defender: 被挑战者
            
        Returns:
            (attacker_wins, battle_log, rewards)
        """
        # 获取双方传承等级
        attacker_impart = await self.db.ext.get_impart_info(attacker.user_id)
        defender_impart = await self.db.ext.get_impart_info(defender.user_id)
        
        # 准备战斗属性
        atk_stats = await self._build_combat_stats(attacker)
        def_stats = await self._build_combat_stats(defender)
        if not atk_stats or not def_stats:
            return False, "❌ 战斗属性计算失败（玩家数据异常）", {}
        
        # 战斗模拟
        atk_hp = atk_stats.hp
        def_hp = def_stats.hp
        
        rounds = 0
        max_rounds = 20
        battle_log = []
        
        while atk_hp > 0 and def_hp > 0 and rounds < max_rounds:
            rounds += 1
            
            # 攻击者出手
            damage = max(1, atk_stats.atk - def_stats.defense // 2)
            damage = int(damage * random.uniform(0.8, 1.2))
            def_hp -= damage
            battle_log.append(f"第{rounds}回合: {attacker.user_name or attacker.user_id} 造成 {damage} 伤害")
            
            if def_hp <= 0:
                break
            
            # 防守者反击
            counter_damage = max(1, def_stats.atk - atk_stats.defense // 2)
            counter_damage = int(counter_damage * random.uniform(0.8, 1.2))
            atk_hp -= counter_damage
            battle_log.append(f"第{rounds}回合: {defender.user_name or defender.user_id} 反击 {counter_damage}")
        
        # 判定胜负
        attacker_wins = def_hp <= 0 or (atk_hp > 0 and atk_hp >= def_hp)
        
        rewards = {}
        if attacker_wins:
            # 胜利奖励：获得传承加成
            impart_gain = random.uniform(0.01, 0.05)  # 1%-5%
            if attacker_impart:
                new_atk_per = min(1.0, attacker_impart.impart_atk_per + impart_gain)
                attacker_impart.impart_atk_per = new_atk_per
                await self.db.ext.update_impart_info(attacker_impart)
                rewards["impart_atk_gain"] = impart_gain
            
            # 失败惩罚
            if defender_impart and defender_impart.impart_atk_per > 0:
                loss = min(impart_gain / 2, defender_impart.impart_atk_per)
                defender_impart.impart_atk_per -= loss
                await self.db.ext.update_impart_info(defender_impart)
                rewards["defender_loss"] = loss
        else:
            # 失败惩罚：损失修为
            exp_loss = int(attacker.experience * 0.01)  # 1%
            attacker.experience = max(0, attacker.experience - exp_loss)
            await self.db.update_player(attacker)
            rewards["exp_loss"] = exp_loss
        
        return attacker_wins, "\n".join(battle_log[-6:]), rewards  # 只返回最后6条log
    
    async def get_impart_ranking(self, limit: int = 10) -> list:
        """获取传承排行榜"""
        # 查询所有传承数据，按攻击加成排序
        async with self.db.conn.execute(
            """
            SELECT user_id, impart_hp_per, impart_mp_per, impart_atk_per, 
                   impart_know_per, impart_burst_per
            FROM impart_info 
            ORDER BY impart_atk_per DESC 
            LIMIT ?
            """,
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                user_id = row[0]
                player = await self.db.get_player_by_id(user_id)
                if player:
                    total_per = row[1] + row[2] + row[3] + row[4] + row[5]
                    results.append({
                        "user_id": user_id,
                        "user_name": player.user_name or user_id[:8],
                        "atk_per": row[3],
                        "total_per": total_per
                    })
            return results
