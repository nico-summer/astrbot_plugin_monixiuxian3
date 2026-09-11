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
            # 委托炼丹（批次3）：炼丹统计 / 委托记录
            # 注：委托人注销时其托管材料随记录一并清理；炼丹师注销时进行中的委托
            # 已托管的材料无法追回（材料在炼丹师身上），同样随记录清理。
            ("DELETE FROM alchemy_stats WHERE user_id = ?", (user_id,)),
            ("DELETE FROM alchemy_commissions WHERE client_id = ?", (user_id,)),
            ("DELETE FROM alchemy_commissions WHERE alchemist_id = ?", (user_id,)),
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

    # ===== 委托炼丹（批次3）：炼丹统计 =====

    async def record_alchemy_attempt(self, user_id: str, success: bool) -> bool:
        """记录一次炼丹尝试（成功 / 失败都会累计）

        炼丹师称号需要「成功炼制 N 次」，因此这里同时维护总次数与成功次数。
        """
        try:
            success_value = 1 if success else 0
            now = int(time.time())
            await self.conn.execute(
                """
                INSERT INTO alchemy_stats (user_id, total_attempts, success_count, last_attempt_time)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    total_attempts = total_attempts + 1,
                    success_count = success_count + ?,
                    last_attempt_time = ?
                """,
                (str(user_id), success_value, now, success_value, now),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.warning(f"[炼丹系统] 记录炼丹统计失败: {e}")
            return False

    async def get_alchemy_success_count(self, user_id: str) -> int:
        """获取成功炼制次数（炼丹师称号判定依据）"""
        stats = await self.get_alchemy_stats(user_id)
        return int(stats.get("success", 0) or 0)

    async def get_alchemy_stats(self, user_id: str) -> dict:
        """获取炼丹统计数据：{"total": int, "success": int, "last_time": int}"""
        empty = {"total": 0, "success": 0, "last_time": 0}
        try:
            async with self.conn.execute(
                """
                SELECT total_attempts, success_count, last_attempt_time
                FROM alchemy_stats WHERE user_id = ?
                """,
                (str(user_id),),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return empty
            return {
                "total": int(row[0] or 0),
                "success": int(row[1] or 0),
                "last_time": int(row[2] or 0),
            }
        except Exception as e:
            logger.warning(f"[炼丹系统] 查询炼丹统计失败: {e}")
            return empty

    # ===== 委托炼丹（批次3）：委托记录 =====

    COMMISSION_FIELDS = (
        "commission_id", "client_id", "alchemist_id", "recipe_id",
        "quantity", "fee", "status", "materials_json",
        "create_time", "accept_time", "complete_time",
    )

    @classmethod
    def _row_to_commission(cls, row) -> dict:
        """把数据库行转换为委托字典"""
        data = dict(row)
        try:
            materials = json.loads(data.get("materials_json") or "{}")
        except (TypeError, ValueError):
            materials = {}
        data["materials"] = materials if isinstance(materials, dict) else {}
        return data

    async def create_commission_record(
        self,
        client_id: str,
        recipe_id: int,
        quantity: int,
        fee: int,
        materials: dict,
        alchemist_id: str = None,
    ) -> Optional[int]:
        """写入一条委托记录，返回委托编号（失败返回 None）

        材料（含配方中的灵石消耗）与手续费在调用本方法前已从委托人身上扣除，
        这里仅负责落库。
        """
        try:
            materials_json = json.dumps(
                {str(k): int(v) for k, v in (materials or {}).items()},
                ensure_ascii=False,
            )
            cursor = await self.conn.execute(
                """
                INSERT INTO alchemy_commissions
                    (client_id, alchemist_id, recipe_id, quantity, fee, status,
                     materials_json, create_time)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    str(client_id),
                    str(alchemist_id) if alchemist_id else None,
                    int(recipe_id),
                    int(quantity),
                    int(fee),
                    materials_json,
                    int(time.time()),
                ),
            )
            await self.conn.commit()
            return int(cursor.lastrowid)
        except Exception as e:
            logger.error(f"[委托炼丹] 创建委托记录失败: {e}")
            return None

    async def get_commission(self, commission_id: int) -> Optional[dict]:
        """按编号获取委托详情"""
        try:
            async with self.conn.execute(
                "SELECT * FROM alchemy_commissions WHERE commission_id = ?",
                (int(commission_id),),
            ) as cursor:
                row = await cursor.fetchone()
            return self._row_to_commission(row) if row else None
        except Exception as e:
            logger.warning(f"[委托炼丹] 查询委托失败: {e}")
            return None

    async def list_commissions(
        self,
        status: str = None,
        alchemist_id: str = None,
        client_id: str = None,
        public_only: bool = False,
        order_by_fee: bool = False,
        limit: int = 20,
    ) -> list:
        """查询委托列表"""
        sql = "SELECT * FROM alchemy_commissions WHERE 1 = 1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(str(status))
        if alchemist_id:
            sql += " AND alchemist_id = ?"
            params.append(str(alchemist_id))
        if client_id:
            sql += " AND client_id = ?"
            params.append(str(client_id))
        if public_only:
            sql += " AND alchemist_id IS NULL"
        sql += " ORDER BY fee DESC, commission_id ASC" if order_by_fee else " ORDER BY commission_id DESC"
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))

        try:
            async with self.conn.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
            return [self._row_to_commission(row) for row in rows]
        except Exception as e:
            logger.warning(f"[委托炼丹] 查询委托列表失败: {e}")
            return []

    async def count_commissions(
        self,
        client_id: str = None,
        status: str = None,
        alchemist_id: str = None,
    ) -> int:
        """统计委托数量"""
        sql = "SELECT COUNT(*) FROM alchemy_commissions WHERE 1 = 1"
        params = []
        if client_id:
            sql += " AND client_id = ?"
            params.append(str(client_id))
        if status:
            sql += " AND status = ?"
            params.append(str(status))
        if alchemist_id:
            sql += " AND alchemist_id = ?"
            params.append(str(alchemist_id))
        try:
            async with self.conn.execute(sql, tuple(params)) as cursor:
                row = await cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.warning(f"[委托炼丹] 统计委托失败: {e}")
            return 0

    async def claim_commission(self, commission_id: int, alchemist_id: str) -> bool:
        """原子接单：仅当委托仍处于待接单状态时才能成功（防并发抢占）

        公开委托（alchemist_id IS NULL）与「指定炼丹师」的委托都可以被接单，
        后者只有被指定的炼丹师能接（此处用 (alchemist_id IS NULL OR alchemist_id = ?) 兜底）。
        """
        try:
            cursor = await self.conn.execute(
                """
                UPDATE alchemy_commissions
                SET alchemist_id = ?, accept_time = ?, status = 'in_progress'
                WHERE commission_id = ? AND status = 'pending'
                    AND (alchemist_id IS NULL OR alchemist_id = ?)
                """,
                (str(alchemist_id), int(time.time()), int(commission_id), str(alchemist_id)),
            )
            await self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"[委托炼丹] 接单失败: {e}")
            return False

    async def release_commission(self, commission_id: int, alchemist_id: str = None) -> bool:
        """把委托退回待接单状态（接单失败 / 炼丹师放弃时使用）

        Args:
            commission_id: 委托编号
            alchemist_id: 退回后保留的「指定炼丹师」；为 None 时变为公开委托
        """
        try:
            await self.conn.execute(
                """
                UPDATE alchemy_commissions
                SET alchemist_id = ?, accept_time = NULL, status = 'pending'
                WHERE commission_id = ? AND status = 'in_progress'
                """,
                (str(alchemist_id) if alchemist_id else None, int(commission_id)),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[委托炼丹] 回滚委托状态失败: {e}")
            return False

    async def complete_commission_record(self, commission_id: int) -> bool:
        """标记委托为已完成"""
        try:
            cursor = await self.conn.execute(
                """
                UPDATE alchemy_commissions
                SET status = 'completed', complete_time = ?
                WHERE commission_id = ? AND status = 'in_progress'
                """,
                (int(time.time()), int(commission_id)),
            )
            await self.conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"[委托炼丹] 完成委托失败: {e}")
            return False

    async def delete_commission(self, commission_id: int) -> bool:
        """删除委托记录（取消 / 放弃时使用）"""
        try:
            await self.conn.execute(
                "DELETE FROM alchemy_commissions WHERE commission_id = ?",
                (int(commission_id),),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"[委托炼丹] 删除委托失败: {e}")
            return False

    async def get_top_alchemists(self, limit: int = 5) -> list:
        """炼丹师排行（按成功炼制次数）"""
        try:
            async with self.conn.execute(
                """
                SELECT s.user_id, p.user_name, s.success_count, s.total_attempts
                FROM alchemy_stats s
                LEFT JOIN players p ON p.user_id = s.user_id
                WHERE s.success_count > 0
                ORDER BY s.success_count DESC, s.total_attempts ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                {
                    "user_id": str(row[0]),
                    "user_name": str(row[1] or ""),
                    "success_count": int(row[2] or 0),
                    "total_attempts": int(row[3] or 0),
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning(f"[委托炼丹] 查询炼丹师排行失败: {e}")
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
