# handlers/team_handlers.py
from astrbot.api.event import AstrMessageEvent
from ..managers.team_manager import TeamManager
from ..managers.rift_manager import RiftManager
from ..data.data_manager import DataBase
from .utils import resolve_target_user_id, extract_at_ids


class TeamHandlers:
    """组队系统处理器"""

    def __init__(self, db: DataBase, team_mgr: TeamManager, rift_mgr: RiftManager):
        self.db = db
        self.team_mgr = team_mgr
        self.rift_mgr = rift_mgr

    async def handle_create_team(self, event: AstrMessageEvent):
        """创建队伍"""
        user_id = event.get_sender_id()
        success, msg, team_id = await self.team_mgr.create_team(user_id)
        yield event.plain_result(msg)

    async def handle_invite_member(self, event: AstrMessageEvent, target: str = ""):
        """邀请队员"""
        user_id = event.get_sender_id()

        # 提取目标用户ID（兼容 At 组件/CQ码/纯数字ID/道号）
        target_id = await resolve_target_user_id(self.db, event, target)

        if not target_id:
            yield event.plain_result(
                "❌ 没识别到要邀请的玩家\n"
                "用法：/邀请入队 @某人 或 /邀请入队 道号"
            )
            return

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！请先使用 /创建队伍")
            return

        success, msg = await self.team_mgr.invite_member(team["team_id"], user_id, target_id)
        yield event.plain_result(msg)

    async def handle_accept_invitation(self, event: AstrMessageEvent):
        """接受邀请"""
        user_id = event.get_sender_id()
        success, msg = await self.team_mgr.accept_invitation(user_id)
        yield event.plain_result(msg)

    async def handle_reject_invitation(self, event: AstrMessageEvent):
        """拒绝邀请"""
        user_id = event.get_sender_id()
        success, msg = await self.team_mgr.reject_invitation(user_id)
        yield event.plain_result(msg)

    async def handle_team_info(self, event: AstrMessageEvent):
        """查看队伍信息"""
        user_id = event.get_sender_id()
        team_info = await self.team_mgr.get_team_info(user_id)

        if not team_info:
            yield event.plain_result("❌ 你不在任何队伍中！使用 /创建队伍 创建新队伍")
            return

        yield event.plain_result(team_info["display"])

    async def handle_leave_team(self, event: AstrMessageEvent):
        """离开队伍"""
        user_id = event.get_sender_id()
        success, msg = await self.team_mgr.leave_team(user_id)
        yield event.plain_result(msg)

    async def handle_kick_member(self, event: AstrMessageEvent, target: str = ""):
        """踢出队员"""
        user_id = event.get_sender_id()

        # 提取目标用户ID（兼容 At 组件/CQ码/纯数字ID/道号）
        target_id = await resolve_target_user_id(self.db, event, target)

        if not target_id:
            yield event.plain_result(
                "❌ 没识别到要踢出的玩家\n"
                "用法：/踢出队伍 @某人 或 /踢出队伍 道号"
            )
            return

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！")
            return

        success, msg = await self.team_mgr.kick_member(team["team_id"], user_id, target_id)
        yield event.plain_result(msg)

    async def handle_disband_team(self, event: AstrMessageEvent):
        """解散队伍"""
        user_id = event.get_sender_id()

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！")
            return

        success, msg = await self.team_mgr.disband_team(team["team_id"], user_id)
        yield event.plain_result(msg)

    async def handle_team_explore(self, event: AstrMessageEvent, rift_id: int = 0):
        """组队探索秘境"""
        user_id = event.get_sender_id()

        if not rift_id:
            yield event.plain_result("❌ 请输入秘境ID，例如：/组队探索 1")
            return

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！")
            return

        # 检查是否为队长
        if team["leader_id"] != user_id:
            yield event.plain_result("❌ 只有队长可以发起组队探索！")
            return

        success, msg = await self.rift_mgr.start_team_exploration(team["team_id"], rift_id, self.team_mgr)
        yield event.plain_result(msg)

    async def handle_team_complete_explore(self, event: AstrMessageEvent):
        """完成组队探索"""
        user_id = event.get_sender_id()

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！")
            return

        # 检查是否为队长
        if team["leader_id"] != user_id:
            yield event.plain_result("❌ 只有队长可以完成探索！")
            return

        success, msg, drops = await self.rift_mgr.finish_team_exploration(team["team_id"], user_id, self.team_mgr)
        yield event.plain_result(msg)

    async def handle_team_loot(self, event: AstrMessageEvent):
        """查看队伍掉落"""
        user_id = event.get_sender_id()

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！")
            return

        # 获取队伍掉落
        async with self.db.conn.execute(
            "SELECT id, item_name, item_type, quantity, assigned_to FROM team_loot WHERE team_id = ? AND assigned_to IS NULL",
            (team["team_id"],)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            yield event.plain_result("✅ 队伍暂无待分配物品。")
            return

        msg = "📦 队伍掉落物品\n━━━━━━━━━━━━━━━\n"
        for row in rows:
            loot_id, item_name, item_type, quantity, _ = row
            icon = {"丹药": "🔥", "装备": "⚔️", "功法": "📜", "材料": "📦"}.get(item_type, "📦")
            msg += f"\n{icon} [{loot_id}] {item_name} x{quantity}"

        msg += "\n\n💡 队长使用 /分配物品 <编号> @某人 分配物品"
        yield event.plain_result(msg)

    async def handle_assign_loot(self, event: AstrMessageEvent, loot_id: int = 0, target: str = ""):
        """分配物品"""
        user_id = event.get_sender_id()

        if not loot_id:
            yield event.plain_result("❌ 请输入物品编号和目标玩家，例如：/分配物品 1 @某人")
            return

        # 提取目标用户ID（兼容 At 组件/CQ码/纯数字ID/道号）
        target_id = await resolve_target_user_id(self.db, event, target)

        if not target_id:
            yield event.plain_result(
                "❌ 没识别到要分配的玩家\n"
                "用法：/分配物品 <编号> @某人"
            )
            return

        # 获取用户的队伍
        team = await self.team_mgr._get_player_team(user_id)
        if not team:
            yield event.plain_result("❌ 你不在任何队伍中！")
            return

        # 检查是否为队长
        if team["leader_id"] != user_id:
            yield event.plain_result("❌ 只有队长可以分配物品！")
            return

        # 检查目标是否在队伍中
        is_member = await self.team_mgr._is_team_member(team["team_id"], target_id)
        if not is_member:
            yield event.plain_result("❌ 该玩家不在队伍中！")
            return

        # 获取物品
        async with self.db.conn.execute(
            "SELECT item_name, item_type, quantity FROM team_loot WHERE id = ? AND team_id = ? AND assigned_to IS NULL",
            (loot_id, team["team_id"])
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            yield event.plain_result("❌ 物品不存在或已被分配！")
            return

        item_name, item_type, quantity = row

        # 获取目标玩家
        target_player = await self.db.get_player_by_id(target_id)
        if not target_player:
            yield event.plain_result("❌ 目标玩家不存在！")
            return

        # 分配物品
        if item_type == "丹药":
            # 存入丹药背包
            inventory = target_player.get_pills_inventory()
            inventory[item_name] = inventory.get(item_name, 0) + quantity
            target_player.set_pills_inventory(inventory)
            await self.db.update_player(target_player)
        else:
            # 存入储物戒
            if self.rift_mgr.storage_ring_manager:
                success, msg_storage = await self.rift_mgr.storage_ring_manager.store_item(
                    target_player, item_name, quantity, silent=True
                )
                if not success:
                    yield event.plain_result(f"❌ {msg_storage}")
                    return

        # 标记为已分配
        import time
        await self.db.conn.execute(
            "UPDATE team_loot SET assigned_to = ?, assigned_time = ? WHERE id = ?",
            (target_id, int(time.time()), loot_id)
        )
        await self.db.conn.commit()

        target_name = target_player.user_name if target_player.user_name else f"道友{target_id[:6]}"
        icon = {"丹药": "🔥", "装备": "⚔️", "功法": "📜", "材料": "📦"}.get(item_type, "📦")
        yield event.plain_result(f"✅ 已将 {icon}{item_name} x{quantity} 分配给 {target_name}")

    async def handle_join_team(self, event: AstrMessageEvent, target: str = ""):
        """主动加入队伍（支持队伍编号 或 @队长）"""
        user_id = event.get_sender_id()
        raw_target = (target or "").strip()

        team_id = None
        has_at = bool(extract_at_ids(event))

        if raw_target.isdigit() and not has_at:
            # 纯数字：按队伍编号处理
            team_id = int(raw_target)
        else:
            # @队长 或 直接输入队长道号
            leader_id = await resolve_target_user_id(self.db, event, raw_target)
            if leader_id:
                team_id = await self.team_mgr.get_open_team_id_by_leader(leader_id)
                if not team_id:
                    leader = await self.db.get_player_by_id(leader_id)
                    leader_name = leader.user_name if leader and leader.user_name else f"道友{leader_id[:6]}"
                    yield event.plain_result(f"❌ 【{leader_name}】当前没有可加入的队伍（未创建队伍或正在探索中）")
                    return

        if not team_id:
            yield event.plain_result(
                "❌ 请提供队伍编号或@队长\n"
                "用法：/加入队伍 3 或 /加入队伍 @队长\n"
                "💡 队伍编号可通过 /队伍信息 或创建队伍时获得"
            )
            return

        success, msg = await self.team_mgr.join_team(user_id, team_id)
        yield event.plain_result(msg)
