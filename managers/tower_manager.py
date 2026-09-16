# managers/tower_manager.py
"""爬塔系统管理器

纯战力检测的100层挑战塔，一键挑战到战力极限。
周榜系统，每周一自动重置排行榜。
"""

import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger

from ..data.data_manager import DataBase
from ..models import Player

__all__ = ["TowerManager"]

SEP = "━━━━━━━━━━━━━━━"


class TowerManager:
    """爬塔系统管理器"""

    CONFIG_FILE = Path(__file__).resolve().parents[1] / "config" / "tower_config.json"

    def __init__(
        self,
        db: DataBase,
        config_manager=None,
        storage_ring_manager=None,
        equipment_manager=None,
    ):
        self.db = db
        self.config_manager = config_manager
        self.storage = storage_ring_manager
        self.equipment_manager = equipment_manager
        self._config_cache = None

    # ==================== 配置 ====================

    def _config(self) -> dict:
        """获取爬塔配置"""
        if self._config_cache is not None:
            return self._config_cache

        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                self._config_cache = config.get("tower_config", {})
                return self._config_cache
        except Exception as e:
            logger.warning(f"[爬塔] 读取配置失败: {e}")
            return {}

    def _total_floors(self) -> int:
        """总层数"""
        return int(self._config().get("total_floors", 100))

    def _floor_power_requirement(self, floor: int) -> int:
        """计算指定层数的战力要求（线性插值）"""
        requirements = self._config().get("floor_power_requirements", {})

        # 如果直接有配置，返回配置值
        if str(floor) in requirements:
            return int(requirements[str(floor)])

        # 否则进行线性插值
        keys = sorted([int(k) for k in requirements.keys()])

        if floor <= keys[0]:
            return int(requirements[str(keys[0])])
        if floor >= keys[-1]:
            return int(requirements[str(keys[-1])])

        # 找到floor所在的区间
        for i in range(len(keys) - 1):
            if keys[i] <= floor <= keys[i + 1]:
                lower_floor = keys[i]
                upper_floor = keys[i + 1]
                lower_power = int(requirements[str(lower_floor)])
                upper_power = int(requirements[str(upper_floor)])

                # 线性插值
                ratio = (floor - lower_floor) / (upper_floor - lower_floor)
                return int(lower_power + (upper_power - lower_power) * ratio)

        return 100

    def _milestone_rewards(self) -> Dict[int, dict]:
        """里程碑奖励配置"""
        milestones = self._config().get("milestone_rewards", {})
        return {int(k): v for k, v in milestones.items()}

    def _floor_rewards(self) -> dict:
        """每层基础奖励"""
        return self._config().get("floor_rewards", {
            "spirit_stone_per_floor": 500,
            "exp_per_floor": 2000
        })

    # ==================== 战力计算 ====================

    def calculate_combat_power(self, player: Player) -> int:
        """计算玩家综合战力

        战力公式：(物攻 + 法攻) × 0.4 + (物防 + 法防) × 0.3 + 神识 × 0.2
        """
        try:
            equipped_items = self._get_player_equipped_items(player)
            pill_multipliers = self._get_player_pill_multipliers(player)
            total_attrs = player.get_total_attributes(equipped_items, pill_multipliers)
        except Exception as e:
            logger.warning(f"[爬塔] 计算战力失败: {e}")
            return 0

        total_attack = total_attrs.get("physical_damage", 0) + total_attrs.get("magic_damage", 0)
        total_defense = total_attrs.get("physical_defense", 0) + total_attrs.get("magic_defense", 0)
        mental = total_attrs.get("mental_power", 0)

        combat_power = int(total_attack * 0.4 + total_defense * 0.3 + mental * 0.2)
        return combat_power

    def _get_player_equipped_items(self, player: Player) -> List:
        """获取玩家装备的物品列表"""
        equipped = []
        equipment_manager = self.equipment_manager
        if not equipment_manager:
            return equipped

        try:
            # 获取武器
            if player.weapon:
                weapon = equipment_manager.get_equipment_by_name(player.weapon)
                if weapon:
                    equipped.append(weapon)

            # 获取防具
            if player.armor:
                armor = equipment_manager.get_equipment_by_name(player.armor)
                if armor:
                    equipped.append(armor)

            # 获取主修心法
            if player.main_technique:
                technique = equipment_manager.get_equipment_by_name(player.main_technique)
                if technique:
                    equipped.append(technique)

            # 获取功法列表
            for tech_name in player.get_techniques_list():
                tech = equipment_manager.get_equipment_by_name(tech_name)
                if tech:
                    equipped.append(tech)
        except Exception as e:
            logger.warning(f"[爬塔] 获取装备列表失败: {e}")

        return equipped

    def _get_player_pill_multipliers(self, player: Player) -> dict:
        """获取玩家当前丹药buff倍率"""
        try:
            from ..core.pill_manager import PillManager

            pill_manager = PillManager(self.db, self.config_manager)
            return pill_manager.get_active_multipliers(player)
        except Exception as e:
            logger.warning(f"[爬塔] 获取丹药buff失败: {e}")
            return {}

    # ==================== 周榜重置 ====================

    def _get_week_key(self) -> str:
        """获取当前周的标识（周一为一周的开始）"""
        now = datetime.now()
        # 获取本周一的日期
        monday = now - timedelta(days=now.weekday())
        return monday.strftime("%Y-W%W")

    async def check_and_reset_weekly(self):
        """检查并重置周榜（每周一重置）"""
        config = self._config().get("weekly_reset", {})
        if not config.get("enabled", True):
            return

        current_week = self._get_week_key()

        try:
            # 检查是否需要重置
            async with self.db.conn.execute(
                "SELECT value FROM system_config WHERE key = 'tower_current_week'"
            ) as cursor:
                row = await cursor.fetchone()

            if row and row["value"] == current_week:
                # 当前周已记录，无需重置
                return

            # 需要重置：清空所有记录
            await self.db.conn.execute("DELETE FROM tower_records")

            # 更新周标识
            await self.db.conn.execute(
                """
                INSERT OR REPLACE INTO system_config (key, value, update_time)
                VALUES ('tower_current_week', ?, ?)
                """,
                (current_week, int(time.time()))
            )

            await self.db.conn.commit()
            logger.info(f"[爬塔] 周榜已重置，新周期：{current_week}")

        except Exception as e:
            logger.error(f"[爬塔] 周榜重置失败: {e}")

    # ==================== 挑战逻辑 ====================

    async def challenge_tower(self, player: Player) -> Tuple[bool, str]:
        """挑战爬塔：根据战力自动通过所有能过的层数"""

        # 检查玩家状态
        if getattr(player, "is_soul_state", False):
            return False, "⚠️ 元神状态无法挑战爬塔，请先复活"

        # 检查周榜重置
        await self.check_and_reset_weekly()

        # 计算玩家战力
        combat_power = self.calculate_combat_power(player)

        if combat_power <= 0:
            return False, "❌ 战力不足，无法挑战爬塔"

        # 获取玩家当前最高记录
        record = await self._get_tower_record(player.user_id)
        current_max = record.get("max_floor", 0) if record else 0

        # 计算能通过的最高层数
        max_floor = self._calculate_max_floor(combat_power)

        if max_floor <= current_max:
            return False, (
                f"⚔️ 当前战力：{combat_power:,}\n"
                f"{SEP}\n"
                f"你的战力不足以突破更高的层数\n"
                f"💡 当前最高记录：第{current_max}层\n"
                f"💡 提升装备、境界或使用丹药增强战力后再来挑战"
            )

        # 计算奖励（只计算新通过的层数）
        start_floor = current_max + 1
        rewards = self._calculate_rewards(start_floor, max_floor)

        # 发放奖励
        stored, failed = await self._grant_rewards(player.user_id, rewards)

        # 更新记录
        await self._update_tower_record(player.user_id, max_floor, rewards)

        # 生成结果消息
        msg = self._format_challenge_result(
            player.user_name or f"道友{player.user_id[:6]}",
            combat_power,
            start_floor,
            max_floor,
            rewards,
            failed
        )

        return True, msg

    def _calculate_max_floor(self, combat_power: int) -> int:
        """根据战力计算能通过的最高层数"""
        total = self._total_floors()

        for floor in range(1, total + 1):
            required = self._floor_power_requirement(floor)
            if combat_power < required:
                return floor - 1

        return total

    def _calculate_rewards(self, start_floor: int, end_floor: int) -> dict:
        """计算通关奖励（从start_floor到end_floor）"""
        rewards = {
            "spirit_stone": 0,
            "exp": 0,
            "items": [],
            "titles": []
        }

        floor_rewards = self._floor_rewards()
        stone_per_floor = floor_rewards.get("spirit_stone_per_floor", 500)
        exp_per_floor = floor_rewards.get("exp_per_floor", 2000)

        # 基础奖励（每层累加）
        for floor in range(start_floor, end_floor + 1):
            rewards["spirit_stone"] += stone_per_floor * floor
            rewards["exp"] += exp_per_floor * floor

        # 里程碑奖励
        milestones = self._milestone_rewards()
        for milestone_floor, milestone_reward in milestones.items():
            if start_floor <= milestone_floor <= end_floor:
                rewards["spirit_stone"] += milestone_reward.get("spirit_stone", 0)

                # 装备奖励
                equipment_rank = milestone_reward.get("equipment_rank")
                if equipment_rank:
                    equipment = self._generate_random_equipment(equipment_rank)
                    if equipment:
                        rewards["items"].append(equipment)

                # 称号奖励
                title = milestone_reward.get("title")
                if title:
                    rewards["titles"].append(title)

        return rewards

    def _generate_random_equipment(self, rank: str) -> Optional[str]:
        """生成随机装备（根据品质）"""
        if not self.equipment_manager:
            return None

        try:
            # 从装备管理器获取对应品质的装备
            all_items = []

            # 获取武器
            if hasattr(self.config_manager, "weapons_data"):
                weapons = [w for w in self.config_manager.weapons_data if w.get("rank") == rank]
                all_items.extend(weapons)

            # 获取防具
            if hasattr(self.config_manager, "items_data"):
                armors = [i for i in self.config_manager.items_data
                         if i.get("type") == "防具" and i.get("rank") == rank]
                all_items.extend(armors)

            if all_items:
                item = random.choice(all_items)
                return item.get("name")
        except Exception as e:
            logger.warning(f"[爬塔] 生成装备失败: {e}")

        return None

    async def _grant_rewards(self, user_id: str, rewards: dict) -> Tuple[List[str], List[str]]:
        """发放奖励

        Returns:
            (成功入库的物品清单, 储物戒空间不足而丢失的物品清单)
        """
        stored: List[str] = []
        failed: List[str] = []

        player = await self.db.get_player_by_id(user_id)
        if not player:
            return stored, failed

        # 发放灵石和修为
        stone = int(rewards.get("spirit_stone", 0))
        exp = int(rewards.get("exp", 0))
        if stone or exp:
            player.gold += stone
            player.experience += exp
            await self.db.update_player(player)

        # 发放称号
        for title in rewards.get("titles", []):
            try:
                await self.db.grant_title(user_id, title)
            except Exception as e:
                logger.warning(f"[爬塔] 授予称号 {title} 失败: {e}")

        # 发放物品
        for item_name in rewards.get("items", []):
            if self.storage is None:
                failed.append(item_name)
                continue
            try:
                ok, _ = await self.storage.store_item(player, item_name, 1, silent=True)
            except Exception as e:
                logger.warning(f"[爬塔] 发放物品 {item_name} 失败: {e}")
                ok = False
            if ok:
                stored.append(item_name)
            else:
                failed.append(item_name)

        return stored, failed

    # ==================== 数据库操作 ====================

    async def _get_tower_record(self, user_id: str) -> Optional[dict]:
        """获取玩家爬塔记录"""
        try:
            async with self.db.conn.execute(
                "SELECT * FROM tower_records WHERE user_id = ?",
                (str(user_id),)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.warning(f"[爬塔] 查询记录失败: {e}")
            return None

    async def _update_tower_record(self, user_id: str, max_floor: int, rewards: dict):
        """更新玩家爬塔记录"""
        try:
            now = int(time.time())
            total_stone = int(rewards.get("spirit_stone", 0))
            total_exp = int(rewards.get("exp", 0))

            await self.db.conn.execute(
                """
                INSERT OR REPLACE INTO tower_records
                (user_id, max_floor, last_challenge_time, total_spirit_stone, total_exp)
                VALUES (?, ?, ?,
                    COALESCE((SELECT total_spirit_stone FROM tower_records WHERE user_id = ?), 0) + ?,
                    COALESCE((SELECT total_exp FROM tower_records WHERE user_id = ?), 0) + ?
                )
                """,
                (str(user_id), max_floor, now, str(user_id), total_stone, str(user_id), total_exp)
            )
            await self.db.conn.commit()
        except Exception as e:
            logger.error(f"[爬塔] 更新记录失败: {e}")

    async def get_ranking(self, limit: int = 10) -> List[dict]:
        """获取爬塔排行榜（按最高层数排序）"""
        try:
            async with self.db.conn.execute(
                """
                SELECT user_id, max_floor, total_spirit_stone, total_exp, last_challenge_time
                FROM tower_records
                ORDER BY max_floor DESC, last_challenge_time ASC
                LIMIT ?
                """,
                (int(limit),)
            ) as cursor:
                rows = await cursor.fetchall()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.warning(f"[爬塔] 查询排行榜失败: {e}")
            return []

    # ==================== 展示 ====================

    async def format_tower_info(self, player: Player) -> str:
        """查看爬塔信息"""
        combat_power = self.calculate_combat_power(player)
        record = await self._get_tower_record(player.user_id)

        current_max = record.get("max_floor", 0) if record else 0
        max_reachable = self._calculate_max_floor(combat_power)

        lines = [
            "🗼 爬塔系统",
            SEP,
            f"⚔️ 当前战力：{combat_power:,}",
            f"🏆 最高记录：第 {current_max} 层",
            f"📊 可挑战至：第 {max_reachable} 层",
            SEP,
        ]

        if max_reachable > current_max:
            lines.append(f"✅ 可以挑战更高层数！发送「挑战爬塔」开始")
        else:
            next_floor = current_max + 1
            if next_floor <= self._total_floors():
                required_power = self._floor_power_requirement(next_floor)
                gap = required_power - combat_power
                lines.append(f"❌ 战力不足以挑战第 {next_floor} 层")
                lines.append(f"💡 还需提升 {gap:,} 战力")
            else:
                lines.append("🎉 你已通关所有层数！")

        lines.append(SEP)
        lines.append("💡 提升装备、境界或使用丹药可增强战力")

        return "\n".join(lines)

    def _format_challenge_result(
        self,
        player_name: str,
        combat_power: int,
        start_floor: int,
        end_floor: int,
        rewards: dict,
        failed_items: List[str]
    ) -> str:
        """格式化挑战结果"""
        lines = [
            "【挑战爬塔】",
            f"⚔️ 当前战力：{combat_power:,}",
            "挑战中...",
            "",
            f"✅ 成功通过第 {start_floor} 层 → 第 {end_floor} 层！",
            SEP,
            "获得奖励：",
            f"💰 灵石：{rewards['spirit_stone']:,}",
            f"✨ 修为：{rewards['exp']:,}",
        ]

        if rewards.get("items"):
            items_str = "、".join(rewards["items"])
            lines.append(f"🎁 装备：{items_str}")

        if rewards.get("titles"):
            titles_str = "、".join(rewards["titles"])
            lines.append(f"🏅 称号：{titles_str}")

        if failed_items:
            lines.append(SEP)
            lines.append(f"⚠️ 储物戒已满，未能入库：{'、'.join(failed_items)}")

        lines.append(SEP)
        lines.append(f"🏆 当前最高记录：第 {end_floor} 层")

        return "\n".join(lines)

    async def format_ranking(self) -> str:
        """格式化排行榜"""
        ranking = await self.get_ranking(10)

        if not ranking:
            return (
                "🏆 爬塔周榜（本周暂无记录）\n"
                f"{SEP}\n"
                "💡 发送「挑战爬塔」开始挑战"
            )

        current_week = self._get_week_key()
        lines = [
            f"🏆 爬塔周榜（{current_week}）",
            SEP,
        ]

        for idx, record in enumerate(ranking, 1):
            user_id = record["user_id"]
            max_floor = record["max_floor"]

            # 获取玩家名称
            try:
                player = await self.db.get_player_by_id(user_id)
                player_name = player.user_name if player and player.user_name else f"道友{user_id[:6]}"
            except Exception:
                player_name = f"道友{user_id[:6]}"

            medal = ""
            if idx == 1:
                medal = "🥇 "
            elif idx == 2:
                medal = "🥈 "
            elif idx == 3:
                medal = "🥉 "

            lines.append(f"{medal}{idx}. {player_name} - 第{max_floor}层")

        lines.append(SEP)
        lines.append("💡 每周一重置排行榜")

        return "\n".join(lines)
