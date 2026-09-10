# handlers/dual_cultivation_handlers.py
"""双修处理器"""
from astrbot.api.event import AstrMessageEvent
from ..data import DataBase
from ..managers.dual_cultivation_manager import DualCultivationManager
from ..models import Player
from .utils import player_required, resolve_target_user_id

__all__ = ["DualCultivationHandlers"]


class DualCultivationHandlers:
    """双修处理器"""
    
    def __init__(self, db: DataBase, dual_mgr: DualCultivationManager):
        self.db = db
        self.mgr = dual_mgr
    
    @player_required
    async def handle_dual_request(self, player: Player, event: AstrMessageEvent, target: str = ""):
        """发起双修"""
        target_id = await resolve_target_user_id(self.db, event, target)
        if not target_id:
            yield event.plain_result(
                "💕 双修系统\n"
                "━━━━━━━━━━━━━━━\n"
                "与他人双修可获得对方10%的修为！\n"
                "冷却时间：1小时\n"
                "━━━━━━━━━━━━━━━\n"
                "💡 使用 /双修 @某人"
            )
            return
        
        success, msg = await self.mgr.send_request(player, target_id)
        yield event.plain_result(msg)
    
    @player_required
    async def handle_accept(self, player: Player, event: AstrMessageEvent):
        """接受双修"""
        success, msg = await self.mgr.accept_request(player)
        yield event.plain_result(msg)
    
    @player_required
    async def handle_reject(self, player: Player, event: AstrMessageEvent):
        """拒绝双修"""
        success, msg = await self.mgr.reject_request(player.user_id)
        yield event.plain_result(msg)
    
