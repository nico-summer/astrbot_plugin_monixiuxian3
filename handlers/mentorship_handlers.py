# handlers/mentorship_handlers.py
"""师徒系统处理器"""
import re
from astrbot.api.event import AstrMessageEvent
from ..data import DataBase
from ..managers.mentorship_manager import MentorshipManager
from ..models import Player
from ..config_manager import ConfigManager
from .utils import player_required

__all__ = ["MentorshipHandlers"]


class MentorshipHandlers:
    """师徒系统处理器"""

    def __init__(self, db: DataBase, mentorship_mgr: MentorshipManager, config: ConfigManager):
        self.db = db
        self.mgr = mentorship_mgr
        self.config = config

    @player_required
    async def handle_become_mentor(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """收徒（邀请）"""
        target_id = self._extract_user_id(target)
        if not target_id:
            yield event.plain_result(
                "👨‍🏫 收徒系统\n"
                "━━━━━━━━━━━━━━━\n"
                "📋 收徒条件：\n"
                "  • 达到元婴期以上境界\n"
                "  • 最多收徒3名\n"
                "━━━━━━━━━━━━━━━\n"
                "🎁 师父权益：\n"
                "  • 徒弟修炼时获得8%传道奖励\n"
                "  • 每日可为徒弟灌顶一次\n"
                "  • 徒弟出师获得丰厚奖励\n"
                "━━━━━━━━━━━━━━━\n"
                "💡 使用：收徒 @某人\n"
                "💡 对方需使用【接受拜师】确认"
            )
            return

        # 检查目标玩家
        target_player = await self.db.get_player_by_id(target_id)
        if not target_player:
            yield event.plain_result("对方尚未踏入修仙之路")
            return

        if target_id == player.user_id:
            yield event.plain_result("不能收自己为徒！")
            return

        success, msg = await self.mgr.create_mentorship_request(player.user_id, target_id, "mentor")
        yield event.plain_result(msg)

    @player_required
    async def handle_become_apprentice(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """拜师（请求）"""
        target_id = self._extract_user_id(target)
        if not target_id:
            yield event.plain_result(
                "🙏 拜师系统\n"
                "━━━━━━━━━━━━━━━\n"
                "📋 拜师条件：\n"
                "  • 境界在金丹期以下\n"
                "  • 尚未拜师\n"
                "━━━━━━━━━━━━━━━\n"
                "🎁 徒弟福利：\n"
                "  • 师父每日可为你灌顶\n"
                "  • 快速提升修为\n"
                "  • 达到筑基期出师获得奖励\n"
                "━━━━━━━━━━━━━━━\n"
                "💡 使用：拜师 @某人\n"
                "💡 对方需使用【接受拜师】确认"
            )
            return

        # 检查目标玩家
        target_player = await self.db.get_player_by_id(target_id)
        if not target_player:
            yield event.plain_result("对方尚未踏入修仙之路")
            return

        if target_id == player.user_id:
            yield event.plain_result("不能拜自己为师！")
            return

        success, msg = await self.mgr.create_mentorship_request(player.user_id, target_id, "apprentice")
        yield event.plain_result(msg)

    @player_required
    async def handle_accept_mentorship(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """接受拜师请求"""
        # 如果有@人，则接受该人的请求
        if target:
            target_id = self._extract_user_id(target)
            if not target_id:
                yield event.plain_result("请@要接受的对象")
                return
        else:
            # 如果没有@人，则接受最新的请求
            request = await self.mgr.get_pending_request(player.user_id)
            if not request:
                yield event.plain_result("没有待处理的拜师请求")
                return
            target_id = request['from_id']

        success, msg = await self.mgr.accept_mentorship_request(player.user_id, target_id)
        yield event.plain_result(msg)

    @player_required
    async def handle_reject_mentorship(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """拒绝拜师请求"""
        # 如果有@人，则拒绝该人的请求
        if target:
            target_id = self._extract_user_id(target)
            if not target_id:
                yield event.plain_result("请@要拒绝的对象")
                return
        else:
            # 如果没有@人，则拒绝最新的请求
            request = await self.mgr.get_pending_request(player.user_id)
            if not request:
                yield event.plain_result("没有待处理的拜师请求")
                return
            target_id = request['from_id']

        success, msg = await self.mgr.reject_mentorship_request(player.user_id, target_id)
        yield event.plain_result(msg)

    @player_required
    async def handle_mentorship_info(self, player: Player, event: AstrMessageEvent):
        """查看师徒信息"""
        info = await self.mgr.get_mentorship_info(player)
        yield event.plain_result(f"👨‍🏫 师徒信息\n━━━━━━━━━━━━━━━\n{info}")

    @player_required
    async def handle_initiation(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """灌顶"""
        target_id = self._extract_user_id(target)
        if not target_id:
            yield event.plain_result(
                "✨ 灌顶系统\n"
                "━━━━━━━━━━━━━━━\n"
                "师父可将自身30%修为传授给徒弟\n"
                "徒弟将获得其中25%\n"
                "━━━━━━━━━━━━━━━\n"
                "⏰ 冷却时间：24小时\n"
                "💡 使用：灌顶 @徒弟"
            )
            return

        # 检查目标玩家
        apprentice = await self.db.get_player_by_id(target_id)
        if not apprentice:
            yield event.plain_result("对方尚未踏入修仙之路")
            return

        success, msg = await self.mgr.perform_initiation(player, apprentice)
        yield event.plain_result(msg)

    @player_required
    async def handle_expel(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """逐出师门"""
        target_id = self._extract_user_id(target)
        if not target_id:
            yield event.plain_result("请指定要逐出的徒弟\n💡 使用：逐出师门 @徒弟")
            return

        # 验证师徒关系
        apprentices = await self.mgr.get_all_apprentices(player.user_id)
        is_apprentice = any(a['apprentice_id'] == target_id for a in apprentices)

        if not is_apprentice:
            yield event.plain_result("对方不是您的徒弟")
            return

        success, msg = await self.mgr.dissolve_mentorship(player.user_id, target_id)
        if success:
            apprentice = await self.db.get_player_by_id(target_id)
            name = apprentice.user_name if apprentice else f"道友{target_id[-6:]}"
            yield event.plain_result(f"已将{name}逐出师门")
        else:
            yield event.plain_result(msg)

    @player_required
    async def handle_leave_mentor(self, player: Player, event: AstrMessageEvent):
        """离开师门"""
        mentorship = await self.mgr.get_mentorship(apprentice_id=player.user_id)
        if not mentorship:
            yield event.plain_result("您尚未拜师")
            return

        mentor_id = mentorship['mentor_id']
        success, msg = await self.mgr.dissolve_mentorship(mentor_id, player.user_id)

        if success:
            mentor = await self.db.get_player_by_id(mentor_id)
            name = mentor.user_name if mentor else f"道友{mentor_id[-6:]}"
            yield event.plain_result(f"您已离开{name}的师门")
        else:
            yield event.plain_result(msg)

    def _extract_user_id(self, msg: str) -> str:
        """提取用户ID（支持At和纯数字）"""
        if not msg:
            return ""

        # 尝试提取At
        at_match = re.search(r'\[CQ:at,qq=(\d+)\]', msg)
        if at_match:
            return at_match.group(1)

        # 尝试提取纯数字
        num_match = re.search(r'(\d{5,12})', msg)
        if num_match:
            return num_match.group(1)

        return ""
