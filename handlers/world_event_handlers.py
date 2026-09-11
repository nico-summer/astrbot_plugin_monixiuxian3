# handlers/world_event_handlers.py
"""世界事件指令处理器（批次4：替代历练系统）

指令一览（注册见 main.py）：
    世界事件          查看本群进行中的世界事件（报名人数 / 剩余时间 / 我的状态）
    加入世界事件      报名参战（报名截止后自动开战，结束后统一结算）
    退出世界事件      报名阶段取消报名
    世界事件战绩      查看自己最近的世界事件记录

世界事件为群内多人协作玩法：报名 → 开战 → 结算，全过程由定时任务自动推进，
结果广播到事件所在群，无需玩家手动提交。
"""

from astrbot.api.event import AstrMessageEvent

from ..data.data_manager import DataBase
from ..managers.world_event_manager import (
    CMD_JOIN_WORLD_EVENT,
    CMD_LEAVE_WORLD_EVENT,
    CMD_WORLD_EVENT,
    CMD_WORLD_EVENT_RECORD,
    WorldEventManager,
)
from ..models import Player
from .utils import player_required

__all__ = [
    "WorldEventHandlers",
    "CMD_WORLD_EVENT",
    "CMD_JOIN_WORLD_EVENT",
    "CMD_LEAVE_WORLD_EVENT",
    "CMD_WORLD_EVENT_RECORD",
]

GROUP_ONLY_HINT = (
    "🌫️ 世界事件只在群聊中开启\n"
    "💡 请在已开启修仙玩法的群聊中使用该指令"
)


class WorldEventHandlers:
    """世界事件指令处理器"""

    def __init__(self, db: DataBase, world_event_mgr: WorldEventManager):
        self.db = db
        self.world_event_mgr = world_event_mgr

    @staticmethod
    def _get_group_id(event: AstrMessageEvent) -> str:
        """获取当前群号（私聊返回空字符串）"""
        try:
            group_id = event.get_group_id()
        except Exception:
            group_id = None
        return str(group_id or "").strip()

    @player_required
    async def handle_show(self, player: Player, event: AstrMessageEvent):
        """查看本群进行中的世界事件"""
        group_id = self._get_group_id(event)
        if not group_id:
            yield event.plain_result(GROUP_ONLY_HINT)
            return

        current = await self.world_event_mgr.get_current_event(group_id)
        if not current:
            yield event.plain_result(self.world_event_mgr.describe_event(None))
            return

        participants = await self.world_event_mgr.get_participants(current["event_id"])
        joined = await self.world_event_mgr.is_participant(current["event_id"], player.user_id)
        yield event.plain_result(
            self.world_event_mgr.describe_event(current, len(participants), joined)
        )

    @player_required
    async def handle_join(self, player: Player, event: AstrMessageEvent):
        """加入世界事件"""
        group_id = self._get_group_id(event)
        if not group_id:
            yield event.plain_result(GROUP_ONLY_HINT)
            return

        current = await self.world_event_mgr.get_current_event(group_id)
        if not current:
            yield event.plain_result(
                "🌫️ 当前没有世界事件\n"
                "💡 事件会自动生成并广播到群，发送「世界事件帮助」查看玩法"
            )
            return

        if current["status"] != "signup":
            yield event.plain_result(
                f"⚔️ 【{current['data'].get('name', '世界事件')}】已经开战，无法中途加入\n"
                "💡 请等待下一场事件"
            )
            return

        success, msg = await self.world_event_mgr.join_event(player, current["event_id"])
        yield event.plain_result(msg)

    @player_required
    async def handle_leave(self, player: Player, event: AstrMessageEvent):
        """退出世界事件（报名阶段）"""
        group_id = self._get_group_id(event)
        if not group_id:
            yield event.plain_result(GROUP_ONLY_HINT)
            return

        current = await self.world_event_mgr.get_current_event(group_id)
        if not current:
            yield event.plain_result("🌫️ 当前没有世界事件")
            return

        success, msg = await self.world_event_mgr.leave_event(player, current["event_id"])
        yield event.plain_result(msg)

    @player_required
    async def handle_record(self, player: Player, event: AstrMessageEvent):
        """查看个人世界事件战绩"""
        history = await self.world_event_mgr.get_player_history(player.user_id)
        yield event.plain_result(self.world_event_mgr.format_player_history(history))
