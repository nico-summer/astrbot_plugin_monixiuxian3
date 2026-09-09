# managers/team_manager.py
"""
组队系统管理器 - 处理队伍创建、邀请、管理等逻辑
"""

import time
from typing import Tuple, List, Optional, Dict
from ..data.data_manager import DataBase


class TeamManager:
    """组队系统管理器"""

    # 队伍人数限制
    MIN_TEAM_SIZE = 2
    MAX_TEAM_SIZE = 4

    # 邀请过期时间（秒）
    INVITATION_EXPIRE_TIME = 300  # 5分钟

    # 队伍状态
    STATUS_WAITING = "waiting"      # 待命
    STATUS_EXPLORING = "exploring"  # 探索中
    STATUS_DISBANDED = "disbanded"  # 已解散

    def __init__(self, db: DataBase):
        self.db = db

    async def create_team(self, leader_id: str) -> Tuple[bool, str, Optional[int]]:
        """
        创建队伍

        Args:
            leader_id: 队长ID

        Returns:
            (成功标志, 消息, 队伍ID)
        """
        # 检查玩家是否存在
        player = await self.db.get_player_by_id(leader_id)
        if not player:
            return False, "❌ 你还未踏入修仙之路！", None

        # 检查是否已在队伍中
        existing_team = await self._get_player_team(leader_id)
        if existing_team:
            return False, "❌ 你已经在一个队伍中了！请先离开当前队伍。", None

        # 创建队伍
        current_time = int(time.time())
        async with self.db.conn.execute(
            "INSERT INTO teams (leader_id, status, create_time) VALUES (?, ?, ?)",
            (leader_id, self.STATUS_WAITING, current_time)
        ) as cursor:
            team_id = cursor.lastrowid

        # 队长自动加入队伍
        await self.db.conn.execute(
            "INSERT INTO team_members (team_id, user_id, join_time) VALUES (?, ?, ?)",
            (team_id, leader_id, current_time)
        )
        await self.db.conn.commit()

        player_name = player.user_name if player.user_name else f"道友{leader_id[:6]}"
        return True, f"✅ 队伍创建成功！队长：{player_name}\n💡 使用 /邀请入队 @某人 邀请队员", team_id

    async def invite_member(self, team_id: int, inviter_id: str, invitee_id: str) -> Tuple[bool, str]:
        """
        邀请玩家加入队伍

        Args:
            team_id: 队伍ID
            inviter_id: 邀请者ID
            invitee_id: 被邀请者ID

        Returns:
            (成功标志, 消息)
        """
        # 检查队伍
        team = await self._get_team_by_id(team_id)
        if not team:
            return False, "❌ 队伍不存在！"

        # 检查队伍状态
        if team["status"] != self.STATUS_WAITING:
            return False, "❌ 队伍正在探索中，无法邀请新成员！"

        # 检查邀请者是否在队伍中
        is_member = await self._is_team_member(team_id, inviter_id)
        if not is_member:
            return False, "❌ 你不在这个队伍中！"

        # 检查被邀请者
        invitee = await self.db.get_player_by_id(invitee_id)
        if not invitee:
            return False, "❌ 被邀请的玩家不存在！"

        if inviter_id == invitee_id:
            return False, "❌ 不能邀请自己！"

        # 检查被邀请者是否已在队伍中
        invitee_team = await self._get_player_team(invitee_id)
        if invitee_team:
            return False, "❌ 该玩家已经在其他队伍中！"

        # 检查队伍人数
        member_count = await self._get_team_member_count(team_id)
        if member_count >= self.MAX_TEAM_SIZE:
            return False, f"❌ 队伍已满（{self.MAX_TEAM_SIZE}人）！"

        # 检查是否已有待处理的邀请
        existing_inv = await self._get_pending_invitation(invitee_id)
        if existing_inv:
            return False, "❌ 该玩家已有待处理的邀请！"

        # 创建邀请
        current_time = int(time.time())
        expire_time = current_time + self.INVITATION_EXPIRE_TIME
        await self.db.conn.execute(
            "INSERT INTO team_invitations (team_id, inviter_id, invitee_id, invite_time, expire_time) VALUES (?, ?, ?, ?, ?)",
            (team_id, inviter_id, invitee_id, current_time, expire_time)
        )
        await self.db.conn.commit()

        inviter = await self.db.get_player_by_id(inviter_id)
        inviter_name = inviter.user_name if inviter and inviter.user_name else f"道友{inviter_id[:6]}"
        invitee_name = invitee.user_name if invitee.user_name else f"道友{invitee_id[:6]}"

        return True, f"✅ 已向 {invitee_name} 发送组队邀请！\n💡 对方可使用 /接受组队 加入队伍（{self.INVITATION_EXPIRE_TIME//60}分钟内有效）"

    async def accept_invitation(self, user_id: str) -> Tuple[bool, str]:
        """
        接受队伍邀请

        Args:
            user_id: 用户ID

        Returns:
            (成功标志, 消息)
        """
        # 检查是否有待处理的邀请
        invitation = await self._get_pending_invitation(user_id)
        if not invitation:
            return False, "❌ 你没有待处理的组队邀请！"

        team_id = invitation["team_id"]
        inviter_id = invitation["inviter_id"]

        # 检查邀请是否过期
        current_time = int(time.time())
        if current_time > invitation["expire_time"]:
            # 删除过期邀请
            await self.db.conn.execute(
                "DELETE FROM team_invitations WHERE id = ?",
                (invitation["id"],)
            )
            await self.db.conn.commit()
            return False, "❌ 邀请已过期！"

        # 检查队伍
        team = await self._get_team_by_id(team_id)
        if not team:
            # 删除无效邀请
            await self.db.conn.execute(
                "DELETE FROM team_invitations WHERE id = ?",
                (invitation["id"],)
            )
            await self.db.conn.commit()
            return False, "❌ 队伍已解散！"

        # 检查队伍状态
        if team["status"] != self.STATUS_WAITING:
            await self.db.conn.execute(
                "DELETE FROM team_invitations WHERE id = ?",
                (invitation["id"],)
            )
            await self.db.conn.commit()
            return False, "❌ 队伍正在探索中，无法加入！"

        # 检查队伍人数
        member_count = await self._get_team_member_count(team_id)
        if member_count >= self.MAX_TEAM_SIZE:
            await self.db.conn.execute(
                "DELETE FROM team_invitations WHERE id = ?",
                (invitation["id"],)
            )
            await self.db.conn.commit()
            return False, f"❌ 队伍已满（{self.MAX_TEAM_SIZE}人）！"

        # 加入队伍
        await self.db.conn.execute(
            "INSERT INTO team_members (team_id, user_id, join_time) VALUES (?, ?, ?)",
            (team_id, user_id, current_time)
        )

        # 删除邀请
        await self.db.conn.execute(
            "DELETE FROM team_invitations WHERE id = ?",
            (invitation["id"],)
        )
        await self.db.conn.commit()

        # 获取队伍信息
        team_info = await self.get_team_info(user_id)
        if team_info:
            return True, f"✅ 加入队伍成功！\n\n{team_info['display']}"

        return True, "✅ 加入队伍成功！"

    async def reject_invitation(self, user_id: str) -> Tuple[bool, str]:
        """
        拒绝队伍邀请

        Args:
            user_id: 用户ID

        Returns:
            (成功标志, 消息)
        """
        # 检查是否有待处理的邀请
        invitation = await self._get_pending_invitation(user_id)
        if not invitation:
            return False, "❌ 你没有待处理的组队邀请！"

        # 删除邀请
        await self.db.conn.execute(
            "DELETE FROM team_invitations WHERE id = ?",
            (invitation["id"],)
        )
        await self.db.conn.commit()

        return True, "✅ 已拒绝组队邀请。"

    async def leave_team(self, user_id: str) -> Tuple[bool, str]:
        """
        离开队伍

        Args:
            user_id: 用户ID

        Returns:
            (成功标志, 消息)
        """
        # 检查是否在队伍中
        team = await self._get_player_team(user_id)
        if not team:
            return False, "❌ 你不在任何队伍中！"

        team_id = team["team_id"]

        # 检查队伍状态
        if team["status"] == self.STATUS_EXPLORING:
            return False, "❌ 队伍正在探索中，无法离开！"

        # 检查是否为队长
        if team["leader_id"] == user_id:
            # 队长离开，转让队长或解散
            member_count = await self._get_team_member_count(team_id)
            if member_count > 1:
                # 转让给最早加入的成员
                new_leader = await self._get_earliest_member(team_id, exclude_user=user_id)
                if new_leader:
                    await self.db.conn.execute(
                        "UPDATE teams SET leader_id = ? WHERE team_id = ?",
                        (new_leader, team_id)
                    )
                    new_leader_player = await self.db.get_player_by_id(new_leader)
                    new_leader_name = new_leader_player.user_name if new_leader_player and new_leader_player.user_name else f"道友{new_leader[:6]}"

                    # 移除队长
                    await self.db.conn.execute(
                        "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
                        (team_id, user_id)
                    )
                    await self.db.conn.commit()

                    return True, f"✅ 已离开队伍。队长转让给 {new_leader_name}"

            # 队伍只有队长，解散队伍
            return await self.disband_team(team_id, user_id)

        # 普通成员离开
        await self.db.conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id)
        )
        await self.db.conn.commit()

        return True, "✅ 已离开队伍。"

    async def kick_member(self, team_id: int, leader_id: str, target_id: str) -> Tuple[bool, str]:
        """
        踢出队员

        Args:
            team_id: 队伍ID
            leader_id: 队长ID
            target_id: 目标玩家ID

        Returns:
            (成功标志, 消息)
        """
        # 检查队伍
        team = await self._get_team_by_id(team_id)
        if not team:
            return False, "❌ 队伍不存在！"

        # 检查是否为队长
        if team["leader_id"] != leader_id:
            return False, "❌ 只有队长可以踢出成员！"

        # 检查队伍状态
        if team["status"] == self.STATUS_EXPLORING:
            return False, "❌ 队伍正在探索中，无法踢人！"

        # 检查目标是否在队伍中
        is_member = await self._is_team_member(team_id, target_id)
        if not is_member:
            return False, "❌ 该玩家不在队伍中！"

        # 不能踢自己
        if leader_id == target_id:
            return False, "❌ 不能踢出自己！请使用 /解散队伍"

        # 踢出成员
        await self.db.conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, target_id)
        )
        await self.db.conn.commit()

        target = await self.db.get_player_by_id(target_id)
        target_name = target.user_name if target and target.user_name else f"道友{target_id[:6]}"

        return True, f"✅ 已将 {target_name} 踢出队伍。"

    async def disband_team(self, team_id: int, leader_id: str) -> Tuple[bool, str]:
        """
        解散队伍

        Args:
            team_id: 队伍ID
            leader_id: 队长ID

        Returns:
            (成功标志, 消息)
        """
        # 检查队伍
        team = await self._get_team_by_id(team_id)
        if not team:
            return False, "❌ 队伍不存在！"

        # 检查是否为队长
        if team["leader_id"] != leader_id:
            return False, "❌ 只有队长可以解散队伍！"

        # 检查队伍状态
        if team["status"] == self.STATUS_EXPLORING:
            return False, "❌ 队伍正在探索中，无法解散！"

        # 删除队伍成员
        await self.db.conn.execute(
            "DELETE FROM team_members WHERE team_id = ?",
            (team_id,)
        )

        # 删除待处理的邀请
        await self.db.conn.execute(
            "DELETE FROM team_invitations WHERE team_id = ?",
            (team_id,)
        )

        # 删除队伍掉落
        await self.db.conn.execute(
            "DELETE FROM team_loot WHERE team_id = ?",
            (team_id,)
        )

        # 删除队伍
        await self.db.conn.execute(
            "DELETE FROM teams WHERE team_id = ?",
            (team_id,)
        )
        await self.db.conn.commit()

        return True, "✅ 队伍已解散。"

    async def get_team_info(self, user_id: str) -> Optional[Dict]:
        """
        获取玩家所在队伍信息

        Args:
            user_id: 用户ID

        Returns:
            队伍信息字典或None
        """
        team = await self._get_player_team(user_id)
        if not team:
            return None

        team_id = team["team_id"]

        # 获取队员列表
        members = await self._get_team_members(team_id)

        # 获取队员详细信息
        member_info_list = []
        for member_id in members:
            player = await self.db.get_player_by_id(member_id)
            if player:
                player_name = player.user_name if player.user_name else f"道友{member_id[:6]}"
                level_name = self._get_level_name(player.level_index)
                is_leader = "👑" if member_id == team["leader_id"] else "  "
                member_info_list.append(f"{is_leader} {player_name} - {level_name}")

        # 计算队伍平均等级
        avg_level = await self._get_team_average_level(team_id)

        status_text = {
            self.STATUS_WAITING: "待命中",
            self.STATUS_EXPLORING: "探索中",
            self.STATUS_DISBANDED: "已解散"
        }.get(team["status"], "未知")

        display = f"""
🤝 队伍信息
━━━━━━━━━━━━━━━
状态：{status_text}
人数：{len(members)}/{self.MAX_TEAM_SIZE}
队伍平均等级：{avg_level}

队员列表：
{chr(10).join(member_info_list)}
        """.strip()

        return {
            "team_id": team_id,
            "leader_id": team["leader_id"],
            "status": team["status"],
            "member_count": len(members),
            "members": members,
            "average_level": avg_level,
            "display": display
        }

    async def get_team_by_id(self, team_id: int) -> Optional[Dict]:
        """
        根据队伍ID获取队伍信息

        Args:
            team_id: 队伍ID

        Returns:
            队伍信息字典或None
        """
        return await self._get_team_by_id(team_id)

    async def is_leader(self, team_id: int, user_id: str) -> bool:
        """
        检查用户是否为队长

        Args:
            team_id: 队伍ID
            user_id: 用户ID

        Returns:
            是否为队长
        """
        team = await self._get_team_by_id(team_id)
        if not team:
            return False
        return team["leader_id"] == user_id

    def _get_level_name(self, level_index: int) -> str:
        """获取境界名称"""
        level_names = [
            "炼气期一层", "炼气期二层", "炼气期三层", "炼气期四层", "炼气期五层",
            "炼气期六层", "炼气期七层", "炼气期八层", "炼气期九层", "炼气期十层",
            "筑基期初期", "筑基期中期", "筑基期后期", "金丹期初期", "金丹期中期", "金丹期后期",
            "元婴期初期", "元婴期中期", "元婴期后期", "化神期初期", "化神期中期", "化神期后期",
            "炼虚期初期", "炼虚期中期", "炼虚期后期", "合体期初期", "合体期中期", "合体期后期",
            "大乘期初期", "大乘期中期", "大乘期后期", "渡劫期", "地仙境", "天仙境", "金仙境"
        ]
        if 0 <= level_index < len(level_names):
            return level_names[level_index]
        return f"境界{level_index}"

    # ========== 内部辅助方法 ==========

    async def _get_team_by_id(self, team_id: int) -> Optional[Dict]:
        """获取队伍信息"""
        async with self.db.conn.execute(
            "SELECT team_id, leader_id, status, create_time, rift_id FROM teams WHERE team_id = ?",
            (team_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "team_id": row[0],
                    "leader_id": row[1],
                    "status": row[2],
                    "create_time": row[3],
                    "rift_id": row[4]
                }
        return None

    async def _get_player_team(self, user_id: str) -> Optional[Dict]:
        """获取玩家所在队伍"""
        async with self.db.conn.execute(
            """SELECT t.team_id, t.leader_id, t.status, t.create_time, t.rift_id
               FROM teams t
               INNER JOIN team_members tm ON t.team_id = tm.team_id
               WHERE tm.user_id = ?""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "team_id": row[0],
                    "leader_id": row[1],
                    "status": row[2],
                    "create_time": row[3],
                    "rift_id": row[4]
                }
        return None

    async def _is_team_member(self, team_id: int, user_id: str) -> bool:
        """检查用户是否在队伍中"""
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] > 0 if row else False

    async def _get_team_member_count(self, team_id: int) -> int:
        """获取队伍成员数量"""
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM team_members WHERE team_id = ?",
            (team_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def _get_team_members(self, team_id: int) -> List[str]:
        """获取队伍成员ID列表"""
        async with self.db.conn.execute(
            "SELECT user_id FROM team_members WHERE team_id = ? ORDER BY join_time",
            (team_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def _get_pending_invitation(self, user_id: str) -> Optional[Dict]:
        """获取待处理的邀请"""
        current_time = int(time.time())
        async with self.db.conn.execute(
            """SELECT id, team_id, inviter_id, invitee_id, invite_time, expire_time
               FROM team_invitations
               WHERE invitee_id = ? AND expire_time > ?
               ORDER BY invite_time DESC
               LIMIT 1""",
            (user_id, current_time)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "team_id": row[1],
                    "inviter_id": row[2],
                    "invitee_id": row[3],
                    "invite_time": row[4],
                    "expire_time": row[5]
                }
        return None

    async def _get_earliest_member(self, team_id: int, exclude_user: str = None) -> Optional[str]:
        """获取最早加入的成员"""
        if exclude_user:
            async with self.db.conn.execute(
                "SELECT user_id FROM team_members WHERE team_id = ? AND user_id != ? ORDER BY join_time LIMIT 1",
                (team_id, exclude_user)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
        else:
            async with self.db.conn.execute(
                "SELECT user_id FROM team_members WHERE team_id = ? ORDER BY join_time LIMIT 1",
                (team_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def _get_team_average_level(self, team_id: int) -> int:
        """获取队伍平均等级"""
        members = await self._get_team_members(team_id)
        if not members:
            return 0

        total_level = 0
        for member_id in members:
            player = await self.db.get_player_by_id(member_id)
            if player:
                total_level += player.level_index

        return total_level // len(members)
