# handlers/tower_handlers.py
"""爬塔系统指令处理器

指令一览（注册见 main.py）：
    爬塔          查看当前进度和战力
    挑战爬塔      根据战力自动挑战到极限层数
    爬塔排行      查看本周排行榜
"""

from astrbot.api.event import AstrMessageEvent

from ..data.data_manager import DataBase
from ..managers.tower_manager import TowerManager
from ..models import Player
from .utils import player_required

__all__ = [
    "TowerHandlers",
    "CMD_TOWER_INFO",
    "CMD_TOWER_CHALLENGE",
    "CMD_TOWER_RANKING",
]

CMD_TOWER_INFO = "爬塔"
CMD_TOWER_CHALLENGE = "挑战爬塔"
CMD_TOWER_RANKING = "爬塔排行"


class TowerHandlers:
    """爬塔系统指令处理器"""

    def __init__(self, db: DataBase, tower_mgr: TowerManager):
        self.db = db
        self.tower_mgr = tower_mgr

    @player_required
    async def handle_info(self, player: Player, event: AstrMessageEvent):
        """查看爬塔信息"""
        msg = await self.tower_mgr.format_tower_info(player)
        yield event.plain_result(msg)

    @player_required
    async def handle_challenge(self, player: Player, event: AstrMessageEvent):
        """挑战爬塔"""
        success, msg = await self.tower_mgr.challenge_tower(player)
        yield event.plain_result(msg)

    @player_required
    async def handle_ranking(self, player: Player, event: AstrMessageEvent):
        """查看排行榜"""
        msg = await self.tower_mgr.format_ranking()
        yield event.plain_result(msg)
