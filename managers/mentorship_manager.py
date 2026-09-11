# managers/mentorship_manager.py
"""
师徒系统管理器
实现师徒关系建立、灌顶、传道奖励、出师等核心功能
"""

import time
from typing import Optional, List, Tuple, Dict
from ..data import DataBase
from ..models import Player
from ..config_manager import ConfigManager


class MentorshipManager:
    """师徒系统管理器"""

    # 师徒系统配置
    MENTOR_MIN_LEVEL = 16  # 元婴期初期(level_index=16)可收徒
    APPRENTICE_MAX_LEVEL = 15  # 金丹期后期(level_index=15)以下可拜师
    MAX_APPRENTICES = 3  # 最多收徒数量
    GRADUATION_LEVEL = 10  # 筑基期初期出师

    # 奖励配置
    MENTOR_REWARD_RATIO = 0.08  # 师父获得徒弟收益的8%
    INITIATION_COOLDOWN = 86400  # 灌顶冷却：24小时
    INITIATION_COST_RATIO = 0.3  # 灌顶消耗师父30%当前修为
    INITIATION_GAIN_RATIO = 0.25  # 徒弟获得师父消耗修为的25%
    GRADUATION_REWARD = {
        "gold": 50000,  # 灵石奖励
        "materials": ["星辰石", "紫金神铁"],  # 材料奖励
        "pill": "玄灵丹"  # 丹药奖励
    }

    def __init__(self, db: DataBase, config_manager: ConfigManager):
        self.db = db
        self.config = config_manager

    async def can_be_mentor(self, player: Player) -> Tuple[bool, str]:
        """检查玩家是否可以成为师父"""
        if player.level_index < self.MENTOR_MIN_LEVEL:
            required_level = self.config.get_level_data(player.cultivation_type)[self.MENTOR_MIN_LEVEL]["level_name"]
            return False, f"需要达到{required_level}才能收徒"

        apprentice_count = await self.db.conn.execute(
            "SELECT COUNT(*) FROM mentorship WHERE mentor_id = ? AND status = 'active'",
            (player.user_id,)
        )
        count = (await apprentice_count.fetchone())[0]

        if count >= self.MAX_APPRENTICES:
            return False, f"您已收满{self.MAX_APPRENTICES}个徒弟，无法再收徒"

        return True, ""

    async def can_be_apprentice(self, player: Player) -> Tuple[bool, str]:
        """检查玩家是否可以拜师"""
        if player.level_index > self.APPRENTICE_MAX_LEVEL:
            return False, "您的境界已超过金丹期，无法拜师"

        # 检查是否已有师父
        cursor = await self.db.conn.execute(
            "SELECT mentor_id FROM mentorship WHERE apprentice_id = ? AND status = 'active'",
            (player.user_id,)
        )
        existing = await cursor.fetchone()
        if existing:
            return False, "您已有师父，无法再次拜师"

        return True, ""

    async def create_mentorship_request(self, from_id: str, target_id: str, request_type: str) -> Tuple[bool, str]:
        """创建拜师请求（request_type: 'apprentice'徒弟请求拜师, 'mentor'师父邀请收徒）"""
        from_player = await self.db.get_player_by_id(from_id)
        target_player = await self.db.get_player_by_id(target_id)

        if not from_player or not target_player:
            return False, "玩家不存在"

        # 根据请求类型验证资格
        if request_type == "apprentice":
            # 徒弟请求拜师：验证徒弟资格和师父资格
            can_apprentice, msg = await self.can_be_apprentice(from_player)
            if not can_apprentice:
                return False, msg

            can_mentor, msg = await self.can_be_mentor(target_player)
            if not can_mentor:
                return False, msg

            mentor_id = target_id
            apprentice_id = from_id
        else:  # request_type == "mentor"
            # 师父邀请收徒：验证师父资格和徒弟资格
            can_mentor, msg = await self.can_be_mentor(from_player)
            if not can_mentor:
                return False, msg

            can_apprentice, msg = await self.can_be_apprentice(target_player)
            if not can_apprentice:
                return False, msg

            mentor_id = from_id
            apprentice_id = target_id

        # 检查是否已有待处理请求
        cursor = await self.db.conn.execute(
            "SELECT id FROM mentorship_requests WHERE from_id = ? AND target_id = ?",
            (from_id, target_id)
        )
        existing = await cursor.fetchone()
        if existing:
            return False, "已有待处理的请求，请等待对方回应"

        # 创建请求
        current_time = int(time.time())
        from_name = from_player.user_name or f"道友{from_id[-6:]}"
        target_name = target_player.user_name or f"道友{target_id[-6:]}"

        await self.db.conn.execute(
            """
            INSERT INTO mentorship_requests (
                from_id, from_name, target_id, target_name, request_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (from_id, from_name, target_id, target_name, request_type, current_time)
        )
        await self.db.conn.commit()

        if request_type == "apprentice":
            return True, f"🙏 已向{target_name}发送拜师请求，等待对方同意"
        else:
            return True, f"👨‍🏫 已向{target_name}发送收徒邀请，等待对方同意"

    async def accept_mentorship_request(self, target_id: str, from_id: str) -> Tuple[bool, str]:
        """接受拜师请求"""
        # 获取请求
        cursor = await self.db.conn.execute(
            "SELECT * FROM mentorship_requests WHERE from_id = ? AND target_id = ?",
            (from_id, target_id)
        )
        request = await cursor.fetchone()
        if not request:
            return False, "没有待处理的请求"

        request_dict = dict(request)
        request_type = request_dict['request_type']

        # 确定师父和徒弟
        if request_type == "apprentice":
            mentor_id = target_id
            apprentice_id = from_id
        else:  # request_type == "mentor"
            mentor_id = from_id
            apprentice_id = target_id

        # 再次验证资格（防止状态变化）
        mentor = await self.db.get_player_by_id(mentor_id)
        apprentice = await self.db.get_player_by_id(apprentice_id)

        if not mentor or not apprentice:
            await self.db.conn.execute("DELETE FROM mentorship_requests WHERE id = ?", (request_dict['id'],))
            await self.db.conn.commit()
            return False, "玩家不存在"

        can_mentor, msg = await self.can_be_mentor(mentor)
        if not can_mentor:
            await self.db.conn.execute("DELETE FROM mentorship_requests WHERE id = ?", (request_dict['id'],))
            await self.db.conn.commit()
            return False, msg

        can_apprentice, msg = await self.can_be_apprentice(apprentice)
        if not can_apprentice:
            await self.db.conn.execute("DELETE FROM mentorship_requests WHERE id = ?", (request_dict['id'],))
            await self.db.conn.commit()
            return False, msg

        # 创建师徒关系
        current_time = int(time.time())
        await self.db.begin_immediate()
        try:
            await self.db.conn.execute(
                """
                INSERT INTO mentorship (
                    mentor_id, apprentice_id, status, start_time,
                    last_initiation_time, total_mentor_rewards
                ) VALUES (?, ?, 'active', ?, 0, 0)
                """,
                (mentor_id, apprentice_id, current_time)
            )

            # 删除请求
            await self.db.conn.execute("DELETE FROM mentorship_requests WHERE id = ?", (request_dict['id'],))

            await self.db.conn.commit()

            mentor_name = mentor.user_name or f"道友{mentor_id[-6:]}"
            apprentice_name = apprentice.user_name or f"道友{apprentice_id[-6:]}"

            return True, f"🎊 {apprentice_name}成功拜入{mentor_name}门下！\n师徒同心，共证大道！"
        except Exception as e:
            await self.db.conn.rollback()
            return False, f"建立师徒关系失败：{str(e)}"

    async def reject_mentorship_request(self, target_id: str, from_id: str) -> Tuple[bool, str]:
        """拒绝拜师请求"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM mentorship_requests WHERE from_id = ? AND target_id = ?",
            (from_id, target_id)
        )
        request = await cursor.fetchone()
        if not request:
            return False, "没有待处理的请求"

        await self.db.conn.execute(
            "DELETE FROM mentorship_requests WHERE from_id = ? AND target_id = ?",
            (from_id, target_id)
        )
        await self.db.conn.commit()

        request_dict = dict(request)
        from_name = request_dict['from_name']
        return True, f"已拒绝{from_name}的请求"

    async def get_pending_request(self, target_id: str) -> Optional[Dict]:
        """获取待处理的拜师请求"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM mentorship_requests WHERE target_id = ? ORDER BY created_at DESC LIMIT 1",
            (target_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    async def get_mentorship(self, mentor_id: str = None, apprentice_id: str = None) -> Optional[Dict]:
        """获取师徒关系"""
        if mentor_id:
            cursor = await self.db.conn.execute(
                "SELECT * FROM mentorship WHERE mentor_id = ? AND status = 'active'",
                (mentor_id,)
            )
        elif apprentice_id:
            cursor = await self.db.conn.execute(
                "SELECT * FROM mentorship WHERE apprentice_id = ? AND status = 'active'",
                (apprentice_id,)
            )
        else:
            return None

        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    async def get_all_apprentices(self, mentor_id: str) -> List[Dict]:
        """获取师父的所有徒弟"""
        cursor = await self.db.conn.execute(
            "SELECT * FROM mentorship WHERE mentor_id = ? AND status = 'active'",
            (mentor_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def perform_initiation(self, initiator: Player, target: Player) -> Tuple[bool, str]:
        """灌顶（支持师父->徒弟 或 徒弟->师父）"""
        # 检查师徒关系（双向检查）
        mentorship_as_mentor = await self.get_mentorship(mentor_id=initiator.user_id)
        mentorship_as_apprentice = await self.get_mentorship(apprentice_id=initiator.user_id)

        # 判断是师父灌顶徒弟还是徒弟灌顶师父
        if mentorship_as_mentor and mentorship_as_mentor['apprentice_id'] == target.user_id:
            # 师父灌顶徒弟
            mentorship = mentorship_as_mentor
            mentor = initiator
            apprentice = target
        elif mentorship_as_apprentice and mentorship_as_apprentice['mentor_id'] == target.user_id:
            # 徒弟灌顶师父
            mentorship = mentorship_as_apprentice
            mentor = target
            apprentice = initiator
        else:
            return False, "你们不是师徒关系"

        # 检查冷却时间
        current_time = int(time.time())
        last_time = mentorship['last_initiation_time']
        if current_time - last_time < self.INITIATION_COOLDOWN:
            remaining = self.INITIATION_COOLDOWN - (current_time - last_time)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            return False, f"灌顶冷却中，还需等待{hours}小时{minutes}分钟"

        # 计算消耗和收益
        mentor_cost = int(mentor.experience * self.INITIATION_COST_RATIO)
        if mentor_cost < 1000:
            return False, "您的修为不足，无法为徒弟灌顶（至少需要3334修为）"

        apprentice_gain = int(mentor_cost * self.INITIATION_GAIN_RATIO)

        # 执行灌顶
        await self.db.begin_immediate()
        try:
            # 扣除师父修为
            await self.db.conn.execute(
                "UPDATE players SET experience = experience - ? WHERE user_id = ?",
                (mentor_cost, mentor.user_id)
            )

            # 增加徒弟修为
            await self.db.conn.execute(
                "UPDATE players SET experience = experience + ? WHERE user_id = ?",
                (apprentice_gain, apprentice.user_id)
            )

            # 更新灌顶时间
            await self.db.conn.execute(
                "UPDATE mentorship SET last_initiation_time = ? WHERE mentor_id = ? AND apprentice_id = ?",
                (current_time, mentor.user_id, apprentice.user_id)
            )

            await self.db.conn.commit()

            return True, f"✨ 灌顶成功！\n师父消耗修为：{mentor_cost:,}\n徒弟获得修为：{apprentice_gain:,}"

        except Exception as e:
            await self.db.conn.rollback()
            return False, f"灌顶失败：{str(e)}"

    async def grant_mentor_reward(self, apprentice: Player, exp_gain: int, gold_gain: int) -> None:
        """徒弟获得收益时，给予师父传道奖励"""
        mentorship = await self.get_mentorship(apprentice_id=apprentice.user_id)
        if not mentorship:
            return

        mentor_id = mentorship['mentor_id']
        mentor_exp = int(exp_gain * self.MENTOR_REWARD_RATIO)
        mentor_gold = int(gold_gain * self.MENTOR_REWARD_RATIO)

        if mentor_exp > 0 or mentor_gold > 0:
            await self.db.begin_immediate()
            try:
                # 给予师父奖励
                await self.db.conn.execute(
                    "UPDATE players SET experience = experience + ?, gold = gold + ? WHERE user_id = ?",
                    (mentor_exp, mentor_gold, mentor_id)
                )

                # 记录累计奖励
                await self.db.conn.execute(
                    "UPDATE mentorship SET total_mentor_rewards = total_mentor_rewards + ? WHERE mentor_id = ? AND apprentice_id = ?",
                    (mentor_exp + mentor_gold, mentor_id, apprentice.user_id)
                )

                await self.db.conn.commit()
            except:
                await self.db.conn.rollback()

    async def check_graduation(self, apprentice: Player) -> Tuple[bool, Optional[str]]:
        """检查徒弟是否可以出师"""
        if apprentice.level_index < self.GRADUATION_LEVEL:
            return False, None

        mentorship = await self.get_mentorship(apprentice_id=apprentice.user_id)
        if not mentorship:
            return False, None

        # 自动出师
        mentor_id = mentorship['mentor_id']
        mentor = await self.db.get_player_by_id(mentor_id)

        await self.db.begin_immediate()
        try:
            # 更新师徒状态
            await self.db.conn.execute(
                "UPDATE mentorship SET status = 'graduated', graduation_time = ? WHERE mentor_id = ? AND apprentice_id = ?",
                (int(time.time()), mentor_id, apprentice.user_id)
            )

            # 给予师父出师奖励
            reward = self.GRADUATION_REWARD
            await self.db.conn.execute(
                "UPDATE players SET gold = gold + ? WHERE user_id = ?",
                (reward["gold"], mentor_id)
            )

            # 存入材料到储物戒
            storage_items = mentor.get_storage_ring_items()
            for material in reward["materials"]:
                if material in storage_items:
                    storage_items[material]["count"] += 1
                else:
                    storage_items[material] = {"count": 1, "bound": False}
            mentor.set_storage_ring_items(storage_items)

            await self.db.conn.execute(
                "UPDATE players SET storage_ring_items = ? WHERE user_id = ?",
                (mentor.storage_ring_items, mentor_id)
            )

            # 给予徒弟丹药
            pills = apprentice.get_pills_inventory()
            pill_name = reward["pill"]
            pills[pill_name] = pills.get(pill_name, 0) + 1
            apprentice.set_pills_inventory(pills)

            await self.db.conn.execute(
                "UPDATE players SET pills_inventory = ? WHERE user_id = ?",
                (apprentice.pills_inventory, apprentice.user_id)
            )

            await self.db.conn.commit()

            mentor_name = mentor.user_name or f"道友{mentor_id[-6:]}"
            apprentice_name = apprentice.user_name or f"道友{apprentice.user_id[-6:]}"

            msg = (
                f"🎓 恭喜出师！\n"
                f"{apprentice_name}已达{apprentice.get_level(self.config)}，正式出师！\n"
                f"师父{mentor_name}获得：\n"
                f"  💰 灵石×{reward['gold']:,}\n"
                f"  📦 {', '.join(reward['materials'])}\n"
                f"徒弟获得：\n"
                f"  💊 {pill_name}×1"
            )

            return True, msg

        except Exception as e:
            await self.db.conn.rollback()
            return False, None

    async def dissolve_mentorship(self, mentor_id: str, apprentice_id: str) -> Tuple[bool, str]:
        """解除师徒关系（逐出师门）"""
        await self.db.conn.execute(
            "UPDATE mentorship SET status = 'dissolved', graduation_time = ? WHERE mentor_id = ? AND apprentice_id = ?",
            (int(time.time()), mentor_id, apprentice_id)
        )
        await self.db.conn.commit()
        return True, "已解除师徒关系"

    async def get_mentorship_info(self, player: Player) -> str:
        """获取师徒信息"""
        info_lines = []

        # 检查是否是师父
        apprentices = await self.get_all_apprentices(player.user_id)
        if apprentices:
            info_lines.append("【您的徒弟】")
            for i, mentorship in enumerate(apprentices, 1):
                apprentice = await self.db.get_player_by_id(mentorship['apprentice_id'])
                if apprentice:
                    name = apprentice.user_name or f"道友{mentorship['apprentice_id'][-6:]}"
                    level = apprentice.get_level(self.config)
                    days = (int(time.time()) - mentorship['start_time']) // 86400
                    rewards = mentorship['total_mentor_rewards']
                    info_lines.append(f"{i}. {name} | {level} | 拜师{days}天 | 累计传道奖励:{rewards:,}")

            # 显示收徒上限
            info_lines.append(f"\n收徒数量：{len(apprentices)}/{self.MAX_APPRENTICES}")

        # 检查是否是徒弟
        mentorship = await self.get_mentorship(apprentice_id=player.user_id)
        if mentorship:
            mentor = await self.db.get_player_by_id(mentorship['mentor_id'])
            if mentor:
                info_lines.append("\n【您的师父】")
                name = mentor.user_name or f"道友{mentorship['mentor_id'][-6:]}"
                level = mentor.get_level(self.config)
                days = (int(time.time()) - mentorship['start_time']) // 86400

                # 灌顶冷却
                last_initiation = mentorship['last_initiation_time']
                if last_initiation == 0:
                    cooldown_msg = "可用"
                else:
                    remaining = self.INITIATION_COOLDOWN - (int(time.time()) - last_initiation)
                    if remaining <= 0:
                        cooldown_msg = "可用"
                    else:
                        hours = remaining // 3600
                        minutes = (remaining % 3600) // 60
                        cooldown_msg = f"冷却中({hours}h{minutes}m)"

                info_lines.append(f"师父：{name} | {level}")
                info_lines.append(f"拜师：{days}天")
                info_lines.append(f"灌顶：{cooldown_msg}")

        if not info_lines:
            return "您还没有师徒关系\n💡 元婴期以上可收徒，金丹期以下可拜师"

        return "\n".join(info_lines)
