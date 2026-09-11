# data/data_manager.py

import aiosqlite
import json
import time
from dataclasses import fields
from pathlib import Path
from typing import Tuple, List, Optional
from astrbot.api import logger
from ..models import Player
from .database_extended import DatabaseExtended, begin_immediate

# 获取 Player 模型的所有字段名（用于过滤数据库中的多余字段，作为迁移未完成时的兼容）
PLAYER_FIELDS = {f.name for f in fields(Player)}

class DataBase:
    """数据库管理类，提供基础玩家操作"""

    def __init__(self, db_file: str = "xiuxian_data_lite.db"):
        self.db_path = Path(db_file)
        self.conn: aiosqlite.Connection = None
        self.ext: Optional[DatabaseExtended] = None  # 扩展操作类

    async def connect(self):
        """连接数据库"""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        self.ext = DatabaseExtended(self.conn)  # 初始化扩展操作

    async def begin_immediate(self):
        """开启 IMMEDIATE 写事务（若连接上残留未提交事务会自动回滚清理）"""
        await begin_immediate(self.conn)

    async def close(self):
        """关闭数据库连接"""
        if self.conn:
            try:
                await self.conn.close()
            finally:
                self.conn = None
                self.ext = None

    async def reconnect(self):
        """重连数据库（用于连接意外断开时）"""
        await self.close()
        await self.connect()

    def _connection_alive(self) -> bool:
        """检测底层aiosqlite连接是否仍然可用"""
        if not self.conn:
            return False
        # aiosqlite Connection 在 close 后会将 _connection 置为 None
        return getattr(self.conn, "_connection", None) is not None

    async def ensure_connection(self):
        """确保数据库连接可用，必要时自动重连"""
        if self._connection_alive():
            return
        logger.warning("[database] 检测到数据库连接断开，正在自动重连...")
        await self.reconnect()

    async def create_player(self, player: Player):
        """创建新玩家"""
        await self.conn.execute(
            """
            INSERT INTO players (
                user_id, level_index, spiritual_root,cultivation_type, user_name, lifespan,
                experience, gold, state, cultivation_start_time, last_check_in_date, level_up_rate,
                weapon, armor, main_technique, techniques,
                hp, mp, atk, atkpractice,
                spiritual_qi, max_spiritual_qi, blood_qi, max_blood_qi,
                magic_damage, physical_damage, magic_defense, physical_defense, mental_power,
                sect_id, sect_position, sect_contribution, sect_task, sect_elixir_get,
                blessed_spot_flag, blessed_spot_name,
                active_pill_effects, permanent_pill_gains, has_resurrection_pill, has_debuff_shield, pills_inventory,
                storage_ring, storage_ring_items,
                daily_pill_usage, last_daily_reset,
                is_soul_state, soul_death_time, soul_exp_before_death, used_rebirth
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                player.user_id,
                player.level_index,
                player.spiritual_root,
                player.cultivation_type,
                player.user_name,
                player.lifespan,
                player.experience,
                player.gold,
                player.state,
                player.cultivation_start_time,
                player.last_check_in_date,
                player.level_up_rate,
                player.weapon,
                player.armor,
                player.main_technique,
                player.techniques,
                player.hp,
                player.mp,
                player.atk,
                player.atkpractice,
                player.spiritual_qi,
                player.max_spiritual_qi,
                player.blood_qi,
                player.max_blood_qi,
                player.magic_damage,
                player.physical_damage,
                player.magic_defense,
                player.physical_defense,
                player.mental_power,
                player.sect_id,
                player.sect_position,
                player.sect_contribution,
                player.sect_task,
                player.sect_elixir_get,
                player.blessed_spot_flag,
                player.blessed_spot_name,
                player.active_pill_effects,
                player.permanent_pill_gains,
                player.has_resurrection_pill,
                int(player.has_debuff_shield),
                player.pills_inventory,
                player.storage_ring,
                player.storage_ring_items,
                player.daily_pill_usage,
                player.last_daily_reset,
                int(player.is_soul_state or 0),
                int(player.soul_death_time or 0),
                int(player.soul_exp_before_death or 0),
                int(player.used_rebirth or 0)
            )
        )
        await self.conn.commit()

    async def get_player_by_id(self, user_id: str) -> Player:
        """根据用户ID获取玩家信息"""
        async with self.conn.execute(
            "SELECT * FROM players WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                # 过滤掉 Player 模型中不存在的字段（兼容旧数据库/迁移未完成的情况）
                filtered_data = {k: v for k, v in dict(row).items() if k in PLAYER_FIELDS}
                return Player(**filtered_data)
            return None

    async def get_player_by_name(self, user_name: str) -> Player:
        """根据道号获取玩家信息"""
        async with self.conn.execute(
            "SELECT * FROM players WHERE user_name = ?",
            (user_name,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                filtered_data = {k: v for k, v in dict(row).items() if k in PLAYER_FIELDS}
                return Player(**filtered_data)
            return None

    async def update_player(self, player: Player):
        """更新玩家信息"""
        await self.conn.execute(
            """
            UPDATE players SET
                level_index = ?,
                spiritual_root = ?,
                cultivation_type = ?,
                user_name = ?,
                lifespan = ?,
                experience = ?,
                gold = ?,
                state = ?,
                cultivation_start_time = ?,
                last_check_in_date = ?,
                level_up_rate = ?,
                weapon = ?,
                armor = ?,
                main_technique = ?,
                techniques = ?,
                hp = ?,
                mp = ?,
                atk = ?,
                atkpractice = ?,
                spiritual_qi = ?,
                max_spiritual_qi = ?,
                blood_qi = ?,
                max_blood_qi = ?,
                magic_damage = ?,
                physical_damage = ?,
                magic_defense = ?,
                physical_defense = ?,
                mental_power = ?,
                sect_id = ?,
                sect_position = ?,
                sect_contribution = ?,
                sect_task = ?,
                sect_elixir_get = ?,
                blessed_spot_flag = ?,
                blessed_spot_name = ?,
                active_pill_effects = ?,
                permanent_pill_gains = ?,
                has_resurrection_pill = ?,
                has_debuff_shield = ?,
                pills_inventory = ?,
                storage_ring = ?,
                storage_ring_items = ?,
                daily_pill_usage = ?,
                last_daily_reset = ?,
                rift_daily_count = ?,
                rift_count_reset_date = ?,
                is_soul_state = ?,
                soul_death_time = ?,
                soul_exp_before_death = ?,
                used_rebirth = ?
            WHERE user_id = ?
            """,
            (
                player.level_index,
                player.spiritual_root,
                player.cultivation_type,
                player.user_name,
                player.lifespan,
                player.experience,
                player.gold,
                player.state,
                player.cultivation_start_time,
                player.last_check_in_date,
                player.level_up_rate,
                player.weapon,
                player.armor,
                player.main_technique,
                player.techniques,
                player.hp,
                player.mp,
                player.atk,
                player.atkpractice,
                player.spiritual_qi,
                player.max_spiritual_qi,
                player.blood_qi,
                player.max_blood_qi,
                player.magic_damage,
                player.physical_damage,
                player.magic_defense,
                player.physical_defense,
                player.mental_power,
                player.sect_id,
                player.sect_position,
                player.sect_contribution,
                player.sect_task,
                player.sect_elixir_get,
                player.blessed_spot_flag,
                player.blessed_spot_name,
                player.active_pill_effects,
                player.permanent_pill_gains,
                player.has_resurrection_pill,
                int(player.has_debuff_shield),
                player.pills_inventory,
                player.storage_ring,
                player.storage_ring_items,
                player.daily_pill_usage,
                player.last_daily_reset,
                player.rift_daily_count,
                player.rift_count_reset_date,
                int(player.is_soul_state or 0),
                int(player.soul_death_time or 0),
                int(player.soul_exp_before_death or 0),
                int(player.used_rebirth or 0),
                player.user_id
            )
        )
        await self.conn.commit()

    async def delete_player(self, user_id: str):
        """删除玩家"""
        await self.conn.execute(
            "DELETE FROM players WHERE user_id = ?",
            (user_id,)
        )
        await self.conn.commit()

    async def delete_player_cascade(self, user_id: str):
        """级联删除玩家及所有关联数据"""
        async def safe_execute(sql: str, params: tuple):
            try:
                await self.conn.execute(sql, params)
            except Exception as e:
                sql_preview = sql.strip().split(" ")[0]
                logger.warning(f"[delete_player_cascade] 忽略执行 {sql_preview}: {e}")

        statements = [
            ("UPDATE spirit_eyes SET owner_id = NULL, owner_name = NULL, claim_time = NULL WHERE owner_id = ?", (user_id,)),
            ("DELETE FROM blessed_lands WHERE user_id = ?", (user_id,)),
            ("DELETE FROM spirit_farms WHERE user_id = ?", (user_id,)),
            ("DELETE FROM bank_accounts WHERE user_id = ?", (user_id,)),
            ("UPDATE bank_loans SET status = 'bad_debt' WHERE user_id = ? AND status = 'active'", (user_id,)),
            ("DELETE FROM bounty_tasks WHERE user_id = ?", (user_id,)),
            ("DELETE FROM dual_cultivation WHERE user_id = ?", (user_id,)),
            ("DELETE FROM dual_cultivation_requests WHERE from_id = ? OR target_id = ?", (user_id, user_id)),
            ("DELETE FROM user_cd WHERE user_id = ?", (user_id,)),
            ("DELETE FROM buff_info WHERE user_id = ?", (user_id,)),
            ("DELETE FROM impart_info WHERE user_id = ?", (user_id,)),
            ("DELETE FROM combat_cooldowns WHERE attacker_id = ? OR defender_id = ?", (user_id, user_id)),
            ("DELETE FROM pending_gifts WHERE sender_id = ? OR receiver_id = ?", (user_id, user_id)),
            # 炼丹系统（批次2）：已学习配方 / 称号
            ("DELETE FROM learned_recipes WHERE user_id = ?", (user_id,)),
            ("DELETE FROM player_titles WHERE user_id = ?", (user_id,)),
        ]

        for sql, params in statements:
            await safe_execute(sql, params)

        await self.conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    # ===== 炼丹系统（批次2）：配方学习 / 玩家称号 =====

    async def has_learned_recipe(self, user_id: str, recipe_id: int) -> bool:
        """检查玩家是否已学习指定配方（表不存在时按「未学习」处理）"""
        try:
            async with self.conn.execute(
                "SELECT 1 FROM learned_recipes WHERE user_id = ? AND recipe_id = ? LIMIT 1",
                (str(user_id), int(recipe_id)),
            ) as cursor:
                return await cursor.fetchone() is not None
        except Exception as e:
            logger.warning(f"[炼丹系统] 查询已学习配方失败: {e}")
            return False

    async def learn_recipe(self, user_id: str, recipe_id: int) -> bool:
        """学习配方（重复学习不会报错）"""
        try:
            await self.conn.execute(
                "INSERT OR IGNORE INTO learned_recipes (user_id, recipe_id, learn_time) VALUES (?, ?, ?)",
                (str(user_id), int(recipe_id), int(time.time())),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[炼丹系统] 学习配方失败: {e}")
            return False

    async def forget_recipe(self, user_id: str, recipe_id: int) -> bool:
        """遗忘配方（主要用于测试 / 管理）"""
        try:
            await self.conn.execute(
                "DELETE FROM learned_recipes WHERE user_id = ? AND recipe_id = ?",
                (str(user_id), int(recipe_id)),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[炼丹系统] 遗忘配方失败: {e}")
            return False

    async def get_learned_recipes(self, user_id: str) -> List[int]:
        """获取玩家已学习的配方ID列表"""
        try:
            async with self.conn.execute(
                "SELECT recipe_id FROM learned_recipes WHERE user_id = ?",
                (str(user_id),),
            ) as cursor:
                rows = await cursor.fetchall()
            return [int(row[0]) for row in rows]
        except Exception as e:
            logger.warning(f"[炼丹系统] 查询已学习配方列表失败: {e}")
            return []

    async def has_title(self, user_id: str, title: str) -> bool:
        """检查玩家是否拥有指定称号"""
        try:
            async with self.conn.execute(
                "SELECT 1 FROM player_titles WHERE user_id = ? AND title = ? LIMIT 1",
                (str(user_id), str(title)),
            ) as cursor:
                return await cursor.fetchone() is not None
        except Exception as e:
            logger.warning(f"[称号系统] 查询称号失败: {e}")
            return False

    async def grant_title(self, user_id: str, title: str) -> bool:
        """授予玩家称号（重复授予不会报错）"""
        try:
            await self.conn.execute(
                "INSERT OR IGNORE INTO player_titles (user_id, title, obtain_time) VALUES (?, ?, ?)",
                (str(user_id), str(title), int(time.time())),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[称号系统] 授予称号失败: {e}")
            return False

    async def revoke_title(self, user_id: str, title: str) -> bool:
        """收回称号（主要用于测试 / 管理）"""
        try:
            await self.conn.execute(
                "DELETE FROM player_titles WHERE user_id = ? AND title = ?",
                (str(user_id), str(title)),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[称号系统] 收回称号失败: {e}")
            return False

    async def get_titles(self, user_id: str) -> List[str]:
        """获取玩家所有称号"""
        try:
            async with self.conn.execute(
                "SELECT title FROM player_titles WHERE user_id = ? ORDER BY obtain_time",
                (str(user_id),),
            ) as cursor:
                rows = await cursor.fetchall()
            return [str(row[0]) for row in rows]
        except Exception as e:
            logger.warning(f"[称号系统] 查询称号列表失败: {e}")
            return []

    async def get_all_players(self):
        """获取所有玩家"""
        async with self.conn.execute("SELECT * FROM players") as cursor:
            rows = await cursor.fetchall()
            # 过滤掉 Player 模型中不存在的字段（兼容旧数据库/迁移未完成的情况）
            return [Player(**{k: v for k, v in dict(row).items() if k in PLAYER_FIELDS}) for row in rows]

    # ===== 商店数据操作 =====

    async def get_shop_data(self, shop_id: str = "global") -> Tuple[int, List[dict]]:
        """获取商店数据

        Args:
            shop_id: 商店ID，默认为全局商店

        Returns:
            (last_refresh_time, current_items) 元组
        """
        async with self.conn.execute(
            "SELECT last_refresh_time, current_items FROM shop WHERE shop_id = ?",
            (shop_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                last_refresh_time = row[0]
                try:
                    current_items = json.loads(row[1])
                except json.JSONDecodeError:
                    current_items = []
                return last_refresh_time, current_items
            return 0, []

    async def update_shop_data(self, shop_id: str, last_refresh_time: int, current_items: List[dict]):
        """更新商店数据

        Args:
            shop_id: 商店ID
            last_refresh_time: 最后刷新时间戳
            current_items: 当前商店物品列表
        """
        items_json = json.dumps(current_items, ensure_ascii=False)
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO shop (shop_id, last_refresh_time, current_items)
            VALUES (?, ?, ?)
            """,
            (shop_id, last_refresh_time, items_json)
        )
        await self.conn.commit()

    async def decrement_shop_item_stock(self, shop_id: str, item_name: str, quantity: int = 1, external_transaction: bool = False) -> tuple[bool, int, int]:
        """尝试扣减指定商店物品的库存（原子操作，可批量）

        Args:
            shop_id: 商店ID
            item_name: 物品名称
            quantity: 扣减数量（默认1，最小1）
            external_transaction: 是否由外部管理事务（True时不执行内部BEGIN/COMMIT/ROLLBACK）

        Returns:
            (是否成功, last_refresh_time, 扣减后的库存数量)
        """
        quantity = max(1, int(quantity))
        if not external_transaction:
            await self.begin_immediate()
        try:
            async with self.conn.execute(
                "SELECT last_refresh_time, current_items FROM shop WHERE shop_id = ?",
                (shop_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                if not external_transaction:
                    await self.conn.rollback()
                return False, 0, 0

            last_refresh_time = row[0]
            try:
                current_items = json.loads(row[1])
            except json.JSONDecodeError:
                current_items = []

            target_index = -1
            for idx, item in enumerate(current_items):
                if item.get('name') == item_name:
                    target_index = idx
                    break

            if target_index == -1:
                if not external_transaction:
                    await self.conn.rollback()
                return False, last_refresh_time, 0

            stock = current_items[target_index].get('stock', 0)
            if stock is None or stock <= 0:
                if not external_transaction:
                    await self.conn.rollback()
                return False, last_refresh_time, max(stock or 0, 0)

            if stock < quantity:
                if not external_transaction:
                    await self.conn.rollback()
                return False, last_refresh_time, stock

            new_stock = stock - quantity
            current_items[target_index]['stock'] = new_stock

            items_json = json.dumps(current_items, ensure_ascii=False)
            await self.conn.execute(
                "UPDATE shop SET current_items = ?, last_refresh_time = ? WHERE shop_id = ?",
                (items_json, last_refresh_time, shop_id)
            )
            if not external_transaction:
                await self.conn.commit()
            return True, last_refresh_time, new_stock
        except Exception:
            if not external_transaction:
                await self.conn.rollback()
            raise

    async def increment_shop_item_stock(self, shop_id: str, item_name: str, quantity: int = 1):
        """回滚库存（在购买失败时恢复库存），支持批量"""
        quantity = max(1, int(quantity))
        await self.begin_immediate()
        try:
            async with self.conn.execute(
                "SELECT last_refresh_time, current_items FROM shop WHERE shop_id = ?",
                (shop_id,)
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await self.conn.rollback()
                return

            last_refresh_time = row[0]
            try:
                current_items = json.loads(row[1])
            except json.JSONDecodeError:
                current_items = []

            for item in current_items:
                if item.get('name') == item_name:
                    current_stock = item.get('stock', 0) or 0
                    item['stock'] = current_stock + quantity
                    break

            items_json = json.dumps(current_items, ensure_ascii=False)
            await self.conn.execute(
                "UPDATE shop SET current_items = ?, last_refresh_time = ? WHERE shop_id = ?",
                (items_json, last_refresh_time, shop_id)
            )
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    # ===== 商店刷新次数（每人每日） =====

    async def get_shop_refresh_count(self, user_id: str, refresh_date: str) -> int:
        """查询某玩家在指定日期已使用的商店刷新次数

        Args:
            user_id: 玩家ID
            refresh_date: 日期字符串，形如 '2026-09-11'

        Returns:
            已使用次数（表不存在或没有记录时返回 0）
        """
        try:
            async with self.conn.execute(
                "SELECT refresh_count FROM shop_refresh_usage WHERE user_id = ? AND refresh_date = ?",
                (str(user_id), refresh_date)
            ) as cursor:
                row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.warning(f"[shop] 读取刷新次数失败（按0处理）: {e}")
            return 0

    async def increment_shop_refresh_count(self, user_id: str, refresh_date: str) -> int:
        """累加某玩家在指定日期的商店刷新次数

        Returns:
            累加后的次数
        """
        await self.conn.execute(
            """
            INSERT INTO shop_refresh_usage (user_id, refresh_date, refresh_count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, refresh_date)
            DO UPDATE SET refresh_count = refresh_count + 1
            """,
            (str(user_id), refresh_date)
        )
        await self.conn.commit()
        return await self.get_shop_refresh_count(user_id, refresh_date)
