# handlers/combat_handlers.py
import re
import time
import random
from astrbot.api.event import AstrMessageEvent
from astrbot.api.all import *
from ..managers.combat_manager import CombatManager, CombatStats
from ..data.data_manager import DataBase
from .utils import player_required
from ..models import Player
from ..models_extended import UserStatus

# 战斗冷却配置（秒）
DUEL_COOLDOWN = 300  # 决斗冷却5分钟
SPAR_COOLDOWN = 60   # 切磋冷却1分钟

class CombatHandlers:
    def __init__(self, db: DataBase, combat_mgr: CombatManager, config_manager=None):
        self.db = db
        self.combat_mgr = combat_mgr
        self.config_manager = config_manager
    
    async def _get_combat_cooldown(self, user_id: str) -> dict:
        """获取战斗冷却信息"""
        try:
            async with self.db.conn.execute(
                "SELECT last_duel_time, last_spar_time FROM combat_cooldowns WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"last_duel_time": row[0], "last_spar_time": row[1]}
        except Exception as e:
            from astrbot.api import logger
            logger.warning(f"获取战斗冷却失败: {e}")
        return {"last_duel_time": 0, "last_spar_time": 0}
    
    async def _update_combat_cooldown(self, user_id: str, combat_type: str):
        """更新战斗冷却时间"""
        now = int(time.time())
        try:
            if combat_type == "duel":
                await self.db.conn.execute(
                    """
                    INSERT INTO combat_cooldowns (user_id, last_duel_time, last_spar_time)
                    VALUES (?, ?, 0)
                    ON CONFLICT(user_id) DO UPDATE SET last_duel_time = ?
                    """,
                    (user_id, now, now)
                )
            else:
                await self.db.conn.execute(
                    """
                    INSERT INTO combat_cooldowns (user_id, last_duel_time, last_spar_time)
                    VALUES (?, 0, ?)
                    ON CONFLICT(user_id) DO UPDATE SET last_spar_time = ?
                    """,
                    (user_id, now, now)
                )
            await self.db.conn.commit()
        except Exception as e:
            from astrbot.api import logger
            logger.warning(f"更新战斗冷却失败: {e}")

    async def _get_target_id(self, event: AstrMessageEvent, arg: str) -> str:
        message_chain = []
        if hasattr(event, "message_obj") and event.message_obj:
            message_chain = getattr(event.message_obj, "message", []) or []

        for component in message_chain:
            if isinstance(component, At):
                candidate = None
                for attr in ("qq", "target", "uin", "user_id"):
                    candidate = getattr(component, attr, None)
                    if candidate:
                        break
                if candidate:
                    return str(candidate).lstrip("@")

        if arg:
            cleaned = arg.strip().lstrip("@")
            if cleaned.isdigit():
                return cleaned

        message_text = ""
        if hasattr(event, "get_message_str"):
            message_text = event.get_message_str() or ""
        match = re.search(r'(\d{5,})', message_text)
        if match:
            return match.group(1)
        return None

    def _calculate_equipment_bonus(self, player) -> dict:
        """计算装备提供的属性加成"""
        bonus = {"atk": 0, "defense": 0}
        if not self.config_manager:
            return bonus
            
        # 武器
        if player.weapon and player.weapon in self.config_manager.weapons_data:
            data = self.config_manager.weapons_data[player.weapon]
            bonus["atk"] += data.get("atk", 0)
            bonus["atk"] += data.get("physical_damage", 0)
            bonus["atk"] += data.get("magic_damage", 0)
        
        # 防具
        if player.armor and player.armor in self.config_manager.items_data:
            data = self.config_manager.items_data[player.armor]
            bonus["defense"] += data.get("physical_defense", 0)
            bonus["defense"] += data.get("magic_defense", 0)
            
        return bonus

    async def _prepare_combat_stats(self, user_id: str, restore: bool = False) -> CombatStats:
        player = await self.db.get_player_by_id(user_id)
        if not player:
            return None
        
        # 获取基础属性
        # 注意：这里我们重新计算属性以确保即时性，特别是Buff
        impart_info = await self.db.ext.get_impart_info(user_id)
        hp_buff = impart_info.impart_hp_per if impart_info else 0.0
        mp_buff = impart_info.impart_mp_per if impart_info else 0.0
        atk_buff = impart_info.impart_atk_per if impart_info else 0.0
        
        # 计算属性
        max_hp, max_mp = self.combat_mgr.calculate_hp_mp(player.experience, hp_buff, mp_buff)
        hp, mp = (max_hp, max_mp) if restore or player.atk == 0 else (player.hp, player.mp)
        base_atk = self.combat_mgr.calculate_atk(player.experience, player.atkpractice, atk_buff)
        active_effects = player.get_active_pill_effects()
        now = int(time.time())
        for effect in active_effects:
            if effect.get("expiry_time", 0) > now and effect.get("subtype") in {"duel_debuff", "duel_buff"}:
                base_atk = int(base_atk * effect.get("attack_multiplier", 1.0))
        
        # 加上装备加成
        equip_bonus = self._calculate_equipment_bonus(player)
        final_atk = base_atk + equip_bonus["atk"]
        defense_multiplier = 1.0
        for effect in active_effects:
            if effect.get("expiry_time", 0) > now and effect.get("subtype") in {"duel_debuff", "duel_buff"}:
                defense_multiplier *= effect.get("defense_multiplier", 1.0)
        final_defense = int(equip_bonus["defense"] * defense_multiplier)
        
        # 更新Player对象（可选，为了持久化）
        player.hp = hp
        player.mp = mp
        player.atk = final_atk
        await self.db.update_player(player)

        return CombatStats(
            user_id=user_id,
            name=player.user_name if player.user_name else f"道友{user_id}",
            hp=hp,
            max_hp=hp,
            mp=mp,
            max_mp=mp,
            atk=final_atk,
            defense=final_defense,
            exp=player.experience
        )

    async def handle_duel(self, event: AstrMessageEvent, target: str):
        """决斗 (消耗气血)"""
        user_id = event.get_sender_id()
        target_id = await self._get_target_id(event, target)
        
        if not target_id:
            yield event.plain_result("❌ 请指定决斗目标")
            return
            
        if user_id == target_id:
            yield event.plain_result("❌ 不能和自己决斗")
            return

        # 检查发起者状态
        user_cd = await self.db.ext.get_user_cd(user_id)
        if user_cd and user_cd.type != UserStatus.IDLE:
            current_status = UserStatus.get_name(user_cd.type)
            yield event.plain_result(f"❌ 你当前正在{current_status}，无法进行战斗！")
            return
        
        # 检查目标状态
        target_cd = await self.db.ext.get_user_cd(target_id)
        if target_cd and target_cd.type != UserStatus.IDLE:
            target_status = UserStatus.get_name(target_cd.type)
            yield event.plain_result(f"❌ 对方当前正在{target_status}，无法进行战斗！")
            return

        # 检查冷却
        now = int(time.time())
        cooldown = await self._get_combat_cooldown(user_id)
        last_duel = cooldown.get("last_duel_time", 0)
        if last_duel and (now - last_duel) < DUEL_COOLDOWN:
            remaining = DUEL_COOLDOWN - (now - last_duel)
            yield event.plain_result(f"❌ 决斗冷却中，还需 {remaining // 60} 分 {remaining % 60} 秒")
            return

        # 获取双方数据
        p1_stats = await self._prepare_combat_stats(user_id, restore=False)
        p2_stats = await self._prepare_combat_stats(target_id, restore=False)
        
        if not p1_stats:
            yield event.plain_result("❌ 你还未踏入修仙之路")
            return
        if not p2_stats:
            yield event.plain_result("❌ 对方还未踏入修仙之路")
            return

        # 战斗
        result = self.combat_mgr.player_vs_player(p1_stats, p2_stats, combat_type=2) # 2=决斗
        
        # 结算（更新HP）
        await self.db.ext.update_player_hp_mp(user_id, result['player1_final_hp'], result['player1_final_mp'])
        await self.db.ext.update_player_hp_mp(target_id, result['player2_final_hp'], result['player2_final_mp'])

        loser_id = target_id if result["winner"] == user_id else user_id
        winner_reward_msg = ""
        if result["winner"] in {user_id, target_id}:
            loser = await self.db.get_player_by_id(loser_id)
            winner = await self.db.get_player_by_id(result["winner"])
            effects = loser.get_active_pill_effects()
            effects.append({
                "pill_name": "决斗负伤",
                "subtype": "duel_debuff",
                "expiry_time": int(time.time()) + 3600,
                "attack_multiplier": 0.8,
                "defense_multiplier": 0.8,
            })
            loser.set_active_pill_effects(effects)
            await self.db.update_player(loser)

            if loser.gold >= 100:
                stolen_gold = random.randint(100, min(1000, loser.gold))
                loser.gold -= stolen_gold
                winner.gold += stolen_gold
                winner_reward_msg = f"🏆 决斗奖励：从败者处获得 {stolen_gold:,} 灵石。"
            else:
                winner_effects = winner.get_active_pill_effects()
                winner_effects.append({
                    "pill_name": "决斗胜势",
                    "subtype": "duel_buff",
                    "expiry_time": int(time.time()) + 3600,
                    "attack_multiplier": 1.2,
                    "defense_multiplier": 1.2,
                })
                winner.set_active_pill_effects(winner_effects)
                winner_reward_msg = "🏆 决斗奖励：败者灵石不足，获得【胜势】状态，攻击与防御提高20%，持续1小时。"
            await self.db.update_player(loser)
            await self.db.update_player(winner)
        
        # 更新冷却
        await self._update_combat_cooldown(user_id, "duel")
        
        # 生成战报
        log = "\n".join(result['combat_log'])
        log += "\n\n⚠️ 决斗败者获得【负伤】状态：攻击与防御降低20%，持续1小时。"
        if winner_reward_msg:
            log += f"\n{winner_reward_msg}"
        yield event.plain_result(log)

    async def handle_spar(self, event: AstrMessageEvent, target: str):
        """切磋 (不消耗气血)"""
        user_id = event.get_sender_id()
        target_id = await self._get_target_id(event, target)
        
        if not target_id:
            yield event.plain_result("❌ 请指定切磋目标")
            return

        if user_id == target_id:
            yield event.plain_result("❌ 不能和自己切磋")
            return

        # 检查发起者状态
        user_cd = await self.db.ext.get_user_cd(user_id)
        if user_cd and user_cd.type != UserStatus.IDLE:
            current_status = UserStatus.get_name(user_cd.type)
            yield event.plain_result(f"❌ 你当前正在{current_status}，无法进行战斗！")
            return
        
        # 检查目标状态
        target_cd = await self.db.ext.get_user_cd(target_id)
        if target_cd and target_cd.type != UserStatus.IDLE:
            target_status = UserStatus.get_name(target_cd.type)
            yield event.plain_result(f"❌ 对方当前正在{target_status}，无法进行战斗！")
            return

        # 检查冷却
        now = int(time.time())
        cooldown = await self._get_combat_cooldown(user_id)
        last_spar = cooldown.get("last_spar_time", 0)
        if last_spar and (now - last_spar) < SPAR_COOLDOWN:
            remaining = SPAR_COOLDOWN - (now - last_spar)
            yield event.plain_result(f"❌ 切磋冷却中，还需 {remaining} 秒")
            return

        p1_stats = await self._prepare_combat_stats(user_id, restore=True)
        p2_stats = await self._prepare_combat_stats(target_id, restore=True)
        
        if not p1_stats or not p2_stats:
             yield event.plain_result("❌ 双方都需要踏入修仙之路")
             return

        result = self.combat_mgr.player_vs_player(p1_stats, p2_stats, combat_type=1) # 1=切磋
        
        # 更新冷却
        await self._update_combat_cooldown(user_id, "spar")
        
        log = "\n".join(result['combat_log'])
        yield event.plain_result(f"{log}")
